# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.strategies.recommendation import get_push_whitelist
from app.database import db
from app.flash import rules

t = rules.beijing_now().strftime("%Y-%m-%d")
print("白名单:", get_push_whitelist())
rows = db.fetch("SELECT strategy_name, count FROM strategy_results WHERE scan_date = %s ORDER BY count DESC", (t,))
print("今日已扫描战法:", [(r["strategy_name"], r["count"]) for r in rows or []])
