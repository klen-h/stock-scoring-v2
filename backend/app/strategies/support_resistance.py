"""
================================================================================
【文件作用】支撑阻力位自动识别模块
================================================================================

自动识别关键支撑/阻力价位，辅助区间交易决策。

识别方法：
  1. 摆动高低点（Swing High/Low）
     - 近期局部高点和低点
     - 被触碰次数越多越重要
  
  2. 成交密集区（Volume Profile）
     - 价格停留时间最长、成交量最大的区域
     - 形成强支撑/阻力
  
  3. 整数关口
     - 心理价位（如 10元、50元、100元）
     - 天然支撑阻力

输出：
  - levels: 关键价位列表（价格、类型、强度）
  - current_position: 当前价格在区间中的位置
  - trading_suggestion: 交易建议

使用方式：
  from app.strategies.support_resistance import find_support_resistance
  
  result = find_support_resistance("000001")
================================================================================
"""

import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from app.tencent import get_kline


def find_support_resistance(
    code: str,
    lookback_days: int = 60,
    num_levels: int = 5,
) -> Dict:
    """
    识别股票的支撑阻力位。
    
    参数：
      code: 股票代码
      lookback_days: 回看天数
      num_levels: 返回的关键价位数量
    
    返回：
      {
        "code": "000001",
        "current_price": 12.5,
        "levels": [
          {"price": 13.0, "type": "resistance", "strength": "strong", "touches": 3},
          {"price": 12.0, "type": "support", "strength": "medium", "touches": 2},
          ...
        ],
        "range": {"high": 13.5, "low": 11.5},
        "position_pct": 65,  # 当前价格在区间中的位置 (0-100)
        "suggestion": "接近阻力位，谨慎追高",
        "timestamp": "2026-08-21 10:30:00"
      }
    """
    # 获取K线数据
    klines = get_kline(code, period="day", count=lookback_days)
    if not klines or len(klines) < 20:
        return _default_result(code, "K线数据不足")
    
    current_price = klines[-1]["close"]
    
    # 1. 识别摆动高低点
    swing_points = _find_swing_points(klines)
    
    # 2. 识别成交密集区
    volume_zones = _find_volume_zones(klines)
    
    # 3. 识别整数关口
    round_levels = _find_round_levels(current_price)
    
    # 合并所有价位
    all_levels = swing_points + volume_zones + round_levels
    
    # 合并相近价位（价格差距 < 2% 视为同一水平）
    merged_levels = _merge_levels(all_levels, threshold=0.02)
    
    # 按强度排序，取前 N 个
    merged_levels.sort(key=lambda x: x["score"], reverse=True)
    top_levels = merged_levels[:num_levels]
    
    # 分类为支撑和阻力
    for level in top_levels:
        if level["price"] > current_price * 1.01:
            level["type"] = "resistance"
        elif level["price"] < current_price * 0.99:
            level["type"] = "support"
        else:
            level["type"] = "neutral"
        
        # 强度文字
        if level["score"] >= 3:
            level["strength"] = "strong"
        elif level["score"] >= 2:
            level["strength"] = "medium"
        else:
            level["strength"] = "weak"
    
    # 计算区间位置
    levels_above = [l for l in top_levels if l["type"] == "resistance"]
    levels_below = [l for l in top_levels if l["type"] == "support"]
    
    nearest_resistance = min([l["price"] for l in levels_above], default=current_price * 1.1)
    nearest_support = max([l["price"] for l in levels_below], default=current_price * 0.9)
    
    range_high = nearest_resistance
    range_low = nearest_support
    range_size = range_high - range_low
    
    if range_size > 0:
        position_pct = int((current_price - range_low) / range_size * 100)
    else:
        position_pct = 50
    
    # 生成交易建议
    suggestion = _generate_suggestion(current_price, position_pct, top_levels)
    
    return {
        "code": code,
        "current_price": current_price,
        "levels": top_levels,
        "range": {"high": range_high, "low": range_low},
        "position_pct": position_pct,
        "suggestion": suggestion,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _find_swing_points(klines: List[Dict], window: int = 5) -> List[Dict]:
    """
    识别摆动高低点。
    
    Swing High: 局部最高点，前后 N 根K线的高点都低于它
    Swing Low: 局部最低点，前后 N 根K线的低点都高于它
    """
    points = []
    
    for i in range(window, len(klines) - window):
        # 检查是否是 Swing High
        is_high = True
        for j in range(1, window + 1):
            if klines[i - j]["high"] >= klines[i]["high"] or \
               klines[i + j]["high"] >= klines[i]["high"]:
                is_high = False
                break
        
        if is_high:
            points.append({
                "price": klines[i]["high"],
                "date": klines[i]["date"],
                "source": "swing_high",
                "score": 1,  # 基础分
            })
        
        # 检查是否是 Swing Low
        is_low = True
        for j in range(1, window + 1):
            if klines[i - j]["low"] <= klines[i]["low"] or \
               klines[i + j]["low"] <= klines[i]["low"]:
                is_low = False
                break
        
        if is_low:
            points.append({
                "price": klines[i]["low"],
                "date": klines[i]["date"],
                "source": "swing_low",
                "score": 1,
            })
    
    return points


def _find_volume_zones(klines: List[Dict], num_zones: int = 3) -> List[Dict]:
    """
    识别成交密集区。
    
    将价格区间分成多个格子，统计每个格子的成交量，
    成交量最大的区域就是成交密集区。
    """
    if not klines:
        return []
    
    # 获取价格范围
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    max_price = max(highs)
    min_price = min(lows)
    
    if max_price <= min_price:
        return []
    
    # 分成 20 个价格格子
    num_bins = 20
    bin_size = (max_price - min_price) / num_bins
    
    # 统计每个格子的成交量
    volume_by_bin = {}
    for k in klines:
        mid_price = (k["high"] + k["low"]) / 2
        bin_idx = int((mid_price - min_price) / bin_size)
        bin_idx = min(bin_idx, num_bins - 1)
        
        bin_price = min_price + (bin_idx + 0.5) * bin_size
        volume_by_bin[bin_idx] = volume_by_bin.get(bin_idx, 0) + k["volume"]
    
    # 取成交量最大的几个格子
    sorted_bins = sorted(volume_by_bin.items(), key=lambda x: x[1], reverse=True)
    
    zones = []
    for bin_idx, volume in sorted_bins[:num_zones]:
        bin_price = min_price + (bin_idx + 0.5) * bin_size
        zones.append({
            "price": bin_price,
            "source": "volume_zone",
            "score": 2,  # 成交密集区权重更高
            "volume": volume,
        })
    
    return zones


def _find_round_levels(current_price: float) -> List[Dict]:
    """
    识别整数关口。
    
    根据当前价格，找出附近的整数关口（如 10、20、50、100 等）。
    """
    levels = []
    
    # 确定整数关口的粒度
    if current_price < 10:
        step = 1
    elif current_price < 50:
        step = 5
    elif current_price < 100:
        step = 10
    else:
        step = 20
    
    # 找出附近的整数关口（上下各 10%）
    lower_bound = current_price * 0.9
    upper_bound = current_price * 1.1
    
    level = int(lower_bound / step) * step
    while level <= upper_bound:
        if level > 0:
            levels.append({
                "price": level,
                "source": "round_level",
                "score": 1,  # 整数关口基础分
            })
        level += step
    
    return levels


def _merge_levels(levels: List[Dict], threshold: float = 0.02) -> List[Dict]:
    """
    合并相近价位。
    
    价格差距小于 threshold（默认 2%）的价位合并为一个，
    合并后的分数是各价位分数之和。
    """
    if not levels:
        return []
    
    # 按价格排序
    sorted_levels = sorted(levels, key=lambda x: x["price"])
    
    merged = []
    current_group = [sorted_levels[0]]
    
    for level in sorted_levels[1:]:
        # 检查是否与当前组合并
        avg_price = np.mean([l["price"] for l in current_group])
        if abs(level["price"] - avg_price) / avg_price < threshold:
            current_group.append(level)
        else:
            # 保存当前组
            merged.append(_create_merged_level(current_group))
            current_group = [level]
    
    # 保存最后一组
    if current_group:
        merged.append(_create_merged_level(current_group))
    
    return merged


def _create_merged_level(group: List[Dict]) -> Dict:
    """创建合并后的价位"""
    avg_price = np.mean([l["price"] for l in group])
    total_score = sum(l["score"] for l in group)
    sources = list(set(l["source"] for l in group))
    touches = len(group)
    
    return {
        "price": round(avg_price, 2),
        "score": total_score,
        "touches": touches,
        "sources": sources,
    }


def _generate_suggestion(
    current_price: float,
    position_pct: int,
    levels: List[Dict],
) -> str:
    """根据当前位置生成交易建议"""
    
    # 找到最近的支撑和阻力
    resistances = [l for l in levels if l["price"] > current_price * 1.01]
    supports = [l for l in levels if l["price"] < current_price * 0.99]
    
    nearest_resistance = min(resistances, key=lambda x: x["price"], default=None)
    nearest_support = max(supports, key=lambda x: x["price"], default=None)
    
    # 根据位置生成建议
    if position_pct >= 90:
        return "接近强阻力位，谨慎追高，可考虑减仓"
    elif position_pct >= 75:
        if nearest_resistance and nearest_resistance["strength"] == "strong":
            return "接近阻力位，注意观察突破情况"
        return "位置偏高，可持有观望"
    elif position_pct >= 50:
        return "处于区间中部，方向不明确，观望为主"
    elif position_pct >= 25:
        if nearest_support and nearest_support["strength"] == "strong":
            return "接近强支撑位，可考虑轻仓试探"
        return "位置偏低，等待企稳信号"
    else:
        return "接近强支撑位，超跌反弹机会，可分批建仓"


def _default_result(code: str, reason: str = "") -> Dict:
    """默认结果（数据不足时）"""
    return {
        "code": code,
        "current_price": 0,
        "levels": [],
        "range": {"high": 0, "low": 0},
        "position_pct": 50,
        "suggestion": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
