"""
================================================================================
【文件作用】周复盘（用户视角的「本周回顾」）
================================================================================

URL：GET /api/report/weekly?days=7&end=YYYY-MM-DD

【为什么做】复盘层此前只有**当日闭环**（日报 + 今日执行回看 + 执行一致性），缺**周期视角**。
  投资纪律的通行习惯是"周日晚上看周度"：本周市场怎么走、我执行得怎么样、系统说了什么。
  用户框架原话——"复盘要记录状态/理由/执行偏差，区分逻辑错/时机错/执行错"：
  日度做不到这件事，周度才可以。

【口径（★ 必须与展示一起披露）】
  · 窗口 = `end`（默认今天，北京时间）往前 `days` 个**自然日**（默认 7），闭区间。
    用**自然日**而非交易日：纪律是"按周看"的，跳过周末会把"7 天"变成"9 天"。
  · regime 轨迹取自 `market_regime_history`（**日批落库**）⇒ 只含**已落库**的交易日，
    可能不连续（日批失败/长假）⇒ 如实展示已有点，**不插值、不补 0**
    （同 `macro._OVERNIGHT_KEYS` 纪律①：缺失 ≠ 0）。
  · 执行一致性**复用 `coach.audit.execution_consistency`**（分母口径的唯一实现），
    **不在本模块重算** —— 避免"同一个执行率出现两个数"（项目前车之鉴：本地 68.8 vs 后端 72.6）。
  · 日报只给**索引**（日期/字数/生成时间），正文仍走 `/api/report/daily`（免得周接口变重）。
  · `push_log` 记录的是"**系统判断要说这件事**"，**不代表企微已送达**（与前端时间线同一标注）。
  · **纯只读聚合**（全部读库）；结论文案由**规则**生成，不用 LLM。
    ⚠️ 唯一例外：§四「组合绩效」块复用 `portfolio_drawdown()`，内部按需拉持仓票 K 线
    （持仓 2 只 + KLINE_CACHE ⇒ 可控；失败静默、不影响其它块）。

【定位】展示层 / 复盘工具。**不改任何闸门、权重、准入**（E2 v0 纪律）。
================================================================================
"""

from datetime import datetime, timedelta
from typing import List, Optional

from app import benchmarks
from app.database import db

_BJ = timedelta(hours=8)
_STATE_CN = {"offensive": "进攻", "neutral": "震荡",
             "neutral_bearish": "震荡偏空", "defensive": "防御"}


def _beijing_today() -> str:
    """北京时间今日（复用 `flash.rules.beijing_now` 的唯一实现，失败则用 UTC+8 兜底）。"""
    try:
        from app.flash.rules import beijing_now
        return beijing_now().strftime("%Y-%m-%d")
    except Exception:
        return (datetime.utcnow() + _BJ).strftime("%Y-%m-%d")


def _window(days: int, end: Optional[str] = None) -> tuple:
    """返回 (start_iso, end_iso) 闭区间；`end` 非法时回退今天。"""
    e = (end or "").strip()[:10]
    try:
        d_end = datetime.strptime(e, "%Y-%m-%d").date() if e else \
            datetime.strptime(_beijing_today(), "%Y-%m-%d").date()
    except ValueError:
        d_end = datetime.strptime(_beijing_today(), "%Y-%m-%d").date()
    d_start = d_end - timedelta(days=max(days, 1) - 1)
    return d_start.isoformat(), d_end.isoformat()


# ── 一、市场：regime 轨迹 ──────────────────────────────────────────────────

def _regime_track(start: str, end: str) -> dict:
    rows = db.fetch(
        "SELECT date, state, regime_score, adx, ma_trend, volatility_regime "
        "FROM market_regime_history WHERE date >= %s AND date <= %s ORDER BY date",
        (start, end))
    pts: List[dict] = []
    for r in rows or []:
        def _fl(v):
            try:
                return round(float(v), 2) if v is not None else None
            except (TypeError, ValueError):
                return None
        pts.append({
            "date": str(r.get("date"))[:10],
            "state": r.get("state"),
            "state_cn": _STATE_CN.get(r.get("state"), r.get("state") or "—"),
            "score": _fl(r.get("regime_score")),
            "adx": _fl(r.get("adx")),
            "ma_trend": r.get("ma_trend"),
            "vol_regime": r.get("volatility_regime"),
        })
    states = [p["state"] for p in pts]
    scores = [p["score"] for p in pts if p["score"] is not None]
    return {
        "points": pts, "n": len(pts),
        "switches": sum(1 for a, b in zip(states, states[1:]) if a != b),
        "score_delta": (round(scores[-1] - scores[0], 1) if len(scores) >= 2 else None),
        "latest": pts[-1] if pts else None,
        "first": pts[0] if pts else None,
    }


def _regime_sentence(tr: dict) -> str:
    """规则生成的轨迹结论（不用 LLM）。"""
    pts = tr["points"]
    if not pts:
        return ("本窗口没有 regime 落库记录（日批未跑或全部休市）⇒ 状态轨迹**不可评估**，"
                "别把空白当成「没有变化」。")
    seq, prev = [], None
    for p in pts:
        if p["state_cn"] != prev:
            seq.append(p["state_cn"])
            prev = p["state_cn"]
    txt = " → ".join(seq)
    extra = []
    if tr["switches"]:
        extra.append(f"切换 {tr['switches']} 次")
    else:
        extra.append("全程未切换")
    if tr["score_delta"] is not None:
        extra.append(f"regime 分 {tr['score_delta']:+.1f}")
    return (f"本窗口 {tr['n']} 个交易日：{txt}（{'；'.join(extra)}）。"
            f"最新 {pts[-1]['state_cn']}（{pts[-1]['date']}，"
            f"ADX {pts[-1]['adx'] if pts[-1]['adx'] is not None else '—'}）。")


# ── 二、执行：一致性（复用 audit）+ 分规则明细 + 放弃理由 ────────────────────

def _exec_sentence(ex: dict) -> str:
    if ex.get("error"):
        return "执行一致性查询失败，本轮不评估。"
    decided = int(ex.get("decided") or 0)
    pushed = int(ex.get("pushed_total") or 0)
    ignored = int(ex.get("ignored") or 0)
    if not decided:
        return (f"本窗口已推送 {pushed} 张卡、**无一张被决策**"
                f"（未响应 {ignored} 张）⇒ 执行一致性**无法评估**（分母为 0）。"
                "注意：没响应 ≠ 执行了，两者在纪律上是完全不同的事。")
    rate = ex.get("exec_rate_pct")
    ab = ex.get("abandon_rate_pct")
    tail = ""
    if rate is not None and rate < 60:
        tail = ("执行率偏低 ⇒ 优先复盘「为什么没照做」，而不是「建议对不对」——"
                "教练的价值是劝住冲动，不是选股。")
    elif rate is not None and rate >= 85:
        tail = "执行率良好 ⇒ 纪律这一环没掉链子。"
    return (f"本窗口已推送 {pushed} 张卡：已决策 {decided}（执行 {ex.get('executed')} / "
            f"放弃 {ex.get('abandoned')}）、未响应 {ignored}。"
            f"执行率 {rate}%、放弃率 {ab}%（分母 = 已决策，不含未响应）。{tail}")


def _alerts_breakdown(start: str, end: str) -> List[dict]:
    """本窗口教练卡按规则聚合。

    ⚠️ **两个数都要给**（项目最忌"数字对不上"）：
      · `n`        = 窗口内**全部**命中（含 `push=false` 的静默卡）；
      · `n_pushed` = 其中 `pushed=1` 的 —— **与执行率 KPI 同源**，Σ`n_pushed` == `pushed_total`。
    两者不等是**正常的**：项目里多数卡默认静默（`push=false`），只有放开推送的才进执行率分母。
    """
    rows = db.fetch(
        "SELECT rule_id, label, COUNT(*) AS n, "
        "SUM(CASE WHEN pushed=1 THEN 1 ELSE 0 END) AS n_pushed, "
        "SUM(CASE WHEN executed='yes' THEN 1 ELSE 0 END) AS yes, "
        "SUM(CASE WHEN executed='no' THEN 1 ELSE 0 END) AS no "
        "FROM coach_alerts WHERE alert_date >= %s AND alert_date <= %s "
        "GROUP BY rule_id, label ORDER BY n DESC", (start, end))
    return [{"rule_id": r.get("rule_id"), "label": r.get("label"),
             "n": int(r.get("n") or 0), "n_pushed": int(r.get("n_pushed") or 0),
             "yes": int(r.get("yes") or 0), "no": int(r.get("no") or 0)}
            for r in (rows or [])]


def _abandon_reasons(start: str, end: str, limit: int = 200) -> List[dict]:
    """复用 `audit.abandon_reasons`（唯一实现）后按窗口过滤 —— 该函数本身不支持窗口。"""
    try:
        from app.coach import audit
        rows = audit.abandon_reasons(limit=limit)
    except Exception as e:
        print(f"[weekly] abandon reasons failed: {str(e)[:80]}")      # ASCII（铁律⑥）
        return []
    return [r for r in rows if start <= str(r.get("alert_date"))[:10] <= end]


# ── 三、日报索引 / 系统提示 / 下周日历 ─────────────────────────────────────

def _reports(start: str, end: str) -> List[dict]:
    rows = db.fetch(
        "SELECT date, created_at, LENGTH(markdown) AS len FROM daily_reports "
        "WHERE date >= %s AND date <= %s ORDER BY date DESC", (start, end))
    return [{"date": str(r.get("date"))[:10], "created_at": r.get("created_at"),
             "len": int(r.get("len") or 0)} for r in (rows or [])]


def _push_summary(start: str, end: str) -> dict:
    rows = db.fetch(
        "SELECT COALESCE(NULLIF(category, ''), '其他') AS category, COUNT(*) AS n "
        "FROM push_log WHERE date >= %s AND date <= %s GROUP BY 1 ORDER BY n DESC",
        (start, end))
    by = [{"category": r.get("category"), "n": int(r.get("n") or 0)} for r in (rows or [])]
    return {"total": sum(x["n"] for x in by), "by_category": by,
            "note": "记录的是「系统判断要说这件事」，不代表企微已送达"}


def _next_week(days: int = 7) -> dict:
    """下周关键事件（复用 `flash.calendar_ahead.upcoming` 的唯一实现）。失败静默。"""
    try:
        from app.flash.calendar_ahead import upcoming
        up = upcoming(days=days) or {}
        items = up.get("items") or []
        return {"items": items, "core_count": up.get("core_count", len(items)),
                "days": days}
    except Exception as e:
        print(f"[weekly] calendar ahead failed: {str(e)[:80]}")       # ASCII（铁律⑥）
        return {"items": [], "core_count": 0, "days": days, "error": str(e)[:80]}


# ── 四、组合绩效（净值 + 三口径）──────────────────────────────────────────

def _perf_sentence(abs_ret, r300, r1000, days=None):
    """规则判读（不用 LLM）—— 与日报双基准同一「跑赢≠赚钱」防误读口径。"""
    if abs_ret is None:
        return "组合净值窗口不足，本期绩效不可评估。"
    span = f"近 {days} 个交易日" if days else "本期"
    s = f"{span}组合 {abs_ret:+.2f}%"
    if r300 is not None:
        s += f"；沪深300 {r300:+.2f}%（超额 {abs_ret - r300:+.2f}pt）"
    if r1000 is not None:
        s += f"；中证1000 {r1000:+.2f}%（超额 {abs_ret - r1000:+.2f}pt）"
    if abs_ret < 0 and (r1000 if r1000 is not None else r300 or 0) < 0:
        s += " ⚠️ 绝对收益为负 —— 「跑赢」≠「赚钱」"
    return s + "。"


def _performance_block(window: int = 10) -> dict:
    """组合绩效块：近 5 个交易日净值变化 + 三口径（绝对 / 对沪深300 / 对中证1000 超额）。

    ★ 为什么（P0 基准错配修复）：此前复盘只有「执行一致性」，没有「我这周赚亏多少、
      相对基准如何」——而复盘最该回答的就是这个；且全项目基准只有沪深300（大盘股），
      持仓/信号偏 20–50 亿中小盘 ⇒ 必须双基准（9/20 审视 B4）。
    ★ 数据：**复用 `coach.position_sizing.portfolio_drawdown()`（净值/回撤的唯一实现，
      自带「按当前持仓回算、未考虑窗口内加减仓」的口径警告）**，不另写净值计算。
      window=10（> 组合回撤默认 5）——「近 5 个交易日」需首末 6 个交易日 ✓。
    ⚠️ 本块是周复盘**唯一可能产生外部请求**的部分（`portfolio_drawdown` 内部按需拉
      持仓票 K 线；持仓仅 2 只 + `KLINE_CACHE` 90s ⇒ 可控）；失败静默、不影响其它块。
    """
    try:
        from app.coach import position_sizing
        pdd = position_sizing.portfolio_drawdown(window=window)
    except Exception as e:
        print(f"[weekly] portfolio pnl failed: {str(e)[:80]}")           # ASCII（铁律⑥）
        return {"available": False, "reason": "组合净值计算失败"}
    if not pdd.get("available"):
        return {"available": False, "reason": pdd.get("note") or "无持仓或净值不可用"}
    curve = pdd.get("curve") or []
    if len(curve) < 2:
        return {"available": False,
                "reason": f"净值窗口不足（{len(curve)} < 2 个交易日）"}
    # ★ 窗口自适应：优先"近 5 个交易日"（首末 6 个点）；持仓较新/刚调仓导致窗口不足时
    #   退化为"建仓以来"（首末 2 个点）—— 少也是信息，但**必须标注实际天数**（口径诚实），
    #   且 `degraded=True` 供前端区分展示。
    degraded = len(curve) < 6
    pts = curve[-6:] if not degraded else list(curve)
    start, end = pts[0], pts[-1]
    days = len(pts) - 1
    d0, d1 = str(start["date"])[:10], str(end["date"])[:10]
    abs_ret = (round((end["nav"] / start["nav"] - 1) * 100, 2)
               if start.get("nav") and start["nav"] > 0 else None)
    # ★ 2026-09-29（口径收敛）：改走 `app.benchmarks` 唯一事实源（原 `_idx_ret` 已删）
    _bench = benchmarks.dual_bench(d0, d1)
    r300, r1000 = _bench.get("hs300"), _bench.get("zz1000")
    return {
        "available": True,
        "window": {"start": d0, "end": d1, "days": days, "degraded": degraded},
        "portfolio_ret": abs_ret, "hs300_ret": r300, "zz1000_ret": r1000,
        "excess_hs300": (round(abs_ret - r300, 2)
                         if abs_ret is not None and r300 is not None else None),
        "excess_zz1000": (round(abs_ret - r1000, 2)
                          if abs_ret is not None and r1000 is not None else None),
        "dd_note": pdd.get("note"),     # 口径警告：按当前持仓回算、未考虑窗口内加减仓
        "sentence": _perf_sentence(abs_ret, r300, r1000, days),
        "note": ("口径：近 5 个交易日组合持仓市值变化（不含现金）、按当前持仓回算；"
                 "基准 = 沪深300 / 中证1000 同期收益（中证1000 更贴近中小盘持仓风格）；"
                 "「跑赢基准」≠「赚钱」（绝对收益为负时仍可能跑赢大盘股指数）。"),
    }


# ── 五、板块（本周：5 日动量 + 新进入异动）─────────────────────────────────

def _sector_block() -> dict:
    """本周板块：5 日动量榜（最强/最弱）+ 本周**新进入**的异动。

    ★ 为什么（2026-09-30，P1）：用户问「前几天的地产、今天的金属拉升，项目似乎察觉不到？」
      —— 复盘本该回答"这一周市场发生了什么"，此前却**只有 regime 与执行一致性**，
      没有"哪些板块在动"（板块序列早在库，只被用来算分化度）。
    ★ 数据：复用 `app.sector_momentum`（zzshare 104 粗分板块序列；日批每晚落库 +
      `task_data_gap` 自愈）—— **与日报同一模块、同一口径**，不另写一套。
    ⚠️ **不关联个股/评分**：zzshare 104 粗分 与 新浪 49 类 实测仅 **4 个同名** ⇒ 强行按名
      匹配会产出误导性的"板块内 0 只上榜"（缺失 ≠ 0）。"板块价格"与「行业主线（评分扎堆）」
      是**两套 taxonomy**，本块只出前者。
    ⚠️ 与日报的分工：日报看 **当日/3 日**（今天谁在动），本块看 **5 日**（本周谁在动）。
    """
    try:
        from app import sector_momentum
        res = sector_momentum.snapshot()
    except Exception as e:
        print(f"[weekly] sector momentum failed: {str(e)[:80]}")      # ASCII（铁律⑥）
        return {"available": False, "reason": "板块序列不可用"}
    if not res.get("available"):
        return {"available": False, "reason": res.get("note") or "无板块数据"}
    top = res.get("strong5") or []
    bottom = res.get("weak5") or []
    mv = [m for m in (res.get("moves") or []) if m.get("kind") == "strong"]
    parts = []
    if top:
        parts.append("本周最强：" + "、".join(f"{r['industry']} {r['ret5']:+.1f}%"
                                           for r in top[:4]))
    if bottom:
        parts.append("最弱：" + "、".join(f"{r['industry']} {r['ret5']:+.1f}%"
                                       for r in bottom[:3]))
    if mv:
        parts.append("新进入异动：" + "、".join(f"{m['industry']}（{m['reason']}）"
                                            for m in mv[:4]))
    return {
        "available": True, "as_of": res.get("as_of"), "days": res.get("days"),
        "sectors": res.get("sectors"),
        "top": top, "bottom": bottom, "moves": mv,
        "sentence": ("；".join(parts) + "。") if parts else "本周无板块级异动。",
        "note": res.get("note"),
    }


# ── 六、组装 ──────────────────────────────────────────────────────────────

def build_weekly_review(days: int = 7, end: Optional[str] = None) -> dict:
    """周复盘聚合（只读）。`days` 已由调用方钳位。"""
    start, end_iso = _window(days, end)
    tr = _regime_track(start, end_iso)

    # ★ 口径对齐（重要）：`audit.execution_consistency(days=N)` 的 SQL 边界是
    #   `alert_date >= 今天 − N` ⇒ **含 N+1 个自然日**；而本模块窗口是 `end − (days−1)`
    #   ⇒ **恰好 days 个自然日**。为了让"执行率"与"分规则明细"落在**同一窗口**，
    #   这里传 `days − 1`，并把回传的 `window_days` 改写成本模块的真实窗口
    #   （否则前端会显示一个与实际统计不符的天数）。
    #   ⚠️ 这是**唯一**允许改写 audit 返回值的地方，且只改这一个展示字段。
    try:
        from app.coach import audit
        ex = audit.execution_consistency(days=max(days - 1, 0))
        ex["window_days"] = days
        ex["window_aligned"] = True     # 标记：窗口已按本模块口径对齐（供前端标注）
    except Exception as e:
        print(f"[weekly] consistency failed: {str(e)[:80]}")          # ASCII（铁律⑥）
        ex = {"error": str(e)[:80]}

    alerts = _alerts_breakdown(start, end_iso)
    drops = _abandon_reasons(start, end_iso)
    return {
        "window": {"start": start, "end": end_iso, "days": days},
        "section": {
            # ★ 2026-09-29（P0）：绩效块放最前 —— 复盘最该先看的数；失败静默不拖垮其它块
            "performance": _performance_block(),
            "regime": {
                "track": tr,
                "sentence": _regime_sentence(tr),
            },
            # ★ 2026-09-30（P1）：本周板块（5 日动量 + 新进入异动）—— 与日报同模块同口径
            "sectors": _sector_block(),
            "execution": {
                "kpi": ex,
                "sentence": _exec_sentence(ex),
                "alerts": alerts,
                "abandon_reasons": drops,
            },
            "system": _push_summary(start, end_iso),
            "reports": _reports(start, end_iso),
            "next_week": _next_week(days=7),
        },
        "notes": [
            "窗口为**自然日**闭区间（按周看纪律；跳过周末会把 7 天变成 9 天）",
            "regime 只含**已落库**的交易日，缺口不插值、不补 0",
            "执行一致性复用 coach.audit（分母 = 已决策，不含未响应）",
            "纯展示层 / 复盘工具，不改任何闸门、权重、准入",
        ],
    }
