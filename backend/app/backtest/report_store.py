"""
================================================================================
【文件作用】回测报告归档（数据库版）
================================================================================
为什么要有它（2026-09-11）：
  周度回测报告原本只写 `backend/backtest_reports/*.md` 文件，而 Render 免费实例
  的文件系统是**临时的**（每次部署/重启清空，render.yaml 里的 disk 未启用）→
  报告随时可能凭空消失，前端「回测中心」会退回到某个旧日期。
  日批（GitHub Actions）生成报告后也无法把文件送到 Render 容器。

  所以报告内容落库（backtest_reports 表），前端统一从库里读：
    · 文件仍照写（本地开发/容器内直接看，兼容旧路径）
    · 库是权威来源（跨进程、跨部署持久）

表结构（幂等）：
  (name) 唯一 —— 文件名即主键，重复生成同名报告为覆盖
================================================================================
"""

from datetime import datetime

from app.database import db

_TABLE_READY = False


def ensure_table():
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS backtest_reports (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            tag TEXT,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(name)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_backtest_reports_created
        ON backtest_reports(created_at DESC)
    """)
    _TABLE_READY = True


def save_report(name: str, content: str, tag: str = "") -> bool:
    """写一份报告到库（同名覆盖）。失败返回 False（调用方继续用文件，不中断）。"""
    if not name or not content:
        return False
    try:
        ensure_table()
        db.upsert("backtest_reports", {
            "name": name,
            "tag": tag or "",
            "content": content,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }, conflict_columns=["name"])
        return True
    except Exception as e:
        print(f"[report_store] 报告落库失败（文件已写）: {e}")
        return False


def list_reports(limit: int = 60) -> list:
    """库中报告清单（新→旧）。字段与文件版对齐：name / size / mtime。"""
    try:
        ensure_table()
        rows = db.fetch("""
            SELECT name, tag, length(content) AS size, created_at
            FROM backtest_reports ORDER BY created_at DESC LIMIT %s
        """, (limit,))
    except Exception as e:
        print(f"[report_store] 报告清单读取失败: {e}")
        return []
    return [{"name": r["name"], "tag": r.get("tag") or "",
             "size": int(r.get("size") or 0),
             "mtime": r.get("created_at") or "", "source": "db"}
            for r in (rows or [])]


def get_report(name: str):
    """取报告正文。不存在返回 None。"""
    try:
        ensure_table()
        row = db.fetch_one(
            "SELECT content FROM backtest_reports WHERE name = %s", (name,))
    except Exception as e:
        print(f"[report_store] 报告读取失败: {e}")
        return None
    return (row or {}).get("content")
