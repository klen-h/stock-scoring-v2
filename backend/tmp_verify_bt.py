# -*- coding: utf-8 -*-
"""验证：模拟重启后历史回测能否跑通（DB 缓存兜底）"""
import sys, os, time, asyncio
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 模拟服务重启：清空内存行情缓存
import app.tencent as t
t._cache["stocks"] = {}
print("已清空内存行情缓存（模拟服务重启）")

from app.scoring.kline_cache import get_cache_codes
codes = get_cache_codes(100)
print(f"DB 兜底回测池: {len(codes)} 只")

from app.routers.scoring import backtest

t0 = time.time()
r = asyncio.run(backtest(top_n=10, days=30, hold_periods="1,3,5,10"))
print(f"\n耗时: {time.time()-t0:.1f}s")
if r.get("error"):
    print("返回错误:", r["error"])
else:
    print(f"回测天数={r.get('backtest_days')} 股票池={r.get('stock_pool_size')}")
    for p, s in (r.get("summary") or {}).items():
        print(f"  持有 {p} 天: 胜率={s['win_rate']}% 平均={s['avg_return']}% 样本={s['total']}")
