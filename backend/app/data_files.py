"""
================================================================================
【文件作用】运行时数据清单 + 完整性检查（丢了/烂了要预警，不再静默蒸发）
================================================================================
背景（2026-09-12）：
  为了对抗 Render 免费实例"每次部署清空文件系统"，历史上做了「浏览器镜像」兜底：
  前端每 5 分钟把 backend/data/*.json 拉到 localStorage，服务端清零后自动回传。
  但数据这些年陆续迁进了数据库，镜像清单里那 9 个文件**全部变成空壳**（flash /
  analyses / reviews / tracking / macro_history / etf_close / flash_state /
  schedule_state / strategies）——于是镜像只剩代价（每次为了取 len() 把 50 条
  诊断正文读出来，实测 40MB/天 Supabase egress），不产生任何保护 → 已退役。

  本模块接管"盯着点"的职责，清单按**数据真实存储位置**划分（2026-09-12 二次修订）：
    · 财经日历（金十）    → DB 表 flash_calendar（同日迁库，文件仅留副本）
    · LLM 用量/日熔断基线 → DB 表 llm_usage_daily（同日迁库，文件仅作兜底）
    · K线磁盘缓存         → 文件 backend/kline_cache.json（可重建，提示级）
    · 后端数据包          → 文件 data/pack/backend-pack.db（可重下，提示级）

  检查时机：进程启动 + 每 6 小时。不健康且标记 critical 的项 → 企微告警
  （force 通道；同一项每天最多一次，避免频繁部署刷屏）。
  当前唯一 critical 项是**财经日历长期未更新/为空**——2026-09-12 实测它就停在
  09-04（`calendar_loop` 在只读模式被关、日批又漏配），正是需要被喊出来的那类问题。

对外函数：
  check_all()       -> {items:[...], unhealthy:[...], checked_at}
  startup_check()   -> 核对 + 更新水位 + 必要时告警
  periodic_loop()   -> 启动即查一次，之后每 6 小时一次（asyncio 任务）
================================================================================
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta

from app.database import db

_APP_DIR = os.path.dirname(os.path.abspath(__file__))          # backend/app
_BACKEND_DIR = os.path.dirname(_APP_DIR)                       # backend
_DATA_DIR = os.path.join(_BACKEND_DIR, "data")

_WM_READY = False
_WM_CACHE = {}
_PERIODIC_HOURS = 6
_CALENDAR_STALE_DAYS = 14      # 日历超过这么久没更新 = 抓取链路断了


def _ensure_wm():
    global _WM_READY
    if _WM_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS data_inventory_state (
            key TEXT PRIMARY KEY,
            last_seen TEXT NOT NULL,
            detail TEXT
        )
    """)
    _WM_READY = True


def _wm_load() -> dict:
    global _WM_CACHE
    if _WM_CACHE:
        return _WM_CACHE
    try:
        _ensure_wm()
        rows = db.fetch("SELECT key, last_seen, detail FROM data_inventory_state")
        _WM_CACHE = {r["key"]: r for r in (rows or [])}
    except Exception as e:
        print(f"[data_files] 水位读取失败（按未见过处理）: {e}")
        _WM_CACHE = {}
    return _WM_CACHE


def _wm_touch(key: str, detail: str = "") -> None:
    """记录"此刻该数据是健康的"（失败不影响主流程）。

    ★ 必须同步更新内存缓存：只写库不更新缓存，会让同一进程内后续读取拿到旧值。
    """
    seen = datetime.now().isoformat()
    try:
        _ensure_wm()
        db.upsert("data_inventory_state",
                  {"key": key, "last_seen": seen, "detail": (detail or "")[:200]},
                  conflict_columns=["key"])
        _WM_CACHE[key] = {"key": key, "last_seen": seen, "detail": detail}
    except Exception as e:
        print(f"[data_files] 水位写入失败 {key}: {e}")


def _parse_dt(s):
    """宽容解析时间 → 本地 naive datetime（带时区的换算，避免 naive-aware 相减报错）。"""
    try:
        d = datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return d


# ── 清单（storage 决定去哪个存储检查）─────────────────────────────────────────
def _specs() -> list:
    pack_on = (os.environ.get("DATA_SOURCE", "db") or "db").strip().lower() in ("pack", "local")
    return [
        {"key": "calendar", "label": "财经日历（金十）", "storage": "db_calendar",
         "critical": True,
         "why": "LLM 复盘的「事件排期」输入 + 前端日历页；长期不更新=抓取链路断了"},
        {"key": "llm_usage", "label": "LLM 用量/日熔断基线", "storage": "db_llm_usage",
         "critical": False,
         "why": "日熔断计数来源；已迁库，部署不再清零"},
        {"key": "kline_cache", "label": "K线磁盘缓存", "storage": "file_json_size",
         "path": os.path.join(_BACKEND_DIR, "kline_cache.json"), "critical": False,
         "why": "丢了重启会重拉全量 K 线（撞腾讯 WAF 的老风险）；pack 模式下可重建"},
        {"key": "pack", "label": "后端数据包", "storage": "file_pack",
         "path": os.path.join(_DATA_DIR, "pack", "backend-pack.db"),
         "critical": False, "skip": (not pack_on),
         "why": "丢了要重新下载 127MB（GitHub Pages）"},
    ]


def _check_db_calendar(item: dict) -> None:
    """财经日历：看 DB 行（items 数 + updated_at 是否新鲜）。"""
    try:
        row = db.fetch_one("SELECT updated_at, items_json FROM flash_calendar "
                           "WHERE key = 'latest'")
    except Exception as e:
        item["ok"] = False
        item["detail"] = f"读取失败: {type(e).__name__}"
        return
    if not row:
        item["ok"] = False
        item["detail"] = "库中无日历缓存"
        return
    try:
        n = len(json.loads(row.get("items_json") or "[]"))
    except (ValueError, TypeError):
        n = 0
    updated = row.get("updated_at") or ""
    item["ok"] = n > 0
    item["detail"] = f"{n} 条，更新于 {updated[:16] or '未知'}"
    age = _parse_dt(updated)
    if age and datetime.now() - age > timedelta(days=_CALENDAR_STALE_DAYS):
        item["ok"] = False
        item["detail"] += f"（已 {(datetime.now() - age).days} 天未更新 → 抓取可能失效）"


def _check_db_llm_usage(item: dict) -> None:
    """LLM 用量：看 DB 最近 days 行数 + 今日调用次数（信息项，不告警）。"""
    try:
        rows = db.fetch("SELECT day, calls FROM llm_usage_daily "
                        "ORDER BY day DESC LIMIT 7")
    except Exception as e:
        item["ok"] = False
        item["detail"] = f"读取失败: {type(e).__name__}"
        return
    rows = rows or []
    today = datetime.now().strftime("%Y-%m-%d")
    today_calls = next((int(r.get("calls") or 0) for r in rows if r.get("day") == today), 0)
    item["ok"] = bool(rows)
    item["detail"] = f"库中 {len(rows)} 天（近 7 天），今日 {today_calls} 次"


def _check_one(spec: dict) -> dict:
    item = {"key": spec["key"], "label": spec["label"], "storage": spec["storage"],
            "critical": bool(spec["critical"]), "why": spec.get("why", ""),
            "ok": True, "detail": "",
            "last_seen": (_wm_load().get(spec["key"]) or {}).get("last_seen")}

    if spec.get("skip"):
        item["detail"] = "当前模式不适用"
        return item

    if spec["storage"] == "db_calendar":
        _check_db_calendar(item)
        return item
    if spec["storage"] == "db_llm_usage":
        _check_db_llm_usage(item)
        return item

    # ── 文件类 ──
    path = spec["path"]
    item["path"] = os.path.relpath(path, _BACKEND_DIR)
    try:
        if not os.path.exists(path):
            item["ok"] = False
            item["detail"] = "文件不存在"
            return item
        size = os.path.getsize(path)
        item["size"] = size
        item["mtime"] = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
        if spec["storage"] == "file_json_size":
            # 只按体积判断：kline_cache.json 可达数十 MB，启动时不应整份解析
            item["ok"] = size > 0
            item["detail"] = f"{round(size / 1048576, 1)}MB" + ("" if size else "（空文件）")
        elif spec["storage"] == "file_pack":
            item["ok"] = size > 10 * 1048576
            item["detail"] = _pack_detail(path, size)
    except Exception as e:
        item["ok"] = False
        item["detail"] = f"检查异常: {type(e).__name__}: {e}"
    return item


def _pack_detail(path: str, size: int) -> str:
    """数据包详情：日期（读 sqlite meta）+ 体积。读失败只报体积。"""
    base = f"{round(size / 1048576, 1)}MB"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = 'pack_date'").fetchone()
        finally:
            conn.close()
        return f"pack_date={row[0]}, {base}" if row else base
    except Exception:
        return base


def check_all() -> dict:
    """核对全部运行时数据。返回 {items, unhealthy, checked_at}。"""
    items = []
    for spec in _specs():
        try:
            items.append(_check_one(spec))
        except Exception as e:      # 单项异常绝不拖垮整体（本模块就是兜底哨兵）
            items.append({"key": spec["key"], "label": spec["label"],
                          "critical": bool(spec["critical"]), "ok": False,
                          "detail": f"检查异常: {type(e).__name__}: {e}"})
    return {"items": items,
            "unhealthy": [i["key"] for i in items if not i["ok"]],
            "checked_at": datetime.now().isoformat()}


# ── 告警 ─────────────────────────────────────────────────────────────────────
def _alert_once_per_day(key: str) -> bool:
    """同一项当天是否已告警过（跨部署去重，避免频繁部署刷屏）。"""
    try:
        from app.flash import store
        task = f"data_check_{key}"
        if store.is_schedule_done(task):
            return False
        store.mark_schedule_done(task)
        return True
    except Exception:
        return True


def _notify(items: list) -> None:
    lines = [f"> **实例：** {os.environ.get('RENDER_INSTANCE_ID', 'local')}",
             f"> **时间：** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             "", "| 数据 | 现状 | 影响 |", "|---|---|---|"]
    for it in items:
        lines.append(f"| {it['label']} | {it.get('detail') or '异常'} | {it.get('why') or '—'} |")
    lines.append("")
    lines.append("> 数据源：`/api/system/runtime-files`（启动检查 + 每 6 小时）")
    body = "\n".join(lines)
    print(f"[data_files] ⚠️ 运行时数据不健康: {[i['key'] for i in items]}")
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched("⚠️ 运行时数据检查异常", body, force=True)
    except Exception as e:
        print(f"[data_files] 告警推送失败: {e}")


def startup_check() -> dict:
    """启动核对：健康项更新水位；critical 且不健康 → 告警（每项每天最多一次）。"""
    res = check_all()
    to_alert = []
    for it in res["items"]:
        if it.get("detail") == "当前模式不适用":
            continue
        flag = "OK  " if it["ok"] else "BAD "
        print(f"[data_files] {flag}{it['label']}: {it.get('detail') or ''}")
        if it["ok"]:
            _wm_touch(it["key"], it.get("detail", ""))
        elif it["critical"] and _alert_once_per_day(it["key"]):
            to_alert.append(it)
    res["alerts"] = [i["key"] for i in to_alert]
    if to_alert:
        _notify(to_alert)
    return res


async def periodic_loop(interval_hours: int = _PERIODIC_HOURS) -> None:
    """启动即查一次，之后每 interval_hours 一次（轻量：只看元数据/小查询）。"""
    while True:
        try:
            await asyncio.to_thread(startup_check)
        except Exception as e:
            print(f"[data_files] 周期检查失败: {e}")
        await asyncio.sleep(max(0.5, interval_hours) * 3600)
