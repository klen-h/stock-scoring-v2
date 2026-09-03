#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】手动触发每日 A 股大盘日报（测试/补跑）
================================================================================
用法：
  python scripts/trigger_daily_report.py            # 生成并推送企微
  python scripts/trigger_daily_report.py --no-push  # 只生成不推送
  python scripts/trigger_daily_report.py --date 2026-09-03   # 读某日（如已存在）
================================================================================
"""

import argparse
import os
import sys

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


load_env()
sys.path.insert(0, BACKEND_DIR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-push", action="store_true", help="只生成不推送企微")
    ap.add_argument("--date", default="", help="读取某日已生成的日报")
    args = ap.parse_args()

    if args.date:
        from app.daily_report import latest
        r = latest(args.date)
        if not r:
            print(f"无 {args.date} 日报")
            return
        print(f"== {r['date']} 日报（{len(r.get('markdown') or '')} 字）==\n")
        print((r.get("markdown") or "")[:2000])
        return

    if args.no_push:
        os.environ["DAILY_REPORT_NO_PUSH"] = "1"
    from app.daily_report import run_daily_report
    res = run_daily_report()
    print("生成结果:", res)
    print("\n--- 预览（前 3000 字）---\n")
    with open(res["path"], "r", encoding="utf-8") as f:
        print(f.read()[:3000])


if __name__ == "__main__":
    main()
