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

★★ 定版（2026-10-18，快照日 **34** 个 / 2026-08-24~10-16，已过 20 日门槛）：
  **FAIL —— 无可用预测力**。T+2 按日 IC 均值 **−0.003**（中位 +0.047，正比 17/25，
  CI [−0.086,+0.071]，P(均值≥0.10)=0.004）；T+5 均值 **+0.004**（13/22，CI 跨 0）。
  判据（PLAN 原定）IC ≥ 0.10 ⇒ 不达标 ⇒ **维持"独立展示、不进综合分"**。
  ⚠️ 当初的 +0.202 是 **3 个截面 + 跨日合并** 的产物，两个因素都不可信：
     · 3 天样本的 IC 只是噪声；
     · **跨日合并**把不同日期的分数水平混在一起排名 ⇒ 会混入"日期级水平差异"
       （实测合并 −0.181 而按日均值 −0.003，符号被压反）。
  ⇒ 判定口径固定为：**按日算 rank IC → 对日取均值 → 按日聚类 bootstrap**（本脚本已实现）。
  若日后消息分算法有实质改动，须**新开预登记 v2** 再验，不得在本判据上"再跑几次看看"。

用法：python scripts/news_score_validation.py [--fwd 2 5]
"""

import argparse
import os
import random
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
        day_ics = []      # ★ 2026-10-18：**按日 IC 序列**（判定的正确口径，见下方说明）
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
            if ic is not None:
                day_ics.append(ic)
            med = statistics.median(xs)
            hi = [y for x, y in zip(xs, ys) if x > med]
            lo = [y for x, y in zip(xs, ys) if x <= med]
            pooled_x += xs
            pooled_y += ys
            # ★ 2026-10-18 修：某日所有个股 score 相同（全 0 或全同一档）时，
            #   `x > med` 为空集 ⇒ 原来直接除零崩掉（实测跑 6 个截面就挂）。
            #   空组显式打 "—"（**不是 0**：0 会被读成"该组收益为零"）。
            hi_s = f"{sum(hi) / len(hi):+.2f}%" if hi else "—"
            lo_s = f"{sum(lo) / len(lo):+.2f}%" if lo else "—"
            print(f"{d} | {len(xs)} | {ic:+.3f} | {hi_s} | {lo_s}")
        pooled_ic = spearman(pooled_x, pooled_y)
        if pooled_ic is not None:
            print(f"合并: n={len(pooled_x)} IC={pooled_ic:+.3f}"
                  "   ← ⚠️ 跨日合并仅参考：把不同日期的原始分数混在一起排名，"
                  "会混入“日期级分数水平差异”，**不能用于判定**")

        # ★★ 2026-10-18：**判定口径 = 按日 IC 的均值**（rank IC 必须**在截面内**算，
        #   再对日取平均；跨日合并在统计上是错的 —— 实测出现过“合并 −0.181 而
        #   按日多为正”的矛盾，就是日期水平差把符号压反了）。
        #   bootstrap 按**日重采样**（与项目其他检验同纪律：按簇重采样，不按个股）。
        if not day_ics:
            print("样本不足（<20），等 news_history 积累")
            continue
        mean_ic = sum(day_ics) / len(day_ics)
        srt = sorted(day_ics)
        med_ic = srt[len(srt) // 2] if len(srt) % 2 else (srt[len(srt) // 2 - 1] + srt[len(srt) // 2]) / 2
        pos = sum(1 for v in day_ics if v > 0)
        rnd = random.Random(20261018)
        boot = []
        for _ in range(2000):
            pick = [day_ics[rnd.randrange(len(day_ics))] for _ in range(len(day_ics))]
            boot.append(sum(pick) / len(pick))
        boot.sort()
        lo, hi = boot[int(0.025 * len(boot))], boot[int(0.975 * len(boot)) - 1]
        p_pass = sum(1 for b in boot if b >= 0.10) / len(boot)
        print(f"按日 IC: 均值 {mean_ic:+.3f} ｜ 中位 {med_ic:+.3f} ｜ 正比 {pos}/{len(day_ics)}"
              f" ｜ 聚类 bootstrap 95%CI [{lo:+.3f}, {hi:+.3f}] ｜ P(均值≥0.100)={p_pass:.3f}")
        if len(day_ics) < 20:
            print(f"判定: INSUFFICIENT（快照日 {len(day_ics)} < 20）")
        elif mean_ic >= 0.10 and lo > 0:
            print("判定: **PASS**（判据：按日 IC 均值 ≥ 0.10 且 CI 下界 > 0）")
        else:
            print("判定: **FAIL**（判据：按日 IC 均值 ≥ 0.10 且 CI 下界 > 0）"
                  " ⇒ 消息分**无可用预测力**，不得纳入综合分/决策链")


if __name__ == "__main__":
    main()
