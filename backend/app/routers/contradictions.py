"""
矛盾扫描引擎路由

URL 前缀 /api/contradictions

接口：
  GET  /api/contradictions            → 今日/指定日期矛盾列表
  GET  /api/contradictions/summary    → 某日矛盾统计摘要
  GET  /api/contradictions/report     → 某日完整报告 markdown
  POST /api/contradictions/scan       → 手动触发扫描（幂等覆盖当日）
  POST /api/contradictions/report     → 手动生成报告
  POST /api/contradictions/resolve    → 标记某条矛盾已兑现/失效
"""

from fastapi import APIRouter, Query
from typing import Optional

from app.contradictions.scanner import scan_all
from app.contradictions.report import generate_report, run_report
from app.contradictions import store

router = APIRouter()


@router.get("")
def list_contradictions(
    date: Optional[str] = Query(None, description="日期 YYYY-MM-DD，缺省最新"),
    level: Optional[str] = Query(None, description="L1/L2/L3"),
    severity: Optional[str] = Query(None, description="minor/obvious/severe"),
):
    """查询矛盾列表。date 缺省返回库中最新日期。"""
    target = date or store.load_latest_date()
    items = store.load_contradictions(date=target, level=level, severity=severity)
    return {"date": target, "total": len(items), "data": items}


@router.get("/summary")
def summary(date: Optional[str] = Query(None)):
    """某日矛盾统计摘要 + 严重级卡片。"""
    target = date or store.load_latest_date() or store._today()
    items = store.load_contradictions(date=target)
    severe = [i for i in items if i.get("severity") == "severe"]
    obvious = [i for i in items if i.get("severity") == "obvious"]
    minor = [i for i in items if i.get("severity") == "minor"]
    return {
        "date": target,
        "total": len(items),
        "breakdown": {
            "severe": len(severe),
            "obvious": len(obvious),
            "minor": len(minor),
        },
        "severe_cards": severe[:3],
    }


@router.get("/report")
def get_report(date: Optional[str] = Query(None)):
    """读取某日矛盾报告；缺省返回最新。"""
    row = store.load_report(date=date)
    if row:
        return row
    return {}


@router.post("/scan")
def trigger_scan(date: Optional[str] = Query(None)):
    """手动触发一次扫描，结果落库（同日幂等覆盖）。"""
    from app.contradictions.store import _today
    target = date or _today()
    items = scan_all(date=target)
    saved = store.save_contradictions(target, items)
    return {"date": target, "scanned": len(items), "saved": saved}


@router.post("/report")
def trigger_report(date: Optional[str] = Query(None)):
    """手动生成某日矛盾报告并落库。"""
    result = run_report(date=date)
    return result


@router.post("/resolve")
def mark_resolved(
    date: str = Query(..., description="矛盾日期"),
    ctype: str = Query(..., description="矛盾类型 type"),
    note: str = Query("", description="备注"),
):
    """标记矛盾已兑现或失效。"""
    ok = store.mark_resolved(date, ctype, note)
    return {"success": ok, "date": date, "type": ctype}
