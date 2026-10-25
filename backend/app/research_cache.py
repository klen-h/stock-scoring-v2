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
def upsert_bars(by_code: dict) -> int:
    """把 `{code: [{date,open,high,low,close,volume}]}` 写回本机 `ohlc` 表。

    ★ 2026-10-22（egress）：给 `gate._load_gate_bars` 的**增量补数**用 ——
      闸门发现本机缓存末日 < 最新信号日时，只按 `date >= 末根` 补几根，
      然后写回本机 ⇒ 下一个进程直接命中，不再退化成"全历史 366k 行"。
      返回写入行数；任何异常由调用方兜住（缓存只是加速，不参与正确性）。
    """
    rows = []
    for c, bars in (by_code or {}).items():
        for b in bars or []:
            rows.append((c, b.get("date"), b.get("open"), b.get("high"),
                         b.get("low"), b.get("close"), b.get("volume")))
    if not rows:
        return 0
    conn = _conn()
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ohlc VALUES (?,?,?,?,?,?,?)", rows)
        return len(rows)
    finally:
        conn.close()


def _flow_from_local(conn) -> dict:
    rows = conn.execute("SELECT code, date, main_net, super_net, big_net, "
                        "main_pct, super_pct, close, pct_chg FROM flow "
                        "ORDER BY code, date").fetchall()
    out = {}
    for c, d, mn, sn, bn, mp, sp, cl, pc in rows:
        out.setdefault(c, []).append({
            "date": d, "main_net": mn, "super_net": sn, "big_net": bn,
            "main_pct": mp, "super_pct": sp, "close": cl, "pct_chg": pc})
    return out


def _top_up_flow(conn) -> dict:
    """把本机 flow 表按 PG 各 code 的 MAX(date) 增量补齐，返回最新映射。

    ★★ 2026-10-25（流量修复，10-22 bars 同款修法的补漏）：bars 的"缓存过旧 ⇒ 整批
      回源"黑洞当日用 `_top_up_bars` 修掉，**flow_map 漏修**——本机缓存过期 ⇒
      `load_flow_map()` 整表读 ≈8.7MB/次（探针实证 49 次/3 天、≤766MB 上限，
      即 10-23 egress 1.35GB 的主因）。本函数只过网两条小查询：
        ① `GROUP BY code` 的每码 MAX（~1500 行）——顺带发现**新增代码**；
        ② 需要补的 code 中 `date > 本机末根` 的行（常态 ≈ 全池 1 根 ≈ 1.5k 行）。
      全部成功 ⇒ 刷新 `flow_updated_at`（此后 max_age_h 内零查询）并返回最新映射；
      任何失败 ⇒ 抛异常，调用方回退旧整表读路径（fail-open）。
    """
    from app.database import db
    local_max = {r[0]: str(r[1] or "")[:10] for r in
                 conn.execute("SELECT code, MAX(date) AS dmax FROM flow GROUP BY code")}
    pg_rows = db.fetch("SELECT code, MAX(date) AS dmax FROM mainflow_history "
                       "GROUP BY code") or []
    pg_max = {str(r["code"]): str(r["dmax"] or "")[:10] for r in pg_rows}
    if not pg_max:
        raise RuntimeError("mainflow_history 为空/不可读（GROUP BY 无结果）")
    need_full = sorted(c for c, d in pg_max.items() if c not in local_max)
    need_tail = sorted(c for c, d in pg_max.items()
                       if c in local_max and d > local_max[c])
    CH = 120

    def _fetch(codes, since):
        out = []
        for i in range(0, len(codes), CH):
            chunk = codes[i:i + CH]
            # ★ join 必须作用在【列表】上：join("%s"*n) 会逐字符迭代字符串，
            #   产出 "%,s,%,s"（psycopg2 见到字面 % 即抛 ValueError——真实环境首跑实测）。
            marks = ",".join(["%s"] * len(chunk))
            sql = ("SELECT code, date, main_net, super_net, big_net, main_pct, "
                   "super_pct, close, pct_chg FROM mainflow_history "
                   f"WHERE code IN ({marks})")
            params = list(chunk)
            if since:
                sql += " AND date > %s"
                params.append(since)
            sql += " ORDER BY code, date"
            out += db.fetch(sql, tuple(params)) or []
        return out

    def _row(r):
        return (r["code"], str(r["date"]), r["main_net"], r["super_net"],
                r["big_net"], r["main_pct"], r["super_pct"], r["close"], r["pct_chg"])

    new_rows = []
    if need_full:
        # 全新代码（本机完全没有）：取这些码的**全量**行（每码 ~146 行，量小）
        new_rows += [_row(r) for r in _fetch(need_full, None)]
    if need_tail:
        # 尾码各自末根不同 ⇒ since 取本批最小（INSERT OR REPLACE 幂等，少量重取无害）
        since = min(local_max[c] for c in need_tail)
        new_rows += [_row(r) for r in _fetch(need_tail, since)]
    with conn:
        if new_rows:
            conn.executemany("INSERT OR REPLACE INTO flow VALUES (?,?,?,?,?,?,?,?,?)",
                             new_rows)
        # 全部 code 已补到与 PG 一致 ⇒ 刷新新鲜度（此后 max_age_h 内零查询）
        _meta_set(conn, "flow_updated_at", datetime.now().isoformat())
        _meta_set(conn, "flow_codes", str(max(len(pg_max), len(local_max))))
    out = _flow_from_local(conn)
    _log(f"flow top-up 完成：{len(out)} 只 / 新增行 {len(new_rows)}"
         f"（全新码 {len(need_full)}、尾码 {len(need_tail)}）——零整表读")
    return out


def flow_map(max_age_h: float = DEFAULT_MAX_AGE_H, force: bool = False) -> dict:
    """{code: [{date, main_net, super_net, big_net, main_pct, super_pct, close, pct_chg}]}。"""
    conn = _conn()
    try:
        if not force and max_age_h > 0 and _fresh(conn, "flow", max_age_h):
            out = _flow_from_local(conn)
            _log(f"命中本地缓存（{len(out)} 只，更新于 "
                 f"{_meta_get(conn, 'flow_updated_at')[:19]}）")
            return out
    finally:
        conn.close()

    # ★ 2026-10-25（流量修复）：缓存过期先试**增量 top-up**（两条小查询），
    #   失败才回退旧整表读（fail-open，行为不劣于修复前）。
    try:
        conn = _conn()
        try:
            return _top_up_flow(conn)
        finally:
            conn.close()
    except Exception as e:
        _log(f"flow top-up 失败，回退整表读: {type(e).__name__}: {str(e)[:80]}")

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
