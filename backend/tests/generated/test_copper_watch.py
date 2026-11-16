# -*- coding: utf-8 -*-
"""铜裁决器 + 关键位动态生成的回归。

背景（2026-11-16 用户："按你建议来"）：
  ① 美铜 `hf_HG` 单位是**美分/磅**（613），归一化成美元/磅（6.13）后
     `copper_oil_ratio` 才从 6.56 变回正常的 0.0656 —— 否则 100 倍错误会喂给 LLM。
  ② `KEY_LEVELS` 硬编码绝对价位（金 4650 而现价 3858、银 84 而现价 46.6）
     已整体失效 ⇒ 改成围绕现价取整档。
  ③ 铜是"裁决者"（油跌时区分供给 D1 / 需求 D2），不是"投票项"。
"""
import unittest.mock as mock

from app import copper_watch as cw
from app.flash import llm


# ── 铜裁决：判据与 prompt 里的 D1/D2 定义必须一致 ──────────────────────────

def test_judge_supply_when_copper_holds():
    """油跌但铜企稳 ⇒ 供给驱动（D1，需求没坏）。"""
    assert cw.judge(-0.6, -2.4) == "supply"
    assert cw.judge(0.8, -2.4) == "supply"


def test_judge_demand_when_copper_crashes():
    """油跌且铜同步暴跌 ⇒ 需求驱动（D2）。"""
    assert cw.judge(-2.5, -2.4) == "demand"
    assert cw.judge(-3.0, -5.0) == "demand"


def test_judge_mixed_in_between():
    """铜跌但没到"暴跌" ⇒ 不硬判（宁可 mixed，也不猜）。"""
    assert cw.judge(-1.2, -2.4) == "mixed"


def test_judge_reflation_and_divergent():
    assert cw.judge(2.5, 2.5) == "reflation"
    # 油涨铜跌 = 成本上行但需求不跟（滞胀形态）
    assert cw.judge(-1.0, 2.5) == "divergent"


def test_judge_neutral_when_oil_quiet():
    """油价没大动 ⇒ 铜单独走不构成裁决（这是"没有信号"，不是"信号是中性"）。"""
    assert cw.judge(1.5, 0.5) == "neutral"
    assert cw.judge(-1.5, -0.3) == "neutral"


def test_judge_missing_is_insufficient():
    """缺失 ≠ 无信号：任一端取不到就必须 abstain。"""
    assert cw.judge(None, -2.4) == "insufficient"
    assert cw.judge(-1.0, None) == "insufficient"
    assert cw.judge(None, None) == "insufficient"


# ── 快照：溢价告警与 confidence ─────────────────────────────────────────

def _panel(**kw):
    base = {
        "copper": {"price": 6.15, "change_pct": -0.6, "time": "11-17 00:01"},
        "lme_copper": {"price": 13314.6, "change_pct": 0.1},
        "brent": {"price": 93.5, "change_pct": -2.4},
        "_derived": {"copper_oil_ratio": 0.0658,
                     "copper_gold_ratio": 0.001593,
                     "copper_premium_pct": 1.76},
    }
    base.update(kw)
    return base


def test_snapshot_supply_case():
    with mock.patch("app.macro.get_macro_panel", return_value=_panel()):
        r = cw.snapshot()
    assert r["verdict"] == "supply"
    assert r["confidence"] == "low"       # 单日判据 ⇒ 永远不装"高置信"
    assert r["copper"]["us"]["price"] == 6.15
    assert "供给端" in r["reason"]


def test_snapshot_flags_abnormal_premium():
    """美铜溢价 >5% ⇒ 必须提示"不能当全球需求读"。"""
    p = _panel()
    p["_derived"]["copper_premium_pct"] = 8.5
    with mock.patch("app.macro.get_macro_panel", return_value=p):
        r = cw.snapshot()
    assert "不可直接当全球需求读" in r["reason"]


def test_snapshot_missing_data_abstains():
    with mock.patch("app.macro.get_macro_panel",
                    return_value={"copper": {}, "brent": {}, "_derived": {}}):
        r = cw.snapshot()
    assert r["verdict"] == "insufficient"
    assert r["confidence"] == "none"


# ── 关键位：动态生成必须夹住现价 ────────────────────────────────────────

def test_auto_levels_brackets_price():
    lv = llm._auto_levels(3858, 50)
    assert lv == [3800, 3850, 3900, 3950]
    above = [x for x in lv if x > 3858]
    below = [x for x in lv if x < 3858]
    assert min(above) == 3900
    assert max(below) == 3850


def test_auto_levels_when_price_on_exact_level():
    """现价正好落在整档时，不能把自己当成支撑/阻力。"""
    lv = llm._auto_levels(3850, 50)
    assert max(x for x in lv if x < 3850) == 3800
    assert min(x for x in lv if x > 3850) == 3900


def test_auto_levels_never_stale():
    """★ 核心修复：金 4650 / 银 84 那类过期硬编码不会再出现。"""
    # 银现价 46.66，步长 1 ⇒ 档位必须在现价附近，而不是 84
    lv = llm._auto_levels(46.66, 1)
    assert all(abs(x - 46.66) <= 3 for x in lv)
    # 铜归一化后 6.15（美元/磅），步长 0.25
    lv2 = llm._auto_levels(6.15, 0.25)
    assert min(lv2) > 5 and max(lv2) < 7


def test_level_step_registered_for_copper():
    """铜与伦铜都必须有步长，否则关键位整段消失。"""
    assert "copper" in llm.LEVEL_STEP
    assert "lme_copper" in llm.LEVEL_STEP
