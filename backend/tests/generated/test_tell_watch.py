# -*- coding: utf-8 -*-
"""跨市场危险信号（tell）检测器回归。

背景（2026-11-16 用户："可以"）：把盘后分析里的 tell 清单固化成常驻检查。
核心语义是**缺席判据**：
  全灭 = 恐慌没有扩散（箱体内轮动，崩的只是高久期资产）；
  点亮越多 = 越像流动性问题（连本该对冲的黄金/人民币/美元也在异动）。
"""
import unittest.mock as mock

from app import tell_watch as tw


def _panel(**kw):
    base = {
        "gold": {"price": 3900.0, "change_pct": 0.10, "time": "11-16 15:00"},
        "usdcnh": {"price": 6.66, "change_pct": 0.00},
        "dxy": {"price": 103.0, "change_pct": 0.00},
        "a50": {"price": 12700.0, "change_pct": 0.00},
        "copper": {"price": 4.20, "change_pct": -0.20},
        "brent": {"price": 93.0, "change_pct": -0.30},
        "us10y": {"price": 5.10},
        "_derived": {"us10y_bp_change": -2.0},
    }
    base.update(kw)
    return base


def _breadth(up=2000, down=1000):
    return {"up": up, "down": down, "total": up + down}


def _run(panel, breadth=None):
    with mock.patch("app.macro.get_macro_panel", return_value=panel), \
            mock.patch("app.routers.market._breadth_now",
                       return_value=breadth if breadth is not None else _breadth()):
        return tw.snapshot()


def test_all_quiet_is_rotation():
    """全灭 ⇒ 箱体内轮动。这是本模块最重要的输出：跌得难看≠要崩。"""
    r = _run(_panel())
    assert r["level"] == "rotation"
    assert r["lit"] == 0
    assert r["level_label"] == "箱体内轮动"


def test_liquidity_double_is_systemic():
    """黄金 + 美元同时异动 = 流动性冲击 ⇒ 直接判系统性（不必凑够总项数）。"""
    r = _run(_panel(
        gold={"price": 3700.0, "change_pct": -2.6, "time": "11-16"},
        dxy={"price": 105.0, "change_pct": 1.4}))
    assert r["level"] == "systemic"
    assert r["liq_lit"] == 2
    assert set(r["lit_ids"]) == {"gold_break", "dxy_spike"}


def test_two_risk_tells_is_pressure():
    """风险偏好组亮 2 项（但流动性组没亮）⇒ 压力扩散，不升级为系统性。"""
    r = _run(_panel(a50={"price": 12300.0, "change_pct": -2.5}),
             breadth=_breadth(up=400, down=2600))
    # a50 破位 + 涨跌比崩塌 = 2 项
    assert r["lit"] >= 2
    assert r["level"] == "pressure"
    assert r["liq_lit"] == 0


def test_missing_data_is_insufficient_not_quiet():
    """★ 缺失 ≠ 未触发：取不到数据的项必须单列，**不能计为安全**。

    这是项目反复踩的坑（`macro_regime_class` / `macro_cycle` 都明文禁止）。
    """
    r = _run({"_derived": {}}, breadth={})
    assert r["insufficient"] >= 5
    assert r["lit"] == 0
    # 全缺时不能因此判"轮动（安全）"让人误读，但至少不能把缺失当 quiet
    states = {x["id"]: x["state"] for x in r["items"]}
    assert states["gold_break"] == "insufficient"
    assert states["breadth"] == "insufficient"


def test_copper_oil_needs_significant_drop():
    """铜油微跌不算需求恐慌（否则这条 tell 天天亮，变噪音）。"""
    mild = _run(_panel(copper={"price": 4.2, "change_pct": -0.1},
                       brent={"price": 93.0, "change_pct": -0.2}))
    item = next(x for x in mild["items"] if x["id"] == "copper_oil")
    assert item["state"] == "quiet"

    heavy = _run(_panel(copper={"price": 4.1, "change_pct": -1.8},
                        brent={"price": 90.0, "change_pct": -2.4}))
    item2 = next(x for x in heavy["items"] if x["id"] == "copper_oil")
    assert item2["state"] == "lit"


def test_as_of_marks_source_when_panel_has_no_time():
    """外盘 24h 在动 ⇒ 不能用"现在"冒充"数据时刻"，必须标注来源。"""
    r1 = _run(_panel())
    assert r1["as_of"] == "11-16 15:00"
    assert r1["as_of_source"] == "行情时间"

    panel = _panel()
    for k in ("gold", "usdcnh", "dxy", "a50"):
        panel[k].pop("time", None)
    r2 = _run(panel)
    assert "未带时间" in r2["as_of_source"]


def test_thresholds_exposed_for_transparency():
    """阈值必须如实暴露（与 `cross_market_watch` 同纪律：可核对、可调）。"""
    r = _run(_panel())
    th = r["thresholds"]
    for k in ("gold_drop_pct", "cnh_weak_pct", "dxy_spike_pct",
              "a50_break_pct", "breadth_min", "us10y_spike_bp",
              "copper_oil_min_drop"):
        assert k in th, k
