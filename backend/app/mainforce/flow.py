# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】主力资金流数据层：东财历史资金流回填 + 流通股本（筹码引擎依赖）
================================================================================

数据表：
  mainflow_history(code, name, date, main_net, super_net, big_net,
                   main_pct, super_pct, close, pct_chg)
  —— UNIQUE(code, date) 幂等写入。主力=大单+超大单（东财口径）。
  float_shares 不落库：clish 每次拉取便宜（1 次分页请求全市场），随用随取。

数据源（两级）：
  1. 新浪 MoneyFlow.ssl_qsfx_lscjfb（历史资金流，稳定、风控宽松）：
     r0/r1/r2/r3 = 特大/大/中/小单成交额，*_net 为对应净流入
     主力净流入 = r0_net + r1_net（≈东财"主力=超大单+大单"口径）
     占比 = 净流入 / (r0+r1+r2+r3) —— 与东财"主力净流入占比"同义
  2. 东财 www.push2his fflow/daykline（字段更规整，但对高频断连/封 IP
     —— 2026-09-05 实测连续 ~600 次请求后被封，仅作兜底/恢复后可用）

数据表：
  mainflow_history(code, name, date, main_net, super_net, big_net,
                   main_pct, super_pct, close, pct_chg)
  —— UNIQUE(code, date) 幂等写入。主力=大单+超大单口径。

限速：新浪逐股间隔 ≥0.25s；东财 0.12s 且失败重试 2 次后跳过。
================================================================================
"""

import json
import time

import requests

from app.database import db

_FLOW_URL = "https://www.push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_CLIST_URL = "https://push2.eastmoney.com/api/qt/clist/get"
_SINA_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
             "json_v2.php/MoneyFlow.ssl_qsfx_lscjfb")
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
})
_SINA_SESSION = requests.Session()
_SINA_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Referer": "https://finance.sina.com.cn",
})
_REQ_GAP = 0.12   # 逐股间隔（东财高频断连保护）
_SINA_GAP = 0.25  # 新浪间隔（礼貌抓取）
_MAX_RETRIES = 2


def _sina_code(code: str) -> str:
    c = code.lower()
    if c.startswith(("sh", "sz", "bj")):
        return c
    if len(c) == 6:
        return ("sh" if c[0] == "6" else "sz") + c
    return c


def fetch_flow_history_sina(code: str, days: int = 130) -> list:
    """新浪历史资金流（降序分页 → 升序返回）。主力=r0_net+r1_net。"""
    out = []
    page = 1
    oldest_needed = ""
    while page <= 5:
        try:
            r = _SINA_SESSION.get(_SINA_URL, params={
                "page": page, "num": 60, "sort": "opendate", "asc": 0,
                "daima": _sina_code(code),
            }, timeout=10)
            txt = r.text.strip()
            rows = json.loads(txt) if txt else []
        except (Exception, ValueError):
            break
        if not rows:
            break
        for row in rows:
            try:
                d = row["opendate"]
                total = float(row.get("r0") or 0) + float(row.get("r1") or 0) \
                    + float(row.get("r2") or 0) + float(row.get("r3") or 0)
                if total <= 0:
                    continue
                r0n = float(row.get("r0_net") or 0)
                r1n = float(row.get("r1_net") or 0)
                main_net = r0n + r1n
                out.append({
                    "date": d,
                    "main_net": round(main_net),
                    "super_net": round(r0n),
                    "big_net": round(r1n),
                    "main_pct": round(main_net / total * 100, 2),
                    "super_pct": round(r0n / total * 100, 2),
                    "close": float(row.get("trade") or 0),
                    "pct_chg": round(float(row.get("changeratio") or 0) * 100, 2),
                })
                oldest_needed = d
            except (KeyError, TypeError, ValueError):
                continue
        # 已覆盖到 130 个自然日之前 / 本页不满 → 停止翻页
        if len(rows) < 60:
            break
        from datetime import datetime, timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        if oldest_needed < cutoff:
            break
        page += 1
        time.sleep(0.1)
    out.sort(key=lambda x: x["date"])
    return out


def to_secid(code: str) -> str:
    c = code.lower()
    if c.startswith(("sh", "sz", "bj")):
        return ("1." if c[:2] == "sh" else "0.") + c[2:]
    if len(c) == 6:
        return ("1." if c[0] == "6" else "0.") + c
    return c


def ensure_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mainflow_history (
                id BIGSERIAL PRIMARY KEY,
                code VARCHAR(12) NOT NULL,
                name VARCHAR(32),
                date DATE NOT NULL,
                main_net BIGINT,
                super_net BIGINT,
                big_net BIGINT,
                main_pct DOUBLE PRECISION,
                super_pct DOUBLE PRECISION,
                close DOUBLE PRECISION,
                pct_chg DOUBLE PRECISION,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                CONSTRAINT uq_mainflow UNIQUE (code, date)
            )
        """)
        db.execute("""
            CREATE INDEX IF NOT EXISTS idx_mainflow_date ON mainflow_history (date)
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS mainflow_history (
                code TEXT NOT NULL, name TEXT, date TEXT NOT NULL,
                main_net REAL, super_net REAL, big_net REAL,
                main_pct REAL, super_pct REAL, close REAL, pct_chg REAL,
                UNIQUE (code, date)
            )
        """)


def fetch_flow_history(code: str, days: int = 130, source: str = "sina") -> list:
    """单只股票日级资金流（升序）。优先新浪，失败降级东财。"""
    rows = fetch_flow_history_sina(code, days)
    if rows:
        return rows
    return fetch_flow_history_em(code, days)


def fetch_flow_history_em(code: str, days: int = 130) -> list:
    """东财日级资金流（字段解析见文件头；IP 被封时返回空）。"""
    params = {
        "lmt": str(days), "klt": "101", "secid": to_secid(code),
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    }
    for attempt in range(_MAX_RETRIES):
        try:
            r = _SESSION.get(_FLOW_URL, params=params, timeout=12)
            klines = ((r.json() or {}).get("data") or {}).get("klines") or []
            out = []
            for line in klines:
                p = line.split(",")
                if len(p) < 13:
                    continue
                try:
                    out.append({
                        "date": p[0],
                        "main_net": int(float(p[1])),
                        "small_net": int(float(p[2])),
                        "mid_net": int(float(p[3])),
                        "big_net": int(float(p[4])),
                        "super_net": int(float(p[5])),
                        "main_pct": float(p[6]),
                        "super_pct": float(p[10]),
                        "close": float(p[11]),
                        "pct_chg": float(p[12]),
                    })
                except (ValueError, IndexError):
                    continue
            return out
        except Exception:
            if attempt < _MAX_RETRIES - 1:
                time.sleep(1.0)
    return []


def save_flow(code: str, name: str, rows: list) -> int:
    if not rows:
        return 0
    n = 0
    BATCH = 200
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        ph = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(batch))
        params = []
        for r in batch:
            params += [code, name, r["date"], r["main_net"], r["super_net"],
                       r["big_net"], r["main_pct"], r["super_pct"],
                       r["close"], r["pct_chg"]]
        if db._use_postgres:
            sql = (f"INSERT INTO mainflow_history (code, name, date, main_net, super_net, "
                   f"big_net, main_pct, super_pct, close, pct_chg) VALUES {ph} "
                   f"ON CONFLICT (code, date) DO UPDATE SET main_net=EXCLUDED.main_net, "
                   f"super_net=EXCLUDED.super_net, big_net=EXCLUDED.big_net, "
                   f"main_pct=EXCLUDED.main_pct, super_pct=EXCLUDED.super_pct, "
                   f"close=EXCLUDED.close, pct_chg=EXCLUDED.pct_chg, name=EXCLUDED.name")
        else:
            sql = (f"INSERT OR REPLACE INTO mainflow_history (code, name, date, main_net, "
                   f"super_net, big_net, main_pct, super_pct, close, pct_chg) VALUES {ph}")
        db.execute(sql, tuple(params))
        n += len(batch)
    return n


def get_all_codes() -> list:
    rows = db.fetch("SELECT DISTINCT code, name FROM backtest_prices ORDER BY code")
    return [{"code": r["code"], "name": r["name"]} for r in rows]


def backfill_all(codes: list = None, gap: float = _SINA_GAP, verbose_every: int = 50) -> dict:
    """全量回填（默认 backtest_prices 里的池）。新浪主源，东财兜底。"""
    ensure_table()
    if not codes:
        codes = [c["code"] for c in get_all_codes()]
    ok, fail, total_rows = 0, 0, 0
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        rows = fetch_flow_history(code)
        if rows:
            total_rows += save_flow(code, "", rows)
            ok += 1
        else:
            fail += 1
        if i % verbose_every == 0:
            print(f"[mainflow] {i}/{len(codes)} ok={ok} fail={fail} rows={total_rows} "
                  f"({time.time() - t0:.0f}s)")
        time.sleep(gap)
    return {"codes": len(codes), "ok": ok, "fail": fail, "rows": total_rows,
            "seconds": round(time.time() - t0, 1)}


# ── 流通股本（筹码引擎换手衰减用） ──────────────────────────────────────────
# 优先从 market_snapshot（腾讯 15:10 全市场快照，float_cap=流通市值万元）反推：
#   流通股本 ≈ 流通市值 / 最新价。股本只有解禁/增发才变，用当日价格反推对
#   近 120 日回测是可接受近似；东财 clist f85 直连已实测会被风控断连，
#   仅作 get_float_shares 的在线兜底。

def get_float_shares_from_snapshot() -> dict:
    """{code: 流通股本}，来源 market_snapshot 最新一份。"""
    row = db.fetch_one("SELECT stocks_json, saved_at FROM market_snapshot "
                       "ORDER BY saved_at DESC LIMIT 1")
    if not row:
        return {}
    raw = row["stocks_json"]
    stocks = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(stocks, dict):
        stocks = list(stocks.values())
    out = {}
    for s in stocks or []:
        try:
            cap = float(s.get("float_cap") or 0)   # 万元
            price = float(s.get("price") or 0)
            if cap > 0 and price > 0:
                shares = cap * 1e4 / price
                if shares > 1e6:
                    out[s["code"]] = shares
        except (TypeError, ValueError):
            continue
    print(f"[float_shares] market_snapshot {row['saved_at']} → {len(out)} 只")
    return out

def get_float_shares(codes: list = None) -> dict:
    """
    全市场/指定代码的流通股本 {code: shares}（在线兜底，东财 clist f85）。
    ★ 不带 fltt 参数：fltt=2 会把大数缩成错误的小数（000001 返回 28.85）。
    主路径用 get_float_shares_from_snapshot()（不联网、无风控）。
    """
    fs = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
    fields = "f12,f14,f85"
    out = {}
    pn = 1
    while pn <= 80:
        params = {
            "pn": pn, "pz": 100, "po": 0, "np": 1,
            "fs": fs, "fields": fields, "fid": "f12",
        }
        try:
            r = _SESSION.get(_CLIST_URL, params=params, timeout=10)
            rows = (r.json().get("data") or {}).get("diff") or []
        except Exception:
            break
        if not rows:
            break
        for row in rows:
            code = str(row.get("f12") or "")
            raw = row.get("f85")
            try:
                shares = float(str(raw).replace(",", ""))
            except (TypeError, ValueError):
                continue
            if code and shares > 0:
                out[code] = shares
        if len(rows) < 100:
            break
        pn += 1
        time.sleep(0.15)
    if codes is not None:
        want = {c.lstrip("shzbj").lower() if len(c) > 6 else c for c in codes}
        want = {c for c in want if len(c) == 6}
        out = {k: v for k, v in out.items() if k in want}
    return out


def load_flow_map(codes: list = None) -> dict:
    """读全表 {code: [{date, main_net, ...}]}（升序），供回测脚本用。"""
    ensure_table()
    sql = ("SELECT code, date, main_net, super_net, big_net, main_pct, super_pct, "
           "close, pct_chg FROM mainflow_history")
    params = None
    if codes:
        sql += " WHERE code = ANY(%s)"
        params = (codes,)
    sql += " ORDER BY code, date ASC"
    rows = db.fetch(sql, params) if db._use_postgres else db.fetch(sql)
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append({
            "date": str(r["date"]), "main_net": r["main_net"], "super_net": r["super_net"],
            "big_net": r["big_net"], "main_pct": r["main_pct"], "super_pct": r["super_pct"],
            "close": r["close"], "pct_chg": r["pct_chg"],
        })
    return by_code


def load_flow(code: str) -> list:
    """单只股票的资金流（升序），详情页/实时叠加用（避免全表加载）。"""
    ensure_table()
    rows = db.fetch("SELECT date, main_net, super_net, big_net, main_pct, super_pct, "
                    "close, pct_chg FROM mainflow_history WHERE code = %s "
                    "ORDER BY date ASC", (code,))
    return [{"date": str(r["date"]), "main_net": r["main_net"], "super_net": r["super_net"],
             "big_net": r["big_net"], "main_pct": r["main_pct"], "super_pct": r["super_pct"],
             "close": r["close"], "pct_chg": r["pct_chg"]} for r in rows]
