# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.sector_industry import cleanup_industry_residual, _SINA_INDUSTRIES
from app.database import db

r = cleanup_industry_residual()
print("结果:", r)

rows = db.fetch("SELECT main_industry, COUNT(*) AS c FROM stock_industry "
                "WHERE main_industry NOT IN %s GROUP BY main_industry ORDER BY c DESC",
                (tuple(_SINA_INDUSTRIES),))
if rows:
    print(f"仍残留 {len(rows)} 类:", [(x["main_industry"], x["c"]) for x in rows])
else:
    print("残留清零，全表已统一为新浪一级行业")
print("表总行数:", db.fetch_one("SELECT COUNT(*) AS c FROM stock_industry")["c"])
print("映射覆盖股票数:", db.fetch_one("SELECT COUNT(DISTINCT code) AS c FROM stock_industry")["c"])
