# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】Thesis 失效条件自动提示（thesis hints）—— **只提示，不修订、不裁决**
================================================================================
出处：2026-11-07 功能审视"档 1"第 2 项（THESIS_TRACKER.md §3.4 的"成本 ≈ 0"选项落地）。

★ 数据源：`flash_news` / `jin10_tidy_news` 里金十转述的 CME FedWatch 句——
    据CME"美联储观察"：美联储12月会议加息25个基点的概率为82.6%
  快讯本来就在抓 ⇒ 本模块**零外呼**（同 thesis_tracker 文档 §3.4 的正则先例）。

★ 它做什么 / 不做什么：
  · 做：把「最新 FedWatch 定价」对照 open 状态 fed_event 假设的
    `invalidation_early` 里用户**自己登记**的阈值（正则抽 `跌破 X%`）；
    触发 ⇒ 输出"失效检查"提示（CLI `thesis_cli.py hints` + 日批 task_thesis_resolve 携带一行）。
  · 不做：**不自动 revise、不自动 resolve**——修订必须人工带 reason（铁律 2 的延伸：
    LLM/正则不能改人的概率），裁决权在 resolve_open_theses 的结构化规则里。

★ 触发口径（冻结 2026-11-07）：
    direction 匹配（假设 claim 含"加息"⇒ 只看加息句；含"降息"⇒ 只看降息句）
    且 最新定价 ≤ 阈值 ⇒ 触发。
    无 invalidation_early 或抽不出阈值 ⇒ 永不触发，只出 info 行（不发明默认阈值）。
================================================================================
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_BJ = timezone(timedelta(hours=8))

# 金十 FedWatch 句式（★ 2026-11-07 按库内真句实测校准，四种变体全兼容）：
#   "美联储12月加息的概率为 82.6%"（无"会议"无"基点"）
#   "美联储12月会议加息25个基点的概率为 82.6%"（完整版）
#   "美联储12月维持利率不变的概率为 X%"（维持变体，只作 info 不触发——
#     触发方向必须与假设 claim 的 加息/降息 匹配）
#   "美联储利率决议后：12月加息的概率为 X%"（前缀隔断变体，[^。] 防跨句）
_FEDWATCH_RE = re.compile(
    r"美联储[^。]{0,30}?(\d{1,2})月(?:会议)?"
    r"(加息|降息|维持利率不变)(?:([\d]{1,3})个基点)?的概率为([\d.]+)%")
# 用户 invalidation_early 里的阈值（"FedWatch 跌破 65% → …"）
_THRES_RE = re.compile(r"跌破\s*(\d{1,3}(?:\.\d+)?)\s*%")


def _window_from(days: int) -> str:
    now = datetime.now(_BJ)
    return (now - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")


def scan_fedwatch(days: int = 5) -> Dict:
    """扫最近 N 天快讯里的 FedWatch 概率句，对照 open 的 fed_event 假设。

    返回 {"ok", "latest": {date,time,direction,bp,pct,src,head} | None,
          "theses": [{id, claim, p_mine, p_market, threshold, latest_pct, hint}],
          "note"}
    """
    frm = _window_from(days)
    # 语料：两源 UNION 到统一 (date/time, direction, bp, pct, src, head)
    matches: List[Dict] = []
    try:
        rows = db.fetch("SELECT time, content FROM flash_news "
                        "WHERE time >= %s", (frm,)) or []
        for r in rows:
            m = _FEDWATCH_RE.search(str(r.get("content") or ""))
            if m:
                matches.append({
                    "sort": str(r.get("time") or ""),
                    "date": str(r.get("time") or "")[:10],
                    "time": str(r.get("time") or ""),
                    "direction": m.group(2),
                    "bp": int(m.group(3)) if m.group(3) else None,
                    "pct": float(m.group(4)),
                    "src": "flash",
                    "head": str(r.get("content") or "")[:80],
                })
    except Exception as e:
        print(f"[thesis_hints] flash_news 读取失败: {str(e)[:80]}")
    try:
        rows = db.fetch("SELECT date, seq, title, content FROM jin10_tidy_news "
                        "WHERE date >= %s", (_window_from(days)[:10],)) or []
        for r in rows:
            text = f"{r.get('title') or ''}｜{r.get('content') or ''}"
            m = _FEDWATCH_RE.search(text)
            if m:
                matches.append({
                    "sort": f"{r.get('date')} 23:59:00",
                    "date": str(r.get("date") or "")[:10],
                    "time": f"{r.get('date')} tidy#{r.get('seq') or 0}",
                    "direction": m.group(2),
                    "bp": int(m.group(3)) if m.group(3) else None,
                    "pct": float(m.group(4)),
                    "src": "tidy",
                    "head": text[:80],
                })
    except Exception as e:
        print(f"[thesis_hints] jin10_tidy_news 读取失败: {str(e)[:80]}")

    matches.sort(key=lambda x: x["sort"], reverse=True)
    latest = matches[0] if matches else None

    # open 的 fed_event 假设
    theses_out: List[Dict] = []
    try:
        rows = db.fetch("SELECT id, claim, thesis_type, p_mine, p_market, "
                        "invalidation_early FROM thesis_registry "
                        "WHERE status='open'") or []
    except Exception as e:
        return {"ok": False, "error": f"thesis_registry 不可读: {str(e)[:80]}"}
    for r in rows:
        if str(r.get("thesis_type") or "") != "fed_event":
            continue
        claim = str(r.get("claim") or "")
        direction = "加息" if "加息" in claim else ("降息" if "降息" in claim else None)
        inv = str(r.get("invalidation_early") or "")
        th_m = _THRES_RE.search(inv)
        threshold = float(th_m.group(1)) if th_m else None
        latest_pct = None
        if latest and direction and latest["direction"] == direction:
            latest_pct = latest["pct"]
        hint = None
        if latest_pct is not None and threshold is not None and latest_pct <= threshold:
            hint = (f"⚠️ FedWatch 最新 {latest_pct:.1f}%（{latest['date']}）已跌破你在 "
                    f"invalidation_early 登记的 {threshold:.0f}% => 失效检查："
                    f"评估是否转分支；如修订概率必须带 reason（thesis_cli revise），"
                    f"本提示不自动改任何数字")
        theses_out.append({
            "id": r.get("id"), "claim": claim[:60],
            "p_mine": r.get("p_mine"), "p_market": r.get("p_market"),
            "threshold": threshold, "latest_pct": latest_pct, "hint": hint,
        })

    triggered = [t for t in theses_out if t.get("hint")]
    return {
        "ok": True,
        "latest": ({k: latest[k] for k in ("date", "time", "direction", "bp",
                                           "pct", "src", "head")} if latest else None),
        "theses": theses_out,
        "triggered": len(triggered),
        "note": ("只提示不修订不裁决；触发口径=最新定价 ≤ invalidation_early 登记阈值"
                 "（方向匹配）；无登记阈值 ⇒ 只出 info 行。"),
    }


def brief_line(days: int = 5) -> Optional[str]:
    """给日批 task_thesis_resolve 携带的一行摘要；无数据返回 None。"""
    r = scan_fedwatch(days=days)
    if not r.get("ok"):
        return None
    latest = r.get("latest")
    if not latest:
        return None
    base = (f"FedWatch 提示: 最新{latest['direction']}"
            f"{(str(latest['bp']) + 'bp') if latest.get('bp') else ''}定价 "
            f"{latest['pct']:.1f}%（{latest['date']}）")
    hints = [t for t in r.get("theses") or [] if t.get("hint")]
    if hints:
        ids = ",".join(f"#{t['id']}" for t in hints)
        return base + f" => 失效检查触发（{ids}，详见 thesis_cli hints）"
    return base + "（未触发失效阈值）"
