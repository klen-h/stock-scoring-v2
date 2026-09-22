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
  2. 指数冲高回落      **日内路径**口径（2026-09-23 新增，见 `_REVERSAL_WATCH`）：
                       日内最高涨幅 ≥ 门槛 **且** 现价距日高 ≤ -门槛
                       （上证 +0.8%/-1.5%；创业板指、科创50 +1.5%/-2.5%）。
                       ★ 为什么单列一条：规则 1 只看**现价 vs 昨收**（绝对涨跌幅）⇒
                         『早盘冲高 +1.2%、午后回落到 -0.5%』的日子**完全静默**
                         （用户 2026-09-23 报的场景），而『攻势没守住』的信息全在
                         **从日内高点的回撤**里。
                       ★ 唯一带 LLM 解读的规则：触发时附一段解读（大盘状态序列 +
                         未兑现矛盾 + 用户持仓 ⇒ 回答『回踩还是反弹失败 / 印证了哪条
                         疑虑 / 持仓怎么办』）；LLM 不可用则**降级为模板文案**。
  3. 涨跌比极值        涨跌比 < 0.25（跌停潮式结构恶化）
  4. 跌停家数激增      跌停（跌幅 ≤ -9.7%）家数 ≥ 30

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
  - ★ 2026-09-23：冲高回落**同样**受"每类每档每日一次"约束（key=`idxreversal:{指数名}`）
    ⇒ LLM 解读调用上限 = 3 指数 × 2 档 = 6 次/日，实际远低于此（一天极少多点同时冲高回落）

调度：intraday_alert_loop 交易时段每 **3 分钟**检查一次（9:40-11:30 / 13:00-15:00）。
      ★ 2026-09-19：30 → 10 → 3 分钟（时效优先）。每轮发 **3 个腾讯指数请求**
      （上证 / 创业板指 / 科创50 各 1 个；`get_index` **无缓存层** ⇒ 间隔就是真实请求
      频率），其余判定全读内存快照 ⇒ ≈**230 请求/交易日**，相对每 5 分钟的**全市场刷新**
      （~4000 只）仍 <1%。全局防骚扰门限 5 分钟。
      ★ 2026-09-23：新增冲高回落规则**复用同一批行情**（`_index_watch_quotes()` 拉一次
      喂两条规则）⇒ 请求量**不增加**，仍是每轮 3 个。
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


# ★ 2026-09-23：『冲高回落』（日内**路径**）专属门槛 —— 与上面的『急跌』互补。
#   为什么需要（用户 2026-09-23 报『今天指数冲高回落，但系统没有声音』）：
#     `_INDEX_WATCH` 只看**现价 vs 昨收**（绝对涨跌幅）⇒ 早盘冲高 +1.2%、随后回落到
#     -0.5% 的日子**完全不触发**（离 -1.5% 差得远），而『多头攻势没守住』的信息全在
#     **从日内高点的回撤**里 —— 现有规则没有这个维度。配套的
#     `llm.format_a_share_context` 恰好也提供『收盘距当日最高』（同为 9-23 新增），
#     故本条规则既是告警、也是 LLM 解读（见 `_reversal_llm_note`）的天然触发点。
#   门槛依据：必须**同时**满足『确实冲过高』(peak_need) 与『确实回得深』(drop_need)，
#     缺一不可 ⇒ 避免把『低位窄幅震荡』（从没涨过）误报成回落。
#     创业板指 / 科创50 日常波动约 2 倍于上证 ⇒ 门槛按倍数加严（与 `_INDEX_WATCH` 同思路）。
#   ★ 阈值频率实测（2026-09-23，`backtest_prices` sh000300 近 120 交易日）：
#     先看『冲高』前提：日内曾涨 ≥+0.8% 的有 **43/120 天**（36%）；
#     其中 **收盘**距日高 ≤-1.5% 的只有 **1 天**，而 **盘中最低**距日高 ≤-1.5% 的有 **24 天**。
#     ⇒ ① 叠加『每类每档每日一次』后约 **每月 2~4 条**（不会天天响，阈值可信）；
#        ② 24 天里 23 天尾盘收复 ⇒ **只在收盘看会几乎全部错过** —— 这正是本条规则
#           （盘中实时 + 3 分钟轮询）而非盘后扫描的存在理由。
_REVERSAL_WATCH = {
    # code: (展示名, 冲高门槛%（日内最高需涨到）, 回撤门槛%（现价距日高需跌到）)
    "000001": ("上证指数", 0.8, -1.5),
    "399006": ("创业板指", 1.5, -2.5),
    "000688": ("科创50", 1.5, -2.5),
}


def _index_watch_quotes() -> List[tuple]:
    """一次性拉取 `_INDEX_WATCH` 全部指数行情（**两个规则共享**）。

    ★ 为什么要共享：`get_index()` **无缓存层**（每次都是真实 HTTP 请求）。若『急跌』
      与『冲高回落』各拉一遍 ⇒ 每轮 6 个请求（原 3 个翻倍）。这里拉一次喂两条规则，
      请求量维持原状（3 分钟一轮 ≈ 230 请求/交易日，占全市场刷新 <1%）。
    """
    from app.tencent import get_index
    out: List[tuple] = []
    for code, name, warn, severe in _INDEX_WATCH:
        try:
            q = get_index(code) or {}
        except Exception as e:
            print(f"[intraday_alert] {name} 行情获取失败: {e}")
            q = {}
        out.append((code, name, warn, severe, q))
    return out


def _index_drop_alert(quotes) -> List[Dict]:
    """主要指数单边急跌（**绝对涨跌幅**口径）。每个指数各自阈值，避免高波动指数天天报警。

    quotes: `_index_watch_quotes()` 的输出（共享行情；不再各自请求，见其注释）。
    返回列表（可能命中多个指数）；de-dup key 带指数名，互不覆盖。
    """
    out: List[Dict] = []
    for code, name, warn, severe, q in quotes:
        try:
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


def _index_reversal_alert(quotes) -> List[Dict]:
    """指数『冲高回落』（**日内路径**口径，2026-09-23 新增）。

    与 `_index_drop_alert` 的区别（这是本规则存在的全部理由）：
      · 急跌 = 现价 vs **昨收**（今天整体跌了多少）
      · 冲高回落 = 日内**最高点涨了多少** + 现价**从最高点回撤了多少**
    ⇒ 早盘 +1.2% 午后回落到 -0.5% 的日子，前者静默、后者报警。

    命中项带 `_facts` 结构化事实（供 `_reversal_llm_note` 生成解读；模板降级路径不依赖它）。
    """
    out: List[Dict] = []
    for code, name, _warn, _severe, q in quotes:
        spec = _REVERSAL_WATCH.get(code)
        if not spec or not q:
            continue
        try:
            _, peak_need, drop_need = spec
            prev = q.get("prev_close") or 0
            high = q.get("high") or 0
            price = q.get("price") or 0
            if prev <= 0 or high <= 0 or price <= 0:
                continue
            peak = (high - prev) / prev * 100      # 日内最高涨幅（冲高度）
            drop = (price - high) / high * 100     # 现价距日内最高（回撤，负值）
            if peak < peak_need or drop > drop_need:
                continue
            now_pct = q.get("change_pct") or 0
            # 红灯：回撤超过门槛 1.5 倍（如上证 -2.25%）⇒ 攻势基本瓦解
            sev = "🔴" if drop <= drop_need * 1.5 else "🟡"
            out.append({
                "key": f"idxreversal:{name}", "sev": sev,
                "text": (f"**{name}** 冲高回落：日内最高 {peak:+.2f}%（{high}）"
                         f"→ 现价 {now_pct:+.2f}%，**距日高 {drop:.2f}%** —— "
                         f"日内买盘被消化、攻势未能守住"),
                "_facts": {"name": name, "code": code, "prev_close": prev,
                           "high": high, "price": price,
                           "peak_pct": round(peak, 2),
                           "drop_from_high": round(drop, 2),
                           "now_pct": round(now_pct, 2)},
            })
        except Exception as e:
            print(f"[intraday_alert] {name} 冲高回落检查失败: {e}")
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


def _open_contradictions_text(limit: int = 6) -> str:
    """今日/最新扫描出的**未兑现疑虑**（供 LLM 做『印证 / 证伪』判断）。

    用户思路（2026-09-23）：把每天积累的矛盾扫描当作**假设**，盘中指数行为当作
    **印证条件** ⇒ 触发时才问『这次盘面印证了哪一条』，而不是凭空让 LLM 猜。
    """
    try:
        from app.contradictions.store import load_contradictions
        items = load_contradictions(resolved=0) or []
        if not items:
            return ""
        # 优先给 severe/obvious（真正值得印证的），不够再回填 minor
        top = [x for x in items if x.get("severity") in ("severe", "obvious")]
        picked = (top or items)[:limit]
        lines = [f"- [{x.get('severity')}] {x.get('title')}："
                 f"{(x.get('summary') or '')[:110]}" for x in picked]
        return "## 系统已识别但尚未兑现的疑虑（矛盾扫描）\n" + "\n".join(lines) + "\n"
    except Exception as e:
        print(f"[intraday_alert] 嫌疑矛盾读取失败（跳过）: {e}")
        return ""


def _reversal_llm_note(facts_list: List[Dict]) -> str:
    """『冲高回落』的 LLM 解读（2026-09-23 新增）；失败/空返回 "" ⇒ 调用方降级为模板。

    为什么用 LLM 而不是再加一条模板：模板能说『跌了多少』，但回答不了用户真正的问题
    —— 『这次回落是涨势中的回踩，还是弱势反弹失败？印证了哪个已有疑虑？对我的持仓
    意味着什么？』。而这三问的**素材系统全都有**（大盘状态序列 / 矛盾扫描 / 持仓），
    拼起来即可 ⇒ 边际成本只有一次 LLM 调用（每指数每日至多一次）。

    ★ 时延与降级：`tier="fast"`（时间敏感）；实测免费站通常 10~30s 返回。调用方在
      `asyncio.to_thread` 里执行 ⇒ 不阻塞事件循环。LLM 不可用时**一定有声音**
      （降级模板文案），不会因为 LLM 故障而漏掉告警。
    """
    try:
        from app.flash.llm import call_llm, format_user_holdings, format_a_share_context
        from app.flash.rules import beijing_now

        fact_lines = []
        for f in facts_list:
            fact_lines.append(
                f"- {f['name']}：昨收 {f['prev_close']}｜日内最高 {f['high']}"
                f"（盘中曾涨 {f['peak_pct']:+.2f}%）｜现价 {f['price']}"
                f"（今 {f['now_pct']:+.2f}%）｜**距日内最高 {f['drop_from_high']:.2f}%**")

        a_ctx = ""
        try:
            a_ctx = format_a_share_context(5) or ""
        except Exception as e:
            print(f"[intraday_alert] A股状态获取失败（跳过）: {e}")

        holdings = ""
        try:
            holdings = format_user_holdings() or ""
        except Exception as e:
            print(f"[intraday_alert] 持仓读取失败（跳过）: {e}")

        system = (
            "你是 A 股盘中异动的解读助手，服务于一个量化评分系统的使用者。"
            "结论先行、不复述数据、不说空话；证据不足时明确说『暂无定论』而不是编造因果。")
        user = (
            f"## 触发事实（{beijing_now().strftime('%H:%M')} 盘中实时）\n"
            + "\n".join(fact_lines) + "\n\n"
            + a_ctx + _open_contradictions_text() + holdings +
            "\n## 请输出（markdown，总长不超过 5 句）\n"
            "1. 这次『冲高回落』说明什么？结合上方市场状态序列判断：是涨势中的回踩，"
            "还是弱势反弹失败 / 高位滞涨？\n"
            "2. 与上方『尚未兑现的疑虑』的关系：**印证 / 证伪 / 无关**，并指名具体哪一条"
            "（没有相关项就直接说无关，不要硬凑）\n"
            "3. 对用户持仓的含义：只点名最相关的 1~2 只（不逐一点评），说明是提醒风险"
            "还是无需动作\n"
            "4. 一句话操作提示（不追高 / 不加仓 / 等尾盘确认 / 可继续持有…）")
        txt = call_llm(system, user, temperature=0.3, tier="fast")
        if not (txt or "").strip():
            # ★ 2026-09-23 实测：免费站有**偶发空响应**（同 prompt 连续两次调用，一次返回空）。
            #   这里重试一次 —— 成本极低（每指数每日至多一次调用），而盘中『有解读』
            #   比『省一次调用』重要得多；仍失败则由调用方降级为模板文案。
            print("[intraday_alert] LLM 解读空响应，重试一次")
            txt = call_llm(system, user, temperature=0.3, tier="fast")
        return (txt or "").strip()
    except Exception as e:
        print(f"[intraday_alert] 冲高回落 LLM 解读失败（降级模板）: {e}")
        return ""


def check_risk_alerts() -> dict:
    """
    盘中风险警示检查（调度器每 30 分钟调用一次，仅交易时段）。
    触发的警示合并为一条企微消息（每类每日最多一次）。
    """
    # ★ 「每类每日一次」的去重 key 也必须是北京时间（跨日窗口下 UTC 会差一天）
    today = beijing_now().strftime("%Y-%m-%d")
    if not _trading_session():
        return {"checked": False, "reason": "非交易时段"}

    quotes = _index_watch_quotes()               # ★ 一次拉取，两条指数规则共享（不增请求）
    candidates = list(_index_drop_alert(quotes))  # ★ 绝对涨跌幅：急跌
    candidates.extend(_breadth_alerts())
    # ★ 2026-09-23：日内**路径**形态（冲高回落）—— 与急跌互斥互补：
    #   今天这种『早盘 +1.2% → 回落』的日子只有它能报警（用户报的场景）。
    candidates.extend(_index_reversal_alert(quotes))
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

    # ★ 2026-09-23：形态类（冲高回落）先请 LLM 做一次解读 —— 要回答的是『这次回落是
    #   涨势回踩还是反弹失败 / 印证了哪条已有疑虑 / 对我的持仓意味着什么』，
    #   模板文案回答不了。解读失败则**退回模板**（保证一定有声音，不因 LLM 故障漏告警）。
    rev = [c for c in to_push if c.get("_facts")]
    parts: List[str] = []
    note_ok = False
    if rev:
        note = _reversal_llm_note([c["_facts"] for c in rev])
        note_ok = bool(note)
        if note_ok:
            parts.append(note)
        else:
            parts.extend(f"{c['sev']} {c['text']}" for c in rev)
    # 模板类（急跌 / 涨跌比 / 跌停）保持原样
    parts.extend(f"{c['sev']} {c['text']}" for c in to_push if not c.get("_facts"))
    body = "\n\n".join(parts)

    title = "先知雷达·盘中异动解读" if note_ok else "先知雷达·盘中风险警示"
    tail = ("\n\n> 盘中快照（每类每日一次；**升级到更严重档会再提醒一次**）。"
            "收盘 15:35 全量扫描为准。")
    if note_ok:
        tail = ("\n\n> 形态由规则识别、解读由 LLM 基于［大盘状态序列 + 未兑现疑虑 + 你的持仓］"
                "生成，可能有误判；每类每日一次。" + "收盘 15:35 全量扫描为准。")
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched(title, body + tail, force=True, category="risk")
        for c in to_push:
            _mark_pushed(today, _dedup_key(c))
        print(f"[intraday_alert] 盘中警示推送 {len(to_push)} 条（LLM解读={'有' if note_ok else '无'}）: "
              f"{[c['key'] for c in to_push]}")
    except Exception as e:
        print(f"[intraday_alert] 推送失败: {e}")
        return {"checked": True, "triggered": len(to_push), "pushed": 0,
                "error": str(e)[:120]}
    return {"checked": True, "triggered": len(to_push), "pushed": len(to_push)}
