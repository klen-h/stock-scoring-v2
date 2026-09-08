# -*- coding: utf-8 -*-
"""
系统状态路由：数据新鲜度仪表盘（GET /api/system/status）

把"每类数据最后更新到什么时候"聚成一个接口——盘后任务链 17 个环节，
哪个环节断了这里一眼可见（2026-09-06 深市两融滞后、东财封禁等问题的运维出口）。

新鲜度判定（交易日感知）：
  ok      数据日期 = 最近交易日（盘后任务当日出即 ok）
  stale   落后最近交易日 1-2 个交易日（可能数据源滞后，如深市两融）
  missing 无数据 或 落后 ≥3 个交易日（任务链断了，需排查）
"""

from datetime import datetime, timedelta
from typing import Dict, Optional

from fastapi import APIRouter, Depends

from app.auth import get_current_user
from app.database import db

router = APIRouter()


def _latest_trading_day() -> str:
    """最近交易日（收盘数据 15:30 后才算当日）。"""
    from app.flash.rules import beijing_now
    now = beijing_now()
    d = now.date()
    if now.hour < 15 or (now.hour == 15 and now.minute < 30):
        d -= timedelta(days=1)
    hol = []
    try:
        from app.flash import rules as _rules
        hol = _rules.HOLIDAYS.get(d.year) or []
    except Exception:
        pass
    while d.weekday() >= 5 or any(s <= (d.month, d.day) <= e for s, e, *_ in hol):
        d -= timedelta(days=1)
    return d.isoformat()


def _trading_days_ago(n: int) -> str:
    d = datetime.strptime(_latest_trading_day(), "%Y-%m-%d").date()
    checked = 0
    while checked < n:
        d -= timedelta(days=1)
        hol = []
        try:
            from app.flash import rules as _rules
            hol = _rules.HOLIDAYS.get(d.year) or []
        except Exception:
            pass
        if d.weekday() < 5 and not any(s <= (d.month, d.day) <= e for s, e, *_ in hol):
            checked += 1
    return d.isoformat()


_SOURCES = [
    # (名称, 查询SQL取最新日期, 预期节奏说明, 容忍滞后交易日数(默认2))
    ("行情收盘快照", "SELECT MAX(saved_at) AS v FROM market_snapshot", "每日 15:05", 2),
    ("回测价格库", "SELECT MAX(date) AS v FROM backtest_prices", "每日 15:40", 2),
    ("主力资金流", "SELECT MAX(date) AS v FROM mainflow_history", "每日 17:00", 2),
    ("主力行为状态", "SELECT MAX(date) AS v FROM mainforce_state", "日批 19:00 后", 2),
    ("龙虎榜", "SELECT MAX(date) AS v FROM lhb_history", "每日 17:45", 2),
    ("评分快照", "SELECT MAX(rank_date) AS v FROM ranking_history", "每日 18:00", 2),
    ("消息分快照", "SELECT MAX(snap_date) AS v FROM news_history", "每日 19:20", 2),
    ("矛盾扫描", "SELECT MAX(date) AS v FROM contradictions", "每日 15:35", 2),
    ("每日日报", "SELECT MAX(date) AS v FROM daily_reports", "每日 19:30", 2),
    # 周任务：季度数据周更保活，容忍窗口放宽到 8 个交易日（约两周）
    ("财报扩展(zzshare)", "SELECT MAX(updated_at) AS v FROM stock_finance_zz", "每周一同步", 8),
    ("市场状态判定", "SELECT MAX(date) AS v FROM market_regime_history", "每日 15:40", 2),
]


def _to_date(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v)
    return s[:10] if len(s) >= 10 else s


@router.get("/status")
def system_status(user: dict = Depends(get_current_user)) -> Dict:
    """数据新鲜度仪表盘：每类数据最后日期 + ok/stale/missing 判定 + 调度器最近运行。"""
    latest = _latest_trading_day()
    t1 = _trading_days_ago(1)
    t2 = _trading_days_ago(2)

    sources = []
    ok_count = 0
    for name, sql, cadence, *rest in _SOURCES:
        max_lag = rest[0] if rest else 2   # 容忍滞后交易日数（周任务放宽）
        last = None
        try:
            row = db.fetch_one(sql)
            last = _to_date((row or {}).get("v"))
        except Exception as e:
            print(f"[system_status] {name} 查询失败: {e}")
        if not last:
            status, lag = "missing", None
        elif last >= latest:
            status, lag = "ok", 0
        elif max_lag > 2:
            # 低频任务（周同步等）：容忍窗口内一律 ok，不按日频误报 stale
            if last >= _trading_days_ago(max_lag):
                status, lag = "ok", max_lag
            else:
                status = "stale"
                try:
                    lag = (datetime.strptime(latest, "%Y-%m-%d")
                           - datetime.strptime(last, "%Y-%m-%d")).days
                except ValueError:
                    lag = None
        elif last >= t1:
            status, lag = "ok", 1        # 深市两融这类 T+1 滞后属正常
        elif last >= t2:
            status, lag = "stale", 2
        else:
            try:
                lag = (datetime.strptime(latest, "%Y-%m-%d")
                       - datetime.strptime(last, "%Y-%m-%d")).days
            except ValueError:
                lag = None
            status = "stale"
        ok_count += 1 if status == "ok" else 0
        sources.append({"name": name, "last": last, "status": status,
                        "lag_days": lag, "cadence": cadence})

    # 调度器最近运行时间戳（进程内存，未重启才有）
    sched = {}
    try:
        from app.flash.scheduler import status as sched_status
        raw = dict(sched_status) if sched_status else {}
        sched = {k: v for k, v in raw.items() if k.startswith("last_")}
    except Exception:
        pass

    return {
        "latest_trading_day": latest,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": f"{ok_count}/{len(sources)} 新鲜",
        "sources": sources,
        "scheduler": sched,
    }
