"""
================================================================================
【文件作用】快讯监控路由（事件流 / 诊断 / 复盘 / 信号 / 手动触发）
================================================================================

URL 前缀 /api/flash：
  GET  /api/flash/events               → 快讯事件流（分页，最新在前）
  GET  /api/flash/diagnosis            → 最新 LLM 诊断（含历史列表）
  GET  /api/flash/review/{phase}       → 最新三段复盘（premarket/lunchbreak/postmarket）
  GET  /api/flash/signals              → 信号跟踪（活跃/历史/绩效/被拒）
  POST /api/flash/ingest               → 手动触发一轮快讯轮询（测试用）
  POST /api/flash/review/{phase}/run   → 手动触发一次复盘（测试用）
  GET  /api/flash/status               → 调度器状态与配置
================================================================================
"""

from fastapi import APIRouter, Query
from app.flash import store, service, scheduler
from app.signals import tracker

router = APIRouter()


@router.get("/events")
def flash_events(page: int = Query(1, ge=1), size: int = Query(50, ge=1, le=200)):
    """快讯事件流（原始快讯，含簇标记字段）。"""
    items = store.load_raw_items()
    total = len(items)
    start = (page - 1) * size
    return {"data": items[start:start + size], "total": total,
            "page": page, "size": size}


@router.get("/diagnosis")
def flash_diagnosis(limit: int = Query(5, ge=1, le=20)):
    """最新 LLM 诊断（列表，最新在前；每条含完整输出）。"""
    analyses = store._load(store.PATHS["analyses"], {"analyses": []}).get("analyses", [])
    return {"data": analyses[:limit], "total": len(analyses),
            "latest": analyses[0] if analyses else None}


@router.get("/review/{phase}")
def flash_review(phase: str):
    """最新复盘（markdown + 信号）。phase: premarket / lunchbreak / postmarket。"""
    if phase not in ("premarket", "lunchbreak", "postmarket"):
        return {"error": f"未知复盘阶段 {phase}"}
    return store.load_review(phase)


@router.post("/review/{phase}/run")
def flash_review_run(phase: str):
    """手动触发一次复盘（测试用；正常由调度器按窗口执行）。"""
    if phase not in ("premarket", "lunchbreak", "postmarket"):
        return {"error": f"未知复盘阶段 {phase}"}
    return service.run_review(phase)


@router.get("/signals")
def flash_signals():
    """信号跟踪总览：活跃 / 历史 / 绩效 / 被拒。"""
    tracking = tracker.load_tracking()
    return {
        "activeSignals": tracking.get("activeSignals", []),
        "history": tracking.get("history", [])[:30],
        "performance": tracking.get("performance", {}),
        "metrics": tracker.calculate_advanced_metrics(tracking.get("history", [])[:100]),
        "rejected": tracking.get("rejectedSignals", [])[:10],
        "etf_pool": tracker.HOLDINGS_MAP,
    }


@router.get("/audit")
def flash_audit():
    """
    LLM 复盘对账：提议 → 门槛 → 跟踪 → 平仓 的完整漏斗，
    按复盘阶段/ETF/方向分组的胜率，拒绝原因分布，平仓明细。
    用于量化"LLM 推荐到底靠不靠谱"。
    """
    return tracker.build_audit()


@router.post("/ingest")
def flash_ingest():
    """手动触发一轮快讯轮询（测试用）。"""
    return service.poll_flash_once()


@router.get("/notifications")
def flash_notifications(since: str = ""):
    """
    页面通知源：返回 since（ISO 时间）之后产生的三类事件（时间升序，最多 20 条）：
      diagnosis 新 LLM 诊断 / review 新复盘 / signal 信号入场或出场。
    前端页面开着时每分钟轮询本接口，有新事件就弹浏览器系统通知。
    """
    from datetime import datetime as _dt

    try:
        since_dt = _dt.fromisoformat(since) if since else None
    except ValueError:
        since_dt = None
    events = []

    def _newer(t: str) -> bool:
        try:
            return not since_dt or _dt.fromisoformat(t) > since_dt
        except (ValueError, TypeError):
            return False

    # 1. 新诊断
    for a in store._load(store.PATHS["analyses"], {"analyses": []})["analyses"][:20]:
        if _newer(a.get("time", "")):
            out = a.get("output") or {}
            corr = out.get("correlation_diagnosis") or {}
            clusters = "、".join(c.get("cluster") or "" for c in (a.get("clusters") or [])[:3])
            events.append({
                "type": "diagnosis", "time": a.get("time"),
                "title": "🧠 新宏观诊断",
                "body": f"{corr.get('correlation_state') or out.get('market_mood', '')}"
                        f" | 事件：{clusters or '无'}",
            })

    # 2. 新复盘
    phase_names = {"premarket": "盘前", "lunchbreak": "午盘", "postmarket": "盘后"}
    for phase, lst in store._load(store.PATHS["reviews"], {}).items():
        if lst and _newer(lst[0].get("time", "")):
            events.append({
                "type": "review", "time": lst[0].get("time"),
                "title": f"📋 {phase_names.get(phase, phase)}复盘已生成",
                "body": f"本轮信号 {len(lst[0].get('signals') or [])} 个，点击查看",
            })

    # 3. 信号入场/出场
    tracking = tracker.load_tracking()
    for s in tracking.get("activeSignals", []) + tracking.get("history", []):
        for kind, title in (("entries", "🚀 信号入场"), ("exits", "💰 信号出场")):
            for e in s.get(kind) or []:
                if _newer(e.get("time", "")):
                    events.append({
                        "type": "signal", "time": e.get("time"), "title": title,
                        "body": f"{s.get('etfName')} @ {e.get('price')}（{e.get('reason', '')}）",
                    })

    events.sort(key=lambda x: x.get("time") or "")
    # 数据源健康告警/恢复事件也进通知管道（页面铃铛自动弹）
    from app import health
    events.extend(health.recent_alerts(since))
    events.sort(key=lambda x: x.get("time") or "")
    return {"events": events[-20:], "now": _dt.now().isoformat()}


@router.get("/status")
def flash_status():
    """调度器状态 + 数据源健康 + LLM 用量（含熔断）+ 当日统计。"""
    from app.flash.llm import get_llm_usage
    from app import health
    from app.flash import store as flash_store
    from datetime import datetime

    # 当日统计：新推簇数 / 诊断次数（每天 LLM 消耗一目了然）
    today = flash_store._bj_date()
    clusters_today = 0
    for c in flash_store.load_state().get("pushedClusters", []):
        t = c.get("firstTime", "")
        if t[:10] == datetime.now().strftime("%Y-%m-%d"):
            clusters_today += 1
    analyses_today = sum(
        1 for a in flash_store._load(flash_store.PATHS["analyses"],
                                     {"analyses": []})["analyses"]
        if str(a.get("time", ""))[:10] == datetime.now().strftime("%Y-%m-%d"))

    result = dict(scheduler.status)
    result["sources"] = health.get_health()
    result["llm_usage"] = get_llm_usage()
    result["today"] = {"clusters_pushed": clusters_today,
                       "llm_diagnoses": analyses_today,
                       "reviews": {k: v for k, v in scheduler.status.get("last_reviews", {}).items()}}
    return result
