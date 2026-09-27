#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
Regime 长期分布统计 —— 回答「一年有多少天允许买入 / 最长要等多久」
================================================================================
数据源：`data/nb_history.json`（由 `scripts/build_nb_history.py` 重建，
        4736 天 / 2007-04-05 ~ 今，**无前视偏差**：MA/ADX/ATR 全为滚动窗口，
        nb 的「连续 2 日确认」也是因果的）。

仓位映射（生产 `mainforce/trade_gate.REGIME_POSITION`，同源）：
  offensive 80% / neutral 50% / neutral_bearish 20% / defensive **0%（禁止买入）**

⚠️ 已知口径缺口：宽度数据（zzshare）只从 **2016-01-01** 起，
   ⇒ 2016 年前的 nb 判定会退化到降级判据（沪深300 近 2 日下跌），
     该判据较易满足 ⇒ **2016 年前 nb 可能偏多**。分年表里会显示出来。

用法：python scripts/regime_distribution.py
================================================================================
"""
import json
import os
import sys
from collections import Counter, defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NB_JSON = os.path.join(ROOT, "data", "nb_history.json")

POS = {"offensive": 80, "neutral": 50, "neutral_bearish": 20, "defensive": 0}
ORDER = ["offensive", "neutral", "neutral_bearish", "defensive"]
CN = {"offensive": "进攻", "neutral": "震荡",
      "neutral_bearish": "偏弱", "defensive": "防御"}
TRADING_DAYS_PER_YEAR = 243.0


def longest_run(seq, target):
    best = cur = 0
    best_end = None
    run_start = None
    for i, s in enumerate(seq):
        if s == target:
            if cur == 0:
                run_start = i
            cur += 1
            if cur > best:
                best, best_end = cur, i
        else:
            cur = 0
    return best, best_end


def main():
    with open(NB_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    dates = sorted(raw)
    seq = [raw[d].get("state_4") or raw[d].get("state_3") for d in dates]
    n = len(seq)

    print("=" * 96)
    print(f"Regime 长期分布 | {n} 个交易日 | {dates[0]} ~ {dates[-1]}"
          f" | 约 {(dates[-1][:4] and '%.1f' % (n / TRADING_DAYS_PER_YEAR))} 年")
    print("=" * 96)

    # ── 一、总体分布 ──
    c = Counter(seq)
    print(f"\n【一、总体分布】")
    print(f"  {'状态':<8}{'天数':>7}{'占比':>9}{'年化天数':>10}{'允许仓位':>10}")
    for st in ORDER:
        m = c.get(st, 0)
        print(f"  {CN[st]:<8}{m:>7}{m / n * 100:>8.1f}%"
              f"{m / n * TRADING_DAYS_PER_YEAR:>10.0f}"
              f"{('%d%%' % POS[st]):>10}")

    allow = sum(c.get(s, 0) for s in ORDER if POS[s] > 0)
    avg_pos = sum(c.get(s, 0) * POS[s] for s in ORDER) / n
    print(f"\n  · 允许买入（仓位>0）: {allow} 天 = {allow / n * 100:.1f}%"
          f"  ≈ {allow / n * TRADING_DAYS_PER_YEAR:.0f} 天/年")
    print(f"  · 禁止买入（defensive 0%）: {c.get('defensive', 0)} 天 = "
          f"{c.get('defensive', 0) / n * 100:.1f}%"
          f"  ≈ {c.get('defensive', 0) / n * TRADING_DAYS_PER_YEAR:.0f} 天/年")
    print(f"  · 高仓位（offensive 80%）: {c.get('offensive', 0)} 天 = "
          f"{c.get('offensive', 0) / n * 100:.1f}%"
          f"  ≈ {c.get('offensive', 0) / n * TRADING_DAYS_PER_YEAR:.0f} 天/年")
    print(f"  · **按状态加权的平均建议仓位 = {avg_pos:.1f}%**")

    # ── 二、最长连续段（回答「最坏要等多久」）──
    print(f"\n【二、最长连续段】")
    for st in ORDER:
        run, end = longest_run(seq, st)
        end_d = dates[end] if end is not None else "-"
        print(f"  {CN[st]:<8} 最长连续 {run:>4} 天（约 {run / 21:.1f} 个月），"
              f"结束于 {end_d}")

    # ── 三、分年分布 ──
    print(f"\n【三、分年分布】（占比 %，括号内为该年交易天数）")
    by_year = defaultdict(list)
    for d, s in zip(dates, seq):
        by_year[d[:4]].append(s)
    print(f"  {'年份':<6}{'交易天数':>8}{'进攻80%':>10}{'震荡50%':>10}"
          f"{'偏弱20%':>10}{'防御0%':>10}{'允许买入':>10}{'平均仓位':>10}")
    for y in sorted(by_year):
        ys = by_year[y]
        cnt = Counter(ys)
        ny = len(ys)
        al = sum(cnt.get(s, 0) for s in ORDER if POS[s] > 0) / ny * 100
        ap = sum(cnt.get(s, 0) * POS[s] for s in ORDER) / ny
        print(f"  {y:<6}{ny:>8}"
              f"{cnt.get('offensive', 0) / ny * 100:>10.1f}"
              f"{cnt.get('neutral', 0) / ny * 100:>10.1f}"
              f"{cnt.get('neutral_bearish', 0) / ny * 100:>10.1f}"
              f"{cnt.get('defensive', 0) / ny * 100:>10.1f}"
              f"{al:>9.1f}%{ap:>9.1f}%")

    # ── 四、近 5 年（新逻辑口径更可靠）──
    print(f"\n【四、近 5 年小计】（2016 年后宽度数据完整，nb 判定更准）")
    recent = [s for d, s in zip(dates, seq) if d >= "2021-09-27"]
    if recent:
        cr = Counter(recent)
        nr = len(recent)
        print(f"  {nr} 天：", end="")
        for st in ORDER:
            print(f"{CN[st]} {cr.get(st, 0) / nr * 100:.1f}%  ", end="")
        print()
        ap = sum(cr.get(s, 0) * POS[s] for s in ORDER) / nr
        al = sum(cr.get(s, 0) for s in ORDER if POS[s] > 0) / nr * 100
        print(f"  · 允许买入 {al:.1f}%  平均仓位 {ap:.1f}%")

    print("\n⚠️ 口径说明：宽度数据仅 2016-01 起；2016 年前 nb 用降级判据，可能偏多。")


if __name__ == "__main__":
    main()
