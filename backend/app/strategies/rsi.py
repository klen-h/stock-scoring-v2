"""
================================================================================
【文件作用】RSI 相对强弱指标模块
================================================================================

RSI（Relative Strength Index）是震荡市标配指标。

核心逻辑：
  - RSI > 70: 超买区，可能回调
  - RSI < 30: 超卖区，可能反弹
  - RSI 50: 多空分界线

信号生成：
  - 金叉：RSI 从下向上穿越 30（超卖区回升）→ 买入信号
  - 死叉：RSI 从上向下穿越 70（超买区回落）→ 卖出信号
  - 背离：价格新低但 RSI 未新低 → 底背离（看涨）
  - 背离：价格新高但 RSI 未新高 → 顶背离（看跌）

使用方式：
  from app.strategies.rsi import calc_rsi_signals
  
  result = calc_rsi_signals("000001")
================================================================================
"""

import numpy as np
from typing import Dict, List, Optional
from datetime import datetime

from app.tencent import get_kline


# RSI 配置
RSI_CONFIG = {
    "period": 14,           # 默认周期
    "overbought": 70,       # 超买阈值
    "oversold": 30,         # 超卖阈值
    "strong_overbought": 80,  # 强超买
    "strong_oversold": 20,    # 强超卖
}


def calc_rsi_signals(
    code: str,
    period: int = 14,
    lookback_days: int = 60,
) -> Dict:
    """
    计算 RSI 指标并生成交易信号。
    
    参数：
      code: 股票代码
      period: RSI 周期（默认 14）
      lookback_days: 回看天数
    
    返回：
      {
        "code": "000001",
        "current_rsi": 45.2,
        "zone": "neutral",           # overbought/oversold/neutral
        "signal": null,              # buy/sell/divergence_bull/divergence_bear/null
        "rsi_history": [...],        # 近期 RSI 值
        "interpretation": "RSI 处于中性区间，无明显信号",
        "timestamp": "2026-08-21 10:30:00"
      }
    """
    # 获取K线数据
    klines = get_kline(code, period="day", count=lookback_days)
    if not klines or len(klines) < period + 5:
        return _default_result(code, "K线数据不足")
    
    # 计算 RSI
    rsi_values = _calc_rsi(klines, period)
    
    if not rsi_values:
        return _default_result(code, "RSI 计算失败")
    
    current_rsi = rsi_values[-1]["rsi"]
    
    # 判断当前区间
    zone = _get_zone(current_rsi)
    
    # 检测信号
    signal = _detect_signal(rsi_values, klines)
    
    # 生成解读
    interpretation = _interpret_rsi(current_rsi, zone, signal)
    
    return {
        "code": code,
        "current_rsi": round(current_rsi, 2),
        "zone": zone,
        "signal": signal,
        "rsi_history": rsi_values[-20:],  # 最近 20 天
        "config": {
            "overbought": RSI_CONFIG["overbought"],
            "oversold": RSI_CONFIG["oversold"],
        },
        "interpretation": interpretation,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _calc_rsi(klines: List[Dict], period: int = 14) -> List[Dict]:
    """
    计算 RSI 值序列。
    
    RSI = 100 - 100 / (1 + RS)
    RS = 平均上涨幅度 / 平均下跌幅度
    """
    if len(klines) < period + 1:
        return []
    
    # 计算价格变化
    changes = []
    for i in range(1, len(klines)):
        change = klines[i]["close"] - klines[i-1]["close"]
        changes.append(change)
    
    # 计算初始平均涨幅和跌幅
    gains = [c for c in changes[:period] if c > 0]
    losses = [-c for c in changes[:period] if c < 0]
    
    if not gains or not losses:
        return []
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    results = []
    
    # 计算第一个 RSI
    if avg_loss == 0:
        rsi = 100
    else:
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
    
    results.append({
        "date": klines[period]["date"],
        "rsi": rsi,
        "close": klines[period]["close"],
    })
    
    # 使用 Wilder 平滑计算后续 RSI
    for i in range(period, len(changes)):
        change = changes[i]
        
        # 更新平均涨幅和跌幅
        if change > 0:
            avg_gain = (avg_gain * (period - 1) + change) / period
            avg_loss = (avg_loss * (period - 1)) / period
        else:
            avg_gain = (avg_gain * (period - 1)) / period
            avg_loss = (avg_loss * (period - 1) - change) / period
        
        # 计算 RSI
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - 100 / (1 + rs)
        
        results.append({
            "date": klines[i + 1]["date"],
            "rsi": rsi,
            "close": klines[i + 1]["close"],
        })
    
    return results


def _get_zone(rsi: float) -> str:
    """判断 RSI 所在区间"""
    if rsi >= RSI_CONFIG["strong_overbought"]:
        return "strong_overbought"
    elif rsi >= RSI_CONFIG["overbought"]:
        return "overbought"
    elif rsi <= RSI_CONFIG["strong_oversold"]:
        return "strong_oversold"
    elif rsi <= RSI_CONFIG["oversold"]:
        return "oversold"
    else:
        return "neutral"


def _detect_signal(rsi_values: List[Dict], klines: List[Dict]) -> Optional[Dict]:
    """
    检测 RSI 信号。
    
    信号类型：
      - buy: RSI 从超卖区回升（金叉 30）
      - sell: RSI 从超买区回落（死叉 70）
      - divergence_bull: 底背离（价格新低，RSI 未新低）
      - divergence_bear: 顶背离（价格新高，RSI 未新高）
    """
    if len(rsi_values) < 5:
        return None
    
    current = rsi_values[-1]
    prev = rsi_values[-2]
    
    # 1. 金叉/死叉检测
    # 金叉：前一天 RSI < 30，今天 RSI > 30
    if prev["rsi"] < RSI_CONFIG["oversold"] and current["rsi"] >= RSI_CONFIG["oversold"]:
        return {
            "type": "buy",
            "description": f"RSI 金叉 {RSI_CONFIG['oversold']}，超卖回升信号",
            "strength": "medium",
        }
    
    # 死叉：前一天 RSI > 70，今天 RSI < 70
    if prev["rsi"] > RSI_CONFIG["overbought"] and current["rsi"] <= RSI_CONFIG["overbought"]:
        return {
            "type": "sell",
            "description": f"RSI 死叉 {RSI_CONFIG['overbought']}，超买回落信号",
            "strength": "medium",
        }
    
    # 强超卖反弹
    if current["rsi"] <= RSI_CONFIG["strong_oversold"]:
        return {
            "type": "buy",
            "description": f"RSI 进入强超卖区 ({current['rsi']:.1f})，可能反弹",
            "strength": "high",
        }
    
    # 强超买回调
    if current["rsi"] >= RSI_CONFIG["strong_overbought"]:
        return {
            "type": "sell",
            "description": f"RSI 进入强超买区 ({current['rsi']:.1f})，注意回调",
            "strength": "high",
        }
    
    # 2. 背离检测（需要更多数据）
    if len(rsi_values) >= 10:
        divergence = _detect_divergence(rsi_values[-10:], klines[-10:])
        if divergence:
            return divergence
    
    return None


def _detect_divergence(rsi_values: List[Dict], klines: List[Dict]) -> Optional[Dict]:
    """
    检测 RSI 背离。
    
    底背离：价格创新低，但 RSI 未创新低 → 看涨
    顶背离：价格创新高，但 RSI 未创新高 → 看跌
    """
    if len(rsi_values) < 5:
        return None
    
    # 找最近两个低点（底背离）或高点（顶背离）
    recent = rsi_values[-5:]
    recent_prices = [k["close"] for k in klines[-5:]]
    
    # 底背离检测
    min_idx_1 = recent[:3].index(min(recent[:3], key=lambda x: x["rsi"]))
    min_idx_2 = recent[3:].index(min(recent[3:], key=lambda x: x["rsi"])) + 3
    
    if min_idx_1 != min_idx_2:
        price_new_low = recent_prices[min_idx_2] < recent_prices[min_idx_1]
        rsi_not_new_low = recent[min_idx_2]["rsi"] > recent[min_idx_1]["rsi"]
        
        if price_new_low and rsi_not_new_low:
            return {
                "type": "divergence_bull",
                "description": "底背离：价格新低但 RSI 未新低，看涨信号",
                "strength": "high",
            }
    
    # 顶背离检测
    max_idx_1 = recent[:3].index(max(recent[:3], key=lambda x: x["rsi"]))
    max_idx_2 = recent[3:].index(max(recent[3:], key=lambda x: x["rsi"])) + 3
    
    if max_idx_1 != max_idx_2:
        price_new_high = recent_prices[max_idx_2] > recent_prices[max_idx_1]
        rsi_not_new_high = recent[max_idx_2]["rsi"] < recent[max_idx_1]["rsi"]
        
        if price_new_high and rsi_not_new_high:
            return {
                "type": "divergence_bear",
                "description": "顶背离：价格新高但 RSI 未新高，看跌信号",
                "strength": "high",
            }
    
    return None


def _interpret_rsi(rsi: float, zone: str, signal: Optional[Dict]) -> str:
    """生成 RSI 解读文字"""
    parts = []
    
    # 区间描述
    if zone == "strong_overbought":
        parts.append(f"RSI={rsi:.1f}，强超买区")
    elif zone == "overbought":
        parts.append(f"RSI={rsi:.1f}，超买区")
    elif zone == "strong_oversold":
        parts.append(f"RSI={rsi:.1f}，强超卖区")
    elif zone == "oversold":
        parts.append(f"RSI={rsi:.1f}，超卖区")
    elif rsi > 50:
        parts.append(f"RSI={rsi:.1f}，偏多区间")
    else:
        parts.append(f"RSI={rsi:.1f}，偏空区间")
    
    # 信号描述
    if signal:
        parts.append(signal["description"])
    else:
        parts.append("无明显信号")
    
    return "，".join(parts)


def _default_result(code: str, reason: str = "") -> Dict:
    """默认结果"""
    return {
        "code": code,
        "current_rsi": 50,
        "zone": "neutral",
        "signal": None,
        "rsi_history": [],
        "config": {
            "overbought": RSI_CONFIG["overbought"],
            "oversold": RSI_CONFIG["oversold"],
        },
        "interpretation": reason,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
