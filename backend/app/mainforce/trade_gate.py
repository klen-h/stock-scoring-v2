# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】买入三条件门禁 + 建议仓位（"跟随主力、大胆买入、有根据卖出"的规则化）
================================================================================

规则（来源：跟随主力操作指南，全部用已验证组件实现）：

  条件A【有根据】：主力吸筹区信号（低位筹码密集 × 主力净流入，回测 10 日 +1.1pt）
                   —— 个股层面的"根据"；指数级矛盾（L1/L2 当日有扫描结果）为加分项
  条件B【不追高】：筹码不拥挤（price_pos ≤0.75 且 winner_ratio ≤0.7，
                   等价于"近20日涨幅<20%、距年内高点>10%"的筹码化表述）
  条件C【状态允许】：regime ≠ defensive；neutral_bearish 只允许轻仓试探

  建议仓位（规则透明，非黑盒）：
    defensive                → 0%（禁止）
    neutral_bearish          → 20%（轻仓试探，只做波段）
    neutral                  → 50%
    offensive                → 80%
    筹码拥挤（B 不过）        → 上表结果 ×0.5（轻仓）
    条件A 不过（无主力根据）  → 上表结果 ×0.5（形态信号无主力背书）
    吸筹区 + 不拥挤 + 非防御  → 不降反升一档（80% 封顶）——"大胆买入"的量化定义

数据全部来自已落库组件：mainforce_state（筹码/信号）、market_regime 缓存（状态）、
contradictions（当日扫描）。零新增网络请求。
开关：TRADE_GATE=on（默认）/ off。
================================================================================
"""

from typing import Dict

from app.mainforce.gate import HIGH_POS_THRESHOLD

WINNER_RATIO_CROWDED = 0.7
REGIME_POSITION = {
    "offensive": 80,
    "neutral": 50,
    "neutral_bearish": 20,
    "defensive": 0,
}


def _mode_on() -> bool:
    import os
    return (os.environ.get("TRADE_GATE") or "on").strip().lower() == "on"


def _regime() -> str:
    """当前市场状态：进程内缓存未热（新进程/Worker 线程）时从历史表恢复。"""
    try:
        from app.backtest.market_regime import (
            get_regime_cache, restore_regime_cache_from_db,
        )
        cache = get_regime_cache() or {}
        if cache.get("state"):
            return cache["state"]
        restore_regime_cache_from_db()
        return (get_regime_cache() or {}).get("state") or ""
    except Exception:
        return ""


# 建议仓位档位（规则化输出，避免 7% 这类伪精度）
POSITION_STEPS = (0, 5, 10, 20, 50, 80)


def _snap(pct: int) -> int:
    """向下落到最近的档位（保守方向）。"""
    out = 0
    for v in POSITION_STEPS:
        if pct >= v:
            out = v
    return out


def _today_contradictions() -> int:
    """当日 L1/L2 矛盾条数（指数级"有根据"的宏观背书）。"""
    try:
        from app.contradictions.store import load_contradictions
        return len(load_contradictions(level="L1")) + len(load_contradictions(level="L2"))
    except Exception:
        return 0


def evaluate(code: str, mf: Dict = None) -> Dict:
    """
    买入三条件评估 + 建议仓位。
    mf: mainforce_state.load_latest([code])[code]（调用方批量传入省查询；缺省自取）。
    返回 {
      conditions: {A_mainforce, B_not_crowded, C_regime_ok},  # 布尔
      regime, crowded, accum,
      position_pct,          # 建议仓位 0~80
      position_label,        # 轻仓试探/半仓/积极/禁止
      reasons: [...],        # 每条判定的中文依据
      allow: bool,           # position_pct > 0
    }
    """
    if mf is None:
        try:
            from app.mainforce.state import load_latest
            mf = load_latest([code]).get(code) or {}
        except Exception:
            mf = {}
    chip = mf.get("chip") or {}
    signal = mf.get("signal")
    regime = _regime()
    pos = chip.get("price_pos")
    winner = chip.get("winner_ratio")

    # 条件A：主力根据（吸筹区 = 强根据；指数级矛盾存在 = 弱背书）
    accum = signal == "accum"
    contradictions = _today_contradictions()
    cond_a = accum
    reasons = []
    if accum:
        reasons.append("A✓ 主力吸筹区（低位筹码密集×主力净流入）")
    elif contradictions:
        reasons.append(f"A△ 无个股主力根据；当日有 {contradictions} 条指数级矛盾（宏观背书弱）")
    else:
        reasons.append("A✗ 无主力根据、当日亦无矛盾识别")

    # 条件B：不追高（筹码不拥挤）
    crowded = (pos is not None and pos > HIGH_POS_THRESHOLD) or \
              (winner is not None and winner > WINNER_RATIO_CROWDED)
    cond_b = not crowded
    if crowded:
        det = []
        if pos is not None:
            det.append(f"筹码位置 {pos:.0%}")
        if winner is not None:
            det.append(f"获利盘 {winner:.0%}")
        reasons.append("B✗ 追高（" + "/".join(det) + "）")
    else:
        reasons.append("B✓ 筹码不拥挤（"
                       + (f"位置 {pos:.0%}/获利盘 {winner:.0%}" if pos is not None else "数据缺") + "）")

    # 条件C：状态允许
    cond_c = regime in ("offensive", "neutral", "neutral_bearish")
    reasons.append(f"C{'✓' if cond_c else '✗'} 状态 {regime or '未知'}"
                   + ("（防御档禁买）" if regime == "defensive" else ""))

    # 建议仓位（档位化）
    pct = REGIME_POSITION.get(regime, 30 if regime else 30)
    if crowded:
        pct = int(pct * 0.5)
    if not cond_a:
        pct = int(pct * 0.5)
    if accum and cond_b and cond_c:
        pct = min(80, max(pct, 50))     # 吸筹区+不拥挤+状态允许 → 至少半仓，"大胆"量化
    pct = _snap(pct)
    label = ("禁止" if pct == 0 else
             "轻仓试探" if pct <= 10 else
             "四分之一仓" if pct <= 20 else
             "半仓" if pct <= 50 else "积极")

    return {
        "conditions": {"A_mainforce": cond_a, "B_not_crowded": cond_b,
                       "C_regime_ok": cond_c},
        "regime": regime, "crowded": crowded, "accum": accum,
        "contradictions_today": contradictions,
        "position_pct": pct, "position_label": label,
        "reasons": reasons,
        "allow": pct > 0,
        "active": _mode_on(),
    }


def render_advice(result: Dict) -> str:
    """评估结果 → 一行中文建议（推送/日报复用）。"""
    if not result.get("active"):
        return ""
    return (f"建议仓位 {result['position_pct']}%（{result['position_label']}）"
            f"｜状态 {result.get('regime') or '未知'}")
