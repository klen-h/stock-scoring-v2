#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""正 alpha 组合的显著性检验：block bootstrap（按信号日）+ 时段外检验 + 回撤。

读 data/trades.json（backtest_rescan_by_regime.py --dump 产出）。
★ block bootstrap：以「信号日」为块重采样（同日多只股票高度相关，不能按笔独立抽样），
  检验均超额是否显著 > 0。
"""
import json
import random
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

trades = json.load(open("data/trades.json"))

TARGETS = [
    ("ma_pullback × 阴跌", "ma_pullback", "neutral_bearish"),
    ("limit_up_boomerang × 进攻", "limit_up_boomerang", "offensive"),
    ("dragon_turnaround × 进攻", "dragon_turnaround", "offensive"),
    ("single_yang × 阴跌", "single_yang_unbroken", "neutral_bearish"),
]


def block_bootstrap(day_means, B=10000, seed=42):
    """按信号日的均超额做 bootstrap（有放回重采样信号日）。返回 (ci_lo, ci_hi, p_leq0)。"""
    rng = random.Random(seed)
    n = len(day_means)
    boot = []
    for _ in range(B):
        sample = [rng.choice(day_means) for _ in range(n)]
        boot.append(sum(sample) / n)
    boot.sort()
    lo = boot[int(B * 0.025)]
    hi = boot[int(B * 0.975)]
    p = sum(1 for m in boot if m <= 0) / B
    return lo, hi, p


def max_drawdown_daily(cell):
    """按信号日等权日收益的净值曲线最大回撤（%）。"""
    by_day = defaultdict(list)
    for t in cell:
        by_day[t["signal_date"]].append(t["pnl_pct"])
    days = sorted(by_day)
    if not days:
        return None, None
    nav, peak, max_dd = 1.0, 1.0, 0.0
    for d in days:
        ret = sum(by_day[d]) / len(by_day[d]) / 100.0
        nav *= (1 + ret)
        peak = max(peak, nav)
        max_dd = max(max_dd, (peak - nav) / peak)
    total = (nav - 1) * 100
    return round(total, 2), round(max_dd * 100, 2)


for name, en, st in TARGETS:
    cell = [t for t in trades if t["strategy_en"] == en and t["regime_state"] == st]
    if not cell:
        print(f"\n=== {name}: 无样本 ===\n")
        continue
    print(f"\n=== {name} ===")
    print(f"成交 {len(cell)} 笔")

    # ① 独立信号日
    days = sorted(set(t["signal_date"] for t in cell))
    print(f"① 独立信号日 {len(days)} 天（{days[0]} ~ {days[-1]}）")

    # ② 按年分布（时段依赖检查）
    by_year = defaultdict(list)
    for t in cell:
        by_year[t["signal_date"][:4]].append(t["excess"])
    print("② 按年超额（年 / 笔数 / 均超额%）:")
    for y in sorted(by_year):
        v = by_year[y]
        print(f"   {y}: {len(v):5d} 笔, 均 {sum(v)/len(v):+6.2f}%")

    # ③ block bootstrap
    day_groups = defaultdict(list)
    for t in cell:
        day_groups[t["signal_date"]].append(t["excess"])
    day_means = [sum(v) / len(v) for v in day_groups.values()]
    lo, hi, p = block_bootstrap(day_means)
    print(f"③ bootstrap 95% CI 均超额: [{lo:+.2f}%, {hi:+.2f}%]")
    print(f"   P(均超额≤0) = {p:.3f}  → {'显著为正' if p < 0.05 else '不显著'}")

    # ④ 回撤（按日等权净值）
    total, mdd = max_drawdown_daily(cell)
    worst = min(t["pnl_pct"] for t in cell)
    print(f"④ 累计收益 {total:+.1f}% | 最大回撤 {mdd:.1f}% | 单笔最差 {worst:.2f}%")
