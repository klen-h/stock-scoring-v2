"""
================================================================================
【文件作用】消息面历史持久化（阶段 3 回测的数据基础）
================================================================================

每日盘后给「持仓股 + 评分 Top50」算一次新闻分并落库（news_history 表）。
积累 4~8 周后即可回测验证"消息分是否预测次日/5日收益"，决定是否进总分。

表结构（幂等，模块导入即建表，与 ranking_history 同款模式）：
  (snap_date, code) 唯一 → 同一天可覆盖重写（快照补跑友好）。

对外函数：
  record_news_snapshot(rows)     批量写入/覆盖某日快照
  get_news_history(code, days)   读某只股票最近 N 天（升序，画走势图用）
================================================================================
"""

from datetime import datetime

from app.database import db


def init_news_history_table():
    """创建消息分历史表（幂等）。"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS news_history (
            id SERIAL PRIMARY KEY,
            snap_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            score REAL,
            level INTEGER,
            level_text TEXT,
            news_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(snap_date, code)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_history_code_date
        ON news_history(code, snap_date DESC)
    """)
    print("[news_history] news_history 表已初始化")


# 导入时初始化
init_news_history_table()


def record_news_snapshot(rows: list, date: str = None) -> int:
    """
    写入某日消息分快照。
    rows: [{code, name, score, level, level_text, news_count}, ...]
    date: 缺省取当天。同日重复写入为覆盖（ON CONFLICT），补跑安全。
    返回写入条数。
    """
    d = date or datetime.now().strftime("%Y-%m-%d")
    count = 0
    for r in rows or []:
        if not r.get("code"):
            continue
        try:
            db.execute("""
                INSERT INTO news_history
                (snap_date, code, name, score, level, level_text, news_count)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (snap_date, code) DO UPDATE
                SET name = EXCLUDED.name, score = EXCLUDED.score,
                    level = EXCLUDED.level, level_text = EXCLUDED.level_text,
                    news_count = EXCLUDED.news_count
            """, (d, r["code"], r.get("name"), r.get("score"),
                  r.get("level"), r.get("level_text"), r.get("news_count", 0)))
            count += 1
        except Exception as e:
            print(f"[news_history] 写入失败 {r.get('code')}: {e}")
    return count


def get_news_history(code: str, days: int = 30) -> list:
    """读某只股票最近 N 天的消息分快照（升序）。无数据返回 []。"""
    rows = db.fetch("""
        SELECT snap_date AS date, score, level_text FROM news_history
        WHERE code = %s
        ORDER BY snap_date DESC LIMIT %s
    """, (code, days))
    return list(reversed(rows or []))
