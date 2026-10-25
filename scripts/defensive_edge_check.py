#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】H2' 阴跌防御影子榜 —— 前瞻段判定（预登记，冻结判据执行器）
================================================================================

判据（docs/plans/20261026_评分分市况重构方案_v0.md §7 v1.0 冻结 / §9 用户拍板①②）：
  · 只判**前瞻段**：defensive 影子榜上线日（首行 rank_date）之后的阴跌段；
    历史段仅 in-sample 定参（scripts/insample_defensive_params.py），不作判。
  · 每日超额差 = defensive top50 当日等权收益 − base top50 当日等权收益
    （「对全池等权超额差」中基准池相减消去；收益 = backtest_prices close 环比，
    t-1 日出榜 t 日计收益，成员缺价剔除）。
  · GO（PASS）= 前瞻阴跌段 ≥10 且 段均值差 ≥ +0.10pt/日 且 段聚类 P(≤0) ≤ 0.05
    —— GO 仅为候选，动生产须用户拍板（红线）。
  · KILL（FAIL）= 段均值差的 95%CI 上界 ≤ 0。
  · 其余 = PARTIAL（方向未定，继续积累）；段 <10 = INSUFFICIENT（样本闸）。

数据（全 PG，可注册 edge_verify 月度自动复核）：
  shadow_rank_daily(variant=defensive/base) + eq_anchor_history + backtest_prices。

用法：python scripts/defensive_edge_check.py [--json]
================================================================================
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as _e:      # 老 Python/非 tty：不影响主流程，但要留痕（卫生门 #11）
    print(f"[warn] stdout reconfigure failed: {_e}", file=sys.stderr)

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

MIN_SEG = 10            # 样本闸（§7 冻结：≥10 个前瞻阴跌段）
GO_LINE = 0.10          # GO 线：+0.10pt/日
BOOT_B = 5000
COV_FLOOR = 0.70        # 当日成员价格覆盖 <70% ⇒ 该日剔除（防口径失真）


def load_rows():
    """{date: {variant: {code: score}}} 与上线日。"""
    from app.database import db
    rows = db.fetch(
        "SELECT rank_date, variant, code, total_score FROM shadow_rank_daily "
        "WHERE variant IN ('defensive', 'base') ORDER BY rank_date, rank_pos") or []
    days = defaultdict(dict)
    launch = None
    for r in rows:
        d = str(r["rank_date"])[:10]
        days[d].setdefault(r["variant"], {})[r["code"]] = r.get("total_score")
        if r["variant"] == "defensive" and (launch is None or d < launch):
            launch = d
    return days, launch


def load_prices(codes, dates):
    """{code: {date: close}}（backtest_prices，一次性窗口查询）。"""
    from app.database import db
    out = defaultdict(dict)
    if not codes or not dates:
        return out
    ph_c = ",".join(["%s"] * len(codes))
    rows = db.fetch(
        f"SELECT code, date, close FROM backtest_prices "
        f"WHERE code IN ({ph_c}) AND date >= %s AND date <= %s",
        (*codes, min(dates), max(dates))) or []
    for r in rows:
        if r.get("close") is not None:
            out[r["code"]][str(r["date"])[:10]] = float(r["close"])
    return out


def load_bear_segments(launch):
    """前瞻阴跌段 [(s, e)]：eq_anchor state=bear_slow 的连续段（与上线日有交集即计）。"""
    from app.database import db
    rows = db.fetch(
        "SELECT anchor_date, state FROM eq_anchor_history "
        "WHERE state IS NOT NULL ORDER BY anchor_date") or []
    segs, cur = [], None
    for r in rows:
        d = str(r["anchor_date"])[:10]
        if r["state"] == "bear_slow":
            cur = [d, d] if cur is None else [cur[0], d]
        else:
            if cur:
                segs.append(tuple(cur))
                cur = None
    if cur:
        segs.append(tuple(cur))
    if not launch:
        return []
    # 与上线日有交集（段末日 ≥ 上线日）的前瞻段
    return [(s, e) for s, e in segs if e >= launch]


def daily_diff(days, prices):
    """前瞻逐日差：t-1 出榜（defensive/base 各 top50）→ t 日收益差。

    返回 {date: diff_pt}（仅两榜齐备且覆盖达标的日）。"""
    dates = sorted(d for d, v in days.items()
                   if "defensive" in v and "base" in v)
    codes = set()
    for d in dates:
        codes.update(days[d]["defensive"]); codes.update(days[d]["base"])
    out = {}
    for prev, d in zip(dates, dates[1:]):
        rets = {}
        for var in ("defensive", "base"):
            vals = []
            roster = days[prev][var]
            for c in roster:
                p0, p1 = prices.get(c, {}).get(prev), prices.get(c, {}).get(d)
                if p0 and p1 and p0 > 0:
                    vals.append(p1 / p0 - 1.0)
            cov = len(vals) / max(len(roster), 1)
            if cov < COV_FLOOR or not vals:
                rets[var] = None
                continue
            rets[var] = sum(vals) / len(vals)
        if rets.get("defensive") is not None and rets.get("base") is not None:
            out[d] = (rets["defensive"] - rets["base"]) * 100.0
    return out


def seg_stats(diffs, segs):
    """段内日差均值列表（段的判定日 ∩ 有差值日 ≥1 天才算段）。"""
    stats = []
    for s, e in segs:
        vals = [v for d, v in diffs.items() if s <= d <= e]
        if vals:
            stats.append({"s": s, "e": e, "days": len(vals),
                          "mean": sum(vals) / len(vals)})
    return stats


def bootstrap(values, b=BOOT_B, seed=42):
    """段聚类 bootstrap：返回 (mean, lo, hi, p_le0)。"""
    n = len(values)
    mean = sum(values) / n
    rng = random.Random(seed)
    means = sorted(sum(values[rng.randrange(n)] for _ in range(n)) / n
                   for _ in range(b))
    lo, hi = means[int(0.025 * b)], means[int(0.975 * b)]
    p = sum(1 for m in means if m <= 0) / b
    return mean, lo, hi, p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出结构化结论（edge_verify 契约）")
    args = ap.parse_args()

    days, launch = load_rows()
    if not launch:
        obj = {"script": "defensive_edge_check", "verdict": "INSUFFICIENT",
               "n": 0, "need": MIN_SEG, "unit": "前瞻阴跌段",
               "detail": "影子榜(defensive)尚无数据——等 task_shadow_defensive 积累"}
        print(json.dumps(obj, ensure_ascii=False) if args.json else obj["detail"]
              + f"（需 ≥{MIN_SEG} 段）")
        return 0
    segs = load_bear_segments(launch)
    codes = set()
    for d, v in days.items():
        codes.update(v.get("defensive", {})); codes.update(v.get("base", {}))
    prices = load_prices(codes, sorted(days))
    diffs = daily_diff(days, prices)
    stats = seg_stats(diffs, segs)
    n_seg = len(stats)

    print(f"上线日 {launch}；前瞻阴跌段 {n_seg} 段（闸 ≥{MIN_SEG}）；"
          f"逐日差覆盖 {len(diffs)} 日")
    if stats:
        for s in stats[-8:]:
            print(f"  {s['s']}~{s['e']}  {s['days']}日  段均值 {s['mean']:+.3f} pt/日")
    if n_seg < MIN_SEG:
        detail = f"样本 {n_seg} 段(需{MIN_SEG})——继续积累"
        verdict = "INSUFFICIENT"
        extra = {}
    else:
        mean, lo, hi, p = bootstrap([s["mean"] for s in stats])
        print(f"段聚类 bootstrap B={BOOT_B}: 均值 {mean:+.3f} "
              f"95%CI [{lo:+.3f},{hi:+.3f}]  P(≤0)={p:.4f}")
        extra = {"mean": round(mean, 4), "ci": [round(lo, 4), round(hi, 4)],
                 "p_le0": round(p, 4)}
        if mean >= GO_LINE and p <= 0.05:
            verdict, detail = "PASS", (f"GO 候选：段均值差 {mean:+.3f}≥+{GO_LINE}"
                                       f" 且 P={p:.4f}≤0.05 —— 动生产须用户拍板")
        elif hi <= 0:
            verdict, detail = "FAIL", f"KILL：95%CI 上界 {hi:+.3f} ≤ 0"
        else:
            verdict, detail = "PARTIAL", f"方向未定：均值 {mean:+.3f} CI[{lo:+.3f},{hi:+.3f}]"
    obj = {"script": "defensive_edge_check", "verdict": verdict, "n": n_seg,
           "need": MIN_SEG, "unit": "前瞻阴跌段", "detail": detail, **extra}
    if args.json:
        print(json.dumps(obj, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
