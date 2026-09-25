"""
================================================================================
【文件作用】快讯监控路由（事件流 / 诊断 / 复盘 / 信号 / 手动触发）
================================================================================

URL 前缀 /api/flash：
  GET  /api/flash/events               → 快讯事件流（分页，最新在前）
  GET  /api/flash/calendar             → 财经日历（未来N天：经济指标/事件讲话/休市）
  POST /api/flash/calendar/refresh     → 手动刷新日历缓存（正常由调度器每日07:00跑）
  GET  /api/flash/diagnosis            → 最新 LLM 诊断（含历史列表）
  GET  /api/flash/review/{phase}       → 最新三段复盘（premarket/lunchbreak/postmarket）
  GET  /api/flash/signals              → 信号跟踪（活跃/历史/绩效/被拒）
  POST /api/flash/ingest               → 手动触发一轮快讯轮询（测试用）
  POST /api/flash/review/{phase}/run   → 手动触发一次复盘（测试用）
  POST /api/flash/wechat-test        → 企微推送连通性测试（测试用）
  GET  /api/flash/status               → 调度器状态与配置
================================================================================
"""

from fastapi import APIRouter, Query
import os

# ★ 2026-09-12：本文件所有时间戳统一走 rules.beijing_now*()（原来混用 datetime.now()，
#   在 Render 上等于 UTC，与库里的北京时间对不上，见下方通知接口注释）
from app.database import db
from app.flash import store, service, scheduler, wechat, rules
from app.signals import tracker

router = APIRouter()

# ── 【已退役 2026-09-12】原 GET /backup + POST /restore（浏览器镜像的自动导出/回填）──
# 退役理由：① 它保护的 9 个文件（flash/analyses/reviews/tracking/macro_history/
#   etf_close/flash_state/schedule_state/strategies）早已全部迁库，文件只剩空壳；
#   ② 不久后连「财经日历 / LLM 用量」也迁进了 DB（flash_calendar / llm_usage_daily），
#      文件仅作为兜底副本 → 手动导出/回填已无对象；
#   ③ 代价却是每 5 分钟 × 每个标签页调一次 /api/flash/backup，为了算"条目数签名"
#      把 50 条诊断正文读出来（实测 40MB/天 Supabase egress）。
# 现在"盯着数据"的职责由 app/data_files.py + /api/system/runtime-files 承担；
# 数据库本身的灾备走 Supabase 备份 + 腾讯云本机 Postgres 的每日 pg_dump。


@router.get("/calendar")
def flash_calendar(days: int = Query(7, ge=1, le=60),
                   min_star: int = Query(0, ge=0, le=5),
                   kind: str = Query("")):
    """
    财经日历：未来 days 天的事件，三类混排（data经济指标 / event事件讲话 / holiday休市）。
    min_star>0 时只返回该星级以上（holiday 无星级字段，会被一并过滤掉）。
    kind 传单个类型可只看某一类。缓存为空时会现场拉一次再返回。
    """
    from app.flash import calendar
    c = calendar.load()
    items = calendar.upcoming(days=days, min_star=min_star,
                              kinds=(kind,) if kind else ())
    return {"updated_at": c.get("updated_at", ""),
            "range": c.get("range", {}),
            "total": len(c.get("items") or []),
            "count": len(items),
            "items": items}


@router.post("/calendar/refresh")
def flash_calendar_refresh(days_ahead: int = Query(14, ge=7, le=60)):
    """手动刷新财经日历缓存（测试/应急用；日常由调度器每日 07:00 自动刷新）。"""
    from app.flash import calendar
    n = calendar.refresh(days_ahead=days_ahead)
    return {"refreshed": n, "time": rules.beijing_now_iso()}   # ★ 北京时间（原为服务器本地时间）


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
    """最新 LLM 诊断（列表，最新在前；每条含完整输出）。

    从数据库表 flash_analyses 读取（与 save_analysis 写入侧对齐）。
    此前读的是迁移前的 data/analyses.json，该文件自迁移后再没被写入，
    表现为「今日诊断」永远显示十几年前的旧诊断。
    """
    analyses = store.load_analyses(limit)
    return {"data": analyses, "total": len(analyses),
            "latest": analyses[0] if analyses else None}


@router.get("/review/{phase}")
def flash_review(phase: str):
    """最新复盘（markdown + 信号）。phase: premarket / lunchbreak / postmarket。"""
    if phase not in ("premarket", "lunchbreak", "postmarket"):
        return {"error": f"未知复盘阶段 {phase}"}
    return store.load_review(phase)


@router.get("/review/{phase}/history")
def flash_review_history(phase: str, limit: int = Query(20, ge=1, le=50)):
    """复盘历史（最新在前），供按日期搜索回溯。"""
    if phase not in ("premarket", "lunchbreak", "postmarket"):
        return {"error": f"未知复盘阶段 {phase}"}
    return {"data": store.load_review_history(phase, limit)}


@router.post("/review/{phase}/run")
def flash_review_run(phase: str):
    """手动触发一次复盘（测试/补跑用；正常由调度器按窗口执行）。"""
    if phase not in ("premarket", "lunchbreak", "postmarket"):
        return {"error": f"未知复盘阶段 {phase}"}
    result = service.run_review(phase)
    # ★ 手动成功同样标记当日已完成：与调度器同语义。
    #   否则（如 2026-09-08 盘前复盘补跑场景）手动跑完后调度循环恢复时
    #   会在窗口内再跑一遍 → LLM 双烧、企微双推。
    if result and not result.get("error"):
        try:
            store.mark_schedule_done(f"review_{phase}")
        except Exception as e:
            print(f"[flash] 手动复盘标记完成失败（不影响本次结果）: {e}")
    return result


@router.get("/signals")
def flash_signals():
    """信号跟踪总览：活跃 / 历史 / 绩效 / 被拒。"""
    tracking = tracker.load_tracking()
    return {
        "activeSignals": tracking.get("activeSignals", []),
        "history": tracking.get("history", [])[:30],
        "performance": tracking.get("performance", {}),
        "metrics": tracker.calculate_advanced_metrics(
            [s for s in tracking.get("history", [])[:100] if s.get("status") == "closed"]),
        "rejected": tracking.get("rejectedSignals", [])[:10],
        "etf_pool": tracker.HOLDINGS_MAP,
    }


@router.get("/push-log")
def flash_push_log(date: str = None, limit: int = 80):
    """系统提示时间线（信号总线，2026-09-23 新增）。

    动机：系统有 ~10 个各自独立的推送入口（数据源告警 / 盘中警示 / 午间雷达 / 日报 /
    周报 / 模拟盘 / 教练纪律与转档 / 主线…），此前**没有汇总视图** ⇒ 只能靠翻企微聊天
    记录回答「系统今天说了什么、有没有漏」。本端点给出**统一时间线**（分类统计 + 逐条）。

    实现：记录器埋在 `flash.wechat.push_markdown_batched`（**全部业务推送的单点入口**）
    ⇒ 零侵入覆盖所有来源（不必改 10 个调用方），详见 `flash/signal_bus.py`。
    ⚠️ 语义：记录的是「**系统判断要说这件事**」，不代表企微一定送达 —— 业务开关关闭 /
       未配 webhook 时也会记录；页面据此仍能看到"系统今天判断过什么"。
    """
    from app.flash import signal_bus
    # ★ 2026-09-25（egress 治理）：列表页只显示 `firstLine(content)` ⇒ 正文截到 400 字符
    #   （原样返回全文时该接口 46 分钟就传了 ≤1.9MB，页面开一天 ≈20MB）。
    return {"stats": signal_bus.stats(date),
            "items": signal_bus.by_date(date, limit, trunc=400),
            "dates": signal_bus.recent_dates(7)}


# ── ★ 系统自洽性审查（2026-09-23 新增）────────────────────────────────────────
_ANALYSIS_TABLE = "radar_analysis"
_ANALYSIS_READY = False


def _analysis_ensure():
    global _ANALYSIS_READY
    if _ANALYSIS_READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_ANALYSIS_TABLE} (
            date TEXT PRIMARY KEY,
            markdown TEXT,
            items_used TEXT,
            created_at TEXT
        )
    """)
    _ANALYSIS_READY = True


@router.get("/radar-analysis")
def flash_radar_analysis(date: str = None, refresh: bool = False):
    """把今日系统提示汇总交给 LLM，找「**系统自身**判断之间的矛盾/张力」（自洽性审查）。

    ★ 与 `contradictions`（矛盾扫描）分工不同：那个扫的是**市场数据**层面的矛盾
      （如"指数红盘但主力净流出"）；本端点扫的是**系统自己发出的多条提示是否打架**
      （如"盘中警示说回避" vs "午间雷达说机会"、早盘判断被午后自己的判断推翻）——
      回答用户的问题：「这么多提示，彼此矛盾吗？我该信哪一条？」

    成本：每次最多一次 LLM 调用；结果按日**落库缓存**，同日再调直接读缓存
    （`refresh=true` 强制重算）。提示词只喂「标题 + 正文前 300 字」并限制条数 ⇒
    不把整份长报告塞进上下文。
    """
    from app.flash import signal_bus, rules
    day = date or rules.beijing_now().strftime("%Y-%m-%d")

    if not refresh:
        try:
            _analysis_ensure()
            row = db.fetch_one(f"SELECT markdown, items_used, created_at FROM {_ANALYSIS_TABLE} "
                              f"WHERE date = %s", (day,))
            if row and row.get("markdown"):
                return {"date": day, "markdown": row["markdown"], "cached": True,
                        "items_used": row.get("items_used"), "created_at": row.get("created_at")}
        except Exception as e:
            print(f"[radar] 读缓存失败（改为现算）: {e}")

    items = signal_bus.by_date(day, 40) or []
    if not items:
        return {"date": day, "markdown": "", "cached": False, "items_used": 0,
                "error": "今日没有系统提示可分析"}

    lines = []
    for x in items:
        body = (x.get("content") or "").replace("\n", " ")[:300]
        lines.append(f"- [{str(x.get('ts'))[11:16]}][{x.get('category') or '其他'}] "
                     f"{x.get('title')}｜{body}")
    digest = "\n".join(lines)

    system = ("你是 A 股量化系统的『自洽性审查员』。任务**不是**分析市场，而是审查"
              "**系统自己今天发出的多条提示之间**是否矛盾/张力/前后反复。"
              "结论先行、指名具体条目（用标题），不编造、不硬凑。")
    user = (f"## 今日系统提示（时间线，共 {len(items)} 条）\n{digest}\n\n"
            "## 请输出（markdown，总长 ≤ 6 句）\n"
            "1. 最值得注意的**矛盾或张力**（最多 3 条）：涉及哪几条提示、矛盾点是什么、"
            "更该相信哪一条（说明理由）\n"
            "2. **时间上的反复**：早盘说 A、午后说非 A 的情况（没有就说没有）\n"
            "3. 若整体自洽，直接写「未发现实质矛盾」—— **不要为了凑数编造**\n"
            "4. 一句话：使用者此刻该优先信哪一条")
    try:
        from app.flash.llm import call_llm
        txt = (call_llm(system, user, temperature=0.3, tier="fast") or "").strip()
    except Exception as e:
        return {"date": day, "markdown": "", "cached": False, "items_used": len(items),
                "error": f"LLM 调用失败: {str(e)[:160]}"}
    if not txt:
        return {"date": day, "markdown": "", "cached": False, "items_used": len(items),
                "error": "LLM 返回空（可稍后重试）"}

    try:
        _analysis_ensure()
        db.upsert(_ANALYSIS_TABLE,
                  {"date": day, "markdown": txt, "items_used": str(len(items)),
                   "created_at": rules.beijing_now().isoformat(timespec="seconds")},
                  conflict_columns=["date"])
    except Exception as e:
        print(f"[radar] 分析落库失败（本次仍返回）: {e}")
    return {"date": day, "markdown": txt, "cached": False, "items_used": len(items)}


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
    # ★ 2026-09-12：统一北京时间。原来 now 用服务器本地时间（Render 上是 UTC）、事件
    #   时间用北京时间 → since 永远比事件早 8 小时，最近 8 小时的事件每轮都会被重新判定为
    #   "新"（靠浏览器通知 tag 去重才没炸）。现在两端同一把尺子；历史无时区标记的记录按
    #   UTC 解释（rules.to_beijing），naive/aware 混用也不会再抛异常漏事件。
    since_dt = rules.to_beijing(since) if since else None
    events = []

    def _newer(t: str) -> bool:
        if not since_dt:
            return True
        t_dt = rules.to_beijing(t)
        return bool(t_dt and t_dt > since_dt)

    # 1. 新诊断（★ 2026-09-12：since 下推到 SQL —— 轮询每分钟一次，原来固定拉 20 条
    #    含完整 output_json 的诊断（约 50KB/次）。过滤仍在"最新 20 条"窗口内做，
    #    与旧行为逐字等价，见 store.load_analyses）
    for a in store.load_analyses(20, since=since):
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
    for phase in ("premarket", "lunchbreak", "postmarket"):
        lst = store.load_review_history(phase, 1, since=since)
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
    return {"events": events[-20:], "now": rules.beijing_now_iso()}


@router.post("/wechat-test")
def flash_wechat_test():
    """企微推送连通性测试：立即向配置的 webhook 发一条验证消息（测试用）。

    部署后可用 curl -X POST https://<你的服务地址>/api/flash/wechat-test 验证
    线上实例的 WECHAT_WEBHOOK 是否有效；企微群收到消息即代表通道正常。
    """
    if not wechat.WECHAT_WEBHOOK:
        return {"ok": False, "error": "未配置 WECHAT_WEBHOOK 环境变量"}
    content = ("## ✅ 企微推送连通性测试\n"
               f"> **实例：** {os.environ.get('RENDER_INSTANCE_ID', 'local')}\n"
               f"> **时间：** {rules.beijing_now().strftime('%Y-%m-%d %H:%M')}\n"
               f"> 收到此消息说明本实例的 WECHAT_WEBHOOK 配置有效，任务失败提醒将正常送达。")
    ok = wechat._send(content, "wechat-test")
    return {"ok": ok, "webhook_configured": True,
            "webhook_prefix": wechat.WECHAT_WEBHOOK[:45] + "..."}


@router.get("/status")
def flash_status():
    """调度器状态 + 数据源健康 + LLM 用量（含熔断）+ 当日统计。"""
    from app.flash.llm import get_llm_usage
    from app import health
    from app.flash import store as flash_store

    # 当日统计：新推簇数 / 诊断次数（每天 LLM 消耗一目了然）
    #   ★ 2026-09-12：统一北京时间 —— 落库的 firstTime / time 都是北京时间，而原来用
    #     服务器本地时间（Render = UTC）取日期前缀，北京时间 00:00~08:00 会算成前一天。
    today = flash_store._bj_date()
    clusters_today = 0
    for c in flash_store.load_state().get("pushedClusters", []):
        t = c.get("firstTime", "")
        if t[:10] == today:
            clusters_today += 1
    # ★ 2026-09-12：原来读 data/analyses.json（迁移前的遗留文件，停在 08-17）→
    #   「今日诊断次数」恒为 0。改为直接 COUNT 数据库表 flash_analyses。
    analyses_today = 0
    try:
        from app.database import db as _db
        row = _db.fetch_one(
            "SELECT COUNT(*) AS n FROM flash_analyses WHERE time LIKE %s",
            (today + "%",))
        analyses_today = int((row or {}).get("n") or 0)
    except Exception as e:
        print(f"[flash] 今日诊断次数统计失败: {e}")

    result = dict(scheduler.status)
    result["sources"] = health.get_health()
    result["llm_usage"] = get_llm_usage()
    result["today"] = {"clusters_pushed": clusters_today,
                       "llm_diagnoses": analyses_today,
                       "reviews": {k: v for k, v in scheduler.status.get("last_reviews", {}).items()}}
    return result
