"""
================================================================================
【文件作用】快速撤退提醒模块
================================================================================

监控已买入的股票，当买入论点失效时立即提醒撤退。

监控维度：
  1. 跌破关键支撑位 → 论点失效
  2. RSI 超买回落 → 动能衰竭
  3. 放量下跌 → 主力出逃
  4. 跌破止损价 → 强制撤退

撤退信号等级：
  - 紧急：跌破止损价 / 跌破强支撑 → 立即撤退
  - 警告：RSI 超买回落 / 放量下跌 → 准备撤退
  - 观察：轻微回调 → 继续观察

使用方式：
  from app.strategies.exit_alert import check_exit_alerts
  
  alerts = check_exit_alerts([
      {"code": "000001", "entry_price": 12.5, "stop_loss": 11.8},
      ...
  ])
================================================================================
"""

from typing import Dict, List, Optional
from datetime import datetime

from app.tencent import get_kline
from app.strategies.rsi import _calc_rsi
from app.strategies.support_resistance import find_support_resistance


# ── 撤退阈值配置 ──
EXIT_CONFIG = {
    # 止损相关
    "stop_loss_buffer": 0.005,  # 止损价缓冲（0.5%），接近即警告
    
    # 支撑位相关
    "support_break_pct": 0.01,  # 跌破支撑 1% 视为失效
    
    # RSI 相关
    "rsi_overbought": 70,       # RSI 超买阈值
    "rsi_drop_threshold": 10,   # RSI 从高位回落 10 点视为动能衰竭
    
    # 量能相关
    "volume_surge_ratio": 2.0,  # 放量下跌量比阈值（2倍均量）
    "price_drop_pct": 0.03,     # 配合放量的跌幅阈值（3%）
}


def check_exit_alerts(positions: List[Dict]) -> List[Dict]:
    """
    检查多个持仓的撤退信号。
    
    参数：
      positions: 持仓列表
        [{
          "code": "000001",
          "name": "平安银行",
          "entry_price": 12.5,
          "stop_loss": 11.8,
          "target_price": 14.0,
          "entry_date": "2026-08-20",  # 可选
        }]
    
    返回：
      撤退提醒列表（按紧急程度排序）
    """
    alerts = []
    
    for pos in positions:
        code = pos.get("code")
        if not code:
            continue
        
        alert = check_single_exit(code, pos)
        if alert and alert["level"] != "safe":
            alerts.append(alert)
    
    # 按紧急程度排序：urgent > warning > watch
    level_order = {"urgent": 0, "warning": 1, "watch": 2}
    alerts.sort(key=lambda x: level_order.get(x["level"], 3))
    
    return alerts


def check_single_exit(code: str, position: Dict) -> Dict:
    """
    检查单个持仓的撤退信号。
    
    返回：
      {
        "code": "000001",
        "name": "平安银行",
        "current_price": 12.3,
        "profit_pct": -1.6,
        "level": "warning",      # urgent/warning/watch/safe
        "reasons": [...],        # 撤退理由列表
        "action": "建议减仓",    # 行动建议
        "timestamp": "..."
      }
    """
    entry_price = position.get("entry_price", 0)
    stop_loss = position.get("stop_loss", 0)
    
    # 获取实时数据
    klines = get_kline(code, period="day", count=30)
    if not klines or len(klines) < 5:
        return _safe_result(code, position, "数据不足")
    
    current_price = klines[-1]["close"]
    prev_close = klines[-2]["close"] if len(klines) >= 2 else current_price
    
    # 计算盈亏
    if entry_price > 0:
        profit_pct = round((current_price - entry_price) / entry_price * 100, 2)
    else:
        profit_pct = 0
    
    reasons = []
    level = "safe"
    
    # ── 检查 1：止损价 ──
    if stop_loss > 0:
        if current_price <= stop_loss:
            reasons.append({
                "type": "stop_loss",
                "level": "urgent",
                "message": f"已跌破止损价 {stop_loss}",
            })
            level = "urgent"
        elif current_price <= stop_loss * (1 + EXIT_CONFIG["stop_loss_buffer"]):
            reasons.append({
                "type": "near_stop_loss",
                "level": "warning",
                "message": f"接近止损价 {stop_loss}，仅 {(current_price - stop_loss) / stop_loss * 100:.1f}%",
            })
            if level != "urgent":
                level = "warning"
    
    # ── 检查 2：支撑位 ──
    sr_result = find_support_resistance(code, lookback_days=30)
    if sr_result and sr_result.get("levels"):
        supports = [l for l in sr_result["levels"] if l["type"] == "support"]
        if supports:
            nearest_support = min(supports, key=lambda x: abs(x["price"] - current_price))
            support_price = nearest_support["price"]
            
            if current_price < support_price * (1 - EXIT_CONFIG["support_break_pct"]):
                reasons.append({
                    "type": "support_broken",
                    "level": "urgent",
                    "message": f"跌破关键支撑 {support_price}",
                })
                level = "urgent"
            elif current_price < support_price:
                reasons.append({
                    "type": "near_support",
                    "level": "warning",
                    "message": f"接近支撑位 {support_price}",
                })
                if level == "safe":
                    level = "warning"
    
    # ── 检查 3：RSI 动能 ──
    rsi_values = _calc_rsi(klines, period=14)
    if rsi_values and len(rsi_values) >= 2:
        current_rsi = rsi_values[-1]["rsi"]
        prev_rsi = rsi_values[-2]["rsi"]
        
        # 超买回落
        if prev_rsi >= EXIT_CONFIG["rsi_overbought"] and current_rsi < prev_rsi - EXIT_CONFIG["rsi_drop_threshold"]:
            reasons.append({
                "type": "rsi_drop",
                "level": "warning",
                "message": f"RSI 从 {prev_rsi:.0f} 回落至 {current_rsi:.0f}，动能衰竭",
            })
            if level == "safe":
                level = "warning"
    
    # ── 检查 4：放量下跌 ──
    if len(klines) >= 6:
        recent_volumes = [k["volume"] for k in klines[-6:-1]]
        avg_volume = sum(recent_volumes) / len(recent_volumes) if recent_volumes else 0
        
        if avg_volume > 0:
            latest_volume = klines[-1]["volume"]
            volume_ratio = latest_volume / avg_volume
            price_change = (current_price - prev_close) / prev_close
            
            if (volume_ratio >= EXIT_CONFIG["volume_surge_ratio"] and 
                price_change <= -EXIT_CONFIG["price_drop_pct"]):
                reasons.append({
                    "type": "volume_drop",
                    "level": "warning",
                    "message": f"放量下跌 量比{volume_ratio:.1f} 跌幅{price_change*100:.1f}%",
                })
                if level == "safe":
                    level = "warning"
    
    # 生成行动建议
    action = _generate_action(level, reasons, profit_pct)
    
    return {
        "code": code,
        "name": position.get("name", ""),
        "current_price": current_price,
        "entry_price": entry_price,
        "profit_pct": profit_pct,
        "level": level,
        "reasons": reasons,
        "action": action,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _generate_action(level: str, reasons: List[Dict], profit_pct: float) -> str:
    """根据撤退等级生成行动建议"""
    if level == "urgent":
        urgent_reasons = [r["message"] for r in reasons if r["level"] == "urgent"]
        return f"⚠️ 紧急撤退：{'，'.join(urgent_reasons)}。建议立即止损。"
    elif level == "warning":
        warning_reasons = [r["message"] for r in reasons if r["level"] in ("warning", "urgent")]
        return f"⚡ 警告：{'，'.join(warning_reasons)}。建议减仓或设好止损。"
    elif level == "watch":
        return f"👀 观察：轻微回调，盈利 {profit_pct}%，继续持有观察。"
    else:
        return f"✓ 安全：盈利 {profit_pct}%，论点未失效，继续持有。"


def _safe_result(code: str, position: Dict, reason: str = "") -> Dict:
    """安全状态"""
    return {
        "code": code,
        "name": position.get("name", ""),
        "current_price": 0,
        "entry_price": position.get("entry_price", 0),
        "profit_pct": 0,
        "level": "safe",
        "reasons": [],
        "action": reason or "✓ 安全",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_exit_summary_for_watchlist(watchlist: List[Dict]) -> Dict:
    """
    为观察池生成撤退摘要。
    
    返回各等级的股票数量统计。
    """
    alerts = check_exit_alerts(watchlist)
    
    summary = {
        "urgent": [],
        "warning": [],
        "watch": [],
        "safe": [],
    }
    
    for alert in alerts:
        level = alert.get("level", "safe")
        if level in summary:
            summary[level].append(alert)
    
    return {
        "total": len(watchlist),
        "urgent_count": len(summary["urgent"]),
        "warning_count": len(summary["warning"]),
        "watch_count": len(summary["watch"]),
        "safe_count": len(summary["safe"]),
        "alerts": alerts,
    }
