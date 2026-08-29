"""
================================================================================
【文件作用】金十财经日历数据源（独立于快讯 source.py）
================================================================================

接口：https://rili-open-api.jin10.com/data/week_info
鉴权：x-app-id: fiXF2nOnDycGutVA + x-version: 2.0（★ 无需 Cookie，与快讯不同）
      实测：缺 x-version 或 app-id 写错 → 网关返回 502（Gm-Error-Code: 81002），
      且响应体为空、不报业务错 —— 排查时容易误判成"接口挂了"。

★ 接口硬限制：单次请求的日期区间不得超过 7 天，超过则**返回 0 条且不报错**
  （实测 15 天/31 天区间均返回空数组）。因此长区间必须按 7 天分段请求后合并，
  已封装在 fetch_range() 里，调用方无需感知。

三类数据（type 字段），归一化成统一结构后供前端/LLM 消费：
  event   会议/讲话/事件：event_time, summary, event_content, star
  data    经济指标：pub_time, indicator_name, previous/consensus/actual（前值/预期/实际）
  holiday 交易所休市：date, exchange_name, rest_note（★ 无 star 字段）

与快讯 source.py 的关系（刻意不复用，故障域隔离）：
  - 不复用 Session：app-id 不同（fiXF2nOnDycGutVA vs bVBF4FyRTn5NJF5n）
  - 不复用 FLASH_COOKIE：日历是开放接口，不该随快讯 Cookie 过期一起挂掉
  - 不复用 get_new_items 游标：日历按日期窗口全量拉取，不需要增量游标
  - 复用：health 埋点模式、失败静默降级（但 key 用 jin10_calendar，告警不串台）

数据落地：data/calendar.json
  刻意不放进 store.PATHS —— 日历随时可从接口重拉，不占浏览器镜像备份的体积
  （PATHS 里的文件会被 /flash/backup 全量塞进 localStorage）。
================================================================================
"""

import os
import json
import requests

from app.flash import store

# ── 接口配置（可用环境变量覆盖，方便金十轮换域名/鉴权时救急）──
JIN10_CALENDAR_URL = os.environ.get(
    "JIN10_CALENDAR_URL", "https://rili-open-api.jin10.com/data/week_info")
_APP_ID = os.environ.get("JIN10_CALENDAR_APP_ID", "fiXF2nOnDycGutVA")
_VERSION = os.environ.get("JIN10_CALENDAR_VERSION", "2.0")

# 单次请求最大跨度（接口硬限制，超过返回 0 条）
_MAX_SPAN_DAYS = 7

# 缓存文件（backend/data/calendar.json）
CALENDAR_PATH = os.path.join(store.DATA_DIR, "calendar.json")

# 默认拉取窗口：昨天起 ~ 未来 14 天（覆盖"本周+下周"，也是接口数据较完整的范围）
DEFAULT_DAYS_BACK = 1
DEFAULT_DAYS_AHEAD = 14

# 独立 Session：不复用 source._session（app-id 不同且日历无需 Cookie）
_session = requests.Session()
_session.headers.update({
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-CN,zh;q=0.9",
    "user-agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"),
    "x-app-id": _APP_ID,
    "x-version": _VERSION,
})


# ================================================================
#  一、拉取（按周分段）
# ================================================================

def _fetch_week(start_date: str, end_date: str) -> list:
    """
    拉取单个区间（跨度必须 ≤7 天，否则接口静默返回空）。
    返回原始 data 数组；异常/空 → []。
    """
    try:
        r = _session.get(JIN10_CALENDAR_URL,
                         params={"start_date": start_date, "end_date": end_date,
                                 "include_holiday": "true"},
                         timeout=25)
        if r.status_code != 200:
            print(f"[calendar] 接口 HTTP {r.status_code}"
                  f"（Gm={r.headers.get('Gm-Error-Code')}）")
            return []
        rows = (r.json() or {}).get("data")
        return rows if isinstance(rows, list) else []
    except Exception as e:
        print(f"[calendar] 拉取失败 {start_date}~{end_date}: {e}")
        return []


def fetch_range(start_date: str, end_date: str) -> list:
    """
    拉取任意跨度区间：自动按 7 天分段请求并合并（接口单次上限 7 天）。
    返回原始条目列表。
    """
    from datetime import date, timedelta

    def _d(s):
        y, m, d = (int(x) for x in s.split("-"))
        return date(y, m, d)

    cur, end = _d(start_date), _d(end_date)
    out, seen = [], set()
    while cur <= end:
        seg_end = min(cur + timedelta(days=_MAX_SPAN_DAYS - 1), end)
        for item in _fetch_week(cur.isoformat(), seg_end.isoformat()):
            key = _item_key(item)
            if key and key not in seen:
                seen.add(key)
                out.append(item)
        cur = seg_end + timedelta(days=1)
    return out


def _item_key(item: dict) -> str:
    """跨段去重键：类型 + 业务 ID + 时间（同一事件可能落在分段边界被重复返回）。"""
    d = item.get("data") or {}
    kind = item.get("type") or ""
    _id = d.get("id") or d.get("data_id") or ""
    t = d.get("event_time") or d.get("pub_time") or d.get("date") or ""
    return f"{kind}:{_id}:{t}"


# ================================================================
#  二、归一化（三类异构 → 统一结构）
# ================================================================

def _norm_one(item: dict) -> dict:
    """
    单条归一化。统一字段：
      id / kind / date / time / country / star / title / summary
      content(event) / period,prev,consensus,actual,revised,unit(data)
      exchange,rest_note(holiday)
    """
    kind = item.get("type") or ""
    d = item.get("data") or {}
    base = {
        "id": str(d.get("id") or d.get("data_id") or ""),
        "kind": kind,
        "date": "",
        "time": "",
        "country": d.get("country") or "",
        "star": d.get("star"),          # holiday 无此字段 → None
        "title": "",
        "summary": d.get("summary") or "",
    }
    if kind == "event":
        t = d.get("event_time") or ""
        base.update({
            "date": t[:10], "time": t,
            "title": d.get("summary") or (d.get("event_content") or "")[:20],
            "content": d.get("event_content") or "",
        })
    elif kind == "data":
        t = d.get("pub_time") or d.get("actual_time") or ""
        base.update({
            "date": t[:10], "time": t,
            "title": d.get("indicator_name") or "",
            "period": d.get("time_period") or "",
            "prev": d.get("previous"),
            "consensus": d.get("consensus"),
            "actual": d.get("actual"),
            "revised": d.get("revised"),
            "unit": d.get("unit") or d.get("measure") or "",
        })
    elif kind == "holiday":
        t = d.get("date") or ""
        base.update({
            "date": t[:10], "time": t,
            "title": d.get("name") or "",
            "exchange": d.get("exchange_name") or "",
            "rest_note": d.get("rest_note") or "",
        })
    return base


def normalize(items: list) -> list:
    """批量归一化并按时间升序。"""
    out = [_norm_one(i) for i in items if (i.get("data") or {})]
    out.sort(key=lambda x: (x.get("time") or "", x.get("kind") or ""))
    return out


# ================================================================
#  三、缓存读写 + 刷新入口
# ================================================================

def _read_cache() -> dict:
    return store._load(CALENDAR_PATH, {}) or {}


def _write_cache(items: list, start: str, end: str) -> None:
    store._save(CALENDAR_PATH, {
        "updated_at": store._now_iso(),
        "range": {"start": start, "end": end},
        "items": items,
    })


def refresh(days_back: int = DEFAULT_DAYS_BACK,
            days_ahead: int = DEFAULT_DAYS_AHEAD) -> int:
    """
    拉取日历并落缓存，返回条数（失败返回 0，不影响调用方）。
    失败时保留旧缓存 —— 日历是低频静态数据，过期一天的数据远好过没有数据。
    """
    from datetime import timedelta
    from app.flash import rules
    from app import health

    today = rules.beijing_now().date()
    start = (today - timedelta(days=days_back)).isoformat()
    end = (today + timedelta(days=days_ahead)).isoformat()
    try:
        raw = fetch_range(start, end)
        items = normalize(raw)
        if items:
            _write_cache(items, start, end)
        # 空列表视为失败：正常区间（约 15 天）必有上百条，持续为空 ≈ 鉴权失效
        health.record("jin10_calendar", bool(items),
                      "" if items else "返回空列表（可能鉴权头失效或接口变更）")
        if items:
            print(f"[calendar] 已更新 {len(items)} 条（{start} ~ {end}）")
        return len(items)
    except Exception as e:
        print(f"[calendar] 刷新失败: {e}")
        health.record("jin10_calendar", False, str(e))
        return 0


def load() -> dict:
    """读取缓存（含 updated_at / range / items）。无缓存返回空结构。"""
    c = _read_cache()
    return c if isinstance(c, dict) and c.get("items") else {
        "updated_at": "", "range": {}, "items": []}


def get_items(refresh_if_empty: bool = True) -> list:
    """取归一化后的日历条目；缓存为空时现场拉一次（供 API/LLM 直接调用）。"""
    c = load()
    if not c["items"] and refresh_if_empty:
        refresh()
        c = load()
    return c["items"]


# ================================================================
#  四、筛选与格式化（前端 API / LLM 共用）
# ================================================================

def upcoming(days: int = 7, min_star: int = 0, kinds: tuple = ()) -> list:
    """
    未来 N 天的事件（含今天），可按星级/类型过滤。
    min_star: 0=不过滤；holiday 无 star，min_star>0 时会被排除。
    """
    from datetime import timedelta
    from app.flash import rules

    today = rules.beijing_now().date().isoformat()
    limit = (rules.beijing_now().date() + timedelta(days=days)).isoformat()
    out = []
    for it in get_items():
        if not (today <= (it.get("date") or "") <= limit):
            continue
        if kinds and it.get("kind") not in kinds:
            continue
        if min_star and (it.get("star") or 0) < min_star:
            continue
        out.append(it)
    return out


def _fmt_num(v):
    if v in (None, ""):
        return "—"
    return str(v)


def format_for_llm(days: int = 3, min_star: int = 4, limit: int = 12) -> str:
    """
    → LLM prompt 文本：未来 days 天内 star>=min_star 的重要事件。

    为什么默认 min_star=4：实测一周 64 条里 3 星占 54 条，用 >=3 等于没过滤
    （84% 都是 3 星，白烧 token）；>=4 只剩 6 条左右，才是真正的高价值事件
    （非农 / FOMC / CPI / 重要央行讲话）。
    """
    items = upcoming(days=days, min_star=min_star)
    if not items:
        return ""
    lines = []
    for it in items[:limit]:
        star = it.get("star")
        star_s = f"★{star}" if star else ""
        t = (it.get("time") or "")[5:]        # 去掉年份，省 token
        if it["kind"] == "data":
            # 前值/预期/实际 —— 未公布时 actual 为空
            vals = f"前值{_fmt_num(it.get('prev'))} 预期{_fmt_num(it.get('consensus'))}"
            if it.get("actual"):
                vals += f" 实际{it['actual']}"
            # 单位单独放括号：否则"预期缺失"会显示成"预期—万桶"，占位符和单位粘一起
            if it.get("unit"):
                vals += f"（{it['unit']}）"
            period = f"({it['period']})" if it.get("period") else ""
            lines.append(f"- {t} [{it.get('country')}] {it.get('title')}{period} "
                         f"{vals} {star_s}")
        elif it["kind"] == "event":
            lines.append(f"- {t} [{it.get('country')}] {it.get('title')} {star_s}")
        else:
            lines.append(f"- {t} [{it.get('country')}] {it.get('exchange')} "
                         f"{it.get('title')}休市（{it.get('rest_note') or ''}）")
    return "\n".join(lines)


def a_stock_holidays(days: int = 60) -> list:
    """
    未来 N 天内的 A 股休市日（沪深及北交所）。
    供人工/后续校验 rules.HOLIDAYS 硬编码日历用 —— 不做自动替换：
    HOLIDAYS 被调度器（决定跑不跑复盘，判错会白烧 LLM token）和
    ranking_history（算连续上榜天数，判错数据虚增）依赖，不能受外部接口波动影响。
    """
    out = []
    for it in upcoming(days=days):
        if it["kind"] != "holiday":
            continue
        if "沪深" in (it.get("exchange") or "") or "北交所" in (it.get("exchange") or ""):
            out.append(it)
    return out
