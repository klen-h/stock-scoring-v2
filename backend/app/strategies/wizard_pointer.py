"""
================================================================================
【文件作用】"仙人指路"战法实现
================================================================================

核心逻辑：
  强势股拉升途中，长上影试盘K线 + 次日高开高走确认

与其他战法的关键区别：
  - 范围最窄：必须先筛选"强势股"
  - 两段式确认：当日预筛选 + 次日早盘确认
  - 90%的长上影是见顶信号，只有强势股拉升途中的才是"仙人指路"

形态：
  前一日：放量大阳线（涨幅≥5%）
  当日：长上影小阴阳K线（振幅≥7%，换手<5%，量比>1）
  次日：高开高走确认（最关键）
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class WizardPointerStrategy(BaseStrategy):
    """仙人指路战法"""
    
    name = "仙人指路"
    name_en = "wizard_pointer"
    description = "强势股拉升途中的长上影试盘形态，次日高开高走确认后进驻。范围最窄但成功率高。"
    
    CONFIG = {
        # ── 强势股过滤 ──
        "strong_stock_min_gain": 15.0,    # 近20日最小涨幅（需超过大盘+15%）
        "trend_ma_short": 5,              # 短期均线
        "trend_ma_long": 10,              # 长期均线
        "min_trend_days": 10,             # 最少趋势天数
        
        # ── 前一日条件 ──
        "prev_min_change": 5.0,           # 前一日最小涨幅
        "prev_min_volume_ratio": 1.5,     # 前一日最小量比
        
        # ── 当日（仙人指路K线）条件 ──
        "min_amplitude": 7.0,             # 最小振幅 %
        "max_turnover_rate": 5.0,         # 最大换手率 %
        "min_volume_ratio": 1.0,          # 最小量比
        "max_change_pct": 3.0,            # 最大涨跌幅 %（收盘保持强势）
        "min_change_pct": -3.0,           # 最小涨跌幅 %
        "max_body_ratio": 0.3,            # 实体占整根K线最大比例（小实体）
        "min_upper_shadow_ratio": 0.5,    # 上影线占整根K线最小比例（长上影）
        "max_lower_shadow_ratio": 0.2,    # 下影线占整根K线最大比例（短下影）
        
        # ── 次日确认条件 ──
        "confirm_min_change": 1.0,        # 次日最小涨幅（高开高走）
        "confirm_must_be_positive": True, # 次日必须是阳线
        
        # ── 位置过滤 ──
        "max_position_pct": 50.0,         # 最大位置（高位放弃）
        "max_recent_gain": 30.0,          # 近期最大涨幅（超过30%放弃）
        
        # ── 止损 ──
        "stop_loss_pct": 0.05,            # 止损幅度 5%
        
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
        klines = get_kline_with_indicators(code, count=30)
        if len(klines) < 15:
            return None
        
        # ── 强势股过滤 ──
        if not self._is_strong_stock(klines):
            return None
        
        # ── 位置过滤 ──
        position = calc_position_in_range(klines, lookback=60)
        if position > cfg["max_position_pct"]:
            return None
        
        # 近期涨幅检查（避免高位）
        recent_gain = self._calc_recent_gain(klines, days=20)
        if recent_gain > cfg["max_recent_gain"]:
            return None
        
        # ── 检查最近一根完整K线是否是"仙人指路" ──
        # 注意：需要检查倒数第2根K线（最后一根可能是次日确认K线）
        wizard_idx = len(klines) - 2
        wizard_k = klines[wizard_idx]
        prev_k = klines[wizard_idx - 1] if wizard_idx > 0 else None
        confirm_k = klines[-1]  # 次日确认K线
        
        # 检查当日（仙人指路K线）条件
        if not self._check_wizard_kline(wizard_k, klines[:wizard_idx]):
            return None
        
        # 检查前一日条件
        if prev_k and not self._check_prev_kline(prev_k, klines[:wizard_idx-1]):
            return None
        
        # 检查次日确认
        if not self._check_confirm_kline(confirm_k, wizard_k):
            return None
        
        # ── 计算置信度 ──
        confidence = self._calc_confidence(wizard_k, confirm_k, position)
        
        # ── 关键价位 ──
        current = klines[-1]
        entry_price = current["close"]
        stop_loss = entry_price * (1 - cfg["stop_loss_pct"])
        target_price = entry_price * 1.15  # 目标15%
        
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
            "position_pct": position,
            "details": {
                "wizard": {
                    "date": wizard_k["date"],
                    "change_pct": wizard_k["change_pct"],
                    "amplitude": self._calc_amplitude(wizard_k),
                    "upper_shadow_ratio": self._calc_upper_shadow_ratio(wizard_k),
                    "volume_ratio": self._calc_volume_ratio(wizard_k, klines[:wizard_idx]),
                },
                "confirm": {
                    "date": confirm_k["date"],
                    "change_pct": confirm_k["change_pct"],
                    "is_high_open": confirm_k["open"] > wizard_k["close"],
                    "is_positive": confirm_k["is_positive"],
                },
                "prev": {
                    "date": prev_k["date"] if prev_k else None,
                    "change_pct": prev_k["change_pct"] if prev_k else None,
                },
                "is_strong_stock": True,
                "recent_gain": round(recent_gain, 2),
            },
            "klines": klines[-5:],
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _is_strong_stock(self, klines: List[Dict]) -> bool:
        """
        检查是否是强势股：
        1. 近20日涨幅 > 15%
        2. 沿5日/10日均线上行
        """
        cfg = self.CONFIG
        if len(klines) < cfg["min_trend_days"]:
            return False
        
        # 计算近20日涨幅
        recent_gain = self._calc_recent_gain(klines, days=20)
        if recent_gain < cfg["strong_stock_min_gain"]:
            return False
        
        # 检查是否沿均线上行
        ma_short = self._calc_ma(klines, cfg["trend_ma_short"])
        ma_long = self._calc_ma(klines, cfg["trend_ma_long"])
        
        if ma_short <= 0 or ma_long <= 0:
            return False
        
        # 短期均线应在长期均线上方
        if ma_short < ma_long:
            return False
        
        # 最近收盘价应在均线上方
        current_close = klines[-1]["close"]
        if current_close < ma_long:
            return False
        
        return True
    
    def _check_prev_kline(self, prev_k: Dict, prev_klines: List[Dict]) -> bool:
        """检查前一日：放量大阳线"""
        cfg = self.CONFIG
        
        # 必须是阳线
        if not prev_k["is_positive"]:
            return False
        
        # 涨幅 ≥ 5%
        if prev_k["change_pct"] < cfg["prev_min_change"]:
            return False
        
        # 放量
        vol_ratio = self._calc_volume_ratio(prev_k, prev_klines)
        if vol_ratio < cfg["prev_min_volume_ratio"]:
            return False
        
        return True
    
    def _check_wizard_kline(self, wizard_k: Dict, prev_klines: List[Dict]) -> bool:
        """检查当日（仙人指路K线）条件"""
        cfg = self.CONFIG
        
        # 振幅 ≥ 7%
        amplitude = self._calc_amplitude(wizard_k)
        if amplitude < cfg["min_amplitude"]:
            return False
        
        # 涨跌幅在 -3% 到 3% 之间
        if wizard_k["change_pct"] > cfg["max_change_pct"]:
            return False
        if wizard_k["change_pct"] < cfg["min_change_pct"]:
            return False
        
        # 量比 ≥ 1
        vol_ratio = self._calc_volume_ratio(wizard_k, prev_klines)
        if vol_ratio < cfg["min_volume_ratio"]:
            return False
        
        # 检查K线形态：长上影 + 小实体 + 短下影
        total_range = wizard_k["high"] - wizard_k["low"]
        if total_range <= 0:
            return False
        
        body = wizard_k["body"]
        upper_shadow = wizard_k["upper_shadow"]
        lower_shadow = wizard_k["lower_shadow"]
        
        # 实体占比（小实体）
        body_ratio = body / total_range
        if body_ratio > cfg["max_body_ratio"]:
            return False
        
        # 上影线占比（长上影）
        upper_ratio = upper_shadow / total_range
        if upper_ratio < cfg["min_upper_shadow_ratio"]:
            return False
        
        # 下影线占比（短下影）
        lower_ratio = lower_shadow / total_range
        if lower_ratio > cfg["max_lower_shadow_ratio"]:
            return False
        
        # 上影线应长于下影线
        if upper_shadow < lower_shadow:
            return False
        
        return True
    
    def _check_confirm_kline(self, confirm_k: Dict, wizard_k: Dict) -> bool:
        """检查次日确认K线"""
        cfg = self.CONFIG
        
        # 必须是阳线
        if cfg["confirm_must_be_positive"] and not confirm_k["is_positive"]:
            return False
        
        # 涨幅 ≥ 1%
        if confirm_k["change_pct"] < cfg["confirm_min_change"]:
            return False
        
        # 最好高开（开盘价 > 仙人指路收盘价）
        # 但不强制，低开高走也可以
        
        return True
    
    def _calc_confidence(self, wizard_k: Dict, confirm_k: Dict, position: float) -> int:
        """计算置信度评分"""
        cfg = self.CONFIG
        score = 0
        
        # 上影线长度评分
        total_range = wizard_k["high"] - wizard_k["low"]
        if total_range > 0:
            upper_ratio = wizard_k["upper_shadow"] / total_range
            if upper_ratio >= 0.7:
                score += 2
            elif upper_ratio >= 0.5:
                score += 1
        
        # 收阳评分（收阳优于收阴）
        if wizard_k["is_positive"]:
            score += 2
        else:
            score += 1
        
        # 次日确认质量评分
        if confirm_k["open"] > wizard_k["close"]:
            # 高开高走
            score += 2
        elif confirm_k["is_positive"]:
            # 低开高走
            score += 1
        
        # 位置评分（低位优于高位）
        if position <= 20:
            score += 2
        elif position <= 35:
            score += 1
        
        return score
    
    def _calc_amplitude(self, kline: Dict) -> float:
        """计算振幅"""
        if kline["low"] <= 0:
            return 0
        return (kline["high"] - kline["low"]) / kline["low"] * 100
    
    def _calc_upper_shadow_ratio(self, kline: Dict) -> float:
        """计算上影线占比"""
        total_range = kline["high"] - kline["low"]
        if total_range <= 0:
            return 0
        return kline["upper_shadow"] / total_range
    
    def _calc_volume_ratio(self, kline: Dict, prev_klines: List[Dict]) -> float:
        """计算量比"""
        if not prev_klines or len(prev_klines) < 5:
            return 1.0
        avg_vol = sum(k["volume"] for k in prev_klines[-5:]) / 5
        if avg_vol == 0:
            return 1.0
        return kline["volume"] / avg_vol
    
    def _calc_recent_gain(self, klines: List[Dict], days: int) -> float:
        """计算近N日涨幅"""
        if len(klines) < days:
            return 0
        start_price = klines[-days]["open"]
        end_price = klines[-1]["close"]
        if start_price <= 0:
            return 0
        return (end_price - start_price) / start_price * 100
    
    def _calc_ma(self, klines: List[Dict], period: int) -> float:
        """计算均线"""
        if len(klines) < period:
            return 0
        return sum(k["close"] for k in klines[-period:]) / period


# ── 注册策略 ──
_strategy = WizardPointerStrategy()


def get_strategy():
    return _strategy
