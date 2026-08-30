# -*- coding: utf-8 -*-
"""测单条 DB 操作耗时（诊断"卡住"是否 = 远程库逐条提交慢）。"""
import sys, os, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
t0 = time.time()
import app  # noqa: F401
print(f"import app: {time.time()-t0:.2f}s")

from app.database import db, DATABASE_URL
import re
m = re.search(r"@([^:/]+)", DATABASE_URL or "")
print("DB host:", (m.group(1) if m else "?")[:40])

t0 = time.time(); db.fetch("SELECT 1 AS ok"); print(f"SELECT 1: {time.time()-t0:.2f}s")
t0 = time.time(); db.upsert("industry_map_meta", {"key": "__t", "value": "1"}, conflict_columns=["key"]); print(f"upsert #1: {time.time()-t0:.2f}s")
t0 = time.time(); db.upsert("industry_map_meta", {"key": "__t", "value": "2"}, conflict_columns=["key"]); print(f"upsert #2: {time.time()-t0:.2f}s")
t0 = time.time(); db.execute("DELETE FROM industry_map_meta WHERE key = %s", ("__t",)); print(f"delete: {time.time()-t0:.2f}s")
