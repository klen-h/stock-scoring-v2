#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法衰退诊断：定位「半衰期告警」战法的胜率拐点，并叠加市场状态
================================================================================
背景（2026-09-20）：白名单重算的半衰期监控报出
  · ma_pullback              后半段 13.0% vs 前半段 45.5%
  · ma_convergence_breakout  后半段 14.2% vs 前半段 45.7%
即「前半段还行、后半段崩了」⇒ 需要定位**拐点日**，看它是否对应市况切换
（若确认"震荡类只在震荡市有效"，准入矩阵就该加"市况"维度）。

方法：与 `recommendation._recompute_whitelist` **同管线**（逐笔口径一致）——
  读 strategy_results → 主力闸门 `gate_states_for_signals` → 退出策略 v2
  → `engine.match_signals` 撮合（T+1 开盘成交、涨停一字剔除）→ 逐笔 {signal_date, pnl_pct}
  此处**复制**该管线而非调用，是为了拿到逐笔明细（生产函数只返回汇总）。
  ⚠️ 若生产管线改动，本脚本需同步（它已注明同源位置）。

输出：
  1. 每战法 **按扫描日** 的 n / 胜率 / 均收益，并标注当日 `market_regime_history` 市况
  2. **滑动窗口**（默认 5 个扫描日）胜率曲线 → 定位拐点
  3. 拐点前后对比 + 市况分布对比

零 egress：DATA_SOURCE=local（K 线走本机数据包），只回源
  strategy_results / mainforce_state / market_regime_history 三张表。

用法：
  python scripts/strategy_decay_diagnosis.py
  python scripts/strategy_decay_diagnosis.py --strategy ma_pullback --window 3
  python scripts/strategy_decay_diagnosis.py --min-n 20
================================================================================
"""
import argparse
import json
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
os.environ.setdefault("DATA_SOURCE", "local")     # K 线走本机包，不回源

from app.database import db                                    # noqa: E402
from app.backtest import engine as bt_engine                   # noqa: E402
from app.backtest.strategies import (                          # noqa: E402
    WARFARE_HOLD_DAYS, _load_prices_map, apply_exit_policy)
from app.mainforce.gate import gate_states_for_signals, _mode_on   # noqa: E402
from app.strategies.recommendation import STRATEGY_ZH          # noqa: E402


def load_trades():
    """复刻 `recommendation._recompute_whitelist` 的管线，返回逐笔 trades。"""
    rows = db.fetch("SELECT strategy_name, scan_date, results_json FROM strategy_results "
                    "WHERE count > 0 ORDER BY scan_date ASC")
    signals = []
    for r in rows or []:
        try:
            items = json.loads(r["results_json"] or "[]")
        except (ValueError, TypeError):
            continue
        for it in items or []:
            code = str(it.get("code") or "").strip()
            if len(code) != 6:
                continue
            signals.append({
                "date": r["scan_date"], "code": code,
                "name": str(it.get("name") or code), "direction": "long",
                "stop_loss": it.get("stop_loss"), "take_profit": it.get("target_price"),
                "hold_days": WARFARE_HOLD_DAYS, "is_etf": False,
                "strategy": r["strategy_name"],
            })
    if not signals:
        return [], {}
    strat_of = {(s["code"], s["date"]): s["strategy"] for s in signals}
    prices_map = _load_prices_map({s["code"] for s in signals},
                                  start=min(s["date"] for s in signals))
    # 主力过滤闸门（与生产同开关）
    if _mode_on():
        states = gate_states_for_signals(signals)
        signals = [s for s in signals
                   if (states.get((s["code"], s["date"])) or {}).get("ok", True)]
    applied = []
    for s in signals:
        base = None
        for b in prices_map.get(s["code"]) or []:
            if b["date"] <= s["date"]:
                base = b["close"]
            else:
                break
        applied.append(apply_exit_policy(s, base_close=base))
    return bt_engine.match_signals(applied, prices_map, skipped_out=[]), strat_of


def load_regimes():
    rows = db.fetch("SELECT date, state FROM market_regime_history ORDER BY date") or []
    return {str(r["date"])[:10]: r["state"] for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default=None, help="只看某个战法")
    ap.add_argument("--window", type=int, default=5, help="滑动窗口（扫描日数）")
    ap.add_argument("--min-n", type=int, default=20, help="只报告 n≥此值的战法")
    args = ap.parse_args()

    trades, strat_of = load_trades()
    if not trades:
        print("无撮合结果（信号为空 / 全被闸门过滤 / K线不可用）")
        return 1
    regimes = load_regimes()

    by_strat = defaultdict(lambda: defaultdict(list))
    for t in trades:
        st = t.get("strategy") or strat_of.get((t.get("code"), t.get("signal_date")), "?")
        by_strat[st][str(t.get("signal_date"))[:10]].append(float(t.get("pnl_pct") or 0))

    print("=" * 92)
    print(f"战法衰退诊断（同生产管线）  逐笔 {len(trades)} 笔 / "
          f"市况表 {len(regimes)} 天 / 窗口 {args.window} 个扫描日")
    print("=" * 92)

    targets = sorted(by_strat, key=lambda k: -sum(len(v) for v in by_strat[k].values()))
    for st in targets:
        dates = by_strat[st]
        n_all = sum(len(v) for v in dates.values())
        if args.strategy and st != args.strategy:
            continue
        if n_all < args.min_n:
            continue
        zh = STRATEGY_ZH.get(st, "")
        print(f"\n### {st}（{zh}）  n={n_all} / {len(dates)} 个扫描日")
        print(f"  {'扫描日':<12}{'n':>4}{'胜率':>8}{'均收益':>9}  市况")
        seq = []
        for d in sorted(dates):
            pnl = dates[d]
            wr = sum(1 for p in pnl if p > 0) / len(pnl) * 100
            seq.append((d, len(pnl), wr, sum(pnl) / len(pnl)))
            print(f"  {d:<12}{len(pnl):>4}{wr:>7.1f}%{sum(pnl)/len(pnl):>+8.2f}%"
                  f"  {regimes.get(d, '—')}")

        # 滑动窗口（按扫描日的成交笔数，窗口内合并计算）
        roll = []
        for i in range(len(seq)):
            win = seq[max(0, i - args.window + 1):i + 1]
            flat = [p for d in [x[0] for x in win] for p in dates[d]]
            if len(flat) < 10:
                continue
            roll.append((seq[i][0], sum(1 for p in flat if p > 0) / len(flat) * 100,
                         sum(flat) / len(flat), len(flat)))
        if roll:
            print(f"  ── 滑动窗口（{args.window} 日累计 ≥10 笔）")
            last_good = None
            for d, wr, av, n in roll:
                if wr >= 50:
                    last_good = d
                print(f"     {d}  n={n:>3}  胜率 {wr:>5.1f}%  均收益 {av:>+6.2f}%")
            print(f"  ★ 最后一次滚动胜率 ≥50% 的窗口止于 **{last_good}**")
            seg = [r for r in roll if last_good is None or r[0] > last_good]
            if seg:
                print(f"     其后 {len(seg)} 个窗口：胜率 "
                      f"{sum(r[1] for r in seg)/len(seg):.1f}% / 均收益 "
                      f"{sum(r[2] for r in seg)/len(seg):+.2f}%")
                reg_after = defaultdict(int)
                for r in seg:
                    reg_after[regimes.get(r[0], "—")] += 1
                print(f"     其后市况分布：{dict(reg_after)}")
            reg_before = defaultdict(int)
            for d, *_ in seq:
                if last_good is None or d <= last_good:
                    reg_before[regimes.get(d, "—")] += 1
            print(f"     之前市况分布：{dict(reg_before)}")
    print("\n注：拐点结论需叠加市况解读；样本仍在积累（断档 4 天刚恢复），"
          "周一后重跑更稳。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
