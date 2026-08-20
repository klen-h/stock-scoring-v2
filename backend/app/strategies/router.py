"""
================================================================================
【文件作用】战法选股路由
================================================================================

URL 前缀 /api/strategies：
  GET /api/strategies/list              → 列出所有已注册战法
  GET /api/strategies/{name}/scan       → 执行战法扫描
  GET /api/strategies/{name}/result     → 获取最近一次扫描结果
  GET /api/strategies/{name}/watch      → 获取观察池
  POST /api/strategies/{name}/watch     → 更新观察池
  GET /api/strategies/{name}/detail/{code} → 获取个股详情（K线+关键价位）

扫描是耗时操作（需要拉取大量K线），建议：
  1. 盘后通过定时任务自动执行（scheduler.py）
  2. 前端手动触发时显示进度
  3. 结果缓存，避免重复扫描
================================================================================
"""

import asyncio
from datetime import datetime
from fastapi import APIRouter, Query, BackgroundTasks, HTTPException, Body
from typing import Optional

from app.strategies import (
    list_strategies,
    get_strategy,
    filter_stock_pool,
    save_scan_result,
    get_scan_result,
    get_watch_pool,
    save_watch_pool,
    get_kline_with_indicators,
    calc_position_in_range,
)
from app.strategies.backtest import (
    run_backtest,
    save_backtest_result,
    get_backtest_result,
    get_all_backtest_summary,
)
from app.tencent import _cache as tencent_cache, refresh_all_stocks

router = APIRouter()

# 扫描状态（防止重复扫描）
_scan_status = {}


@router.get("/list")
def strategies_list():
    """列出所有已注册战法"""
    return {"data": list_strategies()}


@router.get("/{strategy_name}/scan")
async def scan_strategy(
    strategy_name: str,
    background_tasks: BackgroundTasks,
    min_market_cap: float = Query(20e8, description="最小市值（元）"),
    min_avg_volume: float = Query(1000e4, description="最小日均成交额（元）"),
    force: bool = Query(False, description="强制重新扫描（忽略缓存）"),
):
    """
    执行战法扫描。
    
    扫描是耗时操作，默认在后台执行。
    如果 force=False 且今日已有结果，直接返回缓存。
    """
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    # 检查是否已有今日结果
    if not force:
        cached = get_scan_result(strategy_name)
        if cached.get("date") == datetime.now().strftime("%Y-%m-%d"):
            return {
                "data": cached["results"],
                "total": cached["count"],
                "scan_date": cached["date"],
                "cached": True,
            }
    
    # 检查是否正在扫描
    if _scan_status.get(strategy_name, {}).get("scanning"):
        return {
            "data": [],
            "total": 0,
            "scanning": True,
            "message": "扫描进行中，请稍后刷新",
        }
    
    # 后台执行扫描
    background_tasks.add_task(
        _run_scan,
        strategy_name,
        min_market_cap,
        min_avg_volume,
    )
    
    return {
        "data": [],
        "total": 0,
        "scanning": True,
        "message": "扫描已启动，请稍后刷新查看结果",
    }


async def _run_scan(strategy_name: str, min_market_cap: float, min_avg_volume: float):
    """后台执行扫描"""
    _scan_status[strategy_name] = {"scanning": True, "started_at": datetime.now().isoformat()}
    
    try:
        strategy = get_strategy(strategy_name)
        if not strategy:
            return
        
        # 确保行情缓存已加载（后端重启后缓存为空，需要先刷新股票列表）
        from app.tencent import _cache as tencent_cache, refresh_all_stocks
        if not tencent_cache.get("stocks"):
            print(f"[strategies] 行情缓存为空，正在刷新股票列表...")
            await asyncio.to_thread(refresh_all_stocks)
            print(f"[strategies] 股票列表刷新完成: {len(tencent_cache.get('stocks', {}))} 只")
        
        # 在线程池中执行同步扫描（避免阻塞事件循环）
        results = await asyncio.to_thread(
            _do_scan,
            strategy,
            min_market_cap,
            min_avg_volume,
        )
        
        # 保存结果
        save_scan_result(strategy_name, results)
        print(f"[strategies] {strategy_name} 扫描完成: {len(results)} 只")
        
    except Exception as e:
        print(f"[strategies] {strategy_name} 扫描失败: {e}")
    finally:
        _scan_status[strategy_name] = {"scanning": False}


def _do_scan(strategy, min_market_cap: float, min_avg_volume: float):
    """同步执行扫描（在线程池中运行）"""
    # 过滤股票池
    pool = filter_stock_pool(
        min_market_cap=min_market_cap,
        min_avg_volume=min_avg_volume,
    )
    
    # 执行策略扫描
    return strategy.scan(pool)


@router.get("/{strategy_name}/result")
def get_strategy_result(strategy_name: str):
    """获取最近一次扫描结果"""
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    cached = get_scan_result(strategy_name)
    if not cached:
        return {"data": [], "total": 0, "message": "暂无扫描结果，请先执行扫描"}
    
    return {
        "data": cached["results"],
        "total": cached["count"],
        "scan_date": cached["date"],
        "cached": True,
    }


@router.get("/{strategy_name}/watch")
def get_strategy_watch(strategy_name: str):
    """获取观察池"""
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    pool = get_watch_pool(strategy_name)
    return {
        "data": pool.get("stocks", []),
        "date": pool.get("date"),
    }


@router.post("/{strategy_name}/watch")
def update_strategy_watch(strategy_name: str, stocks: list = Body(...)):
    """
    更新观察池。
    
    请求体：[{code, name, entry_price, stop_loss, target_price, ...}, ...]
    """
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    save_watch_pool(strategy_name, stocks)
    return {"success": True, "count": len(stocks)}


@router.get("/{strategy_name}/detail/{code}")
def get_stock_detail(strategy_name: str, code: str):
    """
    获取个股详情（K线 + 关键价位标注）。
    
    用于详情页展示最近K线图形和买卖点标注。
    """
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    # 获取K线
    klines = get_kline_with_indicators(code, count=20)
    if not klines:
        raise HTTPException(status_code=404, detail=f"未找到K线数据: {code}")
    
    # 计算位置
    position = calc_position_in_range(klines, lookback=60)
    
    # 获取扫描结果中的关键价位（如有）
    cached = get_scan_result(strategy_name)
    signal = None
    for s in cached.get("results", []):
        if s["code"] == code:
            signal = s
            break
    
    return {
        "code": code,
        "klines": klines[-10:],  # 最近10根K线
        "position_pct": position,
        "signal": signal,  # 包含 entry_price, stop_loss, target_price 等
    }


@router.get("/{strategy_name}/status")
def get_scan_status(strategy_name: str):
    """获取扫描状态"""
    status = _scan_status.get(strategy_name, {})
    return {
        "scanning": status.get("scanning", False),
        "started_at": status.get("started_at"),
    }


# ================================================================
#  回测接口
# ================================================================

@router.get("/{strategy_name}/backtest")
async def backtest_strategy(
    strategy_name: str,
    background_tasks: BackgroundTasks,
    days: int = Query(60, description="回测天数"),
    force: bool = Query(False, description="强制重新回测"),
):
    """
    执行战法回测。
    
    回测是耗时操作，默认在后台执行。
    如果 force=False 且今日已有结果，直接返回缓存。
    """
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    # 检查是否支持回测
    if not hasattr(strategy, "detect_signal"):
        raise HTTPException(status_code=400, detail=f"战法 {strategy_name} 不支持回测")
    
    # 检查是否已有今日结果
    if not force:
        cached = get_backtest_result(strategy_name)
        if cached and cached.get("backtest_date") == datetime.now().strftime("%Y-%m-%d"):
            return {"data": cached, "cached": True}
    
    # 检查是否正在回测
    if _scan_status.get(f"{strategy_name}_backtest", {}).get("scanning"):
        return {
            "data": {},
            "scanning": True,
            "message": "回测进行中，请稍后刷新",
        }
    
    # 后台执行回测
    background_tasks.add_task(_run_backtest, strategy_name, days)
    
    return {
        "data": {},
        "scanning": True,
        "message": "回测已启动，请稍后刷新查看结果",
    }


async def _run_backtest(strategy_name: str, days: int):
    """后台执行回测"""
    _scan_status[f"{strategy_name}_backtest"] = {"scanning": True, "started_at": datetime.now().isoformat()}
    
    try:
        # 确保行情缓存已加载
        if not tencent_cache.get("stocks"):
            print(f"[backtest] 行情缓存为空，正在刷新股票列表...")
            await asyncio.to_thread(refresh_all_stocks)
        
        # 在线程池中执行回测
        result = await asyncio.to_thread(run_backtest, strategy_name, days)
        
        if "error" not in result:
            save_backtest_result(strategy_name, result)
            print(f"[backtest] {strategy_name} 回测完成: {result.get('signals')} 个信号, 胜率 {result.get('win_rate')}%")
    
    except Exception as e:
        print(f"[backtest] {strategy_name} 回测失败: {e}")
    finally:
        _scan_status[f"{strategy_name}_backtest"] = {"scanning": False}


@router.get("/{strategy_name}/backtest/result")
def get_backtest_result_api(strategy_name: str):
    """获取回测结果"""
    result = get_backtest_result(strategy_name)
    if not result:
        return {"data": {}, "message": "暂无回测结果，请先执行回测"}
    return {"data": result, "cached": True}


@router.get("/backtest/summary")
def get_backtest_summary():
    """获取所有战法的回测摘要"""
    summary = get_all_backtest_summary()
    return {"data": summary}
