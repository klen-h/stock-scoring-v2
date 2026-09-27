#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【分时段验证】「缩量企稳反转」的落地 alpha，近 10 年还在吗？
================================================================================
背景：落地版（`shrink_rebound_live.py`）确认中小市值（20~1000亿）T+20 扣成本后 +1.96pp。
      但这是 21 年全样本，未验证"近 10 年是否仍有效"（可能是早期牛熊剧烈期的产物）。

本脚本：逐年 + 两段（2005~2015 vs 2016~2026）统计**落地超额**
        （= 信号净收益 − 同年同档全市场基准，扣 0.30% 双边成本，剔 ST/一字板）。

【判据（写死）】
  · 近 11 年（2016~2026）落地超额若仍 ≥ +1pp 且多数年份为正 ⇒ alpha 未衰减，可继续推进
  · 若仅前段有效、近段趋零/转负 ⇒ alpha 已衰减（历史现象），应归档
【口径】同 shrink_rebound_live：S1 20日≤-8% ∧ S2 量比≤0.5 ∧ T1 收红 ∧ T2 涨停回暖；
        T+20；中小市值 = 20~1000亿；扣 0.30% 双边；剔 ST / T+1 一字板。

用法：python scripts/shrink_rebound_byperiod.py
================================================================================
"""
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZZDB = os.path.join(ROOT, "data", "zzshare_daily.db")
EV_PATH = os.path.join(ROOT, "data", "event_history.json")

LOOKBACK = 20
MIN_DROP = -0.08
VOL_SHORT = 5
VOL_LONG = 60
VOL_RATIO = 0.50
LU_BOOST = 1.5
HOLD = 20
COST = 0.003
LIMIT_TH = 0.995
CAP_BINS = [0, 20, 50, 100, 300, 1000, float("inf")]
CAP_LABELS = ["<20亿", "20-50亿", "50-100亿", "100-300亿", "300-1000亿", ">1000亿"]
MID_BINS = [1, 2, 3, 4]          # 中小市值 20~1000亿


def _ma(a, w):
    out = np.full(len(a), np.nan)
    c = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def _bin(cap_yi):
    for i in range(len(CAP_BINS) - 1):
        if CAP_BINS[i] <= cap_yi < CAP_BINS[i + 1]:
            return i
    return len(CAP_BINS) - 2


def main():
    ev = json.load(open(EV_PATH, encoding="utf-8"))
    evd = sorted(ev)
    lu = np.array([ev[d].get("limit_up") or 0 for d in evd], float)
    lum = _ma(lu, 20)
    boost = set(evd[i] for i in range(20, len(evd)) if lu[i] > lum[i] * LU_BOOST and lum[i] > 0)

    conn = sqlite3.connect(ZZDB)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM daily ORDER BY code")]

    nb = len(CAP_LABELS)
    base_sums = np.zeros(nb)
    base_ns = np.zeros(nb, dtype=int)
    live_sums = np.zeros(nb)
    live_ns = np.zeros(nb, dtype=int)
    yr = defaultdict(lambda: {"bs": np.zeros(nb), "bn": np.zeros(nb, dtype=int),
                              "ls": np.zeros(nb), "ln": np.zeros(nb, dtype=int)})
    n_sig = 0
    t0 = time.time()

    for k, code in enumerate(codes):
        rows = conn.execute(
            "SELECT date, open, close, volume, turnover_rate, factor, is_st, high_limit "
            "FROM daily WHERE code=? ORDER BY date", (code,)).fetchall()
        if len(rows) < VOL_LONG + HOLD + 2:
            continue
        dates = [r[0] for r in rows]
        opens = np.array([r[1] for r in rows], float)
        closes = np.array([r[2] for r in rows], float)
        vols = np.array([r[3] for r in rows], float)
        turns = np.array([r[4] for r in rows], float)
        factors = np.array([r[5] for r in rows], float)
        is_st = np.array([r[6] for r in rows], int)
        high_lim = np.array([r[7] for r in rows], float)
        adj_open = opens * factors
        adj_close = closes * factors
        n = len(rows)

        with np.errstate(divide="ignore", invalid="ignore"):
            cap_yi = closes * vols / (turns / 100.0) / 1e8
        cap_ok = np.isfinite(cap_yi) & (cap_yi > 0)

        cum = np.full(n, np.nan)
        cum[LOOKBACK:] = adj_close[LOOKBACK:] / adj_close[:-LOOKBACK] - 1
        vr = _ma(vols, VOL_SHORT) / _ma(vols, VOL_LONG)
        red = closes > opens

        h_ret = np.full(n, np.nan)
        idx = np.arange(VOL_LONG, n - 1 - HOLD)
        if len(idx):
            ok = adj_open[idx + 1] > 0
            h_ret[idx[ok]] = (adj_close[idx[ok] + 1 + HOLD] / adj_open[idx[ok] + 1]) - 1

        for i in range(VOL_LONG, n - 1 - HOLD):
            if not cap_ok[i] or np.isnan(h_ret[i]):
                continue
            b = _bin(cap_yi[i])
            y = dates[i][:4]
            base_sums[b] += h_ret[i]
            base_ns[b] += 1
            yr[y]["bs"][b] += h_ret[i]
            yr[y]["bn"][b] += 1
            if (cum[i] <= MIN_DROP and vr[i] <= VOL_RATIO and red[i]
                    and dates[i] in boost):
                if is_st[i] == 1:
                    continue
                if high_lim[i + 1] > 0 and opens[i + 1] >= high_lim[i + 1] * LIMIT_TH:
                    continue
                net = h_ret[i] - COST
                live_sums[b] += net
                live_ns[b] += 1
                yr[y]["ls"][b] += net
                yr[y]["ln"][b] += 1
                n_sig += 1

        if (k + 1) % 1000 == 0:
            print(f"  进度 {k + 1}/{len(codes)}，{time.time() - t0:.0f}s，信号 {n_sig}")

    conn.close()

    def _excess(ls, ln, bs, bn, bins):
        n = ln[bins].sum()
        nb_ = bn[bins].sum()
        if n < 20 or nb_ < 20:
            return None
        return (ls[bins].sum() / n - bs[bins].sum() / nb_) * 100

    print(f"\n{'=' * 100}")
    print("【逐年落地超额】中小市值（20~1000亿），扣 0.30% + 剔 ST/一字板，T+20")
    print(f"{'=' * 100}")
    years = sorted(yr)
    print(f"{'年份':>6}{'信号n':>8}{'落地超额':>11}")
    pos_years = []
    for y in years:
        e = _excess(yr[y]["ls"], yr[y]["ln"], yr[y]["bs"], yr[y]["bn"], MID_BINS)
        if e is None:
            print(f"{y:>6}{int(yr[y]['ln'][MID_BINS].sum()):>8}  （样本不足）")
            continue
        pos_years.append((y, e))
        mark = "  ←正" if e > 0 else ""
        print(f"{y:>6}{int(yr[y]['ln'][MID_BINS].sum()):>8}{e:>+10.2f}pp{mark}")

    n_pos = sum(1 for _, e in pos_years if e > 0)
    print(f"\n正超额年份 = {n_pos}/{len(pos_years)}")

    print(f"\n{'=' * 100}")
    print("【两段汇总】前 11 年 vs 近 11 年（中小市值 20~1000亿 落地超额）")
    print(f"{'=' * 100}")
    for p0, p1, label in [(2005, 2015, "前11年(2005-2015)"),
                          (2016, 2026, "近11年(2016-2026)")]:
        ls = np.zeros(nb); ln = np.zeros(nb, dtype=int)
        bs = np.zeros(nb); bn = np.zeros(nb, dtype=int)
        for y in years:
            if p0 <= int(y) <= p1:
                ls += yr[y]["ls"]; ln += yr[y]["ln"]
                bs += yr[y]["bs"]; bn += yr[y]["bn"]
        e = _excess(ls, ln, bs, bn, MID_BINS)
        n_ = int(ln[MID_BINS].sum())
        print(f"  {label:<18} 信号 {n_:>7}  | 落地超额 {e:+.2f}pp" if e is not None
              else f"  {label:<18} 样本不足")

    print(f"\n【结论】")
    recent = [e for y, e in pos_years if int(y) >= 2016]
    if recent:
        r_mean = np.mean(recent)
        r_pos = sum(1 for e in recent if e > 0)
        print(f"  近 11 年（2016-2026）逐年落地超额均值 = {r_mean:+.2f}pp，"
              f"正年份 {r_pos}/{len(recent)}")
        if r_mean >= 1.0 and r_pos / len(recent) >= 0.6:
            print(f"  ⇒ alpha 未衰减，近 10 年仍有效（值得继续：样本外/实盘复核）")
        elif r_mean > 0:
            print(f"  ⇒ alpha 在近 10 年偏弱但未消失（需谨慎，可能有衰减）")
        else:
            print(f"  ⇒ alpha 近 10 年已转负 ⇒ 历史现象，归档")
    else:
        print(f"  近 11 年无足够样本 ⇒ 无法判断衰减")


if __name__ == "__main__":
    main()
