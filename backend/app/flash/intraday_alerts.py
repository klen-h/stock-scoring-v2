# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】盘中风险警示（先知雷达的"当日时效"层）
================================================================================

规则（全部基于盘中实时数据，触发阈值取"极端"档防噪音）：
  1. 指数单边急跌      **多指数、各用各的阈值**（见 `_INDEX_WATCH`）：
                       上证 ≤-1.5%（≤-2.5 severe）｜创业板指 / 科创50 ≤-2.5%（≤-4.0 severe）
                       ★ 2026-09-19 扩展：原只看上证 ⇒ 漏「结构性行情」（创业板/科创暴跌
                         而上证平稳）。高波动指数**不能沿用上证阈值** —— 它们单日 ±2% 属
                         常见波动，用 -1.5% 会天天报警。
  2. 涨跌比极值        涨跌比 < 0.25（跌停潮式结构恶化）
  3. 跌停家数激增      跌停（跌幅 ≤ -9.7%）家数 ≥ 30

★ 北向规则已移除（2026-09-06）：交易所 2024-05-13 起取消北向盘中披露，
  东财 kamt.rtmin 接口存活但全天返回 0——基于它的"北向流出"警示永不触发，
  属死数据。机构/杠杆行为改由两融（T+1）与主力资金流（盘后）覆盖。

防骚扰设计：
  - 每类**每档**警示每天最多推 1 次（进程内去重；**升级到更严重档会再推一条**；
    重启最多重推一次，可接受）
  - 全局最小间隔 **5 分钟**（跨类别防骚扰；总条数上限由"每类每档一次"兜住 ⇒ ≤12 条/天，
    且只有真崩的日子才会全触发）
  - 仅交易时段检查；触发即推（与战法推送同车道 force=True）
  - 跌停判定用跌幅 ≤-9.7% 近似（主板口径；创业板 20cm 会漏计，
    作为恐慌探测器宁漏勿误）

调度：intraday_alert_loop 交易时段每 **3 分钟**检查一次（9:40-11:30 / 13:00-15:00）。
      ★ 2026-09-19：30 → 10 → 3 分钟（时效优先）。每轮发 **3 个腾讯指数请求**
      （上证 / 创业板指 / 科创50 各 1 个；`get_index` **无缓存层** ⇒ 间隔就是真实请求
      频率），其余判定全读内存快照 ⇒ ≈**230 请求/交易日**，相对每 5 分钟的**全市场刷新**
      （~4000 只）仍 <1%。全局防骚扰门限 5 分钟。
数据源：腾讯指数/行情实时接口 + tencent 内存行情缓存（每 2-3 分钟刷新）。
================================================================================
"""

from __future__ import annotations  # 兼容 Python 3.9（Render/Docker/CI）：允许 -> dict | None 注解

from datetime import timedelta
from typing import Dict, List

# ★ 2026-09-19：本模块原用 `datetime.now()`（服务器本地时间）——Render 上靠
#   `TZ=Asia/Shanghai` 才碰巧正确，**一旦没设就静默失效**（UTC 下 9:40-11:30 的
#   盘中判定整体偏 8 小时 ⇒ 警示永不触发）。统一改用项目「全链路北京时间」口径。
from app.flash.rules import beijing_now

_LAST_PUSH = {}          # {alert_key: date_str} 每类每日一次
_LAST_ANY_PUSH = None    # 全局最小间隔


def _trading_session(now=None) -> bool:
    """交易时段（含尾盘集合竞价前）：9:40-11:30 / 13:00-15:00。

    ★ 2026-09-19：默认取**北京时间**（原 `datetime.now()` 依赖容器 `TZ=Asia/Shanghai`，
      UTC 环境下整体偏 8 小时 ⇒ 判定全部落空、盘中警示静默失效）。
    """
    now = now or beijing_now()
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return (9 * 60 + 40) <= m <= (11 * 60 + 30) or (13 * 60) <= m <= (15 * 60)


def _can_push(today: str, key: str) -> bool:
    global _LAST_ANY_PUSH
    if _LAST_PUSH.get(key) == today:
        return False
    # ★ 2026-09-19：全局最小间隔 30 → 10 → **5 分钟**。原值比检查间隔还长 ⇒ 检查再快也被它吃掉
    #   （例：「上证🟡」推完后 20 分钟才出现的「跌停潮🔴」会被拦到下个窗口）。
    #   总条数上限由「每类每档每日一次」兜住（3 个指数 + 涨跌比 + 跌停 + 黑天鹅 = 6 类，
    #   每类 2 档 ⇒ ≤12 条/天；且只有**真崩**的日子才会全触发）⇒ 降间隔不会变吵。
    if _LAST_ANY_PUSH and beijing_now() - _LAST_ANY_PUSH < timedelta(minutes=5):
        return False
    return True


def _mark_pushed(today: str, key: str) -> None:
    global _LAST_ANY_PUSH
    _LAST_PUSH[key] = today
    _LAST_ANY_PUSH = beijing_now()


# ★ 2026-09-19：多指数各用各的阈值。
#   · 为什么加创业板/科创：只看上证会**漏掉结构性行情**（创业板/科创暴跌而上证平稳）；
#   · 为什么不共用阈值：创业板指/科创50 单日 ±2% 属常见波动，沿用上证的 -1.5% 会天天报警
#     ⇒ 高波动指数用更严的档位（黄 -2.5% / 红 -4.0%）。
#   代码前缀规则见 `tencent.get_index`（0 开头 → sh，其余 → sz）：
#   上证=000001、**科创50=000688（sh）**、**创业板指=399006（sz）**。
_INDEX_WATCH = [
    # (指数代码, 展示名, 黄灯阈值, 红灯阈值)
    ("000001", "上证指数", -1.5, -2.5),
    ("399006", "创业板指", -2.5, -4.0),
    ("000688", "科创50", -2.5, -4.0),
]


def _index_drop_alert() -> List[Dict]:
    """主要指数单边急跌（盘中实时）。**每个指数各自阈值**，避免高波动指数天天报警。

    返回列表（可能命中多个指数）；de-dup key 带指数名，互不覆盖。
    """
    out: List[Dict] = []
    from app.tencent import get_index
    for code, name, warn, severe in _INDEX_WATCH:
        try:
            q = get_index(code)
            chg = q.get("change_pct") if q else None
            if chg is None:
                continue
            if chg <= severe:
                out.append({"key": f"indexdrop:{name}", "sev": "🔴",
                            "text": f"**{name}** 单边急跌 **{chg:.2f}%**"
                                    f"（现价 {q.get('price')}）——指数级风险释放中，"
                                    f"不接飞刀、不加仓"})
            elif chg <= warn:
                out.append({"key": f"indexdrop:{name}", "sev": "🟡",
                            "text": f"**{name}** 下跌 **{chg:.2f}%**——单边走弱，"
                                    f"个股信号可信度下降"})
        except Exception as e:
            print(f"[intraday_alert] {name} 急跌检查失败: {e}")
    return out


def limit_down_count() -> int:
    """当前全市场跌停家数（跌幅 ≤-9.7% 近似，内存行情缓存）。"""
    try:
        from app.tencent import _cache
        stocks = _cache.get("stocks", {}) or {}
        if len(stocks) < 500:
            return 0
        return sum(1 for s in stocks.values()
                   if (s.get("change_pct") or 0) <= -9.7 and (s.get("price") or 0) > 0)
    except Exception:
        return 0


def black_swan_active() -> bool:
    """黑天鹅熔断：全市场跌停 ≥100 家（供模拟盘禁开新仓等联动）。"""
    return limit_down_count() >= 100


def _breadth_alerts() -> List[Dict]:
    """涨跌比极值 + 跌停家数（内存行情缓存）。"""
    out = []
    try:
        from app.tencent import _cache
        stocks = _cache.get("stocks", {}) or {}
        if len(stocks) < 500:
            return out
        valid = [s for s in stocks.values()
                 if s.get("change_pct") is not None and (s.get("price") or 0) > 0]
        if len(valid) < 500:
            return out
        up = sum(1 for s in valid if s["change_pct"] > 0)
        down = sum(1 for s in valid if s["change_pct"] < 0)
        limit_down = limit_down_count()
        if down > 0 and up / down < 0.25:
            out.append({"key": "breadth", "sev": "🔴",
                        "text": f"涨跌比 **{up}/{down}**（{up/max(1,down):.2f}）——"
                                f"跌停潮式结构恶化，普跌行情个股信号可信度下降"})
        if limit_down >= 30:
            out.append({"key": "limitdown", "sev": "🔴" if limit_down >= 60 else "🟡",
                        "text": f"跌停家数 **{limit_down}** 只——恐慌蔓延，不抄底、不补仓"})
    except Exception as e:
        print(f"[intraday_alert] 宽度检查失败: {e}")
    return out


def check_risk_alerts() -> dict:
    """
    盘中风险警示检查（调度器每 30 分钟调用一次，仅交易时段）。
    触发的警示合并为一条企微消息（每类每日最多一次）。
    """
    # ★ 「每类每日一次」的去重 key 也必须是北京时间（跨日窗口下 UTC 会差一天）
    today = beijing_now().strftime("%Y-%m-%d")
    if not _trading_session():
        return {"checked": False, "reason": "非交易时段"}

    candidates = list(_index_drop_alert())       # ★ 现已返回 List（多指数可同时命中）
    candidates.extend(_breadth_alerts())
    # 黑天鹅熔断级（跌停 ≥100）：独立于普通跌停激增警示
    try:
        ld_all = limit_down_count()
        if ld_all >= 100 and _can_push(today, "blackswan:🔴"):
            candidates.append({"key": "blackswan", "sev": "🔴",
                "text": f"**全市场熔断级**：跌停 {ld_all} 只——黑天鹅事件，"
                        f"模拟盘已暂停买入信号，现金为王"})
    except Exception:
        pass

    # ★ 2026-09-19：去重 key 带上**严重度** —— 原按 `key` 去重（每类每日一次）会导致
    #   「先推黄灯（-1.5%）、随后升级为红灯（-2.5%）」时**红灯被抑制**，而升级那一刻
    #   恰恰最需要提醒。带上 sev 后：**同级**仍是每日一次（噪音不增加），
    #   **升级**能再推一条。
    def _dedup_key(c: dict) -> str:
        return f"{c['key']}:{c['sev']}"

    to_push = [c for c in candidates if _can_push(today, _dedup_key(c))]
    if not to_push:
        return {"checked": True, "triggered": len(candidates), "pushed": 0}

    body = "\n\n".join(f"{c['sev']} {c['text']}" for c in to_push)
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched(
            "先知雷达·盘中风险警示",
            body + "\n\n> 盘中快照警示（每类每日一次；**升级到更严重档会再提醒一次**）。"
                   "收盘 15:35 全量扫描为准。",
            force=True, category="risk")
        for c in to_push:
            _mark_pushed(today, _dedup_key(c))
        print(f"[intraday_alert] 盘中风险警示推送 {len(to_push)} 条: "
              f"{[c['key'] for c in to_push]}")
    except Exception as e:
        print(f"[intraday_alert] 推送失败: {e}")
        return {"checked": True, "triggered": len(to_push), "pushed": 0,
                "error": str(e)[:120]}
    return {"checked": True, "triggered": len(to_push), "pushed": len(to_push)}
