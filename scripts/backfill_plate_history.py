# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】回补板块日线快照（`plate_daily_zz`）—— 2026-11-28
================================================================================
为什么需要：
  `plate_daily_zz` 原来只有约 50 个交易日（2026-08-21 起），
  而 `plate_chain.backtest()` 做横截面方向检验需要 1~2 年样本
  （50 天 = 627 个样本，命中率 0.526 ≈ 随机，无法判断关系表对错）。

数据源实测结论（2026-11-28，改动前必读）：
  · `plates_rank(date1=D)` 支持历史，但**有效回溯只到 2023 年年中**：
      2025-06-30 → 104 个板块（完整）
      2024-06-28 → 77 个（板块未长全）
      2021-06-30 → 0 个
  · `plate_kline(b_code=...)` **不可用**：只认同花顺代码（883957），
    我们的 104 个板块是申万 881xxx ⇒ 返回空 DataFrame。
  · 早期（≤2024）`trade_money/net_inflow/market_cap_cir` 为 None，
    **只有 `rate`（涨跌幅）可靠** ⇒ 对走线关系表回测无影响（只用涨跌幅）。

用法：
  python scripts/backfill_plate_history.py --start 2025-01-01
  python scripts/backfill_plate_history.py --start 2023-06-01 --end 2024-12-31
  python scripts/backfill_plate_history.py --start 2025-01-01 --dry-run   # 只看要补几天
  python scripts/backfill_plate_history.py --start 2025-01-01 --max-days 10

幂等：默认跳过库里已有数据的日期，可随时中断重跑。
================================================================================
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))


def _load_env():
    """加载 backend/.env（与其它运维脚本同口径，不覆盖已有环境变量）。"""
    path = os.path.join(ROOT, "backend", ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser(description="回补板块日线快照 plate_daily_zz")
    ap.add_argument("--start", required=True, help="起始日 YYYY-MM-DD（建议 2025-01-01）")
    ap.add_argument("--end", default=None, help="截止日 YYYY-MM-DD（缺省=最近已完成交易日）")
    ap.add_argument("--sleep", type=float, default=0.6, help="每日间隔秒（防限流，默认 0.6）")
    ap.add_argument("--max-days", type=int, default=0, help="最多补几天（试跑用）")
    ap.add_argument("--no-skip", action="store_true", help="不跳过已有日期（覆盖重写）")
    ap.add_argument("--dry-run", action="store_true", help="只统计要补多少天，不请求")
    args = ap.parse_args()

    _load_env()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from app import sector_zz as sz
    from app.database import db

    if args.dry_run:
        from datetime import datetime as _dt
        from app.flash.rules import is_trading_day, latest_completed_trading_day
        from datetime import timedelta as _td
        s = _dt.strptime(args.start, "%Y-%m-%d")
        e = _dt.strptime(args.end or str(latest_completed_trading_day())[:10], "%Y-%m-%d")
        rows = db.fetch("SELECT DISTINCT date FROM plate_daily_zz WHERE kind='industry'") or []
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
        print(f"[dry-run] 区间内交易日 {n} 天，其中待补 {cur} 天（已有 {n - cur} 天）")
        print(f"[dry-run] 预计耗时 ≈ {cur * args.sleep / 60:.1f} 分钟")
        return 0

    r = sz.backfill(args.start, args.end, kinds=("industry",),
                    sleep_sec=args.sleep, skip_existing=not args.no_skip,
                    max_days=args.max_days)
    print("\n=== 回补结果 ===")
    for k in ("ok", "start", "end", "n_days", "done", "written",
              "empty", "failed", "cost_sec"):
        v = r.get(k)
        if k in ("empty", "failed") and isinstance(v, list):
            print(f"  {k:10s} = {len(v)} 天" + (f"  {v[:5]}" if v else ""))
        else:
            print(f"  {k:10s} = {v}")
    rows = db.fetch_one("SELECT COUNT(DISTINCT date) AS d, MIN(date) AS lo, "
                        "MAX(date) AS hi, COUNT(*) AS n FROM plate_daily_zz "
                        "WHERE kind='industry'")
    print(f"\n当前 industry 覆盖: {rows}")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
