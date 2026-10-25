# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】李大霄视角卡 —— xiaopage LLM 解读的接收 / 归一 / 读取（纯展示参考）
================================================================================
链路（事件驱动，.codebuddy/plans/李大霄视角卡片_xiaopage解读接入__3f8a2c61.md 定稿）：
  xiaopage（B站轮询→转写→LLM 解读，D:\\stock-scoring\\xiaopage）在 LLM 输出解读的
  时刻 POST /api/market/lidaxiao/push（payload 携带 新解读+前2条 自愈；fail-soft）
  ⇒ 本模块 ingest_entries()：防御解析 → 主键合成 → 归一 → 瘦身落库
  ⇒ GET /api/market/lidaxiao ← 工作台右栏只读卡片（60s 随教练卡轮询）。
  历史 484 条由 scripts/backfill_lidaxiao.py 一次性本地回填（同一 ingest 口径）。

上游 schema 实勘（2026-10-25，fresh 484 条全量核实）：
  · 字段：_id / bvid / title / date(北京时间 YYYY-MM-DD HH:MM) /
    sentiment_score(0-100，50=中性) / disclaimer_level(1-5) / summary / tags /
    structured{core_views, sectors, operation_advice, risk_tips} /
    deep_analysis{final_conclusion, …} /
    logic_factors[{factor_name, factor_value, weight 0-1, direction, description}] /
    hit_status(pending/hit/miss)。
  · 18 条缺 _id（含 2026-09-24 近期条）⇒ 主键合成兜底（_id ‖ bvid ‖ auto:date|title）；
    缺 date 才跳过（排序/lag 的硬依赖）。
  · direction 全量普查 33 种取值（看跌×1587 / 中性偏空×446 / 中性偏多×348 / 看涨×214 /
    中性×181 / 看多×2 / 变体 28 种，如"看跌（对美股）""看涨/看跌（分化）"）
    ⇒ 包含式模糊归一，不能 == 比较。
  · 情绪六级口径移植自 xiaopage src/utils/sentiment.ts（保证两站同分同色）。
  · deep_analysis 整体不入库（体积大头），唯一例外 final_conclusion（卡面主文案）。

★ 预登记声明：不改分数、不进闸门、不改 regime、不碰模拟盘、不进任何决策链。
  卡面为 LLM 二手解读（xiaopage 产物，非本人原文），仅供研究参考。
================================================================================
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_TABLE = "lidaxiao_views"
_READY = False
_BJ = timezone(timedelta(hours=8))     # 北京时间（上游 date 列即按它写）

_STALE_DAYS = 14      # 他不是每日发视频，滞后 2~7 天是常态；>14 天才警示（措辞两可：停更/推送异常）

_NOTE = ("LLM 结构化解读（xiaopage 项目产物，非本人原文）；"
         "观点仅供参考，不构成投资建议，不进入本项目任何决策链")


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS lidaxiao_views (
            entry_id TEXT PRIMARY KEY,
            bvid TEXT, title TEXT, date TEXT,
            sentiment_score INTEGER, sentiment_level TEXT,
            disclaimer_level INTEGER,
            summary TEXT, tags TEXT,
            core_views TEXT, sectors TEXT, operation_advice TEXT, risk_tips TEXT,
            final_conclusion TEXT,
            factors_json TEXT,
            hit_status TEXT, created_at TEXT
        )
    """)
    _READY = True


# ── 归一纯函数（单测锚点：backend/tests/generated/test_lidaxiao_views.py）──────

def sentiment_level(score) -> Optional[str]:
    """情绪六级（口径移植自 xiaopage src/utils/sentiment.ts，两站同分同色）。"""
    s = _int(score)
    if s is None:
        return None
    if s <= 15:
        return "极度恐慌"
    if s <= 35:
        return "悲观"
    if s <= 45:
        return "谨慎偏空"
    if s <= 55:
        return "谨慎偏多"
    if s <= 75:
        return "乐观"
    return "极度狂热"


def norm_direction(raw) -> str:
    """direction 模糊归一 → bull/bear/mixed/neutral（上游枚举漂移，见模块头普查）。"""
    d = str(raw or "").strip()
    if not d:
        return "neutral"
    bull = ("看涨" in d) or ("看多" in d)
    bear = ("看跌" in d) or ("看空" in d)
    if (bull and bear) or ("分化" in d):
        return "mixed"                       # "看跌（对高估股）/看涨（对防御股）"
    if bear or d.startswith("中性偏空"):
        return "bear"                        # "看跌（对美股）"
    if bull or d.startswith("中性偏多"):
        return "bull"                        # "看多"（不带"涨"字的变体）
    return "neutral"


# ── 防御解析 ────────────────────────────────────────────────────────────────

def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _entry_key(e: dict) -> Optional[str]:
    """主键合成：_id ‖ bvid ‖ auto:date|title（18 条缺 _id 的实证兜底）。"""
    k = str(e.get("_id") or "").strip()
    if k:
        return k
    k = str(e.get("bvid") or "").strip()
    if k:
        return k
    d = str(e.get("date") or "").strip()
    if d:
        return "auto:" + d + "|" + str(e.get("title") or "")[:32]
    return None


def _arr_json(v, limit: int = 0) -> Optional[str]:
    """数组字段 → JSON 字符串（防御：非 list / 空数组一律 None，未知元素转 str）。"""
    if not isinstance(v, list):
        return None
    vals = [str(x) for x in v if x is not None]
    if limit > 0:
        vals = vals[:limit]
    return json.dumps(vals, ensure_ascii=False) if vals else None


def _top_factors(e: dict, n: int = 3) -> Optional[str]:
    """logic_factors 按 weight 降序取 Top n（含归一 direction；入库备用，卡面不展示）。"""
    raw = e.get("logic_factors")
    if not isinstance(raw, list):
        return None
    fs = []
    for f in raw:
        if not isinstance(f, dict):
            continue
        try:
            w = float(f.get("weight"))
        except (TypeError, ValueError):
            w = 0.0
        fs.append((w, f))
    fs.sort(key=lambda x: -x[0])
    out = [{"factor_name": str(f.get("factor_name") or ""),
            "weight": w,
            "direction": str(f.get("direction") or ""),
            "direction_norm": norm_direction(f.get("direction"))}
           for w, f in fs[:n]]
    return json.dumps(out, ensure_ascii=False) if out else None


def _slim(e: dict) -> Optional[dict]:
    """上游 entry → 落库行（防御解析；缺 date 返回 None = 跳过该条）。"""
    date = str(e.get("date") or "").strip()
    key = _entry_key(e)
    if not date or not key:
        return None
    st = e.get("structured") if isinstance(e.get("structured"), dict) else {}
    da = e.get("deep_analysis") if isinstance(e.get("deep_analysis"), dict) else {}
    score = _int(e.get("sentiment_score"))
    return {
        "entry_id": key,
        "bvid": str(e.get("bvid") or "").strip() or None,
        "title": str(e.get("title") or ""),
        "date": date,
        "sentiment_score": score,
        "sentiment_level": sentiment_level(score),
        "disclaimer_level": _int(e.get("disclaimer_level")),
        "summary": str(e.get("summary") or "") or None,
        "tags": _arr_json(e.get("tags")),
        "core_views": _arr_json(st.get("core_views"), limit=5),
        "sectors": _arr_json(st.get("sectors")),
        "operation_advice": str(st.get("operation_advice") or "") or None,
        "risk_tips": _arr_json(st.get("risk_tips"), limit=3),
        "final_conclusion": str(da.get("final_conclusion") or "") or None,
        "factors_json": _top_factors(e),
        "hit_status": str(e.get("hit_status") or "") or None,
        "created_at": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
    }


# ── 入库（POST /lidaxiao/push 与本地回填共用，幂等）─────────────────────────

def ingest_entries(entries) -> Dict:
    """批量入库：逐条防御校验，坏条目 skip 不炸整批（响应带 skipped 明细供推送方留痕）。"""
    ensure_table()
    stored, skipped, reasons = 0, 0, []
    for e in (entries or []):
        if not isinstance(e, dict):
            skipped += 1
            reasons.append("entry 非对象")
            continue
        row = _slim(e)
        if not row:
            skipped += 1
            reasons.append("缺 date：" + str(e.get("_id") or e.get("title") or "")[:30])
            continue
        try:
            db.upsert(_TABLE, row, conflict_columns=["entry_id"])
            stored += 1
        except Exception as ex:
            skipped += 1
            reasons.append("落库失败：" + str(ex)[:60])
    return {"stored": stored, "skipped": skipped,
            "reasons": reasons[:5], "latest": _latest_meta()}


def _latest_meta() -> Optional[Dict]:
    try:
        r = db.fetch_one(f"SELECT entry_id, title, date FROM {_TABLE} "
                         f"ORDER BY date DESC LIMIT 1")
        return {"entry_id": r.get("entry_id"), "title": r.get("title"),
                "date": r.get("date")} if r else None
    except Exception:
        return None


# ── 读取（GET /market/lidaxiao）─────────────────────────────────────────────

def _jload_list(s) -> Optional[list]:
    if not s:
        return None
    try:
        v = json.loads(s)
        return v if isinstance(v, list) else None
    except (TypeError, ValueError):
        return None


def _row_to_latest(r: dict) -> Dict:
    bvid = r.get("bvid")
    return {
        "entry_id": r.get("entry_id"),
        "bvid": bvid,
        "title": r.get("title"),
        "date": r.get("date"),
        "video_url": f"https://www.bilibili.com/video/{bvid}" if bvid else None,
        "sentiment_score": r.get("sentiment_score"),
        "sentiment_level": r.get("sentiment_level"),
        "disclaimer_level": r.get("disclaimer_level"),
        "summary": r.get("summary"),
        "tags": _jload_list(r.get("tags")),
        "final_conclusion": r.get("final_conclusion"),
        "sectors": _jload_list(r.get("sectors")),
        # ↓ 以下字段卡面不渲染（入库备用，Phase 2 日报/折叠区直接取）
        "core_views": _jload_list(r.get("core_views")),
        "operation_advice": r.get("operation_advice"),
        "risk_tips": _jload_list(r.get("risk_tips")),
        "factors": _jload_list(r.get("factors_json")) or [],
        "hit_status": r.get("hit_status"),
    }


def _lag_days(date_str) -> Optional[int]:
    try:
        d = datetime.strptime(str(date_str)[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return (datetime.now(_BJ).date() - d.date()).days


def load(limit: int = 14) -> Dict:
    """最新一期全字段 + 近 N 期轨迹 + 滞后/过期标记（前端卡片唯一数据源）。"""
    ensure_table()
    n = max(1, min(int(limit or 14), 60))
    try:
        rows = db.fetch(f"SELECT * FROM {_TABLE} ORDER BY date DESC LIMIT {n + 1}")
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {str(e)[:80]}",
                "latest": None, "trend": []}
    if not rows:
        return {"ok": True, "latest": None, "trend": [],
                "lag_days": None, "stale": False, "note": _NOTE}
    latest = _row_to_latest(rows[0])
    trend = [{"date": str(r.get("date") or "")[:10],
              "score": r.get("sentiment_score"),
              "title": r.get("title")} for r in rows[1:n + 1]]
    lag = _lag_days(latest.get("date"))
    return {"ok": True, "latest": latest, "trend": trend,
            "lag_days": lag, "stale": lag is not None and lag > _STALE_DAYS,
            "note": _NOTE}
