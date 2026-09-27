#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【P0 验证】评分独立性验证 —— 空仓期 TopN 收益回测
================================================================================

验证问题：
  在战法白名单为空的日子（regime = defensive / neutral_bearish），
  评分 TopN 的「次日开盘买入、持有 5/20 日」收益是否为正期望？

口径（与战法回测同标准）：
  · 买入价 = 快照次日开盘价（T+1 open）
  · 卖出价 = 持有期最后一日收盘价（hold 5/20 个交易日的 close）
  · 收益 = (卖出价 / 买入价 - 1) × 100%
  · 空仓期 = state ∈ ("defensive", "neutral_bearish")
  · 非空仓期 = state ∈ ("offensive", "neutral")  ← 对照组

评分分桶：
  · >= 70 分（买入/强烈买入信号区）
  · 60-70 分
  · 50-60 分
  · < 50 分

输出：
  · 空仓期 vs 非空仓期 的各桶胜率/均收益/中位收益/样本数
  · 同口径下沪深300 基准收益（对照）

用法：
  python scripts/p0_score_independence.py
  python scripts/p0_score_independence.py --top 30 --horizons 5 10 20
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

# ── 配置 ──
DEFAULT_TOP = 50
DEFAULT_HORIZONS = (5, 20)
EMPTY_STATES = {"defensive", "neutral_bearish"}
REF_STATES = {"offensive", "neutral"}


def trading_days():
    """沪深300 的交易日历（升序）—— 与 backtest 管线同源。"""
    rows = db.fetch(
        "SELECT DISTINCT date FROM backtest_prices WHERE code = 'sh000300' ORDER BY date ASC"
    ) or []
    return [str(r["date"]) for r in rows]


def load_regime_map():
    """{date: state} —— 只取有 regime 判定的日子。"""
    rows = db.fetch("SELECT date, state FROM market_regime_history ORDER BY date ASC") or []
    return {str(r["date"]): r["state"] for r in rows}


def load_ranking_snapshots():
    """ ranking_history 全部记录 → [{rank_date, code, total_score, signal, price}, ...]

    说明：price 是快照时的实时价（通常≈收盘价），本脚本用**次日开盘价**成交，
    故 price 只作参考（不直接用于收益计算）。"""
    rows = db.fetch(
        "SELECT rank_date, code, total_score, signal, price FROM ranking_history ORDER BY rank_date ASC"
    ) or []
    out = []
    for r in rows:
        out.append({
            "rank_date": str(r["rank_date"]),
            "code": r["code"],
            "total_score": float(r["total_score"]) if r.get("total_score") is not None else None,
            "signal": r.get("signal") or "观望",
            "price": float(r["price"]) if r.get("price") else None,
        })
    return out


def load_opens_and_closes(codes, dates):
    """批量加载指定股票在指定日期的 { (code,date): {'open':x, 'close':y} }。

    策略：按日期批量查，每天一条 SQL（含所有 code），避免逐只逐日爆炸。
    """
    out = {}
    if not codes or not dates:
        return out
    ph = ",".join(["%s"] * len(codes))
    for d in dates:
        rows = db.fetch(
            f"SELECT code, date, open, close FROM backtest_prices WHERE code IN ({ph}) AND date = %s",
            (*codes, d),
        ) or []
        for r in rows:
            if r.get("close"):
                out[(r["code"], str(r["date"]))] = {
                    "open": float(r["open"]) if r.get("open") else None,
                    "close": float(r["close"]),
                }
    return out


def score_bucket(score):
    if score is None:
        return "unknown"
    if score >= 70:
        return ">=70"
    if score >= 60:
        return "60-70"
    if score >= 50:
        return "50-60"
    return "<50"


def calc_returns(snapshots, td, prices, horizons):
    """逐条快照计算各持有期收益。

    买入 = 次日开盘价（T+1 open）；若次日无开盘价数据，尝试用次日收盘价兜底。
    卖出 = T+h 个交易日收盘价。
    """
    td_idx = {d: i for i, d in enumerate(td)}
    results = {h: [] for h in horizons}

    for s in snapshots:
        d = s["rank_date"]
        code = s["code"]
        i = td_idx.get(d)
        if i is None or i + 1 >= len(td):
            continue

        # 买入日 = 快照次日
        entry_date = td[i + 1]
        entry_info = prices.get((code, entry_date))
        if not entry_info:
            continue
        entry_price = entry_info["open"] or entry_info["close"]
        if not entry_price:
            continue

        for h in horizons:
            if i + 1 + h >= len(td):
                continue
            exit_date = td[i + 1 + h]
            exit_info = prices.get((code, exit_date))
            if not exit_info:
                continue
            exit_price = exit_info["close"]
            if not exit_price:
                continue
            ret = (exit_price / entry_price - 1) * 100
            results[h].append({
                "rank_date": d,
                "code": code,
                "score": s["total_score"],
                "signal": s["signal"],
                "bucket": score_bucket(s["total_score"]),
                "entry_price": entry_price,
                "exit_price": exit_price,
                "return": round(ret, 2),
            })
    return results


def calc_index_baseline(td, horizons):
    """计算沪深300 同口径基准收益（次日开盘买、T+h 收盘卖）。"""
    rows = db.fetch(
        "SELECT date, open, close FROM backtest_prices WHERE code = 'sh000300' ORDER BY date ASC"
    ) or []
    idx_price = {str(r["date"]): {"open": float(r["open"]) if r.get("open") else None,
                                   "close": float(r["close"])} for r in rows}
    baseline = {}
    td_idx = {d: i for i, d in enumerate(td)}
    for h in horizons:
        rets = []
        for d in td:
            i = td_idx[d]
            if i + 1 >= len(td) or i + 1 + h >= len(td):
                continue
            entry = idx_price.get(td[i + 1], {}).get("open") or idx_price.get(td[i + 1], {}).get("close")
            exit_p = idx_price.get(td[i + 1 + h], {}).get("close")
            if entry and exit_p:
                rets.append((exit_p / entry - 1) * 100)
        if rets:
            baseline[h] = {
                "n": len(rets),
                "avg": round(sum(rets) / len(rets), 2),
                "median": round(sorted(rets)[len(rets) // 2], 2),
                "win_rate": round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1),
            }
    return baseline


def group_stats(records):
    """一组收益记录 → 统计字典。"""
    if not records:
        return {"n": 0, "win_rate": None, "avg": None, "median": None}
    rets = [r["return"] for r in records]
    n = len(rets)
    wins = sum(1 for r in rets if r > 0)
    s = sorted(rets)
    med = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    return {
        "n": n,
        "win_rate": round(wins / n * 100, 1),
        "avg": round(sum(rets) / n, 2),
        "median": round(med, 2),
    }


def print_report(empty_recs, ref_recs, baseline, horizons, top_n):
    """打印结构化报告。"""
    print(f"\n{'='*80}")
    print(f"【P0 评分独立性验证报告】Top{top_n} / 空仓期 vs 非空仓期")
    print(f"{'='*80}")

    # 先打印数据覆盖概况
    empty_dates = {r["rank_date"] for r in empty_recs}
    ref_dates = {r["rank_date"] for r in ref_recs}
    print(f"\n数据覆盖：")
    print(f"  · 空仓期（defensive+neutral_bearish）样本日：{len(empty_dates)} 天")
    print(f"  · 非空仓期（offensive+neutral）样本日：{len(ref_dates)} 天")

    for h in horizons:
        er = [r for r in empty_recs if r.get("_horizon") == h]
        rr = [r for r in ref_recs if r.get("_horizon") == h]
        print(f"\n{'─'*80}")
        print(f"持有 {h} 个交易日")
        print(f"{'─'*80}")

        # 空仓期
        print(f"\n  ■ 空仓期  ({len(er)} 条有效记录)")
        if er:
            buckets = defaultdict(list)
            for r in er:
                buckets[r["bucket"]].append(r)
            for b in [">=70", "60-70", "50-60", "<50", "unknown"]:
                if b not in buckets:
                    continue
                st = group_stats(buckets[b])
                sigs = sorted(set(r["signal"] for r in buckets[b]))
                print(f"    [{b:>6}]  n={st['n']:>4}  胜率={st['win_rate']:>5}%  "
                      f"均收益={st['avg']:>+6.2f}%  中位={st['median']:>+6.2f}%  "
                      f"信号分布: {', '.join(sigs)}")
            total = group_stats(er)
            print(f"    [合计  ]  n={total['n']:>4}  胜率={total['win_rate']:>5}%  "
                  f"均收益={total['avg']:>+6.2f}%  中位={total['median']:>+6.2f}%")
        else:
            print("    （无有效记录）")

        # 非空仓期
        print(f"\n  ■ 非空仓期  ({len(rr)} 条有效记录)")
        if rr:
            buckets = defaultdict(list)
            for r in rr:
                buckets[r["bucket"]].append(r)
            for b in [">=70", "60-70", "50-60", "<50", "unknown"]:
                if b not in buckets:
                    continue
                st = group_stats(buckets[b])
                sigs = sorted(set(r["signal"] for r in buckets[b]))
                print(f"    [{b:>6}]  n={st['n']:>4}  胜率={st['win_rate']:>5}%  "
                      f"均收益={st['avg']:>+6.2f}%  中位={st['median']:>+6.2f}%  "
                      f"信号分布: {', '.join(sigs)}")
            total = group_stats(rr)
            print(f"    [合计  ]  n={total['n']:>4}  胜率={total['win_rate']:>5}%  "
                  f"均收益={total['avg']:>+6.2f}%  中位={total['median']:>+6.2f}%")
        else:
            print("    （无有效记录）")

        # 基准
        bl = baseline.get(h)
        if bl:
            print(f"\n  ■ 沪深300 基准（同口径）n={bl['n']}  胜率={bl['win_rate']}%  "
                  f"均收益={bl['avg']:+.2f}%  中位={bl['median']:+.2f}%")

        # 结论提示
        if er and rr:
            e_avg = group_stats(er)["avg"]
            r_avg = group_stats(rr)["avg"]
            if e_avg is not None and r_avg is not None:
                gap = e_avg - r_avg
                if e_avg > 0 and e_avg > (bl.get("avg") or 0):
                    verdict = f"空仓期评分独立为正（+{e_avg:.2f}%），且跑赢基准 → 假设3得证"
                elif e_avg > 0:
                    verdict = f"空仓期评分为正（+{e_avg:.2f}%），但未跑赢基准 → 谨慎乐观"
                elif e_avg > r_avg:
                    verdict = f"空仓期评分优于非空仓期（差{gap:+.2f}pp），但仍为负 → 情绪/结构拖累"
                else:
                    verdict = f"空仓期评分（{e_avg:.2f}%）弱于非空仓期（{r_avg:.2f}%）→ 空仓期不可交易"
                print(f"\n  ★ 结论：{verdict}")


def main():
    parser = argparse.ArgumentParser(description="P0 评分独立性验证")
    parser.add_argument("--top", type=int, default=DEFAULT_TOP, help="取每日 Top N（默认50）")
    parser.add_argument("--horizons", type=int, nargs="+", default=list(DEFAULT_HORIZONS), help="持有期列表")
    args = parser.parse_args()

    print("[P0] 开始评分独立性验证...")

    # 1. 基础数据加载
    td = trading_days()
    if len(td) < 30:
        print(f"[错误] 交易日历过短（{len(td)} 天），无法计算收益。请先回填 backtest_prices。")
        return 1
    print(f"[P0] 交易日历：{td[0]} ~ {td[-1]}（{len(td)} 天）")

    regime_map = load_regime_map()
    print(f"[P0] regime 历史：{len(regime_map)} 天")

    all_snaps = load_ranking_snapshots()
    print(f"[P0] ranking_history 记录：{len(all_snaps)} 条")
    if not all_snaps:
        print("[错误] ranking_history 为空，无数据可验证。")
        return 1

    # 2. 按日取 TopN，并按 regime 分组
    by_date = defaultdict(list)
    for s in all_snaps:
        by_date[s["rank_date"]].append(s)

    empty_snaps = []
    ref_snaps = []
    for d, items in by_date.items():
        state = regime_map.get(d)
        if not state:
            continue
        # 取 TopN（按 total_score 降序）
        items.sort(key=lambda x: (x["total_score"] or 0), reverse=True)
        top = items[:args.top]
        if state in EMPTY_STATES:
            empty_snaps.extend(top)
        elif state in REF_STATES:
            ref_snaps.extend(top)

    print(f"[P0] 空仓期快照：{len(empty_snaps)} 条（来自 {len({s['rank_date'] for s in empty_snaps})} 天）")
    print(f"[P0] 非空仓期快照：{len(ref_snaps)} 条（来自 {len({s['rank_date'] for s in ref_snaps})} 天）")

    # 3. 批量加载价格（需要的价格日期 = 所有快照次日 + 各持有期末日）
    needed_dates = set()
    needed_codes = set()
    for s in empty_snaps + ref_snaps:
        d = s["rank_date"]
        code = s["code"]
        i = td.index(d) if d in td else None
        if i is None or i + 1 >= len(td):
            continue
        needed_codes.add(code)
        needed_dates.add(td[i + 1])  # 买入日
        for h in args.horizons:
            if i + 1 + h < len(td):
                needed_dates.add(td[i + 1 + h])  # 卖出日

    print(f"[P0] 批量加载价格：{len(needed_codes)} 只股票 × {len(needed_dates)} 个日期...")
    prices = load_opens_and_closes(list(needed_codes), sorted(needed_dates))
    print(f"[P0] 价格缓存命中：{len(prices)} 条")

    # 4. 计算收益
    empty_returns = calc_returns(empty_snaps, td, prices, args.horizons)
    ref_returns = calc_returns(ref_snaps, td, prices, args.horizons)

    # 5. 合并为统一记录列表（打标 horizon）
    empty_recs = []
    ref_recs = []
    for h in args.horizons:
        for r in empty_returns.get(h, []):
            r["_horizon"] = h
            empty_recs.append(r)
        for r in ref_returns.get(h, []):
            r["_horizon"] = h
            ref_recs.append(r)

    # 6. 计算基准
    baseline = calc_index_baseline(td, args.horizons)

    # 7. 输出报告
    print_report(empty_recs, ref_recs, baseline, args.horizons, args.top)

    print(f"\n{'='*80}")
    print("[P0] 验证完成。")
    print(f"{'='*80}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
