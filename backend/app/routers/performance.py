# -*- coding: utf-8 -*-
"""
系统级绩效路由（GET /api/system/performance）—— "有没有 edge" 的诚实记录。

三轨对照，每轨明确标注统计属性：
  模拟盘   前瞻真实记录（样本极小，仅积累起点）
  排行榜   半前瞻·重叠窗口（评分权重用同期数据校准，数字偏乐观）
  战法回放 in-sample（v2 参数与过滤阈值在同一批信号上调优，不可外推）

结论导向：本接口不证明"有 edge"，它建立持续记录的框架，
         让 edge 与否在 30 笔平仓 / 20 个快照日之后有数据可答。
"""

import time as _time
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.database import db

router = APIRouter()

_perf_cache = {"ts": 0.0, "data": None}


def _to_date(v) -> Optional[str]:
    return str(v)[:10] if v is not None else None


# ── 轨道一：模拟盘（前瞻真实记录） ──────────────────────────────────

def _paper_track() -> Dict:
    from app.flash.rules import beijing_now
    rows = db.fetch("SELECT * FROM paper_positions ORDER BY created_at ASC")
    if not rows:
        return {"label": "模拟盘（前瞻真实记录）", "available": False, "note": "暂无任何交易记录"}
    closed = [r for r in rows if r.get("status") == "closed" and r.get("pnl_pct") is not None]
    first = str(rows[0].get("created_at"))[:10]
    try:
        days = (beijing_now().date() - datetime.strptime(first, "%Y-%m-%d").date()).days
    except ValueError:
        days = None
    wins = [r for r in closed if float(r["pnl_pct"]) > 0]
    acc = db.fetch_one("SELECT * FROM paper_account ORDER BY id DESC LIMIT 1")
    realized = float((acc or {}).get("realized_pnl") or 0)
    initial = float((acc or {}).get("initial_capital") or 0) or None

    bench = None
    try:
        bars = db.fetch("SELECT date, close FROM backtest_prices WHERE code='sh000300' "
                        "AND date >= %s ORDER BY date ASC", (first,))
        if len(bars) >= 2:
            bench = round((float(bars[-1]["close"]) / float(bars[0]["close"]) - 1) * 100, 2)
    except Exception:
        pass

    n = len(closed)
    return {
        "label": "模拟盘（前瞻真实记录）",
        "available": True,
        "start": first, "days": days,
        "total": len(rows), "closed": n,
        "holding": sum(1 for r in rows if r.get("status") == "holding"),
        "win_rate": round(len(wins) / n * 100, 1) if n else None,
        "avg_pnl": round(sum(float(r["pnl_pct"]) for r in closed) / n, 3) if n else None,
        "realized": round(realized, 0),
        "initial_capital": initial,
        "benchmark_hs300": bench,
        "note": ("样本极小（已平仓 %d 笔 < 30），统计上无意义——这是记录起点，"
                 "满 30 笔平仓后才有参考价值" % n) if n < 30 else None,
    }


# ── 轨道二：排行榜 Top10（半前瞻·重叠窗口） ─────────────────────────

def _ranking_track(fwd: int = 5) -> Dict:
    rows = db.fetch("SELECT rank_date, code, rank_pos, price FROM ranking_history "
                    "WHERE rank_pos <= 10 ORDER BY rank_date ASC")
    if not rows:
        return {"label": "评分排行榜 Top10（半前瞻）", "available": False}

    by_date: Dict[str, list] = {}
    for r in rows:
        price = float(r.get("price") or 0)
        if price > 0:
            by_date.setdefault(str(r["rank_date"]), []).append(
                {"code": r["code"], "price": price})
    dates = sorted(by_date)

    per_day = []
    for d in dates:
        rets = []
        for it in by_date[d]:
            b = db.fetch(
                "SELECT date, close FROM backtest_prices WHERE code=%s AND date >= %s "
                "ORDER BY date ASC LIMIT %s", (it["code"], d, fwd + 1))
            if b and len(b) == fwd + 1 and float(b[0]["close"]) > 0:
                rets.append((float(b[fwd]["close"]) / float(b[0]["close"]) - 1) * 100)
        if len(rets) >= 5:
            per_day.append({"date": d, "mean": sum(rets) / len(rets),
                            "win": sum(1 for x in rets if x > 0) / len(rets) * 100,
                            "n": len(rets)})
    if not per_day:
        return {"label": "评分排行榜 Top10（半前瞻）", "available": False,
                "note": "前瞻窗口未满，等快照积累"}

    bench = None
    try:
        bars = db.fetch("SELECT date, close FROM backtest_prices WHERE code='sh000300' "
                        "AND date > %s AND date <= %s ORDER BY date ASC",
                        (per_day[0]["date"], per_day[-1]["date"]))
        if len(bars) >= 2:
            bench = round((float(bars[-1]["close"]) / float(bars[0]["close"]) - 1) * 100, 2)
    except Exception:
        pass

    all_rets = [d["mean"] for d in per_day]
    cum = 1.0
    for m in all_rets:
        cum *= (1 + m / 100)
    return {
        "label": "评分排行榜 Top10（半前瞻·重叠窗口）",
        "available": True,
        "start": per_day[0]["date"], "end": per_day[-1]["date"],
        "days": len(per_day),
        "win_rate_days": round(sum(1 for m in all_rets if m > 0) / len(all_rets) * 100, 1),
        "avg_daily": round(sum(all_rets) / len(all_rets), 3),
        "cum_return": round((cum - 1) * 100, 2),
        "benchmark_hs300": bench,
        "per_day": [{"date": d["date"], "mean": round(d["mean"], 3),
                     "win": round(d["win"], 1), "n": d["n"]} for d in per_day],
        "note": ("每日 Top10 等权、持有 5 日的重叠窗口近似；评分权重用同期数据校准，"
                 "存在样本内倾向——看趋势别看绝对值"),
    }


# ── 轨道三：战法回放（in-sample） ──────────────────────────────────

def _replay_metrics(signals, prices_map) -> Optional[Dict]:
    from app.backtest import engine as _bt_engine
    from app.backtest.strategies import apply_exit_policy, exit_policy
    applied = []
    if exit_policy() == "v2":
        for s in signals:
            base = None
            for b in prices_map.get(s["code"]) or []:
                if b["date"] <= s["date"]:
                    base = b["close"]
                else:
                    break
            applied.append(apply_exit_policy(s, base_close=base))
    else:
        applied = signals
    sk = []
    trades = _bt_engine.match_signals(applied, prices_map, skipped_out=sk)
    n = len(trades)
    if not n:
        return None
    wins = [t for t in trades if t["pnl_pct"] > 0]
    gw = sum(t["pnl_pct"] for t in wins)
    gl = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0))
    d0, d1 = trades[0]["entry_date"], trades[-1]["exit_date"]
    bench = None
    try:
        bars = db.fetch("SELECT date, close FROM backtest_prices WHERE code='sh000300' "
                        "AND date >= %s AND date <= %s ORDER BY date ASC", (d0, d1))
        if len(bars) >= 2:
            bench = round((float(bars[-1]["close"]) / float(bars[0]["close"]) - 1) * 100, 2)
    except Exception:
        pass
    return {"n": n, "win": round(len(wins) / n * 100, 1),
            "avg": round(sum(t["pnl_pct"] for t in trades) / n, 3),
            "pf": round(gw / gl, 2) if gl > 0 else 999.0, "bench": bench}


def _replay_track() -> Dict:
    from app.backtest.strategies import (
        _load_prices_map, _warfare_signal_stream)
    from app.mainforce.gate import gate_states_for_signals

    signals = _warfare_signal_stream()
    if not signals:
        return {"label": "战法回放（样本内）", "available": False, "note": "无历史信号"}
    prices_map = _load_prices_map({s["code"] for s in signals})

    baseline = _replay_metrics(signals, prices_map)
    try:
        states = gate_states_for_signals(signals)
        gated = [s for s in signals
                 if (states.get((s["code"], s["date"])) or {}).get("ok", True)]
    except Exception as e:
        print(f"[performance] 闸门状态计算失败: {e}")
        gated = signals
    deployed = _replay_metrics(gated, prices_map)

    return {
        "label": "战法回放（样本内）",
        "available": True,
        "baseline": baseline, "deployed": deployed,
        "note": ("★ in-sample：退出参数 v2 与主力过滤阈值就是在同一批信号上调优的，"
                 "正期望不可外推。真正的检验是模拟盘与排行榜的前瞻记录（上方两轨）"),
    }


@router.get("/performance")
def system_performance(user: dict = Depends(get_current_user)) -> Dict:
    """系统级绩效三轨：模拟盘（前瞻）/ 排行榜（半前瞻）/ 战法回放（in-sample）。"""
    now = _time.time()
    if _perf_cache["data"] and now - _perf_cache["ts"] < 3600:
        return _perf_cache["data"]
    data = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "paper": _paper_track(),
        "ranking": _ranking_track(),
        "replay": _replay_track(),
    }
    _perf_cache["ts"] = _time.time()
    _perf_cache["data"] = data
    return data
