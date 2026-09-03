#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】zzshare 财报扩展因子同步 CLI（PLAN_ZZSHARE_INTEGRATION 阶段 1）
================================================================================

用法：
  python scripts/zz_finance_sync.py --limit 50          # 只同步前 50 只（验证）
  python scripts/zz_finance_sync.py --pool scoring      # 全评分池（约 1500+ 只，较慢）
  python scripts/zz_finance_sync.py --dry-run           # 只看池子大小不拉取

池子说明：scoring = stock_finance 中 A 股主板/中小板代码（0/3/6 开头）。
默认限制 --limit 防止误触大面积网络请求（zzshare 慢源，匿名限流）。
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

from app.database import db                                     # noqa: E402
from app.zzshare_finance import sync_latest_finance, stats      # noqa: E402


def scoring_pool(limit: int = None) -> list:
    """评分池候选 = stock_finance 中 A 股（6 位且 0/3/6 开头），不含指数/港股。"""
    rows = db.fetch(
        "SELECT DISTINCT code FROM stock_finance "
        "WHERE length(code) = 6 AND substr(code, 1, 1) IN ('0', '3', '6') "
        "ORDER BY code")
    codes = [r["code"] for r in (rows or [])]
    return codes[:limit] if limit else codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=50, help="同步股票数上限（默认 50）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    codes = scoring_pool(args.limit)
    print(f"[zz_sync] 评分池候选 {len(codes)} 只（limit={args.limit or 'all'}）")
    if args.dry_run:
        print("[zz_sync] dry-run，不拉取")
        return
    if not codes:
        print("[zz_sync] 池为空（请先跑 finance.refresh 填充 stock_finance）")
        return
    res = sync_latest_finance(codes)
    print(f"[zz_sync] 完成: {res}")
    print(f"[zz_sync] 库内总数: {stats()}")


if __name__ == "__main__":
    main()
