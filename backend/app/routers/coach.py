"""
================================================================================
【文件作用】交易员教练 API（W1 补漏，2026-09-13）
================================================================================

URL 前缀 /api/coach（JWT 保护，按用户登录态）：
  GET  /api/coach/alerts                    → 建议历史（前端教练 Tab 数据源）
  POST /api/coach/alerts/{id}/execute       → 执行回写（yes/no + 放弃理由）
  GET  /api/coach/consistency               → 执行一致性度量（教练 KPI）
  GET  /api/coach/abandon-reasons           → 放弃理由清单（周报复盘用）
  GET  /api/coach/plans/execution-rate      → 预承诺执行率（模拟盘剧本闭环 KPI）
  GET  /api/coach/plans                     → 批量查询剧本（A 前端持仓展示）
  POST /api/coach/plans/{id}/abandon        → 主动放弃剧本（A 执行回写）

★ 背景：W1 的企微警报文案是「确认 / 放弃（放弃须填理由）」，但此前没有任何
  回写入口 → `audit.write_back_execution` 成死代码、执行一致性永远为 0。
  本路由补上闭环；前端教练 Tab（W2）以此为数据源。
★ W2 模拟盘完整接入：B-3 新增 `/plans/execution-rate`，度量"开仓写死的退出
  剧本有多少被实际执行"；A-4 新增 `/plans` + `/plans/{id}/abandon`，在模拟盘
  持仓行展示剧本并支持主动放弃回写。
================================================================================
"""

from fastapi import APIRouter, Body, Depends, HTTPException

from app.auth import get_current_user
from app.coach import audit
from app.database import db

router = APIRouter()


@router.get("/alerts")
def get_alerts(limit: int = 50, user: dict = Depends(get_current_user)):
    """教练建议历史（最近 N 条，含执行状态/放弃理由/T+5 结果）。"""
    return {"data": audit.recent_alerts(limit=min(max(limit, 1), 200))}


@router.post("/alerts/{alert_id}/execute")
def execute_alert(alert_id: int, payload: dict = Body(...),
                  user: dict = Depends(get_current_user)):
    """执行回写：`{executed: 'yes'|'no', reason?}`。

    ★ 放弃（no）必须填理由——这是治「再等等看」的良药，理由回写供周报复盘。
    """
    executed = str(payload.get("executed") or "").strip().lower()
    reason = str(payload.get("reason") or "").strip()
    if executed not in ("yes", "no"):
        raise HTTPException(400, "executed 必须是 yes 或 no")
    if executed == "no" and not reason:
        raise HTTPException(400, "放弃必须填写理由（回写供周报复盘）")
    if not db.fetch_one("SELECT id FROM coach_alerts WHERE id=%s", (alert_id,)):
        raise HTTPException(404, f"建议 {alert_id} 不存在")
    if not audit.write_back_execution(alert_id, executed, reason):
        raise HTTPException(500, "回写失败（数据库异常）")
    return {"success": True, "alert_id": alert_id, "executed": executed}


@router.get("/consistency")
def get_consistency(days: int = 30, day: str = None,
                    user: dict = Depends(get_current_user)):
    """执行一致性度量（执行率分母=已决策，未响应单列不进分母）。

    `day`（YYYY-MM-DD，可选）：**只统计该日** —— 供盘后「今日执行回看」。
    ⚠️ 不能用 `days=1` 顶替"今天"（那是"近 1 天"，含昨天）。
    """
    d = (day or "").strip()[:10] or None
    return audit.execution_consistency(days=min(max(days, 1), 365), day=d)


@router.get("/plans/execution-rate")
def get_plan_execution_rate(days: int = 30, user: dict = Depends(get_current_user)):
    """预承诺执行率（W2 模拟盘闭环）：按剧本离场的平仓数 / 已平仓剧本数。"""
    return audit.plan_execution_rate(days=min(max(days, 1), 365))


@router.get("/plans")
def get_plans(position_ids: str = "", user: dict = Depends(get_current_user)):
    """批量查询 coach_plans（A 前端持仓展示）：position_ids 逗号分隔。"""
    ids = [int(x) for x in position_ids.split(",") if x.strip().isdigit()]
    return {"data": audit.get_plans(position_ids=ids)}


@router.post("/plans/{plan_id}/abandon")
def abandon_plan(plan_id: int, payload: dict = Body(...),
                 user: dict = Depends(get_current_user)):
    """主动放弃剧本（A 执行回写）：{reason: '...'}，理由必填。"""
    reason = str(payload.get("reason") or "").strip()
    if not reason:
        raise HTTPException(400, "放弃剧本必须填写理由")
    if not audit.abandon_plan(plan_id, reason):
        raise HTTPException(500, "放弃失败（剧本不存在或已关闭）")
    return {"success": True, "plan_id": plan_id}


@router.get("/abandon-reasons")
def get_abandon_reasons(limit: int = 20, user: dict = Depends(get_current_user)):
    """放弃理由清单（高频理由 = 用户最易失守的纪律点，周报复盘用）。"""
    return {"data": audit.abandon_reasons(limit=min(max(limit, 1), 100))}
