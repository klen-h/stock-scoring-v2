"""
================================================================================
【文件作用】Coach 规则求值器（W1 纯规则骨架，2026-09-13）
================================================================================

★ 设计红线（PLAN §2.4 评审 ①）—— **「事前无权」而非「事后否决」**：
  判定结果与**全部数字由本模块（纯代码）产出**，LLM（W2 explainer）只能"渲染"，
  不能"决定"。这样「LLM 输出与规则冲突」在结构上不可能发生，而不是事后检测丢弃。
  因此本模块输出自洽、可单测、不依赖任何 LLM。

★ 阈值一律来自 `rules.yaml`（可调、可回滚）；判据的**输入**一律取现有信号源
  （regime / macro / margin_sentiment / breadth / 持仓），代码不写死点位。

★ 分层（评审 ②：30s 轮询不得放大 egress）：
    - **light**：只依赖内存缓存（全市场行情 `routers/market._cache`）与持仓表
      → 止损触发/浮亏/持有天数/市场闸门，可 30s 高频跑，**零额外网络**。
    - **heavy**：需读 K 线/筹码（ret20、price_pos/出货）→ 只在低频路径
      （关键时点体检卡 / 每日收盘回写）评估，见 `HEAVY_RULES`。

数据源（全部 fail-open：取不到即跳过该规则，绝不因数据缺失误报）：
  position → `paper_positions(holding)` + 全市场行情内存缓存
  regime   → `app.backtest.market_regime` 当日缓存（回退历史表）
  breadth  → 全市场行情内存缓存推导（涨跌家数/跌停数）
  macro    → `app.macro` 面板（us10y / dxy）
  margin   → `app.flash.margin_sentiment`（两融 5 日净变化 + 情绪温度计，24h 缓存）

用法：
  from app.coach import rules as coach_rules
  advices = coach_rules.evaluate_all()            # light 层（默认）
  advices = coach_rules.evaluate_all(tier="all")  # 含 heavy
================================================================================
"""

import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

import yaml

_RULES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rules.yaml")

# 需读 K 线/筹码的重规则（不在 30s 高频轮询里评估）
HEAVY_RULES = frozenset({"crowded_trim", "distribution_cut"})
# 逐持仓评估的规则；其余按"市场级"整体评估一条
POSITION_SCOPED = frozenset({
    "stop_loss_hit", "hold_3d_review", "loss_over_7pct",
    "crowded_trim", "distribution_cut",
})

_config_cache: Dict = {"data": None, "mtime": 0.0}


# ==============================================================================
#  数据结构
# ==============================================================================

@dataclass
class Advice:
    """一条教练建议（数字已由代码注入，LLM 只能照抄 —— 评审 ①）。"""
    rule_id: str
    label: str
    severity: str          # alert / warn / info
    push: bool             # 是否推企微（第一周仅两条为 True）
    code: str              # 市场级规则为空串
    name: str
    message: str           # 已渲染好数字的完整文案
    numbers: dict          # 结构化数字（落库 + 供 W2 做"引用校验"）

    def to_dict(self) -> dict:
        return asdict(self)


def load_config() -> dict:
    """读 rules.yaml（按 mtime 缓存，改文件即时生效，无需重启）。"""
    try:
        mtime = os.path.getmtime(_RULES_PATH)
    except OSError:
        mtime = 0.0
    if _config_cache["data"] is not None and _config_cache["mtime"] == mtime:
        return _config_cache["data"]
    with open(_RULES_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    cfg.setdefault("rules", [])
    _config_cache.update(data=cfg, mtime=mtime)
    return cfg


# ==============================================================================
#  数据源适配（全部 fail-open）
# ==============================================================================

def _market_cache() -> dict:
    """全市场行情内存缓存（**零网络**）—— 由 scheduler.stock_cache_refresh_loop
    每 120s 刷新（仅交易时段），Coach 高频轮询只读它，不自己拉行情（评审 ②）。"""
    try:
        from app.routers.market import _cache
        return _cache.get("stocks") or {}
    except Exception:
        return {}


def _quote(code: str) -> dict:
    return _market_cache().get(str(code)) or {}


def _breadth() -> Optional[dict]:
    """市场宽度（从内存缓存推导；缓存未就绪返回 None）。"""
    stocks = _market_cache()
    if not stocks:
        return None
    up = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) > 0)
    down = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) < 0)
    ld = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) <= -9.9)
    return {"total": len(stocks), "up": up, "down": down, "limit_down": ld,
            "up_down_ratio": round(up / max(1, up + down), 3)}


def _regime() -> dict:
    """当前市场状态（内存缓存优先 → 回退历史表）。"""
    for getter in ("get_regime_cache", "restore_regime_cache_from_db"):
        try:
            from app.backtest import market_regime as mr
            c = getattr(mr, getter)() or {}
            if c.get("state"):
                d = c.get("detail") or {}
                return {"state": c["state"], "ma_trend": d.get("ma_trend"),
                        "volatility_regime": d.get("volatility_regime"),
                        "regime_score": d.get("regime_score")}
        except Exception:
            continue
    return {}


def _macro() -> dict:
    """宏观面板关键项（us10y / dxy；面板自带缓存）。"""
    try:
        from app.macro import get_macro_panel
        p = get_macro_panel() or {}
        return {"us10y": (p.get("us10y") or {}).get("price"),
                "dxy": (p.get("dxy") or {}).get("price")}
    except Exception:
        return {}


def _margin() -> dict:
    """两融 + 情绪温度计（模块自带 24h 缓存 → 一天最多一次外网请求）。"""
    try:
        from app.flash.margin_sentiment import get_margin, get_sentiment
        m = get_margin() or {}
        s = get_sentiment() or {}
        return {"chg5": m.get("fund_bal_chg5"), "bal": m.get("fund_bal"),
                "score": s.get("score"), "zone": s.get("zone")}
    except Exception:
        return {}


def _hold_days(fill_date: Optional[str]) -> int:
    try:
        from app.strategies.paper_trading import _hold_days as hd
        return hd(fill_date or "")
    except Exception:
        return 0


def _ret20(code: str) -> Optional[float]:
    """近 20 个交易日涨幅 %（heavy：读 backtest_prices）。"""
    try:
        from app.database import db
        rows = db.fetch("SELECT close FROM backtest_prices WHERE code=%s "
                        "ORDER BY date DESC LIMIT 21", (code,))
        if not rows or len(rows) < 21:
            return None
        c0, c20 = float(rows[0]["close"] or 0), float(rows[20]["close"] or 0)
        return round((c0 / c20 - 1) * 100, 2) if c20 > 0 else None
    except Exception:
        return None


def _distribution(code: str) -> tuple:
    """出货嫌疑（heavy：mainforce_overlay 判定）。返回 (bool, info)。"""
    try:
        from app.mainforce.overlay import mainforce_overlay
        from app.backtest.data import load_prices
        bars = load_prices(code)
        if not bars or len(bars) < 120:
            return False, {}
        rows = None
        try:
            from app.mainforce.flow import load_flow
            rows = load_flow(code)
        except Exception:
            rows = None
        r = mainforce_overlay(bars, rows, regime=(_regime().get("state")))
        if r and r.get("signal") == "distribution":
            return True, r
    except Exception:
        pass
    return False, {}


# 真实持仓默认止损（%）：与前端 usePortfolio.ALERT_CONFIG.stopLossPct 一致，
# 避免「前端说 -8% 该卖、后端 Coach 说 -7%」的口径冲突。user_portfolio 无止损
# 字段 → 用成本价 × (1+此值) 生成默认止损（预承诺口径，与模拟盘退出 v2 同思路）。
REAL_STOP_LOSS_PCT = -8.0


def _real_holdings() -> List[dict]:
    """真实持仓（user_portfolio，用户手动录入）→ 转成与模拟盘同构的上下文。

    ★ `cost` 字段是**成本价（每股）**（见前端 usePortfolio.js 注释），不是总成本。
    ★ 无止损位字段 → 用 `REAL_STOP_LOSS_PCT` 生成默认止损。
    ★ 无买入日字段 → 用 `created_at`（录入日）近似持有起始日。
    """
    try:
        from app.database import db
        rows = db.fetch("SELECT * FROM user_portfolio ORDER BY created_at ASC")
    except Exception:
        return []
    # 按 code 去重：后端 upsert 设计是「一码一条」，但历史数据里有同 code 重复
    # （如分次买入未合并）→ 保留 created_at 最新一条，避免同一股票被 Coach
    # 重复盯、重复推止损警报（rows 已按 created_at ASC，后写的覆盖前面的）。
    by_code = {}
    for h in rows or []:
        q = _quote(h["code"])
        price = float((q or {}).get("price") or 0)
        cost = float(h.get("cost") or 0)
        if price <= 0 or cost <= 0:
            continue
        stop = round(cost * (1 + REAL_STOP_LOSS_PCT / 100), 2)
        created = str(h.get("created_at") or "")[:10]
        by_code[str(h["code"])] = {
            "code": str(h["code"]), "name": h.get("name") or h["code"],
            "fill_price": cost, "stop_loss": stop, "shares": h.get("shares"),
            "fill_date": created, "strategy_name": "real", "source": "real",
            "price": price, "pnl_pct": round((price / cost - 1) * 100, 2),
            "dist_stop_pct": round((price / stop - 1) * 100, 2),
            "hold_days": _hold_days(created),
        }
    return list(by_code.values())


def _holdings(heavy: bool = False) -> List[dict]:
    """持仓上下文 = 模拟盘（paper_positions）+ 真实持仓（user_portfolio）合并。

    现价/浮盈亏/距止损/持有天数；heavy 时补 ret20。`source` 区分 paper/real。
    """
    try:
        from app.database import db
        rows = db.fetch("SELECT * FROM paper_positions WHERE status='holding'")
    except Exception:
        rows = []
    out = []
    for h in rows or []:
        q = _quote(h["code"])
        price = float((q or {}).get("price") or 0)
        fill = float(h.get("fill_price") or 0)
        if price <= 0 or fill <= 0:
            continue          # 无现价（缓存未就绪/停牌）→ 本轮跳过，不误报
        stop = float(h.get("stop_loss") or 0)
        item = {
            "code": str(h["code"]), "name": h.get("name") or h["code"],
            "fill_price": fill, "stop_loss": stop, "shares": h.get("shares"),
            "fill_date": h.get("fill_date"), "strategy_name": h.get("strategy_name"),
            "source": "paper",
            "price": price, "pnl_pct": round((price / fill - 1) * 100, 2),
            "dist_stop_pct": (round((price / stop - 1) * 100, 2) if stop > 0 else None),
            "hold_days": _hold_days(h.get("fill_date")),
        }
        if heavy:
            item["ret20"] = _ret20(item["code"])
        out.append(item)
    out += _real_holdings()
    return out


def build_context(tier: str = "light") -> dict:
    ctx = {"regime": _regime(), "breadth": _breadth(), "macro": _macro(),
           "margin": _margin(), "positions": _holdings(heavy=(tier == "all"))}
    ctx["day_pnl_pct"] = None
    try:
        from app.strategies.paper_trading import get_account, INITIAL_CAPITAL
        acc = get_account() or {}
        realized = float(acc.get("realized_pnl") or 0)
        unreal = sum((p["price"] - p["fill_price"]) / p["fill_price"]
                     * float(p.get("shares") or 0) * p["fill_price"]
                     for p in ctx["positions"])
        ctx["day_pnl_pct"] = round((realized + unreal) / INITIAL_CAPITAL * 100, 2)
    except Exception:
        pass
    return ctx


# ==============================================================================
#  求值器（每条规则一个纯函数：返回 None=不触发，或 {message, numbers}）
# ==============================================================================

def _ev_stop_loss_hit(pos, params, ctx):
    stop, price = pos.get("stop_loss") or 0, pos.get("price") or 0
    if stop <= 0 or price > stop:
        return None
    tag = "真实持仓 " if pos.get("source") == "real" else ""
    return {
        "message": (f"【止损触发】{tag}{pos['name']} {pos['code']}：止损位 "
                    f"{stop:.2f} 已破（现价 {price:.2f}，浮盈亏 {pos['pnl_pct']:+.1f}%）。\n"
                    f"按纪律离场；若要放弃请填理由（回写供周报复盘）。"),
        "numbers": {"stop_loss": stop, "price": price, "pnl_pct": pos["pnl_pct"],
                    "source": pos.get("source")},
    }


def _ev_hold_3d_review(pos, params, ctx):
    n = int(params.get("hold_days") or 3)
    hd = pos.get("hold_days") or 0
    if hd < n:
        return None
    tag = "真实持仓 " if pos.get("source") == "real" else ""
    return {
        "message": (f"【第 {n} 日强制评估】{tag}{pos['name']} {pos['code']} "
                    f"已持有 {hd} 日，浮盈亏 {pos['pnl_pct']:+.1f}%"
                    f"（现价 {pos['price']:.2f}）。\n"
                    f"规则：到期/破位/达移动止损三者任一未触发 → 按计划离场，不临场改判。"),
        "numbers": {"hold_days": hd, "threshold": n, "pnl_pct": pos["pnl_pct"],
                    "source": pos.get("source")},
    }


def _ev_loss_over_7pct(pos, params, ctx):
    thr = float(params.get("loss_pct") or 7.0)
    pnl = pos.get("pnl_pct") or 0
    if pnl > -thr:
        return None
    return {
        "message": (f"【浮亏超 {thr:.0f}%】{pos['name']} {pos['code']} 浮亏 {pnl:.1f}%，"
                    f"现价 {pos['price']:.2f}。按纪律评估止损；若同时命中「高位+主力出货」"
                    f"则不等 {thr:.0f}% 直接离场。"),
        "numbers": {"pnl_pct": pnl, "threshold": -thr},
    }


def _ev_crowded_trim(pos, params, ctx):
    thr = float(params.get("ret20_pct") or 30.0)
    r = pos.get("ret20")
    if r is None or r <= thr:
        return None
    return {
        "message": (f"【拥挤赛道借反弹减仓】{pos['name']} {pos['code']} 近 20 日涨幅 "
                    f"{r:+.1f}%（>{thr:.0f}%），现价 {pos['price']:.2f}/浮盈亏 "
                    f"{pos['pnl_pct']:+.1f}%。优先在反弹中减仓，不追加。"),
        "numbers": {"ret20": r, "threshold": thr, "pnl_pct": pos["pnl_pct"]},
    }


def _ev_distribution_cut(pos, params, ctx):
    thr = float(params.get("price_pos") or 0.75)
    hit, info = _distribution(pos["code"])
    if not hit:
        return None
    pp = info.get("price_pos")
    if pp is not None and pp <= thr:
        return None
    return {
        "message": (f"【高位+主力出货 → 不等 7% 直接砍】{pos['name']} {pos['code']}："
                    f"{(info.get('reason') or '')[:80]}。现价 {pos['price']:.2f}"
                    f"（浮盈亏 {pos['pnl_pct']:+.1f}%）。"),
        "numbers": {"price_pos": pp, "threshold": thr, "pnl_pct": pos["pnl_pct"]},
    }


def _ev_gate_no_add(pos, params, ctx):
    st = (ctx.get("regime") or {}).get("state")
    if st not in ("defensive", "neutral_bearish"):
        return None
    return {
        "message": (f"【环境不支持加仓】当前 regime = {st}"
                    f"（ma_trend={(ctx.get('regime') or {}).get('ma_trend')}）。"
                    f"准空仓运行、只减不加；等 regime 修复自动恢复。"),
        "numbers": {"regime": st},
    }


def _ev_gate_reduce(pos, params, ctx):
    hits = []
    m, mc = ctx.get("macro") or {}, ctx.get("margin") or {}
    u10 = m.get("us10y")
    if u10 is not None and u10 > float(params.get("us10y_pct") or 5.0):
        dxy = m.get("dxy")
        if dxy is not None and dxy > float(params.get("dxy") or 100.0):
            hits.append(f"10Y {u10:.2f}%>5% 且美元指数 {dxy:.1f}>100")
    chg5, score = mc.get("chg5"), mc.get("score")
    if (chg5 is not None and chg5 < 0
            and score is not None and score <= float(params.get("margin_cold") or 30.0)):
        hits.append(f"两融 5 日净减 {abs(chg5):.0f} 亿 + 情绪温度计 {score:.0f} 分（寒冷区）")
    if not hits:
        return None
    return {
        "message": "【减仓条件命中】" + "；".join(hits) + "。按纪律砍到最低仓位。",
        "numbers": {"us10y": u10, "dxy": m.get("dxy"), "margin_chg5": chg5,
                    "sentiment": score},
    }


def _today_str() -> str:
    try:
        from app.flash import rules as flash_rules
        return flash_rules.beijing_now().strftime("%Y-%m-%d")
    except Exception:
        return ""


def _gate_add_prev_day_hit(today_str: str) -> bool:
    """上一个交易日 `gate_add` 是否命中（查 coach_alerts 的市场级落库记录）。

    ★ 连续 2 日确认的实现**零新增存储**：gate_add 每命中一天就落一条
      （dedupe_key = 日|规则|-，每天至多一条），今日命中时回查昨日记录即可。
      昨日无记录（未命中 / 宽度数据缺失）→ False（保守方向：不加仓）。
    """
    try:
        from datetime import datetime, timedelta

        from app.database import db
        from app.flash import rules as flash_rules
        d = datetime.strptime(today_str, "%Y-%m-%d").date()
        for _ in range(10):
            d -= timedelta(days=1)
            try:
                if not flash_rules.is_trading_day(datetime(d.year, d.month, d.day)):
                    continue
            except Exception:
                pass   # 交易日历不可用 → 按自然日近似（周末不会有落库记录，自然跳过）
            row = db.fetch_one(
                "SELECT id FROM coach_alerts WHERE rule_id='gate_add' "
                "AND alert_date=%s LIMIT 1", (d.strftime("%Y-%m-%d"),))
            return bool(row)
    except Exception:
        return False
    return False


def _ev_gate_add(pos, params, ctx):
    b = ctx.get("breadth") or {}
    ratio, ld = b.get("up_down_ratio"), b.get("limit_down")
    thr_r = float(params.get("up_down_ratio") or 0.8)
    thr_ld = int(params.get("limit_down_max") or 10)
    if ratio is None or ld is None:
        return None
    if ratio > thr_r and ld < thr_ld:
        # ★ 连续 2 日确认（简报 §2.3 加仓①）：昨日 gate_add 命中过 → 今日再命中
        #   = 确认成立；否则为第 1 日命中，只提示不确认（W1 占位已补齐）。
        confirmed = _gate_add_prev_day_hit(_today_str())
        if confirmed:
            return {
                "message": (f"【加仓条件确认（连续 2 日）】涨跌比 {ratio:.2f}（>{thr_r}）"
                            f"且跌停 {ld} 家（<{thr_ld}），昨日已命中 → 确认成立。"
                            f"按计划从准空仓向中性加仓。"),
                "numbers": {"up_down_ratio": ratio, "limit_down": ld, "confirmed": True},
            }
        return {
            "message": (f"【加仓条件部分命中（第 1 日）】涨跌比 {ratio:.2f}（>{thr_r}）"
                        f"且跌停 {ld} 家（<{thr_ld}）。★ 需连续 2 日确认，"
                        f"明日再命中才成立。"),
            "numbers": {"up_down_ratio": ratio, "limit_down": ld, "confirmed": False},
        }
    return None


def _ev_emotion_fuse(pos, params, ctx):
    thr = float(params.get("day_loss_pct") or 2.0)
    d = ctx.get("day_pnl_pct")
    if d is None or d > -thr:
        return None
    return {
        "message": (f"【情绪熔断】当日浮亏 {d:.2f}%（>{thr:.0f}%）→ 进入闭嘴模式："
                    f"只输出规则原文，不做安抚性解读、不新增建议。"),
        "numbers": {"day_pnl_pct": d, "threshold": -thr},
    }


EVALUATORS = {
    "stop_loss_hit": _ev_stop_loss_hit,
    "hold_3d_review": _ev_hold_3d_review,
    "loss_over_7pct": _ev_loss_over_7pct,
    "crowded_trim": _ev_crowded_trim,
    "distribution_cut": _ev_distribution_cut,
    "gate_no_add": _ev_gate_no_add,
    "gate_reduce": _ev_gate_reduce,
    "gate_add": _ev_gate_add,
    "emotion_fuse": _ev_emotion_fuse,
}


# ==============================================================================
#  主入口
# ==============================================================================

def evaluate_all(tier: str = "light", only_push: bool = False,
                 config: Optional[dict] = None,
                 ctx: Optional[dict] = None) -> List[Advice]:
    """
    求值全部（或仅推送）规则。

    tier="light"：跳过 HEAVY_RULES（读 K 线的规则）→ 可 30s 高频、零额外网络。
    tier="all"  ：含 heavy（关键时点体检卡 / 每日收盘回写用）。
    ctx         ：可传入预构建上下文（体检卡已算过一次，避免重复读行情）。
    """
    cfg = config if config is not None else load_config()
    if ctx is None:
        ctx = build_context(tier=tier)
    out: List[Advice] = []
    for r in cfg.get("rules") or []:
        rid = r.get("id")
        if not r.get("enabled", True) or rid not in EVALUATORS:
            continue
        if only_push and not r.get("push"):
            continue
        if tier != "all" and rid in HEAVY_RULES:
            continue
        fn = EVALUATORS[rid]
        targets = ctx["positions"] if rid in POSITION_SCOPED else [None]
        for t in targets:
            try:
                hit = fn(t, r.get("params") or {}, ctx)
            except Exception as e:
                print(f"[coach] 规则 {rid} 求值异常（跳过）: {e}")
                continue
            if not hit:
                continue
            out.append(Advice(
                rule_id=rid, label=r.get("label") or rid,
                severity=r.get("severity") or "info", push=bool(r.get("push")),
                code=(t or {}).get("code", "") if t else "",
                name=(t or {}).get("name", "") if t else "",
                message=hit["message"], numbers=hit.get("numbers") or {}))
    return out


def format_batch(advices: list) -> str:
    """把建议列表渲染成企微 Markdown（体检卡 / 警报批）。

    兼容 `Advice` 对象与 dict（落库返回值是 dict）。
    """
    if not advices:
        return ""
    lines = []
    for a in advices:
        get = (lambda k: getattr(a, k, None)) if isinstance(a, Advice) \
            else (lambda k: a.get(k))
        icon = {"alert": "🔴", "warn": "🟡", "info": "🔵"}.get(get("severity"), "•")
        lines.append(f"{icon} **{get('label')}**\n{get('message')}")
    return "\n\n".join(lines)
