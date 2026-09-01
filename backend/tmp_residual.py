# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

sina = ["玻璃行业","船舶制造","传媒娱乐","电力行业","电器行业","电子器件","电子信息",
"房地产","发电设备","飞机制造","纺织行业","纺织机械","服装鞋类","公路桥梁","供水供气",
"钢铁行业","环保行业","化工行业","化纤行业","家电行业","酒店旅游","家具行业","金融行业",
"交通运输","机械行业","建筑建材","开发区","酿酒行业","摩托车","煤炭行业","农林牧渔",
"农药化肥","汽车制造","其它行业","塑料制品","水泥行业","食品行业","次新股","生物制药",
"商业百货","石油行业","陶瓷行业","物资外贸","医疗器械","仪器仪表","印刷包装","有色金属",
"综合行业","造纸行业"]
ph = ",".join(["%s"] * len(sina))
rows = db.fetch(f"""
    SELECT main_industry, COUNT(*) AS c
    FROM stock_industry
    WHERE main_industry NOT IN ({ph})
    GROUP BY main_industry ORDER BY c DESC
""", sina)
print("非新浪行业残留:", len(rows), "个分类 /",
      sum(r["c"] for r in rows or []), "只")
for r in rows or []:
    print(f"  {r['main_industry']:<14} {r['c']}")

# Top50 未映射的股票
today = db.fetch_one("SELECT date FROM ranking_history ORDER BY rank_date DESC LIMIT 1")
print("\n最新 Top50 日期:", today["date"] if today else None)
if today:
    inds = {r["code"] for r in db.fetch("SELECT code FROM stock_industry")}
    rows = db.fetch("SELECT code, name, rank_pos FROM ranking_history WHERE rank_date=%s AND rank_pos<=50 ORDER BY rank_pos", (today["date"],))
    miss = [r for r in rows or [] if r["code"] not in inds]
    print(f"未映射 {len(miss)}/{len(rows or [])}:", [(r["code"], r["name"]) for r in miss])
