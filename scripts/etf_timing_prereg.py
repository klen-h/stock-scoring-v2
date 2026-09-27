#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【独立预登记复核】涨停家数阈值 1.8~2.0× 的样本外验证（时间切分）
================================================================================
背景：`etf_timing_sensitivity.py` 用**全样本**扫出 boost ∈ {1.8,2.0} 显著（中证1000 +5.3~6.3pp）。
      但这是样本内扫描，可能是过拟合。按 E2 的"独立预登记"纪律，本脚本做
      **时间切分样本外复核**，防止"对着全样本挑最优阈值"。

【预登记（先于结果写死）】
  第一步（定参，训练段 2005-2015）：
      扫描 boost ∈ {1.2,1.5,1.8,2.0,2.5,3.0}，记录各指数在训练段的 T+20 超额。
  第二步（验证，测试段 2016-2026，样本外）：
      取「训练段超额最大的 boost」作为冻结阈值，在测试段验证。
      判据（测试段，写死）：超额 ≥ +1.0pp 且 簇级 bootstrap P < 0.05 且 簇 ≥ 15。
  通过标准：**若训练段选出的阈值恰在 1.8~2.0，且测试段（样本外）仍通过判据**
            ⇒ 阈值稳健，非过拟合。
  ★ 口径：涨停回暖日 = 涨停家数 > boost×滚动20日均值（只用过去20日，无前视）；
          标的 = 中小市值指数（中证500/中证1000/创业板，作 ETF 代理）；持有 T+20。

用法：python scripts/etf_timing_prereg.py
================================================================================
"""
import json
import os
import sys
from datetime import datetime

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLD = 20
BOOSTS = [1.2, 1.5, 1.8, 2.0, 2.5, 3.0]
TRAIN = (2005, 2015)
TEST = (2016, 2026)
MIN_DIFF = 1.0
ALPHA = 0.05
MIN_CLUSTERS = 15
CLUSTER_GAP = 20
B_BOOT = 10000
SEED = 42

MID_IDX = [("sh000905", "中证500"), ("sh000852", "中证1000"), ("sz399006", "创业板")]


def _ma(a, w):
    out = np.full(len(a), np.nan)
    c = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def _days(s):
    return (datetime.strptime(s, "%Y-%m-%d") - datetime(1970, 1, 1)).days


def cluster_boot_p(dates, rets_map, base, B=B_BOOT, seed=SEED):
    ds = sorted(dates)
    if not ds:
        return None
    clusters = [[ds[0]]]
    for a, b in zip(ds, ds[1:]):
        if _days(b) - _days(a) >= CLUSTER_GAP:
            clusters.append([])
        clusters[-1].append(b)
    mem = [np.array([rets_map[d] for d in c], float) for c in clusters]
    k = len(mem)
    rng = np.random.default_rng(seed)
    means = np.empty(B)
    for b in range(B):
        pick = rng.integers(0, k, size=k)
        vals = np.concatenate([mem[j] for j in pick])
        means[b] = vals.mean()
    return float(np.mean(means <= base))


def main():
    ev = json.load(open(os.path.join(ROOT, "data", "event_history.json"), encoding="utf-8"))
    evd = sorted(ev)
    lu = np.array([ev[d].get("limit_up") or 0 for d in evd], float)
    lum = _ma(lu, 20)
    # 各 boost 的涨停回暖日（滚动20日均值，无前视）
    boost_days = {b: set(evd[i] for i in range(20, len(evd))
                         if lu[i] > lum[i] * b and lum[i] > 0) for b in BOOSTS}

    idx = json.load(open(os.path.join(ROOT, "data", "idx_daily.json"), encoding="utf-8"))
    cache = {}
    for code, cn in MID_IDX:
        rows = sorted(idx[code], key=lambda x: x["date"])
        dates = [r["date"] for r in rows]
        opens = np.array([r["open"] for r in rows], float)
        closes = np.array([r["close"] for r in rows], float)
        n = len(rows)
        fwd = np.full(n, np.nan)
        for i in range(n - 1 - HOLD):
            if opens[i + 1] > 0:
                fwd[i] = (closes[i + 1 + HOLD] / opens[i + 1]) - 1
        cache[code] = (dates, fwd)

    def _excess(code, boost, y0, y1):
        dates, fwd = cache[code]
        all_r = [fwd[i] for i in range(len(dates))
                 if y0 <= int(dates[i][:4]) <= y1 and not np.isnan(fwd[i])]
        if not all_r:
            return None
        base = float(np.mean(all_r))
        bmap = {dates[i]: fwd[i] for i in range(len(dates))
                if dates[i] in boost_days[boost] and y0 <= int(dates[i][:4]) <= y1
                and not np.isnan(fwd[i])}
        if len(bmap) < 15:
            return None
        bmean = float(np.mean(list(bmap.values())))
        p = cluster_boot_p(sorted(bmap), bmap, base)
        cl = sum(1 for i, d in enumerate(sorted(bmap))
                 if i == 0 or _days(d) - _days(sorted(bmap)[i - 1]) >= CLUSTER_GAP)
        return (bmean - base) * 100, p, cl

    print("=" * 100)
    print("【第一步：训练段 2005-2015 定参】各 boost 的 T+20 超额")
    print("=" * 100)
    print(f"{'boost':>6}{'中证500':>10}{'中证1000':>10}{'创业板':>10}")
    train_best = {}
    for b in BOOSTS:
        vals = []
        for code, cn in MID_IDX:
            r = _excess(code, b, *TRAIN)
            vals.append(r)
        def fmt(r):
            return f"{r[0]:+.2f}pp" if r else "  --  "
        print(f"{b:>6.1f}{fmt(vals[0]):>10}{fmt(vals[1]):>10}{fmt(vals[2]):>10}")
        for code, r in zip([x[0] for x in MID_IDX], vals):
            if r and (code not in train_best or r[0] > train_best[code][0]):
                train_best[code] = (b, r[0])
    print("\n训练段各指数最优 boost：")
    for code, cn in MID_IDX:
        if code in train_best:
            print(f"  {cn}: boost={train_best[code][0]:.1f}（超额 {train_best[code][1]:+.2f}pp）")

    print("\n" + "=" * 100)
    print("【第二步：测试段 2016-2026 样本外验证】用训练段最优阈值")
    print("=" * 100)
    print(f"{'指数':<10}{'冻结阈值':>8}{'测试段n':>8}{'超额':>9}{'P':>9}{'簇':>5}  判定")
    for code, cn in MID_IDX:
        if code not in train_best:
            print(f"{cn:<10}  （训练段样本不足）")
            continue
        b, _ = train_best[code]
        r = _excess(code, b, *TEST)
        if r is None:
            print(f"{cn:<10}{b:>8.1f}  （测试段样本不足）")
            continue
        diff, p, cl = r
        ok = diff >= MIN_DIFF and p is not None and p < ALPHA and cl >= MIN_CLUSTERS
        print(f"{cn:<10}{b:>8.1f}{'—':>8}{diff:>+8.2f}pp{p:>9.4f}{cl:>5}  "
              f"{'★样本外通过' if ok else '-'}")
        # 补 n
    print(f"\n判据（测试段）：超额≥+{MIN_DIFF}pp 且 P<{ALPHA} 且 簇≥{MIN_CLUSTERS}")
    print("【说明】训练段选阈值 → 测试段验证 ⇒ 若测试段仍通过，阈值非全样本过拟合。")


if __name__ == "__main__":
    main()
