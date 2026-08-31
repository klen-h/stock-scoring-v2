"""
================================================================================
【文件作用】模拟盘 API（/api/paper）：持仓查看 / 手动转仓 / 手动平仓 / 统计 / 账户
================================================================================
"""

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.database import db
from app.flash import rules
from app.strategies import paper_trading

router = APIRouter()


@router.get("/positions")
def list_positions(status: str = None):
    """持仓列表（pending / holding / closed / cancelled），默认全部。"""
    sql = "SELECT * FROM paper_positions"
    params = []
    if status:
        sql += " WHERE status = %s"
        params.append(status)
    sql += " ORDER BY id DESC"
    rows = db.fetch(sql, tuple(params)) or []
    return {"data": rows}


class ManualIngest(BaseModel):
    strategy_name: str
    code: str
    signal_date: str = None


@router.post("/positions/manual")
def manual_ingest(body: ManualIngest):
    """手动把某战法信号的某只股票转为模拟持仓（pending）。"""
    sd = body.signal_date
    if not sd:
        row = db.fetch_one(
            "SELECT scan_date FROM strategy_results WHERE strategy_name=%s "
            "ORDER BY scan_date DESC LIMIT 1", (body.strategy_name,))
        sd = (row or {}).get("scan_date") or rules.beijing_now().strftime("%Y-%m-%d")
    row = db.fetch_one(
        "SELECT results_json FROM strategy_results WHERE strategy_name=%s AND scan_date=%s",
        (body.strategy_name, sd))
    if not row:
        raise HTTPException(status_code=404, detail="该战法该日期无扫描结果")
    try:
        results = json.loads(row["results_json"]) or []
    except (json.JSONDecodeError, TypeError):
        raise HTTPException(status_code=500, detail="扫描结果解析失败")
    sig = next((s for s in results if str(s.get("code")) == body.code), None)
    if not sig:
        raise HTTPException(status_code=404, detail=f"扫描结果中无 {body.code}")
    dup = db.fetch_one(
        "SELECT id FROM paper_positions WHERE strategy_name=%s AND code=%s AND signal_date=%s",
        (body.strategy_name, body.code, sd))
    if dup:
        raise HTTPException(status_code=409, detail="该信号已在模拟池中")
    db.execute(
        "INSERT INTO paper_positions (code, name, strategy_name, signal_date, entry_price, "
        "stop_loss, target_price, status, confirmation_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)",
        (body.code, sig.get("name") or body.code, body.strategy_name, sd,
         sig.get("entry_price"), sig.get("stop_loss"), sig.get("target_price"),
         json.dumps(sig, ensure_ascii=False), paper_trading._now_iso()))
    return {"ok": True, "message": f"{body.code} 已入模拟池（pending）"}


@router.post("/positions/{pid}/close")
def close_position(pid: int):
    """手动平仓（用最新收盘/现价）。"""
    row = db.fetch_one("SELECT * FROM paper_positions WHERE id=%s", (pid,))
    if not row or row["status"] != "holding":
        raise HTTPException(status_code=404, detail="持仓不存在或已平仓")
    price = paper_trading._latest_close(row["code"])
    if price <= 0:
        raise HTTPException(status_code=409, detail="无有效平仓价")
    paper_trading._close_position(row, price, "manual")
    return {"ok": True, "message": f"{row['code']} 已手动平仓 @ {price}"}


@router.delete("/positions/{pid}")
def cancel_position(pid: int):
    """取消 pending（未确认仓位）。"""
    row = db.fetch_one("SELECT id, status FROM paper_positions WHERE id=%s", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="仓位不存在")
    if row["status"] != "pending":
        raise HTTPException(status_code=409, detail="仅 pending 状态可取消")
    db.execute("UPDATE paper_positions SET status='cancelled', exit_reason='manual_cancel', "
               "closed_at=%s WHERE id=%s", (paper_trading._now_iso(), pid))
    return {"ok": True, "message": "已取消"}


@router.get("/stats")
def stats():
    """分战法已平仓胜率统计。"""
    return paper_trading.paper_stats()


@router.get("/account")
def account():
    return paper_trading.get_account()


@router.post("/whitelist/refresh")
def whitelist_refresh(dry_run: bool = True):
    """模拟盘样本充足后的白名单自动刷新建议（默认 dry_run 只给建议）。"""
    return paper_trading.auto_refresh_whitelist()
