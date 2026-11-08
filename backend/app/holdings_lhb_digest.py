# -*- coding: utf-8 -*-
"""持仓龙虎榜夜报（盯盘计划 G11）——收盘后查当日 lhb_history 是否出现**真实持仓**。

★ 零新数据源（lhb_history 日批已落库）；用户持仓=`user_portfolio`（单用户项目直读全表）。
  推送=企微一条合并夜报（事实陈述：上榜方向/净买额），**不含任何操作建议**；
  幂等=portfolio_alert_log 复用（rule='lhb'）；无持仓上榜=合法空态。
挂日批 `holdings_lhb_digest`，排在 `lhb` 之后（它读的就是当日榜单）。
"""
from datetime import datetime, timedelta, timezone

from app.database import db

_BJ = timezone(timedelta(hours=8))


def run() -> dict:
    day = (datetime.now(_BJ) - timedelta(hours=8)).strftime("%Y-%m-%d")  # 收盘定稿日=昨日口径由 lhb 落库日决定
    try:
        rows = db.fetch("SELECT code, name, net_buy, quote_change FROM lhb_history "
                        "WHERE date=%s", (day,)) or []
        held = db.fetch("SELECT code, name FROM user_portfolio") or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:100]}
    if not rows or not held:
        return {"ok": True, "hits": [], "note": "当日无榜单或无持仓"}
    held_map = {str(h.get("code"))[-6:]: h.get("name") for h in held}
    _ensure_log()
    hits = []
    for r in rows:
        code = str(r.get("code") or "")[-6:]
        if code not in held_map:
            continue
        if _already(day, code):
            continue
        nb = r.get("net_buy")
        nb_txt = "—" if nb in (None, "") else f"{float(nb) / 10000:.2f} 亿"
        hits.append(f"{held_map[code]}({code}) 上榜 · 净买 {nb_txt} · 涨跌 {r.get('quote_change') if r.get('quote_change') is not None else '—'}%")
        _mark(day, code)
    if hits:
        try:
            from app.flash import wechat
            wechat.notify("holdings_lhb", "📜 持仓龙虎榜夜报（事实陈述）",
                          "\n".join(f"· {h}" for h in hits)
                          + "\n\n> 上榜=当日公开信息，非操作建议；明细见龙虎榜页")
        except Exception as e:
            print(f"[holdings_lhb] push failed: {e}")
    return {"ok": True, "hits": len(hits), "date": day}


def _ensure_log() -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_alert_log (
        date TEXT, code TEXT, rule TEXT, price REAL, created_at TEXT,
        PRIMARY KEY (date, code, rule))""")


def _already(day: str, code: str) -> bool:
    try:
        return bool(db.fetch_one("SELECT 1 FROM portfolio_alert_log "
                                 "WHERE date=%s AND code=%s AND rule='lhb'", (day, code)))
    except Exception:
        return False


def _mark(day: str, code: str) -> None:
    try:
        db.execute("INSERT INTO portfolio_alert_log VALUES (%s,%s,'lhb',NULL,%s)",
                   (day, code, datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        print(f"[holdings_lhb] mark failed: {e}")
