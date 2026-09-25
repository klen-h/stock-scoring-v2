"""
================================================================================
【文件作用】交易员决策简报（PLAN_TRADER_WORKFLOW.md Phase 1）
================================================================================

核心思想：以资深交易员的视角，按一天的时间线（盘前/盘中/盘后）聚合系统已有
数据（矛盾/主线/主力/持仓/候选信号），由 LLM 生成三段式决策简报：
「该关注 / 该做 / 该防」——每条判断必须引用具体数据出处。

原则（见 PLAN_TRADER_WORKFLOW.md，v2 采纳两份外部评审）：
  1. 只聚合已有表的数据，不为简报新建采集任务
  2. AI 出观点，数据给出处（引用校验器对 6 位代码做成员检查，未命中标「待核实」）
  3. LLM 不可用/失败 → 规则骨架简报，绝不用 LLM 常识补写
  4. 「该做」段 = 确定性规则引擎渲染（LLM 永不发明动作），每条带 rule_id
  5. 形态定死：每段 ≤3 条 + 严重度排序；边界：简报=面向动作，日报=面向复盘

调度：Phase 1 先做 API 按需生成（前端加载时触发/手动刷新），
      企微推送（仅盘前 9:10 一次；19:35 不推——与 19:30 日报去重）列入收尾。
================================================================================
"""
import json
import os
import re
from datetime import datetime, timedelta, timezone

from app.database import db
from app.flash.llm import call_llm, llm_blocked_reason
from app.flash.rules import beijing_now

_BJ = timezone(timedelta(hours=8))

PHASES = {
    "premarket": (0, 570),      # 00:00-09:30
    "intraday": (570, 900),     # 09:30-15:00
    "postmarket": (900, 1440),  # 15:00-24:00
}


MAX_ITEMS_PER_SECTION = 3

# ★ 2026-09-13 P1-6：盘前固定展示「两融 5 日净变化 + 情绪温度计」。
#   两融 2.64 万亿历史高位仍在净增是磨底市常态；**转净减（≤ 该阈值）→
#   触发 sentiment_vs_margin severe（情绪热但杠杆资金撤 → 拉高出货结构）**，
#   是磨底市最关键的变盘领先指标。阈值可配（亿元）。
MARGIN_DRAIN_ALERT = float(os.environ.get("MARGIN_DRAIN_ALERT_YI", "-200") or -200)


def current_phase() -> str:
    now = beijing_now()
    t = now.hour * 60 + now.minute
    for phase, (lo, hi) in PHASES.items():
        if lo <= t < hi:
            return phase
    return "postmarket"


def init_trader_brief_table():
    db.execute("""
        CREATE TABLE IF NOT EXISTS trader_briefs (
            date TEXT NOT NULL,
            phase TEXT NOT NULL,
            markdown TEXT NOT NULL,
            data_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(date, phase)
        )
    """)


init_trader_brief_table()


# ================================================================
#  数据聚合（只读已有表）
# ================================================================

def collect_brief_data(phase: str) -> dict:
    """按 phase 聚合决策简报的输入数据（全部来自已有表，缺失静默降级）。"""
    today = beijing_now().strftime("%Y-%m-%d")
    data = {"date": today, "phase": phase}

    # 1) 市场状态
    try:
        r = db.fetch_one("SELECT date, state, weights_json FROM market_regime_history "
                         "ORDER BY date DESC LIMIT 1")
        if r:
            data["regime"] = {"date": r["date"], "state": r["state"]}
    except Exception:
        pass

    # 2) 矛盾（最近 2 日未解决 + 最近 3 条）
    try:
        unresolved = db.fetch(
            "SELECT date, level, type, title, severity FROM contradictions "
            "WHERE resolved = 0 ORDER BY date DESC LIMIT 6")
        if unresolved:
            data["contradictions"] = [
                {k: x[k] for k in ("date", "level", "type", "title", "severity")}
                for x in unresolved]
    except Exception:
        pass

    # 3) 主线榜（最近窗口的达标行业 + 趋势）
    try:
        from app.mainline import get_mainline_summary
        s = get_mainline_summary(12)
        if s.get("ok"):
            data["mainlines"] = [
                {"industry": m["industry"], "trend": m["trend"],
                 "recent": m["recent"], "latest_count": m["latest_count"]}
                for m in s["mainlines"][:5]]
            data["switches"] = s.get("switches", [])
    except Exception:
        pass

    # 4) 候选信号（最新快照 Top10，含主力标签）
    try:
        latest = db.fetch_one("SELECT MAX(rank_date) AS d FROM ranking_history")
        d = (latest or {}).get("d")
        if d:
            data["candidates_date"] = d
            data["candidates"] = [
                {k: x[k] for k in ("rank_pos", "code", "name", "total_score",
                                   "signal", "mainforce_signal")}
                for x in db.fetch(
                    "SELECT rank_pos, code, name, total_score, signal, mainforce_signal "
                    "FROM ranking_history WHERE rank_date = %s "
                    "ORDER BY rank_pos LIMIT 10", (d,))]
    except Exception:
        pass

    # 5) 模拟盘持仓（pending 待确认 + holding 在持）
    try:
        holdings = db.fetch(
            "SELECT code, name, strategy_name, signal_date, status, entry_price, "
            "fill_price FROM paper_positions "
            "WHERE status IN ('pending','holding') ORDER BY signal_date DESC LIMIT 15")
        if holdings:
            data["positions"] = [
                {k: x[k] for k in ("code", "name", "strategy_name", "signal_date",
                                   "status", "entry_price", "fill_price")}
                for x in holdings]
    except Exception:
        pass

    # 6) 战法信号（最新一日各战法计数）
    try:
        data["strategy_counts"] = [
            {k: x[k] for k in ("strategy_name", "scan_date", "count")}
            for x in db.fetch(
                "SELECT strategy_name, scan_date, count FROM strategy_results "
                "WHERE scan_date >= %s ORDER BY scan_date DESC LIMIT 8",
                ((datetime.now(_BJ) - timedelta(days=2)).strftime("%Y-%m-%d"),))]
    except Exception:
        pass

    # 7) 持仓风险矩阵（确定性聚合，2026-09-10 自 Phase 2 前移）
    risks = []
    for p in data.get("positions") or []:
        try:
            r = db.fetch_one(
                "SELECT mainforce_signal FROM ranking_history "
                "WHERE code = %s ORDER BY rank_date DESC LIMIT 1", (p["code"],))
            if r and r.get("mainforce_signal") == "distribution":
                risks.append({"code": p["code"], "name": p["name"],
                              "kind": "mainforce_distribution",
                              "detail": "最新评级仍标记出货嫌疑",
                              "severity": "high"})
        except Exception:
            pass
    if risks:
        data["position_risks"] = risks

    # 8) ★ 确定性「该做」清单（规则引擎，LLM 永不发明动作；每条带 rule_id）
    actions = []

    def _add(rule_id, severity, text):
        actions.append({"rule_id": rule_id, "severity": severity, "text": text})

    for c in data.get("contradictions") or []:
        if c.get("severity") == "severe":
            hit = any(p["name"] and p["name"] in (c.get("title") or "")
                      for p in data.get("positions") or [])
            _add("R1", "high",
                f"检查持仓敞口：{c['title']}（{c['date']}）" + ("——命中持仓" if hit else ""))

    for c in data.get("candidates") or []:
        if "买入" in (c.get("signal") or "") and \
                c.get("mainforce_signal") == "distribution":
            _add("R2", "high", f"暂缓买入 {c['name']}({c['code']})：评分买入但主力标记出货（冲突）")

    if phase == "premarket":
        for p in data.get("positions") or []:
            if p.get("status") == "pending":
                _add("R4", "medium", f"9:35 关注确认：{p['name']}({p['code']}) "
                                    f"（{_strategy_cn(p.get('strategy_name'))}，信号日 {p['signal_date']}）")

    for r in risks:
        # ★★ 2026-09-25 修 bug：此处原写作 `add(...)`，但本作用域定义的是 `_add`（见上方 L177）
        #   ⇒ **只要 risks 非空就抛 NameError**（"有持仓被标记主力出货"时），
        #   而 `collect_brief_data` 被 `build_decision_card` 与盘前简报**共用** ⇒ 两者一起失败
        #   （决策卡显示"生成失败"）。⚠️ 平时不触发，正是因为持仓里没有出货标记 —— 典型的
        #   「只在特定数据条件下爆炸」的 bug，靠走查而非运行很难发现。
        _add("R5", "high", f"持仓 {r['name']}({r['code']})：{r['detail']}")

    sev_order = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda a: sev_order.get(a["severity"], 3))
    data["actions"] = actions          # 全量落库（data_json），渲染时取前 3

    # 9) ★ 2026-09-13 P1-6：两融 5 日净变化 + 情绪温度计（**盘前固定展示**）。
    #    磨底市最关键的变盘领先指标：**转净减（≤ MARGIN_DRAIN_ALERT）→
    #    杠杆资金撤离，警惕拉高出货**。数据已在日报模板里，此处复用到盘前简报。
    #    ★ 仅在盘前采集：金十 mp-api 超时 20s，盘中/盘后简报不渲染这段，
    #      没必要为它们付出可能的等待成本（日报那边已有同样数据）。
    if phase == "premarket":
        try:
            from app.flash.margin_sentiment import (get_margin, margin_line,
                                                    sentiment_line)
            m_line, s_line = margin_line(), sentiment_line()
            if m_line or s_line:
                data["margin_sentiment"] = {"margin": m_line, "sentiment": s_line}
            mg = get_margin() or {}
            if mg.get("fund_bal_chg5") is not None:
                chg5 = float(mg["fund_bal_chg5"])
                data["margin_chg5"] = chg5
                if chg5 <= MARGIN_DRAIN_ALERT:
                    data["margin_alert"] = (
                        f"两融 5 日净减 {abs(chg5):.0f} 亿"
                        f"（≤{abs(MARGIN_DRAIN_ALERT):.0f}）——杠杆资金撤离，"
                        f"警惕拉高出货（sentiment_vs_margin severe 级）")
        except Exception as e:
            print(f"[trader_brief] 两融/情绪采集失败: {e}")

    return data


def _data_to_markdown(data: dict) -> str:
    """聚合数据 → LLM 输入 markdown（紧凑、带小节标题）。"""
    lines = [f"日期: {data.get('date')}  阶段: {data.get('phase')}"]
    if data.get("regime"):
        lines.append(f"市场状态: {data['regime']['state']}（{data['regime']['date']}）")
    if data.get("contradictions"):
        lines.append("未解决矛盾:")
        lines += [f"  - {_cons_tag(c)} {c['title']}（{c['date']}）"
                  for c in data["contradictions"]]
    if data.get("mainlines"):
        lines.append("行业主线（近12日扎堆Top50）:")
        lines += [f"  - {m['industry']}：近日均{m['recent']}只，趋势{m['trend']}，"
                  f"今日{m['latest_count']}只" for m in data["mainlines"]]
    if data.get("switches"):
        lines += [f"  风格切换: {w['industry']} {'流入' if w['action']=='in' else '退出'}"
                  f"（{w['from']}→{w['to']}只）" for w in data["switches"]]
    if data.get("candidates"):
        lines.append(f"评分候选（{data.get('candidates_date')} Top10）:")
        lines += [f"  - #{c['rank_pos']} {c['name']}({c['code']}) {c['total_score']}分 "
                  f"[{c['signal']}]" + (f" 主力:{c['mainforce_signal']}"
                                        if c.get("mainforce_signal") else "")
                  for c in data["candidates"]]
    if data.get("positions"):
        lines.append("模拟盘持仓/待确认:")
        lines += [f"  - {p['name']}({p['code']}) [{p['status']}] "
                  f"策略:{_strategy_cn(p.get('strategy_name'))} 信号日:{p['signal_date']}"
                  for p in data["positions"]]
    if data.get("strategy_counts"):
        lines.append("近2日战法信号数:")
        lines += [f"  - {_strategy_cn(s['strategy_name'])} {s['scan_date']}: {s['count']}只"
                  for s in data["strategy_counts"]]
    # ★ 2026-09-13 P1-6：两融 + 情绪温度计（喂给 LLM 判断"该防"段）
    ms = data.get("margin_sentiment") or {}
    if ms.get("margin") or ms.get("sentiment"):
        lines.append("两融与情绪（磨底市领先指标）:")
        if ms.get("margin"):
            lines.append(f"  - {ms['margin']}")
        if ms.get("sentiment"):
            lines.append(f"  - {ms['sentiment']}")
    if data.get("margin_alert"):
        lines.append(f"  ⚠️ 资金警示: {data['margin_alert']}")
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "你是一位严谨的A股短线交易员，管理一个模拟盘组合。"
    "只依据用户给出的数据做判断，禁止编造数据没有的信息；"
    "禁止给出任何操作建议（系统会单独渲染操作清单，你只负责观察与风险叙述）；"
    "禁止生成新的数字（引用数字时必须原样抄写输入中的数字）。"
    "输出为中文 markdown，严格两段：\n"
    "## 该关注\n（当前最值得注意的 2-3 个信号，按重要性排序，每条点名股票名或矛盾标题）\n"
    "## 该防\n（风险 1-3 条：矛盾信号/主力流出/消息负面/持仓风险，每条点名涉及的股票）\n"
    "语气克制，不喊单，不给确定性承诺。"
)




# ★ 2026-09-25（工作台反馈）：简报不再裸奔英文标识——战法 key/矛盾层级与严重度
#   统一中文渲染（LLM 输入与降级骨架/企微推送三处同源受益）。
_LEVEL_CN = {"L1": "预期差", "L2": "行为背离", "L3": "信息断层"}
_SEVERITY_CN = {"severe": "严重", "warn": "警告", "info": "提示"}


def _strategy_cn(name_en) -> str:
    """战法英文 key → 中文名（注册表查不到时回退原值）。"""
    try:
        from app.strategies.base import get_strategy
        s = get_strategy(str(name_en or ""))
        if s is not None and getattr(s, "name", ""):
            return s.name
    except Exception:
        pass
    return str(name_en or "")


def _cons_tag(c) -> str:
    """矛盾条目 → '[信息断层·严重]' 式中文标签。"""
    lv = _LEVEL_CN.get(str(c.get("level") or ""), str(c.get("level") or ""))
    sv = _SEVERITY_CN.get(str(c.get("severity") or ""), str(c.get("severity") or ""))
    return f"[{lv}·{sv}]" if (lv or sv) else ""


def _render_actions_md(actions: list) -> str:
    """确定性「该做」段：规则引擎输出渲染，≤3 条，严重度排序。"""
    if not actions:
        return "无可执行建议，观望。"
    sev = {"high": "[高]", "medium": "[中]"}
    return "\n".join(f"- {sev.get(a['severity'], '')} {a['text']}"
                     for a in actions[:MAX_ITEMS_PER_SECTION])


def _render_funding_md(data: dict, phase: str) -> str:
    """★ 2026-09-13 P1-6：两融 5 日净变化 + 情绪温度计（**仅盘前**固定展示）。

    磨底市最关键的变盘领先指标：两融高位仍在净增是常态，**转净减（≤阈值）
    意味着杠杆资金撤离 → 拉高出货结构**（sentiment_vs_margin severe）。
    """
    if phase != "premarket":
        return ""
    ms = data.get("margin_sentiment") or {}
    if not (ms.get("margin") or ms.get("sentiment")):
        return ""
    out = ["## 资金与情绪"]
    if ms.get("margin"):
        out.append(f"- {ms['margin']}")
    if ms.get("sentiment"):
        out.append(f"- {ms['sentiment']}")
    if data.get("margin_alert"):
        out.append(f"- ⚠️ **{data['margin_alert']}**")
    return "\n".join(out) + "\n\n"


def _fallback_skeleton(data: dict, reason: str) -> str:
    """LLM 不可用/失败时的规则骨架简报（绝不用 LLM 常识补写）。"""
    lines = [f"> AI 暂不可用（{reason}），以下为规则版简报（仅确定性内容）", ""]
    lines.append("## 该关注")
    cons = (data.get("contradictions") or [])[:MAX_ITEMS_PER_SECTION]
    lines += [f"- {_cons_tag(c)} {c['title']}（{c['date']}）"
              for c in cons] or ["- 无未解决矛盾"]
    ml = (data.get("mainlines") or [])[:MAX_ITEMS_PER_SECTION]
    if ml:
        lines.append("- 主线：" + "、".join(
            f"{m['industry']}({m['trend']})" for m in ml))
    return "\n".join(lines)


def _validate_refs(markdown: str, data: dict) -> str:
    """引用校验：叙述中的 6 位代码必须是数据内实体，未命中标「(待核实)」。"""
    known = set()
    for c in data.get("candidates") or []:
        known.add(c["code"])
    for p in data.get("positions") or []:
        known.add(p["code"])

    def _check(m):
        token = m.group(0)
        return token if token in known else f"{token}(待核实)"

    return re.sub(r"\b\d{6}\b", _check, markdown)



def generate_trader_brief(phase: str = None, force: bool = False,
                          reuse_data: bool = False) -> dict:
    """生成（或读取当日已生成的）决策简报。返回 {ok, phase, markdown, data}。

    ★ 2026-09-24 新增 `reuse_data`：**重试时复用已落库的采集结果**（`data_json`），
      不重新采集 —— 采集含金十 mp-api（超时 20s）/两融等慢接口，盘前窗口内重复采集
      既慢又无意义；而 10 分钟内盘前输入（昨收 + 今日事件）基本不变。
      这正是「失败后把内容存起来、等十分钟再触发」里的"内容"。
      ⚠️ 配 `force=True` 使用（否则 `force=False` 会直接返回表里那条**降级**记录，
         根本不会重新调用 LLM —— 这是重试路径必须注意的组合）。
    """
    init_trader_brief_table()
    phase = phase or current_phase()
    today = beijing_now().strftime("%Y-%m-%d")

    if not force:
        row = db.fetch_one("SELECT markdown FROM trader_briefs "
                           "WHERE date=%s AND phase=%s", (today, phase))
        if row:
            return {"ok": True, "date": today, "phase": phase,
                    "markdown": row["markdown"], "cached": True}

    blocked = llm_blocked_reason()
    if reuse_data:
        # ★ 2026-09-24：重试复用已存采集结果（见函数 docstring）。取不到则退化为重新采集。
        data = None
        try:
            row = db.fetch_one("SELECT data_json FROM trader_briefs "
                               "WHERE date=%s AND phase=%s", (today, phase))
            if row and row.get("data_json"):
                data = json.loads(row["data_json"])
        except Exception:
            data = None
        if not isinstance(data, dict) or not data:
            data = collect_brief_data(phase)
    else:
        data = collect_brief_data(phase)
    actions_md = _render_actions_md(data.get("actions") or [])
    degraded = None
    if blocked:
        narrative = _fallback_skeleton(data, blocked)
        degraded = blocked
    else:
        # ★ 2026-09-13：盘前 9:10 窗口时间敏感 → tier="fast"（LLM_TIMEOUT_FAST 短超时，
        #   免费站思考关闭；慢了立刻降级主力站）；盘后/周报走默认 slow
        narrative = call_llm(_SYSTEM_PROMPT, _data_to_markdown(data), temperature=0.3,
                             tier="fast" if phase == "premarket" else "slow")
        if not narrative:
            # ★ 2026-09-11：原来只说「空响应」，无从判断是模型把 token 全花在思考上、
            #   还是鉴权/网络异常（旧版 call_llm 三种失败都返回空串）。
            #   现在把 call_llm 记录的最近失败原因带出来，前端/日志直接可定位。
            err = ""
            try:
                from app.flash.llm import last_llm_error, LLM_MODEL
                err = last_llm_error() or f"model={LLM_MODEL}"
            except Exception:
                pass
            narrative = _fallback_skeleton(data, f"LLM 调用失败（空响应：{err}）")
            degraded = "llm_empty"

    narrative = _validate_refs(narrative, data)
    funding_md = _render_funding_md(data, phase)   # ★ P1-6：盘前固定展示两融+情绪
    md = (f"{narrative}\n\n## 该做\n{actions_md}\n\n{funding_md}"
          f"> 数据窗口：{data.get('candidates_date', today)}")

    data_json = json.dumps(data, ensure_ascii=False)
    db.execute("INSERT INTO trader_briefs (date, phase, markdown, data_json) "
               "VALUES (%s, %s, %s, %s) ON CONFLICT (date, phase) "
               "DO UPDATE SET markdown=EXCLUDED.markdown, "
               "data_json=EXCLUDED.data_json, created_at=CURRENT_TIMESTAMP",
               (today, phase, md, data_json))
    print(f"[trader_brief] {today}/{phase} 简报已生成"
          + (f"（降级: {degraded}）" if degraded else ""))
    items = (data.get("actions") or [])[:MAX_ITEMS_PER_SECTION]
    return {"ok": True, "date": today, "phase": phase, "markdown": md,
            "items": items, "degraded": degraded}


# ══════════════════════════════════════════════════════════════════════════
#  工作台·今日决策卡（2026-09-25 交易方法论 P1，规则引擎确定性输出，无 LLM）
#  回答四问：今日做不做 / 做什么 / 做多少 / 错了怎么办 + 持仓风险扫描
#  只读：不写库、不发推送；数据全部来自 collect_brief_data + 宏观快照 + 情绪快照
# ══════════════════════════════════════════════════════════════════════════

_REGIME_STANCE = {
    # 市况 → (档位, 总仓上限, 单票上限, 一句话)
    "offensive": ("开仓日", "总仓 ≤60%", "单票 ≤10%",
                  "趋势市，按战法白名单正常开仓"),
    "neutral": ("轻仓试错", "总仓 ≤30%", "单票 ≤10%",
                "震荡市，仅白名单战法、轻仓试错"),
    "neutral_bearish": ("空仓观察", "总仓 ≤30%（但战法入场全禁，实际 0 新仓）", "—",
                        "低波阴跌：战法入场已全禁，存量持仓按剧本管理"),
    "defensive": ("空仓观察", "总仓 ≤10%", "—",
                  "防御市，保留防御性持仓，停止开新仓"),
}


def build_decision_card() -> dict:
    """今日决策卡（确定性规则聚合）：做不做/做什么/做多少/错了怎么办 + 持仓扫描。

    ★ 2026-09-25（交易方法论 P1，工作台盘前）：
      - 无 LLM：全部规则/数据聚合，确定性输出，只读不写库不发推送
      - 档位映射 _REGIME_STANCE；止损规则引用退出 v2 常量（口径单源）
      - 持仓扫描复用 portfolio_radar（alerts/主力阶段），环境匹配一句话
    """
    from app.flash import rules as flash_rules
    from app.macro import get_macro_panel
    from app.strategies.recommendation import get_push_whitelist
    from app.backtest.strategies import WARFARE_HOLD_DAYS_V2, WARFARE_STOP_PCT_V2

    data = collect_brief_data("premarket")
    reg = (data.get("regime") or {}).get("state") or "unknown"
    stance = _REGIME_STANCE.get(reg) or ("数据不足", "—", "—", "市况未判定，观望")

    # 市场环境温度（/market/temperature 同源：全市场实时缓存计算；独立于两融情绪温度计）
    temp = None
    try:
        from app.routers.market import market_temperature as _mtemp
        temp = (_mtemp() or {}).get("temperature")
    except Exception:
        temp = None

    # 情绪快照（涨停/跌停/赚钱效应/连板高度）
    em = None
    try:
        from app.routers.market import market_emotion
        em = market_emotion() or {}
    except Exception:
        em = {}
    emotion_verdict = em.get("verdict")
    # ★ 2026-09-25：两处修订 ——
    #   ① 文案：原"昨日涨停今日 X%"缺"平均"，易被读成"今日大盘涨跌"⇒ 改「昨涨停股今均」+ 股数。
    #   ② ⚠️ 原写法 `em.get('prev_limit_today_pct', '—')` 有坑：`.get(k, default)` **只在 key
    #      缺失时**才用 default，**值为 None 时照样返回 None** ⇒ 简报会打出 "None%"。
    #      且不能用 `or '—'`（会把合法的 **0.0** 也变成"—"，0% 是有效读数）⇒ 显式判 None。
    _pct = em.get("prev_limit_today_pct")
    _cnt = em.get("prev_limit_count")
    emotion_detail = (f"涨停 {em.get('limit_up', '—')}/跌停 {em.get('limit_down', '—')} · "
                      f"连板高度 {em.get('max_streak', '—')} · "
                      f"昨涨停股今均{('（' + str(_cnt) + '只）') if _cnt else ''} "
                      f"{_pct if _pct is not None else '—'}%")

    # 做什么：白名单战法（中文名）+ 评分候选 Top3 + 回避方向（宏观空头标签）
    wl = [_strategy_cn(x) for x in get_push_whitelist()]
    candidates = [
        {"rank": c.get("rank_pos"), "code": c.get("code"), "name": c.get("name"),
         "score": c.get("total_score"), "signal": c.get("signal")}
        for c in (data.get("candidates") or [])[:3]
    ]
    avoid = (data.get("macro") or {}).get("tags_bear") or []
    avoid = [t for t in avoid][:3]

    # 错了怎么办：v2 常量 + 退潮信号（宽度不足且昨涨停溢价为负 → 禁止接力）
    stop_rule = (f"按 v2 纪律：介入价 ×(1−{WARFARE_STOP_PCT_V2:.0%}) 止损、"
                 f"{WARFARE_HOLD_DAYS_V2} 个交易日到期，跌破计划位机械执行")
    retreating = None
    try:
        if (em.get("limit_up") or 0) < 20 and (em.get("prev_limit_today_pct") or 0) < 0:
            retreating = "涨停宽度不足且昨涨停溢价为负 → 禁止接力，降仓"
    except Exception:
        pass

    # 持仓风险扫描（portfolio_radar：alerts/主力阶段/盈亏；环境匹配一句话）
    positions_scan = []
    try:
        from app.portfolio_radar import build as _radar_build
        for it in (_radar_build().get("items") or []):
            fit = None
            try:
                if reg in ("neutral_bearish", "neutral") and (it.get("pnl_pct") or 0) > 0:
                    fit = "防御持仓与偏冷环境匹配 ✓"
            except Exception:
                pass
            positions_scan.append({
                "code": it.get("code"), "name": it.get("name"),
                "pnl_pct": it.get("pnl_pct"), "phase_cn": it.get("phase_cn"),
                "alerts": it.get("alerts") or [], "fit": fit,
            })
    except Exception as e:
        print(f"[decision-card] 持仓扫描失败: {e}")

    # ★ 2026-09-25：负面清单聚合（用户需求："负面清单和主线推荐同等重要"）。
    #   ⚠️ **零新增数据源** —— 素材早已算好，此前只是散在别处、没有汇总成"今天要避开的"：
    #     · `data["position_risks"]`（L157-172 已聚合：持仓 ∩ 主力出货嫌疑，带 severity）
    #     · `data["candidates"]` 里 `mainforce_signal == "distribution"`
    #       （评分看多 ∩ 主力出货 = 信号冲突，对应 actions 的 R2，但这里能拿到 code 便于点击）
    #     · `data["actions"]` 的 R1（矛盾扫描命中持仓敞口）
    #   覆盖范围说明（诚实标注）：**不含**解禁/减持/停牌/问询/新股 —— 那些无数据源
    #   （工作台注释自述"公告/解禁类待 C1 数据源"）⇒ 接入后在此追加即可。
    negatives = []
    for r in (data.get("position_risks") or []):
        negatives.append({"code": r.get("code"), "name": r.get("name"), "level": "high",
                          "scope": "持仓", "reason": r.get("detail") or "主力出货嫌疑"})
    for c in (data.get("candidates") or []):
        if (c.get("mainforce_signal") or "") == "distribution":
            negatives.append({"code": c.get("code"), "name": c.get("name"), "level": "high",
                              "scope": "候选",
                              "reason": (f"评分 {c.get('total_score')} 分看多，但主力标记出货"
                                         "（信号冲突，暂缓买入）")})
    for a in (data.get("actions") or []):
        if a.get("rule_id") == "R1":
            negatives.append({"code": None, "name": None,
                              "level": a.get("severity") or "high", "scope": "矛盾",
                              "reason": a.get("text") or ""})

    return {
        "date": data.get("date"),
        "regime": reg,
        "stance": {"level": stance[0], "total_cap": stance[1],
                   "single_cap": stance[2], "why": stance[3]},
        "environment": {"temperature": temp,
                        "emotion_verdict": emotion_verdict,
                        "emotion_detail": emotion_detail},
        "do": {"whitelist": wl, "candidates": candidates, "avoid": avoid},
        "how_much": {"total_cap": stance[1], "single_cap": stance[2]},
        "if_wrong": {"stop_rule": stop_rule, "retreating": retreating},
        # ★ 负面清单（今天要避开的）；空数组 ⇒ 前端不渲染该块
        "negatives": negatives,
        "positions_scan": positions_scan,
    }
