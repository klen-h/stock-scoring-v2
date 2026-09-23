# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】进程内存看护：采样持久化（跨重启）+ 定时巡检 + 阈值企微告警
================================================================================
背景（2026-09-23）：Render **500MB** 实例反复 OOM。监控图显示内存呈**锯齿状** ——
从 ~10% 缓慢爬升到 80~90% → 骤降（进程被杀 + 自动重启），48 小时内多次；CPU 长期 <5%
⇒ 不是算力不足，是内存；且形态是「几小时累积爬升」而非瞬时尖峰。

`GET /api/system/memory`（`routers/system.py`）能回答「谁在占内存 / 谁在涨」，但它的 diff
基准存在**进程内存**里 ⇒ **一 OOM 重启就清零，而那一刻恰恰最需要对比**（重启前的最后
状态 vs 重启后）。本模块补上这一点：

  · **采样落库**（`memory_probe` 表）⇒ diff 基准取**库里上一条** ⇒ **跨重启连续**
  · **定时巡检**：每 `SAMPLE_INTERVAL_MIN` 分钟采一条；前端调 `/memory` 也会顺带落一条，
    **带 5 分钟去抖**（多标签页 / 频繁刷新不会把表写爆）
  · **阈值告警**：RSS ≥ `ALERT_PCT%` 上限 ⇒ 推企微，内容含「较上次采样增长 Top N」
    （＝直接给出排查方向）；**每自然日最多一次**（复用 `flash.store` 的日程标记，
    跨部署去重）—— 噪音控制是硬要求，本项目已多次为此设闸
  · **保留期** `KEEP_DAYS` 天（每次成功采样后顺带清理）

与 `data_files.py` 的分工：那个盯「文件型数据的完整性」，这个盯「进程内存」，同为运维
哨兵、互不依赖（挂载点相邻：`main.py` 的 lifespan）。
================================================================================
"""

import asyncio
import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.database import db

_TABLE = "memory_probe"
_TABLE_READY = False

SAMPLE_INTERVAL_MIN = 30       # 定时巡检间隔（分钟）
ALERT_PCT = 80                 # RSS 占上限百分比 ≥ 此值 ⇒ 企微告警
WARN_PCT = 70                  # ≥ 此值 ⇒ 只在采样里标 note（不惊动用户）
RECORD_DEBOUNCE_SEC = 300      # 两次落库的最小间隔（秒）—— 前端/多标签页去抖
KEEP_DAYS = 14                 # 采样保留天数
_ALERT_TASK = "memory_probe_alert"   # 「今日已告警」日程标记 key（跨部署去重）


def _ensure_table() -> None:
    """建表（幂等）。

    ★ 全列 **TEXT** —— SQLite / PostgreSQL 双库零风险（同 `data_inventory_state` 的做法）；
      且 `ts` 用 ISO 字符串 ⇒ 字典序即时间序，`ORDER BY ts` 两种库都对。
    """
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            ts TEXT PRIMARY KEY,
            rss_mb TEXT,
            peak_mb TEXT,
            used_pct TEXT,
            caches_mb TEXT,
            unaccounted_mb TEXT,
            threads TEXT,
            top_json TEXT,
            growth_json TEXT,
            note TEXT
        )
    """)
    _TABLE_READY = True


def _to_float(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt_key(k: str) -> str:
    """只取「模块.属性」两级（`tencent._cache` 而不是 `_cache`）—— 同名会混淆判读。"""
    return ".".join((k or "").split(".")[-2:])


def _row_out(r: Dict) -> Dict:
    """库里一行 → 结构化（数值回转 + JSON 解包）。"""
    def _j(k):
        try:
            return json.loads(r.get(k) or "null")
        except Exception:
            return None
    return {
        "ts": r.get("ts"),
        "rss_mb": _to_float(r.get("rss_mb")),
        "peak_mb": _to_float(r.get("peak_mb")),
        "used_pct": _to_float(r.get("used_pct")),
        "caches_mb": _to_float(r.get("caches_mb")),
        "unaccounted_mb": _to_float(r.get("unaccounted_mb")),
        "threads": int(_to_float(r.get("threads")) or 0),
        "top": _j("top_json") or [],
        "growth": _j("growth_json") or [],
        "note": r.get("note") or "",
    }


def latest(limit: int = 1) -> List[Dict]:
    """最近 limit 条采样（时间**倒序**）。失败返回 []（只读，失败不抛）。"""
    try:
        _ensure_table()
        rows = db.fetch(f"SELECT * FROM {_TABLE} ORDER BY ts DESC LIMIT %s",
                        (max(1, limit),))
        return [_row_out(r) for r in (rows or [])]
    except Exception as e:
        print(f"[memory_watch] 读取采样失败: {e}")
        return []


def history(limit: int = 48) -> List[Dict]:
    """最近 limit 条（时间**正序**，便于前端按时间画趋势）。"""
    return list(reversed(latest(limit)))


def record_sample(snap: Optional[Dict] = None, force: bool = False,
                  source: str = "timer") -> Optional[Dict]:
    """采集一次并落库（必要时告警）。返回本次采样 dict；被去抖/失败则返回 None。

    ★ diff 基准取自**库里上一条**而不是进程内存：跨 OOM 重启后进程内基准必然丢失
      （而那正是最需要对比的时刻），库里那条才是连续的。
    ★ 去抖：距上一条 < `RECORD_DEBOUNCE_SEC` 且非 force ⇒ 不写。前端每开一次首页都会
      调 `/memory`，多标签页会把它变成高频写。
    """
    from app.flash.rules import beijing_now
    now = beijing_now()
    prev_rows = latest(1)
    prev = prev_rows[0] if prev_rows else None

    if prev and not force:
        try:
            gap = (now - datetime.fromisoformat(prev["ts"])).total_seconds()
            if gap < RECORD_DEBOUNCE_SEC:
                return None
        except Exception:
            pass

    if snap is None:                       # 定时巡检路径：自己采集
        from app.routers.system import collect_memory_snapshot
        snap = collect_memory_snapshot()

    rss = snap.get("rss_mb")
    pct = snap.get("used_pct")
    caches = snap.get("caches") or []

    # 与上一条比（**跨重启**基准）—— 复用端点同一套实现，防两套口径漂移
    from app.routers.system import diff_caches
    prev_map = None
    if prev:
        prev_map = {c.get("key"): {"mb": c.get("mb"), "items": c.get("items")}
                    for c in (prev.get("top") or [])}
        # ⚠️ 库里只存了 top N（控制表体积）⇒ 基准覆盖不全时 diff 偏**保守**（漏报未入榜项）。
        #    这是刻意取舍：「谁在涨」几乎总在 top 里，而表体积要有上限。
    growth = diff_caches(caches, prev_map) if prev_map is not None else []

    top = [{"key": c["key"], "mb": c["mb"], "items": c["items"]}
           for c in caches if (c["mb"] or 0) >= 0.5][:8]

    note = ""
    if pct is not None and pct >= ALERT_PCT:
        note = "alert"
    elif pct is not None and pct >= WARN_PCT:
        note = "warn"

    # ★ 微秒精度：`ts` 是主键，秒级精度下"同一秒内多次采样"会互相**覆盖**（实测：快速
    #   连续 force 采样 5 次只剩 4 条）。真实场景（30 分钟定时 / 5 分钟去抖）几乎撞不上，
    #   但精度不该成为陷阱，且字符串排序仍与时间序一致。
    ts = now.isoformat(timespec="microseconds")
    try:
        _ensure_table()
        db.upsert(_TABLE, {
            "ts": ts,
            "rss_mb": str(rss), "peak_mb": str(snap.get("peak_mb")),
            "used_pct": str(pct), "caches_mb": str(snap.get("caches_total_mb")),
            "unaccounted_mb": str(snap.get("unaccounted_mb")),
            "threads": str(snap.get("threads")),
            "top_json": json.dumps(top, ensure_ascii=False),
            "growth_json": json.dumps(growth[:8], ensure_ascii=False),
            "note": note,
        }, conflict_columns=["ts"])
    except Exception as e:
        print(f"[memory_watch] 写入采样失败: {e}")
        return None

    rec = {"ts": ts, "rss_mb": rss, "used_pct": pct, "caches_mb":
           snap.get("caches_total_mb"), "growth": growth, "note": note,
           "source": source}
    if note == "alert":
        # ★ 告警分支**整体包住**：看护模块绝不允许反噬主流程。实测教训（2026-09-23）：
        #   该分支里的 `print()` 含 emoji，在 **GBK 控制台**（中文 Windows）会抛
        #   UnicodeEncodeError ⇒ 而"今日已告警"标记**已经占用** ⇒ 异常中断后当天不再
        #   重试 = **静默丢告警**。故：① 关键路径 print 一律 ASCII；② 此处再加兜底。
        try:
            _maybe_alert(rec, snap, prev)
        except Exception as e:
            print(f"[memory_watch] 告警流程异常（不影响采样与主流程）: {e}")
    cleanup()
    return rec


def _maybe_alert(rec: Dict, snap: Dict, prev: Optional[Dict]) -> None:
    """RSS 超阈值 ⇒ 企微告警（**每自然日一次**，跨部署去重）。

    ★ 为什么必须先占标记再推送：`is_schedule_done` 按**北京日期**比对 ⇒ 天然"每日一次"；
      先标记可避免推送异常时反复重试刷屏（宁可漏一条，不可变成噪音源）。
    """
    try:
        from app.flash import store
        if store.is_schedule_done(_ALERT_TASK):
            return
        store.mark_schedule_done(_ALERT_TASK)
    except Exception as e:
        print(f"[memory_watch] 告警频控失败（按已告警处理，不重复推）: {e}")
        return

    try:
        from app.routers.system import _MEM_LIMIT_MB
        limit = _MEM_LIMIT_MB
    except Exception:
        limit = 500
    rss, pct = rec.get("rss_mb"), rec.get("used_pct")
    growth = rec.get("growth") or []

    lines = [f"> **实例：** {os.environ.get('RENDER_INSTANCE_ID', 'local')}",
             f"> **时间：** {str(rec.get('ts'))[:16].replace('T', ' ')}", ""]
    lines.append(f"**进程内存 {rss}MB / {limit}MB（{pct}%）**"
                 + (f"，峰值 {snap.get('peak_mb')}MB" if snap.get("peak_mb") else ""))
    if prev and prev.get("ts"):
        lines.append(f"较上次采样（{str(prev['ts'])[:16].replace('T', ' ')}）"
                     f" RSS {round((rss or 0) - (prev.get('rss_mb') or 0), 1):+}MB")
    if growth:
        lines += ["", "**增长最多**"]
        lines += [f"- {_fmt_key(g['key'])} {g['delta_mb']:+.1f}MB"
                  + (f"（{g['items_delta']:+d}条）" if g.get("items_delta") else "")
                  for g in growth[:4]]
    else:
        lines.append("本次采样未见明细增长（可能是请求级峰值 / RSS 高水位）")
    big = [c for c in (snap.get("caches") or []) if (c.get("mb") or 0) >= 1][:4]
    if big:
        lines += ["", "**当前占用最多**：" + "、".join(
            f"{_fmt_key(c['key'])} {c['mb']}MB" for c in big)]
    lines += ["", f"> 阈值 {ALERT_PCT}%（上限 {limit}MB）。持续增长可能触发 OOM 重启；"
                  f"本告警每日最多一次。趋势见首页「数据新鲜度」卡片或 `/api/system/memory`。"]
    body = "\n".join(lines)
    # ★ 日志用 ASCII 标记（不用 emoji）：Windows 本地是 GBK 控制台，emoji 会抛
    #   UnicodeEncodeError（推送**内容**里的 emoji 无此问题 —— 那是走 HTTP 的 UTF-8 正文）。
    print(f"[memory_watch] [WARN] 内存告警（RSS {rss}MB / {pct}%）")
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched("⚠️ 进程内存偏高", body, force=True, category="alert")
    except Exception as e:
        print(f"[memory_watch] 企微推送失败: {e}")


def cleanup(keep_days: int = KEEP_DAYS) -> int:
    """删除 keep_days 天前的采样（返回删除行数）。失败静默（看护模块不得反噬主流程）。"""
    try:
        _ensure_table()
        cutoff = (datetime.now() - timedelta(days=keep_days)).isoformat(timespec="seconds")
        return db.execute(f"DELETE FROM {_TABLE} WHERE ts < %s", (cutoff,))
    except Exception as e:
        print(f"[memory_watch] 清理旧采样失败: {e}")
        return 0


async def periodic_loop(interval_min: int = SAMPLE_INTERVAL_MIN) -> None:
    """启动即采一条，之后每 interval_min 分钟一条（force=True 绕过去抖）。

    ★ 与 `data_files.periodic_loop` 同款：`asyncio.to_thread` 包住同步 DB/采集逻辑，
      避免阻塞事件循环（项目硬规矩：没有 await 的重活不占事件循环）。
    """
    while True:
        try:
            await asyncio.to_thread(record_sample, None, True, "timer")
        except Exception as e:
            print(f"[memory_watch] 巡检失败: {e}")
        await asyncio.sleep(max(1, interval_min) * 60)
