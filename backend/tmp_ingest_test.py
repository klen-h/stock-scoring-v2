# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from collections import Counter
from app.database import db
from app.strategies import paper_trading as pt

# 本次验证新入的单阳不破信号（清理掉，保留用户原有的 pending）
TEST_CODES = ("603713", "002612", "601975", "600346", "603666")
for c in TEST_CODES:
    db.execute("DELETE FROM paper_positions WHERE code=%s AND status='pending'", (c,))

print("=== 清理后 pending 分布 ===")
rows = db.fetch("SELECT strategy_name, code, name FROM paper_positions WHERE status='pending' ORDER BY strategy_name")
for r in rows or []:
    print(f"  {r['strategy_name']:<26} {r['code']} {r['name']}")
print("  ", dict(Counter(r["strategy_name"] for r in rows or [])))

print("\n=== 各战法额度占用（pending + holding）/ 上限 8 ===")
allrows = db.fetch("SELECT strategy_name, status FROM paper_positions "
                   "WHERE status IN ('pending','holding')")
cnt = Counter(r["strategy_name"] for r in allrows or [])
for name, c in cnt.items():
    print(f"  {name:<26} {c}/{pt.MAX_PER_STRATEGY_POSITIONS} "
          f"{'（已满，明日新信号不再入池）' if c >= pt.MAX_PER_STRATEGY_POSITIONS else ''}")

print("\n=== 再跑一次入池（验证上限检查生效，不应新增）===")
print("  结果:", pt.auto_ingest_signals())
