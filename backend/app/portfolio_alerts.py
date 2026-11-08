# -*- coding: utf-8 -*-
"""持仓双口径提醒（盯盘计划 G4）——**只提醒事实，不含操作指令**（教练禁词纪律同款）。

口径（计划文档 §0 写死，双口径独立阈值 env 可调）：
  · 浮亏 = 现价 / user_portfolio.cost − 1 ≤ PA_COST_LOSS_PCT（默认 -5%）
  · 当日亏损 = 现价 / prev_close − 1 ≤ PA_DAY_LOSS_PCT（默认 -3%）
零外呼：行情读 `tencent._cache`（120s 刷新循环维护）；去重=每码每日每口径一条
（portfolio_alert_log 表 PK）。挂 scheduler.portfolio_alert_loop（交易时段 60s）。
"""
import os
from datetime import datetime, timedelta, timezone

from app.database import db

_BJ = timezone(timedelta(hours=8))
_TABLE_READY = False
_COST_LOSS_PCT = float(os.environ.get("PA_COST_LOSS_PCT") or -5.0)
_DAY_LOSS_PCT = float(os.environ.get("PA_DAY_LOSS_PCT") or -3.0)


def _ensure() -> None:
    global _TABLE_READY
    if _TABLE_READY:
        return
    db.execute("""CREATE TABLE IF NOT EXISTS portfolio_alert_log (
        date TEXT, code TEXT, rule TEXT, price REAL, created_at TEXT,
        PRIMARY KEY (date, code, rule))""")
    _TABLE_READY = True


def check_once() -> dict:
    """查一轮双口径阈值；命中 ⇒ 企微一条（合并）。失败由调用方兜（不抛）。"""
    from app import tencent
    stocks = tencent._cache.get("stocks") or {}
    if not stocks:
        return {"ok": True, "alerts": 0, "note": "no quote cache"}
    day = datetime.now(_BJ).strftime("%Y-%m-%d")
    _ensure()
    alerts = []
    try:
        rows = db.fetch("SELECT code, name, shares, cost FROM user_portfolio") or []
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}
    for r in rows:
        code = str(r.get("code") or "")
        shares = float(r.get("shares") or 0)
        cost = float(r.get("cost") or 0)
        s = stocks.get(code)
        if not s or shares <= 0 or not code:
            continue
        price = float(s.get("price") or 0)
        prev = float(s.get("prev_close") or 0)
        if price <= 0:
            continue
        fired = []
        if cost > 0:
            fl = (price / cost - 1) * 100
            if fl <= _COST_LOSS_PCT:
                fired.append(("cost_loss", f"浮亏 {fl:.1f}%（成本 {cost:.2f} → {price:.2f}）"))
        if prev > 0:
            dl = (price / prev - 1) * 100
            if dl <= _DAY_LOSS_PCT:
                fired.append(("day_loss", f"当日 {dl:.1f}%（昨收 {prev:.2f} → {price:.2f}）"))
        for rule, desc in fired:
            try:
                dup = db.fetch_one("SELECT 1 FROM portfolio_alert_log "
                                   "WHERE date=%s AND code=%s AND rule=%s", (day, code, rule))
                if dup:
                    continue
                db.execute("INSERT INTO portfolio_alert_log VALUES (%s,%s,%s,%s,%s)",
                           (day, code, rule, price,
                            datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S")))
            except Exception as e:
                print(f"[pa] dedupe write failed: {e}")
            alerts.append(f"{r.get('name') or code}({code}) {desc}")
    if alerts:
        try:
            from app.flash import wechat
            wechat.notify("portfolio_alert", "⚠️ 持仓阈值提醒（事实非指令）",
                          "\n".join(f"· {a}" for a in alerts)
                          + f"\n\n> 阈值：浮亏 {_COST_LOSS_PCT}% / 当日 {_DAY_LOSS_PCT}%"
                            "（env 可调）；每码每日每口径一条")
        except Exception as e:
            print(f"[pa] push failed: {e}")
    return {"ok": True, "alerts": len(alerts)}
