# -*- coding: utf-8 -*-
"""板幅实现 backtest.engine._limit_pct 的业务语义测试（项目唯一板幅来源）。

语义：科创/创业 20%、北交所 30%、ST 5%、ETF/主板 10%。
这是 2026-10-09 涨跌停误报事故的根因修复点：任何"自造板块号段表"都会漏北交所/ST。
"""
from app.backtest.engine import _limit_pct


def test_main_board_10pct():
    assert _limit_pct("600000") == 0.10
    assert _limit_pct("000001") == 0.10
    assert _limit_pct("002594") == 0.10


def test_star_and_chinext_20pct():
    assert _limit_pct("688001") == 0.20
    assert _limit_pct("689009") == 0.20      # 689 = CDR
    assert _limit_pct("300750") == 0.20
    assert _limit_pct("301001") == 0.20
    assert _limit_pct("302001") == 0.20


def test_bse_30pct():
    assert _limit_pct("830001") == 0.30
    assert _limit_pct("430047") == 0.30
    assert _limit_pct("920001") == 0.30


def test_st_5pct_by_name():
    assert _limit_pct("600000", "ST 华仪") == 0.05
    assert _limit_pct("601318", "*ST 某某") == 0.05


def test_chinext_st_keeps_20pct():
    # 创业板/科创板股票（含 ST/*ST）涨跌幅限制仍是 20cm——ST 5% 只适用主板。
    # 实现按"号段优先于名称"判定，与真实规则一致（勿改成 ST 优先）。
    assert _limit_pct("300001", "*ST 某某") == 0.20
    assert _limit_pct("688001", "ST 某某") == 0.20


def test_st_prefix_does_not_override_20cm_without_name():
    # 板幅按"号段 → 名称"顺序：双创号段即使没传名也是 20cm；ST 只看名称
    assert _limit_pct("300001", "") == 0.20


def test_etf_flag_wins():
    assert _limit_pct("510300", is_etf=True) == 0.10
    assert _limit_pct("300001", "X", True) == 0.10   # ETF 参数优先于号段


def test_market_prefix_stripped():
    # 带交易所前缀的代码（len>6 时剥 sh/zb/bj）不改变板幅
    assert _limit_pct("sh600000") == 0.10
    assert _limit_pct("bj830001") == 0.30
    assert _limit_pct("sz300750") == 0.20


def test_empty_code_defaults_10pct():
    assert _limit_pct("") == 0.10
    assert _limit_pct(None) == 0.10
