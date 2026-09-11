"""
================================================================================
【文件作用】运行时「文件型数据」清单 + 完整性检查（丢了要预警，不再静默蒸发）
================================================================================
背景（2026-09-12）：
  为了对抗 Render 免费实例"每次部署清空文件系统"，历史上做了「浏览器镜像」兜底：
  前端每 5 分钟把 backend/data/*.json 拉到 localStorage，服务端清零后自动回传。

  但数据这些年陆续迁进了数据库，镜像清单里那 9 个文件**全部变成空壳/遗留文件**：
    flash / analyses / reviews / tracking / macro_history / etf_close /
    flash_state / schedule_state / strategies
  —— 实际的读写都在 DB 表（flash_analyses / flash_reviews / tracking_state /
     macro_history / etf_close / flash_state / schedule_state …）。
  于是镜像只剩代价（每次为了取 len() 把 50 条诊断正文读出来，实测 40MB/天
  Supabase egress），却不再保护任何东西 → 前端轮询退役（frontend/src/App.vue）。

  退役之后，**真正还留在文件里的数据必须有人盯着**，否则丢了没人知道：
    calendar.json     财经日历（金十）—— 丢了要重新抓；cookie 失效就一直空
    llm_usage.json    LLM 每日用量/日熔断基线 —— 丢了当日限额保护失效、30 天历史不可恢复
    kline_cache.json  K线磁盘缓存 —— 丢了重启会重拉全量 K 线（撞腾讯 WAF 的历史元凶）
    data/pack/*.db    后端数据包 —— 丢了要重新下 127MB

  本模块把它们清单化，并在**进程启动时 + 每 6 小时**核对一遍：
    · 正常的项：把"最后一次见到"写进 DB 水位表 data_inventory_state
    · 关键项（丢失不可自愈）且水位显示"最近还见过" → 企微告警（force，走失败提醒通道）
      告警按 key 每天最多一次，避免频繁部署时刷屏
    · 其余项只记状态（状态页 /api/system/runtime-files 可查）—— 故意不报警：
      Render 容器新建时这些文件本来就是空的，见一次报一次等于噪音

对外函数：
  check_all()       -> {items:[...], missing_critical:[...], checked_at}
  startup_check()   -> 核对 + 更新水位 + 必要时告警
  periodic_loop()   -> 启动即查一次，之后每 6 小时一次（asyncio 任务）
================================================================================
"""

import asyncio
import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

from app.database import db

_APP_DIR = os.path.dirname(os.path.abspath(__file__))          # backend/app
_BACKEND_DIR = os.path.dirname(_APP_DIR)                       # backend
_DATA_DIR = os.path.join(_BACKEND_DIR, "data")

# 水位表：记录每个文件型数据"最后一次见到"的时间（跨部署保留在 DB 里）
_WM_READY = False
_WM_CACHE = {}
_SEEN_RECENT_DAYS = 7          # 水位在 7 天内 = "最近还见过"，此时丢失才值得告警
_PERIODIC_HOURS = 6


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
    """水位表读入内存（一次）。失败返回空 dict（退化为"没见过"，不误报）。"""
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
    """记录"此刻见到该文件"（失败不影响主流程）。

    ★ 同步更新内存缓存：只 pop 不写会让 _wm_load() 返回"已加载但缺 key"的旧字典，
      之后 _was_seen_recently() 永远读到 None → 丢失告警静默失效。
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
    """宽容解析时间字符串 → 本地 naive datetime（带时区的统一换算，避免
    naive - aware 抛 TypeError；日历/水位的字段就踩过这个坑）。失败返回 None。"""
    try:
        d = datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        return None
    if d.tzinfo is not None:
        d = d.astimezone().replace(tzinfo=None)
    return d


def _was_seen_recently(key: str) -> bool:
    wm = _wm_load().get(key)
    if not wm or not wm.get("last_seen"):
        return False
    seen = _parse_dt(wm["last_seen"])
    if seen is None:
        return False
    return datetime.now() - seen <= timedelta(days=_SEEN_RECENT_DAYS)


# ── 清单 ─────────────────────────────────────────────────────────────────────
def _specs() -> list:
    """文件型数据清单。critical=True 表示"丢失不可自愈"（才值得告警）。"""
    pack_on = (os.environ.get("DATA_SOURCE", "db") or "db").strip().lower() in ("pack", "local")
    return [
        {"key": "calendar", "label": "财经日历（金十）", "kind": "calendar",
         "path": os.path.join(_DATA_DIR, "calendar.json"), "critical": False,
         "why": "丢了重新抓取；若 FLASH_COOKIE 失效会长期为空"},
        {"key": "llm_usage", "label": "LLM 用量/日熔断基线", "kind": "llm_usage",
         "path": os.path.join(_DATA_DIR, "llm_usage.json"), "critical": True,
         "why": "当日调用计数归零 → 限额保护失效；30 天用量历史不可恢复"},
        {"key": "kline_cache", "label": "K线磁盘缓存", "kind": "json_size",
         "path": os.path.join(_BACKEND_DIR, "kline_cache.json"), "critical": False,
         "why": "丢了重启会重拉全量 K 线（撞腾讯 WAF 的老风险）；pack 模式下可重建"},
        {"key": "pack", "label": "后端数据包", "kind": "pack",
         "path": os.path.join(_DATA_DIR, "pack", "backend-pack.db"), "critical": False,
         "skip": (not pack_on), "why": "丢了要重新下载 127MB（GitHub Pages）"},
    ]


def _check_one(spec: dict) -> dict:
    key, path = spec["key"], spec["path"]
    item = {"key": key, "label": spec["label"], "critical": bool(spec["critical"]),
            "why": spec.get("why", ""), "path": os.path.relpath(path, _BACKEND_DIR),
            "exists": os.path.exists(path), "ok": True, "detail": "",
            "last_seen": (_wm_load().get(key) or {}).get("last_seen")}

    if spec.get("skip"):
        item["detail"] = "当前模式不适用"
        return item

    if not item["exists"]:
        item["ok"] = False
        item["detail"] = "文件不存在"
        item["lost"] = _was_seen_recently(key)      # 最近还见过 → 这次是真丢了
        return item

    try:
        size = os.path.getsize(path)
        item["size"] = size
        item["mtime"] = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M")
    except OSError as e:
        item["ok"] = False
        item["detail"] = f"读取失败: {e}"
        item["lost"] = _was_seen_recently(key)
        return item

    kind = spec["kind"]
    try:
        if kind == "calendar":
            data = _read_json(path) or {}
            items = data.get("items") or []
            item["ok"] = bool(items)
            item["detail"] = f"{len(items)} 条，更新于 {data.get('updated_at') or '未知'}"
            age = _parse_dt(data.get("updated_at"))
            if items and age and datetime.now() - age > timedelta(days=30):
                item["detail"] += f"（已 {(datetime.now() - age).days} 天未更新，可能抓取失效）"
        elif kind == "llm_usage":
            data = _read_json(path) or {}
            daily = data.get("daily") or {}
            item["ok"] = bool(daily)
            today = datetime.now().strftime("%Y/%m/%d")
            item["detail"] = (f"{len(daily)} 天用量，今日 "
                              f"{(daily.get(today) or {}).get('calls', 0)} 次"
                              if daily else "无用量记录")
        elif kind == "json_size":
            # 只按体积判断：kline_cache.json 可达数十 MB，启动时不应整份解析
            item["ok"] = size > 0
            item["detail"] = f"{round(size / 1048576, 1)}MB" + ("" if size else "（空文件）")
        elif kind == "pack":
            item["ok"] = size > 10 * 1048576
            item["detail"] = _pack_detail(path, size)
    except Exception as e:
        # 单项检查异常绝不拖垮整体（这个模块本身就是"兜底哨兵"）
        item["ok"] = False
        item["detail"] = f"检查异常: {type(e).__name__}: {e}"

    if not item["ok"]:
        item["lost"] = _was_seen_recently(key)
    return item


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[data_files] {os.path.basename(path)} 解析失败: {e}")
        return None


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
    """核对全部文件型数据。返回 {items, missing_critical, checked_at}。"""
    items = [_check_one(s) for s in _specs()]
    return {
        "items": items,
        "missing_critical": [i["key"] for i in items if i["critical"] and not i["ok"]],
        "lost": [i["key"] for i in items if i.get("lost")],
        "checked_at": datetime.now().isoformat(),
    }


# ── 告警 ─────────────────────────────────────────────────────────────────────
def _alert_once_per_day(key: str) -> bool:
    """同一 key 当天是否已告警过（跨部署去重，避免频繁部署刷屏）。"""
    try:
        from app.flash import store
        task = f"data_loss_{key}"
        if store.is_schedule_done(task):
            return False
        store.mark_schedule_done(task)
        return True
    except Exception:
        return True


def _notify_lost(lost_items: list) -> None:
    lines = [f"> **实例：** {os.environ.get('RENDER_INSTANCE_ID', 'local')}",
             f"> **时间：** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
             "", "| 数据 | 说明 | 影响 |", "|---|---|---|"]
    for it in lost_items:
        lines.append(f"| {it['label']} | {it['path']} 缺失/为空 | {it.get('why') or '—'} |")
    lines += ["", "> 文件系统被清空（部署/重启）或数据被误删；"
                  "可自愈项会在下次调度自动重建，`llm_usage` 的历史不可恢复。"]
    body = "\n".join(lines)
    print(f"[data_files] ⚠️ 文件型数据丢失: {[i['key'] for i in lost_items]}")
    try:
        from app.flash.wechat import push_markdown_batched
        push_markdown_batched("⚠️ 文件型数据丢失", body, force=True)
    except Exception as e:
        print(f"[data_files] 告警推送失败: {e}")


def startup_check() -> dict:
    """启动核对：更新水位；关键项"最近见过但现在没了"→ 告警（每 key 每天一次）。"""
    res = check_all()
    lost_alerts = []
    for it in res["items"]:
        if it.get("detail") == "当前模式不适用":
            continue
        if it["ok"]:
            _wm_touch(it["key"], it.get("detail", ""))
        elif it["critical"] and it.get("lost") and _alert_once_per_day(it["key"]):
            lost_alerts.append(it)
    res["alerts"] = [i["key"] for i in lost_alerts]
    for it in res["items"]:
        flag = "OK " if it["ok"] else ("LOST" if it.get("lost") else "缺失")
        print(f"[data_files] {flag} {it['label']}: {it.get('detail') or ''}")
    if lost_alerts:
        _notify_lost(lost_alerts)
    return res


async def periodic_loop(interval_hours: int = _PERIODIC_HOURS) -> None:
    """启动即查一次，之后每 interval_hours 一次（轻量：只看文件元数据/小 JSON）。"""
    while True:
        try:
            await asyncio.to_thread(startup_check)
        except Exception as e:
            print(f"[data_files] 周期检查失败: {e}")
        await asyncio.sleep(max(0.5, interval_hours) * 3600)
