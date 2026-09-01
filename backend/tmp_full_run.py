# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.sector_industry import build_map_full
from app.database import db

r = build_map_full(verbose=False)
print("结果:", r)

total = db.fetch_one("SELECT COUNT(DISTINCT code) AS c FROM stock_industry")["c"]
print("表内股票数:", total)
inds = {x["code"] for x in db.fetch("SELECT DISTINCT code FROM stock_industry")}
top = {x["code"] for x in db.fetch("SELECT DISTINCT code FROM ranking_history")}
miss = [c for c in top if c not in inds]
print(f"Top50 历史覆盖: {len(top)-len(miss)}/{len(top)}  未覆盖 {len(miss)}")
if miss:
    print("未覆盖:", miss[:20])

# 9/1 那 13 只验证
codes = ["605368","002961","002926","001337","601388","601156","603132",
         "601121","603979","603093","603605","601086","600906"]
for c in codes:
    row = db.fetch_one("SELECT main_industry FROM stock_industry WHERE code=%s", (c,))
    print(f"  {c}: {row['main_industry'] if row else '缺失!'}")
