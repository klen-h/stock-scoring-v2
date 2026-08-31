"""
================================================================================
【文件作用】模拟盘/纸面交易核心逻辑（paper_positions / paper_account）
================================================================================

设计目标（对齐 PLAN_PAPER_TRADING.md）：
  盘后扫描信号自动入池 → 次日 9:35 开盘按量价关系确认成交 → 盘中/盘后跟踪
  止损止盈 → 平仓后回填真实胜率，反哺白名单刷新。

撮合口径与回测引擎完全同源（app/backtest/engine.py）：
  - 成交价 = 开盘价（T+1 开盘成交）
  - 止损/止盈：盘中实时价触发；盘后兜底用日线 low/high（同日止损优先、跳空按 open）
  - 成本 = engine.DEFAULT_COSTS["stock"]（佣金+滑点+卖出印花税）

成交确认规则（对齐 recommendation.build_open_confirmation，企微开盘买点那一套）：
  - 开盘 ≤ 止损位        → 放弃（形态破坏）
  - 高开 >3%             → 放弃（成本抬高、盈亏比变差）
  - 低开 0~3%            → 低吸买点（放量承接更佳；缩量低开→观望放弃）
  - 平开/高开 ≤3%        → 正常买点（缩量高开→防诱多，降级半仓）
================================================================================
"""

import json
from datetime import datetime, timedelta

from app.database import db
from app.flash import rules
from app.backtest.engine import DEFAULT_COSTS, DEFAULT_POSITION_RATIO

ACCOUNT_ID = 1
INITIAL_CAPITAL = 100000.0   # 虚拟本金（元）
MAX_PENDING = 20             # 待确认池上限（按置信度截断）
MAX_PER_STRATEGY = 5         # 单战法最多入池数（避免一个战法垄断模拟盘，保证多样性）
MAX_HOLD_DAYS = 20           # 超期强平天数
POSITION_RATIO = DEFAULT_POSITION_RATIO   # 单仓占用虚拟本金比例（0.2）


def _now_iso() -> str:
    return datetime.now().isoformat()


def _bj_date() -> str:
    return rules.beijing_now().strftime("%Y-%m-%d")


def _costs() -> dict:
    return DEFAULT_COSTS["stock"]


# ================================================================
#  账户
# ================================================================

def get_account() -> dict:
    """虚拟账户总览：总本金 / 已实现盈亏 / 已用 / 可用。"""
    row = db.fetch_one("SELECT * FROM paper_account WHERE id = %s", (ACCOUNT_ID,))
    if not row:
        db.execute("INSERT INTO paper_account (id, initial_capital, realized_pnl, updated_at) "
                   "VALUES (%s, %s, 0, %s)", (ACCOUNT_ID, INITIAL_CAPITAL, _now_iso()))
        row = db.fetch_one("SELECT * FROM paper_account WHERE id = %s", (ACCOUNT_ID,))
    holdings = db.fetch("SELECT cost FROM paper_positions WHERE status='holding'")
    used = sum(float(h["cost"] or 0) for h in holdings or [])
    return {
        "initial_capital": INITIAL_CAPITAL,
        "realized_pnl": round(float(row["realized_pnl"] or 0), 2),
        "used_capital": round(used, 2),
        "available_capital": round(INITIAL_CAPITAL - used, 2),
    }


# ================================================================
#  Phase 1：信号入池（盘后扫描完成后调用）
# ================================================================

def auto_ingest_signals() -> dict:
    """把白名单战法当日高/中置信度信号写入模拟池（status=pending）。
    幂等：UNIQUE(strategy_name, code, signal_date) + 同 code 已有持仓则跳过。"""
    from app.strategies.recommendation import get_push_whitelist
    from app.strategies.market_regime import is_strategy_admitted
    today = _bj_date()
    whitelist = set(get_push_whitelist())
    stats = {"ingested": 0, "skipped_exist": 0, "skipped_low_conf": 0,
             "skipped_bad_stop": 0, "pool_full": False}
    for strategy_en in whitelist:
        admitted, reason, _, _ = is_strategy_admitted(strategy_en)
        if not admitted:
            continue
        row = db.fetch_one(
            "SELECT results_json FROM strategy_results WHERE strategy_name = %s AND scan_date = %s",
            (strategy_en, today))
        if not row:
            continue
        try:
            results = json.loads(row["results_json"]) or []
        except (json.JSONDecodeError, TypeError):
            continue
        cands = [s for s in results
                 if (s.get("confidence_level") or "low") in ("high", "medium")]
        cands.sort(key=lambda s: s.get("confidence") or 0, reverse=True)
        ingested_this = 0
        for s in cands:
            code = s.get("code")
            if not code:
                stats["skipped_low_conf"] += 1
                continue
            if stats["ingested"] >= MAX_PENDING:
                stats["pool_full"] = True
                break
            if ingested_this >= MAX_PER_STRATEGY:
                stats["skipped_exist"] += 1   # 该战法已达均衡上限，看下一个战法
                break
            entry = float(s.get("entry_price") or 0)
            stop = float(s.get("stop_loss") or 0)
            if entry > 0 and stop > 0 and stop >= entry:
                # 止损位不低于介入价 = 信号止损异常，跳过（否则跟踪阶段止损永不触发）
                stats["skipped_bad_stop"] += 1
                continue
            dup = db.fetch_one(
                "SELECT id FROM paper_positions WHERE strategy_name=%s AND code=%s AND signal_date=%s",
                (strategy_en, code, today))
            holding = db.fetch_one(
                "SELECT id FROM paper_positions WHERE code=%s AND status='holding'", (code,))
            if dup or holding:
                stats["skipped_exist"] += 1
                continue
            db.execute(
                "INSERT INTO paper_positions (code, name, strategy_name, signal_date, entry_price, "
                "stop_loss, target_price, status, confirmation_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)",
                (code, s.get("name") or code, strategy_en, today,
                 s.get("entry_price"), s.get("stop_loss"), s.get("target_price"),
                 json.dumps(s, ensure_ascii=False), _now_iso()))
            stats["ingested"] += 1
            ingested_this += 1
    print(f"[paper] 信号入池: {stats}")
    return stats


# ================================================================
#  Phase 2：开盘成交确认（9:35，量价关系 = build_open_confirmation 口径）
# ================================================================

def _confirm_fill(sig: dict, quote: dict, vol_ratio: float) -> tuple:
    """对齐 build_open_confirmation 的判定。返回 (action, note, fill_price)。
    action: cancel(放弃) / full(全仓) / half(半仓，防诱多) / watch(观望放弃)"""
    entry = float(sig.get("entry_price") or 0)
    stop = float(sig.get("stop_loss") or 0)
    open_p = float(quote.get("open") or 0)
    if entry <= 0 or open_p <= 0:
        return "cancel", "无有效参考价/开盘价", open_p
    dev = (open_p - entry) / entry * 100
    hot = vol_ratio is not None and vol_ratio >= 1.5
    cold = vol_ratio is not None and vol_ratio < 0.6
    note = f"今开 {open_p:.2f}（相对参考 {dev:+.1f}%）"
    if vol_ratio is not None:
        note += f"，量比 {vol_ratio:.1f}"
    if stop > 0 and open_p <= stop:
        return "cancel", note + "：开盘已破止损，形态破坏，放弃", open_p
    if dev > 3:
        return "cancel", note + "：高开>3%，成本抬高盈亏比变差，放弃", open_p
    if dev < 0:
        if cold:
            return "watch", note + "：缩量低开，无资金承接，观望放弃", open_p
        if hot:
            return "full", note + "：低开放量承接，可低吸买入", open_p
        return "full", note + "：低开不破位，比参考价更优，可买入", open_p
    # 平开/高开 ≤3%
    if cold:
        return "half", note + "：高开/平开缩量，防诱多，降级半仓", open_p
    return "full", note + "：开盘接近参考价，按计划买入", open_p


def _calc_vol_ratios(codes: list, quotes: dict) -> dict:
    """量比 = 实时成交量(手) / (昨日成交量 × 已交易分钟/240)。与 scheduler._calc_vol_ratios 同口径。"""
    from app.scoring.kline_cache import get_cached_klines_batch
    now = rules.beijing_now()
    elapsed = max(5, (now.hour * 60 + now.minute) - 570)   # 570 = 09:30
    cache = get_cached_klines_batch(list(set(codes)))
    ratios = {}
    for c in set(codes):
        ks = cache.get(c) or []
        if len(ks) < 2 or not ks[-1].get("volume"):
            continue
        q = quotes.get(c)
        if not q or not q.get("volume"):
            continue
        expected = float(ks[-1]["volume"]) * elapsed / 240
        if expected > 0:
            ratios[c] = round(float(q["volume"]) / expected, 2)
    return ratios


def fill_pending_positions() -> dict:
    """9:35 开盘确认：读 pending → 实时行情+量比 → 成交/放弃。幂等由调度器保证。"""
    from app.tencent import get_stocks_batch
    pending = db.fetch("SELECT * FROM paper_positions WHERE status='pending' ORDER BY created_at ASC")
    if not pending:
        return {"filled": 0, "cancelled": 0, "watched": 0}
    codes = [p["code"] for p in pending]
    quotes = {q.get("code"): q for q in get_stocks_batch(codes) if q.get("code")}
    ratios = _calc_vol_ratios(codes, quotes)
    filled = cancelled = watched = 0
    for p in pending:
        quote = quotes.get(p["code"]) or {}
        open_p = float(quote.get("open") or 0)
        if not quote or open_p <= 0:
            continue   # 行情缺失，保留 pending 窗口内重试
        sig = {}
        try:
            sig = json.loads(p["confirmation_json"] or "{}") or {}
        except (json.JSONDecodeError, TypeError):
            pass
        action, note, fill_p = _confirm_fill(sig, quote, ratios.get(p["code"]))
        if action in ("cancel", "watch"):
            db.execute(
                "UPDATE paper_positions SET status='cancelled', exit_reason='fill_rejected', "
                "fill_note=%s, closed_at=%s WHERE id=%s", (note, _now_iso(), p["id"]))
            cancelled += 1
            continue
        # 成交（full/half）
        half = action == "half"
        price = fill_p if fill_p > 0 else float(quote.get("price") or 0)
        if price <= 0:
            continue
        per = INITIAL_CAPITAL * POSITION_RATIO * (0.5 if half else 1.0)
        shares = int(per / price / 100) * 100
        if shares <= 0:
            continue
        cost = round(shares * price, 2)
        db.execute(
            "UPDATE paper_positions SET status='holding', fill_price=%s, fill_date=%s, "
            "shares=%s, cost=%s, fill_note=%s WHERE id=%s",
            (price, _bj_date(), shares, cost, note, p["id"]))
        filled += 1
    print(f"[paper] 开盘确认: filled={filled} cancelled={cancelled} watched={watched}")
    return {"filled": filled, "cancelled": cancelled, "watched": watched}


# ================================================================
#  Phase 3：持仓跟踪（止损/止盈/超期强平）
# ================================================================

def _hold_days(fill_date: str, exit_date: str = None) -> int:
    """持仓自然日（近似交易日）。"""
    try:
        f = datetime.strptime(fill_date, "%Y-%m-%d")
        e = datetime.strptime(exit_date or _bj_date(), "%Y-%m-%d")
        return max(0, (e - f).days)
    except (ValueError, TypeError):
        return 0


def _latest_close(code: str) -> float:
    row = db.fetch_one("SELECT close FROM backtest_prices WHERE code=%s ORDER BY date DESC LIMIT 1", (code,))
    return float(row["close"]) if row and row.get("close") else 0.0


def _close_position(pos: dict, exit_price: float, reason: str) -> None:
    """平仓并结算（含双边成本，与 engine.match_signals 同口径）。"""
    fill = float(pos["fill_price"] or 0)
    if fill <= 0 or exit_price <= 0:
        db.execute(
            "UPDATE paper_positions SET status='closed', exit_reason=%s, exit_date=%s, closed_at=%s "
            "WHERE id=%s", (reason, _bj_date(), _now_iso(), pos["id"]))
        return
    c = _costs()
    buy_cost = c["commission"] + c["slippage"]
    sell_cost = c["commission"] + c["slippage"] + c["stamp"]
    gross = exit_price / fill - 1
    pnl_pct = (gross - buy_cost - sell_cost) * 100        # 百分数（与 _strategy_stat 同口径）
    cost = float(pos["cost"] or 0)
    pnl_amount = cost * (pnl_pct / 100)
    db.execute(
        "UPDATE paper_positions SET status='closed', exit_price=%s, exit_date=%s, exit_reason=%s, "
        "pnl_pct=%s, pnl_amount=%s, is_win=%s, closed_at=%s WHERE id=%s",
        (exit_price, _bj_date(), reason, round(pnl_pct, 2), round(pnl_amount, 2),
         1 if pnl_pct > 0 else 0, _now_iso(), pos["id"]))
    db.execute("UPDATE paper_account SET realized_pnl = realized_pnl + %s, updated_at=%s WHERE id=%s",
               (round(pnl_amount, 2), _now_iso(), ACCOUNT_ID))


def track_positions(use_daily: bool = False) -> dict:
    """
    持仓跟踪：
      use_daily=False → 盘中实时价触发（price<=stop 止损 / price>=target 止盈）
      use_daily=True  → 盘后兜底：用 backtest_prices 日线 low/high 校验（对齐回测
                        口径：同日止损优先、跳空按 min(open,stop)）
    超期（MAX_HOLD_DAYS 日）未触发 → 按最新收盘价离场。
    """
    holdings = db.fetch("SELECT * FROM paper_positions WHERE status='holding'")
    if not holdings:
        return {"closed": 0}
    from app.tencent import get_stocks_batch
    if not use_daily:
        quotes = {q.get("code"): q for q in get_stocks_batch([h["code"] for h in holdings]) if q.get("code")}
    closed = 0
    today = _bj_date()
    for h in holdings:
        # ★ T+1：A股当日买入当日不可卖出，最早 T+1 才能平仓
        if (h.get("fill_date") or "") >= today:
            continue
        stop = float(h["stop_loss"] or 0)
        target = float(h["target_price"] or 0)
        reason, exit_p = None, 0.0
        if use_daily:
            # 盘后兜底：只用 fill_date 之后的日线（T+1 可卖日），排除买入当日
            from app.backtest.data import load_prices
            bars = [b for b in (load_prices(h["code"], start=h["fill_date"]) or [])
                    if b["date"] > h["fill_date"]]
            if bars:
                for b in bars:
                    low, high = float(b["low"] or 0), float(b["high"] or 0)
                    if stop > 0 and low <= stop:
                        reason, exit_p = "stop_loss", min(float(b["open"] or 0), stop)
                        break
                    if target > 0 and high >= target:
                        reason, exit_p = "take_profit", target
                        break
                if not reason:
                    reason, exit_p = "expire", float(bars[-1]["close"] or 0)
        else:
            q = quotes.get(h["code"]) or {}
            price = float(q.get("price") or 0)
            if price > 0 and stop > 0 and price <= stop:
                reason, exit_p = "stop_loss", price
            elif price > 0 and target > 0 and price >= target:
                reason, exit_p = "take_profit", price
            if not reason and _hold_days(h["fill_date"]) >= MAX_HOLD_DAYS:
                reason, exit_p = "expire", _latest_close(h["code"])
        if reason:
            _close_position(h, exit_p, reason)
            closed += 1
    print(f"[paper] 持仓跟踪{'（盘后兜底）' if use_daily else ''}: 平仓 {closed} 笔")
    return {"closed": closed}


# ================================================================
#  Phase 4：统计回填 + 白名单自动刷新
# ================================================================

def paper_stats() -> dict:
    """按战法分组已平仓统计（复用 backtest.strategies._strategy_stat 口径）。"""
    from app.backtest.strategies import _strategy_stat
    rows = db.fetch("SELECT * FROM paper_positions WHERE status='closed' ORDER BY closed_at ASC")
    groups = {}
    for r in rows:
        groups.setdefault(r["strategy_name"], []).append(r)
    out = {}
    for name, items in groups.items():
        trades = [{
            "pnl_pct": float(t["pnl_pct"] or 0),
            "hold_days": _hold_days(t.get("fill_date") or "", t.get("exit_date") or ""),
        } for t in items]
        out[name] = _strategy_stat(trades)
    return out


def auto_refresh_whitelist(min_trades: int = 30, win_threshold: float = 55.0) -> dict:
    """模拟盘样本充足的战法自动进入/移出推送白名单。
    默认不自动写（避免误伤人工名单）；由 API 或人工确认后调用。返回建议清单。"""
    stats = paper_stats()
    promoted, demoted = [], []
    from app.strategies.recommendation import PUSH_STRATEGY_WHITELIST
    current = set(PUSH_STRATEGY_WHITELIST)
    for name, st in stats.items():
        if st.get("trades", 0) < min_trades:
            continue
        if st["win_rate"] >= win_threshold and name not in current:
            promoted.append(name)
        elif st["win_rate"] < win_threshold - 10 and name in current:
            demoted.append(name)
    return {"promoted": promoted, "demoted": demoted,
            "current": sorted(current), "note": "需人工确认后写入 PUSH_STRATEGY_WHITELIST"}
