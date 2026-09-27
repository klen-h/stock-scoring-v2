#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 spring_festival 模块：各阶段判定 + 窗口定位精度（对照历史实测值）。"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))
os.environ.setdefault('RENDER_READ_ONLY', '1')
from datetime import date
from app import spring_festival as sf

print("=" * 106)
print("【1】窗口定位：estimate vs 历史实测（22 年，来自「最长休市缺口」自动定位）")
print("=" * 106)
REAL = {  # 2005~2026 实测（节前最后交易日, 节后第一个交易日）
    2005: ("2005-02-04", "2005-02-16"), 2006: ("2006-01-25", "2006-02-06"),
    2007: ("2007-02-16", "2007-02-26"), 2008: ("2008-02-05", "2008-02-13"),
    2009: ("2009-01-23", "2009-02-02"), 2010: ("2010-02-12", "2010-02-22"),
    2011: ("2011-02-01", "2011-02-09"), 2012: ("2012-01-20", "2012-01-30"),
    2013: ("2013-02-08", "2013-02-18"), 2014: ("2014-01-30", "2014-02-07"),
    2015: ("2015-02-17", "2015-02-25"), 2016: ("2016-02-05", "2016-02-15"),
    2017: ("2017-01-26", "2017-02-03"), 2018: ("2018-02-14", "2018-02-22"),
    2019: ("2019-02-01", "2019-02-11"), 2020: ("2020-01-23", "2020-02-03"),
    2021: ("2021-02-10", "2021-02-18"), 2022: ("2022-01-28", "2022-02-07"),
    2023: ("2023-01-20", "2023-01-30"), 2024: ("2024-02-08", "2024-02-19"),
    2025: ("2025-01-27", "2025-02-05"), 2026: ("2026-02-13", "2026-02-24"),
}
ok = 0
print(f"{'年':>5}{'节日':>12}{'定位':>8}{'预测节前最后':>14}{'实测':>14}{'差':>5}"
       f"{'预测节后首个':>14}{'实测':>14}{'差':>5}")
for y in sorted(REAL):
    w = sf.resolve_window(y)
    rl, rf = REAL[y]
    dl = (date.fromisoformat(w['last_td']) - date.fromisoformat(rl)).days
    df = (date.fromisoformat(w['first_td']) - date.fromisoformat(rf)).days
    ok += (dl == 0 and df == 0)
    print(f"{y:>5}{w['festival']:>12}{w['precision']:>8}{w['last_td']:>14}{rl:>14}{dl:>+5}"
          f"{w['first_td']:>14}{rf:>14}{df:>+5}")
print(f"\n完全命中：{ok}/22")

print("\n" + "=" * 106)
print("【2】阶段判定（关键日期）")
print("=" * 106)
for ds in ["2026-09-27", "2027-01-15", "2027-01-25", "2027-02-05", "2027-02-10",
           "2027-02-15", "2027-02-17", "2027-02-25", "2026-02-13", "2026-02-24"]:
    w = sf.window(date.fromisoformat(ds))
    print(f"  {ds}  phase={w['phase']:<8} available={w['available']}  "
          f"days_to_last_td={w.get('days_to_last_td')}  post_index={w.get('post_index')}  "
          f"year={w.get('year')}")

print("\n" + "=" * 106)
print("【3】calendar_exception 输出（regime=defensive）")
print("=" * 106)
import json
for ds in ["2027-01-25", "2027-02-15", "2027-02-17"]:
    r = sf.calendar_exception(regime="defensive", today=date.fromisoformat(ds))
    print(f"\n--- {ds} ---")
    for k in ("active", "phase", "action", "overrides", "title", "precision"):
        print(f"  {k}: {r.get(k)}")
    print(f"  note: {r.get('note')}")
    print(f"  targets: {[t['code'] + '/' + t['name'] for t in (r.get('targets') or [])]}")
    print(f"  position_hint: {(r.get('position_hint') or {}).get('pct')}%")

print("\n" + "=" * 106)
print("【4】非窗口期 & 非防御市况")
print("=" * 106)
r = sf.calendar_exception(regime="defensive", today=date.fromisoformat("2026-09-27"))
print(f"  2026-09-27(defensive): active={r['active']} note={r.get('note')}")
r = sf.calendar_exception(regime="offensive", today=date.fromisoformat("2027-02-15"))
print(f"  2027-02-15(offensive): active={r['active']} action={r['action']} "
      f"overrides={r['overrides']} pos={(r.get('position_hint') or {}).get('pct')}")
print(f"\n  rules(): {json.dumps(sf.rules(), ensure_ascii=False)}")
