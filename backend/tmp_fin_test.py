# -*- coding: utf-8 -*-
"""验证财务批量查询优化：1550 只耗时 / 缓存命中 / 只取最新一期。"""
import sys, os, time
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app
from app.database import db
from app.finance import get_finance_batch, clear_finance_cache, _FIN_CACHE_TTL

rows = db.fetch("SELECT DISTINCT code FROM stock_finance LIMIT 1550")
codes = [r["code"] for r in rows]
total = db.fetch_one("SELECT COUNT(DISTINCT code) AS n FROM stock_finance")["n"]
print(f"模拟评分池: {len(codes)} 只（对应剔除创业板/科创板/ST 后的规模）")
print(f"全表股票数: {total}")

clear_finance_cache()
t0 = time.time()
r1 = get_finance_batch(codes)
c1 = time.time() - t0
print(f"\n首次查询（分批 {len(codes) // 500 + 1} 条 SQL）: {len(r1)} 条, 耗时 {c1:.2f}s")

t0 = time.time()
r2 = get_finance_batch(codes)
c2 = time.time() - t0
print(f"缓存命中（第二次）: {len(r2)} 条, 耗时 {c2:.4f}s  -> 提速 {c1 / max(c2, 1e-6):.0f} 倍")

sample = codes[0]
allp = db.fetch("SELECT report_date FROM stock_finance WHERE code = %s "
                "ORDER BY report_date DESC", (sample,))
print(f"\n验证只取最新一期（{sample}）:")
print("  库中该股所有报告期:", [r["report_date"] for r in allp])
print("  get_finance_batch 返回:", r1[sample]["report_date"],
      "OK" if r1[sample]["report_date"] == allp[0]["report_date"] else "MISMATCH")

extra = db.fetch("SELECT DISTINCT code FROM stock_finance LIMIT 3 OFFSET 5000")
mixed = [r["code"] for r in extra] + codes[:10]
t0 = time.time()
r3 = get_finance_batch(mixed)
print(f"\n混入 3 个缓存外代码: 返回 {len(r3)} 条, 耗时 {time.time() - t0:.2f}s")

ok = sum(1 for c in codes[:200] if c in r1 and r1[c].get("report_date"))
print(f"\n前 200 只中有财报数据: {ok}/200")
print(f"缓存 TTL: {_FIN_CACHE_TTL}s（{_FIN_CACHE_TTL // 60} 分钟）")
