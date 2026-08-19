"""
================================================================================
【文件作用】"单阳不破"战法实现
================================================================================

核心逻辑：
  一根放量大阳线 → 后续3-13根K线横盘不破其低点 → 放量突破启动

与连板战法的关键区别：
  - 不需要涨停，一根5%+大阳线即可
  - 允许横盘代替回调，时间窗口更宽（3-13天）
  - 适合震荡市/结构性行情

形态：
  Day 0：标志性大阳线（涨幅≥5%，放量1.5倍+）
  Day 1~N：横盘洗盘（3-8根最佳，不破大阳线低点，缩量）
  启动日：放量突破整理区间上沿
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class SingleYangUnbrokenStrategy(BaseStrategy):
    """单阳不破战法"""
    
    name = "单阳不破"
    name_en = "single_yang_unbroken"
    description = "一根放量大阳后横盘3-8天不破其低点，缩量至地量后放量突破时介入。震荡市首选战法。"
    
    CONFIG = {
        # ── 单阳日条件 ──
        "min_change": 5.0,              # 最小涨幅 %
        "strong_change": 7.0,           # 强势型涨幅
        "min_volume_ratio": 1.5,        # 最小量比（相对前5日均量）
        "min_body_ratio": 0.7,          # 实体占振幅最小比例（上下影线短）
        
        # ── 整理期条件 ──
        "consolidation_min_days": 3,    # 最小整理天数
        "consolidation_best_min": 3,    # 最佳整理天数下限
        "consolidation_best_max": 8,    # 最佳整理天数上限
        "consolidation_max_days": 13,   # 最大整理天数（超过放弃）
        "consolidation_max_vol_ratio": 0.5,  # 整理末期均量 < 单阳量 × 此值
        "min_positive_ratio": 0.5,      # 整理期阳线占比下限（阳多阴少）
        
        # ── 启动日条件 ──
        "breakout_min_volume_ratio": 2.0,  # 突破日量比（相对前日）
        "breakout_min_change": 2.0,        # 突破日最小涨幅
        
        # ── 止损 ──
        "stop_loss_buffer": 0.03,       # 大阳线最低价下方3%
        
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
        if len(klines) < 20:
            return None
        
        # ── 找单阳日 ──
        yang = self._find_single_yang(klines)
        if not yang:
            return None
        yang_idx, yang_info = yang
        
        # ── 检测整理期 ──
        consolidation = self._detect_consolidation(klines, yang_idx, yang_info)
        if not consolidation:
            return None
        consol_end_idx, consol_info = consolidation
        
        # ── 检测启动突破 ──
        breakout = self._detect_breakout(klines, consol_end_idx, consol_info)
        if not breakout:
            return None
        breakout_idx, breakout_info = breakout
        
        # ── 置信度 ──
        confidence = self._calc_confidence(yang_info, consol_info, breakout_info)
        
        current = klines[-1]
        entry_price = current["close"]
        stop_loss = yang_info["yang_low"] * (1 - cfg["stop_loss_buffer"])
        # 止损不能超过介入价的15%
        if stop_loss < entry_price * 0.85:
            stop_loss = entry_price * 0.85
        # 目标：单阳高度的一倍量度，至少10%盈利空间
        yang_height = yang_info["yang_close"] - yang_info["yang_low"]
        target_price = max(entry_price + yang_height, entry_price * 1.10)
        
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
                "single_yang": yang_info,
                "consolidation": consol_info,
                "breakout": breakout_info,
                "strength_type": consol_info.get("strength_type", "中势型"),
            },
            "klines": klines[-10:],
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _find_single_yang(self, klines: List[Dict]) -> Optional[Tuple[int, Dict]]:
        """找最近一根符合条件的标志性大阳线"""
        cfg = self.CONFIG
        # 从倒数第5根往前找（给整理期+突破留空间）
        for i in range(len(klines) - 5, max(len(klines) - 25, 0), -1):
            k = klines[i]
            if not k["is_positive"]:
                continue
            if k["change_pct"] < cfg["min_change"]:
                continue
            
            # 量比检查
            pre_klines = klines[max(0, i-5):i]
            if len(pre_klines) < 3:
                continue
            avg_vol = sum(pk["volume"] for pk in pre_klines) / len(pre_klines)
            if avg_vol <= 0 or k["volume"] / avg_vol < cfg["min_volume_ratio"]:
                continue
            
            # 实体占比（影线短）
            amplitude = k["high"] - k["low"]
            if amplitude <= 0:
                continue
            body_ratio = k["body"] / amplitude
            if body_ratio < cfg["min_body_ratio"]:
                continue
            
            # 判断强度类型
            if k["change_pct"] >= cfg["strong_change"]:
                strength = "强势型"
            else:
                strength = "中势型"
            
            yang_info = {
                "date": k["date"],
                "yang_open": k["open"],
                "yang_close": k["close"],
                "yang_high": k["high"],
                "yang_low": k["low"],
                "change_pct": k["change_pct"],
                "volume_ratio": round(k["volume"] / avg_vol, 2),
                "strength_type": strength,
            }
            return i, yang_info
        
        return None
    
    def _detect_consolidation(self, klines: List[Dict], yang_idx: int, yang_info: Dict) -> Optional[Tuple[int, Dict]]:
        """检测整理期：横盘不破大阳线低点"""
        cfg = self.CONFIG
        remaining = klines[yang_idx + 1:]
        if len(remaining) < cfg["consolidation_min_days"]:
            return None
        
        yang_low = yang_info["yang_low"]
        yang_close = yang_info["yang_close"]
        yang_mid = (yang_info["yang_open"] + yang_close) / 2
        
        consol_klines = []
        consol_low = float("inf")
        consol_high = 0
        positive_count = 0
        volumes = []
        
        for i, k in enumerate(remaining):
            if i >= cfg["consolidation_max_days"] + 2:
                break
            
            # 检查是否出现突破（大涨幅阳线，说明启动）
            if k["change_pct"] >= cfg["breakout_min_change"] and k["is_positive"]:
                break
            
            # 核心条件：不破大阳线最低价
            if k["low"] < yang_low:
                return None  # 形态破坏
            
            consol_klines.append(k)
            consol_low = min(consol_low, k["low"])
            consol_high = max(consol_high, k["high"])
            volumes.append(k["volume"])
            if k["is_positive"]:
                positive_count += 1
        
        days = len(consol_klines)
        if days < cfg["consolidation_min_days"]:
            return None
        if days > cfg["consolidation_max_days"]:
            return None
        
        # 阳多阴少
        if positive_count / days < cfg["min_positive_ratio"]:
            return None
        
        # 缩量检查：后期均量 < 单阳量 × 50%
        yang_vol = yang_info.get("yang_close", 0)  # placeholder
        first_vol = klines[yang_idx]["volume"]
        if days >= 3:
            late_avg_vol = sum(volumes[-3:]) / 3
            vol_ratio = late_avg_vol / first_vol if first_vol > 0 else 1
        else:
            vol_ratio = 0.5  # 天数少时假设满足
        
        # 判断整理位置（强势/中势/弱势）
        if consol_low >= yang_mid:
            strength_type = "强势型"
        elif consol_low >= yang_info["yang_open"]:
            strength_type = "中势型"
        else:
            strength_type = "弱势型"
        
        consol_info = {
            "days": days,
            "low": round(consol_low, 2),
            "high": round(consol_high, 2),
            "volume_ratio": round(vol_ratio, 2),
            "positive_ratio": round(positive_count / days, 2),
            "strength_type": strength_type,
            "is_golden_window": cfg["consolidation_best_min"] <= days <= cfg["consolidation_best_max"],
        }
        
        consol_end_idx = yang_idx + 1 + days - 1
        return consol_end_idx, consol_info
    
    def _detect_breakout(self, klines: List[Dict], consol_end_idx: int, consol_info: Dict) -> Optional[Tuple[int, Dict]]:
        """检测启动突破日"""
        cfg = self.CONFIG
        remaining = klines[consol_end_idx + 1:]
        if not remaining:
            return None
        
        consol_high = consol_info["high"]
        
        for i, k in enumerate(remaining):
            if not k["is_positive"]:
                continue
            
            # 涨幅检查
            if k["change_pct"] < cfg["breakout_min_change"]:
                continue
            
            # 放量检查
            if i > 0:
                prev_vol = remaining[i-1]["volume"]
                if prev_vol > 0 and k["volume"] / prev_vol >= cfg["breakout_min_volume_ratio"]:
                    pass
                else:
                    continue
            else:
                pass  # 第一天无前日对比，假设满足
            
            # 突破整理区间上沿
            is_breakout = k["close"] > consol_high
            
            breakout_info = {
                "date": k["date"],
                "change_pct": k["change_pct"],
                "volume_ratio": round(k["volume"] / remaining[i-1]["volume"], 2) if i > 0 and remaining[i-1]["volume"] > 0 else 2.0,
                "is_breakout": is_breakout,
            }
            return consol_end_idx + 1 + i, breakout_info
        
        return None
    
    def _calc_confidence(self, yang_info: Dict, consol_info: Dict, breakout_info: Dict) -> int:
        score = 0
        
        # 整理天数评分
        if consol_info["is_golden_window"]:
            score += 2
        elif consol_info["days"] <= 8:
            score += 1
        
        # 缩量程度评分
        if consol_info["volume_ratio"] <= 0.3:
            score += 2
        elif consol_info["volume_ratio"] <= 0.5:
            score += 1
        
        # 强度类型评分
        if consol_info["strength_type"] == "强势型":
            score += 2
        elif consol_info["strength_type"] == "中势型":
            score += 1
        
        return score


_strategy = SingleYangUnbrokenStrategy()

def get_strategy():
    return _strategy
