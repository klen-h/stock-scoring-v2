# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】FOMC 声明对比（L4 语言层素材）—— **只出素材与候选档位，不自动判定**
================================================================================
来源：`docs/plans/全球宏观周期判断与资产配置逻辑框架.md` §4.2 `language_layer`。
框架原本要「点阵图」，但点阵图**季度才有**，声明**每次会议都有** ⇒ 声明更连续、更及时。
本模块把每次 FOMC 的「声明对比」拉下来，供人工判断措辞分期。

--------------------------------------------------------------------------------
一、接口（2026-11-08 实测，**免 token**）
--------------------------------------------------------------------------------
  GET https://mp-api.jin10.com/api/long-pictures/list
      ?category=FederalReserveFOMCCompare&attr_id=&time_gte=YYYY-MM-DD&time_lte=YYYY-MM-DD
    headers: x-app-id: fiXF2nOnDycGutVA  /  x-version: 1.0

  返回 `data[]`，每条是一期「声明对比」长图；正文在
  `data[].data.json.contents[]`，**恰好 2 条**：
    `contents[0]` = **上期**全文（基准）
    `contents[1]` = **本期**全文（带差异标注）

  ★★ HTML 里的三种标注（这是精华，别当噪音扔掉）：
    · `<i class="highlight">…</i>`     = **本期新增或改写**的表述
    · `<i class="line-through">…</i>`  = **本期删除**的表述
    · `<span class="ctrl-tooltip">取消高亮</span>` = 前端控件文字，**必须剥掉**

  ⚠️ **删除的措辞比新增的更重要**：框架 §4.2 说 `stage_transition` 的标志是
     「**删去加息选项表述**」。只看新增会漏掉这个关键信号，所以 `struck` 单独存。

  实测覆盖：2025 年 8 期 + 2026 年 7 期（1/3/4/6/7/9/10 月），一次请求按年范围拉。

--------------------------------------------------------------------------------
二、★ 为什么"不自动判定 L4"
--------------------------------------------------------------------------------
  措辞分期是**语义判断**，关键词匹配只能给"候选"。框架 §4.2 自己也写
  「媒体转述存在噪音，NLP 模块需设置置信度阈值」。

  ⇒ 本模块输出 `stage_candidate` + `stage_hit`（命中的原句），
    并明确标注 `"confidence": "keyword-only"`、`"need_manual": true`。
        **不得**把候选当结论接进状态机。

--------------------------------------------------------------------------------
三、红线
--------------------------------------------------------------------------------
  纯观察器/素材库：不改分数、不进闸门、不碰模拟盘、不出任何仓位或买卖建议。
================================================================================
"""
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

from app.database import db

_API = "https://mp-api.jin10.com/api/long-pictures/list"
_HEADERS = {
    "x-app-id": "fiXF2nOnDycGutVA",     # 与 jin10_tidy / dynamic-data 同一个（免 token）
    "x-version": "1.0",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.jin10.com/",
}
_CATEGORY = "FederalReserveFOMCCompare"

_TABLE = "fomc_statement"
_READY = False

SCORER_VERSION = "2026-11-08.1"

# ── HTML 解析（正则足够；不引 BeautifulSoup，避免新依赖）──────────────────
_RE_TOOLTIP = re.compile(r'<span class="ctrl-tooltip".*?</span>', re.S)
_RE_HIGHLIGHT = re.compile(r'<i class="highlight">(.*?)</i>', re.S)
_RE_STRIKE = re.compile(r'<i class="line-through">(.*?)</i>', re.S)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_BR = re.compile(r"<br\s*/?>", re.I)

# ── 结构化提取 ─────────────────────────────────────────────────────────────
_RE_VOTE = re.compile(r"(\d+)\s*票赞成[、,，]?\s*(\d+)\s*票反对")
# 「上调25个基点，至3.75%至4%」/「上调25个基点至3.75%—4%」/「下调…」
# ★ 2026-11-08 修正：原文常写「上调25个基点**，至**3.75%**至**4%」——两个"至"连用，
#   旧正则要求只有一个连接符 ⇒ 2026-09 那次加息解析成 None（漏掉了本期唯一的加息）。
_RE_MOVE = re.compile(
    r"(上调|下调)\s*(\d+)\s*个基点[，,、]?\s*(?:至)?\s*([\d.]+)\s*[%％]?\s*"
    r"(?:[—\-–~]|至|到)\s*([\d.]+)\s*[%％]?")
# 「维持在3.75%至4%之间」
_RE_HOLD = re.compile(
    r"维持在\s*([\d.]+)\s*[%％]?\s*[至到\-–~]+\s*([\d.]+)\s*[%％]?")

# ── L4 语言分期关键词（**只给候选**，按"越强越先"排序）─────────────────────
_STAGE_PATTERNS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("stage_confirm", ("讨论何时放松", "降息进入议程", "准备降息", "开始降息")),
    ("stage_pivot", ("保持耐心", "政策处于良好位置", "处于良好位置", "政策立场合适")),
    ("stage_transition", ("随着更多信息的积累", "逐次会议", "数据逐次", "每次会议评估")),
    ("stage_now", ("不排除任何选项", "包括加息")),
)
_STAGE_CN = {
    "stage_confirm": "公开讨论放松（降息进入议程）",
    "stage_pivot": "措辞转「耐心 / 良好位置」",
    "stage_transition": "数据逐次会议评估（删去加息选项）",
    "stage_now": "不排除任何选项（含加息倾向）",
}


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            meeting TEXT PRIMARY KEY,
            decision TEXT,
            vote TEXT,
            highlights_json TEXT,
            struck_json TEXT,
            raw_text TEXT,
            source_time TEXT,
            created_at TEXT
        )
    """)
    _READY = True


# ── 纯函数：HTML → 结构化 ─────────────────────────────────────────────────

def parse_content(html: str) -> Dict:
    """解析一条 `contentTop`，返回 `{plain, highlights, struck}`。

    ★ 顺序很关键：**先剥 tooltip**，否则「取消高亮」会被当成正文提取出来
      （它是前端控件文字，不是声明内容）。
    """
    s = _RE_TOOLTIP.sub("", str(html or ""))
    highlights = [re.sub(_RE_TAG, "", h).strip()
                  for h in _RE_HIGHLIGHT.findall(s)]
    struck = [re.sub(_RE_TAG, "", h).strip()
              for h in _RE_STRIKE.findall(s)]
    plain = _RE_BR.sub("\n", s)
    plain = _RE_TAG.sub("", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain).strip()
    return {"plain": plain,
            "highlights": [h for h in highlights if h],
            "struck": [h for h in struck if h]}


def parse_decision(plain: str) -> Dict:
    """从声明正文提取「利率决议」与「票数」。**提取不到就返回 None，不猜**。"""
    v = _RE_VOTE.search(plain or "")
    vote = f"{v.group(1)}票赞成/{v.group(2)}票反对" if v else None

    m = _RE_MOVE.search(plain or "")
    if m:
        direction, bp, low, high = m.groups()
        decision = f"{direction}{bp}bp 至 {low}%–{high}%"
    else:
        h = _RE_HOLD.search(plain or "")
        decision = f"维持 {h.group(1)}%–{h.group(2)}%" if h else None
    return {"decision": decision, "vote": vote}


_HIKE_TOKENS = ("加息", "基准利率上调", "提高利率", "收紧货币政策")
_RE_BP = re.compile(r"上调\s*\d+\s*个基点")


def is_hike_wording(s: str) -> bool:
    """这段文字是否在讲「加息」。

    ★ 2026-11-08：**不能只找"加息"两个字**。金十声明原文写的是
      「上调25个基点」/「上调25个基点，至3.75%至4%」，**全文不出现"加息"这个词**。
      原判据 `any("加息" in s ...)` 因此在真实数据上**永远为 False**（单测抓出来的）。
    """
    t = str(s or "")
    return any(tok in t for tok in _HIKE_TOKENS) or bool(_RE_BP.search(t))


def judge_stage(plain: str, struck: List[str], decision: str = None) -> Dict:
    """关键词匹配 L4 候选档位。**只给候选，`need_manual` 恒为 True**。

    ★ 额外一条判据：**本期未加息、且删掉了加息表述** ⇒ 强 `stage_transition` 信号。
      框架明写「删去加息选项表述」是这阶段的标志，而这个信息**只在删除项里**。

      ⚠️ 2026-11-08 补限定（`decision` 参数）：**本期本身就是加息月时不算**。
         实测 2026-09 那次 `struck` 里有「上调25个基点」（来自反对票描述），
         但 9 月恰恰是**加息月** —— 把它判成"转向信号"是反的。
    """
    text = str(plain or "")
    not_hiking = not (decision and "上调" in str(decision))
    removed_hike = not_hiking and any(is_hike_wording(s) for s in (struck or []))
    hit = []
    for stage, kws in _STAGE_PATTERNS:
        for kw in kws:
            if kw in text:
                hit.append({"stage": stage, "keyword": kw})
                break

    if removed_hike:
        # 本期未加息，且上期含加息表述被删 —— 按框架定义属于 transition 的典型标志
        if not any(h["stage"] == "stage_transition" for h in hit):
            hit.append({"stage": "stage_transition",
                        "keyword": "（本期未加息，且删除了含加息语义的表述）"})

    if not hit:
        return {"stage_candidate": None, "stage_hit": [],
                "stage_text": "无可识别关键词（**不是**没变化，是措辞不在关键词表内）",
                "removed_hike_wording": removed_hike,
                "confidence": "keyword-only", "need_manual": True}

    # 按 _STAGE_PATTERNS 的顺序（越强越先）取第一个
    order = [s for s, _ in _STAGE_PATTERNS]
    best = min(hit, key=lambda h: order.index(h["stage"]) if h["stage"] in order else 99)
    return {
        "stage_candidate": best["stage"],
        "stage_text": _STAGE_CN.get(best["stage"], best["stage"]),
        "stage_hit": hit,
        "removed_hike_wording": removed_hike,
        "confidence": "keyword-only",
        "need_manual": True,
        "note": "关键词候选，**必须人工确认**后才能作为 L4 结论",
    }


# ── 取数与落库 ─────────────────────────────────────────────────────────────

def _month_of(title: str) -> Optional[str]:
    """`2026年10月` → `2026-10`。"""
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月", str(title or ""))
    if not m:
        return None
    mo = int(m.group(2))
    if not 1 <= mo <= 12:
        return None
    return "%s-%02d" % (m.group(1), mo)


def fetch(gte: str, lte: str, timeout: float = 20.0) -> Dict:
    """拉取某区间内的「声明对比」条目。"""
    p = {"category": _CATEGORY, "attr_id": "",
         "time_gte": gte, "time_lte": lte}
    try:
        j = requests.get(_API, params=p, headers=_HEADERS, timeout=timeout).json()
    except Exception as e:
        return {"ok": False, "items": [], "error": f"请求失败: {str(e)[:90]}"}
    if str(j.get("status")) not in ("200", "0"):
        return {"ok": False, "items": [],
                "error": f"status={j.get('status')} {str(j.get('message'))[:60]}"}

    out: List[Dict] = []
    for x in j.get("data") or []:
        jj = ((x.get("data") or {}).get("json") or {})
        contents = jj.get("contents") or []
        if not contents:
            continue

        # ★★ 2026-11-08 修正（重要）：`contents` 的顺序**不统一**！
        #   · id=67166 → ['2026年09月','2026年10月']  ⇒ [上期, 本期]
        #   · id=65233 → ['2026年09月','2026年07月']  ⇒ [本期, 上期]
        #   原实现取 `contents[-1]` 当本期，在第二种情况下**把 9 月当成 7 月**，
        #   导致 2026-09 那次加息被静默吞掉（并被覆盖到 7 月的记录上）。
        #   ⇒ 必须**按 title 里的月份取最大**来判定本期，不能依赖数组顺序。
        parsed_items = []
        for c in contents:
            m = _month_of(c.get("title"))
            if not m:
                continue
            parsed_items.append((m, parse_content(c.get("contentTop"))))
        if not parsed_items:
            continue
        parsed_items.sort(key=lambda kv: kv[0])
        prev_m, prev_p = parsed_items[0]
        cur_m, cur_p = parsed_items[-1]

        dec = parse_decision(cur_p["plain"])
        # struck 从**两边都收**：line-through 有时标在上期文本里，有时根本没有
        struck = []
        for _, p in parsed_items:
            struck.extend(p["struck"])

        out.append({
            "meeting": cur_m,
            "decision": dec["decision"],
            "vote": dec["vote"],
            "highlights": cur_p["highlights"],
            "struck": struck,
            "raw_text": cur_p["plain"],
            "source_time": str(x.get("time") or "")[:19],
            "prev_meeting": prev_m,
        })
    return {"ok": bool(out), "items": out, "error": ""}


def upsert_item(it: Dict) -> None:
    ensure_table()
    db.execute(
        f"INSERT INTO {_TABLE} (meeting, decision, vote, highlights_json, "
        f"struck_json, raw_text, source_time, created_at) "
        f"VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
        f"ON CONFLICT (meeting) DO UPDATE SET "
        f"decision=EXCLUDED.decision, vote=EXCLUDED.vote, "
        f"highlights_json=EXCLUDED.highlights_json, struck_json=EXCLUDED.struck_json, "
        f"raw_text=EXCLUDED.raw_text, source_time=EXCLUDED.source_time",
        (it["meeting"], it.get("decision"), it.get("vote"),
         json.dumps(it.get("highlights") or [], ensure_ascii=False),
         json.dumps(it.get("struck") or [], ensure_ascii=False),
         it.get("raw_text"), it.get("source_time"),
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )


def refresh(months: int = 16) -> Dict:
    """拉取最近 `months` 个月内的所有 FOMC 声明并落库（1 请求）。"""
    end = datetime.now()
    start = end - timedelta(days=31 * max(1, int(months)))
    r = fetch(start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
    if not r.get("ok"):
        return {"ok": False, "saved": 0, "error": r.get("error")}

    saved = 0
    latest = None
    for it in r["items"]:
        try:
            upsert_item(it)
            saved += 1
            if latest is None or it["meeting"] > latest:
                latest = it["meeting"]
        except Exception:
            continue
    return {"ok": saved > 0, "saved": saved, "latest": latest,
            "rows": len(r["items"])}


def load_one(meeting: str = None) -> Dict:
    """读某一期（默认最新）。"""
    ensure_table()
    if meeting:
        row = db.fetch_one(f"SELECT * FROM {_TABLE} WHERE meeting = %s", (meeting,))
    else:
        row = db.fetch_one(f"SELECT * FROM {_TABLE} ORDER BY meeting DESC LIMIT 1")
    if not row:
        return {}
    try:
        hl = json.loads(row.get("highlights_json") or "[]")
        st = json.loads(row.get("struck_json") or "[]")
    except (json.JSONDecodeError, TypeError):
        hl, st = [], []
    return {**row, "highlights": hl, "struck": st}


def view() -> Dict:
    """只读快照：最新一期声明 + L4 候选档位。**不含操作建议**。"""
    try:
        row = load_one()
    except Exception as e:
        return {"ok": False, "status": "invalid",
                "detail": f"读 {_TABLE} 失败: {str(e)[:80]}",
                "text": "FOMC 声明不可用"}

    if not row:
        return {"ok": False, "status": "empty",
                "text": "FOMC 声明无数据（未刷新）",
                "detail": "先在日批跑 `fomc_statement` 或手动 refresh"}

    stage = judge_stage(row.get("raw_text") or "", row.get("struck") or [],
                        decision=row.get("decision"))
    return {
        "ok": True,
        "status": "ok",
        "meeting": row.get("meeting"),
        "decision": row.get("decision"),
        "vote": row.get("vote"),
        "highlights": row.get("highlights") or [],
        "struck": row.get("struck") or [],
        "source_time": row.get("source_time"),
        **stage,
        "text": (f"FOMC {row.get('meeting')} {row.get('decision') or '决议未识别'}"
                 f"（{row.get('vote') or '票数未识别'}）；"
                 f"措辞候选：{stage.get('stage_text')}"),
        "scorer_version": SCORER_VERSION,
    }


def brief() -> str:
    v = view()
    if not v.get("ok"):
        return str(v.get("text"))[:60]
    return (f"{v.get('meeting')} {v.get('decision') or '?'}"
            f"｜措辞候选 {v.get('stage_candidate') or '无'}")
