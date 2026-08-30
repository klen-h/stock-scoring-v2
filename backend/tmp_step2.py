import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from app.database import db

print("step2: start", flush=True)
rows = db.fetch("SELECT DISTINCT code FROM stock_finance LIMIT 100")
codes = [r["code"] for r in rows]
print(f"step2: 取 {len(codes)} 只", flush=True)

t0 = time.time()
ph = ",".join(["%s"] * len(codes))
r1 = db.fetch(
    "SELECT code, report_date FROM stock_finance f "
    f"WHERE f.code IN ({ph}) AND f.report_date = ("
    "SELECT MAX(f2.report_date) FROM stock_finance f2 WHERE f2.code = f.code)",
    tuple(codes))
print(f"step2: 相关子查询方式 {len(r1)} 行, {time.time()-t0:.1f}s", flush=True)

t0 = time.time()
r2 = db.fetch(
    "SELECT code, report_date FROM stock_finance "
    f"WHERE code IN ({ph})", tuple(codes))
print(f"step2: 全量拉取方式   {len(r2)} 行, {time.time()-t0:.1f}s", flush=True)
