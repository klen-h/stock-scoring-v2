# -*- coding: utf-8 -*-
"""
系统状态路由：数据新鲜度仪表盘（GET /api/system/status）

把"每类数据最后更新到什么时候"聚成一个接口——盘后任务链 17 个环节，
哪个环节断了这里一眼可见（2026-09-06 深市两融滞后、东财封禁等问题的运维出口）。

新鲜度判定（交易日感知）：
  ok      数据日期 = 最近交易日（盘后任务当日出即 ok）
  stale   落后最近交易日 1-2 个交易日（可能数据源滞后，如深市两融）
  missing 无数据 或 落后 ≥3 个交易日（任务链断了，需排查）
"""

from collections import deque
from datetime import datetime, timedelta
from itertools import islice
import os
import sys
import threading
from typing import Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.auth import get_current_user
from app.database import db

router = APIRouter()


def _latest_trading_day() -> str:
    """最近交易日（收盘数据 15:30 后才算当日）。"""
    from app.flash.rules import beijing_now
    now = beijing_now()
    d = now.date()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        d -= timedelta(days=1)
    hol = []
    try:
        from app.flash import rules as _rules
        hol = _rules.HOLIDAYS.get(d.year) or []
    except Exception:
        pass
    while d.weekday() >= 5 or any(s <= (d.month, d.day) <= e for s, e, *_ in hol):
        d -= timedelta(days=1)
    return d.isoformat()


def _trading_days_ago(n: int) -> str:
    d = datetime.strptime(_latest_trading_day(), "%Y-%m-%d").date()
    checked = 0
    while checked < n:
        d -= timedelta(days=1)
        hol = []
        try:
            from app.flash import rules as _rules
            hol = _rules.HOLIDAYS.get(d.year) or []
        except Exception:
            pass
        if d.weekday() < 5 and not any(s <= (d.month, d.day) <= e for s, e, *_ in hol):
            checked += 1
    return d.isoformat()


_SOURCES = [
    # (名称, 查询SQL取最新日期, 预期节奏说明, 容忍滞后交易日数(默认2))
    ("行情收盘快照", "SELECT MAX(saved_at) AS v FROM market_snapshot", "每日 15:05", 2),
    ("回测价格库", "SELECT MAX(date) AS v FROM backtest_prices", "每日 15:40", 2),
    ("主力资金流", "SELECT MAX(date) AS v FROM mainflow_history", "每日 17:00", 2),
    ("主力行为状态", "SELECT MAX(date) AS v FROM mainforce_state", "日批 19:00 后", 2),
    ("龙虎榜", "SELECT MAX(date) AS v FROM lhb_history", "每日 17:45", 2),
    ("评分快照", "SELECT MAX(rank_date) AS v FROM ranking_history", "每日 18:00", 2),
    ("消息分快照", "SELECT MAX(snap_date) AS v FROM news_history", "日批 19:20 后", 2),
    ("矛盾扫描", "SELECT MAX(date) AS v FROM contradictions", "每日 15:35", 2),
    ("每日日报", "SELECT MAX(date) AS v FROM daily_reports", "每日 19:30", 2),
    # 周任务：季度数据周更保活，容忍窗口放宽到 8 个交易日（约两周）
    ("财报扩展(zzshare)", "SELECT MAX(updated_at) AS v FROM stock_finance_zz", "每周一同步", 8),
    # ★ 2026-09-11：周度回测报告（日批周五生成，正文落库 backtest_reports）
    ("周度回测报告", "SELECT MAX(created_at) AS v FROM backtest_reports", "日批 周五", 8),
    ("市场状态判定", "SELECT MAX(date) AS v FROM market_regime_history", "每日 15:40", 2),
    # ★ 2026-09-12：交易员决策简报（日批盘后生成 + 企微推送；此前只有前端按需生成）
    ("交易员决策简报", "SELECT MAX(date) AS v FROM trader_briefs", "日批 盘后", 2),
]


def _to_date(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s[:10] if len(s) >= 10 else s


@router.get("/runtime-files")
def runtime_files(user: dict = Depends(get_current_user)) -> Dict:
    """运行时「文件型数据」清单与完整性（数据库之外还靠文件的地方）。

    ★ 2026-09-12：浏览器镜像退役（它保护的 9 个文件早已全部迁库、只剩空壳），
      改由本接口 + 启动检查盯着真正还在文件里的数据：
      财经日历 / LLM 用量与日熔断基线 / K线磁盘缓存 / 后端数据包。
      字段：exists 是否存在、ok 内容是否可用、lost（最近 7 天见过但现在没了）、
      last_seen（DB 水位记录的最后一次见到时间）。"""
    from app import data_files
    return data_files.check_all()


@router.get("/status")
def system_status(user: dict = Depends(get_current_user)) -> Dict:
    """数据新鲜度仪表盘：每类数据最后日期 + ok/stale/missing 判定 + 调度器最近运行。"""
    latest = _latest_trading_day()
    t1 = _trading_days_ago(1)
    t2 = _trading_days_ago(2)

    sources = []
    ok_count = 0
    for name, sql, cadence, *rest in _SOURCES:
        max_lag = rest[0] if rest else 2   # 容忍滞后交易日数（周任务放宽）
        last = None
        try:
            row = db.fetch_one(sql)
            last = _to_date((row or {}).get("v"))
        except Exception as e:
            print(f"[system_status] {name} 查询失败: {e}")
        if not last:
            status, lag = "missing", None
        elif last >= latest:
            status, lag = "ok", 0
        elif max_lag > 2:
            # 低频任务（周同步等）：容忍窗口内一律 ok，不按日频误报 stale
            if last >= _trading_days_ago(max_lag):
                status, lag = "ok", max_lag
            else:
                status = "stale"
                try:
                    lag = (datetime.strptime(latest, "%Y-%m-%d")
                           - datetime.strptime(last, "%Y-%m-%d")).days
                except ValueError:
                    lag = None
        elif last >= t1:
            status, lag = "ok", 1        # 深市两融这类 T+1 滞后属正常
        elif last >= t2:
            status, lag = "stale", 2
        else:
            try:
                lag = (datetime.strptime(latest, "%Y-%m-%d")
                       - datetime.strptime(last, "%Y-%m-%d")).days
            except ValueError:
                lag = None
            status = "stale"
        ok_count += 1 if status == "ok" else 0
        sources.append({"name": name, "last": last, "status": status,
                        "lag_days": lag, "cadence": cadence})

    # 调度器最近运行时间戳（进程内存，未重启才有）
    sched = {}
    try:
        from app.flash.scheduler import status as sched_status
        raw = dict(sched_status) if sched_status else {}
        sched = {k: v for k, v in raw.items() if k.startswith("last_")}
    except Exception:
        pass

    return {
        "latest_trading_day": latest,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": f"{ok_count}/{len(sources)} 新鲜",
        "sources": sources,
        "scheduler": sched,
    }


@router.get("/trader-brief")
def trader_brief(phase: str = Query(None), refresh: bool = False,
                 user: dict = Depends(get_current_user)):
    """交易员决策简报（PLAN_TRADER_WORKFLOW Phase 1）。

    按当前时间自动选 phase（premarket/intraday/postmarket）；
    默认读取当日已生成简报（幂等），refresh=true 强制重新生成（消耗 1 次 LLM 调用）。
    """
    from app.trader_brief import generate_trader_brief, current_phase
    return generate_trader_brief(phase or current_phase(), force=refresh)


# ══════════════════════════════════════════════════════════════════════════
#  进程内存诊断（GET /api/system/memory）—— 2026-09-23 新增
# ══════════════════════════════════════════════════════════════════════════
# 【背景】Render 实例内存上限 **500MB**（用户 2026-09-23 确认）。监控图显示：
#   内存从 ~10% **缓慢爬升**到 80~90% → **骤降**（进程被杀 + 自动重启），48 小时内
#   反复多次；而 CPU 长期 <5%。⇒ ① 不是算力不足，是内存；② 形态是「几小时内累积
#   爬升」而非瞬时尖峰 ⇒ 需要回答的是「**谁在涨**」，光看总 RSS 曲线做不到。
#
# 【定位】与 `/status`（数据新鲜度）同为运维出口：数据断链看 `/status`，
#   内存爬升/OOM 看这里。
#
# 【怎么用】部署后调一次（基线）→ 隔几小时（或内存又爬高时）再调一次 →
#   看 `diff.growth` 里哪一项在涨。diff 基准是**进程内**上一次调用 ⇒ 若进程已重启，
#   `diff.reset` 会为 true（这本身就是「又 OOM 了一次」的信号）。
#   读法：`caches_total_mb` 与 `unaccounted_mb` 对比 —— 后者大 ⇒ 大头不在已知模块级
#   缓存里（得看 `types` 的类型分布，多半是框架/请求级对象或 RSS 高水位）。
#
# 【成本与安全】纯只读；探针**只读 `sys.modules`**（不主动 import ⇒ 零副作用，且
#   未加载的模块本来就不占内存）；大容器用**采样外推**估算（`islice` 取前 N 项，
#   **不 list() 复制**）；RSS 使用率 >85% 时自动跳过最重的「GC 类型分布」自我保护。
# ══════════════════════════════════════════════════════════════════════════

# 内存上限（MB）。Render 免费/基础档为 500MB；可用环境变量覆盖以便换档后不改码。
_MEM_LIMIT_MB = int(os.environ.get("MEM_LIMIT_MB") or 500)

# 上一次调用的快照（进程内）⇒ 供 diff 计算「谁在涨」。
_MEM_SNAPSHOT: Dict = {"ts": 0.0, "rss_mb": None, "caches": {}}

# 探针清单：(模块路径, 属性名, 说明)。**新增模块级缓存时补一行**（只影响可观测性，
# 漏了不会出错 —— 该项只会在 unaccounted_mb 里被当成「未归类」）。
_CACHE_PROBES = [
    # —— 行情 / K线（最大嫌疑：全市场 + 逐只明细）——
    ("app.tencent", "_cache", "全市场实时行情（stocks 字典）"),
    ("app.tencent", "KLINE_CACHE", "K线缓存（详情页/评分精算）"),
    ("app.tencent", "_CODE_TO_PREFIX", "代码→市场前缀表"),
    ("app.tencent", "_valid_codes", "有效代码列表"),
    # —— 回测价格（历史 bars，按 code+区间）——
    ("app.backtest.data", "_PRICES_CACHE", "回测价格缓存"),
    ("app.backtest.strategies", "_PRICES_CACHE", "回测价格缓存（策略侧）"),
    ("app.backtest.market_regime", "_BARS_CACHE", "市场状态 bars"),
    ("app.backtest.market_regime", "_REGIME_CACHE", "市场状态判定"),
    # —— 主力 / 资金流 ——
    ("app.mainforce.flow", "_FS_CACHE", "资金流分"),
    ("app.mainforce.flow", "_FLOW_MAP_CACHE", "资金流 map（按 codes 组合）"),
    ("app.mainforce.flow", "_FLOW_CACHE", "单只资金流"),
    ("app.mainforce.state", "_latest_cache", "主力行为状态（全池）"),
    ("app.mainforce.gate", "_fs_cache", "闸门资金流"),
    ("app.mainforce.trade_gate", "_contra_cache", "闸门矛盾"),
    ("app.mainforce.confluence", "_env_cache", "合力环境"),
    # —— 评分 / 榜单 ——
    ("app.scoring.engine", "_IND_DIST", "行业分布"),
    ("app.scoring.live_ranking", "_load_cache", "实时榜单"),
    ("app.scoring.ranking_history", "_COMPOSITE_CACHE", "综合分历史"),
    ("app.routers.scoring", "_bt_cache", "回测结果缓存"),
    ("app.routers.scoring", "_bucket_cache", "评分分桶"),
    ("app.routers.scoring", "_composite_cache", "综合分"),
    ("app.routers.scoring", "_GATE_WATCH", "闸门观察"),
    # —— 快讯 / 推送 / 健康 ——
    ("app.flash.store", "_RAW_CACHE", "快讯原始"),
    ("app.flash.store", "_MACRO_CACHE", "宏观快讯"),
    ("app.flash.store", "_SNAP_CACHE", "快讯快照"),
    ("app.flash.signal_bus", "_mem", "信号总线（环形，有上限）"),
    ("app.flash.signal_bus", "_dedup", "信号去重（90s 窗口）"),
    ("app.flash.scheduler", "_news_alerted", "新闻提醒频控"),
    ("app.flash.wechat", "_token_cache", "企微 token"),
    ("app.flash.wechat", "_circuit", "推送熔断分槽"),
    ("app.flash.margin_sentiment", "_margin_cache", "两融"),
    ("app.flash.margin_sentiment", "_sentiment_cache", "情绪"),
    ("app.flash.treasury", "_CACHE", "国债"),
    ("app.health", "_state", "数据源健康（deque 窗口）"),
    ("app.health", "_alerts", "健康告警（最近 20 条）"),
    # —— 教练 / 策略 / 持仓雷达 ——
    ("app.coach.rules", "_config_cache", "教练配置"),
    ("app.coach.rules", "_macro_cache", "宏观（教练）"),
    ("app.coach.rules", "_day_realized_cache", "当日已实现"),
    ("app.coach.rules", "_idx_move_cache", "指数波动"),
    ("app.coach.explainer", "_fuse_cache", "熔断快照"),
    ("app.strategies.recommendation", "_whitelist_cache", "白名单"),
    ("app.strategies.router", "_scan_status", "战法扫描状态"),
    ("app.signals.tracker", "_TRACK_CACHE", "信号跟踪"),
    ("app.contradictions.risk_hook", "_CACHE", "矛盾风险钩子"),
    ("app.portfolio_radar", "_watch_cache", "观察池快照"),
    ("app.portfolio_radar", "_rank_cache", "榜单（持仓雷达）"),
    ("app.portfolio_radar", "_holdings_cache", "持仓（持仓雷达）"),
    ("app.portfolio_radar", "_contra_ctx_cache", "矛盾上下文"),
    # —— 其他 ——
    ("app.macro", "_cache", "宏观数据"),
    ("app.sync_meta", "_VER_CACHE", "版本缓存"),
    ("app.routers.stock", "_news_cache", "个股新闻"),
    ("app.routers.stock", "_news_history_cache", "个股新闻历史"),
]


def _read_rss_mb() -> Optional[float]:
    """当前进程 RSS（MB，常驻物理内存）。

    优先读 `/proc/self/status`（Render = Linux，最准）；回退 `resource.getrusage`
    （注意 **Linux 是 kB、macOS 是 bytes**，单位不同，混用会差 1024 倍）。
    """
    try:
        with open("/proc/self/status", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024.0, 1)      # kB → MB
    except Exception:
        pass
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        div = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
        return round(v / div, 1)
    except Exception:
        return None


def _peak_rss_mb() -> Optional[float]:
    """进程生命周期内的**峰值** RSS（MB）—— 与上限对比可知离 OOM 多近。"""
    try:
        import resource
        v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        div = 1024.0 * 1024.0 if sys.platform == "darwin" else 1024.0
        return round(v / div, 1)
    except Exception:
        return None


def _deep_bytes(obj, depth: int = 0, sample: int = 25) -> int:
    """估算容器对象的**深**占用（字节）。大容器**采样外推**，不复制整个容器。

    ★ 为什么不能只用 `sys.getsizeof`：它只算容器壳（dict 装 8 个条目 ≈ 232B），
      而真正的大头在里面 —— 全市场行情字典壳只有几百字节，内容可能几十 MB。
    ★ 为什么要采样：递归整个大容器是 O(n) 且很慢（4000 只 × 每只几十字段）。
      这里取前 sample 项求均值再乘总数（**`islice` 取，不 `list()` 复制** —— 后者
      会让峰值内存翻倍，在 500MB 边缘是致命的）。
    ★ 采样数随深度衰减（25→12→6→3→1）：否则最坏分支数 25^depth 会爆。
    ★★ 采样取「**前 sample 项 + 末尾 sample 项**」而非只取前 N 项。为什么：新增数据
      总是**追加在末尾**（dict 保序、list 用 append），只采头部会把「刚加进去的大条目」
      平均掉 —— 实测（本端点自测）：给 209 条的 K 线缓存追加 1 条 3000 根的 K 线，
      头部采样完全看不见（+43KB，低于 0.5MB 阈值 ⇒ diff 漏报）。末尾采样只多一次
      islice 遍历（不复制容器，只复制 2×sample 项）。
    """
    try:
        base = sys.getsizeof(obj)
    except Exception:
        return 0
    if depth >= 5:
        return base
    nxt = max(1, sample // 2)
    if isinstance(obj, dict):
        n = len(obj)
        if n == 0:
            return base
        if n <= sample:
            return base + sum(_deep_bytes(k, depth + 1, nxt) + _deep_bytes(v, depth + 1, nxt)
                              for k, v in obj.items())
        picked = list(islice(obj.items(), sample))
        if n > sample * 2:                       # 末尾采样：追加的新数据在尾部
            picked += list(islice(obj.items(), n - sample, n))
        tot = sum(_deep_bytes(k, depth + 1, nxt) + _deep_bytes(v, depth + 1, nxt)
                  for k, v in picked)
        return base + int(tot / len(picked) * n)
    if isinstance(obj, (list, tuple, set, frozenset, deque)):
        n = len(obj)
        if n == 0:
            return base
        if n <= sample:
            return base + sum(_deep_bytes(x, depth + 1, nxt) for x in obj)
        picked = list(islice(obj, sample))
        if n > sample * 2:
            picked += list(islice(obj, n - sample, n))
        tot = sum(_deep_bytes(x, depth + 1, nxt) for x in picked)
        return base + int(tot / len(picked) * n)
    return base


def _cache_sizes() -> list:
    """逐个探针取条目数 + 深估算（MB）。

    模块**只从 `sys.modules` 取**（不主动 import）：① 零副作用（不触发模块顶层代码）；
    ② 语义更对 —— 未加载的模块本来就不占内存，报 0 反而误导。
    """
    out = []
    for mod, attr, note in _CACHE_PROBES:
        key = f"{mod}.{attr}"
        m = sys.modules.get(mod)
        if m is None:
            continue                                  # 尚未加载 ⇒ 不占内存，跳过
        obj = getattr(m, attr, None)
        if obj is None:
            continue
        try:
            n = len(obj) if hasattr(obj, "__len__") else None
            out.append({"key": key, "note": note, "items": n,
                        "mb": round(_deep_bytes(obj) / 1048576.0, 2)})
        except Exception as e:
            out.append({"key": key, "note": note, "mb": None, "error": str(e)[:60]})
    out.sort(key=lambda x: (x.get("mb") or 0), reverse=True)
    return out


def _gc_type_top(limit: int = 12) -> list:
    """存活对象按**类型**计数 Top N（找「某种对象异常多」这类泄漏特征）。

    ★ 这是本端点最重的一步（`gc.get_objects()` 会为全部存活对象建临时列表）；
      调用方在 RSS 吃紧时会跳过它。
    """
    import gc
    from collections import Counter
    c: Counter = Counter()
    try:
        for o in gc.get_objects():
            c[type(o).__name__] += 1
    except Exception:
        pass
    return [{"type": k, "count": v} for k, v in c.most_common(limit)]


@router.get("/memory")
def memory_diag(types: int = 1, top: int = 15,
                user: dict = Depends(get_current_user)) -> Dict:
    """进程内存诊断：RSS / 已知缓存占用 / 对象类型分布 / 与上次调用对比。

    参数：
      types=0  跳过「GC 对象类型分布」（最重的一步；RSS 紧张时自动跳过）
      top=N    缓存与增长清单各返回前 N 项

    返回要点：
      process.used_pct      当前占上限百分比（>80 需警惕，>90 随时 OOM）
      process.peak_rss_mb   历史峰值（判「是否曾逼近上限」）
      caches                **按占用降序**的已知模块级缓存（含条目数）
      caches_total_mb       已知缓存合计
      unaccounted_mb        RSS − 已知缓存 ⇒ 大头是否在别处（框架/请求对象/高水位）
      diff.growth           与上次调用的增量（**核心用法：看谁在涨**）
      diff.reset            true = 进程重启过（上次快照丢失 = 又 OOM 了一次）
    """
    import gc
    import time as _time
    from app.flash.rules import beijing_now
    _tz = beijing_now().tzinfo            # 时间戳一律按北京时间展示（与项目惯例一致）
    _fmt_ts = lambda ts: datetime.fromtimestamp(ts, _tz).isoformat(timespec="seconds")

    rss = _read_rss_mb()
    peak = _peak_rss_mb()
    used_pct = (round(rss / _MEM_LIMIT_MB * 100, 1)
                if (rss and _MEM_LIMIT_MB) else None)

    caches = _cache_sizes()
    caches_total = round(sum(c["mb"] or 0 for c in caches), 2)

    # 与上次调用对比（进程内）—— 这是定位「谁在涨」的关键
    now = _time.time()
    prev = _MEM_SNAPSHOT
    diff = None
    if prev.get("ts"):
        growth = []
        for c in caches:
            old = (prev.get("caches") or {}).get(c["key"])
            if not isinstance(old, dict) or c["mb"] is None:
                continue
            d = round(c["mb"] - old.get("mb", 0), 2)
            # ★ 条目数是**精确**计数（MB 是采样估算，有 ±）⇒ 两者任一明显增长都上报。
            #   实测过两类都要抓：『单个条目变大』（K线缓存塞大票）与『条目数暴涨』。
            di = (c["items"] - old["items"]) if (c["items"] is not None
                                                and old.get("items") is not None) else None
            if abs(d) < 0.5 and not (di is not None and abs(di) >= 20):
                continue                               # 低于噪声阈值
            growth.append({"key": c["key"], "note": c["note"], "mb": c["mb"],
                           "was_mb": old.get("mb"), "delta_mb": d,
                           "items": c["items"], "items_delta": di})
        growth.sort(key=lambda x: (-abs(x["delta_mb"]),
                                   -abs(x.get("items_delta") or 0)))
        rss_delta = (round(rss - prev["rss_mb"], 1)
                     if (rss is not None and prev.get("rss_mb") is not None) else None)
        diff = {
            "prev_ts": _fmt_ts(prev["ts"]),
            "elapsed_min": round((now - prev["ts"]) / 60, 1),
            "rss_delta_mb": rss_delta,
            "growth": growth[:top],
            "reset": False,
        }

    # 更新快照（供下次对比）
    _MEM_SNAPSHOT.update({"ts": now, "rss_mb": rss,
                          "caches": {c["key"]: {"mb": c["mb"], "items": c["items"]}
                                     for c in caches}})

    # 类型分布（最重的一步）：显式关闭 or RSS 已 >85% 时跳过（自我保护，别把进程压垮）
    types_out, types_skipped = [], None
    if not types:
        types_skipped = "参数 types=0"
    elif used_pct is not None and used_pct > 85:
        types_skipped = f"RSS 已 {used_pct}%（>85%），跳过以免加重内存压力"
    else:
        types_out = _gc_type_top()

    try:
        gc_tracked = len(gc.get_objects()) if types_out else None
    except Exception:
        gc_tracked = None

    # ── 人类可读摘要（markdown 一段）—— curl / 前端 / 告警推送复用同一段文本 ──
    _rss_s = f"{rss}MB" if rss is not None else "未知（非 Linux 无 /proc）"
    _pct_s = f"{used_pct}%" if used_pct is not None else "—"
    _sum = [f"**进程内存 {_rss_s} / {_MEM_LIMIT_MB}MB（{_pct_s}）**"
            + (f"，峰值 {peak}MB" if peak is not None else "")
            + f"，线程 {threading.active_count()}"]
    top_c = [c for c in caches if (c["mb"] or 0) >= 0.5][:5]
    if top_c:
        _sum.append("占用最多：" + "、".join(
            f"{c['key'].split('.')[-1]} {c['mb']}MB({c['items']}条)" for c in top_c))
    _sum.append(f"已知缓存合计 {caches_total}MB"
                + (f"，未归类 {round(rss - caches_total, 1)}MB（大头在框架/请求对象或 RSS 高水位）"
                   if rss is not None else ""))
    if diff is None:
        _sum.append("首次调用 ⇒ 本次是基线，隔一段时间再调一次即可看增长")
    elif diff.get("reset"):
        _sum.append("⚠️ 上次快照已丢失 ⇒ **进程重启过**（很可能就是又 OOM 了一次）")
    elif diff.get("growth"):
        _sum.append(f"较上次（{diff['elapsed_min']}分钟前）增长：" + "、".join(
            f"{g['key'].split('.')[-1]} {g['delta_mb']:+.1f}MB"
            + (f"/{g['items_delta']:+d}条" if g.get("items_delta") else "")
            for g in diff["growth"][:3]))
    else:
        _sum.append(f"较上次（{diff['elapsed_min']}分钟前）无明细增长"
                    + (f"（总 RSS {diff['rss_delta_mb']:+.1f}MB）"
                       if diff.get("rss_delta_mb") is not None else ""))

    return {
        "ok": True,
        "generated_at": beijing_now().isoformat(timespec="seconds"),
        "summary": "\n".join(_sum),
        "process": {
            "rss_mb": rss,
            "peak_rss_mb": peak,
            "limit_mb": _MEM_LIMIT_MB,
            "used_pct": used_pct,
            "threads": threading.active_count(),
        },
        "python": {
            "version": sys.version.split()[0],
            "allocated_blocks": sys.getallocatedblocks(),
            "gc_counts": list(gc.get_count()),
            "gc_garbage": len(gc.garbage),
            "gc_tracked_objs": gc_tracked,
        },
        "types": types_out,
        "types_skipped": types_skipped,
        "caches": caches[:max(1, top)],
        "caches_total_mb": caches_total,
        "unaccounted_mb": (round(rss - caches_total, 1) if rss is not None else None),
        "diff": diff or {"reset": True, "note": "首次调用（本次为基线，再调一次即可看增长）"},
        "note": ("diff.growth = 与上次调用的增量（看谁在涨）；unaccounted_mb 大 ⇒ 大头不在"
                 "已知模块级缓存内。进程重启后 diff 会显示 reset（= 又 OOM 过一次）。"),
    }
