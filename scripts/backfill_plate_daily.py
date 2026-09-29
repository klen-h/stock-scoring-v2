#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】板块快照 `plate_daily_zz` 缺口回填 / 巡检（2026-09-30，P0）
================================================================================
【为什么有本脚本】
  2026-09-30 排查发现：`plate_daily_zz`（zzshare 板块快照 = **前端「板块分化」的数据源**）
  **停在 09-24，静默断档 5 个交易日** —— 根因是它原先只挂在 Render 的
  `sector_snapshot_loop`（15:10-16:40）上，而日批只写东财版 ⇒ 循环一停摆就无人发现，
  用户看到的是 09-24 的普跌画面（12 涨 92 跌）而当天（09-29）房地产实际 **+3.82%**。

  处置分两层：
    · **防复发**：zzshare 快照已迁入日批（`task_sector_snapshot`），并由同日批
      `task_data_gap` 每晚自愈 + 断档告警（共用 `app/data_gaps.py`）。
    · **补历史**（本脚本）：zzshare `plates_rank` **支持历史日期**（实测 2026-09-30 取
      `2026-09-28`/`2026-09-29` 各返回 104 个板块）⇒ 缺的日子能补回来。
      ⚠️ 东财版**不能**回补（clist 只给当前快照）—— 这正是当初把「板块分化」切到 zzshare
      的原因（见 `flash/scheduler.py` 的 `sector_snapshot_loop` 注释）。

【与日批自愈的分工】两者**共用** `app.data_gaps` 的同一组函数（口径唯一，别各写一套）：
  日批窗口 = 最近 5 个交易日（自动、每晚、无缺口时零请求）；
  本脚本   = 任意窗口 / 指定日（人工，补历史）。

【用法】
  python scripts/backfill_plate_daily.py                    # 只巡检最近 5 个交易日（默认不写）
  python scripts/backfill_plate_daily.py --apply            # 补最近 5 个交易日的缺口
  python scripts/backfill_plate_daily.py --days 40 --apply
  python scripts/backfill_plate_daily.py --date 2026-09-29 --apply
  python scripts/backfill_plate_daily.py --check            # 附带关键表断档自检
⚠️ 默认 **dry-run**（只打印缺口）：补历史是写操作，必须显式 `--apply`。
================================================================================
"""

import argparse
import os
import sys

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    """加载 backend/.env（本地跑用；CI 走环境变量）。"""
    if not os.path.exists(ENV_PATH):
        return
    for line in open(ENV_PATH, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
sys.path.insert(0, BACKEND_DIR)


def main():
    ap = argparse.ArgumentParser(description="板块快照 plate_daily_zz 缺口回填/巡检")
    ap.add_argument("--days", type=int, default=5, help="巡检/回填最近 N 个交易日（默认 5）")
    ap.add_argument("--date", default=None,
                    help="只处理指定交易日（YYYY-MM-DD，多个用逗号分隔；可跨出 --days 窗口）")
    ap.add_argument("--apply", action="store_true", help="真正写入（默认只巡检，dry-run）")
    ap.add_argument("--check", action="store_true", help="附带跑关键表断档自检（check_gaps）")
    args = ap.parse_args()

    from app.data_gaps import (PLATE_KINDS, TOLERATED_LAG_DAYS, backfill_plate,
                               check_gaps, plate_missing, trading_days)

    dates = [d.strip() for d in args.date.split(",") if d.strip()] if args.date else None
    win = dates or trading_days(args.days)
    if not win:
        print("无法确定巡检窗口（交易日历异常）")
        return 2
    print("=" * 74)
    print(f"板块快照巡检｜kind={PLATE_KINDS}｜窗口 {win[0]} ~ {win[-1]}（{len(win)} 个交易日）")
    print("=" * 74)

    miss = plate_missing(args.days, dates=dates)
    print(f"缺口 {len(miss)} 项" + ("" if miss else "（无）"))
    for d, k in miss:
        print(f"   · {d} / {k}")

    if miss and not args.apply:
        print("\n（dry-run：未写入。确认无误后加 --apply 执行回填）")
    elif miss:
        r = backfill_plate(args.days, dates=dates)
        print(f"\n回填：成功 {r['filled']} 项 / 写入 {r['rows']} 行"
              f"（尝试 {len(r['missing'])} 项）")
        if r["failed"]:
            print(f"   ⚠️ 仍失败 {len(r['failed'])} 项：{', '.join(r['failed'][:10])}")
            print("   排查：zzshare 是否可用 / 本地是否配 ZZSHARE_TOKEN（缺 token 会匿名调用 → 0 行）")

    if args.check:
        res = check_gaps()
        print(f"\n断档自检（应处理交易日 {res['expected']}，已检 {res['checked']} 张表，"
              f"落后 ≥{TOLERATED_LAG_DAYS + 1} 个交易日即告警）：")
        if not res["gaps"]:
            print("   ✓ 全部新鲜")
        for g in res["gaps"]:
            flag = "⚠️ 关键" if g["critical"] else "（参考·不告警）"
            print(f"   · {g['table']:<24} 最新 {g['latest']}  落后 {g['lag']} 个交易日  {flag}")
        for n in res["notes"]:
            print(f"   · 跳过：{n}")
        return 0 if res["ok"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
