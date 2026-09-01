# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.sector_industry import reclassify_others
from app.database import db

r = reclassify_others()
print("重分类:", r["moved"])
for c in ("601388", "603132", "601121", "603979"):
    row = db.fetch_one("SELECT name, main_industry FROM stock_industry WHERE code=%s", (c,))
    if row:
        print(f"  {c} {row['name']} -> {row['main_industry']}")
