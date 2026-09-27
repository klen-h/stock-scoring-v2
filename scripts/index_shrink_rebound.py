#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【预登记回测】「缩量企稳反转」指数版（对象从个股换成指数）
================================================================================
背景：个股版（`shrink_rebound_strategy.py`）四条件组合得 +1.02pp（P=0.0084 显著但
      未过 +2pp 判据）⇒ 用户追问：**对象换成指数会怎样？**

诚实前置：
  · P1-e（`rebound_leading_indicators.py`）已验证过「缩量/涨停回暖」的**单变量**
    在指数上无效（ARCHIVE）；
  · P1-f（`drawdown_rebound_check.py`）验证过「阴跌启动」单条件，亦归档；
  · 本脚本验证的是 **四条件 AND 组合** 应用在指数上 —— 单变量无效≠组合无效，
    与个股版同逻辑。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死）━━━━━━━━━━━━━━━━━━
【对象】指数（sh000300 沪深300 / sh000905 中证500 / sh000852 中证1000 / sz399006 创业板指）
【条件】
  S1 超跌   : 指数 20 日累计收益（close 比值×100）≤ −8.0%
  S2 缩量   : 指数自身 volume 5日均量 ÷ 60日均量 ≤ 0.50
  T1 收红   : 指数当日 close > open
  T2 情绪   : 全市场涨停家数 > 前 20 日均值 × 1.5（市场级，同个股版）
【信号】    S1 ∧ S2 ∧ T1 ∧ T2（T 日收盘判定，无前视）
【入场】    T+1 开盘买入指数 → 持有 HOLD=10 交易日收盘卖出
【基准】    该指数全样本「T+1 开盘买持有 10 日」收益均值（无条件）
【判据】    三条同时满足 ⇒ GO（候选，仍需独立样本复核）
  (a) 超额 ≥ +2.0pp
  (b) 簇级 bootstrap P(均值 ≤ 基准) < 0.05（簇=信号日 gap≥20）
  (c) 独立簇 ≥ 15（指数择时样本天然少，对齐 P1-e 的 MIN_CLUSTERS）
【数据】    data/idx_daily.json（date/open/close/volume）+ event_history.json（limit_up）

用法：python scripts/index_shrink_rebound.py [--h 10]
================================================================================
"""
import argparse
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

CODES = ["sh000300", "sh000905", "sh000852", "sz399006"]

# ── 预登记参数（★ 改这里必须留痕）──
LOOKBACK = 20
MIN_DROP = -8.0        # 百分比（cum ×100）
VOL_SHORT = 5
VOL_LONG = 60
VOL_RATIO = 0.50
LU_BOOST = 1.5
HOLD = 10
MIN_DIFF = 2.0
ALPHA = 0.05
MIN_CLUSTERS = 15
CLUSTER_GAP = 20
B_BOOT = 10000
SEED = 42


def _ma(a, w):
    out = np.full(len(a), np.nan)
    c = np.cumsum(np.insert(a.astype(float), 0, 0.0))
    out[w - 1:] = (c[w:] - c[:-w]) / w
    return out


def load_index(code):
    with open(os.path.join(ROOT, "data", "idx_daily.json"), encoding="utf-8") as f:
        d = json.load(f)
    rows = sorted(d[code], key=lambda x: x["date"])
    return ([r["date"] for r in rows],
            np.array([r["open"] for r in rows], float),
            np.array([r["close"] for r in rows], float),
            np.array([r["volume"] for r in rows], float))


def _days(s):
    return (datetime.strptime(s, "%Y-%m-%d") - datetime(1970, 1, 1)).days


def cluster_ids(sorted_dates, gap=CLUSTER_GAP):
    if not sorted_dates:
        return []
    out = [[sorted_dates[0]]]
    for a, b in zip(sorted_dates, sorted_dates[1:]):
        if _days(b) - _days(a) >= gap:
            out.append([])
        out[-1].append(b)
    return out


def cluster_boot_p(clusters, rets_map, base, B=B_BOOT, seed=SEED):
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=HOLD)
    args = ap.parse_args()
    h = args.h

    ev = json.load(open(os.path.join(ROOT, "data", "event_history.json"), encoding="utf-8"))
    evd = sorted(ev)
    lu = np.array([ev[d].get("limit_up") or 0 for d in evd], float)
    lum = _ma(lu, 20)
    boost = set(evd[i] for i in range(20, len(evd)) if lu[i] > lum[i] * LU_BOOST and lum[i] > 0)
    print(f"T2 涨停回暖日 = {len(boost)} 天")
    print(f"预登记：S1 20日≤{MIN_DROP}% 且 S2 量比≤{VOL_RATIO} 且 T1 收红 且 T2 涨停回暖；"
          f"持有 {h} 日；判据 超额≥+{MIN_DIFF}pp 且 P<{ALPHA} 且 簇≥{MIN_CLUSTERS}")

    summary = []
    for code in CODES:
        try:
            dts, opens, closes, vols = load_index(code)
        except Exception as e:
            print(f"\n{code}: 跳过（{e}）")
            continue
        n = len(closes)
        cum = np.full(n, np.nan)
        cum[LOOKBACK:] = (closes[LOOKBACK:] / closes[:-LOOKBACK] - 1) * 100
        vr = _ma(vols, VOL_SHORT) / _ma(vols, VOL_LONG)
        red = closes > opens

        # 收益：T+1 开盘买持 h 日
        fwd = np.full(n, np.nan)
        idx = np.arange(VOL_LONG, n - 1 - h)
        ok = opens[idx + 1] > 0
        fwd[idx[ok]] = (closes[idx[ok] + 1 + h] / opens[idx[ok] + 1]) - 1
        base = float(np.mean([x for x in fwd if not np.isnan(x)]))

        sig = {}
        for i in range(VOL_LONG, n - 1 - h):
            if (cum[i] <= MIN_DROP and vr[i] <= VOL_RATIO and red[i]
                    and dts[i] in boost):
                sig[dts[i]] = float(fwd[i])

        if len(sig) < 10:
            print(f"\n{code}: 信号不足（{len(sig)}）")
            summary.append((code, len(sig), 0, np.nan, np.nan, np.nan, 0, False))
            continue

        sdates = sorted(sig)
        clusters = cluster_ids(sdates)
        strat = float(np.mean(list(sig.values())))
        diff = (strat - base) * 100
        p = cluster_boot_p(clusters, sig, base)
        wins = sum(1 for v in sig.values() if v > 0)
        ok = (diff >= MIN_DIFF and p is not None and p < ALPHA
              and len(clusters) >= MIN_CLUSTERS)
        summary.append((code, len(sig), len(clusters), strat, base, diff, p, ok))

        print(f"\n{'=' * 96}")
        print(f"【{code}】{dts[0]} ~ {dts[-1]}（{n} 天）")
        print(f"{'=' * 96}")
        print(f"  信号 = **{len(sig)} 次 / {len(clusters)} 簇**")
        print(f"  信号组 T+{h} = {strat * 100:+.2f}%  vs  基准 {base * 100:+.2f}%"
              f"  ⇒ 超额 **{diff:+.2f}pp**")
        print(f"  簇级 bootstrap P = {p:.4f}" if p is not None else "  P = None")
        print(f"  胜率 = {wins}/{len(sig)}（{wins/len(sig)*100:.1f}%）")
        print(f"  判定：{'★ GO（候选）' if ok else 'ARCHIVE'}")
        # 信号年份分布
        by_year = defaultdict(int)
        for d in sdates:
            by_year[d[:4]] += 1
        print("  信号按年: " + "  ".join(f"{y}:{by_year[y]}" for y in sorted(by_year)))

    print(f"\n{'=' * 96}")
    print("【汇总】")
    print(f"{'=' * 96}")
    print(f"  {'指数':<10}{'信号':>6}{'簇':>5}{'信号T+10':>11}{'基准':>9}{'超额':>9}{'P':>9}{'胜率':>8}  判定")
    for code, ns, nc, strat, base, diff, p, ok in summary:
        if ns < 10:
            print(f"  {code:<10}{ns:>6}{'—':>5}{'—':>11}{'—':>9}{'—':>9}{'—':>9}{'—':>8}  样本不足")
            continue
        print(f"  {code:<10}{ns:>6}{nc:>5}{strat*100:>+10.2f}%{base*100:>+8.2f}%"
              f"{diff:>+8.2f}pp{p:>9.4f}"
              f"{sum(1 for _ in [1]):>8}  {'★GO' if ok else '-'}")
    goes = [s for s in summary if s[7]]
    print()
    if goes:
        print(f"  ★ GO：{[s[0] for s in goes]} ⇒ 指数版组合有效（候选，需样本外复核）")
    else:
        print("  ★ 无指数 GO ⇒ 指数版四条件组合归档。")

    print("\n【已知简化】未扣成本；未考虑 T+1 无法成交（指数可近似忽略）；")
    print("  指数 volume 是「指数成分股成交量」，不同指数间量纲不可比（但同指数内时序可比）。")


if __name__ == "__main__":
    main()
