# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】导出 public schema 的结构元数据（列类型/主键/索引）为 JSON
================================================================================
为什么单独做（2026-10-31，Supabase 402 应急备份）：
  `dump_remote_db.py` 只导**数据**（CSV），列类型信息会丢。恢复时如果全建 TEXT：
    · `ORDER BY score DESC` 会按**字符串**排序（"9" > "10"）⇒ 榜单顺序全错；
    · `WHERE date >= %s` 也会按字符串比较（虽然 ISO 日期侥幸正确）；
    · 数值列的 `SUM()/AVG()` 直接失效。
  ⇒ 必须单独把类型抓下来。本脚本只读 information_schema，**不碰业务数据**。

输出：`<dump-dir>/_schema.json`
    {
      "tables": {
        "<table>": {
          "columns": [{name, type, udt, nullable, default, pos}, ...],
          "primary_key": [...],
          "indexes": [{name, def}, ...]
        }, ...
      }
    }

用法：
    python scripts/dump_schema.py --out db_dump_20261031
================================================================================
"""
import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "backend"))

_ENV = os.path.join(os.path.dirname(_HERE), "backend", ".env")
if os.path.exists(_ENV):
    with io.open(_ENV, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ★ Windows 控制台默认 GBK，`✓/✗/⇒` 会抛 UnicodeEncodeError（2026-10-31 实测：
#   文件已写成功但脚本以异常退出，容易被误判成"没导出来"）。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as _e:
    print(f"WARN stdout reconfigure failed: {_e}", file=sys.stderr)

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
if not DATABASE_URL.startswith("postgresql://"):
    print("FAIL DATABASE_URL 不是 PostgreSQL 连接串")
    sys.exit(1)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="导出 schema 元数据（JSON）")
    ap.add_argument("--out", default=".", help="输出目录（dump 目录）")
    ap.add_argument("--file", default="_schema.json", help="输出文件名")
    args = ap.parse_args(argv)

    import psycopg2

    os.makedirs(args.out, exist_ok=True)
    conn = psycopg2.connect(DATABASE_URL, connect_timeout=20)
    conn.autocommit = True
    cur = conn.cursor()

    # ── 列 ────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT table_name, column_name, data_type, udt_name,
               is_nullable, column_default, ordinal_position
        FROM information_schema.columns
        WHERE table_schema = 'public'
        ORDER BY table_name, ordinal_position
    """)
    tables = {}
    for t, c, dt, udt, nl, df, pos in cur.fetchall():
        tables.setdefault(t, {"columns": [], "primary_key": [], "indexes": []})
        tables[t]["columns"].append({
            "name": c, "type": dt, "udt": udt,
            "nullable": (nl == "YES"), "default": df, "pos": int(pos),
        })

    # ── 主键 ──────────────────────────────────────────────────────────
    cur.execute("""
        SELECT tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name
         AND kcu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'PRIMARY KEY'
          AND tc.table_schema = 'public'
        ORDER BY tc.table_name, kcu.ordinal_position
    """)
    for t, c in cur.fetchall():
        tables.setdefault(t, {"columns": [], "primary_key": [], "indexes": []})
        tables[t]["primary_key"].append(c)

    # ── 索引（含表达式索引；恢复时按 def 重放）───────────────────────
    cur.execute("""
        SELECT tablename, indexname, indexdef
        FROM pg_indexes WHERE schemaname = 'public'
        ORDER BY tablename, indexname
    """)
    for t, name, ddl in cur.fetchall():
        tables.setdefault(t, {"columns": [], "primary_key": [], "indexes": []})
        tables[t]["indexes"].append({"name": name, "def": ddl})

    out = {
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "table_count": len(tables),
        "tables": tables,
    }
    path = os.path.join(args.out, args.file)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    ncol = sum(len(v["columns"]) for v in tables.values())
    print(f"✓ {len(tables)} 张表 / {ncol} 列 → {path} "
          f"({os.path.getsize(path) / 1024:.1f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
