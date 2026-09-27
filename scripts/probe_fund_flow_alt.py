#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可行性评估（续）：
  A) 直接请求东财北向原始接口 —— 确认「断供」是数据源问题还是项目解析问题
  B) 用项目自有 mainflow_history 聚合「市场级主力净流入」，看关键日是否可见
     （即：北向死了之后，自有的「资金转向」信号还能不能替代）
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))
os.environ.setdefault('RENDER_READ_ONLY', '1')

print("=" * 100)
print("【A】直接请求东财北向原始接口 kamt.rtmin（绕过项目解析）")
print("=" * 100)
try:
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"})
    r = s.get("http://push2.eastmoney.com/api/qt/kamt.rtmin/get",
              params={"fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56"},
              timeout=15)
    j = r.json()
    d = (j or {}).get("data") or {}
    print(f"  HTTP {r.status_code}  |  data 层键: {list(d.keys())}")
    s2n = d.get("s2n") or []
    print(f"  s2n 点数 = {len(s2n)}")
    for row in (s2n[:3] + s2n[-3:]):
        print(f"    {row}")
    nz = [x for x in s2n if x.split(",")[5] not in ("0.00", "0", "-")]
    print(f"  合计净流入非零的点数 = {len(nz)}  =>  0 表示『已不再披露净流入』")
except Exception as e:
    print(f"  异常: {e}")

print("\n" + "=" * 100)
print("【B】自有替代：mainflow_history 聚合「市场级主力净流入」")
print("=" * 100)
from app.database import db

rows = db.fetch("""
    SELECT date, SUM(main_net) AS net, SUM(super_net) AS snet,
           COUNT(*) AS n, SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) AS up
    FROM mainflow_history GROUP BY date ORDER BY date
""")
# Decimal → float（psycopg2 的 SUM 返回 Decimal，直接除 float 会 TypeError）
for _r in rows:
    _r['net'] = float(_r.get('net') or 0)
    _r['snet'] = float(_r.get('snet') or 0)

print(f"  可用交易日 = {len(rows)}  （{rows[0]['date']} ~ {rows[-1]['date']}）")

ev = json.load(open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 'data', 'event_history.json'), encoding='utf-8'))

print(f"\n  {'日期':>12}{'主力净流入(亿)':>15}{'超大单(亿)':>13}{'覆盖股数':>9}"
      f"{'该股池up率':>11}{'全市场up_ratio':>14}")
for r in rows:
    k = str(r['date'])[:10]
    if k not in ('2026-03-16', '2026-07-17', '2026-07-21', '2026-07-27',
                 '2026-09-16', '2026-09-21', '2026-09-22', '2026-09-24'):
        continue
    upr = (r['up'] or 0) / (r['n'] or 1)
    mkt = (ev.get(k) or {}).get('up_ratio')
    print(f"  {k:>12}{(r['net'] or 0)/1e8:>15.2f}{(r['snet'] or 0)/1e8:>13.2f}"
          f"{r['n']:>9}{upr:>11.3f}{(f'{mkt:.3f}' if mkt is not None else '—'):>14}")

# 相关性：主力净流入 vs 当日/次日全市场 up_ratio
print(f"\n  【全区间相关性（134 天）】")
import statistics
pairs = []
for r in rows:
    k = str(r['date'])[:10]
    m = (ev.get(k) or {}).get('up_ratio')
    if m is not None:
        pairs.append(((r['net'] or 0) / 1e8, m))
if len(pairs) > 5:
    xs = [p[0] for p in pairs]
    ys = [p[1] for p in pairs]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
    print(f"    corr(主力净流入, 当日 up_ratio) = {num/den:+.3f}  (n={len(pairs)})")
    # 次日
    ks = [str(r['date'])[:10] for r in rows]
    nxt = []
    for i, r in enumerate(rows[:-1]):
        nk = ks[i + 1]
        m = (ev.get(nk) or {}).get('up_ratio')
        if m is not None:
            nxt.append(((r['net'] or 0) / 1e8, m))
    if len(nxt) > 5:
        xs = [p[0] for p in nxt]
        ys = [p[1] for p in nxt]
        mx, my = statistics.mean(xs), statistics.mean(ys)
        num = sum((x - mx) * (y - my) for x, y in nxt)
        den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        print(f"    corr(主力净流入, 次日 up_ratio) = {num/den:+.3f}  (n={len(nxt)})")
