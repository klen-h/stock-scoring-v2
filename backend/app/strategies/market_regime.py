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


def detect_market_regime(index_code: str = "sh000300") -> Dict:
    """
    检测当前市场状态（统一口径）。

    ★ 复用 backtest/market_regime 的判定引擎（backtest_prices 本地库 3 年历史，
    基于沪深300：MA20/MA60 + ADX + ATR 历史分位），与回测、评分动态权重完全同源，
    消除此前战法侧（腾讯实时 ADX+布林带+20日波动率 → trending/oscillating/transition）
    与回测侧（MA60+ADX+ATR分位 → 进攻/震荡/防御）两套口径割裂的问题。

    返回：
      {
        "regime": "offensive" | "neutral" | "defensive",
        "confidence": 0-100,             # 置信度
        "adx": 18.5,
        "regime_score": -100~+100,       # 连续分
        "ma_trend": "up"/"down"/"flat",
        "volatility_regime": "high"/"normal"/"low",   # ATR 历史分位
        "atr_percentile": 83.5,
        "price_vs_ma20": 2.1,
        "details": {...},                # 状态/波动中文描述 + 动态权重
        "recommended_strategies": [...], # 该状态下推荐战法
        "timestamp": "..."
      }
    """
    from app.backtest.market_regime import (
        load_regime_history, get_regime_weights, get_regime_description,
    )
    states = load_regime_history()
    if not states:
        return _default_result("沪深300（backtest_prices）历史数据不足（<70 条），"
                               "请先回填指数日线：python -m app.backtest.fill")
    latest = states[-1]
    regime = latest.state
    return {
        "regime": regime,
        "confidence": _state_confidence(latest),
        "adx": round(latest.adx or 0, 2),
        "regime_score": round(latest.regime_score or 0, 2),
        "ma_trend": latest.ma_trend,
        "volatility_regime": latest.volatility_regime,
        "atr_percentile": round(latest.atr_percentile or 0, 1),
        "price_vs_ma20": round(latest.price_vs_ma20 or 0, 2),
        "details": {
            "state_desc": get_regime_description(regime),
            "volatility_desc": _volatility_desc(latest.volatility_regime),
            "weights": get_regime_weights(regime),
        },
        "recommended_strategies": _get_recommended_strategies(regime),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _state_confidence(s) -> int:
    """置信度 0-100：基于 regime_score 连续分（震荡市中性 50）。"""
    if s.state == "neutral":
        return 50
    return int(min(50 + abs(s.regime_score or 0) * 0.5, 95))


def _volatility_desc(v: str) -> str:
    return {"high": "高波动（ATR 处于历史高位）",
            "normal": "波动正常",
            "low": "低波动（ATR 处于历史低位）"}.get(v, v)


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
    """按市场状态推荐战法（统一口径：offensive/neutral/defensive）。
    - 进攻市 → 趋势类战法
    - 震荡市 → 震荡类战法
    - 防御市 → 不推荐（准入收紧由策略层 gate 实现）"""
    if regime == "offensive":
        return TRENDING_STRATEGIES
    elif regime == "neutral":
        return OSCILLATING_STRATEGIES
    elif regime == "defensive":
        return []
    return TRENDING_STRATEGIES + OSCILLATING_STRATEGIES


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
    获取单个战法的适用建议（统一口径）。
    基于当前市场状态（进攻/震荡/防御）+ 波动率（高/正常/低）评估适用性。
    """
    regime_result = detect_market_regime()
    current_regime = regime_result["regime"]
    vol = regime_result.get("volatility_regime")

    # 战法类型：趋势类 / 震荡类
    if strategy_name in TRENDING_STRATEGIES:
        strategy_type = "trending"
    elif strategy_name in OSCILLATING_STRATEGIES:
        strategy_type = "oscillating"
    else:
        strategy_type = "unknown"

    if current_regime == "unknown" or not current_regime:
        suitability = "unknown"
        advice = "市场数据不足，无法判断"
    elif strategy_type == "unknown":
        suitability = "unknown"
        advice = "未分类战法，无准入规则"
    elif current_regime == "offensive" and strategy_type == "trending":
        suitability = "high"
        advice = "当前为进攻市，适合趋势类战法"
    elif current_regime == "neutral" and strategy_type == "oscillating":
        suitability = "high"
        advice = "当前为震荡市，适合震荡类战法"
    elif current_regime == "defensive":
        suitability = "low"
        advice = "当前为防御市，建议暂停战法入场"
    else:
        suitability = "low"
        advice = f"当前市场状态（{current_regime}）与该战法类型不匹配"

    if vol == "high":
        advice += "；当前高波动，注意仓位控制"

    return {
        "strategy": strategy_name,
        "strategy_type": strategy_type,
        "market_regime": current_regime,
        "suitability": suitability,
        "advice": advice,
        "regime_details": regime_result,
    }


# ================================================================
#  战法准入（regime × 波动 gate）— P3
# ================================================================
# 规则先用专家经验（初始版），待「战法 × regime 分层回测」
# （backtest.run --strategy regime_warfare）积累数据后调优。
# 状态基本准入：进攻→趋势类、震荡→震荡类、防御→全禁。
# 「高波动常态」调节：震荡市 + 高波动只放行低吸/反转类（止损明确）。

REGIME_LABELS = {"offensive": "进攻", "neutral": "震荡", "defensive": "防御"}
TYPE_LABELS = {"trending": "趋势类", "oscillating": "震荡类"}

# 高波动常态调节白名单：key=(regime, volatility)，
#   None = 不额外限制（保持状态基本准入）
#   []   = 该状态+波动下全部禁止
#   list = 只放行名单内战法
ADMISSION_MATRIX = {
    ("offensive", "high"): None,
    ("offensive", "normal"): None,
    ("offensive", "low"): None,
    ("neutral", "high"): ["morning_star", "ma_pullback"],
    ("neutral", "normal"): None,
    ("neutral", "low"): None,
    ("defensive", "high"): [],
    ("defensive", "normal"): [],
    ("defensive", "low"): [],
}


def is_strategy_admitted(strategy_name: str, regime: str = None,
                         volatility: str = None) -> tuple:
    """
    判断战法在给定市场状态下是否准入。

    返回 (admitted: bool, reason: str, regime, volatility)。
    regime/volatility 缺省时自动检测当前市场状态。
    非准入战法禁止扫描（由 _do_scan / scan 接口调用）。
    """
    info = detect_market_regime()
    regime = regime or info.get("regime")
    volatility = volatility or info.get("volatility_regime")

    if regime in ("unknown", None):
        return False, "市场状态未知，战法扫描已暂停", regime, volatility

    if strategy_name in TRENDING_STRATEGIES:
        strategy_type = "trending"
    elif strategy_name in OSCILLATING_STRATEGIES:
        strategy_type = "oscillating"
    else:
        return False, "未分类战法，无准入规则", regime, volatility

    base = {"offensive": "trending", "neutral": "oscillating",
            "defensive": None}.get(regime)
    if base is None:
        return False, f"当前为{REGIME_LABELS.get(regime, regime)}市，禁止战法入场", regime, volatility
    if strategy_type != base:
        return False, (f"当前为{REGIME_LABELS.get(regime, regime)}市，"
                       f"{TYPE_LABELS.get(strategy_type, strategy_type)}战法不准入"), regime, volatility

    rule = ADMISSION_MATRIX.get((regime, volatility))
    if rule == []:
        return False, "高波动常态下暂停该战法（待分层数据调优）", regime, volatility
    if rule and strategy_name not in rule:
        return False, "高波动常态下该战法不准入（待分层数据调优）", regime, volatility
    return True, "准入", regime, volatility
