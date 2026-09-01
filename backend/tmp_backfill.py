# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.sector_industry import backfill_missing
from app.database import db

r = backfill_missing(verbose=True)
print("结果:", r)

inds = {x["code"] for x in db.fetch("SELECT DISTINCT code FROM stock_industry")}
top = {x["code"] for x in db.fetch("SELECT DISTINCT code FROM ranking_history")}
miss = [c for c in top if c not in inds]
print(f"\nTop50 覆盖: {len(top)-len(miss)}/{len(top)}  未覆盖 {len(miss)}: {miss}")
