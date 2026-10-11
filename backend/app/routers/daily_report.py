"""
================================================================================
【文件作用】A股大盘日报读取路由
================================================================================

URL 前缀 /api/report，数据源：daily_reports 表（scheduler 每日 16:20 落库）。

接口列表：
  GET /api/report/list           → 最近 N 天日报列表（date / created_at / 长度）
  GET /api/report/daily?date=X   → 某天完整 markdown；date 缺省返回最新一份
  GET /api/report/weekly?days=7  → **周复盘**（regime 轨迹 + 执行一致性 + 日报/系统
                                   提示索引 + 下周日历）。见 app/weekly_review.py。

用途：前端日报阅读页（体验层），配合日报生成（app/daily_report.py）。
================================================================================
"""

from fastapi import APIRouter, Query
from app.database import db
# ★ 2026-10-11（把"结论"约定推到日报）：`conclusion` 从「机械标题」下抽取，见 `report_daily`。
from app.insight import section_first_paragraph

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
    """某天完整日报 markdown；date 缺省返回最新一份。**额外返回 `conclusion`**。

    ★★ 2026-10-11（把"结构化/结论"约定推到日报）：`conclusion` = **`## 今日主线` 小节的第一段**
    （= 模型写的"一句话定性"）。三条口径，别放宽：

    · **标题是我们自己代码写死的**（`daily_report._render_llm_markdown` 的
      `add("## 今日主线")`）⇒ 抽取是**确定性**的，不是"猜模型把结论放哪"（与 L2 的
      `**结论**：` 标记同级：标记必须由**生成侧显式给出**）；
    · 取不到 / 只有标题 / 首段超 120 字 ⇒ `conclusion=''`（**宁缺勿猜、不改模型原话**）⇒
      前端只是不显示结论条，不报错；
    · **`markdown` 原样不动**：日报正文与企微推送（推的就是这份 markdown）**完全不受影响** ——
      这也是"不做结构改造、只在读侧多给一个字段"的理由。
    """
    if date:
        row = db.fetch_one("SELECT * FROM daily_reports WHERE date = %s", (date,))
    else:
        row = db.fetch_one("SELECT * FROM daily_reports ORDER BY date DESC LIMIT 1")
    if not row:
        return {}
    out = dict(row)
    out["conclusion"] = section_first_paragraph(out.get("markdown") or "", "今日主线")
    return out


@router.get("/weekly")
def report_weekly(days: int = Query(7, ge=1, le=90), end: str = None):
    """**周复盘**：本周 regime 轨迹 + 执行一致性/放弃理由 + 日报与系统提示索引 + 下周日历。

    `end`（YYYY-MM-DD，可选）：窗口截止日（用于回看历史某周）；缺省 = 今天（北京时间）。
    口径/边界见 `app/weekly_review.py` 文件头（纯只读聚合、零外部网络请求）。
    """
    from app.weekly_review import build_weekly_review
    return build_weekly_review(days=days, end=end)
