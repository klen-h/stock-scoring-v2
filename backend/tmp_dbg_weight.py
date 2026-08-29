# -*- coding: utf-8 -*-
"""临时排查 weight-advice 500"""
import sys, io, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, '.')
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app, raise_server_exceptions=True)
print("--- POST /api/score/weight-advice (auto) ---")
try:
    r = c.post("/api/score/weight-advice", json={})
    print("status:", r.status_code)
    print(r.json() if r.status_code == 200 else r.text[:2000])
except Exception:
    print("=== EXCEPTION ===")
    traceback.print_exc()
