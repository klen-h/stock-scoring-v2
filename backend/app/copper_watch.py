# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】铜裁决器 —— **纯观察器**
================================================================================
【它回答什么问题】
  油价跌的时候，市场在讲两个完全相反的故事：

    D1 供给驱动：地缘缓和 / 增产 ⇒ 油价跌，但**铜企稳或上涨**
       （需求没坏，只是供给多了）⇒ 利好成本敏感的制造业/科技
    D2 衰退驱动：需求崩塌 ⇒ 油价跌，**铜同步暴跌**
       （连带 VIX 飙升、金银比走高）⇒ 全面防御

  **单看油价分不出来**，能分开它们的正是铜。
  所以铜不是"又一个指标"，是**裁决者**。

【与既有代码的关系（重要，避免重复实现）】
  本项目 `flash/llm.py` 的 prompt 里**早就有**这套 D1/D2 判据
  （见「2.2 原油-黄金相关性诊断」的 D1/D2 定义），但那是**写给 LLM 看的**，
  系统本身没有可核对的确定性输出 —— 也就是说：
    · 同一个油价下跌，LLM 可能今天判 D1、明天判 D2；
    · 没有任何地方能让人一眼看到"铜这边到底站哪边"。
  ⇒ 本模块把那段判据**显式化**，输出确定性的 `verdict`。

【★★ 定位：纯观察器（与 `cross_market_watch` / `tell_watch` 同纪律）】
  · **不改分数、不进闸门、不改 regime 四态、不碰模拟盘**；
  · **不喂** `market_regime._external_panic()`（那条链会连锁改评分权重）；
  · 也**不替代** LLM 的 D1/D2 判断 —— 它是"事实层"，LLM 是"解释层"。

【口径】
  · 美铜 `copper`（**美元/磅**，`macro.py` 已把美分/磅归一化）
  · 伦铜 `lme_copper`（**美元/吨**，LME 惯例）
  · `copper_premium_pct` = 美铜 − 伦铜（折同单位），正常 0~3%
  · 零新增外部请求：全部读 `get_macro_panel()` 的 60s 共享缓存。

【⚠️ 本模块的自知局限（必须如实披露）】
  判据用的是**单日涨跌**，噪声大：一天之内铜可能同时被"美元走强"和
  "库存下降"推动，方向不纯。⇒ `confidence` 恒为 `low`，
  只作**方向提示**，不作为"确认信号"。多日确认需要 `thesis_evidence`
  的铜序列，目前不齐，故不做——**宁可标 low，也不假装有把握**。
================================================================================
"""
import os
from typing import Dict, Optional

# ── 阈值（单日变化 %）────────────────────────────────────────────────────
OIL_MOVE_MIN = float(os.environ.get("CU_OIL_MOVE") or 2.0)      # 油跌幅达此值算"油跌事件"
COPPER_CRASH = float(os.environ.get("CU_CRASH") or 2.0)         # 铜跌幅达此值算"同步暴跌"
# ★★ 2026-11-16：**改成相对判据** —— 铜跌幅不到油跌幅的该比例，就算"扛住"。
#   【为什么不用绝对带 ±0.5%】铜的日常波动就有 ±1%，±0.5% 太窄：
#     实测今天铜 -0.63% / 油 -2.44%，铜明显扛住，却会被绝对带判成"mixed"。
#     "企稳"的本质是**相对油的跌幅**，不是绝对不动。
COPPER_HOLD_RATIO = float(os.environ.get("CU_HOLD_RATIO") or 0.5)
PREMIUM_ALERT = float(os.environ.get("CU_PREMIUM_ALERT") or 5.0)  # 美铜溢价告警线

VERDICT_LABEL = {
    "supply": "供给驱动（D1）",
    "demand": "需求驱动（D2）",
    "reflation": "再通胀",
    "divergent": "油涨铜跌",
    "mixed": "方向待确认",
    "neutral": "无明显信号",
    "insufficient": "数据不足",
}


def _num(v) -> Optional[float]:
    """安全取数：None / '' / NaN / 非数字 ⇒ None（**不回落成 0**）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def judge(copper_chg: Optional[float], oil_chg: Optional[float]) -> str:
    """**纯函数**：铜/油当日涨跌 → 驱动类型。

    判据与 `flash/llm.py` prompt 里的 D1/D2 定义保持一致：
      · 油跌 + 铜同步暴跌（≤-2%）      ⇒ `demand`（D2，需求在崩，**优先判**）
      · 油跌 + 铜跌幅不到油的一半      ⇒ `supply`（D1，需求没坏）
      · 油跌 + 铜跌但超过油的一半      ⇒ `mixed`（不硬判）
      · 油涨 + 铜涨                    ⇒ `reflation`
      · 油涨 + 铜明显不跟              ⇒ `divergent`（成本上行、需求不跟）
      · 其余                          ⇒ `neutral`
    缺任一端 ⇒ `insufficient`（**缺失 ≠ 无信号**）。
    """
    c, o = _num(copper_chg), _num(oil_chg)
    if c is None or o is None:
        return "insufficient"
    if o <= -OIL_MOVE_MIN:                       # 油明显跌
        # 先判"暴跌"（D2 优先）：需求真崩时不该被"相对扛住"覆盖
        if c <= -COPPER_CRASH:
            return "demand"
        # 铜跌幅不到油的一半 ⇒ 需求没跟着坏 ⇒ 供给端驱动
        if c > o * COPPER_HOLD_RATIO:
            return "supply"
        return "mixed"
    if o >= OIL_MOVE_MIN:                        # 油明显涨
        if c >= COPPER_CRASH:
            return "reflation"
        if c <= o * COPPER_HOLD_RATIO:           # 油涨而铜明显不跟
            return "divergent"
        return "mixed"
    return "neutral"                             # 油没大动 ⇒ 铜单独走，不构成裁决


def snapshot() -> Dict:
    """铜裁决快照（纯读取，零新增请求）。"""
    try:
        from app.macro import get_macro_panel
        panel = get_macro_panel() or {}
    except Exception as e:
        print(f"[copper-watch] macro panel failed: {str(e)[:80]}")
        panel = {}
    d = panel.get("_derived") or {}

    us = panel.get("copper") or {}
    lme = panel.get("lme_copper") or {}
    oil = panel.get("brent") or {}

    cu_chg = _num(us.get("change_pct"))
    lme_chg = _num(lme.get("change_pct"))
    oil_chg = _num(oil.get("change_pct"))
    verdict = judge(cu_chg, oil_chg)

    signals = []
    if cu_chg is not None:
        signals.append("美铜 %+.2f%%" % cu_chg)
    if lme_chg is not None:
        signals.append("伦铜 %+.2f%%" % lme_chg)
    if oil_chg is not None:
        signals.append("布伦特 %+.2f%%" % oil_chg)
    if d.get("copper_oil_ratio") is not None:
        signals.append("铜油比 %.4f" % d["copper_oil_ratio"])
    prem = _num(d.get("copper_premium_pct"))
    if prem is not None:
        signals.append("美铜溢价 %+.2f%%" % prem)

    # ── 一句话理由（只陈述事实与判据，不延伸成建议）──
    if verdict == "supply":
        reason = ("油跌 %+.1f%% 而铜 %+.1f%% 企稳 ⇒ 按 D1 口径属**供给端**驱动"
                  "（需求未坏，跌的是成本）" % (oil_chg or 0, cu_chg or 0))
    elif verdict == "demand":
        reason = ("油跌 %+.1f%% 且铜同步跌 %+.1f%% ⇒ 按 D2 口径属**需求端**崩塌"
                  % (oil_chg or 0, cu_chg or 0))
    elif verdict == "reflation":
        reason = ("油铜同涨（%+.1f%% / %+.1f%%）⇒ 需求扩张与供给偏紧并存"
                  % (oil_chg or 0, cu_chg or 0))
    elif verdict == "divergent":
        reason = ("油涨 %+.1f%% 但铜跌 %+.1f%% ⇒ 成本在上、需求不跟（偏滞胀形态）"
                  % (oil_chg or 0, cu_chg or 0))
    elif verdict == "mixed":
        reason = ("油 %+.1f%% / 铜 %+.1f%% 落在判定带中间 ⇒ 不硬判方向"
                  % (oil_chg or 0, cu_chg or 0))
    elif verdict == "insufficient":
        reason = "铜或油价数据缺失 ⇒ 缺失不等于无信号，不作判断"
    else:
        reason = "油价未出现显著异动 ⇒ 铜的方向不构成裁决"

    # 溢价异常单独提示：这是"美铜能不能代表全球需求"的前提
    if prem is not None and abs(prem) >= PREMIUM_ALERT:
        reason += ("；⚠️ 美铜溢价 %+.2f%% 已超 %.0f%%，美铜的涨跌含关税/库存成分，"
                   "**不可直接当全球需求读**" % (prem, PREMIUM_ALERT))

    return {
        "ok": True,
        "as_of": str(oil.get("time") or us.get("time") or "") or "",
        "verdict": verdict,
        "verdict_label": VERDICT_LABEL.get(verdict, verdict),
        "confidence": "low" if verdict not in ("insufficient", "neutral") else "none",
        "reason": reason,
        "copper": {
            "us": {"price": us.get("price"), "chg": cu_chg},
            "lme": {"price": lme.get("price"), "chg": lme_chg},
            "premium_pct": prem,
        },
        "oil": {"price": oil.get("price"), "chg": oil_chg},
        "ratios": {
            "copper_oil": d.get("copper_oil_ratio"),
            "copper_gold": d.get("copper_gold_ratio"),
        },
        "signals": signals,
        "thresholds": {
            "oil_move_min": OIL_MOVE_MIN,
            "copper_crash": COPPER_CRASH,
            "copper_hold_ratio": COPPER_HOLD_RATIO,
            "premium_alert": PREMIUM_ALERT,
        },
        "note": ("纯观察器：不进决策链、不改分数。判据为**单日**涨跌 ⇒ "
                 "confidence 恒为 low，只作方向提示；与 LLM 的 D1/D2 解释层互补而非替代。"),
    }
