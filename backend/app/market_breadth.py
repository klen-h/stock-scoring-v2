# -*- coding: utf-8 -*-
"""
================================================================================
盘面广度确认（2026-11-28）
================================================================================
【为什么做】用户复盘 11-27：13:24 政策 → 13:33 中证1000期货涨停 → 13:36 创业板+10%。
  **"期货涨停"那一刻是比任何分析都硬的信号** —— 它说明不是个别板块在动，是整片市场在动。

  系统当时看不到两件事：
    ① 股指期货触及涨跌停（**没有任何期货行情源**，`cross_market_watch` 只有海外指数）
    ② 板块层面的广度（`plate_daily_zz` 有 104 个板块的涨跌，但没人汇总成"多少个在涨"）

【两条腿，口径不同，必须说清】
  · `futures_limit`  —— **盘中实时**信号：从 `flash_news` 正文识别
    "中证1000股指期货（IM）主力合约触及涨停"。金十一般 1 分钟内发。
    ⚠️ 依赖快讯是否发；没发就抓不到（这是**覆盖不全**，不是判断错误）。
  · `breadth`        —— **收盘定稿**：从 `plate_daily_zz` 算板块上涨占比 / 暴涨板块数。
    盘中该日未落库 ⇒ 返回 None，如实标注。

【定位】纯观察器
  · 只陈述"多少个板块在涨、有没有期货涨停"，不改评分、不进闸门
  · "广度确认"不等于"可以买" —— 11-27 银行是唯一下跌板块，
    买错方向照样亏（广度是环境，不是标的）
================================================================================
"""
import re
from typing import Dict, List, Optional

from app.database import db

# ── 股指期货品种（只认 A 股四个，海外"标普500期货"不在此列） ──
FUTURE_PRODUCTS = {
    "IM": "中证1000",
    "IC": "中证500",
    "IF": "沪深300",
    "IH": "上证50",
}
# 名称兜底：正文可能写"中证1000股指期货（IM）"，也可能只写中文
_NAME2CODE = {v: k for k, v in FUTURE_PRODUCTS.items()}

# 广度分档阈值（板块口径，104 个 industry）
STRONG_PCT = 5.0        # 单板块涨跌 ≥5% 记"暴涨/暴跌"
BREADTH_FULL = 0.90     # 上涨占比 ≥90% ⇒ 全面
BREADTH_MOST = 0.70     # ≥70% ⇒ 普涨
BREADTH_FEW = 0.30      # ≤30% ⇒ 普跌
BREADTH_ALMOST_NONE = 0.10  # ≤10% ⇒ 全面


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest_day() -> str:
    r = db.fetch_one("SELECT MAX(date) AS d FROM plate_daily_zz WHERE kind='industry'")
    return str((r or {}).get("d") or "")[:10]


# ────────────────────────── ① 板块广度 ──────────────────────────
def breadth(day: Optional[str] = None) -> Optional[Dict]:
    """板块广度（**收盘定稿**口径）。当日未落库返回 None。"""
    day = (day or _latest_day())[:10]
    rows = db.fetch(
        """SELECT name, change_pct FROM plate_daily_zz
           WHERE date=%s AND kind='industry'""", (day,)) or []
    chgs = [(r.get("name"), _num(r.get("change_pct"))) for r in rows]
    chgs = [(n, c) for n, c in chgs if c is not None]
    if not chgs:
        return None

    n = len(chgs)
    up = sum(1 for _, c in chgs if c > 0)
    down = sum(1 for _, c in chgs if c < 0)
    flat = n - up - down
    strong_up = sum(1 for _, c in chgs if c >= STRONG_PCT)
    strong_down = sum(1 for _, c in chgs if c <= -STRONG_PCT)
    up_ratio = up / n
    vals = sorted(c for _, c in chgs)
    median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2

    if up_ratio >= BREADTH_FULL and strong_up >= 20:
        verdict, level = "全面暴涨", "extreme_up"
    elif up_ratio >= BREADTH_MOST:
        verdict, level = "普涨", "up"
    elif up_ratio <= BREADTH_ALMOST_NONE and strong_down >= 20:
        verdict, level = "全面暴跌", "extreme_down"
    elif up_ratio <= BREADTH_FEW:
        verdict, level = "普跌", "down"
    else:
        verdict, level = "分化", "mixed"

    # 极端一侧的板块（给"谁在涨/谁在跌"一个抓手，各取 3 个）
    ranked = sorted(chgs, key=lambda x: x[1], reverse=True)
    return {
        "date": day,
        "n_sectors": n,
        "up": up, "down": down, "flat": flat,
        "up_ratio": round(up_ratio, 3),
        "strong_up": strong_up,
        "strong_down": strong_down,
        "median_chg": round(median, 2),
        "verdict": verdict,
        "level": level,
        "top": [{"name": k, "chg_pct": round(v, 2)} for k, v in ranked[:3]],
        "bottom": [{"name": k, "chg_pct": round(v, 2)} for k, v in ranked[-3:][::-1]],
        "caliber": "收盘定稿（plate_daily_zz，板块口径）",
    }


# ────────────────────── ② 股指期货涨跌停（盘中） ──────────────────────
_RE_LIMIT = re.compile(r"涨停|跌停")


def _parse_future(text: str) -> Optional[Dict]:
    """从一条快讯正文解析股指期货涨跌停。命中返回 {product,name,kind}。"""
    if "期货" not in text:
        return None
    # 必须同时出现"股指期货"语境 + 具体品种，避免把"原油期货涨停"误收
    if "股指期货" not in text and not any(k in text for k in FUTURE_PRODUCTS):
        return None
    prod = None
    for k in FUTURE_PRODUCTS:
        if k in text:
            prod = k
            break
    if not prod:
        for name, code in _NAME2CODE.items():
            if name in text:
                prod = code
                break
    if not prod:
        return None
    m = _RE_LIMIT.search(text)
    if not m:
        return None
    kind = "limit_up" if m.group(0) == "涨停" else "limit_down"
    return {"product": prod, "name": FUTURE_PRODUCTS[prod], "kind": kind}


def futures_limit(day: Optional[str] = None, hours: int = 0) -> List[Dict]:
    """股指期货触及涨/跌停（来自快讯）。按时间正序、同品种同方向去重。"""
    if hours and hours > 0:
        from datetime import datetime, timedelta, timezone
        since = (datetime.now(timezone(timedelta(hours=8)))
                 - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")
        rows = db.fetch(
            """SELECT id, time, content FROM flash_news
               WHERE time >= %s ORDER BY time""", (since,)) or []
    else:
        d = (day or _latest_day())[:10]
        rows = db.fetch(
            """SELECT id, time, content FROM flash_news
               WHERE time LIKE %s ORDER BY time""", (d + "%",)) or []

    seen = set()
    out = []
    for r in rows:
        text = r.get("content") or ""
        hit = _parse_future(text)
        if not hit:
            continue
        key = (hit["product"], hit["kind"])
        if key in seen:            # 同一品种同方向只留最早那条（首次触及才是信号）
            continue
        seen.add(key)
        out.append({
            "time": str(r.get("time") or ""),
            "product": hit["product"],
            "name": hit["name"],
            "kind": hit["kind"],
            "label": "%s %s" % (hit["name"], "涨停" if hit["kind"] == "limit_up" else "跌停"),
            "text": text.replace("\n", " ")[:120],
        })
    return out


def snapshot(day: Optional[str] = None, hours: int = 0) -> Dict:
    """广度确认快照：板块广度（收盘）+ 期货涨跌停（盘中）。"""
    b = breadth(day)
    f = futures_limit(day=day, hours=hours)
    notes = []
    if b:
        notes.append("板块广度取 %s 收盘定稿" % b["date"])
    else:
        notes.append("当日板块快照未落库 ⇒ 广度不可用（盘中正常）")
    notes.append("期货涨跌停来自快讯正文识别，快讯没发则抓不到（覆盖不全，非判断错误）")
    return {
        "ok": True,
        "date": (day or _latest_day())[:10],
        "breadth": b,
        "futures_limit": f,
        "n_futures": len(f),
        "note": "；".join(notes),
    }
