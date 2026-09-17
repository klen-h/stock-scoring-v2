# -*- coding: utf-8 -*-
"""临时：查 indicator_cache / flash events 两条候选的实际 egress 量。用完即删。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.database import db

PATS = ["%indicator_cache%", "%flash_events%", "%flash_analyses%",
        "%FROM flash_reviews%", "%coach_alerts%", "%SELECT value FROM flash_state%"]
for p in PATS:
    rows = db.fetch("SELECT calls, rows, LEFT(query, 100) AS q FROM pg_stat_statements "
                    "WHERE query LIKE %s ORDER BY rows DESC LIMIT 5", (p,)) or []
    print(f"=== {p} ===")
    if not rows:
        print("   （无）")
    for r in rows:
        print(f"   calls={r['calls']:<7} rows={r['rows']:<10} "
              f"{(r['q'] or '').replace(chr(10), ' ')[:100]}")
    print()
