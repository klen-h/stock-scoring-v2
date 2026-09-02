# -*- coding: utf-8 -*-
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.database import db
from app.backtest.market_regime import (refresh_regime_cache, get_regime_description,
                                        load_regime_history)

before = db.fetch_one("SELECT date, state FROM market_regime_history "
                      "ORDER BY date DESC LIMIT 1")
print("刷新前缓存:", dict(before) if before else None)

c = refresh_regime_cache()
if not c:
    print("刷新失败：无状态数据")
else:
    print(f"\n★ 当前市场状态: {c['state']} ({get_regime_description(c['state'])})")
    print(f"  日期: {c['date']}")
    print(f"  权重: {c['weights']}")
    print(f"  明细: {c['detail']}")

# 显示最近 8 个交易日状态演变（看是否切换过）
states = load_regime_history()
print("\n近 10 日状态演变:")
for s in (states or [])[-10:]:
    print(f"  {s.date} {s.state:<10} score={s.regime_score:>7.1f} "
          f"adx={s.adx:<6} ma20={s.ma20:<9.2f} ma60={s.ma60:<9.2f} "
          f"trend={s.ma_trend:<5} vol={s.volatility_regime}")
