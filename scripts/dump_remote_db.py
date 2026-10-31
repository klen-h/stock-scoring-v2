# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】远程数据库全量导出（Supabase 402 应急备份 / 迁移前置）
================================================================================
背景（2026-10-31）：Supabase 返回 **402 Egress Exceeded**，PostgREST API 被限制，
但**直连 6543 池化仍可用**（实测 `SELECT 1` 成功、111 张表可读）⇒ 现在是备份窗口。

为什么不用 `pg_dump`：
  本机**没有** pg_dump / psql / docker（2026-10-31 实测 `where` 全为空），
  而 `deploy/migrate_supabase.sh` 依赖 docker 容器跑 pg_dump。
  ⇒ 改用 psycopg2 的 `COPY (SELECT * FROM t) TO STDOUT WITH CSV HEADER`：
     · **流式**写盘，大表（backtest_prices 124MB）也不进内存；
     · 不依赖服务端游标 ⇒ **兼容 6543 事务池**（named cursor 在池化下不可靠）。

输出结构：
    db_dump_YYYYMMDD_HHMMSS/
        manifest.json          表清单 + 行数 + 字节数 + 列名 + 耗时
        _tables.txt            纯表名（按导出顺序，恢复时照此 COPY FROM）
        <table>.csv.gz         数据（CSV + HEADER，UTF-8，gzip）

**不导出 DDL**：恢复时靠 `backend/schema.sql` + 各模块 `ensure_table()` 建表
（111 张表里大部分是模块自建，DDL 散落在代码里，硬拼 information_schema 反而易错）。
manifest 里保留了每张表的列名，恢复脚本可按它校验列序。

用法：
    python scripts/dump_remote_db.py                # 全量
    python scripts/dump_remote_db.py --only big     # 只导 >10MB 的大表
    python scripts/dump_remote_db.py --skip-existing
    python scripts/dump_remote_db.py --tables user_watchlist,daily_reports
================================================================================
"""
import argparse
import gzip
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

_ROOTS = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_ROOTS), "backend"))

# 加载 backend/.env（已存在的环境变量不覆盖）
_ENV = os.path.join(os.path.dirname(_ROOTS), "backend", ".env")
if os.path.exists(_ENV):
    with io.open(_ENV, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ★ Windows 控制台默认 GBK，`✓/✗/⚠️/⇒` 会抛 UnicodeEncodeError
#   （2026-10-31 实测：dump_schema 文件已写成功，却因最后一行 print 异常退出）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
if not DATABASE_URL.startswith("postgresql://"):
    print("FAIL DATABASE_URL 不是 PostgreSQL 连接串，退出")
    sys.exit(1)


def _ts() -> str:
    # ★ 显式带时区（卫生门会拦无时区 now）
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        u = urlparse(url)
        return f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}"
    except Exception:
        return "(unknown)"


def _has_column(cur, table: str, col: str) -> bool:
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s AND column_name=%s
    """, (table, col))
    return cur.fetchone() is not None


def _copy_batched(cur, table: str, path: str, batch: int) -> int:
    """按 `id` keyset 分批 COPY，**每批独立 gzip member 并立即 close**。

    ★ 2026-10-31 实测两个坑（同一条 SQL 单批只要 13~23 秒）：
      ① 单条大 `COPY (SELECT * FROM mainflow_history)` 在 402 限流下
         **无限等待**：文件卡 0 字节 20 分钟、进程活着、`pg_stat_activity`
         查不到会话、也不报错 ⇒ 必须分批；
      ② **一次 `gzip.open('wb')` 连续写多批会卡死**（`copy_expert` 第二次调用
         再也不返回）。拆成"每批 `open('ab')` + close"后稳定。
         `close` 同时保证数据真正落盘——否则块缓冲会让文件长时间显示 0 字节。
    ★ 多个 gzip member 拼接是合法格式，`gzip.open` 读取时自动串联。
    ★ `id` 需唯一且递增（无主键也适用，SERIAL 即可）。
    """
    last = None
    n = 0
    while True:
        prev = last
        where = "" if last is None else f'WHERE "id" > {int(last)} '
        raw = gzip.open(path, "wb" if n == 0 else "ab", compresslevel=6)
        try:
            cur.copy_expert(
                f'COPY (SELECT * FROM public."{table}" {where}'
                f'ORDER BY "id" LIMIT {batch}) TO STDOUT '
                f'WITH (FORMAT CSV, HEADER {"true" if n == 0 else "false"})', raw)
        finally:
            raw.close()          # ★ 每批 close：flush + 结束本 gzip member
        cur.execute(
            f'SELECT MAX("id") FROM (SELECT "id" FROM public."{table}" {where}'
            f'ORDER BY "id" LIMIT {batch}) s')
        new_last = cur.fetchone()[0]
        if new_last is None:                 # 本批空 ⇒ 导完
            break
        if prev is not None and new_last == prev:
            print(f"      WARN id 未前进({new_last})，终止避免死循环", flush=True)
            break
        last = new_last
        n += 1
        print(f"      batch {n} 完成 (id<={last}, "
              f"{os.path.getsize(path) / 1048576:.2f} MB)", flush=True)
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="远程 PostgreSQL 全量导出（CSV.gz）")
    ap.add_argument("--out", default=None, help="输出目录（默认 db_dump_<时间戳>）")
    ap.add_argument("--only", choices=["all", "big"], default="all",
                    help="all=全部；big=只导 >10MB 的表")
    ap.add_argument("--skip-existing", action="store_true",
                    help="已存在的 csv.gz 跳过（断点续传）")
    ap.add_argument("--tables", default=None, help="逗号分隔的表名（覆盖 --only）")
    ap.add_argument("--exclude", default=None,
                    help="逗号分隔的排除表名（如可从数据包重建的 backtest_prices/kline_cache）")
    ap.add_argument("--batch", type=int, default=20000,
                    help="大表分批行数（0=不分批）。★ 402 限流下单条 COPY 会无限等待，"
                         "分批能让单批失败不拖死整表")
    args = ap.parse_args(argv)

    import psycopg2

    root = os.path.dirname(_ROOTS)
    out = args.out or os.path.join(root, f"db_dump_{_ts()}")
    os.makedirs(out, exist_ok=True)

    print(f"源: {_host_of(DATABASE_URL)}")
    print(f"输出: {out}\n")

    conn = psycopg2.connect(DATABASE_URL, connect_timeout=20)
    conn.autocommit = True      # COPY 不需要事务；避免长事务占用池化连接
    cur = conn.cursor()
    # ★ 2026-10-31：402 限流下单条 COPY 会**无限等待**（既不返回也不报错），
    #   设语句超时让"卡住"变成"可重试的失败"，而不是静默挂死。
    try:
        cur.execute("SET statement_timeout = '300s'")
    except Exception:
        pass

    # ── 表清单 + 体积（小表先导，尽快拿到核心数据）────────────────────
    cur.execute("""
        SELECT c.relname, pg_total_relation_size(c.oid) AS bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY bytes ASC
    """)
    all_tables = [(r[0], int(r[1] or 0)) for r in cur.fetchall()]

    if args.tables:
        want = {x.strip() for x in args.tables.split(",") if x.strip()}
        tables = [(t, b) for t, b in all_tables if t in want]
        missing = want - {t for t, _ in tables}
        if missing:
            print(f"⚠️ 不存在的表: {', '.join(sorted(missing))}")
    elif args.only == "big":
        tables = [(t, b) for t, b in all_tables if b > 10 * 1024 * 1024]
    else:
        tables = all_tables

    # ★ 排除可重建的大表（402 下限速 ~32KB/s，省 200MB 能省一小时以上）：
    #   backtest_prices / kline_cache / indicator_cache 都能从 backend-pack 重建；
    #   ranking_live / mainforce_state 可由日批重算。**不可重建的历史必须导**。
    if args.exclude:
        ex = {x.strip() for x in args.exclude.split(",") if x.strip()}
        dropped = [t for t, _ in tables if t in ex]
        tables = [(t, b) for t, b in tables if t not in ex]
        if dropped:
            print(f"已排除 {len(dropped)} 张表: {', '.join(dropped)}")

    total_bytes = sum(b for _, b in tables)
    print(f"共 {len(tables)} 张表，源端约 {total_bytes / 1048576:.1f} MB\n")

    manifest = {
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "source_host": _host_of(DATABASE_URL),
        "tables_total": len(all_tables),
        "tables_exported": 0,
        "tables": [],
    }
    ok_names = []
    t_all = time.time()

    for i, (t, nbytes) in enumerate(tables, 1):
        path = os.path.join(out, f"{t}.csv.gz")
        if args.skip_existing and os.path.exists(path) and os.path.getsize(path) > 0:
            print(f"[{i}/{len(tables)}] {t}: 已存在，跳过")
            ok_names.append(t)
            manifest["tables"].append({"name": t, "skipped": True})
            continue

        t0 = time.time()
        try:
            # 行数（独立查询，COPY 不返回行数）
            cur.execute(f'SELECT COUNT(*) FROM public."{t}"')
            rows = int(cur.fetchone()[0] or 0)

            # 列名（manifest 里留着，恢复时校验列序）
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_schema='public' AND table_name=%s
                ORDER BY ordinal_position
            """, (t,))
            cols = [r[0] for r in cur.fetchall()]

            # ★ COPY TO STDOUT 流式写 gzip：大表也不进内存
            # ★ 大表分批（402 限流下单条 COPY 会无限等待；且不能一次 open 写多批）
            if (args.batch > 0 and rows > args.batch
                    and _has_column(cur, t, "id")):
                _copy_batched(cur, t, path, args.batch)
            else:
                raw = gzip.open(path, "wb", compresslevel=6)
                try:
                    cur.copy_expert(
                        f'COPY (SELECT * FROM public."{t}") TO STDOUT '
                        f'WITH (FORMAT CSV, HEADER true)', raw)
                finally:
                    raw.close()

            size = os.path.getsize(path)
            dt = time.time() - t0
            ok_names.append(t)
            manifest["tables"].append({
                "name": t, "rows": rows, "bytes_src": nbytes,
                "bytes_gz": size, "columns": cols, "seconds": round(dt, 1),
            })
            print(f"[{i}/{len(tables)}] {t}: {rows} 行 "
                  f"→ {size / 1048576:.2f} MB / {dt:.1f}s")
        except Exception as e:
            print(f"[{i}/{len(tables)}] ✗ {t}: {type(e).__name__} {str(e)[:120]}")
            manifest["tables"].append({"name": t, "error": str(e)[:200]})
            # 失败的表删掉半成品，避免误以为是完整备份
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError:
                pass
            try:
                conn.rollback()
            except Exception:
                pass

    manifest["tables_exported"] = len(ok_names)
    manifest["seconds_total"] = round(time.time() - t_all, 1)

    with io.open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    with io.open(os.path.join(out, "_tables.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(ok_names) + "\n")

    failed = [x["name"] for x in manifest["tables"] if x.get("error")]
    print(f"\n完成：成功 {len(ok_names)} / 失败 {len(failed)}"
          f" / 总耗时 {manifest['seconds_total']}s")
    if failed:
        print("失败表：" + ", ".join(failed))
    print(f"目录：{out}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
