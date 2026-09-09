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
                                    f"（{p['strategy_name']}，信号日 {p['signal_date']}）")

    for r in risks:
        add("R5", "high", f"持仓 {r['name']}({r['code']})：{r['detail']}")

    sev_order = {"high": 0, "medium": 1, "low": 2}
    actions.sort(key=lambda a: sev_order.get(a["severity"], 3))
    data["actions"] = actions          # 全量落库（data_json），渲染时取前 3

    return data


def _data_to_markdown(data: dict) -> str:
    """聚合数据 → LLM 输入 markdown（紧凑、带小节标题）。"""
    lines = [f"日期: {data.get('date')}  阶段: {data.get('phase')}"]
    if data.get("regime"):
        lines.append(f"市场状态: {data['regime']['state']}（{data['regime']['date']}）")
    if data.get("contradictions"):
        lines.append("未解决矛盾:")
        lines += [f"  - [{c['level']}|{c['severity']}] {c['title']}（{c['date']}）"
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
                  f"策略:{p['strategy_name']} 信号日:{p['signal_date']}"
                  for p in data["positions"]]
    if data.get("strategy_counts"):
        lines.append("近2日战法信号数:")
        lines += [f"  - {s['strategy_name']} {s['scan_date']}: {s['count']}只"
                  for s in data["strategy_counts"]]
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




def _render_actions_md(actions: list) -> str:
    """确定性「该做」段：规则引擎输出渲染，≤3 条，严重度排序。"""
    if not actions:
        return "无可执行建议，观望。"
    sev = {"high": "[高]", "medium": "[中]"}
    return "\n".join(f"- {sev.get(a['severity'], '')} {a['text']}"
                     for a in actions[:MAX_ITEMS_PER_SECTION])


def _fallback_skeleton(data: dict, reason: str) -> str:
    """LLM 不可用/失败时的规则骨架简报（绝不用 LLM 常识补写）。"""
    lines = [f"> AI 暂不可用（{reason}），以下为规则版简报（仅确定性内容）", ""]
    lines.append("## 该关注")
    cons = (data.get("contradictions") or [])[:MAX_ITEMS_PER_SECTION]
    lines += [f"- [{c['level']}|{c['severity']}] {c['title']}（{c['date']}）"
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



def generate_trader_brief(phase: str = None, force: bool = False) -> dict:
    """生成（或读取当日已生成的）决策简报。返回 {ok, phase, markdown, data}。"""
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
    data = collect_brief_data(phase)
    actions_md = _render_actions_md(data.get("actions") or [])
    degraded = None
    if blocked:
        narrative = _fallback_skeleton(data, blocked)
        degraded = blocked
    else:
        narrative = call_llm(_SYSTEM_PROMPT, _data_to_markdown(data), temperature=0.3)
        if not narrative:
            narrative = _fallback_skeleton(data, "LLM 调用失败（空响应）")
            degraded = "llm_empty"

    narrative = _validate_refs(narrative, data)
    md = (f"{narrative}\n\n## 该做\n{actions_md}\n\n"
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
