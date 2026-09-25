"""
================================================================================
【文件作用】用户数据路由（自选股/交易计划/持仓 → 数据库，按用户隔离）
================================================================================

URL 前缀 /api/user：
  GET    /api/user/watchlist          → 获取自选股列表
  POST   /api/user/watchlist          → 添加/更新自选股
  DELETE /api/user/watchlist/{code}   → 删除自选股

  GET    /api/user/plans              → 获取交易计划列表
  POST   /api/user/plans              → 添加交易计划
  PUT    /api/user/plans/{id}         → 更新交易计划
  DELETE /api/user/plans/{id}         → 删除交易计划

  GET    /api/user/portfolio          → 获取持仓列表
  POST   /api/user/portfolio          → 添加/更新持仓
  DELETE /api/user/portfolio/{code}   → 删除持仓

  POST   /api/user/sync               → 批量同步（全量覆盖）

所有接口需要登录（JWT Token），数据按 user_id 隔离。
================================================================================
"""

import json
from datetime import datetime
from fastapi import APIRouter, HTTPException, Body, Depends
from typing import Optional

from app.database import db
from app.auth import get_current_user

router = APIRouter()


# ================================================================
#  自选股
# ================================================================

@router.get("/watchlist")
def get_watchlist(user: dict = Depends(get_current_user)):
    """获取自选股列表"""
    rows = db.fetch(
        "SELECT * FROM user_watchlist WHERE user_id = %s ORDER BY created_at DESC",
        (user["user_id"],)
    )
    items = []
    for r in rows:
        items.append({
            "code": r["code"],
            "name": r["name"],
            "target_price": r.get("target_price"),
            "note": r.get("note", ""),
            "created_at": r.get("created_at", ""),
        })
    return {"data": items}


@router.post("/watchlist")
def upsert_watchlist(item: dict = Body(...), user: dict = Depends(get_current_user)):
    """添加或更新自选股"""
    code = item.get("code")
    if not code:
        raise HTTPException(400, "缺少 code 字段")
    
    db.upsert("user_watchlist", {
        "user_id": user["user_id"],
        "code": code,
        "name": item.get("name", code),
        "target_price": item.get("target_price"),
        "note": item.get("note", ""),
    }, conflict_columns=["user_id", "code"])

    # ★ 2026-09-25：写入即失效持仓雷达缓存（新加持仓立即可见于 /batch/portfolio-radar）
    try:
        from app import portfolio_radar
        portfolio_radar.invalidate_holdings()
    except Exception:
        pass
    return {"success": True}


@router.delete("/watchlist/{code}")
def delete_watchlist(code: str, user: dict = Depends(get_current_user)):
    """删除自选股"""
    db.execute(
        "DELETE FROM user_watchlist WHERE user_id = %s AND code = %s",
        (user["user_id"], code)
    )
    return {"success": True}


# ================================================================
#  交易计划
# ================================================================

# ★ 2026-09-25（交易方法论落地计划 A4）：结构化触发条件的新列。
#   ⚠️ 为什么必须在这里 ALTER：线上 `user_trade_plans` 早已存在，而
#     schema.sql 用的是 `CREATE TABLE IF NOT EXISTS` —— **它不会给已存在的表加列**
#     ⇒ 只改 schema.sql 的结果是"新库有、老库没有"，线上永远缺列（读 `r.get()` 恒为 None，
#       功能静默失效，且没有任何报错）。⇒ 与 `market_emotion_daily` 同款处理：建表之外补一次。
_PLAN_COLS_READY = False


def _ensure_plan_columns() -> None:
    """幂等补齐 A4 的三列。PG/SQLite 对「列已存在」报错文案不同，故统一忽略异常。"""
    global _PLAN_COLS_READY
    if _PLAN_COLS_READY:
        return
    for col, ddl in (("plan_type", "TEXT DEFAULT 'trial'"),
                     ("trigger_high_open", "REAL"),
                     ("trigger_volume_break", "REAL")):
        try:
            db.execute(f"ALTER TABLE user_trade_plans ADD COLUMN {col} {ddl}")
        except Exception:
            pass                    # 列已存在 ⇒ 忽略（幂等）
    _PLAN_COLS_READY = True

@router.get("/plans")
def get_plans(user: dict = Depends(get_current_user)):
    """获取交易计划列表"""
    _ensure_plan_columns()
    rows = db.fetch(
        "SELECT * FROM user_trade_plans WHERE user_id = %s ORDER BY created_at DESC",
        (user["user_id"],)
    )
    items = []
    for r in rows:
        items.append({
            "id": r["id"],
            "code": r["code"],
            "name": r["name"],
            "buy_price": r.get("buy_price"),
            "stop_loss": r.get("stop_loss"),
            "target": r.get("target"),
            "reason": r.get("reason", ""),
            "expected": r.get("expected", ""),
            "status": r.get("status", "waiting"),
            "hit_at": r.get("hit_at"),
            "created_at": r.get("created_at", ""),
            # ★ A4：结构化触发条件（老行为 NULL ⇒ 前端按"未设置"处理，不影响既有计划）
            "plan_type": r.get("plan_type") or "trial",
            "trigger_high_open": r.get("trigger_high_open"),
            "trigger_volume_break": r.get("trigger_volume_break"),
        })
    return {"data": items}


@router.post("/plans")
def upsert_plan(item: dict = Body(...), user: dict = Depends(get_current_user)):
    """添加或更新交易计划"""
    _ensure_plan_columns()
    plan_id = item.get("id")
    if not plan_id:
        plan_id = f"tp_{int(datetime.now().timestamp() * 1000)}"

    db.upsert("user_trade_plans", {
        "id": plan_id,
        "user_id": user["user_id"],
        "code": item.get("code", ""),
        "name": item.get("name", ""),
        "buy_price": item.get("buy_price", 0),
        "stop_loss": item.get("stop_loss", 0),
        "target": item.get("target", 0),
        "reason": item.get("reason", ""),
        "expected": item.get("expected", ""),
        "status": item.get("status", "waiting"),
        "hit_at": item.get("hit_at"),
        # ★ A4 结构化触发条件（缺省 None ⇒ 该条件不参与触发判断）
        "plan_type": item.get("plan_type") or "trial",
        "trigger_high_open": item.get("trigger_high_open"),
        "trigger_volume_break": item.get("trigger_volume_break"),
    }, conflict_columns=["id"])
    return {"success": True, "id": plan_id}


@router.put("/plans/{plan_id}")
def update_plan(plan_id: str, item: dict = Body(...), user: dict = Depends(get_current_user)):
    """更新交易计划"""
    _ensure_plan_columns()      # ★ A4：三处写入口（POST/PUT/GET）都保证列在，避免半新半旧
    # 构建更新字段
    updates = {k: v for k, v in item.items() if v is not None}
    updates["id"] = plan_id
    updates["user_id"] = user["user_id"]
    db.upsert("user_trade_plans", updates, conflict_columns=["id"])
    return {"success": True}


@router.delete("/plans/{plan_id}")
def delete_plan(plan_id: str, user: dict = Depends(get_current_user)):
    """删除交易计划"""
    db.execute(
        "DELETE FROM user_trade_plans WHERE id = %s AND user_id = %s",
        (plan_id, user["user_id"])
    )
    return {"success": True}


# ================================================================
#  持仓
# ================================================================

@router.get("/portfolio")
def get_portfolio(user: dict = Depends(get_current_user)):
    """获取持仓列表"""
    rows = db.fetch(
        "SELECT * FROM user_portfolio WHERE user_id = %s ORDER BY created_at DESC",
        (user["user_id"],)
    )
    items = []
    for r in rows:
        items.append({
            "code": r["code"],
            "name": r["name"],
            "shares": r.get("shares", 0),
            "cost": r.get("cost", 0),
            "note": r.get("note", ""),
            "created_at": r.get("created_at", ""),
        })
    return {"data": items}


@router.post("/portfolio")
def upsert_portfolio(item: dict = Body(...), user: dict = Depends(get_current_user)):
    """添加或更新持仓"""
    code = item.get("code")
    if not code:
        raise HTTPException(400, "缺少 code 字段")
    
    db.upsert("user_portfolio", {
        "user_id": user["user_id"],
        "code": code,
        "name": item.get("name", code),
        "shares": item.get("shares", 0),
        "cost": item.get("cost", 0),
        "note": item.get("note", ""),
    }, conflict_columns=["user_id", "code"])
    return {"success": True}


@router.delete("/portfolio/{code}")
def delete_portfolio(code: str, user: dict = Depends(get_current_user)):
    """删除持仓"""
    db.execute(
        "DELETE FROM user_portfolio WHERE user_id = %s AND code = %s",
        (user["user_id"], code)
    )
    return {"success": True}


@router.get("/position-sizing")
def get_position_sizing(user: dict = Depends(get_current_user)):
    """持仓仓位建议（W1.5，数据联动）：regime + 宏观 + 情绪温度计 + 宽度 +
    主力筹码/阶段 → 总仓位上限 + 每只建议仓位。替代前端写死的 calcPositionSize。"""
    rows = db.fetch(
        "SELECT code FROM user_portfolio WHERE user_id = %s ORDER BY created_at ASC",
        (user["user_id"],))
    codes, seen = [], set()
    for r in rows or []:
        c = str(r.get("code") or "").strip()
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    from app.coach.position_sizing import position_sizing
    return position_sizing(codes)


# ================================================================
#  批量同步（全量覆盖，用于初次迁移或设备间强制同步）
# ================================================================

@router.post("/sync")
def batch_sync(data: dict = Body(...), user: dict = Depends(get_current_user)):
    """
    批量同步用户数据。
    请求体：{
      "watchlist": [...],    // 可选
      "plans": [...],        // 可选
      "portfolio": [...]     // 可选
    }
    """
    uid = user["user_id"]
    result = {}
    
    # 自选股
    if "watchlist" in data:
        db.execute("DELETE FROM user_watchlist WHERE user_id = %s", (uid,))
        for item in data["watchlist"]:
            db.upsert("user_watchlist", {
                "user_id": uid,
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "target_price": item.get("target_price"),
                "note": item.get("note", ""),
            }, conflict_columns=["user_id", "code"])
        result["watchlist"] = len(data["watchlist"])
    
    # 交易计划
    if "plans" in data:
        db.execute("DELETE FROM user_trade_plans WHERE user_id = %s", (uid,))
        for item in data["plans"]:
            db.upsert("user_trade_plans", {
                "id": item.get("id", f"tp_{int(datetime.now().timestamp() * 1000)}"),
                "user_id": uid,
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "buy_price": item.get("buy_price", 0),
                "stop_loss": item.get("stop_loss", 0),
                "target": item.get("target", 0),
                "reason": item.get("reason", ""),
                "expected": item.get("expected", ""),
                "status": item.get("status", "waiting"),
                "hit_at": item.get("hit_at"),
            }, conflict_columns=["id"])
        result["plans"] = len(data["plans"])
    
    # 持仓
    if "portfolio" in data:
        db.execute("DELETE FROM user_portfolio WHERE user_id = %s", (uid,))
        for item in data["portfolio"]:
            db.upsert("user_portfolio", {
                "user_id": uid,
                "code": item.get("code", ""),
                "name": item.get("name", ""),
                "shares": item.get("shares", 0),
                "cost": item.get("cost", 0),
                "note": item.get("note", ""),
            }, conflict_columns=["user_id", "code"])
        result["portfolio"] = len(data["portfolio"])
    # ★ 2026-09-25：写入即失效持仓雷达缓存（新加持仓立即可见于 /batch/portfolio-radar）
    try:
        from app import portfolio_radar
        portfolio_radar.invalidate_holdings()
    except Exception:
        pass
    
    return {"success": True, "synced": result}
