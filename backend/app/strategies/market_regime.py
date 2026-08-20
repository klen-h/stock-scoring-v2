"""
================================================================================
【文件作用】市场状态识别模块
================================================================================

自动判断当前市场是趋势市还是震荡市，用于战法自动切换。

判定依据：
  1. ADX（平均趋向指标）：衡量趋势强度
     - ADX < 20: 强震荡
     - ADX 20-25: 弱震荡/过渡
     - ADX > 25: 趋势市
  
  2. 布林带宽度：衡量波动率
     - 带宽收窄 → 震荡（蓄势）
     - 带宽扩张 → 趋势（爆发）
  
  3. 20日振幅：近期价格波动幅度
     - 振幅小 → 震荡
     - 振幅大 → 趋势

输出：
  - regime: "trending" / "oscillating" / "transition"
  - confidence: 0-100 置信度
  - details: 各指标详细数值
  - recommended_strategies: 推荐启用的战法列表

使用方式：
  from app.strategies.market_regime import detect_market_regime
  
  result = detect_market_regime()
  if result["regime"] == "oscillating":
      # 启用震荡市战法
================================================================================
"""

import numpy as np
from typing import Dict, List, Optional
from datetime import datetime

from app.tencent import get_kline


# ── 战法分类 ──
TRENDING_STRATEGIES = [
    "advance2retreat1",      # 进二退一
    "limit_up_boomerang",    # 涨停回马枪
    "double_cannon",         # 涨停双响炮
    "dragon_turnaround",     # 龙回头
    "wizard_pointer",        # 仙人指路
]

OSCILLATING_STRATEGIES = [
    "single_yang_unbroken",        # 单阳不破
    "ma_pullback",                 # 均线回踩
    "old_duck_head",               # 老鸭头
    "ma_convergence_breakout",     # 均线粘合突破
    "morning_star",                # 早晨之星
]


def detect_market_regime(index_code: str = "000300") -> Dict:
    """
    检测当前市场状态。
    
    参数：
      index_code: 参考指数代码，默认沪深300
    
    返回：
      {
        "regime": "trending" | "oscillating" | "transition",
        "confidence": 75,           # 置信度 0-100
        "adx": 18.5,                # ADX 值
        "bb_width": 0.08,           # 布林带宽度
        "volatility": 0.025,        # 20日波动率
        "details": {...},           # 详细指标
        "recommended_strategies": [...],  # 推荐战法
        "timestamp": "2026-08-21 10:30:00"
      }
    """
    # 获取指数K线
    klines = get_kline(index_code, period="day", count=60)
    if not klines or len(klines) < 30:
        return _default_result("数据不足")
    
    # 计算各指标
    adx = _calc_adx(klines, period=14)
    bb_width = _calc_bollinger_width(klines, period=20)
    volatility = _calc_volatility(klines, period=20)
    
    # 综合判定
    regime, confidence = _determine_regime(adx, bb_width, volatility)
    
    # 推荐战法
    recommended = _get_recommended_strategies(regime)
    
    return {
        "regime": regime,
        "confidence": confidence,
        "adx": round(adx, 2),
        "bb_width": round(bb_width, 4),
        "volatility": round(volatility, 4),
        "details": {
            "adx_interpretation": _interpret_adx(adx),
            "bb_interpretation": _interpret_bb_width(bb_width),
            "volatility_interpretation": _interpret_volatility(volatility),
        },
        "recommended_strategies": recommended,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _calc_adx(klines: List[Dict], period: int = 14) -> float:
    """
    计算 ADX（平均趋向指标）。
    
    ADX 衡量趋势强度，不考虑方向：
      - ADX < 20: 无明显趋势（震荡）
      - ADX 20-25: 趋势形成中
      - ADX > 25: 明显趋势
      - ADX > 50: 极强趋势
    """
    if len(klines) < period + 1:
        return 0
    
    # 计算 True Range, +DM, -DM
    tr_list = []
    plus_dm_list = []
    minus_dm_list = []
    
    for i in range(1, len(klines)):
        high = klines[i]["high"]
        low = klines[i]["low"]
        prev_close = klines[i-1]["close"]
        prev_high = klines[i-1]["high"]
        prev_low = klines[i-1]["low"]
        
        # True Range
        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )
        tr_list.append(tr)
        
        # Directional Movement
        up_move = high - prev_high
        down_move = prev_low - low
        
        if up_move > down_move and up_move > 0:
            plus_dm = up_move
        else:
            plus_dm = 0
        
        if down_move > up_move and down_move > 0:
            minus_dm = down_move
        else:
            minus_dm = 0
        
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
    
    # 平滑处理（Wilder's smoothing）
    def smooth(values, period):
        result = [0] * len(values)
        if len(values) < period:
            return result
        result[period-1] = sum(values[:period])
        for i in range(period, len(values)):
            result[i] = result[i-1] - result[i-1] / period + values[i]
        return result
    
    tr_smooth = smooth(tr_list, period)
    plus_dm_smooth = smooth(plus_dm_list, period)
    minus_dm_smooth = smooth(minus_dm_list, period)
    
    # 计算 +DI, -DI
    plus_di = []
    minus_di = []
    for i in range(len(tr_smooth)):
        if tr_smooth[i] > 0:
            plus_di.append(100 * plus_dm_smooth[i] / tr_smooth[i])
            minus_di.append(100 * minus_dm_smooth[i] / tr_smooth[i])
        else:
            plus_di.append(0)
            minus_di.append(0)
    
    # 计算 DX
    dx_list = []
    for i in range(len(plus_di)):
        di_sum = plus_di[i] + minus_di[i]
        if di_sum > 0:
            dx_list.append(100 * abs(plus_di[i] - minus_di[i]) / di_sum)
        else:
            dx_list.append(0)
    
    # 计算 ADX（DX 的平滑平均值）
    adx_smooth = smooth(dx_list, period)
    
    # 返回最新的 ADX 值
    if adx_smooth and adx_smooth[-1] > 0:
        return adx_smooth[-1] / period
    return 0


def _calc_bollinger_width(klines: List[Dict], period: int = 20) -> float:
    """
    计算布林带宽度。
    
    布林带宽度 = (上轨 - 下轨) / 中轨
      - 宽度小 → 震荡（蓄势待发）
      - 宽度大 → 趋势（波动剧烈）
    """
    if len(klines) < period:
        return 0
    
    closes = [k["close"] for k in klines[-period:]]
    mean = np.mean(closes)
    std = np.std(closes)
    
    if mean == 0:
        return 0
    
    # 带宽 = 4 * std / mean (上下轨各 2 倍标准差)
    width = 4 * std / mean
    return width


def _calc_volatility(klines: List[Dict], period: int = 20) -> float:
    """
    计算价格波动率（20日年化波动率）。
    
    使用对数收益率的标准差。
    """
    if len(klines) < period + 1:
        return 0
    
    closes = [k["close"] for k in klines[-(period+1):]]
    
    # 计算对数收益率
    returns = []
    for i in range(1, len(closes)):
        if closes[i-1] > 0:
            r = np.log(closes[i] / closes[i-1])
            returns.append(r)
    
    if not returns:
        return 0
    
    # 年化波动率 = 日波动率 * sqrt(252)
    daily_vol = np.std(returns)
    annual_vol = daily_vol * np.sqrt(252)
    return annual_vol


def _determine_regime(adx: float, bb_width: float, volatility: float) -> tuple:
    """
    综合判定市场状态。
    
    返回 (regime, confidence)
    """
    score = 0  # 正值=趋势，负值=震荡
    
    # ADX 评分（权重 50%）
    if adx < 15:
        score -= 3
    elif adx < 20:
        score -= 2
    elif adx < 25:
        score -= 1
    elif adx > 35:
        score += 3
    elif adx > 30:
        score += 2
    elif adx > 25:
        score += 1
    
    # 布林带宽度评分（权重 30%）
    # 带宽阈值需要根据市场调整，这里用经验值
    if bb_width < 0.05:
        score -= 2  # 很窄，蓄势
    elif bb_width < 0.08:
        score -= 1
    elif bb_width > 0.15:
        score += 2  # 很宽，趋势
    elif bb_width > 0.10:
        score += 1
    
    # 波动率评分（权重 20%）
    if volatility < 0.15:
        score -= 1  # 低波动
    elif volatility > 0.30:
        score += 1  # 高波动
    
    # 判定
    if score >= 2:
        regime = "trending"
        confidence = min(50 + score * 10, 95)
    elif score <= -2:
        regime = "oscillating"
        confidence = min(50 + abs(score) * 10, 95)
    else:
        regime = "transition"
        confidence = 40 + abs(score) * 10
    
    return regime, int(confidence)


def _get_recommended_strategies(regime: str) -> List[str]:
    """根据市场状态返回推荐战法"""
    if regime == "trending":
        return TRENDING_STRATEGIES
    elif regime == "oscillating":
        return OSCILLATING_STRATEGIES
    else:  # transition
        # 过渡期两种都推荐，但优先震荡市战法（更安全）
        return OSCILLATING_STRATEGIES + TRENDING_STRATEGIES[:2]


def _interpret_adx(adx: float) -> str:
    """ADX 解读"""
    if adx < 15:
        return "无明显趋势，强震荡"
    elif adx < 20:
        return "趋势较弱，偏震荡"
    elif adx < 25:
        return "趋势形成中"
    elif adx < 35:
        return "明显趋势"
    else:
        return "极强趋势"


def _interpret_bb_width(width: float) -> str:
    """布林带宽度解读"""
    if width < 0.05:
        return "带宽极窄，蓄势待变"
    elif width < 0.08:
        return "带宽收窄，震荡整理"
    elif width < 0.12:
        return "带宽正常"
    elif width < 0.18:
        return "带宽扩张，波动加大"
    else:
        return "带宽极宽，剧烈波动"


def _interpret_volatility(vol: float) -> str:
    """波动率解读"""
    if vol < 0.15:
        return "低波动，市场平静"
    elif vol < 0.25:
        return "波动正常"
    elif vol < 0.35:
        return "波动较大"
    else:
        return "高波动，市场剧烈"


def _default_result(reason: str = "") -> Dict:
    """默认结果（数据不足时）"""
    return {
        "regime": "unknown",
        "confidence": 0,
        "adx": 0,
        "bb_width": 0,
        "volatility": 0,
        "details": {
            "error": reason,
        },
        "recommended_strategies": TRENDING_STRATEGIES + OSCILLATING_STRATEGIES,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_strategy_recommendation(strategy_name: str) -> Dict:
    """
    获取单个战法的适用建议。
    
    返回该战法在当前市场状态下的适用性评估。
    """
    regime_result = detect_market_regime()
    current_regime = regime_result["regime"]
    
    # 判断战法类型
    if strategy_name in TRENDING_STRATEGIES:
        strategy_type = "trending"
    elif strategy_name in OSCILLATING_STRATEGIES:
        strategy_type = "oscillating"
    else:
        strategy_type = "unknown"
    
    # 评估适用性
    if current_regime == "unknown":
        suitability = "unknown"
        advice = "市场数据不足，无法判断"
    elif strategy_type == current_regime:
        suitability = "high"
        advice = f"当前为{('趋势' if current_regime == 'trending' else '震荡')}市，适合使用此战法"
    elif current_regime == "transition":
        suitability = "medium"
        advice = "市场处于过渡期，可谨慎使用"
    else:
        suitability = "low"
        advice = f"当前为{('趋势' if current_regime == 'trending' else '震荡')}市，此战法不太适用"
    
    return {
        "strategy": strategy_name,
        "strategy_type": strategy_type,
        "market_regime": current_regime,
        "suitability": suitability,
        "advice": advice,
        "regime_details": regime_result,
    }
