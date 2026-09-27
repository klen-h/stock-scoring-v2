#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证决策卡集成：calendar_exception 字段存在且结构稳定（不破坏既有字段）。"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))
os.environ.setdefault('RENDER_READ_ONLY', '1')

from app.trader_brief import build_decision_card

r = build_decision_card()
print("[1] build_decision_card 跑通, 顶层字段数 =", len(r))
for k in ("date", "regime", "stance", "do", "how_much", "if_wrong",
          "negatives", "risk_events", "calendar_exception", "positions_scan",
          "signals_industry", "strategy_quality"):
    print(f"    {k:<20} {'OK' if k in r else 'MISSING'}")

ce = r.get("calendar_exception")
print("\n[2] calendar_exception:")
print(json.dumps({k: v for k, v in (ce or {}).items() if k != "evidence"},
                 ensure_ascii=False, indent=2)[:900])

print("\n[3] 窗口期专项（直接调模块，模拟 2027-02-15 防御市）")
from datetime import date
from app.spring_festival import calendar_exception
for ds, reg in [("2027-02-15", "defensive"), ("2027-01-25", "defensive"),
                ("2027-02-17", "neutral")]:
    x = calendar_exception(regime=reg, today=date.fromisoformat(ds))
    print(f"  {ds} [{reg}] phase={x['phase']:<7} action={x['action']:<6} "
          f"overrides={x['overrides']} targets={[t['code'] for t in (x.get('targets') or [])]} "
          f"pos={(x.get('position_hint') or {}).get('pct')}")
    print(f"        {x.get('note')}")
