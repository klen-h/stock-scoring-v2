# -*- coding: utf-8 -*-
"""数据库**存量层**空间清理（Supabase 免费版 0.5 GB 上限）。

【分工：两个清理器，职责不同，不要混】

| | `app/db_retention.py`（日批 `retention` 任务） | 本脚本 |
|---|---|---|
| 管的是 | **增量层**：每天新增、读取只读最新 | **存量层**：历史堆积，要裁一刀 |
| 表 | `ranking_live` / `mainforce_state` / `mainflow_history` | `backtest_prices` / `lhb_history` / ... |
| 频率 | 每天 | **手动 / 季度一次** |
| VACUUM | **不做**（新写入会复用空间，长期平衡即可） | **必须做**，否则文件不缩小 |

★ 2026-11-16 事故复盘（库 440MB / 95% 撞线）：
  `db_retention` **在正常工作**，但它只管增量层；
  而 **95% 里 400MB 是存量**（`backtest_prices` 攒了 3.2 年、
  `mainflow_history` 8 个月无上限）——**存量层从来没被裁过**。
  ⇒ 本次一次裁剪：`440.5 → 349.9 MB（95% → 70%）`。

【清理原则：只删"可重新生成"的东西】
  ✅ 删：历史日线 / 龙虎榜 / 日频快照 —— 都能从数据源重抓或重算
  ❌ 不删：用户数据（`user_portfolio` / `user_trade_plans` / `t_*`）、
          快讯原文（`flash_news`）、推送记录（`push_log`）

【为什么删完还要 VACUUM】
  PostgreSQL 的 `DELETE` **只标记"可复用"，不缩小文件**；
  `pg_database_size()` 不会降，Supabase 控制台数字也不会降。
  ⇒ 必须 `VACUUM FULL`（重写表文件）才能真正释放。
  ⚠️ `VACUUM FULL` 会**锁表**，只在夜间跑；几百 MB 的库通常几分钟。

用法：
  python scripts/db_cleanup.py            # dry-run，只报告
  python scripts/db_cleanup.py --apply    # 真删 + VACUUM
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import db

# 保留策略（天）。数值都留了足够余量，见每行的理由。
RETENTION = [
    # (表, 日期列, 保留天数, 理由)
    # ⚠️ **这里只放"存量层"**（历史多、日增小、需要定期裁一刀 + VACUUM 才回收）。
    #    增量层（`ranking_live` / `mainforce_state` / `mainflow_history`）
    #    归 `app/db_retention.py` + 日批 `retention` 任务管，**不要重复**。
    ("backtest_prices", "date", 900,
     "日线历史：项目最大回测 500 根 ≈ 2 年，留 900 天(~2.5 年)足够"),
    ("lhb_history", "date", 400,
     "龙虎榜：只用于近期资金行为，1 年足够"),
    ("range_watch", "date", 180,
     "震荡区间日快照：可重算，半年足够"),
    ("stock_facts_wide", "date", 180,
     "量能/换手日快照：可重算，半年足够"),
]


def table_sizes():
    rows = db.fetch("""
        SELECT c.relname AS t,
               pg_total_relation_size(c.oid) AS bytes
        FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname='public' AND c.relkind='r'
    """) or []
    return {r["t"]: int(r["bytes"]) for r in rows}


def db_size_mb():
    r = db.fetch_one(
        "SELECT pg_database_size(current_database()) AS b") or {}
    return int(r.get("b") or 0) / 1024 / 1024


def main() -> int:
    apply_ = "--apply" in sys.argv
    before = db_size_mb()
    print("当前数据库 %.1f MB" % before)
    print()

    total_rows = 0
    touched = []
    for tbl, col, keep_days, why in RETENTION:
        # ⚠️ 各表的日期列类型不一致：`backtest_prices.date` / `stock_facts_wide.date`
        #   是 **text**，而 `lhb_history.date` 是 **date**。
        #   直接与文本比较会报 `operator does not exist: date < text`（实测踩到）。
        #   ⇒ 统一 `CAST(col AS DATE)`：text 的 'YYYY-MM-DD' 能转、date 转换无损。
        #   代价是失去索引（清理场景可接受，全表扫一次）。
        cutoff = "CAST(%s AS DATE) < (CURRENT_DATE - INTERVAL '%d days')" % (
            col, int(keep_days))
        try:
            r = db.fetch_one(
                f"SELECT COUNT(1) AS n, MIN(CAST({col} AS DATE)) AS lo "
                f"FROM {tbl} WHERE {cutoff}") or {}
            n = int(r.get("n") or 0)
            print("  %-20s 保留 %4d 天 → 可删 %8s 行（最早 %s）" % (
                tbl, keep_days, f"{n:,}", str(r.get("lo"))[:10]))
            print("      理由：%s" % why)
            if n <= 0:
                continue
            total_rows += n
            if apply_:
                deleted = db.execute(f"DELETE FROM {tbl} WHERE {cutoff}")
                print("      ✓ 已删除 %s 行" % f"{int(deleted or 0):,}")
                touched.append(tbl)
        except Exception as e:
            print("  %-20s ⚠ 跳过：%s" % (tbl, str(e)[:70]))

    print()
    print("合计可删 %s 行" % f"{total_rows:,}")

    if not apply_:
        print()
        print("（dry-run）要真正执行：python scripts/db_cleanup.py --apply")
        return 0

    # ── VACUUM FULL：DELETE 不缩小文件，必须重写 ──
    print()
    print("开始 VACUUM FULL（会锁表，夜间执行）…")
    try:
        from app.database import DATABASE_URL as dsn
    except Exception:
        dsn = ""
    if not dsn:
        print("  ⚠ 拿不到连接串，跳过 VACUUM。请在 Supabase SQL Editor 里执行：")
        for t in touched:
            print("     VACUUM FULL %s;" % t)
        return 0
    try:
        import psycopg2
    except Exception as e:
        print("  ⚠ 未安装 psycopg2（%s），请在 Supabase SQL Editor 里执行：" % str(e)[:50])
        for t in touched:
            print("     VACUUM FULL %s;" % t)
        return 0

    try:
        conn = psycopg2.connect(dsn)
        conn.autocommit = True          # VACUUM 不能在事务块里跑
        with conn.cursor() as cur:
            for t in touched:
                print("  VACUUM FULL %s …" % t)
                cur.execute("VACUUM FULL %s" % t)
        conn.close()
        print("  ✓ VACUUM 完成")
    except Exception as e:
        print("  ⚠ VACUUM 失败：%s" % str(e)[:100])
        print("    可在 Supabase SQL Editor 手动执行上述语句")

    after = db_size_mb()
    print()
    print("数据库 %.1f MB → %.1f MB（释放 %.1f MB，现占用 %.1f%%）" % (
        before, after, before - after, after / 500 * 100))
    return 0


if __name__ == "__main__":
    sys.exit(main())
