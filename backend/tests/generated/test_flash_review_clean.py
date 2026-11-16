# -*- coding: utf-8 -*-
"""三时段复盘卡：盘前无前序时的 `prev_check` 占位文案不得展示。

2026-11-16 用户贴出卡片内容，盘前那条显示
「验证 输入未含前序判断，无需比对」—— 这不是验证结论，
只是 LLM 没遵守"无前序则留空"的指令硬凑的一句话。
"""
import unittest.mock as mock

from app import flash_brief as fb


def test_clean_prev_filters_placeholder():
    for t in ("输入未含前序判断，无需比对", "无前序判断", "没有前序判断",
              "暂无前序", "首次解读"):
        assert fb._clean_prev(t) is None, t
    for t in (None, "", "  ", "null", "None", "-"):
        assert fb._clean_prev(t) is None, t


def test_clean_prev_keeps_real_verification():
    real = "与盘前一致：科创50 -4.95% 仍领跌，研报利好未兑现"
    assert fb._clean_prev(real) == real
    # 真实文案里即使出现"无需重复"这种词也不该被误杀
    assert fb._clean_prev("与盘前一致，无需重复核对") == "与盘前一致，无需重复核对"


def test_review_summary_cleans_loaded_rows():
    """历史 run 已落库，光改 prompt 不够 ⇒ 读路径必须清洗。"""
    rows = [
        {"phase": "盘前", "ts": "2026-11-16 09:27:00",
         "conclusion": "开盘普跌", "stance": "防御", "verdict": "命中",
         "bullish": None, "bearish": None,
         "prev_check": "输入未含前序判断，无需比对"},
        {"phase": "盘中", "ts": "2026-11-16 14:32:00",
         "conclusion": "普跌延续", "stance": "防御", "verdict": None,
         "bullish": None, "bearish": None,
         "prev_check": "与盘前一致：科创50仍领跌"},
    ]
    with mock.patch("app.database.db.fetch", return_value=rows):
        out = fb.review_summary("2026-11-16")

    pre = next(p for p in out["phases"] if p["phase"] == "盘前")
    mid = next(p for p in out["phases"] if p["phase"] == "盘中")
    assert pre["prev_check"] is None
    assert mid["prev_check"] == "与盘前一致：科创50仍领跌"

    # 盘后无 run ⇒ 不编造
    post = next(p for p in out["phases"] if p["phase"] == "盘后")
    assert post["conclusion"] is None
