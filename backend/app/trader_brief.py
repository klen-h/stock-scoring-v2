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

def collect_brief_data(phase: str, date: str = None) -> dict:
    """按 phase 聚合决策简报的输入数据（全部来自已有表，缺失静默降级）。

    ★ 2026-09-25（用户批准修 P0）：`date` = **本轮要处理的交易日**，日批补跑时必须传。
      【为什么】日批语义是"处理最近一个**已完成**交易日"，但它可能在跨午夜（或休市日）
      才跑到 ⇒ 若这里用 `beijing_now()`，`data["date"]` 会标成"今天/周六"，
      而数据全部来自上一交易日 ⇒ **简报自我描述错误**（"数据窗口 09-26"却写着 09-25 的事）。
      ⚠️ 本字段只作**标记**：下游取数一律 `MAX(...)` / `ORDER BY DESC` 取最新
        （见 5)/6) 的查询）⇒ **传参不改变取数结果，只修正日期标注**。
      ★ 泛化：凡"以日期为键"的函数，被批处理调用时都要能**接收外部日期**，
        不能自己 `now()` —— 否则跨午夜补跑必然写错日期（本项目已踩过多次）。
    """
    today = date or beijing_now().strftime("%Y-%m-%d")
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
                          reuse_data: bool = False, date: str = None) -> dict:
    """生成（或读取当日已生成的）决策简报。返回 {ok, phase, markdown, data}。

    ★ 2026-09-24 新增 `reuse_data`：**重试时复用已落库的采集结果**（`data_json`），
      不重新采集 —— 采集含金十 mp-api（超时 20s）/两融等慢接口，盘前窗口内重复采集
      既慢又无意义；而 10 分钟内盘前输入（昨收 + 今日事件）基本不变。
      这正是「失败后把内容存起来、等十分钟再触发」里的"内容"。
      ⚠️ 配 `force=True` 使用（否则 `force=False` 会直接返回表里那条**降级**记录，
         根本不会重新调用 LLM —— 这是重试路径必须注意的组合）。

    ★★ 2026-09-25（用户批准修 P0）：新增 `date` = **本轮要处理的交易日**（`YYYY-MM-DD`）。
      【为什么必须加】`today` 是幂等键 + 落库主键，而它原先**硬编码 `beijing_now()`**
      ⇒ 日批跨午夜补跑（周六 00:30 跑周五的批）时会：
        · 于 `is_trading_day` 判断处被跳过（旧日批逻辑）⇒ **周五盘后简报永远补不出来**；
        · 即使放行，也会往 `trader_briefs` 写一条**日期=周六**的错行。
      ⇒ 根治 = 让日期**由调用方传入**（日批传 `_batch_trading_day()`）。
      ⚠️ 不传时行为与原来完全一致（默认 `beijing_now()`）⇒ **纯新增参数，零回归**。
      （落库是 `ON CONFLICT (date, phase) DO UPDATE` ⇒ 补跑写历史日期安全、幂等。）
    """
    init_trader_brief_table()
    phase = phase or current_phase()
    today = date or beijing_now().strftime("%Y-%m-%d")

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
            data = collect_brief_data(phase, date=today)
    else:
        data = collect_brief_data(phase, date=today)
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

# ★ 2026-09-25：持仓「预案」动作等级（前端按此配色）。
#   用户反馈："持仓联动（目前最弱）——持仓 1 只工行、风险 0、机会 0；盘前应对持仓自动扫描…
#   1 只低波银行在 28.1 分环境下其实是合理的，**这个结论应该由系统说出来**。"
PLAN_ACT = "act"          # 有风险提示 ⇒ 今天优先处理
PLAN_EXIT = "exit"        # 该股档位为 0 ⇒ 不持有/择机清
PLAN_PROTECT = "protect"  # 浮盈较高 ⇒ 保护利润
PLAN_HOLD = "hold"        # 其余 ⇒ 按建议仓位持有


def _position_plan(pnl, alerts, suggested_pct, position_label, fit=None):
    """把「持仓现状 + 仓位建议 + 环境匹配」合成一句 **今天怎么处理它**。

    ★ 设计取舍：**确定性规则、无 LLM**，且**只给纪律提醒不给交易指令**
      （沿用 `coach/monitor.py::POSITION_NOTE` 的口径，别写"系统已自动卖出"这类话）。
    ★ 优先级（高→低）：风险提示 ⇒ 档位为 0 ⇒ 高浮盈保护 ⇒ 环境匹配 ⇒ 持有。
      ⚠️ 故意**不做**"减仓到 X%"这类推算：我们只知道建议档位，不知道你的总资产占比，
        硬凑比例就是臆测（宁缺勿编）。
    """
    risks = [a for a in (alerts or []) if (a or {}).get("level") == "risk"]
    if risks:
        return {"level": PLAN_ACT,
                "text": f"优先处理：{risks[0].get('text') or '有风险提示'}"}
    if suggested_pct == 0:
        return {"level": PLAN_EXIT,
                "text": f"建议仓位 0%（{position_label or '档位为 0'}）⇒ 不持有/择机清"}
    if pnl is not None and pnl >= 20.0:
        return {"level": PLAN_PROTECT,
                "text": f"浮盈 {pnl:.1f}% ⇒ 考虑保护利润（上移止盈位或减半）"}
    if fit:
        # 环境匹配的结论由系统说出来（用户明确要求）
        return {"level": PLAN_HOLD,
                "text": f"持有（建议仓位 ≤{suggested_pct}%）；{fit}"}
    if suggested_pct is not None:
        return {"level": PLAN_HOLD, "text": f"持有（建议仓位 ≤{suggested_pct}%）"}
    return {"level": None, "text": "—（仓位建议缺失，仅作参考）"}


# ══════════════════════════════════════════════════════════════════════════
#  战法信号 × 行业 交叉表 —— 2026-09-25（用户需求 P2）
# ══════════════════════════════════════════════════════════════════════════
# 【要解决的问题】用户原话：
#   · "决策简报：ma_convergence_breakout 说『需结合行业分布判断』，但页面没给分布 ——
#      **58 只信号的行业交叉表应该直接画出来**。"
#   · "融捷（锂）和焦作万方（电解铝）笼统归入『有色/化工链条』，**分类口径要标注**。"
#
# 【数据源】零新增网络：`strategy_results`（最新扫描日） + `stock_industry`（映射表）。
#   ⚠️ 性能实测（2026-09-25）：最新一日 **10 行共 331 KB**（最大 ma_convergence_breakout 190 KB /
#      single_yang_unbroken 116 KB）。`gate_history.py` 注释里的"读 6 行大 JSON 要 15.7s"是
#      **热路径上叠加查询**所致；本函数单独查 + 30 分钟进程缓存 ⇒ 可忽略。
#
# 【口径标注】`main_industry` 是**归一化后的一级行业**（如"有色"），而
#   `main_industry_code` 保留**原始细分名**（如"锂"/"电解铝"）⇒ 后者正是用户要的"口径"。
#   ⚠️ 但实测发现两种来源**混用**：新浪源存的是 node（`new_dlhy`/`new_dqhy`，**不可读**），
#      东财源存的是原始行业名（`油服工程`/`塑料`，可读）⇒ **必须过滤**（见 _raw_industry_label）。
_SIG_IND_CACHE = {"ts": 0.0, "val": None}
_SIG_IND_TTL = 1800.0        # 30 分钟：信号是日频，缓存半天也无害


def _raw_industry_label(raw: str) -> str:
    """原始行业名的**可读化**：只保留含中文的（行业名一定是中文）。

    ⚠️ 实测（2026-09-25，**真实数据**）：`main_industry_code` 里混着多种**不可读代码** ——
        新浪 node（`new_dlhy`）、东财板块码（**`BK1386`/`BK0440`**）、以及 `-`。
        ⇒ 判据不能只列举前缀（`new_`），改为「**必须含中文**」⇒ 更鲁棒，新增来源也不用再改。
      （打桩测试是发现不了 `BK1386` 的 —— 造不出真实全集 ⇒ **必须真跑一次真实数据**。）
    """
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not any("\u4e00" <= ch <= "\u9fff" for ch in raw):
        return ""
    return raw


def signal_industry_cross(limit: int = 12) -> dict:
    """战法信号 × 行业 交叉表：每个行业有多少信号、由哪些战法贡献、**原始细分口径**是什么。

    返回 `{date, rows: [{industry, count, strategies: {战法: n}, codes, raw}], total_codes, note}`；
    `rows` 按信号数降序、截断 `limit` 条。**失败静默**返回空结构（辅助信息不拖垮调用方）。
    """
    import json as _json
    out = {"date": None, "rows": [], "total_codes": 0, "note": None}
    try:
        r = db.fetch_one("SELECT MAX(scan_date) AS d FROM strategy_results")
        d = str((r or {}).get("d") or "")
        if not d:
            out["note"] = "暂无战法扫描结果"
            return out
        rows = db.fetch(
            "SELECT strategy_name, results_json FROM strategy_results WHERE scan_date = %s", (d,))
        code_map: dict = {}          # code -> {code, name, strategies: [战法名]}
        for x in rows or []:
            sn = x.get("strategy_name") or ""
            try:
                arr = _json.loads(x.get("results_json") or "[]")
            except (ValueError, TypeError):
                continue
            if isinstance(arr, dict):                    # 兼容未来可能的 {signals: []} 结构
                arr = arr.get("signals") or arr.get("data") or []
            for it in (arr or []):
                if not isinstance(it, dict):
                    continue
                c = str(it.get("code") or "").strip()
                if not c:
                    continue
                e = code_map.setdefault(c, {"code": c, "name": it.get("name") or c,
                                            "strategies": []})
                if sn and sn not in e["strategies"]:
                    e["strategies"].append(sn)
        if not code_map:
            out["note"] = "扫描结果里没有可解析的信号明细"
            return out
        # 行业映射：分批 IN（避免单条 SQL 参数过多）
        codes = list(code_map)
        ind: dict = {}
        for i in range(0, len(codes), 500):
            chunk = codes[i:i + 500]
            ph = ",".join(["%s"] * len(chunk))
            for r2 in db.fetch(
                    f"SELECT code, main_industry, main_industry_code FROM stock_industry "
                    f"WHERE code IN ({ph})", chunk) or []:
                ind[str(r2.get("code"))] = (r2.get("main_industry") or "未映射",
                                            _raw_industry_label(r2.get("main_industry_code")))
        agg: dict = {}
        for c, e in code_map.items():
            m, raw = ind.get(c, ("未映射", ""))
            a = agg.setdefault(m, {"industry": m, "count": 0, "codes": [],
                                   "raw": set(), "strategies": {}})
            a["count"] += 1
            a["codes"].append(c)
            if raw:
                a["raw"].add(raw)
            for sn in e["strategies"]:
                # ★ 直接用**中文战法名**做 key（复用 `_strategy_cn` 唯一映射源）——
                #   避免前端再抄一份英文→中文表（那正是项目里"前端写死 vs 后端动态漂移"的老坑）。
                cn = _strategy_cn(sn) or sn
                a["strategies"][cn] = a["strategies"].get(cn, 0) + 1
        out["date"] = d
        out["total_codes"] = len(code_map)
        out["rows"] = sorted(agg.values(), key=lambda x: -x["count"])[:max(1, limit)]
        for a in out["rows"]:
            a["raw"] = sorted(a["raw"])[:4]              # 原始细分名（口径标注，最多 4 个）
            a["top_strategy"] = (max(a["strategies"].items(), key=lambda kv: kv[1])[0]
                                 if a["strategies"] else None)
    except Exception as e:
        # ASCII（项目铁律⑥：本地 GBK 控制台中文 print 会抛 UnicodeEncodeError）
        print(f"[trader_brief] signal x industry cross failed: {e}")
        out["note"] = "计算失败"
    return out


def signal_industry_cached(limit: int = 12) -> dict:
    """`signal_industry_cross` 的 30 分钟进程缓存包装（失败也不写缓存，便于下次重试）。

    ⚠️ `import time` 必须在**函数内**（本模块顶层没有 time）。★ 2026-09-25 踩坑：
      漏了它 ⇒ **调用时**才抛 `NameError`，而 `import app.trader_brief` **抓不到**
      （定义期不求值函数体）⇒ **验证纪律升级：新增函数必须被真实调用一次**，
      仅做 import 冒烟只能挡住"定义期"错误（如缺 `Optional` 的注解）。
    """
    import time
    now = time.time()
    c = _SIG_IND_CACHE.get("val")
    if c is not None and now - _SIG_IND_CACHE["ts"] < _SIG_IND_TTL:
        return c
    val = signal_industry_cross(limit)
    if val.get("rows"):
        _SIG_IND_CACHE.update(ts=now, val=val)
    return val


# ══════════════════════════════════════════════════════════════════════════
#  战法质量 · 「为什么推送静默」—— 2026-09-25（用户需求 A）
# ══════════════════════════════════════════════════════════════════════════
# 【要解决的问题】决策卡"做什么"一栏只写「无白名单战法（推送静默）」，读者**分不清**是：
#     · **市场不对**（战法在防守市天然失效）⇒ 正常，等环境转好，不该改战法
#     · **战法坏了**（真的衰减/失效）      ⇒ 要下架或重调
#     · **系统故障**（重算失败/缓存过期）  ⇒ 要修
#   三者处置完全不同，而界面上长得一模一样。
# 【做法】**零新增计算**：`recommendation._recompute_whitelist()` 早已把这些算好并落库到
#   `whitelist_state`（key='whitelist'，6h TTL），本函数**只读不重算**
#   （重算要 ~30s 且读 strategy_results 全表 + 闸门全历史），再叠加当前 `regime` 让那句"静默"自解释。
# ⚠️ 失败静默返回空结构 —— 辅助信息绝不拖垮决策卡。
_STRAT_Q_CACHE = {"ts": 0.0, "val": None}
_STRAT_Q_TTL = 600              # 10 分钟（数据源自身 6h TTL 且只在盘后重算，无需频繁读库）

_REGIME_CN_SHORT = {"offensive": "进攻型（牛市/强势上涨）",
                    "neutral": "震荡型（盘整/无方向）",
                    "neutral_bearish": "震荡偏空（重心下移，反弹宜减不宜追）",
                    "defensive": "防御型（熊市/弱势下跌）"}


def strategy_quality() -> dict:
    """战法质量 + **「为什么静默」的自解释**。只读 `whitelist_state`，不触发重算。"""
    out = {"available": False, "why": None, "whitelist": [], "rows": [],
           "alerts": [], "regime": None, "criterion": None,
           "computed_at": None, "note": None}
    # 当前市场状态：先读进程内存缓存（零 DB 开销），**空了必须回退落库表**
    #   ⚠️ 实测踩到：`get_regime_cache()` 只是**进程内存**，新进程/当日尚未判定时返回 `{}`
    #   ⇒ 若只靠它，"市场处于防御态"这句**本功能的核心解释会静默消失**
    #   （页面照常渲染、只是少了一行字 —— 这种缺失最难被发现）。
    try:
        rc = {}
        try:
            from app.backtest.market_regime import get_regime_cache
            rc = get_regime_cache() or {}
        except Exception:
            rc = {}
        if not rc.get("state"):
            from app.database import db
            _row = db.fetch_one("SELECT date, state, regime_score, adx, ma_trend "
                                "FROM market_regime_history ORDER BY date DESC LIMIT 1")
            if _row:
                rc = {"date": _row.get("date"), "state": _row.get("state"),
                      "detail": {"regime_score": _row.get("regime_score"),
                                 "adx": _row.get("adx"),
                                 "ma_trend": _row.get("ma_trend")}}
        if rc.get("state"):
            _d = rc.get("detail") or {}
            out["regime"] = {"state": rc.get("state"),
                             "cn": _REGIME_CN_SHORT.get(rc.get("state"), rc.get("state")),
                             "date": rc.get("date"),
                             "score": _d.get("regime_score"),
                             "ma_trend": _d.get("ma_trend")}
    except Exception as e:
        print(f"[trader_brief] regime for strategy quality failed: {e}")   # ASCII（铁律⑥）
    try:
        import json as _json
        from app.database import db
        from app.strategies.recommendation import STRATEGY_ZH
        row = db.fetch_one("SELECT value_json, updated_at FROM whitelist_state "
                           "WHERE key='whitelist'")
        if not row:
            out["note"] = "尚无战法质量统计（盘后重算后落库）"
            return out
        v = row.get("value_json")
        v = _json.loads(v) if isinstance(v, str) else (v or {})
        stats = v.get("stats") or {}
        out["available"] = True
        out["whitelist"] = v.get("list") or []
        out["alerts"] = v.get("alerts") or []
        out["criterion"] = v.get("criterion")
        out["computed_at"] = row.get("updated_at")
        alert_by_key = {str(a).split(":")[0].strip(): a for a in out["alerts"]}
        wl = set(out["whitelist"])
        for k, s in stats.items():
            if not isinstance(s, dict):
                continue
            out["rows"].append({
                "key": k, "cn": STRATEGY_ZH.get(k, k),
                "n": s.get("n"), "wins": s.get("wins"), "losses": s.get("losses"),
                "win_rate": s.get("win_rate"), "avg_ret": s.get("avg_ret"),
                "profit_factor": s.get("profit_factor"), "median_ret": s.get("median_ret"),
                "recent_n": s.get("recent_n"), "recent_win_rate": s.get("recent_win_rate"),
                "recent_avg_ret": s.get("recent_avg_ret"),
                "pass": bool(s.get("pass_all_time")) and bool(s.get("pass_recent")),
                "insufficient": bool(s.get("recent_insufficient")),
                "alert": s.get("half_life_alert") or alert_by_key.get(k),
            })
        # 排序：未达标且样本大的排前面（"问题最大"的先看到）；样本不足的沉底
        out["rows"].sort(key=lambda x: (x["insufficient"] or False,
                                        x["pass"] or False,
                                        -(x["n"] or 0)))
        # ── ★ 核心：把"静默"翻译成人话 ──────────────────────────────
        if out["whitelist"]:
            out["why"] = ("当前可推送：" + "、".join(
                STRATEGY_ZH.get(x, x) for x in out["whitelist"]))
        else:
            reasons = []
            if not any(r["pass"] for r in out["rows"]):
                reasons.append("无战法达到判据（期望值/胜率双轨）")
            st = (out["regime"] or {}).get("state")
            if st in ("defensive", "neutral_bearish"):
                reasons.append("市场处于"
                               + _REGIME_CN_SHORT.get(st, st)
                               + "——形态突破类战法在弱势市天然失效")
            if out["alerts"]:
                reasons.append(f"{len(out['alerts'])} 个战法出现半衰期衰减"
                               "（后半段胜率不足前半段一半）")
            # ⚠️ 这里**不能写 Markdown 的 `**`**：前端是纯文本插值渲染，
            #   星号会原样显示出来（本句是给人读的解释，不是 markdown 消息）。
            out["why"] = ("；".join(reasons) +
                          " ⇒ 按设计静默推送（宁可不推，也不推正在衰减的信号）"
                          if reasons else "白名单为空 ⇒ 静默推送")
    except Exception as e:
        print(f"[trader_brief] strategy quality failed: {e}")              # ASCII（铁律⑥）
        out["note"] = "计算失败"
    return out


def strategy_quality_cached() -> dict:
    """`strategy_quality` 的 10 分钟进程缓存（**失败不写缓存**，便于下次重试）。

    ⚠️ `import time` 在函数内（本模块顶层无 `time`；漏了会在**调用时**抛 NameError
      而 import 冒烟抓不到 —— 2026-09-25 已踩过一次，见 `signal_industry_cached` 注释）。
    """
    import time
    now = time.time()
    c = _STRAT_Q_CACHE.get("val")
    if c is not None and now - _STRAT_Q_CACHE["ts"] < _STRAT_Q_TTL:
        return c
    val = strategy_quality()
    if val.get("available"):
        _STRAT_Q_CACHE.update(ts=now, val=val)
    return val


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

    # 持仓扫描 + 预案（★ 2026-09-25：从"它现在怎样"升级为"**今天怎么处理它**"）
    #   数据全现成、零新增网络：
    #     · portfolio_radar.build()  → 盈亏/主力阶段/alerts（含 -8%/-12%/冲高回落等风险阈值）
    #     · position_sizing_for_portfolio() → 个股档位 suggested_pct（= min(个股档位, 总上限)）
    #   用户原话："持仓联动（目前最弱）…这个结论应该由系统说出来"。
    sizing_by_code = {}
    try:
        from app.coach.position_sizing import position_sizing_for_portfolio
        for s in (position_sizing_for_portfolio().get("positions") or []):
            sizing_by_code[str(s.get("code"))] = s
    except Exception as e:
        # ASCII（项目铁律⑥：本地 GBK 控制台中文 print 会抛 UnicodeEncodeError）
        print(f"[decision-card] position sizing failed: {e}")

    positions_scan = []
    try:
        from app.portfolio_radar import build as _radar_build
        for it in (_radar_build().get("items") or []):
            fit = None
            try:
                if reg in ("neutral_bearish", "neutral") and (it.get("pnl_pct") or 0) > 0:
                    fit = "防御持仓与偏冷环境匹配"
            except Exception:
                pass
            alerts = it.get("alerts") or []
            _sz = sizing_by_code.get(str(it.get("code"))) or {}
            _sug = _sz.get("suggested_pct")
            plan = _position_plan(it.get("pnl_pct"), alerts, _sug,
                                  _sz.get("position_label"), fit)
            positions_scan.append({
                "code": it.get("code"), "name": it.get("name"),
                "pnl_pct": it.get("pnl_pct"), "phase_cn": it.get("phase_cn"),
                "alerts": alerts, "fit": fit,
                # ★ 预案字段
                "suggested_pct": _sug,
                "position_label": _sz.get("position_label") or "",
                "sizing_reasons": (_sz.get("reasons") or [])[:2],
                "plan": plan.get("text"), "plan_level": plan.get("level"),
            })
    except Exception as e:
        print(f"[decision-card] positions scan failed: {e}")       # ASCII（铁律⑥）

    # ★ 2026-09-25：负面清单聚合（用户需求："负面清单和主线推荐同等重要"）。
    #   ⚠️ **零新增数据源** —— 素材早已算好，此前只是散在别处、没有汇总成"今天要避开的"：
    #     · `data["position_risks"]`（L157-172 已聚合：持仓 ∩ 主力出货嫌疑，带 severity）
    #     · `data["candidates"]` 里 `mainforce_signal == "distribution"`
    #       （评分看多 ∩ 主力出货 = 信号冲突，对应 actions 的 R2，但这里能拿到 code 便于点击）
    #     · `data["actions"]` 的 R1（矛盾扫描命中持仓敞口）
    #   覆盖范围说明（诚实标注）：**解禁已于 2026-09-25 接入**（见下方 risk_events，C1）；
    #   **仍不含**减持/质押/停牌/问询/新股 —— 那些暂无结构化数据源（接入后在此追加即可）。
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

    # ★★ 2026-09-25（用户需求 C1「风险事件闸门」）—— 补上此前明确标注"无数据源"的那一块。
    #   《专业审视》C2 原话："强势股最典型的死亡路径：大额解禁 / 减持 / 质押 / 商誉 / 立案…
    #   系统目前对这些**零感知** —— 技术面会继续给高分直到崩塌。"
    #   本轮用**限售解禁**（唯一可前瞻的：日期提前数月公开）做闸门，阈值 10% 有回测依据
    #   （见 `risk_events.py` 文件头：≥10% 档前 20 日超额 -2.74%、胜率 40%）。
    #   ⚠️ 只查**决策卡涉及的代码**（用户真实持仓 + 候选）⇒ 不把整张表塞进 payload。
    #   ⚠️ 用 `positions_scan`（用户真实持仓）而不是 `data["positions"]`（模拟盘）。
    try:
        from app.risk_events import upcoming_risks
        _scan = data.get("positions_scan") or []
        _hold = {p.get("code") for p in _scan if p.get("code")}
        _codes = list(_hold) + [c.get("code") for c in (data.get("candidates") or [])
                                if c.get("code")]
        rk = upcoming_risks(codes=_codes)
        for x in (rk.get("high") or []):
            held = x["code"] in _hold
            negatives.append({
                "code": x["code"], "name": x["name"], "level": "high",
                "scope": "持仓" if held else "候选",
                # ⚠️ 前端插值显示 ⇒ 文案不能含 Markdown 标记
                "reason": ("20 日内大额解禁：" + (x.get("detail") or "")
                           + ("（只减不加）" if held else "（暂缓买入）")),
            })
        # 完整结果也放进 payload：前端可显示"已扫描过、今日无命中"（避免"没有 = 没做"的误解）
        data["risk_events"] = {"high": rk.get("high") or [],
                               "watch": rk.get("watch") or [],
                               "scanned": len(_codes), "note": rk.get("note")}
    except Exception as e:
        print(f"[decision-card] risk events failed: {e}")           # ASCII（铁律⑥）

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
        # ★ 2026-09-25（C1）：风险事件扫描结果 —— 含"扫过了但无命中"的状态，
        #   避免用户把"没有条目"误解成"没做这件事"。
        "risk_events": data.get("risk_events") or None,
        "positions_scan": positions_scan,
        # ★ 2026-09-25（P2）：战法信号 × 行业交叉表 —— 回答"58 只信号分布在哪些行业"，
        #   并附**原始细分口径**（用户："融捷(锂)/焦作万方(电解铝)笼统归入有色/化工链条，
        #   分类口径要标注"）。零新增网络 + 30 分钟缓存；失败返回空结构。
        "signals_industry": signal_industry_cached(),
        # ★ 2026-09-25（用户需求 A）：战法质量 + **"为什么推送静默"的自解释**。
        #   决策卡"做什么"只显示"无白名单战法（推送静默）"⇒ 分不清是市场不对 / 战法坏了 /
        #   系统故障（三者处置完全不同）。此处把 `whitelist_state` 已算好的 stats/alerts
        #   叠加当前 regime 暴露出来。零新增计算 + 10 分钟缓存；失败返回空结构。
        "strategy_quality": strategy_quality_cached(),
    }
