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
  GET /api/capital/dragon-tiger          → 龙虎榜（占位，暂未实现）

返回结构：列表类统一 {data, total}；northbound 返回单个对象。

同步端点（内部用 requests 抓东方财富），FastAPI 自动放线程池，不阻塞事件循环。
================================================================================
"""

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


@router.get("/dragon-tiger")
def dragon_tiger():
    """龙虎榜数据（暂未实现）。"""
    return {"data": [], "msg": "龙虎榜暂未实现"}
