# -*- coding: utf-8 -*-
"""临时：取出 backtest_prices 大查询的完整 SQL，用于定位源码位置。用完即删。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.database import db

rows = db.fetch("SELECT calls, rows, query FROM pg_stat_statements "
                "WHERE query LIKE %s ORDER BY rows DESC LIMIT 12",
                ("%backtest_prices%",)) or []
for i, r in enumerate(rows, 1):
    q = (r["query"] or "").replace("\n", " ")
    print(f"--- #{i} calls={r['calls']} rows={r['rows']} 行均={r['rows'] / max(r['calls'], 1):.0f}")
    print("    " + q[:520])
    print()
