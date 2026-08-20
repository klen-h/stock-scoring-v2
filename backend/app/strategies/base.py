"""
================================================================================
【文件作用】战法选股基础架构
================================================================================

提供：
  1. 通用股票池过滤器（市值/成交量/ST/上市天数/板块等）
  2. 策略基类 BaseStrategy（统一接口）
  3. 扫描结果存储（数据库）

设计原则：
  - 过滤器链式调用，各策略可复用
  - K线数据复用 tencent.py 的 get_kline（带缓存）
  - 结果持久化到数据库（SQLite 或 PostgreSQL）
================================================================================
"""

import json
import os
from datetime import datetime, timedelta
from abc import ABC, abstractmethod
from typing import List, Dict, Optional, Any

from app.tencent import get_kline, _cache as tencent_cache
from app.database import db

# ── 路径（保留用于兼容）──
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")


# ================================================================
#  通用过滤器
# ================================================================

def filter_stock_pool(
    min_market_cap: float = 20e8,        # 最小市值（元），默认20亿
    min_avg_volume: float = 1000e4,      # 最小日均成交额（元），默认1000万
    exclude_st: bool = True,             # 排除ST
    exclude_star: bool = True,           # 排除科创板（688开头）
    exclude_chinext: bool = True,        # 排除创业板（300开头）
    min_listing_days: int = 60,          # 最小上市天数
    top_sectors: Optional[List[str]] = None,  # 限定板块代码列表
) -> List[Dict]:
    """
    通用股票池过滤器。
    
    参数：
      min_market_cap:    最小市值（元），默认20亿
      min_avg_volume:    最小日均成交额（元），默认1000万
      exclude_st:        排除ST股
      exclude_star:      排除科创板
      exclude_chinext:   排除创业板
      min_listing_days:  最小上市天数
      top_sectors:       限定板块代码列表（为空则不限）
    
    返回：
      过滤后的股票列表 [{code, name, market_cap, avg_volume, ...}, ...]
    """
    stocks = tencent_cache.get("stocks", {})
    if not stocks:
        return []
    
    result = []
    today = datetime.now()
    cutoff_date = today - timedelta(days=min_listing_days)
    
    for code, info in stocks.items():
        # 基本过滤
        name = info.get("name", "")
        price = info.get("price", 0)
        market_cap = info.get("market_cap", 0) * 10000  # 万元转元
        amount = info.get("amount", 0)  # 成交额（元）
        
        # ST过滤
        if exclude_st and ("ST" in name or "st" in name):
            continue
        
        # 科创板过滤（688开头）
        if exclude_star and code.startswith("688"):
            continue
        
        # 创业板过滤（300开头）
        if exclude_chinext and code.startswith("300"):
            continue
        
        # 市值过滤
        if market_cap < min_market_cap:
            continue
        
        # 成交额过滤（用当日成交额近似，理想应用5日均值）
        if amount < min_avg_volume:
            continue
        
        # 价格有效性
        if price <= 0:
            continue
        
        # 板块过滤（如有指定）
        # TODO: 实现板块关联查询（需要板块成分股数据）
        if top_sectors:
            # 暂时跳过，后续实现
            pass
        
        result.append({
            "code": code,
            "name": name,
            "price": price,
            "market_cap": market_cap,
            "amount": amount,
            "change_pct": info.get("change_pct", 0),
            "pe": info.get("pe", 0),
            "pb": info.get("pb", 0),
        })
    
    print(f"[strategies] 股票池过滤完成: {len(result)} 只（从 {len(stocks)} 只中筛选）")
    return result


# ================================================================
#  K线分析工具
# ================================================================

def get_kline_with_indicators(code: str, count: int = 60) -> List[Dict]:
    """
    获取K线并计算常用指标。
    
    返回：
      K线列表，每根包含 {date, open, close, high, low, volume, change_pct, ...}
    """
    klines = get_kline(code, period="day", count=count)
    if not klines or len(klines) < 5:
        return []
    
    result = []
    prev_close = None
    
    for k in klines:
        item = {
            "date": k.get("date", ""),
            "open": float(k.get("open", 0)),
            "close": float(k.get("close", 0)),
            "high": float(k.get("high", 0)),
            "low": float(k.get("low", 0)),
            "volume": float(k.get("volume", 0)),
        }
        
        # 计算涨跌幅
        if prev_close and prev_close > 0:
            item["change_pct"] = round((item["close"] - prev_close) / prev_close * 100, 2)
        else:
            item["change_pct"] = 0
        
        # 计算实体长度、上影线、下影线
        body = abs(item["close"] - item["open"])
        upper_shadow = item["high"] - max(item["close"], item["open"])
        lower_shadow = min(item["close"], item["open"]) - item["low"]
        
        item["body"] = body
        item["upper_shadow"] = upper_shadow
        item["lower_shadow"] = lower_shadow
        item["is_positive"] = item["close"] > item["open"]  # 阳线
        
        result.append(item)
        prev_close = item["close"]
    
    return result


def calc_position_in_range(klines: List[Dict], lookback: int = 60) -> float:
    """
    计算当前价格在回看区间内的位置百分比。
    
    返回：
      0-100，0表示区间最低，100表示区间最高
    """
    if not klines or len(klines) < lookback:
        return 50  # 数据不足返回中间值
    
    recent = klines[-lookback:]
    highs = [k["high"] for k in recent]
    lows = [k["low"] for k in recent]
    
    range_high = max(highs)
    range_low = min(lows)
    current = klines[-1]["close"]
    
    if range_high == range_low:
        return 50
    
    position = (current - range_low) / (range_high - range_low) * 100
    return round(position, 1)


def calc_volume_ratio(klines: List[Dict], days: int = 5) -> float:
    """
    计算量比：当日成交量 / 前N日平均成交量
    """
    if len(klines) < days + 1:
        return 1.0
    
    today_vol = klines[-1]["volume"]
    avg_vol = sum(k["volume"] for k in klines[-days-1:-1]) / days
    
    if avg_vol == 0:
        return 1.0
    
    return round(today_vol / avg_vol, 2)


# ================================================================
#  策略基类
# ================================================================

class BaseStrategy(ABC):
    """战法策略基类"""
    
    # 子类必须定义
    name: str = ""           # 策略名称（中文）
    name_en: str = ""        # 策略名称（英文，用于API）
    description: str = ""    # 策略描述
    
    @abstractmethod
    def scan(self, stock_pool: List[Dict]) -> List[Dict]:
        """
        扫描股票池，返回符合条件的信号列表。
        
        参数：
          stock_pool: 过滤后的股票池
        
        返回：
          信号列表，每个信号包含：
          {
            "code": "000001",
            "name": "平安银行",
            "signal_date": "2026-08-20",
            "confidence": 6,           # 置信度评分
            "confidence_level": "high", # high/medium/low
            "entry_price": 10.5,       # 建议介入价
            "stop_loss": 10.0,         # 止损价
            "target_price": 11.5,      # 目标价
            "details": {...},          # 策略特定的详细信息
            "klines": [...],           # 最近几根K线（用于展示）
          }
        """
        pass
    
    def get_config(self) -> Dict:
        """获取策略配置（前端展示用）"""
        return {
            "name": self.name,
            "name_en": self.name_en,
            "description": self.description,
        }
    
    def detect_signal(self, klines: List[Dict], idx: int) -> Optional[Dict]:
        """
        检测在指定索引位置是否触发信号（用于回测）。
        
        子类可以重写此方法实现具体的信号检测逻辑。
        默认实现返回 None（表示不支持回测）。
        
        参数：
          klines: 完整的K线数据列表
          idx: 当前检测位置的索引（检测 klines[idx] 是否触发信号）
        
        返回：
          如果触发信号，返回 {entry_price, stop_loss, target_price} 字典
          否则返回 None
        """
        return None


# ================================================================
#  扫描结果管理（数据库版）
# ================================================================

def save_scan_result(strategy_name: str, results: List[Dict]):
    """保存扫描结果到数据库"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        db.upsert("strategy_results", {
            "strategy_name": strategy_name,
            "scan_date": today,
            "count": len(results),
            "results_json": json.dumps(results, ensure_ascii=False)
        }, conflict_columns=["strategy_name", "scan_date"])
        print(f"[strategies] 保存扫描结果: {strategy_name} {len(results)} 只")
    except Exception as e:
        print(f"[strategies] 保存扫描结果失败: {e}")


def get_scan_result(strategy_name: str) -> Dict:
    """获取最近一次扫描结果"""
    row = db.fetch_one(
        "SELECT * FROM strategy_results WHERE strategy_name = %s ORDER BY scan_date DESC LIMIT 1",
        (strategy_name,)
    )
    if not row:
        return {}
    try:
        return {
            "date": row["scan_date"],
            "count": row["count"],
            "results": json.loads(row["results_json"])
        }
    except (json.JSONDecodeError, KeyError):
        return {}


def save_watch_pool(strategy_name: str, pool: List[Dict]):
    """保存观察池到数据库"""
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        # 先删除旧的
        db.execute(
            "DELETE FROM strategy_watch WHERE strategy_name = %s",
            (strategy_name,)
        )
        # 插入新的
        for stock in pool:
            db.execute(
                "INSERT INTO strategy_watch "
                "(strategy_name, code, name, entry_price, stop_loss, target_price, added_date, extra_json) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (strategy_name, stock.get("code", ""), stock.get("name", ""),
                 stock.get("entry_price"), stock.get("stop_loss"),
                 stock.get("target_price"), stock.get("added_date", today),
                 json.dumps({k: v for k, v in stock.items() 
                            if k not in ["code", "name", "entry_price", "stop_loss", "target_price", "added_date"]},
                           ensure_ascii=False))
            )
    except Exception as e:
        print(f"[strategies] 保存观察池失败: {e}")


def get_watch_pool(strategy_name: str) -> Dict:
    """获取观察池"""
    rows = db.fetch(
        "SELECT * FROM strategy_watch WHERE strategy_name = %s ORDER BY added_date DESC",
        (strategy_name,)
    )
    if not rows:
        return {"date": None, "stocks": []}
    
    stocks = []
    for r in rows:
        stock = {
            "code": r["code"],
            "name": r["name"],
            "entry_price": r.get("entry_price"),
            "stop_loss": r.get("stop_loss"),
            "target_price": r.get("target_price"),
            "added_date": r.get("added_date"),
        }
        # 合并 extra_json 中的字段
        if r.get("extra_json"):
            try:
                extra = json.loads(r["extra_json"])
                stock.update(extra)
            except (json.JSONDecodeError, TypeError):
                pass
        stocks.append(stock)
    
    # 获取最新日期
    latest = rows[0].get("added_date") if rows else None
    return {"date": latest, "stocks": stocks}


# ================================================================
#  策略注册表
# ================================================================

_STRATEGIES: Dict[str, BaseStrategy] = {}


def register_strategy(strategy: BaseStrategy):
    """注册策略"""
    _STRATEGIES[strategy.name_en] = strategy


def get_strategy(name_en: str) -> Optional[BaseStrategy]:
    """获取策略实例"""
    return _STRATEGIES.get(name_en)


def list_strategies() -> List[Dict]:
    """列出所有已注册策略"""
    return [s.get_config() for s in _STRATEGIES.values()]
