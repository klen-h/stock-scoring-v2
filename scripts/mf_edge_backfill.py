# -*- coding: utf-8 -*-
"""
================================================================================
【R2】主力节奏假设 · 历史回填实施（PLAN_MAINFORCE_EDGE.md §6；判据已冻结 v1.0）
================================================================================
产出 `mf_edge_daily(date, code, name, phase, signal, ready, flow5_amt, payload)`
—— 每个交易日 × 个股的主力状态 + 闸门就绪度（**全部用生产代码重算**：
mainforce_overlay + trade_gate.evaluate/summarize，序列截断到判定日）。

模式：
  --mode consistency [--sample N]   一致性闸：只跑与真实 mainforce_state 重叠的日期
                                    （逐票逐日比 phase/signal，≥95% 才准用回填数据）
  --mode backfill  --start --end    回填落库（断点续传：已完整落库的日期自动跳过）
  通用：--budget-minutes N（软预算，两个交易日之间停）；--sample N（0=全池）

数据面（R1 实测驱动的设计）：
  · bars：**本地 pack 优先**（零 Supabase egress），未覆盖代码回退 backtest_prices
    （仅抽样/缺口部分，egress 可控）；
  · flow：`load_flow_map(days=None)` 整表一次（本机 SQLite 缓存），逐票截断到判定日
    （与 state.FLOW_WINDOW_DAYS=20 同语义）；
  · regime：`regime_edge_check.regime_map` 同款回填（重叠段一致率 100% 先例），
    **monkeypatch 注入** `trade_gate._regime`；
  · 宇宙：判定日 backtest_prices 有价、`_is_stock_code`（0/3/6，剔 ETF/前缀符号）、
    bars ≥120 根（生产同门槛）。ETF 不落库（观察池/检验宇宙都不要它们）。

已知局限（如实声明，写进 PLAN §9 时引用）：
  · float_shares 用**当前** market_snapshot（历史逐日快照不可得）——chip 的绝对口径
    有微小漂移风险，由一致性闸兜底验证；
  · ready 的"当日矛盾计数"用当前值（cond_a = accum，矛盾只影响 reasons 文案不影响
    就绪度 ⇒ 数值上无影响，但 reasons 不入 payload）。
================================================================================
"""
import argparse
import json
import random
import threading
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.database import db                                    # noqa: E402
from app.mainforce.overlay import mainforce_overlay            # noqa: E402
from app.mainforce.flow import (                               # noqa: E402
    get_float_shares_from_snapshot, load_flow_map,
)
from app.mainforce import trade_gate                           # noqa: E402
from app.mainforce.trade_gate import summarize as gate_sum     # noqa: E402

BARS = 260          # 与 state.BARS_WINDOW 同款
FLOW_KEEP = 20      # 与 state.FLOW_WINDOW_DAYS 同款
OVERLAP_DAYS = ["2026-09-28", "2026-09-29", "2026-10-08", "2026-10-09"]


# ── ★ 落库目标 = 本地 SQLite（2026-10-13 架构变更）───────────────────────────
# 为什么不写 Supabase：回填数据的消费方只有**本地首跑脚本**；而写路径（941 条
# 逐行 INSERT/天）在 Supabase 连接抖动下反复阻塞（一晚两次事故：进程死 / 写挂死）。
# 写本地 = 零网络、executemany 秒写。已写入 Supabase 的完成日用 `--mode migrate`
# 一次迁回（~60-80MB 一次性 egress，远低于 40 小时重算）。
LOCAL_DB = Path(__file__).resolve().parents[1] / "backend" / "data" / "mf_edge_daily.db"
_local_conn = None


def ensure_table() -> None:
    conn = _lconn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS mf_edge_daily (
            date TEXT NOT NULL, code TEXT NOT NULL, name TEXT,
            phase TEXT, signal TEXT, ready INTEGER,
            flow5_amt REAL, flow5_amt_yuan REAL, flow_consec INTEGER,
            payload TEXT, UNIQUE (date, code));
        CREATE TABLE IF NOT EXISTS mf_edge_backfill_progress (
            date TEXT PRIMARY KEY, n INTEGER NOT NULL);
    """)
    conn.commit()


def _lconn():
    global _local_conn
    if _local_conn is None:
        import sqlite3
        _local_conn = sqlite3.connect(str(LOCAL_DB))
        _local_conn.row_factory = sqlite3.Row
        _local_conn.execute("PRAGMA journal_mode=WAL")
    return _local_conn


def save_day(day: str, rows: list) -> int:
    """整日写入（先删后插，幂等覆盖）+ 进度标记。★ 本地 executemany，零网络。"""
    conn = _lconn()
    with conn:
        conn.execute("DELETE FROM mf_edge_daily WHERE date = ?", (day,))
        conn.executemany(
            "INSERT OR REPLACE INTO mf_edge_daily (date, code, name, phase, signal, "
            "ready, flow5_amt, flow5_amt_yuan, flow_consec, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(day, r["code"], r["name"], r["phase"], r["signal"], r["ready"],
              r["flow5_amt"], r["flow5_amt_yuan"], r["flow_consec"],
              json.dumps(r["payload"], ensure_ascii=False)) for r in rows])
        conn.execute("INSERT OR REPLACE INTO mf_edge_backfill_progress (date, n) "
                     "VALUES (?, ?)", (day, len(rows)))
    return len(rows)


def done_dates() -> set:
    return {r["date"] for r in
            _lconn().execute("SELECT date FROM mf_edge_backfill_progress")}


def migrate_from_supabase(batch_days: int = 30) -> int:
    """已写入 Supabase 的完成日 → 本地（分批 + 重试 + progress 断点）。"""
    supa_done = {str(r["date"])[:10] for r in
                 (db.fetch("SELECT date FROM mf_edge_backfill_progress") or [])}
    local_done = done_dates()
    todo = sorted(supa_done - local_done)
    print(f"[migrate] Supabase 完成 {len(supa_done)} 日，本地已有 {len(local_done)} 日，"
          f"待迁移 {len(todo)} 日（一次性 egress）")
    total = 0
    for i in range(0, len(todo), batch_days):
        batch = todo[i:i + batch_days]
        rows = []
        for attempt in (1, 2, 3):
            try:
                rows = db.fetch("SELECT date, code, name, phase, signal, ready, "
                                "flow5_amt, flow5_amt_yuan, flow_consec, payload "
                                "FROM mf_edge_daily WHERE date = ANY(%s)",
                                (batch,)) or []
                break
            except Exception as e:
                print(f"  [migrate] 批 {i // batch_days + 1} 第 {attempt} 次失败"
                      f"（{type(e).__name__}），10s 后重试")
                time.sleep(10)
        conn = _lconn()
        with conn:
            for r in rows:
                payload = r.get("payload")
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except Exception:
                        payload = {}
                conn.execute(
                    "INSERT OR REPLACE INTO mf_edge_daily (date, code, name, phase, "
                    "signal, ready, flow5_amt, flow5_amt_yuan, flow_consec, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(r["date"])[:10], r["code"], r.get("name"), r.get("phase"),
                     r.get("signal"), r.get("ready"), r.get("flow5_amt"),
                     r.get("flow5_amt_yuan"), r.get("flow_consec"),
                     json.dumps(payload or {}, ensure_ascii=False)))
            for d in batch:
                n = sum(1 for r in rows if str(r["date"])[:10] == d)
                if n:
                    conn.execute("INSERT OR REPLACE INTO mf_edge_backfill_progress "
                                 "(date, n) VALUES (?, ?)", (d, n))
        total += len(rows)
        print(f"  [migrate] {batch[0]} ~ {batch[-1]}: {len(rows)} 行")
    print(f"[migrate] 完成，共迁移 {total} 行")
    return total


# ── 数据装载 ──────────────────────────────────────────────────────────────────
def regime_map() -> dict:
    """与 regime_edge_check.regime_map **逐行同款**（返回对象列表 → {date: state}）。"""
    from app.backtest.market_regime import detect_market_regime
    rows = db.fetch("SELECT date, open, high, low, close, volume FROM backtest_prices "
                    "WHERE code = 'sh000300' ORDER BY date ASC") or []
    bars = [{"date": str(r["date"])[:10], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"] or 0}
            for r in rows]
    return {s.date: s.state for s in detect_market_regime(bars)}


class BarsSource:
    """bars 装载：本地 pack 优先（零 egress），未覆盖代码走 **本地缓存的全历史**
    （一次性直读 + JSON 缓存 ⇒ 复跑零 egress）。

    ★★ 2026-10-13（egress 事故修复，pg_stat_statements 实测归因）：旧版回退查询
      （`WHERE code = ANY(...) AND date <= d`）有双重浪费——① SQL 侧**没有行数截断**
      （每票返回全历史 ~500 行，内存里才截 BARS=260）；② **逐日重复读**同一批票的
      同一段历史（pack 未覆盖的 ~9% 票 × 426 天）。实测两条语句合计 **~1.9GB**
      （正是用户看到的"昨天 1.557GB"的大头）。修法 = fallback 票**一次**直读
      （SQL 侧 `ROW_NUMBER()` 截断 260 根）→ 本地 JSON 缓存 → 逐日复用零 egress。
    """

    def __init__(self, end: str, fallback_codes: list):
        self._pack = None
        self._pack_codes = set()
        try:
            from app import pack_source
            p = Path(pack_source.pack_file())
            if p.exists():
                import sqlite3
                self._pack = sqlite3.connect(str(p))
                self._pack.row_factory = sqlite3.Row
                self._pack_codes = {r["code"] for r in
                                    self._pack.execute("SELECT code FROM codes")}
                print(f"[bars] 数据源 = 本地 pack（{len(self._pack_codes)} 只，零 egress）")
        except Exception as e:
            print(f"[bars] pack 不可用（{e}）⇒ 全部走 backtest_prices")
        self._fallback = self._load_fallback([c for c in fallback_codes
                                              if c not in self._pack_codes], end)

    @staticmethod
    def _load_fallback(codes: list, end: str) -> dict:
        """pack 未覆盖票的全历史 bars（每票 ≤260 根，date ≤ end），本地 JSON 缓存。"""
        import hashlib
        import json as _json
        if not codes:
            return {}
        cache_p = Path(__file__).resolve().parents[1] / "backend" / "data" / \
            "mf_edge_bars_cache.json"
        key = hashlib.md5(("|".join(sorted(codes)) + "@" + end).encode()).hexdigest()[:12]
        if cache_p.exists():
            try:
                blob = _json.loads(cache_p.read_text(encoding="utf-8"))
                if blob.get("key") == key:
                    print(f"[bars] fallback 缓存命中（{len(blob['data'])} 只，零 egress）")
                    return blob["data"]
            except Exception:
                pass
        out = {}
        for i in range(0, len(codes), 40):
            chunk = codes[i:i + 40]
            rows = []
            for attempt in (1, 2, 3):
                try:
                    # ★ SQL 侧截断：每票只取 date ≤ end 的最后 260 根（ROW_NUMBER）
                    rows = db.fetch("""
                        SELECT code, name, date, open, high, low, close, volume FROM (
                          SELECT code, name, date, open, high, low, close, volume,
                                 ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn
                          FROM backtest_prices
                          WHERE code = ANY(%s) AND date <= %s
                        ) t WHERE rn <= %s ORDER BY code, date
                    """, (chunk, end, BARS)) or []
                    break
                except Exception as e:
                    print(f"  [bars] 第 {i // 40 + 1} 块第 {attempt} 次失败"
                          f"（{type(e).__name__}），重试...")
                    time.sleep(5)
            for r in rows:
                c = r["code"]
                out.setdefault(c, {"name": r.get("name") or "", "bars": []})
                b = out[c]["bars"]
                if len(b) < BARS:
                    b.append({"date": str(r["date"])[:10], "open": r["open"],
                              "high": r["high"], "low": r["low"],
                              "close": r["close"], "volume": r["volume"]})
            print(f"  [bars] fallback {min(i + 40, len(codes))}/{len(codes)} 只"
                  f"（{len(rows)} 行）")
        try:
            cache_p.parent.mkdir(parents=True, exist_ok=True)
            cache_p.write_text(_json.dumps({"key": key, "data": out},
                                           ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"  [bars] 缓存写盘失败（不影响本次）: {e}")
        return out

    def bars_for(self, code: str, d: str):
        """→ (bars, name)；不在宇宙数据源里返回 ([], '')。"""
        if code in self._pack_codes:
            rows = self._pack.execute(
                "SELECT date, open, high, low, close, volume FROM klines "
                "WHERE code = ? ORDER BY date ASC", (code,)).fetchall()
            return ([dict(r) for r in rows if str(r["date"])[:10] <= d][-BARS:], "")
        fb = self._fallback.get(code)
        if fb:
            return ([b for b in fb["bars"] if b["date"] <= d][-BARS:], fb["name"])
        return ([], "")


# ── ★ db 调用看门狗（2026-10-12 第二次故障的修复）────────────────────────────
# 现象：SSL 死连接上的查询**无限阻塞**（socket 阻塞不抛异常 ⇒ database.py 的
# "异常才重置重连"自愈永远不触发；keepalives 只防死 peer，防不了"连接活着但
# 查询永远不回"）。修法 = 包装 db.execute/fetch：后台线程跑原调用，
# 主线程 join 超时后 `conn.cancel()`（psycopg2 官方的跨线程中断机制）⇒
# 阻塞中的调用立刻抛 QueryCanceled ⇒ database.py 既有自愈（重置+重试）接管。
_DB_TIMEOUT_S = 240


def _run_db(fn, args, kwargs, box):
    try:
        box["v"] = fn(*args, **kwargs)
    except Exception as e:
        box["e"] = e


def _db_call(fn, *args, **kwargs):
    conn = getattr(db._local, "pg_conn", None)     # 主线程的连接（阻塞中那只）
    box = {}
    th = threading.Thread(target=_run_db, args=(fn, args, kwargs, box), daemon=True)
    th.start()
    th.join(_DB_TIMEOUT_S)
    if th.is_alive():
        if conn is not None:
            try:
                conn.cancel()
            except Exception:
                pass
            th.join(30)
        if th.is_alive():
            raise TimeoutError(f"db 调用超 {args and ''}{_DB_TIMEOUT_S}s 且 cancel 无效")
    if "e" in box:
        raise box["e"]
    return box.get("v")


def _install_db_watchdog() -> None:
    db_execute_orig, db_fetch_orig = db.execute, db.fetch
    db.execute = lambda *a, **k: _db_call(db_execute_orig, *a, **k)
    db.fetch = lambda *a, **k: _db_call(db_fetch_orig, *a, **k)
    print("[watchdog] db 调用看门狗已装（超时 240s ⇒ cancel ⇒ 自愈重连）")


def _is_stock(code: str) -> bool:
    return (bool(code) and len(code) == 6 and code.isdigit() and code[0] in "036")


def universe_bulk(dates: list) -> dict:
    """批量取各判定日宇宙（30 天/批 + 本地 JSON 缓存）。

    ★ 2026-10-13：原为**每日一条** DISTINCT 查询——单条本身很小，但网络差时秒级
    ×426 天 ⇒ 回填从 15 小时劣化到 26 小时。批量化后：426 次 → 14 批，且复跑零查询。
    """
    import hashlib
    import json as _json
    cache_p = Path(__file__).resolve().parents[1] / "backend" / "data" / \
        "mf_edge_universe_cache.json"
    key = hashlib.md5(("|".join(dates)).encode()).hexdigest()[:12]
    out, done = {}, set()
    if cache_p.exists():
        try:
            blob = _json.loads(cache_p.read_text(encoding="utf-8"))
            if blob.get("key") == key:
                out = {d: v for d, v in (blob.get("data") or {}).items()}
                done = set(blob.get("done") or [])
                print(f"[universe] 缓存命中 {len(done)}/{len(dates)} 日（续装缺的）")
        except Exception:
            pass
    todo_dates = [d for d in dates if d not in done]
    for i in range(0, len(todo_dates), 30):
        chunk = todo_dates[i:i + 30]
        rows = []
        for attempt in (1, 2, 3):
            try:
                rows = db.fetch("SELECT DISTINCT date, code FROM backtest_prices "
                                "WHERE date = ANY(%s) AND LEFT(code, 1) IN ('0', '3', '6')",
                                (chunk,)) or []
                break
            except Exception as e:
                print(f"  [universe] 批 {i // 30 + 1} 第 {attempt} 次失败"
                      f"（{type(e).__name__}），10s 后重试")
                time.sleep(10)
        for r in rows:
            d = str(r["date"])[:10]
            if _is_stock(r["code"]):
                out.setdefault(d, []).append(r["code"])
        for d in chunk:
            out.setdefault(d, [])
            done.add(d)
        # ★ 每批增量存盘（2026-10-13：原只在**全部装载结束**时写 ⇒ 中途被杀 = 白装）
        _save_universe_cache(cache_p, key, out, done)
        print(f"  [universe] {len(done)}/{len(dates)} 日（本批 {len(rows)} 行）")
    for d in out:
        out[d] = sorted(out[d])
    return out


def _save_universe_cache(cache_p: Path, key: str, out: dict, done: set) -> None:
    import json as _json
    try:
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        cache_p.write_text(_json.dumps({"key": key, "data": out,
                                        "done": sorted(done)},
                                       ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  [universe] 缓存写盘失败（不影响本次）: {e}")


def universe_at(day: str, by_day: dict = None) -> list:
    """判定日宇宙（优先内存 by_day；无缓存时回落单日查询）。"""
    if by_day is not None:
        return by_day.get(day, [])
    rows = db.fetch("SELECT DISTINCT code FROM backtest_prices "
                    "WHERE date = %s AND LEFT(code, 1) IN ('0', '3', '6')", (day,)) or []
    return sorted(r["code"] for r in rows if _is_stock(r["code"]))


def compute_day(day: str, sample: int, seed: int, bars_src: BarsSource,
                flow_map: dict, fs_map: dict, names: dict, regime_d: str,
                by_day: dict = None) -> tuple:
    """重算单个判定日 → (rows, stats)。生产代码路径，序列截断到 day。"""
    codes = universe_at(day, by_day)
    if sample and sample < len(codes):
        codes = sorted(random.Random(seed).sample(codes, sample))
    t = time.time()
    rows, n_skip = [], 0
    for code in codes:
        bars, _bname = bars_src.bars_for(code, day)
        if len(bars) < 120:                    # 生产同门槛（overlay 最低要求）
            n_skip += 1
            continue
        name = names.get(code) or code
        try:
            ov = mainforce_overlay(bars, flow_rows=_flow_trunc(flow_map, code, day),
                                   float_shares=fs_map.get(code), regime=regime_d)
        except Exception:
            n_skip += 1
            continue
        if not ov:
            n_skip += 1
            continue
        mf = {"code": code, "name": name, "date": day, "phase": ov.get("phase"),
              "signal": ov.get("signal"), "mult": ov.get("mult", 1.0),
              "chip": ov.get("chip") or {}, "flow5_amt": ov.get("flow5_amt"),
              "flow5_amt_yuan": ov.get("flow5_amt_yuan"),
              "flow_consec": ov.get("flow_consec")}
        try:
            ready = gate_sum(trade_gate.evaluate(code, mf=mf)).get("ready")
        except Exception:
            ready = None
        close = bars[-1].get("close")
        rows.append({"code": code, "name": name, "phase": ov.get("phase"),
                     "signal": ov.get("signal"), "ready": ready,
                     "flow5_amt": ov.get("flow5_amt"),
                     "flow5_amt_yuan": ov.get("flow5_amt_yuan"),
                     "flow_consec": ov.get("flow_consec"),
                     "payload": {"chip": ov.get("chip") or {},
                                 "close": close, "regime": regime_d,
                                 "mult": ov.get("mult", 1.0)}})
    return rows, {"universe": len(codes), "skip": n_skip,
                  "seconds": round(time.time() - t, 1)}


def _flow_trunc(flow_map: dict, code: str, day: str) -> list:
    rows = flow_map.get(code) or []
    return [r for r in rows if str(r.get("date"))[:10] <= day][-FLOW_KEEP:]


def _names() -> dict:
    rows = db.fetch("SELECT code, MAX(name) AS name FROM backtest_prices "
                    "GROUP BY code") or []
    return {r["code"]: (r.get("name") or "") for r in rows}


def universe_union(dates: list) -> list:
    rows = db.fetch("SELECT DISTINCT code FROM backtest_prices "
                    "WHERE date = ANY(%s) AND LEFT(code, 1) IN ('0', '3', '6')",
                    (dates,)) or []
    return sorted(r["code"] for r in rows if _is_stock(r["code"]))


def load_flow_chunked(codes: list, end: str, chunk: int = 80) -> dict:
    """直读 mainflow_history（分小块 + 重试 + 本地 JSON 缓存）。

    为什么不走 `flow.load_flow_map`：整表/大窗 SQL 两次实测失败（statement timeout、
    SSL 连接被断）。本函数：每块 80 只、只取 4 列、`date <= end`、失败重试 2 次；
    结果缓存到 `backend/data/mf_edge_flow_cache.json`（键含 end ⇒ 复跑零 egress）。"""
    import hashlib
    import json as _json
    cache_p = Path(__file__).resolve().parents[1] / "backend" / "data" / \
        "mf_edge_flow_cache.json"
    key = hashlib.md5(("|".join(codes) + "@" + end).encode()).hexdigest()[:12]
    if cache_p.exists():
        try:
            blob = _json.loads(cache_p.read_text(encoding="utf-8"))
            if blob.get("key") == key:
                print(f"  [flow] 命中本地缓存（{len(blob['data'])} 只，零 egress）")
                return blob["data"]
        except Exception:
            pass
    out = {}
    for i in range(0, len(codes), chunk):
        part_codes = codes[i:i + chunk]
        rows = []
        for attempt in (1, 2, 3):
            try:
                rows = db.fetch(
                    "SELECT code, date, main_net, main_pct FROM mainflow_history "
                    "WHERE code = ANY(%s) AND date <= %s ORDER BY code, date",
                    (part_codes, end)) or []
                break
            except Exception as e:
                print(f"  [flow] 第 {i // chunk + 1} 块第 {attempt} 次失败"
                      f"（{type(e).__name__}），重试...")
                time.sleep(5)
        for r in rows:
            out.setdefault(r["code"], []).append(
                {"date": str(r["date"])[:10], "main_net": r["main_net"],
                 "main_pct": r["main_pct"]})
        print(f"  [flow] {min(i + chunk, len(codes))}/{len(codes)} 只"
              f"（累计 {len(out)}，{len(rows)} 行）")
    try:
        cache_p.parent.mkdir(parents=True, exist_ok=True)
        cache_p.write_text(_json.dumps({"key": key, "data": out},
                                       ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        print(f"  [flow] 缓存写盘失败（不影响本次）: {e}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["consistency", "backfill", "migrate"],
                    required=True)
    ap.add_argument("--start", default="2025-01-01")
    ap.add_argument("--end", default="2026-10-09")
    ap.add_argument("--sample", type=int, default=0, help="0 = 全池")
    ap.add_argument("--seed", type=int, default=20261011)
    ap.add_argument("--budget-minutes", type=int, default=0,
                    help="软预算（分钟），两个交易日之间检查；0 = 不限")
    args = ap.parse_args()

    ensure_table()
    _install_db_watchdog()
    t0 = time.time()
    dates = OVERLAP_DAYS if args.mode == "consistency" \
        else trading_days_range(args.start, args.end)
    codes_u = universe_union(dates)
    print(f"[R2] 装载共享数据（regime / flow / float_shares / names，一次性；"
          f"日期 {len(dates)} 个、宇宙并集 {len(codes_u)} 只）...")
    reg = regime_map()
    # ★ 2026-10-13（CPU 7% / 进程 14s / 单日 281s 的归因）：`_today_contradictions`
    #   的 30s 缓存 ⇒ 每天 ~10 次重读矛盾表，网络差时每次几十秒（实测吃满 250s/日）。
    #   它只进 evaluate 的 reasons 文案、**不影响 ready 数值** ⇒ 启动读一次、之后恒定。
    _contra0 = trade_gate._today_contradictions()
    trade_gate._today_contradictions = lambda: _contra0
    print(f"[R2] 矛盾计数快照 = {_contra0}（之后恒定，每日免 10 次网络查询）")
    end = args.end if args.mode == "backfill" else max(OVERLAP_DAYS)
    flow_map = load_flow_chunked(codes_u, end=end)
    fs_map = get_float_shares_from_snapshot()
    names = _names()
    bars_src = BarsSource(end=end, fallback_codes=codes_u)
    print(f"[R2] regime {len(reg)} 日 ｜ flow {len(flow_map)} 只 ｜ "
          f"fs {len(fs_map)} 只 ｜ 装载 {time.time() - t0:.0f}s")

    if args.mode == "consistency":
        return _consistency(args, reg, flow_map, fs_map, names, bars_src)
    if args.mode == "migrate":
        return migrate_from_supabase()
    return _backfill(args, reg, flow_map, fs_map, names, bars_src)


def _consistency(args, reg, flow_map, fs_map, names, bars_src) -> int:
    """一致性闸：重叠日逐票逐日比 phase/signal（≥95% 才准用回填数据）。"""
    print(f"\n=== 一致性闸（重叠日 {OVERLAP_DAYS}，sample={args.sample or '全池'}）===")
    by_day = universe_bulk(OVERLAP_DAYS)
    tot_m = tot_n = 0
    for d in OVERLAP_DAYS:
        real = {r["code"]: r for r in
                (db.fetch("SELECT code, phase, signal FROM mainforce_state "
                          "WHERE date = %s", (d,)) or [])}
        trade_gate._regime = lambda d=d: reg.get(d, "")      # 逐日注入
        rows, st = compute_day(d, args.sample, args.seed, bars_src,
                               flow_map, fs_map, names, reg.get(d, ""), by_day)
        m = n = 0
        for r in rows:
            x = real.get(r["code"])
            if x:
                n += 1
                m += (x.get("phase") == r["phase"]) + (x.get("signal") == r["signal"])
        rate = (m / (2 * n)) if n else 0.0
        tot_m += m
        tot_n += 2 * n
        print(f"  {d}: 宇宙 {st['universe']} ｜ 耗时 {st['seconds']}s ｜ "
              f"可比 {n} ｜ phase+signal 一致率 {rate:.1%}")
    rate = (tot_m / tot_n) if tot_n else 0.0
    print(f"\n  总一致率 = {rate:.1%}（闸 = ≥95%）⇒ "
          f"{'✅ 通过：回填数据可用' if rate >= 0.95 else '❌ 未过闸：回填数据不得用于检验'}")
    return 0 if rate >= 0.95 else 1


def _backfill(args, reg, flow_map, fs_map, names, bars_src) -> int:
    days = trading_days_range(args.start, args.end)
    done = done_dates()
    todo = [d for d in days if d not in done]
    print(f"\n=== 回填 {args.start} ~ {args.end}：{len(days)} 个交易日，"
          f"已完成 {len(days) - len(todo)}（断点续传），待跑 {len(todo)} ===")
    by_day = universe_bulk(todo)      # 只需待跑日的宇宙（30 天/批 + 本地缓存）
    budget_s = args.budget_minutes * 60
    t0 = time.time()
    for i, d in enumerate(todo, 1):
        if budget_s and time.time() - t0 > budget_s:
            print(f"[R2] 达软预算（{args.budget_minutes} 分钟）⇒ 停在 {d}（本次已完成 {i - 1} 日），"
                  f"重跑同命令续传")
            break
        trade_gate._regime = lambda d=d: reg.get(d, "")      # 逐日注入
        # ★ 整日容错（2026-10-12 实测教训：单日任何异常不得带崩进程——
        #   该日不写 progress ⇒ 断点续传自动重跑，语义正确）
        try:
            rows, st = compute_day(d, args.sample, args.seed, bars_src,
                                   flow_map, fs_map, names, reg.get(d, ""), by_day)
            n = save_day(d, rows)
        except Exception as e:
            print(f"  [{i}/{len(todo)}] {d}: ★ 单日失败（{type(e).__name__}: {e}）"
                  f"⇒ 跳过，续传时自动重跑")
            time.sleep(10)
            continue
        rd = {}
        for r in rows:
            rd[r["ready"]] = rd.get(r["ready"], 0) + 1
        print(f"  [{i}/{len(todo)}] {d}: 落库 {n}（宇宙 {st['universe']} 跳过 {st['skip']}）"
              f"｜ {st['seconds']}s ｜ ready {dict(sorted((rd or {}).items()))}")
    print(f"[R2] 本次运行结束，累计耗时 {(time.time() - t0) / 60:.1f} 分钟")
    return 0


def trading_days_range(start: str, end: str) -> list:
    rows = db.fetch("SELECT DISTINCT date FROM backtest_prices "
                    "WHERE date >= %s AND date <= %s ORDER BY date", (start, end)) or []
    return [str(r["date"])[:10] for r in rows]


if __name__ == "__main__":
    sys.exit(main())
