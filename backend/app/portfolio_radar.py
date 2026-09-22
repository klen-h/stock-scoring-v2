# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】持仓雷达 —— 以**用户持仓**为锚的状态聚合（2026-09-23 新增）
================================================================================

为什么需要（用户 2026-09-23 提出）：
  系统此前所有"看板"都以**全市场**为主语（榜单/观察池/矛盾/盘中警示），而持仓只在
  LLM prompt 与教练止损里出现 ⇒ 用户看到的是「156 只候选」，却答不出「我手里那三只
  怎么样了」。本模块把散落在各处的状态**按持仓聚合**成一个视图。

★ 定位（沿用 `trade_gate.summarize` 的纪律，避免误用）：
  这是**状态展示 + 提示聚合**，不是买卖信号 —— 不参与排序、不改评分、不新增网络请求。
  每条 alert 都指向某个**已有模块的既有结论**（闸门就绪 / 主力阶段 / 战法 / 矛盾 /
  涨跌），不引入新的判据（防止出现"第五套口径"）。

数据来源（**全部复用既有单一事实源**，不重算）：
  · 持仓         `user_portfolio` + `portfolio_scope.portfolio_where()`（多用户隔离，
                 与教练/日报/LLM 同一口径 —— 否则会串到别的账号）
  · 现价/日内高低 `tencent._cache`（内存行情，**零请求**；缺失才批量补一次）
  · 主力阶段     `mainforce.state.load_latest()`（含 `phase_cn`，2026-09-23 统一到该入口）
  · 闸门就绪     `mainforce.trade_gate.evaluate + summarize`（唯一就绪度事实源）
  · 战法命中     `routers.scoring._load_signal_map()`（延迟 import 防循环；与观察池同源）
  · 是否在观察池 `gate_snapshot_history` 最新快照（日批落库，**不重算全市场**）
  · 评分/排名    `ranking_live` 最新 rank_date（日批榜单，**不实时精算** —— 持仓十几只
                 实时精算要几十秒，且盘中口径本就不稳）
  · 市场矛盾     `contradictions.store.load_contradictions(resolved=0)`（今日未兑现）
  · 所属行业     `routers.scoring._load_industry_map()`（延迟 import，同源）
================================================================================
"""

from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

# ── 提示阈值（**刻意克制**：只在真正值得看一眼时出声，噪音纪律见 9-23 东财告警教训）──
_LOSS_WARN = -8.0          # 浮亏 ≤ -8% ⇒ 提醒（止损线通常 -8%）
_LOSS_SEVERE = -12.0       # 浮亏 ≤ -12% ⇒ 高优先
_PROFIT_HIGH = 20.0        # 浮盈 ≥ +20% ⇒ 提示（可考虑保护利润，非建议）
_DAY_DROP_FROM_HIGH = -5.0  # 当日距日内最高 ≤ -5% ⇒ 提示（个股级「冲高回落」）

_MSG_CAP = 3               # 每只股票最多显示 3 条提示（防刷屏）

# ── 缓存 TTL（★ 2026-09-23 实测驱动：冷启动 build() 本地 36s、热缓存 1.28s）──
#   egress/性能纪律：**TTL 必须匹配数据更新频率**（日频数据配 30s TTL 是反面教材）。
#   三处慢读取实测：`_load_signal_map` 14~20s、`load_contradictions` 6s、
#   `gate_snapshot_history` 1.5~4.5s ⇒ 都远慢于 CPU 计算，必须缓存。
#   ⚠️ 冷启动仍需预热（见 `flash.scheduler.portfolio_radar_warm_loop`）——首次请求
#     若撞冷缓存会超前端 20s 超时（与观察池同款坑）。
_watch_cache = {"ts": 0.0, "map": {}}       # 观察池快照（日批）
_WATCH_TTL = 1800.0                         # 30 分钟
_holdings_cache = {"ts": 0.0, "val": []}    # 持仓（用户可改 ⇒ TTL 短）
_HOLDINGS_TTL = 60.0                        # 1 分钟
_contra_ctx_cache = {"ts": 0.0, "val": None}  # 矛盾（盘后 + 午间各一次）
_CONTRA_CTX_TTL = 900.0                     # 15 分钟
_rank_cache = {"ts": 0.0, "val": {}}        # 榜单（日批）
_RANK_TTL = 600.0                           # 10 分钟


def _holdings() -> List[Dict]:
    """用户持仓（主用户隔离）。失败返回 []。60s 进程内缓存。"""
    now = time.time()
    if _holdings_cache["ts"] and now - _holdings_cache["ts"] < _HOLDINGS_TTL:
        return _holdings_cache["val"]
    out: List[Dict] = []
    try:
        from app.database import db
        from app.portfolio_scope import portfolio_where
        w, p = portfolio_where()
        rows = db.fetch(
            f"SELECT code, name, shares, cost FROM user_portfolio {w} "
            f"ORDER BY created_at ASC", p)
        for r in rows or []:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            out.append({"code": code, "name": r.get("name") or code,
                        "shares": float(r.get("shares") or 0),
                        "cost": float(r.get("cost") or 0)})
    except Exception as e:
        print(f"[portfolio_radar] 持仓读取失败: {e}")
    _holdings_cache.update({"ts": now, "val": out})
    return out


def _quotes(codes: List[str]) -> Dict[str, Dict]:
    """现价/日内高低：优先内存行情缓存（零请求）；缺失的批量补一次 HTTP。

    ★ egress/请求纪律：全市场刷新本就每 2-3 分钟刷 ~4000 只，持仓必在其中 ⇒ 正常
      情况**一次网络请求都不发**。
    """
    out: Dict[str, Dict] = {}
    try:
        from app.tencent import _cache
        stocks = _cache.get("stocks", {}) or {}
        for c in codes:
            s = stocks.get(c)
            if s:
                out[c] = dict(s)
    except Exception as e:
        print(f"[portfolio_radar] 行情缓存读取失败: {e}")
    missing = [c for c in codes if c not in out]
    if missing:
        try:
            from app.tencent import _fetch_tencent, _CODE_TO_PREFIX
            parts = []
            for c in missing:
                prefix = _CODE_TO_PREFIX.get(c, "sh" if c.startswith("6") else "sz")
                parts.append(f"{prefix}{c}")
            data = _fetch_tencent(",".join(parts))
            for qt, v in (data or {}).items():
                code = (v or {}).get("code")
                if code:
                    out[str(code)] = v
        except Exception as e:
            print(f"[portfolio_radar] 行情补拉失败（跳过）: {e}")
    return out


def _watch_map() -> Dict[str, Dict]:
    """观察池（`gate_snapshot_history` 最新快照）→ {code: {ready, label, hint}}。

    ★ 用**日批快照**而不是实时重算：`_gate_watch_live()` 要遍历全市场 ~2200 只、
      本地实测 39.8s（线上分钟级）⇒ 绝不能挂在请求路径上。
      快照缺失/为空时返回 {}（本模块降级：不显示观察池列，不影响其它状态）。
    """
    now = time.time()
    if _watch_cache["ts"] and now - _watch_cache["ts"] < _WATCH_TTL:
        return _watch_cache["map"]
    out: Dict[str, Dict] = {}
    try:
        import json as _json
        from app.database import db
        row = db.fetch_one("SELECT date, payload FROM gate_snapshot_history "
                           "ORDER BY date DESC LIMIT 1")
        pay = _json.loads((row or {}).get("payload") or "{}")
        for x in pay.get("candidates") or []:
            c = x.get("code")
            if c:
                out[str(c)] = {"date": row.get("date"),
                               "ready": x.get("ready"),
                               "label": x.get("label") or "",
                               "hint": x.get("hint") or ""}
    except Exception as e:
        print(f"[portfolio_radar] 观察池快照读取失败（跳过该列）: {e}")
    _watch_cache.update({"ts": now, "map": out})
    return out


def _rank_map_all() -> Dict[str, Dict]:
    """最新 `rank_date` 的**全量**榜单 → {code: {...}}（10 分钟缓存）。

    ★ 为什么查全量而不按 codes 过滤：榜单一天一行一票、总量小（百来行），查全量一次
      即可服务任意持仓组合；按 codes 过滤则每次请求都要重查（实测 1.2~2.1s/次）。
      榜单是**日批**数据 ⇒ 10 分钟 TTL 安全。
    """
    now = time.time()
    if _rank_cache["ts"] and now - _rank_cache["ts"] < _RANK_TTL:
        return _rank_cache["val"]
    out: Dict[str, Dict] = {}
    try:
        from app.database import db
        row = db.fetch_one("SELECT MAX(rank_date) AS d FROM ranking_live")
        d = (row or {}).get("d")
        if d:
            rows = db.fetch("SELECT code, total_score, signal, signal_level, rank_pos "
                            "FROM ranking_live WHERE rank_date = %s", (str(d)[:10],))
            for r in rows or []:
                out[str(r["code"])] = {"total_score": r.get("total_score"),
                                       "signal": r.get("signal"),
                                       "signal_level": r.get("signal_level"),
                                       "rank_pos": r.get("rank_pos"),
                                       "rank_date": str(d)[:10]}
    except Exception as e:
        print(f"[portfolio_radar] 榜单读取失败（跳过该列）: {e}")
    _rank_cache.update({"ts": now, "val": out})
    return out


def _latest_rank(codes: List[str]) -> Dict[str, Dict]:
    """持仓在最新榜单里的分数/排名（**不实时精算**）。

    ★ 为什么不实时算分：持仓十几只走精算路径要几十秒且盘中口径不稳；榜单是日批产出、
      口径与前端榜单一致 ⇒ 只在**持仓曾上榜**时给分，未上榜就不显示（诚实降级）。
    """
    if not codes:
        return {}
    allmap = _rank_map_all()
    return {c: allmap[c] for c in codes if c in allmap}


def _contradiction_context() -> Dict:
    """今日未兑现矛盾 → {items, sectors}。

    `sectors` = 矛盾证据里点名的板块/个股名集合（`*_samples` 字段）—— 用于判断
    「持仓所属行业是否被矛盾点名」，这是用户提出的『矛盾为基础、盘面为印证』思路
    在持仓维度的落地。
    """
    now = time.time()
    if _contra_ctx_cache["ts"] and now - _contra_ctx_cache["ts"] < _CONTRA_CTX_TTL:
        return _contra_ctx_cache["val"]
    ctx = {"items": [], "sectors": set()}
    try:
        from app.contradictions.store import load_contradictions
        items = load_contradictions(resolved=0) or []
        for x in items[:8]:
            ctx["items"].append({"severity": x.get("severity"),
                                 "title": x.get("title") or "",
                                 "summary": (x.get("summary") or "")[:120]})
            metrics = ((x.get("evidence") or {}).get("metrics") or {})
            for k, v in metrics.items():
                if isinstance(k, str) and k.endswith("_samples") and v:
                    if isinstance(v, (list, tuple)):
                        for s in v:
                            ctx["sectors"].add(str(s).strip())
                    else:
                        ctx["sectors"].add(str(v).strip())
    except Exception as e:
        print(f"[portfolio_radar] 矛盾上下文读取失败（跳过）: {e}")
    _contra_ctx_cache.update({"ts": now, "val": ctx})
    return ctx


def _alerts_for(item: Dict, industry: Optional[str], ctx: Dict) -> List[Dict]:
    """单只持仓的提示列表（最多 `_MSG_CAP` 条，风险优先）。"""
    out: List[Dict] = []

    def add(level: str, text: str):
        out.append({"level": level, "text": text})

    pnl = item.get("pnl_pct")
    phase = item.get("phase")
    ready = item.get("ready")
    # ── 风险类（先加，保证不被机会类挤掉）──
    if phase == "distribution":
        add("risk", "主力阶段为『出货』")
    if pnl is not None and pnl <= _LOSS_SEVERE:
        add("risk", f"浮亏 {pnl:.1f}%（超 -12% 警戒）")
    elif pnl is not None and pnl <= _LOSS_WARN:
        add("risk", f"浮亏 {pnl:.1f}%（接近 -8% 止损线）")
    dh = item.get("drop_from_high")
    if dh is not None and dh <= _DAY_DROP_FROM_HIGH:
        add("risk", f"日内自高点回落 {dh:.1f}%（个股级冲高回落）")
    if industry and industry in (ctx.get("sectors") or set()):
        add("risk", f"所属板块『{industry}』被今日矛盾点名")
    # ── 机会类 ──
    if ready is not None and ready >= 3:
        add("opportunity", "闸门三条件就绪（主力有根据 + 不追高 + 市况允许）")
    elif ready == 2 and item.get("signal") == "accum":
        add("opportunity", f"闸门 2/3 就绪（{item.get('ready_label') or '等状态'}）")
    if item.get("strategies"):
        names = "、".join(s.get("name") or "" for s in item["strategies"][:2])
        add("opportunity", f"战法命中：{names}")
    if item.get("in_watch") and (ready is None or ready < 3):
        add("info", f"在观察池（ready {item.get('in_watch_ready')}/{3}）")
    if pnl is not None and pnl >= _PROFIT_HIGH:
        add("info", f"浮盈 {pnl:.1f}%（可考虑保护利润，非建议）")
    return out[:_MSG_CAP]


# ★ single-flight 保护（2026-09-23 实测驱动）：冷缓存 `_build_impl()` 本地 **31~36s**
#   （`_load_signal_map` 14~20s + `load_contradictions` 6s + `evaluate` 首只 8s + 各 DB
#   往返）。若后台预热与用户请求**并发**，两边会各自跑一遍且都慢（实测 call1=31.3s
#   —— 正是"预热还没写缓存、请求又进来"的窗口）⇒ 用"正在计算"标记让**并发调用立即
#   返回**（有上次结果就复用，否则给 warming 占位）⇒ 杜绝前端 20s 超时。
#   与 `routers.scoring._rank_result_cache["computing"]` 同一模式。
_build_gate = threading.Lock()
_build_state = {"computing": False, "last": None}


def _warming_placeholder() -> Dict:
    """预热期间（无历史结果可用）的占位响应 —— 前端据此显示"正在准备"。"""
    return {"as_of": None, "warming": True,
            "summary": {"n": 0, "risk": 0, "opportunity": 0},
            "items": [], "market": {},
            "note": "正在准备持仓数据（首次约需半分钟，之后秒开）…"}


def build() -> Dict:
    """聚合入口（**带 single-flight 保护**）。返回 {as_of, regime, summary, items, market}。"""
    if _build_state["computing"]:
        return _build_state["last"] or _warming_placeholder()
    with _build_gate:
        if _build_state["computing"]:
            return _build_state["last"] or _warming_placeholder()
        _build_state["computing"] = True
    try:
        result = _build_impl()
        _build_state["last"] = result
        return result
    finally:
        _build_state["computing"] = False


def _build_impl() -> Dict:
    """真正的聚合逻辑。⚠️ 勿直接调用 —— 走 `build()` 才有 single-flight 与缓存复用。"""
    t0 = time.time()
    holdings = _holdings()
    if not holdings:
        return {"as_of": None, "summary": {"n": 0, "risk": 0, "opportunity": 0},
                "items": [], "market": {}, "note": "无持仓记录"}

    codes = [h["code"] for h in holdings]
    quotes = _quotes(codes)
    watch = _watch_map()
    ranks = _latest_rank(codes)
    ctx = _contradiction_context()

    # 所属行业（延迟 import：本模块被 routers.scoring 调用，模块级 import 会循环）
    #   复用 `_industry_map`（该端点 2026-09-23 为榜单「板块」列所建，含 10 分钟进程缓存）
    industry_map: Dict[str, str] = {}
    try:
        from app.routers.scoring import _industry_map as _im
        raw = _im(codes) or {}
        industry_map = {c: (v.get("industry") if isinstance(v, dict) else v)
                        for c, v in raw.items() if v}
    except Exception as e:
        print(f"[portfolio_radar] 行业映射失败（跳过该列）: {e}")

    # 主力状态（一次批量）
    mf_map: Dict[str, Dict] = {}
    try:
        from app.mainforce.state import load_latest
        mf_map = load_latest(codes) or {}
    except Exception as e:
        print(f"[portfolio_radar] 主力状态读取失败（跳过）: {e}")

    # 战法信号（与观察池同一事实源）
    sig_map, sig_date = {}, None
    try:
        from app.routers.scoring import _load_signal_map
        sig_map, sig_date = _load_signal_map()
    except Exception as e:
        print(f"[portfolio_radar] 战法信号读取失败（跳过该列）: {e}")

    # 闸门就绪（唯一事实源）
    gate_mod = None
    try:
        from app.mainforce import trade_gate as gate_mod  # noqa: F811
    except Exception as e:
        print(f"[portfolio_radar] 闸门模块加载失败（跳过该列）: {e}")

    regime = ""
    items = []
    for h in holdings:
        code = h["code"]
        q = quotes.get(code) or {}
        price = float(q.get("price") or 0)
        high = float(q.get("high") or 0)
        mf = mf_map.get(code) or {}
        item: Dict = {
            "code": code, "name": h["name"],
            "shares": h["shares"], "cost": h["cost"],
            "price": price or None,
            "day_pct": q.get("change_pct"),
            "industry": industry_map.get(code),
            "phase": mf.get("phase"), "phase_cn": mf.get("phase_cn") or "",
            "signal": mf.get("signal"),
            "flow5_amt": mf.get("flow5_amt"),
        }
        # 盈亏 + 日内距高（个股级「冲高回落」—— 与指数规则同口径）
        if price and h["cost"] > 0:
            item["pnl_pct"] = round((price / h["cost"] - 1) * 100, 2)
            item["mv"] = round(price * h["shares"], 2) if h["shares"] else None
        if price and high:
            item["drop_from_high"] = round((price - high) / high * 100, 2)

        # 榜单（日批）
        r = ranks.get(code)
        if r:
            item["score"] = r.get("total_score")
            item["score_signal"] = r.get("signal")
            item["rank_pos"] = r.get("rank_pos")
            item["rank_date"] = r.get("rank_date")

        # 闸门就绪
        if gate_mod is not None:
            try:
                s = gate_mod.summarize(gate_mod.evaluate(code, mf=mf))
                item["ready"] = s.get("ready")
                item["ready_label"] = s.get("label")
                item["ready_hint"] = s.get("hint")
                item["position_pct"] = s.get("position_pct")
                item["position_label"] = s.get("position_label")
                regime = regime or (s.get("regime") or "")
            except Exception:
                pass

        # 观察池
        w = watch.get(code)
        if w:
            item["in_watch"] = True
            item["in_watch_ready"] = w.get("ready")
        # 战法
        if sig_map.get(code):
            item["strategies"] = sig_map[code]

        item["alerts"] = _alerts_for(item, item.get("industry"), ctx)
        items.append(item)

    # 排序：风险多的优先，其次机会多，最后按盈亏
    def _key(x: Dict):
        al = x.get("alerts") or []
        n_risk = sum(1 for a in al if a["level"] == "risk")
        n_opp = sum(1 for a in al if a["level"] == "opportunity")
        return (-n_risk, -n_opp, x.get("pnl_pct") if x.get("pnl_pct") is not None else 0)

    items.sort(key=_key)
    n_risk = sum(1 for x in items for a in (x.get("alerts") or []) if a["level"] == "risk")
    n_opp = sum(1 for x in items for a in (x.get("alerts") or []) if a["level"] == "opportunity")

    return {
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "regime": regime,
        "strategy_date": str(sig_date)[:10] if sig_date else None,
        "watch_date": (list(watch.values())[0].get("date") if watch else None),
        "summary": {"n": len(items), "risk": n_risk, "opportunity": n_opp,
                    "in_watch": sum(1 for x in items if x.get("in_watch")),
                    "elapsed_ms": int((time.time() - t0) * 1000)},
        "market": {"contradictions": ctx.get("items") or []},
        "items": items,
    }
