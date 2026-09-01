# -*- coding: utf-8 -*-
"""Top50 板块共振/主线分析：行业在每日 Top50 的扎堆程度 + 多日趋势。"""
import io, sys
from collections import defaultdict
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

rows = db.fetch("SELECT rank_date, code, name, rank_pos, total_score FROM ranking_history ORDER BY rank_date")
by_date = defaultdict(list)
for r in rows or []:
    by_date[r["rank_date"]].append(r)
dates = sorted(by_date)
N = len(dates)
ind_map = {r["code"]: r["main_industry"]
           for r in db.fetch("SELECT code, main_industry FROM stock_industry")}

# 每日行业聚合
daily = defaultdict(lambda: defaultdict(dict))
for d, items in by_date.items():
    for it in items:
        ind = ind_map.get(it["code"], "未知行业")
        st = daily[ind][d]
        st["count"] = st.get("count", 0) + 1
        st["sum_rank"] = st.get("sum_rank", 0) + it["rank_pos"]
        st.setdefault("stocks", []).append(f"{it['code']}{it['name']}")

split = max(1, N // 2)
early, late = dates[:split], dates[split:]

print(f"样本：{N} 个交易日（{dates[0]} ~ {dates[-1]}），每日 Top50\n")
print(f"{'行业':<10}{'出现天数':<8}{'日均只数':<8}{'早期均数':<8}{'近期均数':<8}{'平均排名':<8}趋势")
all_rows = []
for ind, dd in daily.items():
    appear = len(dd)
    tot_count = sum(v["count"] for v in dd.values())
    avg_count = tot_count / appear
    avg_rank = sum(v["sum_rank"] for v in dd.values()) / max(1, tot_count)
    e_days = [d for d in early if d in dd]
    l_days = [d for d in late if d in dd]
    early_avg = sum(dd[d]["count"] for d in e_days) / max(1, len(e_days))
    late_avg = sum(dd[d]["count"] for d in l_days) / max(1, len(l_days))
    trend = "↑上升" if late_avg > early_avg + 0.5 else ("↓下降" if late_avg < early_avg - 0.5 else "→持平")
    all_rows.append((ind, appear, avg_count, early_avg, late_avg, avg_rank, trend))

# 按"近期扎堆程度 × 出现天数"排序，输出共振明显的行业
cand = [r for r in all_rows if r[1] >= N * 0.5 and r[2] >= 1.2]
cand.sort(key=lambda r: (-r[4], -r[1], r[5]))
for ind, appear, avg_count, ea, la, ar, trend in cand:
    print(f"{ind:<10}{appear}/{N:<6}{avg_count:<8.2f}{ea:<8.2f}{la:<8.2f}{ar:<8.1f}{trend}")

print(f"\n=== 候选主线：出现≥{int(N*0.5)}天 且 日均≥1.2 只，共 {len(cand)} 个行业 ===")
for ind, appear, avg_count, ea, la, ar, trend in cand[:6]:
    print(f"\n■ {ind}（近期日均 {la:.1f} 只，平均排名 {ar:.0f}，{trend}）")
    for d in dates:
        st = daily[ind].get(d)
        if st:
            stocks = "、".join(st["stocks"])
            print(f"  {d}  {st['count']}只  [{stocks}]")
