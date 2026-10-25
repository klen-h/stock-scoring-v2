#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】H2' 阴跌防御因子 —— 历史 in-sample 定参（★ 只定参，不判定）
================================================================================

纪律（方案 §7 冻结判据 v1.0 / §9 用户拍板②）：
  · 历史 38 段（本实现复现为更细粒度段，见 backfill_eq_anchor 自检说明）只做
    **in-sample 定参**；影子期**前瞻段才作判**（defensive_edge_check.py）。
  · 本脚本输出的任何收益数字**不构成 GO/KILL 证据**，仅用于选择加成形式与系数。

方法：
  · 历史（2010 起）没有五维评分 ⇒ 用「60 日动量的横截面分位」做代理排序
    （与现行评分的技术动量导向同向；已知局限：无质量过滤/无资金面，登记于报告）。
  · 阴跌段（eq_anchor 判态 bear_slow，中位口径 60 日滚动 ≤−5% 非急跌）内逐日：
      base 榜      = 代理分降序 top50
      defensive 榜 = 防御组成员分数 ×(1+δ) 后重排 top50
      t 日出榜 → t+1 日等权收益（无前视；t+1 停牌成员剔除）。
  · 评估指标：段内日均 (defensive_top50 − base_top50) 收益差（pt/日）——判据
    「对全池等权超额差」中基准池在两榜相减时消去，直接报告榜间差与各自对池超额。
  · 参数网格：防御组 {bank / lowvol_q30 / union} × δ {0.05,0.10,0.15,0.20,0.30}；
    参考统计：段均值差、正差段占比、段聚类 bootstrap 95%CI（B=5000）。

零 egress：本地 zzshare_daily.db + data/eq_daily_cache.csv + data/bank_codes.json。
用法：python scripts/insample_defensive_params.py [--start 2010-01-01] [--top 50]
================================================================================
"""
import argparse
import json
import os
import sys
from array import array
from collections import deque

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as _e:      # 老 Python/非 tty：不影响主流程，但要留痕（卫生门 #11）
    print(f"[warn] stdout reconfigure failed: {_e}", file=sys.stderr)

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

ROOT = os.path.dirname(os.path.abspath(__file__))
ZZSHARE_DB = os.path.join(ROOT, "..", "data", "zzshare_daily.db")
EQ_CACHE = os.path.join(ROOT, "..", "data", "eq_daily_cache.csv")
BANK_JSON = os.path.join(ROOT, "..", "data", "bank_codes.json")
REPORT = os.path.join(ROOT, "..", "reports",
                      f"insample_defensive_params_{os.environ.get('RUN_DATE', '20261025')}.md")

TOP = 50
# 第一轮实测：δ≤0.30 的乘法微调段均值差仅 ±0.01pt/日（top50 构成几乎不变）
# ⇒ 判据设想的"防御因子加成"须为实质性重排。第二轮扩 δ + 席位注入形式。
DELTAS = (0.10, 0.20, 0.30, 0.50, 0.80, 1.20)
QUOTAS = (5, 10, 20)          # 席位注入：防御组占 top50 中 k 席


def bear_slow_segments(start: str = "2010-01-01"):
    """阴跌段列表 [(s_date, e_date), ...]（eq 判态 bear_slow 的连续 run）。"""
    import pandas as pd
    from app.backtest.eq_anchor import judge_states, STATE_BEAR_SLOW
    daily = pd.read_csv(EQ_CACHE, index_col=0).sort_index()
    daily = daily[daily.index >= start]
    judged = judge_states([float(x) for x in daily["ret_median"]])
    dates = list(daily.index)
    segs, cur, prev_slow = [], None, False
    for d, j in zip(dates, judged):
        slow = bool(j) and j["state"] == STATE_BEAR_SLOW
        if slow:
            if prev_slow and cur:          # 判态序列相邻的 bear_slow 日归同段
                cur[1] = d
            else:
                if cur:
                    segs.append(tuple(cur))
                cur = [d, d]
        else:
            if cur:
                segs.append(tuple(cur))
                cur = None
        prev_slow = slow
    if cur:
        segs.append(tuple(cur))
    return segs


def collect_bear_days(segs):
    """流式逐股：阴跌段内每日的 (code_idx, ret, mom60, std60, bank)。

    内存：每交易日 5 个 array（~84KB）× 段内天数；逐股滚动窗口 deque(60)。
    每日结果按 code_idx 升序排齐（t→t+1 用 searchsorted 对齐成员收益）。
    """
    import sqlite3
    import numpy as np
    with open(BANK_JSON, encoding="utf-8") as f:
        banks = {"%06d" % int(c) for c in json.load(f)}
    code_idx = {}

    def _idx(code):
        i = code_idx.get(code)
        if i is None:
            i = code_idx[code] = len(code_idx)
        return i

    needed = set()
    for s, e in segs:
        needed.add(s); needed.add(e)
    day_rows = {d: {"idx": array("i"), "ret": array("d"), "mom": array("d"),
                    "std": array("d"), "bank": array("b")} for d in needed}

    conn = sqlite3.connect(ZZSHARE_DB)
    cur_code = None
    adjs: deque = deque(maxlen=60)
    rets: deque = deque(maxlen=60)
    sq = ssum = 0.0

    try:
        cur = conn.execute(
            "SELECT code, date, close * factor FROM daily "
            "WHERE close IS NOT NULL AND factor IS NOT NULL AND factor > 0 "
            "AND close > 0 ORDER BY code, date")
        while True:
            rows = cur.fetchmany(100_000)
            if not rows:
                break
            for c, d, adj in rows:
                if c != cur_code:
                    cur_code = c
                    adjs.clear(); rets.clear(); sq = ssum = 0.0
                ret = (adj / adjs[-1] - 1.0) if adjs and adjs[-1] > 0 else None
                # 先更新窗口（含当日），再取满窗指标——动量/std 口径均含当日
                if ret is not None:
                    rets.append(ret)
                    ssum += ret
                    sq += ret * ret
                    if len(rets) > 60:          # deque(maxlen=60) 丢旧，同步减
                        old = rets[0]
                        ssum -= old
                        sq -= old * old
                adjs.append(adj)
                if len(adjs) == 60 and len(rets) == 60:
                    row = day_rows.get(d)
                    if row is not None:
                        m = 60
                        var = max(sq / m - (ssum / m) ** 2, 0.0)
                        row["idx"].append(_idx(c))
                        row["ret"].append(ret)
                        row["mom"].append(adjs[-1] / adjs[0] - 1.0)
                        row["std"].append(var ** 0.5)
                        row["bank"].append(1 if c[:6] in banks else 0)
    finally:
        conn.close()
    out = {}
    for d, r in day_rows.items():
        if len(r["idx"]) >= 100:
            order = sorted(range(len(r["idx"])), key=lambda i: r["idx"][i])
            out[d] = {k: np.array([r[k][i] for i in order])
                      for k in ("idx", "ret", "mom", "std", "bank")}
    return out, code_idx


def evaluate(day_rows, segs, group, delta=None, quota=None, top=TOP):
    """一个参数组合在全部阴跌段上的表现。返回段级指标列表。

    group: 'bank' | 'lowvol' | 'union'
    delta: 乘法加成（防御组分数 ×(1+δ)）
    quota: 席位注入（防御组内代理分 top-k 直接占榜 + 其余席位全池递补）——
           二选一（quota 优先）。
    每段：遍历段内相邻日对 (t, t+1)：t 日横截面分位 = 代理分 → 两榜 top50 →
    t+1 成员等权收益（searchsorted 对齐，停牌剔除）→ 日差 = def − base。
    """
    import numpy as np
    seg_stats = []
    for s, e in segs:
        days = sorted(d for d in day_rows if s <= d <= e)
        diffs, def_ex, base_ex = [], [], []
        for t, t1 in zip(days, days[1:]):
            a, b = day_rows[t], day_rows[t1]
            mom = a["mom"]
            if len(mom) < top + 50:
                continue
            # 防御组成员
            if group == "bank":
                memb = a["bank"] == 1
            elif group == "lowvol":
                thr = np.quantile(a["std"], 0.30)
                memb = a["std"] <= thr
            else:
                thr = np.quantile(a["std"], 0.30)
                memb = (a["bank"] == 1) | (a["std"] <= thr)
            # 横截面分位（0-100）作代理分
            score = (np.argsort(np.argsort(mom)).astype(float) + 0.5) / len(mom) * 100.0
            base_rank = np.argsort(-score)
            base_top = base_rank[:top]
            if quota:
                order = base_rank
                def_members = [i for i in order if memb[i]][:quota]
                chosen = set(int(i) for i in def_members)
                def_top = list(def_members) + [
                    i for i in order if int(i) not in chosen][:top - quota]
            else:
                def_score = np.where(memb, score * (1.0 + (delta or 0.0)), score)
                def_top = np.argsort(-def_score)[:top]
            # t+1 收益对齐（两日均按 idx 升序）
            pos = np.searchsorted(b["idx"], a["idx"])
            ok = (pos < len(b["idx"])) & (b["idx"][np.minimum(pos, len(b["idx"]) - 1)]
                                          == a["idx"])
            ret_t1 = np.where(ok, b["ret"][np.minimum(pos, len(b["ret"]) - 1)], np.nan)
            pool = float(np.nanmean(b["ret"]))
            def_r = ret_t1[np.array(def_top, dtype=int)]
            base_r = ret_t1[base_top]
            def_ret = float(np.nanmean(def_r)) if np.isfinite(def_r).any() else None
            base_ret = float(np.nanmean(base_r)) if np.isfinite(base_r).any() else None
            if def_ret is None or base_ret is None:
                continue
            diffs.append((def_ret - base_ret) * 100.0)      # pt/日
            def_ex.append((def_ret - pool) * 100.0)
            base_ex.append((base_ret - pool) * 100.0)
        if diffs:
            seg_stats.append({"days": len(diffs), "diff": sum(diffs) / len(diffs),
                              "def_ex": sum(def_ex) / len(def_ex),
                              "base_ex": sum(base_ex) / len(base_ex)})
    return seg_stats


def bootstrap_ci(values, b=5000, seed=42):
    """段均值聚类 bootstrap 95%CI（重抽段）。"""
    import random
    import statistics
    if len(values) < 2:
        return (None, None)
    rng = random.Random(seed)
    n = len(values)
    means = []
    for _ in range(b):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    return means[int(0.025 * b)], means[int(0.975 * b)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2010-01-01")
    args = ap.parse_args()

    segs = bear_slow_segments(args.start)
    print(f"阴跌段 {len(segs)} 段（{args.start} 起）")
    print("流式收集段内横截面（~2 分钟）...")
    day_rows, _ = collect_bear_days(segs)
    print(f"  收集 {len(day_rows)} 日（横截面中位规模 "
          f"{sorted(len(v['idx']) for v in day_rows.values())[len(day_rows)//2]}）")

    import numpy as np
    lines = [f"# H2' in-sample 定参报告（{args.start} 起，只定参不判定）",
             "", f"- 阴跌段数：{len(segs)}；榜：top{TOP}；代理分=60日动量横截面分位",
             "- 判据提醒：本表数字不构成 GO/KILL 证据（前瞻段才判）", "",
             "| 组 | δ | 段均值差 pt/日 | 95%CI | 正差段占比 | def对池 | base对池 |",
             "|---|---|---|---|---|---|---|"]
    results = []
    combos = ([("mult", g, d, None) for g in ("bank", "lowvol", "union")
               for d in DELTAS]
              + [("quota", g, None, q) for g in ("bank", "lowvol", "union")
                 for q in QUOTAS])
    for kind, group, delta, quota in combos:
            seg_stats = evaluate(day_rows, segs, group, delta=delta, quota=quota)
            if not seg_stats:
                continue
            label = f"δ={delta:.2f}" if kind == "mult" else f"k={quota}"
            diffs = [s["diff"] for s in seg_stats]
            mean = float(np.mean(diffs))
            pos_ratio = sum(1 for d in diffs if d > 0) / len(diffs)
            lo, hi = bootstrap_ci(diffs)
            def_ex = float(np.mean([s["def_ex"] for s in seg_stats]))
            base_ex = float(np.mean([s["base_ex"] for s in seg_stats]))
            results.append({"kind": kind, "group": group, "delta": delta,
                            "quota": quota, "mean": mean, "ci": (lo, hi),
                            "pos": pos_ratio, "n_seg": len(diffs)})
            lines.append(f"| {group} {kind} | {label} | {mean:+.3f} | "
                         f"[{lo:+.3f},{hi:+.3f}] | {pos_ratio:.0%} | "
                         f"{def_ex:+.3f} | {base_ex:+.3f} |")
            print(f"  {group:6s} {kind:5s} {label:7s} 段均值差 {mean:+.3f} pt/日  "
                  f"正差段 {pos_ratio:.0%}  (n={len(diffs)})")

    # baseline：base 榜对池超额（各段平均，δ 无关）
    base_only = evaluate(day_rows, segs, "bank", delta=0.0)
    lines += ["", f"## base 代理榜（纯动量 top{TOP}）对全池等权超额",
              f"- 段均值 {float(np.mean([s['base_ex'] for s in base_only])):+.3f} pt/日"
              f"（阴跌段动量策略自身基准，判 GO 时两榜相减该基准消去）"]
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n报告已写 {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
