# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "backend")
from app.database import db

print("=== daily_reports 近 4 行 ===")
rows = db.fetch("SELECT date, created_at FROM daily_reports ORDER BY date DESC LIMIT 4")
for r in rows:
    print("  ", dict(r))

print("\n=== market_snapshot 近 3 行 ===")
for r in db.fetch("SELECT saved_at, data_date FROM market_snapshot ORDER BY saved_at DESC LIMIT 3"):
    print("  ", dict(r))

print("\n=== 10-19 的批次类产物 ===")
for t, c in (("contradictions", "created_at"), ("gate_snapshot_history", "created_at"),
             ("strategy_results", "date"), ("ranking_live", "created_at")):
    try:
        r = db.fetch_one(f"SELECT MAX({c}) AS x FROM {t}")
        print(f"  {t}: {r['x'] if r else None}")
    except Exception as e:
        print(f"  {t}: {str(e)[:60]}")
