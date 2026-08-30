# -*- coding: utf-8 -*-
"""端到端 HTTP 测试：/api/flash/calendar 各参数组合 + 参数校验。"""
import sys, os, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app  # noqa: F401
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routers import flash

_app = FastAPI()
_app.include_router(flash.router, prefix="/api/flash")
c = TestClient(_app)

CASES = [
    "",                          # 默认
    "?days=7&min_star=4",        # 高星
    "?days=14&min_star=5",       # 仅★★★★★
    "?days=3&kind=data",         # 只看经济指标
    "?days=14&kind=holiday",     # 只看休市
    "?days=14&kind=event",       # 只看事件
    "?days=100",                 # 越界 → 期望 422
    "?min_star=9",               # 越界 → 期望 422
]
for q in CASES:
    r = c.get("/api/flash/calendar" + q)
    if r.status_code != 200:
        print(f"{q or '(默认)'} -> HTTP {r.status_code}（参数校验生效）")
        continue
    d = r.json()
    print(f"{q or '(默认)'} -> count={d['count']} total={d['total']} "
          f"updated={d['updated_at'][:16]}")
    for it in d["items"][:2]:
        extra = ""
        if it["kind"] == "data":
            extra = f"前值{it.get('prev')} 预期{it.get('consensus')} 实际{it.get('actual')}"
        elif it["kind"] == "holiday":
            extra = f"{it.get('exchange')} {it.get('rest_note')}"
        print(f"    {it['date']} [{it['kind']}] {it['country']} "
              f"{it['title']} ★{it.get('star')} {extra}")

print("\n=== POST /api/flash/calendar/refresh ===")
r = c.post("/api/flash/calendar/refresh?days_ahead=14")
print("HTTP", r.status_code, r.json())

print("\n=== 边界：days=1（仅今天）===")
r = c.get("/api/flash/calendar?days=1")
print("HTTP", r.status_code, "count =", r.json()["count"])
