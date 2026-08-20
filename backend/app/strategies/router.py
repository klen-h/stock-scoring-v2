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
from app.strategies.market_regime import (
    detect_market_regime,
    get_strategy_recommendation,
    TRENDING_STRATEGIES,
    OSCILLATING_STRATEGIES,
)
from app.strategies.support_resistance import find_support_resistance
from app.strategies.rsi import calc_rsi_signals
from app.strategies.signal_confirmation import confirm_signal, batch_confirm_signals
from app.strategies.signal_persistence import update_persistence, get_persistence_summary, get_top_persistent_signals
from app.strategies.exit_alert import check_exit_alerts, get_exit_summary_for_watchlist
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
    results = strategy.scan(pool)
    
    # 对每个信号进行共振验证
    if results:
        print(f"[strategies] {strategy.name} 产生 {len(results)} 个信号，正在进行共振验证...")
        results = batch_confirm_signals(results)
        
        # 统计各等级信号数量
        grade_counts = {}
        for r in results:
            grade = r.get("signal_grade", "D")
            grade_counts[grade] = grade_counts.get(grade, 0) + 1
        print(f"[strategies] 共振验证完成: {grade_counts}")
        
        # 更新信号持久度（连续上榜追踪）
        print(f"[strategies] 正在更新信号持久度...")
        results = update_persistence(strategy.name, results)
        trust_counts = {}
        for r in results:
            tg = r.get("trust_grade", "D")
            trust_counts[tg] = trust_counts.get(tg, 0) + 1
        print(f"[strategies] 持久度更新完成: {trust_counts}")
    
    return results


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 市场状态识别
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/market/regime")
def get_market_regime():
    """
    获取当前市场状态。
    
    返回：
      - regime: "trending" / "oscillating" / "transition"
      - confidence: 置信度 0-100
      - adx: ADX 指标值
      - bb_width: 布林带宽度
      - volatility: 波动率
      - recommended_strategies: 推荐战法列表
    """
    result = detect_market_regime()
    return {"data": result}


@router.get("/market/strategy-types")
def get_strategy_types():
    """
    获取战法分类（趋势市 / 震荡市）。
    """
    return {
        "data": {
            "trending": TRENDING_STRATEGIES,
            "oscillating": OSCILLATING_STRATEGIES,
        }
    }


@router.get("/{strategy_name}/recommendation")
def get_strategy_rec(strategy_name: str):
    """
    获取单个战法的适用性建议。
    
    根据当前市场状态，评估该战法是否适合使用。
    """
    result = get_strategy_recommendation(strategy_name)
    return {"data": result}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 支撑阻力位 + RSI 指标
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/{code}/support-resistance")
def get_support_resistance(code: str, lookback: int = Query(60, ge=20, le=120)):
    """
    获取股票的支撑阻力位。
    
    参数：
      code: 股票代码
      lookback: 回看天数（默认 60）
    
    返回：
      - levels: 关键价位列表（价格、类型、强度）
      - current_price: 当前价格
      - position_pct: 在区间中的位置 (0-100)
      - suggestion: 交易建议
    """
    result = find_support_resistance(code, lookback_days=lookback)
    return {"data": result}


@router.get("/{code}/rsi")
def get_rsi_signals(code: str, period: int = Query(14, ge=5, le=30)):
    """
    获取股票的 RSI 指标和信号。
    
    参数：
      code: 股票代码
      period: RSI 周期（默认 14）
    
    返回：
      - current_rsi: 当前 RSI 值
      - zone: 超买/超卖/中性
      - signal: 交易信号（金叉/死叉/背离）
      - interpretation: 解读
    """
    result = calc_rsi_signals(code, period=period)
    return {"data": result}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 信号持久度 + 撤退提醒
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/{strategy_name}/persistence")
def get_persistence_api(strategy_name: str):
    """
    获取战法的信号持久度摘要。
    
    返回各连续天数的信号数量统计。
    """
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    result = get_persistence_summary(strategy_name)
    return {"data": result}


@router.get("/{strategy_name}/persistent-signals")
def get_persistent_signals_api(strategy_name: str, min_days: int = Query(3, ge=1, le=10)):
    """
    获取连续上榜天数 >= min_days 的信号（强者恒强）。
    """
    strategy = get_strategy(strategy_name)
    if not strategy:
        raise HTTPException(status_code=404, detail=f"未找到战法: {strategy_name}")
    
    result = get_top_persistent_signals(strategy_name, min_days=min_days)
    return {"data": result}


@router.post("/exit-alerts")
def check_exit_alerts_api(positions: list = Body(...)):
    """
    检查持仓的撤退信号。
    
    请求体：[{code, name, entry_price, stop_loss, target_price}, ...]
    """
    alerts = check_exit_alerts(positions)
    return {"data": alerts}


@router.post("/exit-summary")
def exit_summary_api(positions: list = Body(...)):
    """
    为持仓列表生成撤退摘要。
    """
    result = get_exit_summary_for_watchlist(positions)
    return {"data": result}
