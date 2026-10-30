# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】磨底 2.0 推演证伪记分卡（scenario watch）—— **纯观察器**
================================================================================
背景（2026-10-23，用户给出三段式推演，要求系统替他证伪）：
  未来 6~8 周"内外双重挤压下的阴跌磨底"；三个情景：基准（~55%）、熊市加深（~20%）、
  提前修复（~25%）；触发器全在**外部**（伊朗油价 / 美联储点阵图）与**政策**。
  ⇒ 把推演里的"验证信号"逐条拉出来，每天自动判多/空/中性，落到一张记分卡。

★ 本模块**只组合既有观察器**（零新数据源）：
  · 成交额/涨跌比/10Y/油价/银行补跌/创业板周线  ← `policy_bottom_watch`（已日批落库）
  · 上证位置                                       ← `key_level_watch`
  · 汇金 ETF 份额确认                              ← `etf_share_watch`

★ 判定口径（与用户推演一致，阈值 env 可调）：
  · 成交额(亿)：≥15400 空（恐慌未出清）/ ≤12000 多（抛压衰竭）/ 其余中性
  · 涨跌比：≥1.0 多 / ≤0.35 空 / 其余中性
  · 10Y：≤5.0 多 / ≥5.3 空 / 其余中性
  · 布伦特：≤85 多 / ≥120 空 / 其余中性
  · 银行补跌 = 多（磨底末段信号）；创业板周线止跌 = 多
  · 上证：≥3674 多（结构修复）/ <SC_SH_LAST 空（锚点失守，2026-10-23 起为 3574）/ 其余中性
  · 汇金 ETF 确认 = 多（承接加码）
  · 伊朗/中东：近 7 日金十快讯，升级词 > 缓和词 → 空；硬触发（空袭/开战/布雷/触雷/
    封锁霍尔木兹，且须含"伊朗"）→ 判空 + `geo_alert` 高亮，但**不直接切换情景**
    （情景仍按油价/10Y/点位，避免单条新闻过度反应）；含"编造/虚假/否认"与金十聚合稿剔除。
  · 情景：任一熊市触发器命中 → 熊市加深；任一修复触发器命中 → 提前修复；否则基准。

★ 红线：观察器，不改分数、不进闸门、不碰模拟盘。多/空只是"信号倾向"，不是仓位指令。
================================================================================
"""
import json
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.database import db

_TABLE = "scenario_watch"
_READY = False

# 阈值（env 可调；改阈值请在文档留痕）
SC_AMT_HIGH = float(os.environ.get("SC_AMT_HIGH") or 15400)   # 亿：恐慌未出清
SC_AMT_LOW = float(os.environ.get("SC_AMT_LOW") or 12000)     # 亿：抛压衰竭
SC_RATIO_HIGH = float(os.environ.get("SC_RATIO_HIGH") or 1.0)
SC_RATIO_LOW = float(os.environ.get("SC_RATIO_LOW") or 0.35)
SC_US10Y_LOW = float(os.environ.get("SC_US10Y_LOW") or 5.0)
SC_US10Y_HIGH = float(os.environ.get("SC_US10Y_HIGH") or 5.3)
SC_BRENT_LOW = float(os.environ.get("SC_BRENT_LOW") or 85.0)
SC_BRENT_HIGH = float(os.environ.get("SC_BRENT_HIGH") or 120.0)
SC_SH_FIX = float(os.environ.get("SC_SH_FIX") or 3674.0)      # 上证收复结构（3674–3681）
SC_SH_LAST = float(os.environ.get("SC_SH_LAST") or 3574.0)    # 上证锚点（2026-10-23 低点）
SC_SH_REDLINE = float(os.environ.get("SC_SH_REDLINE") or 3500.0)
SC_SH_ANCHOR = float(os.environ.get("SC_SH_ANCHOR") or 3574.0)  # 多空锚点
# ★ 2026-10-23（用户："单日 V 型在这个位置已经失效过一次"）：
#   结构修复必须**连续 2 个交易日**收在 3674 上方，单日不算。
SC_SH_FIX_DAYS = int(os.environ.get("SC_SH_FIX_DAYS") or 2)
# ★ 地缘动能候选阈值（2026-10-23 用户）：近 48h 实际动能事件 ≥3 条 ⇒ 熊市情景候选
SC_GEO_KINETIC = int(os.environ.get("SC_GEO_KINETIC") or 3)

# 关键日历（用户推演给定；时间仅作倒计时，不自动执行任何动作）
# ★ 2026-10-31（用户："11/3 降档等只出文字提示，不碰仓位"）：
#   `impact=high` 的事件在 **T-N 天内**产出 `event_alert` 文字提示，
#   **只提示、不改任何分数/闸门/仓位** —— 本模块连 `position_hint` 都不写。
EVENTS = [
    {"date": "2026-10-27", "name": "FOMC 加息 25bp（无SEP）",
     "note": "声明鹰鸽定周方向", "impact": "high"},
    {"date": "2026-10-31", "name": "A股三季报截止",
     "note": "AI 业绩雷区出清；10/31 前零操作", "impact": "normal"},
    {"date": "2026-11-03", "name": "美国中期选举",
     "note": "选后伊朗决策窗口=最大尾部风险", "impact": "high"},
    {"date": "2026-12-08", "name": "FOMC + 点阵图",
     "note": "全年最重要会议；点阵图 4.10/4.25/4.50 → 进攻/中性/防守", "impact": "high"},
]

# 事件前提示窗口（天）：T-N ~ T 日期间输出 event_alert。
# 默认 2：T-2 就开始提示，避免"只在当天提醒、已经来不及降仓"。
SC_EVENT_ALERT_DAYS = int(os.environ.get("SC_EVENT_ALERT_DAYS") or 2)
POINT_PLOT = [("4.10%", "进攻"), ("4.25%", "中性"), ("4.50%", "防守")]

# ★ 地缘事件（金十快讯按**内容**匹配；实测 flash_news.cluster 字段当前为空）
GEO_KW = ["伊朗", "美伊", "伊美", "霍尔木兹", "中东", "哈梅内伊",
          "以色列", "真主党", "哈马斯", "红海", "油轮"]
GEO_UP = ["军事打击", "空袭", "导弹", "开战", "袭击", "封锁", "布雷",
          "地面部队", "最后通牒", "核武", "铀浓缩", "冲突升级", "军事行动"]
# ★ 否定式升级（"否认谈判"/"拒绝谈判"）—— 若只按子串匹配会被算成"缓和"
GEO_UP_RE = [r"否认.{0,8}谈判", r"并未.{0,8}谈判", r"拒绝.{0,8}谈判",
             r"不谈.{0,4}判"]
GEO_DOWN = ["谈判", "停火", "协议", "和谈", "会谈", "解除制裁", "缓和", "撤军"]
GEO_HARD = ["空袭", "开战", "战争爆发", "封锁霍尔木兹", "布雷", "水雷", "触雷",
            "地面部队", "导弹袭击", "击落"]
# 剔除：明显否定/传闻 + 金十自己的聚合稿（否则一条汇总会重复计数）
GEO_NOISE = ["编造", "虚假", "辟谣", "传言", "金十数据整理",
             "重要新闻汇总", "中东局势跟踪"]


def _geo_signal(day: str) -> Dict:
    """近 7 日金十快讯里的伊朗/中东事件：升级 vs 缓和。

    ★ 硬触发（军事打击/空袭/开战/封锁霍尔木兹/布雷）直接判空；
      含"编造/虚假/否认"的条目剔除（例：特朗普称伊朗编造袭击推高油价）。
    """
    try:
        from app.database import db
        try:
            base = datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            base = datetime.now()
        cutoff = (base - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        rows = db.fetch("SELECT time, content FROM flash_news WHERE time >= %s "
                        "ORDER BY id DESC", (cutoff,)) or []
    except Exception as e:
        return _sig("geo", "伊朗/中东事件", None, "条", "不足",
                    f"快讯库不可用：{str(e)[:40]}")

    geo = [r for r in rows
           if any(k in (r.get("content") or "") for k in GEO_KW)
           and not any(k in (r.get("content") or "") for k in GEO_NOISE)]
    if not geo:
        return _sig("geo", "伊朗/中东事件", 0, "条", "中性", "近 7 日无相关快讯")

    def _hit(r, words):
        c = r.get("content") or ""
        return any(w in c for w in words)

    def _hit_up(r):
        c = r.get("content") or ""
        if any(w in c for w in GEO_UP):
            return True
        return any(re.search(p, c) for p in GEO_UP_RE)

    up_rows = [r for r in geo if _hit_up(r)]
    down_rows = [r for r in geo if _hit(r, GEO_DOWN)]
    # ★ 硬触发必须是"伊朗相关的实际动能事件"（计划/威胁/警告只算升温，不算硬触发）
    hard_row = next((r for r in geo
                     if _hit(r, GEO_HARD) and "伊朗" in (r.get("content") or "")), None)
    lean = "空" if hard_row else ("空" if len(up_rows) > len(down_rows)
                                  else ("多" if len(down_rows) > len(up_rows) else "中性"))
    latest = (hard_row or geo[0]).get("content") or ""
    latest = latest.replace("\n", " ")[:60]
    # ★ 动能 vs 口头：硬触发（须含伊朗）算动能，其余算口头威胁
    def _t2dt(s):
        try:
            return datetime.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None

    kin_rows = [r for r in geo if _hit(r, GEO_HARD)
                and "伊朗" in (r.get("content") or "")]
    n_kinetic = len(kin_rows)
    n_verbal = len(geo) - n_kinetic
    latest_dt = max((d for d in (_t2dt(r.get("time")) for r in geo) if d),
                    default=None)
    cut48 = latest_dt - timedelta(hours=48) if latest_dt else None
    k48 = len([r for r in kin_rows
               if cut48 and _t2dt(r.get("time")) and _t2dt(r.get("time")) >= cut48])
    cand = k48 >= SC_GEO_KINETIC
    return {**_sig("geo", "伊朗/中东事件", len(up_rows), "条", lean,
                   f"升级{len(up_rows)}/缓和{len(down_rows)}｜"
                   f"动能{n_kinetic}(近48h {k48})/口头{n_verbal}"
                   f"{'，★硬触发' if hard_row else ''}；最新：{latest}"),
            "hard": bool(hard_row), "kinetic": n_kinetic,
            "kinetic_48h": k48, "candidate": cand}


def _today() -> str:
    try:
        from app.flash.rules import latest_completed_trading_day
        return str(latest_completed_trading_day())[:10]
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS scenario_watch (
            date TEXT PRIMARY KEY,
            signals_json TEXT NOT NULL,
            lean TEXT,
            scenario TEXT,
            note TEXT,
            extra_json TEXT,
            created_at TEXT
        )
    """)
    try:                     # 老表补列（Postgres/SQLite 均可；已存在则忽略）
        db.execute("ALTER TABLE scenario_watch ADD COLUMN extra_json TEXT")
    except Exception:
        pass
    _READY = True


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

def _last_of(v):
    """pbw item 的 value 历史上两种形态都写过（列表 / 标量）——取"最新观测值"：
    列表取末元素，标量原样返回，None/空 → None。
    ★ 2026-10-25（BK-60）：旧实现 `(v or [None])[-1]` 对**标量** value 直接
    TypeError ⇒ 整张记分卡当日失败。"""
    if isinstance(v, (list, tuple)):
        return v[-1] if v else None
    return v



def _sig(key, label, value, unit, lean, note=""):
    return {"key": key, "label": label, "value": value, "unit": unit,
            "lean": lean, "note": note}


def evaluate(day: str = None) -> Dict:
    day = str(day or _today())[:10]
    from app import policy_bottom_watch as pbw
    from app import key_level_watch as kl
    from app import etf_share_watch as esw

    pb = pbw.evaluate(day) or {}
    items = pb.get("items") or {}
    klv = kl.load(day) or {}
    kl_rows = {r["code"]: r for r in (klv.get("rows") or [])}
    if "000001" not in kl_rows:
        # 当日行可能只写入了部分指数（或日批还没跑）⇒ 回退到最近一条**含上证**的记录，
        # 并在锚点/情景上明确标注 as_of，绝不静默冒充当天。
        try:
            from app.database import db
            prev = db.fetch_one(
                "SELECT MAX(date) AS d FROM key_level_watch WHERE code = %s",
                ("000001",))
            if prev and prev.get("d"):
                prev = {"date": prev["d"]}
            if prev:
                klv = kl.load(prev["date"]) or klv
                kl_rows = {r["code"]: r for r in (klv.get("rows") or [])}
        except Exception:
            pass
    kl_date = klv.get("date")
    etf = esw.load(day) or {}

    sigs: List[Dict] = []

    # 1 成交额（近 N 日里取最新一天）
    a = items.get("amount_dry_up") or {}
    amt = _num(_last_of(a.get("value")))
    if amt is None:
        sigs.append(_sig("amount", "两市成交额", None, "亿", "不足"))
    else:
        lean = "空" if amt >= SC_AMT_HIGH else ("多" if amt <= SC_AMT_LOW else "中性")
        sigs.append(_sig("amount", "两市成交额", round(amt, 0), "亿", lean,
                         f"≥{SC_AMT_HIGH:.0f} 恐慌未完 / ≤{SC_AMT_LOW:.0f} 抛压衰竭"))

    # 2 涨跌比
    b = items.get("breadth_ratio_2d") or {}
    br = _num(_last_of(b.get("values")))
    if br is None:
        sigs.append(_sig("breadth", "涨跌比", None, "", "不足"))
    else:
        lean = "多" if br >= SC_RATIO_HIGH else ("空" if br <= SC_RATIO_LOW else "中性")
        sigs.append(_sig("breadth", "涨跌比", round(br, 2), "", lean,
                         f"≥{SC_RATIO_HIGH:g} 修复 / ≤{SC_RATIO_LOW:g} 普跌"))

    # 3/4 10Y 与油价
    m = pbw._macro_snapshot(day) if hasattr(pbw, "_macro_snapshot") else {}
    us = _num(m.get("us10y"))
    oil = _num(m.get("brent"))
    if us is None:
        sigs.append(_sig("us10y", "10Y 美债", None, "%", "不足"))
    else:
        lean = "多" if us <= SC_US10Y_LOW else ("空" if us >= SC_US10Y_HIGH else "中性")
        sigs.append(_sig("us10y", "10Y 美债", round(us, 2), "%", lean,
                         f"≤{SC_US10Y_LOW:g} 缓和 / ≥{SC_US10Y_HIGH:g} 恶化"))
    if oil is None:
        sigs.append(_sig("brent", "布伦特", None, "$", "不足"))
    else:
        lean = "多" if oil <= SC_BRENT_LOW else ("空" if oil >= SC_BRENT_HIGH else "中性")
        sigs.append(_sig("brent", "布伦特", round(oil, 1), "$", lean,
                         f"≤{SC_BRENT_LOW:g} 修复 / ≥{SC_BRENT_HIGH:g} 熊市触发"))

    # 5 银行补跌（磨底末段信号）—— value=连续逆势跌天数，避免 UI 只显示 "—"
    # ★ 2026-10-25（BK-60）：数据不足 ⇒ lean 显式"不足"，不再冒充"中性"
    #   （缺失 ≠ 中性——中性会拉低记分卡的判读质量）
    bank = items.get("bank_catchdown") or {}
    bank_lean = ("不足" if bank.get("insufficient")
                 else ("多" if bank.get("checked") else "中性"))
    sigs.append(_sig("bank_catchdown", "强势股补跌",
                     (None if bank.get("insufficient")
                      else bank.get("streak", 0)), "日",
                     bank_lean, bank.get("detail") or ""))

    # 6 创业板周线止跌 —— value=本周涨跌幅（%），避免 UI 只显示 "—"
    cw = items.get("chinext_weekly") or {}
    cw_lean = ("不足" if cw.get("insufficient")
               else ("多" if cw.get("checked") else "中性"))
    sigs.append(_sig("chinext_weekly", "创业板周线",
                     (None if cw.get("insufficient") else cw.get("value")), "%",
                     cw_lean, cw.get("detail") or ""))

    # 7 上证位置（结构修复要求连续 2 日 ≥3674，单日不算）
    sh = kl_rows.get("000001") or {}
    shc = _num(sh.get("close"))
    prev_sh = None
    try:
        from app.database import db as _db
        _r = _db.fetch_one("SELECT close FROM key_level_watch WHERE code=%s "
                           "AND date < %s ORDER BY date DESC LIMIT 1",
                           ("000001", day))
        prev_sh = _num((_r or {}).get("close"))
    except Exception:
        prev_sh = None
    fixed = bool(shc is not None and shc >= SC_SH_FIX
                 and prev_sh is not None and prev_sh >= SC_SH_FIX)
    if shc is None:
        sigs.append(_sig("sh_level", "上证位置", None, "", "不足"))
    else:
        lean = ("多" if fixed
                else ("空" if shc < SC_SH_LAST else "中性"))
        sigs.append(_sig("sh_level", "上证位置", round(shc, 2), "", lean,
                         (f"≥{SC_SH_FIX:.0f} 且连续 {SC_SH_FIX_DAYS} 日=结构修复"
                          f"（当前 {1 if shc >= SC_SH_FIX else 0}/{SC_SH_FIX_DAYS} 日）"
                          f" / <{SC_SH_LAST:.0f} 锚点失守（下看 {SC_SH_REDLINE:.0f}/3400）"
                          + (f"；数据截至 {kl_date}" if kl_date and kl_date != day else ""))))

    # 8 汇金 ETF —— value=当日最大份额增幅（%），避免 UI 只显示 "—"
    etf_conf = bool(etf.get("confirmed"))
    etf_rows = etf.get("rows") or []
    # ★ BK-60：缺失 chg_pct 不再 `or 0`（0 会把 max 拉低 ⇒ 低估份额放大）
    etf_max = max((_num(r.get("chg_pct")) for r in etf_rows
                   if _num(r.get("chg_pct")) is not None), default=None)
    etf_note = ("连续 2 日份额放大=确认" if etf_conf else "未确认")
    if etf.get("fallback") and etf.get("data_date"):
        etf_note += f"（当日未披露，截至 {etf['data_date']}）"
    etf_lean = ("不足" if not etf else ("多" if etf_conf else "中性"))
    sigs.append(_sig("etf_share", "汇金 ETF 份额", etf_max, "%",
                     etf_lean, etf_note))

    # 9 地缘事件（金十快讯：伊朗/中东）
    geo = _geo_signal(day)
    sigs.append(geo)
    hard_geo = bool(geo.get("hard"))
    geo_cand = bool(geo.get("candidate"))

    n_up = sum(1 for s in sigs if s["lean"] == "多")
    n_dn = sum(1 for s in sigs if s["lean"] == "空")
    lean = "偏多" if n_up > n_dn else ("偏空" if n_dn > n_up else "中性")

    # ★ 情景切换仍按**可交易的市场观测值**（油价/10Y/点位）—— 单条新闻不直接改情景，
    #   只作为 geo_alert 高亮；这是用户推演里"选后打伊朗（油价120+）"的原始口径。
    if shc is None:
        scenario = "待日批（缺指数收盘）"
    elif kl_date and kl_date != day:
        # 只有旧数据 ⇒ 不冒充基准/修复，避免单靠一个 10Y 读数就翻情景
        scenario = f"待日批（指数截至 {kl_date}）"
    else:
        bear = (oil is not None and oil >= SC_BRENT_HIGH) or \
               (us is not None and us >= SC_US10Y_HIGH) or \
               (shc < SC_SH_REDLINE)
        # ★ 修复情景不看 10Y（10Y 是**支持信号**，不是情景触发器）：
        #   只认 油价≤85 或 上证连续 2 日站上 3674，避免单一宏观读数翻情景。
        repair = (oil is not None and oil <= SC_BRENT_LOW) or fixed
        scenario = "熊市加深" if bear else ("提前修复" if repair else "基准（磨底）")

    # 事件倒计时
    now = datetime.now()
    evts = []
    for e in EVENTS:
        try:
            # ★ 2026-10-31：按**日历日**比较（`.date()`）。
            #   原实现是 datetime 相减，`(11-03 00:00 - 10-31 16:xx).days = 2`
            #   ⇒ "3 个日历日后"被显示成"2 天后"，T-N 提示会早一天跑偏。
            d = (datetime.strptime(e["date"], "%Y-%m-%d").date() - now.date()).days
        except ValueError:
            d = None
        evts.append({**e, "t_minus": d,
                     "soon": d is not None and 0 <= d <= 1})

    # ★ 2026-10-31：高影响事件的**文字提示**（用户："只出文字提示，不碰仓位"）。
    #   为什么不做成"自动降档"：降档属于仓位决策，系统直接改会越过用户；
    #   本模块的既有红线就是"观察器，不改分数、不进闸门"。
    _alerts = []
    for e in evts:
        t = e.get("t_minus")
        if t is None or not (0 <= t <= SC_EVENT_ALERT_DAYS):
            continue
        if e.get("impact") != "high":
            continue
        when = "今天" if t == 0 else ("明天" if t == 1 else f"{t} 天后")
        _alerts.append({
            "date": e["date"], "name": e["name"], "t_minus": t,
            "text": (f"{when}（{e['date']}）{e['name']}："
                     f"建议最低仓位、不开新仓，让事件落地（仅提示，不改仓位）"),
        })
    event_alert = {
        "active": bool(_alerts),
        "items": _alerts,
        "text": ("★事件前提示：" + "；".join(a["text"] for a in _alerts)
                 if _alerts else ""),
    }

    note = (f"推演证伪记分卡：{len(sigs)} 项信号，偏多 {n_up} / 偏空 {n_dn}。"
            f"情景倾向={scenario}。"
            + ("★地缘硬触发（实际动能事件已出现；油价未达阈值前不切换情景）。"
               if hard_geo else "")
            + (f"★地缘候选：近 48h 动能 {geo.get('kinetic_48h')} 条 ≥ "
               f"{SC_GEO_KINETIC} ⇒ 熊市情景候选（仅提示，不自动改仓）。"
               if geo_cand else "")
            + "近 7 日内关键日历："
            + ("、".join(f"{e['name']}({e['t_minus']}d)" for e in evts
                         if e["t_minus"] is not None and 0 <= e["t_minus"] <= 7)
               or "无")
            + ("。" + event_alert["text"] if event_alert["active"] else ""))

    anchor = {
        "level": SC_SH_ANCHOR,
        "close": shc,
        "as_of": kl_date,
        "state": ("—" if shc is None
                  else ("守住" if shc >= SC_SH_ANCHOR else "跌破")),
        "downside": "3500/3400",
        "structure_fix": fixed,
        "fix": (f"收复 {SC_SH_FIX:.0f}–3681 且连续 {SC_SH_FIX_DAYS} 日"
                f"（当前 {1 if (shc or 0) >= SC_SH_FIX else 0}/{SC_SH_FIX_DAYS}）"
                f"⇒ 磨底结构修复（清单进 2/5）"),
    }
    return {"date": day, "signals": sigs, "lean": lean, "scenario": scenario,
            "events": evts, "point_plot": POINT_PLOT, "geo_alert": hard_geo,
            "geo_candidate": geo_cand, "event_alert": event_alert,
            "scenario_candidate": "熊市加深（地缘候选）" if geo_cand else "",
            "anchor": anchor, "data_as_of": kl_date,
            "checklist": {"n_checked": pb.get("n_checked"),
                          "n_total": pb.get("n_total"),
                          "hint": pb.get("position_hint")},
            "n_up": n_up, "n_down": n_dn, "note": note}


def save(data: Dict) -> bool:
    ensure_table()
    try:
        extra = {k: data.get(k) for k in
                 ("anchor", "checklist", "geo_alert", "geo_candidate",
                  "scenario_candidate", "event_alert")}
        db.upsert(_TABLE, {
            "date": str(data.get("date"))[:10],
            "signals_json": json.dumps(data.get("signals") or [], ensure_ascii=False),
            "lean": data.get("lean") or "",
            "scenario": data.get("scenario") or "",
            "note": data.get("note") or "",
            "extra_json": json.dumps(extra, ensure_ascii=False),
            "created_at": datetime.now().isoformat(),
        }, conflict_columns=["date"])
        return True
    except Exception as e:
        print(f"[scenario] 落库失败: {str(e)[:100]}")
        return False


def load(day: str = None) -> Dict:
    ensure_table()
    if day:
        d = str(day)[:10]
    else:
        r = db.fetch_one("SELECT MAX(date) AS d FROM scenario_watch")
        d = str((r or {}).get("d") or "")[:10]
    if not d:
        return evaluate()
    try:
        row = db.fetch_one("SELECT * FROM scenario_watch WHERE date = %s", (d,))
    except Exception:
        row = None
    if not row:
        return evaluate()
    try:
        signals = json.loads(row.get("signals_json") or "[]")
    except (ValueError, TypeError):
        signals = []
    try:
        extra = json.loads(row.get("extra_json") or "{}")
    except (ValueError, TypeError):
        extra = {}
    # 兼容老行（无 extra_json）：从 sh_level 现价推锚点
    anchor = extra.get("anchor")
    if not anchor:
        shc = next((s.get("value") for s in signals if s.get("key") == "sh_level"),
                   None)
        anchor = {"level": SC_SH_ANCHOR, "close": shc,
                  "state": ("—" if shc is None
                            else ("守住" if float(shc) >= SC_SH_ANCHOR else "跌破")),
                  "downside": "3500/3400",
                  "fix": f"收复 {SC_SH_FIX:.0f}–3681 ⇒ 磨底结构修复（清单进 2/5）"}
    cand = bool(extra.get("geo_candidate")
                or any(s.get("candidate") for s in signals))
    return {"date": d, "signals": signals, "lean": row.get("lean"),
            "scenario": row.get("scenario"), "note": row.get("note"),
            "events": EVENTS, "point_plot": POINT_PLOT,
            "event_alert": (extra.get("event_alert")
                            or {"active": False, "items": [], "text": ""}),
            "geo_alert": bool(extra.get("geo_alert")
                              or any(s.get("hard") for s in signals)),
            "geo_candidate": cand,
            "scenario_candidate": (extra.get("scenario_candidate")
                                   or ("熊市加深（地缘候选）" if cand else "")),
            "anchor": anchor, "checklist": extra.get("checklist") or {},
            "n_up": sum(1 for s in signals if s.get("lean") == "多"),
            "n_down": sum(1 for s in signals if s.get("lean") == "空")}


def run_daily(day: str = None) -> Dict:
    """日批入口：算 + 落库（异常不外抛）。"""
    try:
        data = evaluate(day)
        save(data)
        return {"ok": True, "date": data["date"], "scenario": data["scenario"],
                "lean": data["lean"], "n_up": data["n_up"],
                "n_down": data["n_down"],
                "event_alert": data.get("event_alert") or {}}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
