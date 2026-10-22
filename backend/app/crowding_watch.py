# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】拥挤赛道观察器（crowding watch）—— **纯观察器，不产生买卖信号**
================================================================================
背景（2026-10-23，用户 10-22 复盘）：
  "元件 30 日 +38% 的拥挤赛道，末日轮用 57% 回撤完成兑现；price_pos 的统治力再次验证。"
  ⇒ 把"哪些行业已经拥挤、拥挤到什么程度、开始回撤没有"变成每天可看的表。

口径（全部读数据包，零外部请求）：
  · 行业：`app.industry_map`（月级重建的 code→行业映射）；
  · 个股 30 日/5 日收益、120 日价格分位（pos120）、距 120 日高点回撤；
  · 行业聚合用**中位数**（抗单票极端值），仅保留成员 ≥10 的行业；
  · 拥挤分 = 0.6 × 行业 30 日收益的横截面分位 + 0.4 × 高位股占比（pos120≥0.9）；
  · 等级：≥80 高拥挤 / ≥60 关注 / 其余正常。

★ 预登记声明（红线）：不改分数、不进闸门、不改 regime、不碰模拟盘；
  删掉本模块系统其余部分照常运行。它回答"哪里拥挤"，不回答"该不该卖"。
================================================================================
"""
import importlib
import os
import tempfile
import gzip
import shutil
from datetime import datetime
from statistics import median
from typing import Dict, List, Optional

from app.database import db

_TABLE = "crowding_watch"
_READY = False


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS crowding_watch (
            date TEXT, industry TEXT, n INTEGER,
            ret5 REAL, ret30 REAL, pos_high REAL, dd120 REAL,
            score REAL, level TEXT, leaders TEXT, created_at TEXT,
            PRIMARY KEY (date, industry)
        )
    """)
    _READY = True


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _close(b) -> Optional[float]:
    if isinstance(b, dict):
        return _num(b.get("close"))
    try:
        return float(b[4])
    except (TypeError, ValueError, IndexError):
        return None


def _date(b) -> str:
    if isinstance(b, dict):
        return str(b.get("date") or "")[:10]
    try:
        return str(b[0])[:10]
    except (TypeError, IndexError):
        return ""


def _pack():
    """返回可用的 pack 模块；本地 kline 包比 pack_source 新时临时解压一份。"""
    ps = importlib.import_module("app.pack_source")
    try:
        if ps._db_fresh():
            return ps
    except Exception:
        pass
    gz = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "data", "kline", "backend-pack.db.gz")
    if os.path.exists(gz):
        tmp = os.path.join(tempfile.gettempdir(), "crowding-pack.db")
        if not os.path.exists(tmp) or os.path.getsize(tmp) < 10 * 1024 * 1024:
            with gzip.open(gz, "rb") as fi, open(tmp, "wb") as fo:
                shutil.copyfileobj(fi, fo, 1024 * 1024)
        return _PackView(tmp)
    return ps


class _PackView:
    """用 sqlite 直接读临时包（与 pack_source.get_klines 返回同构）。"""

    def __init__(self, path):
        self.path = path

    def get_codes(self):
        import sqlite3
        c = sqlite3.connect(self.path)
        try:
            return [r[0] for r in c.execute("SELECT code FROM codes")]
        finally:
            c.close()

    def get_klines(self, code):
        import sqlite3
        c = sqlite3.connect(self.path)
        try:
            rows = c.execute(
                "SELECT date, open, high, low, close, volume FROM klines "
                "WHERE code=? ORDER BY date", (code,)).fetchall()
            return [{"date": r[0], "open": r[1], "high": r[2], "low": r[3],
                     "close": r[4], "volume": r[5]} for r in rows]
        finally:
            c.close()

    def get_name_cap(self, code):
        import sqlite3
        c = sqlite3.connect(self.path)
        try:
            r = c.execute("SELECT name, market_cap FROM codes WHERE code=?",
                          (code,)).fetchone()
            return (r[0] if r else "", r[1] if r else 0)
        finally:
            c.close()


def _industry_of() -> Dict[str, str]:
    try:
        from app.industry_map import get_industry_map
        return get_industry_map() or {}
    except Exception:
        return {}


def compute(day: str = None) -> Dict:
    ensure_table()
    ps = _pack()
    imap = _industry_of()
    if not imap:
        return {"ok": False, "reason": "行业映射为空"}
    groups: Dict[str, list] = {}
    data_day = ""
    codes = [c for c in (ps.get_codes() or [])
             if str(c).isdigit() and len(str(c)) == 6]
    for i, code in enumerate(codes, 1):
        bars = ps.get_klines(code) or []
        if len(bars) < 131:
            continue
        closes = [x for x in (_close(b) for b in bars) if x]
        if len(closes) < 131:
            continue
        if not data_day:
            data_day = _date(bars[-1])
        c = closes[-1]
        ret5 = c / closes[-6] - 1
        ret30 = c / closes[-31] - 1
        hi = max(closes[-120:])
        lo = min(closes[-120:])
        pos = (c - lo) / (hi - lo) if hi > lo else None
        dd = c / hi - 1 if hi else None
        ind = imap.get(str(code)) or ""
        if not ind or ind in ("其它", "综合", "综合行业"):
            continue
        groups.setdefault(ind, []).append(
            {"code": str(code), "ret5": ret5, "ret30": ret30,
             "pos": pos, "dd": dd})
        if i % 800 == 0:
            print(f"[crowding] 读取 {i}/{len(codes)} …")

    rows = []
    for ind, arr in groups.items():
        if len(arr) < 10:
            continue
        r5 = median([x["ret5"] for x in arr])
        r30 = median([x["ret30"] for x in arr])
        pos_high = sum(1 for x in arr if (x["pos"] or 0) >= 0.9) / len(arr)
        dds = [x["dd"] for x in arr if x["dd"] is not None]
        dd = median(dds) if dds else 0.0
        leaders = [x["code"] for x in sorted(arr, key=lambda y: -y["ret30"])[:3]]
        rows.append({"industry": ind, "n": len(arr),
                     "ret5": round(r5 * 100, 2), "ret30": round(r30 * 100, 2),
                     "pos_high": round(pos_high, 2), "dd120": round(dd * 100, 2),
                     "leaders": leaders})
    if not rows:
        return {"ok": False, "reason": "无有效行业（包/映射不可用）"}

    ordered = sorted(rows, key=lambda x: x["ret30"])
    n = len(ordered)
    med_dd = median([r["dd120"] for r in rows])
    for i, r in enumerate(ordered):
        pct = i / max(n - 1, 1)
        r["score"] = round(60 * pct + 40 * r["pos_high"], 1)
        # ★ 2026-10-23：加绝对/相对门槛 —— 普跌市里"最不跌"的行业也会拿到 60 分，
        #   只看分位会把 -3% 的行业标成"关注"（10-22 实测）；同样，全市场都在回撤时
        #   "dd≤-25%" 会把 21/38 个行业都标成退潮（也实测过，太宽）。
        #   · 退潮 = 回撤**比全行业中位数还差 10pp** 且近 5 日仍跌（真正的末日轮兑现）
        #   · 高拥挤 = 分位高 **且** 30 日仍涨 ≥10%（真拥挤）
        #   · 升温 = 分位 ≥60 **且** 30 日涨 ≥3%
        if r["dd120"] <= med_dd - 10 and r["ret5"] < 0:
            r["level"] = "退潮"
        elif r["score"] >= 80 and r["ret30"] >= 10:
            r["level"] = "高拥挤"
        elif r["score"] >= 60 and r["ret30"] >= 3:
            r["level"] = "升温"
        else:
            r["level"] = "正常"

    # 龙头名称（最多 3×行业数，量可控）
    for r in rows:
        named = []
        for code in r["leaders"]:
            try:
                nm, _cap = ps.get_name_cap(code)
            except Exception:
                nm = ""
            named.append(f"{nm}({code})" if nm else code)
        r["leaders"] = named

    day = str(day or data_day or datetime.now().strftime("%Y-%m-%d"))[:10]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        db.execute("DELETE FROM crowding_watch WHERE date = %s", (day,))
        for r in rows:
            db.upsert(_TABLE, {
                "date": day, "industry": r["industry"], "n": r["n"],
                "ret5": r["ret5"], "ret30": r["ret30"],
                "pos_high": r["pos_high"], "dd120": r["dd120"],
                "score": r["score"], "level": r["level"],
                "leaders": ",".join(r["leaders"]), "created_at": now,
            }, conflict_columns=["date", "industry"])
    except Exception as e:
        print(f"[crowding] 落库失败: {str(e)[:100]}")
        return {"ok": False, "reason": f"落库失败: {str(e)[:80]}", "rows": rows}

    top = sorted(rows, key=lambda x: -x["score"])[:5]
    collapse = sorted(rows, key=lambda x: x["dd120"])[:5]
    return {"ok": True, "date": day, "n_industries": len(rows),
            "top": top, "collapse": collapse,
            "high_n": sum(1 for r in rows if r["level"] in ("高拥挤", "升温")),
            "ebb_n": sum(1 for r in rows if r["level"] == "退潮")}


def load(day: str = None, limit: int = 60) -> Dict:
    ensure_table()
    try:
        if day:
            rows = db.fetch("SELECT * FROM crowding_watch WHERE date=%s "
                            "ORDER BY score DESC LIMIT %s", (str(day)[:10], limit))
            d = str(day)[:10]
        else:
            r = db.fetch_one("SELECT MAX(date) AS d FROM crowding_watch")
            d = str((r or {}).get("d") or "")[:10]
            rows = db.fetch("SELECT * FROM crowding_watch WHERE date=%s "
                            "ORDER BY score DESC LIMIT %s", (d, limit)) if d else []
    except Exception as e:
        return {"date": day, "rows": [], "note": f"读取失败: {str(e)[:80]}"}
    out = []
    for x in rows:
        out.append({
            "industry": x.get("industry"), "n": x.get("n"),
            "ret5": _num(x.get("ret5")), "ret30": _num(x.get("ret30")),
            "pos_high": _num(x.get("pos_high")), "dd120": _num(x.get("dd120")),
            "score": _num(x.get("score")), "level": x.get("level"),
            "leaders": (x.get("leaders") or "").split(",") if x.get("leaders") else [],
        })
    return {"date": d, "rows": out,
            "note": "观察器：拥挤≠卖出信号；高拥挤=30日涨幅分位高+高位股占比高，需自行结合量价。"}
