import sys, os, time
sys.stdout.reconfigure(encoding="utf-8", errors="replace") if hasattr(sys.stdout, "reconfigure") else None
print("step1: start", flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
print("step1: app imported", flush=True)
from app.database import db
print("step1: db module ok", flush=True)
t0 = time.time()
rows = db.fetch("SELECT DISTINCT code FROM stock_finance LIMIT 1550")
print(f"step1: DISTINCT 查询 {len(rows)} 行, 耗时 {time.time()-t0:.1f}s", flush=True)
