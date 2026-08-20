"""
================================================================================
【文件作用】信号共振验证模块
================================================================================

对战法产生的信号进行多重验证，过滤假信号，提升胜率。

验证维度：
  1. RSI 共振：RSI 是否处于有利区间（超卖回升/中性偏多）
  2. 支撑阻力共振：价格是否接近支撑位（买入信号）或阻力位（卖出信号）
  3. 量能共振：成交量是否配合（突破放量/回调缩量）
  4. 趋势共振：均线排列是否支持（多头排列/空头排列）

评分规则：
  - 每个维度 0-25 分，总分 100
  - ≥75 分：A 级信号（强共振，建议重仓）
  - 50-74 分：B 级信号（中等共振，可轻仓）
  - <50 分：C 级信号（弱共振，建议放弃）

使用方式：
  from app.strategies.signal_confirmation import confirm_signal
  
  result = confirm_signal("000001", entry_price=12.5, direction="buy")
  if result["grade"] == "A":
      # 高胜率信号，执行交易
================================================================================
"""

from typing import Dict, Optional, List
from datetime import datetime

from app.tencent import get_kline
from app.strategies.rsi import calc_rsi_signals, _calc_rsi
from app.strategies.support_resistance import find_support_resistance


# ── 配置 ──
CONFIRMATION_CONFIG = {
    # RSI 共振阈值
    "rsi_buy_ideal_low": 25,      # RSI 理想买入区下限
    "rsi_buy_ideal_high": 45,     # RSI 理想买入区上限
    "rsi_buy_acceptable": 55,     # RSI 可接受买入区上限
    
    # 支撑阻力共振阈值
    "support_proximity_pct": 0.02,  # 距离支撑位 2% 以内视为共振
    "resistance_distance_pct": 0.05,  # 距离阻力位至少 5% 才有空间
    
    # 量能共振阈值
    "volume_shrink_ratio": 0.7,    # 回调缩量到均量 70% 以下
    "volume_breakout_ratio": 1.5,  # 突破放量到均量 150% 以上
    
    # 趋势共振阈值
    "ma_trend_periods": [5, 10, 20],  # 均线周期
}


def confirm_signal(
    code: str,
    entry_price: float,
    direction: str = "buy",
    signal_date: Optional[str] = None,
) -> Dict:
    """
    对信号进行多维度共振验证。
    
    参数：
      code: 股票代码
      entry_price: 介入价格
      direction: "buy" 或 "sell"
      signal_date: 信号日期（可选）
    
    返回：
      {
        "code": "000001",
        "entry_price": 12.5,
        "total_score": 82,
        "grade": "A",           # A/B/C
        "confirmations": {
          "rsi": {"score": 20, "details": "RSI=35，超卖回升"},
          "support": {"score": 25, "details": "距支撑位 1.2%"},
          "volume": {"score": 20, "details": "回调缩量 65%"},
          "trend": {"score": 17, "details": "短期多头排列"},
        },
        "verdict": "强共振信号，建议介入",
        "timestamp": "2026-08-21 10:30:00"
      }
    """
    # 获取K线数据
    klines = get_kline(code, period="day", count=60)
    if not klines or len(klines) < 30:
        return _default_result(code, entry_price, "K线数据不足")
    
    # 各维度验证
    rsi_result = _check_rsi_confirmation(klines, direction)
    support_result = _check_support_confirmation(code, entry_price, direction)
    volume_result = _check_volume_confirmation(klines, direction)
    trend_result = _check_trend_confirmation(klines, direction)
    
    # 计算总分
    total_score = (
        rsi_result["score"] +
        support_result["score"] +
        volume_result["score"] +
        trend_result["score"]
    )
    
    # 判定等级
    if total_score >= 75:
        grade = "A"
        verdict = "强共振信号，建议介入"
    elif total_score >= 50:
        grade = "B"
        verdict = "中等共振，可轻仓试探"
    elif total_score >= 30:
        grade = "C"
        verdict = "共振不足，建议观望"
    else:
        grade = "D"
        verdict = "无明显共振，建议放弃"
    
    return {
        "code": code,
        "entry_price": entry_price,
        "total_score": total_score,
        "grade": grade,
        "confirmations": {
            "rsi": rsi_result,
            "support": support_result,
            "volume": volume_result,
            "trend": trend_result,
        },
        "verdict": verdict,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _check_rsi_confirmation(klines: List[Dict], direction: str) -> Dict:
    """
    RSI 共振检查。
    
    买入信号：RSI 在超卖区或从超卖区回升
    卖出信号：RSI 在超买区或从超买区回落
    """
    rsi_values = _calc_rsi(klines, period=14)
    if not rsi_values or len(rsi_values) < 2:
        return {"score": 0, "details": "RSI 数据不足"}
    
    current_rsi = rsi_values[-1]["rsi"]
    prev_rsi = rsi_values[-2]["rsi"]
    
    cfg = CONFIRMATION_CONFIG
    
    if direction == "buy":
        # 理想情况：RSI 在 25-45 之间且回升
        if cfg["rsi_buy_ideal_low"] <= current_rsi <= cfg["rsi_buy_ideal_high"]:
            if current_rsi > prev_rsi:  # 回升中
                return {"score": 25, "details": f"RSI={current_rsi:.0f}，超卖回升 ✓"}
            else:
                return {"score": 20, "details": f"RSI={current_rsi:.0f}，超卖区间 ✓"}
        elif current_rsi <= cfg["rsi_buy_acceptable"]:
            return {"score": 15, "details": f"RSI={current_rsi:.0f}，偏低区间"}
        elif current_rsi <= 60:
            return {"score": 10, "details": f"RSI={current_rsi:.0f}，中性区间"}
        else:
            return {"score": 5, "details": f"RSI={current_rsi:.0f}，偏高"}
    else:  # sell
        # 卖出信号：RSI 在超买区
        if current_rsi >= 70:
            if current_rsi < prev_rsi:  # 回落中
                return {"score": 25, "details": f"RSI={current_rsi:.0f}，超买回落 ✓"}
            else:
                return {"score": 20, "details": f"RSI={current_rsi:.0f}，超买区间 ✓"}
        elif current_rsi >= 60:
            return {"score": 10, "details": f"RSI={current_rsi:.0f}，偏高区间"}
        else:
            return {"score": 5, "details": f"RSI={current_rsi:.0f}，偏低"}


def _check_support_confirmation(code: str, entry_price: float, direction: str) -> Dict:
    """
    支撑阻力共振检查。
    
    买入信号：价格接近支撑位（有支撑）
    卖出信号：价格接近阻力位（有压力）
    """
    sr_result = find_support_resistance(code, lookback_days=60)
    if not sr_result or not sr_result.get("levels"):
        return {"score": 10, "details": "无支撑阻力数据"}
    
    levels = sr_result["levels"]
    current_price = sr_result.get("current_price", entry_price)
    
    cfg = CONFIRMATION_CONFIG
    
    if direction == "buy":
        # 找最近的支撑位
        supports = [l for l in levels if l["type"] == "support"]
        if not supports:
            return {"score": 10, "details": "无明显支撑位"}
        
        nearest_support = min(supports, key=lambda x: abs(x["price"] - current_price))
        distance_pct = abs(nearest_support["price"] - current_price) / current_price
        
        if distance_pct <= cfg["support_proximity_pct"]:
            strength = nearest_support.get("strength", "weak")
            if strength == "strong":
                return {"score": 25, "details": f"距强支撑 {distance_pct*100:.1f}% ✓"}
            elif strength == "medium":
                return {"score": 20, "details": f"距中支撑 {distance_pct*100:.1f}% ✓"}
            else:
                return {"score": 15, "details": f"距弱支撑 {distance_pct*100:.1f}%"}
        elif distance_pct <= cfg["support_proximity_pct"] * 2:
            return {"score": 10, "details": f"距支撑 {distance_pct*100:.1f}%"}
        else:
            return {"score": 5, "details": f"远离支撑 {distance_pct*100:.1f}%"}
    else:  # sell
        # 找最近的阻力位
        resistances = [l for l in levels if l["type"] == "resistance"]
        if not resistances:
            return {"score": 10, "details": "无明显阻力位"}
        
        nearest_resistance = min(resistances, key=lambda x: abs(x["price"] - current_price))
        distance_pct = abs(nearest_resistance["price"] - current_price) / current_price
        
        if distance_pct <= cfg["support_proximity_pct"]:
            return {"score": 25, "details": f"距强阻力 {distance_pct*100:.1f}% ✓"}
        elif distance_pct <= cfg["support_proximity_pct"] * 2:
            return {"score": 15, "details": f"距阻力 {distance_pct*100:.1f}%"}
        else:
            return {"score": 10, "details": f"远离阻力 {distance_pct*100:.1f}%"}


def _check_volume_confirmation(klines: List[Dict], direction: str) -> Dict:
    """
    量能共振检查。
    
    买入信号：回调缩量 + 最新K线放量
    卖出信号：上涨缩量 + 最新K线放量下跌
    """
    if len(klines) < 10:
        return {"score": 10, "details": "K线数据不足"}
    
    # 计算近 5 日均量
    recent_volumes = [k["volume"] for k in klines[-6:-1]]
    avg_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0
    
    if avg_volume == 0:
        return {"score": 10, "details": "成交量数据异常"}
    
    latest_volume = klines[-1]["volume"]
    volume_ratio = latest_volume / avg_volume
    
    # 检查前几根K线的量能趋势
    prev_volumes = [k["volume"] for k in klines[-5:-1]]
    volume_trend = "increasing" if prev_volumes[-1] > prev_volumes[0] else "decreasing"
    
    cfg = CONFIRMATION_CONFIG
    
    if direction == "buy":
        # 买入信号：希望看到缩量回调后放量上涨
        latest_kline = klines[-1]
        is_positive = latest_kline["close"] > latest_kline["open"]
        
        if is_positive and volume_ratio >= cfg["volume_breakout_ratio"]:
            return {"score": 25, "details": f"放量上涨 量比{volume_ratio:.1f} ✓"}
        elif is_positive and volume_ratio >= 1.0:
            return {"score": 20, "details": f"温和放量 量比{volume_ratio:.1f}"}
        elif volume_ratio <= cfg["volume_shrink_ratio"]:
            # 缩量但未跌破
            if is_positive:
                return {"score": 15, "details": f"缩量小阳 量比{volume_ratio:.1f}"}
            else:
                return {"score": 10, "details": f"缩量回调 量比{volume_ratio:.1f}"}
        else:
            return {"score": 10, "details": f"量能平淡 量比{volume_ratio:.1f}"}
    else:  # sell
        # 卖出信号：放量下跌
        latest_kline = klines[-1]
        is_negative = latest_kline["close"] < latest_kline["open"]
        
        if is_negative and volume_ratio >= cfg["volume_breakout_ratio"]:
            return {"score": 25, "details": f"放量下跌 量比{volume_ratio:.1f} ✓"}
        elif is_negative:
            return {"score": 15, "details": f"缩量下跌 量比{volume_ratio:.1f}"}
        else:
            return {"score": 10, "details": f"量能平淡 量比{volume_ratio:.1f}"}


def _check_trend_confirmation(klines: List[Dict], direction: str) -> Dict:
    """
    趋势共振检查。
    
    买入信号：短期均线多头排列（MA5 > MA10 > MA20）
    卖出信号：短期均线空头排列
    """
    if len(klines) < 25:
        return {"score": 10, "details": "K线数据不足"}
    
    # 计算均线
    closes = [k["close"] for k in klines]
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    current_price = closes[-1]
    
    if direction == "buy":
        # 多头排列检查
        if ma5 > ma10 > ma20:
            return {"score": 25, "details": "短期多头排列 ✓"}
        elif ma5 > ma10 and current_price > ma20:
            return {"score": 20, "details": "偏多排列"}
        elif current_price > ma20:
            return {"score": 15, "details": "站上 MA20"}
        elif current_price > ma10:
            return {"score": 10, "details": "站上 MA10"}
        else:
            return {"score": 5, "details": "均线下方"}
    else:  # sell
        # 空头排列检查
        if ma5 < ma10 < ma20:
            return {"score": 25, "details": "短期空头排列 ✓"}
        elif ma5 < ma10 and current_price < ma20:
            return {"score": 20, "details": "偏空排列"}
        elif current_price < ma20:
            return {"score": 15, "details": "跌破 MA20"}
        else:
            return {"score": 5, "details": "均线上方"}


def _default_result(code: str, entry_price: float, reason: str = "") -> Dict:
    """默认结果"""
    return {
        "code": code,
        "entry_price": entry_price,
        "total_score": 0,
        "grade": "D",
        "confirmations": {
            "rsi": {"score": 0, "details": reason},
            "support": {"score": 0, "details": reason},
            "volume": {"score": 0, "details": reason},
            "trend": {"score": 0, "details": reason},
        },
        "verdict": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def batch_confirm_signals(signals: List[Dict]) -> List[Dict]:
    """
    批量验证信号。
    
    参数：
      signals: 战法产生的信号列表 [{"code": "000001", "entry_price": 12.5, ...}]
    
    返回：
      添加了共振验证结果的信号列表
    """
    confirmed = []
    for signal in signals:
        code = signal.get("code")
        entry_price = signal.get("entry_price")
        if not code or not entry_price:
            continue
        
        result = confirm_signal(code, entry_price, direction="buy")
        signal["confirmation"] = result
        signal["signal_grade"] = result["grade"]
        signal["signal_score"] = result["total_score"]
        confirmed.append(signal)
    
    # 按评分排序
    confirmed.sort(key=lambda x: x.get("signal_score", 0), reverse=True)
    return confirmed
