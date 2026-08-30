# -*- coding: utf-8 -*-
"""验证嫌疑：排行快照（ranking_history）里的分数是否为旧权重体系所算。"""
import sys, os
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from app.database import db

# 最新的排行快照日期
latest = db.fetch_one("SELECT MAX(rank_date) AS d FROM ranking_history")
d = latest["d"] if latest else None
print(f"排行快照最新日期: {d}（今天是周日，快照应为周五 08-28）")

if d:
    rows = db.fetch("SELECT code, name, total_score FROM ranking_history "
                    "WHERE rank_date = %s ORDER BY rank_pos LIMIT 5", (d,))
    print(f"\n{d} 快照前 5 名（当时的分数）:")
    for r in rows:
        print(f"   {r['code']} {r['name']:<8} {r['total_score']}")

    # 用现在的五维度体系抽查其中一只（茅台若在榜）
    m = next((r for r in rows if r["code"] == "600519"), None)
    print(f"\n600519 茅台: 快照分 = {m['total_score'] if m else '不在前5'}")
    print("  现算分（五维度新权重）= 51.9（上一步实测）")
    print("  若两者不同 → 快照是旧体系（40/25/35 三维度）算的，等下个交易日自动刷新")

    # 看快照表里有没有存维度明细，确认体系
    cols = db.fetch_one("SELECT * FROM ranking_history WHERE rank_date = %s LIMIT 1", (d,))
    if cols:
        has_dims = any(k for k in cols.keys() if "dim" in (k or "").lower() or "json" in (k or "").lower())
        print(f"\n快照字段: {list(cols.keys())}")
