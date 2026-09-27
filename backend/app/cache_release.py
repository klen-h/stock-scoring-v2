# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】进程内存「主动释放」—— 把按需加载的大缓存还回去，避免 OOM 被杀重启
================================================================================
背景（2026-09-28 定位；用户："盘后是不是可以将一些不必要的缓存释放掉"）：
  Render **500MB** 实例反复 OOM。`memory_probe` 跨重启采样把形态钉死了 ——
    · 09-24 09:35  RSS 159MB（已知缓存 18MB）
    · 09-24 09:44  RSS 384MB（已知缓存 **358MB**）← 10 分钟内跳升
    · 09-24 13:35  RSS 409MB（81.8%）⇒ **企微告警**
    · 09-24 19:05  RSS 420MB（83.9%，当日峰值）
    · 09-24 19:31  RSS 105MB  ← **被杀重启**（缓存清零）
    · 09-24 20:01  RSS 367MB  ← **半小时又填满**
  ⇒ ① 342MB 全在**已知模块级缓存**里（不是框架/请求对象 ⇒ 不是"RSS 高水位"那种无解问题）；
     ② 清空后能被**迅速重新填满** ⇒ 光调上限不够，必须有**主动释放**的兜底。

★ 为什么是「主动释放」而不只是把上限调小：
  两个元凶缓存的**单条**就极大（回测价格实测 ≈1.2MB/条、资金流整表读 ≈110MB/次），
  而它们原先只有**条数**上限（200 条 / 6 条）—— 在字节意义上等于没上限（实测 232MB）。
  另两处改动已补「按 bar 数 / 按行数」的字节级兜底；本模块再给内存一个**确定的下降点**：
  本项目里 OOM 重启才是最大代价（丢全部缓存 + 重新下载解压 126MB SQLite +
  历史事故里会演成"启动→OOM→重启"的崩溃循环）。

【与 `memory_watch.py` 的分工】—— 一个观测、一个干预，口径共用
  · `memory_watch`：**只读观测**（采样落库 `memory_probe`、阈值推企微），从不干预；
  · 本模块：**动手**（释放），且复用它的同一套快照实现
    （`routers.system.collect_memory_snapshot`）⇒ 前后对比与"占用最多"口径不漂。
  两者都挂在 `main.py` 的 lifespan，且**都不受 READ_ONLY 约束** —— 只读模式下进程
  只服务 API、缓存全靠用户访问触发，恰恰是最容易"悄悄攒满"的场景（两次告警都在那里）。

【释放什么、绝不释放什么】
  ✅ 释放（判据：**大 + 重建不烧外部流量 + 不在盘后关键路径上**）
     · 回测价格 `backtest.strategies/_PRICES_CACHE`、`backtest.data/_PRICES_CACHE`
       —— 重建走 `pack_source` 本机 SQLite；且回测是低频手动操作
     · 资金流整表 map `mainforce.flow/_FLOW_MAP_CACHE` —— 有**本机 SQLite 跨进程缓存**
       兜底 ⇒ 重建**零 Supabase 流量**（本项目最贵的资源，已核实）
     · 主力行为全池状态、K 线缓存、回测结果 / 实时榜单缓存
       —— 都是"重算即可得出、无副作用"的派生数据
  ❌ **绝不释放**（显式留档，防止后人"顺手全清"酿成事故）：
     · `tencent._cache`（全市场实时行情）—— 盘后它是**收盘定稿快照**，是
       `event_alert_loop` / `record_event_snapshot` / 日报等多处的**唯一今日行情来源**；
       本项目已有"盘后重启后该缓存为空的窗口内多处静默降级"的历史
     · `flash.*` 快讯 / 推送去重 / 频控 —— 清掉会**重复推送**（噪音，本项目硬红线）
     · `flash.wechat._token_cache`（清掉多一次取 token）、`signal_bus` 环形缓冲（清掉=丢时间线）
     · `sync_meta` / `_CODE_TO_PREFIX` 等**元数据** —— 重建要网络，而体积很小
================================================================================
"""

import asyncio
import gc
import sys
import time
from typing import Dict, List, Optional

# 盘后释放窗口（北京时间，当日分钟数）。19:00 起：
#   · 已过收盘 + 15:40 regime 判定 + 16:00 事件推送；· 早于次日开盘，释放后无人在用
RELEASE_WINDOW = (1140, 1440)
# 自适应兜底阈值：RSS 占上限 ≥ 此值 ⇒ **不等窗口**立刻释放（这才是真正防 OOM 的那条）
ADAPTIVE_PCT = 75
# 最小释放间隔（分钟）：防止"释放→立刻被重新填满→再释放"的抖动（缓存重建有成本）
MIN_GAP_MIN = 120
# 巡检间隔（秒）
CHECK_INTERVAL_SEC = 600
# 「今日已释放」日程标记 key（复用 flash.store，跨部署去重）
_TASK_KEY = "cache_release"

# 释放清单：(模块路径, 属性名, 说明)。**新增模块级大缓存时补一行**。
# 判据见模块头；⚠️ 不要往里加「行情/快讯/元数据」类（理由同上）。
_RELEASABLE = [
    ("app.backtest.strategies", "_PRICES_CACHE", "回测价格缓存（策略侧）"),
    ("app.backtest.data", "_PRICES_CACHE", "回测价格缓存"),
    ("app.mainforce.flow", "_FLOW_MAP_CACHE", "资金流整表 map"),
    ("app.mainforce.flow", "_FS_CACHE", "资金流分"),
    ("app.mainforce.state", "_latest_cache", "主力行为状态（全池）"),
    ("app.tencent", "KLINE_CACHE", "K线缓存"),
    ("app.routers.scoring", "_bt_cache", "回测结果缓存"),
    ("app.scoring.live_ranking", "_load_cache", "实时榜单"),
]

# 进程内：上次释放时刻（只用于 MIN_GAP 频控；重启即清零 = 允许立刻释放）
_last_release_ts = 0.0


def _clear_one(mod_path: str, attr: str) -> bool:
    """清空一个模块级缓存容器（dict/list/set）。

    ★ **只碰已加载的模块**（`sys.modules.get`，不 import）—— 与 `routers/system.py`
      的探针同一条原则：不主动 import ⇒ 零副作用，且未加载的模块本来就不占内存。
    """
    mod = sys.modules.get(mod_path)
    if mod is None:
        return False
    c = getattr(mod, attr, None)
    if c is None:
        return False
    try:
        if isinstance(c, (dict, list, set)):
            c.clear()
            return True
    except Exception:
        pass
    return False


def _rss_pct() -> Optional[float]:
    """当前 RSS 占上限百分比（失败返回 None —— 绝不因此报错）。"""
    try:
        from app.routers.system import collect_memory_snapshot
        return (collect_memory_snapshot() or {}).get("used_pct")
    except Exception:
        return None


def release(reason: str = "manual", min_gap_min: int = 0) -> Dict:
    """释放清单里的全部大缓存，返回**释放前后对比**。

    ★ 全程不抛异常（干预模块不得反噬主流程；调用方可能是定时任务或 HTTP 端点）。
    ★ `min_gap_min`：距上次释放不足该分钟数则跳过（返回 `skipped`）—— 防抖动。
    """
    global _last_release_ts
    now = time.time()
    if min_gap_min and _last_release_ts and (now - _last_release_ts) < min_gap_min * 60:
        return {"skipped": "距上次释放不足 %d 分钟" % min_gap_min,
                "last_release": _last_release_ts}

    # 释放前快照（用来看"清掉了多少"；与 /memory 端点、看护模块同一实现）
    before = None
    try:
        from app.routers.system import collect_memory_snapshot
        before = collect_memory_snapshot()
    except Exception:
        before = None
    before_map = {c.get("key"): c for c in ((before or {}).get("caches") or [])}

    cleared: List[Dict] = []
    for mod_path, attr, note in _RELEASABLE:
        full = mod_path + "." + attr
        prev = before_map.get(full) or {}
        if _clear_one(mod_path, attr):
            cleared.append({"key": full, "note": note,
                            "mb": prev.get("mb"), "items": prev.get("items")})

    try:
        gc.collect()          # 容器清空后对象才真正回收；这一步决定 RSS 是否真降
    except Exception:
        pass

    after = None
    try:
        from app.routers.system import collect_memory_snapshot
        after = collect_memory_snapshot()
    except Exception:
        after = None

    rss_b = (before or {}).get("rss_mb")
    rss_a = (after or {}).get("rss_mb")
    freed = round(rss_b - rss_a, 1) if (rss_b is not None and rss_a is not None) else None
    _last_release_ts = now

    out = {
        "reason": reason,
        "rss_before_mb": rss_b, "rss_after_mb": rss_a, "freed_mb": freed,
        "caches_mb_before": (before or {}).get("caches_total_mb"),
        "caches_mb_after": (after or {}).get("caches_total_mb"),
        "cleared": cleared,
        "cleared_count": len(cleared),
        # 诚实标注：RSS 是**采样外推**（容器估算，±），且 RSS 不会立刻归还 OS
        #   ⇒ `freed_mb` 可能明显小于 `caches_mb_before - caches_mb_after`，两者都给出。
        "note": ("freed_mb 是 RSS 前后差（RSS 为采样估算且高水位不一定立刻归还 OS）；"
                 "caches_* 是已知模块级缓存合计（更接近真实释放量）。"),
    }
    # ★ 日志一律 ASCII：Windows GBK 控制台遇 emoji/部分字符会抛 UnicodeEncodeError
    #   （项目已有"关键路径 print 崩掉导致静默丢告警"的实测教训）
    print("[cache_release] released %d caches (%s), RSS %s -> %s MB"
          % (len(cleared), reason, rss_b, rss_a))
    return out


def _tick() -> None:
    """一次巡检：① 盘后每日一次；② RSS 超阈值不等窗口（受 MIN_GAP 频控）。"""
    from app.flash import rules, store
    now = rules.beijing_now()
    t = now.hour * 60 + now.minute

    # ① 盘后窗口：**每日一次**（`schedule_state` 按北京日期记账 ⇒ 跨部署/多次部署都只做一次）
    if RELEASE_WINDOW[0] <= t < RELEASE_WINDOW[1] and not store.is_schedule_done(_TASK_KEY):
        res = release("post-close")
        store.mark_schedule_done(_TASK_KEY)
        print("[cache_release] post-close done: freed=%s MB, caches %s -> %s MB"
              % (res.get("freed_mb"), res.get("caches_mb_before"),
                 res.get("caches_mb_after")))
        return

    # ② 自适应兜底：内存已逼近告警线（80%）⇒ 不等窗口。**这才是真正防住 OOM 的那条** ——
    #    实测 09-24 是 09:44 就冲到 358MB，而盘后窗口要到 19:00 才动手。
    pct = _rss_pct()
    if pct is not None and pct >= ADAPTIVE_PCT:
        res = release("adaptive(%.1f pct)" % pct, min_gap_min=MIN_GAP_MIN)
        if res.get("skipped"):
            return
        print("[cache_release] adaptive done: was %.1f%%, freed=%s MB"
              % (pct, res.get("freed_mb")))


async def periodic_loop() -> None:
    """每 CHECK_INTERVAL_SEC 一次巡检（与 `memory_watch.periodic_loop` 同款结构）。

    ★ `asyncio.to_thread` 包住同步的 DB / 快照逻辑 —— 项目硬规矩：没有 await 的重活
      不占事件循环。
    """
    while True:
        try:
            await asyncio.to_thread(_tick)
        except Exception as e:
            print("[cache_release] tick error: %s" % e)
        await asyncio.sleep(max(60, CHECK_INTERVAL_SEC))
