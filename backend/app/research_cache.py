"""
================================================================================
【文件作用】研究脚本的本地数据缓存（避免每次跑脚本都整表拉远程库）
================================================================================
背景（2026-09-12，egress 治理第三步）：
  `scripts/mainforce_factor_backtest.py` 的 `load_ohlc_all()` + `load_flow_map()`、
  `scripts/strategy_mainforce_filter_test.py` 的逐股 K 线、`scripts/factor_analysis.py`
  的批量日线——每次运行都要把 backtest_prices（48.9 万行 / 82MB 表）与
  mainflow_history（8.4 万行 / 21MB 表）整表过一遍网。pg_stat_statements 里
  "返回 40 万行/次"的那几条就是这么来的（21 天累计 ~90MB/次 × 4 次）。
  而这类数据是"历史冻结、只追加"的，本机跑研究完全没必要每次回源。

做法：落到本机 SQLite（`backend/data/research-cache.db`），默认 24 小时内复用；
  K 线**优先从数据包取**（本地 sqlite，零 egress；包内每只 ~750 根 = 3 年，与回测口径一致），
  包未覆盖的代码再从库补（分块 IN 查询）。`force=True` / `max_age_h=0` 强制重建。

对外函数：
  ohlc_all(max_age_h=24, force=False) -> {code: [{date,open,high,low,close,volume}]} 升序
  flow_map(max_age_h=24, force=False) -> {code: [{date,main_net,...}]} 升序
  stats() -> 缓存文件信息（体积/行数/更新时间，供脚本打印与排障）
================================================================================
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

from app.database import db

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.dirname(_APP_DIR)
_PATH = os.path.join(_BACKEND_DIR, "data", "research-cache.db")

DEFAULT_MAX_AGE_H = 24


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    conn = sqlite3.connect(_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlc (
            code TEXT NOT NULL, date TEXT NOT NULL, open REAL, high REAL,
            low REAL, close REAL, volume REAL, PRIMARY KEY (code, date)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS flow (
            code TEXT NOT NULL, date TEXT NOT NULL, main_net REAL, super_net REAL,
            big_net REAL, main_pct REAL, super_pct REAL, close REAL, pct_chg REAL,
            PRIMARY KEY (code, date)
        )""")
    conn.execute("CREATE TABLE IF NOT EXISTS cache_meta (key TEXT PRIMARY KEY, value TEXT)")
    return conn


def _meta_get(conn, key: str) -> str:
    row = conn.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
    return (row[0] if row else "") or ""


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO cache_meta (key, value) VALUES (?, ?)",
                 (key, value))


def _fresh(conn, key: str, max_age_h: float) -> bool:
    ts = _meta_get(conn, f"{key}_updated_at")
    if not ts:
        return False
    try:
        when = datetime.fromisoformat(ts)
    except ValueError:
        return False
    return datetime.now() - when <= timedelta(hours=max_age_h)


def _log(msg: str) -> None:
    print(f"[research_cache] {msg}")


# ── K 线 ────────────────────────────────────────────────────────────────────
def _pack_bars(code: str) -> list:
    """从本地数据包取某只日线（零 egress）。包不可用/未覆盖返回 []。"""
    try:
        from app import pack_source
        if not pack_source.enabled():
            return []
        return pack_source.get_prices(code) or []
    except Exception:
        return []


def ohlc_all(max_age_h: float = DEFAULT_MAX_AGE_H, force: bool = False,
             use_pack: bool = True) -> dict:
    """{code: [{date, open, high, low, close, volume}]}（升序，按 code 排序）。

    首次/过期时重建：K线优先从数据包取（零 Supabase 流量），未覆盖的再从库补。
    """
    conn = _conn()
    try:
        if not force and max_age_h > 0 and _fresh(conn, "ohlc", max_age_h):
            rows = conn.execute("SELECT code, date, open, high, low, close, volume "
                                "FROM ohlc ORDER BY code, date").fetchall()
            out = {}
            for c, d, o, h, l, cl, v in rows:
                out.setdefault(c, []).append({"date": d, "open": o, "high": h,
                                              "low": l, "close": cl, "volume": v})
            _log(f"命中本地缓存 {_PATH}（{len(out)} 只 / {len(rows)} 行，"
                 f"更新于 {_meta_get(conn, 'ohlc_updated_at')[:19]}）")
            return out
    finally:
        conn.close()

    codes = [r["code"] for r in db.fetch(
        "SELECT DISTINCT code FROM backtest_prices ORDER BY code")]
    _log(f"重建缓存：目标 {len(codes)} 只（源码：数据包优先 → 库补齐）")
    by_code, miss = {}, []
    t0 = time.time()
    if use_pack:
        for i, c in enumerate(codes, 1):
            bars = _pack_bars(c)
            if bars:
                by_code[c] = bars
            else:
                miss.append(c)
            if i % 500 == 0:
                _log(f"  数据包 {i}/{len(codes)}（命中 {len(by_code)}）")
        _log(f"数据包命中 {len(by_code)}/{len(codes)}，缺 {len(miss)} 只要从库补")
    else:
        miss = list(codes)

    CH = 120
    for i in range(0, len(miss), CH):
        chunk = miss[i:i + CH]
        rows = db.fetch(
            "SELECT code, date, open, high, low, close, volume FROM backtest_prices "
            "WHERE code = ANY(%s) ORDER BY code, date ASC", (chunk,))
        for r in rows:
            by_code.setdefault(r["code"], []).append({
                "date": str(r["date"]), "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"]})
        _log(f"  库补齐 {min(i + CH, len(miss))}/{len(miss)}")

    conn = _conn()
    try:
        with conn:                                  # 事务：失败则保留旧缓存
            conn.execute("DELETE FROM ohlc")
            conn.executemany("INSERT OR REPLACE INTO ohlc VALUES (?,?,?,?,?,?,?)",
                             [(c, b["date"], b["open"], b["high"], b["low"],
                               b["close"], b["volume"])
                              for c, bars in by_code.items() for b in bars])
            _meta_set(conn, "ohlc_updated_at", datetime.now().isoformat())
            _meta_set(conn, "ohlc_codes", str(len(by_code)))
    finally:
        conn.close()
    n = sum(len(v) for v in by_code.values())
    _log(f"缓存已重建：{len(by_code)} 只 / {n} 行（{time.time() - t0:.0f}s）→ {_PATH}")
    return by_code


def ohlc_for(codes, max_age_h: float = DEFAULT_MAX_AGE_H) -> dict:
    """{code: bars} —— **只取指定代码**的日线（升序），本机优先、零回源为常态。

    ★ 2026-09-18（审查 P2-㉔，egress）：研究脚本原先走
      `backtest.strategies._load_prices_map(codes)` —— 它在 pack 未命中后是**一条不分块的
      `code IN (...)` 直连回源**，而脚本**未传 `start`** ⇒ 读**全历史**
      （pg_stat_statements：12 次 / 22.7 万行每次，即每条约 300 只 × 750 根 ≈ 8MB/次）。
      改走本函数：先读本机 `ohlc` 表（**零 egress**）；只有本机确实没有的代码才
      「数据包 → 回源库」，并**回填本机**（下次即命中）。
      相比 `ohlc_all()`：按需读、不进全量内存（全量 ≈ 154 万行），语义等价。
    """
    want = sorted({str(c) for c in codes if c})
    if not want:
        return {}
    out = {}
    conn = _conn()
    try:
        CH = 300
        for i in range(0, len(want), CH):
            chunk = want[i:i + CH]
            marks = ",".join("?" * len(chunk))
            for c, d, o, h, l, cl, v in conn.execute(
                    f"SELECT code, date, open, high, low, close, volume FROM ohlc "
                    f"WHERE code IN ({marks}) ORDER BY code, date", chunk):
                out.setdefault(c, []).append({"date": d, "open": o, "high": h,
                                              "low": l, "close": cl, "volume": v})
        missing = [c for c in want if c not in out]
        if not missing:
            _log(f"ohlc_for 命中本机缓存 {len(out)} 只（零 egress）")
            return out
        _log(f"ohlc_for 本机缺 {len(missing)}/{len(want)} 只 → 数据包优先，其次回源")
        new_rows, still = [], []
        for c in missing:                       # ① 数据包（零 egress）
            bars = _pack_bars(c)
            if bars:
                out[c] = bars
                new_rows += [(c, b.get("date"), b.get("open"), b.get("high"),
                              b.get("low"), b.get("close"), b.get("volume"))
                             for b in bars]
            else:
                still.append(c)
        if still:                               # ② 仍未覆盖 → 回源（分块，防超参数上限）
            for i in range(0, len(still), CH):
                chunk = still[i:i + CH]
                for r in db.fetch(
                        "SELECT code, date, open, high, low, close, volume "
                        "FROM backtest_prices WHERE code = ANY(%s) "
                        "ORDER BY code, date ASC", (chunk,)) or []:
                    out.setdefault(r["code"], []).append({
                        "date": r["date"], "open": r["open"], "high": r["high"],
                        "low": r["low"], "close": r["close"], "volume": r["volume"]})
                _log(f"  库补齐 {min(i + CH, len(still))}/{len(still)}")
            for c in still:
                new_rows += [(c, b.get("date"), b.get("open"), b.get("high"),
                              b.get("low"), b.get("close"), b.get("volume"))
                             for b in (out.get(c) or [])]
        if new_rows:                            # 回填本机 → 下次直接命中
            try:
                with conn:                      # 事务：失败不影响本次返回
                    conn.executemany("INSERT OR REPLACE INTO ohlc VALUES (?,?,?,?,?,?,?)",
                                     new_rows)
                # ★ 刻意**不改** `ohlc_updated_at`：回填只是"补几行"，不该让
                #   `ohlc_all()` 的全量缓存被判为"刚更新"而延后重建。
            except Exception as e:
                _log(f"回填本机缓存失败（不影响本次结果）: {e}")
        return out
    finally:
        conn.close()


# ── 资金流 ──────────────────────────────────────────────────────────────────
def flow_map(max_age_h: float = DEFAULT_MAX_AGE_H, force: bool = False) -> dict:
    """{code: [{date, main_net, super_net, big_net, main_pct, super_pct, close, pct_chg}]}。"""
    conn = _conn()
    try:
        if not force and max_age_h > 0 and _fresh(conn, "flow", max_age_h):
            rows = conn.execute("SELECT code, date, main_net, super_net, big_net, "
                                "main_pct, super_pct, close, pct_chg FROM flow "
                                "ORDER BY code, date").fetchall()
            out = {}
            for c, d, mn, sn, bn, mp, sp, cl, pc in rows:
                out.setdefault(c, []).append({
                    "date": d, "main_net": mn, "super_net": sn, "big_net": bn,
                    "main_pct": mp, "super_pct": sp, "close": cl, "pct_chg": pc})
            _log(f"命中本地缓存（{len(out)} 只 / {len(rows)} 行，"
                 f"更新于 {_meta_get(conn, 'flow_updated_at')[:19]}）")
            return out
    finally:
        conn.close()

    from app.mainforce.flow import load_flow_map
    _log("重建缓存：从库读 mainflow_history（整表一次）")
    src = load_flow_map() or {}
    rows = []
    for c, items in src.items():
        for r in items:
            rows.append((c, str(r.get("date")), r.get("main_net"), r.get("super_net"),
                         r.get("big_net"), r.get("main_pct"), r.get("super_pct"),
                         r.get("close"), r.get("pct_chg")))
    conn = _conn()
    try:
        with conn:
            conn.execute("DELETE FROM flow")
            conn.executemany("INSERT OR REPLACE INTO flow VALUES (?,?,?,?,?,?,?,?,?)", rows)
            _meta_set(conn, "flow_updated_at", datetime.now().isoformat())
            _meta_set(conn, "flow_codes", str(len(src)))
    finally:
        conn.close()
    _log(f"缓存已重建：{len(src)} 只 / {len(rows)} 行 → {_PATH}")
    return src


def stats() -> dict:
    """缓存文件现状（体积/行数/更新时间），供脚本打印与排障。"""
    if not os.path.exists(_PATH):
        return {"exists": False, "path": _PATH}
    conn = _conn()
    try:
        return {
            "exists": True,
            "path": _PATH,
            "size_mb": round(os.path.getsize(_PATH) / 1048576, 1),
            "ohlc_rows": conn.execute("SELECT COUNT(*) FROM ohlc").fetchone()[0],
            "flow_rows": conn.execute("SELECT COUNT(*) FROM flow").fetchone()[0],
            "ohlc_updated_at": _meta_get(conn, "ohlc_updated_at"),
            "flow_updated_at": _meta_get(conn, "flow_updated_at"),
        }
    finally:
        conn.close()


if __name__ == "__main__":          # 手动预热：python -m app.research_cache
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
    ohlc_all(force=True)
    flow_map(force=True)
    print(json.dumps(stats(), ensure_ascii=False, indent=2))
