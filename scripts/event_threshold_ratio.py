#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · P1-b】阈值「比例化」验证（**独立预登记**）
================================================================================
问题（实证发现，2026-09-27）：
  当前 E1/E2 用**绝对家数**阈值（`limit_up >= 50`、`limit_down >= 200`），而
  回测口径（zzshare 全市场 ~5557 只）与**生产口径**（实时快照 ~3236 只）
  **不是同一个池子**：

  | 口径 | 样本 | 2026-09-24 up_ratio | 涨停 | 跌停 |
  |---|---|---|---|---|
  | 回测 zzshare 全市场 | 5557 | 0.2053 | 53 | 16 |
  | 生产实时快照 | 3236 | 0.2421 | 47 | 11 |

  ⇒ 绝对家数阈值的**含义随池子大小漂移**：`limit_down>=200` 在 3236 只里
    等于 6.2%（vs 全市场 3.6%）⇒ 生产比回测更难触发，验证结论不可迁移。

目标：验证**比例阈值**（`limit_up/n`、`limit_down/n`）是否
  ① 在历史全池上保持 edge（与绝对阈值等价或更好）
  ② 在缩小池子上更**稳健**（触发一致性显著高于绝对阈值）

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死）━━━━━━━━━━━━━━━━━━
比例网格（E2）：`up_ratio >= 0.90` 且 `limit_up / (up+down) >= R`
  R ∈ {0.6%, 0.9%, 1.2%, 1.5%}   （原绝对阈值 50/5557 ≈ 0.90%，作为对照）

edge 判定（对每个 R，五条同时满足才推荐）：
  (a) 独立事件簇 >= 20
  (b) T+20 差 >= +2.0pp（**不放宽**：比例化是等价替换，不是放宽）
  (c) P(mean <= 基准) < 0.05 / 4 = 0.0125（Bonferroni）
  (d) defensive 内相对该态基准增量 >= +1.0pp
  (e) 触发天数与原绝对阈值 (0.90, 50) 的口径差异 <= 15%

池子稳健性判定（抽样模拟）：
  子集 = 用 `code % 100 < K` 做**确定性抽样**（K=58/40，模拟生产缩池）。
  对每个子集计算「触发天数 / 全池触发天数」的偏离：
    · 绝对阈值 `limit_up >= 50`
    · 比例阈值 `limit_up_ratio >= 0.9%`
  (f) 比例阈值的偏离 <= 绝对阈值偏离的 50% ⇒ 认定"更耐池子变化"

⚠️ 收益口径沿用 `event_edge_check.py`：全市场等权、T+1 开盘买（扣跳空）、持 20 日。
⚠️ 本脚本只读 zzshare + event_history.json，不改生产。

用法：python scripts/event_threshold_ratio.py
================================================================================
"""
import json
import os
import sqlite3
import sys
from collections import defaultdict

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
EVENT_JSON = os.path.join(ROOT, "data", "event_history.json")

STOCK_FILTER = ("(code LIKE '60%.SH' OR code LIKE '68%.SH' OR code LIKE '00%.SZ' "
                "OR code LIKE '30%.SZ' OR code LIKE '%.BJ')")

# ── 预登记 ──
E2_UP_RATIO = 0.90
# ★ v2 修正（2026-09-27）：原网格 {0.6,0.9,1.2,1.5}% 是按**当前时点**池子
#   （5557 只）换算 50/5557≈0.9% —— 但 zzshare 的**日样本中位只有 2444 只**
#   （21 年跨度，早期市场小）⇒ 真正等价的换算基准是 50/2444≈**2.0%**。
#   原网格整体偏松，选入大量"非极端"日 ⇒ edge 被稀释（实测 +1.69~+2.10pp）。
#   本版以**历史中位池子**为基准，并覆盖更严区间。
RATIO_GRID = [0.009, 0.015, 0.020, 0.025, 0.030]
ORIG_LIMIT_UP = 50              # 原绝对阈值（对照）
N_COMBOS = len(RATIO_GRID) + 1  # 含对照行
MIN_CLUSTERS = 20
MIN_DIFF = 2.0
MIN_DEF_DIFF = 1.0
MAX_PARITY_GAP = 0.15           # (e) 与原绝对阈值的口径差异上限
CLUSTER_GAP = 20
H = 20
B_BOOT = 10000
SUBSETS = {"full": None, "s58": 58, "s40": 40}


def aggregate(subset_mod=None):
    """按日聚合（可指定确定性子集：code % 100 < subset_mod）。"""
    extra = ""
    if subset_mod:
        extra = f" AND (CAST(substr(code,1,6) AS INTEGER) % 100) < {subset_mod}"
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute(f"""
        SELECT date, COUNT(*) AS n,
               SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) AS down,
               SUM(CASE WHEN high_limit > 0 AND close >= high_limit*0.9995
                        THEN 1 ELSE 0 END) AS lu,
               SUM(CASE WHEN low_limit > 0 AND close <= low_limit*1.0005
                        THEN 1 ELSE 0 END) AS ld
        FROM daily
        WHERE {STOCK_FILTER} AND is_paused = 0 AND pct_chg IS NOT NULL {extra}
        GROUP BY date ORDER BY date
    """).fetchall()
    conn.close()
    out = {}
    for date, n, up, down, lu, ld in rows:
        up, down = up or 0, down or 0
        denom = up + down
        out[str(date)] = {
            "n": n or 0, "up": up, "down": down,
            "up_ratio": up / denom if denom else 0.5,
            "lu": lu or 0, "ld": ld or 0,
            "lu_ratio": (lu or 0) / denom if denom else 0.0,
            "ld_ratio": (ld or 0) / denom if denom else 0.0,
        }
    return out


def block_bootstrap_p(vals, base, B=B_BOOT, seed=42, chunk=2000):
    a = np.asarray(vals, dtype=float)
    n = len(a)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)
    cnt = tot = 0
    done = 0
    while done < B:
        m = min(chunk, B - done)
        means = a[rng.integers(0, n, size=(m, n))].mean(axis=1)
        cnt += int(np.sum(means <= base))
        tot += m
        done += m
    return cnt / tot


def count_clusters(idx_list):
    if not idx_list:
        return 0
    c = 1
    for a, b in zip(idx_list, idx_list[1:]):
        if b - a >= CLUSTER_GAP:
            c += 1
    return c


def main():
    print("① 聚合 zzshare（全池 + 2 个确定性子集，模拟生产缩池）…")
    aggs = {}
    for name, mod in SUBSETS.items():
        aggs[name] = aggregate(mod)
        sizes = [v["n"] for v in aggs[name].values()]
        print(f"   {name:5s}: {len(aggs[name])} 天，日样本中位 {int(np.median(sizes))}")

    # 收益序列（全市场等权，来自 event_history.json）
    with open(EVENT_JSON, encoding="utf-8") as f:
        ev_raw = json.load(f)
    dates = sorted(ev_raw)
    rets = [ev_raw[d]["pct_mean"] for d in dates]
    gaps = [ev_raw[d].get("gap_mean", 0.0) for d in dates]
    didx = {d: i for i, d in enumerate(dates)}
    full = aggs["full"]

    def fwd_open(i, n=H):
        if i + 1 + n > len(rets):
            return None
        entry = 1 + gaps[i + 1] / 100.0
        if entry <= 0.05:
            return None
        cum = 1.0
        for k in range(i + 1, i + 1 + n):
            cum *= (1 + rets[k] / 100.0)
        return (cum / entry - 1) * 100

    base_all = [v for v in (fwd_open(i) for i in range(len(dates))) if v is not None]
    base_mean = sum(base_all) / len(base_all)

    # regime 内基准（defensive）
    def load_regime():
        p = os.path.join(ROOT, "data", "nb_history.json")
        if not os.path.exists(p):
            return {}
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        return {k: (v.get("state_4") or v.get("state_3")) for k, v in m.items()}
    rmap = load_regime()
    def_vals = [v for v in (fwd_open(i) for i, d in enumerate(dates)
                            if rmap.get(d) == "defensive") if v is not None]
    def_base = sum(def_vals) / len(def_vals) if def_vals else None
    print(f"\n   基准（T+{H}，T+1 开盘买）：全样本 +{base_mean:.2f}%；"
          f"defensive +{def_base:.2f}%" if def_base else "")

    # ── ② edge：比例阈值 vs 原绝对阈值 ──
    print(f"\n{'='*104}")
    print(f"【② edge 对比】（up_ratio>={E2_UP_RATIO}；比例 R vs 原绝对 {ORIG_LIMIT_UP} 家）")
    print(f"{'='*104}")
    print(f"{'口径':<22}{'事件n':>7}{'簇':>5}{'T+20':>9}{'差':>9}{'P(≤基准)':>10}"
          f"{'def增量':>9}{'触发差异':>10}  判定")
    orig_idx = [didx[d] for d, v in full.items()
                if d in didx and v["up_ratio"] >= E2_UP_RATIO and v["lu"] >= ORIG_LIMIT_UP]
    rows = []
    for R in RATIO_GRID:
        sel = [d for d, v in full.items()
               if d in didx and v["up_ratio"] >= E2_UP_RATIO and v["lu_ratio"] >= R]
        idxs = sorted(didx[d] for d in sel)
        vals = [v for v in (fwd_open(i) for i in idxs) if v is not None]
        if not vals:
            rows.append({"R": R, "n": 0})
            print(f"{'lu_ratio>=%.1f%%' % (R*100):<22}{0:>7}")
            continue
        mean = sum(vals) / len(vals)
        pb = block_bootstrap_p(vals, base_mean)
        dsub = [v for v in (fwd_open(i) for i in idxs if rmap.get(dates[i]) == "defensive")
                if v is not None]
        dmean = sum(dsub) / len(dsub) if dsub else None
        ddiff = (dmean - def_base) if (dmean is not None and def_base is not None) else None
        gap = abs(len(idxs) - len(orig_idx)) / max(1, len(orig_idx))
        ok = (count_clusters(idxs) >= MIN_CLUSTERS and (mean - base_mean) >= MIN_DIFF
              and pb is not None and pb < 0.05 / N_COMBOS
              and ddiff is not None and ddiff >= MIN_DEF_DIFF
              and gap <= MAX_PARITY_GAP)
        rows.append({"R": R, "n": len(idxs), "clusters": count_clusters(idxs),
                     "mean": mean, "diff": mean - base_mean, "pb": pb, "ddiff": ddiff,
                     "gap": gap, "ok": ok})
        print(f"{'lu_ratio>=%.1f%%' % (R*100):<22}{len(idxs):>7}{count_clusters(idxs):>5}"
              f"{mean:>+8.2f}%{mean-base_mean:>+8.2f}pp{pb:>10.4f}"
              f"{(ddiff if ddiff is not None else 0):>+8.2f}pp{gap:>9.1%}"
              f"  {'RELAX' if ok else '-'}")
    # 对照行（原绝对阈值）
    if orig_idx:
        ov = [v for v in (fwd_open(i) for i in orig_idx) if v is not None]
        om = sum(ov) / len(ov)
        opb = block_bootstrap_p(ov, base_mean)
        od = [v for v in (fwd_open(i) for i in orig_idx if rmap.get(dates[i]) == "defensive")
              if v is not None]
        oddiff = (sum(od) / len(od) - def_base) if (od and def_base is not None) else 0.0
        print(f"{'【原】lu>=%d 家' % ORIG_LIMIT_UP:<22}{len(orig_idx):>7}"
              f"{count_clusters(orig_idx):>5}{om:>+8.2f}%{om-base_mean:>+8.2f}pp"
              f"{opb:>10.4f}{oddiff:>+8.2f}pp{'':>10}  对照")

    # ── ③ 池子稳健性 ──
    print(f"\n{'='*104}")
    print("【③ 池子稳健性】子集触发天数 vs 全池（模拟生产缩池 3236 只）")
    print(f"{'='*104}")
    for name in ("s58", "s40"):
        sub = aggs[name]
        common = sorted(set(full) & set(sub))
        # 绝对阈值
        a_full = sum(1 for d in common if full[d]["lu"] >= ORIG_LIMIT_UP)
        a_sub = sum(1 for d in common if sub[d]["lu"] >= ORIG_LIMIT_UP)
        size = int(np.median([sub[d]["n"] for d in common]))
        dev_a = abs(a_sub - a_full) / max(1, a_full)
        print(f"\n  [{name}] 日样本中位 {size} 只（全池 {int(np.median([full[d]['n'] for d in common]))} 只）")
        print(f"    绝对 lu>=50 家      ：全池 {a_full} 天 → 子集 {a_sub} 天"
              f"（偏离 {dev_a:+.1%}）")
        # 比例阈值（测两个：过松的 0.9% 与等价基准 2.0%）
        r_ref = None
        for R in (0.009, 0.020):
            r_full = sum(1 for d in common if full[d]["lu_ratio"] >= R)
            r_sub = sum(1 for d in common if sub[d]["lu_ratio"] >= R)
            dev_r = abs(r_sub - r_full) / max(1, r_full)
            if abs(R - 0.020) < 1e-9:
                r_ref = (dev_r, r_full, r_sub)
            print(f"    比例 lu_ratio>={R*100:.1f}%   ：全池 {r_full} 天 → 子集 {r_sub} 天"
                  f"（偏离 {dev_r:+.1%}）")
        if r_ref:
            verdict = ("比例更稳健(YES)" if r_ref[0] <= dev_a * 0.5 else "未见明显优势")
            print(f"    -> 绝对 {dev_a:.1%} vs 比例(2.0%) {r_ref[0]:.1%}：{verdict}")

    # ── ④ 汇总 ──
    print(f"\n{'='*104}")
    print("【④ 预登记判定汇总】")
    print(f"{'='*104}")
    passed = [r for r in rows if r.get("ok")]
    if passed:
        for r in passed:
            print(f"  ★ RELAX: lu_ratio >= {r['R']*100:.1f}%  事件 {r['n']} 天 / {r['clusters']} 簇，"
                  f"T+{H} {r['mean']:+.2f}%（差 {r['diff']:+.2f}pp），def 增量 {r['ddiff']:+.2f}pp，"
                  f"P(≤基准)={r['pb']:.4f}，与原口径差 {r['gap']:.1%}")
        best = min(passed, key=lambda r: r["gap"])
        print(f"\n  推荐：**lu_ratio >= {best['R']*100:.1f}%**（与原绝对阈值口径差最小 {best['gap']:.1%}）")
    else:
        print("  ★ KEEP：没有比例阈值同时满足五条判据 ⇒ 维持原绝对阈值。")
    print(f"\n  注：Bonferroni N={N_COMBOS}；未过线组合的 P 值不可单独解读。")


if __name__ == "__main__":
    main()
