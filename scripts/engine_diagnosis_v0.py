# -*- coding: utf-8 -*-
"""
评分引擎体检报告 v0（诊断脚本，纯读数据）。
回答三个问题：
  ① 评分为什么挤在 60-70？（总分 + 各维度分布/区分度）
  ② 哪个维度真的有预测力？（单因子 IC：各维度得分 vs 未来 5 日收益）
  ③ 主力标签为什么 97% none？（按日期的覆盖率 → 验证"日批覆盖"假设）
"""
import os
import sys
from collections import defaultdict

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from app.database import db
from app.scoring.ranking_history import get_verified_records, _parse_dims

HORIZON = 5


def _spearman(xs, ys):
    """手写 Spearman 秩相关（不依赖 scipy）。"""
    n = len(xs)
    if n < 10:
        return None

    def _ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx > 0 and vy > 0 else None


def _dist(values, label):
    """分布摘要：n / min / p10 / p50 / p90 / max / std / 区分度。"""
    if not values:
        print(f"    {label:<14s} n=0")
        return
    s = sorted(values)
    n = len(s)

    def p(q):
        return s[min(n - 1, int(n * q))]

    mean = sum(s) / n
    std = (sum((x - mean) ** 2 for x in s) / n) ** 0.5
    span = s[-1] - s[0]
    spread = std / span * 100 if span > 0 else 0
    print(f"    {label:<14s} n={n:<4d} min={s[0]:.1f} p10={p(0.1):.1f} "
          f"p50={p(0.5):.1f} p90={p(0.9):.1f} max={s[-1]:.1f} "
          f"std={std:.2f} 区分度={spread:.0f}%")


def main():
    # ═══ 诊断③：主力标签按日期覆盖率（先跑，验证日批覆盖假设）═══
    print("=" * 62)
    print("诊断③ 主力标签覆盖率（按快照日）")
    print("=" * 62)
    rows = db.fetch("""
        SELECT rank_date, COUNT(*) AS n,
               COUNT(mainforce_signal) AS n_mf,
               COUNT(dimensions_json) AS n_dims
        FROM ranking_history
        GROUP BY rank_date ORDER BY rank_date ASC
    """)
    for r in rows:
        pct = r["n_mf"] / r["n"] * 100
        flag = " <-- 无标签日" if r["n_mf"] == 0 else ""
        print(f"  {r['rank_date']}  n={r['n']:3d}  主力标签={r['n_mf']:3d} ({pct:5.1f}%)"
              f"  维度分={r['n_dims']:3d}{flag}")

    # ═══ 诊断①：评分分布 ═══
    print()
    print("=" * 62)
    print("诊断① 评分分布（最近 90 天快照）")
    print("=" * 62)
    all_rows = db.fetch("""
        SELECT total_score, dimensions_json FROM ranking_history
        WHERE dimensions_json IS NOT NULL AND dimensions_json != ''
    """)
    scores = [r["total_score"] for r in all_rows if r["total_score"] is not None]
    _dist(scores, "total_score")

    dim_keys = {}
    for r in all_rows:
        for k, v in _parse_dims(r).items():
            if isinstance(v, (int, float)):
                dim_keys.setdefault(k, []).append(float(v))
    print("  --- 各维度分布（区分度 = std/(max-min)，越低越没区分度）---")
    for k in sorted(dim_keys, key=lambda x: -len(dim_keys[x])):
        _dist(dim_keys[k], k)

    # ═══ 诊断②：单因子 IC（各维度 vs 未来 5 日收益）═══
    print()
    print("=" * 62)
    print(f"诊断② 单因子 IC（固定持有 {HORIZON} 个交易日，复用 get_verified_records 口径）")
    print("=" * 62)
    records = get_verified_records(min_age_days=2, horizon_days=HORIZON)
    print(f"  样本: {len(records)} 条（快照+维度+固定{HORIZON}日收益）")
    if len(records) < 30:
        print("  样本不足 30，IC 不可靠")
        return

    # 因子 = total_score + 各维度分
    factors = {"total_score": [r["score"] for r in records]}
    for rec in records:
        for k, v in rec["dimensions"].items():
            if isinstance(v, (int, float)):
                factors.setdefault(k, [])
                factors[k].append(float(v))
    # 注意：各维度可能缺值（不同日期维度不同），按因子各自的非空配对算
    rets_all = [r["returnPct"] for r in records]
    print(f"  {'因子':<14s} {'n':>5s} {'IC(Spearman)':>12s}  解读")
    print("  " + "-" * 58)
    for name in sorted(factors, key=lambda x: -len(factors[x])):
        pairs = []
        for rec in records:
            v = rec["dimensions"].get(name, rec["score"] if name == "total_score" else None)
            if isinstance(v, (int, float)) and rec["returnPct"] is not None:
                pairs.append((float(v), rec["returnPct"]))
        if len(pairs) < 30:
            print(f"  {name:<14s} {len(pairs):>5d} {'--':>12s}  样本不足")
            continue
        ic = _spearman([p[0] for p in pairs], [p[1] for p in pairs])
        if ic is None:
            print(f"  {name:<14s} {len(pairs):>5d} {'--':>12s}")
            continue
        # 分位对照：因子 top30% vs bottom30% 的均收益
        pairs.sort(key=lambda x: x[0])
        m = max(1, len(pairs) * 3 // 10)
        lo = sum(p[1] for p in pairs[:m]) / m
        hi = sum(p[1] for p in pairs[-m:]) / m
        verdict = ("有正向预测力" if ic >= 0.05 else
                   "反向（高分更差）" if ic <= -0.05 else "基本无效(|IC|<0.05)")
        print(f"  {name:<14s} {len(pairs):>5d} {ic:>12.3f}  {verdict}"
              f"  [低30%均收={lo:+.2f}% 高30%均收={hi:+.2f}%]")

    # 总分分位组合收益（模拟"买 top30%"）
    print()
    pairs = sorted([(r["score"], r["returnPct"]) for r in records
                    if r["returnPct"] is not None], key=lambda x: x[0])
    m = max(1, len(pairs) * 3 // 10)
    segs = [("最低30%", pairs[:m]), ("中间40%", pairs[m:len(pairs) - m]),
            ("最高30%", pairs[len(pairs) - m:])]
    print(f"  总分分位组合（等权持有{HORIZON}日）:")
    for label, seg in segs:
        wr = sum(1 for _, x in seg if x > 0) / len(seg) * 100
        avg = sum(x for _, x in seg) / len(seg)
        print(f"    {label:<8s} n={len(seg):<4d} 胜率={wr:5.1f}% 均收益={avg:+.2f}%")

    # ═══ 诊断②b：组合因子 IC（基本面+资金面，秩平均）——v1 选股公式预验证 ═══
    print()
    print("=" * 62)
    print("诊断②b 组合因子 IC（基本面 + 资金面，秩平均；v1 选股公式预验证）")
    print("=" * 62)
    combos = [
        ("基本面+资金面", ("基本面", "资金面")),
        ("基本面+资金面-技术面", ("基本面", "资金面", "技术面_neg")),
        ("基本面+资金面+成长", ("基本面", "资金面", "成长")),
    ]
    for cname, parts in combos:
        pairs_c = []
        for rec in records:
            if rec["returnPct"] is None:
                continue
            vals = []
            for p in parts:
                neg = p.endswith("_neg")
                key = p[:-4] if neg else p
                v = rec["dimensions"].get(key)
                if not isinstance(v, (int, float)):
                    vals = None
                    break
                vals.append(-float(v) if neg else float(v))
            if vals:
                pairs_c.append((vals, rec["returnPct"]))
        if len(pairs_c) < 30:
            print(f"  {cname:<22s} n={len(pairs_c):<4d} 样本不足")
            continue
        # 因子各分量秩归一化后取平均
        k = len(pairs_c)
        combined = []
        for i, (vals, ret) in enumerate(pairs_c):
            combined.append((vals, ret))
        # 对每个分量做秩
        n_comp = len(combos[0][1]) if False else len(pairs_c[0][0])
        ranks_avg = []
        for comp in range(n_comp):
            vals = sorted(range(k), key=lambda i: pairs_c[i][0][comp])
            r = [0.0] * k
            for pos, i in enumerate(vals):
                r[i] = pos / (k - 1) if k > 1 else 0.5
            ranks_avg.append(r)
        factor = [sum(ranks_avg[c][i] for c in range(n_comp)) / n_comp
                  for i in range(k)]
        ic = _spearman(factor, [p[1] for p in pairs_c])
        order = sorted(range(k), key=lambda i: factor[i])
        m2 = max(1, k * 3 // 10)
        lo_i = order[:m2]
        hi_i = order[k - m2:]
        lo = sum(pairs_c[i][1] for i in lo_i) / m2
        hi = sum(pairs_c[i][1] for i in hi_i) / m2
        lo_wr = sum(1 for i in lo_i if pairs_c[i][1] > 0) / m2 * 100
        hi_wr = sum(1 for i in hi_i if pairs_c[i][1] > 0) / m2 * 100
        print(f"  {cname:<22s} n={k:<4d} IC={ic:+.3f}  "
              f"[低30%: 胜率{lo_wr:.0f}% {lo:+.2f}% | 高30%: 胜率{hi_wr:.0f}% {hi:+.2f}%]")


if __name__ == "__main__":
    main()
