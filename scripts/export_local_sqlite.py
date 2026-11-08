#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】把远程 Postgres（Supabase）导成本地 SQLite 副本 —— 本地开发零 egress
================================================================================
背景（2026-11-07，用户："导一份 SQLite 副本"）：
  `DATA_SOURCE=local` 只覆盖**行情/K线**（读数据包），而 `db` 仍连 Supabase ⇒
  改前端时每次刷新教练卡/评分榜/持仓都在走远程，egress 白流。
  本脚本把库整体搬一份到 `backend/data/app.db`，之后把 `DATABASE_URL` 留空
  （或注释掉）即走本地 SQLite，**读写全本地**。

用法：
  # 默认：小表全量 + 大表最近 90 天
  backend/.venv/Scripts/python.exe scripts/export_local_sqlite.py

  # 只导指定表（改前端只关心几张表时，省时间也省 egress）
  ... export_local_sqlite.py --tables coach_alerts,user_portfolio,stock_facts_wide

  # 大表保留天数
  ... export_local_sqlite.py --days 30

  # 预览（只列表 + 估计行数，不写）
  ... export_local_sqlite.py --dry-run

★ 为什么是"重建"而不是"增量"：
  这是**开发用副本**，不是备份。每次 drop + 重建能得到确定的状态；
  增量同步需要处理删除/更新，复杂度远超收益（真要增量就重跑一次）。

★ 大表策略（实测体积，2026-11-04）：
  backtest_prices 124.9MB / mainflow_history 78.6MB / kline_cache 41.7MB。
  前两张有 `date` 列 ⇒ 按天数截断；kline_cache 是"每只股票一行"
  （主键 code，无日期列）⇒ 只能全量，但它是本地看 K 线/详情页的必需品。
  最终体积一般 100~150MB，换后续零 egress，划算。

★ 口径：只导 `public` schema 的业务表；Supabase 平台表（auth/storage/
  extensions 等）一律跳过 —— 它们与本项目无关。
================================================================================
"""
import argparse
import os
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
DEFAULT_OUT = os.path.join(BACKEND, "data", "app.db")

# 需要按天数截断的大表：表名 -> 可比较的日期列
# ⚠️ `flash_news.time` 是 TEXT（'YYYY-MM-DD HH:MM:SS'），字符串比较对同格式成立。
_BIG_DATE_COL = {
    "backtest_prices": "date",
    "mainflow_history": "date",
    "flash_news": "time",
    "news_history": "snap_date",
}

# Postgres 类型 -> SQLite 类型（未命中一律 TEXT，宁可宽松也不要导不进去）
_TYPE_MAP = {
    "integer": "INTEGER", "bigint": "INTEGER", "smallint": "INTEGER",
    "smallserial": "INTEGER", "serial": "INTEGER", "bigserial": "INTEGER",
    "text": "TEXT", "character varying": "TEXT", "character": "TEXT",
    "citext": "TEXT", "uuid": "TEXT", "json": "TEXT", "jsonb": "TEXT",
    "numeric": "REAL", "double precision": "REAL", "real": "REAL",
    "boolean": "INTEGER",
    "date": "TEXT", "timestamp without time zone": "TEXT",
    "timestamp with time zone": "TEXT", "time without time zone": "TEXT",
    "bytea": "BLOB",
}

BATCH = 2000          # 每批读取行数（大表靠它控制内存）

# 单行特别大的表用小批量：一次取 2000 行会把几百 MB 压进客户端缓冲，
# 也让一次网络往返传太久（`indicator_cache` 就是这么卡死的）。
_SMALL_BATCH = {"indicator_cache", "kline_cache", "stock_facts_wide",
                "backtest_prices", "mainflow_history", "sector_daily"}
SMALL_BATCH = 200

# ★★ 默认**不导**的表（2026-11-07 实测：这两张是导出卡死的主要来源）：
#   `kline_cache` 单行 `kline_data` ≈ 30KB（2220 行 ≈ 66MB）、
#   `indicator_cache` 同理，且**单行可能特别大**（某只票的 K 线数组）。
#   而本项目 `DATA_SOURCE=local` 时 **K 线/指标读的是本地数据包**
#   （`app.pack_source`），前端详情页根本不查这两张表
#   ⇒ 本地开发导它们纯属浪费 egress + 增加卡死概率。
#   确实需要时用 `--include-big` 强制包含。
_SKIP_DEFAULT = {"kline_cache", "indicator_cache"}


def _load_env():
    """读 backend/.env（不覆盖已存在的环境变量，与项目其它脚本一致）。"""
    path = os.path.join(BACKEND, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _pg_connect(url: str):
    import psycopg2
    from psycopg2.extras import RealDictCursor
    conn = psycopg2.connect(
        url, connect_timeout=20, cursor_factory=RealDictCursor,
        keepalives=1, keepalives_idle=30,
        keepalives_interval=10, keepalives_count=5,
        # ★★ 服务端语句超时（2026-11-07 实测踩坑）：
        #   `indicator_cache` 这类大 JSON 表会**永久卡住** —— `fetchmany` 一直在等，
        #   CPU 0%、连接既不返回也不报错，只能 kill 进程（实测卡了 3 分钟无任何进展）。
        #   设 statement_timeout 让**服务端**主动报错 ⇒ 至少能记一笔并继续下一张表。
        options="-c statement_timeout=180000",
    )
    with conn.cursor() as cur:
        cur.execute("SET default_transaction_read_only = on")   # 只读，绝不写源库
    return conn


def _list_tables(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public' AND table_type='BASE TABLE'
            ORDER BY table_name
        """)
        return [r["table_name"] for r in cur.fetchall()]


def _columns(conn, table):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
        """, (table,))
        return cur.fetchall()


def _pk_cols(conn, table):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON tc.constraint_name = kcu.constraint_name
             AND tc.table_schema = kcu.table_schema
            WHERE tc.table_schema='public' AND tc.table_name=%s
              AND tc.constraint_type='PRIMARY KEY'
            ORDER BY kcu.ordinal_position
        """, (table,))
        return [r["column_name"] for r in cur.fetchall()]


def _sqlite_type(pg_type: str) -> str:
    t = (pg_type or "").lower()
    if t.startswith("_"):          # 数组类型在信息schema里以 _xxx 出现
        return "TEXT"
    return _TYPE_MAP.get(t, "TEXT")


def _create_table(dst, table, cols, pk):
    defs = [f'"{c["column_name"]}" {_sqlite_type(c["data_type"])}' for c in cols]
    if pk:
        defs.append("PRIMARY KEY (" + ", ".join(f'"{c}"' for c in pk) + ")")
    sql = f'CREATE TABLE "{table}" ({", ".join(defs)})'
    dst.execute(f'DROP TABLE IF EXISTS "{table}"')
    dst.execute(sql)


def _to_sqlite(v):
    """把 psycopg2 返回的值转成 SQLite 能接受的类型。"""
    if isinstance(v, (dict, list)):
        import json
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v)
    if isinstance(v, (int, float, str)) or v is None:
        return v
    return str(v)      # Decimal / datetime / UUID 等一律字符串化


def _where_clause(cols, table, days):
    """大表的日期过滤子句。

    ⚠️ 必须区分列类型：`date` 是 DATE、`flash_news.time` 是 TEXT。
       直接把 DATE 和文本比会隐式转换（可能报错、且必然丢索引）；
       统一 `::text` 又会把 DATE 列的索引废掉 ⇒ 按实际类型分支。
    """
    date_col = _BIG_DATE_COL.get(table)
    if not days or not date_col:
        return ""
    types = {c["column_name"]: (c["data_type"] or "").lower() for c in cols}
    t = types.get(date_col)
    if t is None:
        return ""
    expr = f"CURRENT_DATE - INTERVAL '{int(days)} days'"
    if "date" in t or "timestamp" in t:
        return f'WHERE "{date_col}" >= {expr}'
    # TEXT 列（'YYYY-MM-DD HH:MM:SS'）：用同格式日期串比较，前缀可比
    return f'WHERE "{date_col}" >= to_char({expr}, \'YYYY-MM-DD\')'


def _copy_table(conn, dst, table, days, dry_run):
    cols = _columns(conn, table)
    if not cols:
        return 0, 0
    names = [c["column_name"] for c in cols]
    pk = _pk_cols(conn, table)
    where = _where_clause(cols, table, days)

    with conn.cursor() as cur:
        cur.execute(f'SELECT COUNT(*) AS n FROM "{table}" {where}')
        total = int((cur.fetchone() or {}).get("n") or 0)

    if dry_run:
        return total, 0

    _create_table(dst, table, cols, pk)
    sel = ", ".join(f'"{n}"' for n in names)
    ins = (f'INSERT OR REPLACE INTO "{table}" ({sel}) VALUES ('
           + ", ".join("?" * len(names)) + ")")

    copied = 0
    t0 = time.time()
    batch = SMALL_BATCH if table in _SMALL_BATCH else BATCH
    with conn.cursor(name=f"c_{table}") as cur:      # server-side cursor：不整表进内存
        cur.itersize = batch
        cur.execute(f'SELECT {sel} FROM "{table}" {where}')
        while True:
            rows = cur.fetchmany(batch)
            if not rows:
                break
            dst.executemany(ins, [tuple(_to_sqlite(r[n]) for n in names) for r in rows])
            copied += len(rows)
            if total and copied % 20000 < batch:
                print(f"      {copied}/{total} ...", flush=True)
    dst.commit()
    # ★★ 必须结束源库事务（2026-11-07 实测踩坑）：
    #   Supabase 用 PgBouncer（6543 事务池化），**长时间 open transaction 会被
    #   服务端直接断连** —— 实测导到第 32 张表报
    #   `SSL connection has been closed unexpectedly`，之后 82 张表全废。
    #   只读事务 `commit()` 无副作用（不写数据），但能让池化器回收连接。
    try:
        conn.commit()
    except Exception:  # noqa: except_pass  只读事务的 commit 仅用于让池化器回收连接，失败不影响已导出数据
        pass
    return total, copied


def main():
    ap = argparse.ArgumentParser(description="远程 Postgres -> 本地 SQLite 副本")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"输出 SQLite 路径（默认 {DEFAULT_OUT}）")
    ap.add_argument("--tables", default="", help="只导这些表（逗号分隔）；默认全部")
    ap.add_argument("--days", type=int, default=90,
                    help="大表保留最近 N 天（默认 90；0 = 不限）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    ap.add_argument("--resume", action="store_true",
                    help="续传：不备份旧文件，跳过「目标库已有数据」的表（中断后重跑用）")
    ap.add_argument("--include-big", action="store_true",
                    help="包含 kline_cache / indicator_cache（默认跳过：本地读数据包，不查这两张表）")
    args = ap.parse_args()

    _load_env()
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url.startswith("postgresql://"):
        print("✗ backend/.env 里没有可用的 DATABASE_URL（postgresql://...）")
        print("  本脚本是『远程 -> 本地』，源必须是远程库。")
        return 1

    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)

    print(f"源  ：PostgreSQL（{url.split('@')[-1].split('/')[0]}）")
    print(f"目标：{out}" + ("   [dry-run]" if args.dry_run else ""))
    print(f"大表保留：{'不限' if not args.days else str(args.days) + ' 天'}\n")

    conn = _pg_connect(url)
    try:
        all_tables = _list_tables(conn)
        want = [t.strip() for t in args.tables.split(",") if t.strip()]
        tables = [t for t in all_tables if (not want or t in want)]
        if not args.include_big:
            # 只在"没被显式点名"时剔除：用户用 --tables kline_cache 明确要了就尊重
            tables = [t for t in tables if t not in _SKIP_DEFAULT or t in want]
        missing = [t for t in want if t not in all_tables]
        if missing:
            print(f"⚠ 源库没有这些表（已跳过）：{missing}\n")
        print(f"共 {len(tables)} 张表\n")

        if args.dry_run:
            grand = 0
            for t in tables:
                cols = _columns(conn, t)
                where = _where_clause(cols, t, args.days)
                with conn.cursor() as cur:
                    cur.execute(f'SELECT COUNT(*) AS n FROM "{t}" {where}')
                    n = int((cur.fetchone() or {}).get("n") or 0)
                grand += n
                print(f"  {t:34s} {n:>9,}")
            print(f"\n合计约 {grand:,} 行")
            return 0

        if args.resume and os.path.exists(out):
            print("续传模式：目标库已有数据的表会跳过\n")
        elif os.path.exists(out):
            bak = out + ".bak"
            os.replace(out, bak)
            print(f"旧副本已移到 {os.path.basename(bak)}\n")

        dst = sqlite3.connect(out)
        dst.execute("PRAGMA journal_mode=WAL")
        dst.execute("PRAGMA synchronous=OFF")     # 导入期提速；写完后正常使用不受影响

        def _has_data(tbl):
            try:
                return dst.execute(f'SELECT COUNT(*) FROM "{tbl}"').fetchone()[0] > 0
            except Exception:
                return False

        total_rows = 0
        failed = []
        skipped = []
        for i, t in enumerate(tables, 1):
            if args.resume and _has_data(t):
                skipped.append(t)
                continue
            print(f"[{i}/{len(tables)}] {t}", flush=True)
            try:
                tot, got = _copy_table(conn, dst, t, args.days, False)
                total_rows += got
                mark = "✓" if got == tot else f"⚠ {got}/{tot}"
                print(f"    {mark}  {got:,} 行")
            except Exception as e:
                dst.rollback()
                failed.append((t, str(e)[:100]))
                print(f"    ✗ 跳过：{str(e)[:100]}")
                # ★ 连接已废就重连 —— 否则后面每张表都会报 "connection already closed"，
                #   一次断连会把整轮导出毁掉（实测 114 张里只成功 31 张）。
                if conn.closed or "closed" in str(e).lower():
                    try:
                        conn.close()
                    except Exception:  # noqa: except_pass  连接已废，close 报错也无所谓；下面会重连
                        pass
                    try:
                        conn = _pg_connect(url)
                        print("    ↻ 已重连源库")
                    except Exception as e2:
                        print(f"    ↻ 重连失败：{str(e2)[:80]}")
        try:
            dst.execute("ANALYZE")
            dst.commit()
        except Exception:  # noqa: except_pass  ANALYZE 只影响查询计划，失败不影响数据完整性
            pass
        dst.close()
    finally:
        try:
            conn.close()
        except Exception:  # noqa: except_pass  收尾关连接，失败无补救动作且不影响产物
            pass

    size = os.path.getsize(out) / 1024 / 1024
    print(f"\n完成：{len(tables) - len(failed)}/{len(tables)} 张表，"
          f"{total_rows:,} 行，{size:.1f} MB → {out}")
    if skipped:
        print(f"续传跳过 {len(skipped)} 张（目标库已有数据）")
    if failed:
        print(f"⚠ 失败 {len(failed)} 张：")
        for t, e in failed:
            print(f"   {t}: {e}")

    print("""
下一步（切到本地库）：
  1) 备份并注释掉 backend/.env 第 51 行的 DATABASE_URL
       # DATABASE_URL=postgresql://...
  2) 重启后端进程（database.py 在 import 时读环境变量）
  3) 控制台应出现： [database] 使用 SQLite 数据库: .../data/app.db
  ⚠️ 本地库是**某个时点的快照**，不会自动更新。要看最新数据就重跑本脚本。
  ⚠️ 本地是只读展示用；写操作（教练落库/模拟盘）会写进副本，不影响线上库。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
