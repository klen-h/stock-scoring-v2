# -*- coding: utf-8 -*-
"""详情页口径对齐回归（Guardrails #5：支撑阻力/买点位置只描述，无指令式建议）。

背景（2026-10-25，601398 实例）：评分卡"观望+风险清单"与支撑阻力模块
"可分批建仓"同屏矛盾——后者是 2026-09 前免检老逻辑，从未检验。
本测试锁：新文案全部为位置事实，无 买入/建仓/减仓/介入/试探/持有 指令词。
"""
from app.strategies.support_resistance import _generate_suggestion
from app.scoring.engine import ScoreEngine

_BANNED = ("建仓", "减仓", "介入", "试探", "持有", "买入", "卖出", "追高", "止盈")


def _levels():
    return [
        {"price": 100.0, "type": "resistance", "strength": "strong"},
        {"price": 90.0, "type": "resistance", "strength": "medium"},
        {"price": 80.0, "type": "support", "strength": "strong"},
        {"price": 70.0, "type": "support", "strength": "medium"},
    ]


def test_suggestion_all_bands_no_directives():
    # 7 个位置档全覆盖（price=85 固定，position_pct 0~100 扫档）
    for pct in (0, 10, 30, 40, 60, 80, 85, 95, 100):
        s = _generate_suggestion(85.0, pct, _levels())
        for w in _BANNED:
            assert w not in s, (pct, s, w)


def test_suggestion_describes_position_not_action():
    s_bottom = _generate_suggestion(85.0, 10, _levels())
    assert "底部" in s_bottom and "支撑" in s_bottom
    s_top = _generate_suggestion(85.0, 95, _levels())
    assert "高位" in s_top and "阻力" in s_top


def test_buy_timing_no_directives():
    eng = ScoreEngine.__new__(ScoreEngine)   # 只用静态方法，不初始化权重
    # 构造 40 根 K：价格远离支撑（deviation > 6%）
    tech_far = [{"close": 10.0 + i * 0.3, "low": 9.5, "ma20": 11.0, "ma60": 10.0,
                 "boll_upper": 13, "boll_mid": 11, "boll_lower": 9} for i in range(40)]
    r = eng._calc_buy_point(tech_far)
    for w in _BANNED:
        assert w not in r["buy_timing"], (r["buy_timing"], w)
    # 事实语义在位：偏离度数字与支撑位明细保留
    assert r["deviation"] is not None and r["supports"]


def test_buy_timing_bands_are_factual():
    eng = ScoreEngine.__new__(ScoreEngine)
    tech = [{"close": 12.0, "low": 11.8, "ma20": 11.5, "ma60": 11.0,
             "boll_upper": 13, "boll_mid": 12, "boll_lower": 11} for i in range(40)]
    r = eng._calc_buy_point(tech)
    assert r["buy_timing"] in ("价格处于支撑区内", "贴近支撑位（≤2%）",
                               "高于支撑位 2%~6%", "高于支撑位 6% 以上",
                               "价格处于低位区（无可参考支撑）")
