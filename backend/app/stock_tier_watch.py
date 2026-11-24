# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】持仓 / 指定个股的**分级观察**（含「上涨质量」证据）
================================================================================
背景（2026-11-25，用户："对我的持仓或者指定个股进行实时分级的观察，因为我每天
 都要盯好几个股票，顾不过来，也不知道支撑位和阻力位，上涨是正常的还是套人的"）

三个痛点 → 三个已有/新增能力的对应关系：

| 痛点 | 谁来解决 | 状态 |
|---|---|---|
| 顾不过来 | 本模块 `tier_view()` 一屏给完 | **新增（聚合）** |
| 不知道支撑阻力 | `range_watch` 箱体上下沿 + `state` | 已有，本模块改用**实时价**重算位置 |
| 上涨是正常还是套人 | 本模块 `score_rise()` | **新增（真缺口）** |

★★ 为什么「上涨质量」是真正缺的那块：
  `washout_alert` 解决的是**下沿问题**——"跌到箱体下沿，是洗盘还是破位？"
  （它自己的 docstring：`给'到了区间下沿，怎么知道是洗还是破'`）。
  而用户问的是它的**镜像**——"涨到上沿/上涨途中，是正常上涨还是诱多套人？"
  两个问题结构对称（量能/资金/位置/形态），但方向相反，`washout_alert` 不覆盖。

★ 红线（与本项目其它观察器同级）：
  **不改评分、不进闸门、不碰模拟盘、不生成买卖指令。**
  只输出「事实 + 证据条数」，要不要动手由人判断。

--------------------------------------------------------------------------------
一、为什么用证据列表而不是加权总分（改前必读）
--------------------------------------------------------------------------------
  `washout_alert` 用的是「证据条数求和」（score = Σ delta，-4 ~ +4），
  它的注释已经承认："分数只是证据条数的汇总，不是概率"。
  本模块**沿用同一套结构**（便于前端复用、便于对照读），但更保守：
    · 不做"总分→结论"的映射，只给 `n_bad / n_good` 与逐条 `side`
    · 因为「涨得正常 vs 诱多」的权重**没有任何回测背书**，
      加权会把"拍的权重"包装成"精确分数"，比不排序更危险。

二、四条证据的口径与阈值（全部集中在本文件顶部）
--------------------------------------------------------------------------------
  ① **量能** `vol_ratio` = 近5日均量 / 60日均量（`range_watch` 算好的，零额外成本）
     缩量上涨 < 0.75 ⇒ bad（没有量在推，接力弱）
     温和放量 0.75~1.5 ⇒ good
     异常放量 > 2.5 且已在箱体上沿 ⇒ bad（高位巨量常是换手/出货）
  ② **主力资金** `flow5`（`stock_facts_wide`，5 日主力净流入，单位亿）
     涨而净流出 < -0.5 ⇒ bad（价升量减/对倒）
     净流入 > +0.5 ⇒ good
  ③ **位置** `pos` = 箱体内位置（0=下沿, 1=上沿）
     ≥ 0.85 且未突破 ⇒ bad（上方空间 < 15%）
     `state=突破` 且放量 ⇒ good
  ④ **日内形态** 上影线占比 = (high − price) / (high − low)
     > 0.5 且当日涨 ⇒ bad（冲高回落，有人在高位卖）
     ⚠️ 只用实时行情的 high/low；一字板（high==low）不判（避免除零与误判）

三、分级 `tier`（三档，只表"该不该多看一眼"，不是买卖建议）
--------------------------------------------------------------------------------
  alert  破位 / 跌破箱体下沿 / 浮亏 ≤ -8% / 上涨质量明确可疑（bad≥2 且 good=0）
  watch  在上沿附近 / 有 1 条 bad / 浮亏 -5%~-8%
  safe   其余
  ⚠️ 阈值全是**经验初值**（与项目其它观察器一致），要校准得靠
     "alert 组的后续 5 日收益是否显著差于 safe 组"——本模块**未做**该检验。

四、数据来源与性能
--------------------------------------------------------------------------------
  · 箱体：`range_watch.for_codes()`（日批落库，一次 IN 查询）
  · 资金：`washout_alert.load_stats()`（一次 IN 查询）
  · 实时价：`tencent.get_stock()`（有 60 秒缓存，逐只；10 只以内可接受）
  ⇒ **零额外外部请求**（除实时价，且那本来是主链路）。
  ⚠️ 箱体是**日批口径（截至上一交易日收盘）**，实时价是**当下** ⇒
     两者混用时 `pos` 会随盘中价格变化，这是**期望行为**（位置本来就该实时），
     但必须把两个日期都返回给前端，否则会出现"用今天的价配昨天的箱体还说不上"。
================================================================================
"""
import os
from datetime import datetime
from typing import Dict, List, Optional

from app.database import db

# ── 阈值（集中，便于回测校准；改动必须同步 docstring）──
VOL_SHRINK = float(os.environ.get("TIER_VOL_SHRINK") or 0.75)
VOL_EXPAND = float(os.environ.get("TIER_VOL_EXPAND") or 2.5)
FLOW_GOOD = float(os.environ.get("TIER_FLOW_GOOD") or 0.5)
FLOW_BAD = float(os.environ.get("TIER_FLOW_BAD") or -0.5)
UPPER_EDGE = float(os.environ.get("TIER_UPPER_EDGE") or 0.85)
LOWER_EDGE = float(os.environ.get("TIER_LOWER_EDGE") or 0.15)
SHADOW_BAD = 0.5           # 上影线占比 > 此值 ⇒ 冲高回落
LOSS_ALERT = -8.0          # 浮亏 ≤ 此值 ⇒ alert
LOSS_WATCH = -5.0          # 浮亏 ≤ 此值 ⇒ watch
MAX_CODES = 50             # 单次上限（防止一次拉 3000 只把接口拖死）

TIER_CN = {"alert": "警戒", "watch": "留意", "safe": "正常", "unknown": "无数据"}


def _f(v) -> Optional[float]:
    """安全转 float；转不动返回 None（**不返回 0**）。"""
    if v is None or v == "" or v == "-":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _live(code: str) -> Dict:
    """实时行情（腾讯缓存；失败返回 {}，调用方降级为日批收盘价）。"""
    try:
        from app.tencent import get_stock
        q = get_stock(code) or {}
        return {
            "price": _f(q.get("price")),
            "chg_pct": _f(q.get("change_pct")),
            "high": _f(q.get("high")),
            "low": _f(q.get("low")),
            "open": _f(q.get("open")),
            "name": q.get("name") or "",
            "time": str(q.get("time") or ""),
        }
    except Exception as e:
        print(f"[tier] live quote failed {code}: {str(e)[:60]}")   # ASCII
        return {}


def score_rise(rng: Dict, st: Dict, live: Dict) -> Dict:
    """**上涨质量**：涨得正常，还是像诱多。

    返回 `{n_bad, n_good, signals[], verdict, caveat}`；
    `signals` 每项 `{key, label, value, side(good|bad|na), why}`。

    ⚠️ 无回测背书：四条证据的方向有交易常识支撑，但**权重与阈值都没校准**。
    """
    chg = _f(live.get("chg_pct"))
    price = _f(live.get("price"))
    pos = _f(rng.get("pos"))
    state = str(rng.get("state") or "")
    volr = _f(rng.get("vol_ratio"))
    flow5 = _f(st.get("flow5"))
    hi, lo = _f(live.get("high")), _f(live.get("low"))

    sigs: List[Dict] = []

    # ① 量能
    if volr is not None:
        if volr < VOL_SHRINK:
            sigs.append({"key": "vol", "label": "量能", "value": volr, "side": "bad",
                         "why": f"5日均量/60日均量 {volr:.2f} < {VOL_SHRINK}"
                                f"，涨得没有量在推"})
        elif volr > VOL_EXPAND and pos is not None and pos >= UPPER_EDGE:
            sigs.append({"key": "vol", "label": "量能", "value": volr, "side": "bad",
                         "why": f"量比 {volr:.2f} > {VOL_EXPAND} 且已在上沿"
                                f"，高位巨量常是换手/出货"})
        else:
            sigs.append({"key": "vol", "label": "量能", "value": volr, "side": "good",
                         "why": f"量比 {volr:.2f}，量能配合"})

    # ② 主力资金
    if flow5 is not None:
        if flow5 < FLOW_BAD:
            sigs.append({"key": "flow", "label": "主力5日", "value": flow5, "side": "bad",
                         "why": f"5日主力净流出 {flow5:.2f} 亿 < {FLOW_BAD}"
                                f"，价格在涨但资金在走"})
        elif flow5 > FLOW_GOOD:
            sigs.append({"key": "flow", "label": "主力5日", "value": flow5, "side": "good",
                         "why": f"5日主力净流入 {flow5:.2f} 亿"})

    # ③ 位置
    #   ⚠️ 2026-11-25 修正：初版把 `pos < UPPER_EDGE` 一律判 good ——
    #      实测破位票 `pos=-0.05`（已跌穿下沿）被标成"仍在区间内"，
    #      与 `state=破位` 自相矛盾。破位/下沿**绝不能算 good**。
    if pos is not None:
        if state == "突破":
            sigs.append({"key": "pos", "label": "位置", "value": pos, "side": "good",
                         "why": "已突破箱体上沿"})
        elif pos <= 0:
            sigs.append({"key": "pos", "label": "位置", "value": pos, "side": "bad",
                         "why": f"箱体位置 {pos:.2f} ≤ 0，已跌穿下沿"})
        elif pos >= UPPER_EDGE:
            sigs.append({"key": "pos", "label": "位置", "value": pos, "side": "bad",
                         "why": f"箱体位置 {pos:.2f} ≥ {UPPER_EDGE}，上方空间 "
                                f"{(1 - pos) * 100:.0f}% 左右"})
        else:
            sigs.append({"key": "pos", "label": "位置", "value": pos, "side": "good",
                         "why": f"箱体位置 {pos:.2f}，仍在区间内"})

    # ④ 日内上影线（只在"上涨"且 high/low 有效时判）
    if (hi and lo and price and hi > lo
            and chg is not None and chg > 0):
        shadow = round((hi - price) / (hi - lo), 3)
        if shadow >= SHADOW_BAD:
            sigs.append({"key": "shadow", "label": "上影线", "value": shadow,
                         "side": "bad",
                         "why": f"上影占比 {shadow:.0%} ≥ {SHADOW_BAD:.0%}"
                                f"，冲高回落（高位有人卖）"})
        else:
            sigs.append({"key": "shadow", "label": "上影线", "value": shadow,
                         "side": "good", "why": f"上影占比 {shadow:.0%}"})

    n_bad = sum(1 for s in sigs if s["side"] == "bad")
    n_good = sum(1 for s in sigs if s["side"] == "good")

    if not sigs:
        verdict = "无数据"
    elif n_bad >= 2 and n_good == 0:
        verdict = "多空证据都指向『涨得不实』"
    elif n_bad >= 2:
        verdict = "偏可疑（有反证但也有正向证据）"
    elif n_bad == 1:
        verdict = "基本正常，有一处需留意"
    else:
        verdict = "正常（量价资金形态未见异常）"

    return {
        "n_bad": n_bad, "n_good": n_good,
        "signals": sigs, "verdict": verdict,
        "caveat": ("四条证据的方向有交易常识支撑，但**阈值与权重未回测校准**；"
                   "这是观察项，不构成买卖建议。"),
    }


def _tier(state: str, pos, pnl_pct, rise: Dict) -> Dict:
    """三档分级 + 理由（只表"该多看一眼"，不是买卖建议）。

    ★ 2026-11-25：新增 `unknown`（无数据）档 —— 初版在"箱体/资金/行情全缺失"时
      返回 `safe/无异常`，这等于把"不知道"说成"没问题"（项目铁律：缺失 ≠ 满足）。
    """
    bad = int(rise.get("n_bad") or 0)
    good = int(rise.get("n_good") or 0)
    reasons: List[str] = []

    # 完全没有可用证据（无箱体位置、无持仓成本、无任何信号）⇒ 明确"无法判断"
    if pos is None and pnl_pct is None and not (rise.get("signals") or []):
        return {"tier": "unknown", "tier_cn": TIER_CN["unknown"],
                "tier_reason": "缺少箱体/资金/行情数据，无法判断（缺失 ≠ 正常）"}

    if state == "破位":
        reasons.append("已跌破箱体下沿（破位）")
    if pos is not None and pos <= LOWER_EDGE:
        reasons.append(f"处于箱体下沿（位置 {pos:.2f}）")
    if pos is not None and pos >= UPPER_EDGE and state != "突破":
        reasons.append(f"顶到箱体上沿（位置 {pos:.2f}），上方空间小")
    if pnl_pct is not None and pnl_pct <= LOSS_ALERT:
        reasons.append(f"浮亏 {pnl_pct:.1f}% ≤ {LOSS_ALERT}%")
    elif pnl_pct is not None and pnl_pct <= LOSS_WATCH:
        reasons.append(f"浮亏 {pnl_pct:.1f}%")
    if bad >= 2 and good == 0:
        reasons.append(f"上涨质量可疑（{bad} 条反向证据，0 条正向）")
    elif bad >= 1:
        reasons.append(f"有 {bad} 条反向证据")

    if (state == "破位"
            or (pnl_pct is not None and pnl_pct <= LOSS_ALERT)
            or (bad >= 2 and good == 0)):
        tier = "alert"
    elif (bad >= 1 or (pos is not None and (pos >= UPPER_EDGE or pos <= LOWER_EDGE))
          or (pnl_pct is not None and pnl_pct <= LOSS_WATCH)):
        tier = "watch"
    else:
        tier = "safe"

    return {"tier": tier, "tier_cn": TIER_CN.get(tier, tier),
            "tier_reason": "；".join(reasons) or "无异常"}


def tier_view(codes: Optional[List[str]] = None,
              day: str = None, live: bool = True) -> Dict:
    """持仓 / 指定个股的**一屏分级观察**。

    `codes` 为空 ⇒ 自动取**当前持仓**（真实 + 模拟，见 `t_trade_exec.holdings`）。
    返回 `{date, live_as_of, items[], counts{}, note}`；
    `items` 按 tier 严重度排序（alert → watch → safe）。
    """
    # ── 取代码 ──
    want: List[str] = []
    if codes:
        want = [str(c).strip() for c in codes if str(c).strip()]
    else:
        try:
            from app.t_trade_exec import holdings
            want = list(holdings().keys())
        except Exception as e:
            print(f"[tier] holdings failed: {str(e)[:70]}")     # ASCII
    # 去重保序 + 上限
    seen = set()
    want = [c for c in want if not (c in seen or seen.add(c))][:MAX_CODES]

    if not want:
        return {"ok": True, "date": day, "items": [], "counts": {},
                "note": "没有可观察的代码（持仓为空，且未指定 codes）"}

    # ── 箱体（一次 IN 查询）──
    try:
        from app import range_watch as rg
        rng = rg.for_codes(want, day) or {}
        rng_date = ""
        try:
            r = db.fetch_one(f"SELECT MAX(date) AS d FROM range_watch")
            rng_date = str((r or {}).get("d") or "")[:10]
        except Exception as e:
            # 吞异常必须可见（GUARDRAILS #11）：拿不到箱体日期要让前端知道
            # 展示的是"没有日期锚"的数据
            print(f"[tier] range date failed: {str(e)[:60]}")
    except Exception as e:
        print(f"[tier] range_watch failed: {str(e)[:70]}")
        rng, rng_date = {}, ""

    # ── 资金（一次 IN 查询）──
    try:
        from app.washout_alert import load_stats
        stats = load_stats(want) or {}
        stats_date = next((str((v or {}).get("_stats_date") or "")
                           for v in stats.values()), "")
    except Exception as e:
        print(f"[tier] load_stats failed: {str(e)[:70]}")
        stats, stats_date = {}, ""

    # ── 持仓成本 ──
    pos_map: Dict[str, Dict] = {}
    try:
        from app.t_trade_exec import holdings
        pos_map = holdings() or {}
    except Exception:
        pos_map = {}

    items: List[Dict] = []
    live_any = ""
    for code in want:
        r = rng.get(code) or {}
        st = stats.get(code) or {}
        q = _live(code) if live else {}
        if q.get("time"):
            live_any = q["time"]

        price = _f(q.get("price")) or _f(r.get("close"))
        chg = _f(q.get("chg_pct"))
        low, high = _f(r.get("low")), _f(r.get("high"))
        # 用**实时价**重算箱体位置（箱体上下沿是日批口径，不重算）
        posp = None
        if price and low and high and high > low:
            posp = round((price - low) / (high - low), 4)

        hold = pos_map.get(code) or {}
        shares = _f(hold.get("shares"))
        cost = _f(hold.get("cost"))
        pnl_pct = (round((price / cost - 1) * 100, 2)
                   if (price and cost and cost > 0) else None)

        rise = score_rise(r, st, q)
        t = _tier(str(r.get("state") or ""), posp, pnl_pct, rise)

        items.append({
            "code": code,
            "name": (q.get("name") or hold.get("name") or r.get("name") or code),
            "price": price,
            "chg_pct": chg,
            "shares": shares, "cost": cost, "pnl_pct": pnl_pct,
            "sim": bool(hold.get("sim")),
            # 箱体（支撑=下沿，阻力=上沿）
            "support": low, "resistance": high,
            "pos_in_range": posp,
            "range_state": str(r.get("state") or ""),
            "range_days": r.get("range_days"),
            "in_range": bool(r.get("in_range")),
            "vol_ratio": _f(r.get("vol_ratio")),
            "dist_support_pct": (round((price / low - 1) * 100, 2)
                                 if (price and low) else None),
            "dist_resistance_pct": (round((high / price - 1) * 100, 2)
                                    if (price and high) else None),
            "rise": rise,
            **t,
        })

    order = {"alert": 0, "watch": 1, "safe": 2, "unknown": 3}
    items.sort(key=lambda x: (order.get(x["tier"], 9),
                              -(abs(x.get("chg_pct") or 0))))

    counts = {}
    for x in items:
        counts[x["tier"]] = counts.get(x["tier"], 0) + 1

    return {
        "ok": True,
        "date": rng_date or (str(day)[:10] if day else ""),
        "live_as_of": live_any,
        "funds_date": stats_date,
        "items": items,
        "counts": counts,
        "note": ("观察器：支撑/阻力 = 箱体下沿/上沿（日批口径，前 59 日高低点）；"
                 "位置 `pos_in_range` 用**实时价**重算；上涨质量为**未回测校准**的"
                 "证据汇总，不是买卖建议、不进决策链。"),
    }
