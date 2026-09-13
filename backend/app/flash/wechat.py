"""
================================================================================
【文件作用】企业微信推送（可选，移植自 fetch-flash.js / review.js 的推送部分）
================================================================================

环境变量：WECHAT_WEBHOOK（未配置时所有推送静默跳过，只走 Web 界面）。
企业微信 markdown 消息上限 4096 字节，超长自动按段落分批。

★ 2026-09-13 分类推送层（notify）：
  自建应用消息（个人项目企业，支持按人路由/多应用分类）优先，
  发送失败自动回落群机器人 webhook——自建应用有「可信IP」限制
  （动态出口 IP 的 Render/Actions/本机都可能被 60020 拦），回落保证不丢通知。
  配置：
    WECHAT_APP_CORP_ID=wwXXXX                     # 项目企业 ID（全局）
    WECHAT_NOTIFY_RISK/COACH/BRIEF/ALERT=agentid:secret   # 分应用（未建的应用不配）
    WECHAT_NOTIFY_DEFAULT=agentid:secret          # 未专属配置的分类共用
    WECHAT_NOTIFY_TOUSER=HuangHeLiang|user2       # 默认接收人（通讯录账号）
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
_circuit = {"key": None, "fails": 0, "until": 0.0}


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
    if _circuit["key"] != key:
        return False
    if _circuit["fails"] >= _APP_FAIL_THRESHOLD and _time.time() < _circuit["until"]:
        return True
    if _time.time() >= _circuit["until"]:
        _circuit.update(key=None, fails=0, until=0.0)
    return False


def _mark_app_fail(key: str, why: str) -> None:
    if _circuit["key"] != key:
        _circuit.update(key=key, fails=0, until=0.0)
    _circuit["fails"] += 1
    if _circuit["fails"] >= _APP_FAIL_THRESHOLD:
        _circuit["until"] = _time.time() + _APP_COOLDOWN_SEC
        print(f"[wechat] 应用通道连续失败 {_circuit['fails']} 次（{why}）→ 熔断 30min，回落群 webhook")


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
    """分类推送统一入口：自建应用消息优先 → 失败/未配置回落群 webhook。

    category: risk / coach / brief / alert（决定用哪个应用 + 接收人语义）
    返回是否至少有一条通道送达（供调用方记日志/告警）。
    """
    if not force and not BUSINESS_ALERTS_ENABLED:
        return False   # 与 push_markdown_batched 的业务开关语义一致
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
                _circuit.update(key=None, fails=0, until=0.0)
                return True
            _mark_app_fail(key, title[:30])
    # 回落：群机器人（原有逻辑，force 语义一致）
    push_markdown_batched(title, content, force=force)
    return bool(WECHAT_WEBHOOK)


def _send(content: str, label: str) -> bool:
    if not WECHAT_WEBHOOK or not content or not content.strip():
        return False
    try:
        r = _session.post(WECHAT_WEBHOOK,
                          json={"msgtype": "markdown", "markdown": {"content": content}},
                          timeout=30)
        ok = r.json().get("errcode") == 0
    except Exception as e:
        print(f"[wechat] {label} 推送失败: {e}")
        return False
    # print 放 try 外，避免控制台编码问题（如 Windows GBK 下的 emoji）影响发送结果
    print(f"[wechat] [{'OK' if ok else 'FAIL'}] {label}")
    return ok


def _truncate(text: str, max_bytes: int) -> str:
    """按 UTF-8 字节截断（不产生半个字符）。"""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def push_markdown_batched(title: str, content: str, force: bool = False) -> None:
    """按段落边界分批推送长 Markdown（每批 ≤4KB，标题带序号）。
    force=True 用于关键通知（如定时任务失败），不受业务推送开关限制。"""
    if not WECHAT_WEBHOOK:
        return
    if not force and not BUSINESS_ALERTS_ENABLED:
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
    if not BUSINESS_ALERTS_ENABLED:
        return
    lines = []
    for a in alerts.get("entries", []):
        lines.append(a["message"])
    for a in alerts.get("exits", []):
        lines.append(a["message"])
    if lines:
        push_markdown_batched("🎯 交易信号提醒", "\n".join(lines))


def push_strategy_signals(messages: list) -> None:
    """
    推送战法买入信号到企微（白名单战法盘后扫描出的新信号）。

    ★ 这是核心交易通知（用户明确要的买入提醒），不受 WECHAT_BUSINESS_ALERTS
    业务推送开关限制——只要配置了 WECHAT_WEBHOOK 即推送（与失败告警同等级）。
    消息由 strategies.recommendation.format_signal_message 生成（含买入逻辑/目标推导）。
    """
    if not WECHAT_WEBHOOK:
        print("[wechat] 未配置 WECHAT_WEBHOOK，跳过战法信号推送")
        return
    if not messages:
        return
    for m in messages:
        _send(m, "战法买入信号")
