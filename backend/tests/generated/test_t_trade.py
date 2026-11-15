# -*- coding: utf-8 -*-
"""做T 支撑/阻力预设（`app.t_trade`）的单元测试。

背景（2026-11-15）：
  用户要"以 000429 为例"做量化做T，明确两点：
    · **反T 不用全仓**（默认 1/3 底仓，可配）；
    · 先做价值股，但**为未来高波动股铺路**。

  000429 实测（338 个交易日）暴露了原设计的硬伤：
    支撑→阻力空间平均 **3.58%**，而当日振幅平均只有 **1.93%**
    ⇒ 109 天"有空间"却**两端同时触及 0 天**。
  根因是"前高/前低"天然离现价 1.5~2.7%，对低波动价值股太远。
  ⇒ 距离上限改为受 ATR 约束，并新增 `现价 ± m×ATR` 的近端兜底候选。

  本文件锁住这些口径，防止以后被"优化"回去。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import t_trade


def _bars(n=80, base=10.0, step=0.02):
    """平滑上涨的日线（ATR 很小）——用于测"空间不足"。"""
    out = []
    for i in range(n):
        c = base + i * step
        out.append({"date": "2026-01-%02d" % (i % 28 + 1),
                    "high": round(c + 0.05, 3), "low": round(c - 0.05, 3),
                    "close": round(c, 3)})
    return out


def _volatile(n=80, base=10.0, amp=0.35):
    """上下大幅震荡的日线（ATR 大）——用于测"空间足够"。"""
    out = []
    for i in range(n):
        c = base + (amp if i % 2 else -amp)
        out.append({"date": "2026-01-%02d" % (i % 28 + 1),
                    "high": round(c + amp, 3), "low": round(c - amp, 3),
                    "close": round(c, 3)})
    return out


def _mild(n=80, base=10.0, amp=0.08):
    """温和震荡（ATR% ≈ 2%，与 000429 这类价值股同量级）——用于测"两端都有位"。"""
    out = []
    for i in range(n):
        c = base + (amp if i % 2 else -amp)
        out.append({"date": "2026-01-%02d" % (i % 28 + 1),
                    "high": round(c + amp, 3), "low": round(c - amp, 3),
                    "close": round(c, 3)})
    return out


# ── 基础指标 ───────────────────────────────────────────────────────────

def test_atr_positive_and_scales_with_range():
    """ATR 应随振幅增大；数据不足时返回 0（不抛异常）。

    ⚠️ 用 80 根：默认 `T_ATR_PERIOD=60`（见模块注释：14 日会被近期异动打高，
       做T 判定要用常态波动），数据量必须 > period+1。
    """
    small = t_trade.atr(_bars(80, step=0.02))
    big = t_trade.atr(_volatile(80))
    assert small > 0 and big > 0
    assert big > small * 3, "震荡序列的 ATR 必须显著大于平滑序列"
    assert t_trade.atr([]) == 0.0
    assert t_trade.atr(_bars(3)) == 0.0          # 不足 period+1


def test_round_levels_near_price():
    """整数位只给最近的两个，且步长按价格量级放大（避免 13 元股给 13.00 这种远位）。"""
    lv = t_trade.round_levels(13.79)
    assert len(lv) == 2
    assert all(12.0 <= x <= 15.0 for x in lv)
    hi = t_trade.round_levels(260.0)
    assert all(x > 0 for x in hi)


# ── 支撑 / 阻力 ────────────────────────────────────────────────────────

def test_support_below_price_resistance_above():
    """支撑必须严格低于参照价、阻力严格高于 —— 否则"买在支撑"无从谈起。

    ⚠️ 用**温和**震荡序列（AMP 0.08）：极端的 `_volatile(amp=0.35)` ATR% 会到 ~14%，
       所有候选位都超出 3% 上限 ⇒ 两端为 None 是**正确行为**，不是 bug
       （高波动股本来就该"没有近端位可分"，只能靠触发层放宽或换标的）。
    """
    bars = _mild(80)
    px = 10.0
    lv = t_trade.compute_levels(bars, px)
    assert lv["support"] is not None and lv["resistance"] is not None
    assert lv["support"] < px < lv["resistance"]


def test_extreme_vol_has_no_near_levels():
    """极端波动下允许两端为 None —— 但必须给原因（不能静默返回空）。"""
    lv = t_trade.compute_levels(_volatile(80), 10.0)
    if lv["support"] is None or lv["resistance"] is None:
        assert lv["reason"], "无候选时必须说明原因"


def test_levels_exclude_today():
    """★ 候选位必须来自**不含今日**的历史（今日高低是结果，不是原因）。

    构造：历史平稳在 10 附近，今日突然插针到 20 —— 若把今日算进候选，
    阻力会跑到 20；正确实现应忽略今日。
    """
    bars = _bars(80, base=10.0, step=0.0)
    bars.append({"date": "2026-12-31", "high": 20.0, "low": 9.0, "close": 19.0})
    lv = t_trade.compute_levels(bars, price=10.0)
    res_c = [c["value"] for c in lv["candidates"]["resistance"]]
    assert all(v < 20 for v in res_c), "今日最高 20 不该出现在阻力候选里"


def test_distance_cap_follows_atr():
    """★ 距离上限受 ATR 约束：低波动股收紧，高波动股放宽到绝对上限。"""
    low = t_trade.compute_levels(_bars(80, step=0.01))
    high = t_trade.compute_levels(_volatile(80))
    assert low["max_dist_pct"] <= t_trade.T_MAX_DIST_PCT
    assert high["max_dist_pct"] >= low["max_dist_pct"], "波动越大，允许的距离应越宽"


def test_atr_candidates_present_when_history_far():
    """历史位都在 ATR 之外时，`ATR×m` 近端候选必须顶上来（否则永远无解）。"""
    bars = _bars(80, base=10.0, step=0.05)       # 单边上涨，前低远在下方
    lv = t_trade.compute_levels(bars, price=bars[-1]["close"])
    types = [c["type"] for c in lv["candidates"]["support"]]
    assert any(t.startswith("ATR×") for t in types), \
        "近端 ATR 候选缺失 ⇒ 低波动股会再次出现 0 触发（000429 的老问题）"


# ── 仓位（反T 不用全仓）────────────────────────────────────────────────

def test_suggest_ratio_default_one_third():
    """默认 1/3 —— 用户明确"不一定全仓"，即使接不回来最多丢 1/3 底仓。

    ⚠️ 实现里 `round(ratio, 3)` 是有意的（界面/日志可读）⇒ 1/3 变 0.333，
       容差取 1e-3，不要用 1e-6 卡死。
    """
    assert abs(t_trade.suggest_ratio(1.5)["ratio"] - 1 / 3) < 1e-3


def test_suggest_ratio_scales_down_on_high_vol():
    """高波动自动缩量（为未来高波动股铺路），且不低于下限。"""
    normal = t_trade.suggest_ratio(t_trade.T_HIGH_VOL_ATR_PCT - 0.5)["ratio"]
    high = t_trade.suggest_ratio(t_trade.T_HIGH_VOL_ATR_PCT + 3)["ratio"]
    assert high < normal
    assert high >= t_trade.T_RATIO_MIN


def test_suggest_ratio_clamped():
    """传入越界比例要被夹住（0.05 不许，1.5 也不许）。"""
    assert t_trade.suggest_ratio(1.0, base=0.01)["ratio"] == t_trade.T_RATIO_MIN
    assert t_trade.suggest_ratio(1.0, base=5.0)["ratio"] == t_trade.T_RATIO_MAX


# ── 完整计划 ───────────────────────────────────────────────────────────

def test_plan_not_feasible_when_space_too_narrow():
    """空间过薄 ⇒ feasible=False，且给出可读原因（不是静默返回）。

    ⚠️ 必须用 step=0.001（ATR% ≈ 0.1%）：这样连 `ATR×1` 候选都落在 0.3% 下限之内，
       两端都被滤掉 ⇒ 真正"无位可分"。
       用 step=0.005 会 feasible=True —— 因为 `ATR×1` 候选给到 ±0.48%，
       空间 0.96%，本来就在门槛之上（这是**正确**的，不是 bug）。
    """
    p = t_trade.build_plan("000001", "测试", _bars(80, step=0.001))
    assert p["feasible"] is False
    assert p["reason"], "不可行必须有原因"


def test_min_space_pct_guard():
    """★ 绝对百分比下限必须真的起作用（防 `ATR×m` 候选"自我满足"门槛）。

    构造 ATR% ≈ 1.6% 的温和震荡：`ATR×1` 候选给到 ±1.6%，空间约 3.2%，
    但若把 `T_MIN_SPACE_PCT` 调到 5% 就应该判不可行 —— 证明这条不是摆设。
    """
    bars = _mild(80)
    ok = t_trade.build_plan("000429", "x", bars)
    old = t_trade.T_MIN_SPACE_PCT
    try:
        t_trade.T_MIN_SPACE_PCT = 50.0      # 故意拉到不可达
        blocked = t_trade.build_plan("000429", "x", bars)
    finally:
        t_trade.T_MIN_SPACE_PCT = old
    if ok["feasible"]:
        assert blocked["feasible"] is False, "百分比下限被绕过 ⇒ 门槛形同虚设"
        assert "太薄" in blocked["reason"] or "空间" in blocked["reason"]


def test_plan_feasible_with_space_and_fields_complete():
    """空间足够时 feasible=True，且关键字段齐全（供触发层直接消费）。"""
    for bars in (_volatile(80), _volatile(80, base=30.0, amp=1.2)):
        p = t_trade.build_plan("000429", "粤高速A", bars)
        if not p["feasible"]:
            continue
        for k in ("support", "resistance", "atr", "space_abs", "space_pct",
                  "space_atr_mult", "ratio", "as_of"):
            assert p.get(k) is not None, "可行计划缺字段 %s" % k
        assert p["support"] < p["price"] < p["resistance"]
        assert p["space_atr_mult"] >= t_trade.T_MIN_SPACE_ATR
        assert 0 < p["ratio"] <= 1


def test_plan_handles_insufficient_data():
    """数据不足要返回不可行 + 原因，不能抛异常（触发层会天天调它）。"""
    p = t_trade.build_plan("000429", "x", [])
    assert p["feasible"] is False and p["reason"]
    p2 = t_trade.build_plan("000429", "x", _bars(3))
    assert p2["feasible"] is False and p2["reason"]


# ── 方向选择（用户："正T/反T 都支持，按持仓与资金自动判断"）──────────────

def test_resolve_direction_defaults_to_long_only():
    """★ 默认**只做正T**。

    2026-11-15 回测（19 只 × 400 天）：正T 1457 次均值 **+0.122%**，
    反T 1410 次均值 **-0.046%**（负期望，90% 的卖出在 5 日内被更高价越过）。
    ⇒ 反T 默认关闭，仅 `T_ALLOW_SHORT_T=1` 时开启。
    """
    assert t_trade.resolve_direction(True, True) == ["long_t"]
    assert t_trade.resolve_direction(True, False) == []
    assert t_trade.resolve_direction(False, True) == ["long_t"]
    assert t_trade.resolve_direction(False, False) == []


def test_resolve_direction_short_t_when_enabled(monkeypatch):
    """显式开启反T 后才允许多方向（有底仓时反T 可用）。"""
    monkeypatch.setattr(t_trade, "_ALLOW_SHORT_T", True)
    assert t_trade.resolve_direction(True, True) == ["long_t", "short_t"]
    assert t_trade.resolve_direction(True, False) == ["short_t"]
    # 有资金仍优先正T（顺序即优先级）
    assert t_trade.resolve_direction(False, True) == ["long_t"]


# ── 股数（★ 反T 不用全仓 + T+1 扣减）──────────────────────────────────

def test_calc_shares_short_t_never_full_position():
    """★ 用户明确"不一定使用全仓"：默认 1/3 底仓，接不回来最多丢 1/3。"""
    sh = t_trade.calc_shares("short_t", 10.0, base_shares=10000, cash=0, ratio=1 / 3)
    assert sh == 3300, "10000 × 1/3 取整到 100 股应为 3300"
    assert sh < 10000, "绝不能卖光底仓"


def test_calc_shares_short_t_deducts_today_bought():
    """T+1：今日买入的部分不可卖，必须从可卖里扣掉。"""
    sh = t_trade.calc_shares("short_t", 10.0, base_shares=10000, cash=0,
                             ratio=1 / 3, today_bought=3000)
    # (10000 − 3000) × 1/3 = 2333 → 向下取整到 2300
    assert sh == 2300


def test_calc_shares_short_t_zero_when_all_bought_today():
    """今日买入已占满底仓 ⇒ 可卖 0（绝不能卖成"负的"）。"""
    assert t_trade.calc_shares("short_t", 10.0, 10000, 0, 1 / 3,
                               today_bought=10000) == 0
    assert t_trade.calc_shares("short_t", 10.0, 10000, 0, 1 / 3,
                               today_bought=20000) == 0


def test_calc_shares_long_t_uses_cash_when_no_position():
    """**无底仓**时只能用资金口径（此时没有"底仓"可言）。"""
    # 50000 × 1/3 = 16666.7 元，价 10 元 ⇒ 1666 股 → 1600
    assert t_trade.calc_shares("long_t", 10.0, 0, cash=50000, ratio=1 / 3) == 1600
    assert t_trade.calc_shares("long_t", 10.0, 0, cash=0, ratio=1 / 3) == 0
    assert t_trade.calc_shares("long_t", 0, 0, cash=50000, ratio=1 / 3) == 0


def test_calc_shares_long_t_capped_by_position():
    """★ 有底仓时**以底仓为基准**（2026-11-15 修正）。

    原来的实现只按 `cash × ratio / price` 算，会算出与底仓完全不匹配的量：
    实测底仓 200 股时正T 却买 2600 股（13 倍底仓）—— 那不是做T，是建仓。
    """
    # 底仓 300 股 × 1/3 = 100 股；资金上限 50000×1/3/10 = 1666 股 ⇒ 取小 = 100
    assert t_trade.calc_shares("long_t", 10.0, 300, cash=50000, ratio=1 / 3) == 100
    # 底仓 3000 股 × 1/3 = 1000 股；资金上限 1666 股 ⇒ 取小 = 1000
    assert t_trade.calc_shares("long_t", 10.0, 3000, cash=50000, ratio=1 / 3) == 1000
    # 资金很紧时资金成为约束：1000 元 × 1/3 / 10 = 33 股 → 0
    assert t_trade.calc_shares("long_t", 10.0, 3000, cash=1000, ratio=1 / 3) == 0


def test_calc_shares_long_t_zero_when_position_too_small():
    """★ 底仓不足 300 股时 1/3 仓取整为 0 —— **A 股最小 100 股，做不了**。

    实测当前持仓：工行 100 / 粤高速 200 / 内蒙一机 100 股 ⇒ 全部做不了。
    这不是 bug，是"仓位太小，摊薄成本没有意义"。
    """
    for base in (100, 200, 299):
        assert t_trade.calc_shares("long_t", 10.0, base, cash=50000, ratio=1 / 3) == 0


# ── 触发判断（开仓 / 平仓 / 止损）─────────────────────────────────────

def _p(sup=10.0, res=10.5, atr=0.2):
    return {"support": sup, "resistance": res, "atr": atr,
            "support_type": "前低", "resistance_type": "前高"}


def test_trigger_open_long_when_touching_support():
    """价格贴到支撑 ⇒ 正T 开仓（容差 0.2%）。"""
    r = t_trade.check_trigger(_p(), 10.01)
    assert r and r["direction"] == "long_t" and r["kind"] == "open"


def test_trigger_open_short_when_touching_resistance():
    """价格贴到阻力 ⇒ 反T 开仓。"""
    r = t_trade.check_trigger(_p(), 10.49)
    assert r and r["direction"] == "short_t" and r["kind"] == "open"


def test_trigger_none_in_middle():
    """中间价不触发（否则会天天开仓）。"""
    assert t_trade.check_trigger(_p(), 10.25) is None


def test_trigger_close_and_stop_for_long_t():
    """持正T：到阻力兑现；跌破 支撑−0.5×ATR 止损。"""
    close = t_trade.check_trigger(_p(), 10.49, open_side="long_t")
    assert close and close["kind"] == "close"
    stop = t_trade.check_trigger(_p(), 9.85, open_side="long_t")   # 10 − 0.1 = 9.9
    assert stop and stop["kind"] == "stop"


def test_trigger_close_and_stop_for_short_t():
    """持反T：回落到支撑接回；涨破 阻力+0.5×ATR 认错。"""
    close = t_trade.check_trigger(_p(), 10.01, open_side="short_t")
    assert close and close["kind"] == "close"
    stop = t_trade.check_trigger(_p(), 10.65, open_side="short_t")  # 10.5 + 0.1 = 10.6
    assert stop and stop["kind"] == "stop"


def test_trigger_handles_missing_levels():
    """预案缺位（不可行的票）⇒ 一律不触发，不能抛异常。"""
    assert t_trade.check_trigger({}, 10.0) is None
    assert t_trade.check_trigger({"support": None, "resistance": 10.5}, 10.0) is None
