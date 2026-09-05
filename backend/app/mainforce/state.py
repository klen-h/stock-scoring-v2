# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】主力行为日批状态表：全池计算 mainforce overlay → mainforce_state
================================================================================

为什么是"日批 + 查表"而不是端点现算：
  - Render 0.1 CPU：筹码分布对每只要滚 750 根 × 150 档，请求路径算不动
  - 数据依赖都是盘后落库的（backtest_prices 16:10 回填、mainflow_history 17:00）
  - 主力行为是慢信号（筹码结构日内几乎不变），日频足够

表：mainforce_state(code, name, date, phase, signal, mult, chip_json, flow5_amt)
  UNIQUE(code, date) 幂等。score_top 一次 IN 查询挂到榜单。

调度：mainforce_state_refresh_loop（17:30，资金流日更 17:00 之后）。
================================================================================
"""

import json
import time
from datetime import datetime

from app.database import db

from app.mainforce.flow import (get_float_shares_from_snapshot,
                                load_flow_map)
from app.mainforce.overlay import mainforce_overlay

# 日线回看窗口（交易日数）。主力状态计算需求：chip warmup 60 / MA120 量代理 /
# ret60 / phase ≥70 —— 260 根留足余量；再深对结果无增益，纯烧 Supabase egress。
BARS_WINDOW = 260


def ensure_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mainforce_state (
                id BIGSERIAL PRIMARY KEY,
                code VARCHAR(12) NOT NULL,
                name VARCHAR(32),
                date DATE NOT NULL,
                phase VARCHAR(16),
                signal VARCHAR(16),
                mult DOUBLE PRECISION DEFAULT 1.0,
                chip_json JSONB,
                flow5_amt DOUBLE PRECISION,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_mainforce_state UNIQUE (code, date)
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mainforce_state (
                code TEXT NOT NULL, name TEXT, date TEXT NOT NULL,
                phase TEXT, signal TEXT, mult REAL DEFAULT 1.0,
                chip_json TEXT, flow5_amt REAL,
                UNIQUE (code, date)
            )
        """)


def _load_bars_all() -> dict:
    """
    合并两个日线来源（优先 backtest_prices，kline_cache 补覆盖）：
      - backtest_prices：544+ 只 × 750 根，16:10 每日回填（最深最新）
      - kline_cache：评分精算路径写回的全量 K 线（~2200 只），
        但存在战法短拉取污染的 30 根行 → 只收 kline_count ≥ 250 的行
    返回 {code: (bars, name)}，冲突时取更长的一方（名字随最长来源）。

    ★ egress 控制（2026-09-05）：本函数每天 17:30 全量跑一次，原先把
      backtest_prices 整表（~71MB）+ kline_cache 大 JSON（~19MB）拖下来，
      实测是 Supabase 出站流量的大头。而主力状态计算只需 ~120-250 根
      （chip warmup 60 / MA120 量代理 / ret60 / phase ≥70），故：
      ① backtest_prices 按日期窗口截断（BARS_WINDOW≈260 个交易日）；
      ② kline_cache 回退只补 backtest_prices 没覆盖的代码 —— 覆盖了的
         原来 750>500 必胜出，读了也白读（纯烧流量）。
    """
    bars: dict = {}

    # ★ DATA_SOURCE=pack/local：整段直接读数据包（零 Supabase 流量）。
    #   包内 500 根 ≥ 主力状态需求（~120-250 根），无需再合并 kline_cache。
    try:
        from app import pack_source
        if pack_source.enabled():
            for code in pack_source.get_codes():
                b = pack_source.get_klines(code)
                if b and len(b) >= 120:
                    nm, _cap = pack_source.get_name_cap(code)
                    bars[code] = (b, nm)
            print(f"[mainforce_state] 数据源=pack: {len(bars)} 只")
            return bars
    except Exception as e:
        print(f"[mainforce_state] pack 读取失败，回退数据库: {e}")

    def _put(code, rows, name=""):
        if rows and (code not in bars or len(rows) > len(bars[code][0])):
            bars[code] = (rows, name)

    from datetime import timedelta
    # 260 交易日 ≈ 372 个日历日（*8/5），再放宽到 380 防长假断层
    cutoff = (datetime.now() - timedelta(days=max(BARS_WINDOW * 8 // 5, 380))
              ).strftime("%Y-%m-%d")

    try:
        cn = db.fetch("SELECT code, MAX(name) AS name FROM backtest_prices "
                      "GROUP BY code ORDER BY code")
        name_by = {r["code"]: (r.get("name") or "") for r in cn}
        codes = [r["code"] for r in cn]
        for i in range(0, len(codes), 120):
            chunk = codes[i:i + 120]
            rows = db.fetch(
                "SELECT code, date, open, high, low, close, volume FROM backtest_prices "
                "WHERE code = ANY(%s) AND date >= %s ORDER BY code, date ASC",
                (chunk, cutoff))
            tmp = {}
            for r in rows:
                tmp.setdefault(r["code"], []).append({
                    "date": str(r["date"]), "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"]})
            for c, b in tmp.items():
                _put(c, b, name_by.get(c, ""))
    except Exception as e:
        print(f"[mainforce_state] backtest_prices 读取失败：{e}")

    try:
        # kline_data 是大 JSON（整表一次查会 statement timeout）→ 分块 + 限列
        # ★ 只补 backtest_prices 没覆盖的代码：被覆盖的原先 750>500 必胜出，
        #   读了也白读（每行 ~9KB 的 kline_data 纯烧 egress）
        cnts = db.fetch("SELECT code FROM kline_cache WHERE kline_count >= 250 "
                        "ORDER BY code")
        kcodes = [r["code"] for r in cnts if r["code"] not in name_by]
        for i in range(0, len(kcodes), 150):
            chunk = kcodes[i:i + 150]
            rows = db.fetch("SELECT code, name, kline_data FROM kline_cache "
                            "WHERE code = ANY(%s)", (chunk,))
            for r in rows:
                try:
                    data = r["kline_data"]
                    arr = json.loads(data) if isinstance(data, str) else data
                    if arr and len(arr) >= 120:
                        _put(r["code"], arr, r.get("name") or "")
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        print(f"[mainforce_state] kline_cache 读取失败：{e}")
    return bars


def refresh_all(codes: list = None, regime: str = None, verbose_every: int = 100) -> dict:
    """全池计算当日主力行为状态（幂等覆盖当日）。"""
    ensure_table()
    bars_map = _load_bars_all()
    if codes:
        bars_map = {c: bars_map[c] for c in codes if c in bars_map}
    fs_map = get_float_shares_from_snapshot()
    flow_map = load_flow_map()
    today = None
    n = 0
    t0 = time.time()
    for i, (code, (bars, name)) in enumerate(sorted(bars_map.items()), 1):
        today = today or bars[-1]["date"]
        ov = mainforce_overlay(bars, flow_rows=flow_map.get(code),
                               float_shares=fs_map.get(code), regime=regime)
        if not ov:
            continue
        _save(code, name, today, ov)
        n += 1
        if i % verbose_every == 0:
            print(f"[mainforce_state] {i}/{len(bars_map)} n={n} ({time.time() - t0:.0f}s)")
    return {"codes": len(bars_map), "saved": n, "date": today,
            "seconds": round(time.time() - t0, 1)}


def _save(code: str, name: str, date: str, ov: dict) -> None:
    chip_json = json.dumps(ov.get("chip") or {}, ensure_ascii=False)
    if db._use_postgres:
        db.execute("""
            INSERT INTO mainforce_state (code, name, date, phase, signal, mult,
                                         chip_json, flow5_amt)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (code, date) DO UPDATE SET
                name=EXCLUDED.name, phase=EXCLUDED.phase, signal=EXCLUDED.signal,
                mult=EXCLUDED.mult, chip_json=EXCLUDED.chip_json,
                flow5_amt=EXCLUDED.flow5_amt
        """, (code, name, date, ov.get("phase"), ov.get("signal"),
              ov.get("mult", 1.0), chip_json, ov.get("flow5_amt")))
    else:
        db.execute("""
            INSERT OR REPLACE INTO mainforce_state (code, name, date, phase, signal,
                                                    mult, chip_json, flow5_amt)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (code, name, date, ov.get("phase"), ov.get("signal"),
              ov.get("mult", 1.0), chip_json, ov.get("flow5_amt")))


def load_latest(codes: list) -> dict:
    """{code: state_dict}——每只取最新日期的一条。"""
    if not codes:
        return {}
    rows = db.fetch("""
        SELECT DISTINCT ON (code) code, name, date, phase, signal, mult,
               chip_json, flow5_amt
        FROM mainforce_state WHERE code = ANY(%s)
        ORDER BY code, date DESC
    """, (codes,))
    out = {}
    for r in rows:
        try:
            chip = (json.loads(r["chip_json"])
                    if isinstance(r["chip_json"], str) else (r["chip_json"] or {}))
        except (TypeError, ValueError):
            chip = {}
        out[r["code"]] = {
            "date": str(r["date"]), "phase": r["phase"], "signal": r["signal"],
            "mult": r["mult"] or 1.0, "chip": chip, "flow5_amt": r["flow5_amt"],
        }
    return out


if __name__ == "__main__":
    import sys
    regime = sys.argv[1] if len(sys.argv) > 1 else None
    print(json.dumps(refresh_all(regime=regime), ensure_ascii=False))
