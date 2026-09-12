# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】宽度崩塌 → 次日风控钩子（PLAN_2026-09-12 §3b#6）
================================================================================

背景：index_vs_breadth 扫描器只抓"指数红盘 + 个股失血"（护盘假象），指数同步
下跌的普跌日是它的盲区——2026-09-11 涨跌比 0.13（涨 354 / 跌 2668）未触发的实证。
scan_breadth_collapse 补上普跌日；本模块把 severe 宽度崩塌接到**仓位动作**：

  触发条件：最近一个已落库扫描日（date < 今天）存在 severe 的
            breadth_collapse 或 index_vs_breadth（宽度类 severe 统一口径）
  生效范围（次日全天）：
    1) 模拟盘/推送新仓单笔风险额 ×0.5（paper_trading._calc_shares 挂钩）
    2) 趋势类战法暂停企微推送与模拟盘入池（scheduler + auto_ingest_signals 挂钩；
       信号照常落库，不影响回测样本积累）

开关：CONTRADICTION_RISK_HOOK（默认 off，"带开关、可回滚"纪律——未经验证的新
风控逻辑不默认生效）。设 1 启用。

fail-open 原则：表不可用 / 无记录 / 开关关闭 / 查询异常 → active=False，
绝不因钩子自身故障挡住交易。

时效：severe 记录距今 ≤7 个自然日才生效（长假后不继承节前的崩塌状态）。

调用上下文：全部为同步函数，挂点均在线程池（scan_all_strategies /
fill_pending_positions 由 asyncio.to_thread 调用），同步轻量 DB 查询不阻塞事件循环；
进程内按自然日缓存（每天至多查一次库，多战法/多笔共享）。
================================================================================
"""

import os
from datetime import datetime, timedelta
from typing import Dict, Optional

# 趋势类战法（全景文档 §六：offensive 市准入的 5 条；强趋势战法在宽度崩塌市
# 无正向期望——09-04 起龙回头/进二退一信号连续归零即实证）
TREND_STRATEGIES = frozenset({
    "advance2retreat1",      # 进二退一
    "dragon_turnaround",     # 龙回头
    "limit_up_boomerang",    # 涨停回马枪
    "wizard_pointer",        # 仙人指路
    "double_cannon",         # 涨停双响炮
})

# 触发钩子的宽度类矛盾类型（severe 统一口径）
_BREADTH_TYPES = ("breadth_collapse", "index_vs_breadth")
_MAX_AGE_DAYS = 7

# 进程内缓存：key = 查询基准日（自然日），value = 查询结果 dict
_CACHE = {"day": None, "result": None}


def _hook_enabled() -> bool:
    return os.environ.get("CONTRADICTION_RISK_HOOK", "0").strip() == "1"


def breadth_collapse_active(date: Optional[str] = None) -> Dict:
    """查询"最近一个已落库扫描日是否存在 severe 宽度崩塌"。

    返回 {"active": bool, "date"/"type"/"severity"/"title": ...}。
    active=True 时钩子生效（当日全天）。
    """
    try:
        from app.flash.rules import beijing_now
        key = date or beijing_now().strftime("%Y-%m-%d")
    except Exception:
        key = date or datetime.now().strftime("%Y-%m-%d")

    if _CACHE["day"] == key and _CACHE["result"] is not None:
        return _CACHE["result"]

    result = {"active": False}
    if _hook_enabled():
        try:
            from app.database import db
            cutoff = (datetime.strptime(key, "%Y-%m-%d")
                      - timedelta(days=_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
            row = db.fetch_one(
                "SELECT date, type, severity, title FROM contradictions "
                "WHERE type IN (%s, %s) AND severity = 'severe' "
                "AND date >= %s AND date < %s "
                "ORDER BY date DESC LIMIT 1",
                (_BREADTH_TYPES[0], _BREADTH_TYPES[1], cutoff, key))
            if row:
                result = {"active": True, "date": row["date"],
                          "type": row["type"], "severity": row["severity"],
                          "title": row["title"]}
        except Exception as e:
            print(f"[risk_hook] 宽度崩塌查询失败（fail-open 放行）: {e}")

    _CACHE["day"] = key
    _CACHE["result"] = result
    return result


def risk_multiplier(date: Optional[str] = None) -> float:
    """新仓单笔风险额乘数：severe 宽度崩塌次日 0.5，否则 1.0。"""
    return 0.5 if breadth_collapse_active(date).get("active") else 1.0


def trend_paused(date: Optional[str] = None) -> bool:
    """趋势类战法当日是否暂停推送/入池。"""
    return bool(breadth_collapse_active(date).get("active"))


def reset_cache() -> None:
    """清空进程缓存（测试用）。"""
    _CACHE["day"] = None
    _CACHE["result"] = None
