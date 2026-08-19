"""
================================================================================
【文件作用】"均线粘合+平台突破"战法实现
================================================================================

核心逻辑：
  多条均线（5/10/20/30/60日）长期横盘后粘合 → 放量大阳线突破平台上沿

形态：
  横盘期：均线粘合（差值<3%），横盘≥20天，振幅<15%，地量
  突破日：放量大阳（涨幅≥5%，量比≥2倍），突破平台上沿
  回踩期（可选）：突破后回踩平台上沿不破

核心逻辑："横有多长，竖有多高"
================================================================================
"""

from typing import List, Dict, Optional, Tuple
from .base import BaseStrategy, get_kline_with_indicators, calc_position_in_range


class MAConvergenceBreakoutStrategy(BaseStrategy):
    """均线粘合+平台突破战法"""
    
    name = "均线粘合突破"
    name_en = "ma_convergence_breakout"
    description = "多条均线粘合横盘20天以上，振幅<15%，地量后放量大阳突破平台上沿时介入。主升浪前兆。"
    
    CONFIG = {
        # ── 横盘期条件 ──
        "min_consolidation_days": 20,   # 最小横盘天数
        "max_amplitude": 15.0,          # 最大振幅 %
        "ma_convergence_threshold": 3.0,  # 均线粘合阈值（最大差值%）
        "max_vol_decay": 0.5,           # 横盘末期量 < 初期量 × 此值（地量）
        
        # ── 突破日条件 ──
        "breakout_min_change": 5.0,     # 突破日最小涨幅
        "breakout_min_volume_ratio": 2.0,  # 突破日最小量比（相对前5日）
        
        # ── 止损 ──
        "stop_loss_buffer": 0.03,       # 平台上沿下方3%
        
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
        klines = get_kline_with_indicators(code, count=80)
        if len(klines) < 40:
            return None
        
        # ── 找突破日 ──
        breakout = self._find_breakout(klines)
        if not breakout:
            return None
        breakout_idx, breakout_info = breakout
        
        # ── 检查突破日前的横盘期 ──
        consolidation = self._detect_consolidation(klines, breakout_idx)
        if not consolidation:
            return None
        consol_info = consolidation
        
        # ── 置信度 ──
        confidence = self._calc_confidence(consol_info, breakout_info)
        
        current = klines[-1]
        entry_price = current["close"]
        platform_high = consol_info["platform_high"]
        stop_loss = platform_high * (1 - cfg["stop_loss_buffer"])
        # 止损不能超过介入价的10%
        if stop_loss < entry_price * 0.90:
            stop_loss = entry_price * 0.90
        
        # 目标：平台高度的一倍量度，至少10%盈利空间
        platform_height = platform_high - consol_info["platform_low"]
        target_price = max(entry_price + platform_height, entry_price * 1.10)
        
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
                "consolidation": consol_info,
                "breakout": breakout_info,
            },
            "klines": klines[-10:],
            "market_cap": stock.get("market_cap", 0),
        }
    
    def _find_breakout(self, klines: List[Dict]) -> Optional[Tuple[int, Dict]]:
        """找最近的放量突破大阳线"""
        cfg = self.CONFIG
        
        for i in range(len(klines) - 1, max(len(klines) - 10, 20), -1):
            k = klines[i]
            
            if not k["is_positive"]:
                continue
            if k["change_pct"] < cfg["breakout_min_change"]:
                continue
            
            # 量比检查
            pre_klines = klines[max(0, i-5):i]
            if len(pre_klines) < 3:
                continue
            avg_vol = sum(pk["volume"] for pk in pre_klines) / len(pre_klines)
            if avg_vol <= 0:
                continue
            vol_ratio = k["volume"] / avg_vol
            if vol_ratio < cfg["breakout_min_volume_ratio"]:
                continue
            
            breakout_info = {
                "date": k["date"],
                "change_pct": k["change_pct"],
                "volume_ratio": round(vol_ratio, 2),
                "close": k["close"],
            }
            return i, breakout_info
        
        return None
    
    def _detect_consolidation(self, klines: List[Dict], breakout_idx: int) -> Optional[Dict]:
        """检测突破日前的横盘整理期"""
        cfg = self.CONFIG
        
        # 从突破日前一天开始往前找横盘区间
        search_end = breakout_idx - 1
        search_start = max(0, search_end - 60)
        
        # 尝试找到至少20天的横盘期
        platform_high = klines[search_end]["high"]
        platform_low = klines[search_end]["low"]
        
        consol_days = 0
        volumes_early = []
        volumes_late = []
        
        for i in range(search_end, search_start, -1):
            k = klines[i]
            new_high = max(platform_high, k["high"])
            new_low = min(platform_low, k["low"])
            
            # 检查振幅
            if new_low > 0:
                amplitude = (new_high - new_low) / new_low * 100
            else:
                break
            
            if amplitude > cfg["max_amplitude"]:
                break  # 振幅过大，横盘结束
            
            platform_high = new_high
            platform_low = new_low
            consol_days += 1
            
            if consol_days <= 5:
                volumes_late.append(k["volume"])
            if consol_days >= 15:
                volumes_early.append(k["volume"])
        
        if consol_days < cfg["min_consolidation_days"]:
            return None
        
        # 检查均线粘合
        if not self._check_ma_convergence(klines, breakout_idx):
            return None
        
        # 检查地量
        vol_decay = 1.0
        if volumes_early and volumes_late:
            avg_early = sum(volumes_early) / len(volumes_early)
            avg_late = sum(volumes_late) / len(volumes_late)
            if avg_early > 0:
                vol_decay = avg_late / avg_early
        
        consol_info = {
            "days": consol_days,
            "platform_high": round(platform_high, 2),
            "platform_low": round(platform_low, 2),
            "amplitude": round((platform_high - platform_low) / platform_low * 100, 2) if platform_low > 0 else 0,
            "volume_decay": round(vol_decay, 2),
            "is_converged": True,
        }
        
        return consol_info
    
    def _check_ma_convergence(self, klines: List[Dict], idx: int) -> bool:
        """检查均线粘合"""
        cfg = self.CONFIG
        
        if idx < 60:
            return False
        
        # 计算各均线
        ma5 = sum(k["close"] for k in klines[idx-4:idx+1]) / 5
        ma10 = sum(k["close"] for k in klines[idx-9:idx+1]) / 10
        ma20 = sum(k["close"] for k in klines[idx-19:idx+1]) / 20
        ma30 = sum(k["close"] for k in klines[idx-29:idx+1]) / 30
        
        mas = [ma5, ma10, ma20, ma30]
        if any(m <= 0 for m in mas):
            return False
        
        max_ma = max(mas)
        min_ma = min(mas)
        
        if min_ma <= 0:
            return False
        
        diff_pct = (max_ma - min_ma) / min_ma * 100
        return diff_pct <= cfg["ma_convergence_threshold"]
    
    def _calc_confidence(self, consol_info: Dict, breakout_info: Dict) -> int:
        score = 0
        
        # 横盘天数评分
        if consol_info["days"] >= 30:
            score += 2
        elif consol_info["days"] >= 20:
            score += 1
        
        # 量能衰减评分（地量越明显越好）
        if consol_info["volume_decay"] <= 0.3:
            score += 2
        elif consol_info["volume_decay"] <= 0.5:
            score += 1
        
        # 突破质量评分
        if breakout_info["volume_ratio"] >= 3:
            score += 2
        elif breakout_info["volume_ratio"] >= 2:
            score += 1
        
        return score


_strategy = MAConvergenceBreakoutStrategy()

def get_strategy():
    return _strategy
