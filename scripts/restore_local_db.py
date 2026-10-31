# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】把 `db_dump_*` 目录恢复到目标数据库
================================================================================
配合 `scripts/dump_remote_db.py`（导数据）+ `scripts/dump_schema.py`（导类型）。

支持两种目标：
    --target-url postgresql://user:pass@host:5432/db   PostgreSQL（COPY，快、类型准）
    --sqlite backend/data/app.db                       SQLite（逐行 insert，无需装 PG）

为什么类型要从 `_schema.json` 读：
  数据 CSV 里没有类型。若全建 TEXT，`ORDER BY score DESC` 会按**字符串**排序
  （"9" > "10"）⇒ 榜单顺序全错；`SUM()`/`AVG()` 直接失效。
  缺 `_schema.json` 时脚本会**降级为全 TEXT 并显式告警**，不会静默成功。

设计取舍：
  · **不建 DEFAULT**：`nextval(...)` 依赖源库序列，恢复必然失败；数据本身已有值，
    默认值由项目各模块的 `ensure_table()` 在后运行时补齐。
  · **不建外键**：项目基本不用 FK（靠代码保证），省掉拓扑排序。
  · **索引最后建**：先灌数据再建索引，比边灌边维护快一个数量级。

用法：
    python scripts/restore_local_db.py --dump-dir db_dump_20261031 \
        --target-url postgresql://stockapp:pass@127.0.0.1:5432/stockapp
    python scripts/restore_local_db.py --dump-dir db_dump_20261031 \
        --sqlite backend/data/app.db
================================================================================
"""
import argparse
import csv
import gzip
import io
import json
import os
import sqlite3
import sys

try:                      # Windows 控制台 GBK 会拦特殊符号
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


# ══════════════════════════════════════════════════════════════════════════
# 类型映射
# ══════════════════════════════════════════════════════════════════════════
_PG_MAP = {
    "int4": "integer", "int8": "bigint", "int2": "smallint",
    "float4": "real", "float8": "double precision", "numeric": "numeric",
    "bool": "boolean",
    "text": "text", "varchar": "varchar", "bpchar": "char",
    "json": "json", "jsonb": "jsonb", "uuid": "uuid",
    "date": "date", "timestamp": "timestamp", "timestamptz": "timestamptz",
    "time": "time", "timetz": "timetz",
}


def pg_type(col: dict) -> str:
    udt = str(col.get("udt") or "").lower()
    if udt.startswith("_"):                       # 数组：_text → text[]
        return pg_type({"udt": udt[1:], "type": "text"}) + "[]"
    if udt in _PG_MAP:
        return _PG_MAP[udt]
    dt = str(col.get("type") or "").lower()
    if dt in ("array", "user-defined", ""):
        return "text"                             # 兜底，避免建表失败
    return dt


def sqlite_type(col: dict) -> str:
    udt = str(col.get("udt") or "").lower()
    if udt in ("int4", "int8", "int2", "bool"):
        return "INTEGER"
    if udt in ("float4", "float8", "numeric"):
        return "REAL"
    return "TEXT"


def _load_schema(dump_dir: str) -> dict:
    path = os.path.join(dump_dir, "_schema.json")
    if not os.path.exists(path):
        print("WARN 缺少 _schema.json（类型信息丢失），将降级为全 TEXT —— "
              "数值排序/聚合会失真，建议先跑 scripts/dump_schema.py")
        return {"tables": {}}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


def _load_manifest(dump_dir: str) -> dict:
    path = os.path.join(dump_dir, "manifest.json")
    if not os.path.exists(path):
        return {"tables": []}
    with io.open(path, encoding="utf-8") as f:
        return json.load(f)


# ══════════════════════════════════════════════════════════════════════════
# PostgreSQL 目标
# ══════════════════════════════════════════════════════════════════════════
def restore_pg(dump_dir: str, target_url: str, tables_meta: dict,
               manifest: dict, recreate: bool) -> int:
    import psycopg2

    conn = psycopg2.connect(target_url, connect_timeout=20)
    conn.autocommit = True
    cur = conn.cursor()
    expected = {x["name"]: x.get("rows") for x in manifest.get("tables", [])}
    failed, ok = [], 0

    for i, (t, meta) in enumerate(tables_meta.items(), 1):
        csv_path = os.path.join(dump_dir, f"{t}.csv.gz")
        if not os.path.exists(csv_path):
            print(f"[{i}/{len(tables_meta)}] skip {t}（无 csv）")
            continue
        try:
            if recreate:
                cur.execute(f'DROP TABLE IF EXISTS public."{t}" CASCADE')
            cols = meta["columns"]
            defs = [f'"{c["name"]}" {pg_type(c)}' for c in cols]
            if meta.get("primary_key"):
                pk = ", ".join(f'"{x}"' for x in meta["primary_key"])
                defs.append(f"PRIMARY KEY ({pk})")
            cur.execute(f'CREATE TABLE IF NOT EXISTS public."{t}" ({", ".join(defs)})')

            raw = gzip.open(csv_path, "rb")
            try:
                cur.copy_expert(
                    f'COPY public."{t}" FROM STDIN '
                    f'WITH (FORMAT CSV, HEADER true)', raw)
            finally:
                raw.close()

            cur.execute(f'SELECT COUNT(*) FROM public."{t}"')
            n = int(cur.fetchone()[0] or 0)
            exp = expected.get(t)
            flag = "" if (exp is None or exp == n) else f"  ⚠ 期望 {exp}"
            print(f"[{i}/{len(tables_meta)}] {t}: {n} 行{flag}")
            ok += 1
            if exp is not None and exp != n:
                failed.append((t, f"rows {n} != {exp}"))
        except Exception as e:
            print(f"[{i}/{len(tables_meta)}] FAIL {t}: {type(e).__name__} {str(e)[:140]}")
            failed.append((t, str(e)[:160]))
            try:
                conn.rollback()
            except Exception:
                pass

    # 索引（先灌数再建，快很多）
    for t, meta in tables_meta.items():
        for idx in meta.get("indexes") or []:
            ddl = idx.get("def") or ""
            if not ddl:
                continue
            try:
                cur.execute(ddl)
            except Exception:
                pass      # 索引失败不影响数据可用性

    conn.close()
    print(f"\n完成：成功 {ok} / 失败 {len(failed)}")
    for t, why in failed:
        print(f"  FAIL {t}: {why}")
    return 0 if not failed else 2


# ══════════════════════════════════════════════════════════════════════════
# SQLite 目标
# ══════════════════════════════════════════════════════════════════════════
def restore_sqlite(dump_dir: str, db_path: str, tables_meta: dict,
                   manifest: dict, recreate: bool) -> int:
    os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    cur = conn.cursor()
    expected = {x["name"]: x.get("rows") for x in manifest.get("tables", [])}
    failed, ok = [], 0

    for i, (t, meta) in enumerate(tables_meta.items(), 1):
        csv_path = os.path.join(dump_dir, f"{t}.csv.gz")
        if not os.path.exists(csv_path):
            continue
        try:
            if recreate:
                cur.execute(f'DROP TABLE IF EXISTS "{t}"')
            cols = meta["columns"]
            defs = [f'"{c["name"]}" {sqlite_type(c)}' for c in cols]
            if meta.get("primary_key"):
                pk = ", ".join(f'"{x}"' for x in meta["primary_key"])
                defs.append(f"PRIMARY KEY ({pk})")
            cur.execute(f'CREATE TABLE IF NOT EXISTS "{t}" ({", ".join(defs)})')

            names = [c["name"] for c in cols]
            ph = ", ".join("?" * len(names))
            sql = f'INSERT OR REPLACE INTO "{t}" ({", ".join(chr(34) + x + chr(34) for x in names)}) VALUES ({ph})'

            batch, n = [], 0
            with gzip.open(csv_path, "rt", encoding="utf-8", newline="") as f:
                rd = csv.reader(f)
                header = next(rd, None)
                for row in rd:
                    batch.append(row)
                    if len(batch) >= 2000:
                        cur.executemany(sql, batch)
                        n += len(batch)
                        batch = []
                if batch:
                    cur.executemany(sql, batch)
                    n += len(batch)
            conn.commit()

            exp = expected.get(t)
            flag = "" if (exp is None or exp == n) else f"  ⚠ 期望 {exp}"
            print(f"[{i}/{len(tables_meta)}] {t}: {n} 行{flag}")
            ok += 1
            if exp is not None and exp != n:
                failed.append((t, f"rows {n} != {exp}"))
        except Exception as e:
            print(f"[{i}/{len(tables_meta)}] FAIL {t}: {type(e).__name__} {str(e)[:140]}")
            failed.append((t, str(e)[:160]))
            conn.rollback()

    conn.close()
    print(f"\n完成：成功 {ok} / 失败 {len(failed)}")
    for t, why in failed:
        print(f"  FAIL {t}: {why}")
    return 0 if not failed else 2


# ══════════════════════════════════════════════════════════════════════════
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="恢复 db_dump_* 到目标库")
    ap.add_argument("--dump-dir", required=True)
    ap.add_argument("--target-url", default=None, help="PostgreSQL 目标连接串")
    ap.add_argument("--sqlite", dest="sqlite_path", default=None, help="SQLite 目标文件")
    ap.add_argument("--tables", default=None, help="逗号分隔，只恢复这些表")
    ap.add_argument("--keep", action="store_true",
                    help="不删旧表（默认 DROP 后重建，避免旧行混入）")
    args = ap.parse_args(argv)

    if not args.target_url and not args.sqlite_path:
        print("FAIL 必须指定 --target-url 或 --sqlite 之一")
        return 1

    schema = _load_schema(args.dump_dir)
    manifest = _load_manifest(args.dump_dir)
    tables_meta = schema.get("tables") or {}

    # 没有 schema 时，用 manifest 的 columns 兜底（全 TEXT）
    if not tables_meta:
        for x in manifest.get("tables", []):
            if x.get("columns"):
                tables_meta[x["name"]] = {
                    "columns": [{"name": c, "udt": "text"} for c in x["columns"]],
                    "primary_key": [], "indexes": [],
                }

    if args.tables:
        want = {x.strip() for x in args.tables.split(",") if x.strip()}
        tables_meta = {k: v for k, v in tables_meta.items() if k in want}

    if not tables_meta:
        print("FAIL 没有可恢复的表")
        return 1

    print(f"dump: {args.dump_dir}")
    print(f"表数: {len(tables_meta)}"
          f"{'（types 来自 _schema.json）' if schema.get('tables') else '（降级全 TEXT）'}")
    recreate = not args.keep
    if args.sqlite_path:
        print(f"目标: SQLite {args.sqlite_path}\n")
        return restore_sqlite(args.dump_dir, args.sqlite_path, tables_meta,
                              manifest, recreate)
    print(f"目标: PostgreSQL {args.target_url.split('@')[-1]}\n")
    return restore_pg(args.dump_dir, args.target_url, tables_meta,
                      manifest, recreate)


if __name__ == "__main__":
    sys.exit(main())
