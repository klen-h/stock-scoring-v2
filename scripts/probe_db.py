#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】数据库体检：容量 / 建连延迟 / **单次查询延迟**（迁移决策用）
================================================================================

用法（服务器上）：
    backend/.venv/bin/python scripts/probe_db.py

为什么单独测「复用连接后的查询」：
  `database.py` 是 **thread-local 长连接**（`self._local.pg_conn` + keepalive），
  所以「建连 596ms」只是**每个线程首次**的成本，不是每次查询。
  真正决定页面体验的是「复用连接后一次 SELECT 要多久」——
  实测本地家宽：建连 2576ms，但复用后 `SELECT 1` 仍要 **647ms** ⇒ 慢的是**往返**，不是握手。
  ⇒ 若单次查询 >300ms，页面查 5 次就是 1.5 秒，只能靠迁移解决；
     若 <150ms，优先做「合并查询 / 加缓存」比搬家划算。

判读阈值（经验值，不是硬标准）：
    < 100ms   很好，不用迁
    100~300ms 可接受，先优化查询次数
    > 300ms   建议迁到同地域（国内云 RDS）
================================================================================
"""
import os
import sys
import time

_BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, _BACKEND)


def _load_dotenv(path: str) -> int:
    n = 0
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                n += 1
    return n


_loaded = _load_dotenv(os.path.join(_BACKEND, ".env"))


def _ms(t0):
    return (time.perf_counter() - t0) * 1000


def main():
    from app.database import db

    print("=" * 84)
    print("数据库体检（从 backend/.env 载入 %d 个变量）" % _loaded)
    print("=" * 84)

    url = os.environ.get("DATABASE_URL", "")
    host = ""
    try:
        # 只打印主机名，不泄露密码
        host = url.split("@")[-1].split("/")[0] if "@" in url else "(SQLite 或本地)"
    except Exception:
        host = "?"
    print(f"\n【目标】{host}")

    # ── ① 容量 ──
    print("\n【容量】")
    try:
        r = db.fetch_one("SELECT pg_database_size(current_database()) AS sz")
        sz = float((r or {}).get("sz") or 0)
        print(f"  总大小 {sz/1048576:.1f} MB")
        # Supabase 免费版 500MB
        if sz > 0:
            pct = sz / (500 * 1048576) * 100
            print(f"  占 Supabase 免费额度(500MB) {pct:.0f}%" +
                  ("  ⚠️ 接近上限" if pct >= 75 else ""))
        rows = db.fetch("""
            SELECT relname AS t, pg_total_relation_size(relid) AS total, n_live_tup AS n
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC LIMIT 5""")
        for x in rows or []:
            print(f"    {x['t']:<28} {float(x['total'] or 0)/1048576:7.2f} MB  行≈{int(x['n'] or 0)}")
    except Exception as e:
        print(f"  失败: {type(e).__name__}: {str(e)[:80]}")

    # ── ② 建连 ──
    print("\n【建连】（每线程仅首次，参考值）")
    t0 = time.perf_counter()
    try:
        db.fetch_one("SELECT 1 AS x")
        print(f"  首次查询(含建连) {_ms(t0):.0f} ms")
    except Exception as e:
        print(f"  失败: {e}")
        return 2

    # ── ③ 复用连接后的查询（真正决定体验）──
    print("\n【单次查询】（复用连接，10 次）")
    ts = []
    for _ in range(10):
        t0 = time.perf_counter()
        db.fetch_one("SELECT 1 AS x")
        ts.append(_ms(t0))
    ts.sort()
    med = ts[len(ts) // 2]
    print(f"  SELECT 1    中位 {med:.0f} ms   最小 {ts[0]:.0f}   最大 {ts[-1]:.0f}")

    ts2 = []
    try:
        for _ in range(5):
            t0 = time.perf_counter()
            db.fetch_one("SELECT COUNT(*) AS n FROM ranking_history")
            ts2.append(_ms(t0))
        ts2.sort()
        print(f"  业务 COUNT  中位 {ts2[2]:.0f} ms")
        biz = ts2[2]
    except Exception as e:
        print(f"  业务查询失败: {type(e).__name__}: {str(e)[:60]}")
        biz = med

    # ── ④ 结论 ──
    page5 = (med + biz) / 2 * 5
    print("\n【结论】")
    print(f"  一页查询 5 次的估算耗时 ≈ {page5:.0f} ms")
    if biz > 300:
        print("  ⇒ ❌ 慢在**每次往返**，>300ms，建议迁到同地域（国内云 RDS）")
    elif biz > 100:
        print("  ⇒ ⚠️ 可接受区间（100~300ms）：先优化查询次数/加缓存，比搬家划算")
    else:
        print("  ⇒ ✅ 延迟健康，不需要为延迟迁移；容量另议")
    print("\n  ⚠️ 迁移前务必先解决：GitHub Actions 的日批也连这个库")
    print("     （daily-batch.yml / indicator-refresh.yml 都用 secrets.DATABASE_URL）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
