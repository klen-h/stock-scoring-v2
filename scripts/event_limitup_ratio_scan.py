#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · P1-d】E2 **核心变量**（涨停比例）阈值敏感性
================================================================================
背景（P1-c 的结论，`scripts/event_threshold_lower.py`）：
  已证明 **「涨停潮」才是 E2 的核心条件，`up_ratio` 是次要条件**：
    · 带涨停约束时，up_ratio 0.85→0.70 ⇒ edge 几乎不变（+2.26 ~ +2.36pp）；
    · **去掉涨停约束** ⇒ edge 立刻掉 ~1pp，且**独立簇从 60 崩到 4~36**。
  ⇒ 但 **`涨停比例 ≥ 2.0%` 这个阈值从未被当作可调参数检验过** ——
    它是 P1-b 用「50 家 ÷ 历史中位池子 2444 只」**换算**得来的（口径统一），
    不是优化出来的 ⇒ **最优性未知**。
  ⇒ 本脚本：扫**涨停比例阈值**，量出 edge 对它（核心变量）的敏感度。

动机之二（继续声明）：2026-09-16（V 型反弹第一天）涨停比例 **1.70%** / up_ratio 0.777，
  未触发 E2。若要覆盖它，需 **up_ratio ≤ 0.75 且 涨停比例 ≤ 1.5%**。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死）━━━━━━━━━━━━━━━━━━
网格（2 个 up_ratio 横截面 × 5 个涨停比例 = 10 组）：
  up_ratio  ∈ {0.90（生产）, 0.75（P1-c 已证无损的下限）}
  涨停比例   ∈ {1.00%, 1.25%, 1.50%, 1.75%, 2.00%（生产）}
  ⇒ N = 10，Bonferroni α = 0.05 / 10 = 0.005

判据（五条**同时**满足才 RECOMMEND）：
  (a) 独立事件簇 ≥ 20
  (b) T+20 差 ≥ +1.5pp
  (c) P(mean ≤ 全样本基准) < 0.005（Bonferroni）
  (d) defensive 内相对该态基准增量 ≥ +1.0pp
  (e) 分年正收益占比 ≥ 60%

额外报告（不作判据）：
  · **触发率** —— 涨停比例是核心变量，其触发率决定 E2 是否还配叫「稀有脉冲」
  · **09-16 在各方格下是否触发**
  · **边际分析**：固定 up_ratio，量 edge 对涨停比例阈值的弹性（弹性小 ⇒ 阈值不敏感）

收益口径：与 `event_edge_check.py` 一致 —— 全市场等权、**T+1 开盘买**（扣跳空）、持 20 日。
⚠️ 未剔除「次日一字涨停买不进」⇒ 偏乐观（涨停比例越低，纳入的日子越普通，此偏差越小）。

用法：python scripts/event_limitup_ratio_scan.py [--h 20]
================================================================================
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 预登记网格 ──
UP_RATIOS = [0.90, 0.75]
LU_RATIOS = [0.010, 0.0125, 0.015, 0.0175, 0.020]
N_COMBOS = len(UP_RATIOS) * len(LU_RATIOS)      # 10

# ── 预登记判据 ──
MIN_CLUSTERS = 20
MIN_DIFF = 1.5
ALPHA = 0.05
BONFERRONI = ALPHA / N_COMBOS                   # 0.005
MIN_DEF_DIFF = 1.0
MIN_POS_YEAR_RATIO = 0.60
CLUSTER_GAP = 20
B_BOOT = 10000

# ── 生产阈值 ──
CUR_UP_RATIO = 0.90
CUR_LU_RATIO = 0.020

TARGET = "2026-09-16"


def load_data(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    dates = sorted(raw)
    return (dates,
            [raw[d]["pct_mean"] for d in dates],
            [raw[d].get("gap_mean", 0.0) for d in dates],
            [raw[d] for d in dates])


def load_regime_map():
    p = os.path.join(ROOT, "data", "nb_history.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        return {d: (v.get("state_4") or v.get("state_3")) for d, v in m.items()}
    except Exception:
        return {}


def fwd_cum_open(rets, gaps, i, n):
    """T+1 开盘买入 → T+n 收盘（可执行口径，扣跳空）。"""
    if i + 1 + n > len(rets):
        return None
    entry = 1 + gaps[i + 1] / 100.0
    if entry <= 0.05:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        cum *= (1 + rets[k] / 100.0)
    return (cum / entry - 1) * 100


def boot_p(vals, base, B=B_BOOT, seed=42, chunk=2000):
    a = np.asarray(vals, dtype=float)
    n = len(a)
    if n == 0:
        return None, None
    rng = np.random.default_rng(seed)
    c0 = cb = tot = 0
    done = 0
    while done < B:
        m = min(chunk, B - done)
        idx = rng.integers(0, n, size=(m, n))
        means = a[idx].mean(axis=1)
        c0 += int(np.sum(means <= 0))
        cb += int(np.sum(means <= base))
        tot += m
        done += m
    return c0 / tot, cb / tot


def count_clusters(idxs):
    if not idxs:
        return 0
    c = 1
    for a, b in zip(idxs, idxs[1:]):
        if b - a >= CLUSTER_GAP:
            c += 1
    return c


def lu_ratio_of(v):
    denom = (v.get("up") or 0) + (v.get("down") or 0)
    return (v["limit_up"] / denom) if denom else 0.0


def evaluate(idxs, dates, rets, gaps, regime_map, reg_base, base_all_mean, h):
    vals = [x for x in (fwd_cum_open(rets, gaps, i, h) for i in idxs) if x is not None]
    if not vals:
        return None
    mean = sum(vals) / len(vals)
    p0, pb = boot_p(vals, base_all_mean)
    didx = [i for i in idxs if regime_map.get(dates[i]) == "defensive"]
    dvals = [x for x in (fwd_cum_open(rets, gaps, i, h) for i in didx) if x is not None]
    dmean = (sum(dvals) / len(dvals)) if dvals else None
    ddiff = ((dmean - reg_base["defensive"])
             if (dmean is not None and reg_base.get("defensive")) else None)
    by_year = defaultdict(list)
    for i in idxs:
        x = fwd_cum_open(rets, gaps, i, h)
        if x is not None:
            by_year[dates[i][:4]].append(x)
    pos = (sum(1 for y in by_year if sum(by_year[y]) / len(by_year[y]) > 0) / len(by_year)) \
        if by_year else None
    return {"n": len(vals), "n_trig": len(idxs), "clusters": count_clusters(idxs),
            "mean": mean, "diff": mean - base_all_mean, "p0": p0, "pb": pb,
            "def_n": len(dvals), "def_mean": dmean, "def_diff": ddiff, "pos_year": pos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, "data", "event_history.json"))
    ap.add_argument("--h", type=int, default=20)
    args = ap.parse_args()

    dates, rets, gaps, seq = load_data(args.json)
    regime_map = load_regime_map()
    h = args.h
    n_all = len(dates)
    urs = np.array([s["up_ratio"] for s in seq])
    lus = np.array([lu_ratio_of(s) for s in seq])

    print(f"数据：{n_all} 天  {dates[0]} ~ {dates[-1]}；"
          f"regime 覆盖 {sum(1 for d in dates if d in regime_map)} 天")
    print(f"网格：up_ratio {UP_RATIOS} × 涨停比例 {LU_RATIOS} = {N_COMBOS} 组"
          f" ⇒ Bonferroni α = {BONFERRONI:.4f}")
    print(f"生产阈值：(up_ratio≥{CUR_UP_RATIO}, 涨停比例≥{CUR_LU_RATIO:.3f})")

    base_all = [v for v in (fwd_cum_open(rets, gaps, i, h) for i in range(n_all))
                if v is not None]
    base_all_mean = sum(base_all) / len(base_all)
    reg_base = {}
    for st in ("defensive", "neutral_bearish", "neutral", "offensive"):
        vv = [x for x in (fwd_cum_open(rets, gaps, i, h) for i in range(n_all)
                          if regime_map.get(dates[i]) == st) if x is not None]
        reg_base[st] = sum(vv) / len(vv) if vv else None
    print(f"\n基准（T+{h}，T+1 开盘买）：全样本 +{base_all_mean:.2f}%；"
          f"defensive +{reg_base.get('defensive') or 0:.2f}%")

    print(f"\n{'='*122}")
    print(f"【涨停比例敏感性网格】（T+{h}，T+1 开盘买）")
    print(f"{'='*122}")
    print(f"{'up_ratio':>9}{'涨停比':>8}{'触发n':>7}{'占比':>7}{'簇':>5}{'T+20':>9}{'差':>9}"
          f"{'P(≤基准)':>10}{'def态n':>8}{'def增量':>9}{'正年比':>8}  判定")

    verdicts, grid = [], {}
    for ur in UP_RATIOS:
        for lu in LU_RATIOS:
            idxs = [i for i, v in enumerate(seq)
                    if v["up_ratio"] >= ur and lu_ratio_of(v) >= lu]
            r = evaluate(idxs, dates, rets, gaps, regime_map, reg_base, base_all_mean, h)
            grid[(ur, lu)] = r
            if r is None:
                print(f"{ur:>9.2f}{lu:>8.4f}{0:>7}{'—':>7}{0:>5}{'—':>9}{'—':>9}{'—':>10}")
                continue
            ok = (r["clusters"] >= MIN_CLUSTERS and r["diff"] >= MIN_DIFF
                  and r["pb"] is not None and r["pb"] < BONFERRONI
                  and r["def_diff"] is not None and r["def_diff"] >= MIN_DEF_DIFF
                  and r["pos_year"] is not None and r["pos_year"] >= MIN_POS_YEAR_RATIO)
            v = "RELAX" if ok else "-"
            verdicts.append((ur, lu, r, v))
            is_cur = (ur == CUR_UP_RATIO and abs(lu - CUR_LU_RATIO) < 1e-9)
            tie = " ★覆盖09-16" if TARGET in [dates[i] for i in idxs] else ""
            mark = " ●" if is_cur else ""
            print(f"{ur:>9.2f}{lu:>8.4f}{r['n_trig']:>7}{r['n_trig']/n_all*100:>6.1f}%"
                  f"{r['clusters']:>5}{r['mean']:>+8.2f}%{r['diff']:>+8.2f}pp"
                  f"{r['pb']:>10.4f}{r['def_n']:>8}"
                  f"{(r['def_diff'] if r['def_diff'] is not None else 0):>+8.2f}pp"
                  f"{(r['pos_year'] or 0)*100:>7.0f}%  {v}{mark}{tie}")

    # ── 边际分析：edge 对涨停比例阈值的弹性 ──
    print(f"\n{'='*122}")
    print("【边际分析：edge 对「涨停比例」阈值的弹性】（固定 up_ratio）")
    print(f"{'='*122}")
    for ur in UP_RATIOS:
        ref = grid.get((ur, CUR_LU_RATIO))
        print(f"  up_ratio≥{ur}（以生产阈值 2.00% 为参照）：")
        for lu in LU_RATIOS:
            g = grid.get((ur, lu))
            if not g:
                continue
            dd = (g["diff"] - ref["diff"]) if ref else 0.0
            dm = (g["mean"] - ref["mean"]) if ref else 0.0
            tag = "← 生产" if abs(lu - CUR_LU_RATIO) < 1e-9 else ""
            print(f"    涨停比≥{lu:.4f}：T+{h} {g['mean']:+.2f}%（差 {g['diff']:+.2f}pp，"
                  f"相对生产 {dm:+.2f}pp）｜触发 {g['n_trig']:>5} 天"
                  f"({g['n_trig']/n_all*100:>4.1f}%)｜簇 {g['clusters']:>3} {tag}")

    # ── 09-16 专项 ──
    print(f"\n{'='*122}")
    print(f"【★ {TARGET} 专项】")
    print(f"{'='*122}")
    if TARGET in dates:
        ti = dates.index(TARGET)
        v = seq[ti]
        print(f"  宽度：up_ratio {v['up_ratio']:.4f}（历史 {(urs < v['up_ratio']).mean()*100:.1f}% 分位）"
              f"｜涨停 {v['limit_up']} / 跌停 {v['limit_down']}"
              f"｜**涨停比例 {lu_ratio_of(v):.4f}**"
              f"（历史 {(lus < lu_ratio_of(v)).mean()*100:.1f}% 分位）"
              f"｜regime {regime_map.get(TARGET, '?')}")
        cover = [(ur, lu) for ur in UP_RATIOS for lu in LU_RATIOS
                 if v["up_ratio"] >= ur and lu_ratio_of(v) >= lu]
        print(f"  能被覆盖的方格：{cover if cover else '无'}")
        print(f"  ⇒ 覆盖条件 = up_ratio≤0.75 **且** 涨停比例≤0.015（其涨停比例 {lu_ratio_of(v):.4f}）")

    # ── 汇总判定 ──
    print(f"\n{'='*122}")
    print("【预登记判定汇总】")
    print(f"{'='*122}")
    recs = [(ur, lu, r) for ur, lu, r, v in verdicts if v == "RELAX"]
    if recs:
        print(f"  ★ 过线组合 {len(recs)}/{N_COMBOS}（**是 {N_COMBOS} 次尝试之一**，不作单独推荐）：")
        for ur, lu, r in recs:
            print(f"    · up_ratio≥{ur} 且 涨停比例≥{lu:.4f}：n={r['n_trig']}/{r['clusters']}簇，"
                  f"T+{h} {r['mean']:+.2f}%（差 {r['diff']:+.2f}pp），"
                  f"def 增量 {(r['def_diff'] or 0):+.2f}pp，正年比 {(r['pos_year'] or 0)*100:.0f}%")
        print(f"\n  ⚠️ 多组过线 ⇒ 说明**该阈值不敏感**（一片平台）⇒ 维持现值是安全的，")
        print(f"     但**不得**因「09-16 会漏掉」而单点下调（事后调参）。")
    else:
        print("  ★ 无组合过线 ⇒ 维持生产阈值。")


if __name__ == "__main__":
    main()
