#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证北向「沉默错误」修复：显式不可用 + 向后兼容既有真值判断。"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))
os.environ.setdefault('RENDER_READ_ONLY', '1')

from app.eastmoney import get_northbound

nb = get_northbound()
print("=" * 90)
print("【1】get_northbound() 返回结构")
print("=" * 90)
for k in ("available", "stopped_since", "reason", "time", "sh_net", "sz_net", "total_net"):
    print(f"  {k:<16} = {nb.get(k)}")
print(f"  series 点数      = {len(nb.get('series') or [])}")

print("\n【2】向后兼容性（关键）")
print("=" * 90)
print(f"  `if nb and nb.get('total_net'):` → {bool(nb and nb.get('total_net'))}  "
      f"（应为 False => 老调用方行为不变，0 被过滤）")
print(f"  `get_northbound() or {{}}` 仍非空 → {'是' if nb else '否'} "
      f"（前端可读到 available/reason 以显示明确提示）")

print("\n【3】下游：market_temperature 的 northbound_available")
print("=" * 90)
try:
    from app.routers.market import market_temperature
    t = market_temperature()
    print(f"  northbound_net       = {t.get('northbound_net')}")
    print(f"  northbound_available = {t.get('northbound_available')}  （应为 False）")
    print(f"  temperature          = {t.get('temperature')}  （权重已补给宽度/趋势）")
except Exception as e:
    print(f"  （跳过：{e}）")

print("\n【4】下游：macro 的 note（应显示『已停止披露』而非『仅盘后披露』）")
print("=" * 90)
try:
    from app.macro import get_macro_panel
    p = get_macro_panel() or {}
    notes = p.get("notes") or []
    hit = [x for x in notes if "北向" in str(x)]
    print(f"  含『北向』的 note: {hit}")
    print(f"  含『仅盘后』的旧错误文案: "
          f"{[x for x in notes if '仅盘后' in str(x)] or '（已清除）'}")
except Exception as e:
    print(f"  （跳过：{e}）")
