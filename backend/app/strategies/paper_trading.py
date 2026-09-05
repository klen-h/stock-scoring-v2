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

仓位规则（本金只用来控制"同时持仓笔数"，胜率统计不依赖本金）：
  - 虚拟本金 100 万；单仓 = 本金 / MAX_POSITIONS（默认 20 笔）→ 资金恰好够 20 笔同持
  - 单战法最多 MAX_PER_STRATEGY_POSITIONS 笔（≈40% 额度），保证多战法并行验证
  - 额度不足时放弃并记原因：no_capital（资金不足）/ strategy_limit（战法超限）

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
INITIAL_CAPITAL = 1_000_000.0   # 虚拟本金（元）
MAX_POSITIONS = 20              # 最大同时持仓笔数

# ★ 单仓比例 = 1/MAX_POSITIONS（而非固定 20%）：
#   本金恰好够 MAX_POSITIONS 笔 —— 既不超买，也不会因资金不足错失样本。
#   此前固定 20% → 5 笔即打满 10 万本金，第 6 笔起全被 no_capital 取消，
#   样本积累过慢（而胜率/盈亏比是按笔统计的，与本金大小无关）。
POSITION_RATIO = 1 / MAX_POSITIONS

MAX_PER_STRATEGY_POSITIONS = 8  # 单战法最多持仓笔数（≈40% 额度，保证多战法并行验证）
MAX_PER_STRATEGY = 5            # 单战法最多入池数（避免一个战法垄断待确认池）
MAX_PENDING = 20                # 待确认池上限（按置信度截断）
MAX_HOLD_DAYS = 20              # 超期强平天数


def exit_policy_v2() -> bool:
    """退出策略 v2 是否生效（与 backtest.strategies.exit_policy 同一开关）。"""
    from app.backtest.strategies import exit_policy
    return exit_policy() == "v2"


def _max_hold() -> int:
    """超期强平天数：v2 策略持有 3 个交易日（网格扫描定版），v1 用原 20 天。"""
    return 3 if exit_policy_v2() else MAX_HOLD_DAYS


def _limit_down_locked(bars: list, j: int) -> bool:
    """bars[j] 是否跌停一字（全天封死跌停，卖单无法成交）。
    判定：high==low 且收盘较前一日跌幅 ≥9.9%（主板口径，容忍 20cm 板漏判为
    保守——漏判只是多持有一天，误判会错过真实止损）。"""
    if j <= 0:
        return False
    b, prev = bars[j], bars[j - 1]
    high, low = float(b.get("high") or 0), float(b.get("low") or 0)
    close, prev_close = float(b.get("close") or 0), float(prev.get("close") or 0)
    if high <= 0 or low <= 0 or prev_close <= 0:
        return False
    return high == low and (close / prev_close - 1) <= -0.099

# ── 组合风控（PLAN_PAPER_RISK.md；阈值为专家经验初值，risk_events 攒数据后回调）──
MAX_SAME_INDUSTRY = 2           # G1 同行业持仓上限（行业映射缺失视为独立行业不限制）
DRAWDOWN_FREEZE_PCT = 5.0       # G2 净值自峰值回撤 ≥ 此值 → 冻结开新仓
CONSEC_LOSS_COOLDOWN = 3        # G3 连续止损笔数
COOLDOWN_DAYS = 1               # G3 冷却天数
DAILY_LOSS_LIMIT_PCT = 2.0      # G4 当日平仓亏损 ≥ 本金此比例 → 当日停开

# ── 仓位模型（PLAN_PAPER_RISK.md B 节：波动率/风险仓位，替代固定等分）──
RISK_PER_TRADE_PCT = 0.008      # 单笔风险额 = 本金 × 0.8%（对齐 tracker 1.5%，20 仓更保守）
MIN_POSITION_VALUE = 20_000     # 单仓市值钳位下限
MAX_POSITION_VALUE = 80_000     # 单仓市值钳位上限
ATR_PERIOD = 14                 # 止损缺失时的兜底波动度量


def _atr14(code: str) -> float:
    """14 日 ATR（股票日线 TR 均值）；失败返回 0。

    ★ 不能直接用 tencent.get_kline：它会把 000001/399001 等当指数（INDEX_MAP），
      而战法信号是股票（000001=平安银行）→ 会拉到上证指数 K 线、波动率差一个量级。
      这里手动构造带市场前缀的股票 K 线请求（qfqday）规避该判定。
    """
    try:
        import requests
        prefix = "sh" if code.startswith("6") else "sz"
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        r = requests.get(url, params={
            "param": f"{prefix}{code},day,2026-01-01,2026-12-31,40,qfq"}, timeout=15)
        raw = ((r.json() or {}).get("data") or {}).get(f"{prefix}{code}", {}).get("qfqday") or []
        if len(raw) < ATR_PERIOD + 1:
            return 0.0
        trs = []
        for i in range(1, len(raw)):
            # 腾讯 fqkline 每根：[date, open, close, high, low, volume, ...]
            h, l, pc = float(raw[i][3]), float(raw[i][4]), float(raw[i - 1][2])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD
    except Exception:
        return 0.0


def _calc_shares(code: str, price: float, stop: float, entry: float, half: bool) -> int:
    """
    风险仓位：股数 = 单笔风险额 / 每股风险，市值钳位 [MIN, MAX]。
      - 每股风险 = entry - stop（信号自带止损，天然可用）
      - stop 缺失/异常 → 每股风险 = 2 × ATR14 兜底
      - 无有效风险度量 → 返回 0（宁可放弃，不用拍脑袋仓位）
    半仓档 = 风险额减半（股数同减半，保留防诱多语义）。
    """
    risk_budget = INITIAL_CAPITAL * RISK_PER_TRADE_PCT * (0.5 if half else 1.0)
    per_share_risk = 0.0
    if entry > 0 and stop > 0 and stop < entry:
        per_share_risk = entry - stop
    else:
        atr = _atr14(code)
        if atr > 0:
            per_share_risk = 2 * atr
    if per_share_risk <= 0:
        return 0
    shares = int(risk_budget / per_share_risk / 100) * 100
    if shares <= 0:
        return 0
    # 市值钳位（先压上限，再保下限）
    if shares * price > MAX_POSITION_VALUE:
        shares = int(MAX_POSITION_VALUE / price / 100) * 100
    if shares * price < MIN_POSITION_VALUE:
        shares = max(shares, int(MIN_POSITION_VALUE / price / 100) * 100)
    return shares


def _migrate() -> None:
    """风控表结构幂等迁移（已有库加列/建表；新部署走 schema.sql）。"""
    for sql in (
        "ALTER TABLE paper_account ADD COLUMN IF NOT EXISTS peak_equity REAL",
        "ALTER TABLE paper_account ADD COLUMN IF NOT EXISTS cooldown_until TEXT",
        "ALTER TABLE paper_account ADD COLUMN IF NOT EXISTS risk_frozen INTEGER DEFAULT 0",
        """CREATE TABLE IF NOT EXISTS paper_risk_events (
            id SERIAL PRIMARY KEY,
            time TEXT NOT NULL,
            event_type TEXT NOT NULL,
            code TEXT,
            message TEXT
        )""",
    ):
        try:
            db.execute(sql)
        except Exception as e:
            print(f"[paper] 风控表迁移: {e}")


_migrate()


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
    """
    把**所有 regime 准入战法**当日高/中置信度信号写入模拟池（status=pending）。

    ★ 入池范围为什么不是"仅推送白名单"：
      白名单的准入条件是"样本≥30 且胜率≥55%"，而样本要靠模拟盘积累 ——
      若只入池白名单战法，其他战法永远攒不到样本、永远进不了白名单（鸡生蛋），
      计划里"自动发现新的达标战法"这个目标就落空了。因此：
        - 白名单战法     → 入池 + 企微推送（可作实盘参考）
        - 非白名单战法   → 同样入池（纯攒样本），但不推送（避免误导实盘）
      推送侧由 scan_all_strategies 的 `key in whitelist` 过滤，与本函数解耦。

    幂等：UNIQUE(strategy_name, code, signal_date) + 同 code 已有持仓则跳过。
    """
    from app.strategies import list_strategies
    from app.strategies.recommendation import get_push_whitelist
    from app.strategies.market_regime import is_strategy_admitted
    today = _bj_date()
    whitelist = set(get_push_whitelist())
    stats = {"ingested": 0, "skipped_exist": 0, "skipped_low_conf": 0,
             "skipped_bad_stop": 0, "skipped_mainforce_gate": 0,
             "pool_full": False, "non_whitelist": 0}
    gate_on = None
    for cfg in list_strategies():
        strategy_en = cfg["name_en"]        # 注册/查询用英文 key，name 只是显示名
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
        # 该战法"待确认 + 持仓"已达上限则不再入池：否则明天确认时会被
        # strategy_limit 判取消，白占其他战法的入池额度
        held = db.fetch_one(
            "SELECT COUNT(*) AS c FROM paper_positions WHERE strategy_name=%s "
            "AND status IN ('pending','holding')", (strategy_en,))
        if held and (held.get("c") or 0) >= MAX_PER_STRATEGY_POSITIONS:
            continue
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
            # ★ 主力过滤闸门（与推送同一口径；历史重放验证见 mainforce/gate.py）：
            #   高位（price_pos>0.75）或拉升段信号不入模拟池——形态相似但主力阶段不对
            if gate_on is None:
                from app.mainforce.gate import _mode_on as _gate_on
                gate_on = _gate_on()
            if gate_on:
                from app.mainforce.gate import strategy_gate
                g = strategy_gate(str(code), s.get("name") or "")
                if not g["ok"]:
                    stats["skipped_mainforce_gate"] += 1
                    print(f"[paper] {code} {s.get('name')} 主力过滤拦截: {g['reason']}")
                    continue
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
            # ★ 退出策略 v2（backtest.strategies.apply_exit_policy 同口径）：
            #   止损=介入价×-7%、不设目标价（让利润奔跑，到期/止损离场）。
            #   到期天数由 track 阶段的 _max_hold() 统一按策略取值。
            if exit_policy_v2():
                entry = float(s.get("entry_price") or 0)
                if entry > 0:
                    s = dict(s, stop_loss=round(entry * 0.93, 2), target_price=None)
            db.execute(
                "INSERT INTO paper_positions (code, name, strategy_name, signal_date, entry_price, "
                "stop_loss, target_price, status, confirmation_json, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending', %s, %s)",
                (code, s.get("name") or code, strategy_en, today,
                 s.get("entry_price"), s.get("stop_loss"), s.get("target_price"),
                 json.dumps(s, ensure_ascii=False), _now_iso()))
            stats["ingested"] += 1
            ingested_this += 1
            if strategy_en not in whitelist:
                stats["non_whitelist"] += 1
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
    # ★ 涨停一字买不进（与 backtest.engine.match_signals 同口径）：
    #   开盘即涨停且现价仍封死在涨停价 → 买单排队无法成交，模拟盘直接放弃
    try:
        from app.backtest.engine import _limit_pct, _round_tick
        pct = _limit_pct(str(sig.get("code") or ""), sig.get("name") or "", False)
        prev_close = float(quote.get("prev_close") or 0)
        price_now = float(quote.get("price") or 0)
        if pct > 0 and prev_close > 0:
            limit_up = _round_tick(prev_close * (1 + pct))
            if open_p >= limit_up - 0.001 and price_now >= limit_up - 0.001:
                return "cancel", f"涨停一字买不进（{limit_up:.2f} 封死，排队无法成交）", open_p
    except Exception:
        pass
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
    risk = _risk_state()          # G2/G3/G4 账户级状态，循环中不变，查一次
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
        # ★ G2 回撤熔断 / G3 连亏冷却 / G4 日亏损限额（账户级）
        if risk["frozen"]:
            _reject_position(p["id"], "risk_freeze",
                             note + f"｜组合回撤 {risk['drawdown']}% 熔断中，暂停开新仓")
            cancelled += 1
            continue
        if risk["cooldown"]:
            _reject_position(p["id"], "cooldown", note + "｜连亏冷却中，暂停开新仓")
            cancelled += 1
            continue
        if risk["daily_stop"]:
            _reject_position(p["id"], "daily_stop",
                             note + f"｜当日平仓亏损 {risk['daily_loss']:,.0f} 元达限额，暂停开新仓")
            cancelled += 1
            continue
        # ★ G1 行业集中度：同 main_industry 持仓达上限则放弃（映射缺失不限制）
        same, ind = _same_industry_count(p["code"])
        if ind and same >= MAX_SAME_INDUSTRY:
            _reject_position(p["id"], "industry_limit",
                             note + f"｜同行业({ind})持仓已达 {same} 只上限")
            cancelled += 1
            continue
        # ★ 单战法持仓上限：保证多战法并行验证，避免单一战法吃满全部额度
        cnt = db.fetch_one(
            "SELECT COUNT(*) AS c FROM paper_positions "
            "WHERE status='holding' AND strategy_name=%s", (p["strategy_name"],))
        if cnt and (cnt.get("c") or 0) >= MAX_PER_STRATEGY_POSITIONS:
            db.execute(
                "UPDATE paper_positions SET status='cancelled', exit_reason='strategy_limit', "
                "fill_note=%s, closed_at=%s WHERE id=%s",
                (note + f"｜该战法持仓已达上限 {MAX_PER_STRATEGY_POSITIONS} 笔，放弃",
                 _now_iso(), p["id"]))
            cancelled += 1
            continue
        # ★ 资金检查：可用资金不足最小仓则放弃（风险仓位金额在 [2万, 8万] 区间）
        avail = get_account()["available_capital"]
        if avail < MIN_POSITION_VALUE:
            db.execute(
                "UPDATE paper_positions SET status='cancelled', exit_reason='no_capital', "
                "fill_note=%s, closed_at=%s WHERE id=%s",
                (note + f"｜可用资金 {avail:,.0f} 元不足最小仓 {MIN_POSITION_VALUE:,.0f} 元，放弃",
                 _now_iso(), p["id"]))
            cancelled += 1
            continue
        # ★ 仓位模型（PLAN_PAPER_RISK.md B 节）：股数 = 风险额 / 每股风险
        #   （entry-stop，信号止损缺失用 2×ATR14 兜底；市值钳位 2万~8万）
        shares = _calc_shares(p["code"], price,
                              float(sig.get("stop_loss") or 0),
                              float(sig.get("entry_price") or 0), half)
        if shares <= 0:
            _reject_position(p["id"], "no_risk_metric",
                             note + "｜无有效止损/ATR，无法定仓，放弃")
            cancelled += 1
            continue
        if shares * price > avail:            # 可用资金不足目标仓位 → 缩至可用
            shares = int(avail / price / 100) * 100
            if shares <= 0 or shares * price < MIN_POSITION_VALUE * 0.5:
                _reject_position(p["id"], "no_capital",
                                 note + f"｜可用资金 {avail:,.0f} 元不足以维持最小仓，放弃")
                cancelled += 1
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
#  组合风控（PLAN_PAPER_RISK.md：G1-G4 gate + 净值/峰值维护 + 事件审计）
# ================================================================

def _log_risk_event(event_type: str, code: str, message: str) -> None:
    """风控事件落审计表 + 企微推送（重要通知，不受业务开关限制）。"""
    try:
        db.execute("INSERT INTO paper_risk_events (time, event_type, code, message) "
                   "VALUES (%s, %s, %s, %s)", (_now_iso(), event_type, code, message))
    except Exception as e:
        print(f"[paper] 风控事件落库失败: {e}")
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched("🛡 模拟盘风控", f"**{event_type}**\n{message}", force=True)
    except Exception as e:
        print(f"[paper] 风控事件推送失败: {e}")


def _current_equity() -> float:
    """账户净值 = 初始本金 + 已实现盈亏 + 持仓浮盈（按实时价，缺价按成交价）。"""
    acc = get_account()
    unrealized = 0.0
    holdings = db.fetch("SELECT code, fill_price, shares FROM paper_positions WHERE status='holding'")
    if holdings:
        from app.tencent import get_stocks_batch
        quotes = {q.get("code"): q for q in get_stocks_batch([h["code"] for h in holdings]) if q.get("code")}
        for h in holdings:
            q = quotes.get(h["code"]) or {}
            price = float(q.get("price") or 0) or float(h["fill_price"] or 0)
            unrealized += (price - float(h["fill_price"] or 0)) * int(h["shares"] or 0)
    return INITIAL_CAPITAL + float(acc["realized_pnl"]) + unrealized


def _update_peak_equity() -> float:
    """刷新净值峰值（track 与平仓时调用）；返回当前峰值。"""
    eq = _current_equity()
    row = db.fetch_one("SELECT peak_equity FROM paper_account WHERE id=%s", (ACCOUNT_ID,))
    peak = max(float((row or {}).get("peak_equity") or 0), eq, INITIAL_CAPITAL)
    db.execute("UPDATE paper_account SET peak_equity=%s WHERE id=%s", (peak, ACCOUNT_ID))
    return peak


def _risk_state() -> dict:
    """
    组合风控状态（fill 循环前调用一次）：
      - G2 回撤：净值自峰值回撤 ≥ 阈值 → 置 risk_frozen=1（钉住，人工解锁——
        保守面：避免 V 型反弹立即恢复开仓，先评估战法是否失效）
      - G3 冷却：cooldown_until > 今天 → 冷却中；到期自动清除
      - G4 日亏：当日平仓亏损合计 ≥ 本金限额 → 当日停开
    """
    row = db.fetch_one("SELECT * FROM paper_account WHERE id=%s", (ACCOUNT_ID,)) or {}
    cooldown = False
    cu = row.get("cooldown_until")
    if cu:
        if str(cu) > _bj_date():
            cooldown = True
        else:
            db.execute("UPDATE paper_account SET cooldown_until=NULL WHERE id=%s", (ACCOUNT_ID,))
    frozen = bool(row.get("risk_frozen"))
    drawdown = 0.0
    if not frozen:
        peak = float(row.get("peak_equity") or INITIAL_CAPITAL)
        eq = _current_equity()
        if peak > 0:
            drawdown = (peak - eq) / peak * 100
            if drawdown >= DRAWDOWN_FREEZE_PCT:
                frozen = True
                db.execute("UPDATE paper_account SET risk_frozen=1 WHERE id=%s", (ACCOUNT_ID,))
                _log_risk_event("risk_freeze", "",
                                f"组合净值 {eq:,.0f} 自峰值 {peak:,.0f} 回撤 {drawdown:.1f}% "
                                f"≥ {DRAWDOWN_FREEZE_PCT}%，冻结开新仓（需人工解锁：POST /api/paper/risk/unfreeze）")
    daily = db.fetch_one(
        "SELECT COALESCE(SUM(pnl_amount), 0) AS s FROM paper_positions "
        "WHERE status='closed' AND exit_date=%s AND pnl_amount < 0", (_bj_date(),))
    daily_loss = abs(float((daily or {}).get("s") or 0))
    daily_stop = daily_loss >= INITIAL_CAPITAL * DAILY_LOSS_LIMIT_PCT / 100
    return {"frozen": frozen, "cooldown": cooldown, "daily_stop": daily_stop,
            "drawdown": round(drawdown, 2), "daily_loss": round(daily_loss, 0)}


def unfreeze() -> dict:
    """人工解除回撤熔断（评估战法是否失效后再解锁）。"""
    db.execute("UPDATE paper_account SET risk_frozen=0, peak_equity=%s WHERE id=%s",
               (_current_equity(), ACCOUNT_ID))   # 峰值重置为当前净值，避免立刻再触发
    _log_risk_event("unfreeze", "", "人工解除回撤熔断，恢复开新仓（净值峰值已重置为当前净值）")
    return {"ok": True, "message": "已解除熔断"}


def _same_industry_count(code: str) -> tuple:
    """(同行业持仓数, 行业名)。行业映射缺失 → (0, "") 不限制（攒样本优先，plan 开放问题 1）。"""
    try:
        from app.sector_industry import get_stock_industry
        ind = (get_stock_industry(code) or {}).get("main_industry") or ""
    except Exception:
        return 0, ""
    if not ind:
        return 0, ""
    holdings = db.fetch("SELECT code FROM paper_positions WHERE status='holding'")
    same = 0
    for h in holdings or []:
        try:
            if (get_stock_industry(h["code"]) or {}).get("main_industry") == ind:
                same += 1
        except Exception:
            continue
    return same, ind


def _reject_position(pos_id: int, reason: str, note: str) -> None:
    db.execute("UPDATE paper_positions SET status='cancelled', exit_reason=%s, "
               "fill_note=%s, closed_at=%s WHERE id=%s", (reason, note, _now_iso(), pos_id))


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
    # ★ 组合风控挂钩：G3 连亏冷却检测（只统计止损，manual/expire/timeout_no_trigger 不算）+ 净值峰值维护
    recent = db.fetch("SELECT exit_reason FROM paper_positions WHERE status='closed' "
                      "ORDER BY closed_at DESC LIMIT %s", (CONSEC_LOSS_COOLDOWN,))
    if (len(recent or []) >= CONSEC_LOSS_COOLDOWN
            and all(r["exit_reason"] == "stop_loss" for r in recent)):
        until = (datetime.now() + timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
        db.execute("UPDATE paper_account SET cooldown_until=%s WHERE id=%s", (until, ACCOUNT_ID))
        _log_risk_event("cooldown_start", pos["code"],
                        f"连续 {CONSEC_LOSS_COOLDOWN} 笔止损，冷却至 {until} 不开新仓")
    _update_peak_equity()


def track_positions(use_daily: bool = False) -> dict:
    """
    持仓跟踪：
      use_daily=False → 盘中实时价触发（price<=stop 止损 / price>=target 止盈）
      use_daily=True  → 盘后兜底：用 backtest_prices 日线 low/high 校验（对齐回测
                        口径：同日止损优先、跳空按 min(open,stop)）
    exit_reason 语义：
      - stop_loss / take_profit：止损/止盈触发
      - expire：持仓 ≥ MAX_HOLD_DAYS 日仍未触发（真·超期强平）
      - timeout_no_trigger：止损止盈均未触发（盘后兜底离场）
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
            # ★ 跌停一字卖不出（与 engine.match_signals 同口径）：封死跌停的交易日
            #   卖单排队无法成交 → 跳过该日，顺延到下一个可卖日
            from app.backtest.data import load_prices
            bars = [b for b in (load_prices(h["code"], start=h["fill_date"]) or [])
                    if b["date"] > h["fill_date"]]
            if bars:
                for j, b in enumerate(bars):
                    low, high = float(b["low"] or 0), float(b["high"] or 0)
                    if stop > 0 and low <= stop:
                        if _limit_down_locked(bars, j):
                            continue          # 封死跌停卖不出，看下一日
                        reason, exit_p = "stop_loss", min(float(b["open"] or 0), stop)
                        break
                    if target > 0 and high >= target:
                        reason, exit_p = "take_profit", target
                        break
                if not reason:
                    # 语义区分：满超期天数真超期 → expire；否则止损止盈均未触发 → timeout_no_trigger
                    if _hold_days(h["fill_date"], today) >= _max_hold():
                        reason, exit_p = "expire", float(bars[-1]["close"] or 0)
                    else:
                        reason, exit_p = "timeout_no_trigger", float(bars[-1]["close"] or 0)
        else:
            q = quotes.get(h["code"]) or {}
            price = float(q.get("price") or 0)
            # 盘中：跌停封死（高=低且价格触及止损）→ 卖单无法成交，顺延下次跟踪
            locked = (q.get("high") and q.get("low")
                      and float(q["high"]) == float(q["low"]) and float(q["low"]) > 0)
            if locked and price > 0 and stop > 0 and price <= stop:
                print(f"[paper] {h['code']} 跌停封死（{price:.2f}），卖出顺延")
            elif price > 0 and stop > 0 and price <= stop:
                reason, exit_p = "stop_loss", price
            elif price > 0 and target > 0 and price >= target:
                reason, exit_p = "take_profit", price
            if not reason and _hold_days(h["fill_date"]) >= _max_hold():
                reason, exit_p = "expire", _latest_close(h["code"])
        if reason:
            _close_position(h, exit_p, reason)
            closed += 1
    _update_peak_equity()   # G2 基准：每次跟踪后刷新净值峰值
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
