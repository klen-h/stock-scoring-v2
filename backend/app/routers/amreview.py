# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「早盘复盘」标签页的**单一聚合端点**
================================================================================
`GET /api/market/amreview?date=YYYY-MM-DD`

## 为什么必须是"一个"端点
Supabase 在 `ap-southeast-1`，**单次往返约 570ms**（实测 12 次极稳定）。
前端若分别打 5~6 个接口，串起来就是 3 秒以上 ⇒ 切换标签页明显卡顿。
本端点内部把 5 次查询**并发**发出，并且整体加 300 秒进程缓存
（11:30 之后早盘数据本就不再变化，缓存不损新鲜度）。

## 数据从哪来（全部"只读"，绝不现场重算）
| 块 | 来源 | 历史回放 |
|---|---|---|
| 盘面 | `market_emotion_daily.am_*`（11:30 定稿列） | 同左；缺失则回退 `close_*` 并**显式标注口径** |
| 半日成交额 | `market_amount.peek()`（今日）/ `market_amount_daily.series_json` 截 11:30 | 同左 |
| 指数 | `market_overview()`（已有 3 秒微缓存） | **不支持**（无分钟级历史快照）→ `missing` |
| 宏观·状态 | `market_regime_history` 按日取 | 同左 |
| 宏观·外盘 | `macro_daily_history` 按日取（日频收盘表） | 同左 |
| 宏观·数据 | `flash_calendar` 当日已公布项（含实际/预期/解读） | 同左 |
| 消息 | `flash_brief.load(date)`（**只读，绝不触发 LLM**） | 同左 |
| 结论 | `flash_reviews` 的 `lunchbreak` 按日取 | 同左 |

## 铁律（2026-11-01 事故后确立）
**展示型接口只读快照；查不到就进 `missing` 并说明原因，绝不为展示现场重算。**
（同日 `scenario_watch` 因为"查不到就现算"导致单次请求 114 秒。）
================================================================================
"""

from typing import Dict, List, Optional

from fastapi import APIRouter

from app.database import db
from app.flash import rules

router = APIRouter()

# 11:30 之后数据冻结 ⇒ 5 分钟缓存与真实新鲜度不冲突。
# ★ 必须**按日**缓存：用单槽会在"今天 ⇄ 历史日期"来回切换时反复重算，
#   而每次重算要发 5 次跨境查询（约 570ms/次）。
_CACHE: Dict[str, tuple] = {}        # {day: (ts, payload)}
_TTL = 300.0
_CACHE_MAX = 8                        # 回放时来回翻几天；超过就淘汰最旧的

# ★★ 常驻线程池（**不要**每次请求 `with ThreadPoolExecutor(...)`）。
#   `app.database` 的连接是**线程本地**的 ⇒ 每次新建线程池 = 每次新建线程 =
#   每次重新建连（Supabase 冷连接实测 2.8 秒）⇒ "并发"反而比串行更慢。
#   模块级池子的线程存活，连接随之复用；实测热态从 6.2s 降到 1s 级。
_POOL = None
_POOL_LOCK = None


def _executor():
    global _POOL, _POOL_LOCK
    if _POOL is None:
        import threading
        from concurrent.futures import ThreadPoolExecutor
        _POOL_LOCK = _POOL_LOCK or threading.Lock()
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="amreview")
    return _POOL

# 外盘展示项（code 对齐 `macro_daily.SYMBOLS`；标普/纳指不在日频表内 ⇒ 不硬凑）
_OUTER = [("brent", "布伦特原油"), ("gold_com", "COMEX金"),
          ("us10y", "美债10Y"), ("dxy", "美元指数")]

# 财经日历是**日频**数据（每天刷新一次）⇒ 进程内缓存 10 分钟。
# 实测 `calendar.get_items()` 单次约 3.2 秒（多次远程读），而复用度极高。
_CAL_CACHE = {"ts": 0.0, "items": []}
_CAL_TTL = 600.0

# `market_amount_daily` 是否存在（不存在就 10 分钟内不再尝试，理由见 `_am_amount`）
_AMT_TABLE = {"missing": False, "ts": 0.0}

# 市况枚举 → 中文（`caliber` 会出现在悬停提示里，不能露英文）
_STATE_CN = {
    "offensive": "进攻",
    "neutral": "震荡",
    "neutral_bearish": "偏空震荡",
    "defensive": "防御",
}


def _calendar_items() -> list:
    import time as _t
    now = _t.time()
    if _CAL_CACHE["items"] and now - float(_CAL_CACHE["ts"] or 0) < _CAL_TTL:
        return _CAL_CACHE["items"]
    try:
        from app.flash import calendar
        items = calendar.get_items(refresh_if_empty=False) or []
    except Exception as e:
        print(f"[amreview] calendar load failed: {e}")
        return _CAL_CACHE["items"]
    if items:
        _CAL_CACHE.update(ts=now, items=items)
    return items


def _num(v) -> Optional[float]:
    """字符串/数字 → float；缺失或非法返回 None（**不是 0**）。"""
    try:
        if v is None or v == "" or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _today() -> str:
    return rules.beijing_now().strftime("%Y-%m-%d")


def _prev_day(day: str) -> str:
    """上一交易日（跳过周末/法定节假日）—— 外盘日频对比的基准日。"""
    try:
        from app.flash.rules import prev_trading_day
        return str(prev_trading_day(day) or "")[:10]
    except Exception:
        return ""


# ── ① 盘面 ────────────────────────────────────────────────────────────────

def _am_amount(day: str) -> Optional[float]:
    """半日（截至 11:30）两市累计成交额（亿元）。取不到返回 None。"""
    # 今日：只读内存缓存；**不**回落到分时表 ——
    #   今日那一行要收盘后才写，查了必然是空/报错，纯浪费一次跨境往返。
    if day == _today():
        try:
            from app import market_amount
            a = market_amount.peek(max_age=900) or {}
            if str(a.get("data_date") or "")[:10] == day:
                return _num(a.get("cum_yi"))
        except Exception as e:
            print(f"[amreview] amount peek failed: {e}")
        return None

    # 历史：落库的整日分时序列里截到 11:30
    # ★★ 表不存在时必须**跳过**：`market_amount_daily` 由 `save_daily()` 收盘创建，
    #   此前该表在线上长时间不存在，而 PG 对"查不存在的表"**每次要 2.8~4.6 秒**
    #   （实测：错误路径比正常查询贵 5~8 倍）⇒ 命中一次就记 10 分钟，别反复撞。
    import time as _t
    if _AMT_TABLE.get("missing") and _t.time() - float(_AMT_TABLE.get("ts") or 0) < 600:
        return None
    try:
        row = db.fetch_one(
            "SELECT series_json FROM market_amount_daily WHERE date = %s", (day,))
        _AMT_TABLE.update(missing=False, ts=_t.time())
        if row and row.get("series_json"):
            import json
            pts = json.loads(row["series_json"]) or {}
            keys = [k for k in pts if str(k) <= "1130"]
            if keys:
                return _num(pts[max(keys)])
    except Exception:
        # 表不存在属**正常状态**（首个交易日前为空）⇒ 不打印，静默记 10 分钟
        _AMT_TABLE.update(missing=True, ts=_t.time())
    return None


def _market_block(day: str, is_live: bool, missing: List[dict]) -> List[dict]:
    """盘面走势：涨跌家数 / 涨跌停 / 连板 / 判读 / 半日成交额 / 指数。"""
    items: List[dict] = []
    src = "market_emotion_daily"

    row: Dict = {}
    try:
        # ⚠️ **刻意不调 `_ensure_emotion_table()`**：它会为 23 个列各跑一次
        #   `ALTER TABLE`，而 Supabase 单次往返约 570ms ⇒ 首次调用要 13 秒，
        #   直接把"展示型端点"变成事故（2026-11-01 实测）。
        #   建列是**写入侧**（`snapshot_am` / 调度循环）的职责；这里 `SELECT *` 拿不到
        #   的列 `.get()` 自然是 None ⇒ 走"回退 close_* + 标注口径"分支，结果依然诚实。
        row = db.fetch_one(
            "SELECT * FROM market_emotion_daily WHERE date = %s", (day,)) or {}
    except Exception as e:
        print(f"[amreview] emotion row failed: {e}")

    # 口径判定：优先 11:30 定稿；没有则回退收盘定稿，并**明确标注**
    prefix, caliber, as_of = "am", "11:30 早盘定稿", row.get("am_as_of")
    if not row.get("am_as_of"):
        if row.get("close_as_of"):
            prefix, caliber, as_of = "close", "该日无 11:30 快照，以下为收盘定稿口径", row.get("close_as_of")
        else:
            prefix = None

    if prefix:
        up, down = _num(row.get(f"{prefix}_up")), _num(row.get(f"{prefix}_down"))
        if up is not None and down is not None:
            items.append({"label": "涨 / 跌 家数", "value": f"{int(up)} / {int(down)}",
                          "unit": "家", "source": src, "as_of": as_of, "caliber": caliber})
        lu, ld = _num(row.get(f"{prefix}_limit_up")), _num(row.get(f"{prefix}_limit_down"))
        if lu is not None:
            items.append({"label": "涨停 / 跌停", "value": f"{int(lu)} / {int(ld or 0)}",
                          "unit": "家", "source": src, "as_of": as_of, "caliber": caliber})
        streak = _num(row.get(f"{prefix}_max_streak"))
        if streak is not None:
            items.append({"label": "最高连板", "value": f"{int(streak)}", "unit": "板",
                          "source": src, "as_of": as_of, "caliber": caliber})
        verdict = row.get(f"{prefix}_verdict")
        if verdict:
            items.append({"label": "情绪判读", "value": str(verdict), "unit": "",
                          "source": src, "as_of": as_of, "caliber": caliber})
    else:
        # ★ 2026-11-01（用户："其中有些是英文字眼，得换成中文"）：原因文案不带表名/列名，
        #   表名只留在 `trace` 的 `source` 字段里（排障时对代码用），展示层由前端翻成中文。
        missing.append({"block": "market", "item": "涨跌家数 / 涨跌停 / 连板 / 判读",
                        "reason": "该日未留存（库中既无 11:30 早盘定稿，也无收盘定稿）"})

    amt = _num(row.get("am_amount_yi")) or _am_amount(day)
    if amt is not None:
        items.append({"label": "半日成交额（截至 11:30）", "value": f"{amt:,.0f}", "unit": "亿元",
                      "source": "market_amount", "as_of": "11:30",
                      "caliber": "两市（沪+深）累计，不含北交所"})
    else:
        missing.append({"block": "market", "item": "半日成交额",
                        "reason": "该日未留存（没有落库的分时成交额）"})

    # 指数：只有当日可取（无分钟级历史快照）；历史一律进 missing，不拿收盘值冒充
    if is_live:
        # ★★ 只读工作台已经算好的那份（`market_overview` 每次生成时留快照），
        #   **绝不在这里同步外呼腾讯** —— 冷启动时那 6 个指数实测要 30 秒以上，
        #   而工作台自身 60 秒轮询 `/pulse` 必然会把这份快照填上。
        try:
            from app.routers import market as mk
            import time as _t
            snap = mk._OVERVIEW_IDX_SNAP
            age = _t.time() - float(snap.get("ts") or 0)
            if snap.get("val") and age <= 180:
                from app.flash.rules import beijing_now
                as_of = beijing_now().strftime("%H:%M")
                for idx in snap["val"][:3]:
                    chg = _num(idx.get("change_pct"))
                    if chg is None:
                        continue
                    items.append({"label": idx.get("name") or idx.get("code"),
                                  "value": f"{chg:+.2f}", "unit": "%", "delta": chg,
                                  "source": "market_overview 快照", "as_of": as_of,
                                  "caliber": f"工作台实时口径（{int(age)} 秒前刷新）"})
            else:
                missing.append({"block": "market", "item": "指数涨跌",
                                "reason": "指数缓存未就绪（工作台轮询后会填入，最多 60 秒）"})
        except Exception as e:
            print(f"[amreview] indices failed: {e}")
    else:
        missing.append({"block": "market", "item": "指数涨跌",
                        "reason": "该日 11:30 的指数读数未留存（仅有日线，不能反推盘中）"})
    return items


# ── ② 宏观 ────────────────────────────────────────────────────────────────

def _macro_block(day: str, missing: List[dict]) -> List[dict]:
    items: List[dict] = []
    # 市场状态：按日取「<= 该日的最近一条」，与评分/看板同一张表
    try:
        r = db.fetch_one(
            "SELECT date, state FROM market_regime_history WHERE date <= %s "
            "ORDER BY date DESC LIMIT 1", (day,))
        if r and r.get("state"):
            from app.backtest.market_regime import get_regime_description
            try:
                desc = get_regime_description(r["state"])
            except Exception:
                desc = ""
            items.append({"label": "市场状态", "value": desc or str(r["state"]), "unit": "",
                          "source": "market_regime_history", "as_of": str(r.get("date"))[:10],
                          # ★ 2026-11-01：`caliber` 会显示在悬停提示里，不能露出 `state=defensive` 这种英文
                          "caliber": f"量化引擎判定：{_STATE_CN.get(str(r['state']), '未知')}"})
        else:
            missing.append({"block": "macro", "item": "市场状态", "reason": "该日无 regime 判定记录"})
    except Exception as e:
        print(f"[amreview] regime failed: {e}")

    # 外盘：日频收盘表，取该日与其前一交易日算涨跌幅（**一次**查询覆盖全部品种）
    try:
        prev = _prev_day(day)
        start = prev or day
        rows = db.fetch(
            "SELECT code, date, close FROM macro_daily_history "
            "WHERE date >= %s AND date <= %s ORDER BY date", (start, day)) or []
        by_code: Dict[str, list] = {}
        for r in rows:
            by_code.setdefault(str(r.get("code")), []).append(
                (str(r.get("date"))[:10], _num(r.get("close"))))
        hit = 0
        for code, label in _OUTER:
            seq = [x for x in by_code.get(code, []) if x[1] is not None]
            if not seq:
                continue
            cur = seq[-1][1]
            prev_close = seq[-2][1] if len(seq) >= 2 else None
            pct = None
            if prev_close:
                pct = round((cur / prev_close - 1) * 100, 2)
            hit += 1
            items.append({"label": label, "value": f"{pct:+.2f}" if pct is not None else f"{cur:,.2f}",
                          "unit": "%" if pct is not None else "", "delta": pct,
                          "source": "macro_daily_history", "as_of": seq[-1][0],
                          "caliber": "日频收盘价环比" if pct is not None else "仅收盘价（无前值）"})
        if not hit:
            missing.append({"block": "macro", "item": "外盘品种",
                            "reason": "该日附近没有外盘日线记录"})
    except Exception as e:
        print(f"[amreview] macro daily failed: {e}")

    # 当日已公布的经济数据（有 actual 才算"已公布"）
    try:
        for it in _calendar_items():
            if str(it.get("date") or "")[:10] != day:
                continue
            act = it.get("actual")
            if act in (None, "", "-"):
                continue
            name = it.get("title") or ""
            bits = [f"实际 {act}{it.get('unit') or ''}"]
            if it.get("consensus") not in (None, "", "-"):
                bits.append(f"预期 {it.get('consensus')}{it.get('unit') or ''}")
            if it.get("prev") not in (None, "", "-"):
                bits.append(f"前值 {it.get('prev')}{it.get('unit') or ''}")
            items.append({"label": f"{it.get('country') or ''} {name}".strip(),
                          "value": " ／ ".join(bits), "unit": "",
                          "source": "flash_calendar", "as_of": str(it.get("time") or "")[:5],
                          "caliber": it.get("impact") or "无解读映射"})
    except Exception as e:
        print(f"[amreview] calendar failed: {e}")
    return items


# ── ③ 消息面 ──────────────────────────────────────────────────────────────

def _news_block(day: str, missing: List[dict]) -> List[dict]:
    """消息面：**直接读 `flash_brief` 表**，不调 `flash_brief.load()`。

    ★★ 为什么不复用现成的 `fb.load()`（2026-11-01 实测踩到）：
      它在落库快照**超过 10 分钟**时会调 `build_snapshot()` **重算**（指数/板块/
      异动/快讯全部重新取一遍），本机实测单次 **7.5~30 秒**。
      那对"60 秒轮询的卡片"是合理设计，但对**历史回放的只读端点**是灾难：
      回看 10-28 会去抓"现在"的指数再拼进 10-28 的消息块 —— 既是慢，也是错的。
    ⇒ 只取该行已落库的 `conclusion` 与 `llm_json.news`，一次查询、零外呼。
    """
    items: List[dict] = []
    try:
        import json as _json
        row = db.fetch_one(
            "SELECT date, as_of, conclusion, llm_json FROM flash_brief WHERE date = %s",
            (day,)) or {}
    except Exception as e:
        print(f"[amreview] brief row failed: {e}")
        row = {}
    if not row:
        missing.append({"block": "news", "item": "快讯要点",
                        "reason": "该日尚未生成快讯解读（只读接口不会触发 LLM）"})
        return items
    concl = str(row.get("conclusion") or "").strip()
    if concl:
        items.append({"label": "要点归纳", "value": concl, "unit": "",
                      "source": "flash_brief", "as_of": str(row.get("as_of") or "")[:5],
                      "caliber": "LLM 归纳（原文可追溯至快讯时间线）"})
    try:
        llm = _json.loads(row.get("llm_json") or "{}")
    except (ValueError, TypeError):
        llm = {}
    news = llm.get("news") if isinstance(llm, dict) else None
    for n in (news or [])[:5]:
        if not isinstance(n, dict):
            continue
        t = str(n.get("title") or n.get("text") or "").strip()
        if not t:
            continue
        items.append({"label": str(n.get("time") or "快讯"), "value": t[:120], "unit": "",
                      "source": "flash_brief.news", "as_of": str(n.get("time") or ""),
                      "caliber": str(n.get("impact") or "")})
    if not items:
        missing.append({"block": "news", "item": "快讯要点",
                        "reason": "该日解读记录里没有结论正文"})
    return items


# ── ④ 持仓异动（核心：信号 → 原因 → 影响 → 午后动作）──────────────────────

# 动作优先级：数字越小越紧急（前端按此排序，P0 永远在最上面）
_PRIO_ORDER = {"P0": 0, "P1": 1, "P2": 2}


def _action_of(item: Dict) -> List[dict]:
    """把逐只 alerts 翻译成「带优先级的具体动作」。

    ★ 为什么在**后端**做：同一套判定将来企微推送也要用（推送不可能跑前端逻辑）。
      前端只负责排版，避免"页面说减仓、推送说观望"这种两套口径。
    ★ 全部是**确定性规则**（不是 LLM）：阈值与 `portfolio_radar._alerts_for` 同源，
      只是把"描述"改写成"动作"，并给出优先级。
    """
    acts: List[dict] = []
    pnl = item.get("pnl_pct")
    for a in (item.get("alerts") or []):
        lvl, txt = a.get("level"), str(a.get("text") or "")
        if not txt:
            continue
        if lvl == "risk":
            if "出货" in txt or (pnl is not None and pnl <= -12):
                acts.append({"priority": "P0", "text": f"减仓/认错 —— {txt}"})
            else:
                acts.append({"priority": "P1", "text": f"今日盯盘处理 —— {txt}"})
        elif lvl == "opportunity":
            acts.append({"priority": "P2", "text": f"可考虑加仓（需自行确认）—— {txt}"})
    return acts


def _holdings_block(is_live: bool, missing: List[dict]) -> Dict:
    """持仓异动：只读 `portfolio_radar` 的现成结果（**不触发 31~36s 冷构建**）。

    返回结构（前端按"先结论后数据"排版）：
      · `actions[]`  —— 跨持仓汇总的**待办清单**（P0/P1/P2 + 归属个股）
      · `items[]`    —— 只保留**有异动**的持仓（无 alert 的不罗列，避免信息过载）
      · `structure`  —— 仓位结构/敞口（总市值、最大行业占比、风格）
    """
    empty = {"available": False, "actions": [], "items": [], "structure": {}}
    if not is_live:
        # 历史日：持仓雷达用的是**此刻**的行情 ⇒ 放进历史页就是"拿今天的价格冒充那天"
        missing.append({"block": "holdings", "item": "持仓异动",
                        "reason": "历史日不提供（当日行情未留存，不能用今天的价格反推那天的持仓）"})
        return empty
    try:
        from app import portfolio_radar
        r = portfolio_radar.peek()
    except Exception as e:
        print(f"[amreview] radar peek failed: {e}")
        r = None
    if not r or r.get("warming") or not r.get("items"):
        missing.append({"block": "holdings", "item": "持仓异动",
                        "reason": "持仓雷达尚未就绪（打开工作台后约 1 分钟生成；也可点右侧持仓卡刷新）"})
        return empty

    items_out: List[dict] = []
    actions: List[dict] = []
    total_mv = 0.0
    for it in (r.get("items") or []):
        try:
            total_mv += float(it.get("mv") or 0)
        except (TypeError, ValueError):
            pass

    for it in (r.get("items") or []):
        acts = _action_of(it)
        if not acts:
            continue                      # ★ 无异动不罗列（用户要求"减少数据罗列"）
        mv = it.get("mv") or 0
        try:
            weight = round(float(mv) / total_mv * 100, 1) if total_mv > 0 else None
        except (TypeError, ValueError, ZeroDivisionError):
            weight = None
        items_out.append({
            "code": it.get("code"), "name": it.get("name"),
            "day_pct": it.get("day_pct"), "pnl_pct": it.get("pnl_pct"),
            "weight": weight, "industry": it.get("industry"),
            "phase_cn": it.get("phase_cn") or "",
            "alerts": [{"level": a.get("level"), "text": a.get("text")}
                       for a in (it.get("alerts") or []) if a.get("text")],
            "actions": acts,
        })
        for a in acts:
            actions.append({**a, "code": it.get("code"), "name": it.get("name")})

    # 仓位结构（组合层）：最大行业占比 + 风格，直接抄 `_portfolio_risk` 的结论
    structure: Dict = {}
    risk = r.get("portfolio_risk") or {}
    try:
        conc = risk.get("concentration") or []
        if conc:
            top = max(conc, key=lambda x: float(x.get("weight") or 0))
            structure["top_industry"] = top.get("industry")
            structure["top_weight"] = round(float(top.get("weight") or 0), 1)
            structure["concentration_note"] = (
                f"{top.get('industry')} 占 {structure['top_weight']}%"
                + ("（过度集中，表面分散实则一个板块）" if structure["top_weight"] >= 50 else ""))
    except Exception:
        pass
    style = risk.get("style") or {}
    if style.get("label"):
        structure["style"] = style.get("label")
    structure["total_mv"] = round(total_mv, 0)
    structure["risk_count"] = len(items_out)

    # 组合层建议也进待办（集中度是"结构问题"，优先级低于个股硬风险）
    if structure.get("top_weight") and structure["top_weight"] >= 50:
        actions.append({"priority": "P1", "code": "", "name": "",
                        "text": f"分散仓位 —— {structure['concentration_note']}"})

    actions.sort(key=lambda x: _PRIO_ORDER.get(x.get("priority"), 9))
    items_out.sort(key=lambda x: _PRIO_ORDER.get((x["actions"] or [{}])[0].get("priority"), 9))

    return {
        "available": True,
        "quote_as_of": r.get("quote_as_of"),
        "quote_data_date": r.get("quote_data_date"),
        "quote_from_snapshot": r.get("quote_from_snapshot"),
        "actions": actions[:8],           # 待办最多 8 条，多了没人看
        "items": items_out,
        "structure": structure,
    }


# ── ⑤ 结论（午后研判）────────────────────────────────────────────────────

# ── ⑤ 补充维度（用户："项目里有大把有用的数据没用上"）────────────────────
#   这些不进页面的"数据附录"主表（避免信息过载），但**必须喂给 AI 解读** ——
#   它们恰好是"解释成因"最需要的几个维度：钱从哪来、打到哪个板块、情景概率怎么变。

# ★ 2026-11-02：枚举值以**库里实际出现的**为准（实测 `SELECT DISTINCT phase`：
#   decline 1981 / sideways 532 / accumulation 237 / markup 67 / distribution 22 / shakeout 14）。
#   ⚠️ 首版按"想当然"写成 accum/wash/range/down ⇒ 4/6 个没映射上，喂给模型的还是英文。
_PHASE_CN = {
    "accumulation": "吸筹", "accum": "吸筹",
    "markup": "拉升",
    "shakeout": "洗盘", "wash": "洗盘",
    "distribution": "出货",
    "sideways": "盘整", "range": "盘整",
    "decline": "下跌", "down": "下跌",
}


# 情景推演是**日频**数据（日批落库）⇒ 按日缓存 10 分钟。
# `scenario_watch.load()` 内部要跑 2 次查询（MAX(date) + 取行），跨境约 1.1 秒。
_SC_CACHE: Dict[str, tuple] = {}
_SC_TTL = 600.0


def _scenario_of(day: str) -> Dict:
    import time as _t
    now = _t.time()
    hit = _SC_CACHE.get(day)
    if hit and now - float(hit[0] or 0) < _SC_TTL:
        return hit[1]
    try:
        from app import scenario_watch as sw
        sc = sw.load(day) or {}
    except Exception as e:
        print(f"[amreview] scenario failed: {e}")
        return {}
    if sc:
        _SC_CACHE[day] = (now, sc)
        if len(_SC_CACHE) > 8:
            for k in sorted(_SC_CACHE, key=lambda x: _SC_CACHE[x][0])[:len(_SC_CACHE) - 8]:
                _SC_CACHE.pop(k, None)
    return sc


def _extra_block(day: str, is_live: bool, missing: List[dict]) -> Dict:
    """主力资金结构 / 板块强弱 / 情景推演概率 —— 三个"成因层"维度。

    ★ 全部零外部请求：`mainforce_state`、`scenario_watch` 是**日批落库**；
      板块占比走 `industry_amount_share`（读内存行情池 + 落库行业映射）。
    ★ 每个维度独立 try：任一失败只影响自己，不拖垮整页。
    """
    out: Dict = {}

    # ① 主力资金结构：全市场当日 phase 分布（钱在吸筹还是在出货）
    #   ★ 2026-11-02：`date` 与 `phase` 一次 GROUP BY 取回 —— 原来多跑一条 MAX(date)，
    #     跨境 570ms/次，白等半秒。
    try:
        rows = db.fetch(
            "SELECT date, phase, COUNT(*) AS n FROM mainforce_state "
            "WHERE date = (SELECT MAX(date) FROM mainforce_state WHERE date <= %s) "
            "GROUP BY date, phase", (day,)) or []
        dist = [{"phase": _PHASE_CN.get(str(r.get("phase") or ""), str(r.get("phase") or "未分类")),
                 "n": int(r.get("n") or 0)} for r in rows]
        dist.sort(key=lambda x: -x["n"])
        total = sum(x["n"] for x in dist)
        _d = str((rows[0].get("date") if rows else "") or "")[:10]
        out["mainforce"] = {"date": _d, "dist": dist, "total": total}
        if not total:
            missing.append({"block": "extra", "item": "主力资金结构",
                            "reason": "该日无主力行为快照（日批 19:00 后生成）"})
    except Exception as e:
        print(f"[amreview] mainforce dist failed: {e}")

    # ② 板块强弱：成交额占比 Top6（钱打到哪、哪个板块在放量）
    if is_live:
        try:
            from app.sector_industry import industry_amount_share
            s = industry_amount_share(top=6) or {}
            if s.get("available"):
                out["sectors"] = {
                    "rows": [{"industry": r.get("industry"),
                              "share": _num(r.get("share_pct")),
                              "chg": _num(r.get("avg_change_pct")),
                              "n": r.get("stock_n")}
                             for r in (s.get("rows") or [])],
                    "top5_share": _num(s.get("top5_share_pct")),
                    "as_of": s.get("as_of"),
                }
            else:
                missing.append({"block": "extra", "item": "板块成交占比",
                                "reason": "行情缓存未就绪（工作台轮询后可用）"})
        except Exception as e:
            print(f"[amreview] sectors failed: {e}")
    else:
        # 历史日：板块占比来自**实时内存池**，无法回溯 ⇒ 如实说明（不拿今天冒充）
        missing.append({"block": "extra", "item": "板块成交占比",
                        "reason": "历史日未留存（成交占比由实时行情聚合，盘中未落库）"})

    # ③ 情景推演：**当前情景名 + 倾向 + 多空信号数 + 上证锚点状态**
    #   ⚠️ 2026-11-02 修正：首版按"base/bull/bear 概率字典"写，实测 `load()` 不返回那种结构
    #      （`scenario` 是字符串、`lean` 是倾向、`anchor` 是上证锚点）⇒ 首版恒为空。
    try:
        sc = _scenario_of(day)
        anchor = (sc or {}).get("anchor") or {}
        sc = sc or {}
        if sc.get("scenario") or sc.get("lean"):
            out["scenario"] = {
                "date": str(sc.get("date") or "")[:10],
                "stale": bool(sc.get("stale")),
                "name": sc.get("scenario") or "",
                "lean": sc.get("lean") or "",
                "n_up": sc.get("n_up"),
                "n_down": sc.get("n_down"),
                "anchor_level": _num(anchor.get("level")),
                "anchor_state": anchor.get("state") or "",
                "note": str(sc.get("note") or "")[:80],
            }
    except Exception as e:
        print(f"[amreview] scenario failed: {e}")

    return out


def _conclusion(day: str) -> Dict:
    """早盘复盘的文字结论 = 当日 `lunchbreak` 复盘（11:32 自动生成）。"""
    try:
        from app.flash import store
        r = store.load_review(day=day, phase="lunchbreak") or {}
    except TypeError:
        # 兼容旧签名（尚无 date 参数时）：退回"最新一条"并标注
        try:
            from app.flash import store
            r = store.load_review("lunchbreak") or {}
            r["note"] = "该接口尚未支持按日取，返回的是最近一条复盘"
        except Exception:
            r = {}
    except Exception as e:
        print(f"[amreview] review failed: {e}")
        r = {}
    if not r:
        return {"markdown": "", "time": "", "phase": "lunchbreak", "signal": []}
    return {"markdown": r.get("markdown") or "", "time": r.get("time") or "",
            "phase": "lunchbreak", "signal": r.get("signals") or []}


# ── ⑥ 数据附录的「AI 解读」（按需生成，不阻塞主端点）──────────────────────

# {day: (ts, markdown)} —— 11:30 后数据冻结 ⇒ 同一天重复点"重新生成"才有必要
_DIGEST_CACHE: Dict[str, tuple] = {}
_DIGEST_TTL = 1800.0

_DIGEST_SYS = (
    "你是一名 A 股盘面解读员。**你只能使用消息里给出的数字**，一个都不许自己造。\n"
    "\n"
    "【绝对红线】\n"
    "A. 消息里没有出现过的**板块名、个股名、指数名、外盘品种、经济数据值**，"
    "一律不得出现在输出里 —— 包括「白酒 -2.8%」「纳指 -1.9%」「PMI 49.8」这种"
    "看起来很合理的数字，只要数据里没有，写了就是编造。\n"
    "B. 「⚠️ 数据缺失」段列出的条目，属于你**明确没有**的数据，"
    "不得给出该类的任何具体数值或结论。\n"
    "C. 消息里「可用数据」少于 4 项时，**只输出一行**："
    "「数据不足，暂不解读（缺：X、Y）」——不要凑条数，不要用常识补。\n"
    "D. 你需要计算时（如环比、占比）只能基于给出的数字，并把算式隐含在结论里；"
    "**禁止**引入外部数字（如成交量、换手率、北向资金）。\n"
    "\n"
    "【写作要求】\n"
    "1. 条数**由数据量决定**：数据充足 4~6 条，数据较少 2~3 条，"
    "**宁可 1 条都有出处，也不要 5 条有一半是编的**；每条一行，以 `- ` 开头；\n"
    "2. 每条必须是「数据 → 含义」的因果，**不允许只复述数字**；\n"
    "3. 优先使用成因层证据：主力资金结构（吸筹/拉升/出货家数）、"
    "板块成交占比（钱去了哪）、情景推演（系统对后市的先验）；\n"
    "4. 若确实存在**两组数据互相打架**，指出这处矛盾；没有就跳过，不要硬造；\n"
    "5. 若给了持仓，最后加 1 条说明对**该持仓结构**意味着什么"
    "（集中度/风格/浮亏），**不要给具体买卖指令**；\n"
    "6. 中文输出，不用表格、不用标题、不用 emoji。"
)


def _fmt_item(it: Dict) -> str:
    """单条数据 → 一行文本。**有涨跌幅就给带符号的百分比**，没有就标"无前值"。

    ★ 为什么必须区分（2026-11-02 实测发现）：`macro_daily_history` 缺前值时 delta 为 None，
      原来只输出裸价格（`布伦特原油：111.43`）⇒ 模型不知道是涨是跌，只能瞎猜或空泛带过，
      正好违反 system 里"不得编造"那一条。
    """
    label = it.get("label") or ""
    unit = it.get("unit") or ""
    delta = it.get("delta")
    if delta is not None:
        return f"- {label}：{float(delta):+.2f}{unit}"
    val = it.get("value")
    if unit and unit not in ("%",):
        return f"- {label}：{val} {unit}"          # 数值与单位之间留空格（`1100 家`）
    cal = str(it.get("caliber") or "")
    tail = "（无前值，仅为水平值）" if "无前值" in cal else ""
    return f"- {label}：{val}{unit}{tail}"


def _digest_user_text(payload: Dict) -> str:
    """把**全部可用维度**压成一段紧凑文本喂给 LLM（只给事实，不给结论）。

    ★★ 2026-11-02（用户："提示词还不够，项目里有大把有用的数据没用上"）：
      原来只喂了盘面 4 项 + 宏观 5 项 + 消息 1 条 ⇒ 只有 246 字符，
      模型除了"涨跌家数多"说不出别的。现在补上**成因层**与**结构层**：
        ① 主力资金结构（吸筹/拉升/出货 家数）—— 回答"钱在做什么"
        ② 板块成交占比 Top6 + 涨跌 —— 回答"钱去了哪里"
        ③ 情景推演概率 —— 回答"系统自己怎么看后市"
        ④ 持仓结构（市值/集中度/风格）—— 回答"对这个人意味着什么"
        ⑤ 每条数据的**口径**（是否 11:30 定稿 / 是否快照）—— 让模型知道自己看的是什么
      仍然坚持：只喂数字与口径，**不喂结论**（结论是模型要产出的东西）。
    """
    lines = [f"# {payload.get('date')} 早盘（09:30-11:30）数据"]
    blocks = payload.get("blocks") or {}
    for key, name in (("market", "盘面"), ("macro", "宏观"), ("news", "消息")):
        items = blocks.get(key) or []
        if not items:
            continue
        lines.append(f"\n## {name}")
        for it in items:
            # 消息面是长文本，原样给（它本身就是"发生了什么"）
            if key == "news":
                lines.append(f"- {(it.get('label') or '')}：{(it.get('value') or '')}")
            else:
                lines.append(_fmt_item(it))

    ex = payload.get("extras") or {}
    mf = ex.get("mainforce") or {}
    if mf.get("total"):
        seg = "、".join(f"{x['phase']} {x['n']} 只" for x in (mf.get("dist") or [])[:6])
        lines.append(f"\n## 主力资金结构（{mf.get('date')} 全市场 {mf['total']} 只）")
        lines.append(f"- {seg}")

    sec = ex.get("sectors") or {}
    if sec.get("rows"):
        lines.append("\n## 板块成交占比 Top6（钱去了哪里）")
        for r in sec["rows"]:
            chg = r.get("chg")
            chg_s = f"{float(chg):+.2f}%" if chg is not None else "涨跌未知"
            share = f"{float(r['share']):.1f}%" if r.get("share") is not None else "—"
            lines.append(f"- {r.get('industry')}：成交占比 {share}，板块涨跌 {chg_s}，{r.get('n') or '—'} 只")
        if sec.get("top5_share") is not None:
            lines.append(f"- Top5 合计占 {float(sec['top5_share']):.1f}%（越高说明资金越集中）")

    sc = ex.get("scenario") or {}
    if sc.get("name") or sc.get("lean"):
        head = f"\n## 情景推演（{sc.get('date')}）"
        lines.append(head)
        parts = []
        if sc.get("name"):
            parts.append(f"当前情景「{sc['name']}」")
        if sc.get("lean"):
            parts.append(f"倾向 {sc['lean']}")
        if sc.get("n_up") is not None and sc.get("n_down") is not None:
            parts.append(f"信号 多{sc['n_up']}/空{sc['n_down']}")
        if sc.get("anchor_level") is not None:
            parts.append(f"上证锚点 {sc['anchor_level']:.0f} {sc.get('anchor_state') or ''}".strip())
        if parts:
            lines.append("- " + "；".join(parts))
        if sc.get("note"):
            lines.append(f"- 说明：{sc['note']}")

    h = payload.get("holdings") or {}
    st = h.get("structure") or {}
    if h.get("available") and (h.get("actions") or st):
        lines.append("\n## 持仓（供你判断盘面对这个人的实际影响）")
        # ★ 2026-11-07：与前端同一处精度问题 —— 3050 元写成"0 万元"会让 LLM
        #   顺着说"持仓市值为 0"，而用户明明有仓。小市值按元说，别硬折成"万"。
        _mv = float(st.get("total_mv") or 0)
        if _mv > 0:
            lines.append(f"- 持仓市值约 {_mv / 10000:.1f} 万元" if _mv >= 10000
                         else f"- 持仓市值约 {int(_mv)} 元")
        if st.get("concentration_note"):
            lines.append(f"- 集中度：{st['concentration_note']}")
        if st.get("style"):
            lines.append(f"- 风格：{st['style']}")
        for a in (h.get("actions") or [])[:6]:
            lines.append(f"- [{a.get('priority')}] {a.get('name') or ''} {a.get('text')}")

    # 口径提示：让模型知道哪些数字是"定稿"、哪些是"实时快照"
    # ⚠️ 过滤掉兜底文案（"无解读映射"这种对模型零信息量的占位）
    calibers = {str(it.get("caliber") or "") for lst in blocks.values() for it in (lst or [])}
    calibers = {c for c in calibers if c and "无解读映射" not in c}
    if calibers:
        lines.append("\n## 数据口径（判断可信度用）")
        for c in list(calibers)[:5]:
            lines.append(f"- {c}")

    # ★★ 2026-11-02（**严重**）：`missing` 原来**根本没进 prompt**。
    #   后果实测：今天盘面/成交额/指数/板块/快讯全空，模型仍按"4~6 条"输出，
    #   于是编出「白酒 -2.8%」「纳指 -1.9%」「半日成交 9842 亿」「PMI 49.8」
    #   「水泥/钢铁跌幅小于指数」——**每条都像真的，但没有一条有出处**。
    #   必须把"你缺什么"显式告诉模型，并把"可用条数"摆在最前面。
    counts = {k: len(v or []) for k, v in blocks.items()}
    lines.insert(1, f"\n可用数据：盘面 {counts.get('market', 0)} 项 / "
                     f"宏观 {counts.get('macro', 0)} 项 / 消息 {counts.get('news', 0)} 项")
    miss = payload.get("missing") or []
    if miss:
        lines.append("\n## ⚠️ 数据缺失（以下内容你没有，**禁止推测、禁止补数字**）")
        seen = set()
        for m in miss[:12]:
            item = str(m.get("item") or "")
            if item in seen:
                continue
            seen.add(item)
            lines.append(f"- {item}：{m.get('reason')}")

    lines.append("\n请按系统要求输出解读。")
    return "\n".join(lines)


@router.get("/amreview/digest")
def am_review_digest_get(date: str = None):
    """**只读**：返回已生成的 AI 解读（绝不触发 LLM）。"""
    day = str(date or _today())[:10]
    hit = _DIGEST_CACHE.get(day)
    if not hit:
        return {"ok": True, "date": day, "markdown": "", "cached": False,
                "hint": "尚未生成；点「AI 解读」生成（消耗 1 次 LLM）"}
    return {"ok": True, "date": day, "markdown": hit[1], "cached": True,
            "generated_at": rules.beijing_now().strftime("%H:%M")}


@router.post("/amreview/digest")
def am_review_digest_run(date: str = None, force: bool = False):
    """生成（或强制重生成）数据附录的 AI 解读 —— **同步 LLM 调用，十几秒**。

    ★ 为什么单独开端点而不是塞进 `/amreview`：
      主端点是"打开标签页就要出内容"的关键路径，塞一次 LLM 会把首屏拖成十几秒
      （本项目刚因"展示型接口现场重算"吃过 114 秒的亏）。
    """
    import time as _t
    day = str(date or _today())[:10]
    now = _t.time()
    hit = _DIGEST_CACHE.get(day)
    if hit and not force and now - float(hit[0] or 0) < _DIGEST_TTL:
        return {"ok": True, "date": day, "markdown": hit[1], "cached": True}

    try:
        from app.flash.llm import call_llm, llm_configured, last_llm_error
    except Exception as e:
        return {"ok": False, "error": f"LLM 模块不可用: {str(e)[:80]}"}
    if not llm_configured():
        return {"ok": False, "error": "未配置 LLM（LLM_API_KEY / LLM_FREE_*）"}

    # 复用主端点（带 300s 缓存）拿数据，保证"AI 解读看到的"与"页面上显示的"是同一份
    payload = am_review(day)

    # ★★ 2026-11-02：**硬门**。没有盘面数据就根本不该叫"早盘复盘"，
    #   更不能让模型拿 4 行输入硬凑成 5 条结论（实测会编出板块涨跌/外盘/成交额）。
    #   这里直接拒绝、**不烧 LLM**，并把缺失原因原样返回给前端。
    _blocks = payload.get("blocks") or {}
    _counts = {k: len(v or []) for k, v in _blocks.items()}
    _total = sum(_counts.values())
    if not _counts.get("market"):
        _DIGEST_CACHE.pop(day, None)      # 清掉可能已缓存的"编造版"解读
        return {"ok": False, "date": day, "blocked": True,
                "error": "早盘盘面数据未就绪（涨跌家数 / 涨跌停 / 成交额 / 指数均无），"
                         "已停止生成，避免编造",
                "missing": payload.get("missing") or [],
                "hint": "打开工作台让它轮询一次（约 60 秒，指数/板块依赖进程缓存），"
                        "或等 11:30 早盘定稿落库后再试"}
    if _total < 4:
        return {"ok": False, "date": day, "blocked": True,
                "error": f"可用数据仅 {_total} 项，不足以支撑解读",
                "missing": payload.get("missing") or [],
                "hint": "等数据补齐后再试"}

    user = _digest_user_text(payload)
    md = ""
    try:
        md = (call_llm(_DIGEST_SYS, user, temperature=0.3, tier="slow") or "").strip()
    except Exception as e:
        return {"ok": False, "error": f"调用失败: {str(e)[:100]}"}
    if not md:
        return {"ok": False, "error": last_llm_error() or "LLM 返回空（可能熔断/超时）"}

    _DIGEST_CACHE[day] = (now, md)
    print(f"[amreview] AI digest generated {day} len={len(md)}")
    return {"ok": True, "date": day, "markdown": md, "cached": False,
            "generated_at": rules.beijing_now().strftime("%H:%M")}


@router.get("/amreview")
def am_review(date: str = None):
    """早盘复盘聚合数据（盘面 / 宏观 / 消息 + 午后研判结论）。"""
    import time as _t

    day = str(date or _today())[:10]
    now = _t.time()
    hit = _CACHE.get(day)
    if hit and now - float(hit[0] or 0) < _TTL:
        return hit[1]

    is_live = day == _today()
    # ★★ 四块**并发**取数。Supabase 每次往返约 570ms、这里共 5 次查询
    #   ⇒ 串行约 3~8 秒（实测），并发后压到最慢那一块（约 1~2 秒）。
    #   `app.database` 的连接是**线程本地**复用，多线程各持一条，安全。
    miss_m: List[dict] = []
    miss_c: List[dict] = []
    miss_n: List[dict] = []
    miss_h: List[dict] = []
    miss_e: List[dict] = []
    try:
        ex = _executor()
        f_mkt = ex.submit(_market_block, day, is_live, miss_m)
        f_mac = ex.submit(_macro_block, day, miss_c)
        f_new = ex.submit(_news_block, day, miss_n)
        f_hol = ex.submit(_holdings_block, is_live, miss_h)
        f_ext = ex.submit(_extra_block, day, is_live, miss_e)
        f_con = ex.submit(_conclusion, day)
        market_items, macro_items = f_mkt.result(), f_mac.result()
        news_items, conclusion = f_new.result(), f_con.result()
        holdings, extras = f_hol.result(), f_ext.result()
    except Exception as e:
        # 极端情况下（线程池不可用）退回串行 —— 慢一点，但绝不返回空页
        print(f"[amreview] parallel failed, fallback to serial: {e}")
        miss_m, miss_c, miss_n, miss_h, miss_e = [], [], [], [], []
        market_items = _market_block(day, is_live, miss_m)
        macro_items = _macro_block(day, miss_c)
        news_items = _news_block(day, miss_n)
        holdings = _holdings_block(is_live, miss_h)
        extras = _extra_block(day, is_live, miss_e)
        conclusion = _conclusion(day)
    missing = miss_m + miss_c + miss_n + miss_h + miss_e

    trace = [{"block": b, "item": it["label"], "source": it["source"], "as_of": it["as_of"]}
             for b, lst in (("market", market_items), ("macro", macro_items), ("news", news_items))
             for it in lst]

    out = {
        "ok": True,
        "date": day,
        "is_live": is_live,
        "as_of": rules.beijing_now().strftime("%Y-%m-%d %H:%M"),
        # ★ 2026-11-01（用户："减少数据罗列…重点转向异动解读"）：
        #   三块数据仍在 `blocks` 里，但**前端默认折叠**（数据附录）；
        #   页面的主角变成 `holdings.actions / holdings.items`（结论与行动）。
        "holdings": holdings,
        # ★ 2026-11-02：补充维度（主力资金结构 / 板块占比 / 情景概率）。
        #   只喂 AI 解读、**不进页面主表** —— 页面已经"减少数据罗列"了，
        #   这些是"解释成因"的原料，摆出来反而增加信息过载。
        "extras": extras,
        "blocks": {"market": market_items, "macro": macro_items, "news": news_items},
        "conclusion": conclusion,
        "trace": trace,
        "missing": missing,
    }
    # 按日缓存 + 轻量淘汰（只保留最近几个日期，防回放翻页时无限增长）
    _CACHE[day] = (now, out)
    if len(_CACHE) > _CACHE_MAX:
        for k in sorted(_CACHE, key=lambda x: _CACHE[x][0])[:len(_CACHE) - _CACHE_MAX]:
            _CACHE.pop(k, None)
    return out
