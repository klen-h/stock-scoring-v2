#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把线上后端数据包**就地改造成新表结构**（纯结构重写，不动一行数据）。

【为什么需要这个脚本】
2026-10-29：`klines` 加了 `WITHOUT ROWID`（实测 249.9MB → 184.0MB，-26.4%），
但当晚日批**没有生效** —— `update_backend_pack_incremental.py` 有一段：

    if not target or target_ymd <= prev_ymd:
        print("  已是最新，无需更新。")
        return 0

当天数据已经到位，于是直接早退，表结构原封不动。
⇒ **光改生成代码不够，还需要一次"数据没变、但结构要重写"的操作。**

而 `daily_batch` / 增量脚本都不能干这件事：
  · 增量脚本早退，进不到 `write_sqlite`；
  · 全量重建要逐只回源拉 K 线（3~6 小时 + WAF 风险），为了改个表结构不值得。

【本脚本做什么】
    下载线上包 → 解压 → 若还是旧结构就重建表 → VACUUM → 校验 → 重新压缩
**不重新拉数据、不重算指标、不碰 Supabase** ⇒ 只花"下载 + 写盘"的时间（Actions 上约 1 分钟）。

【为什么不直接改 write_sqlite 让它强制跑】
`write_sqlite` 需要完整的 `merged` / `ind_out`（2.1M 行 + 2884 份指标 JSON），
在内存里重组一遍纯属浪费 —— 数据一个字节都没变，SQLite 自己 `INSERT ... SELECT` 更安全也更快。

【校验（任何一条不过就 exit 1，不覆盖线上包）】
  行数 / SUM(close) / 数据末日 / 抽查单只逐行指纹 / PRAGMA integrity_check
"""
import argparse
import gzip
import hashlib
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.request

DEFAULT_URL = "https://klen-h.github.io/stock-scoring-v2/data/backend-pack.db.gz"
IND_NAME = "indicators-pack.json.gz"

# 与 generate_backend_pack.write_sqlite 完全一致（列顺序/类型/主键必须同源，否则读写层会错位）
KLINES_DDL = (
    "CREATE TABLE klines_new ("
    "code TEXT NOT NULL, date TEXT NOT NULL,"
    "open REAL, high REAL, low REAL, close REAL, volume REAL,"
    "PRIMARY KEY (code, date)) WITHOUT ROWID"
)


def _mb(p):
    return os.path.getsize(p) / 1048576


def _remote_size(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers.get("Content-Length") or 0)


def download(url, path, rounds=5):
    """下载并**校验字节数**。

    2026-10-29 实测教训：`urlopen` + `copyfileobj` 在链路抖动时会**静默截断**
    （拿到 62.11/62.53 MB 且不抛异常），直到 gzip 解压才报 "Compressed file ended
    before EOF"。⇒ 必须先用 HEAD 拿 Content-Length，落盘后比大小，不足就重下。
    """
    total = _remote_size(url)
    print(f"[1] 远端 {total / 1048576:.2f} MB")
    for i in range(rounds):
        try:
            with urllib.request.urlopen(url, timeout=300) as r, open(path, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
        except Exception as e:
            print(f"    第 {i + 1} 轮失败: {str(e)[:80]}")
        got = os.path.getsize(path) if os.path.exists(path) else 0
        if total and got == total:
            print(f"[1] 下载完成 {got / 1048576:.2f} MB")
            return
        print(f"    只拿到 {got / 1048576:.2f} MB，重下")
        time.sleep(3)
    raise SystemExit(f"下载始终不完整：{got}/{total} 字节")


def main():
    ap = argparse.ArgumentParser(description="后端包结构重写（WITHOUT ROWID）")
    ap.add_argument("--url", default=os.environ.get("PACK_URL") or DEFAULT_URL)
    ap.add_argument("--pack-dir", default="./data/kline")
    ap.add_argument("--dry-run", action="store_true",
                    help="只报告是否已是新结构，不改文件")
    args = ap.parse_args()

    pack_dir = os.path.abspath(args.pack_dir)
    os.makedirs(pack_dir, exist_ok=True)
    out_gz = os.path.join(pack_dir, "backend-pack.db.gz")
    tmp = tempfile.mkdtemp(prefix="packstruct_")
    src_gz = os.path.join(tmp, "in.db.gz")
    db = os.path.join(tmp, "work.db")

    print("=== 后端包结构重写（WITHOUT ROWID）===")
    download(args.url, src_gz)

    t0 = time.time()
    with gzip.open(src_gz, "rb") as fi, open(db, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    size_before = _mb(db)
    print(f"[2] 解压 {size_before:.1f} MB / {time.time() - t0:.1f}s")

    conn = sqlite3.connect(db)
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='klines'").fetchone()[0]
    if "WITHOUT ROWID" in sql.upper():
        print(">>> 已是 WITHOUT ROWID，无需重写。")
        conn.close()
        # 仍然同步一份到 pack_dir（workflow 的 publish 步骤需要文件存在）
        shutil.copyfile(src_gz, out_gz)
        _ensure_indicators(args.url, pack_dir)
        print(f"[out] {out_gz} ({_mb(out_gz):.2f} MB)")
        shutil.rmtree(tmp, ignore_errors=True)
        return 0

    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    n0 = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    s0 = conn.execute("SELECT SUM(close) FROM klines").fetchone()[0]
    mx0 = conn.execute("SELECT MAX(date) FROM klines").fetchone()[0]
    probe = conn.execute("SELECT code FROM klines GROUP BY code "
                         "ORDER BY COUNT(*) DESC LIMIT 1").fetchone()[0]
    rows0 = conn.execute("SELECT code,date,open,high,low,close,volume FROM klines "
                         "WHERE code=? ORDER BY date", (probe,)).fetchall()
    print(f"[3] 旧结构 {size_before:.1f} MB | pack_date={meta.get('pack_date')} "
          f"末日={mx0} | {n0} 行 | 抽查 {probe} {len(rows0)} 根")

    if args.dry_run:
        print(">>> --dry-run：需要重写，但未执行。")
        conn.close()
        shutil.rmtree(tmp, ignore_errors=True)
        return 0

    t0 = time.time()
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute(KLINES_DDL)
    conn.execute("INSERT INTO klines_new(code,date,open,high,low,close,volume) "
                 "SELECT code,date,open,high,low,close,volume FROM klines")
    conn.execute("DROP TABLE klines")
    conn.execute("ALTER TABLE klines_new RENAME TO klines")
    conn.commit()
    print(f"[4] 重建表 {time.time() - t0:.1f}s")

    t0 = time.time()
    conn.execute("VACUUM")
    print(f"[5] VACUUM {time.time() - t0:.1f}s")

    # ── 硬校验：任何一条不过就不覆盖线上包 ──────────────────
    n1 = conn.execute("SELECT COUNT(*) FROM klines").fetchone()[0]
    s1 = conn.execute("SELECT SUM(close) FROM klines").fetchone()[0]
    mx1 = conn.execute("SELECT MAX(date) FROM klines").fetchone()[0]
    rows1 = conn.execute("SELECT code,date,open,high,low,close,volume FROM klines "
                         "WHERE code=? ORDER BY date", (probe,)).fetchall()
    integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
    sql2 = conn.execute("SELECT sql FROM sqlite_master WHERE name='klines'").fetchone()[0]
    conn.close()
    size_after = _mb(db)

    checks = [
        ("行数", n0, n1),
        ("SUM(close)", round(s0 or 0.0, 6), round(s1 or 0.0, 6)),
        ("数据末日", mx0, mx1),
        ("抽查逐行指纹",
         hashlib.md5(repr(rows0).encode()).hexdigest()[:12],
         hashlib.md5(repr(rows1).encode()).hexdigest()[:12]),
        ("integrity_check", "ok", integ),
        ("表结构已含 WITHOUT ROWID", True, "WITHOUT ROWID" in sql2.upper()),
    ]
    bad = [(n, a, b) for n, a, b in checks if a != b]
    print(f"[6] 新结构 {size_after:.1f} MB")
    for n, a, b in checks:
        print(f"    {'[OK]' if a == b else '[!!]'} {n}: {a} vs {b}")
    if bad:
        print("::error::校验失败，拒绝覆盖线上包")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1

    t0 = time.time()
    with open(db, "rb") as fi, gzip.open(out_gz, "wb", compresslevel=9) as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    print(f"[7] 压缩 {_mb(out_gz):.2f} MB / {time.time() - t0:.1f}s")
    _ensure_indicators(args.url, pack_dir)

    print()
    print("=" * 58)
    print(f"  db {size_before:7.1f} -> {size_after:7.1f} MB  "
          f"({(size_after / size_before - 1) * 100:+.1f}%)")
    print(f"  gz {_mb(src_gz):7.2f} -> {_mb(out_gz):7.2f} MB  "
          f"({(_mb(out_gz) / _mb(src_gz) - 1) * 100:+.1f}%)")
    print("=" * 58)
    shutil.rmtree(tmp, ignore_errors=True)
    return 0


def _ensure_indicators(url, pack_dir):
    """publish 步骤需要 `indicators-pack.json.gz` 一起发布；结构重写不产出它，
    所以本地缺失时从线上下载上一份（内容不变，无需重算）。"""
    dst = os.path.join(pack_dir, IND_NAME)
    if os.path.exists(dst) and os.path.getsize(dst) > 1024:
        print(f"[8] 指标包已存在 {_mb(dst):.2f} MB")
        return
    ind_url = url.replace("backend-pack.db.gz", IND_NAME)
    try:
        with urllib.request.urlopen(ind_url, timeout=300) as r, open(dst, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        print(f"[8] 下载指标包 {_mb(dst):.2f} MB")
    except Exception as e:
        print(f"::warning::指标包下载失败（不影响结构重写）: {str(e)[:80]}")


if __name__ == "__main__":
    sys.exit(main())
