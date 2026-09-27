# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】统一出场建议（「什么时候卖」的**单一事实源**）
================================================================================
为什么需要（2026-09-27 用户旅程盘点发现）：
  项目此前有**三套互不相通**的"什么时候卖"口径：
    ① `strategies/exit_alert.py`      止损 / 支撑 / RSI / 放量   → 只在评分榜 Top tab
                                                                  与战法观察池出现（**选股页**）
    ② `portfolio_radar.py`            浮亏 / 浮盈 / 主力 / 闸门   → 工作台「持仓预案」
    ③ 前端 `usePortfolio.evaluateAlerts`  −8% / +30% / 移动止盈 / 评分 → 「我的持仓」页
  ⇒ 同一只持仓，在「我的持仓」说"减仓"、在工作台说"持有"，用户无法判断信谁。

本模块的定位（**合并，不是新增第四套判据**）：
  · 判据**全部来自既有模块**，本模块只做「**优先级仲裁 + 统一成单一 action**」；
  · 组合/状态面复用 `portfolio_radar.build()`（它已声明"不引入新判据"）；
  · 技术面复用 `exit_alert.check_single_exit`（止损/支撑/RSI/放量）；
  · 移动止盈**补齐到后端**（此前只在前端）：用 `user_portfolio.created_at` 近似买入日，
    取 kline 中该日之后的最高收盘 → 真实「阶段高点回撤」（不再是前端的高水位记忆）。

优先级（**从高到低，命中即定档**）：
  1. 闸门禁止持仓（regime=defensive 或主力出货 ⇒ `position_pct == 0`）→ 清仓
  2. 硬止损（浮亏 ≤ −8%，与 `coach.rules.REAL_STOP_LOSS_PCT` 同源）→ 清仓
  3. 技术面 urgent（跌破止损价 / 跌破强支撑）→ 清仓
  4. 移动止盈（阶段高点回撤 ≥ 8% 且浮盈 > 0）→ 减仓⅓
  5. 技术面 warning（RSI 超买回落 / 放量下跌）→ 减仓½
  6. 硬止盈（浮盈 ≥ +30%）→ 减仓½
  7. 主力出货 → 减仓½
  8. 评分恶化（≤ 35）→ 关注减仓
  9. 否则 → 持有

性能（实测约束，与 `portfolio_radar` 同款）：
  · `exit_alert` 每只持仓发一次 kline 请求 ⇒ N 次网络。
    故：**进程内缓存 TTL 10 分钟**（RSI/支撑/均量都是日频量，无需秒级）+ 线程锁。
  · 技术面失败（无网络/数据不足）**不影响**组合面结论 ⇒ 安全降级。

★ 刻意**不做后台预热**（与 `portfolio_radar_warm_loop` 的内存纪律一致）：
  该 loop 的注释明确「线上 Render free 计划 512MB，**内存比首次请求的几十秒更贵**」。
  本模块的额外开销 = 每只持仓一次 kline（判定结果进 `_TA_CACHE`，**不驻留 kline 本体**），
  且 `portfolio_radar` 侧已被预热 ⇒ 首调只慢在 kline。
  ⇒ 用「**前端异步 + 静默降级**」承接首调（失败即回退本地口径），而不是加常驻预热。

开关：`EXIT_ADVICE=off` 可停用（返回降级结构）。
================================================================================
"""
from __future__ import annotations

import threading
import time
from typing import Dict, List, Optional

# ── 统一档位（唯一输出口径）──
ACTIONS = ("清仓", "减仓½", "减仓⅓", "关注减仓", "持有", "可加仓")
_LEVEL = {
    "清仓": "danger",
    "减仓½": "warning",
    "减仓⅓": "warning",
    "关注减仓": "warning",
    "持有": "info",
    "可加仓": "success",
}

# ── 阈值（**与既有单一事实源对齐**，改这里必须同步改对应模块）──
HARD_STOP_PCT = -8.0        # 与 coach.rules.REAL_STOP_LOSS_PCT 一致（同一 −8% 口径）
HARD_TAKE_PCT = 30.0        # 与前端 usePortfolio.ALERT_CONFIG.takeProfitPct 一致
TRAILING_PCT = 8.0          # 与前端 usePortfolio.ALERT_CONFIG.trailingDrawdownPct 一致
SCORE_SELL = 35.0           # 与前端 usePortfolio.ALERT_CONFIG.scoreSellThreshold 一致

# ── 缓存（技术面结果按 code 缓存；日频量 ⇒ 10 分钟）──
_TA_CACHE: Dict[str, Dict] = {}
_TA_TTL = 600.0
_ta_lock = threading.Lock()


def _enabled() -> bool:
    import os
    return (os.environ.get("EXIT_ADVICE") or "on").strip().lower() != "off"


def _tech_alert(code: str, item: Dict) -> Optional[Dict]:
    """单只技术面退出信号（缓存 + 失败静默）。返回 None = 不可用（降级）。

    用 `exit_alert.check_single_exit`；`entry_price` 用成本价，
    `stop_loss` 由成本价 ×(1+HARD_STOP_PCT) 生成（user_portfolio 无止损字段）。
    """
    key = f"{code}|{item.get('cost') or 0}"
    now = time.time()
    hit = _TA_CACHE.get(key)
    if hit and now - hit["ts"] < _TA_TTL:
        return hit["val"]
    val: Optional[Dict] = None
    try:
        from app.strategies.exit_alert import check_single_exit
        cost = float(item.get("cost") or 0)
        val = check_single_exit(code, {
            "code": code, "name": item.get("name") or code,
            "entry_price": cost,
            "stop_loss": cost * (1 + HARD_STOP_PCT / 100.0) if cost > 0 else 0,
        })
    except Exception as e:
        print(f"[exit_advice] 技术面检测失败（降级为组合面）: {code} {e}")
        val = None
    with _ta_lock:
        _TA_CACHE[key] = {"ts": now, "val": val}
    return val


def _trailing_from_kline(code: str, item: Dict) -> Optional[float]:
    """阶段高点回撤（%）：用 kline 中 `created_at` 之后的最高收盘 vs 现价。

    为什么放后端：前端用 localStorage 的 `high_water_mark` 记「持仓期最高价」，
    换设备/清缓存即丢失；且「我的持仓」与工作台**两处各记一份** ⇒ 口径不一。
    这里用 `user_portfolio.created_at`（录入日，近似买入日 —— 与 `coach.rules._real_holdings`
    同一近似口径）从日线取真实最高收盘。
    返回 None = 数据不足（不参与判定）。
    """
    try:
        from app.tencent import get_kline
        price = float(item.get("price") or 0)
        if price <= 0:
            return None
        start = str(item.get("created_at") or "")[:10]
        kl = get_kline(code, period="day", count=250)
        if not kl:
            return None
        closes = []
        for k in kl:
            d = str(k.get("date") or k.get("time") or "")[:10]
            c = k.get("close")
            if not c:
                continue
            if start and d and d < start:
                continue
            closes.append(float(c))
        if not closes:
            return None
        high = max(closes)
        if high <= 0:
            return None
        return round((price - high) / high * 100, 2)
    except Exception:
        return None


def advise_for_item(item: Dict) -> Dict:
    """单只持仓 → 统一 action（纯仲裁，不做新判据）。

    返回 {code, name, action, level, reasons[], sources[], ...}
    """
    reasons: List[str] = []
    sources: List[str] = []
    pnl = item.get("pnl_pct")
    action = "持有"

    ta = _tech_alert(str(item.get("code") or ""), item)
    ta_level = (ta or {}).get("level")
    ta_reasons = list((ta or {}).get("reasons") or [])

    # 1) 闸门禁止持仓（regime=defensive / 主力出货 ⇒ position_pct=0）
    if item.get("position_pct") == 0:
        action = "清仓"
        why = item.get("position_label") or "闸门判定禁止持仓"
        reasons.append(f"闸门：{why}")
        sources.append("trade_gate")
    # 2) 硬止损
    elif pnl is not None and pnl <= HARD_STOP_PCT:
        action = "清仓"
        reasons.append(f"浮亏 {pnl:.1f}%，触及 {HARD_STOP_PCT:g}% 止损线")
        sources.append("hard_stop")
    # 3) 技术面 urgent（破止损价 / 破强支撑）
    elif ta_level == "urgent":
        action = "清仓"
        reasons.extend(ta_reasons or ["技术面紧急撤退信号"])
        sources.append("exit_alert")
    # 4) 移动止盈（阶段高点回撤）
    else:
        dd = _trailing_from_kline(str(item.get("code") or ""), item)
        if dd is not None and dd <= -TRAILING_PCT and (pnl or 0) > 0:
            action = "减仓⅓"
            reasons.append(f"自阶段高点回撤 {abs(dd):.1f}%（≥{TRAILING_PCT:g}%），保住利润")
            sources.append("trailing")
        # 5) 技术面 warning（RSI 回落 / 放量下跌）
        elif ta_level == "warning":
            action = "减仓½"
            reasons.extend(ta_reasons or ["技术面警告信号"])
            sources.append("exit_alert")
        # 6) 硬止盈
        elif pnl is not None and pnl >= HARD_TAKE_PCT:
            action = "减仓½"
            reasons.append(f"浮盈 {pnl:.1f}%，达 {HARD_TAKE_PCT:g}% 止盈目标，可分批锁利")
            sources.append("hard_take")
        # 7) 主力出货
        elif item.get("phase") == "distribution":
            action = "减仓½"
            reasons.append("主力阶段为『出货』")
            sources.append("mainforce")
        # 8) 评分恶化
        elif item.get("score") is not None and item["score"] <= SCORE_SELL:
            action = "关注减仓"
            reasons.append(f"评分 {item['score']:.0f}（≤{SCORE_SELL:g}），多因子转弱")
            sources.append("score")
        else:
            if item.get("ready") is not None and item["ready"] >= 3 and (pnl or 0) > 0:
                action = "可加仓"
                reasons.append("闸门三条件就绪且已有浮盈，可顺势加仓")
                sources.append("trade_gate")
            else:
                reasons.append("未触发任何出场条件")
                sources.append("default")

    return {
        "code": item.get("code"), "name": item.get("name"),
        "price": item.get("price"), "cost": item.get("cost"),
        "pnl_pct": pnl, "day_pct": item.get("day_pct"),
        "score": item.get("score"), "phase_cn": item.get("phase_cn") or "",
        "ready": item.get("ready"), "position_pct": item.get("position_pct"),
        "action": action, "level": _LEVEL.get(action, "info"),
        "reasons": reasons, "sources": sources,
        # 原始明细（透明可追溯，前端可折叠展示）
        "tech": ({"level": ta_level, "reasons": ta_reasons} if ta else None),
        "radar_alerts": item.get("alerts") or [],
    }


def _warming() -> Dict:
    return {"as_of": None, "warming": True,
            "summary": {"n": 0, "clear": 0, "reduce": 0, "hold": 0},
            "items": [], "note": "正在准备出场建议（首次约需半分钟，之后秒开）…"}


def build() -> Dict:
    """统一出场建议（**复用 portfolio_radar，不重算组合面**）。

    返回 {as_of, regime, summary, items, source}。
    """
    if not _enabled():
        return {"as_of": None, "summary": {}, "items": [],
                "note": "EXIT_ADVICE=off（已停用）", "source": "disabled"}
    try:
        from app import portfolio_radar
        radar = portfolio_radar.build()
    except Exception as e:
        print(f"[exit_advice] portfolio_radar 调用失败: {e}")
        return {"as_of": None, "summary": {}, "items": [],
                "note": f"组合数据不可用：{e}", "source": "error"}
    if radar.get("warming"):
        return _warming()

    items = []
    for it in radar.get("items") or []:
        try:
            items.append(advise_for_item(it))
        except Exception as e:
            print(f"[exit_advice] {it.get('code')} 仲裁失败: {e}")
            items.append({
                "code": it.get("code"), "name": it.get("name"),
                "price": it.get("price"), "pnl_pct": it.get("pnl_pct"),
                "action": "持有", "level": "info",
                "reasons": ["出场判定失败（已降级）"], "sources": ["error"],
            })

    order = {"清仓": 0, "减仓½": 1, "减仓⅓": 1, "关注减仓": 2, "可加仓": 3, "持有": 4}
    items.sort(key=lambda x: (order.get(x["action"], 9),
                              x.get("pnl_pct") if x.get("pnl_pct") is not None else 0))

    return {
        "as_of": radar.get("as_of"),
        "regime": radar.get("regime"),
        "summary": {
            "n": len(items),
            "clear": sum(1 for x in items if x["action"] == "清仓"),
            "reduce": sum(1 for x in items if x["action"].startswith("减仓")),
            "watch": sum(1 for x in items if x["action"] == "关注减仓"),
            "hold": sum(1 for x in items if x["action"] in ("持有", "可加仓")),
        },
        "thresholds": {"hard_stop": HARD_STOP_PCT, "hard_take": HARD_TAKE_PCT,
                       "trailing": TRAILING_PCT, "score_sell": SCORE_SELL},
        "source": "portfolio_radar + exit_alert（统一仲裁）",
        "items": items,
    }
