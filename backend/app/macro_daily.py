# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】外盘日频收盘历史表 `macro_daily_history` —— 补 `macro_history` 的短板
================================================================================

背景（2026-10-10 黄金/商品联动研究，见 .codebuddy/memory/2026-10-10.md §13）：
  `macro_history` 从 2026-08 起每天记 09:10（盘前）+ 15:03（盘后）两条快照，到当时只有
  ~32 个收盘点 ⇒ 做不了"6 个月/多年"的领先滞后与相关性。本模块落一张**日频收盘表**：
    · 历史回填：`scripts/backfill_macro_daily.py`（akshare，一次性、幂等）
    · 逐日增量：`flash/store.append_macro_history` 在 15:03 盘后快照时顺手 upsert
      （复用既有快照，零新增网络/运行时依赖）
表：macro_daily_history(code, date, close)  PRIMARY KEY(code, date)

⚠️ 美元指数 DXY 无稳定免费日频源（东财被断连）⇒ 只靠 macro_history 的 15:03 快照
   自 2026-08 起向前累积；历史缺失属**已知数据缺口**（见脚本 docstring）。
================================================================================
"""
from app.database import db

# code -> 中文名（code 与 macro 面板键尽量对齐，便于对照）
SYMBOLS = {
    "gold_com": "COMEX金",
    "gold_spot": "伦敦金现",
    "brent": "布伦特原油",
    "wti": "WTI原油",
    "us10y": "美债10Y",
    "us2y": "美债2Y",
    "dxy": "美元指数",
    "gld": "GLD ETF",
}

# macro 面板键 -> 本表 code（`upsert_from_panel` 用）。
# ⚠️ 注意：面板键是 `us10y`/`us2y`（而 `macro_history` 里存的是 `us10yt`，两者易混）。
_PANEL_KEY_MAP = {
    "gold": "gold_com",
    "gold_spot": "gold_spot",
    "brent": "brent",
    "wti": "wti",
    "us10y": "us10y",
    "us2y": "us2y",
    "dxy": "dxy",
    "gld": "gld",
}


def ensure_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS macro_daily_history (
                code VARCHAR(24) NOT NULL,
                date DATE NOT NULL,
                close DOUBLE PRECISION,
                PRIMARY KEY (code, date)
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS macro_daily_history (
                code TEXT NOT NULL, date TEXT NOT NULL, close REAL,
                PRIMARY KEY (code, date)
            )
        """)


def upsert(code: str, date: str, close: float) -> None:
    """幂等写一行（PG `ON CONFLICT` / SQLite `INSERT OR REPLACE`）。close 为空跳过。"""
    if close is None:
        return
    ensure_table()
    d = str(date)[:10]
    c = float(close)
    if db._use_postgres:
        db.execute(
            "INSERT INTO macro_daily_history (code, date, close) VALUES (%s, %s, %s) "
            "ON CONFLICT (code, date) DO UPDATE SET close = EXCLUDED.close",
            (code, d, c))
    else:
        db.execute(
            "INSERT OR REPLACE INTO macro_daily_history (code, date, close) "
            "VALUES (%s, %s, %s)", (code, d, c))


def bulk_upsert(code: str, rows) -> None:
    """批量幂等写（rows = [(date, close), ...]）。PG 分块 `ON CONFLICT`，SQLite 逐行（本地快）。"""
    pairs = [(str(d)[:10], float(c)) for d, c in rows if c is not None]
    if not pairs:
        return
    ensure_table()
    if db._use_postgres:
        for i in range(0, len(pairs), 500):
            chunk = pairs[i:i + 500]
            values = ", ".join(["(%s, %s, %s)"] * len(chunk))
            params = []
            for d, c in chunk:
                params += [code, d, c]
            db.execute(
                f"INSERT INTO macro_daily_history (code, date, close) VALUES {values} "
                "ON CONFLICT (code, date) DO UPDATE SET close = EXCLUDED.close",
                tuple(params))
    else:
        for d, c in pairs:
            db.execute(
                "INSERT OR REPLACE INTO macro_daily_history (code, date, close) "
                "VALUES (%s, %s, %s)", (code, d, c))


def load(code: str, start: str = None) -> list:
    """升序 [{date, close}]。未命中返回 []。"""
    ensure_table()
    if start:
        rows = db.fetch(
            "SELECT date, close FROM macro_daily_history "
            "WHERE code = %s AND date >= %s ORDER BY date", (code, str(start)[:10]))
    else:
        rows = db.fetch(
            "SELECT date, close FROM macro_daily_history WHERE code = %s ORDER BY date",
            (code,))
    return [{"date": str(r["date"])[:10], "close": float(r["close"])} for r in (rows or [])]


def upsert_from_panel(panel: dict, date_str: str) -> None:
    """把 macro 面板快照里带的外盘价格转存为当日收盘（`append_macro_history` 盘后调用）。"""
    ensure_table()
    for key, code in _PANEL_KEY_MAP.items():
        item = panel.get(key) or {}
        price = item.get("price")
        if price:
            upsert(code, date_str, price)


def stats() -> dict:
    """各 code 的行数/日期区间（排障/自检用）。"""
    ensure_table()
    rows = db.fetch(
        "SELECT code, COUNT(*) n, MIN(date) mn, MAX(date) mx "
        "FROM macro_daily_history GROUP BY code ORDER BY code")
    return {str(r["code"]): {"n": int(r["n"]), "mn": str(r["mn"])[:10], "mx": str(r["mx"])[:10]}
            for r in (rows or [])}
