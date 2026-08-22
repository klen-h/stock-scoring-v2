"""
================================================================================
【文件作用】回测 API：GET /api/backtest/strategy?name=xxx（引擎计算 + 10 分钟缓存）
================================================================================
name 取值：
  - signals：LLM 信号绩效追踪（已落盘交易统计）
  - warfare：战法选股回测（全体战法，含前70%/后30%切分）
  - macro  ：宏观方向分回测

实现要点：
  - 计算耗时（战法需加载几百只个股日线），结果内存缓存 10 分钟
  - 响应裁剪：逐笔交易只回 Top10（按单笔收益绝对值），净值曲线只回概要
================================================================================
"""

import time

from fastapi import APIRouter, Query

from app.backtest import strategies

router = APIRouter()

_CACHE_TTL = 600          # 结果缓存 10 分钟
_cache = {}               # {name: (ts, result_json)}

_STRATEGY_NAMES = {"signals", "warfare", "macro"}


def _trade_light(t: dict) -> dict:
    """逐笔交易裁剪：去掉每日收益路径，只留展示字段。"""
    return {k: v for k, v in t.items() if k != "daily"}


def _curve_summary(curve: list) -> dict:
    """净值曲线概要（前端画不了全曲线也要有数字可看）。"""
    if not curve:
        return None
    first, last = curve[0], curve[-1]
    peak = max(curve, key=lambda c: c["nav"])
    # 最大回撤段：峰值日 → 之后最低点日
    mdd_pair, peak_nav = None, curve[0]
    for c in curve:
        if c["nav"] > peak_nav["nav"]:
            peak_nav = c
        ratio = c["nav"] / peak_nav["nav"]
        if mdd_pair is None or ratio < mdd_pair[0]:
            mdd_pair = (ratio, peak_nav["date"], c["date"])
    return {
        "start": first["date"], "end": last["date"],
        "days": len(curve),
        "start_nav": round(first["nav"], 4), "end_nav": round(last["nav"], 4),
        "peak_date": peak["date"], "peak_nav": round(peak["nav"], 4),
        "mdd_from": mdd_pair[1], "mdd_to": mdd_pair[2],
        "mdd_ratio": round(mdd_pair[0] - 1, 4),
    }


def _build_response(name: str, result: dict) -> dict:
    """统一响应结构（裁剪大字段）。"""
    resp = {
        "type": name,
        "label": result.get("label", name),
        "sample_note": result.get("sample_note", ""),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if name == "signals":
        resp["total"] = result.get("total")
        resp["by_source"] = result.get("by_source", {})
        return resp
    resp["metrics"] = result.get("metrics")
    if name == "warfare":
        resp["top_trades"] = [_trade_light(t) for t in
                              sorted(result.get("trades") or [],
                                     key=lambda t: abs(t["pnl_pct"]),
                                     reverse=True)[:10]]
        resp["curve_summary"] = _curve_summary(result.get("curve") or [])
        for key in ("in_sample", "out_sample"):
            part = result.get(key)
            if part:
                resp[key] = {
                    "label": part.get("label"),
                    "metrics": part.get("metrics"),
                    "sample_note": part.get("sample_note"),
                }
    elif name == "macro":
        resp["curve_summary"] = _curve_summary(result.get("curve") or [])
    return resp


@router.get("/strategy")
def get_strategy(name: str = Query(..., description="signals / warfare / macro")):
    """计算并返回某策略回测结果（10 分钟内存缓存）。"""
    if name not in _STRATEGY_NAMES:
        return {"error": f"未知策略 {name}，可选: {sorted(_STRATEGY_NAMES)}"}
    cached = _cache.get(name)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        resp = cached[1]
        resp["cached"] = True
        return resp

    start = time.time()
    if name == "signals":
        result = strategies.backtest_llm_signals()
    elif name == "warfare":
        result = strategies.backtest_warfare()
    else:
        result = strategies.backtest_macro()
    resp = _build_response(name, result)
    resp["compute_seconds"] = round(time.time() - start, 2)
    _cache[name] = (time.time(), resp)
    return resp
