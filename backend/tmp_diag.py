# -*- coding: utf-8 -*-
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from app.tencent import get_kline, _fetch_tencent
from app.tencent import _waf_blocked_until
import app.tencent as tx

print("WAF冷却中:", time.time() < tx._waf_blocked_until)
print("内存K线缓存条目:", len(tx.KLINE_CACHE))
print("缓存样例key:", list(tx.KLINE_CACHE.keys())[:3])

# 1. K线新鲜度（平安银行）
k = get_kline("000001", period="day", count=8)
print("\n平安银行 最后3根K线:", [(x["date"], x["close"]) for x in k[-3:]] if k else "空")

# 2. 两只新ETF行情
for code in ("sz159825", "sz159865", "sz159985"):
    try:
        d = _fetch_tencent(code)
        for v in d.values():
            print(f"{code}: name={v.get('name')} price={v.get('price')}")
    except Exception as e:
        print(f"{code} 获取异常: {e}")
