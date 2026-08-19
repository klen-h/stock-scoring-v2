"""
================================================================================
【文件作用】"均线回踩"战法实现
================================================================================

核心逻辑：
  上升趋势中股价缩量回踩5日/10日/20日均线不破，放量反弹时介入

三种模式：
  强势回踩（5日线）：主升浪强势股，回踩1-3天
  标准回踩（10日线）：波段上升趋势股，回踩3-5天
  稳健回踩（20日线）：中线趋势股，回踩5-8天

适合震荡市和趋势行情，不需要涨停。
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class MAPullbackStrategy(BaseStrategy):
    """均线回踩战法"""
    
    name = "均线回踩"
    name_en = "ma_pullback"
    description = "上升趋势中缩量回踩5日/10日/20日均线不破，出现止跌信号后低吸。最简单的趋势跟随战法。"
    
    CONFIG = {
        # ── 均线参数 ──
        "ma_strong": 5,
        "ma_standard": 10,
        "ma_stable": 20,
        
        # ── 趋势确认 ──
        "ma_trend_days": 5,             # 均线方向判断天数
        "min_ma_slope_pct": 0.5,        # 均线最小斜率（%）
        
        # ── 回踩条件 ──
        "pullback_touch_pct": 2.0,      # 回踩到均线附近（±2%以内）
        "pullback_min_days": 1,         # 最小回踩天数
        "pullback_max_days_strong": 3,  # 强势回踩最大天数
        "pullback_max_days_standard": 5,  # 标准回踩最大天数
        "pullback_max_days_stable": 8,  # 稳健回踩最大天数
        "pullback_max_vol_ratio": 0.7,  # 回踩时最大量比（缩量）
        
        # ── 反弹确认 ──
        "bounce_min_change": 1.0,       # 反弹日最小涨幅
        "bounce_min_volume_ratio": 1.5, # 反弹日最小量比
        
        # ── 止损 ──
        "stop_loss_buffer": 0.03,       # 均线下方3%
        
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
        klines = get_kline_with_indicators(code, count=60)
        if len(klines) < 30:
            return None
        
        # ── 检查多头排列 ──
        if not self._check_ma_alignment(klines):
            return None
        
        # ── 依次检查三种回踩模式 ──
        for mode_name, ma_period, max_days in [
            ("强势回踩", cfg["ma_strong"], cfg["pullback_max_days_strong"]),
            ("标准回踩", cfg["ma_standard"], cfg["pullback_max_days_standard"]),
            ("稳健回踩", cfg["ma_stable"], cfg["pullback_max_days_stable"]),
        ]:
            result = self._check_pullback(klines, ma_period, max_days, mode_name)
            if result:
                pullback_info, bounce_info = result
                
                confidence = self._calc_confidence(pullback_info, mode_name)
                
                current = klines[-1]
                entry_price = current["close"]
                ma_value = pullback_info["ma_value"]
                stop_loss = ma_value * (1 - cfg["stop_loss_buffer"])
                target_price = entry_price * 1.12  # 目标12%
                
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
                        "mode": mode_name,
                        "ma_period": ma_period,
                        "pullback": pullback_info,
                        "bounce": bounce_info,
                    },
                    "klines": klines[-5:],
                    "market_cap": stock.get("market_cap", 0),
                }
        
        return None
    
    def _check_ma_alignment(self, klines: List[Dict]) -> bool:
        """检查均线多头排列"""
        if len(klines) < 20:
            return False
        
        ma5 = sum(k["close"] for k in klines[-5:]) / 5
        ma10 = sum(k["close"] for k in klines[-10:]) / 10
        ma20 = sum(k["close"] for k in klines[-20:]) / 20
        
        # 至少 5日线 > 10日线 > 20日线
        if ma5 <= ma10:
            return False
        if ma10 <= ma20:
            return False
        
        # 20日均线必须向上
        ma20_prev = sum(k["close"] for k in klines[-25:-5]) / 20 if len(klines) >= 25 else ma20
        if ma20 <= ma20_prev:
            return False
        
        return True
    
    def _check_pullback(self, klines: List[Dict], ma_period: int, max_days: int, mode_name: str) -> Optional[Tuple[Dict, Dict]]:
        """检查某种模式的回踩"""
        cfg = self.CONFIG
        
        # 计算当前均线值
        if len(klines) < ma_period:
            return None
        ma_value = sum(k["close"] for k in klines[-ma_period:]) / ma_period
        
        # 检查均线方向向上
        ma_prev = sum(k["close"] for k in klines[-ma_period-cfg["ma_trend_days"]:-cfg["ma_trend_days"]]) / ma_period
        if ma_prev <= 0:
            return None
        ma_slope = (ma_value - ma_prev) / ma_prev * 100
        if ma_slope < cfg["min_ma_slope_pct"]:
            return None
        
        # 从最近往前找回踩区间
        # 检查最近几根K线是否有回踩均线的行为
        search_end = len(klines) - 1
        search_start = max(0, search_end - max_days - 3)
        
        pullback_klines = []
        pullback_low = float("inf")
        
        for i in range(search_end, search_start - 1, -1):
            k = klines[i]
            # 检查是否回踩到均线附近
            distance_to_ma = abs(k["low"] - ma_value) / ma_value * 100
            if distance_to_ma <= cfg["pullback_touch_pct"]:
                pullback_klines.insert(0, k)
                pullback_low = min(pullback_low, k["low"])
            elif pullback_klines:
                break  # 已经离开均线区域
        
        if len(pullback_klines) < cfg["pullback_min_days"]:
            return None
        
        # 检查缩量
        pre_vol = sum(k["volume"] for k in klines[-10:-len(pullback_klines)]) / max(1, 10 - len(pullback_klines))
        pb_avg_vol = sum(k["volume"] for k in pullback_klines) / len(pullback_klines)
        vol_ratio = pb_avg_vol / pre_vol if pre_vol > 0 else 1
        
        if vol_ratio > cfg["pullback_max_vol_ratio"]:
            return None  # 没有缩量
        
        # 检查是否出现反弹阳线
        last_k = klines[-1]
        if not last_k["is_positive"] or last_k["change_pct"] < cfg["bounce_min_change"]:
            return None
        
        # 反弹放量
        prev_vol = klines[-2]["volume"] if len(klines) >= 2 else last_k["volume"]
        bounce_vol_ratio = last_k["volume"] / prev_vol if prev_vol > 0 else 1
        if bounce_vol_ratio < cfg["bounce_min_volume_ratio"]:
            return None
        
        # 不能跌破均线太多
        if pullback_low < ma_value * (1 - cfg["pullback_touch_pct"] / 100):
            return None
        
        pullback_info = {
            "days": len(pullback_klines),
            "ma_value": round(ma_value, 2),
            "pullback_low": round(pullback_low, 2),
            "distance_pct": round(abs(pullback_low - ma_value) / ma_value * 100, 2),
            "volume_ratio": round(vol_ratio, 2),
        }
        
        bounce_info = {
            "date": last_k["date"],
            "change_pct": last_k["change_pct"],
            "volume_ratio": round(bounce_vol_ratio, 2),
        }
        
        return pullback_info, bounce_info
    
    def _calc_confidence(self, pullback_info: Dict, mode_name: str) -> int:
        score = 0
        
        # 模式评分
        if mode_name == "强势回踩":
            score += 2
        elif mode_name == "标准回踩":
            score += 1
        
        # 缩量程度
        if pullback_info["volume_ratio"] <= 0.4:
            score += 2
        elif pullback_info["volume_ratio"] <= 0.7:
            score += 1
        
        # 回踩精准度（越接近均线越好）
        if pullback_info["distance_pct"] <= 0.5:
            score += 2
        elif pullback_info["distance_pct"] <= 1.5:
            score += 1
        
        return score


_strategy = MAPullbackStrategy()

def get_strategy():
    return _strategy
