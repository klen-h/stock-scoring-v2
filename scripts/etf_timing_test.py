#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【中小市值 ETF 择时】「涨停回暖日」买入中小市值 ETF，持有 T+20，能否捕获 +1.96pp？
================================================================================
背景：个股版「缩量企稳反转」在中小市值（20~1000亿）落地 +1.96pp。但那是**横截面选股**
      （每天几千只里挑超跌缩量收红的）。用户追问：能否用 ETF 简化执行？

本脚本测的是**择时**（非选股）：
  「涨停回暖日」（全市场涨停家数 > 20日均值×1.5）T+1 开盘买入**中小市值指数**，
  持有 T+20，相对无条件基准的超额。
  —— 若涨停回暖日本身就捕获了中小市值的反弹，则可用 ETF 一键执行，无需选股。

【★ 数据口径（重要）】
  ETF 自身 K 线仅 3 年（pack klines，2023-08~2026-09，752 天）⇒ 样本不足。
  故用**指数**作 ETF 代理（ETF 跟踪指数，收益高度相关，仅差跟踪误差+成本）：
    · sh000905 中证500  → 中证500ETF(510500)
    · sh000852 中证1000 → 中证1000ETF(512100)
    · sz399006 创业板指 → 创业板ETF(159915)
  指数数据 21 年（idx_daily.json）。

【预登记（写死）】
  T2 涨停回暖 : limit_up > 20日均值 × 1.5
  入场       : 涨停回暖日 T+1 开盘买入，持有 HOLD=20 交易日收盘
  对照       : 无条件「T+1 开盘买持有 20 日」均值
  判据       : 超额 ≥ +1.0pp 且 簇级 bootstrap P < 0.05 且 簇 ≥ 15

用法：python scripts/etf_timing_test.py
================================================================================
"""
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLD = 20
LU_BOOST = 1.5
MIN_DIFF = 1.0
ALPHA = 0.05
MIN_CLUSTERS = 15
CLUSTER_GAP = 20
B_BOOT = 10000
SEED = 42

MID_IDX = [("sh000905", "中证500", "510500 中证500ETF"),
           ("sh000852", "中证1000", "512100 中证1000ETF"),
           ("sz399006", "创业板指", "159915 创业板ETF")]


def _ma(a, w):
    out = np.full(len(a), np.nan)
    c = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def _days(s):
    return (datetime.strptime(s, "%Y-%m-%d") - datetime(1970, 1, 1)).days


def cluster_boot_p(dates, rets_map, base, B=B_BOOT, seed=SEED):
    # 簇 = 涨停回暖日（gap>=20）
    ds = sorted(dates)
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
    boost = set(evd[i] for i in range(20, len(evd)) if lu[i] > lum[i] * LU_BOOST and lum[i] > 0)
    print(f"涨停回暖日（涨停家数>20日均值×{LU_BOOST}）= {len(boost)} 天（占 {len(evd)} 交易日 {len(boost)/len(evd)*100:.1f}%）")

    idx = json.load(open(os.path.join(ROOT, "data", "idx_daily.json"), encoding="utf-8"))

    print(f"\n{'=' * 100}")
    print(f"【中小市值 ETF 择时】涨停回暖日 T+1 开盘买入，持有 T+{HOLD}")
    print(f"{'=' * 100}")
    print(f"{'指数/ETF':<22}{'回暖日n':>7}{'回暖日T+20':>12}{'无条件':>9}{'超额':>9}{'P值':>9}  判定")

    for code, cn, etf in MID_IDX:
        if code not in idx:
            print(f"{cn+'('+etf+')':<22}  （无数据）")
            continue
        rows = sorted(idx[code], key=lambda x: x["date"])
        dates = [r["date"] for r in rows]
        opens = np.array([r["open"] for r in rows], float)
        closes = np.array([r["close"] for r in rows], float)
        n = len(rows)
        fwd = np.full(n, np.nan)
        for i in range(n - 1 - HOLD):
            if opens[i + 1] > 0:
                fwd[i] = (closes[i + 1 + HOLD] / opens[i + 1]) - 1

        all_rets = [fwd[i] for i in range(n) if not np.isnan(fwd[i])]
        base = float(np.mean(all_rets))
        bmap = {dates[i]: fwd[i] for i in range(n)
                if dates[i] in boost and not np.isnan(fwd[i])}
        bdates = sorted(bmap)
        if len(bdates) < 15:
            print(f"{cn+'('+etf+')':<22}{len(bdates):>7}  （样本不足）")
            continue
        bmean = float(np.mean(list(bmap.values())))
        diff = (bmean - base) * 100
        p = cluster_boot_p(bdates, bmap, base)
        ok = diff >= MIN_DIFF and p < ALPHA
        print(f"{cn+'('+etf+')':<22}{len(bdates):>7}{bmean*100:>+11.2f}%{base*100:>+8.2f}%"
              f"{diff:>+8.2f}pp{p:>9.4f}  {'★捕获' if ok else '-'}")

    print(f"\n判据：超额≥+{MIN_DIFF}pp 且 P<{ALPHA}（对照：个股版中小市值 +1.96pp）")
    print("【说明】ETF 自身仅 3 年数据 ⇒ 用指数 21 年数据作代理；ETF 实际收益 ≈ 指数收益 − 跟踪误差 − 交易成本。")


if __name__ == "__main__":
    main()
