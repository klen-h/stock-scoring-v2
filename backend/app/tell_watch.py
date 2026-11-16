# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】跨市场危险信号（tell）检测器 —— **纯观察器**
================================================================================
【它解决的问题】
  "今天跌了"和"今天要崩了"是两件事，但盘面上都表现为指数下跌。
  区别在于：**本该一起崩的东西有没有一起崩。**

    箱体内轮动：崩溃的是高久期资产（创业板、KOSPI、纳指期货），
                而避险/低久期资产（黄金、人民币、A50）**纹丝不动**。
    系统性清仓：连"本该对冲"的东西也在跌 —— 黄金失守、人民币急贬、
                美元飙升、铜油同跌。

  ⇒ 所以判据不是"跌多少"，而是"**缺席了几项**"。
     全灭 = 恐慌没有扩散；点亮得越多 = 越可能是流动性问题。

【命名与来源】
  用户（2026-11-16）贴出一份盘后分析，其中列出了这张清单：
    黄金<3850、CNH 急贬、DXY 飙升、A50 破位、上证50 开裂、
    涨跌比<0.3、铜油同跌 —— "全灭=箱体内的暴力轮动；任何一个点亮，
    剧本升级为系统性清仓"。

  ★ 本模块**不照抄绝对价位**（3850 是那天的水平，会随时间漂移），
    改用**单日变化率 + 相对阈值**，这样阈值不会因价格中枢移动而失效。

【★★ 定位：纯观察器（与 `cross_market_watch` / `style_spread` 同纪律）】
  · **不改分数、不进闸门、不改 regime 四态、不碰模拟盘**；
  · 删掉本模块，系统其余部分照常运行；
  · **绝不**把结论喂给 `market_regime._external_panic()`
    —— 那条链会连锁改评分权重/准入闸门（`cross_market_watch` 已用同样理由拒绝）。

【口径与代价】
  · **零新增外部请求**：全部读 `get_macro_panel()` 的 60s 共享缓存
    + `routers.market._breadth_now()` 的内存宽度（行情每 120s 刷）。
  · ⚠️ `a50` 是**期货**（东财 `104.CN00Y`），含夜盘 ⇒ 白天读到的可能已含隔夜跳空。
    这一点在输出 `note` 里必须说明，不能让人以为它是 A 股当日跌幅。
  · ⚠️ 美债 `us10y` 的日变化 bp 在 `panel["_derived"]`（**下划线私有键**）——
    `market_regime.py` 曾误读 `panel["derived"]` ⇒ 那条腿静默恒空。
  · **缺失 ≠ 未触发**：取不到数据的项记 `state="insufficient"`，
    **不计入点亮数**，也绝不当成"安全"（`macro_regime_class` 同款纪律）。

【阈值留痕】
  初始值按"单日显著异动"取（黄金 -2%、CNH +0.5%、DXY +1%、A50 -2%、
  涨跌比 0.3、10Y +5bp）。全部可用环境变量覆盖，改阈值请在此处写明依据。
================================================================================
"""
import os
from typing import Dict, List, Optional

# ── 单项阈值（单日变化，单位 %；bp 项单独标注）─────────────────────────────
GOLD_DROP_PCT = float(os.environ.get("TELL_GOLD_DROP") or -2.0)   # 黄金大跌=避险失效
CNH_WEAK_PCT = float(os.environ.get("TELL_CNH_WEAK") or 0.5)      # USDCNH 上行=人民币贬
DXY_SPIKE_PCT = float(os.environ.get("TELL_DXY_SPIKE") or 1.0)    # 美元融资挤压
A50_BREAK_PCT = float(os.environ.get("TELL_A50_BREAK") or -2.0)   # 外资定价的中国资产
BREADTH_MIN = float(os.environ.get("TELL_BREADTH") or 0.3)        # 涨/跌 家数比
US10Y_SPIKE_BP = float(os.environ.get("TELL_US10Y_BP") or 5.0)    # 全球贴现率跳升
# 铜油同跌的"够跌"门槛：两者都跌**且**至少一个跌超过该值。
#   【为什么不只看"都 < 0"】微跌（铜 -0.1% / 油 -0.2%）天天都有，
#     会把这条 tell 变成噪音源；用户分析里的语义是"需求恐慌"，
#     需要至少一端出现显著跌幅才算数。
COPPER_OIL_MIN_DROP = float(os.environ.get("TELL_COPPER_OIL_DROP") or 1.5)

# ── 分档阈值 ──────────────────────────────────────────────────────────────
# 【为什么流动性组单独算】黄金/人民币/美元同时异动 = 典型的流动性冲击；
#   而 A50/涨跌比/铜油更像"风险偏好"而非"没钱了"，两者不能等权。
SYSTEMIC_LIQ = int(os.environ.get("TELL_SYSTEMIC_LIQ") or 2)   # 流动性组亮 ≥2 ⇒ 系统性
SYSTEMIC_TOTAL = int(os.environ.get("TELL_SYSTEMIC_TOTAL") or 4)  # 总亮 ≥4 ⇒ 系统性
PRESSURE_TOTAL = int(os.environ.get("TELL_PRESSURE_TOTAL") or 2)  # 总亮 ≥2 ⇒ 压力

LEVEL_LABEL = {
    "rotation": "箱体内轮动",
    "pressure": "压力扩散",
    "systemic": "系统性风险",
}


def _num(v) -> Optional[float]:
    """安全取数：None / '' / NaN / 非数字 ⇒ None（**不能回落成 0**）。"""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # NaN 判断


def _now_str() -> str:
    try:
        from app.flash import rules as flash_rules
        return flash_rules.beijing_now().strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def _as_of(panel: dict) -> tuple:
    """数据时点 `(as_of, source)`：优先行情自带时间，取不到才用当前时刻。

    ⚠️ 不能用"现在"冒充"数据时刻" —— 外盘是 24h 的，A 股收盘后
      黄金/美元仍在变，混在一起会让人以为这是同一时点。
    ⇒ 回落时**必须标注来源**，让前端能显示"数据时刻未知"而不是假装精确。
    """
    for k in ("brent", "gold", "usdcnh", "a50"):
        t = str((panel.get(k) or {}).get("time") or "").strip()
        if t:
            return t, "行情时间"
    return _now_str(), "行情源未带时间（以下为当前时刻）"


def _item(tid: str, label: str, group: str, lit: Optional[bool],
          value: Optional[float], threshold: float, text: str,
          unit: str = "%") -> dict:
    """单项结果。`lit is None` ⇒ 数据不足（state=insufficient）。"""
    if lit is None:
        state = "insufficient"
    elif lit:
        state = "lit"
    else:
        state = "quiet"
    return {"id": tid, "label": label, "group": group,
            "state": state, "lit": bool(lit),
            "value": value, "threshold": threshold,
            "unit": unit, "text": text}


def snapshot() -> Dict:
    """跨市场 tell 快照（纯函数式读取，零新增请求）。"""
    try:
        from app.macro import get_macro_panel
        panel = get_macro_panel() or {}
    except Exception as e:
        # 吞异常必须可见（GUARDRAILS #11）
        print(f"[tell-watch] macro panel failed: {str(e)[:80]}")
        panel = {}
    d = panel.get("_derived") or {}

    items: List[dict] = []

    # ① 黄金失守 —— 避险资产也在跌 = 抛售不是为了避险，是为了拿现金
    g = panel.get("gold") or {}
    gv = _num(g.get("change_pct"))
    items.append(_item(
        "gold_break", "黄金失守", "liquidity",
        None if gv is None else (gv <= GOLD_DROP_PCT), gv, GOLD_DROP_PCT,
        ("黄金 %s（%+.2f%%）" % (g.get("price") or "-", gv)) if gv is not None
        else "黄金数据缺失"))

    # ② 人民币急贬 —— USDCNH 上行即人民币贬值，外资定价压力
    cnh = panel.get("usdcnh") or {}
    cv = _num(cnh.get("change_pct"))
    items.append(_item(
        "cnh_weak", "人民币急贬", "liquidity",
        None if cv is None else (cv >= CNH_WEAK_PCT), cv, CNH_WEAK_PCT,
        ("USDCNH %s（%+.2f%%）" % (cnh.get("price") or "-", cv)) if cv is not None
        else "USDCNH 数据缺失"))

    # ③ 美元飙升 —— 全球美元融资挤压
    dx = panel.get("dxy") or {}
    dv = _num(dx.get("change_pct"))
    items.append(_item(
        "dxy_spike", "美元飙升", "liquidity",
        None if dv is None else (dv >= DXY_SPIKE_PCT), dv, DXY_SPIKE_PCT,
        ("美元指数 %s（%+.2f%%）" % (dx.get("price") or "-", dv)) if dv is not None
        else "美元指数数据缺失"))

    # ④ A50 破位 —— 外资定价的中国大盘（⚠️ 期货，含夜盘）
    a5 = panel.get("a50") or {}
    av = _num(a5.get("change_pct"))
    items.append(_item(
        "a50_break", "A50 破位", "risk",
        None if av is None else (av <= A50_BREAK_PCT), av, A50_BREAK_PCT,
        ("A50期货 %s（%+.2f%%，含夜盘）" % (a5.get("price") or "-", av))
        if av is not None else "A50 数据缺失"))

    # ⑤ 涨跌比崩塌 —— 内部宽度，比指数更能反映"是不是只有权重在撑"
    breadth = {}
    try:
        from app.routers.market import _breadth_now
        breadth = _breadth_now() or {}
    except Exception as e:
        print(f"[tell-watch] breadth failed: {str(e)[:80]}")
    up, down = _num(breadth.get("up")), _num(breadth.get("down"))
    ratio = None
    if up is not None and down is not None and (up + down) > 0:
        ratio = round(up / (up + down), 3)
    items.append(_item(
        "breadth", "涨跌比崩塌", "risk",
        None if ratio is None else (ratio < BREADTH_MIN), ratio, BREADTH_MIN,
        ("涨 %d / 跌 %d（涨跌比 %.2f）" % (int(up or 0), int(down or 0), ratio))
        if ratio is not None else "市场宽度缺失",
        unit=""))

    # ⑥ 铜油同跌 —— 上游商品一起跌 = 需求端恐慌（而非供给冲击）
    co, br = panel.get("copper") or {}, panel.get("brent") or {}
    cov, brv = _num(co.get("change_pct")), _num(br.get("change_pct"))
    both_down = None
    if cov is not None and brv is not None:
        # 两者都跌 **且** 至少一端跌得够狠（微跌不算需求恐慌）
        both_down = (cov < 0 and brv < 0
                     and min(cov, brv) <= -COPPER_OIL_MIN_DROP)
    items.append(_item(
        "copper_oil", "铜油同跌", "risk", both_down,
        None if both_down is None else round(min(cov, brv), 2),
        -COPPER_OIL_MIN_DROP,
        ("铜 %+.2f%% / 布伦特 %+.2f%%" % (cov, brv))
        if both_down is not None else "铜或油价数据缺失"))

    # ⑦ 美债收益率跳升 —— 全球贴现率，A 股高估值资产的定价锚
    bp = _num(d.get("us10y_bp_change"))
    items.append(_item(
        "us10y_spike", "美债跳升", "risk",
        None if bp is None else (bp >= US10Y_SPIKE_BP), bp, US10Y_SPIKE_BP,
        ("美债10Y %s（%+.1fbp）" % (panel.get("us10y", {}).get("price") or "-", bp))
        if bp is not None else "美债10Y数据缺失",
        unit="bp"))

    lit = [x for x in items if x["state"] == "lit"]
    insuf = [x for x in items if x["state"] == "insufficient"]
    liq_lit = sum(1 for x in lit if x["group"] == "liquidity")

    # ── 分档 ──
    # 【为什么"全灭"要单独给结论】这是本模块的核心用途：告诉用户
    #   "今天跌得很难看，但恐慌没有扩散"，避免在轮动日误判为系统性风险。
    if liq_lit >= SYSTEMIC_LIQ or len(lit) >= SYSTEMIC_TOTAL:
        level = "systemic"
    elif len(lit) >= PRESSURE_TOTAL:
        level = "pressure"
    else:
        level = "rotation"

    _as_of_val, _as_of_src = _as_of(panel)
    return {
        "ok": True,
        "as_of": _as_of_val,
        "as_of_source": _as_of_src,
        "level": level,
        "level_label": LEVEL_LABEL.get(level, level),
        "lit": len(lit),
        "insufficient": len(insuf),
        "total": len(items),
        "liq_lit": liq_lit,
        "items": items,
        "lit_ids": [x["id"] for x in lit],
        "insufficient_ids": [x["id"] for x in insuf],
        "thresholds": {
            "gold_drop_pct": GOLD_DROP_PCT,
            "cnh_weak_pct": CNH_WEAK_PCT,
            "dxy_spike_pct": DXY_SPIKE_PCT,
            "a50_break_pct": A50_BREAK_PCT,
            "breadth_min": BREADTH_MIN,
            "us10y_spike_bp": US10Y_SPIKE_BP,
            "copper_oil_min_drop": COPPER_OIL_MIN_DROP,
            "systemic_liq": SYSTEMIC_LIQ,
            "systemic_total": SYSTEMIC_TOTAL,
            "pressure_total": PRESSURE_TOTAL,
        },
        "note": ("纯观察器：不进决策链、不改分数。全灭 = 恐慌未扩散（轮动）；"
                 "点亮越多越像流动性问题。A50 为期货含夜盘。"),
    }
