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


@router.post("/parse")
def parse_draft(payload: Dict = Body(...),
                user: dict = Depends(get_current_user)) -> Dict:
    """把自然语言推演解析成登记草案 —— **只解析，不落库**。

    ★ 2026-11-22（用户："加入一个导入功能…自动写入，我到时审阅"）：
      刻意**不直接写库**。解析结果回填表单，用户审阅/改数字后自己点「登记」，
      这样 LLM 认错指标或编造阈值时人工一定看得见。
    ⚠️ 会调 1 次 LLM（约十几秒），前端给 60s 超时。
    """
    r = ts.parse_text(str((payload or {}).get("text") or ""))
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "解析失败")
    return r


# ══════════════════════════════════════════════════════════════════════════
#  动作协议卡（★ 2026-11-28）：回答"怎么办"，与情景卡"会怎样"并列
# ══════════════════════════════════════════════════════════════════════════
# 【为什么单列】用户复盘 11-27："12:32 企微就收到了平准基金命中，到开盘还有
#   28 分钟，我却不知所措" —— 不是信息失败，是**没有卡可翻**。
#   情景卡的 checks 是事后验证，缺的正是"触发后第一步做什么"。
@router.post("/protocols")
def create_protocol(payload: Dict = Body(...),
                    user: dict = Depends(get_current_user)) -> Dict:
    """登记动作协议卡。缺 first_move 或 trigger.keywords 直接拒（不可执行的卡等于没有）。"""
    from app import action_protocol as ap
    r = ap.create(payload or {})
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "登记失败")
    return r


@router.get("/protocols")
def list_protocols(active_only: bool = Query(False),
                   user: dict = Depends(get_current_user)) -> Dict:
    from app import action_protocol as ap
    return {"data": ap.list_protocols(active_only=active_only)}


@router.get("/protocols/match")
def match_protocol(text: str = Query(..., description="待匹配文本（快讯/通稿）"),
                   user: dict = Depends(get_current_user)) -> Dict:
    """**纯匹配**：给一段文本，返回命中的协议卡。不落库、不推送、不调 LLM。

    用于人工核对"这条通稿到底会翻哪张卡"。
    """
    from app import action_protocol as ap
    hits = ap.match_items([{"text": text, "time": ""}])
    return {"ok": True, "n": len(hits), "data": hits}


@router.get("/protocols/news")
def protocol_news(day: str = Query(None, description="YYYY-MM-DD，缺省=今天"),
                  limit: int = Query(60, ge=1, le=300),
                  user: dict = Depends(get_current_user)) -> Dict:
    """今日**重要快讯留账**：哪些有协议可翻、哪些是**缺口**。

    ★ 2026-11-28（用户："动作协议也改为自动收集快讯重要内容"）：
      此前协议卡只处理关键词命中的快讯，没命中的重要快讯直接丢掉 ⇒
      用户看不到"今天有大事，但我没有卡可翻"。

    口径：`important=1` 或热度 ∈ 爆/沸/热。`unmatched` 就是最该补卡的地方。
    """
    from app import action_protocol as ap
    return ap.news_pool(day=day, limit=limit)


@router.post("/protocols/{pid}/archive")
def archive_protocol(pid: str, user: dict = Depends(get_current_user)) -> Dict:
    from app import action_protocol as ap
    if not ap.get(pid):
        raise HTTPException(404, "协议不存在")
    return {"ok": ap.archive(pid)}


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


@router.get("/scenarios/{tid}/invalidation")
def scenario_invalidation(tid: str,
                          lookback: int = Query(5, ge=1, le=30),
                          user: dict = Depends(get_current_user)) -> Dict:
    """**失效检测**：连续多日高失败率 ⇒ 建议复核尺子。

    ★ 2026-11-25（用户贴的五层架构 L4 里唯一真缺的一块）：
      已有「换尺子」是人工动作（`PUT /checks`），但人不会天天问"我这把尺子还准吗"。
      本接口只回答"**该不该问**"，**不改任何东西** ——
      换不换取决于"这次不一样在哪"，那是人的判断，规则写不出来。
    """
    if not ts.get_thesis(tid):
        raise HTTPException(404, "推演不存在")
    return ts.invalidation_check(tid, lookback=lookback)


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
    ev["invalidation"] = ts.invalidation_check(tid)
    rv = ts.llm_review(t, day, ev)
    saved = ts.save_daily(tid, day, ev, rv, t)
    pushed = ts.push(t, day, ev, rv) if saved else False
    return {"ok": saved, "date": day, "checked": ev["checked"],
            "n_pass": ev["n_pass"], "n_fail": ev["n_fail"],
            "n_nodata": ev["n_nodata"],
            "invalidation": ev.get("invalidation"),
            "llm": rv, "pushed": pushed}


@router.post("/scenarios/{tid}/archive")
def scenario_archive(tid: str, user: dict = Depends(get_current_user)) -> Dict:
    if not ts.get_thesis(tid):
        raise HTTPException(404, "推演不存在")
    return {"ok": ts.archive(tid)}


@router.put("/scenarios/{tid}/checks")
def scenario_update_checks(tid: str, payload: Dict = Body(...),
                           user: dict = Depends(get_current_user)) -> Dict:
    """替换验证项 —— 不动情景/有效期，**历史对照全部保留**。

    ★ 2026-11-23（用户："行情性质变了就要立刻换框架"）：换尺子不该重建推演，
      重建会换 id，`thesis_daily` 的历史对照就断了。
    """
    r = ts.update_checks(tid, (payload or {}).get("checks"))
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "更新失败")
    return r


@router.get("/metrics")
def list_metrics(user: dict = Depends(get_current_user)) -> Dict:
    """可用验证项清单（前端表单用，避免把 key 写死在两处）。"""
    return {"data": [{"key": k, "label": v} for k, v in ts.METRIC_LABELS.items()]}
