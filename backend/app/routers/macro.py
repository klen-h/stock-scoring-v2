"""
================================================================================
【文件作用】宏观数据路由
================================================================================

URL 前缀 /api/macro，数据源：新浪财经（见 app/macro.py）。

接口列表：
  GET /api/macro/snapshot → 宏观快照（全球+国内面板 + 规则标签 + 今日方向分 + 市场温度）

用途：这份 JSON 是自包含的「LLM-ready」输入——直接喂给大模型做今日方向分析，
      或供人工/外部规则系统消费。方向分不进个股评分，只作环境参考。
================================================================================
"""

from fastapi import APIRouter, Query
from app.macro import get_macro_snapshot, get_macro_panel, RULES, RULES_VERSION

router = APIRouter()


@router.get("/snapshot")
def macro_snapshot():
    """
    宏观快照：
      panel(全球+国内面板) + derived(衍生比率/基差) + rules_triggered(触发的规则)
      + tags(多空标签) + direction(score/level/advisory/分组得分) + 市场温度。
    """
    return get_macro_snapshot()


@router.get("/daily")
def macro_daily(date: str = None, days: int = Query(1, ge=1, le=60)):
    """宏观每日快照（早盘锁定，按日期归档）：date=某日单条；days=近 N 日列表（正序）。"""
    from app.flash.store import load_macro_daily, load_macro_daily_history
    if date:
        snap = load_macro_daily(date)
        return {"date": date, "snapshot": snap or None}
    return {"items": load_macro_daily_history(days)}


@router.get("/panel")
def macro_panel():
    """仅宏观面板（不含规则评估），调试/其他用途。"""
    return get_macro_panel()


@router.get("/rules")
def macro_rules():
    """规则配置表（当前生效的规则集），便于审计与前端展示阈值。"""
    return {"version": RULES_VERSION, "rules": RULES}


@router.get("/reserves")
def macro_reserves(months: int = Query(24, ge=6, le=120)):
    """央行储备月度面板（外储 / 黄金储备 / 黄金占储备比重）——**慢变量，非交易信号**。

    定位见 `PLAN_RESERVE_SIGNALS.md` §6.2：把“减美债、增黄金”的月度事实落成可追溯数据，
    供面板与长期研究引用；**不进决策链、不产生买卖信号**。
    """
    from app import reserve_monthly
    items = reserve_monthly.series_with_share(months)
    return {
        "disclaimer": "结构背景（月频慢变量），非交易信号，不进决策链",
        "unit_note": "黄金储备单位=万盎司（源口径），吨为换算展示值；外储单位=亿美元",
        "share_note": "黄金占比用 COMEX 月末收盘近似估算，仅看趋势方向",
        "latest": items[-1] if items else None,
        "items": items,
    }
