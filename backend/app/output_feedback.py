# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】输出消费闭环（C2；判据/规格 = `PLAN_NEXT.md` 【附 B】v0，2026-10-11 立项）
把「系统输出改变了我什么」从感觉变成**可统计的数据** —— 这是项目第二个"一直没问的问题"
（第一个 = 评分预测力，已于同日检验 FAIL）的最低成本起步。
================================================================================
【边界（别放宽）】
  · 只记 (date, source, ref, action) 四元组，**不存任何内容文本** —— 隐私与 egress 双最小化；
  · `action` 只有两档：`acted`(有影响) / `dismissed`(没影响) —— 二选一成本低才做得久
    （PLAN_NEXT 附 B 未决问题1 的结论）；
  · 同一 `(source, ref)` **幂等覆盖** ⇒ 可改主意，一个输出只保留最新结论；
  · **不联动**模拟盘/观察池（附 B 未决问题2 的结论：先白嫖分布，再决定要不要闭环到操作）。
【与 coach_alerts 的关系】不复用：那是"教练建议"的领域表（含 executed/outcome 语义）；
  这里是**任意系统输出**的消费标记，语义不同 ⇒ 独立小表，避免把领域表改成杂货铺。
================================================================================
"""
from fastapi import APIRouter, Query

from app.database import db

router = APIRouter()

SOURCE_ALLOWED = ("radar", "brief", "diag", "daily_report")
ACTION_ALLOWED = ("acted", "dismissed")
_TABLE_READY = False


def _ensure() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS output_feedback (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL,
            source TEXT NOT NULL,
            ref TEXT NOT NULL,
            action TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 幂等覆盖靠 (source, ref) 唯一：一个输出只保留最新结论
    try:
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_output_feedback_src_ref "
                   "ON output_feedback (source, ref)")
    except Exception:
        pass                      # 已存在
    _TABLE_READY = True


def save_feedback(source: str, ref: str, action: str) -> dict:
    """记一条标记（幂等覆盖）。非法 source/action **直接拒绝（不猜）**。"""
    _ensure()
    if source not in SOURCE_ALLOWED:
        return {"ok": False, "error": f"未知 source: {source}"}
    if action not in ACTION_ALLOWED:
        return {"ok": False, "error": f"未知 action: {action}"}
    ref = str(ref or "").strip()[:120]
    if not ref:
        return {"ok": False, "error": "ref 为空"}
    from app.flash.rules import beijing_now
    day = beijing_now().strftime("%Y-%m-%d")
    db.upsert("output_feedback",
              {"date": day, "source": source, "ref": ref, "action": action},
              conflict_columns=["source", "ref"])
    return {"ok": True, "source": source, "ref": ref, "action": action}


def feedback_summary(days: int = 30) -> dict:
    """近 N 天各来源的标记分布 —— 周复盘「提示 → 标记」小节的数据源。"""
    _ensure()
    from datetime import timedelta
    from app.flash.rules import beijing_now
    start = (beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetch("SELECT source, action, COUNT(*) AS n FROM output_feedback "
                    "WHERE date >= %s GROUP BY source, action ORDER BY source, action",
                    (start,)) or []
    return {"days": days, "items": [dict(r) for r in rows]}


@router.post("")
def post_feedback(source: str = Query(...), ref: str = Query(...),
                  action: str = Query(...)):
    """记一条「这条输出对我有没有影响」的标记（C2）。幂等覆盖，可改选。"""
    return save_feedback(source, ref, action)


@router.get("/summary")
def get_feedback_summary(days: int = Query(30, ge=1, le=365)):
    """近 N 天标记分布（周复盘消费）。"""
    return feedback_summary(days)
