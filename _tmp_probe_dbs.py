# -*- coding: utf-8 -*-
"""盘点工作区所有 SQLite 库（零 egress）：找"全市场历史日线"以解决龙虎榜覆盖率缺口。"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
SKIP = ("node_modules", ".git", "dist", "__pycache__", ".venv")
dbs = []
for dp, dns, fns in os.walk(ROOT):
    dns[:] = [d for d in dns if d not in SKIP]
    for f in fns:
        if f.endswith((".db", ".sqlite")):
            p = os.path.join(dp, f)
            try:
                mb = os.path.getsize(p) / 1048576.0
            except OSError:
                continue
            dbs.append((mb, p))
dbs.sort(reverse=True)
print("=== 发现的 SQLite 库（按大小）===")
for mb, p in dbs:
    print("  %8.1f MB  %s" % (mb, os.path.relpath(p, ROOT)))

for mb, p in dbs:
    if mb < 1:                      # 小库跳过（与本题无关）
        continue
    print("\n=== %s (%.1f MB) ===" % (os.path.relpath(p, ROOT), mb))
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
        tabs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        for t in tabs:
            try:
                n = conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0]
            except Exception as e:
                print("  %-22s ERR %s" % (t, str(e)[:40]))
                continue
            cols = [r[1] for r in conn.execute("PRAGMA table_info(%s)" % t)]
            info = ""
            dcol = next((c for c in ("date", "trade_date", "day") if c in cols), None)
            if dcol:
                r = conn.execute("SELECT MIN(%s), MAX(%s) FROM %s" % (dcol, dcol, t)).fetchone()
                info += "  %s..%s" % (r[0], r[1])
            if "code" in cols:
                r = conn.execute("SELECT COUNT(DISTINCT code) FROM %s" % t).fetchone()
                info += "  codes=%d" % r[0]
            print("  %-22s rows=%-10d%s" % (t, n, info))
            if "code" in cols and dcol and n > 100000:
                print("      ★ 候选全市场日线库：cols=%s" % ",".join(cols[:14]))
        conn.close()
    except Exception as e:
        print("  ERR %s" % str(e)[:100])
