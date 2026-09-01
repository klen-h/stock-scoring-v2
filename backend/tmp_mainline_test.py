# -*- coding: utf-8 -*-
import io, sys, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.mainline import compute_mainline, get_mainline_summary, push_mainline_report
from app.database import db

dates = [r["rank_date"] for r in db.fetch(
    "SELECT DISTINCT rank_date FROM ranking_history ORDER BY rank_date")]
for d in dates:
    print(compute_mainline(d))

s = get_mainline_summary(days=12)
print("\n== 汇总 ==")
print("days:", s.get("days"), "dates:", s.get("dates"))
print("主线榜:")
for m in s.get("mainlines", []):
    print(f"  {m['industry']:<8} {m['appear']:<6} 日均{m['avg']:<5} "
          f"早{m['early']:<5} 近{m['recent']:<5} 均排名{m['avg_rank']:<4} "
          f"趋势{m['trend']:<5} 今日{m['latest_count']}只 "
          f"{[x['name'] for x in m['latest_stocks'][:4]]}")
print("切换信号:")
for w in s.get("switches", []):
    print(f"  {w['industry']:<8} {w['action']:<4} {w['from']} -> {w['to']}")

# 企微推送（dry-run 会真实发送，注释掉避免打扰）
# push_mainline_report()
