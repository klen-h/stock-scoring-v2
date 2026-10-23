# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】指数关键位观察器（key level watch）—— **纯观察器，不产生买卖信号**
================================================================================
背景（2026-10-23，用户 10-22 复盘）：
  "3674.82/3681.28 双底今天被击穿（最低 3649.86，年内首次收在 3700 下方）……
   这意味着政策底 1.0 试探失败。"
  ⇒ 把"前低/整数位/年内低点有没有失守、离支撑多远"变成每天可核对的表。

口径（指数日 K，腾讯；失败返回 insufficient，不猜）：
  · 前低 = 最近 60 个交易日（不含今日）最低收盘；
  · 整数位 = floor(收盘/100)×100；
  · 年内低点 = 最近 250 个交易日（不含今日）最低收盘；
  · 失守 = **今日收盘跌破、且昨日收盘还在其上**（首次跌破，避免连续多日重复计数）；
  · 支撑 = 候选位中"低于现价且最高"的那个；离支撑距离 = 收盘/支撑 − 1。

★ 预登记声明：不改分数、不进闸门、不改 regime、不碰模拟盘。
================================================================================
"""
import math
import os
from datetime import datetime
from typing import Dict, List, Optional

from app.database import db

_TABLE = "key_level_watch"
_READY = False

INDICES = [
    ("000001", "上证指数"),
    ("399001", "深证成指"),
    ("399006", "创业板指"),
    ("000300", "沪深300"),
    ("000905", "中证500"),
    ("000985", "中证全指"),
]

# ★ 2026-10-23（用户推演）：结构性关键位（前双底/颈线），非整数关口。
#   格式 env `KEY_LEVELS_EXTRA`：`代码:位1,位2;代码:位1`（默认上证 3674/3681/3750）。
def _extra_levels() -> Dict[str, List[float]]:
    # ★ 2026-10-23：加入当日低点 **3574**（用户："把 3574 写进触发器"）；
    #   3674/3681 = 前双底，3750 = 双底颈线。
    raw = (os.environ.get("KEY_LEVELS_EXTRA")
           or "000001:3574,3674,3681,3750").strip()
    out: Dict[str, List[float]] = {}
    for seg in raw.split(";"):
        seg = seg.strip()
        if ":" not in seg:
            continue
        code, lv = seg.split(":", 1)
        vals = []
        for x in lv.split(","):
            try:
                vals.append(float(x.strip()))
            except (TypeError, ValueError):
                pass
        if vals:
            out[code.strip()] = vals
    return out


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS key_level_watch (
            date TEXT, code TEXT, name TEXT,
            close REAL, chg_pct REAL,
            support REAL, support_type TEXT, dist_pct REAL,
            status TEXT, broke INTEGER, first_year_low INTEGER,
            detail TEXT, created_at TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    _READY = True


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _analyze(bars: List[dict]) -> Optional[Dict]:
    closes = [_num(b.get("close")) for b in bars]
    closes = [x for x in closes if x]
    if len(closes) < 65:
        return None
    c, prev = closes[-1], closes[-2]
    prev60 = min(closes[-61:-1])
    year_low = min(closes[-250:-1]) if len(closes) >= 250 else prev60
    grid = 100.0
    round_level = math.floor(c / grid) * grid
    broke_prev = c < prev60 <= prev
    broke_round = c < round_level <= prev
    first_year_low = c < year_low
    cands = [(x, t) for x, t in ((prev60, "前低"), (round_level, "整数位"),
                                 (year_low, "年内低点")) if x < c]
    if cands:
        support, stype = max(cands, key=lambda x: x[0])
    else:
        support, stype = min((prev60, "前低"), (round_level, "整数位"),
                             (year_low, "年内低点"), key=lambda x: x[0])
    dist = (c / support - 1) * 100 if support else None
    if first_year_low:
        status = "失守年内低点"
    elif broke_prev:
        status = "失守前低"
    elif broke_round:
        status = "失守整数位"
    elif dist is not None and dist <= 1.5:
        status = "逼近支撑"
    else:
        status = "正常"
    detail = (f"{stype} {support:.1f}（{status}）；"
              f"前低 {prev60:.1f} / 整数位 {round_level:.0f} / 年内低点 {year_low:.1f}")
    return {"close": round(c, 2),
            "chg_pct": round((c / prev - 1) * 100, 2),
            "support": round(support, 2), "support_type": stype,
            "dist_pct": round(dist, 2) if dist is not None else None,
            "status": status,
            "broke": bool(broke_prev or broke_round or first_year_low),
            "first_year_low": bool(first_year_low),
            "detail": detail}


def compute(day: str = None) -> Dict:
    ensure_table()
    from app.tencent import get_kline
    rows, errors, datas = [], [], []
    for code, name in INDICES:
        try:
            bars = get_kline(code, count=300) or []
        except Exception as e:
            bars = []
            errors.append(f"{name}: {str(e)[:50]}")
        if not bars:
            errors.append(f"{name}: 无K线")
            continue
        d = str(bars[-1].get("date") or "")[:10]
        a = _analyze(bars)
        if not a:
            errors.append(f"{name}: K线不足")
            continue
        datas.append(d)
        rows.append({"code": code, "name": name, "kline_date": d, **a})
    if not rows:
        return {"ok": False, "reason": "指数K线全部不可用", "errors": errors}

    day = str(day or max(datas))[:10]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute("DELETE FROM key_level_watch WHERE date = %s", (day,))
        for r in rows:
            db.upsert(_TABLE, {
                "date": day, "code": r["code"], "name": r["name"],
                "close": r["close"], "chg_pct": r["chg_pct"],
                "support": r["support"], "support_type": r["support_type"],
                "dist_pct": r["dist_pct"], "status": r["status"],
                "broke": 1 if r["broke"] else 0,
                "first_year_low": 1 if r["first_year_low"] else 0,
                "detail": r["detail"], "created_at": now,
            }, conflict_columns=["date", "code"])
    except Exception as e:
        print(f"[keylevel] 落库失败: {str(e)[:100]}")
        return {"ok": False, "reason": f"落库失败: {str(e)[:80]}", "rows": rows}

    return {"ok": True, "date": day, "rows": rows, "errors": errors,
            "broke_n": sum(1 for r in rows if r["broke"]),
            "year_low_n": sum(1 for r in rows if r["first_year_low"])}


def load(day: str = None) -> Dict:
    ensure_table()
    try:
        if day:
            d = str(day)[:10]
            rows = db.fetch("SELECT * FROM key_level_watch WHERE date=%s "
                            "ORDER BY code", (d,))
        else:
            r = db.fetch_one("SELECT MAX(date) AS d FROM key_level_watch")
            d = str((r or {}).get("d") or "")[:10]
            rows = db.fetch("SELECT * FROM key_level_watch WHERE date=%s "
                            "ORDER BY code", (d,)) if d else []
    except Exception as e:
        return {"date": day, "rows": [], "note": f"读取失败: {str(e)[:80]}"}
    extra_map = _extra_levels()
    out = []
    for x in rows:
        code = x.get("code")
        close = _num(x.get("close"))
        chg = _num(x.get("chg_pct"))
        prev = (close / (1 + chg / 100)) if close and chg is not None else None
        extra = []
        for L in extra_map.get(code or "", []):
            extra.append({
                "level": L,
                "dist_pct": round((close / L - 1) * 100, 2) if close else None,
                "broke": bool(close and close < L),
                "reclaimed": bool(prev and close >= L > prev),
            })
        out.append({
            "code": code, "name": x.get("name"),
            "close": close, "chg_pct": chg,
            "support": _num(x.get("support")), "support_type": x.get("support_type"),
            "dist_pct": _num(x.get("dist_pct")), "status": x.get("status"),
            "broke": bool(x.get("broke")),
            "first_year_low": bool(x.get("first_year_low")),
            "detail": x.get("detail"),
            "extra": extra,
        })
    return {"date": d, "rows": out,
            "note": "观察器：关键位失守只说明结构走坏，不构成买卖信号；"
                    "失守后需看能否快速收回。"}
