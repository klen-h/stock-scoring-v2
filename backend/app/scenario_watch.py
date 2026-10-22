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
  · 上证：≥3674 多（结构修复）/ <3550 空（最后缓冲之下）/ 其余中性
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
SC_SH_FIX = float(os.environ.get("SC_SH_FIX") or 3674.0)      # 上证收复结构
SC_SH_LAST = float(os.environ.get("SC_SH_LAST") or 3550.0)    # 上证最后缓冲
SC_SH_REDLINE = float(os.environ.get("SC_SH_REDLINE") or 3500.0)

# 关键日历（用户推演给定；时间仅作倒计时，不自动执行任何动作）
EVENTS = [
    {"date": "2026-10-27", "name": "FOMC 加息 25bp（无SEP）",
     "note": "声明鹰鸽定周方向"},
    {"date": "2026-10-31", "name": "A股三季报截止",
     "note": "AI 业绩雷区出清；10/31 前零操作"},
    {"date": "2026-11-03", "name": "美国中期选举",
     "note": "选后伊朗决策窗口=最大尾部风险；T-1 自动降档"},
    {"date": "2026-12-08", "name": "FOMC + 点阵图",
     "note": "全年最重要会议；点阵图 4.10/4.25/4.50 → 进攻/中性/防守"},
]
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
    n_kinetic = len([r for r in geo if _hit(r, GEO_HARD)
                     and "伊朗" in (r.get("content") or "")])
    n_verbal = len(geo) - n_kinetic
    return {**_sig("geo", "伊朗/中东事件", len(up_rows), "条", lean,
                   f"升级{len(up_rows)}/缓和{len(down_rows)}｜"
                   f"动能{n_kinetic}/口头{n_verbal}"
                   f"{'，★硬触发' if hard_row else ''}；最新：{latest}"),
            "hard": bool(hard_row)}


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
            created_at TEXT
        )
    """)
    _READY = True


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
    etf = esw.load(day) or {}

    sigs: List[Dict] = []

    # 1 成交额（近 N 日里取最新一天）
    a = items.get("amount_dry_up") or {}
    amt = (a.get("value") or [None])[-1]
    amt = _num(amt)
    if amt is None:
        sigs.append(_sig("amount", "两市成交额", None, "亿", "不足"))
    else:
        lean = "空" if amt >= SC_AMT_HIGH else ("多" if amt <= SC_AMT_LOW else "中性")
        sigs.append(_sig("amount", "两市成交额", round(amt, 0), "亿", lean,
                         f"≥{SC_AMT_HIGH:.0f} 恐慌未完 / ≤{SC_AMT_LOW:.0f} 抛压衰竭"))

    # 2 涨跌比
    b = items.get("breadth_ratio_2d") or {}
    br = _num((b.get("values") or [None])[-1])
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

    # 5 银行补跌（磨底末段信号）
    bank = items.get("bank_catchdown") or {}
    sigs.append(_sig("bank_catchdown", "强势股补跌", None, "",
                     "多" if bank.get("checked") else "中性",
                     bank.get("detail") or ""))

    # 6 创业板周线止跌
    cw = items.get("chinext_weekly") or {}
    sigs.append(_sig("chinext_weekly", "创业板周线", None, "",
                     "多" if cw.get("checked") else "中性",
                     cw.get("detail") or ""))

    # 7 上证位置
    sh = kl_rows.get("000001") or {}
    shc = _num(sh.get("close"))
    if shc is None:
        sigs.append(_sig("sh_level", "上证位置", None, "", "不足"))
    else:
        lean = ("多" if shc >= SC_SH_FIX
                else ("空" if shc < SC_SH_LAST else "中性"))
        sigs.append(_sig("sh_level", "上证位置", round(shc, 2), "", lean,
                         f"≥{SC_SH_FIX:.0f} 结构修复 / <{SC_SH_LAST:.0f} 最后缓冲之下"))

    # 8 汇金 ETF
    etf_conf = bool(etf.get("confirmed"))
    sigs.append(_sig("etf_share", "汇金 ETF 份额", None, "",
                     "多" if etf_conf else "中性",
                     "连续 2 日份额放大=确认" if etf_conf else "未确认"))

    # 9 地缘事件（金十快讯：伊朗/中东）
    geo = _geo_signal(day)
    sigs.append(geo)
    hard_geo = bool(geo.get("hard"))

    n_up = sum(1 for s in sigs if s["lean"] == "多")
    n_dn = sum(1 for s in sigs if s["lean"] == "空")
    lean = "偏多" if n_up > n_dn else ("偏空" if n_dn > n_up else "中性")

    # ★ 情景切换仍按**可交易的市场观测值**（油价/10Y/点位）—— 单条新闻不直接改情景，
    #   只作为 geo_alert 高亮；这是用户推演里"选后打伊朗（油价120+）"的原始口径。
    bear = (oil is not None and oil >= SC_BRENT_HIGH) or \
           (us is not None and us >= SC_US10Y_HIGH) or \
           (shc is not None and shc < SC_SH_REDLINE)
    repair = (oil is not None and oil <= SC_BRENT_LOW) or \
             (us is not None and us <= SC_US10Y_LOW) or \
             (shc is not None and shc >= SC_SH_FIX)
    scenario = "熊市加深" if bear else ("提前修复" if repair else "基准（磨底）")

    # 事件倒计时
    now = datetime.now()
    evts = []
    for e in EVENTS:
        try:
            d = (datetime.strptime(e["date"], "%Y-%m-%d") - now).days
        except ValueError:
            d = None
        evts.append({**e, "t_minus": d,
                     "soon": d is not None and 0 <= d <= 1})
    note = (f"推演证伪记分卡：{len(sigs)} 项信号，偏多 {n_up} / 偏空 {n_dn}。"
            f"情景倾向={scenario}。"
            + ("★地缘硬触发（实际动能事件已出现；油价未达阈值前不切换情景）。"
               if hard_geo else "")
            + "近 7 日内关键日历："
            + ("、".join(f"{e['name']}({e['t_minus']}d)" for e in evts
                         if e["t_minus"] is not None and 0 <= e["t_minus"] <= 7)
               or "无"))

    return {"date": day, "signals": sigs, "lean": lean, "scenario": scenario,
            "events": evts, "point_plot": POINT_PLOT, "geo_alert": hard_geo,
            "n_up": n_up, "n_down": n_dn, "note": note}


def save(data: Dict) -> bool:
    ensure_table()
    try:
        db.upsert(_TABLE, {
            "date": str(data.get("date"))[:10],
            "signals_json": json.dumps(data.get("signals") or [], ensure_ascii=False),
            "lean": data.get("lean") or "",
            "scenario": data.get("scenario") or "",
            "note": data.get("note") or "",
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
    return {"date": d, "signals": signals, "lean": row.get("lean"),
            "scenario": row.get("scenario"), "note": row.get("note"),
            "events": EVENTS, "point_plot": POINT_PLOT,
            "geo_alert": any(s.get("hard") for s in signals),
            "n_up": sum(1 for s in signals if s.get("lean") == "多"),
            "n_down": sum(1 for s in signals if s.get("lean") == "空")}


def run_daily(day: str = None) -> Dict:
    """日批入口：算 + 落库（异常不外抛）。"""
    try:
        data = evaluate(day)
        save(data)
        return {"ok": True, "date": data["date"], "scenario": data["scenario"],
                "lean": data["lean"], "n_up": data["n_up"],
                "n_down": data["n_down"]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}
