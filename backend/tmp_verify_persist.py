# -*- coding: utf-8 -*-
"""临时验证：连续上榜天数修复效果（周末/盘前场景）"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app.database import db
from app.scoring.ranking_history import get_ranking_persistence, _today_str

print("today =", _today_str(), "（周六：当天无快照）")

codes = [r["code"] for r in db.fetch(
    "SELECT code FROM ranking_history WHERE rank_date="
    "(SELECT MAX(rank_date) FROM ranking_history) ORDER BY rank_pos LIMIT 8")]

t0 = time.time()
res = get_ranking_persistence(codes)
print(f"\n查询耗时: {time.time()-t0:.2f}s（{len(codes)} 只）\n")

print("=== 连续上榜天数（修复后）===")
for r in res:
    print(f"  {r['code']} {r['name']}: 连续={r['consecutive_days']}天 "
          f"可信度={r['trust_grade']} 分数={r['latest_score']} 信号={r['latest_signal']}")

print("\n=== 分布统计（全量 Top50）===")
all_codes = [r["code"] for r in db.fetch(
    "SELECT code FROM ranking_history WHERE rank_date="
    "(SELECT MAX(rank_date) FROM ranking_history)")]
all_res = get_ranking_persistence(all_codes)
from collections import Counter
c = Counter(r["consecutive_days"] for r in all_res)
for d in sorted(c, reverse=True):
    print(f"  连续 {d} 天: {c[d]} 只")
g = Counter(r["trust_grade"] for r in all_res)
print("  可信度分布:", dict(g))
