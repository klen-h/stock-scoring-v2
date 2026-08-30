# -*- coding: utf-8 -*-
"""确认快照是三维度(旧)还是五维度(新)体系。"""
import sys, os, json
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from app.database import db

# 看 8-29 快照里五洲交通的维度明细
rows = db.fetch("SELECT code, name, total_score, dimensions_json, created_at "
                "FROM ranking_history WHERE rank_date = '2026-08-29' "
                "ORDER BY rank_pos LIMIT 5")
print("8-29 快照前 5 名的维度明细:")
for r in rows:
    try:
        dims = json.loads(r["dimensions_json"]) if r["dimensions_json"] else {}
    except Exception:
        dims = {}
    keys = list(dims.keys()) if isinstance(dims, dict) else []
    print(f"  {r['code']} {r['name']:<8} 总分={r['total_score']:<6} "
          f"维度键={keys}  创建于 {str(r['created_at'])[:19]}")

# 五维度新体系现算（茅台）的维度键应是: 技术面/资金面/基本面/成长/质量
# 三维度旧体系应是: 技术面/资金面/基本面
print("\n判定: 五维度(新) = {技术面,资金面,基本面,成长,质量}")
print("      三维度(旧) = {技术面,资金面,基本面}")
