"""
================================================================================
【文件作用】"涨停双响炮"战法实现
================================================================================

核心逻辑：
  首板涨停 → 缩量回调1-5天 → 再度涨停启动主升浪

与其他战法的区别：
  - 两炮之间必须缩量且温和回调
  - 对"位置"要求仅次于N字战法
  - 买点：第二炮涨停打板 或 中间段缩量低吸

形态：
  第一炮：放量涨停突破平台
  中间段：1-5根缩量回调K线（不破首板收盘价最佳）
  第二炮：再度放量涨停启动
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class DoubleCannonStrategy(BaseStrategy):
    """涨停双响炮战法"""
    
    name = "涨停双响炮"
    name_en = "double_cannon"
    description = "首板涨停后缩量回调1-5天，再度放量涨停时介入的双响炮形态。低位启动信号，中间段越缩越安全。"
    
    CONFIG = {
        # ── 位置过滤 ──
        "max_60d_gain": 80.0,           # 近60日最大涨幅 %（超过排除）
        
        # ── 第一炮：首板涨停 ──
        "lookback_for_first": 15,       # 回看多少天内找第一炮
        "min_limit_change": 9.5,        # 涨停阈值 %
        "first_min_volume_ratio": 2.0,  # 首板最小量比（相对前5日均量）
        
        # ── 中间段：缩量回调 ──
        "pullback_min_days": 1,         # 最小回调K线数
        "pullback_best_min": 2,         # 最佳回调天数下限
        "pullback_best_max": 3,         # 最佳回调天数上限
        "pullback_max_days": 5,         # 最大回调K线数（超过形态失效）
        "pullback_max_vol_ratio": 0.5,  # 中间段均量 < 首板量 × 此值（缩量）
        "pullback_max_depth": 0.50,     # 回调幅度不超过首板涨幅的50%
        "max_single_drop": -7.0,        # 中间段单根最大跌幅（超过有大阴线，排除）
        
        # ── 第二炮：再板启动 ──
        "second_min_volume_ratio": 1.5, # 第二炮最小量比（相对中间段均量）
        
        # ── 止损 ──
        "stop_loss_buffer": 0.03,       # 止损缓冲：首板开盘价下方3%
        
        # ── 置信度 ──
        "high_confidence_score": 6,
        "medium_confidence_score": 4,
    }
    
    def scan(self, stock_pool: List[Dict]) -> List[Dict]:
        """扫描股票池"""
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
        """检查单只股票"""
        cfg = self.CONFIG
        
        # 获取K线
        klines = get_kline_with_indicators(code, count=60)
        if len(klines) < 20:
            return None
        
        # ── 位置过滤 ──
        if not self._check_position(klines):
            return None
        
        # ── 第一炮：找首板涨停 ──
        first_cannon = self._detect_first_cannon(klines)
        if not first_cannon:
            return None
        
        first_idx, first_info = first_cannon
        
        # ── 中间段：检测缩量回调 ──
        pullback = self._detect_pullback(klines, first_idx, first_info)
        if not pullback:
            return None
        
        pullback_end_idx, pullback_info = pullback
        
        # ── 第二炮：检测再度涨停 ──
        second_cannon = self._detect_second_cannon(klines, pullback_end_idx, pullback_info)
        if not second_cannon:
            return None
        
        second_idx, second_info = second_cannon
        
        # ── 计算置信度 ──
        confidence = self._calc_confidence(first_info, pullback_info, second_info)
        
        # ── 关键价位 ──
        current = klines[-1]
        entry_price = current["close"]
        
        # 止损：首板开盘价下方3%，但不能超过介入价的15%
        stop_loss = first_info["first_open"] * (1 - cfg["stop_loss_buffer"])
        if stop_loss < entry_price * 0.85:
            stop_loss = entry_price * 0.85
        
        # 目标：两炮距离的一倍量度，至少15%盈利空间
        cannon_distance = second_info["second_close"] - first_info["first_close"]
        target_price = max(entry_price + cannon_distance, entry_price * 1.15)
        
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
                "first_cannon": first_info,
                "pullback": pullback_info,
                "second_cannon": second_info,
                "support_levels": {
                    "strong": round(first_info["first_close"], 2),   # 首板收盘价
                    "weak": round(first_info["first_open"], 2),      # 首板开盘价（止损参考）
                },
            },
            "klines": klines[-10:],
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _check_position(self, klines: List[Dict]) -> bool:
        """位置过滤：近60日涨幅<80%"""
        cfg = self.CONFIG
        if len(klines) < 20:
            return False
        
        recent = klines[-min(60, len(klines)):]
        start_price = recent[0]["open"]
        if start_price <= 0:
            return False
        
        end_price = recent[-1]["close"]
        gain = (end_price - start_price) / start_price * 100
        if gain >= cfg["max_60d_gain"]:
            return False
        
        return True
    
    def _detect_first_cannon(self, klines: List[Dict]) -> Optional[Tuple[int, Dict]]:
        """
        检测第一炮：首板涨停。
        在回看区间内找到放量涨停板。
        """
        cfg = self.CONFIG
        lookback = cfg["lookback_for_first"]
        
        # 从最近往前找（给中间段和第二炮留出空间）
        search_end = len(klines) - 2  # 至少留2根给中间段+第二炮
        search_start = max(0, search_end - lookback)
        
        for i in range(search_end, search_start - 1, -1):
            k = klines[i]
            
            # 检查是否涨停（涨幅≥9.5%）
            if k["change_pct"] < cfg["min_limit_change"]:
                continue
            
            # 检查放量：成交量 > 前5日均量 × 2
            pre_klines = klines[max(0, i-5):i]
            if len(pre_klines) < 3:
                continue
            
            avg_pre_vol = sum(pk["volume"] for pk in pre_klines) / len(pre_klines)
            if avg_pre_vol <= 0:
                continue
            
            vol_ratio = k["volume"] / avg_pre_vol
            if vol_ratio < cfg["first_min_volume_ratio"]:
                continue
            
            # 首板信息
            first_info = {
                "date": k["date"],
                "first_open": k["open"],
                "first_close": k["close"],
                "first_high": k["high"],
                "first_low": k["low"],
                "first_volume": k["volume"],
                "first_change": k["change_pct"],
                "volume_ratio": round(vol_ratio, 2),
            }
            
            return i, first_info
        
        return None
    
    def _detect_pullback(self, klines: List[Dict], first_idx: int, first_info: Dict) -> Optional[Tuple[int, Dict]]:
        """
        检测中间段：缩量回调。
        回调1-5根K线，量能萎缩，不破首板收盘价最佳。
        """
        cfg = self.CONFIG
        
        # 从第一炮后第一天开始
        pullback_start = first_idx + 1
        remaining = klines[pullback_start:]
        
        if len(remaining) < cfg["pullback_min_days"]:
            return None
        
        first_close = first_info["first_close"]
        first_volume = first_info["first_volume"]
        
        pullback_klines = []
        pullback_low = float("inf")
        pullback_high = 0
        
        for i, k in enumerate(remaining):
            # 超过最大回调天数
            if i >= cfg["pullback_max_days"] + 2:  # +2 给第二炮留空间
                break
            
            # 检查是否出现第二炮（涨停），说明中间段结束
            if k["change_pct"] >= cfg["min_limit_change"]:
                break
            
            # 检查中间段不能有大阴线
            if k["change_pct"] < cfg["max_single_drop"]:
                return None  # 中间段出现大阴线，形态破坏
            
            pullback_klines.append(k)
            pullback_low = min(pullback_low, k["low"])
            pullback_high = max(pullback_high, k["high"])
        
        pullback_days = len(pullback_klines)
        if pullback_days < cfg["pullback_min_days"]:
            return None
        if pullback_days > cfg["pullback_max_days"]:
            return None  # 回调过长，形态失效
        
        # 检查缩量：中间段均量 < 首板量 × 50%
        if pullback_days > 0:
            avg_pb_vol = sum(k["volume"] for k in pullback_klines) / pullback_days
            vol_ratio = avg_pb_vol / first_volume if first_volume > 0 else 1
            if vol_ratio > cfg["pullback_max_vol_ratio"]:
                return None  # 中间段没有明显缩量
        else:
            avg_pb_vol = 0
            vol_ratio = 1
        
        # 检查回调幅度：不超过首板涨幅的50%
        first_gain = first_info["first_change"]
        if first_gain > 0:
            pullback_depth = (first_close - pullback_low) / first_close * 100
            max_allowed = first_gain * cfg["pullback_max_depth"]
            if pullback_depth > max_allowed:
                return None  # 回调过深
        else:
            pullback_depth = 0
        
        # 判断支撑强度
        if pullback_low >= first_close:
            support_level = "strong"  # 不破首板收盘价
        elif pullback_low >= first_info["first_open"]:
            support_level = "medium"  # 不破首板开盘价
        else:
            support_level = "weak"    # 跌破首板开盘价
        
        pullback_end_idx = pullback_start + pullback_days - 1
        
        pullback_info = {
            "days": pullback_days,
            "low": round(pullback_low, 2),
            "high": round(pullback_high, 2),
            "depth_pct": round(pullback_depth, 2),
            "volume_ratio": round(vol_ratio, 2),
            "avg_volume": round(avg_pb_vol, 0),
            "support_level": support_level,
            "is_golden_window": cfg["pullback_best_min"] <= pullback_days <= cfg["pullback_best_max"],
        }
        
        return pullback_end_idx, pullback_info
    
    def _detect_second_cannon(self, klines: List[Dict], pullback_end_idx: int, pullback_info: Dict) -> Optional[Tuple[int, Dict]]:
        """
        检测第二炮：再度涨停启动。
        在中间段之后出现放量涨停。
        """
        cfg = self.CONFIG
        
        # 从中间段结束后开始找
        remaining = klines[pullback_end_idx + 1:]
        if not remaining:
            return None
        
        avg_pb_vol = pullback_info.get("avg_volume", 0)
        
        for i, k in enumerate(remaining):
            # 检查是否涨停
            if k["change_pct"] < cfg["min_limit_change"]:
                continue
            
            # 检查放量（相对中间段均量）
            if avg_pb_vol > 0:
                vol_ratio = k["volume"] / avg_pb_vol
            else:
                vol_ratio = 2.0  # 数据不足时假设满足
            
            if vol_ratio < cfg["second_min_volume_ratio"]:
                continue
            
            # 检查是否突破中间段高点
            is_breakout = k["close"] > pullback_info.get("high", 0)
            
            second_info = {
                "date": k["date"],
                "second_close": k["close"],
                "second_high": k["high"],
                "second_volume": k["volume"],
                "second_change": k["change_pct"],
                "volume_ratio": round(vol_ratio, 2),
                "is_breakout": is_breakout,
            }
            
            return pullback_end_idx + 1 + i, second_info
        
        return None
    
    def _calc_confidence(self, first_info: Dict, pullback_info: Dict, second_info: Dict) -> int:
        """计算置信度评分"""
        cfg = self.CONFIG
        score = 0
        
        # 中间段天数评分（2-3天最佳）
        if pullback_info["is_golden_window"]:
            score += 2
        elif pullback_info["days"] <= cfg["pullback_best_max"]:
            score += 1
        
        # 缩量程度评分
        vol_ratio = pullback_info["volume_ratio"]
        if vol_ratio <= 0.3:
            score += 2  # 极度缩量
        elif vol_ratio <= 0.5:
            score += 1  # 明显缩量
        
        # 支撑强度评分
        if pullback_info["support_level"] == "strong":
            score += 2  # 不破首板收盘价
        elif pullback_info["support_level"] == "medium":
            score += 1  # 不破首板开盘价
        
        # 第二炮质量评分
        if second_info.get("is_breakout") and second_info.get("volume_ratio", 0) >= 2:
            score += 2  # 放量突破中间段高点
        elif second_info.get("is_breakout"):
            score += 1  # 突破中间段高点
        
        return score


# ── 注册策略 ──
_strategy = DoubleCannonStrategy()


def get_strategy():
    return _strategy
