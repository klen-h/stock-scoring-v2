# -*- coding: utf-8 -*-
"""pack 扩容（纳入科创板）的真实成本测算（临时，跑完即删）"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "backend"))

PACK = os.path.join(ROOT, "backend", "data", "pack", "backend-pack.db")
PACK_GZ = PACK + ".gz"

print("=== 1) 现有包体积 ===")
for p in (PACK, PACK_GZ):
    if os.path.exists(p):
        print("  %s: %.1f MB" % (os.path.basename(p), os.path.getsize(p) / 1048576))
    else:
        print("  %s: 不存在（本地未下载包）" % os.path.basename(p))

if os.path.exists(PACK):
    conn = sqlite3.connect(PACK)
    cur = conn.cursor()
    n_codes = cur.execute("SELECT COUNT(*) FROM codes").fetchone()[0]
    n_krows = cur.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    k_bytes = cur.execute("SELECT SUM(length(code)+length(date))*0 "
                          "FROM klines").fetchone()  # 占位
    # 用页数近似：整库大小已知，按行数折算每只成本
    n_ind = cur.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]
    ind_bytes = cur.execute("SELECT SUM(LENGTH(json)) FROM indicators").fetchone()[0] or 0
    print("\n=== 2) 包内构成 ===")
    print("  codes=%d ｜ klines 行=%d（均 %.0f 行/只）｜ indicators=%d（json 合计 %.1f MB，均 %.1f KB/只）"
          % (n_codes, n_krows, n_krows / max(n_codes, 1),
             n_ind, ind_bytes / 1048576, ind_bytes / 1024 / max(n_ind, 1)))
    # 688 在不在包里（应≈0）
    n688 = cur.execute("SELECT COUNT(*) FROM codes WHERE code LIKE '688%'").fetchone()[0]
    print("  包内 688 只数: %d（预期 0 = 未覆盖）" % n688)
    conn.close()

print("\n=== 3) 科创板纳入规模（market_snapshot: 市值≥50亿）===")
try:
    from app.database import db
    row = db.fetch_one("SELECT COUNT(*) AS n FROM market_snapshot "
                       "WHERE code LIKE '688%' AND (market_cap/10000.0) >= 50")
    total = db.fetch_one("SELECT COUNT(*) AS n FROM market_snapshot WHERE code LIKE '688%%'")
    print("  快照中 688 共 %s 只，其中市值≥50亿 = %s 只（拟纳入规模）" % (total["n"], row["n"]))
except Exception as e:
    print("  market_snapshot 查询失败: %s" % e)
