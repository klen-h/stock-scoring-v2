# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

print("=== ranking_history 快照日期分布 ===")
rows = db.fetch("SELECT rank_date, COUNT(*) AS c, AVG(total_score) AS avg_score "
                "FROM ranking_history GROUP BY rank_date ORDER BY rank_date")
for r in rows or []:
    print(f"  {r['rank_date']}  {r['c']} 条  均分 {float(r['avg_score'] or 0):.1f}")

print("\n=== 单日样例（最新一天前 5 条）===")
if rows:
    latest = rows[-1]["rank_date"]
    s = db.fetch("SELECT * FROM ranking_history WHERE rank_date=%s ORDER BY id LIMIT 3", (latest,))
    for r in s or []:
        print(" ", dict(r))

print("\n=== 行业映射覆盖 ===")
m = db.fetch("SELECT COUNT(*) AS c FROM stock_industry")
print(f"  stock_industry 表: {m[0]['c'] if m else 0} 只")
d = db.fetch("SELECT COUNT(DISTINCT main_industry) AS c FROM stock_industry")
print(f"  行业数: {d[0]['c'] if d else 0}")
