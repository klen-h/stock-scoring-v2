"""
================================================================================
【文件作用】"进二退一"战法实现
================================================================================

形态定义：
  三根K线组合：放量大阳 → 强攻上影 → 缩量回调
  低位出现为买点，放量回调为陷阱。

量化条件：
  Day 1（放量大阳）：涨幅 ≥ 5%，成交量 > 前5日均量 × 1.2
  Day 2（强攻上影）：涨幅 ≥ 6%，涨幅 ≤ Day1，上影线/实体 ≥ 0.1，继续放量
  Day 3（缩量回调）：涨幅 ≤ 1%，成交量 < 前两日均量 × 0.7，回调 < 3%，不破Day1高点

位置要求：
  60日区间位置 < 30%（低位）
  MA5 ≥ MA10 × 0.98（均线初现多头）

置信度评分：
  Day1涨幅：涨停+2，7-9.5%+1，5-7%+0
  Day2上影线：≥0.3+2，0.15-0.3+1，<0.15+0
  Day3缩量：≤50%+2，50-70%+1，70-80%+0
  位置：≤15%+2，15-25%+1，25-30%+0
  总分 ≥ 6高置信，4-5中置信，<4低置信
================================================================================
"""

from typing import List, Dict
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class Advance2Retreat1Strategy(BaseStrategy):
    """进二退一战法"""
    
    name = "进二退一"
    name_en = "advance2retreat1"
    description = "低位放量大阳后缩量回调的短线追涨战法。三根K线组合：放量大阳→强攻上影→缩量回调不破支撑。"
    
    # 可配置参数
    CONFIG = {
        # Day 1 条件
        "day1_min_change": 5.0,        # 最小涨幅 %
        "day1_min_volume_ratio": 1.2,  # 最小量比（相对前5日均量）
        
        # Day 2 条件
        "day2_min_change": 6.0,        # 最小涨幅 %
        "day2_max_upper_shadow_ratio": 0.1,  # 最小上影线/实体比
        
        # Day 3 条件
        "day3_max_change": 1.0,        # 最大涨幅 %（允许微涨）
        "day3_max_volume_ratio": 0.7,  # 最大量比（相对前两日均量）
        "day3_max_pullback": 3.0,      # 最大回调幅度 %
        
        # 位置条件
        "max_position_pct": 30.0,      # 60日区间最大位置 %
        "ma_alignment_threshold": 0.98,  # MA5/MA10 多头排列阈值
        
        # 置信度阈值
        "high_confidence_score": 6,
        "medium_confidence_score": 4,
    }
    
    def scan(self, stock_pool: List[Dict]) -> List[Dict]:
        """扫描股票池，返回符合进二退一形态的信号"""
        signals = []
        
        for stock in stock_pool:
            code = stock["code"]
            try:
                signal = self._check_stock(code, stock)
                if signal:
                    signals.append(signal)
            except Exception as e:
                # 单只股票失败不影响整体
                continue
        
        # 按置信度排序
        signals.sort(key=lambda x: x["confidence"], reverse=True)
        return signals
    
    def _check_stock(self, code: str, stock: Dict) -> Dict:
        """检查单只股票是否符合形态"""
        cfg = self.CONFIG
        
        # 获取K线（需要至少65根：60日回看 + 最近5根用于形态判断）
        klines = get_kline_with_indicators(code, count=65)
        if len(klines) < 63:
            return None
        
        # 取最近3根K线
        day1 = klines[-3]
        day2 = klines[-2]
        day3 = klines[-1]
        
        # ── Day 1 检查：放量大阳 ──
        if not self._check_day1(day1, klines[:-3]):
            return None
        
        # ── Day 2 检查：强攻上影 ──
        if not self._check_day2(day1, day2):
            return None
        
        # ── Day 3 检查：缩量回调 ──
        if not self._check_day3(day1, day2, day3):
            return None
        
        # ── 位置检查 ──
        position = calc_position_in_range(klines, lookback=60)
        if position > cfg["max_position_pct"]:
            return None
        
        # ── 均线检查 ──
        ma5 = self._calc_ma(klines, 5)
        ma10 = self._calc_ma(klines, 10)
        if ma5 < ma10 * cfg["ma_alignment_threshold"]:
            return None
        
        # ── 计算置信度 ──
        confidence = self._calc_confidence(day1, day2, day3, position)
        
        # ── 计算关键价位 ──
        entry_price = day3["close"]  # 介入价：Day3收盘价
        stop_loss = day1["low"] * 0.98  # 止损：Day1最低点 × 0.98
        # 止损不能超过介入价的10%
        if stop_loss < entry_price * 0.90:
            stop_loss = entry_price * 0.90
        # 目标：Day2最高价×1.05，但至少保证10%盈利空间
        target_price = max(day2["high"] * 1.05, entry_price * 1.10)
        
        # ── 构造信号 ──
        confidence_level = "low"
        if confidence >= cfg["high_confidence_score"]:
            confidence_level = "high"
        elif confidence >= cfg["medium_confidence_score"]:
            confidence_level = "medium"
        
        return {
            "code": code,
            "name": stock["name"],
            "signal_date": day3["date"],
            "confidence": confidence,
            "confidence_level": confidence_level,
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "target_price": round(target_price, 2),
            "position_pct": position,
            "details": {
                "day1": {
                    "date": day1["date"],
                    "change_pct": day1["change_pct"],
                    "volume_ratio": self._calc_vol_ratio(day1, klines[:-3]),
                },
                "day2": {
                    "date": day2["date"],
                    "change_pct": day2["change_pct"],
                    "upper_shadow_ratio": self._calc_upper_shadow_ratio(day2),
                },
                "day3": {
                    "date": day3["date"],
                    "change_pct": day3["change_pct"],
                    "volume_ratio": self._calc_vol_ratio(day3, [day1, day2]),
                    "pullback_pct": round((day2["close"] - day3["close"]) / day2["close"] * 100, 2),
                },
            },
            "klines": klines[-5:],  # 最近5根K线用于展示
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _check_day1(self, day1: Dict, prev_klines: List[Dict]) -> bool:
        """检查Day1：放量大阳"""
        cfg = self.CONFIG
        
        # 必须是阳线
        if not day1["is_positive"]:
            return False
        
        # 涨幅 ≥ 5%
        if day1["change_pct"] < cfg["day1_min_change"]:
            return False
        
        # 成交量 > 前5日均量 × 1.2
        if len(prev_klines) < 5:
            return False
        
        avg_vol = sum(k["volume"] for k in prev_klines[-5:]) / 5
        if avg_vol == 0:
            return False
        
        vol_ratio = day1["volume"] / avg_vol
        if vol_ratio < cfg["day1_min_volume_ratio"]:
            return False
        
        return True
    
    def _check_day2(self, day1: Dict, day2: Dict) -> bool:
        """检查Day2：强攻上影"""
        cfg = self.CONFIG
        
        # 必须是阳线
        if not day2["is_positive"]:
            return False
        
        # 涨幅 ≥ 6%
        if day2["change_pct"] < cfg["day2_min_change"]:
            return False
        
        # 涨幅 ≤ Day1涨幅（避免透支）
        if day2["change_pct"] > day1["change_pct"]:
            return False
        
        # 上影线/实体 ≥ 0.1
        upper_ratio = self._calc_upper_shadow_ratio(day2)
        if upper_ratio < cfg["day2_max_upper_shadow_ratio"]:
            return False
        
        # 继续放量（成交量 > Day1）
        if day2["volume"] <= day1["volume"]:
            return False
        
        return True
    
    def _check_day3(self, day1: Dict, day2: Dict, day3: Dict) -> bool:
        """检查Day3：缩量回调"""
        cfg = self.CONFIG
        
        # 涨幅 ≤ 1%（允许微涨或下跌）
        if day3["change_pct"] > cfg["day3_max_change"]:
            return False
        
        # 明显缩量：成交量 < 前两日均量 × 0.7
        avg_vol = (day1["volume"] + day2["volume"]) / 2
        if avg_vol == 0:
            return False
        
        vol_ratio = day3["volume"] / avg_vol
        if vol_ratio >= cfg["day3_max_volume_ratio"]:
            return False
        
        # 回调幅度 < 3%（相对Day2收盘价）
        pullback = (day2["close"] - day3["close"]) / day2["close"] * 100
        if pullback > cfg["day3_max_pullback"]:
            return False
        
        # 收盘不破Day1高点
        if day3["close"] < day1["high"]:
            # 允许小幅跌破（容差1%）
            if day3["close"] < day1["high"] * 0.99:
                return False
        
        return True
    
    def _calc_confidence(self, day1: Dict, day2: Dict, day3: Dict, position: float) -> int:
        """计算置信度评分"""
        score = 0
        
        # Day1涨幅评分
        if day1["change_pct"] >= 9.5:  # 接近涨停
            score += 2
        elif day1["change_pct"] >= 7:
            score += 1
        
        # Day2上影线评分
        upper_ratio = self._calc_upper_shadow_ratio(day2)
        if upper_ratio >= 0.3:
            score += 2
        elif upper_ratio >= 0.15:
            score += 1
        
        # Day3缩量评分
        avg_vol = (day1["volume"] + day2["volume"]) / 2
        if avg_vol > 0:
            vol_ratio = day3["volume"] / avg_vol
            if vol_ratio <= 0.5:
                score += 2
            elif vol_ratio <= 0.7:
                score += 1
        
        # 位置评分
        if position <= 15:
            score += 2
        elif position <= 25:
            score += 1
        
        return score
    
    def _calc_upper_shadow_ratio(self, kline: Dict) -> float:
        """计算上影线/实体比"""
        body = kline["body"]
        if body == 0:
            return 0
        return kline["upper_shadow"] / body
    
    def _calc_vol_ratio(self, kline: Dict, prev_klines: List[Dict]) -> float:
        """计算量比"""
        if not prev_klines:
            return 1.0
        avg_vol = sum(k["volume"] for k in prev_klines) / len(prev_klines)
        if avg_vol == 0:
            return 1.0
        return round(kline["volume"] / avg_vol, 2)
    
    def _calc_ma(self, klines: List[Dict], period: int) -> float:
        """计算均线"""
        if len(klines) < period:
            return 0
        return sum(k["close"] for k in klines[-period:]) / period
    
    def detect_signal(self, klines: List[Dict], idx: int) -> Dict:
        """
        检测在指定索引位置是否触发进二退一信号（用于回测）。
        
        检查 klines[idx-2], klines[idx-1], klines[idx] 是否构成进二退一形态。
        """
        if idx < 5:
            return None
        
        cfg = self.CONFIG
        
        # 取 idx 位置及其前两根K线
        day1 = klines[idx - 2]
        day2 = klines[idx - 1]
        day3 = klines[idx]
        
        # 获取前5根K线（用于计算均量）
        prev_klines = klines[max(0, idx - 7):idx - 2]
        
        # ── Day 1 检查：放量大阳 ──
        if not day1.get("is_positive", day1["close"] > day1["open"]):
            return None
        if day1["change_pct"] < cfg["day1_min_change"]:
            return None
        
        if len(prev_klines) < 5:
            return None
        avg_vol = sum(k["volume"] for k in prev_klines[-5:]) / 5
        if avg_vol == 0:
            return None
        vol_ratio = day1["volume"] / avg_vol
        if vol_ratio < cfg["day1_min_volume_ratio"]:
            return None
        
        # ── Day 2 检查：强攻上影 ──
        if not day2.get("is_positive", day2["close"] > day2["open"]):
            return None
        if day2["change_pct"] < cfg["day2_min_change"]:
            return None
        if day2["change_pct"] > day1["change_pct"]:
            return None
        
        body = abs(day2["close"] - day2["open"])
        if body == 0:
            return None
        upper_shadow = day2["high"] - max(day2["close"], day2["open"])
        upper_ratio = upper_shadow / body
        if upper_ratio < cfg["day2_max_upper_shadow_ratio"]:
            return None
        if day2["volume"] <= day1["volume"]:
            return None
        
        # ── Day 3 检查：缩量回调 ──
        if day3["change_pct"] > cfg["day3_max_change"]:
            return None
        
        avg_vol_12 = (day1["volume"] + day2["volume"]) / 2
        if avg_vol_12 == 0:
            return None
        vol_ratio_3 = day3["volume"] / avg_vol_12
        if vol_ratio_3 >= cfg["day3_max_volume_ratio"]:
            return None
        
        pullback = (day2["close"] - day3["close"]) / day2["close"] * 100
        if pullback > cfg["day3_max_pullback"]:
            return None
        
        if day3["close"] < day1["high"] * 0.99:
            return None
        
        # ── 计算关键价位 ──
        entry_price = day3["close"]
        stop_loss = day1["low"] * 0.98
        if stop_loss < entry_price * 0.90:
            stop_loss = entry_price * 0.90
        target_price = max(day2["high"] * 1.05, entry_price * 1.10)
        
        return {
            "entry_price": entry_price,
            "stop_loss": stop_loss,
            "target_price": target_price,
        }


# ── 注册策略 ──
_strategy = Advance2Retreat1Strategy()


def get_strategy():
    return _strategy
