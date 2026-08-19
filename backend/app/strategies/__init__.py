"""
================================================================================
【文件作用】战法选股模块
================================================================================

提供多种量化战法的选股扫描功能。

已实现战法：
  - advance2retreat1: 进二退一（低位放量大阳后缩量回调）
  - dragon_turnaround: 龙回头（龙头股第一波拉升后缩量回调，止跌反包时介入）
  - limit_up_boomerang: 涨停回马枪（涨停突破后缩量回调不破实体，放量反包时介入）
  - wizard_pointer: 仙人指路（强势股拉升途中长上影试盘，次日高开高走确认）
  - double_cannon: 涨停双响炮（首板涨停后缩量回调，再度涨停启动主升浪）

震荡市备选战法：
  - single_yang_unbroken: 单阳不破（一根大阳后横盘不破低点，缩量突破时介入）
  - ma_pullback: 均线回踩（上升趋势中缩量回踩均线不破，放量反弹时低吸）
  - old_duck_head: 老鸭头（5/10/60日均线多头排列后，缩量回调不破60日线，金叉放量启动）
  - ma_convergence_breakout: 均线粘合突破（多均线粘合横盘后放量突破平台上沿）
  - morning_star: 早晨之星（下跌末期长阴+跳空十字星+放量中阳的底部反转信号）

使用方式：
  from app.strategies import get_strategy, list_strategies, filter_stock_pool
  
  # 获取股票池
  pool = filter_stock_pool(min_market_cap=50e8, min_avg_volume=3000e4)
  
  # 获取策略并扫描
  strategy = get_strategy("advance2retreat1")
  results = strategy.scan(pool)
================================================================================
"""

from .base import (
    BaseStrategy,
    filter_stock_pool,
    get_kline_with_indicators,
    calc_position_in_range,
    calc_volume_ratio,
    register_strategy,
    get_strategy,
    list_strategies,
    save_scan_result,
    get_scan_result,
    save_watch_pool,
    get_watch_pool,
)

# 导入并注册所有策略
from . import advance2retreat1
from . import dragon_turnaround
from . import limit_up_boomerang
from . import wizard_pointer
from . import double_cannon

# 震荡市备选战法
from . import single_yang_unbroken
from . import ma_pullback
from . import old_duck_head
from . import ma_convergence_breakout
from . import morning_star

# 注册进二退一策略
register_strategy(advance2retreat1.get_strategy())
# 注册龙回头策略
register_strategy(dragon_turnaround.get_strategy())
# 注册涨停回马枪策略
register_strategy(limit_up_boomerang.get_strategy())
# 注册仙人指路策略
register_strategy(wizard_pointer.get_strategy())
# 注册涨停双响炮策略
register_strategy(double_cannon.get_strategy())

# 注册震荡市备选战法
register_strategy(single_yang_unbroken.get_strategy())
register_strategy(ma_pullback.get_strategy())
register_strategy(old_duck_head.get_strategy())
register_strategy(ma_convergence_breakout.get_strategy())
register_strategy(morning_star.get_strategy())

__all__ = [
    "BaseStrategy",
    "filter_stock_pool",
    "get_kline_with_indicators",
    "calc_position_in_range",
    "calc_volume_ratio",
    "register_strategy",
    "get_strategy",
    "list_strategies",
    "save_scan_result",
    "get_scan_result",
    "save_watch_pool",
    "get_watch_pool",
]
