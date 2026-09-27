#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
可行性评估：北向资金数据在 2024-08-19 披露规则调整后是否还能用？
+ 项目自有替代（mainflow_history 主力资金）的覆盖范围。
"""
import os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend'))
os.environ.setdefault('RENDER_READ_ONLY', '1')

print("=" * 100)
print("【1】实测项目 get_northbound()：2024-08-19 后是否还能拿到数据？")
print("=" * 100)
try:
    from app.eastmoney import get_northbound
    nb = get_northbound()
    if not nb:
        print("  返回空 {} ⇒ 接口不可用")
    else:
        print(f"  time      = {nb.get('time')}")
        print(f"  sh_net    = {nb.get('sh_net'):,.0f} 元")
        print(f"  sz_net    = {nb.get('sz_net'):,.0f} 元")
        print(f"  total_net = {nb.get('total_net'):,.0f} 元")
        ser = nb.get('series') or []
        print(f"  series 点数 = {len(ser)}")
        if ser:
            print(f"  首个 = {ser[0]}")
            print(f"  末个 = {ser[-1]}")
            nz = [x for x in ser if abs(x.get('total_net') or 0) > 0]
            print(f"  非零点数 = {len(nz)}  ⇒ 若为 0 说明『已不披露净流入』")
except Exception as e:
    print(f"  异常: {e}")

print("\n" + "=" * 100)
print("【2】项目自有替代：mainflow_history（个股主力资金）覆盖范围")
print("=" * 100)
try:
    from app.database import db
    cols = db.fetch("""SELECT column_name, data_type FROM information_schema.columns
                       WHERE table_name='mainflow_history' ORDER BY ordinal_position""")
    print("  字段:", [c['column_name'] for c in cols])
    r = db.fetch_one("""SELECT COUNT(*) AS n, COUNT(DISTINCT date) AS days,
                               MIN(date) AS d0, MAX(date) AS d1,
                               COUNT(DISTINCT code) AS codes
                        FROM mainflow_history""")
    print(f"  行数={r['n']:,}  交易日={r['days']}  区间={r['d0']} ~ {r['d1']}  股票数={r['codes']:,}")
    # 样本
    s = db.fetch("SELECT * FROM mainflow_history ORDER BY date DESC LIMIT 2")
    print("  样本:")
    for x in s:
        print("   ", x)
except Exception as e:
    print(f"  异常: {e}")

print("\n" + "=" * 100)
print("【3】indices 相关表（是否已有指数级资金流落库）")
print("=" * 100)
try:
    from app.database import db
    t = db.fetch("""SELECT table_name FROM information_schema.tables
                    WHERE table_name LIKE '%flow%' OR table_name LIKE '%north%'
                       OR table_name LIKE '%capital%' OR table_name LIKE '%money%'
                    ORDER BY table_name""")
    print("  相关表:", [x['table_name'] for x in t])
except Exception as e:
    print(f"  异常: {e}")
