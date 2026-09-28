"""
================================================================================
【文件作用】财经日历「前瞻」告警（提前 1~3 天主动提醒，2026-09-29）
================================================================================
背景（为什么需要它）：
  现有 `contradictions/scanner.scan_today_calendar_focus` 只扫 **当天**，且只进
  「矛盾扫描」体系（不主动推送）⇒ 用户必须**当天自己去翻日历页**才知道有雷。
  而真实需求是"**提前知道**"：例如 9/29 就该知道 9/30 有 PCE、10/2 有非农
  （用户 2026-09-29 上轮亲自点名的正是这两个节点）。

数据源：金十财经日历（`flash/calendar.py`，已落库 `flash_calendar`）
  归一化字段：`id / kind / date / time / country / star / title / summary / content`
  ★ 实测（2026-09-29）：窗口 14 天、140 条；含海外事件（美国PCE 3星、ADP 4星、
    非农 5星、欧元区CPI、大量美联储官员讲话）。

★★ 筛选口径（为什么是"双通道"而不是"按星级"）：
  实测星级分布 3星112 / 4星8 / 5星2 —— 若只按 `star>=4`，**美国PCE（仅 3 星）会被漏掉**，
  而它恰是链路上最关键的通胀读数。反之若放到 `star>=3`，112 条里绝大多数是
  「美联储某官员讲话·致辞·活动」——方向不定、不构成事件驱动，属噪音。
  ⇒ 采用三条通道（任一命中即入选）：
    ① `star >= 5`            最高级（非农级）——无条件下必推；
    ② 标题命中 `_CORE_KW`    核心指标（PCE/CPI/非农/利率决议/FOMC/GDP/PMI/初请…）；
    ③ `star >= 4` 且非讲话类  次级重要事件（排除 `_NOISE_KW`）。
  推送门槛更高一层：仅"通道①或②"才推企微（③只进结构化输出供工作台用），
  避免"4 星讲话"这类噪音刷屏。

去重：`calendar_alert_log`（event_id 主键）—— 同一事件只推一次（跨日多次运行安全）。

用法：
  from app.flash.calendar_ahead import run_alert, upcoming
  print(run_alert())                  # 盘前/日批调用（有料才推）
  print(run_alert(dry_run=True))      # 只组装文案，不推送/不落库（测试）
  print(upcoming(days=3))             # 结构化前瞻（供接口/工作台消费）
================================================================================
"""

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

# ── 筛选词表（新增/调整只需改这里）────────────────────────────────────────────
# 核心指标：对应「通胀 → 美联储路径 → 利率/汇率 → A股」这条链上的关键读数
_CORE_KW = ("PCE", "CPI", "非农", "利率决议", "FOMC", "议息", "GDP",
            "PMI", "初请", "零售销售", "失业率", "工业产出")
# 噪音词：官员讲话/致辞类（方向不定、不构成事件驱动）——实测 3 星条目里占绝大多数
_NOISE_KW = ("讲话", "致辞", "活动", "发表", "演讲", "发布会", "新闻发布会")
# ★ 国家白名单（★ 2026-09-29 实测修正）：首版没有它 ⇒ 7 天窗口入选 41 条（法国/德国/
#   英国的 PMI 终值、瑞士零售销售…全进来了，推送会变刷屏）。对 A 股的传导链只有
#   **美国**（美联储路径/全球贴现率）与**中国**（本土政策与景气）是主线，其余国家
#   仅在 `star>=5`（非农级）时才值得占位。
_KEY_COUNTRIES = ("美国", "中国")

# 事件 → 传导路径（**静态知识映射，不是预测**；只做"这件事影响哪条链"的解释）
_IMPACT = (
    (("非农",), "就业强弱 → 加息路径（强 = 美元/美债上 → 压制 A 股估值）"),
    (("PCE", "CPI"), "通胀读数 → 美联储路径（超预期 = higher-for-longer 加深）"),
    (("利率决议", "FOMC", "议息"), "直接定调全球贴现率 → 汇率/A 股外资"),
    (("初请", "失业率"), "就业高频 → 加息预期微调"),
    (("PMI", "工业产出", "零售销售", "GDP"), "景气度 → 顺周期板块情绪"),
)

_TABLE_READY = False


def _impact_of(title: str) -> str:
    for kws, note in _IMPACT:
        if any(k in title for k in kws):
            return note
    return ""


def _classify(item: dict) -> Optional[str]:
    """返回命中通道（'5star'/'core'/'4star'）或 None（含国家白名单过滤）。"""
    title = str(item.get("title") or "")
    star = item.get("star") or 0
    country = str(item.get("country") or "")
    if star >= 5:
        return "5star"                       # 最高级：不限国家（如非农）
    if country not in _KEY_COUNTRIES:
        return None                          # 非美中数据：只认 5 星（见 _KEY_COUNTRIES 注释）
    if any(k in title for k in _CORE_KW):
        return "core"
    if star >= 4 and not any(k in title for k in _NOISE_KW):
        return "4star"
    return None


# 同一指标的多种口径后缀（★ 实测：美国「核心PCE物价指数」在 9/30 有 年率/月率/年化季率
# 三条 ⇒ 不去重会在推送里占 3 行、稀释其余信息）。剥离后取前缀作为合并键。
_METRIC_SUFFIX = ("年化季率终值", "年率初值", "月率初值", "年率终值", "月率终值",
                  "季率终值", "年化季率", "年率", "月率", "季率", "终值", "初值")


def _metric_key(title: str) -> str:
    """指标归一键（去口径后缀 → 截断）：用于同日同指标的合并。"""
    t = str(title or "").strip()
    for sfx in _METRIC_SUFFIX:
        if t.endswith(sfx):
            t = t[: -len(sfx)]
            break
    return t or str(title or "")


def ensure_table() -> None:
    """创建 calendar_alert_log（幂等，兼容 PostgreSQL/SQLite）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    from app.database import db
    col = "VARCHAR(64)" if db._use_postgres else "TEXT"
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS calendar_alert_log (
            event_id {col} PRIMARY KEY,
            event_date TEXT,
            title TEXT,
            pushed_at TEXT
        )""")
    _TABLE_READY = True


def upcoming(days: int = 3, include_4star: bool = True) -> Dict:
    """未来 `days` 天（含今天）的关键事件前瞻。

    返回 `{items: [...], by_date: {date: [...]}, core_count: n}`；失败静默返回空结构。
    """
    out = {"items": [], "by_date": {}, "core_count": 0}
    try:
        from app.flash import calendar
        items = calendar.get_items()
    except Exception as e:
        print(f"[cal_ahead] 读取日历失败: {str(e)[:80]}")        # ASCII（铁律⑥）
        return out
    if not items:
        return out
    today = datetime.now().strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    seen_metric = set()          # 同日同指标去重（见 _metric_key 注释）
    for it in items:
        if str(it.get("kind")) == "holiday":
            continue
        d = str(it.get("date") or "")[:10]
        if not d or d < today or d > end:
            continue
        ch = _classify(it)
        if not ch or (ch == "4star" and not include_4star):
            continue
        mk = (d, _metric_key(it.get("title")))
        if mk in seen_metric:
            continue
        seen_metric.add(mk)
        row = {"id": str(it.get("id") or ""), "date": d, "time": str(it.get("time") or ""),
               "country": it.get("country"), "star": it.get("star"),
               "title": it.get("title"), "channel": ch,
               "impact": _impact_of(str(it.get("title") or ""))}
        out["items"].append(row)
        out["by_date"].setdefault(d, []).append(row)
    # 排序：日期 → 通道优先级（5star > core > 4star）→ 时间
    _rank = {"5star": 0, "core": 1, "4star": 2}
    out["items"].sort(key=lambda x: (x["date"], _rank.get(x["channel"], 9), x["time"]))
    for v in out["by_date"].values():
        v.sort(key=lambda x: (_rank.get(x["channel"], 9), x["time"]))
    out["core_count"] = sum(1 for x in out["items"] if x["channel"] in ("5star", "core"))
    return out


def _short_date(d: str) -> str:
    """2026-09-30 → 09-30（周三）。"""
    try:
        dt = datetime.strptime(d, "%Y-%m-%d")
        wd = "一二三四五六日"[dt.weekday()]
        return f"{d[5:]}（周{wd}）"
    except Exception:
        return d


def run_alert(days: int = 3, dry_run: bool = False) -> str:
    """扫描未来 `days` 天核心事件 → 推企微（按 event_id 去重）。返回状态文案。"""
    up = upcoming(days=days)
    core = [x for x in up["items"] if x["channel"] in ("5star", "core")]
    if not core:
        return f"日历前瞻: 未来 {days} 天无核心事件（PCE/CPI/非农/FOMC 类），静默"
    from app.database import db
    ensure_table()
    fresh = []
    for x in core:
        if not x["id"]:
            fresh.append(x)          # 无 id（异常数据）不落库但照推（宁可多推不可漏）
            continue
        try:
            if db.fetch_one("SELECT event_id FROM calendar_alert_log WHERE event_id = %s",
                            (x["id"],)):
                continue
        except Exception:
            pass                     # 查失败按"未推过"处理（宁可重复，不可漏）
        fresh.append(x)
    if not fresh:
        return f"日历前瞻: {len(core)} 条核心事件已全部推送过，跳过"
    today = datetime.now().strftime("%Y-%m-%d")
    fresh_ids = set(id(x) for x in fresh)      # 身份比较（fresh 出自上面同一批对象）
    lines = []
    for d, rows in sorted(up["by_date"].items()):
        sel = [x for x in rows if id(x) in fresh_ids]
        if not sel:
            continue
        tag = "今天" if d == today else _short_date(d)
        lines.append(f"**{tag}**")
        for x in sel:
            star = f"{'★' * (x['star'] or 0)}" if x.get("star") else ""
            lines.append(f"- {x['country'] or ''} {x['title']} {star}"
                         + (f"\n  ↳ {x['impact']}" if x["impact"] else ""))
    title = f"📅 日历前瞻 · 未来 {days} 天关键事件（{len(fresh)} 条）"
    content = ("\n".join(lines) +
               "\n\n（口径：核心指标词 / 5星 / 4星非讲话类；官员讲话已过滤。"
               "用途：**提前 1~3 天知道哪天是数据敏感期**，"
               "当天开盘 30 分钟不追高、不赌数据方向。）")
    if dry_run:
        return f"【dry-run 不推送】{title}\n{content}"
    try:
        from app.flash import wechat
        if not wechat.WECHAT_WEBHOOK:
            return "日历前瞻: 检测到核心事件但未配置 WECHAT_WEBHOOK，未推送"
        wechat.notify("brief", title, content, force=True)
    except Exception as e:
        return f"日历前瞻: 推送失败（{str(e)[:80]}）"
    try:
        for x in fresh:
            if not x["id"]:
                continue
            db.execute(
                "INSERT INTO calendar_alert_log (event_id, event_date, title, pushed_at) "
                "VALUES (%s, %s, %s, %s)",
                (x["id"], x["date"], str(x["title"])[:120],
                 datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        print(f"[cal_ahead] 去重记录写入失败（已推送，可能重复推）: {e}")
    return f"日历前瞻: 已推送 {len(fresh)} 条核心事件（未来 {days} 天）"
