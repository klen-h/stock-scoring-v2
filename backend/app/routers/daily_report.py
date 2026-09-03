"""
================================================================================
【文件作用】A股大盘日报读取路由
================================================================================

URL 前缀 /api/report，数据源：daily_reports 表（scheduler 每日 16:20 落库）。

接口列表：
  GET /api/report/list           → 最近 N 天日报列表（date / created_at / 长度）
  GET /api/report/daily?date=X   → 某天完整 markdown；date 缺省返回最新一份

用途：前端日报阅读页（体验层），配合日报生成（app/daily_report.py）。
================================================================================
"""

from fastapi import APIRouter, Query
from app.database import db

router = APIRouter()


@router.get("/list")
def report_list(limit: int = Query(30, ge=1, le=120)):
    """最近 N 天日报列表（不含正文，只返回元信息）。"""
    rows = db.fetch(
        "SELECT date, created_at, LENGTH(markdown) AS len "
        "FROM daily_reports ORDER BY date DESC LIMIT %s", (limit,))
    return {"data": [dict(r) for r in (rows or [])]}


@router.get("/daily")
def report_daily(date: str = None):
    """某天完整日报 markdown；date 缺省返回最新一份。"""
    if date:
        row = db.fetch_one("SELECT * FROM daily_reports WHERE date = %s", (date,))
    else:
        row = db.fetch_one("SELECT * FROM daily_reports ORDER BY date DESC LIMIT 1")
    return dict(row) if row else {}
