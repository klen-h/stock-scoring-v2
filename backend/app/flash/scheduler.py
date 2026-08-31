"""
================================================================================
【文件作用】内置定时调度器（FastAPI lifespan 启动的 asyncio 后台任务）
================================================================================

三个循环（时间均为北京时间）：
  快讯轮询  flash_loop   每 10 分钟全天（无新事件近零成本）
  信号跟踪  track_loop   每 15 分钟，仅 A 股交易时段实际执行
  三段复盘  review_loop  每分钟检查时间窗（09:10/11:32/15:03），每日各一次

时间窗 + "今日已跑"标记（schedule_state.json）共同保证幂等：
错过窗口（如服务重启）会在窗口后补跑一次，同一天不会重复跑。
所有同步网络/LLM 工作通过 asyncio.to_thread 放线程池，不阻塞事件循环。
================================================================================
"""

import asyncio
import os

from app.flash import service, store, rules, calendar
from app.flash.source import FLASH_COOKIE
from app.flash.llm import llm_configured
from app.flash.wechat import WECHAT_WEBHOOK

# 调度参数（可用环境变量覆盖；轮询便宜——LLM 只在新事件簇时才触发，收紧间隔不加 token 成本）
#   FLASH_POLL_INTERVAL_SEC  快讯轮询间隔，默认 300（5分钟）。再小要留意金十限流/Cookie 风险。
#   TRACK_INTERVAL_SEC       信号跟踪间隔，默认 300（5分钟），仅盘中执行。
FLASH_POLL_INTERVAL = int(os.environ.get("FLASH_POLL_INTERVAL_SEC", "300"))
TRACK_INTERVAL = int(os.environ.get("TRACK_INTERVAL_SEC", "300"))
REVIEW_WINDOWS = {               # 复盘窗口：任务名 → (开始分钟, 结束分钟)（北京时间）
    # 窗口刻意加宽以支持"错过补跑"：本机部署的电脑不是 24 小时开机，
    # 只要当天窗口内开机（结合"当日已跑"标记），错过的复盘会自动补上。
    "premarket":  (550, 690),    # 09:10-11:30（上午任意时刻开机都能补盘前）
    "lunchbreak": (692, 780),    # 11:32-13:00（午休任意时刻开机都能补午盘）
    "postmarket": (903, 1439),   # 15:03-23:59（晚上开机也能补盘后）
}

# 运行状态（/api/flash/status 读取）
status = {
    "running": False,
    "started_at": None,
    "last_flash_poll": None,
    "last_track": None,
    "last_reviews": {},          # {phase: 时间}
    "last_backtest_backfill": None,
    "last_backtest_preheat": None,
    "last_score_snapshot": None,
    "last_market_snapshot": None,
    "last_open_confirm": None,
    "last_news_alert": None,
    "last_news_history": None,
    "config": {
        "flash_interval_sec": FLASH_POLL_INTERVAL,
        "track_interval_sec": TRACK_INTERVAL,
        "flash_cookie_configured": bool(FLASH_COOKIE),
        "llm_configured": llm_configured(),
        "wechat_configured": bool(WECHAT_WEBHOOK),
    },
}


# 任务失败企微提醒：当天每任务最多一次（防刷屏）
_failure_notified = set()


def _notify_failure(task: str, err: str):
    """核心定时任务失败 → 企微推送。当天同一任务只提醒一次。"""
    today = rules.beijing_now().strftime("%Y-%m-%d")
    key = f"{task}|{today}"
    if key in _failure_notified:
        return
    _failure_notified.add(key)
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched(
            f"⚠️ {task}失败",
            f"> **任务：** {task}\n> **时间：** {rules.beijing_now().strftime('%H:%M')}\n"
            f"> **错误：** {err}\n\n请查看后端日志排查（数据源可能临时不可用，次日窗口内自动重试）。",
            force=True)
    except Exception as e:
        print(f"[scheduler] 企微通知失败: {e}")


async def _run_sync(fn, *args):
    """把同步阻塞工作丢线程池执行，异常吞掉（循环不能死）。"""
    try:
        return await asyncio.to_thread(fn, *args)
    except Exception as e:
        print(f"[scheduler] 任务异常 {fn.__name__}: {e}")
        return None


async def flash_loop():
    """快讯轮询循环。"""
    while True:
        result = await _run_sync(service.poll_flash_once)
        if result:
            status["last_flash_poll"] = result
        await asyncio.sleep(FLASH_POLL_INTERVAL)


async def track_loop():
    """信号跟踪循环（仅 A 股交易时段执行实际工作）。"""
    while True:
        market = rules.get_china_market_status()
        if market["is_open"]:
            result = await _run_sync(service.track_signals_once)
            if result:
                status["last_track"] = result
        await asyncio.sleep(TRACK_INTERVAL)


async def review_loop():
    """三段复盘循环：交易日 + 到窗口 + 当日未跑 → 执行并标记。"""
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        # 非交易日不跑：REVIEW_WINDOWS 只按"一天中的时刻"判断，周末/节假日开机
        # 同样会命中 postmarket 的 15:03-23:59，白烧 LLM token 还会往复盘历史
        # 里写入无意义的记录（此前周六照跑三段复盘就是这个原因）
        if not rules.is_trading_day(now):
            await asyncio.sleep(60)
            continue
        for phase, (start, end) in REVIEW_WINDOWS.items():
            task_key = f"review_{phase}"
            if start <= t < end and not store.is_schedule_done(task_key):
                print(f"[scheduler] 触发复盘: {phase}")
                result = await _run_sync(service.run_review, phase)
                # 只有成功（无 error）才标记完成；失败则允许窗口内重试
                if result and not result.get("error"):
                    store.mark_schedule_done(task_key)
                    status["last_reviews"][phase] = result.get("time")
                    print(f"[scheduler] 复盘 {phase} 完成")
                else:
                    err = result.get("error") if result else "任务异常（返回 None）"
                    print(f"[scheduler] 复盘 {phase} 失败，未标记完成（允许重试）: {err}")
                    # 推送失败提醒到企微（force=True：任务失败告警不受业务推送开关限制，
                    # 与 _notify_failure 语义一致；此前缺 force 导致复盘失败永远推不出去）
                    try:
                        from app.flash import wechat
                        wechat.push_markdown_batched(
                            f"⚠️ {phase} 复盘失败",
                            f"复盘阶段 **{phase}** 执行失败，将在窗口内重试。\n\n错误：{err}",
                            force=True)
                    except Exception as e:
                        print(f"[scheduler] 推送失败提醒异常: {e}")
        await asyncio.sleep(60)


HEALTH_PROBE_INTERVAL = 300     # 健康探针：空闲时段也定期采样各数据源
STOCK_CACHE_INTERVAL = 120       # 行情缓存刷新：盘中每 2 分钟刷新全量股票行情（供评分排行使用）


async def stock_cache_refresh_loop():
    """
    盘中行情缓存自动刷新（供评分排行 /score/batch/top 使用）。
    仅 A 股交易时段执行：refresh_all_stocks() 内部有 60 秒冷却，
    这里每 3 分钟触发一次，保证评分排行始终用近实时数据。
    非交易时段不执行（休市数据无意义，且浪费接口配额）。
    """
    while True:
        market = rules.get_china_market_status()
        if market["is_open"]:
            try:
                from app.tencent import refresh_all_stocks
                await asyncio.to_thread(refresh_all_stocks)
            except Exception as e:
                print(f"[scheduler] 行情缓存刷新失败: {e}")
        await asyncio.sleep(STOCK_CACHE_INTERVAL)


async def health_loop():
    """
    健康探针循环：定期调用各数据源（内部自带埋点），保证健康状态持续刷新——
    没有它，安静的交易日里宏观/东财源只在 LLM 触发时才被采样，健康状态会失真。
    顺带预热缓存。每个源一次轻量请求，成本可忽略。
    """
    def _probe():
        try:
            from app.macro import get_macro_panel
            get_macro_panel()
        except Exception:
            pass
        try:
            from app.eastmoney import get_sector_flow
            get_sector_flow("industry", limit=5)
        except Exception:
            pass
        try:
            from app.signals import tracker
            tracker.get_etf_quotes()
        except Exception:
            pass
    while True:
        await _run_sync(_probe)
        await asyncio.sleep(HEALTH_PROBE_INTERVAL)


# 宏观每日快照：工作日早盘锁定（08:55-13:00 窗口生成一次，跨重启幂等）
# 窗口放宽到午休前：窄窗口（08:55-10:00）曾导致电脑未开机就整天错过，宏观回测无数据
MACRO_DAILY_WINDOW = (535, 780)   # 北京时间分钟：08:55-13:00

async def macro_daily_loop():
    """
    宏观快照每日锁定：工作日早盘前生成一次并落盘（macro_daily），
    当日方向分从此固定不漂移；错过窗口（>10:00）当天不再补。
    """
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "macro_daily"
        if (now.weekday() < 5 and MACRO_DAILY_WINDOW[0] <= t < MACRO_DAILY_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发宏观每日快照锁定")
            try:
                from app.macro import get_macro_snapshot
                snap = await asyncio.to_thread(get_macro_snapshot)
                store.save_macro_daily(snap)
                store.mark_schedule_done(task_key)
                d = snap.get("direction", {})
                print(f"[scheduler] 宏观每日快照已锁定: {d.get('level')} {d.get('score')} "
                      f"(数据截至 {snap.get('data_time')})")
            except Exception as e:
                print(f"[scheduler] 宏观每日快照生成失败: {e}")
                _notify_failure("宏观每日快照", str(e))
        await asyncio.sleep(60)


# ── K线数据库缓存每日刷新 ──
KLINE_CACHE_REFRESHED_TODAY = False

async def kline_cache_refresh_loop():
    """
    每日盘后 15:30 自动刷新 K 线数据库缓存。
    刷新完成后当天不再重复。
    """
    global KLINE_CACHE_REFRESHED_TODAY

    while True:
        now = rules.beijing_now()
        h, m = now.hour, now.minute
        today = now.strftime("%Y-%m-%d")
        weekday = now.weekday()  # 0=周一, 6=周日
        
        # 工作日 15:30-23:59 触发刷新
        if weekday < 5 and h >= 15 and m >= 30 and not KLINE_CACHE_REFRESHED_TODAY:
            try:
                print(f"[scheduler] 开始每日 K 线缓存刷新...")
                from app.scoring.kline_cache import refresh_kline_cache
                result = await asyncio.to_thread(refresh_kline_cache)
                print(f"[scheduler] K 线缓存刷新完成: {result}")
                KLINE_CACHE_REFRESHED_TODAY = True
            except Exception as e:
                print(f"[scheduler] K 线缓存刷新失败: {e}")
        
        # 跨天重置标记
        if h == 0 and m < 5:
            KLINE_CACHE_REFRESHED_TODAY = False
        
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 指标数据库缓存每日刷新 ──
INDICATOR_CACHE_REFRESHED_TODAY = False

async def indicator_cache_refresh_loop():
    """
    每日盘后 16:00 自动刷新指标数据库缓存（在 K 线缓存刷新之后）。
    从 K 线缓存计算技术指标（MA/MACD/RSI/KDJ/BOLL），存入数据库。
    刷新完成后当天不再重复。
    """
    global INDICATOR_CACHE_REFRESHED_TODAY

    while True:
        now = rules.beijing_now()
        h, m = now.hour, now.minute
        weekday = now.weekday()  # 0=周一, 6=周日
        
        # 工作日 16:00-23:59 触发刷新（确保 K 线缓存已刷新）
        if weekday < 5 and h >= 16 and m >= 0 and not INDICATOR_CACHE_REFRESHED_TODAY:
            try:
                print(f"[scheduler] 开始每日指标缓存刷新...")
                from app.scoring.indicator_cache import refresh_indicator_cache
                result = await asyncio.to_thread(refresh_indicator_cache)
                print(f"[scheduler] 指标缓存刷新完成: {result}")
                INDICATOR_CACHE_REFRESHED_TODAY = True
            except Exception as e:
                print(f"[scheduler] 指标缓存刷新失败: {e}")
        
        # 跨天重置标记
        if h == 0 and m < 5:
            INDICATOR_CACHE_REFRESHED_TODAY = False
        
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 回测价格库每日增量回填 ──
BACKTEST_BACKFILL_WINDOW = (940, 1440)   # 北京时间 15:40-23:59（收盘后数据稳定）

async def backtest_prices_refresh_loop():
    """
    每日盘后增量回填 backtest_prices（ETF 池 + 沪深300 + 战法新个股）。
    工作日 15:40 后触发一次，用 schedule_state 幂等标记防重复（跨重启安全）；
    错过窗口晚上开机也能补。新交易日价格入库后，战法回测才能完成
    T+1 撮合与持有期平仓。
    """
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "backtest_backfill"
        if (now.weekday() < 5 and BACKTEST_BACKFILL_WINDOW[0] <= t < BACKTEST_BACKFILL_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发回测价格库增量回填")
            try:
                from backfill_history import backfill_daily
                stats = await asyncio.to_thread(backfill_daily)
                store.mark_schedule_done(task_key)
                status["last_backtest_backfill"] = rules.beijing_now().isoformat()
                print(f"[scheduler] 回测价格回填完成: {stats}")
                # 行情落后于沪深300 → 部分回填失败（backfill 内部失败不抛异常，
                # 但会静默导致行情停更、宏观/战法回测饿死），主动告警便于排查数据源
                missing = stats.get("stock_missing") or []
                etf_missing = stats.get("etf_missing") or []
                if etf_missing:
                    _notify_failure(
                        "回测价格回填",
                        f"{len(etf_missing)} 只ETF行情缺失/滞后（宏观回测数据底座）：\n"
                        f"> " + "、".join(etf_missing[:8]) + (f" 等共 {len(etf_missing)} 只" if len(etf_missing) > 8 else ""))
                if missing:
                    _notify_failure(
                        "回测价格回填",
                        f"{len(missing)} 只战法个股行情缺失/滞后（正常应同步到最新交易日）：\n"
                        f"> " + "、".join(missing[:8]) + (f" 等共 {len(missing)} 只" if len(missing) > 8 else ""))
            except Exception as e:
                print(f"[scheduler] 回测价格回填失败: {e}")
                _notify_failure("回测价格回填", str(e))
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 战法每日自动扫描 ──
STRATEGY_SCAN_WINDOW = (940, 1440)   # 北京时间 15:40-23:59（与回测回填同窗口，收盘后数据稳定）

# 战法买入信号推送：每个白名单战法最多推送条数 + 全天总量上限（防刷屏，多战法时仍可控）
PUSH_MAX_PER_STRATEGY = 8
PUSH_MAX_TOTAL = 10


def _latest_trading_day() -> str:
    """最近交易日（YYYY-MM-DD）：当日 15:00 前算上一交易日，跳过周末/节假日。"""
    from datetime import timedelta
    now = rules.beijing_now()
    d = now.date()
    if now.hour < 15:
        d -= timedelta(days=1)
    hol = rules.HOLIDAYS.get(d.year) or []
    while d.weekday() >= 5 or any(s <= (d.month, d.day) <= e for s, e, *_ in hol):
        d -= timedelta(days=1)
    return d.isoformat()


def scan_all_strategies() -> dict:
    """盘后扫描全部注册战法并保存结果（同步函数，由调度器丢线程池执行）。

    复用 router._do_scan（股票池过滤 + 共振验证 + 持久度更新），与手动
    /api/strategies/{name}/scan 完全同源。此前战法信号仅靠手动触发、
    strategy_results 只有一次性迁移的 5 天数据；战法回测（backtest_warfare）
    依赖持续信号样本，本任务让其可持续积累。"""
    from app.tencent import _cache, refresh_all_stocks
    if not _cache.get("stocks"):
        print("[scheduler] 行情缓存为空，先刷新全量行情")
        refresh_all_stocks()
    from app.strategies import list_strategies, get_strategy, save_scan_result
    from app.strategies.router import _do_scan
    from app.strategies.market_regime import is_strategy_admitted
    from app.strategies.recommendation import get_push_whitelist
    stats = {"scanned": 0, "signals": 0, "failed": 0, "skipped": 0}

    # ★ 当日K线就绪校验：腾讯 K 线在收盘后延迟更新，过早扫描会拿到上一交易日数据
    #   （实测：15:40 扫描 → 16:45 推送的信号全是上周五收盘价，当日已收盘却未更新）
    #   → 未就绪则本轮跳过且不 mark_done，调度器窗口内每 5 分钟重试至数据就绪。
    from app.tencent import get_kline
    expect_day = _latest_trading_day()
    probe = get_kline("000001", period="day", count=5)
    if probe and (probe[-1].get("date") or "") < expect_day:
        print(f"[strategies] 当日K线未就绪（数据源最新 {probe[-1].get('date')} "
              f"< 交易日 {expect_day}），本轮跳过，等待重试")
        stats["not_ready"] = True
        stats["data_date"] = probe[-1].get("date")
        stats["expect_date"] = expect_day
        return stats

    push_pool = {}  # 白名单战法 → 当天扫描信号（用于企微推送买入提醒）
    whitelist = set(get_push_whitelist())
    for cfg in list_strategies():
        # 注册/查询用 name_en（英文 key），name 只是前端显示名
        key = cfg["name_en"]
        strategy = get_strategy(key)
        if not strategy:
            stats["failed"] += 1
            continue
        # ★ P3 战法准入：未准入战法不进调度器（不调 _do_scan、不写空结果，单独计 skipped）
        admitted, admit_reason, _, _ = is_strategy_admitted(key)
        if not admitted:
            stats["skipped"] += 1
            print(f"[scheduler] 战法 {key} 未准入（{admit_reason}），跳过扫描")
            continue
        try:
            results = _do_scan(strategy, 20e8, 1000e4)
            save_scan_result(key, results)
            stats["scanned"] += 1
            stats["signals"] += len(results)
            print(f"[scheduler] 战法 {key} 扫描完成: {len(results)} 只")
            if key in whitelist and results:
                push_pool[key] = results
        except Exception as e:
            stats["failed"] += 1
            print(f"[scheduler] 战法 {key} 扫描失败: {e}")
    _push_buy_signals(push_pool)
    return stats


def _push_buy_signals(push_pool: dict) -> None:
    """推送白名单战法当天的新买入信号到企微（置信度过滤 + 限量，避免刷屏）。"""
    if not push_pool:
        return
    try:
        from app.strategies.market_regime import detect_market_regime
        from app.strategies.recommendation import format_signal_message
        from app.flash.wechat import push_strategy_signals
        market = detect_market_regime()
        messages = []
        seen_codes = set()
        for strategy_en, results in push_pool.items():
            # 只推 高/中 置信度信号，按置信度降序限量
            cands = [s for s in results
                     if (s.get("confidence_level") or "low") in ("high", "medium")]
            cands.sort(key=lambda s: s.get("confidence") or 0, reverse=True)
            for s in cands[:PUSH_MAX_PER_STRATEGY]:
                if len(messages) >= PUSH_MAX_TOTAL:  # 全天总量上限（防多战法刷屏）
                    break
                code = s.get("code")
                if code in seen_codes:  # 同一股票多战法共振时只推一条，避免刷屏
                    continue
                seen_codes.add(code)
                messages.append(format_signal_message(strategy_en, s, market))
            if len(messages) >= PUSH_MAX_TOTAL:
                break
        push_strategy_signals(messages)
        if messages:
            print(f"[scheduler] 战法买入信号推送: {len(messages)} 条")
    except Exception as e:
        print(f"[scheduler] 战法买入信号推送失败: {e}")


# ── 次日开盘买点确认（白名单战法昨日信号的执行指引）──
OPEN_CONFIRM_WINDOW = (575, 680)   # 北京时间 09:35-11:20（开盘价稳定后；窗口加宽支持补跑）


def run_open_confirmation() -> dict:
    """执行开盘买点确认：读白名单战法最近一次扫描信号 → 拉实时开盘价 → 生成买点指引 → 推送。"""
    try:
        from app.strategies.recommendation import get_push_whitelist, build_open_confirmation
        from app.flash.wechat import push_strategy_signals
        from app.tencent import get_stocks_batch
        from app.database import db
        import json
    except Exception as e:
        print(f"[scheduler] 开盘买点确认导入失败: {e}")
        return {"sent": 0, "no_signals": False}

    signals = []
    for strategy_en in get_push_whitelist():
        row = db.fetch_one(
            "SELECT scan_date, results_json FROM strategy_results "
            "WHERE strategy_name=%s AND count>0 ORDER BY scan_date DESC LIMIT 1",
            (strategy_en,))
        if not row:
            continue
        try:
            items = json.loads(row["results_json"])
        except (json.JSONDecodeError, KeyError):
            continue
        for s in items:
            s["_strategy"] = strategy_en
            s["_scan_date"] = row["scan_date"]
            signals.append(s)
    if not signals:
        print("[scheduler] 开盘买点确认：白名单战法无历史信号")
        return {"sent": 0, "no_signals": True}

    quotes = {q.get("code"): q for q in get_stocks_batch([s["code"] for s in signals])}
    # ★ 量价验证：量比 = 实时量 / (昨日量 × 已交易分钟/240)，防诱多
    vol_ratios = _calc_vol_ratios([s["code"] for s in signals], quotes)
    messages = []
    for s in signals:
        q = quotes.get(s["code"])
        if not q:
            continue
        msg = build_open_confirmation(s, q, vol_ratios.get(s["code"]))
        if msg:
            messages.append(msg)
    push_strategy_signals(messages)
    print(f"[scheduler] 开盘买点确认推送: {len(messages)} 条")
    return {"sent": len(messages), "no_signals": False}


def _calc_vol_ratios(codes: list, quotes: dict) -> dict:
    """量比 = 实时成交量(手) / (昨日成交量 × 已交易分钟/240)。
    昨日量从数据库 kline_cache 读（全量缓存最后根=最近交易日收盘量）。
    数据缺失/过期的股票返回空（跳过量能维度，只按价格判定）。
    9:30 开盘起算已交易分钟；早于 9:30 时按 5 分钟兜底。"""
    from app.scoring.kline_cache import get_cached_klines_batch
    now = rules.beijing_now()
    elapsed = max(5, (now.hour * 60 + now.minute) - 570)  # 570 = 09:30
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


async def open_confirmation_loop():
    """工作日开盘后，对白名单战法最近一次扫描信号做「买点确认」：
    实时开盘价 vs 参考介入价 vs 止损位 → 低吸/正常/回踩/放弃指引，推送企微。
    幂等：当天只推一次（schedule_state 按日期标记）。"""
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        day_key = now.strftime("%Y%m%d")
        if (now.weekday() < 5 and OPEN_CONFIRM_WINDOW[0] <= t < OPEN_CONFIRM_WINDOW[1]
                and not store.is_schedule_done("open_confirm", date_str=day_key)):
            print("[scheduler] 触发开盘买点确认")
            try:
                r = await asyncio.to_thread(run_open_confirmation)
                # 无信号也 mark_done（当天不再重试）；有信号推送成功即完成
                if r.get("sent") > 0 or r.get("no_signals"):
                    store.mark_schedule_done("open_confirm", date_str=day_key)
                    status["last_open_confirm"] = rules.beijing_now().isoformat()
                else:
                    print("[scheduler] 开盘买点确认无有效消息，稍后重试")
            except Exception as e:
                print(f"[scheduler] 开盘买点确认失败: {e}")
                _notify_failure("开盘买点确认", str(e))
        await asyncio.sleep(60)


async def strategy_scan_loop():
    """工作日盘后自动扫描全部战法并落库 strategy_results（幂等，错过窗口当日可补跑）。
    部分战法失败不阻塞整体（不 mark_done 会导致窗口内全部重扫，浪费资源）——
    只要成功 ≥1 个即视为当日完成；全部失败才告警。"""
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "strategy_scan"
        if (now.weekday() < 5 and STRATEGY_SCAN_WINDOW[0] <= t < STRATEGY_SCAN_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发战法全量扫描")
            try:
                stats = await asyncio.to_thread(scan_all_strategies)
                if stats.get("not_ready"):
                    print(f"[scheduler] 当日K线未就绪（{stats.get('data_date')} < "
                          f"{stats.get('expect_date')}），5 分钟后重试")
                elif stats["scanned"] > 0 or stats["failed"] == 0:
                    store.mark_schedule_done(task_key)
                    status["last_strategy_scan"] = rules.beijing_now().isoformat()
                    print(f"[scheduler] 战法扫描完成: {stats}")
                    if stats["failed"]:
                        _notify_failure("战法扫描",
                                        f"{stats['failed']} 个战法扫描失败（成功 {stats['scanned']} 个）")
                else:
                    print(f"[scheduler] 战法扫描全部失败: {stats}，稍后重试")
                    _notify_failure("战法扫描", f"全部 {stats['failed']} 个战法扫描失败")
            except Exception as e:
                print(f"[scheduler] 战法扫描失败: {e}")
                _notify_failure("战法扫描", str(e))
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 市场状态每日判定（生产评分动态权重数据源）──
REGIME_CACHE_WINDOW = (940, 1440)   # 北京时间 15:40-23:59（沪深300收盘后，与回测回填同窗口）


async def regime_cache_loop():
    """工作日盘后判定市场状态并缓存/落库，评分接口据此动态切换三维权重。
    依赖 backtest_prices 中当日沪深300数据（回填任务先写入）；数据未就绪则
    不 mark_done，窗口内每 5 分钟重试（回填完成后即成功）。"""
    from app.backtest.market_regime import refresh_regime_cache
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        if (now.weekday() < 5 and REGIME_CACHE_WINDOW[0] <= t < REGIME_CACHE_WINDOW[1]
                and not store.is_schedule_done("regime_cache")):
            try:
                cache = await asyncio.to_thread(refresh_regime_cache)
                if cache and cache.get("state"):
                    store.mark_schedule_done("regime_cache")
                    status["last_regime"] = f"{cache['date']} {cache['state']}"
                    print(f"[scheduler] 市场状态缓存完成: {cache['date']} {cache['state']} "
                          f"权重={cache['weights']}")
                else:
                    print("[scheduler] 沪深300当日数据未就绪，等待回填后重试")
            except Exception as e:
                print(f"[scheduler] 市场状态缓存失败: {e}")
                _notify_failure("市场状态缓存", str(e))
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 回测预热：盘后自动计算三类策略并写持久缓存，用户访问秒回（冷启动不再 30s+）──
BACKTEST_PREHEAT_WINDOW = (1605, 2359)   # 北京时间 16:05-23:59

async def backtest_preheat_loop():
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        day_key = now.strftime("%Y%m%d")
        if (now.weekday() < 5 and BACKTEST_PREHEAT_WINDOW[0] <= t < BACKTEST_PREHEAT_WINDOW[1]
                and not store.is_schedule_done("backtest_preheat", date_str=day_key)):
            print("[scheduler] 触发回测预热（signals/warfare/macro 写持久缓存）")
            try:
                from app.routers.backtest import preheat_all
                await asyncio.to_thread(preheat_all)
                store.mark_schedule_done("backtest_preheat", date_str=day_key)
                status["last_backtest_preheat"] = rules.beijing_now().isoformat()
                print("[scheduler] 回测预热完成")
            except Exception as e:
                print(f"[scheduler] 回测预热失败: {e}")
        await asyncio.sleep(600)  # 每 10 分钟检查一次


# ── 回测报告周度自动生成 + 企微推送 ──
def run_weekly_backtest_report() -> dict:
    """生成完整回测报告（写文件）与精简摘要（企微推送用）。同步函数，线程池执行。"""
    from app.backtest.run import generate_report, save_report, generate_summary
    content = generate_report("all")
    path = save_report(content, tag="weekly")
    summary = generate_summary()
    return {"path": path, "summary": summary}


async def backtest_report_loop():
    """每周五盘后（周六补跑）自动生成全策略回测报告并推送企微摘要。
    周幂等：同一 ISO 周只跑一次（mark_done 用周 key 而非日期，跨周自动失效）。"""
    from app.flash.wechat import push_markdown_batched
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        iso = now.isocalendar()
        week_key = f"W{iso[0]}-{iso[1]:02d}"
        in_week_window = (now.weekday() == 4 and t >= 16 * 60) or now.weekday() == 5
        if in_week_window and not store.is_schedule_done("backtest_report", date_str=week_key):
            print(f"[scheduler] 触发周度回测报告生成（{week_key}）")
            try:
                result = await asyncio.to_thread(run_weekly_backtest_report)
                store.mark_schedule_done("backtest_report", date_str=week_key)
                status["last_backtest_report"] = rules.beijing_now().isoformat()
                push_markdown_batched("📊 周度回测报告", result["summary"])
                print(f"[scheduler] 周度回测报告完成: {result['path']}")
            except Exception as e:
                print(f"[scheduler] 周度回测报告失败: {e}")
                _notify_failure("周度回测报告", str(e))
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 评分排行快照每日保存 ──
SCORE_SNAPSHOT_WINDOW = (915, 1020)   # 北京时间 15:15-17:00（收盘后评分稳定，窗口宽支持补跑）

async def score_snapshot_loop():
    """
    每日盘后主动拉取评分 Top50 并记录快照（ranking_history 表）。
    此前排行记录只在用户访问评分页时被动写入（score_top 的 background_tasks）——
    页面不开当天就没快照、失败也无感知。本任务不依赖页面：主动请求与
    /score/batch/top 同源的 score_top()，失败重试 3 次，仍失败企微提醒。
    """
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "score_snapshot"
        if (now.weekday() < 5 and SCORE_SNAPSHOT_WINDOW[0] <= t < SCORE_SNAPSHOT_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发评分排行快照")
            last_err = ""
            for attempt in range(1, 4):   # 最多 3 次，间隔 90s（给行情缓存刷新留时间）
                try:
                    from app.tencent import _cache
                    if not _cache.get("stocks"):
                        # Render 休眠唤醒后内存缓存可能为空：先全量刷新（约 1-2 分钟）
                        print("[scheduler] 行情缓存为空，先刷新全量行情")
                        from app.tencent import refresh_all_stocks
                        await asyncio.to_thread(refresh_all_stocks)
                    from app.routers.scoring import score_top
                    result = await score_top(limit=50)
                    if result.get("data"):
                        # score_top 在调度器上下文 background_tasks=None 不会自动记录，
                        # 这里显式写入 ranking_history（ON CONFLICT 幂等）。
                        # 带维度分 + 快照价：供「胜率回查」与「权重优化分析」复用，无需前端手动保存。
                        from app.scoring.ranking_history import record_daily_ranking
                        from app.tencent import _cache as _t_cache
                        stocks_map = _t_cache.get("stocks", {})
                        stocks = [
                            {"code": r["code"], "name": r["name"],
                             "total_score": r["total_score"], "signal": r["signal"],
                             "rank": i + 1,
                             "dimensions": r.get("dimensions") or {},
                             "price": (stocks_map.get(r["code"]) or {}).get("price") or 0}
                            for i, r in enumerate(result["data"])
                        ]
                        # 盘后权威快照：清空当天记录再写入，避免盘中 background 记录的残留
                        count = await asyncio.to_thread(
                            record_daily_ranking, stocks, False, True
                        )
                        store.mark_schedule_done(task_key)
                        status["last_score_snapshot"] = rules.beijing_now().isoformat()
                        print(f"[scheduler] 评分快照已保存: {count} 条")
                        # ★ 预热今日 Top50 的 K 线缓存：快照代码（多为中小盘强势股）通常不在
                        #   市值前 500 的缓存池里，不预热则次日精算实时拉腾讯 K 线易触发 WAF 掉榜
                        try:
                            from app.scoring.kline_cache import refresh_kline_cache
                            top_codes = [r["code"] for r in result["data"]]
                            refresh_result = await asyncio.to_thread(
                                refresh_kline_cache, top_codes)
                            print(f"[scheduler] Top50 K线预热完成: {refresh_result}")
                        except Exception as e:
                            print(f"[scheduler] Top50 K线预热失败: {e}")
                        break
                    last_err = f"第{attempt}次返回空（cache_status={result.get('cache_status')}）"
                except Exception as e:
                    last_err = f"第{attempt}次异常: {e}"
                print(f"[scheduler] 评分快照{last_err}，90s 后重试")
                await asyncio.sleep(90)
            else:
                _notify_failure("评分排行快照", last_err or "未知错误")
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 全市场行情收盘快照（盘后/周末免刷新）──
MARKET_SNAPSHOT_WINDOW = (905, 935)   # 北京时间 15:05-15:35（收盘后数据稳定，窗口宽支持补跑）

async def market_snapshot_loop():
    """
    交易日收盘后保存全市场行情快照（方案 A）+ 持久化 _valid_codes（方案 C）。
    A 股数据仅盘中有时效性：盘后/周末/重启后直接从快照恢复内存缓存，
    避免 2-4 分钟全量扫描，/api/market/overview 首页秒开。
    保存前先确保缓存有收盘数据（为空则先增量刷新）。幂等（当日一次）。
    """
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "market_snapshot"
        if (now.weekday() < 5 and MARKET_SNAPSHOT_WINDOW[0] <= t < MARKET_SNAPSHOT_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发全市场行情收盘快照")
            try:
                from app.tencent import _cache, refresh_all_stocks, save_market_snapshot
                # 缓存为空（如 Render 休眠唤醒）→ 先增量刷新拿收盘数据（_valid_codes 已在则走增量）
                if not _cache.get("stocks"):
                    await asyncio.to_thread(refresh_all_stocks)
                ok = await asyncio.to_thread(save_market_snapshot)
                if ok:
                    store.mark_schedule_done(task_key)
                    status["last_market_snapshot"] = rules.beijing_now().isoformat()
                else:
                    print("[scheduler] 行情快照保存返回失败，稍后重试")
            except Exception as e:
                print(f"[scheduler] 行情快照保存失败: {e}")
                _notify_failure("行情收盘快照", str(e))
        await asyncio.sleep(300)  # 每 5 分钟检查一次


# ── 持仓负面消息提醒（消息面阶段 1）──
NEWS_ALERT_INTERVAL = 600    # 盘中每 10 分钟扫一轮持仓新闻分
NEWS_ALERT_THRESHOLD = -4    # 新闻分 ≤ -4（强烈负面）才推送，避免噪音刷屏
_news_alerted = {"date": "", "codes": set()}   # 同一股票同一自然日最多推一次（内存频控）


def _news_alert_markdown(name: str, code: str, score: float, items: list) -> str:
    """拼装负面消息提醒正文（抽出便于干跑验证格式）。"""
    top = "\n".join(f"> - {it['title']}" for it in (items or [])[:3]) or "> - （无情绪倾向条目）"
    return (f"> **{name}({code})** 新闻分 **{score}**（强烈负面）\n"
            f"> **相关快讯：**\n{top}\n\n关键词规则打分仅供参考，不构成买卖建议。")


def _check_holding_news_once():
    """扫描合并持仓的新闻分，强烈负面且当日未提醒过 → 企微推送。
    推送走 push_markdown_batched（force=False），受 WECHAT_BUSINESS_ALERTS 开关控制。
    返回本轮推送条数。
    """
    from app.database import db
    from app.eastmoney_news import get_stock_news
    from app.news_sentiment import score_stock_news
    from app.flash.wechat import push_markdown_batched

    today = rules.beijing_now().strftime("%Y-%m-%d")
    if _news_alerted["date"] != today:      # 跨天清空频控集合
        _news_alerted["date"] = today
        _news_alerted["codes"] = set()

    rows = db.fetch("SELECT DISTINCT code, name FROM user_portfolio")
    alerts = []
    for r in rows or []:
        code = r["code"]
        if code in _news_alerted["codes"]:
            continue
        res = score_stock_news(get_stock_news(code))
        if res["score"] <= NEWS_ALERT_THRESHOLD:
            _news_alerted["codes"].add(code)
            alerts.append((r.get("name") or code, code, res["score"], res["items"]))
    for name, code, score, items in alerts:
        push_markdown_batched(f"🚨 持仓负面消息 {name}", _news_alert_markdown(name, code, score, items))
    return len(alerts)


async def news_alert_loop():
    """盘中定期扫描持仓负面消息（同股同日最多推一次，推送受业务开关控制）。"""
    while True:
        market = rules.get_china_market_status()
        if market["is_open"]:
            try:
                n = await asyncio.to_thread(_check_holding_news_once)
                if n:
                    status["last_news_alert"] = rules.beijing_now().isoformat()
            except Exception as e:
                print(f"[scheduler] 持仓新闻提醒异常: {e}")
        await asyncio.sleep(NEWS_ALERT_INTERVAL)


# ── 消息分每日历史快照（阶段 3 回测的数据积累）──
NEWS_HISTORY_WINDOW = (920, 1020)   # 北京时间 15:20-17:00（与评分快照同窗口，宽窗口支持补跑）


def take_news_snapshot_once() -> int:
    """
    给「持仓股 + 评分 Top50」算当日新闻分并落库（news_history）。
    同步函数，由调度器丢线程池执行。逐只调搜索/快讯接口，间隔 0.3s 防限流。
    返回写入条数。
    """
    import time as _time
    from app.database import db
    from app.eastmoney_news import get_stock_news
    from app.news_sentiment import score_stock_news
    from app.news_history import record_news_snapshot

    # 股票池：持仓股 + 当日评分 Top50（直接读 ranking_history，避免重算评分）
    today = rules.beijing_now().strftime("%Y-%m-%d")
    pool = {}
    for r in db.fetch("SELECT DISTINCT code, name FROM user_portfolio") or []:
        pool[r["code"]] = r.get("name") or ""
    for r in db.fetch("""SELECT code, name FROM ranking_history
                         WHERE rank_date = %s ORDER BY rank_pos LIMIT 50""", (today,)) or []:
        pool.setdefault(r["code"], r.get("name") or "")

    rows = []
    for code, name in pool.items():
        try:
            items = get_stock_news(code)
            res = score_stock_news(items)
            rows.append({"code": code, "name": name, "score": res["score"],
                         "level": res["level"], "level_text": res["level_text"],
                         "news_count": len(items)})
        except Exception as e:
            print(f"[scheduler] 消息快照 {code} 计算失败: {e}")
        _time.sleep(0.3)
    return record_news_snapshot(rows)


async def news_history_loop():
    """工作日盘后保存消息分历史快照（幂等，错过窗口当日可补跑）。失败不吵：
    非核心任务（不影响当日功能），仅打印日志，窗口内每 5 分钟自然重试。"""
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "news_history"
        if (now.weekday() < 5 and NEWS_HISTORY_WINDOW[0] <= t < NEWS_HISTORY_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发消息分每日快照")
            try:
                n = await asyncio.to_thread(take_news_snapshot_once)
                if n:
                    store.mark_schedule_done(task_key)
                    status["last_news_history"] = rules.beijing_now().isoformat()
                    print(f"[scheduler] 消息分快照已保存: {n} 只")
                else:
                    print("[scheduler] 消息分快照写入 0 条，稍后重试")
            except Exception as e:
                print(f"[scheduler] 消息分快照失败: {e}")
        await asyncio.sleep(300)


# ── 财经日历缓存刷新（低频：每天一次即可）──
# 日历是静态排期数据（下周的非农/CPI 早就排好了），不需要像快讯那样分钟级轮询；
# 每天盘前刷一次足够。缓存为空时 calendar.get_items() 会现场拉一次兜底。
CALENDAR_WINDOW = (420, 480)   # 北京时间 07:00-08:00


async def calendar_loop():
    """每日盘前刷新财经日历缓存（幂等，错过窗口当日可补跑）。
    失败仅打印日志：日历是辅助数据，挂了不影响事件流/复盘，
    且缓存里仍保留着上一次成功拉取的数据。"""
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "calendar"
        if (CALENDAR_WINDOW[0] <= t < CALENDAR_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发财经日历刷新")
            try:
                n = await asyncio.to_thread(calendar.refresh)
                if n:
                    store.mark_schedule_done(task_key)
                    status["last_calendar"] = rules.beijing_now().isoformat()
                    print(f"[scheduler] 财经日历已更新: {n} 条")
                else:
                    print("[scheduler] 财经日历返回 0 条，稍后重试")
            except Exception as e:
                print(f"[scheduler] 财经日历刷新失败: {e}")
        await asyncio.sleep(600)


# ── 个股→行业映射表（每月重建一次）──
# 为什么定期重建：新股 IPO 持续新增（A股每年 200-400 只），不刷新它们就一直没有行业
# 归属；个股主业变更也会换分类。全量重建约 300-500 次请求，内部已加间隔防东财限流。
INDUSTRY_MAP_DAY = 1                  # 每月 1 号
# 窗口放宽到 03:00-20:00：起点仍是凌晨低峰（优先在低峰跑），但若当时东财主站
# 正被风控封禁（build_map 内有守卫会自动推迟），窗口内每小时重试，解封后当天补跑——
# 否则错过 1 小时窗口就要再等一个月。
INDUSTRY_MAP_WINDOW = (180, 1200)


async def industry_map_loop():
    """每月 1 号凌晨重建个股→行业映射表（幂等，窗口内错过可补跑）。
    失败只打日志：映射表是板块分化因子的可选输入，缺失时因子自动跳过，
    不影响快讯/复盘/信号等主流程。"""
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        month_key = f"M{now.year}-{now.month:02d}"
        if (now.day == INDUSTRY_MAP_DAY
                and INDUSTRY_MAP_WINDOW[0] <= t < INDUSTRY_MAP_WINDOW[1]
                and not store.is_schedule_done("industry_map", date_str=month_key)):
            print("[scheduler] 触发个股→行业映射表重建")
            try:
                from app.sector_industry import build_map
                r = await asyncio.to_thread(build_map, False)
                if r.get("ok"):
                    store.mark_schedule_done("industry_map", date_str=month_key)
                    status["last_industry_map"] = rules.beijing_now().isoformat()
                    print(f"[scheduler] 行业映射已重建: {r.get('stocks')} 只 / "
                          f"{r.get('sectors')} 板块 / {r.get('cost_sec')}s")
                else:
                    print(f"[scheduler] 行业映射重建失败: {r.get('error')}")
            except Exception as e:
                print(f"[scheduler] 行业映射重建异常: {e}")
        await asyncio.sleep(3600)


# ── 板块每日快照（收盘后记录，积累板块历史序列）──
SECTOR_SNAPSHOT_WINDOW = (910, 1000)   # 北京时间 15:10-16:40


async def sector_snapshot_loop():
    """每个交易日收盘后记录板块快照（幂等，错过窗口可补跑）。

    只在交易日跑：非交易日板块数据不更新，记进去全是和上一天一样的重复行，
    会污染序列（比如算板块动量时凭空多出几天零变化）。

    失败只打日志：快照是给未来攒数据的，漏一天不影响现有功能。
    """
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        task_key = "sector_snapshot"
        if (rules.is_trading_day(now)
                and SECTOR_SNAPSHOT_WINDOW[0] <= t < SECTOR_SNAPSHOT_WINDOW[1]
                and not store.is_schedule_done(task_key)):
            print("[scheduler] 触发板块每日快照")
            try:
                from app.sector_industry import take_snapshot
                r = await asyncio.to_thread(take_snapshot)
                if r.get("written"):
                    store.mark_schedule_done(task_key)
                    status["last_sector_snapshot"] = rules.beijing_now().isoformat()
                    print(f"[scheduler] 板块快照已记录: {r['written']} 条 {r.get('kinds')}")
                else:
                    print(f"[scheduler] 板块快照未写入（{r.get('skipped')}），稍后重试")
            except Exception as e:
                print(f"[scheduler] 板块快照失败: {e}")
        await asyncio.sleep(600)


# ── 财务数据更新（每天凌晨检查，新报告期出现才拉取）──
FINANCE_WINDOW = (200, 260)   # 北京时间 03:20-04:20


async def finance_loop():
    """
    财务数据季度更新：每天检查一次"接口最新报告期"（1 次请求，秒回），
    库里没有该报告期才拉取（约 14 秒/期）。幂等 key = 报告期本身，
    同一期绝不重复拉；错过凌晨窗口当日任意时刻开机也会补。

    财报披露节奏：一季报4月底 / 中报8月底 / 三季报10月底 / 年报次年4月底，
    披露是渐进的（个股会延期），所以"每天检查"比"固定季度跑"更可靠。
    """
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        if (FINANCE_WINDOW[0] <= t < FINANCE_WINDOW[1]
                and not store.is_schedule_done("finance_probe")):
            store.mark_schedule_done("finance_probe")   # 每天只探测一次
            try:
                from app.finance import latest_report_date, has_report, refresh
                latest = await asyncio.to_thread(latest_report_date)
                if not latest:
                    print("[scheduler] 财务数据：无法探测最新报告期，明天再试")
                elif store.is_schedule_done("finance_refresh", date_str=latest):
                    print(f"[scheduler] 财务数据已是最新（{latest}）")
                elif has_report(latest):
                    # 库里已有（如手动跑过脚本）但没标记 → 补标记即可
                    store.mark_schedule_done("finance_refresh", date_str=latest)
                    print(f"[scheduler] 财务数据 {latest} 已在库，补标记")
                else:
                    print(f"[scheduler] 发现新报告期 {latest}，拉取财务数据")
                    r = await asyncio.to_thread(refresh, 2)
                    if r.get("ok"):
                        store.mark_schedule_done("finance_refresh", date_str=latest)
                        status["last_finance_refresh"] = rules.beijing_now().isoformat()
                        print(f"[scheduler] 财务数据已更新: {r.get('written')} 条 "
                              f"({r.get('cost_sec')}s)")
                    else:
                        print(f"[scheduler] 财务刷新失败: {r.get('error')}")
            except Exception as e:
                print(f"[scheduler] 财务数据检查异常: {e}")
        await asyncio.sleep(600)


async def start():
    """启动全部调度循环（由 main.py 的 lifespan 调用，返回任务句柄便于关闭时取消）。"""
    status["running"] = True
    status["started_at"] = rules.beijing_now().isoformat()
    tasks = [asyncio.create_task(flash_loop()),
             asyncio.create_task(track_loop()),
             asyncio.create_task(review_loop()),
             asyncio.create_task(macro_daily_loop()),
             asyncio.create_task(health_loop()),
             asyncio.create_task(stock_cache_refresh_loop()),
             asyncio.create_task(kline_cache_refresh_loop()),
             asyncio.create_task(indicator_cache_refresh_loop()),
             asyncio.create_task(backtest_prices_refresh_loop()),
             asyncio.create_task(backtest_preheat_loop()),
             asyncio.create_task(strategy_scan_loop()),
             asyncio.create_task(regime_cache_loop()),
             asyncio.create_task(backtest_report_loop()),
             asyncio.create_task(score_snapshot_loop()),
             asyncio.create_task(market_snapshot_loop()),
             asyncio.create_task(news_alert_loop()),
             asyncio.create_task(news_history_loop()),
             asyncio.create_task(calendar_loop()),
             asyncio.create_task(industry_map_loop()),
             asyncio.create_task(sector_snapshot_loop()),
             asyncio.create_task(finance_loop()),
             asyncio.create_task(open_confirmation_loop())]
    print(f"[scheduler] 已启动: 快讯{FLASH_POLL_INTERVAL}s / 跟踪{TRACK_INTERVAL}s / "
          f"行情缓存{STOCK_CACHE_INTERVAL}s / K线缓存每日15:30 / 指标缓存每日16:00 / "
          f"回测价格每日15:40 / 战法扫描每日15:40 / 市场状态每日15:40 / "
          f"周度回测报告周五16:00 / 评分快照每日15:15 / 行情收盘快照每日15:05 / "
          f"消息分快照每日15:20 / 持仓负面消息盘中每10分钟 / "
          f"财经日历每日{CALENDAR_WINDOW[0] // 60}:{CALENDAR_WINDOW[0] % 60:02d} / "
          f"行业映射每月{INDUSTRY_MAP_DAY}号03:00 / "
          f"板块快照交易日{SECTOR_SNAPSHOT_WINDOW[0] // 60}:"
          f"{SECTOR_SNAPSHOT_WINDOW[0] % 60:02d} / "
          f"宏观锁定每日{MACRO_DAILY_WINDOW[0] // 60}:{MACRO_DAILY_WINDOW[0] % 60:02d} / "
          f"复盘窗口 {REVIEW_WINDOWS} | LLM{'✅' if llm_configured() else '❌未配置'} "
          f"金十{'✅' if FLASH_COOKIE else '❌未配置'} 微信{'✅' if WECHAT_WEBHOOK else '❌未配置'}")
    return tasks


def stop(tasks):
    """优雅取消全部循环。"""
    for t in tasks:
        t.cancel()
    status["running"] = False
