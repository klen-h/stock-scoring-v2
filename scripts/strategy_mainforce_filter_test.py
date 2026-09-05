#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法 × 主力过滤 历史重放验证（PLAN_NEXT_PHASE P0-2 第一路）
================================================================================

问题：全部战法胜率 ~47%（无一达 55% 白名单线）。假设：信号里混进了
"形态像但主力已出货"的假信号，用主力行为状态过滤能否提纯？

方法（先验证再接线）：
  1. 取 strategy_results 全部历史信号（~547 条）
  2. 对每个信号，用 backtest_prices + mainflow_history 还原【信号日当日】的
     主力状态（筹码位置/获利盘/阶段/5日主力净流入）——全部用信号日之前的数据，
     无前视偏差
  3. 按过滤器分组重放撮合（T+1 开盘成交 + 涨停一字剔除，engine 同口径）：
     A 全部信号（基线）
     B 剔除出货嫌疑（高位高获利×主力流出）
     C 只留吸筹区（低位密集×主力净流入）
     D 剔除高位（price_pos > 0.75）
     E 剔除出货段标签（phase=distribution）
     F 剔除高位且剔除出货段（B∪E）
     G 只留非高位非拉升（price_pos≤0.75 且 phase≠markup）

输出：整体 + 分战法的 n/胜率/均收益 对照表，写入
      backend/backtest_reports/strategy_mainforce_filter_YYYYMMDD.md

用法：python scripts/strategy_mainforce_filter_test.py
================================================================================
"""

import datetime as dt
import os
import sys
import time

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)
with open(os.path.join(BACKEND_DIR, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

from app.database import db                                    # noqa: E402
from app.backtest import engine                                # noqa: E402
from app.backtest.strategies import WARFARE_HOLD_DAYS, _load_prices_map  # noqa: E402
from app.mainforce.chips import chip_series                    # noqa: E402
from app.mainforce.phases import phase_series                  # noqa: E402
from app.mainforce.flow import load_flow_map, get_float_shares_from_snapshot  # noqa: E402

_orig = db.fetch


def q(sql, params=None, retries=4):
    last = None
    for i in range(retries):
        try:
            return _orig(sql, params)
        except Exception as e:
            last = e
            try:
                db._reset_pg_conn()
            except Exception:
                pass
            time.sleep(1.5 * (i + 1))
    raise last


def load_signals() -> list:
    rows = q("SELECT strategy_name, scan_date, results_json FROM strategy_results "
             "WHERE count > 0 ORDER BY scan_date ASC")
    signals = []
    for r in rows or []:
        import json as _json
        try:
            items = _json.loads(r["results_json"] or "[]")
        except (ValueError, TypeError):
            continue
        for it in items or []:
            code = str(it.get("code") or "").strip()
            if len(code) != 6:
                continue
            signals.append({
                "date": str(r["scan_date"]), "code": code,
                "name": str(it.get("name") or code),
                "stop_loss": it.get("stop_loss"),
                "take_profit": it.get("target_price"),
                "strategy_en": r["strategy_name"],
            })
    return signals


def mainforce_states(signals: list) -> dict:
    """{(code, date): 状态dict}——信号日当日的主力状态（无前视）。"""
    fs_map = get_float_shares_from_snapshot()
    flow_map = load_flow_map()
    by_code = {}
    for s in signals:
        by_code.setdefault(s["code"], []).append(s["date"])

    states = {}
    codes = sorted(by_code)
    for i, code in enumerate(codes, 1):
        bars = q("SELECT date, open, high, low, close, volume FROM backtest_prices "
                 "WHERE code=%s ORDER BY date ASC", (code,))
        bars = [{"date": str(r["date"]), "open": r["open"], "high": r["high"],
                 "low": r["low"], "close": r["close"], "volume": r["volume"]}
                for r in bars]
        if len(bars) < 130:
            continue
        dates_out = [d for d in by_code[code] if bars[69]["date"] <= d <= bars[-1]["date"]]
        if not dates_out:
            continue
        chips = chip_series(bars, float_shares=fs_map.get(code), dates_out=dates_out)
        phases = phase_series(bars, dates_out=dates_out)
        flow_rows = flow_map.get(code) or []
        flow_idx = {r["date"]: k for k, r in enumerate(flow_rows)}
        idx = {b["date"]: j for j, b in enumerate(bars)}
        for d in dates_out:
            cm, pm = chips.get(d), phases.get(d)
            if not cm or not pm:
                continue
            flow5 = None
            if d in flow_idx:
                k = flow_idx[d]
                w = flow_rows[max(0, k - 4):k + 1]
                flow5 = round(sum(float(r.get("main_pct") or 0) for r in w), 2)
            states[(code, d)] = {"chip": cm, "phase": pm["phase"], "flow5": flow5}
        if i % 60 == 0:
            print(f"[state] {i}/{len(codes)} ({time.time():.0f})")
    return states


def classify(m: dict) -> dict:
    """信号日主力状态 → 过滤器布尔位。"""
    chip, phase, flow5 = m["chip"], m["phase"], m["flow5"]
    high_pos = chip["price_pos"] > 0.75
    high_winner = chip["winner_ratio"] > 0.7
    flow_out = flow5 is not None and flow5 < 0
    flow_in = flow5 is not None and flow5 > 0
    weak_high = phase == "distribution" and chip["price_pos"] > 0.75
    return {
        "distribution": high_pos and high_winner and (flow_out or (flow5 is None and weak_high)),
        "accum": chip["price_pos"] < 0.35 and chip["concentration"] < 0.25 and flow_in,
        "high_pos": high_pos,
        "phase_distribution": phase == "distribution",
        "phase_markup": phase == "markup",
    }


FILTERS = [
    ("A_all", "全部信号（基线）", lambda c: True),
    ("B_no_distrib", "剔除出货嫌疑组合", lambda c: not c["distribution"]),
    ("C_accum_only", "只留吸筹区组合", lambda c: c["accum"]),
    ("D_no_highpos", "剔除高位（price_pos>0.75）", lambda c: not c["high_pos"]),
    ("E_no_distrib_phase", "剔除出货段标签", lambda c: not c["phase_distribution"]),
    ("F_no_distrib_any", "剔除组合或标签任一出货", lambda c: not c["distribution"] and not c["phase_distribution"]),
    ("G_no_high_no_markup", "剔除高位且剔除拉升段", lambda c: not c["high_pos"] and not c["phase_markup"]),
]


def replay(signals, prices_map, keep_fn, states):
    kept, skipped = [], []
    for s in signals:
        m = states.get((s["code"], s["date"]))
        ok = False
        if m:
            c = classify(m)
            ok = keep_fn(c)
        if ok:
            kept.append({**s, "direction": "long", "hold_days": WARFARE_HOLD_DAYS,
                         "is_etf": False, "strategy": s["strategy_en"]})
        else:
            skipped.append(s)
    sk = []
    trades = engine.match_signals(kept, prices_map, skipped_out=sk)
    n = len(trades)
    if not n:
        return {"n": 0, "win": None, "avg": None, "pf": None, "filtered": len(skipped)}
    wins = [t for t in trades if t["pnl_pct"] > 0]
    gw = sum(t["pnl_pct"] for t in wins)
    gl = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0))
    return {"n": n, "win": round(len(wins) / n * 100, 1),
            "avg": round(sum(t["pnl_pct"] for t in trades) / n, 3),
            "pf": round(gw / gl, 2) if gl > 0 else 999.0,
            "filtered": len(skipped)}


def main():
    signals = load_signals()
    print(f"signals: {len(signals)}")
    prices_map = _load_prices_map({s["code"] for s in signals})
    states = mainforce_states(signals)
    covered = sum(1 for s in signals if (s["code"], s["date"]) in states)
    print(f"主力状态覆盖 {covered}/{len(signals)}")

    report = ["# 战法 × 主力过滤 历史重放验证", "",
              f"> 生成：{dt.datetime.now():%Y-%m-%d %H:%M} ｜ 信号 {len(signals)} 条，"
              f"主力状态覆盖 {covered} 条 ｜ 撮合：T+1 开盘 + 涨停一字剔除 + 持有 {WARFARE_HOLD_DAYS} 日", ""]

    # 整体对照
    report += ["## 一、整体对照", "",
               "| 过滤器 | 规则 | 保留 | 被滤 | 胜率% | 均收益% | 盈亏比 |",
               "|---|---|---|---|---|---|---|"]
    for key, desc, fn in FILTERS:
        r = replay(signals, prices_map, fn, states)
        report.append(f"| {key} | {desc} | {r['n']} | {r['filtered']} | "
                      f"{r['win'] if r['win'] is not None else '-'} | "
                      f"{r['avg'] if r['avg'] is not None else '-'} | "
                      f"{r['pf'] if r['pf'] is not None else '-'} |")
        print(f"{key}: {r}")

    # 分战法 × 两个最有希望的过滤器
    report += ["", "## 二、分战法（基线 vs B 剔出货嫌疑 vs G 剔高位+拉升）", "",
               "| 战法 | 过滤 | n | 胜率% | 均收益% |", "|---|---|---|---|---|"]
    strat_names = sorted({s["strategy_en"] for s in signals})
    for st in strat_names:
        st_signals = [s for s in signals if s["strategy_en"] == st]
        for key, desc, fn in [("A", "全部", FILTERS[0][2]),
                              ("B", "剔出货嫌疑", FILTERS[1][2]),
                              ("G", "剔高位+拉升", FILTERS[6][2])]:
            r = replay(st_signals, prices_map, fn, states)
            if r["n"] == 0:
                continue
            report.append(f"| {st} | {desc} | {r['n']} | {r['win']} | {r['avg']} |")

    out = os.path.join(BACKEND_DIR, "backtest_reports",
                       f"strategy_mainforce_filter_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
