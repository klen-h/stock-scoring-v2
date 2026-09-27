#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【市值中性检验】T+20 的 +2.79pp 超额，是小市值因子的别名吗？
================================================================================
背景：`shrink_rebound_strategy.py` 的四条件组合在 T+20 得 +2.79pp（P≈0、胜率 63.3%）。
      但「超跌股」天然偏小市值，而小市值本身有 alpha ⇒ 必须做市值中性，判断
      这 +2.79pp 里有多少是「缩量企稳」贡献、多少是「小市值」贡献。

【方法】信号日**流通市值**分档（★ 历史市值，非当前快照）：
  流通股本 = volume ÷ (turnover_rate/100)      # 成交量 ÷ 换手率
  流通市值 = close × 流通股本                   # 单位：亿元
  分档：<20 / 20~50 / 50~100 / 100~300 / 300~1000 / >1000 亿（对数分档）
  对照：每档内「信号股 T+20」 vs 「同档全市场 T+20」（同档相减 = 市值中性超额）

【判据（先于结果写死）】
  核心问题：**大市值档（≥100 亿）的信号是否仍有正超额？**
    · 若大市值档超额也 >0 且样本够 ⇒ 「缩量企稳」有独立 alpha，非小市值别名
    · 若仅小市值档有超额、大市值档≈0 ⇒ +2.79pp 主要是小市值因子，策略价值有限

【数据】data/zzshare_daily.db（含 volume/turnover_rate/factor）+ event_history.json
【口径】忽略 ST / 停牌（turnover_rate=0 时市值无法反推 ⇒ 跳过该样本）
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

# ── 预登记 ──
LOOKBACK = 20
MIN_DROP = -0.08
VOL_SHORT = 5
VOL_LONG = 60
VOL_RATIO = 0.50
LU_BOOST = 1.5
HOLD = 20                     # 聚焦 T+20（最强持有期）
CAP_BINS = [0, 20, 50, 100, 300, 1000, float("inf")]   # 亿元
CAP_LABELS = ["<20亿", "20-50亿", "50-100亿", "100-300亿", "300-1000亿", ">1000亿"]


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
    print(f"T2 涨停回暖日 = {len(boost)} 天；持有 T+{HOLD}")

    conn = sqlite3.connect(ZZDB)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM daily ORDER BY code")]

    nb = len(CAP_LABELS)
    base_sums = np.zeros(nb)
    base_ns = np.zeros(nb, dtype=int)
    sig_sums = np.zeros(nb)
    sig_ns = np.zeros(nb, dtype=int)
    n_sig = 0
    t0 = time.time()

    for k, code in enumerate(codes):
        rows = conn.execute(
            "SELECT date, open, close, volume, turnover_rate, factor FROM daily "
            "WHERE code=? ORDER BY date", (code,)).fetchall()
        if len(rows) < VOL_LONG + HOLD + 2:
            continue
        dates = [r[0] for r in rows]
        opens = np.array([r[1] for r in rows], float)
        closes = np.array([r[2] for r in rows], float)
        vols = np.array([r[3] for r in rows], float)
        turns = np.array([r[4] for r in rows], float)
        factors = np.array([r[5] for r in rows], float)
        adj_open = opens * factors
        adj_close = closes * factors
        n = len(rows)

        # 信号日流通市值（亿）：close × volume / (turnover_rate/100) / 1e8
        with np.errstate(divide="ignore", invalid="ignore"):
            cap_yi = closes * vols / (turns / 100.0) / 1e8
        cap_ok = np.isfinite(cap_yi) & (cap_yi > 0)

        # 条件
        cum = np.full(n, np.nan)
        cum[LOOKBACK:] = adj_close[LOOKBACK:] / adj_close[:-LOOKBACK] - 1
        vr = _ma(vols, VOL_SHORT) / _ma(vols, VOL_LONG)
        red = closes > opens

        # T+20 收益
        h_ret = np.full(n, np.nan)
        idx = np.arange(VOL_LONG, n - 1 - HOLD)
        if len(idx):
            ok = adj_open[idx + 1] > 0
            h_ret[idx[ok]] = (adj_close[idx[ok] + 1 + HOLD] / adj_open[idx[ok] + 1]) - 1

        for i in range(VOL_LONG, n - 1 - HOLD):
            if not cap_ok[i] or np.isnan(h_ret[i]):
                continue
            b = _bin(cap_yi[i])
            # 全市场基准（同档）
            base_sums[b] += h_ret[i]
            base_ns[b] += 1
            # 信号
            if (cum[i] <= MIN_DROP and vr[i] <= VOL_RATIO and red[i]
                    and dates[i] in boost):
                sig_sums[b] += h_ret[i]
                sig_ns[b] += 1
                n_sig += 1

        if (k + 1) % 1000 == 0:
            print(f"  进度 {k + 1}/{len(codes)}，{time.time() - t0:.0f}s，信号 {n_sig}")

    conn.close()

    print(f"\n{'=' * 100}")
    print("【市值中性检验】每档内：信号 T+20 vs 同档全市场 T+20")
    print(f"{'=' * 100}")
    print(f"{'市值档':<12}{'信号n':>7}{'信号T+20':>11}{'全市场T+20':>13}{'超额':>9}  解读")
    print("-" * 100)
    for b in range(nb):
        if base_ns[b] == 0:
            continue
        base = base_sums[b] / base_ns[b]
        if sig_ns[b] < 10:
            print(f"{CAP_LABELS[b]:<12}{sig_ns[b]:>7}  （信号样本<10，跳过）")
            continue
        sig = sig_sums[b] / sig_ns[b]
        diff = (sig - base) * 100
        note = ("★大市值档仍正超额" if diff > 1.0 and b >= 2 else
                ("小市值为主" if b < 2 else "-"))
        print(f"{CAP_LABELS[b]:<12}{sig_ns[b]:>7}{sig*100:>+10.2f}%{base*100:>+12.2f}%"
              f"{diff:>+8.2f}pp  {note}")

    # 市值分布对比
    print(f"\n{'=' * 100}")
    print("【市值分布】信号股 vs 全市场（信号日流通市值，亿元）")
    print(f"{'=' * 100}")
    tot_sig = int(sum(sig_ns))
    tot_base = int(sum(base_ns))
    print(f"  各档占比（信号 {tot_sig:,} vs 全市场 {tot_base:,} 个观测）：")
    for b in range(nb):
        p_all = base_ns[b] / tot_base * 100 if tot_base else 0
        p_sig = sig_ns[b] / tot_sig * 100 if tot_sig else 0
        print(f"    {CAP_LABELS[b]:<12} 全市场 {p_all:5.1f}% ｜ 信号 {p_sig:5.1f}%")

    print(f"\n【结论】")
    big_diffs = [b for b in range(2, nb) if sig_ns[b] >= 10
                 and (sig_sums[b] / sig_ns[b] - base_sums[b] / base_ns[b]) > 0.01]
    if big_diffs:
        print(f"  大市值档（≥100亿）仍有正超额（≥1pp）的档数 = {len(big_diffs)}")
        print(f"  ⇒ 「缩量企稳」含独立于小市值的 alpha（非纯小市值别名），值得进一步复核")
    else:
        print(f"  大市值档（≥100亿）无正超额 ⇒ +2.79pp 主要由小市值贡献，策略价值有限")


if __name__ == "__main__":
    main()
