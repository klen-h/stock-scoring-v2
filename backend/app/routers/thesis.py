# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】推演登记 API（2026-11-22，用户："开始做 P0"）
================================================================================

URL 前缀 /api/thesis（JWT 保护）：

  POST /api/thesis/scenarios                 登记一份推演
  GET  /api/thesis/scenarios?active_only=1   列表（active_only 只返回有效期内的）
  GET  /api/thesis/scenarios/{id}/daily      逐日对照历史
  POST /api/thesis/scenarios/{id}/review     手动重跑某天（补跑/盘中异动）
  POST /api/thesis/scenarios/{id}/archive    归档

【与观察器 / 决策链的关系】纯复盘：写入两张自有表，**不改评分、不进闸门、不碰模拟盘**。
【LLM 成本】只有 `review` 与日批 `task_thesis_review` 会调 LLM（每天 1 次）。
================================================================================
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app import thesis_scenarios as ts
from app.auth import get_current_user

router = APIRouter()
_BJ = timezone(timedelta(hours=8))


def _default_day() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


@router.post("/scenarios")
def create_scenario(payload: Dict = Body(...),
                    user: dict = Depends(get_current_user)) -> Dict:
    """登记推演。校验失败返回 400（宁可拒绝，也不写脏数据）。"""
    r = ts.create(payload or {})
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "登记失败")
    return r


@router.get("/scenarios")
def list_scenarios(active_only: bool = Query(False),
                   date: Optional[str] = Query(None, description="YYYY-MM-DD"),
                   user: dict = Depends(get_current_user)) -> Dict:
    return {"data": ts.list_theses(active_only=active_only, day=date)}


@router.get("/scenarios/{tid}/daily")
def scenario_daily(tid: str, limit: int = Query(30, ge=1, le=200),
                   user: dict = Depends(get_current_user)) -> Dict:
    t = ts.get_thesis(tid)
    if not t:
        raise HTTPException(404, "推演不存在")
    return {"thesis": t, "data": ts.daily_rows(tid, limit),
            "metrics": ts.METRIC_LABELS}


@router.post("/scenarios/{tid}/review")
def scenario_review(tid: str,
                    date: Optional[str] = Query(None, description="YYYY-MM-DD"),
                    user: dict = Depends(get_current_user)) -> Dict:
    """手动重跑某天的对照 + LLM 修正（日批漏跑或盘中大异动时用）。

    ⚠️ 会调用 1 次 LLM；确定性对照即使 LLM 失败也会落库。
    """
    t = ts.get_thesis(tid)
    if not t:
        raise HTTPException(404, "推演不存在")
    day = str(date or _default_day())[:10]
    ev = ts.evaluate_day(t, day)
    rv = ts.llm_review(t, day, ev)
    saved = ts.save_daily(tid, day, ev, rv)
    pushed = ts.push(t, day, ev, rv) if saved else False
    return {"ok": saved, "date": day, "checked": ev["checked"],
            "n_pass": ev["n_pass"], "n_fail": ev["n_fail"],
            "n_nodata": ev["n_nodata"],
            "llm": rv, "pushed": pushed}


@router.post("/scenarios/{tid}/archive")
def scenario_archive(tid: str, user: dict = Depends(get_current_user)) -> Dict:
    if not ts.get_thesis(tid):
        raise HTTPException(404, "推演不存在")
    return {"ok": ts.archive(tid)}


@router.get("/metrics")
def list_metrics(user: dict = Depends(get_current_user)) -> Dict:
    """可用验证项清单（前端表单用，避免把 key 写死在两处）。"""
    return {"data": [{"key": k, "label": v} for k, v in ts.METRIC_LABELS.items()]}
