# -*- coding: utf-8 -*-
"""模拟 CI：导入 main.py 的依赖链（重点验证 routers/backtest 与 news_sentiment）"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app.routers import backtest as backtest_router
print("[OK] app.routers.backtest 导入成功")

from app.news_sentiment import parse_news_time
print("[OK] app.news_sentiment 导入成功")

from app.scoring.ranking_history import get_verified_records, _current_prices
print("[OK] app.scoring.ranking_history 导入成功")

from app.scoring.kline_cache import get_cache_codes
print("[OK] app.scoring.kline_cache 导入成功")

from app.backtest import strategies, engine, data, run
print("[OK] app.backtest 全套导入成功")

from app.flash import scheduler
print("[OK] app.flash.scheduler 导入成功")

print("\n全部导入通过（无语法/名称错误）")
