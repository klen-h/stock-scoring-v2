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

from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Optional
from app.auth import get_current_user, get_current_user_optional
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


@router.get("/liquidity")
def macro_liquidity(days: int = Query(30, ge=5, le=120,
                                      description="历史序列天数（趋势用）")):
    """流动性三件套（两融 / 期指基差 / 中美利差）—— 日批落库的历史序列 + 最新一期。

    匿名理由（AUTH_POLICY §2）：GET 只读、读 liquidity_daily 小表（≤120 行）、
    无外部请求（**不在请求里现拉**——两融/基差的实时链路另有 margin_sentiment /
    /macro/panel，本接口只读落库结果，保证口径可审计）。
    ★ **纯观察器，不进决策链**；两融为 T+1 数据（margin_date 列自证）；
    基差=现货−期货收盘（正=贴水，当月合约）。口径全文见 `app/liquidity_watch.py`。
    """
    from app import liquidity_watch
    return liquidity_watch.load(days)


@router.get("/earnings-pressure")
def earnings_pressure(limit: int = Query(30, ge=5, le=200), refresh: bool = False,
                      _user: Optional[dict] = Depends(get_current_user_optional)):
    # ★ 2026-10-24（BK-40）：refresh=true 绕过 6h 缓存触发东财/财报重算 ⇒ 必须登录
    if refresh and not _user:
        raise HTTPException(401, "refresh=true 触发重算需要登录（GUARDRAILS #12）")
    """盈利承压监测清单（P2-2，滞胀 → 盈利端）。

    ★ **这是监测清单，不是卖出信号**：不改评分、不参与排序、不进闸门。
      判据 = 毛利率**同比下滑**（必备）+ 总分 ≥60（低毛利/净利营收同比为负加权），
      只报"**正在边际恶化**"的票；宏观用**分位口径**定级（油/10Y）。
    ★ 数据：`stock_finance`（多报告期，可做同期同比）+ `stock_industry`（行业中位）
      + `macro_daily_history`（油/10Y 分位）。6h 进程内缓存。
    ★ 不做方向断言：高油高利率同时是上游资源/油服的**受益**环境 —— 清单附行业分布，
      由读者自行判断（`note` 字段里也写明了这一点）。
    """
    from app.earnings_pressure import scan
    return scan(limit=limit, refresh=refresh)


@router.get("/reserves")
def macro_reserves(months: int = Query(24, ge=6, le=120), _user: dict = Depends(get_current_user)):
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
