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

from app.flash import service, store, rules
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
    "last_score_snapshot": None,
    "last_market_snapshot": None,
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
    """三段复盘循环：到窗口且当日未跑 → 执行并标记。"""
    while True:
        now = rules.beijing_now()
        t = now.hour * 60 + now.minute
        for phase, (start, end) in REVIEW_WINDOWS.items():
            task_key = f"review_{phase}"
            if start <= t < end and not store.is_schedule_done(task_key):
                print(f"[scheduler] 触发复盘: {phase}")
                result = await _run_sync(service.run_review, phase)
                store.mark_schedule_done(task_key)
                if result:
                    status["last_reviews"][phase] = result.get("time")
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
            except Exception as e:
                print(f"[scheduler] 回测价格回填失败: {e}")
                _notify_failure("回测价格回填", str(e))
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
                        # 这里显式写入 ranking_history（ON CONFLICT 幂等）
                        from app.scoring.ranking_history import record_daily_ranking
                        stocks = [
                            {"code": r["code"], "name": r["name"],
                             "total_score": r["total_score"], "signal": r["signal"],
                             "rank": i + 1}
                            for i, r in enumerate(result["data"])
                        ]
                        count = await asyncio.to_thread(record_daily_ranking, stocks)
                        store.mark_schedule_done(task_key)
                        status["last_score_snapshot"] = rules.beijing_now().isoformat()
                        print(f"[scheduler] 评分快照已保存: {count} 条")
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
             asyncio.create_task(score_snapshot_loop()),
             asyncio.create_task(market_snapshot_loop())]
    print(f"[scheduler] 已启动: 快讯{FLASH_POLL_INTERVAL}s / 跟踪{TRACK_INTERVAL}s / "
          f"行情缓存{STOCK_CACHE_INTERVAL}s / K线缓存每日15:30 / 指标缓存每日16:00 / "
          f"回测价格每日15:40 / 评分快照每日15:15 / 行情收盘快照每日15:05 / "
          f"宏观锁定每日{MACRO_DAILY_WINDOW[0] // 60}:{MACRO_DAILY_WINDOW[0] % 60:02d} / "
          f"复盘窗口 {REVIEW_WINDOWS} | LLM{'✅' if llm_configured() else '❌未配置'} "
          f"金十{'✅' if FLASH_COOKIE else '❌未配置'} 微信{'✅' if WECHAT_WEBHOOK else '❌未配置'}")
    return tasks


def stop(tasks):
    """优雅取消全部循环。"""
    for t in tasks:
        t.cancel()
    status["running"] = False
