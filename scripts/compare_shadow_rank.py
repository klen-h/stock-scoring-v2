#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】对比「生产 top N」vs「旁路衰减 top N」的未来收益（灰度验证衰减是否有效）
================================================================================

用法：
  python scripts/compare_shadow_rank.py               # 持有 5/10 交易日
  python scripts/compare_shadow_rank.py --horizon 5 10 --top 30

前置：shadow_decay_ranking.py --apply 至少跑过若干交易日（建议两周 ≈ 10 个快照日），
数据落在 shadow_rank_daily 表。

口径：
  · 快照日 = shadow_rank_daily.rank_date；买入价 = 该日 snapshot_price（快照真实价）。
  · 卖出价 = backtest_prices 中「快照日后第 h 个交易日」的收盘价（固定窗口，与
    ranking_history._records_fixed_horizon 同源）。
  · 收益 = (卖出价 / 买入价 - 1) × 100%；胜率 = 收益 > 0 的比例。
  · 只统计「未来 h 个交易日价格已存在」的样本（太新的快照自然跳过）。
================================================================================
"""
import argparse
import os
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

from app.database import db   # noqa: E402


def trading_days():
    rows = db.fetch("SELECT DISTINCT date FROM backtest_prices "
                    "WHERE code = 'sh000300' ORDER BY date ASC") or []
    return [str(r["date"]) for r in rows]


def load_prices(codes, dates):
    """{(code, date): close} —— 按日期批量查 backtest_prices。"""
    out = {}
    if not codes or not dates:
        return out
    ph = ",".join(["%s"] * len(codes))
    for d in dates:
        rows = db.fetch(
            f"SELECT code, close FROM backtest_prices WHERE code IN ({ph}) "
            "AND date = %s", (*codes, d)) or []
        for r in rows:
            if r.get("close") is not None:
                out[(r["code"], d)] = float(r["close"])
    return out


def fwd_return(code, snap_date, snapshot_price, td, horizon, prices):
    """快照日 snap_date 起第 horizon 个交易日的收益；价格缺失返回 None。"""
    if snap_date not in td:
        return None
    idx = td.index(snap_date)
    if idx + horizon >= len(td):
        return None
    target = td[idx + horizon]
    p1 = prices.get((code, target))
    if not p1 or not snapshot_price:
        return None
    return (p1 / snapshot_price - 1) * 100


def avg_ret(rows, td, horizon, prices):
    """一组 top N 的平均收益、胜率、有效样本数。"""
    rets = [r for r in (fwd_return(x["code"], x["rank_date"], x["snapshot_price"],
                                  td, horizon, prices) for x in rows) if r is not None]
    if not rets:
        return None, None, 0
    avg = sum(rets) / len(rets)
    wr = sum(1 for r in rets if r > 0) / len(rets) * 100
    return avg, wr, len(rets)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, nargs="+", default=[5, 10],
                    help="持有交易日数")
    ap.add_argument("--top", type=int, default=30)
    args = ap.parse_args()

    rows = db.fetch(
        "SELECT rank_date, variant, code, name, rank_pos, total_score, snapshot_price "
        "FROM shadow_rank_daily ORDER BY rank_date ASC, variant, rank_pos ASC") or []
    if not rows:
        print("shadow_rank_daily 为空 —— 先跑 shadow_decay_ranking.py --apply 若干天")
        return 1

    groups = defaultdict(lambda: {"base": [], "decay": []})
    for r in rows:
        if r["variant"] in groups[r["rank_date"]]:
            groups[r["rank_date"]][r["variant"]].append(r)
    dates = sorted(groups.keys())

    td = trading_days()
    codes = sorted({r["code"] for r in rows})
    needed = set(dates)
    for d in dates:
        if d in td:
            idx = td.index(d)
            for h in args.horizon:
                if idx + h < len(td):
                    needed.add(td[idx + h])
    prices = load_prices(codes, sorted(needed))

    print("=" * 78)
    print(f"旁路衰减灰度对比  shadow 榜覆盖 {len(dates)} 个快照日："
          f"{dates[0]} ~ {dates[-1]}")
    print(f"样本池 {len(codes)} 只股票；口径 = 快照价买入 → T+N 交易日收盘价卖出")
    print("=" * 78)

    # 汇总（跨快照日平均）
    for h in args.horizon:
        b_all, d_all = [], []
        print(f"\n=== 持有 {h} 个交易日 ===")
        print(f"{'快照日':<12}{'base收益%':>10}{'base胜率%':>10}"
              f"{'decay收益%':>12}{'decay胜率%':>12}{'decay-base':>12}")
        print("-" * 68)
        for d in dates:
            b_avg, b_wr, b_n = avg_ret(groups[d]["base"], td, h, prices)
            d_avg, d_wr, d_n = avg_ret(groups[d]["decay"], td, h, prices)
            if b_avg is None and d_avg is None:
                print(f"{d:<12}{'未到期':>10}{'-':>10}{'未到期':>12}{'-':>12}")
                continue
            diff = (d_avg - b_avg) if (b_avg is not None and d_avg is not None) else None
            b_s = f"{b_avg:+.2f}" if b_avg is not None else "-"
            d_s = f"{d_avg:+.2f}" if d_avg is not None else "-"
            b_w = f"{b_wr:.0f}" if b_wr is not None else "-"
            d_w = f"{d_wr:.0f}" if d_wr is not None else "-"
            df_s = f"{diff:+.2f}" if diff is not None else "-"
            print(f"{d:<12}{b_s:>10}{b_w:>10}{d_s:>12}{d_w:>12}{df_s:>12}")
            if b_avg is not None:
                b_all.append(b_avg)
            if d_avg is not None:
                d_all.append(d_avg)

        if b_all and d_all:
            mb, md = sum(b_all) / len(b_all), sum(d_all) / len(d_all)
            print("-" * 68)
            print(f"{'平均':<12}{mb:>+10.2f}{'':>10}{md:>+12.2f}{'':>12}"
                  f"{md - mb:>+12.2f}")
            verdict = ("decay 更优" if md > mb else
                       "base 更优" if md < mb else "两者相当")
            print(f"\n  → 持有 {h} 日：decay − base = {md - mb:+.2f}pt"
                  f"（{'✓' if md > mb else '✗'} {verdict}）")

    print("\n注：需 ≥10 个快照日（约两周）结论才可信；快照日太少时仅作参考。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
