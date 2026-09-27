#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探查：市值数据在哪（pack codes.market_cap / tencent float_cap / flow 流通股本）。"""
import os, sqlite3, sys, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 1. pack 本地包
for p in [os.path.join(ROOT, "backend", "data", "pack", "backend-pack.db"),
          os.path.join(ROOT, "data", "pack", "backend-pack.db")]:
    if os.path.exists(p):
        print(f"pack 存在: {p}  ({os.path.getsize(p)/1048576:.1f} MB)")
        c = sqlite3.connect(p)
        tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        print("  表:", tabs)
        if "codes" in tabs:
            cols = [r[1] for r in c.execute("PRAGMA table_info(codes)")]
            print("  codes 字段:", cols)
            n = c.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
            print("  codes 行数:", n)
            s = c.execute("SELECT * FROM codes LIMIT 3").fetchall()
            print("  样本:", s)
        c.close()
        break
else:
    print("pack 本地包不存在")

# 2. tencent stocks 缓存字段（离线探查文件缓存，若有）
print("\n=== 市值字段来源（grep 定位，不 import）===")
for f in ["backend/app/tencent.py", "backend/app/mainforce/flow.py"]:
    fp = os.path.join(ROOT, f)
    if os.path.exists(fp):
        hits = []
        for i, line in enumerate(open(fp, encoding="utf-8"), 1):
            if any(k in line for k in ("market_cap", "float_cap", "流通", "总市值", "marketcap", "liutong")):
                hits.append(f"{i}: {line.strip()[:100]}")
        print(f"\n{f} 命中 {len(hits)} 处:")
        for h in hits[:15]:
            print("  " + h)
