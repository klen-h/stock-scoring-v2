# -*- coding: utf-8 -*-
"""
「缩量调整→放量启动」形态 · API

URL 前缀 /api/pattern：
  GET    /api/pattern/scan             → 最新扫描结果（读落库快照）
  GET    /api/pattern/highlight-codes  → 当日命中的 code 集合（前端醒目标注用，轻量）
  GET    /api/pattern/watch            → 观察清单（含后续变化 T+N）
  POST   /api/pattern/watch            → 加入观察 {code, note?}
  PATCH  /api/pattern/watch/{code}     → 更新 {status?, note?}
  DELETE /api/pattern/watch/{code}     → 移除
  GET    /api/pattern/config           → 当前参数
  PUT    /api/pattern/config           → 重设参数（要求④：未来重新设定回测条件）
"""
from fastapi import APIRouter, Body, Depends, HTTPException

from app import pattern_launch as pl
from app.auth import get_current_user

router = APIRouter()


@router.get("/scan")
def scan(date: str = None):
    """最新扫描结果（读落库；无落库时现场扫一次兜底）。"""
    data = pl.get_scan(date)
    if not data["items"]:
        # 兜底：日批尚未跑过（或首日）→ 现场扫
        result = pl.scan_latest(date)
        if result.get("available"):
            data = {"date": result["date"], "n": result["n"], "items": result["items"]}
    return data


@router.get("/highlight-codes")
def highlight_codes():
    return pl.highlight_codes()


@router.get("/watch")
def watch():
    return {"items": pl.get_watch()}


@router.post("/watch")
def watch_add(payload: dict = Body(...), _user: dict = Depends(get_current_user)):
    code = (payload.get("code") or "").strip()
    if not code:
        raise HTTPException(400, "缺少 code")
    r = pl.watch_add(code, note=payload.get("note") or "", name=payload.get("name") or "")
    if not r.get("ok"):
        raise HTTPException(400, r.get("error") or "加入观察失败")
    return r


@router.patch("/watch/{code}")
def watch_update(code: str, payload: dict = Body(...), _user: dict = Depends(get_current_user)):
    pl.watch_update(code, status=payload.get("status"), note=payload.get("note"))
    return {"ok": True}


@router.delete("/watch/{code}")
def watch_remove(code: str, _user: dict = Depends(get_current_user)):
    pl.watch_remove(code)
    return {"ok": True}


@router.get("/config")
def config_get():
    return pl.get_config()


@router.put("/config")
def config_set(payload: dict = Body(...), _user: dict = Depends(get_current_user)):
    return pl.set_config(payload)
