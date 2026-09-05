#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法退出参数扫描（PLAN_NEXT_PHASE P0-2 第三路：退出改造）
================================================================================

问题：战法止盈/止损/持有期多为拍脑袋参数。用全部历史信号做网格扫描，
为每条战法找数据支撑的退出参数。

网格：
  hold_days:  3 / 5 / 10
  stop_loss:  信号日收盘 -5% / -7% / -10%（另含原参数、无止损）
  take_profit: 原目标价 / 不止盈（持有到期或止损）

撮合口径与 engine.match_signals 完全一致（T+1 开盘成交、涨停一字剔除、
跌停顺延）。每格输出 n/胜率/均收益/盈亏比/平均持有天数。

输出：backend/backtest_reports/strategy_exit_sweep_YYYYMMDD.md
用法：python scripts/strategy_exit_sweep.py
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
                "orig_stop": it.get("stop_loss"),
                "orig_tp": it.get("target_price"),
                "strategy_en": r["strategy_name"],
            })
    return signals


def signal_day_close(signals, prices_map) -> None:
    """用信号日收盘价补 stop 基准（写到 signal['base_close']）。"""
    for s in signals:
        bars = prices_map.get(s["code"]) or []
        base = None
        for b in bars:
            if b["date"] <= s["date"]:
                base = b["close"]
            else:
                break
        s["base_close"] = base


def run_variant(signals, prices_map, hold, stop_pct, tp_mode):
    """stop_pct=None 用原参数；'none' 无止损。tp_mode: 'orig'/'none'。"""
    sigs = []
    for s in signals:
        st = dict(s, direction="long", is_etf=False, strategy=s["strategy_en"],
                  hold_days=hold)
        if stop_pct == "orig":
            st["stop_loss"] = s["orig_stop"]
        elif stop_pct == "none":
            st["stop_loss"] = None
        else:
            st["stop_loss"] = (round(s["base_close"] * (1 - stop_pct), 2)
                               if s.get("base_close") else None)
        st["take_profit"] = s["orig_tp"] if tp_mode == "orig" else None
        sigs.append(st)
    sk = []
    trades = engine.match_signals(sigs, prices_map, skipped_out=sk)
    n = len(trades)
    if not n:
        return None
    wins = [t for t in trades if t["pnl_pct"] > 0]
    gw = sum(t["pnl_pct"] for t in wins)
    gl = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0))
    return {
        "n": n, "filtered": len(sk),
        "win": round(len(wins) / n * 100, 1),
        "avg": round(sum(t["pnl_pct"] for t in trades) / n, 3),
        "pf": round(gw / gl, 2) if gl > 0 else 999.0,
        "avg_hold": round(sum(t["hold_days"] for t in trades) / n, 1),
        "stop_exits": sum(1 for t in trades if "止损" in (t["exit_reason"] or "")),
    }


def main():
    signals = load_signals()
    print(f"signals: {len(signals)}")
    prices_map = _load_prices_map({s["code"] for s in signals})
    signal_day_close(signals, prices_map)
    have_base = sum(1 for s in signals if s.get("base_close"))
    print(f"base_close 可用 {have_base}/{len(signals)}")

    # 基线（原参数原持有期）
    variants = [("原参数", WARFARE_HOLD_DAYS, "orig", "orig")]
    for hold in (3, 5, 10):
        for stop_pct in (0.05, 0.07, 0.10):
            for tp_mode in ("orig", "none"):
                variants.append((f"h{hold}/s{int(stop_pct*100)}/{tp_mode}",
                                 hold, stop_pct, tp_mode))
    variants.append(("h5/无止损/原tp", 5, "none", "orig"))

    rows = []
    for label, hold, stop_pct, tp_mode in variants:
        r = run_variant(signals, prices_map, hold, stop_pct, tp_mode)
        if r:
            rows.append((label, r))
            print(f"{label}: n={r['n']} win={r['win']}% avg={r['avg']}% "
                  f"pf={r['pf']} hold={r['avg_hold']}d 止损出场={r['stop_exits']}")

    best = sorted([x for x in rows if x[1]["n"] >= 300],
                  key=lambda x: x[1]["avg"], reverse=True)[:5]
    report = ["# 战法退出参数扫描", "",
              f"> 生成：{dt.datetime.now():%Y-%m-%d %H:%M} ｜ 信号 {len(signals)} 条",
              "",
              "## 全体信号（各战法合并）", "",
              "| 配置 | n | 胜率% | 均收益% | 盈亏比 | 平均持有 | 止损出场数 |",
              "|---|---|---|---|---|---|---|"]
    for label, r in rows:
        report.append(f"| {label} | {r['n']} | {r['win']} | {r['avg']} | {r['pf']} | "
                      f"{r['avg_hold']}d | {r['stop_exits']} |")
    report += ["", "## 均收益 Top5（n≥300）", ""]
    for label, r in best:
        report.append(f"- {label}: 胜率 {r['win']}% / 均收益 {r['avg']}% / 盈亏比 {r['pf']}")

    out = os.path.join(BACKEND_DIR, "backtest_reports",
                       f"strategy_exit_sweep_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
