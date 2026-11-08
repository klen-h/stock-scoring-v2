# -*- coding: utf-8 -*-
"""持仓当晚公告/事件夜报（盯盘计划 G11c）——真实持仓 × 当日个股快讯关键词筛选。

数据源：`eastmoney_news.get_stock_news`（**既有依赖**，消息分同源；每码 1 次，
持仓通常 10~20 只 ⇒ 一次夜报 5~10s，日批深夜跑不抢盘中带宽）。
筛选关键词冻结：公告/回购/增持/减持/中标/合同/立案/业绩预告/预增/预亏/停牌/复牌。
推送：企微一条合并夜报（事实陈述，无操作建议）；幂等=portfolio_alert_log(rule='news')。
"""
from datetime import datetime, timedelta, timezone

from app.database import db

_BJ = timezone(timedelta(hours=8))
_KWS = ("公告", "回购", "增持", "减持", "中标", "合同", "立案",
        "业绩预告", "预增", "预亏", "停牌", "复牌", "警示")
_MAX_CODES = 30          # 持仓上限保护（egress 纪律）
_PER_LIMIT = 15          # 每码取最近条数


def run() -> dict:
    day = datetime.now(_BJ).strftime("%Y-%m-%d")
    _ensure_log()
    try:
        held = db.fetch("SELECT code, name FROM user_portfolio") or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}
    codes = [str(h.get("code"))[-6:] for h in held
             if str(h.get("code") or "")[-6:]][:_MAX_CODES]
    names = {str(h.get("code"))[-6:]: (h.get("name") or "") for h in held}
    if not codes:
        return {"ok": True, "hits": 0, "note": "无真实持仓"}
    from app.eastmoney_news import get_stock_news
    digest, today_head = [], f"{day} 00:00"
    for c in codes:
        if _already(day, c):
            continue
        try:
            items = get_stock_news(c, limit=_PER_LIMIT) or []
        except Exception:
            continue
        hits = []
        for it in items:
            t = str((it or {}).get("content") or (it or {}).get("title") or "")
            tm = str((it or {}).get("time") or (it or {}).get("published") or "")
            if tm[:10] != day:
                continue                       # 只推当晚/当日
            if any(k in t for k in _KWS):
                hits.append(f"{tm[5:16]} {t[:70]}")
        if hits:
            _mark(day, c)
            digest.append(f"【{names.get(c) or c}({c})】")
            digest += [f"· {h}" for h in hits[:3]]
    if digest:
        try:
            from app.flash import wechat
            wechat.notify("holdings_news", "📰 持仓公告/事件夜报（事实陈述）",
                          "\n".join(digest) + "\n\n> 关键词筛选非操作建议；明细见个股详情页消息流")
        except Exception as e:
            print(f"[holdings_news] push failed: {e}")
    return {"ok": True, "hits": len(digest), "codes": len(codes)}


def _ensure_log() -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_alert_log (
        date TEXT, code TEXT, rule TEXT, price REAL, created_at TEXT,
        PRIMARY KEY (date, code, rule))""")


def _already(day: str, code: str) -> bool:
    try:
        return bool(db.fetch_one("SELECT 1 FROM portfolio_alert_log "
                                 "WHERE date=%s AND code=%s AND rule='news'",
                                 (day, code)))
    except Exception:
        return False


def _mark(day: str, code: str) -> None:
    try:
        db.execute("INSERT INTO portfolio_alert_log VALUES (%s,%s,'news',NULL,%s)",
                   (day, code, datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")))
    except Exception as e:
        print(f"[holdings_news] mark failed: {e}")
