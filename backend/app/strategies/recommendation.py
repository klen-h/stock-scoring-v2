"""
================================================================================
【文件作用】战法买入信号推送：白名单筛选 + 有说服力的买入理由生成
================================================================================

设计原则：
  1. 白名单推送：只推「胜率达标」的战法，避免把低胜率战法噪音推给用户。
     名单基于「战法 × regime 分层回测」（backtest.run --strategy regime_warfare）筛选：
     样本 ≥ 30 且 胜率 ≥ 55%。数据积累后可改为自动刷新。
  2. 理由必须有说服力：把「形态逻辑 + 目标价推导 + 止损依据 + 历史胜率 + 市场匹配」
     讲清楚，让用户知道为什么买、目标价怎么来的、破位止损依据是什么，
     而不是只丢三个数字。

使用：
  from app.strategies.recommendation import get_push_whitelist, build_buy_reason
================================================================================
"""

# ── 推送白名单 ──
# 当前（2026-08-28 分层回测数据）：仅单阳不破 60.7%（56 样本）达标
# （样本≥30 且 胜率≥55%）。其他战法：龙回头 13.2%/38、均线回踩 45.5%/11、
# 均线粘合突破 37.5%/8 —— 均不满足。
# ★ 数据积累后建议自动刷新：每次分层回测后，用样本≥30 且 胜率≥55% 覆盖此名单。
PUSH_STRATEGY_WHITELIST = ["single_yang_unbroken"]

# 战法中文名（推送标题用）
STRATEGY_ZH = {
    "single_yang_unbroken": "单阳不破", "advance2retreat1": "进二退一",
    "ma_pullback": "均线回踩", "old_duck_head": "老鸭头",
    "ma_convergence_breakout": "均线粘合突破", "morning_star": "早晨之星",
    "wizard_pointer": "仙人指路", "double_cannon": "双响炮",
    "limit_up_boomerang": "涨停回马枪", "dragon_turnaround": "龙回头",
}

# 战法历史表现背书（人工同步自最新分层回测 backtest_warfare_by_regime）
# key = 战法英文名，value = {win_rate: 胜率%, n: 样本, regime: 优势市场}
STRATEGY_STATS = {
    "single_yang_unbroken": {"win_rate": 60.7, "n": 56, "regime": "震荡市"},
}


def get_push_whitelist() -> list:
    """返回当前可推送的战法英文名列表。"""
    return list(PUSH_STRATEGY_WHITELIST)


def format_signal_message(strategy_en: str, signal: dict, market: dict = None) -> str:
    """
    把单条战法信号格式化成企微 markdown 消息（含买入理由，目标/止损带推导）。
    供 scheduler 盘后扫描落库后调用推送。
    """
    name = signal.get("name") or signal.get("code")
    code = signal.get("code")
    entry = float(signal.get("entry_price") or 0)
    stop = float(signal.get("stop_loss") or 0)
    target = float(signal.get("target_price") or 0)
    conf = signal.get("confidence_level") or "unknown"
    conf_map = {"high": "高", "medium": "中", "low": "低"}
    zh = STRATEGY_ZH.get(strategy_en, strategy_en)

    upside = (target - entry) / entry * 100 if entry > 0 and target > 0 else 0
    risk = (entry - stop) / entry * 100 if entry > 0 and stop > 0 else 0

    reason = build_buy_reason(strategy_en, signal, market)
    reason_lines = [f"- {seg.strip()}" for seg in reason.split("；") if seg.strip()]
    sig_date = signal.get("signal_date") or ""
    sig_date = f"{sig_date}收盘" if sig_date else "信号日收盘"
    lines = [
        f"## 🎯 战法买入信号 · {zh}",
        f"📈 **{name}({code})**  参考介入 **{entry:.2f}**（{sig_date}）",
        f"🎯 目标 **{target:.2f}**（{upside:+.1f}%）｜止损 **{stop:.2f}**（-{abs(risk):.1f}%）",
        "",
        "📊 **买入逻辑：**",
        *reason_lines,
        "",
        "📌 **执行提示：** 次日开盘按实际价介入，参考价仅作锚点；高开过多（>3%）建议放弃或减仓；止损以形态位为准",
        "",
        f"⭐ 置信度：{conf_map.get(conf, conf)}",
    ]
    return "\n".join(lines)


def build_open_confirmation(signal: dict, quote: dict, vol_ratio: float = None) -> str:
    """
    生成次日开盘买点确认消息（把「低开买点规律」变成可执行指引）。

    判定维度：
      - 价格维度：实时开盘价 vs 参考介入价 vs 止损位
      - 量能维度：量比 vol_ratio（防诱多）——实时量(手) / (昨日量 × 已交易分钟/240)
        ≥1.5 显著放量（资金承接/真突破），<0.6 明显缩量（无量=不可信/可能诱多）

    判定规则：
      - 开盘 ≤ 止损位      → 放弃（形态破坏，论点失效）
      - 低开 >3% 未破位    → 回踩企稳再买（放量承接更佳）
      - 低开 0~3%          → 低吸买点（放量承接最佳；缩量低开→先观望防阴跌）
      - 平开/高开 ≤3%      → 正常买点（缩量高开→⚠️防诱多，谨慎追高）
      - 高开 >3%           → 放弃/减仓（成本抬高、盈亏比变差）
    vol_ratio 为 None（数据缺失）时跳过量能维度，只按价格判定。

    参数：
      signal: 战法扫描信号（需含 _strategy 等上下文字段）
      quote:  腾讯实时行情 dict（open 今开 / price 现价 / volume 手）
      vol_ratio: 量比（可选）
    返回企微 markdown；数据缺失时返回空字符串。
    """
    entry = float(signal.get("entry_price") or 0)
    stop = float(signal.get("stop_loss") or 0)
    target = float(signal.get("target_price") or 0)
    open_p = float(quote.get("open") or 0)
    price = float(quote.get("price") or 0)
    if entry <= 0 or open_p <= 0:
        return ""
    strategy_en = signal.get("_strategy") or ""
    zh = STRATEGY_ZH.get(strategy_en, strategy_en)
    name = signal.get("name") or signal.get("code")
    code = signal.get("code")
    dev = (open_p - entry) / entry * 100

    hot = vol_ratio is not None and vol_ratio >= 1.5
    cold = vol_ratio is not None and vol_ratio < 0.6

    if open_p <= stop:
        action = "🚫 **放弃**：开盘已跌破止损位，形态破坏，不介入"
    elif dev < -3:
        if hot:
            action = ("🔍 **回踩企稳再买**：低开较深但**放量承接**，等不破止损位企稳"
                      "可低吸，破位则放弃")
        else:
            action = ("🔍 **回踩企稳再买**：低开较深但未破位，等不破止损位/突破位企稳"
                      "可挂单低吸，破位则放弃")
    elif dev < 0:
        if cold:
            action = "🔍 **缩量低开，先观望**：无资金承接，可能阴跌，等放量企稳再介入"
        elif hot:
            action = "✅ **低吸买点**：低开**放量承接**，比参考价更优，可开盘直接买入"
        else:
            action = "✅ **低吸买点**：低开不破位，比参考价更优，可开盘直接买入"
    elif dev <= 3:
        if cold:
            action = "⚠️ **谨慎追高（防诱多）**：高开/平开但**缩量**，无量上涨不可信，建议等放量确认"
        else:
            action = "✅ **正常买点**：开盘接近参考价，可按计划买入"
    else:
        action = "⚠️ **高开偏多**：成本抬高、盈亏比变差，建议放弃或减仓"

    vol_str = f"｜量比 {vol_ratio:.1f}" if vol_ratio is not None else ""
    return (
        f"## 📌 开盘买点确认 · {zh}\n"
        f"📈 **{name}({code})**  今开 **{open_p:.2f}**（相对参考 {dev:+.1f}%）｜现价 {price:.2f}{vol_str}\n"
        f"🎯 目标 {target:.2f}｜止损 {stop:.2f}\n\n"
        f"{action}"
    )


def build_buy_reason(strategy_name: str, signal: dict, market: dict = None) -> str:
    """
    生成有说服力的买入理由。

    参数：
      strategy_name: 战法英文名
      signal: 战法扫描信号（含 entry_price/stop_loss/target_price/details/confidence_level）
      market: detect_market_regime() 结果（可选，提供市场环境背书）

    返回：一段中文买入理由（含形态逻辑 + 目标推导 + 止损依据 + 市场匹配）。
    """
    details = signal.get("details") or {}
    entry = float(signal.get("entry_price") or 0)
    stop = float(signal.get("stop_loss") or 0)
    target = float(signal.get("target_price") or 0)

    parts = []

    # ① 形态逻辑（战法专用话术，讲清楚"为什么现在买"）
    logic = _strategy_logic(strategy_name, details)
    if logic:
        parts.append(logic)

    # ② 目标价推导（讲清楚"目标价怎么来的"，让目标有依据）
    if entry > 0 and target > 0:
        upside = (target - entry) / entry * 100
        derivation = _target_derivation(strategy_name, details, entry, target)
        parts.append(f"目标 {target:.2f}（{upside:+.1f}%空间）：{derivation}")

    # ③ 止损依据（讲清楚"跌破哪里就是逻辑失效"）
    if entry > 0 and stop > 0:
        risk = (entry - stop) / entry * 100
        parts.append(f"止损 {stop:.2f}（约{risk:.1f}%风险）：跌破形态关键位即逻辑失效，果断离场")

    # ④ 市场环境背书（当前市场与战法的匹配度）
    if market:
        parts.append(_market_backing(strategy_name, market))

    return "；".join(p for p in parts if p)


# ──────────────────────────────────────────────────────────────
#  各战法形态话术
# ──────────────────────────────────────────────────────────────

def _strategy_logic(strategy_name: str, d: dict) -> str:
    """各战法形态逻辑话术。返回 "" 表示无专门话术（用通用话术兜底）。"""
    zh = {"single_yang_unbroken": "单阳不破", "advance2retreat1": "进二退一",
          "ma_pullback": "均线回踩", "old_duck_head": "老鸭头",
          "ma_convergence_breakout": "均线粘合突破", "morning_star": "早晨之星",
          "wizard_pointer": "仙人指路", "double_cannon": "双响炮",
          "limit_up_boomerang": "涨停回马枪", "dragon_turnaround": "龙回头"}.get(strategy_name, strategy_name)

    if strategy_name == "single_yang_unbroken":
        sy = d.get("single_yang") or {}
        co = d.get("consolidation") or {}
        br = d.get("breakout") or {}
        seg = []
        if sy:
            seg.append(f"{sy.get('date')}放量大阳 +{sy.get('change_pct')}%（量比{sy.get('volume_ratio')}）")
        if co:
            golden = "黄金整理期" if co.get("is_golden_window") else "缩量整理"
            seg.append(f"随后{co.get('days')}日{golden}不破大阳低点（末期量比{co.get('volume_ratio')}）")
        if br:
            seg.append(f"{br.get('date')}放量突破整理上沿（+{br.get('change_pct')}%，量比{br.get('volume_ratio')}）")
        if seg:
            return "【单阳不破】放量大阳确立底部，缩量横盘蓄势，放量突破即启动——" + " → ".join(seg)
    elif strategy_name == "advance2retreat1":
        d1, d2, d3 = d.get("day1") or {}, d.get("day2") or {}, d.get("day3") or {}
        return (f"【进二退一】连涨两天（Day1 +{d1.get('change_pct')}%、Day2 +{d2.get('change_pct')}%）"
                f"后缩量回调（Day3 量比{d3.get('volume_ratio')}），回踩不破再启动")
    elif strategy_name == "ma_pullback":
        return "【均线回踩】上升趋势中缩量回踩均线不破，今日放量反弹，回踩低吸"
    elif strategy_name == "old_duck_head":
        return "【老鸭头】均线多头排列后缩量回调不破60日线，金叉放量启动主升"
    elif strategy_name == "ma_convergence_breakout":
        return "【均线粘合突破】多均线粘合横盘蓄势，放量突破平台上沿"
    elif strategy_name == "morning_star":
        return "【早晨之星】下跌末期长阴+跳空星线+放量中阳，底部反转信号"
    elif strategy_name == "wizard_pointer":
        return "【仙人指路】拉升途中长上影试盘，次日高开高走确认"
    elif strategy_name == "double_cannon":
        return "【双响炮】首板涨停确立，缩量回调后再度涨停启动主升"
    elif strategy_name == "limit_up_boomerang":
        return "【涨停回马枪】涨停突破后缩量回调不破实体，放量反包再攻"
    elif strategy_name == "dragon_turnaround":
        return "【龙回头】龙头第一波拉升后缩量回调，止跌反包时介入"
    return f"【{zh}】形态触发买入信号"


def _target_derivation(strategy_name: str, d: dict, entry: float, target: float) -> str:
    """目标价推导依据（说明目标怎么来的）。"""
    if strategy_name == "single_yang_unbroken":
        sy = d.get("single_yang") or {}
        return "介入价 + 单阳线高度（一倍量度），不足 10% 以 +10% 保底"
    if strategy_name == "ma_pullback":
        return "固定目标 +12%（均线回踩的温和反弹空间）"
    if strategy_name == "old_duck_head":
        return "固定目标 +20%（主升浪空间）"
    if strategy_name == "limit_up_boomerang":
        return "固定目标 +20%（涨停股惯性空间）"
    if strategy_name == "wizard_pointer":
        return "固定目标 +15%（确认后空间）"
    if strategy_name == "morning_star":
        return "固定目标 +15%（反转修复空间）"
    if strategy_name == "double_cannon":
        return "介入价 + 两炮间距（一倍量度），不足 15% 以 +15% 保底"
    if strategy_name == "dragon_turnaround":
        return "前高 ×0.98 与介入价 +15% 取较大值"
    if strategy_name == "advance2retreat1":
        return "Day2 高点 ×1.05，不足 10% 以 +10% 保底"
    if strategy_name == "ma_convergence_breakout":
        return "介入价 + 平台高度（一倍量度），不足 10% 以 +10% 保底"
    return "形态量度推导的目标空间"


def _market_backing(strategy_name: str, market: dict) -> str:
    """市场环境背书：当前状态 + 战法优势市场 + 历史胜率。"""
    regime = market.get("regime")
    vol = market.get("volatility_regime")
    regime_desc = {"offensive": "进攻市", "neutral": "震荡市", "defensive": "防御市"}.get(regime, regime)
    vol_desc = {"high": "高波动", "normal": "波动正常", "low": "低波动"}.get(vol, vol)

    stat = STRATEGY_STATS.get(strategy_name)
    backing = []
    if stat:
        backing.append(f"历史胜率 {stat['win_rate']}%（样本{stat['n']}，{stat['regime']}）")
    backing.append(f"当前市场 {regime_desc}（{vol_desc}）")
    if stat and regime == {"single_yang_unbroken": "neutral",
                           "advance2retreat1": "offensive"}.get(strategy_name):
        backing.append("环境匹配，胜率背书有效")
    elif stat:
        backing.append("注意市场状态切换，胜率背书可能变化")
    return "市场匹配：" + "；".join(backing)
