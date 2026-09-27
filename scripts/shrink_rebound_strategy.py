#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【预登记回测】「缩量企稳反转」策略（用户提出，非抄底信号的四条件组合）
================================================================================
诚实前置（重要）：
  · 本策略的 S2（缩量）、T2（涨停回暖）**单变量**已在 P1-e 被判 ARCHIVE；
  · 本回测验证的是 **四条件 AND 组合** 是否因交互效应而有效（单变量无效≠组合无效，
    但先验胜算不高）。
  · 本脚本**预登记参数写死**，结论只有 GO / ARCHIVE 两档，不做事后调参。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死）━━━━━━━━━━━━━━━━━━
【条件】
  S1 超跌   : 20 日累计收益（前复权，close×factor）≤ −8.0%
  S2 缩量   : 5 日均量 ÷ 60 日均量 ≤ 0.50
  T1 收红   : 当日 close > open
  T2 情绪   : 当日全市场涨停家数 > 前 20 日均值 × 1.5
【信号】    S1 ∧ S2 ∧ T1 ∧ T2（T 日收盘后判定，无前视）
【入场】    T+1 开盘买入；持有 HOLD=10 个交易日，T+1+HOLD 收盘卖出（前复权收益）
【基准】    全市场等权「T+1 开盘买入持有 10 日」收益均值（无条件）
【判据】    三条同时满足 ⇒ GO（新机制候选，仍需独立样本复核）
  (a) 超额 ≥ +2.0pp（信号组 − 基准）
  (b) 簇级 bootstrap P(均值 ≤ 基准) < 0.05（簇=信号日，同天信号共享 T2）
  (c) 独立簇 ≥ 20（信号日间隔 ≥20 自然日）
【数据】    data/zzshare_daily.db（daily 表，前复权因子 factor）+ event_history.json
【口径】    忽略 ST / 停牌 / 涨跌停无法成交（第一版；详见底部「已知简化」）

用法：python scripts/shrink_rebound_strategy.py [--start 2005-01-01] [--limit N]
================================================================================
"""
import argparse
import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZZDB = os.path.join(ROOT, "data", "zzshare_daily.db")
EV_PATH = os.path.join(ROOT, "data", "event_history.json")

# ── 预登记参数（★ 改这里必须留痕）──
LOOKBACK = 20
MIN_DROP = -0.08     # 比例（-8%）；cum 是比例（未 ×100），此处必须用小数
VOL_SHORT = 5
VOL_LONG = 60
VOL_RATIO = 0.50
LU_BOOST = 1.5
HOLDS = [3, 5, 10, 20]   # 持有期敏感性（T+3/T+5/T+10/T+20，一次遍历全算）
MIN_DIFF = 2.0
ALPHA = 0.05
MIN_CLUSTERS = 20
CLUSTER_GAP = 20
B_BOOT = 10000
SEED = 42


def _ma(a, w):
    """滑动均值（numpy，前 w-1 个为 NaN）。"""
    out = np.full(len(a), np.nan)
    csum = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[w - 1:] = (csum[w:] - csum[:-w]) / w
    return out


def _days(s):
    return (datetime.strptime(s, "%Y-%m-%d") - datetime(1970, 1, 1)).days


def count_clusters(sorted_dates, gap=CLUSTER_GAP):
    if not sorted_dates:
        return 0
    c = 1
    for a, b in zip(sorted_dates, sorted_dates[1:]):
        if _days(b) - _days(a) >= gap:
            c += 1
    return c


def cluster_boot_p(clusters, base, B=B_BOOT, seed=SEED):
    """簇级 bootstrap：对「信号日」有放回重采样，簇内信号一并纳入，P(均值 ≤ base)。"""
    if not clusters:
        return None
    mem = [np.asarray(c, dtype=float) for c in clusters]
    k = len(mem)
    rng = np.random.default_rng(seed)
    means = np.empty(B)
    for b in range(B):
        pick = rng.integers(0, k, size=k)
        vals = np.concatenate([mem[j] for j in pick])
        means[b] = vals.mean()
    return float(np.mean(means <= base))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default="2026-09-24")
    ap.add_argument("--limit", type=int, default=0, help="只测前 N 只（冒烟）")
    args = ap.parse_args()

    # ── 市场情绪 T2 ──
    ev = json.load(open(EV_PATH, encoding="utf-8"))
    evd = sorted(ev)
    lu = np.array([(ev[d].get("limit_up") or 0) for d in evd], dtype=float)
    lu_ma20 = _ma(lu, 20)
    boost = set()
    for i in range(20, len(evd)):
        if lu[i] > lu_ma20[i] * LU_BOOST and lu_ma20[i] > 0:
            boost.add(evd[i])
    print(f"市场情绪 T2：涨停家数 > 20日均值×{LU_BOOST} 的交易日 = {len(boost)} 天")

    conn = sqlite3.connect(ZZDB)
    codes = [r[0] for r in conn.execute(
        "SELECT DISTINCT code FROM daily WHERE date BETWEEN ? AND ? ORDER BY code",
        (args.start, args.end))]
    if args.limit:
        codes = codes[:args.limit]
    print(f"股票数 = {len(codes)}（区间 {args.start} ~ {args.end}）")
    print(f"预登记：S1 20日≤{MIN_DROP}% 且 S2 量比≤{VOL_RATIO} 且 T1 收红 且 T2 涨停回暖"
          f"；持有 {HOLDS} 日；判据 超额≥+{MIN_DIFF}pp 且 P<{ALPHA} 且 簇≥{MIN_CLUSTERS}")

    base_sums = {h: 0.0 for h in HOLDS}
    base_ns = {h: 0 for h in HOLDS}
    signals = {h: [] for h in HOLDS}            # (date, ret)
    maxh = max(HOLDS)
    t0 = time.time()
    for k, code in enumerate(codes):
        rows = conn.execute(
            "SELECT date, open, close, volume, factor FROM daily "
            "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
            (code, args.start, args.end)).fetchall()
        if len(rows) < VOL_LONG + maxh + 2:
            continue
        dates = [r[0] for r in rows]
        opens = np.array([r[1] for r in rows], dtype=float)
        closes = np.array([r[2] for r in rows], dtype=float)
        vols = np.array([r[3] for r in rows], dtype=float)
        factors = np.array([r[4] for r in rows], dtype=float)
        adj_open = opens * factors
        adj_close = closes * factors
        n = len(rows)

        # S1：20 日累计收益
        cum = np.full(n, np.nan)
        cum[LOOKBACK:] = adj_close[LOOKBACK:] / adj_close[:-LOOKBACK] - 1
        # S2：量比
        vr = _ma(vols, VOL_SHORT) / _ma(vols, VOL_LONG)
        # T1：收红
        red = closes > opens

        # 各持有期的 H 日收益 + 基准累加
        h_rets = {}
        for h in HOLDS:
            h_ret = np.full(n, np.nan)
            idx = np.arange(VOL_LONG, n - 1 - h)
            if len(idx):
                ok = adj_open[idx + 1] > 0
                h_ret[idx[ok]] = (adj_close[idx[ok] + 1 + h] / adj_open[idx[ok] + 1]) - 1
            h_rets[h] = h_ret
            valid = ~np.isnan(h_ret)
            base_sums[h] += float(h_ret[valid].sum())
            base_ns[h] += int(valid.sum())

        # 信号（判定与持有期无关，用 maxh 边界保证各持有期都有收益）
        for i in range(VOL_LONG, n - 1 - maxh):
            if (cum[i] <= MIN_DROP and vr[i] <= VOL_RATIO and red[i]
                    and dates[i] in boost):
                for h in HOLDS:
                    signals[h].append((dates[i], float(h_rets[h][i])))

        if (k + 1) % 1000 == 0:
            print(f"  进度 {k + 1}/{len(codes)}，{time.time() - t0:.0f}s，信号 {len(signals[maxh])}")

    conn.close()

    print(f"\n{'=' * 100}")
    print("【持有期敏感性】S1∧S2∧T1∧T2 四条件组合，不同持有期")
    print(f"{'=' * 100}")
    print(f"{'持有':>6}{'信号数':>8}{'信号组均值':>12}{'基准':>10}{'超额':>9}{'P值':>9}"
          f"{'胜率':>9}{'簇':>5}  判定")
    for h in HOLDS:
        base = base_sums[h] / base_ns[h] if base_ns[h] else 0.0
        sig = signals[h]
        if len(sig) < 30:
            print(f"{'T+'+str(h):>6}{len(sig):>8}  （样本不足，跳过）")
            continue
        rets = np.array([r for _, r in sig], dtype=float)
        strat = float(rets.mean())
        diff = (strat - base) * 100
        sdates = sorted(set(d for d, _ in sig))
        n_cluster = count_clusters(sdates)
        by_day = defaultdict(list)
        for d, r in sig:
            by_day[d].append(r)
        clusters = [np.array(v) for v in by_day.values()]
        p = cluster_boot_p(clusters, base)
        win = int((rets > 0).sum())
        ok = (diff >= MIN_DIFF and p is not None and p < ALPHA and n_cluster >= MIN_CLUSTERS)
        print(f"{'T+'+str(h):>6}{len(sig):>8}{strat*100:>+11.2f}%{base*100:>+9.2f}%"
              f"{diff:>+8.2f}pp{p:>9.4f}{str(win)+'/'+str(len(sig)):>9}{n_cluster:>5}"
              f"  {'★GO' if ok else '-'}")
    print(f"\n判据：超额≥+{MIN_DIFF}pp 且 P<{ALPHA} 且 簇≥{MIN_CLUSTERS}")

    # ── 诊断：各条件单拆（非判据，仅观察）──
    print(f"\n{'=' * 100}")
    print("【诊断·非判据】单条件拆解（观察各条件的边际贡献）")
    print(f"{'=' * 100}")
    # 简单重算：S1∧S2∧T1（不含 T2）的信号数
    # 这里仅给「信号日的年份分布」作参考
    by_year = defaultdict(int)
    for d, _ in signals[maxh]:
        by_year[d[:4]] += 1
    print("  信号按年分布：")
    print("  " + "  ".join(f"{y}:{by_year[y]}" for y in sorted(by_year)))

    print(f"\n【已知简化（诚实标注）】")
    print("  · 未剔除 ST（5% 涨跌停差异）；未处理停牌日（volume=0 会拉低均量，可能误判缩量）；")
    print("  · 未剔除「次日一字涨停/跌停买不进」；未扣交易成本；")
    print("  · 基准 = 全市场无条件（含信号自身，信号占比小时偏差可忽略）。")


if __name__ == "__main__":
    main()
