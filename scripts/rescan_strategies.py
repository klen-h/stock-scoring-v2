#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""历史重扫：zzshare 全市场长历史 K 线逐交易日重跑战法检测 → 独立表 rescan_strategy_results。

【用途】为「战法体系重构 · 第 1 步 ③ 历史重扫」生成历史信号集（造样本）。
【无前视保证】`RESCAN_AS_OF.set(T)` → 战法 scan 内部 `get_kline_with_indicators`
  走 `_get_klines_asof`，只读 `date <= T` 的 K 线（base.py 的 contextvar 分支）。
【存储】独立 db `data/rescan.db`，**绝不碰生产 strategy_results**（现网扫描仍在写它）。
【股票池】非 ST / 非停牌 / close>0 / 成交额>=min_amount（默认不过滤；全市场口径，
  与生产 filter_stock_pool 的市值过滤不同 —— 详见报告 §口径）。

用法：
  python scripts/rescan_strategies.py --start 2026-09-01 --end 2026-09-24      # 冒烟
  python scripts/rescan_strategies.py --start 2023-09-26 --end 2026-09-24      # 3 年
"""
import argparse
import json
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

# 复用回填脚本的 .env 加载（保证 DATA_SOURCE 等一致）
for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
RESCAN_DB = os.path.join(ROOT, "data", "rescan.db")

from app.strategies import list_strategies, get_strategy  # noqa: E402
from app.strategies.base import RESCAN_AS_OF, _clear_kline_cache  # noqa: E402

# 重扫的核心战法（有信号 / 待评估的 6 个）；--all 跑全部注册战法
CORE_STRATEGIES = [
    "single_yang_unbroken", "ma_pullback", "dragon_turnaround",
    "ma_convergence_breakout", "limit_up_boomerang", "advance2retreat1",
]


def init_rescan_db():
    conn = sqlite3.connect(RESCAN_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""CREATE TABLE IF NOT EXISTS rescan_strategy_results (
        strategy_name TEXT NOT NULL, scan_date TEXT NOT NULL,
        count INTEGER DEFAULT 0, results_json TEXT,
        PRIMARY KEY (strategy_name, scan_date))""")
    conn.commit()
    return conn


def build_pool(conn, day, min_amount=0.0):
    """从 zzshare 构造 T 日股票池（非 ST / 非停牌 / close>0 / 成交额>=min_amount）。"""
    rows = conn.execute(
        "SELECT code, close, amount, is_st, is_paused FROM daily WHERE date = ?",
        (day,)).fetchall()
    pool = []
    for r in rows:
        if r[3]:            # is_st
            continue
        if r[4]:            # is_paused
            continue
        close = r[1] or 0
        if close <= 0:
            continue
        amount = r[2] or 0
        if amount < min_amount:
            continue
        code6 = str(r[0]).split(".")[0]
        pool.append({"code": code6, "name": code6, "price": close,
                     "market_cap": 0, "amount": amount, "change_pct": 0})
    return pool


def rescan_day(conn, day, strategies):
    """对 T 日跑全部指定战法，返回 {strategy_en: [signals]}。"""
    pool = build_pool(conn, day)
    if not pool:
        return {}
    token = RESCAN_AS_OF.set(day)      # ★ 无前视锚：本日 scan 只读 <=day 的 K 线
    out = {}
    try:
        for en in strategies:
            strat = get_strategy(en)
            if not strat:
                continue
            try:
                out[en] = strat.scan(pool)
            except Exception as e:
                print(f"  [warn] {en} @ {day} 失败: {type(e).__name__}: {str(e)[:60]}")
                out[en] = []
    finally:
        RESCAN_AS_OF.reset(token)
    _clear_kline_cache()      # 每天清一次，防 5400 天 × 5000 股缓存无限膨胀
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="起始交易日 YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="结束交易日 YYYY-MM-DD")
    ap.add_argument("--all", action="store_true", help="跑全部注册战法（默认 6 个核心）")
    ap.add_argument("--min-amount", type=float, default=0.0, help="成交额下限（元），默认不过滤")
    args = ap.parse_args()

    src = sqlite3.connect(ZZSHARE_DB)
    # 确保复合索引存在（幂等；⚠️ 回填写入期间勿运行本脚本，会锁冲突 —— 建议回填完成后再跑）
    src.execute("CREATE INDEX IF NOT EXISTS idx_daily_code_date ON daily(code, date DESC)")
    src.commit()
    days = [r[0] for r in src.execute(
        "SELECT DISTINCT date FROM daily WHERE date >= ? AND date <= ? ORDER BY date",
        (args.start, args.end)).fetchall()]
    if not days:
        print("无交易日范围。zzshare 已有 2023-09-26 ~ 2026-09-24（3 年完整）；"
              "2005~2023 段仍在回填中。")
        return

    strategies = [s["name_en"] for s in list_strategies()] if args.all else CORE_STRATEGIES
    print(f"战法 {len(strategies)} 个: {strategies}")
    print(f"交易日 {len(days)} 天: {days[0]} ~ {days[-1]}")
    print(f"股票池过滤: 非ST/非停牌/close>0/amount>={args.min_amount}")

    out = init_rescan_db()
    # 断点续传：跳过已完成的 scan_date（一天一个 commit，中断不会半截）
    done_dates = set(r[0] for r in out.execute(
        "SELECT DISTINCT scan_date FROM rescan_strategy_results").fetchall())
    if done_dates:
        print(f"断点续传：跳过已完成的 {len(done_dates)} 天")
    todo_days = [d for d in days if d not in done_dates]
    t0 = time.time()
    total_signals = 0
    for i, day in enumerate(todo_days, 1):
        per_day = rescan_day(src, day, strategies)
        n_day = 0
        for en, signals in per_day.items():
            n_day += len(signals)
            out.execute(
                "INSERT OR REPLACE INTO rescan_strategy_results "
                "(strategy_name, scan_date, count, results_json) VALUES (?,?,?,?)",
                (en, day, len(signals), json.dumps(signals, ensure_ascii=False)))
        out.commit()
        total_signals += n_day
        if i % 20 == 0 or i == len(todo_days):
            el = time.time() - t0
            rate = el / i
            print(f"  [{i}/{len(todo_days)}] {day} 当日信号={n_day} | 累计={total_signals} | "
                  f"已耗时 {el/60:.1f}min | 预计剩余 {rate*(len(todo_days)-i)/60:.1f}min")
    src.close()
    out.close()
    print(f"\n完成：{len(days)} 天，累计信号 {total_signals}，耗时 {(time.time()-t0)/60:.1f} 分钟")


if __name__ == "__main__":
    main()
