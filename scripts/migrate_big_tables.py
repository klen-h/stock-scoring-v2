#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】大 JSON 表的分页迁移兜底（源 Supabase → 目标 Supabase），专治断线
================================================================================

背景（2026-09-12）：
  换新 Supabase 账号迁移时，pg_dump / COPY 在 `indicator_cache`、`kline_cache`
  这类"单行几百 KB 大 JSON"的表上稳定断线（普通表正常）。
  根因通常是：① 整表结果在客户端一次性缓冲（几十上百 MB）② 单连接传太久被
  中间设备掐断 ③ 一断就从头再来。
  本脚本用「keyset 分页 + 小批事务 + 断点续传」绕开全部三点。

用法：
  # 0) 设两个连接串（源=旧项目，目标=新项目；都用【直连串】db.xxx:5432）
  export MIGRATE_SOURCE_URL="postgresql://postgres:***@db.<旧ref>.supabase.co:5432/postgres"
  export MIGRATE_TARGET_URL="postgresql://postgres:***@db.<新ref>.supabase.co:5432/postgres"

  # 1) 只验证连通性/行数/大行尺寸，不写任何数据（安全，可随时跑）
  python scripts/migrate_big_tables.py --probe

  # 2) 正式迁移（⚠️ 与 pg_dump 那路不要并行；表结构需已由 dump 建好）
  python scripts/migrate_big_tables.py --tables kline_cache,indicator_cache
  #    常用参数：--batch 20（每批行数） --rows-per-stmt 5（每条 INSERT 行数）
  #             --max-rows 100（只迁前 100 行试跑） --tables ...（逗号分隔）

  # 3) 断了/中断后重跑同一条命令即可 —— 从"最后成功的主键"续传，不重复
  #    状态文件：backend/data/migrate_big_tables_state.json（gitignore 内）

安全设计：
  · 源连接强制只读（default_transaction_read_only = on）
  · 目标写入 ON CONFLICT (pk) DO UPDATE → 幂等，重跑不产生重复
  · 每批一个事务、成功即落状态文件 → 断线最多损失一批
  · 全程不打印连接串与数据内容，只打印行数/主键/尺寸
================================================================================
"""
import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
STATE_FILE = os.path.join(BACKEND, "data", "migrate_big_tables_state.json")
BJ = timezone(timedelta(hours=8))

DEFAULT_TABLES = ["kline_cache", "indicator_cache"]
RETRY_BACKOFF = [5, 15, 45, 90, 180]      # 断线退避（秒），5 次后放弃该表


def _load_env():
    path = os.path.join(BACKEND, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _connect(url: str, readonly: bool = False, label: str = ""):
    """keepalives 是关键：大 JSON 行之间有读取间隙，NAT/代理容易掐空闲连接。"""
    import psycopg2
    conn = psycopg2.connect(url, connect_timeout=20,
                            keepalives=1, keepalives_idle=30,
                            keepalives_interval=10, keepalives_count=5,
                            application_name="migrate_big_tables")
    conn.autocommit = True
    if readonly:
        cur = conn.cursor()
        cur.execute("SET default_transaction_read_only = on")
        cur.execute("SET statement_timeout = '10min'")
        cur.close()
    print(f"  [{label}] 已连接")
    return conn


def _pk_of(cur, table: str):
    cur.execute("""SELECT a.attname FROM pg_index i
                   JOIN pg_attribute a ON a.attrelid = i.indrelid
                        AND a.attnum = ANY(i.indkey)
                   WHERE i.indrelid = to_regclass(%s) AND i.indisprimary
                   ORDER BY a.attnum LIMIT 1""", (f"public.{table}",))
    r = cur.fetchone()
    return r[0] if r else None


def _columns(cur, table: str):
    cur.execute("""SELECT column_name, data_type FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""",
                (table,))
    return [(r[0], r[1]) for r in cur.fetchall()]


def _count(cur, table: str) -> int:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    return int(cur.fetchone()[0])


def _probe(args):
    src = os.environ.get("MIGRATE_SOURCE_URL") or os.environ.get("DATABASE_URL") or ""
    tgt = os.environ.get("MIGRATE_TARGET_URL") or ""
    if not tgt:
        print("::error:: 请设置 MIGRATE_TARGET_URL（新项目直连串）")
        return 2
    tables = [t.strip() for t in (args.tables or ",".join(DEFAULT_TABLES)).split(",") if t.strip()]

    print("=== 源（旧项目，只读）===")
    sc = _connect(src, readonly=True, label="源")
    scur = sc.cursor()
    scur.execute("SELECT version()")
    print("  ", scur.fetchone()[0][:80])
    for t in tables:
        try:
            pk = _pk_of(scur, t)
            n = _count(scur, t)
            cols = _columns(scur, t)
            print(f"  {t}: {n:,} 行 / 主键={pk} / {len(cols)} 列")
            # 抽 1 行测"单行过网尺寸"——这正是 pg_dump 断线的根源，先证明逐行可行
            if pk and n:
                big = max((c for c, dt in cols
                           if dt in ("text", "json", "jsonb")), key=lambda c: 0, default=None)
                scur.execute(f"SELECT {pk}, pg_column_size({big or pk}) FROM {t} "
                             f"ORDER BY {pk} DESC LIMIT 1")
                k, size = scur.fetchone()
                print(f"     最大行参考: {pk}={k}  {big or pk} 过网约 {size/1024:.0f} KB")
        except Exception as e:
            sc.rollback()
            print(f"  {t}: 读取失败 {str(e)[:100]}")

    print("\n=== 目标（新项目，只读检查）===")
    tc = _connect(tgt, readonly=True, label="目标")
    tcur = tc.cursor()
    tcur.execute("SELECT version()")
    print("  ", tcur.fetchone()[0][:80])
    tcur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
    all_tables = [r[0] for r in tcur.fetchall()]
    print(f"  已有表 {len(all_tables)} 张: {', '.join(all_tables[:25])}"
          f"{' …' if len(all_tables) > 25 else ''}")
    for t in tables:
        if t not in all_tables:
            print(f"  ⚠️ {t}: 目标库还没有这张表（等 pg_dump 那路建完结构再跑本脚本）")
            continue
        n = _count(tcur, t)
        pk = _pk_of(tcur, t)
        print(f"  {t}: {n:,} 行 / 主键={pk}")
    sc.close()
    tc.close()
    return 0


def _migrate_table(src, tgt, table: str, args, state: dict) -> bool:
    """迁移单张表（keyset 分页 + 断点续传）。返回是否全部完成。"""
    scur, tcur = src.cursor(), tgt.cursor()
    pk = _pk_of(scur, table) or getattr(args, "pk", None)
    if not pk:
        print(f"  [{table}] ✗ 源表没有主键且未指定 --pk，跳过（本脚本按主键 keyset 分页）")
        return False
    src_cols = [c for c, _ in _columns(scur, table)]
    tgt_cols = [c for c, _ in _columns(tgt, table)]
    cols = [c for c in src_cols if c in tgt_cols]
    missing = set(src_cols) - set(tgt_cols)
    if missing:
        print(f"  [{table}] ⚠️ 目标缺列 {sorted(missing)}，这些列不迁")
    total_src = _count(scur, table)
    st = state.setdefault(table, {"last_pk": None, "done": 0})
    last, done = st.get("last_pk"), int(st.get("done") or 0)
    print(f"  [{table}] 源 {total_src:,} 行，主键={pk}，从 {last!r} 之后续传"
          f"（已完成 {done:,}）")

    upd = ", ".join(f"{c}=EXCLUDED.{c}" for c in cols if c != pk)
    if getattr(args, "no_conflict", False):
        ins = f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s"
    else:
        ins = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
               f"ON CONFLICT ({pk}) DO UPDATE SET {upd}")

    t0 = time.time()
    failures = 0
    while True:
        where = f" WHERE {pk} > %s" if last is not None else ""
        sql = (f"SELECT {', '.join(cols)} FROM {table}{where} "
               f"ORDER BY {pk} LIMIT {args.batch}")
        try:
            scur.execute(sql, (last,) if last is not None else None)
            rows = scur.fetchall()
        except Exception as e:
            failures += 1
            if failures > len(RETRY_BACKOFF):
                print(f"  [{table}] ✗ 连续失败放弃: {str(e)[:120]}")
                return False
            wait = RETRY_BACKOFF[failures - 1]
            print(f"  [{table}] 读取断线（{str(e)[:80]}），{wait}s 后从 {last!r} 重试")
            time.sleep(wait)
            try:
                src.close()
            except Exception:
                pass
            src = _connect(os.environ.get("MIGRATE_SOURCE_URL")
                           or os.environ.get("DATABASE_URL"), readonly=True, label="源重连")
            scur = src.cursor()
            continue
        if not rows:
            break

        # 写入：小批事务 + 幂等；写成功才推进 last（断线不重复）
        try:
            _write_chunk(tcur, ins, cols, rows, args.rows_per_stmt)
        except Exception as e:
            failures += 1
            if failures > len(RETRY_BACKOFF):
                print(f"  [{table}] ✗ 写入连续失败放弃: {str(e)[:120]}")
                return False
            wait = RETRY_BACKOFF[failures - 1]
            print(f"  [{table}] 写入断线（{str(e)[:80]}），{wait}s 后重试本批")
            time.sleep(wait)
            try:
                tgt.close()
            except Exception:
                pass
            tgt = _connect(os.environ.get("MIGRATE_TARGET_URL"), label="目标重连")
            tcur = tgt.cursor()
            continue

        last = rows[-1][cols.index(pk)]
        done += len(rows)
        failures = 0
        state[table] = {"last_pk": last, "done": done,
                        "ts": datetime.now(BJ).isoformat(timespec="seconds")}
        _save_state(state)
        if args.max_rows and done >= args.max_rows:
            print(f"  [{table}] 达到 --max-rows={args.max_rows}，停止（续传可继续）")
            return False
        print(f"  [{table}] {done:,}/{total_src:,} 行 "
              f"({done * 100 // max(total_src, 1)}%) 最后主键={last} "
              f"本批 {len(rows)} 行 用时 {time.time() - t0:.0f}s", flush=True)
        t0 = time.time()
        if len(rows) < args.batch:
            break

    print(f"  [{table}] ✅ 完成：源 {total_src:,} 行 → 目标累计写入 {done:,} 行")
    return True


def _write_chunk(tcur, ins_sql, cols, rows, rows_per_stmt):
    """把一批行写进目标库（每 rows_per_stmt 行一条 INSERT，小事务）。"""
    from psycopg2.extras import execute_values
    tpl = "(" + ",".join(["%s"] * len(cols)) + ")"
    for i in range(0, len(rows), max(1, rows_per_stmt)):
        part = rows[i:i + max(1, rows_per_stmt)]
        tcur.execute("BEGIN")
        try:
            execute_values(tcur, ins_sql, part, template=tpl, page_size=len(part))
            tcur.execute("COMMIT")
        except Exception:
            tcur.execute("ROLLBACK")
            raise


def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE, encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def _save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    json.dump(state, open(STATE_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


def cmd_migrate(args):
    src_url = os.environ.get("MIGRATE_SOURCE_URL") or os.environ.get("DATABASE_URL") or ""
    tgt_url = os.environ.get("MIGRATE_TARGET_URL") or ""
    if not tgt_url:
        print("::error:: 请设置 MIGRATE_TARGET_URL（新项目直连串）")
        return 2
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    state = _load_state()
    src = _connect(src_url, readonly=True, label="源")
    tgt = _connect(tgt_url, label="目标")
    results, verify = {}, []
    try:
        # ★ 源库这两张表没有主键/唯一索引（实测）—— keyset 分页与 ON CONFLICT 都需要一个
        #   唯一键。默认用 --pk（语义上 code 就是唯一的：打包/刷新都是按 code upsert），
        #   并在【目标】补一个唯一索引（幂等写入的前提，也是新库该有的结构）。
        for t in tables:
            scur, tcur = src.cursor(), tgt.cursor()
            if not _pk_of(scur, t):
                if not args.pk:
                    print(f"  [{t}] ✗ 源表无主键且未指定 --pk，跳过")
                    results[t] = False
                    continue
                try:
                    tcur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS "
                                 f"ux_{t}_{args.pk} ON {t} ({args.pk})")
                    print(f"  [{t}] 目标已建唯一索引 ux_{t}_{args.pk}({args.pk})"
                          "（幂等写入的前提）")
                except Exception as e:
                    tgt.rollback()
                    print(f"  [{t}] ⚠️ 目标建唯一索引失败（可能有重复行）: {str(e)[:100]}\n"
                          f"        → 将退化为普通 INSERT（要求目标表此时为空）")
                    args.no_conflict = True
        for t in tables:
            results[t] = _migrate_table(src, tgt, t, args, state)
        # 校验要在连接关闭前做
        scur, tcur = src.cursor(), tgt.cursor()
        for t in tables:
            try:
                a, b = _count(scur, t), _count(tcur, t)
                verify.append((t, a, b))
                print(f"  {t}: 源 {a:,} / 目标 {b:,}"
                      f"  {'✅' if a == b else '⚠️ 不一致（可重跑续传）'}")
            except Exception as e:
                print(f"  {t}: 校验失败 {str(e)[:80]}")
    finally:
        _save_state(state)
        try:
            src.close()
        except Exception:
            pass
        try:
            tgt.close()
        except Exception:
            pass
    return 0 if all(results.values()) else 1


def main():
    _load_env()
    ap = argparse.ArgumentParser(description="大 JSON 表分页迁移兜底")
    ap.add_argument("--probe", action="store_true", help="只验证连通性/行数/大行尺寸，不写")
    ap.add_argument("--tables", default=",".join(DEFAULT_TABLES))
    ap.add_argument("--batch", type=int, default=20, help="每批读取行数")
    ap.add_argument("--rows-per-stmt", type=int, default=5, help="每条 INSERT 的行数")
    ap.add_argument("--max-rows", type=int, default=0, help="限制总行数（试跑用，0=不限）")
    ap.add_argument("--pk", default="code",
                    help="源表无主键时的分页键（本项目 kline_cache/indicator_cache 均为 code）")
    args = ap.parse_args()
    if args.probe:
        return _probe(args)
    return cmd_migrate(args)


if __name__ == "__main__":
    sys.exit(main())
