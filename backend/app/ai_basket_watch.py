# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】AI 硬件主线篮子回撤观察器 —— **纯观察器，不产生买卖信号**
================================================================================
背景（2026-10-23，用户 10-22 复盘）：
  "新易盛 -12.28%，较年内高点回撤 57.5%；中际旭创 -8.76%、天孚通信 -8.36%
   （高点回撤 47.3%）……AI 主线自己崩了。"
  ⇒ 把"主线龙头篮子"的 5/30 日收益与距 120 日高点回撤做成每天可看的表。

口径（读数据包，零外部请求）：
  · 篮子 = 6 只 AI 硬件龙头（可用 env `AI_BASKET_CODES` 覆盖，逗号分隔）；
  · 每只股票以 120 日前收盘归一化，**等权平均**成篮子曲线（日度再平衡的近似）；
  · 个股与篮子都输出 ret5 / ret30 / 距 120 日高点回撤 / pos120；
  · 表 `ai_basket_watch`，篮子用合成行 code='__BASKET__'。

★ 预登记声明：不改分数、不进闸门、不碰模拟盘；"回撤"只描述事实。
================================================================================
"""
import os
from datetime import datetime
from typing import Dict, List, Optional

from app.database import db

_TABLE = "ai_basket_watch"
_READY = False

DEFAULT_BASKET = [
    ("300502", "新易盛"), ("300308", "中际旭创"), ("300394", "天孚通信"),
    ("601138", "工业富联"), ("002463", "沪电股份"), ("688256", "寒武纪"),
]


def _basket():
    raw = (os.environ.get("AI_BASKET_CODES") or "").strip()
    if not raw:
        return DEFAULT_BASKET
    names = dict(DEFAULT_BASKET)
    out = []
    for c in raw.split(","):
        c = c.strip()
        if c:
            out.append((c, names.get(c, c)))
    return out or DEFAULT_BASKET


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS ai_basket_watch (
            date TEXT, code TEXT, name TEXT,
            ret5 REAL, ret30 REAL, dd120 REAL, pos120 REAL,
            is_basket INTEGER DEFAULT 0, created_at TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    _READY = True


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _closes(bars: List[dict]) -> List[float]:
    out = []
    for b in bars:
        c = b.get("close") if isinstance(b, dict) else None
        c = _num(c)
        if c:
            out.append(c)
    return out


def compute(day: str = None) -> Dict:
    ensure_table()
    from app.crowding_watch import _pack
    ps = _pack()
    rows, series, data_day = [], [], ""
    for code, fallback_name in _basket():
        bars = ps.get_klines(code) or []
        closes = _closes(bars)
        if len(closes) < 130:
            continue
        if not data_day and bars:
            data_day = str(bars[-1].get("date") or "")[:10]
        base = closes[-120]
        if base <= 0:
            continue
        series.append([c / base for c in closes[-120:]])
        c = closes[-1]
        hi, lo = max(closes[-120:]), min(closes[-120:])
        name = fallback_name
        try:
            nm, _cap = ps.get_name_cap(code)
            name = nm or fallback_name
        except Exception:
            pass
        rows.append({
            "code": code, "name": name,
            "ret5": round((c / closes[-6] - 1) * 100, 2),
            "ret30": round((c / closes[-31] - 1) * 100, 2),
            "dd120": round((c / hi - 1) * 100, 2),
            "pos120": round((c - lo) / (hi - lo), 3) if hi > lo else None,
        })
    if not series:
        return {"ok": False, "reason": "篮子股票无有效K线"}

    L = min(len(s) for s in series)
    basket = [sum(s[i] for s in series) / len(series) for i in range(L)]
    hi, lo = max(basket), min(basket)
    b = {
        "code": "__BASKET__", "name": "AI硬件篮子",
        "ret5": round((basket[-1] / basket[-6] - 1) * 100, 2),
        "ret30": round((basket[-1] / basket[-31] - 1) * 100, 2),
        "dd120": round((basket[-1] / hi - 1) * 100, 2),
        "pos120": round((basket[-1] - lo) / (hi - lo), 3) if hi > lo else None,
    }
    rows.append(b)

    day = str(day or data_day or datetime.now().strftime("%Y-%m-%d"))[:10]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute("DELETE FROM ai_basket_watch WHERE date = %s", (day,))
        for r in rows:
            db.upsert(_TABLE, {
                "date": day, "code": r["code"], "name": r["name"],
                "ret5": r["ret5"], "ret30": r["ret30"],
                "dd120": r["dd120"], "pos120": r["pos120"],
                "is_basket": 1 if r["code"] == "__BASKET__" else 0,
                "created_at": now,
            }, conflict_columns=["date", "code"])
    except Exception as e:
        print(f"[aibasket] 落库失败: {str(e)[:100]}")
        return {"ok": False, "reason": f"落库失败: {str(e)[:80]}", "rows": rows}

    stock_rows = [r for r in rows if r["code"] != "__BASKET__"]
    return {"ok": True, "date": day, "basket": b, "rows": stock_rows,
            "n": len(stock_rows)}


def load(day: str = None) -> Dict:
    ensure_table()
    try:
        if day:
            d = str(day)[:10]
            rows = db.fetch("SELECT * FROM ai_basket_watch WHERE date=%s "
                            "ORDER BY is_basket DESC, dd120", (d,))
        else:
            r = db.fetch_one("SELECT MAX(date) AS d FROM ai_basket_watch")
            d = str((r or {}).get("d") or "")[:10]
            rows = db.fetch("SELECT * FROM ai_basket_watch WHERE date=%s "
                            "ORDER BY is_basket DESC, dd120", (d,)) if d else []
    except Exception as e:
        return {"date": day, "basket": None, "rows": [],
                "note": f"读取失败: {str(e)[:80]}"}
    basket = None
    out = []
    for x in rows:
        item = {"code": x.get("code"), "name": x.get("name"),
                "ret5": _num(x.get("ret5")), "ret30": _num(x.get("ret30")),
                "dd120": _num(x.get("dd120")), "pos120": _num(x.get("pos120"))}
        if x.get("is_basket"):
            basket = item
        else:
            out.append(item)
    return {"date": d, "basket": basket, "rows": out,
            "note": "观察器：篮子回撤只描述主线状态，不构成买卖信号。"}
