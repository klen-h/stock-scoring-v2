# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法信号主力过滤闸门（PLAN_NEXT_PHASE P0-2 第一路的落地）
================================================================================

验证依据（scripts/strategy_mainforce_filter_test.py，547 信号历史重放，2026-09-05）：

  | 过滤器            | 保留 | 胜率%  | 均收益% | 盈亏比 |
  |---                |---   |---     |---      |---     |
  | A 全部信号（基线） | 501  | 48.1   | 0.07    | 1.05   |
  | D 剔高位           | 407  | 50.4   | 0.402   | 1.32   |
  | G 剔高位+拉升段    | 364  | 50.3   | 0.44    | 1.36   |

分战法（G）：均线回踩 47.1%→76.9%、单阳不破 51.1%→54.5%、龙回头均收益
-0.13%→+0.27%。高位（筹码区间顶部）与拉升段（放量追高）的战法信号
是主要亏损源——形态相似，但主力阶段不对。

口径（G）：信号股 price_pos > 0.75（筹码区间顶部）或 phase=markup（拉升段）
  → 不放行。信号落库保留全量（数据不失真），仅在【企微推送】与【模拟盘入池】
  两个可执行路径生效。

开关：STRATEGY_MAINFORCE_GATE=on（默认）/ off。
================================================================================
"""

import time

from app.mainforce.chips import chip_metrics
from app.mainforce.phases import detect_phase

HIGH_POS_THRESHOLD = 0.75   # 筹码区间顶部（与验证口径一致）
_fs_cache = {"ts": 0.0, "map": None}


def _mode_on() -> bool:
    import os
    return (os.environ.get("STRATEGY_MAINFORCE_GATE") or "on").strip().lower() == "on"


def _float_map() -> dict:
    if _fs_cache["map"] is None or time.time() - _fs_cache["ts"] > 6 * 3600:
        try:
            from app.mainforce.flow import get_float_shares_from_snapshot
            _fs_cache["map"] = get_float_shares_from_snapshot()
            _fs_cache["ts"] = time.time()
        except Exception:
            _fs_cache["map"] = {}
    return _fs_cache["map"]


def _load_bars(code: str) -> list:
    """K线优先级：评分 K 线缓存（当日就绪）→ backtest_prices。"""
    try:
        from app.scoring.kline_cache import get_cached_klines
        bars = get_cached_klines(code)
        if bars and len(bars) >= 130:
            return bars
    except Exception:
        pass
    try:
        from app.backtest.data import load_prices
        bars = load_prices(code, start="2025-01-01")
        if bars and len(bars) >= 130:
            return bars
    except Exception:
        pass
    return []


def strategy_gate(code: str, name: str = "") -> dict:
    """
    战法信号主力过滤。返回 {ok, reason, phase, price_pos, winner_ratio, flow5}。
    ok=False：高位（price_pos>0.75）或拉升段——形态信号与主力阶段背离。
    """
    empty = {"ok": True, "reason": "", "phase": None, "price_pos": None,
             "winner_ratio": None, "flow5": None}
    if not _mode_on():
        return {**empty, "reason": "gate off"}
    bars = _load_bars(code)
    if not bars:
        return {**empty, "reason": "no bars"}          # 无数据不拦截（保守放行）
    chip = chip_metrics(bars, float_shares=_float_map().get(code))
    phase = detect_phase(bars)
    if not chip or not phase:
        return {**empty, "reason": "no chip/phase"}

    flow5 = None
    try:
        from app.mainforce.flow import load_flow
        rows = load_flow(code)[-5:]
        if rows:
            flow5 = round(sum(float(r.get("main_pct") or 0) for r in rows), 2)
    except Exception:
        pass

    blocked = []
    if chip["price_pos"] > HIGH_POS_THRESHOLD:
        blocked.append(f"高位(price_pos {chip['price_pos']:.2f})")
    if phase["phase"] == "markup":
        blocked.append(f"拉升段(ret20 {phase['ret_20']:+.0f}%,量比 {phase['vol_ratio']})")

    return {
        "ok": not blocked,
        "reason": "；".join(blocked),
        "phase": phase["phase"],
        "price_pos": chip["price_pos"],
        "winner_ratio": chip["winner_ratio"],
        "flow5": flow5,
    }


def gate_states_for_signals(signals: list) -> dict:
    """
    批量还原信号日当日的主力过滤状态（无前视，供白名单重算/回测复用）。
    signals: [{code, date, ...}]；返回 {(code, date): gate_dict}（同 strategy_gate 结构）。
    """
    by_code = {}
    for s in signals:
        by_code.setdefault(s["code"], []).append(s["date"])
    from app.mainforce.flow import load_flow_map
    flow_map = load_flow_map()
    fs_map = _float_map()
    out = {}
    from app.backtest.data import load_prices
    for code, dates in by_code.items():
        bars = load_prices(code, start="2025-01-01")
        if not bars or len(bars) < 130:
            continue
        valid = [d for d in dates if bars[69]["date"] <= d <= bars[-1]["date"]]
        if not valid:
            continue
        chips = chip_series(bars, float_shares=fs_map.get(code), dates_out=valid)
        phases = _phase_series_safe(bars, valid)
        flow_rows = flow_map.get(code) or []
        flow_idx = {r["date"]: k for k, r in enumerate(flow_rows)}
        for d in valid:
            cm, pm = chips.get(d), phases.get(d)
            if not cm or not pm:
                continue
            flow5 = None
            if d in flow_idx:
                k = flow_idx[d]
                flow5 = round(sum(float(r.get("main_pct") or 0)
                                  for r in flow_rows[max(0, k - 4):k + 1]), 2)
            blocked = []
            if cm["price_pos"] > HIGH_POS_THRESHOLD:
                blocked.append(f"高位(price_pos {cm['price_pos']:.2f})")
            if pm["phase"] == "markup":
                blocked.append("拉升段")
            out[(code, d)] = {
                "ok": not blocked, "reason": "；".join(blocked),
                "phase": pm["phase"], "price_pos": cm["price_pos"],
                "winner_ratio": cm["winner_ratio"], "flow5": flow5,
            }
    return out


def _phase_series_safe(bars: list, dates_out: list) -> dict:
    try:
        from app.mainforce.phases import phase_series
        return phase_series(bars, dates_out=dates_out)
    except Exception:
        return {}
