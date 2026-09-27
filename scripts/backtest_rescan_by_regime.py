#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段 2：历史重扫信号 → 撮合 + 分状态统计（战法 × 市场状态 × 波动）。

【数据流】
  rescan.db（信号，47.6 万） + zzshare_daily.db（个股价格）
  + sh000300（东财 fetch_history 拉 10 年，detect_market_regime 判状态）
  → engine.match_signals 撮合（T+1 开盘 + 涨停一字剔除 + _limit_pct 分板块）
  → 按「战法 × state(四态含 nb) × volatility」分组统计胜率/期望。

【口径】与生产 backtest_warfare_by_regime 对齐：apply_exit_policy v2（持有 3 日、
止损 -7%、不设目标价）。★ 差异：① 状态用**四态**（offensive/neutral/neutral_bearish/
defensive，含 nb，不再把 nb 折叠进 neutral）；② 闸门（gate_states_for_signals）默认
跳过 —— 主力闸门依赖 mainflow_history，历史段覆盖不全，需先验证覆盖再决定是否加。

用法：
  python scripts/backtest_rescan_by_regime.py --start 2016-01-01 --end 2026-09-24
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
RESCAN_DB = os.path.join(ROOT, "data", "rescan.db")

from app.backtest import engine, data  # noqa: E402
from app.backtest.market_regime import detect_market_regime  # noqa: E402
from app.backtest.strategies import apply_exit_policy, exit_policy, WARFARE_HOLD_DAYS  # noqa: E402
from app.zzshare_client import to_zz_code  # noqa: E402

STATE_LABELS = {"offensive": "进攻", "neutral": "震荡", "neutral_bearish": "阴跌",
                "defensive": "防御"}
VOL_LABELS = {"high": "高波动", "normal": "正常波动", "low": "低波动"}


def load_signals(start, end):
    """rescan.db → 信号流（对齐 _warfare_signal_stream 格式）。"""
    conn = sqlite3.connect(RESCAN_DB)
    signals = []
    for strategy_name, scan_date, results_json in conn.execute(
            "SELECT strategy_name, scan_date, results_json FROM rescan_strategy_results "
            "WHERE scan_date >= ? AND scan_date <= ?", (start, end)):
        try:
            items = json.loads(results_json)
        except (json.JSONDecodeError, TypeError):
            continue
        for it in items or []:
            code = str(it.get("code") or "").strip()
            if len(code) != 6:
                continue
            pos = float(it.get("position_pct") or 20) / 100.0
            signals.append({
                "date": scan_date,
                "code": code,
                "name": str(it.get("name") or "").strip() or code,
                "direction": "long",
                "stop_loss": it.get("stop_loss"),
                "take_profit": it.get("target_price"),
                "hold_days": WARFARE_HOLD_DAYS,
                "position_ratio": min(max(pos, 0.05), 1.0),
                "is_etf": False,
                "strategy": strategy_name,
                "strategy_en": strategy_name,
                "entry_price": it.get("entry_price"),
            })
    conn.close()
    return signals


def build_regime_map():
    """sh000300 日线 → detect_market_regime → {date: {state, vol, score}}。

    ★ 优先读 nb_history.json（build_nb_history.py 产出的**四态**，含 nb 阴跌态 +
      波动/分数）；否则读 sh000300 本地缓存现算三态（nb 缺失）。"""
    nb_cache = os.path.join(ROOT, "data", "nb_history.json")
    if os.path.exists(nb_cache):
        with open(nb_cache, encoding="utf-8") as f:
            nb_map = json.load(f)
        return {d: {"state": v["state_4"], "volatility_regime": v.get("volatility_regime"),
                    "regime_score": v.get("regime_score")} for d, v in nb_map.items()}
    cache = os.path.join(ROOT, "data", "sh000300_daily.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            bars = json.load(f)
    else:
        bars = data.fetch_history("sh000300", start="2016-01-01")
    states = detect_market_regime(bars)
    m = {}
    for s in states:
        m[s.date] = {"state": s.state, "volatility_regime": s.volatility_regime,
                     "regime_score": s.regime_score}
    return m


def _load_index_bars():
    """读 sh000300 日线（本地缓存优先，与 build_regime_map 同源）。"""
    cache = os.path.join(ROOT, "data", "sh000300_daily.json")
    if os.path.exists(cache):
        with open(cache, encoding="utf-8") as f:
            return json.load(f)
    return data.fetch_history("sh000300", start="2016-01-01")


def _apply_phase_gate(signals, prices_map):
    """部分闸门：剔除 phase=='markup'（拉升段）的信号。

    ★ 只做 phase 过滤：phase 是纯量价规则（只需 bars），可用 zzshare 10 年可信回测。
      price_pos（筹码高位）过滤依赖流通股本历史（float_shares 只有当前快照、无历史，
      10 年回测偏差大），故省略 —— 生产闸门 = phase + price_pos，回测 = 仅 phase。
    ★ 内存：用 phase_series 的 dates_out 参数只算**信号日**的 phase（否则缓存每只
      2608 天 × 5919 只 ≈ 7.7GB，MemoryError）。
    """
    from app.mainforce.phases import phase_series
    by_code = {}
    for s in signals:
        by_code.setdefault(s["code"], set()).add(s["date"])
    phase_map = {}
    for code, dates in by_code.items():
        bars = prices_map.get(code, [])
        if len(bars) < 70:
            continue
        ps = phase_series(bars, dates_out=dates)
        for d, pm in ps.items():
            phase_map[(code, d)] = pm.get("phase")
    kept = []
    for s in signals:
        if phase_map.get((s["code"], s["date"])) == "markup":
            continue
        kept.append(s)
    return kept


def nearest_regime(m, date):
    """按日期查状态，查不到向前找最近交易日（≤10 天）。"""
    if date in m:
        return m[date]
    d = datetime.strptime(str(date).strip(), "%Y-%m-%d")
    for _ in range(10):
        d -= timedelta(days=1)
        key = d.strftime("%Y-%m-%d")
        if key in m:
            return m[key]
    return None


def load_prices_map(codes, start):
    """zzshare → {code: [{date, open, high, low, close, volume}]}（升序，date >= start）。"""
    conn = sqlite3.connect(ZZSHARE_DB)
    m = {}
    n = len(codes)
    for i, c in enumerate(codes, 1):
        full = to_zz_code(c)
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume FROM daily "
            "WHERE code = ? AND date >= ? ORDER BY date ASC", (full, start)).fetchall()
        m[c] = [{"date": r[0], "open": r[1], "high": r[2], "low": r[3],
                 "close": r[4], "volume": r[5]} for r in rows]
        if i % 1000 == 0:
            print(f"  价格加载 {i}/{n}")
    conn.close()
    return m


def _stat(trades):
    n = len(trades)
    if not n:
        return {"n": 0, "win_rate": None, "avg_pnl_pct": None, "profit_factor": None,
                "bench_ret": None, "excess": None}
    wins = sum(1 for t in trades if t["pnl_pct"] > 0)
    profits = [t["pnl_pct"] for t in trades]
    gw = sum(p for p in profits if p > 0)
    gl = abs(sum(p for p in profits if p < 0))
    benchs = [t["bench_ret"] for t in trades if t.get("bench_ret") is not None]
    excesses = [t["excess"] for t in trades if t.get("excess") is not None]
    return {"n": n,
            "win_rate": round(wins / n * 100, 1),
            "avg_pnl_pct": round(sum(profits) / n, 2),
            "profit_factor": round(gw / gl, 2) if gl > 0 else (999.0 if gw > 0 else 0.0),
            "bench_ret": round(sum(benchs) / len(benchs), 2) if benchs else None,
            "excess": round(sum(excesses) / len(excesses), 2) if excesses else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-09-24")
    ap.add_argument("--gate-phase", action="store_true",
                    help="启用部分闸门（剔除 phase==markup 拉升段信号）")
    args = ap.parse_args()

    t0 = time.time()
    print("① 读信号…")
    signals = load_signals(args.start, args.end)
    print(f"   信号 {len(signals)} 条")

    print("② 状态标签（sh000300 → detect_market_regime）…")
    regime_map = build_regime_map()
    print(f"   状态 {len(regime_map)} 天")
    for s in signals:
        r = nearest_regime(regime_map, s["date"])
        s["regime_state"] = r["state"] if r else None
        s["regime_vol"] = r["volatility_regime"] if r else None
    # 无状态标签的信号剔除
    signals = [s for s in signals if s["regime_state"] is not None]
    print(f"   打标签后 {len(signals)} 条")

    print("③ 价格（zzshare）…")
    codes = sorted({s["code"] for s in signals})
    prices_map = load_prices_map(codes, args.start)
    print(f"   价格 {len(prices_map)} 只")

    print("④ 退出策略 + 撮合…")
    if exit_policy() == "v2":
        # ★ 不传 base_close：战法 entry_price 就是信号日收盘（重扫 K 线来自 zzshare，
        #   _check_stock 里 entry_price = klines[-1]["close"]），与 zzshare 信号日 close 完全一致。
        #   省掉全量 _close_idx（5919 股 × 2608 天 ≈ 1.5GB，曾 MemoryError）。
        signals = [apply_exit_policy(s) for s in signals]
    if args.gate_phase:
        _before = len(signals)
        signals = _apply_phase_gate(signals, prices_map)
        print(f"   部分闸门（phase）：{_before} → {len(signals)} 信号（剔除拉升段 {_before - len(signals)}）")
    trades = engine.match_signals(signals, prices_map)
    print(f"   成交 {len(trades)} 笔")

    # ⑤ benchmark：同期沪深300（入场日开盘 → 出场日收盘，与 _benchmark_ret 同口径）
    idx_bars = _load_index_bars()
    idx_map = {b["date"]: b for b in idx_bars}
    for t in trades:
        e = idx_map.get(t["entry_date"])
        x = idx_map.get(t["exit_date"])
        if e and x and e.get("open"):
            t["bench_ret"] = round((x["close"] / e["open"] - 1) * 100, 3)
            t["excess"] = round(t["pnl_pct"] - t["bench_ret"], 3)
        else:
            t["bench_ret"] = None
            t["excess"] = None

    # ⑥ 分状态统计
    by_strategy = defaultdict(lambda: {"by_state": defaultdict(list), "by_vol": defaultdict(list)})
    state_all = defaultdict(list)
    for t in trades:
        en = t.get("strategy_en") or "unknown"
        by_strategy[en]["by_state"][t.get("regime_state")].append(t)
        by_strategy[en]["by_vol"][t.get("regime_vol")].append(t)
        state_all[t.get("regime_state")].append(t)

    print("\n===== 全市场总览 =====")
    print(f"总信号 {len(signals)} → 成交 {len(trades)} 笔")
    print(f"整体: {_stat(trades)}")
    print("\n===== 按状态（四态含 nb）=====")
    for st in ["offensive", "neutral", "neutral_bearish", "defensive"]:
        r = _stat(state_all.get(st, []))
        print(f"  {STATE_LABELS.get(st, st):6s} ({st}): {r}")

    print("\n===== 按战法 × 状态 =====")
    for en in sorted(by_strategy, key=lambda x: -sum(len(v) for v in by_strategy[x]["by_state"].values())):
        b = by_strategy[en]
        total = sum(len(v) for v in b["by_state"].values())
        print(f"\n[{en}] 成交 {total} 笔")
        for st in ["offensive", "neutral", "neutral_bearish", "defensive"]:
            r = _stat(b["by_state"].get(st, []))
            print(f"    {STATE_LABELS.get(st, st):6s}: {r}")

    print(f"\n完成，耗时 {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
