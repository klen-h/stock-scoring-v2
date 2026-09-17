#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】本地开发「零 Supabase 流量」启动器 —— 严格按顺序执行
================================================================================

背景（2026-09-17 复盘；同型事故第 3 次，前两次记录在 app/pack_source.py:363-371）：
  Supabase 免费版 egress 只有 167MB/天，而本地开发会把它打爆：
    · 2026-09-12 实测 596MB/天（见 pack_source.py 注释）
    · 2026-09-17 用户再遇 ~600MB
  触发链是**静默**的、用户毫无感知：
      DATA_SOURCE=local/pack + 本地包陈旧
        → pack_source._is_stale() = True
        → get_klines() 返回 None（"宁缺毋旧"）
        → 各调用方回退查 Supabase（K线/指标/backtest_prices 全是大表）
        → 本地跑几个脚本就是几百 MB

  ★ 为什么"包看起来不旧"也会踩（本次新发现的口径矛盾）：
    · `_is_stale()`  用**交易日历**判：交易日 22:00 之后要求"当天"包
      （_PACK_READY_HHMM = (22, 0)，见 pack_source.py:299-310）
    · 而 pack 模式重下用 `_db_fresh()` 判 **mtime < 30h**
    两个判据不一致 → mtime 还"新"时**不会重下**，但日历已判陈旧 → 静默回退。
    也就是每天 22:00 之后到本地文件变旧之前，是一个**必然的泄漏窗口**。
    local 模式更糟：**完全没有自动重下**，会一直回退，直到手动同步。

本脚本按顺序做四件事（**顺序不可颠倒** —— 颠倒就会先生成流量）：
  ① 固定 DATA_SOURCE=local（只影响本进程与子进程，不改 backend/.env）
  ② 预检本地包 pack_date 是否 >= 「此刻本应可用日」；不满足 → 从 **GitHub Pages**
     重下（零 Supabase 流量；_PAGES 是唯一可用源，见 sync_local.py 注释）
  ③ 复检：仍不满足 → **硬失败退出**（宁可不开服务，也不静默烧 egress）
  ④ 以 DATA_SOURCE=local 启动后端

用法：
  python scripts/dev_local.py                 # 预检 + 必要同步 + 启动后端
  python scripts/dev_local.py --check         # 只做①②的"只读版"：报告状态，不下载不启动
  python scripts/dev_local.py --force-sync    # 强制重下包（跳过 20h 跳过逻辑）
  python scripts/dev_local.py --no-start      # 同步完就退出（CI / 手动验收用）
  python scripts/dev_local.py --readonly      # 另加 RENDER_READ_ONLY=1（关重活 loop，最省）
  python scripts/dev_local.py --port 8001     # 指定端口

注意：`--check` 会 import 项目模块以复用 pack_source 的**同一条**新鲜度判据
（交易日历可能查一次库，量级极小）。这一点是刻意的 —— 自检必须和生产判
据完全一致，否则自检通过、运行时仍然回退。
================================================================================
"""
import argparse
import importlib.util
import os
import sqlite3
import subprocess
import sys
import time

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
PACK_DB = os.path.join(BACKEND_DIR, "data", "pack", "backend-pack.db")

# ① 必须最先做：在任何 app.* import 之前固定数据源，否则后面 import 会按 db 初始化
os.environ["DATA_SOURCE"] = "local"
sys.path.insert(0, BACKEND_DIR)


def _step(n, text):
    print(f"\n[{n}/4] {text}")


def local_pack_date():
    """本地包的 pack_date（无包/读失败 → None）。只读本地 SQLite，零流量。"""
    if not os.path.exists(PACK_DB):
        return None
    con = None
    try:
        con = sqlite3.connect(PACK_DB)
        row = con.execute("SELECT value FROM meta WHERE key = 'pack_date'").fetchone()
        return (row[0] if row else None) or None
    except Exception:
        return None
    finally:
        if con is not None:
            con.close()


def expected_pack_day():
    """「此刻本应可用的最新包日期」—— 直接复用 pack_source 的同一条判据。

    ★ 刻意不自己写近似公式：自检必须与运行时判据**完全一致**，否则会出现
      "自检通过、运行时仍判陈旧 → 静默回退"的假安全。
    """
    try:
        from app.pack_source import _latest_available_pack_day, _parse_pack_date
        return _latest_available_pack_day(), _parse_pack_date
    except Exception as e:
        print(f"  [WARN] 无法复用 pack_source 判据（{e}），退化为按自然日判断")
        from datetime import datetime, timedelta
        now = datetime.now()
        if (now.hour, now.minute) >= (22, 0):
            return now.date(), None
        for i in range(1, 8):
            c = now - timedelta(days=i)
            if c.weekday() < 5:
                return c.date(), None
        return now.date(), None


def _sync(force):
    """从 GitHub Pages 重下 backend-pack.db.gz（零 Supabase 流量）。"""
    spec = importlib.util.spec_from_file_location(
        "sync_local", os.path.join(SCRIPTS_DIR, "sync_local.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if (not force and os.path.exists(PACK_DB)
            and time.time() - os.path.getmtime(PACK_DB) < 20 * 3600):
        print("  本地包文件较新（<20h），跳过下载（--force-sync 可强制）")
        return
    mod._download(os.environ.get("PACK_URL", mod.DEFAULT_URL),
                  threads=mod.DEFAULT_THREADS)


def _status(expected, parse):
    """返回 (ok, 描述)。ok=True 表示当前包满足运行时判据、不会回退 Supabase。"""
    raw = local_pack_date()
    if raw is None:
        return False, "本地无数据包（会全量回退 Supabase → egress 暴涨）"
    d = parse(raw) if parse else raw
    if d is None:
        return False, f"pack_date 无法解析（{raw}）"
    if d < expected:
        return False, (f"包陈旧：pack_date={raw} < 应可用 {expected}"
                       f" → 读侧会静默回退 Supabase")
    return True, f"包满足判据：pack_date={raw} >= 应可用 {expected}"


def main():
    ap = argparse.ArgumentParser(
        description="本地开发零 Supabase 流量启动器（严格按顺序执行）")
    ap.add_argument("--check", action="store_true",
                    help="只报告状态：不下载、不启动")
    ap.add_argument("--force-sync", action="store_true", help="强制重下数据包")
    ap.add_argument("--no-start", action="store_true", help="同步完即退出")
    ap.add_argument("--readonly", action="store_true",
                    help="另加 RENDER_READ_ONLY=1（关掉重活 loop，最省 DB）")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    print("=" * 78)
    print("本地开发零 Supabase 流量启动器")
    print(f"  数据源        : DATA_SOURCE=local（本轮进程与子进程生效）")
    print(f"  本地包        : {PACK_DB}")
    print("=" * 78)

    # ② 预检
    _step(1, "预检本地数据包新鲜度")
    expected, parse = expected_pack_day()
    ok, desc = _status(expected, parse)
    print(f"  {desc}")

    if args.check:
        print("\n[--check] 只报告，不下载、不启动。")
        return 0 if ok else 2

    if not ok:
        _step(2, "包不满足判据 → 从 GitHub Pages 重下（零 Supabase 流量）")
        # ★ 必须 force=True：sync_local 默认用「mtime < 20h 就跳过下载」，
        #   而**判陈旧的是交易日历** —— 这正是 pack_source 里那个口径矛盾的翻版。
        #   若在这里沿用 mtime 短路，就会出现「跳过下载 → 复检仍陈旧 → 白跑」。
        try:
            _sync(force=True)
        except Exception as e:
            print(f"  [ERROR] 下载失败：{e}")
            print("  → 已中止，**不启动服务**（启动即静默回退 Supabase）")
            return 3
        # ③ 复检：硬失败，绝不带病启动
        _step(3, "复检（仍不满足则拒绝启动）")
        ok2, desc2 = _status(expected, parse)
        print(f"  {desc2}")
        if not ok2:
            print("\n[ERROR] 同步后仍不满足判据 → 拒绝启动。")
            print("  可能原因：当天包尚未发布（后端包 19:00 触发、约 20:43 完成；")
            print("            判据按交易日 22:00 起要求当天包）。")
            print("  处理：等到 22:00 后重跑本脚本，或先用 --check 观察。")
            return 4
    else:
        _step(2, "包已满足判据 → 跳过下载")
        _step(3, "复检 → 通过")

    if args.no_start:
        print("\n[--no-start] 同步完成，未启动服务。")
        return 0

    # ④ 启动
    _step(4, "启动后端（DATA_SOURCE=local）")
    env = dict(os.environ)
    env["DATA_SOURCE"] = "local"
    if args.readonly:
        env["RENDER_READ_ONLY"] = "1"
        print("  已加 RENDER_READ_ONLY=1（重活 loop 全关）")
    run_py = os.path.join(BACKEND_DIR, "run.py")
    if os.path.exists(run_py):
        cmd = [sys.executable, "run.py"]
    else:
        cmd = [sys.executable, "-m", "uvicorn", "app.main:app",
               "--host", "127.0.0.1", "--port", str(args.port)]
    print(f"  $ cd backend && {' '.join(cmd)}")
    print("  提示：前端另开一个终端跑 `cd frontend && npm run dev`。")
    print("  退出后端后如需再起，直接重跑本脚本即可（包已新鲜会跳过下载）。\n")
    try:
        return subprocess.call(cmd, cwd=BACKEND_DIR, env=env)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
