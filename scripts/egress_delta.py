#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】即时查询"自上次快照以来"的数据库流量增量 —— 本地开发/验证用
================================================================================

为什么需要（2026-11-21）：
  `egress_monitor.snapshot()` 是**每天日批末尾**写一次，第二天才能差分。
  但周末本地修复问题、想当场验证"我这次跑到底烧了多少流量"时，
  等不到第二天快照。本脚本直接对比 **pg_stat_statements 当前累计值**
  与本脚本自己存在本地的基线文件 ⇒ 秒级得到这次的增量。

用法：
    # 1) 跑任务【之前】打基线
    python scripts/egress_delta.py --mark

    # 2) 跑你的日批 / 测试 / 优化验证
    python scripts/daily_batch.py --only=...    # 或任何操作

    # 3) 跑【之后】看增量
    python scripts/egress_delta.py

    # 换库或换项目时清基线
    python scripts/egress_delta.py --reset

口径边界（与 egress_monitor 一致）：
  · rows 是返回行数，不是字节。字节 = 行数 × 平均行宽，误差 ±30%
  · 增量按 queryid 差分；统计被重置/淘汰导致负值时按 0 处理
  · 基线存在 backend/data/egress_delta_baseline.json（gitignore 内）
  · 基线绑定数据库连接串的 ref（防切库后拿旧基线对比出新库的错数字）
================================================================================
"""
import argparse
import io
import json
import os
import sys
import urllib.parse
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
BASELINE = os.path.join(BACKEND, "data", "egress_delta_baseline.json")
_ENV = os.path.join(BACKEND, ".env")

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as e:
    # 控制台不支持 UTF-8 时降级为默认编码即可，但必须留痕（GUARDRAILS #11）
    print("[egress_delta] stdout reconfigure 失败: %s" % str(e)[:60],
          file=sys.stderr)


def _load_env():
    if os.path.exists(_ENV):
        for line in io.open(_ENV, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
import psycopg2

DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()


def _ref() -> str:
    """从连接串取出项目 ref，作为基线身份标识。"""
    try:
        u = urllib.parse.urlparse(DATABASE_URL)
        return (urllib.parse.unquote(u.username or "").split(".")[-1]
                + "@" + (u.hostname or ""))
    except Exception:
        return "unknown"


def _connect():
    p = urllib.parse.urlparse(DATABASE_URL)
    kw = {}
    if p.hostname and p.hostname.endswith("supabase.com"):
        # ★ 本机 DNS（阿里 223.5.5.5）解析不了 pooler 域名，连接串里的
        #   hostaddr 参数由 libpq 处理；这里直接透传整串即可（psycopg2 支持 URI）。
        pass
    return psycopg2.connect(DATABASE_URL, connect_timeout=20)


def _fetch_counters():
    """pg_stat_statements 偶发 SSL 断连（实测老库会掐），重试 3 次。"""
    import time
    last = None
    for attempt in range(3):
        c = None
        try:
            c = _connect()
            c.autocommit = True
            cur = c.cursor()
            # ★ 排除自观测噪音：本脚本自己查 pg_stat_statements 也会产生
            #   几千行返回，pgbouncer 认证是平台行为 —— 都不属于业务流量，
            #   计入会让"优化效果"验证失真。
            cur.execute("""
                SELECT queryid, calls, rows, total_exec_time AS ms,
                       regexp_replace(query, '\\s+', ' ', 'g') AS q
                FROM pg_stat_statements
                WHERE queryid IS NOT NULL AND rows > 0
                  AND query NOT ILIKE '%pg_stat_statements%'
                  AND query NOT ILIKE '%pgbouncer%'
                ORDER BY rows DESC
            """)
            out = {}
            for qid, calls, rows, ms, q in cur.fetchall():
                out[str(qid)] = {"calls": int(calls or 0), "rows": int(rows or 0),
                                 "ms": float(ms or 0), "q": (q or "")[:160]}
            c.close()
            return out
        except Exception as e:
            last = e
            if c is not None:
                try:
                    c.close()
                except Exception as e:
                    # 关闭失败只是清理瑕疵，不影响主流程，但要可见
                    print("[egress_delta] 连接关闭失败: %s" % str(e)[:60],
                          file=sys.stderr)
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise last


def _save(cur):
    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
    with io.open(BASELINE, "w", encoding="utf-8") as f:
        json.dump({"ref": _ref(), "ts": datetime.now().isoformat(timespec="seconds"),
                   "counters": cur}, f, ensure_ascii=False)


def _load():
    if not os.path.exists(BASELINE):
        return None
    try:
        with io.open(BASELINE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser(description="即时流量增量（对比 pg_stat_statements 基线）")
    ap.add_argument("--mark", action="store_true", help="打基线（跑任务之前）")
    ap.add_argument("--reset", action="store_true", help="清除基线")
    args = ap.parse_args()

    if args.reset:
        if os.path.exists(BASELINE):
            os.remove(BASELINE)
            print("基线已清除")
        else:
            print("本无基线")
        return

    if not DATABASE_URL.startswith("postgresql://"):
        print("FAIL: DATABASE_URL 缺失或非 PostgreSQL")
        sys.exit(1)

    cur = _fetch_counters()
    ref = _ref()

    if args.mark:
        _save(cur)
        print("基线已记录: %s | %d 条语句 | %s"
              % (ref, len(cur), datetime.now().strftime("%H:%M:%S")))
        return

    base = _load()
    if not base:
        print("尚无基线。先跑: python scripts/egress_delta.py --mark")
        sys.exit(1)
    if base.get("ref") != ref:
        print("FAIL: 基线属于 [%s]，当前是 [%s]" % (base.get("ref"), ref))
        print("       切库了 —— 请 python scripts/egress_delta.py --reset 后重新 --mark")
        sys.exit(1)

    rows = calls = 0
    ms = 0.0
    top = []
    for qid, now in cur.items():
        old = base["counters"].get(qid)
        if not old:
            # 新出现的语句：从 0 算（保守，可能高估）
            dr, dc, dm = now["rows"], now["calls"], now["ms"]
        else:
            dr = max(now["rows"] - old["rows"], 0)
            dc = max(now["calls"] - old["calls"], 0)
            dm = max(now["ms"] - old["ms"], 0)
        rows += dr
        calls += dc
        ms += dm
        if dr > 0:
            top.append((dr, dc, now["q"]))

    print("=== 增量（%s → 现在）===" % base.get("ts", "?"))
    print("  返回行数: {:,}".format(rows))
    print("  调用次数: {:,}".format(calls))
    print("  累计耗时: %.1fs" % (ms / 1000))
    if calls:
        print("  平均每次: {:,} 行".format(rows // calls))
    print()
    print("=== top 10 ===")
    for dr, dc, q in sorted(top, reverse=True)[:10]:
        print("  {:,}行 {:>6,}次  {}".format(dr, dc, q[:90]))


if __name__ == "__main__":
    main()
