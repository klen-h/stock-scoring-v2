"""
================================================================================
【文件作用】评分排行榜连续上榜追踪
================================================================================

追踪每天评分 Top N 的股票，计算连续上榜天数。
连续上榜天数越多 = 强者恒强 = 买入可信度越高。

可信度评级：
  - 连续 1 天：新上榜（观察期）
  - 连续 2 天：初步确认
  - 连续 3 天：持续强势（可轻仓）
  - 连续 5 天：极强共识（放心买）

综合可信度 = 评分(40%) + 连续天数(40%) + 信号强度(20%)

使用方式：
  from app.scoring.ranking_history import record_daily_ranking, get_ranking_persistence
  
  # 盘后记录当日排行
  record_daily_ranking(top_stocks)
  
  # 查询可信度
  result = get_ranking_persistence(["000001", "600519"])
================================================================================
"""

from typing import Dict, List
from datetime import datetime, timedelta

from app.database import db


# ── 数据库表初始化 ──
def init_ranking_history_table():
    """创建排行榜历史表"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS ranking_history (
            id SERIAL PRIMARY KEY,
            rank_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            rank_pos INTEGER,
            total_score REAL,
            signal TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rank_date, code)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ranking_history_date_code 
        ON ranking_history(rank_date, code)
    """)
    print("[ranking] ranking_history 表初始化完成")


# 启动时初始化
init_ranking_history_table()


def record_daily_ranking(top_stocks: List[Dict]) -> int:
    """
    记录当日评分排行榜。
    
    参数：
      top_stocks: [{code, name, total_score, signal, rank}, ...]
    
    返回：成功记录的条数
    """
    today = datetime.now().strftime("%Y-%m-%d")
    count = 0
    
    for i, stock in enumerate(top_stocks):
        code = stock.get("code")
        if not code:
            continue
        try:
            db.execute("""
                INSERT INTO ranking_history 
                (rank_date, code, name, rank_pos, total_score, signal)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (rank_date, code) DO UPDATE 
                SET name = EXCLUDED.name, rank_pos = EXCLUDED.rank_pos,
                    total_score = EXCLUDED.total_score, signal = EXCLUDED.signal
            """, (
                today,
                code,
                stock.get("name"),
                stock.get("rank", i + 1),
                stock.get("total_score"),
                stock.get("signal"),
            ))
            count += 1
        except Exception as e:
            print(f"[ranking] 记录排行失败 {code}: {e}")
    
    print(f"[ranking] 已记录 {count} 只股票的排行（{today}）")
    return count


def get_ranking_persistence(codes: List[str]) -> List[Dict]:
    """
    查询多只股票的连续上榜天数和可信度。
    
    返回：
      [{code, consecutive_days, trust_score, trust_grade, latest_score, latest_signal, advice}, ...]
    """
    if not codes:
        return []
    
    today = datetime.now().strftime("%Y-%m-%d")
    results = []
    
    for code in codes:
        consecutive = _calc_consecutive_days(code, today)
        
        # 获取最新排行信息
        latest = db.fetch_one("""
            SELECT total_score, signal, name, rank_pos 
            FROM ranking_history 
            WHERE code = %s
            ORDER BY rank_date DESC LIMIT 1
        """, (code,))
        
        if latest:
            total_score = latest.get("total_score", 0) or 0
            signal = latest.get("signal", "观望") or "观望"
            name = latest.get("name", "")
            rank_pos = latest.get("rank_pos", 0)
        else:
            total_score = 0
            signal = "观望"
            name = ""
            rank_pos = 0
        
        # 计算综合可信度
        trust_score = _calc_trust_score(total_score, consecutive, signal)
        trust_grade = _trust_grade(trust_score)
        advice = _trust_advice(trust_grade, consecutive)
        
        results.append({
            "code": code,
            "name": name,
            "consecutive_days": consecutive,
            "trust_score": trust_score,
            "trust_grade": trust_grade,
            "latest_score": total_score,
            "latest_signal": signal,
            "rank_pos": rank_pos,
            "advice": advice,
        })
    
    return results


def _calc_consecutive_days(code: str, end_date: str) -> int:
    """计算从 end_date 往前连续上榜的天数（最多回溯 30 天）"""
    consecutive = 0
    current = datetime.strptime(end_date, "%Y-%m-%d")
    
    for _ in range(30):
        date_str = current.strftime("%Y-%m-%d")
        row = db.fetch_one("""
            SELECT 1 FROM ranking_history 
            WHERE code = %s AND rank_date = %s
        """, (code, date_str))
        
        if row:
            consecutive += 1
            current -= timedelta(days=1)
        else:
            break
    
    return consecutive


def _calc_trust_score(total_score: float, consecutive_days: int, signal: str) -> int:
    """
    计算综合可信度（0-100）。
    
    公式：
      可信度 = 评分(40%) + 连续天数(40%) + 信号强度(20%)
    
    - 评分：total_score 满分 100，取 40%
    - 连续天数：每天 20 分，最高 5 天满分（100），取 40%
    - 信号强度：强烈买入=100, 买入=70, 观望=30, 卖出=0
    """
    # 评分部分（40分满分）
    score_part = min(total_score * 0.4, 40)
    
    # 连续天数部分（40分满分，每天20分，最高5天满分）
    days_ratio = min(consecutive_days / 5.0, 1.0)
    persistence_part = days_ratio * 40
    
    # 信号强度部分（20分满分）
    signal_scores = {
        "强烈买入": 100, "买入": 70, "观望": 30, "卖出": 10, "强烈卖出": 0
    }
    signal_part = (signal_scores.get(signal, 30) / 100) * 20
    
    return int(score_part + persistence_part + signal_part)


def _trust_grade(trust_score: int) -> str:
    """根据可信度分数返回等级"""
    if trust_score >= 80:
        return "A+"  # 极强共识，放心买
    elif trust_score >= 65:
        return "A"   # 持续强势，可重仓
    elif trust_score >= 50:
        return "B"   # 初步确认，可轻仓
    elif trust_score >= 35:
        return "C"   # 观察期
    else:
        return "D"   # 不可信


def _trust_advice(trust_grade: str, consecutive_days: int) -> str:
    """根据可信度等级返回操作建议"""
    if trust_grade == "A+":
        return "放心买，极强共识"
    elif trust_grade == "A":
        return "可重仓，持续强势"
    elif trust_grade == "B":
        return "可轻仓，初步确认"
    elif trust_grade == "C":
        return "建议观望，观察期"
    else:
        return "不建议操作"


# ── 供调度器调用的自动记录函数 ──
async def auto_record_ranking():
    """
    自动记录当日评分 Top 50 排行。
    由调度器在盘后调用。
    """
    from app.tencent import _cache
    from app.scoring.engine import ScoringEngine
    
    stocks = _cache.get("stocks", {})
    if not stocks:
        print("[ranking] 行情缓存为空，跳过排行记录")
        return 0
    
    stock_list = list(stocks.values())
    valid = [s for s in stock_list if s.get("price", 0) > 0 and s.get("change_pct") is not None]
    
    # 用简化评分快速排序
    engine = ScoringEngine()
    results = engine.score_batch(valid[:200])  # 只算前200只
    results.sort(key=lambda r: r.total_score, reverse=True)
    
    top_stocks = [
        {
            "code": r.code,
            "name": r.name,
            "total_score": r.total_score,
            "signal": r.signal,
            "rank": i + 1,
        }
        for i, r in enumerate(results[:50])
    ]
    
    return record_daily_ranking(top_stocks)
