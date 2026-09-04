#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【文件作用】消息分（news_score）vs 未来收益验证（CONTEXT 待办 #5 的固化）

数据：news_history 每日 15:20 快照（score 独立展示，不参与综合分）。
口径：快照日收盘 → T+2/T+5 收盘收益（backtest_prices 定长行号），
      截面内做 Spearman IC + 高/低分组对照。

当前样本：2026-08-24 起积累（前 4 天有效 T+2 ≈ 161 样本）。
初步结论（2026-09-05，n=161）：合并 IC +0.202（T+2）——方向为正，
但仅 3 个截面日，不足以定论；建议积累 ≥20 个快照日（约 4 周）后重跑定版。

用法：python scripts/news_score_validation.py [--fwd 2 5]
"""

import argparse
import os
import sys
import time

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)
with open(os.path.join(BACKEND_DIR, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

from app.database import db                                  # noqa: E402

_orig = db.fetch


def q(sql, params=None, retries=4):
    last = None
    for i in range(retries):
        try:
            return _orig(sql, params)
        except Exception as e:
            last = e
            try:
                db._reset_pg_conn()
            except Exception:
                pass
            time.sleep(1.5 * (i + 1))
    raise last


def _rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.0] * len(v)
    for i, idx in enumerate(order):
        r[idx] = i
    return r


def spearman(xs, ys):
    if len(xs) < 20:
        return None
    rx, ry = _rank(xs), _rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    return num / (dx * dy) if dx and dy else None


def forward(code, d, n):
    bars = q("SELECT date, close FROM backtest_prices WHERE code=%s AND date >= %s "
             "ORDER BY date ASC LIMIT %s", (code, d, n + 1))
    if len(bars) < n + 1:
        return None
    return (float(bars[n]["close"]) / float(bars[0]["close"]) - 1) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd", type=int, nargs="+", default=[2, 5])
    args = ap.parse_args()

    rows = q("SELECT snap_date, code, score FROM news_history "
             "WHERE snap_date >= '2026-08-24' ORDER BY snap_date")
    by_date = {}
    for r in rows:
        by_date.setdefault(str(r["snap_date"]), []).append(
            (r["code"], float(r["score"] or 0)))
    print(f"快照日 {len(by_date)} 个：{sorted(by_date)[:1]} ~ {sorted(by_date)[-1:]}")

    import statistics
    for n in args.fwd:
        print(f"\n=== T+{n} ===")
        print("日期 | n | IC | 高分组均收益 | 低分组均收益")
        pooled_x, pooled_y = [], []
        for d in sorted(by_date):
            xs, ys = [], []
            for code, s in by_date[d]:
                f = forward(code, d, n)
                if f is not None:
                    xs.append(s)
                    ys.append(f)
            if len(xs) < 20:
                continue
            ic = spearman(xs, ys)
            med = statistics.median(xs)
            hi = [y for x, y in zip(xs, ys) if x > med]
            lo = [y for x, y in zip(xs, ys) if x <= med]
            pooled_x += xs
            pooled_y += ys
            print(f"{d} | {len(xs)} | {ic:+.3f} | {sum(hi)/len(hi):+.2f}% | {sum(lo)/len(lo):+.2f}%")
        ic = spearman(pooled_x, pooled_y)
        if ic is not None:
            print(f"合并: n={len(pooled_x)} IC={ic:+.3f}"
                  + ("  → 方向为正，继续积累" if ic > 0.05 else
                     "  → 方向为负/无信号，纳入综合分需谨慎" if ic < -0.05 else
                     "  → 无有效信号"))
        else:
            print("样本不足（<20），等 news_history 积累")


if __name__ == "__main__":
    main()
