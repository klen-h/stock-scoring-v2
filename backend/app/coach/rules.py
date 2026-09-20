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

import json
import os
import time as _time
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

    def _ld_thr(code: str) -> float:
        # ★ 审查 P2-14：创业板(30)/科创板(68) 为 20cm 涨跌幅——-9.9% 未封死
        #   跌停的票不应计入跌停家数（北交所 30cm 未单独处理，样本极少）
        return -19.9 if str(code).startswith(("30", "68")) else -9.9

    ld = sum(1 for c, s in stocks.items()
             if (s.get("change_pct") or 0) <= _ld_thr(c))
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


_macro_cache = {"ts": 0.0, "val": {}}


def _macro() -> dict:
    """宏观面板关键项（us10y / dxy；面板自带缓存 + 本地 10min 结果缓存）。

    ★ 审查 P2-18：30s 轮询下 macro 面板 60s TTL 过期即重建（回源外网多源请求），
      与「light 层零额外网络」不符 → 结果缓存 10min，盘中至多 6 次重建/小时。
    """
    now = _time.time()
    if _macro_cache["val"] and now - _macro_cache["ts"] < 600:
        return _macro_cache["val"]
    try:
        from app.macro import get_macro_panel
        p = get_macro_panel() or {}
        nq = p.get("nasdaq") or {}
        val = {"us10y": (p.get("us10y") or {}).get("price"),
               "dxy": (p.get("dxy") or {}).get("price"),
               # ★ 2026-09-20：纳指隔夜（外部领先因子，见 `_ev_external_lead`）
               "nasdaq": nq.get("price"),
               "nasdaq_pct": nq.get("change_pct")}
    except Exception:
        return _macro_cache["val"]   # 重建失败沿用旧值（fail-open）
    _macro_cache.update(ts=now, val=val)
    return val


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
        # ★ 2026-09-17：user_portfolio 是**多用户表**，此前这里全表读 → 教练会盯
        #   **所有账号**的持仓（实测：admin 的教练一直提示 sky 的海德股份 000567，
        #   而 admin 界面上根本看不到这条 → 现象即"持仓删了，教练还提示"）。
        from app.portfolio_scope import portfolio_where
        _w, _p = portfolio_where()
        rows = db.fetch(
            f"SELECT * FROM user_portfolio {_w} ORDER BY created_at ASC", _p)
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


_day_realized_cache = {"key": None, "val": None}


def build_context(tier: str = "light") -> dict:
    ctx = {"regime": _regime(), "breadth": _breadth(), "macro": _macro(),
           "margin": _margin(), "positions": _holdings(heavy=(tier == "all")),
           "idx_prev_move": _idx_last_move(),
           "market_avg_change_pct": _market_avg_change_pct()}
    ctx["day_pnl_pct"] = None
    try:
        from app.strategies.paper_trading import INITIAL_CAPITAL, _bj_date
        # ★ 审查 P1-15：「当日浮亏」= **当日已实现盈亏**（exit_date=今天）+ 当前
        #   浮盈亏。此前用 paper_account.realized_pnl（账户终身累计）→ 熔断跨日
        #   不重置、历史盈亏永久污染当日口径（真实持仓浮盈亏已含在 unreal 内；
        #   真实持仓的卖出不入账，属已知口径边界）。
        from app.flash import rules as flash_rules
        key = f"{_bj_date()}:{flash_rules.beijing_now().strftime('%H%M')}"
        if _day_realized_cache["key"] != key:
            from app.database import db
            _row = db.fetch_one(
                "SELECT COALESCE(SUM(pnl_amount), 0) AS v FROM paper_positions "
                "WHERE status='closed' AND exit_date = %s", (_bj_date(),))
            _day_realized_cache["key"] = key
            _day_realized_cache["val"] = float((_row or {}).get("v") or 0)
        day_realized = _day_realized_cache["val"]
        unreal = sum((p["price"] - p["fill_price"]) / p["fill_price"]
                     * float(p.get("shares") or 0) * p["fill_price"]
                     for p in ctx["positions"])
        ctx["day_pnl_pct"] = round((day_realized + unreal) / INITIAL_CAPITAL * 100, 2)
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
    # ★ 审查 P1-16：只在第 n 日触发一次（旧 hd >= n 会对存量老持仓**每天**推送，
    #   几天即造成警报免疫）。真实持仓以 created_at 近似买入日，hd 只会 > n
    #   → 上线前就持有的老票不会再被此规则重复轰炸。
    if hd != n:
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


_idx_move_cache = {"key": None, "val": None}


def _idx_last_move() -> Optional[dict]:
    """沪深300 最近一个交易日涨跌幅（backtest_prices 最新两根收盘）。

    ★ Phase 0 画像净产出（2026-09-16，scripts/rhythm_profile.py，n=127）：
      本市场短期反转 R_{t-1}=-0.169（t=-2.33）→ 昨日大涨/大跌对今日有反向含义。
    盘中最新一根=昨日（当日 15:40 才回填）；按日期缓存，30s 轮询零重复查询。
    """
    try:
        from app.database import db
        rows = db.fetch("SELECT date, close FROM backtest_prices WHERE code='sh000300' "
                        "ORDER BY date DESC LIMIT 2")
        if not rows or len(rows) < 2:
            return None
        d = str(rows[0]["date"])
        if _idx_move_cache["key"] != d:
            c0, c1 = float(rows[0]["close"] or 0), float(rows[1]["close"] or 0)
            _idx_move_cache.update(key=d, val=(
                {"date": d, "pct": round((c0 / c1 - 1) * 100, 2)} if c1 > 0 else None))
        return _idx_move_cache["val"]
    except Exception:
        return None


def _market_avg_change_pct() -> Optional[float]:
    """全市场等权平均涨幅 %（内存缓存推导，零网络；缓存未就绪返回 None）。"""
    stocks = _market_cache()
    if not stocks:
        return None
    vals = [s.get("change_pct") for s in stocks.values() if s.get("change_pct") is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def _today_str() -> str:
    try:
        from app.flash import rules as flash_rules
        return flash_rules.beijing_now().strftime("%Y-%m-%d")
    except Exception:
        return ""


def _gate_add_prev_day_hit(today_str: str, need_days: int = 2) -> bool:
    """近 need_days-1 个交易日 gate_add 是否均命中（连续 need_days 日确认）。
    任一日无记录（未命中 / 宽度数据缺失）→ False（保守方向：不加仓）。
    ★ 审查 P2-⑧：此前 yaml consecutive_days 参数不被读取、代码固定回看 1 日。"""
    try:
        from datetime import datetime, timedelta

        from app.database import db
        from app.flash import rules as flash_rules
        need = max(1, int(need_days) - 1)
        d = datetime.strptime(today_str, "%Y-%m-%d").date()
        confirmed = 0
        for _ in range(30):
            if confirmed >= need:
                return True
            d -= timedelta(days=1)
            try:
                if not flash_rules.is_trading_day(datetime(d.year, d.month, d.day)):
                    continue
            except Exception:
                pass   # 交易日历不可用 → 按自然日近似（周末不会有落库记录，自然跳过）
            row = db.fetch_one(
                "SELECT id FROM coach_alerts WHERE rule_id='gate_add' "
                "AND alert_date=%s LIMIT 1", (d.strftime("%Y-%m-%d"),))
            if not row:
                return False
            confirmed += 1
        return confirmed >= need
    except Exception:
        return False


def _ev_gate_add(pos, params, ctx):
    b = ctx.get("breadth") or {}
    ratio, ld = b.get("up_down_ratio"), b.get("limit_down")
    thr_r = float(params.get("up_down_ratio") or 0.8)
    thr_ld = int(params.get("limit_down_max") or 10)
    need_days = int(params.get("consecutive_days") or 2)
    if ratio is None or ld is None:
        return None
    if ratio > thr_r and ld < thr_ld:
        # ★ 连续 N 日确认（简报 §2.3 加仓①，N = yaml consecutive_days）：
        #   昨日（及此前 N-1 日）gate_add 命中过 → 今日再命中 = 确认成立；
        #   否则为第 1 日命中，只提示不确认（W1 占位已补齐）。
        confirmed = _gate_add_prev_day_hit(_today_str(), need_days)
        if confirmed:
            return {
                "message": (f"【加仓条件确认（连续 {need_days} 日）】涨跌比 {ratio:.2f}（>{thr_r}）"
                            f"且跌停 {ld} 家（<{thr_ld}），此前已连续命中 → 确认成立。"
                            f"按计划从准空仓向中性加仓。"),
                "numbers": {"up_down_ratio": ratio, "limit_down": ld,
                            "confirmed": True, "consecutive_days": need_days},
            }
        return {
            "message": (f"【加仓条件部分命中（第 1 日）】涨跌比 {ratio:.2f}（>{thr_r}）"
                        f"且跌停 {ld} 家（<{thr_ld}）。★ 需连续 {need_days} 日确认，"
                        f"明日再命中才成立。"),
            "numbers": {"up_down_ratio": ratio, "limit_down": ld,
                        "confirmed": False, "consecutive_days": need_days},
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


# ── D. 市场微观结构纪律（Phase 0 画像三条净产出，2026-09-16 落库，第一周不推）────
# 依据：scripts/rhythm_profile.py（n=127 交易日，详见 Gate 判定书 2026-09-16 §6）。
#  ① 确认流入 = 短期顶部选择器（流入确认日后 20 日 -2.2%，差 -236bp）
#  ② 市场短期反转 R_{t-1}=-0.169（t=-2.33）
#  ③ 涨日即轧差口径「主力」净卖日（指数+1% → 次日超额流 -27 亿）

def _ev_no_chase_rally(pos, params, ctx):
    thr = float(params.get("big_move_pct") or 1.5)
    m = ctx.get("market_avg_change_pct")
    if m is None or m < thr:
        return None
    return {
        "message": (f"【涨了别追】今日全市场等权涨幅 {m:+.2f}%（≥{thr:g}%）。\n"
                    f"127 日画像：本市场短期反转（R_(t-1)=-0.169，t=-2.33），大涨后次日"
                    f"平均偏弱；且指数每 +1% 当日主力轧差口径净卖 27 亿——涨日就是主力在卖。"
                    f"大涨日不追加，已有仓位按剧本持有。"),
        "numbers": {"market_avg_pct": m, "threshold": thr},
    }


def _ev_reversal_no_add(pos, params, ctx):
    thr = float(params.get("big_move_pct") or 1.5)
    m = ctx.get("idx_prev_move")
    if not m or m.get("pct") is None or m["pct"] < thr:
        return None
    return {
        "message": (f"【昨日大涨，今日不加仓】沪深300 {m['date']} 收 {m['pct']:+.2f}%"
                    f"（≥{thr:g}%）。\n"
                    f"127 日画像：本市场短期反转（R_(t-1) 系数 -0.169，t=-2.33），"
                    f"大涨后一日平均偏弱。纪律：不追昨日涨幅。"),
        "numbers": {"idx_pct": m["pct"], "date": m["date"], "threshold": thr},
    }


def _ev_reversal_no_panic(pos, params, ctx):
    thr = float(params.get("big_move_pct") or 1.5)
    m = ctx.get("idx_prev_move")
    if not m or m.get("pct") is None or m["pct"] > -thr:
        return None
    return {
        "message": (f"【昨日大跌，不恐慌割肉】沪深300 {m['date']} 收 {m['pct']:+.2f}%"
                    f"（≤{-thr:g}%）。\n"
                    f"127 日画像：短期反转（R_(t-1)=-0.169），大跌后一日有反弹倾向；"
                    f"离场按剧本（止损/到期）执行，不情绪化追卖。"),
        "numbers": {"idx_pct": m["pct"], "date": m["date"], "threshold": -thr},
    }


# ── E. 外部领先（Phase 3，2026-09-20 落地）────────────────────────────────────
# 与 D 组同源（都是"短期反转/不追涨"的纪律），但**数据源在外部**：
#   依据 `scripts/regime_external_lead_test.py`（regime 历史重放 687 天）——
#   外部领先因子（纳指隔夜 + 美元 5 日）**只在 defensive 市有增量价值**：
#     防御市未预警日：未来 5/10 日 +1.22% / +2.50%（超跌反弹）
#     防御市预警日　：未来 5/10 日 -0.22% / -0.33%
#     （差 1.44 / 2.83pt；滞后 1 日可交易口径 10 日差 2.53pt）
#     ⚠️ 但同口径 **5 日仅差 0.96pt（<1.0 门槛，不达标）** ⇒ 可交易效力集中在
#       **10 日窗**，别按 5 日读（2026-09-20 复核补充）。
#   因子集复核（2026-09-20）：VIX 此前**从未被评估**（预警定义硬编码 NQ+USD 两项），
#     补测后确认**不该加入** —— 与 NQ 中度重叠（Jaccard 0.52，独有 12 天），
#     并入后 10 日差 2.53pt → **2.05pt**（稀释）⇒ 维持两因子 ✓
#   offensive/neutral 无增量（差 ≤0.3pt）⇒ 本评估器**硬性限定 defensive**。
#   单变量领先性：纳指 1 日窗 IC +0.1609 ｜ 美元（UDI）5 日窗 IC -0.1005；
#   ⚠️ 这两个数是 **same（同日）口径**、含**时区前视**（美股夜盘在 A 股收盘之后）
#     ⇒ **不可交易**。改按 **lag1 可交易口径**实测：三者 |IC| 全部塌陷至 ≤0.04
#     （NQ +0.1609→+0.0115、VX -0.1124→-0.0052、UDI -0.0972→-0.0230）
#     ⇒ 见 `scripts/fake_lead_diagnosis.py`。故本规则的依据**不是"线性领先"**，
#     而是 defensive 下的**条件差异**（那才是 Phase 2 用 lag1 分组测出的 1.44/2.83pt）。
#   美债 10Y/30Y 仅 ~0.066（同步而非领先）⇒ 不采用。

_USD5_CACHE = {"ts": 0.0, "val": None}


def _parse_macro_date(s):
    """`macro_daily.date` → date 对象。兼容 `2026/9/7` 与 `2026-09-07` 两种写法。

    ⚠️ 该表历史格式是**斜杠且不补零**，**不能靠 SQL 字符串排序**
      （`'2026/9/10' < '2026/9/7'` 会错序）⇒ 必须取回后按日期对象排序。
    """
    from datetime import datetime as _dt
    parts = str(s or "").strip().replace("/", "-").split("-")
    if len(parts) != 3:
        return None
    try:
        return _dt(int(parts[0]), int(parts[1]), int(parts[2])).date()
    except (ValueError, TypeError):
        return None


def _usd_5d_change(days: int = 5):
    """美元指数近 N 日**累计**变化（%）—— 数据源 `macro_daily` 日度快照。

    取不到（快照攒得不够 / 面板缺 dxy）返回 None ⇒ 调用方只用纳指一侧，**不误报**。
    快照结构与 `get_macro_snapshot()` 同构：`{"panel": {"dxy": {"price": ...}}}`。
    1 小时进程缓存（日度数据，不必频繁读库）。
    """
    now = _time.time()
    if _USD5_CACHE["val"] is not None and now - _USD5_CACHE["ts"] < 3600:
        return _USD5_CACHE["val"]
    try:
        from app.database import db
        rows = db.fetch("SELECT date, data_json FROM macro_daily "
                        "ORDER BY date DESC LIMIT %s", (int(days) + 4,)) or []
        pts = []
        for r in rows:
            d = _parse_macro_date(r.get("date"))
            if not d:
                continue
            try:
                price = (json.loads(r.get("data_json") or "{}")
                         .get("panel", {}).get("dxy", {}).get("price"))
            except (ValueError, TypeError, AttributeError):
                price = None
            if price:
                pts.append((d, float(price)))
        if len(pts) < int(days) + 1:
            return None
        pts.sort(key=lambda x: x[0])
        # ★★ 新鲜度校验（2026-09-20）：最后一点必须接近「最近已完成交易日」，
        #   否则 `macro_daily` 停更时会拿**过期数据**算出"近 N 日变化"⇒ 误报。
        #   实测就是这种情况：9-19 之前该表断更，最新只是 8-31~9-07 那批残留。
        #   用全项目唯一口径 `latest_completed_trading_day()`，容忍 5 个自然日。
        try:
            from app.flash.rules import latest_completed_trading_day
            expect = _parse_macro_date(latest_completed_trading_day())
        except Exception:
            expect = None
        if expect and (expect - pts[-1][0]).days > 5:
            print(f"[coach] 美元序列过期（最新 {pts[-1][0]}，应至 {expect}）"
                  f"⇒ 本次只用纳指一侧")
            return None
        base = pts[-(int(days) + 1)][1]
        if not base:
            return None
        val = (pts[-1][1] / base - 1) * 100
        _USD5_CACHE.update(ts=now, val=val)
        return val
    except Exception as e:
        print(f"[coach] 美元 N 日变化读取失败（降级为只用纳指）: {e}")
        return None


def _external_lead(ctx: dict, params: dict) -> dict:
    """外部领先预警合成：纳指隔夜跌 **或** 美元 N 日走强（任一命中即预警）。"""
    macro = ctx.get("macro") or {}
    nq = macro.get("nasdaq_pct")
    usd5 = _usd_5d_change(int(params.get("usd5_days") or 5))
    thr_nq = float(params.get("nq_pct") or -0.76)
    thr_usd = float(params.get("usd5_pct") or 0.66)
    hits, missing = [], []
    if nq is None:
        missing.append("纳指隔夜")
    elif nq < thr_nq:
        hits.append(f"纳指隔夜 {nq:+.2f}%（<{thr_nq:g}%）")
    if usd5 is None:
        missing.append(f"美元{int(params.get('usd5_days') or 5)}日")
    elif usd5 > thr_usd:
        hits.append(f"美元{int(params.get('usd5_days') or 5)}日累计 {usd5:+.2f}%（>{thr_usd:g}%）")
    return {"warn": bool(hits), "hits": hits, "missing": missing,
            "nasdaq_pct": nq, "usd_pct": usd5}


def _ev_external_lead(pos, params, ctx):
    """【防御市 + 外部预警】不要期待超跌反弹。**只在 defensive 触发**。

    与同组的 `reversal_no_panic`（昨日大跌不恐慌割肉）**不矛盾**，是互补：
      那条说"别恐慌割肉"，这条说"但也别指望反弹"——**不恐慌 ≠ 期待反弹**。
    """
    regime = (ctx.get("regime") or {}).get("state")
    if regime != "defensive":
        return None
    lead = _external_lead(ctx, params)
    if not lead["warn"]:
        return None
    note = (f"（{'、'.join(lead['missing'])} 数据缺，本次未纳入判定）"
            if lead["missing"] else "")
    return {
        "message": (
            f"【防御市 + 外部预警】{'；'.join(lead['hits'])}。\n"
            "回测（regime 重放 687 天）：防御市里**未预警**日未来 5/10 日 "
            "+1.22%/+2.50%（超跌反弹），而**外部预警**日退到 -0.22%/-0.33% "
            "⇒ 本次『跌多了会弹』的预期**不成立**。\n"
            "纪律：① 不抢反弹、不因跌幅大而抄底；② 已持仓仍按剧本离场"
            "（止损/到期），**不要**因此恐慌提前割 —— 与「昨日大跌不恐慌割肉」"
            "不矛盾：不恐慌 ≠ 期待反弹。" + note),
        "numbers": {"regime": regime, "nasdaq_pct": lead["nasdaq_pct"],
                    "usd_pct": lead["usd_pct"], "hits": lead["hits"],
                    "missing": lead["missing"]},
    }


EVALUATORS = {
    "external_lead_no_rebound": _ev_external_lead,
    "stop_loss_hit": _ev_stop_loss_hit,
    "hold_3d_review": _ev_hold_3d_review,
    "loss_over_7pct": _ev_loss_over_7pct,
    "crowded_trim": _ev_crowded_trim,
    "distribution_cut": _ev_distribution_cut,
    "gate_no_add": _ev_gate_no_add,
    "gate_reduce": _ev_gate_reduce,
    "gate_add": _ev_gate_add,
    "emotion_fuse": _ev_emotion_fuse,
    "no_chase_rally": _ev_no_chase_rally,
    "reversal_no_add": _ev_reversal_no_add,
    "reversal_no_panic": _ev_reversal_no_panic,
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
