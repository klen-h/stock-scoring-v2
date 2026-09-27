#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【E4 · 实盘样本复核】涨停家数激增 → 中证1000 ETF 择时
================================================================================
背景：
  E4 = 涨停家数 > 2.0×滚动20日均值，已通过**时间切分样本外预登记复核**
  （训练段 2005-2015 定参 boost=2.0，测试段 2016-2026 样本外 +5.82pp，P=0.0004）。
  但那是历史数据。要确认"实盘中这个信号仍有效"，只能靠实盘样本（market_events 落库）。

数据：
  · `market_events`（每日事件快照，含 e4_limit_surge / limit_up_ma20，
    由 `scheduler.regime_cache_loop` 盘后经 `record_event_snapshot()` 落库）
  · `data/idx_daily.json` 的 sh000852（中证1000，作 512100 ETF 代理）

━━━━━━━━━━━━━━━━━━ 预登记判据（积累到足够样本才适用）━━━━━━━━━━━━━━━━━━
  (a) 样本门槛：E4 实盘天数 >= 10 **且** 独立簇 >= 5；否则 insufficient，不下结论。
  (b) 收益：E4 触发后 T+20 中证1000 收益均值 > 无条件基准 + 1.0pp。
  (c) 胜率：E4 后 T+20 中证1000 收涨的比例 >= 60%。
  三条全过 ⇒ 建议进入「单独预登记的升级评估」；否则维持 v0（只展示）。

⚠️ 本脚本**只读**，不修改任何表、不影响生产行为。
⚠️ 早期样本极少属正常（E4 需先积累 19 天历史才开始判定）。

用法：python scripts/event_e4_review.py
================================================================================
"""
import json
import os
import sys
from datetime import datetime

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from app.database import db          # noqa: E402

# ── 预登记判据 ──
MIN_E4_DAYS = 10
MIN_CLUSTERS = 5
MIN_RET_DIFF = 1.0        # 收益差门槛（pp）
MIN_WIN_RATE = 0.60       # 胜率门槛
HORIZON = 20
CLUSTER_GAP = 28          # 自然日 ≈ 20 交易日


def _days(s):
    return (datetime.strptime(str(s)[:10], "%Y-%m-%d") - datetime(1970, 1, 1)).days


def count_clusters(dates):
    if not dates:
        return 0
    c = 1
    for a, b in zip(dates, dates[1:]):
        if _days(b) - _days(a) >= CLUSTER_GAP:
            c += 1
    return c


def load_idx():
    idx = json.load(open(os.path.join(ROOT, "data", "idx_daily.json"), encoding="utf-8"))
    rows = sorted(idx.get("sh000852") or [], key=lambda x: x["date"])
    dates = [r["date"] for r in rows]
    opens = np.array([r["open"] for r in rows], float)
    closes = np.array([r["close"] for r in rows], float)
    n = len(rows)
    fwd = np.full(n, np.nan)
    for i in range(n - 1 - HORIZON):
        if opens[i + 1] > 0:
            fwd[i] = (closes[i + 1 + HORIZON] / opens[i + 1]) - 1
    return dates, fwd


def main():
    print("=" * 80)
    print("【E4 实盘样本复核】涨停家数激增 → 中证1000 ETF 择时")
    print("=" * 80)

    try:
        events = db.fetch(
            "SELECT date, limit_up, limit_up_ma20, e4_limit_surge "
            "FROM market_events ORDER BY date") or []
    except Exception as e:
        print(f"\nmarket_events 读取失败（可能 e4 列未就绪）: {e}")
        print("⇒ 状态：insufficient（数据未就绪），不下结论。")
        return

    if not events:
        print("\nmarket_events 暂无数据。")
        print("说明：该表由 `scheduler.regime_cache_loop` 在工作日盘后落库（需 ≥19 天历史"
              "\n      才能开始判定 E4，即上线后第 20 个交易日才可能有第一条 E4 记录）。")
        print("⇒ 状态：insufficient（尚未积累任何实盘样本），不下结论。")
        return

    e4_dates = [str(r["date"])[:10] for r in events if r.get("e4_limit_surge")]
    all_dates = [str(r["date"])[:10] for r in events]
    clusters = count_clusters(e4_dates)

    print(f"\n① 数据覆盖")
    print(f"  · 事件快照：{len(events)} 天（{all_dates[0]} ~ {all_dates[-1]}）")
    print(f"  · E4 涨停家数激增：{len(e4_dates)} 天 / {clusters} 簇"
          + (f"  → {', '.join(e4_dates)}" if e4_dates else ""))

    print(f"\n② 样本门槛（E4 ≥ {MIN_E4_DAYS} 天 且 簇 ≥ {MIN_CLUSTERS}）")
    if len(e4_dates) < MIN_E4_DAYS or clusters < MIN_CLUSTERS:
        print(f"  · 当前 E4 {len(e4_dates)} 天 / {clusters} 簇 ⇒ **insufficient**，不下结论。")
        print(f"  · 按历史（21 年 224 天 @boost=2.0，约每年 10.7 天）推算，"
              f"达到门槛预计还需 ~{max(0, MIN_E4_DAYS - len(e4_dates))} 个 E4 日。")
        return

    # ③ 收益（中证1000 作 ETF 代理）
    try:
        dates, fwd = load_idx()
        dpos = {d: i for i, d in enumerate(dates)}
    except Exception as e:
        print(f"  · 中证1000 数据读取失败: {e}")
        return

    def t20_ret(d):
        i = dpos.get(d)
        if i is None or np.isnan(fwd[i]):
            return None
        return float(fwd[i])

    all_rets = [float(fwd[i]) for i in range(len(dates)) if not np.isnan(fwd[i])]
    base = float(np.mean(all_rets))
    e4_rets = [t20_ret(d) for d in e4_dates if t20_ret(d) is not None]
    print(f"\n③ 收益（中证1000，T+{HORIZON}）")
    if len(e4_rets) < 5:
        print(f"  · 可评估 E4 样本 {len(e4_rets)} < 5 ⇒ insufficient，不下结论。")
        return
    e4_mean = float(np.mean(e4_rets))
    diff = (e4_mean - base) * 100
    win = sum(1 for r in e4_rets if r > 0)
    win_rate = win / len(e4_rets)
    print(f"  · E4 后 T+20 均值 = {e4_mean*100:+.2f}%  vs  无条件基准 {base*100:+.2f}%"
          f"  ⇒ 差 {diff:+.2f}pp")
    print(f"  · 胜率 = {win}/{len(e4_rets)} = {win_rate:.0%}（门槛 ≥ {MIN_WIN_RATE:.0%}）")

    print(f"\n④ 预登记判定")
    ok_ret = diff >= MIN_RET_DIFF
    ok_win = win_rate >= MIN_WIN_RATE
    print(f"  (a) 样本门槛：{'✓' if (len(e4_dates) >= MIN_E4_DAYS and clusters >= MIN_CLUSTERS) else '✗'}")
    print(f"  (b) 收益差：{'✓' if ok_ret else '✗'}（{diff:+.2f}pp vs 门槛 +{MIN_RET_DIFF}pp）")
    print(f"  (c) 胜率：{'✓' if ok_win else '✗'}（{win_rate:.0%} vs 门槛 {MIN_WIN_RATE:.0%}）")
    if ok_ret and ok_win:
        print("\n  ★ 暂支持「E4 实盘仍有效」⇒ 可进入单独预登记的升级评估（仍不等同于直接改生产）。")
    else:
        print("\n  ★ 暂不支持 ⇒ 维持 v0（只展示，不进决策链）。")
    print("\n  注：实盘样本仍在积累，本结论随样本增长而更新。")


if __name__ == "__main__":
    main()
