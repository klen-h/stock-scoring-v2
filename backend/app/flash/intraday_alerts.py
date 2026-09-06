# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】盘中风险警示（先知雷达的"当日时效"层）
================================================================================

规则（全部基于盘中实时数据，触发阈值取"极端"档防噪音）：
  1. 上证单边急跌      当日跌幅 ≤ -1.5%（≤ -2.5% severe）
  2. 涨跌比极值        涨跌比 < 0.25（跌停潮式结构恶化）
  3. 跌停家数激增      跌停（跌幅 ≤ -9.7%）家数 ≥ 30

★ 北向规则已移除（2026-09-06）：交易所 2024-05-13 起取消北向盘中披露，
  东财 kamt.rtmin 接口存活但全天返回 0——基于它的"北向流出"警示永不触发，
  属死数据。机构/杠杆行为改由两融（T+1）与主力资金流（盘后）覆盖。

防骚扰设计：
  - 每类警示每天最多推 1 次（进程内去重；重启最多重推一次，可接受）
  - 全局最小间隔 30 分钟
  - 仅交易时段检查；触发即推（与战法推送同车道 force=True）
  - 跌停判定用跌幅 ≤-9.7% 近似（主板口径；创业板 20cm 会漏计，
    作为恐慌探测器宁漏勿误）

调度：intraday_alert_loop 交易时段每 30 分钟检查一次（9:40-11:30 / 13:00-15:00）。
数据源：腾讯指数/行情实时接口 + tencent 内存行情缓存（每 2-3 分钟刷新）。
================================================================================
"""

from __future__ import annotations  # 兼容 Python 3.9（Render/Docker/CI）：允许 -> dict | None 注解

from datetime import datetime, timedelta
from typing import Dict, List

_LAST_PUSH = {}          # {alert_key: date_str} 每类每日一次
_LAST_ANY_PUSH = None    # 全局最小间隔


def _trading_session(now=None) -> bool:
    """交易时段（含尾盘集合竞价前）：9:40-11:30 / 13:00-15:00。"""
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    m = now.hour * 60 + now.minute
    return (9 * 60 + 40) <= m <= (11 * 60 + 30) or (13 * 60) <= m <= (15 * 60)


def _can_push(today: str, key: str) -> bool:
    global _LAST_ANY_PUSH
    if _LAST_PUSH.get(key) == today:
        return False
    if _LAST_ANY_PUSH and datetime.now() - _LAST_ANY_PUSH < timedelta(minutes=30):
        return False
    return True


def _mark_pushed(today: str, key: str) -> None:
    global _LAST_ANY_PUSH
    _LAST_PUSH[key] = today
    _LAST_ANY_PUSH = datetime.now()


def _index_drop_alert() -> Dict | None:
    """上证单边急跌（盘中实时）。"""
    try:
        from app.tencent import get_index
        q = get_index("000001")
        chg = q.get("change_pct") if q else None
        if chg is None:
            return None
        if chg <= -2.5:
            return {"key": "indexdrop", "sev": "🔴",
                    "text": f"上证指数单边急跌 **{chg:.2f}%**（现价 {q.get('price')}）——"
                            f"指数级风险释放中，不接飞刀、不加仓"}
        if chg <= -1.5:
            return {"key": "indexdrop", "sev": "🟡",
                    "text": f"上证指数下跌 **{chg:.2f}%**——单边走弱，个股信号可信度下降"}
    except Exception as e:
        print(f"[intraday_alert] 指数急跌检查失败: {e}")
    return None


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
    today = datetime.now().strftime("%Y-%m-%d")
    if not _trading_session():
        return {"checked": False, "reason": "非交易时段"}

    candidates = []
    idx = _index_drop_alert()
    if idx:
        candidates.append(idx)
    candidates.extend(_breadth_alerts())
    # 黑天鹅熔断级（跌停 ≥100）：独立于普通跌停激增警示
    try:
        ld_all = limit_down_count()
        if ld_all >= 100 and _can_push(today, "blackswan"):
            candidates.append({"key": "blackswan", "sev": "🔴",
                "text": f"**全市场熔断级**：跌停 {ld_all} 只——黑天鹅事件，"
                        f"模拟盘已暂停买入信号，现金为王"})
    except Exception:
        pass

    to_push = [c for c in candidates if _can_push(today, c["key"])]
    if not to_push:
        return {"checked": True, "triggered": len(candidates), "pushed": 0}

    body = "\n\n".join(f"{c['sev']} {c['text']}" for c in to_push)
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched(
            "先知雷达·盘中风险警示",
            body + "\n\n> 盘中快照警示（每类每日一次）。收盘 15:35 全量扫描为准。",
            force=True)
        for c in to_push:
            _mark_pushed(today, c["key"])
        print(f"[intraday_alert] 盘中风险警示推送 {len(to_push)} 条: "
              f"{[c['key'] for c in to_push]}")
    except Exception as e:
        print(f"[intraday_alert] 推送失败: {e}")
        return {"checked": True, "triggered": len(to_push), "pushed": 0,
                "error": str(e)[:120]}
    return {"checked": True, "triggered": len(to_push), "pushed": len(to_push)}
