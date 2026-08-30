# -*- coding: utf-8 -*-
"""验证：模拟服务重启（清空内存行情缓存）后能否算出收益"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app.scoring.ranking_history import get_verified_records, get_daily_rankings

# 模拟 Render 重启：清空内存行情缓存
import app.tencent as t
t._cache["stocks"] = {}
print("已清空内存行情缓存（模拟服务重启）\n")

try:
    recs = get_verified_records(min_age_days=2)
    print(f"已验证记录: {len(recs)} 条（权重优化需要 >= 20）")
    for r in recs[:5]:
        print(f"  {r['code']} {r['name']} {r['date']} 收益={r['returnPct']}% 维度={r['dimensions']}")
    dates = sorted({r["date"] for r in recs}, reverse=True)
    print(f"  覆盖日期: {dates[:6]}")
except Exception as e:
    import traceback; traceback.print_exc()

print("\n=== 每日快照（胜率回查）===")
try:
    snaps = get_daily_rankings(days=7)
    for s in snaps[:4]:
        print(f"  {s['date']}: {len(s['stocks'])}只 已验证={s['verified']} "
              f"胜率={s['winRate']}% 平均={s['avgReturn']}%")
except Exception as e:
    import traceback; traceback.print_exc()
