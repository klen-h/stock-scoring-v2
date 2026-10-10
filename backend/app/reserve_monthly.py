# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】央行储备月度面板：外汇储备 + 黄金储备（慢变量，**非交易信号**）
================================================================================

定位（见 `PLAN_RESERVE_SIGNALS.md` §6.2）：
  “减美债、增黄金”是**月频结构变量**，不是日频择时信号。本模块只负责把它落成
  **可追溯的月度事实**，供面板展示与长期研究引用；**不进决策链、不产生买卖信号**。

数据源：akshare `macro_china_foreign_exchange_gold()`（外管局/央行月度口径）。

⚠️ 单位**照抄源，不换算**（换算一次就容易与官方口径对不上——本项目踩过单位坑）：
     · fx_reserve_100m_usd  外汇储备（**亿美元**，如 34002.51）
     · gold_reserve_wan_oz  黄金储备（**万盎司**，如 7747）
   展示层再换算：吨 = 万盎司 × 1e4 × 31.1035 ÷ 1e6。

⚠️ 已见**源缺口**：2025.10~2025.12 三个月缺号（源自身如此）⇒ **不插值、不填补**，缺就是缺。

表：reserve_monthly(month, fx_reserve_100m_usd, gold_reserve_wan_oz)  PK(month)
    month 形如 '2026-09'。
================================================================================
"""
from app.database import db

_OZ_TO_TONNE = 31.1035 / 1e6      # 万盎司 → 吨：×1e4 盎司 ×31.1035g ÷1e6 g/t


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def ensure_table() -> None:
    if db._use_postgres:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reserve_monthly (
                month VARCHAR(7) NOT NULL,
                fx_reserve_100m_usd DOUBLE PRECISION,
                gold_reserve_wan_oz DOUBLE PRECISION,
                PRIMARY KEY (month)
            )
        """)
    else:
        db.execute("""
            CREATE TABLE IF NOT EXISTS reserve_monthly (
                month TEXT NOT NULL,
                fx_reserve_100m_usd REAL,
                gold_reserve_wan_oz REAL,
                PRIMARY KEY (month)
            )
        """)


def bulk_upsert(rows) -> None:
    """rows = [(month, fx_reserve_100m_usd, gold_reserve_wan_oz), ...]。幂等。"""
    items = [(str(m)[:7], fx, g) for m, fx, g in rows if m]
    if not items:
        return
    ensure_table()
    for i in range(0, len(items), 500):
        chunk = items[i:i + 500]
        if db._use_postgres:
            values = ", ".join(["(%s, %s, %s)"] * len(chunk))
            params = []
            for m, fx, g in chunk:
                params += [m, fx, g]
            db.execute(
                "INSERT INTO reserve_monthly "
                "(month, fx_reserve_100m_usd, gold_reserve_wan_oz) "
                f"VALUES {values} ON CONFLICT (month) DO UPDATE SET "
                "fx_reserve_100m_usd = EXCLUDED.fx_reserve_100m_usd, "
                "gold_reserve_wan_oz = EXCLUDED.gold_reserve_wan_oz", tuple(params))
        else:
            for m, fx, g in chunk:
                db.execute(
                    "INSERT OR REPLACE INTO reserve_monthly "
                    "(month, fx_reserve_100m_usd, gold_reserve_wan_oz) VALUES (%s, %s, %s)",
                    (m, fx, g))


def gold_reserve_tonnes(wan_oz) -> float:
    """万盎司 → 吨（仅展示用换算）。"""
    v = _f(wan_oz)
    return round(v * 1e4 * _OZ_TO_TONNE, 1) if v else None


def load(months: int = None) -> list:
    """升序 [{month, fx_reserve_100m_usd, gold_reserve_wan_oz}]；months 取最近 N 个月。"""
    ensure_table()
    rows = db.fetch("SELECT month, fx_reserve_100m_usd, gold_reserve_wan_oz "
                    "FROM reserve_monthly ORDER BY month ASC")
    out = [{"month": str(r["month"])[:7],
            "fx_reserve_100m_usd": _f(r.get("fx_reserve_100m_usd")),
            "gold_reserve_wan_oz": _f(r.get("gold_reserve_wan_oz"))}
           for r in (rows or [])]
    return out[-months:] if months else out


def series_with_share(months: int = 24) -> list:
    """月度序列 + 展示字段（黄金吨数 / 黄金占储备比重%）。

    ⚠️ 口径诚实说明：官方的“黄金占储备比重”用**内部估价**；这里用 **COMEX 月末收盘**近似
       （取自 `macro_daily`，零新增数据源），**只用于看趋势方向**，绝对值可能与官方口径有差。
    """
    items = load(months)
    if not items:
        return []
    try:
        from app import macro_daily
        gold = macro_daily.load("gold_com") or []       # [{date, close}] 升序
    except Exception:
        gold = []
    out = []
    for it in items:
        m = it["month"]
        px = None
        for g in gold:                                  # 月末收盘（最后一条 ≤ 月末的日线）
            if g["date"][:7] <= m:
                px = g["close"]
            else:
                break
        g_oz, fx = it.get("gold_reserve_wan_oz"), it.get("fx_reserve_100m_usd")
        share = None
        if px and g_oz and fx:
            gold_100m_usd = g_oz * 1e4 * px / 1e8       # 盎司 × USD/oz → 亿美元
            share = round(gold_100m_usd / fx * 100, 1)
        row = dict(it)
        row["gold_price_usd"] = px
        row["gold_reserve_tonnes"] = gold_reserve_tonnes(g_oz)
        row["gold_share_pct"] = share
        out.append(row)
    return out


def stats() -> dict:
    """覆盖概览（行数/月份区间/最新值），供排障与自检。"""
    ensure_table()
    r = db.fetch_one("SELECT COUNT(*) AS n, MIN(month) AS mn, MAX(month) AS mx "
                     "FROM reserve_monthly") or {}
    last = load(1)
    return {"n": int(r.get("n") or 0), "mn": str(r.get("mn") or "")[:7],
            "mx": str(r.get("mx") or "")[:7], "latest": last[-1] if last else None}
