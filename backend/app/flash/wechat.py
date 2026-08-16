"""
================================================================================
【文件作用】企业微信推送（可选，移植自 fetch-flash.js / review.js 的推送部分）
================================================================================

环境变量：WECHAT_WEBHOOK（未配置时所有推送静默跳过，只走 Web 界面）。
企业微信 markdown 消息上限 4096 字节，超长自动按段落分批。
================================================================================
"""

import os
import requests

WECHAT_WEBHOOK = os.environ.get("WECHAT_WEBHOOK", "")
MAX_CONTENT_BYTES = 4000

_session = requests.Session()


def _send(content: str, label: str) -> bool:
    if not WECHAT_WEBHOOK or not content or not content.strip():
        return False
    try:
        r = _session.post(WECHAT_WEBHOOK,
                          json={"msgtype": "markdown", "markdown": {"content": content}},
                          timeout=30)
        ok = r.json().get("errcode") == 0
        print(f"[wechat] [{'✅' if ok else '❌'}] {label}")
        return ok
    except Exception as e:
        print(f"[wechat] {label} 推送失败: {e}")
        return False


def _truncate(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断（不产生半个字符）。"""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def push_markdown_batched(title: str, content: str) -> None:
    """按段落边界分批推送长 Markdown（每批 ≤4KB，标题带序号）。"""
    if not WECHAT_WEBHOOK:
        return
    full = f"## {title}\n---\n{content}"
    if len(full.encode("utf-8")) <= MAX_CONTENT_BYTES:
        _send(full, title)
        return
    remaining, idx = content, 0
    while remaining.strip():
        idx += 1
        header = f"## {title} ({idx}/?)\n---\n"
        budget = MAX_CONTENT_BYTES - len(header.encode("utf-8")) - 20
        batch = _truncate(remaining, budget)
        # 优先在段落边界断开
        cut = batch.rfind("\n\n")
        if cut > len(batch) * 0.5:
            batch = batch[:cut]
        _send(header + batch + ("\n\n...(续)" if idx > 1 else ""), f"{title}({idx})")
        remaining = remaining[len(batch):].strip()


def push_analysis(analysis: dict, clusters: list) -> None:
    """诊断流三段推送：诊断+情景 / 重点事件 / 策略+合规。"""
    if not WECHAT_WEBHOOK:
        return
    diag = analysis.get("diagnostic_status") or {}
    corr = analysis.get("correlation_diagnosis") or {}
    narrative = analysis.get("dominant_narrative") or {}

    p1 = (f"## ⚡ 宏观信号过滤引擎\n"
          f"> 数据质量：**{diag.get('data_quality', '未知')}** (置信度:{diag.get('overall_confidence', '低')})\n"
          f"> 诊断状态：{analysis.get('market_mood', '未明')} [{analysis.get('uncertainty_level', '中')}不确定性]\n---\n"
          f"### 📊 核心相关性诊断\n"
          f"- **当前阶段：** 状态 {corr.get('current_phase', '未知')} ({corr.get('correlation_state', '无法判断')})\n"
          f"- **主导叙事：** {narrative.get('narrative', '未明')}\n"
          f"- **叙事脆弱点：** {narrative.get('fragility', '无')}\n---\n")
    for s in (analysis.get("scenarios") or [])[:3]:
        p1 += (f"> **{s.get('scenario_name')}** ({s.get('probability_qualitative')})\n"
               f"> 路径: {s.get('oil_path', '无')} | 触发: {s.get('trigger_to_watch')}\n\n")
    _send(p1, "诊断摘要")

    events = analysis.get("top_events") or []
    if events:
        p2 = "### 🔍 重点事件分析\n"
        for e in events[:3]:
            urgent = " [紧急]" if e.get("time_sensitive") else ""
            p2 += (f"#### {urgent} {e.get('action')} {e.get('target')}\n"
                   f"**事件：** {e.get('cluster_name')} ({e.get('value_score')}分)\n"
                   f"**逻辑：** {e.get('why')}\n"
                   f"**链条：** {e.get('transmission_chain')}\n\n")
        _send(p2, "事件分析")

    strategy = analysis.get("daily_strategy") or {}
    comp = analysis.get("d_state_compliance") or {}
    p3 = (f"### 📅 交易策略 [{strategy.get('max_position_confidence', '低')}置信度]\n"
          f"> **总仓位：{strategy.get('overall_position', '观望')}**\n"
          f"> **核心逻辑：** {strategy.get('core_logic', '无')}\n"
          f"> **禁入标的：** {' | '.join(strategy.get('do_not_touch') or []) or '无'}\n---\n"
          f"**D状态合规：** {comp.get('compliance_note', '已通过逻辑检查')}\n"
          f"**数据缺失：** {' | '.join(diag.get('missing_items') or []) or '无'}")
    _send(p3, "每日策略")


def push_alerts(alerts: dict) -> None:
    """信号跟踪提醒（入场/出场/接近目标）。"""
    if not WECHAT_WEBHOOK:
        return
    lines = []
    for a in alerts.get("entries", []):
        lines.append(a["message"])
    for a in alerts.get("exits", []):
        lines.append(a["message"])
    if lines:
        push_markdown_batched("🎯 交易信号提醒", "\n".join(lines))
