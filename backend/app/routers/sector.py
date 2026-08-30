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
  GET /api/sector/stock-industry/{code} → 个股→行业归属（主行业 + 层级链）
  GET /api/sector/industry-map/stats    → 映射表统计（构建时间/覆盖数/分布）
  POST /api/sector/industry-map/build   → 手动重建映射表（调度器每月1号自动跑）

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


# ── 个股 → 行业映射表（本地反建，供评分引擎算板块分化因子用）──

@router.get("/stock-industry/{code}")
def stock_industry(code: str):
    """
    查单只股票的行业归属。
    返回 {code, name, main_industry（最细层）, industry_chain（从细到粗，如
    氮肥>农化制品>基础化工）, industry_codes}；无记录返回空 dict。
    """
    from app.sector_industry import get_stock_industry
    return get_stock_industry(code)


@router.get("/industry-map/stats")
def industry_map_stats():
    """映射表统计：构建时间 / 覆盖股票数 / 板块数 / 主行业分布 Top10。"""
    from app.sector_industry import get_stats
    return get_stats()


@router.post("/industry-map/build")
def industry_map_build(verbose: bool = Query(False)):
    """
    手动重建映射表（正常由调度器每月 1 号 03:00 自动跑）。

    ★ 耗时较长：约 100 个板块 × 分页，带间隔防东财限流，实测 2-5 分钟。
      不要频繁调用 —— 连续高频请求会让东财临时封 IP（封禁会连累盘中板块资金流）。
    """
    from app.sector_industry import build_map
    return build_map(verbose=verbose)


# ── 板块每日快照（历史序列：板块分化度 / 板块动量的数据基础）──

@router.get("/snapshot-stats")
def sector_snapshot_stats():
    """快照表概况：已积累多少天、多少行、最新日期（判断历史序列攒够了没）。"""
    from app.sector_industry import snapshot_stats
    return snapshot_stats()


@router.get("/history/{code}")
def sector_history(code: str, days: int = Query(30, ge=1, le=365)):
    """
    单板块历史序列（日期正序，最新在后）。
    例：/api/sector/history/BK1206?days=60 看"基础化工"板块近 60 天走势。
    """
    from app.sector_industry import get_sector_history
    rows = get_sector_history(code, days)
    return {"data": rows, "total": len(rows)}


@router.get("/snapshot/{date}")
def sector_snapshot(date: str, kind: str = Query("industry"),
                    limit: int = Query(500, ge=1, le=1000)):
    """某交易日的全部板块快照（按涨跌幅降序）。date 格式 YYYY-MM-DD。"""
    from app.sector_industry import get_date_snapshot
    return _wrap(get_date_snapshot(date, kind, limit))


@router.get("/dispersion")
def sector_dispersion(date: str = Query(None), kind: str = Query("industry")):
    """
    ★ 板块分化度：当日各板块涨跌幅的标准差 / 最强最弱差距 / 上涨板块占比。

    分化高 = 结构性行情（选对板块很关键，选错就亏）
    分化低 = 系统性行情（普涨普跌，仓位比选板块重要）
    不传 date 则取今天（无数据会提示快照不足）。
    """
    from app.sector_industry import dispersion
    return dispersion(date, kind)


@router.post("/snapshot/take")
def sector_snapshot_take(date: str = Query(None)):
    """
    手动记录一次板块快照（正常由调度器每个交易日 15:10 自动跑）。
    ★ 东财不可用（降级新浪）时会跳过 —— 避免两套代码体系混进同一张表。
    """
    from app.sector_industry import take_snapshot
    return take_snapshot(date)
