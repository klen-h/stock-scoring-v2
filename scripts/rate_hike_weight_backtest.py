#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】利率高压档权重回测背书（回答"阴跌/加息前夜评分该用什么权重"）
================================================================================

背景（2026-09-15，用户观察）：近两周 neutral_bearish 阴跌市，评分排行榜前列
  多数在跌。诊断②已证：总分 IC≈+0.016（排序失效），而单因子 IC——
  基本面 +0.167 / 资金面 +0.075 / 技术面 -0.069 / 成长 -0.196 / 质量 +0.017。
  问题在权重：nb 市沿用 neutral 权重（技术面 27% + 成长 17% = 44% 压在两个
  反向因子上），把"技术面强势 + 高成长"的补跌票排到前面。

本脚本回答：**把权重切到防御化 / 利率高压档，总分 IC 和 top/bottom 分离
  是否真的改善？** 纯离线、只读 ranking_history（dimensions_json），零行情回源。

方法：对每条已验证快照，用其五维度分**重算**不同权重下的合成总分，再算
  Spearman IC 与 top30%/bottom30% 分离。对比 4 档权重 + 组合因子基准。

用法：
  python scripts/rate_hike_weight_backtest.py            # horizon=5 日
  python scripts/rate_hike_weight_backtest.py --horizon 10
================================================================================
"""
import argparse
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

from app.database import db                                    # noqa: E402
from app.scoring.ranking_history import get_verified_records    # noqa: E402

DIMS = ("技术面", "资金面", "基本面", "成长", "质量")

# 权重候选（和 = 1.0）
WEIGHTS = {
    "现行 nb(=neutral)": {"技术面": 0.27, "资金面": 0.23, "基本面": 0.23,
                          "成长": 0.17, "质量": 0.11},
    "defensive 档":      {"技术面": 0.12, "资金面": 0.15, "基本面": 0.25,
                          "成长": 0.13, "质量": 0.35},
    "利率高压档(拟)":     {"技术面": 0.10, "资金面": 0.25, "基本面": 0.30,
                          "成长": 0.10, "质量": 0.25},
    "纯防御档":          {"技术面": 0.08, "资金面": 0.22, "基本面": 0.30,
                          "成长": 0.08, "质量": 0.32},
}


def _spearman(xs, ys):
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


def _rank_norm(vals):
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    r = [0.0] * n
    for pos, i in enumerate(order):
        r[i] = pos / (n - 1) if n > 1 else 0.5
    return r


def _evaluate(name, factor, rets):
    """算 IC + top30/bottom30 分离 + 胜率。"""
    k = len(factor)
    ic = _spearman(factor, rets)
    order = sorted(range(k), key=lambda i: factor[i])
    m = max(1, k * 3 // 10)
    lo_i, hi_i = order[:m], order[k - m:]
    lo = sum(rets[i] for i in lo_i) / m
    hi = sum(rets[i] for i in hi_i) / m
    lo_wr = sum(1 for i in lo_i if rets[i] > 0) / m * 100
    hi_wr = sum(1 for i in hi_i if rets[i] > 0) / m * 100
    spread = hi - lo
    return {"name": name, "ic": ic, "lo": lo, "hi": hi, "spread": spread,
            "lo_wr": lo_wr, "hi_wr": hi_wr}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5)
    args = ap.parse_args()
    H = args.horizon

    records = get_verified_records(min_age_days=2, horizon_days=H)
    # 过滤：五维齐全
    recs = [r for r in records
            if all(isinstance((r.get("dimensions") or {}).get(k), (int, float))
                   for k in DIMS)]
    n = len(recs)
    print("=" * 72)
    print(f"利率高压档权重回测（持有 {H} 交易日）  样本 {n} 条")
    print("=" * 72)
    if n < 30:
        print("样本不足 30，结论不可靠")
        return 1

    dates = sorted({r["date"] for r in recs})
    print(f"日期范围 {dates[0]} ~ {dates[-1]}（{len(dates)} 个快照日）")

    rets = [float(r["returnPct"]) for r in recs]

    # 单因子 IC（对照）
    print("\n【单因子 IC 对照】")
    rows = []
    for dim in DIMS:
        vals = [float(r["dimensions"][dim]) for r in recs]
        ic = _spearman(vals, rets)
        rows.append((dim, ic))
    for dim, ic in rows:
        print(f"  {dim:<6s} IC={ic:+.3f}")

    # 各档权重（原始分加权）
    print("\n【各档权重 · 原始分加权合成总分】")
    print(f"  {'权重档':<18s}{'IC':>8s}{'低30%均收':>12s}{'高30%均收':>12s}"
          f"{'分离':>8s}{'低胜率':>8s}{'高胜率':>8s}")
    print("  " + "-" * 66)
    results = []
    for name, w in WEIGHTS.items():
        factor = [sum(w[k] * float(r["dimensions"][k]) for k in DIMS) for r in recs]
        ev = _evaluate(name, factor, rets)
        results.append(ev)
        print(f"  {name:<18s}{ev['ic']:>+8.3f}{ev['lo']:>+11.2f}%"
              f"{ev['hi']:>+11.2f}%{ev['spread']:>+8.2f}"
              f"{ev['lo_wr']:>7.0f}%{ev['hi_wr']:>7.0f}%")

    # 组合因子基准（rank 平均，与 get_composite_stats 同口径）
    print("\n【组合因子基准 · rank(基本面)+rank(资金面)-rank(技术面)】")
    r_fund = _rank_norm([float(r["dimensions"]["基本面"]) for r in recs])
    r_cap = _rank_norm([float(r["dimensions"]["资金面"]) for r in recs])
    r_tech = _rank_norm([float(r["dimensions"]["技术面"]) for r in recs])
    comp = [(r_fund[i] + r_cap[i] - r_tech[i]) / 3.0 for i in range(n)]
    ev = _evaluate("组合因子", comp, rets)
    print(f"  IC={ev['ic']:+.3f}  低30%={ev['lo']:+.2f}%  高30%={ev['hi']:+.2f}%  "
          f"分离={ev['spread']:+.2f}  胜率 {ev['lo_wr']:.0f}%/{ev['hi_wr']:.0f}%")

    # 稳健性：秩归一化加权（消除量级差异）
    print("\n【稳健性 · 秩归一化加权（消除维度量级差异）】")
    rn = {k: _rank_norm([float(r["dimensions"][k]) for r in recs]) for k in DIMS}
    print(f"  {'权重档':<18s}{'IC':>8s}{'分离':>8s}")
    for name, w in WEIGHTS.items():
        factor = [sum(w[k] * rn[k][i] for k in DIMS) for i in range(n)]
        ic = _spearman(factor, rets)
        order = sorted(range(n), key=lambda i: factor[i])
        m = max(1, n * 3 // 10)
        lo = sum(rets[i] for i in order[:m]) / m
        hi = sum(rets[i] for i in order[n - m:]) / m
        print(f"  {name:<18s}{ic:>+8.3f}{hi - lo:>+8.2f}")

    print("\n【结论判读】")
    best = max(results, key=lambda x: (x["spread"] if x["spread"] else -999))
    print(f"  原始分口径分离最大档 = {best['name']}（分离 {best['spread']:+.2f}）")
    print("  注：本回测样本几乎全部为 neutral_bearish 单一市况（见诊断②），")
    print("      in-sample 择优会偏乐观；结论用于'要不要落地'的参考，")
    print("      落地后需用后续快照做前瞻验证。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
