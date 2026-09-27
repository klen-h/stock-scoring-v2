#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · P1】E2 阈值敏感性分析（**独立预登记**）
================================================================================
问题：
  E2 政策脉冲（原阈值 `up_ratio >= 0.95 且 limit_up >= 50`）经预登记检验 PASS
  （T+20 差 +3.02pp，防御内增量 +2.17pp），但**极稀有**：
  21 年仅 181 天，且 2021-2023 三年零事件、2026 年 0 事件。
  ⇒ 问：**放宽阈值能否在保持统计显著的前提下提高触发率？**

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死，禁止事后改格）━━━━━━━━━━━━━━━━━━
扫描网格（5 × 4 = 20 组）：
  up_ratio ∈ {0.85, 0.88, 0.90, 0.92, 0.95}
  limit_up ∈ {50, 100, 150, 0}（0 = 不设涨停约束）
  （原阈值 = (0.95, 50)，作为基准行）

判定标准（对每个组合独立判定，三选一）：
  ★ RECOMMEND_RELAX —— 五条**同时**满足才推荐放宽：
    (a) 独立事件簇 >= 20
    (b) T+20 差 >= +1.5pp（较原 +2.0 略降：放宽必然稀释 edge）
    (c) 双侧检验 P(mean <= 基准) < 0.05 / 20 = 0.0025（**Bonferroni 校正**，
        因本轮扫描了 20 个组合）
    (d) defensive 内相对「该态基准」增量 >= +1.0pp（接入的实际用途在防御期）
    (e) 分年正收益占比 >= 60%
  KEEP —— 所有组合都不满足 ⇒ **维持 (0.95, 50)**，承认 E2 天生稀有
  INSUFFICIENT —— 网格无法评估

⚠️ 多重比较声明：本脚本扫描 20 个组合，Bonferroni 已写入判据 (c)。
   若某组合"刚好过线"，结论中必须标注其为 20 次尝试之一，**不作单独推荐**。
⚠️ 收益口径与 `event_edge_check.py` 完全一致：全市场等权、**T+1 开盘买入**
   （已扣 T+1 跳空 gap_mean）、持有 1/5/10/20 日。
⚠️ 阈值改动若被采纳，必须同步更新 `event_edge_check.py` 与
   `app/events/signal.py` 的常量，并在两处留痕（本项目「改阈值必须留痕」纪律）。

用法：python scripts/event_threshold_sensitivity.py [--horizons 1 5 20]
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
UP_RATIOS = [0.85, 0.88, 0.90, 0.92, 0.95]
LIMIT_UPS = [50, 100, 150, 0]        # 0 = 不设涨停约束
N_COMBOS = len(UP_RATIOS) * len(LIMIT_UPS)   # 20

# ── 预登记判据 ──
MIN_CLUSTERS = 20
MIN_DIFF = 1.5
ALPHA = 0.05
BONFERRONI = ALPHA / N_COMBOS        # 0.0025
MIN_DEF_DIFF = 1.0
MIN_POS_YEAR_RATIO = 0.60
CLUSTER_GAP = 20
B_BOOT = 10000


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
    """返回 (P(mean<=0), P(mean<=base))——有放回重采样，分块控制内存。"""
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, "data", "event_history.json"))
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 5, 20])
    ap.add_argument("--h-main", type=int, default=20)
    args = ap.parse_args()

    dates, rets, gaps, seq = load_data(args.json)
    regime_map = load_regime_map()
    h = args.h_main

    print(f"数据：{len(dates)} 天  {dates[0]} ~ {dates[-1]}；regime 覆盖 "
          f"{sum(1 for d in dates if d in regime_map)} 天")
    print(f"预登记网格：up_ratio {UP_RATIOS} × limit_up {LIMIT_UPS} = {N_COMBOS} 组")
    print(f"判据：簇≥{MIN_CLUSTERS} 且 差≥+{MIN_DIFF}pp 且 P(mean≤基准)<{BONFERRONI:.4f}"
          f"（Bonferroni）且 defensive增量≥+{MIN_DEF_DIFF}pp 且 正年比≥{MIN_POS_YEAR_RATIO:.0%}")

    # 基准（两种口径 + regime 内）
    base_all = [v for v in (fwd_cum_open(rets, gaps, i, h) for i in range(len(dates)))
                if v is not None]
    base_all_mean = sum(base_all) / len(base_all)
    reg_base = {}
    for st in ("defensive", "neutral_bearish", "neutral", "offensive"):
        vv = [x for x in (fwd_cum_open(rets, gaps, i, h)
                          for i in range(len(dates))
                          if regime_map.get(dates[i]) == st) if x is not None]
        reg_base[st] = sum(vv) / len(vv) if vv else None
    print(f"\n基准（T+{h}，T+1 开盘买）：全样本 +{base_all_mean:.2f}%；"
          f"defensive +{reg_base.get('defensive', 0):.2f}%")

    # ── 网格扫描 ──
    rows = []
    for ur in UP_RATIOS:
        for lu in LIMIT_UPS:
            idxs = [i for i, v in enumerate(seq)
                    if v["up_ratio"] >= ur and (lu == 0 or v["limit_up"] >= lu)]
            vals = [x for x in (fwd_cum_open(rets, gaps, i, h) for i in idxs)
                    if x is not None]
            if not vals:
                rows.append({"ur": ur, "lu": lu, "n": 0, "clusters": 0,
                             "mean": None, "diff": None, "p0": None, "pb": None,
                             "def_n": 0, "def_mean": None, "def_diff": None,
                             "pos_year": None,
                             "is_original": (ur == 0.95 and lu == 50)})
                continue
            mean = sum(vals) / len(vals)
            p0, pb = boot_p(vals, base_all_mean)
            # defensive 内
            didx = [i for i in idxs if regime_map.get(dates[i]) == "defensive"]
            dvals = [x for x in (fwd_cum_open(rets, gaps, i, h) for i in didx)
                     if x is not None]
            dmean = (sum(dvals) / len(dvals)) if dvals else None
            ddiff = (dmean - reg_base["defensive"]) if (dmean is not None
                                                        and reg_base.get("defensive")) else None
            # 分年正收益比
            by_year = defaultdict(list)
            for i in idxs:
                x = fwd_cum_open(rets, gaps, i, h)
                if x is not None:
                    by_year[dates[i][:4]].append(x)
            pos = (sum(1 for y in by_year if sum(by_year[y]) / len(by_year[y]) > 0)
                   / len(by_year)) if by_year else None
            rows.append({
                "ur": ur, "lu": lu, "n": len(vals), "clusters": count_clusters(idxs),
                "mean": mean, "diff": mean - base_all_mean, "p0": p0, "pb": pb,
                "def_n": len(dvals), "def_mean": dmean, "def_diff": ddiff,
                "pos_year": pos, "years": sorted(by_year),
                "is_original": (ur == 0.95 and lu == 50),
            })

    # ── 输出网格表 ──
    print(f"\n{'='*112}")
    print(f"【E2 阈值敏感性网格】（T+{h}，T+1 开盘买；★=原阈值）")
    print(f"{'='*112}")
    print(f"{'up_ratio':>9}{'limit_up':>9}{'事件n':>7}{'簇':>5}{'T+20':>9}{'差':>9}"
          f"{'P(≤基准)':>10}{'def态n':>8}{'def增量':>9}{'正年比':>8}  判定")
    verdicts = []
    for r in rows:
        if not r["n"]:
            print(f"{r['ur']:>9.2f}{r['lu']:>9}{0:>7}{0:>5}{'—':>9}{'—':>9}{'—':>10}")
            verdicts.append((r, "NO_DATA"))
            continue
        # 判定
        ok = (r["clusters"] >= MIN_CLUSTERS and r["diff"] >= MIN_DIFF
              and r["pb"] is not None and r["pb"] < BONFERRONI
              and r["def_diff"] is not None and r["def_diff"] >= MIN_DEF_DIFF
              and r["pos_year"] is not None and r["pos_year"] >= MIN_POS_YEAR_RATIO)
        v = "RELAX" if ok else "-"
        verdicts.append((r, v))
        star = "★" if r["is_original"] else " "
        print(f"{r['ur']:>8.2f}{star}{r['lu']:>9}{r['n']:>7}{r['clusters']:>5}"
              f"{r['mean']:>+8.2f}%{r['diff']:>+8.2f}pp{r['pb']:>10.4f}"
              f"{r['def_n']:>8}{r['def_diff']:>+8.2f}pp"
              f"{r['pos_year']*100:>7.0f}%  {v}")

    # ── 按年触发次数（看放松后分布是否更均匀）──
    print(f"\n{'='*112}")
    print("【按年触发次数】（判断放松后是否摆脱「三年零事件」）")
    print(f"{'='*112}")
    years = sorted({d[:4] for d in dates})
    show = [(0.95, 50), (0.92, 50), (0.90, 50), (0.90, 0), (0.85, 0)]
    hdr = f"{'阈值':<16}" + "".join(f"{y[-2:]:>4}" for y in years)
    print(hdr)
    for ur, lu in show:
        idxs = [i for i, v in enumerate(seq)
                if v["up_ratio"] >= ur and (lu == 0 or v["limit_up"] >= lu)]
        cnt = defaultdict(int)
        for i in idxs:
            cnt[dates[i][:4]] += 1
        label = f"up>={ur},lu>={lu}" if lu else f"up>={ur},lu=any"
        line = f"{label:<16}" + "".join(f"{cnt.get(y, 0):>4}" for y in years)
        print(line)

    # ── 汇总判定 ──
    print(f"\n{'='*112}")
    print("【预登记判定汇总】")
    print(f"{'='*112}")
    recs = [(r, v) for r, v in verdicts if v == "RELAX"]
    if recs:
        print(f"  ★ RECOMMEND_RELAX：{len(recs)} 个组合过线（{N_COMBOS} 次尝试中的一员）")
        for r, _ in recs:
            print(f"    · up_ratio>={r['ur']} 且 limit_up>={r['lu'] or 'any'}："
                  f"n={r['n']}／{r['clusters']}簇，T+{h} {r['mean']:+.2f}%"
                  f"（差 {r['diff']:+.2f}pp），def 增量 {r['def_diff']:+.2f}pp，"
                  f"正年比 {r['pos_year']*100:.0f}%，P(≤基准)={r['pb']:.4f}")
    else:
        print("  ★ KEEP：**没有任何组合**同时满足五条判据 ⇒ 维持原阈值 (0.95, 50)。")
        print("    E2 天生稀有是数据事实，不是阈值设置失误；因「太少」放宽 = 事后调参。")

    print(f"\n  注：判据 (c) 用 Bonferroni 校正（{N_COMBOS} 组尝试）。"
          f"未过线组合的 P 值不可单独解读。")


if __name__ == "__main__":
    main()
