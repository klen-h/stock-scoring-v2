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


def invalidate_holdings() -> None:
    """★ 2026-09-25：user_portfolio 落库方（routers/user.py）调用——写入即失效
    持仓缓存。此前新加持仓要等 60s TTL 才进雷达（工作台"0 只"困惑的根因）。"""
    _holdings_cache.update({"ts": 0.0, "val": []})
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


def _bj_ts_str(ts: float):
    """Unix 秒 → 北京时间的 `YYYY-MM-DD HH:MM`。

    ⚠️ 必须 `gmtime(ts + 8h)`：`datetime.fromtimestamp` 走**服务器本地时区**
    （Render/Actions 是 UTC，开发机是 +08）⇒ 直接用会**双加 8 小时**、时点差 8 小时
    （本项目已被这个坑咬过多次）。`gmtime` 与时区无关，最稳。
    """
    import time as _time
    if not ts:
        return None
    return _time.strftime("%Y-%m-%d %H:%M", _time.gmtime(float(ts) + 8 * 3600))


def _tq_cache() -> Dict:
    """腾讯内存行情缓存本体（**只读**）—— 用于暴露"数据自身时刻"（`data_ts`/`from_snapshot`）。

    ★ 2026-09-29：为什么单独开一个函数而不是各处直接 `from app.tencent import _cache` ——
      `tencent` 的导入较重（会话/常量），这里统一 try 兜底：拿不到就返回 {}（消费方降级，
      不因为"想标注时点"而把整张持仓卡搞挂）。
    """
    try:
        from app.tencent import _cache
        return _cache or {}
    except Exception:
        return {}


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


def _latest_rank(codes: List[str]) -> Dict[str, Dict]:
    """持仓在最新榜单里的分数/排名（**不实时精算**）。

    ★ 为什么不实时算分：持仓十几只走精算路径要几十秒且盘中口径不稳；榜单是日批产出、
      口径与前端榜单一致 ⇒ 只在**持仓曾上榜**时给分，未上榜就不显示（诚实降级）。
    ★ 2026-09-23：**改回按 codes 过滤**（此前为省一次查询改成"读全量再过滤"）。
      原因：线上 Render 是 free 计划（512MB）⇒ **内存优先于查询次数**，持仓通常只有
      个位数，`IN (...)` 的内存占用远小于把当天全量榜单读进内存。
    """
    if not codes:
        return {}
    now = time.time()
    key = ",".join(sorted(codes))
    cache = _rank_cache.get("by_codes") or {}
    hit = cache.get(key)
    if hit and now - hit["ts"] < _RANK_TTL:
        return hit["val"]
    out: Dict[str, Dict] = {}
    try:
        from app.database import db
        row = db.fetch_one("SELECT MAX(rank_date) AS d FROM ranking_live")
        d = (row or {}).get("d")
        if d:
            marks = ",".join(["%s"] * len(codes))
            rows = db.fetch(
                f"SELECT code, total_score, signal, signal_level, rank_pos FROM ranking_live "
                f"WHERE rank_date = %s AND code IN ({marks})", (str(d)[:10], *codes))
            for r in rows or []:
                out[str(r["code"])] = {"total_score": r.get("total_score"),
                                       "signal": r.get("signal"),
                                       "signal_level": r.get("signal_level"),
                                       "rank_pos": r.get("rank_pos"),
                                       "rank_date": str(d)[:10]}
    except Exception as e:
        print(f"[portfolio_radar] 榜单读取失败（跳过该列）: {e}")
    cache[key] = {"ts": now, "val": out}
    if len(cache) > 8:      # 防无限增长（持仓组合数有限）
        for k in list(cache)[:-8]:
            cache.pop(k, None)
    _rank_cache["by_codes"] = cache
    return out


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


# ★ 2026-09-25（交易方法论落地计划 A5）：基本面雷区排雷标签 v0
#   数据：stock_finance_zz（zzshare finance_latest 快照，bal_json/cf_json/ind_json）
#   规则（v0，先规则后回测）：商誉/总资产 >30% → 商誉悬顶；
#     扣非净利 >0 但经营现金流 <0 → 利润含金量警示（金融行业跳过——银行负债结构
#     天然导致经营现金流口径失真）。
_FUND_FLAG_TTL = 6 * 3600.0
_fund_flag_cache: Dict[str, tuple] = {}   # {code: (ts, flags)}


def _fundamental_flags(codes: List[str], industry_of=None) -> Dict[str, List[Dict]]:
    """按 code 返回排雷提示（风险级）。财报低频 → 进程缓存 6h；失败返回 {}。"""
    import json as _json
    import time as _t
    now = _t.time()
    want = [c for c in dict.fromkeys(codes)
            if c not in _fund_flag_cache or now - _fund_flag_cache[c][0] > _FUND_FLAG_TTL]
    try:
        if want:
            from app.database import db
            rows = db.fetch(
                "SELECT code, bal_json, cf_json, ind_json FROM stock_finance_zz "
                "WHERE code = ANY(%s) LIMIT 500", (want,))
            fetched = {str(r.get("code")): r for r in (rows or [])}
            for c in want:
                flags: List[Dict] = []
                r = fetched.get(c)
                if r:
                    def _j(v):
                        try:
                            d = _json.loads(v) if isinstance(v, str) else (v or {})
                            return (d[0] if isinstance(d, list) and d else d) or {}
                        except Exception:
                            return {}
                    bal, cf, ind = _j(r.get("bal_json")), _j(r.get("cf_json")), _j(r.get("ind_json"))
                    gw, ta = bal.get("goodwill"), bal.get("total_assets")
                    try:
                        if gw and ta and float(ta) > 0 and float(gw) / float(ta) > 0.30:
                            flags.append({"level": "risk",
                                          "text": f"商誉/总资产 {float(gw) / float(ta) * 100:.0f}%（商誉悬顶，警惕减值）"})
                    except (TypeError, ValueError):
                        pass
                    ocf, ap = cf.get("net_operate_cash_flow"), ind.get("adjusted_profit")
                    ind_name = (industry_of(c) or "") if industry_of else ""
                    try:
                        if (not is_financial(ind_name) and ocf is not None
                                and float(ocf) < 0 and ap is not None and float(ap) > 0):
                            flags.append({"level": "risk",
                                          "text": "净利润为正但经营现金流为负（利润含金量警示）"})
                    except (TypeError, ValueError):
                        pass
                _fund_flag_cache[c] = (now, flags)
    except Exception as e:
        print(f"[portfolio_radar] fundamental flags failed: {e}")
    return {c: _fund_flag_cache[c][1] for c in codes if c in _fund_flag_cache}


def is_financial(ind_name: str) -> bool:
    """金融行业判定（排雷现金流规则跳过——银行负债结构致经营现金流口径失真）。"""
    return "金融" in (ind_name or "")


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


# ==============================================================================
#  组合层风控三件（2026-09-29）
# ==============================================================================
# 【为什么需要】`_alerts_for` 是**逐只**视角（止损/浮亏/板块被点名），专业风控还要看
#   **组合层**（工作台评价里指出的缺口）：
#     ① 风格暴露 —— 持仓大小盘 vs 当前 regime（defensive 期满仓小盘 = 顶风，
#        且 E2 已证明小盘 edge 是**条件性**的）；
#     ② 板块集中度 —— N 只同行业 = 隐性 β 集中（表面分散、实则一个板块）；
#     ③ 海外敏感链 —— 隔夜纳指/油价/汇率异动 → 点出受影响持仓。
#   三者数据**全部已有**（`quotes.float_cap` / `industry_map` / `macro` 面板）⇒ **零新增请求**。
# 【内存纪律】不加常驻缓存（每次 build 现算；持仓通常个位数）—— 见 scheduler
#   `portfolio_radar_warm_loop` 注释：Render 512MB 下常驻缓存是净负担。

# 流通市值分档（阈值用**亿元**；口径参照项目既有"市值 20-50 亿为最佳区间"的讨论）
_CAP_BANDS = ((50e8, "小盘"), (200e8, "中盘"), (float("inf"), "大盘"))

# 海外敏感链：行业关键词 → (隔夜指标, 阈值%, 链名, 市场名)
#   ★ 静态知识映射（不是预测），只回答"隔夜这件事可能影响哪些持仓"；阈值 = 经验初值。
#   ★★ 2026-09-29 词表校准（踩坑驱动）：首版用"石油/化工"等**想当然的词**，实测匹配不到——
#     同花顺细分行业名是「油气开采Ⅱ / 油服工程 / 炼化及贸易」，不是"石油"；
#     中国海油因此一条链都没命中。现按 `SELECT DISTINCT main_industry_code` 的**实际清单**
#     重写（实测清单见 memory）。
#     ⚠️ 刻意**不含**"能源"：会把「能源金属」（锂/钴，新能源链）误并进油气链。
_OVERSEAS_CHAINS = (
    (("电子", "半导体", "光学光电", "计算机", "通信", "软件", "元件", "消费电子"), "nasdaq", 1.5,
     "科技/果链", "纳指"),
    (("油气开采", "油服工程", "炼化", "煤炭开采", "焦炭", "燃气"), "brent", 3.0,
     "油气链", "布伦特"),
    (("家电", "纺织", "服装", "机械", "汽车", "航运", "港口", "造纸"), "usdcnh", 0.3,
     "出口链", "离岸人民币"),
)


def _cap_band(cap_wan: float) -> Optional[str]:
    """流通市值（万元）→ 分档。"""
    try:
        cap = float(cap_wan) * 1e4            # 万元 → 元
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    for thr, name in _CAP_BANDS:
        if cap < thr:
            return name
    return "大盘"


def _portfolio_risk(items: List[Dict], quotes: Dict[str, Dict],
                    industry_map: Dict[str, str], regime: str = "") -> Dict:
    """组合层风控三件。fail-open（任一段失败只跳过该段，不拖垮持仓卡）。"""
    out: Dict = {"available": False, "style": None, "concentration": [],
                 "overseas": [], "notes": []}
    try:
        # ── ① 风格暴露（流通市值中位数）──
        caps = []
        for it in items:
            fc = (quotes.get(it.get("code")) or {}).get("float_cap")
            if fc:
                caps.append(float(fc))
        if caps:
            caps.sort()
            med = caps[len(caps) // 2]
            band = _cap_band(med)
            style = {"median_float_cap_yi": round(med * 1e4 / 1e8, 1),
                     "band": band, "n": len(caps)}
            # 与 regime 的配合提示（评价里的原话：defensive 期满仓小盘 = 与 regime 顶风）
            if band == "小盘" and regime in ("defensive", "neutral_bearish"):
                style["warn"] = f"当前 {regime} 档 + 持仓以小盘为主 ⇒ 与市况顶风（小盘 edge 是条件性的）"
            out["style"] = style
        # ── ② 板块集中度（同行业 ≥2 只）──
        by_ind: Dict[str, List[str]] = {}
        for it in items:
            ind = it.get("industry")
            if ind:
                by_ind.setdefault(str(ind), []).append(it.get("name") or str(it.get("code")))
        conc = [{"industry": k, "n": len(v), "names": v[:6]} for k, v in by_ind.items()
                if len(v) >= 2]
        conc.sort(key=lambda x: -x["n"])
        out["concentration"] = conc
        if conc:
            out["notes"].append(
                "多只持仓同属一个行业 = 隐性 β 集中（表面分散、实际同涨同跌）："
                + "；".join(f"{c['industry']}×{c['n']}" for c in conc[:3]))
        # ── ③ 海外敏感链（隔夜异动 → 受影响持仓）──
        #   ★ 2026-09-29 踩坑修正：一级行业太粗（中国海油 = "其它行业"）⇒ 关键词匹配不到
        #     "石油"。改用 `stock_industry.main_industry_code`（细分名，如"油服工程"），
        #     一次批量查（持仓个位数，egress 可忽略）；失败回退一级行业（fail-open）。
        detail: Dict[str, str] = {}
        try:
            from app.database import db as _db
            _codes = [it.get("code") for it in items if it.get("code")]
            if _codes:
                _ph = ",".join(["%s"] * len(_codes))
                for r in _db.fetch(
                        f"SELECT code, main_industry, main_industry_code FROM stock_industry "
                        f"WHERE code IN ({_ph})", tuple(_codes)) or []:
                    detail[str(r.get("code"))] = str(
                        r.get("main_industry_code") or r.get("main_industry") or "")
        except Exception as e:
            print(f"[portfolio_radar] 细分行业读取失败（回退一级）: {e}")
        try:
            from app.macro import get_macro_panel
            panel = get_macro_panel() or {}
            for kws, src, thr, chain, label in _OVERSEAS_CHAINS:
                chg = (panel.get(src) or {}).get("change_pct")
                if chg is None:
                    continue
                try:
                    chg = float(chg)
                except (TypeError, ValueError):
                    continue
                if abs(chg) < thr:
                    continue
                hits = []
                for it in items:
                    c = str(it.get("code"))
                    ind = f"{it.get('industry') or ''}{detail.get(c, '')}"
                    if any(k in ind for k in kws):
                        hits.append(it.get("name") or c)
                if not hits:
                    continue
                out["overseas"].append({
                    "chain": chain, "market": label, "change_pct": round(chg, 2),
                    "threshold": thr, "holdings": hits[:6],
                    "note": f"{label} {chg:+.2f}%（阈值 ±{thr}%）⇒ 关注：{'、'.join(hits[:6])}"})
        except Exception as e:
            print(f"[portfolio_radar] 海外敏感链计算失败（跳过）: {e}")
        out["available"] = bool(out["style"] or conc or out["overseas"])
    except Exception as e:
        print(f"[portfolio_radar] 组合风控计算失败（跳过）: {e}")     # ASCII（铁律⑥）
    return out


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
        # ★ A5：合并基本面排雷标签（商誉悬顶/现金流含金量）
        try:
            _fmap = _fundamental_flags([code], industry_of=lambda c: industry_map.get(c) or "")
            item["alerts"] = (item["alerts"] or []) + _fmap.get(code, [])
        except Exception:
            pass
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

    # ★★ 2026-09-28（用户："持仓状态…数据时点 15:52" + "好几处的时间差 8 小时"）：
    #   原为 `time.strftime(...)` = **服务器本地时间** —— 生产（Render / Actions）上是 **UTC**
    #   ⇒ 前端显示的"数据时点"**少 8 小时**。本项目全链路北京时间（`flash.rules.beijing_now`）
    #   ⇒ 此处补齐（局部导入，避免与 flash 包产生模块级循环依赖）。
    from app.flash.rules import beijing_now as _bj_now
    return {
        "as_of": _bj_now().strftime("%Y-%m-%d %H:%M"),
        # ★★ 2026-09-29：**行情数据自身的时刻**（与上面 `as_of` = 计算时刻严格区分）。
        #   为什么必须分开（用户实测："中国海油昨天 -0.61%、今天还是 -0.61%"）：
        #   排查发现线上 `tencent._cache` 冻结在 **09-24 收盘快照**（`from_snapshot=True`、
        #   `data_ts≈09-24 15:00`），而卡片原来显示的是 **`as_of`（构建时刻 00:21）**
        #   ⇒ 看起来"刚刷新过"，实际数据是两天前的 —— 用户完全无从察觉。
        #   `tencent.py` 106-123 行的注释**早就写明**这条纪律（`last_update` = 缓存填充时刻、
        #   `data_ts` = 数据时刻），消费者要显示"数据截至"必须用后者。此处把它暴露给前端。
        "quote_as_of": _bj_ts_str(_tq_cache().get("data_ts") or 0),
        "quote_from_snapshot": bool(_tq_cache().get("from_snapshot")),
        "regime": regime,
        "strategy_date": str(sig_date)[:10] if sig_date else None,
        "watch_date": (list(watch.values())[0].get("date") if watch else None),
        "summary": {"n": len(items), "risk": n_risk, "opportunity": n_opp,
                    "in_watch": sum(1 for x in items if x.get("in_watch")),
                    "elapsed_ms": int((time.time() - t0) * 1000)},
        "market": {"contradictions": ctx.get("items") or []},
        # ★ 2026-09-29：组合层风控三件（逐只 alerts 之外的**组合视角**）
        "portfolio_risk": _portfolio_risk(items, quotes, industry_map, regime),
        "items": items,
    }
