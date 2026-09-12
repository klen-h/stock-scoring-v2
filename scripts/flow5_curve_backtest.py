#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】flow5 评分曲线矛盾离线回测（PLAN #8）
================================================================================
背景：
  当前 _score_flow5 锚点单调递增（-10→0 ... +5→100），意味着"主力流入越多越"。
  但项目自身 20 截面验证与 09-11 高换手高标补跌案例表明：极端流入往往是
  散户追涨/主力出货陷阱，因子与未来收益呈倒 U 关系。

本脚本对比三套评分口径，看哪套与 forward return 的 IC 更高、五分位更单调：
  A. 原始曲线（现行）
  B. 倒 U 曲线（+2 封顶后下降，极端流入低分）
  C. 原始曲线 + overlay 惩罚（flow5>+5 且 price_pos>0.75 时 score×0.85）

方法：
  - 直接复用 mainforce_factor_backtest.build_samples 生成样本（含 fwd5/fwd10、
    price_pos、flow5_amt 等字段）
  - 为每个样本计算三种 flow5 评分
  - IC（Spearman）、五分位收益、高换手高标子集收益
================================================================================
用法：python scripts/flow5_curve_backtest.py [--hold 5 10] [--step 5] [--refresh]
"""

import argparse
import datetime as dt
import os
import sys

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


load_env()
sys.path.insert(0, BACKEND_DIR)

from mainforce_factor_backtest import build_samples, spearman, bucket_table, fmt_bucket  # noqa: E402


def _score_flow5_orig(flow5: float) -> float:
    """现行锚点：单调递增。"""
    pts = [(-10, 0.0), (-6, 15.0), (-3, 30.0), (0, 55.0), (2, 80.0), (5, 100.0)]
    if flow5 <= pts[0][0]:
        return 0.0
    if flow5 >= pts[-1][0]:
        return 100.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= flow5 <= x2:
            return round(y1 + (flow5 - x1) / (x2 - x1) * (y2 - y1), 1)
    return 50.0


def _score_flow5_invu(flow5: float) -> float:
    """倒 U 锚点：温和流入最优，极端流入（散户陷阱）低分。

    保持左侧与原始曲线一致（-10~+2 完全相同），+2 封顶 80 后右侧下降：
      +2→80 / +5→55 / +10→30 / +20→5，>+20 归零。
    """
    pts = [(-10, 0.0), (-6, 15.0), (-3, 30.0), (0, 55.0),
           (2, 80.0), (5, 55.0), (10, 30.0), (20, 5.0)]
    if flow5 <= pts[0][0]:
        return 0.0
    if flow5 >= pts[-1][0]:
        return 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= flow5 <= x2:
            return round(y1 + (flow5 - x1) / (x2 - x1) * (y2 - y1), 1)
    return 50.0


def _score_flow5_overlay(flow5: float, price_pos: float) -> float:
    """原始曲线 + 高位极端流入惩罚：与 distribution 的 ×0.85 同款闸门。"""
    base = _score_flow5_orig(flow5)
    if flow5 > 5 and price_pos > 0.75:
        return round(base * 0.85, 1)
    return base


def _add_scores(samples):
    for s in samples:
        f5 = s.get("flow5_amt")
        pp = s.get("price_pos")
        if f5 is None or pp is None:
            s["score_orig"] = s["score_invu"] = s["score_overlay"] = None
            continue
        s["score_orig"] = _score_flow5_orig(f5)
        s["score_invu"] = _score_flow5_invu(f5)
        s["score_overlay"] = _score_flow5_overlay(f5, pp)
    return samples


def ic_table(samples, holds):
    keys = [
        ("flow5_amt", "原始因子值(%)"),
        ("score_orig", "现行曲线 0-100"),
        ("score_invu", "倒 U 曲线"),
        ("score_overlay", "原始曲线+高位惩罚"),
    ]
    lines = []
    for h in holds:
        lines.append(f"\n### 持有 {h} 日\n")
        lines.append("| 评分口径 | 原始 IC | 去超额 IC | n |")
        lines.append("|---|---|---|---|")
        for key, label in keys:
            ic_raw = spearman(
                [s[key] for s in samples if s.get(key) is not None],
                [s[f"fwd{h}"] for s in samples if s.get(key) is not None])
            ic_x = spearman(
                [s[key] for s in samples if s.get(key) is not None],
                [s[f"x_fwd{h}"] for s in samples if s.get(key) is not None])
            lines.append(
                f"| {label} | "
                f"{ic_raw['rho'] if ic_raw else '-'} | "
                f"{ic_x['rho'] if ic_x else '-'} | "
                f"{ic_raw['n'] if ic_raw else 0} |")
    return "\n".join(lines)


def bucket_section(samples, holds):
    lines = []
    for key, label in [("score_orig", "现行曲线"),
                       ("score_invu", "倒 U 曲线"),
                       ("score_overlay", "原始+高位惩罚")]:
        lines.append(f"\n## {label} 五分位 → 未来收益\n")
        for h in holds:
            rows = bucket_table(samples, key, fwd=f"fwd{h}")
            lines.append(f"\n### 持有 {h} 日\n")
            lines.append(fmt_bucket(rows))
    return "\n".join(lines)


def trap_section(samples, holds):
    """高换手高标子集：price_pos>0.75 & flow5_amt>+5（散户陷阱区）。"""
    from collections import defaultdict
    by_date = defaultdict(list)
    for s in samples:
        by_date[s["date"]].append(s)

    lines = []
    lines.append("\n# 散户陷阱区收益对比（price_pos>0.75 & flow5>+5）\n")
    lines.append("| 方案 | 持有 | n(日) | 命中n | 胜率% | 均收益% | 去超额% |")
    lines.append("|---|---|---|---|---|---|---|")

    for h in holds:
        trap_all = []
        for s in samples:
            if s.get("price_pos") and s.get("flow5_amt") is not None:
                if s["price_pos"] > 0.75 and s["flow5_amt"] > 5:
                    trap_all.append(s)

        for key, label in [("score_orig", "现行曲线"),
                           ("score_invu", "倒 U 曲线"),
                           ("score_overlay", "原始+高位惩罚")]:
            # 每天在该子集里按 score 取 top 20%（模拟榜单择优）
            daily_picks = []
            for d, grp in by_date.items():
                sub = [s for s in grp
                       if s.get("price_pos") and s.get("flow5_amt") is not None
                       and s["price_pos"] > 0.75 and s["flow5_amt"] > 5
                       and s.get(key) is not None]
                if len(sub) < 5:
                    continue
                sub.sort(key=lambda x: x[key], reverse=True)
                k = max(1, len(sub) // 5)
                daily_picks.extend(sub[:k])

            if not daily_picks:
                continue
            rets = [s[f"fwd{h}"] for s in daily_picks]
            xrets = [s[f"x_fwd{h}"] for s in daily_picks]
            win = sum(1 for r in rets if r > 0) / len(rets) * 100
            lines.append(
                f"| {label} | {h}日 | {len(trap_all)} | {len(daily_picks)} | "
                f"{win:.1f} | {sum(rets)/len(rets):.3f} | "
                f"{sum(xrets)/len(xrets):.3f} |")
    return "\n".join(lines)


def analyze(samples, holds):
    samples = _add_scores(samples)
    lines = []
    add = lines.append

    add("# flow5 评分曲线矛盾离线回测（PLAN #8）\n")
    add(f"> 运行：flow5_curve_backtest.py ｜ 生成：{dt.datetime.now():%Y-%m-%d %H:%M}\n")
    add(f"> 截面样本 {len(samples)} 条\n\n")

    add("## 方案定义\n")
    add("- **现行曲线**：`_score_flow5` 单调递增，+5 亿=100 满分。")
    add("- **倒 U 曲线**：温和流入最优，+2 封顶 80，+5→55、+10→30、+20→5。")
    add("- **原始+高位惩罚**：现行曲线不变，但对 `flow5>+5 & price_pos>0.75` 的票"
        "整体 score×0.85（与 distribution 的 ×0.85 闸门同款）。\n")

    add("## 1. IC 对比\n")
    add(ic_table(samples, holds))

    add("\n\n## 2. 五分位单调性\n")
    add(bucket_section(samples, holds))

    add(trap_section(samples, holds))

    # 结论建议
    add("\n\n## 结论建议\n")
    add("- 若 **倒 U 曲线** 的 IC 显著高于现行曲线，且高分位（Q5）收益不反转向下，"
        "建议直接替换后端 `_score_flow5` 与前端的 `scoreFlow5` 同曲线。")
    add("- 若 **原始+高位惩罚** 的 IC 与倒 U 接近，但改动面更小，"
        "建议优先走 overlay 路线（与 distribution 处理一致，避免全面改锚点）。")
    add("- 若两者均不优于现行，说明当前样本下'散户陷阱'证据不足，保持现状。\n")

    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()

    samples, sec_dates = build_samples(args.hold, args.step, refresh=args.refresh)
    report = analyze(samples, args.hold)
    out_dir = os.path.join(BACKEND_DIR, "backtest_reports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"flow5_curve_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
