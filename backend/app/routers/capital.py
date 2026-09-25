"""
================================================================================
【文件作用】资金流向路由
================================================================================

URL 前缀 /api/capital，数据源：东方财富（见 app/eastmoney.py）。

接口列表：
  GET /api/capital                       → 说明信息
  GET /api/capital/northbound            → 北向资金（沪深港通当日净流入 + 分时序列）
  GET /api/capital/main-flow             → 个股主力资金流向排名
  GET /api/capital/northbound-holdings   → 北向持股明细（占位，暂未实现）
  GET /api/capital/dragon-tiger          → 龙虎榜（★ 2026-09-25 已实现，读库 lhb_history）

返回结构：列表类统一 {data, total}；northbound 返回单个对象。

同步端点（内部用 requests 抓东方财富），FastAPI 自动放线程池，不阻塞事件循环。
================================================================================
"""

import json

from fastapi import APIRouter, Query
from app.eastmoney import get_northbound, get_stock_flow

router = APIRouter()


@router.get("")
def capital_root():
    """根路径：可用接口说明。"""
    return {
        "msg": "资金流向数据",
        "endpoints": {
            "northbound": "/api/capital/northbound （北向资金实时）",
            "main_flow": "/api/capital/main-flow?order=desc&limit=100 （个股主力资金流向）",
        },
    }


@router.get("/northbound")
def northbound():
    """
    北向资金实时净流入。

    返回 {time, sh_net, sz_net, total_net, series:[...]}，金额单位：元。
    休市/非交易时段净流入为 0；数据不可用时返回 {}。
    """
    return get_northbound() or {}


@router.get("/main-flow")
def main_flow(
    order: str = Query("desc", description="desc=主力净流入最多；asc=净流出最多"),
    limit: int = Query(100, ge=1, le=500),
):
    """
    个股主力资金流向排名。

    order=desc：资金涌入榜（主力净流入最多）
    order=asc ：资金出逃榜（主力净流出最多）
    """
    if order not in ("desc", "asc"):
        order = "desc"
    rows = get_stock_flow(order=order, limit=limit)
    return {"data": rows, "total": len(rows)}


@router.get("/northbound-holdings")
def northbound_holdings():
    """北向持股明细（数据量较大，暂未实现）。"""
    return {"data": [], "msg": "北向持股明细暂未实现"}


def _seats_of(v):
    """席位明细的**兼容读取**：PG 的 JSON 列经 psycopg2 读出来**已经是 list/dict**；
    SQLite 则存 TEXT，需要 `json.loads`。⇒ 两种形态都要能吃（实测踩过：
    PG 下对该列做 `NOT IN ('', ...)` 字符串比较会报 `invalid input syntax for type json`）。
    """
    if v is None:
        return []
    if isinstance(v, (list, dict)):
        return v
    try:
        return json.loads(v)
    except (ValueError, TypeError):
        return []


@router.get("/dragon-tiger")
def dragon_tiger(
    date: str = Query(None, description="交易日 YYYY-MM-DD；缺省=库内最新"),
    limit: int = Query(30, ge=1, le=200),
    code: str = Query(None, description="传个股代码则返回该股上榜历史（含席位明细，如有）"),
):
    """龙虎榜（**读库** `lhb_history`，不是实时抓取 —— 数据由每日 17:45 同步）。

    ★ 2026-09-25 实现（此前是占位"暂未实现"）。动机（用户）：
      "龙虎榜：昨日知名游资/机构席位动向，短线盘前必看，目前完全没有。"
      而数据层 `mainforce/lhb.py` 早已完备（实测 **8004 行 / 101 个交易日**）。

    两种用法：
      · 不传 `code` ⇒ 某日「净买 Top / 净卖 Top」+ 当日统计 + 席位覆盖率；
      · 传 `code`   ⇒ 该股最近 `limit` 条上榜记录（含席位明细，如有）。

    ⚠️ 两个必须如实告知的事实：
      ① 席位明细**只对池内个股**富化（实测覆盖 **163/8004 ≈ 2%**）⇒ 返回 `seats_count`
         让人知道"今天有几只有席位"，而不是误以为全都没有/全都有；
      ② 金额字段 `net_buy` 等的单位是**元** ⇒ 一并给出 `*_wan`（万元）避免前端自己猜。
    """
    from app.database import db
    out = {"date": None, "limit": limit, "mode": "day",
           "top_buy": [], "top_sell": [], "stats": {}, "seats_count": 0,
           "dates": [], "note": None}
    try:
        if code:
            # ── 个股模式：该股上榜历史 ──
            out["mode"] = "stock"
            out["code"] = code
            rows = db.fetch(
                "SELECT code, name, date, net_buy, buy_total, sell_total, quote_change, "
                "up_reason, concepts, turnover_ratio, seats_json FROM lhb_history "
                "WHERE code = %s ORDER BY date DESC LIMIT %s", (code, limit))
            items = []
            for r in rows or []:
                seats = _seats_of(r.get("seats_json"))
                _nb = r.get("net_buy")
                try:
                    _nb = float(_nb) if _nb is not None else None
                except (TypeError, ValueError):
                    _nb = None
                items.append({
                    "date": str(r.get("date") or ""), "name": r.get("name"),
                    "net_buy": _nb,
                    "net_buy_wan": (round(_nb / 10000) if _nb is not None else None),
                    "buy_total": r.get("buy_total"), "sell_total": r.get("sell_total"),
                    "quote_change": r.get("quote_change"),
                    "up_reason": r.get("up_reason"), "concepts": r.get("concepts"),
                    "turnover_ratio": r.get("turnover_ratio"),
                    "seats": seats,
                    "seats_count": len(seats) if isinstance(seats, list) else 0,
                })
            out["data"] = items
            out["total"] = len(items)
            if not items:
                out["note"] = "该股暂无龙虎榜记录"
            return out

        # ── 日榜模式 ──
        if not date:
            r = db.fetch_one("SELECT MAX(date) AS d FROM lhb_history")
            date = str((r or {}).get("d") or "")
        if not date:
            out["note"] = "龙虎榜表为空（可能尚未同步）"
            return out
        out["date"] = date
        try:
            ds = db.fetch("SELECT DISTINCT date FROM lhb_history ORDER BY date DESC LIMIT 30")
            out["dates"] = [str(x.get("date") or "") for x in (ds or [])]
        except Exception:
            out["dates"] = []

        def _num(v):
            """金额字段统一转 float —— ⚠️ PG 的 SUM(numeric) 回的是 **Decimal**，
            直接塞进响应会被序列化成字符串（前端拿到 '1211695537' 而不是数字）。"""
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        def _fmt(r):
            seats = _seats_of(r.get("seats_json"))
            nb = _num(r.get("net_buy"))
            return {
                "code": r.get("code"), "name": r.get("name"),
                "net_buy": nb,
                "net_buy_wan": (round(nb / 10000) if nb is not None else None),
                "buy_total": _num(r.get("buy_total")), "sell_total": _num(r.get("sell_total")),
                "quote_change": _num(r.get("quote_change")),
                "up_reason": r.get("up_reason"), "concepts": r.get("concepts"),
                "turnover_ratio": _num(r.get("turnover_ratio")),
                "seats_count": len(seats) if isinstance(seats, list) else 0,
                "seats": seats,
            }

        cols = ("code, name, date, net_buy, buy_total, sell_total, quote_change, "
                "up_reason, concepts, turnover_ratio, seats_json")
        # ⚠️ 排序用 COALESCE 而非 `NULLS LAST` —— 后者是 **PG 专有语法，SQLite 不支持**
        #   （项目要求双库兼容，db 层只转占位符、不转方言）。
        out["top_buy"] = [_fmt(r) for r in db.fetch(
            f"SELECT {cols} FROM lhb_history WHERE date = %s "
            f"ORDER BY COALESCE(net_buy, 0) DESC LIMIT %s", (date, limit)) or []]
        out["top_sell"] = [_fmt(r) for r in db.fetch(
            f"SELECT {cols} FROM lhb_history WHERE date = %s "
            f"ORDER BY COALESCE(net_buy, 0) ASC LIMIT %s", (date, limit)) or []]
        st = db.fetch_one(
            "SELECT COUNT(*) AS n, SUM(net_buy) AS net, "
            "SUM(CASE WHEN seats_json IS NOT NULL AND seats_json::text NOT IN ('[]', 'null') "
            "THEN 1 ELSE 0 END) AS seats_n "
            "FROM lhb_history WHERE date = %s", (date,))
        st = st or {}
        _netsum = _num(st.get("net"))          # ← Decimal ⇒ float（见 _num 注释）
        out["stats"] = {"count": st.get("n") or 0,
                        "net_buy_sum": _netsum,
                        "net_buy_sum_wan": (round(_netsum / 10000) if _netsum is not None else None)}
        out["seats_count"] = st.get("seats_n") or 0
        out["total"] = len(out["top_buy"])
        if not out["total"]:
            out["note"] = f"{date} 无龙虎榜数据（可能非交易日或尚未同步）"
        elif not out["seats_count"]:
            out["note"] = "当日无席位明细（席位仅对池内个股富化，属正常）"
    except Exception as e:
        # ASCII（项目铁律⑥）
        print(f"[capital] dragon-tiger failed: {e}")
        out["note"] = "读取失败"
    return out
