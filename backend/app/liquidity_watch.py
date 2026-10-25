# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】流动性三件套日度落库（两融 / 期指基差 / 中美利差）—— 纯观察器
================================================================================
背景（2026-10-25，eypai.net 溯源调研结论 · docs/reports/20261025_eypai_research.md）：
  eypai 最有价值的互补数据是「两融 / 期指基差 / 国债收益率」，而溯源（各文件内嵌
  src 字段）证明其上游全部是公开源：两融=SSE+SZSE 官方、基差=新浪期货(持仓量加权)、
  收益率=akshare·中债官网 ⇒ 本项目**直连上游自建**，不依赖 eypai（避免其停更）。

现状盘点（2026-10-25 实查）——三件套的**实时**链路其实已全部存在：
  · 两融：`flash/margin_sentiment.get_margin()`（金十 mp-api _vir_96，沪/深分列，T+1）；
  · 期指基差：`macro.get_index_futures_basis()`（新浪期货 nff 族 × 现货 sidx，当月/次月）；
  · 中债 10Y：`macro._PANEL_MAP["cn10y"]`（新浪 globalbd_cn10yt）。
⇒ 本模块补的是**缺的那一层：每日落库**（三者此前都只有实时/进程缓存，无历史序列）
  ⇒ MacroEnvCard 基差块注释里"暂不设阈值，须先积累历史基线"由此开始积累。

口径（★ 必须与展示一起披露）：
  · 两融：T+1 数据（margin_date 列自证）；主源金十=**沪+深**（亿元），兜底东财
    RPTA_RZRQ_LSHJ=**沪深北**（含北交所，约 +80 亿台阶）⇒ 序列以 margin_src 列为准；
  · 基差：当月合约，基差 = 现货收盘 − 期货收盘（**正 = 期货贴水**），非年化 basis_rate
    作趋势口径（年化对剩余天数敏感，macro.py 模块头有完整警示）；
  · 中美利差 = 中债 10Y − 美债 10Y（bp）。

★ 预登记声明：不改分数、不进闸门、不改 regime、不碰模拟盘、不进任何决策链。
  失败 fail-open：单源失败落 NULL + degraded 标注，绝不因此让日批红。
================================================================================
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from app.database import db

_TABLE = "liquidity_daily"
_READY = False
_BJ = timezone(timedelta(hours=8))

# 东财 datacenter 两融余额汇总（兜底源）。低频（1 次/日）安全：东财封禁事故
# （2026-09-05）是 fflow/clist **高频**路径；datacenter-web 单日 1 请求与 finance.py
# /解禁日历同域日常使用，无风险。口径 = 沪深北（RZRQYE，元）。
_EM_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_HEADERS = {"Referer": "https://data.eastmoney.com/",
               "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

_NOTE = ("流动性观察器（纯展示，不进决策链）：两融为 T+1 数据（margin_date 列）；"
         "基差=现货−期货收盘（正=贴水，当月合约）；利差=中债10Y−美债10Y。"
         "两融主源金十=沪+深，兜底东财=沪深北（序列或含口径台阶，以 margin_src 为准）。")


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute("""
        CREATE TABLE IF NOT EXISTS liquidity_daily (
            date TEXT PRIMARY KEY,
            margin_date TEXT, margin_src TEXT,
            margin_total_yi REAL, margin_add_yi REAL,
            margin_sse_yi REAL, margin_szse_yi REAL,
            cn10y REAL, us10y REAL, spread_bp REAL,
            basis_json TEXT,
            degraded TEXT, created_at TEXT
        )
    """)
    _READY = True


def _now_str() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ── 两融（纯函数：解析层，单测锚点）────────────────────────────────────────

def _jin10_margin_from(m: Optional[dict]) -> Optional[dict]:
    """金十 get_margin() 输出 → 落库行字段（亿元；沪+深口径）。"""
    if not m or not m.get("date") or _num(m.get("fund_bal")) is None:
        return None
    hist = m.get("hist") or []
    add = None
    if len(hist) >= 2 and _num(hist[0].get("fund_bal")) is not None \
            and _num(hist[1].get("fund_bal")) is not None:
        add = round(_num(hist[0]["fund_bal"]) - _num(hist[1]["fund_bal"]), 1)
    return {"date": str(m["date"])[:10], "src": "jin10",
            "total_yi": round(_num(m["fund_bal"]), 1), "add_yi": add,
            "sse_yi": _num(m.get("sse_yi")), "szse_yi": _num(m.get("szse_yi"))}


def _em_margin_from_rows(rows: Optional[list]) -> Optional[dict]:
    """东财 RPTA_RZRQ_LSHJ 的 result.data → 落库行字段（沪深北口径，亿元）。"""
    if not isinstance(rows, list) or len(rows) < 2:
        return None
    vals = []
    for r in rows[:2]:
        d = str(r.get("DIM_DATE") or "")[:10]
        ye = _num(r.get("RZRQYE"))          # 两融余额（元）
        if not d or ye is None:
            return None
        vals.append((d, ye))
    return {"date": vals[0][0], "src": "eastmoney",
            "total_yi": round(vals[0][1] / 1e8, 1),
            "add_yi": round((vals[0][1] - vals[1][1]) / 1e8, 1),
            "sse_yi": None, "szse_yi": None}


def _margin_from_jin10() -> Optional[dict]:
    try:
        from app.flash.margin_sentiment import get_margin
        return _jin10_margin_from(get_margin(days=8))
    except Exception as e:
        print(f"[liquidity] jin10 margin failed: {e}")     # ASCII（铁律⑥）
        return None


def _margin_from_eastmoney() -> Optional[dict]:
    try:
        r = requests.get(_EM_URL, params={
            "reportName": "RPTA_RZRQ_LSHJ", "columns": "ALL",
            "sortColumns": "dim_date", "sortTypes": "-1",
            "pageSize": 3, "pageNumber": 1,
        }, headers=_EM_HEADERS, timeout=20)
        rows = ((r.json() or {}).get("result") or {}).get("data")
        return _em_margin_from_rows(rows)
    except Exception as e:
        print(f"[liquidity] eastmoney margin failed: {e}")  # ASCII（铁律⑥）
        return None


# ── 基差 + 中美利差（纯函数：从宏观面板快照提取）──────────────────────────

def _panel_liquidity(panel: Optional[dict]) -> Dict:
    """宏观面板 → {cn10y, us10y, spread_bp, basis:{品种:{当月合约字段}}}。

    全部 `.get()` 防御：面板单品种失败时留 None（缺失 ≠ 0，绝不填 0）。
    """
    out: Dict = {"cn10y": None, "us10y": None, "spread_bp": None, "basis": {}}
    if not isinstance(panel, dict):
        return out
    cn = _num((panel.get("cn10y") or {}).get("price"))
    us = _num((panel.get("us10y") or {}).get("price"))
    if cn is not None:
        out["cn10y"] = cn
    if us is not None:
        out["us10y"] = us
    if cn is not None and us is not None:
        out["spread_bp"] = round((cn - us) * 100, 1)
    fb = panel.get("_futures_basis") or {}
    for item in (fb.get("items") or []):
        tenors = item.get("tenors") or []
        cur = next((t for t in tenors if t.get("label") == "当月"), None) or \
              (tenors[0] if tenors else None)
        if not cur:
            continue
        out["basis"][item.get("product") or "?"] = {
            "code": cur.get("code"), "basis": cur.get("basis"),
            "basis_rate": cur.get("basis_rate"),
            "annualized": cur.get("annualized"), "oi": cur.get("oi"),
            "days_left": cur.get("days_left"),
        }
    return out


def _panel_snapshot() -> Dict:
    try:
        from app.macro import get_macro_panel
        return _panel_liquidity(get_macro_panel())
    except Exception as e:
        print(f"[liquidity] macro panel failed: {e}")      # ASCII（铁律⑥）
        return _panel_liquidity(None)


# ── 日批入口（fail-open：单源失败落 NULL，不让日批红）──────────────────────

def run_daily(day: str = None) -> Dict:
    ensure_table()
    if not day:
        from app.flash.rules import latest_completed_trading_day
        day = str(latest_completed_trading_day())[:10]
    degraded: List[str] = []
    margin = _margin_from_jin10() or _margin_from_eastmoney()
    if not margin:
        degraded.append("margin")
    pl = _panel_snapshot()
    if pl.get("cn10y") is None or pl.get("us10y") is None:
        degraded.append("bonds")
    if not pl.get("basis"):
        degraded.append("basis")
    row = {
        "date": day,
        "margin_date": (margin or {}).get("date"),
        "margin_src": (margin or {}).get("src"),
        "margin_total_yi": (margin or {}).get("total_yi"),
        "margin_add_yi": (margin or {}).get("add_yi"),
        "margin_sse_yi": (margin or {}).get("sse_yi"),
        "margin_szse_yi": (margin or {}).get("szse_yi"),
        "cn10y": pl.get("cn10y"), "us10y": pl.get("us10y"),
        "spread_bp": pl.get("spread_bp"),
        "basis_json": json.dumps(pl.get("basis"), ensure_ascii=False)
                      if pl.get("basis") else None,
        "degraded": ",".join(degraded) or None,
        "created_at": _now_str(),
    }
    try:
        db.upsert(_TABLE, row, conflict_columns=["date"])
    except Exception as e:
        return {"ok": False, "error": f"落库失败: {str(e)[:80]}", "degraded": degraded}
    return {"ok": True, "date": day, "degraded": degraded,
            "margin_date": row["margin_date"], "margin_src": row["margin_src"]}


# ── 读取（GET /macro/liquidity；history 正序供前端画趋势）──────────────────

def _jload(s):
    try:
        v = json.loads(s) if s else None
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


def load(days: int = 30) -> Dict:
    ensure_table()
    n = max(1, min(int(days or 30), 120))
    try:
        rows = db.fetch(f"SELECT * FROM {_TABLE} ORDER BY date DESC LIMIT {n}")
    except Exception as e:
        return {"ok": False, "error": f"读取失败: {str(e)[:80]}",
                "latest": None, "history": []}
    if not rows:
        return {"ok": True, "date": None, "latest": None, "history": [],
                "degraded": None, "note": _NOTE}
    latest_r = rows[0]
    latest_basis = _jload(latest_r.get("basis_json")) or {}
    history = []
    for r in reversed(rows):                      # 正序（旧 → 新）
        b = _jload(r.get("basis_json")) or {}
        history.append({
            "date": r.get("date"),
            "margin_total_yi": _num(r.get("margin_total_yi")),
            "margin_add_yi": _num(r.get("margin_add_yi")),
            "spread_bp": _num(r.get("spread_bp")),
            "basis_rate": {k: _num(v.get("basis_rate"))
                           for k, v in b.items() if isinstance(v, dict)},
        })
    # 5 日净变化：用**本表序列**算（与 jin10 的 fund_bal_chg5 口径一致，但兜底源也可算）
    chg5 = None
    if len(history) >= 6:
        a, b5 = history[-1].get("margin_total_yi"), history[-6].get("margin_total_yi")
        if a is not None and b5 is not None:
            chg5 = round(a - b5, 1)
    latest = {
        "margin": {"date": latest_r.get("margin_date"), "src": latest_r.get("margin_src"),
                   "total_yi": _num(latest_r.get("margin_total_yi")),
                   "add_yi": _num(latest_r.get("margin_add_yi")), "chg5_yi": chg5,
                   "sse_yi": _num(latest_r.get("margin_sse_yi")),
                   "szse_yi": _num(latest_r.get("margin_szse_yi"))},
        "bonds": {"cn10y": _num(latest_r.get("cn10y")),
                  "us10y": _num(latest_r.get("us10y")),
                  "spread_bp": _num(latest_r.get("spread_bp"))},
        "basis": latest_basis,
    }
    return {"ok": True, "date": latest_r.get("date"), "latest": latest,
            "history": history, "degraded": latest_r.get("degraded"), "note": _NOTE}
