#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】只补日批「未完成项」—— 对比 sync_meta 与日批 45 任务，自动拼出缺口 --tasks
================================================================================
背景：日批整批 45 个任务 / 180 分钟上限，超时或个别任务失败后被砍掉时，
  重跑 `--tasks all` 会把已成功的（含 LLM 类）再烧一遍。本脚本读 `sync_meta`
  （每个任务成功后写的版本号，note 以**数据日**开头）反推缺口，只跑缺的。

用法：
  python scripts/batch_resume.py                # 只打印缺口与拼好的命令（不执行）
  python scripts/batch_resume.py --run          # 拼好后直接调 daily_batch.py 执行
  python scripts/batch_resume.py --core-only    # 只看/只补 core 24 个
  python scripts/batch_resume.py --day 2026-10-24   # 指定数据日（默认=最近已完成交易日）

判定口径（与 scripts/daily_batch.py 的写入约定一致，见其 1956-1965 行）：
  · 任务成功   → sync_meta.touch(key, note=f"{数据日} | ...") ⇒ note[:10] 即数据日
  · 任务失败   → 不写 ⇒ note 停留在「上次成功日」⇒ 与目标数据日不等 = 缺
  · 周任务     （zz_finance 仅周一 / weekly_report 仅周五 / subfactor_ic 月首个 /
    verify_monthly 月度）在任务内部判定，非当日「秒过」也会写 sync_meta
    ⇒ 补跑它们无害（秒过，不重复计算）
前置：
  · DATABASE_URL（backend/.env 或环境变量）—— 本脚本只读 sync_meta / batch_runs，不写
  · 补跑时 DATA_SOURCE 强制 pack（与日批同口径，杜绝回源拉 K 线）
================================================================================
"""
import argparse
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)                                    # 复用 daily_batch 的任务注册表
sys.path.insert(0, os.path.join(HERE, "..", "backend"))     # app.*

import daily_batch as db_mod  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="只补日批未完成项（对比 sync_meta 反推缺口）")
    ap.add_argument("--day", default=None,
                    help="数据日 YYYY-MM-DD（默认=最近已完成交易日，与 daily_batch 同口径）")
    ap.add_argument("--core-only", action="store_true", help="只看/只补 core 任务")
    ap.add_argument("--run", action="store_true",
                    help="拼好后直接调 scripts/daily_batch.py 执行（不加 --force）")
    ap.add_argument("--timeout", type=int, default=1800, help="透传 daily_batch --task-timeout")
    args = ap.parse_args()

    db_mod.load_env()
    os.environ["DATA_SOURCE"] = "pack"          # 与日批同口径（脚本内只读，不落任何数据）

    day = (args.day or db_mod._batch_trading_day().isoformat())[:10]

    # ── 1) batch_runs：全量是否已成功收尾（n_fail=0）⇒ 直接结束 ──
    try:
        from app.database import db as _db
        r = _db.fetch_one(
            "SELECT finished_at, n_ok FROM batch_runs "
            "WHERE data_day = %s AND COALESCE(n_fail, 0) = 0", (day,))
        if r:
            print(f"✅ {day} 的日批已全量完成（{str(r.get('finished_at'))[:19]}，"
                  f"成功 {r.get('n_ok')} 项），无需补跑。")
            return 0
    except Exception as e:
        print(f"  [batch_runs] 查询失败（按未完成继续反推）: {str(e)[:120]}")

    # ── 2) sync_meta：每个任务「最后一次成功」的数据日 ──
    try:
        from app import sync_meta as sm
        vers = sm.versions() or {}
        last_ok = {k: ((v.get("note") or "")[:10]) for k, v in vers.items()}
    except Exception as e:
        print(f"⚠️ sync_meta 读取失败（{str(e)[:120]}）⇒ 按全部未完成处理")
        last_ok = {}

    candidates = ([t for t in db_mod.DEFAULT_ORDER if t in set(db_mod.CORE_TASKS)]
                  if args.core_only else list(db_mod.DEFAULT_ORDER))
    missing = [t for t in candidates if last_ok.get(t) != day]

    scope = "core" if args.core_only else "all"
    print(f"\n数据日: {day}    范围: {scope}    已完成 {len(candidates) - len(missing)}"
          f" / {len(candidates)}，缺口 {len(missing)}")
    for t in missing:
        tag = "core" if t in db_mod.CORE_TASKS else "extra"
        prev = last_ok.get(t) or "从未成功"
        print(f"  [{tag:>5}] {t:<20} 上次成功: {prev}")

    if not missing:
        print("✅ 无缺口，无需补跑。")
        return 0

    tasks_csv = ",".join(missing)
    print(f"\n拼好的 --tasks（{len(missing)} 项）:\n  {tasks_csv}")

    if not args.run:
        print("\n执行（二选一）:")
        print(f"  本地   : python scripts/daily_batch.py --tasks \"{tasks_csv}\"")
        print(f"  Actions: gh workflow run daily-batch.yml -f tasks=\"{tasks_csv}\" -f force=false")
        return 0

    print("\n▶ 调用 daily_batch.py 执行（缺口任务本就未完成，故不加 --force）...")
    cmd = [sys.executable, os.path.join(HERE, "daily_batch.py"),
           "--tasks", tasks_csv, "--task-timeout", str(args.timeout)]
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main())
