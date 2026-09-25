"""查涨停清单为何出现**重复行**（用户：金辰股份重复 6 次、上工申贝 2 次）。

假设（待验）：
  A. `uplimit_stocks` 返回的是"**多日**涨停股"（每天一行）⇒ 连板股自然重复；
  B. 或同一只有多条记录（不同题材/不同 id）；
  C. 或落库/读取把多日 payload 拼在一起。
先看真实分布再决定修法（后端过滤 or 前端去重）。
"""
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from app.zzshare_daily import load_range  # noqa: E402

rows = load_range("2026-09-20", "2026-09-25") or []
last = rows[-1]
us = last.get("uplimit_stocks") or []
print(f"快照日期 = {last.get('date')}｜uplimit_stocks 行数 = {len(us)}")

codes = [r.get("stock_code") for r in us]
cc = Counter(codes)
print(f"去重后代码数 = {len(cc)}｜重复代码 = {[k for k, v in cc.items() if v > 1][:10]}")

dates = Counter(str(r.get("date1"))[:10] for r in us)
print("date1 分布 =", dict(dates))

print("\n全部行（date1 / code / name / desc / time / id）：")
for r in us:
    print(f"  {str(r.get('date1'))[:10]}  {r.get('stock_code')}  {r.get('stock_name')}"
          f"  {r.get('up_limit_desc')}  {r.get('up_limit_time')}  id={r.get('id')}"
          f"  keep={r.get('up_limit_keep_times')}")

# 复现接口输出，看前端实际拿到的 8 行
print("\n===== 接口 stocks[:8] =====")
try:
    from app.routers.market import market_limit_review  # noqa: E402

    out = market_limit_review(None)
    print(f"source={out.get('source')} as_of={out.get('as_of')} stale={out.get('stale')}")
    for r in (out.get("stocks") or [])[:8]:
        print(f"  {r.get('stock_code')} {r.get('stock_name')} {r.get('up_limit_desc')} "
              f"{r.get('up_limit_time')} {r.get('amount')}亿")
except Exception as e:
    print(f"  调用失败: {type(e).__name__}: {e}")
