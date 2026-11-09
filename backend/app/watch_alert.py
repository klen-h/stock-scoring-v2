# -*- coding: utf-8 -*-
from __future__ import annotations

"""
================================================================================
【文件作用】日终观察器告警 —— 关键位破位 + 事件落点新主题（2026-11-10）
================================================================================

背景（用户选 C：全量推送，风险 + 机会 + 关键位 + 事件都要）：
  盘点后发现 C 的四类**三类早已实现**，只有两个「有数据、没出口」：
    · 关键位破位 —— `key_level_watch` 表有 `broke` 列，但**只在前端展示**，
      用户不开页面就永远看不到（`key_level_watch.py` 里 grep 不到任何 notify）。
    · 事件落点   —— `event_targets.build()` 能算出「今天具体看哪只」，
      但它是**纯查询接口**，同样只在打开页面时才存在。

  已实现、本模块**不重复造**的部分（避免"两个出口"破功）：
    · 持仓破止损 / 当日浮亏 / 减仓闸门  ← `coach` 的
      `stop_loss_hit` / `emotion_fuse` / `gate_reduce`（均 `push: true`，实时价）
    · 买点到达 / 目标兑现 / 计划止损     ← `scheduler.trade_plan_trigger_loop`（60s）
    · 指数急跌 / 冲高回落 / 跌停潮       ← `intraday_alerts`（3 分钟）
    · 市况转档                          ← `coach.regime_alert`
    · E2 政策脉冲                       ← `events.alert`（`event_alert_loop`）

★★ 为什么这两项必须是「日终推送」而不是盘中：
  `key_level_watch.py:261` 自己写着 ——
    「盘中用未定稿的当日 bar 会让 `broke`（是否失守）**随分钟反复翻转**」
  它的 `broke` 口径本来就是 `broke_prev = c < prev60 <= prev`（**今日收盘跌破、
  昨日还在上方**），天然是收盘确认量。盘中判会来回横跳 ⇒ 只能日终推。
  事件落点同理：它读 `stock_facts_wide`（日批产物），盘中不会有新东西。

★★ 防骚扰（复用项目既有模式，不另造一套）：
  · 关键位：**每天最多 1 条**（当天所有破位合成一条），靠
    `store.is_schedule_done("watch_alert:{date}")` —— 与 `intraday_alerts`
    同一套落库去重（进程重启不会重推）。
  · 事件主题：**只推"从未出现过"的主题**，一次最多 3 个（`WATCH_ALERT_THEME_TOP_N`）——
    同一波事件只提一次，不需要冷却期。
    ⚠️ **首次运行只建基线、不推送**：没有参照物时"新出现"无从谈起，
       一次性把 11 个热门主题砸给用户就是噪音。
    用 `schedule_state` 的 `evt_theme:{主题}` 记录"已提过"。
  · **平静的日子零推送**：没有破位、没有新主题 ⇒ 直接 return，不发空消息。

  ⚠️ 判定「热点主题」**不能**用 `event_targets.list_themes()`：它的 docstring 写
     「近 7 天快讯条数」，但 SQL **没有时间过滤**（实测 `伊朗局势` 是全历史 158 条）。
     本模块自己按近 24 小时统计新增条数，详见 `_theme_hot_now`。

★★ 触发时机（不设死时间，靠产物判断）：
  日批（`scripts/daily_batch.py`）跑完才有 `key_level_watch` 的当日行。
  本模块在 20:00-23:59 每 5 分钟醒一次，**先查当日行是否存在**：
  不存在 ⇒ 日批还没跑完，下一轮再看（不写"已推"标记，所以不会漏）。
  这样即使日批因故延后，当天仍然推得出去。

红线：只报事实（哪个指数破了哪条线、哪个主题今天有快讯），
      **不给买卖建议、不改评分、不进闸门**（与其它观察器同级）。
================================================================================
"""

import asyncio
import os
from datetime import datetime, timedelta

from app.database import db

# ── 窗口与节奏 ────────────────────────────────────────────────────────────────
# 20:00 起 —— 日批（约 20:30）之前就醒着，靠"产物是否存在"自我等待，不用猜时间。
_WINDOW_START_MIN = 20 * 60          # 20:00
_WINDOW_END_MIN = 23 * 60 + 59       # 23:59
_POLL_SECONDS = 300                  # 5 分钟一轮

# ── 事件主题的推送门槛（env 可调，改完无需改代码）──────────────────────────────
# 近 24h 新增快讯条数低于该值视为噪音（1-2 条可能是零星转载）。
THEME_MIN_NEWS = int(os.environ.get("WATCH_ALERT_THEME_MIN_NEWS", "3") or 3)
# 一次最多推几个主题（防"某天全市场都在说同一件事"时一次铺满）。
THEME_TOP_N = int(os.environ.get("WATCH_ALERT_THEME_TOP_N", "3") or 3)
# 首次运行的基线标记：**没有基线时只建立、不推送**（见 `_event_new_themes`）。
_BASELINE_TASK = "evt_theme_baseline"
# 主题的"已提过"标记前缀。★ 提成变量是为了**可测试**：
#   硬编码时测试脚本无法隔离，会把基线写进生产 `schedule_state`
#   （2026-11-10 实测踩过一次，事后手工清理了 4 条）。
_THEME_TASK_PREFIX = "evt_theme:"


def _bj_now() -> datetime:
    """全链路北京时间（与 `flash.rules.beijing_now` 同源，避免再来一次差 8h）。"""
    from app.flash.rules import beijing_now
    return beijing_now()


def _last_push_date(task: str) -> str:
    """`schedule_state` 里某 task 的最后执行日（YYYY-MM-DD）；读不到返回空串。

    ⚠️ 不复用 `store.is_schedule_done`：它只回答"是不是今天"，
       而主题冷却要的是"**距今几天**"。
    """
    try:
        row = db.fetch_one("SELECT done_date FROM schedule_state WHERE task = %s",
                           (task,))
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）：读不到就当"从没推过"，宁可多推一次
        print(f"[watch-alert] 读 {task} 失败，按未推过处理: {str(e)[:70]}")
        return ""
    return str((row or {}).get("done_date") or "")[:10]


# ==============================================================================
#  ① 关键位破位
# ==============================================================================

def _key_level_breaks(day: str) -> list:
    """当日 `broke=1` 的指数行。

    `broke` 的语义（见 `key_level_watch._analyze`）：
      · `broke_prev`      —— 今日收盘跌破 60 日支撑，**昨日还在上方**（首次失守）
      · `broke_round`     —— 跌破整数关口
      · `first_year_low`  —— 创年内新低
    三者任一即 `broke=1`。它是"**首次**"语义 ⇒ 同一条线破一次只记一天，
    所以每天最多推一次、且只有真破位那天才有内容。
    """
    return db.fetch(
        "SELECT code, name, close, chg_pct, support, support_type, "
        "first_year_low, status, detail "
        "FROM key_level_watch WHERE date = %s AND broke = 1 "
        "ORDER BY code", (day,)) or []


def _fmt_key_level(day: str, rows: list) -> str:
    """破位明细（企微 markdown）。

    ⚠️ 表格在企微机器人里不渲染（`wechat.notify` 会统一转成列表），
       这里直接写成列表，省掉一次转换。
    """
    out = []
    for r in rows:
        code = str(r.get("code") or "")
        name = str(r.get("name") or "")
        try:
            close = float(r.get("close") or 0)
            chg = float(r.get("chg_pct") or 0)
            sup = float(r.get("support") or 0)
        except (TypeError, ValueError):
            close = chg = sup = 0.0
        sup_type = str(r.get("support_type") or "支撑")
        extra = "　**创年内新低**" if r.get("first_year_low") else ""
        out.append(f"- **{name}**（{code}）收 {close:.2f}（{chg:+.2f}%），"
                   f"失守{sup_type} {sup:.2f}{extra}")
    return "\n".join(out)


# ==============================================================================
#  ② 事件落点 · 新主题
# ==============================================================================

def _theme_hot_now(hours: int = 24) -> list:
    """近 `hours` 小时内**新增**快讯条数达标的主题（按条数倒序）。

    ★★ 为什么不直接用 `event_targets.list_themes()`（实测踩过）：
      它的 docstring 写「近 7 天快讯条数」，但 SQL 是
      `SELECT cluster, COUNT(*) ... GROUP BY cluster` —— **没有任何时间过滤**
      ⇒ 实际是**全历史累计**。实测 `伊朗局势 = 158 条`、`原油能源 = 168 条`，
      那是几十天的存量。拿它当「今天有 158 条」会**一次把 11 个主题全推出去**，
      正是「事件推送退化成每日噪音」的经典死法。
      ⇒ 本函数自己按 `time >= now-hours` 统计**新增**（同表 `flash_news`，同 `cluster` 口径）。
      ⚠️ `flash_news.time` 是 `YYYY-MM-DD HH:MM:SS` 的**字符串**（不是 timestamp），
        所以用字符串比较即可（格式定长，字典序=时间序）。
    """
    since = (_bj_now() - timedelta(hours=max(1, int(hours)))).strftime(
        "%Y-%m-%d %H:%M:%S")
    try:
        rows = db.fetch(
            "SELECT cluster, COUNT(*) AS n FROM flash_news "
            "WHERE cluster IS NOT NULL AND cluster <> '' AND time >= %s "
            "GROUP BY cluster ORDER BY n DESC", (since,)) or []
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）：主题层挂了不该拖垮关键位那一半
        print(f"[watch-alert] 统计近 {hours}h 快讯失败: {str(e)[:70]}")
        return []

    from app.event_targets import THEME_INDUSTRY
    out = []
    for r in rows:
        name = str(r.get("cluster") or "").strip()
        if not name or name not in THEME_INDUSTRY:
            continue    # 没配行业映射 ⇒ 查不到票，推了只会困惑（同 build() 的 mapped 口径）
        try:
            n = int(r.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        if n < THEME_MIN_NEWS:
            continue
        out.append({"theme": name, "news_count": n})
    return out


def _event_new_themes(day: str) -> list:
    """当日「**新出现**」的事件主题（此前从未推过的）。

    ★★ 首次运行**只建基线、不推送**（这条是防轰炸的关键）：
      没有基线时，所有热门主题都属于「从没见过」⇒ 一次性全推 = 第一天就把
      11 个主题砸给用户。而「变化」本身需要**参照物**，第一天没有参照物，
      诚实的做法是**先把当前状态记为基线**，从第二天起只报「今天新冒出来的」。
    ★ 之后：只推**从未标记过 `evt_theme:{主题}`** 的 ⇒ 同一波事件只提一次，
      天然永久去重，不需要冷却期（事件真的沉寂半年后重来，届时可手工 clear 标记）。
    """
    from app.flash import store

    hot = _theme_hot_now(24)
    if not _has_baseline():
        for t in hot:
            store.mark_schedule_done(_THEME_TASK_PREFIX + t["theme"], day)
        store.mark_schedule_done(_BASELINE_TASK, day)
        if hot:
            print(f"[watch-alert] 首次运行：已建立 {len(hot)} 个主题基线"
                  f"（本次不推送，避免一次性轰炸）")
        return []

    fresh = [t for t in hot
             if not _last_push_date(_THEME_TASK_PREFIX + t["theme"])]
    return fresh[:THEME_TOP_N]


def _has_baseline() -> bool:
    return bool(_last_push_date(_BASELINE_TASK))


def _fmt_event_themes(day: str, items: list) -> str:
    """新主题明细 + 落到哪些行业（**不给个股，避免被读成推荐**）。"""
    out = []
    for it in items:
        th = it["theme"]
        line = f"- **{th}**：近 24h 新增 {it['news_count']} 条"
        try:
            from app.event_targets import THEME_INDUSTRY
            spec = THEME_INDUSTRY.get(th) or []
            inds = [s[0] for s in spec if s]
            if inds:
                line += f"，落点行业 {'/'.join(inds[:4])}"
        except Exception as e:
            print(f"[watch-alert] 取 {th} 行业映射失败: {str(e)[:60]}")
        out.append(line)
    return "\n".join(out)


# ==============================================================================
#  入口
# ==============================================================================

def run_daily(day: str = None, force: bool = False) -> dict:
    """检查一次并推送（幂等：当天推过就跳过）。

    参数：
      day    YYYY-MM-DD（默认北京今天）
      force  True ⇒ 忽略"今日已推"标记（手工补推/调试用）

    返回：{ok, pushed, skipped?, breaks, themes}
    """
    from app.flash import store
    from app.flash.wechat import notify

    day = str(day or _bj_now().strftime("%Y-%m-%d"))[:10]
    tag = "watch_alert:" + day
    if not force and store.is_schedule_done(tag):
        return {"ok": True, "pushed": False, "skipped": "今日已推送"}

    # ★ 日批产物存在性检查：没有当日行 ⇒ 日批还没跑完（或今天非交易日）。
    #   **不写"已推"标记**，所以下一轮还会再查 —— 日批延后也推得出去。
    if not db.fetch_one(
            "SELECT 1 AS x FROM key_level_watch WHERE date = %s LIMIT 1", (day,)):
        return {"ok": False, "pushed": False,
                "skipped": f"日批尚未生成 {day} 的关键位数据"}

    breaks = _key_level_breaks(day)
    themes = _event_new_themes(day)
    if not breaks and not themes:
        # 平静的一天：什么都不发（发"今天没事"本身就是打扰）
        store.mark_schedule_done(tag, day)
        return {"ok": True, "pushed": False, "skipped": "无破位、无新主题",
                "breaks": 0, "themes": 0}

    parts = [f"**{day} 日终观察**（{_bj_now().strftime('%H:%M')}）", ""]
    if breaks:
        parts.append(f"### ⚠️ 关键位破位 {len(breaks)} 个")
        parts.append(_fmt_key_level(day, breaks))
        parts.append("")
    if themes:
        parts.append(f"### 📌 事件落点 · 新主题 {len(themes)} 个")
        parts.append(_fmt_event_themes(day, themes))
        parts.append("")
    parts.append("---")
    parts.append("以上为收盘确认后的事实，不构成买卖建议；破位=首次失守，"
                 "同一条线只记一次。")

    title = f"日终观察：{len(breaks)} 个破位 / {len(themes)} 个新主题"
    body = "\n".join(parts)

    ok = False
    try:
        # force=True：与 `event_alert` 同车道 —— 稀有且关键，穿透业务开关
        ok = bool(notify("alert", title, body, force=True))
    except Exception as e:
        print(f"[watch-alert] 推送异常: {str(e)[:90]}")

    if ok:
        for it in themes:
            store.mark_schedule_done(_THEME_TASK_PREFIX + it["theme"], day)
    else:
        # ★ 推送失败**仍然标记已推**：主因是 webhook 配置/网络，
        #   重试几十次没有价值；数据已落库，页面上看得到。
        #   这里只把失败打出来，避免"以为推了其实没推"。
        print(f"[watch-alert] ⚠️ 推送失败（数据已落库，页面可查）{day}")
    # 关键位是"每天最多一条"，无论推送成功与否都不该当天再发第二遍
    store.mark_schedule_done(tag, day)

    return {"ok": True, "pushed": ok, "breaks": len(breaks),
            "themes": len(themes), "date": day}


async def watch_alert_loop():
    """盘中/盘后常驻循环：20:00-23:59 每 5 分钟检查一次。

    ★ 为什么不用 `_heavy` 包装：本循环**零外部请求**（只读库），
      只读模式下也应该跑 —— 否则线上永远收不到这条推送。
    """
    while True:
        try:
            now = _bj_now()
            minute = now.hour * 60 + now.minute
            if (now.weekday() < 5
                    and _WINDOW_START_MIN <= minute <= _WINDOW_END_MIN):
                r = run_daily()
                if r.get("pushed"):
                    print(f"[watch-alert] 已推送：{r.get('breaks')} 破位 / "
                          f"{r.get('themes')} 新主题")
        except Exception as e:
            # 吞异常必须可见（GUARDRAILS #11）：整个 loop 静默死亡 = 推送永远不来
            print(f"[watch-alert] 本轮失败: {str(e)[:90]}")
        await asyncio.sleep(_POLL_SECONDS)
