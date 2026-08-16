"""
================================================================================
【文件作用】板块数据路由
================================================================================

URL 前缀 /api/sector，数据源：东方财富（见 app/eastmoney.py）。
返回结构统一为 {"data": [...], "total": N}，与 market/score 等接口保持一致，
前端用 `const { data } = await getXxx(); data.data` 取列表。

接口列表：
  GET /api/sector/industry        → 行业板块列表（按涨跌幅降序）
  GET /api/sector/concept         → 概念板块列表
  GET /api/sector/industry-flow   → 行业板块资金流（按主力净流入降序）
  GET /api/sector/concept-flow    → 概念板块资金流

说明：这些端点同步执行（内部用 requests 抓取东方财富）。
FastAPI 会把同步 def 自动放到线程池跑，不会阻塞事件循环。
================================================================================
"""

from fastapi import APIRouter, Query
from app.eastmoney import get_sectors, get_sector_flow

router = APIRouter()


def _wrap(rows: list) -> dict:
    """统一包装成 {data, total}。"""
    return {"data": rows, "total": len(rows)}


@router.get("/industry")
def sector_industry(limit: int = Query(200, ge=1, le=500)):
    """行业板块列表（代码/名称/涨跌幅/涨跌家数/领涨股等）。"""
    return _wrap(get_sectors("industry", limit=limit))


@router.get("/concept")
def sector_concept(limit: int = Query(200, ge=1, le=500)):
    """概念板块列表。"""
    return _wrap(get_sectors("concept", limit=limit))


@router.get("/industry-flow")
def sector_industry_flow(limit: int = Query(200, ge=1, le=500)):
    """行业板块资金流（主力/超大单/大单/中单/小单净流入）。"""
    return _wrap(get_sector_flow("industry", limit=limit))


@router.get("/concept-flow")
def sector_concept_flow(limit: int = Query(200, ge=1, le=500)):
    """概念板块资金流。"""
    return _wrap(get_sector_flow("concept", limit=limit))
