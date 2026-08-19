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
    "config": {
        "flash_interval_sec": FLASH_POLL_INTERVAL,
        "track_interval_sec": TRACK_INTERVAL,
        "flash_cookie_configured": bool(FLASH_COOKIE),
        "llm_configured": llm_configured(),
        "wechat_configured": bool(WECHAT_WEBHOOK),
    },
}


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


async def start():
    """启动全部调度循环（由 main.py 的 lifespan 调用，返回任务句柄便于关闭时取消）。"""
    from datetime import datetime
    status["running"] = True
    status["started_at"] = datetime.now().isoformat()
    tasks = [asyncio.create_task(flash_loop()),
             asyncio.create_task(track_loop()),
             asyncio.create_task(review_loop()),
             asyncio.create_task(health_loop()),
             asyncio.create_task(stock_cache_refresh_loop())]
    print(f"[scheduler] 已启动: 快讯{FLASH_POLL_INTERVAL}s / 跟踪{TRACK_INTERVAL}s / "
          f"行情缓存{STOCK_CACHE_INTERVAL}s / "
          f"复盘窗口 {REVIEW_WINDOWS} | LLM{'✅' if llm_configured() else '❌未配置'} "
          f"金十{'✅' if FLASH_COOKIE else '❌未配置'} 微信{'✅' if WECHAT_WEBHOOK else '❌未配置'}")
    return tasks


def stop(tasks):
    """优雅取消全部循环。"""
    for t in tasks:
        t.cancel()
    status["running"] = False
