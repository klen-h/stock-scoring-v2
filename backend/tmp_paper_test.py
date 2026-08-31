# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

db.execute("DELETE FROM paper_positions")
db.execute("DELETE FROM paper_account")

from app.strategies.paper_trading import get_account, auto_ingest_signals, paper_stats

print("账户:", get_account())
r = auto_ingest_signals()
print("入池:", r)
rows = db.fetch("SELECT strategy_name, code, name, entry_price, stop_loss FROM paper_positions ORDER BY id ASC")
from collections import Counter
cnt = Counter(x["strategy_name"] for x in rows or [])
print("按战法分布:", dict(cnt))
for x in rows or []:
    flag = " ⚠️止损异常" if (x["stop_loss"] and x["entry_price"] and x["stop_loss"] >= x["entry_price"]) else ""
    print(f"  {x['strategy_name']:<22} {x['code']} {x['name']:<8} entry={x['entry_price']} stop={x['stop_loss']}{flag}")
print("统计:", paper_stats())
