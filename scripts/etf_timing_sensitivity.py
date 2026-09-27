#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【涨停家数阈值敏感性】中小市值 ETF 择时的 edge 是否随阈值平滑？
================================================================================
背景：`etf_timing_test.py` 用「涨停家数 > 1.5×20日均值」买入中小市值 ETF，T+20 捕获
      +1.34~+2.29pp。但 1.5× 这个值是沿用个股版 T2，**从未扫描**。
      本脚本扫 boost ∈ {1.2,1.5,1.8,2.0,2.5,3.0}，判断：
        · edge 是**平滑平台**（对阈值不敏感 ⇒ 稳健）还是**单点尖峰**（过拟合）？
        · 1.5× 是不是合理值？

【预登记（写死）】
  网格      : boost ∈ {1.2, 1.5, 1.8, 2.0, 2.5, 3.0}
  指数      : sh000905 中证500 / sh000852 中证1000 / sz399006 创业板
  信号      : 涨停家数 limit_up > boost × 20日均值（自适应，规避绝对家数漂移）
  入场      : 信号日 T+1 开盘买入，持有 20 交易日
  判据      : 超额 ≥ +1.0pp 且 簇级 bootstrap P < Bonferroni α（18 次 ⇒ α=0.0028）且 簇≥15
  ★ 目标    : 看 edge 是否随 boost 平滑（稳健性证据），而非"找出最优值"（避免事后调参）

用法：python scripts/etf_timing_sensitivity.py
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
MIN_DIFF = 1.0
MIN_CLUSTERS = 15
CLUSTER_GAP = 20
B_BOOT = 10000
SEED = 42
N_TESTS = len(BOOSTS) * 3          # 6 阈值 × 3 指数
ALPHA = 0.05 / N_TESTS             # Bonferroni

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
    # 各 boost 的涨停回暖日
    boost_days = {b: set(evd[i] for i in range(20, len(evd))
                         if lu[i] > lum[i] * b and lum[i] > 0) for b in BOOSTS}
    print(f"{'boost':>7}{'涨停回暖日数':>12}{'占比':>8}")
    for b in BOOSTS:
        print(f"{b:>7.1f}{len(boost_days[b]):>12}{len(boost_days[b])/len(evd)*100:>7.1f}%")

    idx = json.load(open(os.path.join(ROOT, "data", "idx_daily.json"), encoding="utf-8"))

    # 预加载各指数 fwd
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
        all_rets = [fwd[i] for i in range(n) if not np.isnan(fwd[i])]
        base = float(np.mean(all_rets))
        cache[code] = (dates, fwd, base)

    print(f"\n{'=' * 112}")
    print(f"【阈值敏感性】超额（T+{HOLD}，涨停回暖日 vs 无条件），Bonferroni α={ALPHA:.4f}")
    print(f"{'=' * 112}")
    print(f"{'boost':>6}{'指数':>8}{'回暖日n':>7}{'T+20':>8}{'无条件':>8}{'超额':>8}{'P':>9}{'簇':>5}  判定")
    print("-" * 112)
    results = []
    for b in BOOSTS:
        for code, cn in MID_IDX:
            dates, fwd, base = cache[code]
            bmap = {dates[i]: fwd[i] for i in range(len(dates))
                    if dates[i] in boost_days[b] and not np.isnan(fwd[i])}
            bdates = sorted(bmap)
            if len(bdates) < 15:
                print(f"{b:>6.1f}{cn:>8}{len(bdates):>7}  （样本不足）")
                results.append((b, cn, len(bdates), None, None, None, False))
                continue
            bmean = float(np.mean(list(bmap.values())))
            diff = (bmean - base) * 100
            p = cluster_boot_p(bdates, bmap, base)
            cl = sum(1 for i, d in enumerate(bdates)
                     if i == 0 or _days(d) - _days(bdates[i - 1]) >= CLUSTER_GAP)
            ok = diff >= MIN_DIFF and p is not None and p < ALPHA and cl >= MIN_CLUSTERS
            results.append((b, cn, len(bdates), diff, p, cl, ok))
            print(f"{b:>6.1f}{cn:>8}{len(bdates):>7}{bmean*100:>+7.2f}%{base*100:>+7.2f}%"
                  f"{diff:>+7.2f}pp{p:>9.4f}{cl:>5}  {'★' if ok else '-'}")

    # 汇总：各指数 edge 随 boost 的轨迹
    print(f"\n{'=' * 112}")
    print("【edge 随阈值轨迹】（看是否平滑平台 vs 单点尖峰）")
    print(f"{'=' * 112}")
    for code, cn in MID_IDX:
        line = f"  {cn:<8} "
        for b in BOOSTS:
            r = [x for x in results if x[0] == b and x[1] == cn]
            if r and r[0][3] is not None:
                line += f"boost{b:.1f}:{r[0][3]:+.2f}pp  "
            else:
                line += f"boost{b:.1f}:--  "
        print(line)

    print(f"\n【结论】")
    zz = [r for r in results if r[1] == "中证1000" and r[3] is not None]
    if zz:
        diffs = [r[3] for r in zz]
        print(f"  中证1000 超额范围 = {min(diffs):+.2f} ~ {max(diffs):+.2f}pp"
              f"（若跨度小 ⇒ 对阈值不敏感 = 稳健；若单点突出 ⇒ 过拟合）")


if __name__ == "__main__":
    main()
