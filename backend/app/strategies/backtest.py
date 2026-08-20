"""
================================================================================
【文件作用】战法回测引擎
================================================================================

对历史数据进行战法回测，验证战法有效性。

回测逻辑：
  1. 遍历历史K线（至少60个交易日）
  2. 在每个交易日，检查是否触发战法信号
  3. 触发后跟踪后续走势：
     - 达到目标价 → 盈利
     - 触及止损价 → 亏损
     - 超过最大持有天数 → 按收盘价退出
  4. 汇总统计：胜率、盈亏比、平均收益等

依赖：
  - 各战法的 detect_signal(klines, idx) 方法
  - 腾讯K线数据接口
================================================================================
"""

import json
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor

from app.database import db
from app.strategies import get_strategy, list_strategies, filter_stock_pool
from app.tencent import get_kline, _cache as tencent_cache, refresh_all_stocks


# 回测配置
BACKTEST_CONFIG = {
    "lookback_days": 120,      # 回看天数（需要足够长的历史数据）
    "max_hold_days": 10,       # 最大持有天数
    "min_market_cap": 30e8,    # 最小市值
    "min_avg_volume": 1500e4,  # 最小日均成交额
    "max_stocks": 50,          # 最多回测股票数（控制耗时）
}


def run_backtest(
    strategy_name: str,
    days: int = 60,
    stock_codes: List[str] = None,
) -> Dict:
    """
    执行战法回测。
    
    参数：
      strategy_name: 战法名称
      days: 回测天数（从最近交易日往前推）
      stock_codes: 指定股票代码列表（为空则用股票池）
    
    返回：
      {
        "strategy": "战法名称",
        "backtest_days": 60,
        "stock_count": 50,
        "signals": 12,           # 触发信号数
        "wins": 8,               # 盈利次数
        "losses": 4,             # 亏损次数
        "win_rate": 66.7,        # 胜率 %
        "avg_profit_pct": 5.2,   # 平均收益率 %
        "avg_win_pct": 8.5,      # 平均盈利 %
        "avg_loss_pct": -3.2,    # 平均亏损 %
        "profit_factor": 2.1,    # 盈亏比
        "max_profit": 15.3,      # 最大单笔盈利 %
        "max_loss": -6.8,        # 最大单笔亏损 %
        "trades": [...]          # 详细交易记录
      }
    """
    strategy = get_strategy(strategy_name)
    if not strategy:
        return {"error": f"未找到战法: {strategy_name}"}
    
    # 检查战法是否支持回测（需要有 detect_signal 方法）
    if not hasattr(strategy, "detect_signal"):
        return {"error": f"战法 {strategy_name} 不支持回测（缺少 detect_signal 方法）"}
    
    # 获取股票池
    if not stock_codes:
        # 确保行情缓存已加载
        if not tencent_cache.get("stocks"):
            refresh_all_stocks()
        pool = filter_stock_pool(
            min_market_cap=BACKTEST_CONFIG["min_market_cap"],
            min_avg_volume=BACKTEST_CONFIG["min_avg_volume"],
        )
        # pool 是列表 [{code, name, ...}] 或字典 {code: info}
        if isinstance(pool, dict):
            stock_codes = list(pool.keys())[:BACKTEST_CONFIG["max_stocks"]]
        else:
            stock_codes = [s["code"] for s in pool][:BACKTEST_CONFIG["max_stocks"]]
    
    # 执行回测
    trades = []
    for code in stock_codes:
        code_trades = _backtest_stock(strategy, code, days)
        trades.extend(code_trades)
    
    # 统计汇总
    return _calc_statistics(strategy_name, days, len(stock_codes), trades)


def _enrich_klines(klines: List[Dict]) -> List[Dict]:
    """
    补充K线计算字段。
    
    原始数据只有 open/close/high/low/volume，
    补充 change_pct, is_positive, body, upper_shadow, lower_shadow。
    """
    for i, k in enumerate(klines):
        o, c, h, l = k["open"], k["close"], k["high"], k["low"]
        
        # 涨跌幅
        if i > 0 and klines[i - 1]["close"] > 0:
            k["change_pct"] = round((c - klines[i - 1]["close"]) / klines[i - 1]["close"] * 100, 2)
        else:
            k["change_pct"] = 0
        
        # 阳线/阴线
        k["is_positive"] = c > o
        
        # 实体/影线
        k["body"] = abs(c - o)
        k["upper_shadow"] = h - max(c, o)
        k["lower_shadow"] = min(c, o) - l
    
    return klines


def _backtest_stock(strategy, code: str, days: int) -> List[Dict]:
    """
    对单只股票执行回测。
    
    返回交易记录列表：
    [{
      "code": "000001",
      "name": "平安银行",
      "signal_date": "2024-01-15",
      "entry_price": 10.5,
      "stop_loss": 10.0,
      "target_price": 12.0,
      "exit_date": "2024-01-20",
      "exit_price": 11.2,
      "exit_reason": "target",  # target/stop_loss/timeout
      "profit_pct": 6.67,
      "hold_days": 5
    }]
    """
    trades = []
    
    try:
        # 获取K线数据（需要足够长）
        klines = get_kline(code, period="day", count=BACKTEST_CONFIG["lookback_days"] + days)
        if not klines or len(klines) < 60:
            return trades
        
        # 补充计算字段（change_pct, is_positive, body, upper_shadow 等）
        klines = _enrich_klines(klines)
        
        # 从后往前遍历（最近的日期在前）
        # 回测区间：最后 days 个交易日
        start_idx = max(60, len(klines) - days)
        
        for idx in range(start_idx, len(klines) - BACKTEST_CONFIG["max_hold_days"]):
            # 检查是否触发信号
            signal = strategy.detect_signal(klines, idx)
            if not signal:
                continue
            
            # 提取信号信息
            entry_price = signal.get("entry_price") or klines[idx]["close"]
            stop_loss = signal.get("stop_loss", entry_price * 0.95)
            target_price = signal.get("target_price", entry_price * 1.10)
            
            # 跟踪后续走势
            trade = _track_trade(
                code, klines, idx, entry_price, stop_loss, target_price
            )
            if trade:
                trades.append(trade)
    
    except Exception as e:
        print(f"[backtest] {code} 回测失败: {e}")
    
    return trades


def _track_trade(
    code: str,
    klines: List[Dict],
    signal_idx: int,
    entry_price: float,
    stop_loss: float,
    target_price: float,
) -> Optional[Dict]:
    """
    跟踪一笔交易的后续走势。
    
    从 signal_idx+1 开始，检查每日高低点：
    - 最高价 >= target_price → 止盈退出
    - 最低价 <= stop_loss → 止损退出
    - 超过 max_hold_days → 超时退出
    """
    max_hold = BACKTEST_CONFIG["max_hold_days"]
    exit_reason = "timeout"
    exit_price = None
    exit_idx = None
    
    for i in range(1, max_hold + 1):
        if signal_idx + i >= len(klines):
            # 数据不足，按最后收盘价退出
            exit_idx = len(klines) - 1
            exit_price = klines[exit_idx]["close"]
            break
        
        kline = klines[signal_idx + i]
        high = kline["high"]
        low = kline["low"]
        
        # 检查是否触及目标价（优先检查目标，因为通常目标 > 止损）
        if high >= target_price:
            exit_reason = "target"
            exit_price = target_price
            exit_idx = signal_idx + i
            break
        
        # 检查是否触及止损
        if low <= stop_loss:
            exit_reason = "stop_loss"
            exit_price = stop_loss
            exit_idx = signal_idx + i
            break
    
    # 超时退出：用最后一天收盘价
    if exit_price is None:
        exit_idx = min(signal_idx + max_hold, len(klines) - 1)
        exit_price = klines[exit_idx]["close"]
        exit_reason = "timeout"
    
    # 计算收益率
    profit_pct = ((exit_price - entry_price) / entry_price) * 100
    hold_days = exit_idx - signal_idx
    
    # 获取股票名称
    name = ""
    stocks = tencent_cache.get("stocks", {})
    if code in stocks:
        name = stocks[code].get("name", "")
    
    return {
        "code": code,
        "name": name,
        "signal_date": klines[signal_idx]["date"],
        "entry_price": round(entry_price, 2),
        "stop_loss": round(stop_loss, 2),
        "target_price": round(target_price, 2),
        "exit_date": klines[exit_idx]["date"],
        "exit_price": round(exit_price, 2),
        "exit_reason": exit_reason,
        "profit_pct": round(profit_pct, 2),
        "hold_days": hold_days,
    }


def _calc_statistics(
    strategy_name: str,
    backtest_days: int,
    stock_count: int,
    trades: List[Dict],
) -> Dict:
    """计算回测统计指标"""
    if not trades:
        return {
            "strategy": strategy_name,
            "backtest_days": backtest_days,
            "stock_count": stock_count,
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_profit_pct": 0,
            "avg_win_pct": 0,
            "avg_loss_pct": 0,
            "profit_factor": 0,
            "max_profit": 0,
            "max_loss": 0,
            "trades": [],
        }
    
    wins = [t for t in trades if t["profit_pct"] > 0]
    losses = [t for t in trades if t["profit_pct"] <= 0]
    
    total_profit = sum(t["profit_pct"] for t in trades)
    avg_profit = total_profit / len(trades) if trades else 0
    
    win_profits = [t["profit_pct"] for t in wins]
    loss_profits = [t["profit_pct"] for t in losses]
    
    avg_win = sum(win_profits) / len(win_profits) if win_profits else 0
    avg_loss = sum(loss_profits) / len(loss_profits) if loss_profits else 0
    
    # 盈亏比 = 平均盈利 / 平均亏损的绝对值
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    
    return {
        "strategy": strategy_name,
        "backtest_days": backtest_days,
        "stock_count": stock_count,
        "signals": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "avg_profit_pct": round(avg_profit, 2),
        "avg_win_pct": round(avg_win, 2),
        "avg_loss_pct": round(avg_loss, 2),
        "profit_factor": round(profit_factor, 2),
        "max_profit": round(max(t["profit_pct"] for t in trades), 2) if trades else 0,
        "max_loss": round(min(t["profit_pct"] for t in trades), 2) if trades else 0,
        "trades": trades,
    }


def save_backtest_result(strategy_name: str, result: Dict) -> None:
    """保存回测结果到数据库"""
    db.upsert("strategy_backtest", {
        "strategy_name": strategy_name,
        "backtest_date": datetime.now().strftime("%Y-%m-%d"),
        "stats_json": json.dumps({
            "backtest_days": result.get("backtest_days"),
            "stock_count": result.get("stock_count"),
            "signals": result.get("signals"),
            "wins": result.get("wins"),
            "losses": result.get("losses"),
            "win_rate": result.get("win_rate"),
            "avg_profit_pct": result.get("avg_profit_pct"),
            "avg_win_pct": result.get("avg_win_pct"),
            "avg_loss_pct": result.get("avg_loss_pct"),
            "profit_factor": result.get("profit_factor"),
            "max_profit": result.get("max_profit"),
            "max_loss": result.get("max_loss"),
        }, ensure_ascii=False),
        "trades_json": json.dumps(result.get("trades", []), ensure_ascii=False),
    }, conflict_columns=["strategy_name", "backtest_date"])


def get_backtest_result(strategy_name: str, date: str = None) -> Dict:
    """获取回测结果"""
    if date:
        row = db.fetch_one(
            "SELECT * FROM strategy_backtest WHERE strategy_name = %s AND backtest_date = %s",
            (strategy_name, date)
        )
    else:
        row = db.fetch_one(
            "SELECT * FROM strategy_backtest WHERE strategy_name = %s ORDER BY backtest_date DESC LIMIT 1",
            (strategy_name,)
        )
    
    if not row:
        return {}
    
    stats = json.loads(row.get("stats_json", "{}"))
    trades = json.loads(row.get("trades_json", "[]"))
    
    return {
        "strategy": strategy_name,
        "backtest_date": row.get("backtest_date"),
        **stats,
        "trades": trades,
    }


def get_all_backtest_summary() -> List[Dict]:
    """获取所有战法的回测摘要"""
    rows = db.fetch("""
        SELECT strategy_name, backtest_date, stats_json
        FROM strategy_backtest
        ORDER BY backtest_date DESC
    """)
    
    summary = []
    for row in rows:
        stats = json.loads(row.get("stats_json", "{}"))
        summary.append({
            "strategy": row.get("strategy_name"),
            "backtest_date": row.get("backtest_date"),
            **stats,
        })
    
    return summary
