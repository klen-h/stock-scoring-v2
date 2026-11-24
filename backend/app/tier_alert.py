# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】持仓「分级观察」的盘中企微提醒
================================================================================
背景（2026-11-25，用户："只打开工作台页面会有这个提醒吗？是 60s 获取实时数据吗？
 需要推送企微"）

三个问题的答案（决定了本模块的形状）：
  ① 工作台**没有** —— 分级观察原本只挂在「我的持仓」页 ⇒ 见 `Workbench` 侧改动；
  ② **不是 60s**，是**交易时段 30s / 非交易时段 300s** 的**前端轮询**
     （`usePortfolio.REFRESH_CONFIG`）⇒ 只在页面打开时跑，关掉页面就没有了；
  ③ 企微推送**原本没有** ⇒ 本模块补的就是这一块。

★★ 为什么不能"每 30 秒推一次"
  前端 30s 轮询是**展示**，推送是**打扰**，两者频率绝不能对齐。否则一天 240 个
  交易分钟会推出几百条，用户第一件事就是关掉它 —— 这与项目里
  "推送去重一律落库（`store.is_schedule_done`）"是同一条纪律。
  ⇒ 本模块的推送条件收紧成**事件**而不是**状态**：

     · 只在**交易时段**检查（非盘中推"持仓有什么变化"没有操作意义）
     · 只在 **tier 恶化到 `alert`** 时推（`watch` 不推 —— 全市场一天能有大半点）
     · **每个 code 每个交易日最多推 1 次**（落库主键 `(date, code)` 天然去重）
     · 多次检查合并成**一条消息**（不逐只发，避免连响）

★ 红线：**只提示，不给买卖指令**；不改评分、不进闸门、不碰模拟盘。
  文案里必须带"观察器、不是买卖建议"，否则会被当成交易信号。

⚠️ 与既有推送的分工（新增告警前必须查这张表，防重复）：
  `coach.stop_loss_hit`  持仓破止损（成本 × 硬止损线）—— **计划价位**触发
  `coach.emotion_fuse`   当日浮亏超阈值 —— **当日盈亏**触发
  `portfolio_radar`      组合面风险（-8%/-12%）
  **本模块**              箱体位置 + 上涨质量 —— **结构/证据**触发
  ⇒ 四者判据不同，可能同一只票同时命中；这是**信息冗余**不是 bug，
    但任何一方都不得声称"另一方的结论"。
================================================================================
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_TABLE = "tier_alert_log"
_BJ = timezone(timedelta(hours=8))
_READY = False

# 检查间隔（秒）：盘中 5 分钟。**不要调到 60s** —— 见文件头。
CHECK_INTERVAL = max(120, int(os.environ.get("TIER_ALERT_INTERVAL_SEC") or 300))
# 推送开关（与其它业务告警同一口径：默认跟随 WECHAT_BUSINESS_ALERTS 的语义，
# 但本模块独立可控，便于单独关掉）
PUSH_ENABLED = (os.environ.get("TIER_ALERT_PUSH") or "1").strip() != "0"


def _today() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            date TEXT, code TEXT, tier TEXT,
            price REAL, chg_pct REAL, reason TEXT,
            pushed_at TEXT,
            PRIMARY KEY (date, code)
        )
    """)
    _READY = True


def _pushed_today(day: str) -> set:
    """当天已推过的代码集合（落库去重，**不用进程内 dict** —— 重启会重推）。"""
    ensure_table()
    try:
        rows = db.fetch(
            f"SELECT code FROM {_TABLE} WHERE date=%s", (day,)) or []
        return {str(r.get("code") or "") for r in rows}
    except Exception as e:
        print(f"[tier-alert] read pushed failed: {str(e)[:70]}")   # ASCII
        return set()


def _mark(day: str, it: Dict) -> None:
    ensure_table()
    try:
        db.upsert(_TABLE, {
            "date": day, "code": it.get("code"), "tier": it.get("tier"),
            "price": it.get("price"), "chg_pct": it.get("chg_pct"),
            "reason": str(it.get("tier_reason") or "")[:300],
            "pushed_at": datetime.now(_BJ).strftime("%Y-%m-%d %H:%M:%S"),
        }, conflict_columns=["date", "code"])
    except Exception as e:
        print(f"[tier-alert] mark failed {it.get('code')}: {str(e)[:70]}")


def _line(it: Dict) -> str:
    """单只票的一行（企微不支持 markdown 表格，用列表）。"""
    price = it.get("price")
    chg = it.get("chg_pct")
    head = "**%s %s**" % (it.get("name") or it.get("code"), it.get("code"))
    if price is not None:
        s = " %.2f" % float(price)
        if chg is not None:
            s += "（%+.2f%%）" % float(chg)
        head += s
    rs = str(it.get("tier_reason") or "").strip()
    # 上涨质量：只把 bad 的证据写出来（good 的不用占篇幅）
    rise = it.get("rise") or {}
    bads = [s.get("why") for s in (rise.get("signals") or [])
            if s.get("side") == "bad"]
    body = (" ↳ " + rs) if rs else ""
    if bads:
        body += "\n   ↳ " + "；".join(str(b) for b in bads[:3])
    return "- " + head + body


def check_once(day: str = None, force: bool = False) -> Dict:
    """检查持仓分级，**只在有新的 alert 时**推一条企微。

    返回 `{ok, n_alert, pushed[], skipped[]}`；异常不外抛（提醒层不影响主链）。
    """
    d = str(day or _today())[:10]
    try:
        from app.stock_tier_watch import tier_view
        res = tier_view(codes=None, live=True)     # codes 为空 ⇒ 取当前持仓
    except Exception as e:
        print(f"[tier-alert] tier_view failed: {str(e)[:80]}")
        return {"ok": False, "error": str(e)[:80], "pushed": []}

    items = res.get("items") or []
    alerts = [x for x in items if x.get("tier") == "alert"]
    if not alerts:
        return {"ok": True, "n_alert": 0, "pushed": [], "skipped": [],
                "note": "无 alert（watch/safe 不推，避免刷屏）"}

    seen = set() if force else _pushed_today(d)
    fresh = [x for x in alerts if x.get("code") not in seen]
    skipped = [x.get("code") for x in alerts if x.get("code") in seen]
    if not fresh:
        return {"ok": True, "n_alert": len(alerts), "pushed": [], "skipped": skipped,
                "note": "今日已推过（每只票每天最多一次）"}

    if not PUSH_ENABLED:
        return {"ok": True, "n_alert": len(alerts), "pushed": [],
                "skipped": skipped, "note": "推送已关闭（TIER_ALERT_PUSH=0）"}

    lines = ["**%d 只进入警戒**（%s）" % (len(fresh),
                                        res.get("live_as_of") or "盘中"), ""]
    for x in fresh:
        lines.append(_line(x))
    lines.append("")
    lines.append("口径：支撑/阻力=箱体下沿/上沿（截至 %s）；位置用实时价重算。"
                 % (res.get("date") or "—"))
    lines.append("⚠️ 观察器，**不是买卖建议**；阈值未回测校准。")

    pushed: List[str] = []
    try:
        from app.flash import wechat
        title = "持仓分级提醒 %s" % d
        ok = bool(wechat.notify("alert", title, "\n".join(lines)))
        if ok:
            for x in fresh:
                _mark(d, x)
                pushed.append(x.get("code"))
        else:
            print("[tier-alert] wechat notify returned False")
    except Exception as e:
        print(f"[tier-alert] push failed: {str(e)[:80]}")

    return {"ok": True, "n_alert": len(alerts), "pushed": pushed,
            "skipped": skipped}


def summary(day: str = None) -> Dict:
    """当天已推记录（前端/接口查看用）。"""
    ensure_table()
    d = str(day or _today())[:10]
    try:
        rows = db.fetch(
            f"SELECT * FROM {_TABLE} WHERE date=%s ORDER BY pushed_at DESC",
            (d,)) or []
    except Exception as e:
        print(f"[tier-alert] summary failed: {str(e)[:70]}")
        return {"date": d, "rows": []}
    return {"date": d, "rows": [dict(r) for r in rows]}
