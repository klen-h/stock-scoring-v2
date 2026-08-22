"""
================================================================================
【文件作用】快讯/复盘/信号跟踪的编排层（移植 fetch-flash.js main() / review.js main()）
================================================================================

三个入口（同步函数，调度器和手动触发都走这里）：
  poll_flash_once()     快讯轮询：抓取→过滤→聚类→去重→LLM诊断→推送→落盘
  run_review(phase)     三段复盘：宏观+事件链+ETF+内部状态→LLM→信号→跟踪→推送
  track_signals_once()  信号跟踪：行情→状态机→提醒

设计原则：任一环节失败只打日志，绝不向上抛（调度器不能被单次失败杀死）。
================================================================================
"""

from datetime import datetime, timedelta

from app.flash import rules, store, source, llm, wechat
from app.signals import tracker


def _now_iso() -> str:
    """北京时间 ISO（服务器可能跑在 UTC，时间戳统一北京时间）。"""
    return rules.beijing_now().isoformat()


# ================================================================
#  一、快讯轮询（fetch-flash.js main 的移植）
# ================================================================

def _update_state_after_push(state: dict, to_analyze: list) -> None:
    """推送成功后更新已推送簇状态（pushCount/紧急/军事标记）。"""
    for cluster in to_analyze:
        existing = next((p for p in state.get("pushedClusters", [])
                         if p["cluster"] == cluster["_cluster"]), None)
        if existing:
            existing["pushCount"] = existing.get("pushCount", 0) + 1
            existing["lastUpdateId"] = cluster["id"]
            existing["lastUpdateTime"] = _now_iso()
            if rules.has_urgent_time(cluster.get("content") or ""):
                existing["hadUrgent"] = True
            if "军事行动" in (cluster.get("content") or ""):
                existing["hasMilitary"] = True
        else:
            state.setdefault("pushedClusters", []).append({
                "cluster": cluster["_cluster"],
                "firstId": cluster["id"],
                "firstTime": _now_iso(),
                "lastUpdateId": cluster["id"],
                "lastUpdateTime": _now_iso(),
                "pushCount": 1,
                "hotMax": "爆" if cluster.get("hot") == "爆" else "沸",
                "hadUrgent": rules.has_urgent_time(cluster.get("content") or ""),
                "hasMilitary": "军事行动" in (cluster.get("content") or ""),
            })


def poll_flash_once() -> dict:
    """
    快讯轮询一轮。返回执行摘要（供 /status 与手动触发展示）。
    幂等：无新事件时只推进游标，近零成本。
    """
    summary = {"time": _now_iso(), "fetched": 0, "new": 0,
               "filtered": 0, "clusters": 0, "analyzed": 0}
    try:
        items = source.fetch_jin10()
        summary["fetched"] = len(items)
        if not items:
            summary["note"] = "未获取到数据（可能未配置 FLASH_COOKIE 或接口异常）"
            return summary

        new_items = source.get_new_items(items)
        summary["new"] = len(new_items)
        if not new_items:
            summary["note"] = "无新增快讯"
            store.save_raw_data(items, [])
            return summary

        filtered = rules.pre_filter(new_items)
        clustered = rules.cluster_items(filtered)
        summary["filtered"] = len(filtered)
        summary["clusters"] = len(clustered)

        # 与已推送簇比对：新簇 / 升爆 / 重大更新 → 送 LLM；其余只更新游标
        state = store.load_state()
        to_analyze, to_update = [], []
        for cluster in clustered:
            existing = next((p for p in state.get("pushedClusters", [])
                             if p["cluster"] == cluster["_cluster"]), None)
            if existing is None:
                to_analyze.append(cluster)
            elif cluster.get("hot") == "爆" and existing.get("pushCount") == 0:
                to_analyze.append(cluster)          # 升级为"爆"，补推
            elif rules.is_major_update(cluster.get("content") or "", existing):
                to_analyze.append(cluster)          # 重大更新，重推
            else:
                to_update.append(cluster)

        if to_analyze:
            summary["analyzed"] = len(to_analyze)
            from app.macro import get_macro_panel
            panel = get_macro_panel()
            market_data = tracker.get_market_data()
            analysis = llm.analyze_with_llm(
                to_analyze, panel, market_data["holdings"], tracker.holdings_text())
            removed = llm.strip_d_state_violations(analysis)   # D 状态合规代码层审查
            for r in removed:
                print(f"[flash] 🚫 {r}")
            wechat.push_analysis(analysis, to_analyze)
            _update_state_after_push(state, to_analyze)
            store.save_analysis(analysis, to_analyze)   # ★ LLM 全文落盘
        else:
            summary["note"] = "无新事件需分析，静默"

        for cluster in to_update:
            existing = next((p for p in state.get("pushedClusters", [])
                             if p["cluster"] == cluster["_cluster"]), None)
            if existing:
                existing["lastUpdateId"] = cluster["id"]
                existing["lastUpdateTime"] = _now_iso()

        store.save_state(state)
        store.save_raw_data(items, new_items)
    except Exception as e:
        summary["error"] = str(e)[:300]
        print(f"[flash] 轮询异常: {e}")
    return summary


# ================================================================
#  二、三段复盘（review.js main 的移植）
# ================================================================

def _recent_pushed_clusters(hours: int) -> list:
    """最近 N 小时有更新的已推送簇（时间升序）。"""
    cutoff = rules.beijing_now() - timedelta(hours=hours)
    out = []
    for c in store.load_state().get("pushedClusters", []):
        try:
            if datetime.fromisoformat(c["lastUpdateTime"]) > cutoff:
                out.append(c)
        except (ValueError, KeyError):
            continue
    return sorted(out, key=lambda c: c.get("lastUpdateTime", ""))


def run_review(phase: str) -> dict:
    """
    复盘一轮（phase: premarket / lunchbreak / postmarket）。
    返回 {phase, markdown, signals_added, alerts}。
    """
    result = {"phase": phase, "time": _now_iso(),
              "markdown": "", "signals_added": 0, "alerts": {"entries": [], "exits": []}}
    try:
        # 1. 宏观面板 + 历史落盘
        from app.macro import get_macro_panel
        panel = get_macro_panel()
        store.append_macro_history(panel)

        # 2. 事件链（盘前24h / 午间6h / 盘后12h）
        hours = {"premarket": 24, "lunchbreak": 6, "postmarket": 12}[phase]
        clusters = _recent_pushed_clusters(hours)

        # 3. ETF 行情（force，休市拿静态收盘数据）+ 盘后存收盘快照
        market_data = tracker.get_market_data(force=True)
        holdings = market_data["holdings"]
        if phase == "postmarket" and holdings:
            now = rules.beijing_now()
            t = now.hour * 60 + now.minute
            valid = sum(1 for h in holdings if h["price"] > 0)
            if 900 <= t < 1020 and valid > len(holdings) * 0.8:   # 15:00-17:00 且数据有效
                store.save_etf_close(holdings)

        # 4. LLM 复盘（markdown + 结构化信号）
        markdown, signals, model = llm.run_review_llm(
            phase, clusters, panel, holdings, tracker.holdings_text(),
            tracker.CORE_ETFS, hours)
        result["markdown"] = markdown
        store.save_review(phase, markdown, signals)

        # 5. 信号入库（门槛校验）+ 状态机更新
        for s in signals:
            full = tracker.build_signal_from_llm(s, source=phase)
            r = tracker.add_signal_with_validation(full)
            if r["validation"]["passed"]:
                result["signals_added"] += 1
        track = tracker.update_signals(market_data)
        result["alerts"] = track["alerts"]

        # 6. 推送
        title = llm._PHASE_TITLES.get(phase, phase)
        wechat.push_markdown_batched(title, markdown)
        if track["alerts"]["entries"] or track["alerts"]["exits"]:
            wechat.push_alerts(track["alerts"])
        wechat.push_markdown_batched("🎯 专业交易员报告",
                                     tracker.generate_pro_trader_report())
    except Exception as e:
        result["error"] = str(e)[:300]
        print(f"[review] {phase} 复盘异常: {e}")
    return result


# ================================================================
#  三、信号跟踪（track-signals.js 的移植）
# ================================================================

def track_signals_once() -> dict:
    """拉行情 → 状态机 → 有入场/出场才推送。返回 {alerts}。"""
    result = {"time": _now_iso(),
              "alerts": {"entries": [], "exits": [], "updates": []}}
    try:
        market_data = tracker.get_market_data()
        if not market_data["holdings"]:
            result["note"] = "休市，无ETF数据"
            return result
        track = tracker.update_signals(market_data)
        result["alerts"] = track["alerts"]
        if track["alerts"]["entries"] or track["alerts"]["exits"]:
            wechat.push_alerts(track["alerts"])
    except Exception as e:
        result["error"] = str(e)[:300]
        print(f"[track] 信号跟踪异常: {e}")
    return result
