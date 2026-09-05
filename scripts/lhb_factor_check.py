#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【文件作用】龙虎榜净买额 vs 未来收益快检（PLAN_NEXT_PHASE P1-4 验收前置）

口径：lhb_history 榜单日收盘 → T+5 收盘收益（backtest_prices 定长行号），
按净买额强度分桶（净买额 / 流通市值，股本来自 market_snapshot 反推）。
样本仅覆盖 544 股评分池内的上榜记录——n 小，只作方向参考，达标才上确认器。

用法：python scripts/lhb_factor_check.py [--fwd 5]
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

from app.database import db                                   # noqa: E402
from app.mainforce.flow import get_float_shares_from_snapshot  # noqa: E402

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd", type=int, default=5)
    args = ap.parse_args()
    n_fwd = args.fwd

    rows = q("SELECT code, name, date, net_buy, quote_change, up_reason FROM lhb_history "
             "ORDER BY date ASC")
    fs_map = get_float_shares_from_snapshot()
    bars_cache = {}
    samples = []
    for r in rows:
        code = r["code"]
        d = str(r["date"])
        bars = bars_cache.get(code)
        if bars is None:
            b = q("SELECT date, close FROM backtest_prices WHERE code=%s ORDER BY date ASC",
                  (code,))
            bars = [(str(x["date"]), float(x["close"])) for x in b]
            bars_cache[code] = bars
        idx = {dd: i for i, (dd, _) in enumerate(bars)}
        i = idx.get(d)
        if i is None or i + n_fwd >= len(bars):
            continue
        fwd = (bars[i + n_fwd][1] / bars[i][1] - 1) * 100
        fs = fs_map.get(code)
        strength = None
        if fs and fs > 0:
            close = bars[i][1]
            strength = (float(r["net_buy"] or 0)) / (close * fs) * 100  # 净买占流通市值%
        samples.append({"code": code, "name": r["name"], "date": d,
                        "fwd": fwd, "strength": strength,
                        "chg": float(r["quote_change"] or 0),
                        "reason": r["up_reason"] or ""})

    print(f"样本 {len(samples)} 条（评分池内上榜，T+{n_fwd}）")
    if len(samples) < 40:
        print("样本不足 40，仅作方向参考")

    # 按净买强度分桶
    def bucket(s):
        if s["strength"] is None:
            return None
        if s["strength"] > 1:
            return "大额净买(>1%流通市值)"
        if s["strength"] > 0:
            return "小额净买"
        if s["strength"] > -1:
            return "小额净卖"
        return "大额净卖(<-1%流通市值)"

    from collections import defaultdict
    groups = defaultdict(list)
    for s in samples:
        b = bucket(s)
        if b:
            groups[b].append(s)
    print(f"\n按净买强度分桶（T+{n_fwd}）:")
    print("| 桶 | n | 胜率% | 均收益% |")
    print("|---|---|---|---|")
    for b in ["大额净买(>1%流通市值)", "小额净买", "小额净卖", "大额净卖(<-1%流通市值)"]:
        g = groups.get(b) or []
        if not g:
            continue
        win = sum(1 for s in g if s["fwd"] > 0) / len(g) * 100
        avg = sum(s["fwd"] for s in g) / len(g)
        print(f"| {b} | {len(g)} | {win:.1f} | {avg:+.2f} |")

    # 上榜日涨跌 vs 后续（追榜 vs 低吸视角）
    hot = [s for s in samples if s["chg"] >= 9.9]
    cold = [s for s in samples if s["chg"] <= 0]
    if hot:
        print(f"\n涨停上榜（追榜视角）: n={len(hot)} T+{n_fwd} 均收益 "
              f"{sum(s['fwd'] for s in hot)/len(hot):+.2f}% "
              f"胜率 {sum(1 for s in hot if s['fwd']>0)/len(hot)*100:.0f}%")
    if cold:
        print(f"非涨停上榜（含跌停上榜）: n={len(cold)} T+{n_fwd} 均收益 "
              f"{sum(s['fwd'] for s in cold)/len(cold):+.2f}% "
              f"胜率 {sum(1 for s in cold if s['fwd']>0)/len(cold)*100:.0f}%")


if __name__ == "__main__":
    main()
