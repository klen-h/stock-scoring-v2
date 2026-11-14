# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】做T API（2026-11-15）
================================================================================
URL 前缀 /api/t-trade（JWT 保护）：

  GET  /api/t-trade/plans?date=&only_feasible=1   → 当日（或最近一批）预案清单
  GET  /api/t-trade/plan?code=000048              → 单只预案（库里有读库，没有则实时算）
  GET  /api/t-trade/explain?code=000048           → 实时解释"为什么能做/不能做"
  GET  /api/t-trade/summary?date=                 → 当日汇总（预案 + 未平 + 已平 + 盈亏）
  POST /api/t-trade/scan                          → 手动补跑全市场扫描（后台任务）

【数据流（理解这个才能看懂接口）】
  盘后 15:40 `t_trade_scan_loop` → `scan_market()` 扫全市场 → 只把 `feasible=True`
  的预案写进 `t_plans`；盘中 `t_trade_loop` 60s 读这些预案 + 腾讯内存价判断触发。
  ⇒ 所以 `plans` 里的"日期"通常是**上一交易日收盘**算的，接口会把 `plan_date`
    原样返回，**前端必须显示它是哪天的**（不能假装是今天盘中算的）。
================================================================================
"""
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from app import t_trade_exec as TX
from app.auth import get_current_user

router = APIRouter()


@router.get("/plans")
def get_plans(date: str = None, only_feasible: bool = True,
              watch_only: bool = False, limit: int = 60,
              user: dict = Depends(get_current_user)):
    """当日（或最近一批）做T 预案清单，按 `space_atr_mult` 降序（空间越足越靠前）。

    `watch_only=1` ⇒ 只返回**自选+持仓**里可做的（工作台卡用这个，
      否则会给用户糊 300+ 只他根本不关心的票）。
    """
    rows = TX.list_plans(date, feasible_only=only_feasible)
    if watch_only:
        w = TX.watch_codes()
        rows = [r for r in rows if str(r.get("code") or "") in w]
    rows.sort(key=lambda x: -(x.get("space_atr_mult") or 0))
    lim = min(max(limit, 1), 500)
    return {"data": rows[:lim], "total": len(rows), "watch_only": watch_only}


@router.get("/plan")
def get_plan(code: str = "", date: str = None,
             user: dict = Depends(get_current_user)):
    """单只预案：优先读库（盘后生成的），库里没有就**实时算一次**（含不可行原因）。"""
    c = str(code or "").strip()
    if not c:
        raise HTTPException(400, "缺少 code")
    p = TX.load_plan(c, date)
    if p:
        return {"data": p, "source": "db"}
    return {"data": TX.explain(c, date), "source": "live"}


@router.get("/explain")
def get_explain(code: str = "",
                user: dict = Depends(get_current_user)):
    """实时解释某只票**此刻**能不能做T —— 用于回答"为什么它不在清单里"。"""
    c = str(code or "").strip()
    if not c:
        raise HTTPException(400, "缺少 code")
    return {"data": TX.explain(c)}


@router.get("/summary")
def get_summary(date: str = None, user: dict = Depends(get_current_user)):
    """当日做T 汇总（预案 + 未平 + 已平 + 模拟盈亏）。"""
    return TX.daily_summary(date)


@router.post("/scan")
def trigger_scan(background: BackgroundTasks, date: str = None,
                 user: dict = Depends(get_current_user)):
    """手动补跑全市场扫描。

    ⚠️ 同步跑要 **72 秒**（1025 只 × 算 ATR/位）⇒ 走 BackgroundTasks 立即返回，
      结果看日志 `[t-trade] scan done`。**不要**在这里同步阻塞（会让 HTTP 超时）。
    """
    background.add_task(TX.scan_market, date, 120, 200, True)
    return {"success": True, "note": "后台扫描已启动（约 1~2 分钟），完成后预案自动落库"}
