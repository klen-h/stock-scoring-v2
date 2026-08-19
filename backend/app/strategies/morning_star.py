"""
================================================================================
【文件作用】"早晨之星"战法实现
================================================================================

核心逻辑：
  下跌末期三根K线组合：长阴线 → 跳空十字星 → 放量中阳线
  代表空方力量衰竭，多方反攻。适合左侧抄底。

形态：
  第一根：跌幅≥3%的长阴线（恐慌盘涌出）
  第二根：跳空低开的十字星/小阴阳（振幅<2%，缩量）
  第三根：放量中阳线（涨幅≥3%，覆盖第一根阴线50%+）
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class MorningStarStrategy(BaseStrategy):
    """早晨之星战法"""
    
    name = "早晨之星"
    name_en = "morning_star"
    description = "下跌末期长阴+跳空十字星+放量中阳的三根K线组合。空方衰竭信号，适合左侧抄底。"
    
    CONFIG = {
        # ── 第一根：长阴线 ──
        "min_drop_pct": -3.0,           # 最小跌幅 %
        "min_drop_strong": -5.0,        # 强势型跌幅
        "min_volume_drop": 1.0,         # 下跌时最小量比（有恐慌盘）
        
        # ── 第二根：星线 ──
        "star_max_amplitude": 2.0,      # 星线最大振幅 %
        "star_max_body": 1.0,           # 星线最大实体 %（占股价）
        "star_must_gap_down": True,     # 是否必须跳空低开
        "star_min_vol_shrink": 0.7,     # 星线量 < 前日量 × 此值（缩量）
        
        # ── 第三根：确认阳线 ──
        "confirm_min_change": 3.0,      # 确认阳线最小涨幅
        "confirm_min_coverage": 0.5,    # 最小覆盖第一根阴线比例
        "confirm_min_volume_ratio": 1.5,  # 确认阳线最小量比（相对星线）
        
        # ── 位置要求 ──
        "max_position_pct": 30.0,       # 最大位置（低位有效）
        "prior_decline_days": 10,       # 前期下跌天数
        "prior_min_decline": -15.0,     # 前期最小跌幅（需要下跌末期）
        
        # ── 止损 ──
        "stop_loss_buffer": 0.03,       # 星线最低点下方3%
        
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
        klines = get_kline_with_indicators(code, count=30)
        if len(klines) < 15:
            return None
        
        # ── 位置过滤（低位有效） ──
        position = calc_position_in_range(klines, lookback=60)
        if position > cfg["max_position_pct"]:
            return None
        
        # ── 检查前期是否有下跌 ──
        if not self._check_prior_decline(klines):
            return None
        
        # ── 检查三根K线组合 ──
        pattern = self._detect_morning_star(klines)
        if not pattern:
            return None
        
        day1_info, day2_info, day3_info = pattern
        
        # ── 置信度 ──
        confidence = self._calc_confidence(day1_info, day2_info, day3_info, position)
        
        current = klines[-1]
        entry_price = current["close"]
        stop_loss = day2_info["star_low"] * (1 - cfg["stop_loss_buffer"])
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
                "day1": day1_info,
                "day2": day2_info,
                "day3": day3_info,
            },
            "klines": klines[-5:],
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _check_prior_decline(self, klines: List[Dict]) -> bool:
        """检查前期是否有下跌"""
        cfg = self.CONFIG
        if len(klines) < cfg["prior_decline_days"] + 3:
            return False
        
        # 检查最近3根K线之前是否有下跌
        prior_klines = klines[-(cfg["prior_decline_days"]+3):-3]
        if len(prior_klines) < 5:
            return False
        
        start_price = prior_klines[0]["open"]
        end_price = prior_klines[-1]["close"]
        if start_price <= 0:
            return False
        
        decline = (end_price - start_price) / start_price * 100
        return decline <= cfg["prior_min_decline"]
    
    def _detect_morning_star(self, klines: List[Dict]) -> Optional[Tuple[Dict, Dict, Dict]]:
        """检测早晨之星三根K线组合"""
        cfg = self.CONFIG
        
        if len(klines) < 3:
            return None
        
        # 检查最近三根K线
        day1 = klines[-3]
        day2 = klines[-2]
        day3 = klines[-1]
        
        # ── 第一根：长阴线 ──
        if day1["is_positive"]:
            return None
        if day1["change_pct"] > cfg["min_drop_pct"]:
            return None
        
        # 第一根放量（恐慌盘）
        pre_klines = klines[:-3]
        if len(pre_klines) >= 5:
            avg_pre_vol = sum(k["volume"] for k in pre_klines[-5:]) / 5
            if avg_pre_vol > 0 and day1["volume"] / avg_pre_vol < cfg["min_volume_drop"]:
                return None
        
        day1_info = {
            "date": day1["date"],
            "change_pct": day1["change_pct"],
            "close": day1["close"],
            "open": day1["open"],
            "low": day1["low"],
        }
        
        # ── 第二根：星线 ──
        # 振幅检查
        if day2["low"] > 0:
            amplitude = (day2["high"] - day2["low"]) / day2["low"] * 100
        else:
            return None
        if amplitude > cfg["star_max_amplitude"]:
            return None
        
        # 实体检查
        if day2["low"] > 0:
            body_pct = day2["body"] / day2["low"] * 100
        else:
            return None
        if body_pct > cfg["star_max_body"]:
            return None
        
        # 跳空低开检查
        if cfg["star_must_gap_down"]:
            if day2["open"] >= day1["close"]:
                return None  # 没有跳空
        
        # 缩量检查
        if day1["volume"] > 0:
            vol_shrink = day2["volume"] / day1["volume"]
            if vol_shrink > cfg["star_min_vol_shrink"]:
                return None
        
        day2_info = {
            "date": day2["date"],
            "amplitude": round(amplitude, 2),
            "body_pct": round(body_pct, 2),
            "star_low": day2["low"],
            "star_high": day2["high"],
            "is_gap_down": day2["open"] < day1["close"],
            "vol_shrink": round(day2["volume"] / day1["volume"], 2) if day1["volume"] > 0 else 1,
        }
        
        # ── 第三根：确认阳线 ──
        if not day3["is_positive"]:
            return None
        if day3["change_pct"] < cfg["confirm_min_change"]:
            return None
        
        # 覆盖第一根阴线
        day1_body = day1["open"] - day1["close"]  # 阴线实体
        if day1_body > 0:
            day3_coverage = (day3["close"] - day1["close"]) / day1_body
            if day3_coverage < cfg["confirm_min_coverage"]:
                return None
        else:
            day3_coverage = 1
        
        # 放量检查
        if day2["volume"] > 0:
            vol_ratio = day3["volume"] / day2["volume"]
            if vol_ratio < cfg["confirm_min_volume_ratio"]:
                return None
        else:
            vol_ratio = 2
        
        day3_info = {
            "date": day3["date"],
            "change_pct": day3["change_pct"],
            "coverage": round(min(day3_coverage, 1), 2),
            "volume_ratio": round(vol_ratio, 2),
        }
        
        return day1_info, day2_info, day3_info
    
    def _calc_confidence(self, day1_info: Dict, day2_info: Dict, day3_info: Dict, position: float) -> int:
        score = 0
        
        # 第一根跌幅评分
        if day1_info["change_pct"] <= -5:
            score += 2
        elif day1_info["change_pct"] <= -3:
            score += 1
        
        # 星线质量评分（振幅越小越好）
        if day2_info["amplitude"] <= 1:
            score += 2
        elif day2_info["amplitude"] <= 2:
            score += 1
        
        # 第三根覆盖程度评分
        if day3_info["coverage"] >= 0.8:
            score += 2
        elif day3_info["coverage"] >= 0.5:
            score += 1
        
        return score


_strategy = MorningStarStrategy()

def get_strategy():
    return _strategy
