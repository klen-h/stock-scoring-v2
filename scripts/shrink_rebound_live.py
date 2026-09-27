#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【落地版】「缩量企稳反转」扣成本 + 剔 ST/一字板 后，真实超额还剩多少？
================================================================================
背景：市值中性检验（`market_cap_neutral.py`）确认该 alpha 在中小市值（20亿~1000亿）
      真实存在（非小市值别名）。但此前口径有 3 处偏乐观：
        · 未扣交易成本（个股双边佣金+印花税+滑点+冲击）
        · 未剔 ST（5% 涨跌停、基本面差）
        · 未剔「T+1 一字涨停买不进」

本脚本把这 3 处补齐，看 T+20 的超额扣完后**还剩多少**。

【预登记（写死）】
  剔 ST      : 信号日 is_st == 1 → 跳过
  剔一字板   : T+1 开盘价 ≥ 涨停价 × 0.995（开盘即涨停，买不进）→ 跳过
  扣成本     : 双边 0.30%（佣金+印花税+滑点，中小市值中性偏保守），净收益 = 收益 − 0.003
  市值分档   : 同 market_cap_neutral（<20/20-50/50-100/100-300/300-1000/>1000 亿）
  持有       : T+20（最强持有期）
【数据】    data/zzshare_daily.db（含 is_st / high_limit / volume / turnover_rate）
【输出】    每市值档：原始超额 vs 落地版超额（扣成本+剔除后）

用法：python scripts/shrink_rebound_live.py
================================================================================
"""
import json
import os
import sqlite3
import sys
import time

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
HOLD = 20
COST = 0.003                 # 双边成本 0.30%
EXCLUDE_ST = True
EXCLUDE_LIMIT = True
LIMIT_TH = 0.995             # T+1 open >= high_limit*0.995 ⇒ 买不进
CAP_BINS = [0, 20, 50, 100, 300, 1000, float("inf")]
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

    conn = sqlite3.connect(ZZDB)
    codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM daily ORDER BY code")]

    nb = len(CAP_LABELS)
    base_sums = np.zeros(nb)
    base_ns = np.zeros(nb, dtype=int)
    raw_sums = np.zeros(nb)      # 原始（未扣成本、未剔除）
    raw_ns = np.zeros(nb, dtype=int)
    live_sums = np.zeros(nb)     # 落地（扣成本 + 剔 ST/一字板）
    live_ns = np.zeros(nb, dtype=int)
    n_skip_st = n_skip_limit = 0
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
            base_sums[b] += h_ret[i]
            base_ns[b] += 1
            if (cum[i] <= MIN_DROP and vr[i] <= VOL_RATIO and red[i]
                    and dates[i] in boost):
                n_sig += 1
                # 原始口径（同 market_cap_neutral）
                raw_sums[b] += h_ret[i]
                raw_ns[b] += 1
                # 落地口径：剔 ST + 剔一字板
                if EXCLUDE_ST and is_st[i] == 1:
                    n_skip_st += 1
                    continue
                if EXCLUDE_LIMIT and high_lim[i + 1] > 0 and \
                        opens[i + 1] >= high_lim[i + 1] * LIMIT_TH:
                    n_skip_limit += 1
                    continue
                live_sums[b] += h_ret[i] - COST    # 扣双边成本
                live_ns[b] += 1

        if (k + 1) % 1000 == 0:
            print(f"  进度 {k + 1}/{len(codes)}，{time.time() - t0:.0f}s，信号 {n_sig}")

    conn.close()

    print(f"\n{'=' * 106}")
    print("【落地版】扣成本 0.30% + 剔 ST + 剔 T+1 一字板 后的真实超额（T+20）")
    print(f"{'=' * 106}")
    print(f"剔除统计：剔 ST = {n_skip_st}，剔 T+1 一字板 = {n_skip_limit}（总信号 {n_sig}）")
    print(f"\n{'市值档':<12}{'落地n':>7}{'原始超额':>10}{'落地超额':>10}{'成本+剔除损耗':>13}  判定")
    print("-" * 106)
    for b in range(nb):
        if base_ns[b] == 0:
            continue
        base = base_sums[b] / base_ns[b]
        if live_ns[b] < 10:
            print(f"{CAP_LABELS[b]:<12}{live_ns[b]:>7}  （落地样本<10，跳过）")
            continue
        raw = raw_sums[b] / raw_ns[b] - base
        live = live_sums[b] / live_ns[b] - base
        loss = (raw - live) * 100
        ok = "★仍有真实超额" if live > 0.01 else "扣完为负"
        print(f"{CAP_LABELS[b]:<12}{live_ns[b]:>7}{raw*100:>+9.2f}pp{live*100:>+9.2f}pp"
              f"{loss:>+12.2f}pp  {ok}")

    # 汇总：中小市值（20~1000亿）整体
    mid_b = [b for b in range(1, 5)]
    raw_mid = (raw_sums[mid_b].sum() / raw_ns[mid_b].sum()
               - base_sums[mid_b].sum() / base_ns[mid_b].sum()) * 100
    live_mid = (live_sums[mid_b].sum() / live_ns[mid_b].sum()
                - base_sums[mid_b].sum() / base_ns[mid_b].sum()) * 100
    print(f"\n{'=' * 106}")
    print("【汇总】中小市值（20~1000亿）")
    print(f"{'=' * 106}")
    print(f"  原始超额 = {raw_mid:+.2f}pp  →  落地（扣成本+剔除）超额 = {live_mid:+.2f}pp")
    print(f"  扣成本+剔除损耗 = {raw_mid - live_mid:+.2f}pp")
    print(f"\n【结论】")
    if live_mid > 1.0:
        print(f"  落地后仍 ≥ +1pp ⇒ 「缩量企稳」在中小市值有**可执行的真实 alpha**"
              f"（需独立样本/实盘复核）")
    else:
        print(f"  落地后 < +1pp ⇒ 扣完成本后 edge 太薄，不值得上线（ARCHIVE）")


if __name__ == "__main__":
    main()
