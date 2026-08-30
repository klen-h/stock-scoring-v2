# -*- coding: utf-8 -*-
"""临时验证：P0 改动后美债字段与规则是否生效"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import macro

panel = macro.get_macro_panel()
d = panel.get("_derived", {})

print("=== 美债面板 ===")
for k in ("us2y", "us10y", "us30y"):
    v = panel.get(k)
    if v:
        print(f"  {k}: price={v['price']} prev={v['prev_close']} chg%={v['change_pct']}")
    else:
        print(f"  {k}: 无数据")

print("=== 美债衍生指标 ===")
for k in ("us10y_bp_change", "us2y_bp_change", "us_curve_10y2y_bp", "us_curve_bp_change"):
    print(f"  {k}: {d.get(k)}")

triggered, group_scores, score, level = macro.evaluate_rules(panel, None, None)
print(f"\n=== 方向分 ===")
print(f"  score={score} level={level}")
print(f"  group_scores={group_scores}")
print(f"  rules_version={macro.RULES_VERSION}")

print("=== 全部触发规则 ===")
for t in triggered:
    print(f"  [{t['id']}] {t['tag']} dir={t['direction']} v={t['value']}")

snap = macro.get_macro_snapshot()
print(f"\n=== 快照 ===")
print(f"  data_time={snap['data_time']}")
print(f"  direction: {snap['direction']['score']} / {snap['direction']['level']}")
print(f"  tags_bull={snap['tags_bull']}")
print(f"  tags_bear={snap['tags_bear']}")
