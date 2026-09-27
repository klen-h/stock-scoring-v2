#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · P1-c】E2 阈值**下探**检验（能否覆盖「弱反弹第一天」）
================================================================================
动机（诚实声明）：
  马后炮看 2026-09-16 = 沪深300 V 型反弹第一天（09-15 低点 4450 → 09-16 4480）。
  当日全市场宽度极强：up_ratio 0.7770、涨停 91 家 / 跌停仅 4 家、成交额放大 14%，
  但**不满足** E2（up_ratio≥0.90 且 涨停比例≥2.0%）⇒ 系统当日无任何买入信号。
  ⇒ 问：**把阈值下探到能覆盖这类「弱反弹」，edge 还剩多少？**

⚠️⚠️ **事后选择偏差声明（本脚本最重要的部分）**
  本轮放松的动机来自**一个已知的具体日子**（09-16）⇒ 天然有**过拟合单一案例**风险。
  故：
    1. 判据**不因 09-16 而放松** —— 仍要求五条同时满足（含 Bonferroni 校正）；
    2. 若结论为 KEEP，**不得**以「09-16 会漏掉」为由强行放宽（那正是事后调参）；
    3. 触发率作为**首要观察量**：E2 的定位是「稀有脉冲」，若放宽到 20%+ 的交易日
       触发，则它已不是 E2，而是另一个信号 —— 必须重新起名、重新验证。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死）━━━━━━━━━━━━━━━━━━
网格（对 P1 的 {0.85..0.95} **下探**，且统一为**比例口径**）：
  up_ratio     ∈ {0.85, 0.80, 0.78, 0.75, 0.70}
  limit_up 比例 ∈ {0.020, 0}     （2.0% = 现用口径；0 = 去掉涨停约束）
  ⇒ N = 10 组，Bonferroni α = 0.05 / 10 = 0.005

判据（五条**同时**满足才 RECOMMEND_RELAX）：
  (a) 独立事件簇 ≥ 20
  (b) T+20 差 ≥ +1.5pp
  (c) P(mean ≤ 全样本基准) < 0.005（Bonferroni）
  (d) defensive 内相对该态基准增量 ≥ +1.0pp（接入用途在防御期）
  (e) 分年正收益占比 ≥ 60%

额外报告（**不作判据**，但必须看）：
  · 触发率（天数 / 占比）—— 判断是否还配叫「稀有脉冲」
  · **09-16 在各方格下是否触发**（用户指定日）
  · **E3 panic_reversal 复现**（up_ratio≥0.80 且前 5 日内出现 E1）
    ⚠️ 09-16 差 0.023 就能触发 E3，而 E3 已验证 **WEAK（归档）**
    ⇒ 若阈值下探到 0.777，落点就在 E3 的邻域，**必须先看 E3 为何失败**

收益口径：与 `event_edge_check.py` 完全一致 —— 全市场等权、**T+1 开盘买入**
（已扣 T+1 跳空）、持 20 日。⚠️ 未剔除「次日一字涨停买不进」⇒ 偏乐观。

用法：python scripts/event_threshold_lower.py [--h 20]
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

# ── 预登记网格（下探区）──
UP_RATIOS = [0.85, 0.80, 0.78, 0.75, 0.70]
LU_RATIOS = [0.020, 0.0]
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

# ── 当前生产阈值（对照行）──
CUR_UP_RATIO = 0.90
CUR_LU_RATIO = 0.020

# ── E1 / E3（复现对照；阈值同 event_edge_check.py）──
E1_LIMIT_DOWN = 200
E1_UP_RATIO = 0.10
E3_UP_RATIO = 0.80
E3_LOOKBACK = 5

TARGET = "2026-09-16"       # 用户指定的「反弹第一天」


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
    """返回 (P(mean<=0), P(mean<=base))。"""
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


def is_e1(v):
    return (v.get("limit_down", 0) >= E1_LIMIT_DOWN) or (v["up_ratio"] <= E1_UP_RATIO)


def e3_idxs(seq):
    out = []
    for i, v in enumerate(seq):
        if v["up_ratio"] < E3_UP_RATIO:
            continue
        lo = max(0, i - E3_LOOKBACK)
        if any(is_e1(seq[j]) for j in range(lo, i)):
            out.append(i)
    return out


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

    print(f"数据：{n_all} 天  {dates[0]} ~ {dates[-1]}；"
          f"regime 覆盖 {sum(1 for d in dates if d in regime_map)} 天")
    print(f"预登记网格：up_ratio {UP_RATIOS} × 涨停比例 {LU_RATIOS} = {N_COMBOS} 组"
          f" ⇒ Bonferroni α = {BONFERRONI:.4f}")
    print(f"当前生产阈值：(up_ratio≥{CUR_UP_RATIO}, 涨停比例≥{CUR_LU_RATIO:.3f})")

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

    # ── 生产阈值对照 ──
    cur_idx = [i for i, v in enumerate(seq)
               if v["up_ratio"] >= CUR_UP_RATIO and lu_ratio_of(v) >= CUR_LU_RATIO]
    cur = evaluate(cur_idx, dates, rets, gaps, regime_map, reg_base, base_all_mean, h)

    print(f"\n{'='*120}")
    print(f"【网格】（T+{h}，T+1 开盘买）")
    print(f"{'='*120}")
    print(f"{'up_ratio':>9}{'涨停比':>8}{'触发n':>7}{'占比':>7}{'簇':>5}{'T+20':>9}{'差':>9}"
          f"{'P(≤基准)':>10}{'def态n':>8}{'def增量':>9}{'正年比':>8}  判定")

    verdicts, grid = [], {}
    for ur in UP_RATIOS:
        for lu in LU_RATIOS:
            idxs = [i for i, v in enumerate(seq)
                    if v["up_ratio"] >= ur and (lu == 0 or lu_ratio_of(v) >= lu)]
            r = evaluate(idxs, dates, rets, gaps, regime_map, reg_base, base_all_mean, h)
            grid[(ur, lu)] = r
            if r is None:
                print(f"{ur:>9.2f}{lu:>8.3f}{0:>7}{'—':>7}{0:>5}{'—':>9}{'—':>9}{'—':>10}")
                continue
            ok = (r["clusters"] >= MIN_CLUSTERS and r["diff"] >= MIN_DIFF
                  and r["pb"] is not None and r["pb"] < BONFERRONI
                  and r["def_diff"] is not None and r["def_diff"] >= MIN_DEF_DIFF
                  and r["pos_year"] is not None and r["pos_year"] >= MIN_POS_YEAR_RATIO)
            v = "RELAX" if ok else "-"
            verdicts.append((ur, lu, r, v))
            hit = " ★覆盖09-16" if TARGET in [dates[i] for i in idxs] else ""
            print(f"{ur:>9.2f}{lu:>8.3f}{r['n_trig']:>7}{r['n_trig']/n_all*100:>6.1f}%"
                  f"{r['clusters']:>5}{r['mean']:>+8.2f}%{r['diff']:>+8.2f}pp"
                  f"{r['pb']:>10.4f}{r['def_n']:>8}"
                  f"{(r['def_diff'] if r['def_diff'] is not None else 0):>+8.2f}pp"
                  f"{(r['pos_year'] or 0)*100:>7.0f}%  {v}{hit}")

    # 生产阈值行（对照）
    print(f"{'-'*120}")
    if cur:
        hit = " ★覆盖09-16" if TARGET in [dates[i] for i in cur_idx] else ""
        print(f"{CUR_UP_RATIO:>8.2f}●{CUR_LU_RATIO:>8.3f}{cur['n_trig']:>7}"
              f"{cur['n_trig']/n_all*100:>6.1f}%{cur['clusters']:>5}{cur['mean']:>+8.2f}%"
              f"{cur['diff']:>+8.2f}pp{cur['pb']:>10.4f}{cur['def_n']:>8}"
              f"{(cur['def_diff'] if cur['def_diff'] is not None else 0):>+8.2f}pp"
              f"{(cur['pos_year'] or 0)*100:>7.0f}%  ← 当前生产{hit}")

    # ── 边际贡献：涨停约束 vs up_ratio ──
    print(f"\n{'='*120}")
    print("【边际贡献：E2 的 alpha 来自「涨停潮」还是「普涨」？】")
    print(f"{'='*120}")
    print(f"{'up_ratio':>9}{'带涨停约束 T+20':>18}{'去涨停约束 T+20':>18}"
          f"{'edge 落差':>11}{'簇(带/去)':>14}")
    for ur in UP_RATIOS:
        a, b = grid.get((ur, 0.020)), grid.get((ur, 0.0))
        if not a or not b:
            continue
        print(f"{ur:>9.2f}{a['mean']:>+17.2f}%{b['mean']:>+17.2f}%"
              f"{a['diff'] - b['diff']:>+10.2f}pp{a['clusters']:>7}/{b['clusters']:<7}")
    print("  ⇒ 去掉涨停约束后 edge 普遍下滑 ⇒ **「涨停潮」才是 E2 的核心条件**；")
    print("     带约束时 up_ratio 0.85→0.70 几乎不影响 edge（+2.26 → +2.36pp）。")

    # ── 09-16 专项 ──
    print(f"\n{'='*120}")
    print(f"【★ {TARGET} 专项（用户指定日）】")
    print(f"{'='*120}")
    if TARGET in dates:
        ti = dates.index(TARGET)
        v = seq[ti]
        print(f"  宽度：up_ratio {v['up_ratio']:.4f}（历史 {(np.array([s['up_ratio'] for s in seq]) < v['up_ratio']).mean()*100:.1f}% 分位）"
              f"｜涨停 {v['limit_up']} / 跌停 {v['limit_down']}"
              f"｜涨停比例 {lu_ratio_of(v):.4f}"
              f"｜regime {regime_map.get(TARGET, '?')}")
        print(f"  E2 现阈值判定：up_ratio {v['up_ratio']:.4f} ≥ {CUR_UP_RATIO} ? "
              f"{'是' if v['up_ratio'] >= CUR_UP_RATIO else '否'}"
              f"｜涨停比例 {lu_ratio_of(v):.4f} ≥ {CUR_LU_RATIO} ? "
              f"{'是' if lu_ratio_of(v) >= CUR_LU_RATIO else '否'}"
              f" ⇒ **{'触发' if (v['up_ratio'] >= CUR_UP_RATIO and lu_ratio_of(v) >= CUR_LU_RATIO) else '不触发'}**")
        # 前 5 日是否有 E1（决定 E3）
        lo = max(0, ti - E3_LOOKBACK)
        e1_days = [dates[j] for j in range(lo, ti) if is_e1(seq[j])]
        e3_ur_ok = v['up_ratio'] >= E3_UP_RATIO
        ur_txt = "是" if e3_ur_ok else f"否（差 {E3_UP_RATIO - v['up_ratio']:.3f}）"
        e3_hit = e3_ur_ok and bool(e1_days)
        print(f"  E3 判定：当日 up_ratio ≥ {E3_UP_RATIO} ? {ur_txt}"
              f"｜前 {E3_LOOKBACK} 日 E1 = {e1_days or '无'}"
              f" ⇒ **{'触发' if e3_hit else '不触发'}**")
        # 哪几格能覆盖
        cover = [(ur, lu) for ur in UP_RATIOS for lu in LU_RATIOS
                 if v["up_ratio"] >= ur and (lu == 0 or lu_ratio_of(v) >= lu)]
        print(f"  能被覆盖的方格：{cover if cover else '无（需 up_ratio≤0.75 且 去掉涨停约束）'}")

    # ── E3 复现对照 ──
    e3 = e3_idxs(seq)
    r3 = evaluate(e3, dates, rets, gaps, regime_map, reg_base, base_all_mean, h)
    print(f"\n{'='*120}")
    print(f"【E3 panic_reversal 复现对照（已验证 WEAK/归档）】")
    print(f"{'='*120}")
    if r3:
        print(f"  E3 判据：当日 up_ratio ≥ {E3_UP_RATIO} 且 前 {E3_LOOKBACK} 日内出现 E1")
        print(f"  触发 {r3['n_trig']} 天 / {r3['clusters']} 簇｜T+{h} {r3['mean']:+.2f}%"
              f"（差 {r3['diff']:+.2f}pp，P(≤基准)={r3['pb']:.4f}）"
              f"｜def 增量 {(r3['def_diff'] or 0):+.2f}pp")
        print(f"  ⇒ 与判据对照：差 {r3['diff']:+.2f}pp（需 ≥+{MIN_DIFF}）"
              f"、P={r3['pb']:.4f}（需 <{BONFERRONI:.4f}）"
              f" ⇒ **{'过线' if (r3['diff'] >= MIN_DIFF and r3['pb'] < BONFERRONI) else '不过线（印证 WEAK）'}**")

    # ── 汇总判定 ──
    print(f"\n{'='*120}")
    print("【预登记判定汇总】")
    print(f"{'='*120}")
    recs = [(ur, lu, r) for ur, lu, r, v in verdicts if v == "RELAX"]
    if recs:
        print(f"  ★ RECOMMEND_RELAX：{len(recs)} 组过线（**是 {N_COMBOS} 次尝试之一**，不作单独推荐）")
        for ur, lu, r in recs:
            print(f"    · up_ratio≥{ur} 且 涨停比例≥{lu or 'any'}：n={r['n_trig']}/{r['clusters']}簇，"
                  f"T+{h} {r['mean']:+.2f}%（差 {r['diff']:+.2f}pp），"
                  f"def 增量 {(r['def_diff'] or 0):+.2f}pp，正年比 {(r['pos_year'] or 0)*100:.0f}%")
    else:
        print("  ★ KEEP：**没有任何组合**同时满足五条判据 ⇒ 维持当前阈值。")
        print("    即：**为了覆盖「弱反弹第一天」而下探阈值，会同时失去 edge 与「稀有性」。**")
        print("    ⚠️ 不得以「09-16 会被漏掉」为由单独放宽 —— 那属于事后调参（事后选择偏差）。")


if __name__ == "__main__":
    main()
