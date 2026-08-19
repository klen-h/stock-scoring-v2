"""
================================================================================
【文件作用】"涨停回马枪"（N字战法）实现
================================================================================

核心逻辑：
  涨停突破平台 → 缩量回调不破涨停实体 → 放量反包/再涨停

与"进二退一"和"龙回头"的区别：
  - 不要求是龙头，范围更广
  - 位置必须低（100日涨幅<100%）
  - 核心是"突破"逻辑，不是"追强"逻辑
  - 买点在于B末端反包时，不必等到涨停

形态：
  A段（左侧）：涨停突破平台，1-3个涨停板
  B段（中间）：缩量回调1-13天，不破涨停实体底部
  C段（右侧）：放量反包阳线或再涨停
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class LimitUpBoomerangStrategy(BaseStrategy):
    """涨停回马枪（N字战法）"""
    
    name = "涨停回马枪"
    name_en = "limit_up_boomerang"
    description = "涨停突破平台后缩量回调，不破涨停实体底部，放量反包时介入的N字形态战法。"
    
    CONFIG = {
        # ── 位置过滤 ──
        "max_100d_gain": 100.0,       # 近100日最大涨幅 %（超过排除）
        "max_100d_amplitude": 150.0,  # 近100日最大振幅 %（超过排除）
        
        # ── A段：涨停突破 ──
        "lookback_for_limit": 10,     # 回看多少天内出现过涨停
        "min_limit_ups": 1,           # 最少涨停数
        "max_limit_ups": 3,           # 最多涨停数（超过放弃）
        "consolidation_days": 80,     # 横盘整理期天数（W区间）
        "consolidation_max_gain": 100.0,  # 横盘期最大涨幅
        "consolidation_max_amplitude": 100.0,  # 横盘期最大振幅
        
        # ── B段：缩量回调 ──
        "pullback_min_days": 1,       # 最小回调天数
        "pullback_best_days": (3, 8), # 最佳回调天数
        "pullback_max_days": 13,      # 最大回调天数（超过形态失效）
        "pullback_strong_support": "close",   # 最强支撑：涨停收盘价
        "pullback_medium_support": "midpoint", # 中等支撑：涨停腰线
        "pullback_weak_support": "open",       # 最弱支撑：涨停开盘价
        
        # ── C段：反包启动 ──
        "reversal_min_volume_ratio": 2.0,  # 反包日量能倍数（相对前日）
        
        # ── 止损 ──
        "stop_loss_key": "open",      # 止损位：涨停开盘价（最后防线）
        "stop_loss_buffer": 0.02,     # 止损缓冲 2%
        
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
        klines = get_kline_with_indicators(code, count=100)
        if len(klines) < 30:
            return None
        
        # ── 位置过滤 ──
        if not self._check_position(klines):
            return None
        
        # ── A段：检测涨停突破 ──
        limit_up = self._detect_limit_up_breakout(klines)
        if not limit_up:
            return None
        
        limit_idx, limit_info = limit_up
        
        # ── B段：检测缩量回调 ──
        pullback = self._detect_pullback(klines, limit_idx, limit_info)
        if not pullback:
            return None
        
        pullback_end_idx, pullback_info = pullback
        
        # ── C段：检测反包启动 ──
        reversal = self._detect_reversal(klines, pullback_end_idx, pullback_info)
        if not reversal:
            return None
        
        reversal_idx, reversal_info = reversal
        
        # ── 计算置信度 ──
        confidence = self._calc_confidence(limit_info, pullback_info, reversal_info)
        
        # ── 关键价位 ──
        current = klines[-1]
        entry_price = current["close"]
        
        # 止损：涨停开盘价下方2%
        stop_loss = limit_info["limit_open"] * (1 - cfg["stop_loss_buffer"])
        
        # 目标：涨幅20%
        target_price = entry_price * 1.20
        
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
                "limit_up": limit_info,
                "pullback": pullback_info,
                "reversal": reversal_info,
                # 三条关键支撑线
                "support_levels": {
                    "strong": round(limit_info["limit_close"], 2),    # 涨停收盘价
                    "medium": round(limit_info["limit_midpoint"], 2), # 涨停腰线
                    "weak": round(limit_info["limit_open"], 2),       # 涨停开盘价
                },
            },
            "klines": klines[-10:],
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _check_position(self, klines: List[Dict]) -> bool:
        """位置过滤：近100日涨幅<100%，振幅<150%"""
        cfg = self.CONFIG
        if len(klines) < 20:
            return False
        
        recent = klines[-min(100, len(klines)):]
        start_price = recent[0]["open"]
        if start_price <= 0:
            return False
        
        # 计算涨幅
        end_price = recent[-1]["close"]
        gain = (end_price - start_price) / start_price * 100
        if gain >= cfg["max_100d_gain"]:
            return False
        
        # 计算振幅
        high = max(k["high"] for k in recent)
        low = min(k["low"] for k in recent)
        if low <= 0:
            return False
        amplitude = (high - low) / low * 100
        if amplitude >= cfg["max_100d_amplitude"]:
            return False
        
        return True
    
    def _detect_limit_up_breakout(self, klines: List[Dict]) -> Optional[Tuple[int, Dict]]:
        """
        检测A段：涨停突破。
        在近10日内找到涨停板，且涨停前最好有横盘整理。
        """
        cfg = self.CONFIG
        lookback = cfg["lookback_for_limit"]
        
        # 从最近往前找涨停板
        for i in range(len(klines) - 1, max(len(klines) - lookback - 5, 0), -1):
            k = klines[i]
            
            # 检查是否涨停（涨幅≥9.5%）
            if k["change_pct"] < 9.5:
                continue
            
            # 检查涨停数（连续涨停算一组）
            limit_ups = [k]
            for j in range(i - 1, max(i - 5, 0), -1):
                if klines[j]["change_pct"] >= 9.5:
                    limit_ups.insert(0, klines[j])
                else:
                    break
            
            limit_count = len(limit_ups)
            if limit_count < cfg["min_limit_ups"]:
                continue
            if limit_count > cfg["max_limit_ups"]:
                continue
            
            # 第一个涨停板的信息
            first_limit = limit_ups[0]
            last_limit = limit_ups[-1]
            limit_idx = klines.index(first_limit)
            
            # 检查横盘整理期（W区间）
            w_start = max(0, limit_idx - cfg["consolidation_days"])
            w_klines = klines[w_start:limit_idx]
            
            if len(w_klines) < 10:
                continue  # 横盘期太短
            
            w_high = max(k["high"] for k in w_klines)
            w_low = min(k["low"] for k in w_klines)
            w_start_price = w_klines[0]["open"]
            
            if w_start_price <= 0:
                continue
            
            w_gain = (w_high - w_start_price) / w_start_price * 100
            w_amplitude = (w_high - w_low) / w_low * 100 if w_low > 0 else 0
            
            # 横盘期涨幅和振幅不能太大
            if w_gain >= cfg["consolidation_max_gain"]:
                continue
            if w_amplitude >= cfg["consolidation_max_amplitude"]:
                continue
            
            # 涨停时放量
            pre_limit_vol = sum(k["volume"] for k in w_klines[-5:]) / 5 if len(w_klines) >= 5 else 0
            if pre_limit_vol > 0 and first_limit["volume"] < pre_limit_vol * 1.5:
                continue  # 涨停时没有明显放量
            
            # 涨停板关键价位
            limit_open = first_limit["open"]
            limit_close = last_limit["close"]
            limit_high = max(k["high"] for k in limit_ups)
            limit_low = min(k["low"] for k in limit_ups)
            limit_midpoint = (limit_open + limit_close) / 2  # 腰线
            
            limit_info = {
                "date": first_limit["date"],
                "end_date": last_limit["date"],
                "count": limit_count,
                "limit_open": limit_open,
                "limit_close": limit_close,
                "limit_high": limit_high,
                "limit_low": limit_low,
                "limit_midpoint": limit_midpoint,
                "consolidation_gain": round(w_gain, 2),
                "consolidation_amplitude": round(w_amplitude, 2),
            }
            
            return limit_idx, limit_info
        
        return None
    
    def _detect_pullback(self, klines: List[Dict], limit_idx: int, limit_info: Dict) -> Optional[Tuple[int, Dict]]:
        """
        检测B段：缩量回调。
        回调不破涨停实体底部（开盘价），量能萎缩。
        """
        cfg = self.CONFIG
        
        # 从涨停后第一天开始
        pullback_start = limit_idx + 1
        remaining = klines[pullback_start:]
        
        if len(remaining) < cfg["pullback_min_days"]:
            return None
        
        # 涨停收盘价作为最强支撑参考
        support_close = limit_info["limit_close"]
        support_open = limit_info["limit_open"]  # 最后防线
        
        pullback_days = 0
        pullback_low = float("inf")
        pullback_klines = []
        
        for i, k in enumerate(remaining):
            if i >= cfg["pullback_max_days"]:
                break
            
            # 检查是否跌破最后防线（涨停开盘价）
            if k["close"] < support_open * 0.98:  # 允许2%容差
                break  # 形态破坏
            
            pullback_klines.append(k)
            pullback_days += 1
            pullback_low = min(pullback_low, k["low"])
        
        if pullback_days < cfg["pullback_min_days"]:
            return None
        
        # 检查缩量：回调期成交量应低于涨停后首日
        ref_volume = remaining[0]["volume"] if remaining else 0
        if ref_volume == 0:
            return None
        
        avg_pullback_vol = sum(k["volume"] for k in pullback_klines) / len(pullback_klines)
        vol_ratio = avg_pullback_vol / ref_volume
        
        # 回调期应该有缩量
        # （不严格要求所有日都缩，但平均要缩）
        
        # 判断支撑强度
        if pullback_low >= support_close:
            support_level = "strong"  # 不破涨停收盘价
        elif pullback_low >= limit_info["limit_midpoint"]:
            support_level = "medium"  # 不破腰线
        elif pullback_low >= support_open:
            support_level = "weak"    # 不破开盘价
        else:
            return None  # 跌破开盘价，形态破坏
        
        # 回调幅度
        pullback_depth = (limit_info["limit_close"] - pullback_low) / limit_info["limit_close"] * 100
        
        pullback_end_idx = pullback_start + len(pullback_klines) - 1
        
        pullback_info = {
            "days": pullback_days,
            "low": round(pullback_low, 2),
            "depth_pct": round(pullback_depth, 2),
            "volume_ratio": round(vol_ratio, 2),
            "support_level": support_level,
            "is_golden_window": cfg["pullback_best_days"][0] <= pullback_days <= cfg["pullback_best_days"][1],
        }
        
        return pullback_end_idx, pullback_info
    
    def _detect_reversal(self, klines: List[Dict], pullback_end_idx: int, pullback_info: Dict) -> Optional[Tuple[int, Dict]]:
        """
        检测C段：反包启动。
        出现放量反包阳线，量能是前日2倍以上。
        """
        cfg = self.CONFIG
        
        # 从回调期末端往后找
        remaining = klines[pullback_end_idx:]
        if len(remaining) < 2:
            return None
        
        # 找反包信号
        for i in range(len(remaining)):
            today = remaining[i]
            
            # 必须是阳线
            if not today["is_positive"]:
                continue
            
            # 检查放量
            if i > 0:
                prev = remaining[i - 1]
                if prev["volume"] > 0:
                    vol_ratio = today["volume"] / prev["volume"]
                else:
                    continue
            else:
                # 第一天没有前日对比，用回调均量
                vol_ratio = 1.5  # 假设满足
            
            if vol_ratio < cfg["reversal_min_volume_ratio"]:
                continue
            
            # 检查是否反包（收盘价超过前日最高价）
            is_engulfing = False
            if i > 0:
                prev = remaining[i - 1]
                is_engulfing = today["close"] > prev["high"]
            
            # 检查是否涨停
            is_limit_up = today["change_pct"] >= 9.5
            
            reversal_info = {
                "date": today["date"],
                "change_pct": today["change_pct"],
                "volume_ratio": round(vol_ratio, 2),
                "is_engulfing": is_engulfing,
                "is_limit_up": is_limit_up,
            }
            
            return pullback_end_idx + i, reversal_info
        
        return None
    
    def _calc_confidence(self, limit_info: Dict, pullback_info: Dict, reversal_info: Dict) -> int:
        """计算置信度评分"""
        cfg = self.CONFIG
        score = 0
        
        # 涨停数量评分
        if limit_info["count"] in (1, 2):
            score += 2  # 1-2个涨停最安全
        elif limit_info["count"] == 3:
            score += 1
        
        # 回调天数评分
        best_min, best_max = cfg["pullback_best_days"]
        if best_min <= pullback_info["days"] <= best_max:
            score += 2
        elif pullback_info["days"] <= 8:
            score += 1
        
        # 支撑强度评分
        if pullback_info["support_level"] == "strong":
            score += 2
        elif pullback_info["support_level"] == "medium":
            score += 1
        
        # 反包质量评分
        if reversal_info.get("is_limit_up"):
            score += 2
        elif reversal_info.get("is_engulfing"):
            score += 1
        
        return score


# ── 注册策略 ──
_strategy = LimitUpBoomerangStrategy()


def get_strategy():
    return _strategy
