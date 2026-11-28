# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】回补题材轮动历史（`topic_rotation`）—— 2026-11-28
================================================================================
数据源：`plates_rank_days_new(plate_type=17, date2=D, n_days=N, prev_days=3)`
  实测可回溯到 **2023-06-30**（每个日期都返回 100 条），比行业板块（77~82 条）更完整。

每天存 3 档累计口径（`n_days=1/5/20`）：
  · 1  = 当日涨幅（`verify()` 算次日收益全靠它，**不能省**）
  · 5  = 5 日累计（轮动排名主口径）
  · 20 = 20 日累计（算加速度：5 日 vs 20 日日均差）

成本：3 次请求/天 × 0.6s ≈ 2 秒/天。
  · 回补 1 年（约 250 交易日）≈ 8 分钟
  · 回补 2.5 年（约 600 交易日）≈ 20 分钟

用法：
  python scripts/backfill_topic_rotation.py --start 2025-01-01 --dry-run
  python scripts/backfill_topic_rotation.py --start 2025-01-01 --max-days 60
  python scripts/backfill_topic_rotation.py --start 2023-06-01
================================================================================
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _load_env():
    path = os.path.join(ROOT, "backend", ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser(description="回补题材轮动历史 topic_rotation")
    ap.add_argument("--start", required=True, help="起始日 YYYY-MM-DD（建议 2025-01-01）")
    ap.add_argument("--end", default=None, help="截止日（缺省=最近已完成交易日）")
    ap.add_argument("--sleep", type=float, default=0.6, help="每日间隔秒（默认 0.6）")
    ap.add_argument("--max-days", type=int, default=0, help="最多补几天（分批跑用）")
    ap.add_argument("--n-days", default="1,5,20",
                    help="累计口径，逗号分隔（默认 1,5,20）。只验证时用 1,5 可省 1/3 时间")
    ap.add_argument("--no-skip", action="store_true", help="不跳过已有日期")
    ap.add_argument("--dry-run", action="store_true", help="只统计待补天数")
    args = ap.parse_args()

    _load_env()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from app.database import db
    from app import topic_rotation as tr

    if args.dry_run:
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        from app.flash.rules import is_trading_day, latest_completed_trading_day
        s = _dt.strptime(args.start, "%Y-%m-%d")
        e = _dt.strptime(args.end or str(latest_completed_trading_day())[:10], "%Y-%m-%d")
        rows = db.fetch("SELECT DISTINCT date FROM topic_rotation WHERE plate_type=17") or []
        have = {str(r.get("date"))[:10] for r in rows if r.get("date")}
        n = cur = 0
        while s <= e:
            day = s.strftime("%Y-%m-%d")
            try:
                ok = bool(is_trading_day(s))
            except Exception:
                ok = s.weekday() < 5
            if ok:
                n += 1
                if day not in have:
                    cur += 1
            s += _td(days=1)
        print(f"[dry-run] 区间内交易日 {n} 天，待补 {cur} 天（已有 {n - cur} 天）")
        print(f"[dry-run] 预计耗时 ≈ {cur * 3 * args.sleep / 60:.1f} 分钟（3 档口径）")
        return 0

    try:
        nds = tuple(int(x) for x in str(args.n_days).split(",") if str(x).strip())
    except ValueError:
        nds = (1, 5, 20)
    r = tr.backfill(args.start, args.end, n_days_list=nds, sleep_sec=args.sleep,
                    skip_existing=not args.no_skip, max_days=args.max_days)
    print("\n=== 回补结果 ===")
    for k in ("ok", "start", "end", "n_days", "done", "written",
              "empty", "failed", "cost_sec"):
        v = r.get(k)
        if k in ("empty", "failed") and isinstance(v, list):
            print(f"  {k:10s} = {len(v)} 天" + (f"  {v[:3]}" if v else ""))
        else:
            print(f"  {k:10s} = {v}")
    rows = db.fetch_one("SELECT COUNT(DISTINCT date) AS d, MIN(date) AS lo, "
                        "MAX(date) AS hi, COUNT(*) AS n FROM topic_rotation")
    print(f"\n当前 topic_rotation 覆盖: {rows}")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
