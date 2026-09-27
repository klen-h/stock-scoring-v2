#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【回填 / 积累】zzshare 板块快照（表 `plate_daily_zz`）
================================================================================
为什么需要它：
  · 东财 `sector_daily` 因 push2 被限流而**缺日**（实测停在 2026-09-23），
    且 clist 只给**当前**快照 ⇒ 错过当天即永久缺失，无法回补。
  · zzshare `plates_rank(plate_type, date1)` **支持任意历史日期** ⇒ 可回填。
  · 两套 taxonomy 不兼容（东财 BK/496 vs zzshare 881/104）⇒ 本脚本写**独立表**
    `plate_daily_zz`，绝不混入东财表。

用法：
  python scripts/backfill_plate_zz.py --days 20            # 回填最近 20 个交易日
  python scripts/backfill_plate_zz.py --dates 2026-09-24 2026-09-23
  python scripts/backfill_plate_zz.py --days 5 --force     # 已有也重写

幂等：默认跳过已有该日数据的日期（`--force` 可强制重写）。
限流：zzshare 匿名有限流 ⇒ 每个日期间隔 1.2s，失败自动重试一次。
================================================================================
"""
import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def recent_trading_days(n: int) -> list:
    """最近 n 个交易日（含最近已完成交易日），正序。"""
    from app.flash.rules import is_trading_day, latest_completed_trading_day
    d = datetime.strptime(latest_completed_trading_day(), "%Y-%m-%d").date()
    out = []
    guard = 0
    while len(out) < n and guard < n * 4 + 60:
        guard += 1
        if is_trading_day(d):
            out.append(d.strftime("%Y-%m-%d"))
        d -= timedelta(days=1)
    return list(reversed(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=20, help="回填最近 N 个交易日")
    ap.add_argument("--dates", nargs="*", default=None, help="指定日期（YYYY-MM-DD）")
    ap.add_argument("--force", action="store_true", help="已有数据也重写")
    args = ap.parse_args()

    from app.database import db
    from app.sector_zz import take_snapshot

    dates = args.dates or recent_trading_days(args.days)
    print(f"待处理 {len(dates)} 个交易日：{dates[0]} ~ {dates[-1]}")

    have = set()
    if not args.force:
        try:
            rows = db.fetch("SELECT DISTINCT date FROM plate_daily_zz") or []
            have = {str(r["date"])[:10] for r in rows}
        except Exception as e:
            print(f"读取已有日期失败（首次运行属正常）: {e}")

    done = skip = fail = 0
    for i, d in enumerate(dates):
        if d in have and not args.force:
            skip += 1
            continue
        r = take_snapshot(d)
        n = r.get("written", 0)
        if n > 0:
            done += 1
            print(f"  [{i + 1}/{len(dates)}] {d}  写入 {n} 行  {r.get('kinds')}")
        else:
            fail += 1
            print(f"  [{i + 1}/{len(dates)}] {d}  失败/空  {r.get('skipped')}")
        if i < len(dates) - 1:
            time.sleep(1.2)          # zzshare 匿名限流保护

    print(f"\n完成：写入 {done} 天 / 跳过 {skip} 天 / 失败 {fail} 天")
    from app.sector_zz import stats
    print("表概况:", stats())


if __name__ == "__main__":
    main()
