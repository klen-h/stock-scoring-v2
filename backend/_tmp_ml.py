# 临时诊断：主线数据断档情况（用完即删，勿提交）
from app.database import db

print("== industry_mainline 最新日期 ==")
r = db.fetch_one("SELECT MAX(date) AS d, COUNT(*) AS c FROM industry_mainline")
print(dict(r or {}))

print("\n== industry_mainline 日期分布（9月以来）==")
for r in db.fetch("SELECT date, COUNT(DISTINCT industry) AS inds FROM industry_mainline "
                  "WHERE date >= '2026-09-01' GROUP BY date ORDER BY date"):
    print(f"{r['date']}  {r['inds']} 行业")

print("\n== ranking_history 9月以来快照日（mainline 的输入）==")
for r in db.fetch("SELECT rank_date, COUNT(*) c FROM ranking_history "
                  "WHERE rank_date >= '2026-09-01' GROUP BY rank_date ORDER BY rank_date"):
    print(f"{r['rank_date']}  {r['c']} 条")
