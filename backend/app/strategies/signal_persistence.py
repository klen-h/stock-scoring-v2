"""
================================================================================
【文件作用】信号连续上榜追踪模块
================================================================================

追踪每个信号连续出现的天数，识别"强者恒强"的股票。

核心逻辑：
  - 每日扫描后，将信号存入 signal_history 表
  - 计算每个股票连续上榜天数
  - 连续天数越多，信号越可靠
  
可信度评级：
  - 连续 1 天：观察期（新信号，需验证）
  - 连续 2 天：初步确认
  - 连续 3 天：持续强势（可轻仓）
  - 连续 5 天：极强共识（可重仓）

综合可信度 = 共振评分(40%) + 连续天数(30%) + 市场状态(30%)

使用方式：
  from app.strategies.signal_persistence import update_persistence, get_persistence
  
  # 扫描后更新持久度
  update_persistence("advance2retreat1", signals)
  
  # 获取带持久度的信号
  enriched = get_persistence("advance2retreat1")
================================================================================
"""

from typing import Dict, List, Optional
from datetime import datetime, timedelta

from app.database import db


# ── 数据库表初始化 ──
def init_signal_history_table():
    """创建信号历史表"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS signal_history (
            id SERIAL PRIMARY KEY,
            strategy_name TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            signal_date TEXT NOT NULL,
            entry_price REAL,
            stop_loss REAL,
            target_price REAL,
            confidence INTEGER,
            signal_grade TEXT,
            signal_score INTEGER,
            confirmation_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(strategy_name, code, signal_date)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_history_strategy_date 
        ON signal_history(strategy_name, signal_date)
    """)
    print("[persistence] signal_history 表初始化完成")


# 启动时初始化
init_signal_history_table()


def update_persistence(strategy_name: str, signals: List[Dict]) -> List[Dict]:
    """
    更新信号持久度。
    
    将当日信号存入历史表，并计算每个信号的连续上榜天数。
    
    参数：
      strategy_name: 战法名称
      signals: 当日扫描产生的信号列表
    
    返回：
        添加了持久度信息的信号列表
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 存入今日信号
    for signal in signals:
        code = signal.get("code")
        if not code:
            continue
        
        try:
            db.execute("""
                INSERT INTO signal_history 
                (strategy_name, code, name, signal_date, entry_price, stop_loss, target_price, 
                 confidence, signal_grade, signal_score, confirmation_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (strategy_name, code, signal_date) DO NOTHING
            """, (
                strategy_name,
                code,
                signal.get("name"),
                today,
                signal.get("entry_price"),
                signal.get("stop_loss"),
                signal.get("target_price"),
                signal.get("confidence"),
                signal.get("signal_grade"),
                signal.get("signal_score"),
                str(signal.get("confirmation", {})),
            ))
        except Exception as e:
            print(f"[persistence] 存储信号失败 {code}: {e}")
    
    # 计算连续天数并返回
    return enrich_with_persistence(strategy_name, signals)


def enrich_with_persistence(strategy_name: str, signals: List[Dict]) -> List[Dict]:
    """
    为信号添加连续上榜天数和可信度评级。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    for signal in signals:
        code = signal.get("code")
        if not code:
            continue
        
        # 计算连续天数
        consecutive_days = _calc_consecutive_days(strategy_name, code, today)
        signal["consecutive_days"] = consecutive_days
        
        # 计算综合可信度
        trust_score = _calc_trust_score(
            signal_score=signal.get("signal_score", 0),
            consecutive_days=consecutive_days,
        )
        signal["trust_score"] = trust_score
        signal["trust_grade"] = _trust_grade(trust_score)
    
    # 按可信度排序
    signals.sort(key=lambda x: x.get("trust_score", 0), reverse=True)
    return signals


def _calc_consecutive_days(strategy_name: str, code: str, end_date: str) -> int:
    """
    计算从 end_date 往前连续上榜的天数。
    """
    consecutive = 0
    current = datetime.strptime(end_date, "%Y-%m-%d")
    
    # 最多回溯 30 天
    for _ in range(30):
        date_str = current.strftime("%Y-%m-%d")
        
        # 检查该日是否有信号
        row = db.fetch_one("""
            SELECT 1 FROM signal_history 
            WHERE strategy_name = %s AND code = %s AND signal_date = %s
        """, (strategy_name, code, date_str))
        
        if row:
            consecutive += 1
            current -= timedelta(days=1)
        else:
            break
    
    return consecutive


def _calc_trust_score(signal_score: int, consecutive_days: int) -> int:
    """
    计算综合可信度分数（0-100）。
    
    公式：
      可信度 = 共振评分(40%) + 连续天数(30%) + 基础分(30%)
    
    - 共振评分：signal_score 满分 100，取 40%
    - 连续天数：每天 15 分，最高 45 分（3天满分），取 30%
    - 基础分：固定 30 分（有信号就给）
    """
    # 共振评分部分（40分满分）
    resonance_part = min(signal_score * 0.4, 40)
    
    # 连续天数部分（30分满分，每天15分，最高2天满分）
    persistence_part = min(consecutive_days * 15, 30)
    
    # 基础分（30分）
    base_part = 30
    
    return int(resonance_part + persistence_part + base_part)


def _trust_grade(trust_score: int) -> str:
    """根据可信度分数返回等级"""
    if trust_score >= 85:
        return "A+"  # 极强共识，放心买
    elif trust_score >= 70:
        return "A"   # 持续强势，可重仓
    elif trust_score >= 55:
        return "B"   # 初步确认，可轻仓
    elif trust_score >= 40:
        return "C"   # 观察期，建议观望
    else:
        return "D"   # 不可信


def get_persistence_summary(strategy_name: str) -> Dict:
    """
    获取战法的持久度摘要。
    
    返回各连续天数的信号数量统计。
    """
    rows = db.fetch("""
        SELECT code, MAX(signal_date) as latest_date
        FROM signal_history 
        WHERE strategy_name = %s
        GROUP BY code
    """, (strategy_name,))
    
    today = datetime.now().strftime("%Y-%m-%d")
    stats = {"1天": 0, "2天": 0, "3天+": 0, "5天+": 0}
    
    for row in rows:
        code = row["code"]
        consecutive = _calc_consecutive_days(strategy_name, code, today)
        
        if consecutive >= 5:
            stats["5天+"] += 1
        elif consecutive >= 3:
            stats["3天+"] += 1
        elif consecutive >= 2:
            stats["2天"] += 1
        else:
            stats["1天"] += 1
    
    return {
        "strategy": strategy_name,
        "stats": stats,
        "total": len(rows),
    }


def get_top_persistent_signals(strategy_name: str, min_days: int = 3) -> List[Dict]:
    """
    获取连续上榜天数 >= min_days 的信号。
    
    这些是"强者恒强"的股票。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 获取最近有信号的所有股票
    rows = db.fetch("""
        SELECT DISTINCT code, name 
        FROM signal_history 
        WHERE strategy_name = %s
    """, (strategy_name,))
    
    persistent = []
    for row in rows:
        code = row["code"]
        consecutive = _calc_consecutive_days(strategy_name, code, today)
        
        if consecutive >= min_days:
            # 获取最新信号详情
            latest = db.fetch_one("""
                SELECT * FROM signal_history 
                WHERE strategy_name = %s AND code = %s
                ORDER BY signal_date DESC LIMIT 1
            """, (strategy_name, code))
            
            if latest:
                persistent.append({
                    "code": latest["code"],
                    "name": latest["name"],
                    "consecutive_days": consecutive,
                    "entry_price": latest["entry_price"],
                    "stop_loss": latest["stop_loss"],
                    "target_price": latest["target_price"],
                    "signal_grade": latest["signal_grade"],
                    "signal_score": latest["signal_score"],
                    "latest_date": latest["signal_date"],
                })
    
    # 按连续天数排序
    persistent.sort(key=lambda x: x["consecutive_days"], reverse=True)
    return persistent
