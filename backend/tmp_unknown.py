# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

codes = ["605368","002961","002926","001337","601388","601156","603132",
         "601121","603979","603093","603605","601086","600906"]
ph = ",".join(["%s"] * len(codes))
rows = db.fetch(f"SELECT code, name, main_industry FROM stock_industry "
                f"WHERE code IN ({ph})", codes)
print(f"在 stock_industry 中的: {len(rows)}")
for r in rows or []:
    print(f"  {r['code']} {r['name']} -> {r['main_industry']}")

# 看这些股票是否曾出现在历史 Top50 里（是否新股）
rows2 = db.fetch(f"SELECT rank_date, name FROM ranking_history "
                 f"WHERE code IN ({ph}) ORDER BY rank_date", codes)
dates = {}
for r in rows2 or []:
    dates.setdefault(r["code"], []).append(r["rank_date"])
for c in codes:
    print(f"  {c}: 上榜 {len(dates.get(c, []))} 天 {dates.get(c, [])[:3]}")
