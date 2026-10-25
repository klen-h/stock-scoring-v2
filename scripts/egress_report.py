#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数据库流量（egress）归因 —— 快照 / 日报。

用法：
  python scripts/egress_report.py --snapshot      # 存今天这份计数器（日批已自动做）
  python scripts/egress_report.py                 # 看最近两份快照的差值（谁最贵）
  python scripts/egress_report.py --day 2026-10-25
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app import egress_monitor as em  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", action="store_true", help="存一份当前计数器快照")
    ap.add_argument("--day", default=None, help="看哪一天的差（默认最新快照）")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    if args.snapshot or not args.day:
        if args.snapshot:
            print(json.dumps(em.snapshot(), ensure_ascii=False))
            return 0
    r = em.report(day=args.day, top=args.top)
    if not r.get("ok"):
        print(r.get("note") or "无数据")
        return 1
    print(f"== {r['prev']} → {r['day']}  估算出站 ≈ {r['est_total_mb']} MB ==")
    print("\n-- 语句 Top --")
    for it in r["statements"]:
        print(f"  {it['est_mb']:>8.2f}MB  rows={it['rows']:>10}  calls={it['calls']:>7}  "
              f"[{it['table'] or '?'}] {it['q'][:90]}")
    print("\n-- 表读放大 Top --")
    for it in r["tables"]:
        print(f"  {it['est_mb']:>8.2f}MB  rows={it['rows']:>10}  {it['table']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
