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
        missing.append({"block": "market", "item": "涨跌家数 / 涨跌停 / 连板 / 判读",
                        "reason": "该日未留存（market_emotion_daily 无 am_* 也无 close_*）"})

    amt = _num(row.get("am_amount_yi")) or _am_amount(day)
    if amt is not None:
        items.append({"label": "半日成交额（截至 11:30）", "value": f"{amt:,.0f}", "unit": "亿元",
                      "source": "market_amount", "as_of": "11:30",
                      "caliber": "两市（沪+深）累计，不含北交所"})
    else:
        missing.append({"block": "market", "item": "半日成交额",
                        "reason": "该日未留存（market_amount_daily 无分时序列）"})

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
                          "caliber": f"量化引擎 state={r['state']}"})
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
                            "reason": "该日附近无 macro_daily_history 记录"})
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


# ── ④ 结论（午后研判）────────────────────────────────────────────────────

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
    try:
        ex = _executor()
        f_mkt = ex.submit(_market_block, day, is_live, miss_m)
        f_mac = ex.submit(_macro_block, day, miss_c)
        f_new = ex.submit(_news_block, day, miss_n)
        f_con = ex.submit(_conclusion, day)
        market_items, macro_items = f_mkt.result(), f_mac.result()
        news_items, conclusion = f_new.result(), f_con.result()
    except Exception as e:
        # 极端情况下（线程池不可用）退回串行 —— 慢一点，但绝不返回空页
        print(f"[amreview] parallel failed, fallback to serial: {e}")
        miss_m, miss_c, miss_n = [], [], []
        market_items = _market_block(day, is_live, miss_m)
        macro_items = _macro_block(day, miss_c)
        news_items = _news_block(day, miss_n)
        conclusion = _conclusion(day)
    missing = miss_m + miss_c + miss_n

    trace = [{"block": b, "item": it["label"], "source": it["source"], "as_of": it["as_of"]}
             for b, lst in (("market", market_items), ("macro", macro_items), ("news", news_items))
             for it in lst]

    out = {
        "ok": True,
        "date": day,
        "is_live": is_live,
        "as_of": rules.beijing_now().strftime("%Y-%m-%d %H:%M"),
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
