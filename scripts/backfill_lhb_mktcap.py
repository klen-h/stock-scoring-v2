#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】回填 `lhb_history.circ_mktcap`（**上榜日流通市值**）—— 2026-10-11
================================================================================
背景（用户："按你建议来" = 让龙虎榜风险标签覆盖科创板）：
  风险标签的分档分母是**流通市值**（强度 = 净买额 / 流通市值）。
  · 2026-10-11 起 `mainforce/lhb.fetch_day_list` 会把 zzshare `lhb_list` **自带的
    `circ_price`** 存进 `circ_mktcap` ⇒ 新行零额外取数、**全板块可用**；
  · 但**此前入库的行该列为 NULL** ⇒ 读取侧只能回退"本地包收盘 × 股本"，
    而包**无科创板/北交所** ⇒ 那些老行**静默打不出风险标签**（实测近 10 日净卖样本
    6 只仅 4 只可打）。本脚本把这些老行补上。

口径：与 `app.mainforce.lhb.fetch_day_list` **同源同字段**（`lhb_list.circ_price`），
  数学等价于预登记脚本的 `上榜日收盘 × 流通股本`（见 `lhb.ensure_table` 处注释）。

成本：每个已入库交易日 1 次 zzshare 调用（间隔 `--gap` 秒）⇒ 约 106 日 ≈ 1~2 分钟；
  **零 Supabase 读**（只有小批量 UPDATE），不写其它字段、不碰 `seats_json`。

用法：
  python scripts/backfill_lhb_mktcap.py                     # 干跑，只报告
  python scripts/backfill_lhb_mktcap.py --apply             # 落库
  python scripts/backfill_lhb_mktcap.py --apply --days 30   # 只补最近 30 个自然日
================================================================================
"""
import argparse
import os
import sys
import time

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)
for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from datetime import datetime, timedelta                  # noqa: E402

from app.database import db                               # noqa: E402
from app.mainforce.lhb import fetch_day_list, ensure_table   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真落库（默认干跑）")
    ap.add_argument("--days", type=int, default=0, help="只补最近 N 个自然日（0=全部）")
    ap.add_argument("--gap", type=float, default=0.6, help="每次调用间隔秒（防限流）")
    args = ap.parse_args()
    ensure_table()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print("=" * 74)
    print("回填 lhb_history.circ_mktcap（上榜日流通市值）  ［%s］" % mode)
    print("=" * 74)

    # 待补的行（该列为 NULL）按日期归组
    sql = ("SELECT date, COUNT(*) AS n FROM lhb_history "
           "WHERE circ_mktcap IS NULL GROUP BY date ORDER BY date")
    miss = db.fetch(sql) or []
    if args.days:
        since = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        miss = [r for r in miss if str(r["date"])[:10] >= since]
    tot_miss = sum(int(r["n"] or 0) for r in miss)
    got = db.fetch_one("SELECT COUNT(*) AS c FROM lhb_history") or {}
    print("表内共 %s 行；**缺 circ_mktcap 的 %s 行**（分布在 %d 个日期）"
          % (got.get("c"), tot_miss, len(miss)))
    if not miss:
        print("⇒ 无需回填（新行已自带该列）")
        return 0

    upd = 0
    for r in miss:
        d = str(r["date"])[:10]
        items = fetch_day_list(d)                 # ← 与生产**同一函数**（口径同源）
        if not items:
            print("  %s：榜单拉取为空（跳过，可能已不在源的历史范围）" % d)
            time.sleep(args.gap)
            continue
        m = {it["code"]: it["circ_mktcap"] for it in items if it.get("circ_mktcap")}
        n_day = 0
        for code, mc in m.items():
            if args.apply:
                db.execute("UPDATE lhb_history SET circ_mktcap = %s "
                           "WHERE code = %s AND date = %s AND circ_mktcap IS NULL",
                           (mc, code, d))
            n_day += 1
        upd += n_day
        print("  %s：源回 %d 行（可补 %d）%s" % (d, len(items), n_day,
                                          "" if args.apply else "  [干跑]"))
        time.sleep(args.gap)

    print("-" * 74)
    # ★ 统计口径要说清：`upd` = **源里带回该字段的行数**（含本来就已补齐的），
    #   实际写入受 `circ_mktcap IS NULL` 守卫约束 ⇒ 落库后复查"仍缺多少行"才准确。
    left = db.fetch_one("SELECT COUNT(*) AS c FROM lhb_history "
                        "WHERE circ_mktcap IS NULL") or {}
    print("源共带回 %d 行（本轮前缺 %d 行）⇒ 落库后**仍缺 %s 行**%s"
          % (upd, tot_miss, left.get("c"),
             "" if args.apply else "  —— 加 --apply 落库"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
