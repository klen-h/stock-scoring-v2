# -*- coding: utf-8 -*-
import io, sys
from collections import Counter
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db

bench = db.fetch_one("SELECT MAX(date) AS d FROM backtest_prices "
                     "WHERE code='sh000300'")["d"]
print(f"基准（沪深300）最新日期: {bench}")

rows = db.fetch("SELECT code, MAX(date) AS d FROM backtest_prices GROUP BY code")
total = len(rows)
lag = [r for r in rows if r["d"] < bench]
print(f"backtest_prices 共 {total} 只，已同步到基准 {total - len(lag)} 只，"
      f"滞后 {len(lag)} 只")
print("滞后日期分布（Top10）:")
for d, c in Counter(r["d"] for r in lag).most_common(10):
    print(f"  {d}: {c} 只")

# ETF 池单独看（宏观回测/信号跟踪底座）
from app.signals.tracker import HOLDINGS_MAP
etf_codes = set(HOLDINGS_MAP.values())
etf_lag = [r for r in lag if r["code"] in etf_codes]
print(f"\nETF 池 {len(etf_codes)} 只，滞后 {len(etf_lag)} 只")
if etf_lag:
    for r in etf_lag[:15]:
        print(f"  {r['code']} 停于 {r['d']}")
