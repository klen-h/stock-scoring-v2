# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】个股做T 的「支撑/阻力预设」—— **纯计算，零 IO 副作用**
================================================================================
【用户需求】（2026-11-15）
  "新增一个股票量化做T功能，以 000429 为例：需支持基于技术指标提前预设该股的
   支撑位与阻力位，并在盘中获取实时行情数据。"

【用户确认的三点（同轮问答）】
  1. 触发后**两者都要**：企微推送 + 记模拟盘（推送里带模拟持仓实时盈亏）；
  2. **正T / 反T 都支持**，按当前持仓与资金自动判断；
  3. **先回测验证**，再决定是否上线触发。

【★★ 反T 不用全仓 —— 这是本模块的第一个关键设计】
  卖出数量 = 底仓 × `ratio`（默认 **1/3**，可配 0.1~1.0）。
  ⇒ 即使"卖了接不回来"，最多丢 1/3 底仓，而不是清仓。
  价值股（如 000429 粤高速A）波动小，1/3 仓位做T 的收益/风险比更合理；
  将来做高波动股，把 `ratio` 调小即可，**逻辑不用改**。
  ⚠️ 反T 卖出仍需扣除"今日买入"部分（T+1 不可卖），这属于执行层，不在本模块。

【★★ 为高波动品种铺路 —— 本模块的第二个关键设计】
  所有阈值都**参数化 + 由 ATR% 驱动**，不写死单一股票的绝对价差：
    · `space >= T_MIN_SPACE_ATR × ATR`：ATR 越大，要求日内空间越宽；
    · `suggest_ratio()`：ATR% 超过 `T_HIGH_VOL_ATR_PCT` 时自动缩小仓位比例。
  ⇒ 换标的只需调参数（或环境变量），**不用改代码**。

【支撑/阻力的口径（沿用 `key_level_watch` 已验证的"候选位 + 择优"思路）】
  · 候选位一律来自 **不含今日** 的历史（今日高低不能当支撑/阻力，那是结果不是原因）；
  · 支撑 = 所有"低于现价"的候选里**最高**的那个；阻力 = "高于现价"里**最低**的；
  · 距离带 `0.3% ~ 3%`：太近是噪音（一个 tick 就破），太远当日不可达。

【⚠️ 与 `key_level_watch` 的分工】
  那个模块**只算指数**，且明确"纯观察器、不产生买卖信号"；
  本模块**面向个股**，输出的是**可执行价位**（供触发层消费）。
  两者共享"候选位 + 择优"的算法思想，但**不共用代码**（指数与个股的口径不同）。
================================================================================
"""
import os
from typing import Dict, List, Optional

# ── 可调参数（环境变量覆盖，方便换标的/换波动率档位时不改代码）──
T_RATIO_DEFAULT = float(os.environ.get("T_RATIO_DEFAULT") or 1 / 3)
T_RATIO_MIN = 0.1
T_RATIO_MAX = 1.0

T_SUPPORT_LOOKBACK = int(os.environ.get("T_SUPPORT_LOOKBACK") or 60)   # 前低/前高
T_SHORT_LOOKBACK = int(os.environ.get("T_SHORT_LOOKBACK") or 20)        # 短线前高低
# ★★ ATR 周期默认 **60**（不是教科书的 14）。
#   【依据】2026-11-15 用 4 只标的对比 14 日 vs 60 日（同一套支撑/阻力规则）：
#     000048 净贡献 +3.04% → **+8.40%**（胜率 20.9% → 30.8%）
#     000429 +0.56% → **+3.08%**
#     000021 +5.76% → +6.01%
#     000017 -1.54% → -3.96%（淘汰，单边趋势票本来就不该做T）
#   【为什么】14 日 ATR 会被**近期异动**打高（000017 的 14 日 6.41% vs 80 日 3.70%），
#     于是支撑/阻力被设得过宽、当天根本走不到。
#     做T 要的是"**常态**波动能不能覆盖空间"，所以用更长窗口更稳。
#   ⚠️ 14 日仍可通过 `T_ATR_PERIOD=14` 覆盖（保留可调，不写死）。
T_ATR_PERIOD = int(os.environ.get("T_ATR_PERIOD") or 60)

# ★★ 2026-11-15：**反T 默认关闭**（回测 1410 次均值 -0.046%/次，负期望）。
#   见 `resolve_direction` 的完整说明。要开：环境变量 `T_ALLOW_SHORT_T=1`。
#   ⚠️ 这是**数据决定的默认值**，不是保守倾向 —— 关掉它等于承认
#     "先卖后买"在当前规则下不成立。
_ALLOW_SHORT_T = str(os.environ.get("T_ALLOW_SHORT_T") or "").lower() in ("1", "true", "yes")

# ★★ 2026-11-16（用户："价格到位，系统自动处理"）：**建仓点命中时自动建模拟底仓**。
#
#   【它到底做了什么】建仓计划（`plan_type='t_build'`）的买价被触发时，
#     系统**在 `t_sim_positions` 里记一笔模拟底仓**（股数按 `min_base_shares`），
#     于是这只票立刻"有底仓"⇒ 盘中 `t_trade_loop` 能对它做T，全链路无人值守。
#
#   【它**没有**做什么（必须让用户知道）】
#     · **没有真实下单**（没有券商接口），也不写 `user_portfolio`；
#     · 不会替用户花一分钱 —— 所有盈亏都是模拟记账。
#     真实买入仍需用户自己操作；真实买入后建议把模拟仓清掉
#     （`DELETE /api/t-trade/sim/{code}`），否则同一只票会有两层底仓
#     （`holdings()` 里真实优先，所以只是展示噪音，不会算错量）。
#
#   【为什么默认开】用户明确要求"系统自动处理"。
#     关掉：环境变量 `T_AUTO_BUILD=0`。
_AUTO_BUILD = str(os.environ.get("T_AUTO_BUILD") or "1").lower() not in (
    "0", "false", "no")

# 正T 的**安全上限**（不是"真实资金"）——真实资金系统拿不到，见 `calc_shares` 说明。
# ⚠️ 2026-11-15：曾有"设为不限"的想法，被否 —— 设成 1 亿会算出 526 万股 / 3330 万元。
#   它只是"别买超过这个数"的兜底，**主依据是底仓**。
T_CASH_ASSUMED = float(os.environ.get("T_TRADE_CASH") or 50000)

T_MIN_DIST_PCT = float(os.environ.get("T_MIN_DIST_PCT") or 0.3)   # 候选位距现价下限 %
T_MAX_DIST_PCT = float(os.environ.get("T_MAX_DIST_PCT") or 3.0)   # 绝对上限 %
# ★★ 2026-11-15（000429 回测实测后修正）：距离上限**还要受 ATR 约束**。
#   【为什么】实测 000429（粤高速A）338 个交易日：
#       支撑→阻力空间 平均 **3.58%**，而当日振幅平均只有 **1.93%**
#       ⇒ 109 天"有空间"，但**两端同时触及 0 天**（做T 一天走不完一个来回）。
#   根因是"前高/前低"这类历史位天然离现价 1.5~2.7%，对低波动价值股太远。
#   ⇒ 上限改成 `min(T_MAX_DIST_PCT, atr_pct × T_DIST_ATR_MULT)`：
#     低波动股（ATR%≈1.4）自动收到 1.7% 以内，高波动股（ATR%≈5）放宽到绝对上限。
T_DIST_ATR_MULT = float(os.environ.get("T_DIST_ATR_MULT") or 1.2)
# ATR 近端候选倍数：当历史位全都太远时，用 `现价 ± m×ATR` 兜底（类型标「ATR×m」）
T_ATR_CAND_MULTS = tuple(
    float(x) for x in (os.environ.get("T_ATR_CAND_MULTS") or "0.5,1.0").split(","))
# 空间门槛（×ATR）。★ 原写 1.5 与"支撑≈-0.5ATR / 阻力≈+0.5ATR"自相矛盾
#   （那样 space 至多 1×ATR，永远过不了门槛）。
#   ★★ 但改成 0.8 后又踩了另一个坑：`ATR×m` 候选让 space 几乎恒等于 ~1×ATR
#      ⇒ **倍数门槛永远满足，可行性判定形同虚设**（写单测时才发现）。
#   ⇒ 必须**同时**要求绝对百分比下限（见 `T_MIN_SPACE_PCT`）：
#     倍数管"相对波动够不够"，百分比管"扣成本后**还剩多少**"。
T_MIN_SPACE_ATR = float(os.environ.get("T_MIN_SPACE_ATR") or 0.8)
# 空间绝对下限（%）。做T 双边成本约 0.1%，再留出利润 ⇒ 低于 1% 的来回不值得动手
#   （ATR% 0.5% 的股票 space 只有 ~0.4%，扣成本剩 0.3%，却要承担方向风险）。
T_MIN_SPACE_PCT = float(os.environ.get("T_MIN_SPACE_PCT") or 1.0)

# 高波动自动缩量：ATR% ≥ 此值时，仓位比例乘以 SCALE（为未来高波动股铺路）
T_HIGH_VOL_ATR_PCT = float(os.environ.get("T_HIGH_VOL_ATR_PCT") or 4.0)
T_HIGH_VOL_RATIO_SCALE = float(os.environ.get("T_HIGH_VOL_RATIO_SCALE") or 0.6)


# ══════════════════════════════════════════════════════════════════════════
#  基础指标
# ══════════════════════════════════════════════════════════════════════════

def _f(v) -> Optional[float]:
    """安全转 float；非法返回 None（不把 "-"/"" 当 0）。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if x > 0 else None


def atr(bars: List[Dict], period: int = T_ATR_PERIOD) -> float:
    """ATR（真实波幅均值）。数据不足返回 0.0。

    ⚠️ **不直接用 `paper_trading._atr14()`**：它内部把日期硬编码成
      `2026-01-01~2026-12-31` 且只拉 40 根，跨年后就失效；而且做T 需要
      60 日前低，40 根根本不够。这里从调用方传进来的 bars 算，零额外请求。
    """
    if len(bars) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(bars)):
        h, l = _f(bars[i].get("high")), _f(bars[i].get("low"))
        pc = _f(bars[i - 1].get("close"))
        if h is None or l is None or pc is None:
            continue
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return 0.0
    return round(sum(trs[-period:]) / period, 4)


def _ma(bars: List[Dict], n: int) -> Optional[float]:
    if len(bars) < n:
        return None
    vals = [_f(b.get("close")) for b in bars[-n:]]
    if any(v is None for v in vals):
        return None
    return round(sum(vals) / n, 4)


def round_levels(price: float) -> List[float]:
    """现价附近的整数/半整数关口（A 股心理位）。

    只返回最近的两个（上一个、下一个），由 `compute_levels` 再按距离过滤。
    ⚠️ 不用 `floor/ceil` 到整数元：13.79 的 13.00 差 5.7%，超过 3% 上限、永远选不上，
       白占候选。这里用 **0.5 元步长**，覆盖 13.5 / 14.0 这种真正会形成阻力的位置。
    """
    if price <= 0:
        return []
    step = 0.5 if price < 50 else (1.0 if price < 200 else 5.0)
    lo = (int(price / step)) * step
    hi = lo + step
    out = []
    for v in (round(lo, 2), round(hi, 2)):
        if v > 0 and v not in out:
            out.append(v)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  支撑 / 阻力
# ══════════════════════════════════════════════════════════════════════════

def compute_levels(bars: List[Dict], price: Optional[float] = None) -> Dict:
    """算当前价下方最近支撑、上方最近阻力（含来源解释）。

    参数
      bars  : 升序日线（最后一项 = 最近交易日）；须含 high/low/close
      price : 参照价（盘中传实时价；盘前传最新收盘）。缺省用最后收盘。

    返回
      {price, support, support_type, resistance, resistance_type,
       atr, atr_pct, candidates: {support: [...], resistance: [...]}, reason}
      `support`/`resistance` 为 None 时看 `reason`。
    """
    out = {"price": None, "support": None, "support_type": "",
           "resistance": None, "resistance_type": "",
           "atr": 0.0, "atr_pct": 0.0, "max_dist_pct": 0.0,
           "candidates": {"support": [], "resistance": []},
           "reason": ""}
    if not bars or len(bars) < 2:
        out["reason"] = "日线不足（需至少 2 根）"
        return out

    px = _f(price) or _f(bars[-1].get("close"))
    if not px:
        out["reason"] = "参照价无效"
        return out
    out["price"] = round(px, 3)

    # ★ 候选位**一律来自不含今日的历史**：今日的高低点是"结果"，拿来当支撑/阻力是同义反复
    hist = bars[:-1]
    if len(hist) < 2:
        out["reason"] = "去掉今日后历史不足"
        return out

    a = atr(bars)
    out["atr"] = a
    out["atr_pct"] = round(a / px * 100, 2) if a else 0.0

    # ── 候选池（每条都带类型标签，便于界面解释"为什么是这个价"）──
    sup_c: List[tuple] = []   # (value, type)
    res_c: List[tuple] = []

    seg = hist[-T_SUPPORT_LOOKBACK:]
    closes = [_f(b.get("close")) for b in seg]
    closes = [c for c in closes if c]
    if closes:
        sup_c.append((min(closes), "前低(%d日)" % len(closes)))
        res_c.append((max(closes), "前高(%d日)" % len(closes)))

    seg2 = hist[-T_SHORT_LOOKBACK:]
    lows = [_f(b.get("low")) for b in seg2]
    highs = [_f(b.get("high")) for b in seg2]
    lows = [x for x in lows if x]
    highs = [x for x in highs if x]
    if lows:
        sup_c.append((min(lows), "近%d日最低" % len(lows)))
    if highs:
        res_c.append((max(highs), "近%d日最高" % len(highs)))

    for n, label in ((20, "MA20"), (60, "MA60")):
        m = _ma(hist, n)
        if m:
            sup_c.append((m, label))
            res_c.append((m, label))

    for v in round_levels(px):
        if v < px:
            sup_c.append((v, "整数位"))
        elif v > px:
            res_c.append((v, "整数位"))

    # ★★ ATR 近端候选（兜底）：当历史位（前高低/均线）全都落在 ATR 之外时，
    #   `现价 ± m×ATR` 是**唯一还能当日走到**的位置。没有它，
    #   低波动价值股会永远"支撑/阻力太远、做T 走不完"（000429 实测 0/109 天）。
    if a:
        for m in T_ATR_CAND_MULTS:
            d = a * m
            if px - d > 0:
                sup_c.append((px - d, "ATR×%.1f" % m))
            res_c.append((px + d, "ATR×%.1f" % m))

    # ── 择优：距离带过滤（下限固定 0.3%，上限受 ATR 约束）──
    max_dist = T_MAX_DIST_PCT
    if out["atr_pct"] > 0:
        max_dist = min(T_MAX_DIST_PCT, out["atr_pct"] * T_DIST_ATR_MULT)
    out["max_dist_pct"] = round(max_dist, 2)

    def _pick(cands, below: bool):
        kept = []
        for v, t in cands:
            dist = abs(px - v) / px * 100
            if v == 0 or dist < T_MIN_DIST_PCT or dist > max_dist:
                continue
            if below and v >= px:
                continue
            if (not below) and v <= px:
                continue
            kept.append({"value": round(v, 3), "type": t, "dist_pct": round(dist, 2)})
        kept.sort(key=lambda x: x["dist_pct"])
        return kept

    sup_kept = _pick(sup_c, below=True)
    res_kept = _pick(res_c, below=False)
    out["candidates"] = {"support": sup_kept, "resistance": res_kept}

    if sup_kept:
        out["support"] = sup_kept[0]["value"]
        out["support_type"] = sup_kept[0]["type"]
    if res_kept:
        out["resistance"] = res_kept[0]["value"]
        out["resistance_type"] = res_kept[0]["type"]

    if not sup_kept and not res_kept:
        out["reason"] = ("现价 %.2f 附近 %.1f%%~%.1f%% 内无有效候选位（波动过小或"
                         "已贴近前高/前低）" % (px, T_MIN_DIST_PCT, T_MAX_DIST_PCT))
    return out


# ══════════════════════════════════════════════════════════════════════════
#  仓位比例（★ 反T 不用全仓 + 高波动自动缩量）
# ══════════════════════════════════════════════════════════════════════════

def suggest_ratio(atr_pct: float, base: Optional[float] = None) -> Dict:
    """做T 仓位比例建议（占底仓/可用资金的比例）。

    默认 **1/3**；`atr_pct >= T_HIGH_VOL_ATR_PCT` 时再乘 `T_HIGH_VOL_RATIO_SCALE`
    ⇒ 高波动股自动缩量（同等比例下波动风险更大）。
    """
    b = T_RATIO_DEFAULT if base is None else float(base)
    b = max(T_RATIO_MIN, min(T_RATIO_MAX, b))
    scaled = b
    note = "默认 %d%%" % round(b * 100)
    if atr_pct >= T_HIGH_VOL_ATR_PCT:
        scaled = max(T_RATIO_MIN, b * T_HIGH_VOL_RATIO_SCALE)
        note = ("ATR %.2f%% ≥ %.1f%% ⇒ 高波动，比例 %.0f%% → %.0f%%"
                % (atr_pct, T_HIGH_VOL_ATR_PCT, b * 100, scaled * 100))
    # ★ 2026-11-16：**3 位 → 6 位**。
    #   原来 `round(1/3, 3) = 0.333` ⇒ `300 × 0.333 = 99.9` ⇒ 差 0.1 股被判"不足"
    #   ⇒ 用户把底仓加到 300 股（1/3 正好 100 股）仍然显示"做不了"。
    #   存库字段是 `real`（约 7 位有效数字），6 位足够表达 1/3 且不丢整手边界。
    return {"ratio": round(scaled, 6), "note": note}


# ══════════════════════════════════════════════════════════════════════════
#  完整计划
# ══════════════════════════════════════════════════════════════════════════

def plan_signals_for_holding(hits: list, has_base: bool) -> list:
    """按"有没有底仓"过滤交易计划信号 —— **无仓可兑现，也无仓可认错**。

    ★★ 2026-11-16（用户：企微收到"按计划兑现"，页面今日成交却是空的）：
      建仓点计划 `buy=支撑 / target=阻力` 原来是**同一条计划**的两个信号。
      若当天价格**直接高开越过 target**（没回踩到 buy），`target` 会先命中，
      计划被置 `hit` 并推"按计划兑现" —— 但**底仓根本没建成**，
      所谓"兑现"是**空兑现**，随后 buy 信号永远不会再触发。
      实测今天 600483 福能股份（买 11.10 / 标 11.40，09:52 hit）、
      000415 渤海租赁（买 4.59 / 标 4.731，09:54 hit）都是这样。

    【规则】离场信号（`stop` / `target`）**必须有仓位才成立**：
      · 有底仓 ⇒ 全部信号照常；
      · 无底仓 ⇒ 只保留进场信号（`buy` / `high_open` / `volume_break`）。

    ⚠️ 这是通用规则，不是只对 `t_build` 开特例：
      用户手工建的计划（plan_type=trial）同样可能"没建仓就兑现"，
      今天 002241 那条 2026-10-29 的 hit 也是这个毛病。
    """
    if has_base:
        return hits
    return [(kind, text) for kind, text in hits
            if kind not in ("stop", "target")]


def should_auto_build(plan_type: str, enabled: Optional[bool] = None) -> bool:
    """这条交易计划命中买点时，**是否该自动建模拟底仓**。

    用户（2026-11-16）："价格到位，系统自动处理。"

    · 只对 `plan_type='t_build'`（从做T 台「设建仓点」建出来的）生效；
    · 普通试仓（`trial`）**仍然只提醒** —— 那是用户自己的决策，系统不代劳；
    · 总开关 `_AUTO_BUILD`（env `T_AUTO_BUILD=0` 可关）。

    ⚠️ 必须用 `plan_type` 而不是匹配 `reason` 里的中文：
      文案一改就失效，而且"做T 底仓建仓点"这类字符串在别处也可能出现。
    """
    e = _AUTO_BUILD if enabled is None else bool(enabled)
    return bool(e) and str(plan_type or "") == "t_build"


def resolve_direction(has_position: bool, has_cash: bool) -> List[str]:
    """按当前持仓/资金，给出**可用且按优先级排序**的做T 方向。

    用户（2026-11-15）："正T/反T 都支持，按当前持仓与资金自动判断。"

    ★★★ 2026-11-15 回测后**改过一次**（19 只 × 400 天，1457/1410 次）：

        方向   次数    胜率     单次均值    最差一笔
        正T    1457   54.2%   **+0.122%**   -9.23%
        反T    1410   51.8%   **-0.046%**   -9.81%

      **反T 是负期望**。原因在"卖飞"：1392 次反T 卖出后，
      **90% 的样本在 5 日内最高价都高于卖价**（平均踏空 3.64%、最大 31.71%）。
      ⇒ 阻力位往往是**突破的起点**而不是终点，先卖再等回落，
        多数时候等不到，只能追高买回或止损。

      ⇒ **默认只做正T**（`T_ALLOW_SHORT_T=1` 才开反T）。
        ⚠️ 这不是"保守"，是**数据说不该做**：1/3 仓也只是把 -0.046% 缩成
          -0.015%，**方向错了，缩仓位救不回来**。

    · 有资金 ⇒ 可做正T。
    · 有底仓 ⇒ 仅在 `T_ALLOW_SHORT_T=1` 时可做反T（卖出的是昨日及之前的仓位，T+1 合规）。
    · 都没有 ⇒ 返回空列表，调用方据此跳过。
    """
    # ★★★ 2026-11-15：**正T 也要求有底仓**（原来只要求有资金，这是错的）。
    #   A 股 T+1：今天买的不能今天卖 ⇒ 正T 的"卖出"卖的是**原有底仓**。
    #   无底仓时按资金买 = **建仓**，不是做T（实测池内 19 只全无底仓，
    #   旧逻辑会显示"可买 2600 股"，等于在推荐建仓）。
    #   ⇒ 无底仓一律返回 []，调用方跳过。
    dirs: List[str] = []
    if has_position and has_cash:
        dirs.append("long_t")
    if has_position and _ALLOW_SHORT_T:
        dirs.append("short_t")
    return dirs


def calc_shares(direction: str, price: float, base_shares: float,
                cash: float, ratio: float,
                today_bought: float = 0.0) -> int:
    """算出这一笔做T 的**股数**（100 股整数倍，A 股最小单位）。

    参数
      direction     : `long_t`（先买）/ `short_t`（先卖）
      price         : 触发价
      base_shares   : 当前底仓股数
      cash          : 可用资金（元）—— **系统并不知道实盘现金**，见下
      ratio         : 仓位比例（`suggest_ratio` 给出，默认 1/3）
      today_bought  : **今日已买入**股数（含手动加仓无法感知，只能扣做T 自己的）

    ★ 反T：卖出量 = `底仓 × ratio`，且不超过 `底仓 − 今日买入`。
      ⇒ 即使用了 1/3 也接不回来，最多丢 1/3 底仓，不会清仓；且严格满足 T+1。

    ★★★ 2026-11-15 修正（用户："建议将做T 的资金设为不限吗？"）
      **原实现只按 `cash × ratio / price` 算正T，这是错的。**

      【为什么错】`cash` 是环境变量 `T_TRADE_CASH`（默认 5 万），
        **系统根本没有实盘可用资金**（`user_portfolio` 无现金字段，
        `paper_account.initial_capital` 是模拟盘本金，不是实盘钱）。
        ⇒ 用假设值当真实资金，会算出**与底仓完全不匹配的量**：
        实测底仓 200 股粤高速时，正T 却买 **2600 股中国宝安**（13 倍底仓）。

      【为什么不能"设为不限"】实测把资金设成 1 亿 ⇒
        中国宝安 **526 万股 / 3330 万元**；模拟盈亏随之失真成天文数字，
        而"不限"本质是**假装系统知道你有多少钱**，比默认 5 万更危险。

      【修法】正T 改为**以底仓为基准**（与反T 对称）：
        买入量 = `min(底仓 × ratio, cash × ratio / price)`。
        · 有底仓 ⇒ 底仓约束生效（做T 是**围绕底仓摊薄成本**，不是建仓）；
        · 无底仓 ⇒ 才退回资金口径（此时没有"底仓"可言，只能按钱算）。
        ⚠️ `cash` 仍保留为**安全上限**而不是主依据。

      【★ 随之而来的真话】A 股最小 100 股，`1/3` 仓要求底仓 ≥ 300 股。
        实测当前持仓 工行 100 / 粤高速 200 / 内蒙一机 100 ⇒ **都做不了**。
        这不是 bug，是"仓位太小，摊薄成本没有意义"。
    """
    if price <= 0 or ratio <= 0:
        return 0

    # ★★★ 2026-11-16（用户："做T 交易台的池子不会自动加入吗？" 顺带实测发现）：
    #   **浮点误差导致「刚好够」变成「不够」**。
    #   实测：底仓 **300 股** × `1/3`，数学上正好 **100 股**（A 股最小单位），
    #   但 `300 * (1/3) = 99.99999999999999` ⇒ `// 100 * 100 = 0`
    #   ⇒ 用户按建议把仓位加到 300 股，**仍然显示"做不了"**。
    #   修法：先把"想要的股数"四舍五入到 **6 位小数**（消除 1/3 这类无限小数的尾巴），
    #     再向下取整到 100 股。这样 99.9 仍归零、100.000000 正确留下。
    def _floor_lot(x: float) -> int:
        """向下取整到 100 股，**容忍"整手边界上 <0.01 股"的比例表示误差**。

        【为什么需要】底仓 300 股 × `1/3` **数学上正好 100 股**，
          但 `ratio` 存库是 `real`（`1/3 → 0.333`），算出 `99.9`
          ⇒ 直接 `//100` 归零 ⇒ **用户按建议加到 300 股仍然做不了**。
        【判据】只有"四舍五入后正好是整手、且与整手差 <0.01 股"才进位：
          · 99.99999 → 100（差 0.00001 ⇒ 是误差）✓
          · 99.567   → 0  （差 0.433 ⇒ 是真的不够）✓
          · 199.6    → 100（差 0.4 ⇒ 不够）✓
          这样不会因为"容差太宽"造成实质超买。
        """
        v = float(x)
        r = round(v)
        if abs(v - r) < 0.01 and r % 100 == 0 and r > 0:
            return int(r)
        return int(v // 100) * 100

    if direction == "short_t":
        sellable = max(0.0, float(base_shares) - float(today_bought))
        return _floor_lot(sellable * ratio)
    if direction == "long_t":
        caps = []
        if base_shares and base_shares > 0:
            caps.append(float(base_shares) * ratio)          # ① 底仓为基准
        if cash and cash > 0:
            caps.append(float(cash) * ratio / price)         # ② 资金为上限
        if not caps:
            return 0
        return _floor_lot(min(caps))
    return 0


def min_base_shares(ratio: float) -> int:
    """按仓位比例反算**最小可用底仓股数**（100 股整数倍）。

    【为什么需要】做T 卖出的量 = `底仓 × ratio`，要成一手（100 股）才行。
      ⇒ 底仓至少要 `100 / ratio` 股，否则**建完仓也做不了 T**。

    【踩过的坑（2026-11-16）】接口原先**硬编码 `suggest_shares = 300`**：
      · `ratio = 1/3` ⇒ 300 × 0.3333 ≈ 100 ✓ 正好够
      · `ratio = 0.2`（000878 云南铜业）⇒ 300 × 0.2 = **60 股** ⇒ 不够！
      用户"照建议建完仓，结果还是做不了"，等于白建。

    【为什么用 round 而不是 ceil】
      `100 / (1/3) = 300.00000000000006` ⇒ `ceil` 会要 **400 股**（多花 3000 元），
      而 `calc_shares` 有"整手边界容差"（`_floor_lot`），300 股确实够。
      ⇒ 四舍五入到整百，与 `calc_shares` 的口径**保持一致**。

    · `ratio <= 0` ⇒ 返回 0（无法反算，调用方当作"不适用"）。
    """
    if ratio is None or float(ratio) <= 0:
        return 0
    need = 100.0 / float(ratio)
    return max(100, int(round(need / 100.0)) * 100)


def buy_check(base_shares: float, cash: float, price: float, ratio: float,
              today_bought: float = 0.0) -> Dict:
    """这一笔**能不能真的下单**？返回 `{ok, shares, need_cash, reason}`。

    【与 `calc_shares` 的分工】
      · `calc_shares` 只算数量（算不出就 0，**不知道为什么**）；
      · 本函数**给出"为什么不能"** —— 否则用户只看到"今天没触发"，
        而实际是"仓位不足以下单"，这两件事的处置完全不同。

    ★★★ 刻意**不检查**的三样东西（用户 2026-11-15："之前防御状态下不影响买入"）：

      · `coach` 的 `REGIME_POSITION[regime]`（`defensive` 档 = **0，禁买**）
      · `paper_account.risk_frozen`（G2 回撤冻结，钉住等人工解锁）
      · `paper_account.cooldown_until`（G3 冷却期）

      【为什么】这些闸门拦的是「**加新仓**」。做T 是围绕**已有底仓**做日内差价：
        卖的是自己的持仓、买回的也是自己的持仓 ⇒ **净仓位不变，不新增风险敞口**。
        在"需要摊薄成本"的时候把工具锁上，逻辑是反的。

      ⚠️ 但 **资金 / 底仓够不够下单必须检查** —— 那是物理约束，不是风控。

      ⚠️ 若将来真想"防御期不做T"，应当加**独立开关**，
        而不是顺手读 `REGIME_POSITION` —— 那是两件事。

    【实测背景】A 股最小 100 股，`ratio=1/3` 要求底仓 ≥ 300 股。
      当前持仓 工行 100 / 粤高速 200 / 内蒙一机 100 股 ⇒ `ok=False` 且原因明确。
    """
    if price is None or price <= 0:
        return {"ok": False, "shares": 0, "need_cash": 0, "reason": "价格无效"}
    if ratio is None or ratio <= 0:
        return {"ok": False, "shares": 0, "need_cash": 0, "reason": "仓位比例无效"}

    # ★★★ 2026-11-15：**无底仓就不能做正T**。
    #   【为什么】A 股 T+1：当天买入**不能当天卖出**。
    #     做T 的"先买后卖"里，卖出那一步卖的是**原有底仓**（今日买入的部分不可卖）。
    #     ⇒ 没有底仓时，买了只能等次日 ⇒ **那不是做T，是建仓**。
    #   【实测踩到】池内 19 只用户都没持仓（底仓 0），
    #     旧逻辑退回资金口径 ⇒ 显示"可买 2600 股"，
    #     实际是让系统在用户没底仓时推荐建仓，与"做T"名不符实。
    #   ⇒ 明确拒绝，并说清原因（用户能据此判断"该加仓还是该换标的"）。
    if not base_shares or float(base_shares) <= 0:
        return {"ok": False, "shares": 0, "need_cash": 0,
                "reason": "无底仓 —— 做T 必须有底仓（A 股 T+1，当天买入不能当天卖）"}

    shares = calc_shares("long_t", price, base_shares, cash, ratio, today_bought)
    if shares > 0:
        return {"ok": True, "shares": shares,
                "need_cash": round(shares * price, 2), "reason": ""}

    b = float(base_shares or 0)
    c = float(cash or 0)
    if b > 0 and b * ratio < 100:
        return {"ok": False, "shares": 0, "need_cash": 0,
                "reason": "底仓 %d 股 × %.0f%% = %d 股，不足 A 股最小 100 股" % (
                    int(b), ratio * 100, int(b * ratio))}
    if b > 0 and c > 0:
        # 底仓够但资金上限卡住
        affordable = int((c * ratio / price) // 100) * 100
        return {"ok": False, "shares": 0, "need_cash": round(c * ratio, 2),
                "reason": "资金上限 %.0f 元 × %.0f%% 只够 %d 股，不足 100 股" % (
                    c, ratio * 100, affordable)}
    if b > 0 and c <= 0:
        return {"ok": False, "shares": 0, "need_cash": 0,
                "reason": "底仓 %d 股够但无可用资金（正T 要先买）" % int(b)}
    return {"ok": False, "shares": 0, "need_cash": 0, "reason": "可买数量不足 100 股"}


def check_trigger(plan: Dict, price: float, open_side: Optional[str] = None) -> Optional[Dict]:
    """判断此刻是否触发，返回 `{direction, kind, text_why}` 或 `None`。

    【两阶段语义】—— 这是做T 与"一次买入计划"最大的不同：
      · `open_side is None` ⇒ 找**开仓**机会：
          - 正T：现价跌到支撑（≤ 支撑×1.002）
          - 反T：现价涨到阻力（≥ 阻力×0.998）
      · `open_side` 已开仓 ⇒ 找**平仓**机会：
          - 正T（先买）⇒ 涨到阻力平掉 / 跌破止损
          - 反T（先卖）⇒ 回落到支撑接回 / 涨破止损（接不回来要认错）

    ⚠️ 容差 0.2% 是"贴价"判断：日K 回测里用 `low<=sup` 是理想化的，
      实盘不可能卖在最低点，所以触发用"接近"而非"等于"。
    """
    sup, res = plan.get("support"), plan.get("resistance")
    a = plan.get("atr") or 0
    if not (sup and res and price > 0):
        return None
    tol = 0.002

    if open_side is None:
        if price <= sup * (1 + tol):
            return {"direction": "long_t", "kind": "open",
                    "text_why": "现价 %.2f 触及支撑 %.2f（%s）" % (
                        price, sup, plan.get("support_type") or "-")}
        if price >= res * (1 - tol):
            return {"direction": "short_t", "kind": "open",
                    "text_why": "现价 %.2f 触及阻力 %.2f（%s）" % (
                        price, res, plan.get("resistance_type") or "-")}
        return None

    stop_buf = a * 0.5      # 止损缓冲：半个 ATR
    if open_side == "long_t":
        if price >= res * (1 - tol):
            return {"direction": "long_t", "kind": "close",
                    "text_why": "现价 %.2f 触及阻力 %.2f，正T 兑现" % (price, res)}
        if stop_buf and price <= sup - stop_buf:
            return {"direction": "long_t", "kind": "stop",
                    "text_why": "现价 %.2f 跌破 支撑 %.2f − 0.5×ATR ⇒ 正T 止损" % (price, sup)}
        return None

    if open_side == "short_t":
        if price <= sup * (1 + tol):
            return {"direction": "short_t", "kind": "close",
                    "text_why": "现价 %.2f 回落到支撑 %.2f，反T 接回" % (price, sup)}
        if stop_buf and price >= res + stop_buf:
            return {"direction": "short_t", "kind": "stop",
                    "text_why": "现价 %.2f 涨破 阻力 %.2f + 0.5×ATR ⇒ 反T 接不回，认错" % (price, res)}
        return None
    return None


def build_plan(code: str, name: str, bars: List[Dict],
               price: Optional[float] = None,
               ratio: Optional[float] = None) -> Dict:
    """生成一只股票的做T 预案（支撑/阻力 + 可行性 + 建议仓位）。

    `feasible=True` 的三个条件（缺一不可）：
      1. 支撑、阻力都找到了；
      2. 空间 ≥ `T_MIN_SPACE_ATR × ATR`（日内振幅盖不过一个 ATR，做T 没肉）；
      3. 空间百分比 ≥ `2 × T_MIN_DIST_PCT`（否则买在支撑、卖在阻力还不够覆盖门槛）。
    """
    lv = compute_levels(bars, price)
    a, px = lv["atr"], lv["price"]
    sup, res = lv["support"], lv["resistance"]
    ratio_info = suggest_ratio(lv["atr_pct"], ratio)

    plan = {
        "code": code, "name": name,
        "price": px, "as_of": (bars[-1].get("date") if bars else None),
        "support": sup, "support_type": lv["support_type"],
        "resistance": res, "resistance_type": lv["resistance_type"],
        "atr": a, "atr_pct": lv["atr_pct"],
        "max_dist_pct": lv.get("max_dist_pct"),
        "ratio": ratio_info["ratio"], "ratio_note": ratio_info["note"],
        "space_abs": None, "space_pct": None, "space_atr_mult": None,
        "feasible": False, "reason": lv["reason"],
        "candidates": lv["candidates"],
    }
    if not (sup and res and a and px):
        if not plan["reason"]:
            plan["reason"] = "支撑/阻力/ATR 至少一项缺失"
        return plan

    space_abs = round(res - sup, 3)
    space_pct = round(space_abs / sup * 100, 2)
    mult = round(space_abs / a, 2)
    plan.update({"space_abs": space_abs, "space_pct": space_pct,
                 "space_atr_mult": mult})

    if mult < T_MIN_SPACE_ATR:
        plan["reason"] = ("空间 %.2f（%.2f%%）= %.2f×ATR < 门槛 %.1f×ATR ⇒ "
                          "日内空间不够，做T 不划算" % (space_abs, space_pct, mult, T_MIN_SPACE_ATR))
        return plan
    # ★ 绝对下限：倍数门槛会被 `ATR×m` 候选"自我满足"（space≈1×ATR 恒 ≥0.8×ATR），
    #   所以必须再卡一道百分比 —— 这一条才是"扣完成本还剩多少"的真正判据。
    if space_pct < T_MIN_SPACE_PCT:
        plan["reason"] = ("空间 %.2f%% < %.1f%% ⇒ 扣掉成本后太薄，不值得为这点差价"
                          "承担方向风险" % (space_pct, T_MIN_SPACE_PCT))
        return plan

    plan["feasible"] = True
    return plan
