#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】换号迁移修复：补齐新库缺失的主键/唯一约束
================================================================================

背景（2026-09-12）：
  新 Supabase 项目由 pg_dump/pg_restore 迁移，但**约束/索引阶段没有跑成**——
  public 50 张表只剩 3 个 CHECK 约束和 26 个普通索引（ensure_schema 补的），
  主键和 UNIQUE 全部缺失。后果：
    · 所有 ON CONFLICT 幂等写入全部 42P10（快讯 flash_state 每 5 分钟报错 ×2）
    · 周一日批（评分快照/战法扫描/主力状态/消息分/调度状态…）会集体写失败
    · 无唯一键 → 用户同步/回填类写入可产生重复行；全表扫描变慢

用法：
  python scripts/fix_restored_constraints.py --check   # 只查重+列缺失清单（只读）
  python scripts/fix_restored_constraints.py --fix     # 无重复的表补建约束（幂等，可重跑）
  python scripts/fix_restored_constraints.py --fix --yes  # 跳过确认

安全设计：
  · 每个键先 GROUP BY 查重：有重复 → 跳过该表并打印，绝不自动删数据
  · 约束创建全部 IF NOT EXISTS 语义（先查 pg_indexes/pg_constraint）
  · 唯一键用 CREATE UNIQUE INDEX 实现（ON CONFLICT 对唯一索引同样生效）；
    主键用 ALTER TABLE ADD PRIMARY KEY
  · 失败只影响单表（逐表提交），中断重跑安全
================================================================================
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
BJ = timezone(timedelta(hours=8))


def _load_env():
    path = os.path.join(BACKEND, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _conn():
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url.startswith("postgres"):
        print("::error:: 需要 DATABASE_URL")
        sys.exit(2)
    import psycopg2
    for i in range(4):
        try:
            return psycopg2.connect(url, connect_timeout=15, sslmode="require")
        except Exception as e:
            print(f"  连接重试 {i+1}: {str(e)[:60]}")
            time.sleep(4)
    sys.exit(2)


# (表, 类型 PK|U, 列) —— 与 backend/app/database.py ensure_schema 及各模块建表语句同口径
TARGETS = [
    ("flash_news", "PK", ["id"]),
    ("flash_analyses", "PK", ["id"]),
    ("flash_reviews", "PK", ["id"]),
    ("flash_state", "PK", ["key"]),
    ("macro_history", "PK", ["id"]),
    ("macro_daily", "PK", ["date"]),
    ("backtest_prices", "U", ["code", "date"]),
    ("etf_close", "U", ["date"]),
    ("etf_signals", "PK", ["id"]),
    ("etf_price_history", "U", ["etf_name", "date"]),
    ("strategy_results", "U", ["strategy_name", "scan_date"]),
    ("paper_positions", "U", ["strategy_name", "code", "signal_date"]),
    ("paper_account", "PK", ["id"]),
    ("paper_risk_events", "PK", ["id"]),
    ("strategy_watch", "U", ["strategy_name", "code"]),
    ("tracking_state", "PK", ["id"]),
    ("schedule_state", "PK", ["task"]),
    ("market_snapshot", "PK", ["key"]),
    ("users", "PK", ["id"]),
    ("users", "U", ["username"]),
    ("user_watchlist", "U", ["user_id", "code"]),
    ("user_trade_plans", "PK", ["id"]),
    ("user_portfolio", "U", ["user_id", "code"]),
    ("strategy_backtest", "U", ["strategy_name", "backtest_date"]),
    ("contradictions", "PK", ["id"]),
    ("contradictions", "U", ["date", "type"]),
    ("contradiction_reports", "PK", ["date"]),
    ("stock_industry", "PK", ["code"]),
    ("industry_map_meta", "PK", ["key"]),
    ("sector_daily", "U", ["date", "kind", "code"]),
    ("stock_finance", "U", ["code", "report_date"]),
    ("news_history", "U", ["snap_date", "code"]),
    ("ranking_history", "U", ["rank_date", "code"]),
    ("kline_cache", "PK", ["code"]),
    ("sync_meta", "PK", ["key"]),
    ("mainflow_history", "U", ["code", "date"]),
    ("data_inventory_state", "PK", ["key"]),
    ("market_regime_history", "U", ["date"]),
    ("trader_briefs", "U", ["date", "phase"]),
    ("llm_usage_daily", "PK", ["day"]),
    ("backtest_reports", "U", ["name"]),
    ("backtest_cache", "PK", ["strategy"]),
    ("signal_history", "U", ["strategy_name", "code", "signal_date"]),
    ("indicator_cache", "PK", ["code"]),
    ("mainforce_state", "U", ["code", "date"]),
    ("lhb_history", "U", ["code", "date"]),
    ("stock_finance_zz", "PK", ["code"]),
    ("stock_finance_ocf_hist", "U", ["code", "report_date"]),
    ("industry_mainline", "U", ["date", "industry"]),
    ("daily_reports", "PK", ["date"]),
    ("flash_calendar", "PK", ["key"]),
]


def _idx_name(table, kind, cols):
    return ("uq_" if kind == "U" else "pk_") + table + "_" + "_".join(cols)


def _col_list(cur, table):
    cur.execute(
        "SELECT a.attname FROM pg_attribute a JOIN pg_class c ON a.attrelid=c.oid "
        "JOIN pg_namespace n ON c.relnamespace=n.oid "
        "WHERE c.relname=%s AND n.nspname='public' AND a.attnum>0 AND NOT a.attisdropped",
        (table,))
    return {r[0] for r in cur.fetchall()}


def _existing(cur, table, kind, cols):
    cur.execute(
        "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i "
        "JOIN pg_class c ON i.indrelid=c.oid JOIN pg_namespace n ON c.relnamespace=n.oid "
        "WHERE c.relname=%s AND n.nspname='public' AND i.indisunique AND NOT i.indisprimary",
        (table,))
    uidx = cur.fetchall()
    cur.execute(
        "SELECT pg_get_constraintdef(c.oid) FROM pg_constraint c "
        "JOIN pg_class r ON c.conrelid=r.oid JOIN pg_namespace ns ON r.relnamespace=ns.oid "
        "WHERE r.relname=%s AND ns.nspname='public' AND c.contype IN ('p','u')",
        (table,))
    for (d,) in cur.fetchall():
        if all(col in d for col in cols):
            return True
    if kind == "U":
        for (d,) in uidx:
            if all(col in d for col in cols):
                return True
    return False


def _dup_count(cur, table, cols):
    cl = ", ".join(cols)
    cur.execute(f'SELECT count(*) FROM (SELECT {cl} FROM public."{table}" '
                f"GROUP BY {cl} HAVING count(*)>1) d")
    return cur.fetchone()[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("--yes", action="store_true")
    args = ap.parse_args()
    if not args.check and not args.fix:
        args.check = True

    _load_env()
    conn = _conn()
    conn.autocommit = False
    cur = conn.cursor()
    print(f"== 迁移约束修复 {datetime.now(BJ):%Y-%m-%d %H:%M} 北京时间 ==\n")

    missing, dup_tables, done, skipped = [], [], [], []
    # 1) 盘点：缺什么、哪些表有重复数据
    for table, kind, cols in TARGETS:
        cur.execute(
            "SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename=%s", (table,))
        if not cur.fetchone():
            print(f"  [跳过] {table}: 表不存在")
            continue
        cols_avail = _col_list(cur, table)
        if not set(cols) <= cols_avail:
            print(f"  [跳过] {table}: 缺列 {set(cols)-cols_avail}")
            skipped.append(table)
            continue
        if _existing(cur, table, kind, cols):
            continue
        missing.append((table, kind, cols))
        dups = _dup_count(cur, table, cols)
        if dups:
            dup_tables.append((table, kind, cols, dups))
    conn.rollback()

    print(f"缺失键 {len(missing)} 个；其中 {len(dup_tables)} 个表存在重复数据（将跳过）\n")
    for table, kind, cols, dups in dup_tables:
        print(f"  [重复] {table} {kind}({','.join(cols)}) — {dups} 组重复，需人工清理")
    if args.check or not args.fix:
        print("\n（--check 模式结束，未做任何修改）")
        conn.close()
        return
    if dup_tables and not args.yes:
        print("\n存在重复数据表，已全部跳过；确认后重跑 --fix --yes 只修无重复表。")
        conn.close()
        return

    # 2) 逐表补建（无重复的才动手；逐表提交，失败不影响其余）
    ok = fail = 0
    for table, kind, cols in missing:
        if any(t == table for t, _, _, _ in dup_tables):
            continue
        name = _idx_name(table, kind, cols)
        cl = ", ".join(cols)
        try:
            if kind == "U":
                cur.execute(f'CREATE UNIQUE INDEX IF NOT EXISTS "{name}" '
                            f'ON public."{table}" ({cl})')
            else:
                cur.execute(f'ALTER TABLE public."{table}" '
                            f'ADD CONSTRAINT "{name}" PRIMARY KEY ({cl})')
            conn.commit()
            ok += 1
            print(f"  [OK] {table}: {kind}({cl})")
        except Exception as e:
            conn.rollback()
            fail += 1
            print(f"  [失败] {table}: {kind}({cl}) — {str(e).strip()[:120]}")

    print(f"\n完成：补建 {ok} 个，失败 {fail} 个，重复跳过 {len(dup_tables)} 个表。")
    print("验证：重跑 --check 应显示缺失为 0；观察 Supabase 日志 42P10 应停止。")
    conn.close()


if __name__ == "__main__":
    main()
