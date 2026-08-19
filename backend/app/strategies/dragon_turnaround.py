"""
================================================================================
【文件作用】"龙回头"战法实现
================================================================================

核心逻辑：
  1. 龙头识别：板块涨幅前3 + 涨停次数最多
  2. 第一波检测：5-8天内涨幅≥30%，至少2-3个涨停
  3. 回调期监控：缩量回调20%-30%，3-7天内
  4. 启动点确认：止跌反包，放量阳线覆盖回调

与"进二退一"的关键区别：
  - 需要板块数据识别龙头（不是全A盲扫）
  - 需要多日跟踪（第一波→回调→启动）
  - 买点在于"止跌反包当日"，非回调最后一天
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class DragonTurnaroundStrategy(BaseStrategy):
    """龙回头战法"""
    
    name = "龙回头"
    name_en = "dragon_turnaround"
    description = "龙头股第一波拉升后缩量回调，在支撑位止跌反包时介入的二波战法。"
    
    # 可配置参数
    CONFIG = {
        # 第一波条件
        "wave1_days": (5, 8),           # 第一波天数范围
        "wave1_min_gain": 30.0,         # 第一波最小涨幅 %
        "wave1_max_gain": 70.0,         # 第一波最大涨幅（超过则透支）
        "wave1_min_limits": 2,          # 最少涨停数
        "wave1_best_limits": (3, 5),    # 最佳涨停数范围
        "wave1_max_limits": 7,          # 最大涨停数（超过则透支）
        "wave1_min_volume_ratio": 3.0,  # 第一波量能放大倍数
        
        # 回调期条件
        "pullback_days": (3, 7),        # 回调天数范围（黄金窗口）
        "pullback_max_days": 10,        # 最大回调天数（超过放弃）
        "pullback_min_depth": 0.15,     # 最小回调深度（首波涨幅的15%）
        "pullback_best_depth": (0.20, 0.30),  # 最佳回调深度
        "pullback_max_depth": 0.50,     # 最大回调深度（超过放弃）
        "pullback_max_volume_ratio": 0.5,  # 回调期量能缩至首波峰值的比例
        
        # 启动点条件
        "reversal_min_volume_ratio": 1.5,  # 反包日量能倍数（相对回调均量）
        
        # 支撑位
        "ma_support": 10,               # 均线支撑（10日线）
        "ma_tolerance": 0.03,           # 均线乖离容忍度 3%
        
        # 置信度阈值
        "high_confidence_score": 6,
        "medium_confidence_score": 4,
    }
    
    def scan(self, stock_pool: List[Dict]) -> List[Dict]:
        """扫描股票池，返回符合龙回头形态的信号"""
        signals = []
        
        for stock in stock_pool:
            code = stock["code"]
            try:
                signal = self._check_stock(code, stock)
                if signal:
                    signals.append(signal)
            except Exception as e:
                continue
        
        # 按置信度排序
        signals.sort(key=lambda x: x["confidence"], reverse=True)
        return signals
    
    def _check_stock(self, code: str, stock: Dict) -> Optional[Dict]:
        """检查单只股票是否符合龙回头形态"""
        cfg = self.CONFIG
        
        # 获取K线（需要足够长以检测第一波+回调）
        klines = get_kline_with_indicators(code, count=30)
        if len(klines) < 20:
            return None
        
        # ── 步骤1：检测第一波拉升 ──
        wave1 = self._detect_first_wave(klines)
        if not wave1:
            return None
        
        wave1_start_idx, wave1_end_idx, wave1_info = wave1
        
        # ── 步骤2：检测回调期 ──
        pullback = self._detect_pullback(klines, wave1_end_idx, wave1_info)
        if not pullback:
            return None
        
        pullback_end_idx, pullback_info = pullback
        
        # ── 步骤3：检测启动点（止跌反包）──
        reversal = self._detect_reversal(klines, pullback_end_idx, wave1_info, pullback_info)
        if not reversal:
            return None
        
        reversal_idx, reversal_info = reversal
        
        # ── 计算置信度 ──
        confidence = self._calc_confidence(wave1_info, pullback_info, reversal_info)
        
        # ── 计算关键价位 ──
        current = klines[-1]
        entry_price = current["close"]  # 介入价：当前价（反包日）
        
        # 止损：10日均线或回调低点下方3%，取较高者
        ma10 = self._calc_ma(klines, 10)
        pullback_low = pullback_info.get("low", entry_price * 0.9)
        stop_loss = max(ma10, pullback_low) * 0.97
        # 止损不能超过介入价的15%（A股跌停限制）
        max_stop_loss_pct = 0.15
        if stop_loss < entry_price * (1 - max_stop_loss_pct):
            stop_loss = entry_price * (1 - max_stop_loss_pct)
        
        # 目标：取“前高×0.98”和“介入价×1.15”的较大值
        # 确保目标价始终高于介入价（至少15%盈利空间）
        wave1_high = max(k["high"] for k in klines[wave1_start_idx:wave1_end_idx+1])
        target_price = max(wave1_high * 0.98, entry_price * 1.15)
        
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
                "wave1": wave1_info,
                "pullback": pullback_info,
                "reversal": reversal_info,
            },
            "klines": klines[-10:],
            "market_cap": stock.get("market_cap", 0),
            # 龙头标识（需要板块数据，暂时留空）
            "is_leader": None,
            "sector_rank": None,
        }
    
    def _detect_first_wave(self, klines: List[Dict]) -> Optional[Tuple[int, int, Dict]]:
        """
        检测第一波拉升。
        返回：(start_idx, end_idx, wave_info) 或 None
        """
        cfg = self.CONFIG
        min_days, max_days = cfg["wave1_days"]
        
        # 从最近往前找，寻找连续上涨的窗口
        for end_idx in range(len(klines) - 1, min_days, -1):
            # 尝试不同的起始位置
            for start_idx in range(max(end_idx - max_days - 5, 0), end_idx - min_days + 1):
                wave = klines[start_idx:end_idx + 1]
                if len(wave) < min_days:
                    continue
                
                # 计算涨幅
                start_price = wave[0]["open"]
                end_price = wave[-1]["close"]
                gain = (end_price - start_price) / start_price * 100
                
                if gain < cfg["wave1_min_gain"]:
                    continue
                if gain > cfg["wave1_max_gain"]:
                    continue
                
                # 计算涨停数
                limit_up_count = sum(1 for k in wave if k["change_pct"] >= 9.5)
                if limit_up_count < cfg["wave1_min_limits"]:
                    continue
                if limit_up_count > cfg["wave1_max_limits"]:
                    continue
                
                # 计算量能放大
                pre_wave = klines[max(0, start_idx - 5):start_idx]
                if not pre_wave:
                    continue
                pre_avg_vol = sum(k["volume"] for k in pre_wave) / len(pre_wave)
                wave_peak_vol = max(k["volume"] for k in wave)
                
                if pre_avg_vol == 0:
                    continue
                vol_ratio = wave_peak_vol / pre_avg_vol
                
                if vol_ratio < cfg["wave1_min_volume_ratio"]:
                    continue
                
                # 符合条件，返回第一波信息
                wave_info = {
                    "start_date": wave[0]["date"],
                    "end_date": wave[-1]["date"],
                    "days": len(wave),
                    "gain": round(gain, 2),
                    "limit_up_count": limit_up_count,
                    "peak_volume": wave_peak_vol,
                    "volume_ratio": round(vol_ratio, 2),
                    "high": max(k["high"] for k in wave),
                    "start_price": start_price,
                }
                
                return start_idx, end_idx, wave_info
        
        return None
    
    def _detect_pullback(self, klines: List[Dict], wave1_end_idx: int, wave1_info: Dict) -> Optional[Tuple[int, Dict]]:
        """
        检测回调期。
        返回：(pullback_end_idx, pullback_info) 或 None
        """
        cfg = self.CONFIG
        min_days, max_days = cfg["pullback_days"]
        
        # 从第一波结束开始，往后找回调
        pullback_start_idx = wave1_end_idx + 1
        if pullback_start_idx >= len(klines):
            return None
        
        # 找回调结束位置（最近几天）
        # 回调期应该是连续的缩量下跌或横盘
        remaining = klines[pullback_start_idx:]
        if len(remaining) < 2:
            return None
        
        # 计算回调深度
        wave1_high = wave1_info["high"]
        wave1_gain = wave1_info["gain"]
        
        # 找回调低点
        pullback_days = min(len(remaining), cfg["pullback_max_days"])
        pullback_klines = remaining[:pullback_days]
        
        if not pullback_klines:
            return None
        
        pullback_low = min(k["low"] for k in pullback_klines)
        pullback_depth = (wave1_high - pullback_low) / wave1_high * 100
        
        # 检查回调深度
        depth_ratio = pullback_depth / wave1_gain if wave1_gain > 0 else 0
        if depth_ratio < cfg["pullback_min_depth"]:
            return None
        if depth_ratio > cfg["pullback_max_depth"]:
            return None
        
        # 检查回调期量能
        pullback_avg_vol = sum(k["volume"] for k in pullback_klines) / len(pullback_klines)
        vol_ratio = pullback_avg_vol / wave1_info["peak_volume"] if wave1_info["peak_volume"] > 0 else 1
        
        if vol_ratio > cfg["pullback_max_volume_ratio"]:
            return None
        
        # 检查回调天数
        actual_days = len(pullback_klines)
        if actual_days < min_days:
            # 回调时间不足，但可能还在进行中
            # 暂时接受，但降低置信度
            pass
        
        # 找到回调结束位置（低点附近）
        pullback_end_idx = pullback_start_idx
        for i, k in enumerate(pullback_klines):
            if k["low"] == pullback_low:
                pullback_end_idx = pullback_start_idx + i
                break
        
        pullback_info = {
            "days": actual_days,
            "depth_pct": round(pullback_depth, 2),
            "depth_ratio": round(depth_ratio, 2),
            "avg_volume": pullback_avg_vol,
            "volume_ratio": round(vol_ratio, 2),
            "low": pullback_low,
            "is_golden_window": min_days <= actual_days <= max_days,
        }
        
        return pullback_end_idx, pullback_info
    
    def _detect_reversal(self, klines: List[Dict], pullback_end_idx: int, 
                         wave1_info: Dict, pullback_info: Dict) -> Optional[Tuple[int, Dict]]:
        """
        检测启动点（止跌反包）。
        返回：(reversal_idx, reversal_info) 或 None
        """
        cfg = self.CONFIG
        
        # 从回调结束位置往后找反包信号
        remaining = klines[pullback_end_idx:]
        if len(remaining) < 2:
            return None
        
        # 找最近的反包信号
        for i in range(len(remaining) - 1):
            today = remaining[i]
            yesterday = remaining[i - 1] if i > 0 else None
            
            # 检查是否是反包阳线
            if not today["is_positive"]:
                continue
            
            # 检查量能放大
            recent_vols = [k["volume"] for k in remaining[max(0, i-3):i]]
            if not recent_vols:
                continue
            avg_vol = sum(recent_vols) / len(recent_vols)
            if avg_vol == 0:
                continue
            
            vol_ratio = today["volume"] / avg_vol
            if vol_ratio < cfg["reversal_min_volume_ratio"]:
                continue
            
            # 检查是否覆盖前一日阴线
            if yesterday and not yesterday["is_positive"]:
                if today["close"] <= yesterday["open"]:
                    continue  # 没有覆盖
            
            # 检查是否在支撑位附近
            ma10 = self._calc_ma_at(klines, pullback_end_idx + i, cfg["ma_support"])
            if ma10 > 0:
                distance_to_ma = abs(today["close"] - ma10) / ma10
                if distance_to_ma > cfg["ma_tolerance"]:
                    # 距离均线太远，不是好的支撑位
                    pass  # 暂时接受，但可能不是最佳
            
            # 找到反包信号
            reversal_info = {
                "date": today["date"],
                "change_pct": today["change_pct"],
                "volume_ratio": round(vol_ratio, 2),
                "is_engulfing": yesterday and today["close"] > yesterday["open"] and not yesterday["is_positive"],
                "distance_to_ma10": round((today["close"] - ma10) / ma10 * 100, 2) if ma10 > 0 else None,
            }
            
            return pullback_end_idx + i, reversal_info
        
        return None
    
    def _calc_confidence(self, wave1_info: Dict, pullback_info: Dict, reversal_info: Dict) -> int:
        """计算置信度评分"""
        cfg = self.CONFIG
        score = 0
        
        # 涨停数量评分
        limit_count = wave1_info["limit_up_count"]
        best_min, best_max = cfg["wave1_best_limits"]
        if best_min <= limit_count <= best_max:
            score += 2
        elif limit_count == 2:
            score += 1
        
        # 回调深度评分
        depth_ratio = pullback_info["depth_ratio"]
        best_min, best_max = cfg["pullback_best_depth"]
        if best_min <= depth_ratio <= best_max:
            score += 2
        elif 0.15 <= depth_ratio <= 0.40:
            score += 1
        
        # 缩量程度评分
        vol_ratio = pullback_info["volume_ratio"]
        if vol_ratio <= 0.3:
            score += 2
        elif vol_ratio <= 0.5:
            score += 1
        
        # 止跌信号评分
        if reversal_info.get("is_engulfing"):
            score += 2
        elif reversal_info["change_pct"] > 0:
            score += 1
        
        return score
    
    def _calc_ma(self, klines: List[Dict], period: int) -> float:
        """计算均线"""
        if len(klines) < period:
            return 0
        return sum(k["close"] for k in klines[-period:]) / period
    
    def _calc_ma_at(self, klines: List[Dict], idx: int, period: int) -> float:
        """计算指定位置的均线"""
        if idx < period - 1:
            return 0
        return sum(k["close"] for k in klines[idx - period + 1:idx + 1]) / period


# ── 注册策略 ──
_strategy = DragonTurnaroundStrategy()


def get_strategy():
    return _strategy
