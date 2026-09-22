"""
================================================================================
【文件作用】企业微信推送（可选，移植自 fetch-flash.js / review.js 的推送部分）
================================================================================

环境变量：WECHAT_WEBHOOK（未配置时所有推送静默跳过，只走 Web 界面）。
企业微信 markdown 消息上限 4096 字节，超长自动按段落分批。

★ 2026-09-13 分类推送层（notify）：
  解析顺序：① 分类群机器人 WECHAT_HOOK_{RISK|COACH|BRIEF|ALERT}（webhook 无
  IP 限制，Render/Actions 直接可用，**生产分类靠它**）→ ② 自建应用消息
  （WECHAT_NOTIFY_*，有「可信IP」限制，动态出口 IP 会被 60020 拦，尽力而为）
  → ③ 主群 webhook（WECHAT_WEBHOOK）兜底。任一通道失败自动降级，通知不丢。
  配置：
    WECHAT_WEBHOOK=...                            # 主群（兜底）
    WECHAT_HOOK_RISK/COACH/BRIEF/ALERT=...        # 分类群机器人（推荐，生产可用）
    WECHAT_APP_CORP_ID=wwXXXX                     # 项目企业 ID（应用消息通道）
    WECHAT_NOTIFY_RISK/COACH/BRIEF/ALERT=agentid:secret   # 分应用（可选）
    WECHAT_NOTIFY_DEFAULT=agentid:secret          # 未专属配置的分类共用
    WECHAT_NOTIFY_TOUSER=HuangHeLiang|user2       # 应用消息接收人（通讯录账号）
================================================================================
"""

import os
import time as _time

import requests

WECHAT_WEBHOOK = os.environ.get("WECHAT_WEBHOOK", "")
MAX_CONTENT_BYTES = 4000

# 业务推送开关：诊断/复盘/信号提醒等日常推送。只需要任务失败提醒时保持关闭，
# 设 WECHAT_BUSINESS_ALERTS=1 可恢复全部日常推送（失败提醒不受此开关限制）。
BUSINESS_ALERTS_ENABLED = os.environ.get("WECHAT_BUSINESS_ALERTS", "0") == "1"

_session = requests.Session()

# ── 自建应用通道（2026-09-13）────────────────────────────────────────────────
WECHAT_APP_CORP_ID = os.environ.get("WECHAT_APP_CORP_ID", "")
WECHAT_NOTIFY_TOUSER = os.environ.get("WECHAT_NOTIFY_TOUSER", "")
_APP_MSG_MAX_BYTES = 1900          # 应用消息 markdown 上限 2048 字节，留余量
_APP_FAIL_THRESHOLD = 3            # 连续失败 N 次 → 熔断冷却（避免每次都白等超时）
_APP_COOLDOWN_SEC = 1800

_token_cache = {"key": None, "token": None, "ts": 0.0}
_circuit = {}   # {app_key: {"fails": int, "until": float}}（按应用分槽；2026-09-13 修单槽隐患）


def _hook_for(category: str):
    """分类 → 专属群机器人 webhook（WECHAT_HOOK_{CAT}；webhook 无 IP 限制）。"""
    cat = (category or "").strip().lower()
    if not cat:
        return None
    return (os.environ.get(f"WECHAT_HOOK_{cat.upper()}") or "").strip() or None


def _app_for(category: str):
    """分类 → (agentid, secret)：专属 env 优先 → DEFAULT → None（走 webhook）。"""
    cat = (category or "").strip().lower()
    for env in (f"WECHAT_NOTIFY_{cat.upper()}", "WECHAT_NOTIFY_DEFAULT"):
        raw = (os.environ.get(env) or "").strip()
        if raw and ":" in raw:
            agentid, secret = raw.split(":", 1)
            if agentid.strip() and secret.strip() and WECHAT_APP_CORP_ID:
                return agentid.strip(), secret.strip()
    return None


def _get_token(secret: str) -> str:
    now = _time.time()
    if (_token_cache["token"] and _token_cache["key"] == secret
            and now - _token_cache["ts"] < 7000):
        return _token_cache["token"]
    r = _session.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                     params={"corpid": WECHAT_APP_CORP_ID, "corpsecret": secret},
                     timeout=15)
    j = r.json()
    if j.get("errcode") != 0 or not j.get("access_token"):
        raise RuntimeError(f"gettoken {j.get('errcode')}: {(j.get('errmsg') or '')[:80]}")
    _token_cache.update(key=secret, token=j["access_token"], ts=now)
    return j["access_token"]


def _circuit_open(key: str) -> bool:
    st = _circuit.get(key)
    if not st:
        return False
    if st["fails"] >= _APP_FAIL_THRESHOLD:
        if _time.time() < st["until"]:
            return True
        _circuit.pop(key, None)   # 冷却结束，恢复尝试
    return False


def _mark_app_fail(key: str, why: str) -> None:
    st = _circuit.setdefault(key, {"fails": 0, "until": 0.0})
    st["fails"] += 1
    if st["fails"] >= _APP_FAIL_THRESHOLD:
        st["until"] = _time.time() + _APP_COOLDOWN_SEC
        print(f"[wechat] 应用通道连续失败 {st['fails']} 次（{why}）→ 熔断 30min，回落群 webhook")


def _send_app(agentid: str, secret: str, touser: str, content: str, label: str) -> bool:
    """单条应用消息（markdown）。成功 True；任何异常/非 0 errcode 都 False。"""
    try:
        tok = _get_token(secret)
        r = _session.post(
            "https://qyapi.weixin.qq.com/cgi-bin/message/send",
            params={"access_token": tok},
            json={"touser": touser, "msgtype": "markdown", "agentid": int(agentid),
                  "markdown": {"content": _truncate(content, _APP_MSG_MAX_BYTES)}},
            timeout=15)
        j = r.json()
        ok = j.get("errcode") == 0
    except Exception as e:
        print(f"[wechat] 应用消息失败 {label}: {e}")
        return False
    if not ok:
        print(f"[wechat] 应用消息拒绝 {label}: {j.get('errcode')} {(j.get('errmsg') or '')[:60]}")
    return ok


def notify(category: str, title: str, content: str, force: bool = False,
           touser: str = None) -> bool:
    """分类推送统一入口（解析顺序见文件头）。

    category: risk / coach / brief / alert（决定专属群机器人与应用/接收人）
    返回是否至少有一条通道送达（供调用方记日志/告警）。
    """
    if not force and not BUSINESS_ALERTS_ENABLED:
        return False   # 与 push_markdown_batched 的业务开关语义一致

    # ★ 2026-09-15：全局兜底——企微自定义机器人 markdown **不支持表格渲染**
    #   （`| a | b |` 会按纯文本渲染、竖线错位）。推送前统一把表格块转成
    #   「每行一条」列表（幂等：无表格时原文不变）。这样任何调用方（含 LLM
    #   生成的简报/复盘 Markdown）都无需各自处理。
    try:
        from app.wechat_fmt import markdown_tables_to_lists
        content = markdown_tables_to_lists(content)
    except Exception:
        pass

    # ① 分类群机器人：无 IP 限制，失败回落主群 webhook
    hook = _hook_for(category)
    if hook and _send_batched(hook, title, content):
        return True
    if hook and WECHAT_WEBHOOK:
        print(f"[wechat] [{category}] 分类群推送失败，回落主 webhook")
        return _send_batched(WECHAT_WEBHOOK, title, content)

    # ② 自建应用（可信IP 限制，尽力而为）→ ③ 主群 webhook 兜底
    full = f"## {title}\n---\n{content}"
    app = _app_for(category)
    key = f"{app[0]}:{app[1][:6]}" if app else None
    if app and not _circuit_open(key):
        receiver = (touser or WECHAT_NOTIFY_TOUSER or "").strip()
        if not receiver:
            print(f"[wechat] [{category}] 未配置 WECHAT_NOTIFY_TOUSER，跳过应用通道")
        else:
            # 长内容分批（应用通道上限更小）；任一批失败即整体回落 webhook
            ok_all = True
            remaining, idx = full, 0
            while remaining.strip():
                idx += 1
                header = f"## {title} ({idx}/?)\n---\n" if idx > 1 else ""
                budget = _APP_MSG_MAX_BYTES - len(header.encode("utf-8")) - 20
                batch = _truncate(remaining, budget)
                cut = batch.rfind("\n\n")
                if cut > len(batch) * 0.5:
                    batch = batch[:cut]
                if not _send_app(app[0], app[1], receiver,
                                 header + batch + ("\n\n...(续)" if idx > 1 else ""),
                                 f"{title}({idx})"):
                    ok_all = False
                    break
                remaining = remaining[len(batch):].strip()
            if ok_all:
                _circuit.pop(key, None)
                return True
            _mark_app_fail(key, title[:30])
    push_markdown_batched(title, content, force=force)
    return bool(WECHAT_WEBHOOK)


def _send(content: str, label: str, hook: str = None) -> bool:
    target = hook or WECHAT_WEBHOOK
    if not target or not content or not content.strip():
        return False
    try:
        r = _session.post(target,
                          json={"msgtype": "markdown", "markdown": {"content": content}},
                          timeout=30)
        ok = r.json().get("errcode") == 0
    except Exception as e:
        print(f"[wechat] {label} 推送失败: {e}")
        return False
    # print 放 try 外，避免控制台编码问题（如 Windows GBK 下的 emoji）影响发送结果
    print(f"[wechat] [{'OK' if ok else 'FAIL'}] {label}" + ("（分类群）" if hook else ""))
    return ok


def _truncate(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断（不产生半个字符）。"""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _send_batched(hook: str, title: str, content: str) -> bool:
    """分批推送到指定 webhook（每批 ≤4KB，优先段落边界断开）。全部批次成功 True。"""
    full = f"## {title}\n---\n{content}"
    if len(full.encode("utf-8")) <= MAX_CONTENT_BYTES:
        return _send(full, title, hook)
    ok_all = True
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
        if not _send(header + batch + ("\n\n...(续)" if idx > 1 else ""), f"{title}({idx})", hook):
            ok_all = False
        remaining = remaining[len(batch):].strip()
    return ok_all


def push_markdown_batched(title: str, content: str, force: bool = False,
                          category: str = None) -> None:
    """按段落边界分批推送长 Markdown 到主群 webhook（每批 ≤4KB，标题带序号）。
    force=True 用于关键通知（如定时任务失败），不受业务推送开关限制。
    category 传入时改走 notify()（分类群 → 应用 → 主群 解析链）。

    ★ 2026-09-23：本函数是**全部业务推送的单点入口** ⇒ 在此处埋「信号总线」记录器
      （`flash/signal_bus.py`），即可覆盖 ~10 个各自独立的推送入口而**不必改调用方**。
      记录语义 = 「系统判断要说这件事」（进入即记，不保证企微真送达）；去重逻辑
      （notify 回落会二次进入）在 signal_bus 内。记录失败绝不影响推送本身。
    """
    try:
        from app.flash import signal_bus
        signal_bus.record(title, content, category=category, force=force)
    except Exception:
        pass
    # ★ 2026-09-15：全局兜底——表格转列表（见 notify 内注释；幂等）
    try:
        from app.wechat_fmt import markdown_tables_to_lists
        content = markdown_tables_to_lists(content)
    except Exception:
        pass
    if category:
        notify(category, title, content, force=force)
        return
    if not WECHAT_WEBHOOK:
        return
    if not force and not BUSINESS_ALERTS_ENABLED:
        return
    _send_batched(WECHAT_WEBHOOK, title, content)


def push_analysis(analysis: dict, clusters: list) -> None:
    """诊断流三段推送：诊断+情景 / 重点事件 / 策略+合规（→ brief 分类群）。"""
    hook = _hook_for("brief") or WECHAT_WEBHOOK
    if not hook:
        return
    if not BUSINESS_ALERTS_ENABLED:
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
    _send(p1, "诊断摘要", hook)

    events = analysis.get("top_events") or []
    if events:
        p2 = "### 🔍 重点事件分析\n"
        for e in events[:3]:
            urgent = " [紧急]" if e.get("time_sensitive") else ""
            p2 += (f"#### {urgent} {e.get('action')} {e.get('target')}\n"
                   f"**事件：** {e.get('cluster_name')} ({e.get('value_score')}分)\n"
                   f"**逻辑：** {e.get('why')}\n"
                   f"**链条：** {e.get('transmission_chain')}\n\n")
        _send(p2, "事件分析", hook)

    strategy = analysis.get("daily_strategy") or {}
    comp = analysis.get("d_state_compliance") or {}
    p3 = (f"### 📅 交易策略 [{strategy.get('max_position_confidence', '低')}置信度]\n"
          f"> **总仓位：{strategy.get('overall_position', '观望')}**\n"
          f"> **核心逻辑：** {strategy.get('core_logic', '无')}\n"
          f"> **禁入标的：** {' | '.join(strategy.get('do_not_touch') or []) or '无'}\n---\n"
          f"**D状态合规：** {comp.get('compliance_note', '已通过逻辑检查')}\n"
          f"**数据缺失：** {' | '.join(diag.get('missing_items') or []) or '无'}")
    _send(p3, "每日策略", hook)


def push_alerts(alerts: dict) -> None:
    """信号跟踪提醒（入场/出场/接近目标）。"""
    if not WECHAT_WEBHOOK and not _hook_for("risk"):
        return
    if not BUSINESS_ALERTS_ENABLED:
        return
    lines = []
    for a in alerts.get("entries", []):
        lines.append(a["message"])
    for a in alerts.get("exits", []):
        lines.append(a["message"])
    if lines:
        push_markdown_batched("🎯 交易信号提醒", "\n".join(lines), category="risk")


def push_strategy_signals(messages: list) -> None:
    """
    推送战法买入信号到企微（白名单战法盘后扫描出的新信号）。

    ★ 这是核心交易通知（用户明确要的买入提醒），不受 WECHAT_BUSINESS_ALERTS
    业务推送开关限制——只要配置了 WECHAT_WEBHOOK 即推送（与失败告警同等级）。
    消息由 strategies.recommendation.format_signal_message 生成（含买入逻辑/目标推导）。
    """
    if not WECHAT_WEBHOOK and not _hook_for("risk"):
        print("[wechat] 未配置 WECHAT_WEBHOOK/风险群，跳过战法信号推送")
        return
    if not messages:
        return
    for m in messages:
        notify("risk", "🎯 战法买入信号", m, force=True)
