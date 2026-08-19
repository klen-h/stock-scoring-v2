"""
================================================================================
【文件作用】"老鸭头"战法实现
================================================================================

核心逻辑：
  5/10/60日均线多头排列 → 5日死叉10日缩量回调不破60日线 → 5日再金叉10日放量启动

三阶段：
  鸭颈：建仓拉升，均线多头排列
  鸭头：洗盘调整，5日死叉10日，不破60日线
  鸭嘴：再起动，5日金叉10日，放量

适合捕捉主升浪的稳健战法。
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class OldDuckHeadStrategy(BaseStrategy):
    """老鸭头战法"""
    
    name = "老鸭头"
    name_en = "old_duck_head"
    description = "5/10/60日均线多头排列后，5日死叉10日缩量回调不破60日线，5日再金叉10日且放量时介入。"
    
    CONFIG = {
        # ── 均线参数 ──
        "ma_short": 5,
        "ma_mid": 10,
        "ma_long": 60,
        
        # ── 鸭颈条件 ──
        "neck_min_rise": 10.0,          # 鸭颈最小涨幅 %
        "neck_head_ratio": 1.1,         # 鸭头顶价格 > 60日线 × 此值
        
        # ── 鸭头条件（洗盘） ──
        "head_min_pullback": 5.0,       # 最小回调幅度 %
        "head_max_pullback": 30.0,      # 最大回调幅度 %
        "head_best_min": 8.0,           # 最佳回调下限
        "head_best_max": 25.0,          # 最佳回调上限
        "head_max_vol_ratio": 0.5,      # 鸭头缩量程度（相对鸭颈）
        
        # ── 鸭嘴条件（启动） ──
        "mouth_min_vol_ratio": 1.5,     # 鸭嘴最小量比（相对鸭头）
        "mouth_min_change": 2.0,        # 鸭嘴最小涨幅
        
        # ── 止损 ──
        "stop_loss_buffer": 0.03,       # 60日线下方3%
        
        # ── 置信度 ──
        "high_confidence_score": 6,
        "medium_confidence_score": 4,
    }
    
    def scan(self, stock_pool: List[Dict]) -> List[Dict]:
        signals = []
        for stock in stock_pool:
            code = stock["code"]
            try:
                signal = self._check_stock(code, stock)
                if signal:
                    signals.append(signal)
            except Exception:
                continue
        signals.sort(key=lambda x: x["confidence"], reverse=True)
        return signals
    
    def _check_stock(self, code: str, stock: Dict) -> Optional[Dict]:
        cfg = self.CONFIG
        klines = get_kline_with_indicators(code, count=120)
        if len(klines) < 70:
            return None
        
        # ── 检测三阶段 ──
        duck = self._detect_duck_pattern(klines)
        if not duck:
            return None
        
        neck_info, head_info, mouth_info = duck
        
        # ── 置信度 ──
        confidence = self._calc_confidence(neck_info, head_info, mouth_info)
        
        current = klines[-1]
        entry_price = current["close"]
        ma60 = head_info["ma60_value"]
        stop_loss = ma60 * (1 - cfg["stop_loss_buffer"])
        target_price = entry_price * 1.20  # 目标20%
        
        confidence_level = "low"
        if confidence >= cfg["high_confidence_score"]:
            confidence_level = "high"
        elif confidence >= cfg["medium_confidence_score"]:
            confidence_level = "medium"
        
        return {
            "code": code,
            "name": stock["name"],
            "signal_date": current["date"],
            "confidence": confidence,
            "confidence_level": confidence_level,
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "target_price": round(target_price, 2),
            "position_pct": calc_position_in_range(klines, lookback=60),
            "details": {
                "neck": neck_info,
                "head": head_info,
                "mouth": mouth_info,
            },
            "klines": klines[-10:],
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _detect_duck_pattern(self, klines: List[Dict]) -> Optional[Tuple[Dict, Dict, Dict]]:
        """检测老鸭头三阶段形态"""
        cfg = self.CONFIG
        
        # 计算各均线
        def calc_ma(idx, period):
            if idx < period - 1:
                return None
            return sum(k["close"] for k in klines[idx-period+1:idx+1]) / period
        
        # 找最近的5日金叉10日（鸭嘴）
        golden_cross_idx = None
        for i in range(len(klines) - 1, 10, -1):
            ma5_now = calc_ma(i, cfg["ma_short"])
            ma10_now = calc_ma(i, cfg["ma_mid"])
            ma5_prev = calc_ma(i-1, cfg["ma_short"])
            ma10_prev = calc_ma(i-1, cfg["ma_mid"])
            
            if ma5_now and ma10_now and ma5_prev and ma10_prev:
                if ma5_prev <= ma10_prev and ma5_now > ma10_now:
                    golden_cross_idx = i
                    break
        
        if not golden_cross_idx:
            return None
        
        # 找5日死叉10日（鸭头）- 在金叉之前
        death_cross_idx = None
        for i in range(golden_cross_idx - 1, max(golden_cross_idx - 30, 10), -1):
            ma5_now = calc_ma(i, cfg["ma_short"])
            ma10_now = calc_ma(i, cfg["ma_mid"])
            ma5_prev = calc_ma(i-1, cfg["ma_short"])
            ma10_prev = calc_ma(i-1, cfg["ma_mid"])
            
            if ma5_now and ma10_now and ma5_prev and ma10_prev:
                if ma5_prev >= ma10_prev and ma5_now < ma10_now:
                    death_cross_idx = i
                    break
        
        if not death_cross_idx:
            return None
        
        # 鸭颈：死叉前的上升段
        neck_start = max(0, death_cross_idx - 20)
        neck_klines = klines[neck_start:death_cross_idx]
        if len(neck_klines) < 5:
            return None
        
        neck_rise = (neck_klines[-1]["close"] - neck_klines[0]["open"]) / neck_klines[0]["open"] * 100
        if neck_rise < cfg["neck_min_rise"]:
            return None
        
        # 60日均线
        ma60_at_death = calc_ma(death_cross_idx, cfg["ma_long"])
        if not ma60_at_death:
            return None
        
        # 60日线必须向上或走平
        ma60_prev = calc_ma(death_cross_idx - 5, cfg["ma_long"])
        if ma60_prev and ma60_at_death < ma60_prev * 0.99:
            return None  # 60日线向下
        
        # 鸭头顶价格与60日线的关系
        head_high = max(k["high"] for k in neck_klines)
        if head_high < ma60_at_death * cfg["neck_head_ratio"]:
            return None
        
        # 鸭头：洗盘期（死叉到金叉之间）
        head_klines = klines[death_cross_idx:golden_cross_idx+1]
        if len(head_klines) < 3:
            return None
        
        head_low = min(k["low"] for k in head_klines)
        head_high_price = max(k["high"] for k in head_klines)
        
        # 不能跌破60日线
        if head_low < ma60_at_death:
            return None
        
        # 回调幅度
        peak_price = head_high
        pullback_pct = (peak_price - head_low) / peak_price * 100
        if pullback_pct < cfg["head_min_pullback"]:
            return None
        if pullback_pct > cfg["head_max_pullback"]:
            return None
        
        # 缩量检查
        neck_avg_vol = sum(k["volume"] for k in neck_klines) / len(neck_klines)
        head_avg_vol = sum(k["volume"] for k in head_klines) / len(head_klines)
        vol_ratio = head_avg_vol / neck_avg_vol if neck_avg_vol > 0 else 1
        
        if vol_ratio > cfg["head_max_vol_ratio"]:
            return None  # 没有明显缩量
        
        # 鸭嘴：金叉日
        mouth_k = klines[golden_cross_idx]
        mouth_vol_ratio = mouth_k["volume"] / head_avg_vol if head_avg_vol > 0 else 1
        
        if mouth_k["change_pct"] < cfg["mouth_min_change"]:
            return None
        
        if mouth_vol_ratio < cfg["mouth_min_vol_ratio"]:
            return None
        
        neck_info = {
            "rise_pct": round(neck_rise, 2),
            "days": len(neck_klines),
            "avg_volume": round(neck_avg_vol, 0),
        }
        
        head_info = {
            "days": len(head_klines),
            "pullback_pct": round(pullback_pct, 2),
            "low": round(head_low, 2),
            "ma60_value": round(ma60_at_death, 2),
            "volume_ratio": round(vol_ratio, 2),
            "is_best_window": cfg["head_best_min"] <= pullback_pct <= cfg["head_best_max"],
        }
        
        mouth_info = {
            "date": mouth_k["date"],
            "change_pct": mouth_k["change_pct"],
            "volume_ratio": round(mouth_vol_ratio, 2),
        }
        
        return neck_info, head_info, mouth_info
    
    def _calc_confidence(self, neck_info: Dict, head_info: Dict, mouth_info: Dict) -> int:
        score = 0
        
        # 回调幅度评分
        if head_info["is_best_window"]:
            score += 2
        elif head_info["pullback_pct"] <= 25:
            score += 1
        
        # 缩量程度评分
        if head_info["volume_ratio"] <= 0.3:
            score += 2
        elif head_info["volume_ratio"] <= 0.5:
            score += 1
        
        # 鸭嘴量能评分
        if mouth_info["volume_ratio"] >= 2:
            score += 2
        elif mouth_info["volume_ratio"] >= 1.5:
            score += 1
        
        return score


_strategy = OldDuckHeadStrategy()

def get_strategy():
    return _strategy
