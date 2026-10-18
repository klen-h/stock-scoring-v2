# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】市场底确认清单（policy bottom watch）—— **纯观察器，不产生任何买卖信号**
================================================================================
背景（2026-10-18，用户提供研判 + 拍板落地）：
  "政策底轮廓已立，但市场底通常滞后 1~3 个月；确认要看放量、赚钱效应重建。"
  ⇒ 把"等市场底"这件拍脑袋的事，变成**每天可打勾的 5 条清单**。

★★ 预登记声明（红线，改之前先读）
  1. 本模块是**观察器**：不改分数、不参与排序、不进闸门、不改 regime 四态、不碰模拟盘准入。
     删掉本模块，系统其余部分照常运行。
  2. 它**不产生买卖信号**。`position_hint` 只是一句**提示文案**（"建议总仓位上限 30%"），
     **不自动执行**任何仓位动作。
  3. ★ 原始需求里"用试错仓介入防御主线"**本项目不做**：与 2026-10-18 已拍板
     "不开影子仓、防御市禁入场"直接冲突。若将来要做，须以"清单达成日的前瞻收益"为对象
     **另行预登记**（Phase 2），不得直接接进模拟盘/仓位。

五条判据（阈值全部 env 可调，见下方常量）：
  ① 汇金/国新等国家队新公告        —— **人工打勾**（无结构化数据源；快讯只做提示，打勾权在人）
  ② 两市成交连续地量 < 13000 亿      —— 自动（market_amount_daily → intraday_path 回退）
  ③ 创业板指周线止跌（周线转涨）      —— 自动（指数日 K 聚合周线）
  ④ 涨跌比连续 2 日 > 0.8           —— 自动（market_emotion_daily.close_up/close_down）
  ⑤ 10Y 美债 < 5.0% 或 布伦特 < 95  —— 自动（macro_daily 最新快照）

★★ 数据口径的坑（实现时踩到，后续别改回去）
  · `market_emotion_daily.up/down` 记录于 **00:0x / 09:10**（当日开盘前）⇒ 实际是**前一交易日**的宽度，
    **不能**当"当日收盘涨跌比"。要用 `close_up/close_down`。
  · `close_up/close_down` 还会**冻结**（实测 10-15 与 10-16 都是 388/4607，10-16 盘后没刷新）
    ⇒ 必须校验 `close_as_of` 的日期 == 行日期，且不得与前一行完全相同，否则该日标 `insufficient`
    （**缺失 ≠ 不满足**，不许当 false 处理）。
  · `market_amount_daily` 是懒建表（`market_amount.save_daily()` 首次写入才建），当前库**还没有**；
    一级源缺失时回退 `intraday_path`（`sh000001` + `sz399001` 末点 `cum_amount` 相加），起 2026-10-09。
  · `macro_daily.date` 是 TEXT 且**混着 `2026/9/7` 斜杠格式** ⇒ 不能字符串排序，按 replace('/','-') 排。
================================================================================
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from app.database import db

# ── 阈值（env 可调；改阈值请在文档里留痕，别静默调） ─────────────────────────
PBW_AMOUNT_YI = float(os.environ.get("PBW_AMOUNT_YI") or 13000)   # 两市全天成交额"地量"门槛（亿元）
PBW_AMOUNT_DAYS = int(os.environ.get("PBW_AMOUNT_DAYS") or 3)      # 连续几个交易日均低于门槛
PBW_RATIO = float(os.environ.get("PBW_RATIO") or 0.8)              # 涨跌比门槛
PBW_RATIO_DAYS = int(os.environ.get("PBW_RATIO_DAYS") or 2)        # 连续几个交易日
PBW_US10Y = float(os.environ.get("PBW_US10Y") or 5.0)              # 美债 10Y 水平门槛（%）
PBW_BRENT = float(os.environ.get("PBW_BRENT") or 95.0)             # 布伦特门槛（美元）
PBW_PASS = int(os.environ.get("PBW_PASS") or 4)                    # 清单达成数（含）即"确认"
PBW_POSITION_CAP = float(os.environ.get("PBW_POSITION_CAP") or 30)  # 未达成时的**提示**仓位上限（%）

_TABLE_READY = False


def _default_day() -> str:
    """默认交易日 = **最近已完成交易日**（与 `routers/market._price_date` 同口径）。

    ★ 不能用"今天"：周日/节假日跑批时"今天"没有任何行情，会把清单算在空日上，
      展示出来就是"今天的清单"（实际数据是上一交易日的）—— 与本项目反复修的
      "写入时刻冒充数据时刻"是同一类错误。
    """
    try:
        from app.flash.rules import latest_completed_trading_day
        return str(latest_completed_trading_day())[:10]
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


# ────────────────────────────────────────────────────────────────
#  建表
# ────────────────────────────────────────────────────────────────
def ensure_table() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS policy_bottom_watch (
            date TEXT PRIMARY KEY,
            checklist_json TEXT NOT NULL,
            n_checked INTEGER,
            n_total INTEGER,
            crossed_4 INTEGER DEFAULT 0,
            manual_nation_team INTEGER DEFAULT 0,
            manual_note TEXT,
            manual_at TEXT,
            created_at TEXT
        )
    """)
    _TABLE_READY = True


# ────────────────────────────────────────────────────────────────
#  数据读取（全部读既有表，零外部请求）
# ────────────────────────────────────────────────────────────────
def _num(v) -> Optional[float]:
    try:
        f = float(str(v).strip())
        return f
    except (TypeError, ValueError):
        return None


def _amount_series(limit: int = 12, as_of: str = None) -> Tuple[List[Tuple[str, float]], str]:
    """两市全天成交额序列 [(date, 亿元)]，升序。返回 (序列, 来源说明)。

    一级源 `market_amount_daily`（懒建表，可能不存在）；二级源 `intraday_path`
    （沪指 + 深成指分时末点 `cum_amount` 相加，2026-10-09 起）。

    ★ `as_of`：只取 **该日及之前**的数据 —— 回放历史日时绝不能用到"未来"的成交额
      （前视偏差）；不给则取最新。
    """
    cutoff = str(as_of or "")[:10] or None
    rows = []
    try:
        if cutoff:
            rows = db.fetch("SELECT date, amount_yi FROM market_amount_daily "
                            f"WHERE date <= %s ORDER BY date DESC LIMIT {int(limit)}",
                            (cutoff,)) or []
        else:
            rows = db.fetch("SELECT date, amount_yi FROM market_amount_daily "
                            f"ORDER BY date DESC LIMIT {int(limit)}") or []
        out = [(str(r["date"])[:10], _num(r["amount_yi"])) for r in rows]
        out = [(d, v) for d, v in out if d and v]
        if out:
            return sorted(out), "market_amount_daily"
    except Exception:
        pass
    # 回退：指数分时末点累计成交额
    try:
        if cutoff:
            rows = db.fetch(
                "SELECT date, path_json FROM intraday_path "
                "WHERE code IN ('sh000001','sz399001') AND date <= %s "
                "ORDER BY date DESC", (cutoff,))
        else:
            rows = db.fetch(
                "SELECT date, path_json FROM intraday_path "
                "WHERE code IN ('sh000001','sz399001') "
                "ORDER BY date DESC")
    except Exception:
        return [], "none"
    agg: Dict[str, float] = {}
    for r in rows:
        d = str(r["date"])[:10]
        p = r.get("path_json")
        try:
            if isinstance(p, str):
                p = json.loads(p)
            last = p[-1]
            amt = float(last[3])          # [HHMM, price, cum_vol, cum_amount]
        except Exception:
            continue
        agg[d] = agg.get(d, 0.0) + amt
    out = sorted((d, v / 1e8) for d, v in agg.items())
    return out[-limit:], ("intraday_path" if out else "none")


def _breadth_series(limit: int = 10, as_of: str = None) -> Tuple[List[Tuple[str, float]], List[str]]:
    """收盘涨跌比序列 [(date, up/down)]，升序；同时返回"被剔除的无效日"清单。

    ★ 有效性校验（见模块头"数据口径的坑"）：
      · `close_as_of` 的日期必须等于行日期（否则是别的时点写的）；
      · 不得与前一行 `close_up/close_down` 完全相同（冻结检测）。
    无效日**不进序列**（调用方据此判 insufficient），**不当作 0**。
    """
    cutoff = str(as_of or "")[:10] or None
    try:
        if cutoff:
            rows = db.fetch(
                "SELECT date, close_as_of, close_up, close_down FROM market_emotion_daily "
                f"WHERE date <= %s ORDER BY date DESC LIMIT {int(limit) + 4}",
                (cutoff,)) or []
        else:
            rows = db.fetch(
                "SELECT date, close_as_of, close_up, close_down FROM market_emotion_daily "
                f"ORDER BY date DESC LIMIT {int(limit) + 4}") or []
    except Exception:
        return [], ["market_emotion_daily 不可读"]
    out, bad = [], []
    prev = None
    for r in reversed(rows):                     # 升序处理，便于冻结检测
        d = str(r["date"])[:10]
        asof = str(r.get("close_as_of") or "")[:10]
        up, down = _num(r.get("close_up")), _num(r.get("close_down"))
        if not up or not down or down <= 0:
            bad.append(f"{d}(close 缺失)")
            prev = (up, down)
            continue
        if asof != d:
            bad.append(f"{d}(close_as_of={asof or '空'})")
            prev = (up, down)
            continue
        if prev == (up, down):
            bad.append(f"{d}(与前一日完全相同=疑似冻结)")
            continue
        out.append((d, round(up / down, 3)))
        prev = (up, down)
    return out[-limit:], bad


def _chinext_weekly(as_of: str = None) -> Dict:
    """创业板指周线止跌：最近一周（含未走完）收盘 vs 上一周收盘。

    checked = 本周收盘 > 上周收盘（即周线转涨）。周线由日 K 按 ISO 周聚合。
    ★ `as_of`：只取该日及之前的 K 线（回放历史日时不许看未来）。
    """
    try:
        from app.contradictions.scanner import _load_index_kline
        bars = _load_index_kline("399006", count=80) or []
    except Exception as e:
        return {"checked": False, "insufficient": True, "detail": f"取数失败: {str(e)[:60]}"}
    cutoff = str(as_of or "")[:10]
    if cutoff:
        bars = [b for b in bars if str(b.get("date") or "")[:10] <= cutoff]
    if len(bars) < 15:
        return {"checked": False, "insufficient": True,
                "detail": f"日K不足（{len(bars)} 根，需 ≥15）"}
    weeks: List[Tuple[str, float]] = []
    cur_key, cur_close = None, None
    for b in bars:
        d = str(b.get("date") or "")[:10]
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            continue
        key = f"{dt.isocalendar()[0]}-W{dt.isocalendar()[1]:02d}"
        if key != cur_key:
            if cur_key is not None:
                weeks.append((cur_key, cur_close))
            cur_key, cur_close = key, float(b["close"])
        else:
            cur_close = float(b["close"])
    if cur_key is not None:
        weeks.append((cur_key, cur_close))
    if len(weeks) < 3:
        return {"checked": False, "insufficient": True, "detail": f"周线不足（{len(weeks)} 周）"}
    (w0, c0), (w1, c1), (w2, c2) = weeks[-3], weeks[-2], weeks[-1]
    chg_prev = (c1 / c0 - 1) * 100
    chg_now = (c2 / c1 - 1) * 100
    return {
        "checked": c2 > c1,
        "insufficient": False,
        "detail": (f"本周 {w2} 收 {c2:.1f} vs 上周 {c1:.1f}（{chg_now:+.2f}%）；"
                   f"再前周 {chg_prev:+.2f}%"),
        "value": round(chg_now, 2),
    }


def _parse_macro_date(s) -> Optional[datetime]:
    """`macro_daily.date` 兼容 `2026-10-16` / `2026/9/7` 两种写法。

    ★★ 只 `replace('/','-')` **不够**：`2026-9-7` 与 `2026-10-16` 按字符串比时
      `'2026-1' < '2026-9'` ⇒ **10 月排在 9 月前面**（实测因此取到了 09-07 的陈旧快照，
      us10y 4.78 被当成"最新"）。必须解析成 datetime 再排。
    """
    s = str(s or "").strip()[:10].replace("/", "-")
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _macro_snapshot(as_of: str = None) -> Dict:
    """macro_daily 最新一条的 us10y / brent（读库，零外部请求）。

    ★ `date` 是 TEXT 且混着 `2026/9/7` 斜杠格式 ⇒ **解析成 datetime 后排序**（见上）。
      逐条尝试直到解析出需要的键（有些行 data_json 可能不完整）。
    """
    try:
        rows = db.fetch("SELECT date, data_json FROM macro_daily") or []
    except Exception as e:
        return {"ok": False, "detail": f"macro_daily 不可读: {str(e)[:60]}"}
    cutoff = str(as_of or "")[:10]
    cut_dt = None
    if cutoff:
        try:
            cut_dt = datetime.strptime(cutoff, "%Y-%m-%d")
        except ValueError:
            cut_dt = None
    parsed = []
    for r in rows:
        dt = _parse_macro_date(r.get("date"))
        if dt is None:
            continue
        if cut_dt is not None and dt > cut_dt:
            continue
        parsed.append((dt, r))
    parsed.sort(key=lambda x: x[0], reverse=True)
    for dt, r in parsed[:6]:
        try:
            d = json.loads(r.get("data_json") or "{}")
        except (ValueError, TypeError):
            continue
        panel = d.get("panel") or {}
        us10y = _num((panel.get("us10y") or {}).get("price"))
        brent = _num((panel.get("brent") or {}).get("price"))
        if us10y is not None or brent is not None:
            return {"ok": True, "us10y": us10y, "brent": brent,
                    "date": dt.strftime("%Y-%m-%d"),
                    "generated_at": d.get("generated_at")}
    return {"ok": False, "detail": "macro_daily 近 6 条都解析不出 us10y/brent"}


# ────────────────────────────────────────────────────────────────
#  判定
# ────────────────────────────────────────────────────────────────
def _manual_of(day: str) -> Dict:
    ensure_table()
    try:
        r = db.fetch_one("SELECT manual_nation_team, manual_note, manual_at "
                         "FROM policy_bottom_watch WHERE date = %s", (day,))
    except Exception:
        return {}
    if not r:
        return {}
    return {"checked": bool(r.get("manual_nation_team")), "note": r.get("manual_note") or "",
            "ts": str(r.get("manual_at") or "")}


def evaluate(day: str = None, manual: Dict = None) -> Dict:
    """计算某日清单。`manual` 不传时读库中该日的人工打勾记录。

    返回结构见模块头/计划（items + n_checked + crossed_4 + position_hint）。
    """
    ensure_table()
    day = str(day or _default_day())[:10]
    manual = manual if manual is not None else _manual_of(day)

    items: Dict[str, Dict] = {}

    # ① 国家队公告（人工）
    items["nation_team_ann"] = {
        "checked": bool((manual or {}).get("checked")),
        "source": "manual",
        "note": (manual or {}).get("note") or "未确认（有公告请人工打勾）",
        "ts": (manual or {}).get("ts") or "",
    }

    # ② 成交连续地量
    series, src = _amount_series(limit=max(PBW_AMOUNT_DAYS + 2, 6), as_of=day)
    last_n = [v for _, v in series[-PBW_AMOUNT_DAYS:]]
    if len(series) < PBW_AMOUNT_DAYS:
        items["amount_dry_up"] = {
            "checked": False, "source": "auto", "insufficient": True,
            "value": round(series[-1][1], 0) if series else None, "unit": "亿",
            "detail": (f"历史不足（{len(series)}/{PBW_AMOUNT_DAYS} 个交易日，源={src}）"
                       " ⇒ 从上线日起积累，不猜"),
        }
    else:
        items["amount_dry_up"] = {
            "checked": all(v < PBW_AMOUNT_YI for v in last_n),
            "source": "auto", "insufficient": False,
            "value": [round(v, 0) for v in last_n], "unit": "亿",
            "window": f"近{PBW_AMOUNT_DAYS}日", "source_detail": src,
            "detail": (f"近{PBW_AMOUNT_DAYS}日 {'/'.join(f'{v:.0f}' for v in last_n)} 亿"
                       f"（门槛 <{PBW_AMOUNT_YI:.0f} 亿，源={src}）"),
        }

    # ③ 创业板周线止跌
    cw = _chinext_weekly(as_of=day)
    items["chinext_weekly"] = {"source": "auto", **cw}

    # ④ 涨跌比连续 2 日 > 0.8
    bseries, bbad = _breadth_series(limit=max(PBW_RATIO_DAYS + 3, 5), as_of=day)
    last_r = [v for _, v in bseries[-PBW_RATIO_DAYS:]]
    if len(bseries) < PBW_RATIO_DAYS:
        items["breadth_ratio_2d"] = {
            "checked": False, "source": "auto", "insufficient": True,
            "values": last_r,
            "detail": (f"有效历史不足（{len(bseries)}/{PBW_RATIO_DAYS} 日）"
                       + (f"；剔除 {', '.join(bbad[-3:])}" if bbad else "")),
        }
    else:
        items["breadth_ratio_2d"] = {
            "checked": all(v > PBW_RATIO for v in last_r),
            "source": "auto", "insufficient": False,
            "values": last_r,
            "detail": (f"近{PBW_RATIO_DAYS}日 涨跌比 {'/'.join(f'{v:.2f}' for v in last_r)}"
                       f"（门槛 >{PBW_RATIO:g}）"
                       + (f"；剔除 {', '.join(bbad[-2:])}" if bbad else "")),
        }

    # ⑤ 10Y / 油价
    m = _macro_snapshot(as_of=day)
    if not m.get("ok"):
        items["us10y_or_oil"] = {"checked": False, "source": "auto", "insufficient": True,
                                 "detail": m.get("detail") or "宏观快照不可用"}
    else:
        us, br = m.get("us10y"), m.get("brent")
        ok_us = (us is not None and us < PBW_US10Y)
        ok_oil = (br is not None and br < PBW_BRENT)
        parts = []
        if us is not None:
            parts.append(f"10Y {us:.2f}%（门槛 <{PBW_US10Y:g}%）")
        if br is not None:
            parts.append(f"布伦特 {br:.1f}（门槛 <{PBW_BRENT:g}）")
        items["us10y_or_oil"] = {
            "checked": bool(ok_us or ok_oil),
            "source": "auto", "insufficient": (us is None and br is None),
            "detail": "、".join(parts) or "无数据",
            "as_of": m.get("generated_at") or m.get("date"),
        }

    n_checked = sum(1 for v in items.values() if v.get("checked"))
    n_total = len(items)
    prev_n = _prev_checked(day)
    crossed = bool(n_checked >= PBW_PASS and (prev_n is None or prev_n < PBW_PASS))

    if n_checked >= PBW_PASS:
        hint = (f"清单 {n_checked}/{n_total} 已达 {PBW_PASS} 条门槛 ⇒ "
                "市场底**候选确认**（仍属观察结论，不自动改仓位）")
    else:
        hint = (f"清单 {n_checked}/{n_total} 未达 {PBW_PASS} 条 ⇒ "
                f"建议总仓位上限 {PBW_POSITION_CAP:g}%（**提示，不自动执行**）")

    return {"date": day, "items": items, "n_checked": n_checked, "n_total": n_total,
            "crossed_4": crossed, "position_hint": hint,
            "prev_n_checked": prev_n}


def _prev_checked(day: str) -> Optional[int]:
    try:
        r = db.fetch_one("SELECT n_checked FROM policy_bottom_watch WHERE date < %s "
                         "ORDER BY date DESC LIMIT 1", (day,))
        return int(r["n_checked"]) if r and r.get("n_checked") is not None else None
    except Exception:
        return None


# ────────────────────────────────────────────────────────────────
#  落库 / 读取 / 人工打勾
# ────────────────────────────────────────────────────────────────
def save(data: Dict) -> bool:
    """幂等落库（同日覆盖，但**保留人工打勾**）。"""
    ensure_table()
    day = str(data.get("date"))[:10]
    manual = _manual_of(day)
    try:
        db.upsert("policy_bottom_watch", {
            "date": day,
            "checklist_json": json.dumps(
                {k: v for k, v in data.items() if k != "date"}, ensure_ascii=False),
            "n_checked": int(data.get("n_checked") or 0),
            "n_total": int(data.get("n_total") or 5),
            "crossed_4": 1 if data.get("crossed_4") else 0,
            "manual_nation_team": 1 if manual.get("checked") else 0,
            "manual_note": manual.get("note") or "",
            "manual_at": manual.get("ts") or "",
            "created_at": datetime.now().isoformat(),
        }, conflict_columns=["date"])
        return True
    except Exception as e:
        print(f"[pbw] 落库失败: {str(e)[:100]}")      # ASCII（铁律⑥）
        return False


def load(day: str = None) -> Optional[Dict]:
    ensure_table()
    day = str(day or _default_day())[:10]
    try:
        r = db.fetch_one("SELECT * FROM policy_bottom_watch WHERE date = %s", (day,))
    except Exception:
        return None
    if not r:
        return None
    d = dict(r)
    try:
        body = json.loads(d.get("checklist_json") or "{}")
    except (ValueError, TypeError):
        body = {}
    body["date"] = day
    body["manual"] = {"checked": bool(d.get("manual_nation_team")),
                      "note": d.get("manual_note") or "", "ts": str(d.get("manual_at") or "")}
    return body


def confirm_nation_team(day: str, checked: bool, note: str = "") -> Dict:
    """第①条人工打勾（唯一人工项）。打勾后**立即重算**当日清单并落库。"""
    ensure_table()
    day = str(day or _default_day())[:10]
    ts = datetime.now().isoformat()
    manual = {"checked": bool(checked), "note": note or "", "ts": ts}
    data = evaluate(day, manual=manual)
    # evaluate 已带 manual；save 会从库里读旧 manual ⇒ 先把 manual 写进去
    try:
        db.upsert("policy_bottom_watch", {
            "date": day,
            "checklist_json": json.dumps(
                {k: v for k, v in data.items() if k != "date"}, ensure_ascii=False),
            "n_checked": int(data.get("n_checked") or 0),
            "n_total": int(data.get("n_total") or 5),
            "crossed_4": 1 if data.get("crossed_4") else 0,
            "manual_nation_team": 1 if checked else 0,
            "manual_note": note or "",
            "manual_at": ts,
            "created_at": ts,
        }, conflict_columns=["date"])
    except Exception as e:
        print(f"[pbw] 人工打勾落库失败: {str(e)[:100]}")
        return {"ok": False, "error": str(e)[:100]}
    return {"ok": True, **data}


# ────────────────────────────────────────────────────────────────
#  渲染（日报 / 简报共用同一份文案，避免漂移）
# ────────────────────────────────────────────────────────────────
_ITEM_CN = (
    ("nation_team_ann", "国家队新公告"),
    ("amount_dry_up", "成交连续地量"),
    ("chinext_weekly", "创业板周线止跌"),
    ("breadth_ratio_2d", "涨跌比连续转暖"),
    ("us10y_or_oil", "美债/油价缓和"),
)


def render_markdown(data: Dict = None, prefix: str = "") -> str:
    data = data or load()
    if not data:
        return f"{prefix}- 市场底清单：暂无（当日未计算）"
    lines = [f"{prefix}**市场底确认清单 {data.get('n_checked')}/{data.get('n_total')}**"]
    for key, cn in _ITEM_CN:
        it = (data.get("items") or {}).get(key) or {}
        if it.get("checked"):
            mark = "✅"
        elif it.get("insufficient"):
            mark = "⬜(数据不足)"
        else:
            mark = "⬜"
        lines.append(f"{prefix}- {mark} {cn}：{it.get('detail') or it.get('note') or '—'}")
    lines.append(f"{prefix}- {data.get('position_hint') or ''}")
    return "\n".join(lines)


def run_daily(day: str = None) -> Dict:
    """日批入口：算 + 落库 + 返回（异常不外抛，避免拖垮日批）。

    ★ 幂等：同一天重跑时，若当日行**已经**标记过 crossed_4，则不再触发推送
      （否则补跑/重试会把"跨过 4/5"重复推企微 —— 与本项目"告警脱敏"纪律冲突）。
    """
    try:
        day = str(day or _default_day())[:10]
        prev_today = load(day) or {}
        already_crossed = bool(prev_today.get("crossed_4"))
        data = evaluate(day)
        if already_crossed:
            data["crossed_4"] = False
        save(data)
        return {"ok": True, "date": data["date"], "n_checked": data["n_checked"],
                "crossed_4": data["crossed_4"], "already_crossed": already_crossed}
    except Exception as e:
        print(f"[pbw] 每日计算失败: {str(e)[:120]}")
        return {"ok": False, "error": str(e)[:120]}
