# -*- coding: utf-8 -*-
"""app/flash/rules.py（市场时钟/时间口径/快讯过滤，自述"纯函数、无 IO"）业务语义测试。

语义锚点：
  - 全项目时间戳统一北京时间（tz-aware，UTC+8）；naive 历史值按 UTC 解释（2026-09-12 统一口径）。
  - 「最近已完成交易日」= 当日 15:00 后且为交易日才含当天；跳过周末 + 法定节假日。
    （自然日 ≠ 交易日：数据包日期对不上、信号被打"周六"标签等 3 次事故的根因口径。）
  - is_trading_day 只判周末+节假日，不判盘中时段（盘后复盘窗口依赖此语义）。
"""
from datetime import datetime, timedelta, timezone

import pytest

from app.flash import rules
from app.flash.rules import (
    HOLIDAYS, beijing_now, beijing_now_iso, get_china_market_status,
    is_sector_move, is_trading_day, latest_completed_trading_day, to_beijing,
)

TZ8 = timezone(timedelta(hours=8))


# ---------- 北京时间统一口径 ----------

def test_beijing_now_is_aware_utc8():
    now = beijing_now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(hours=8)


def test_beijing_now_iso_has_offset():
    assert "+08:00" in beijing_now_iso()


def test_to_beijing_naive_interpreted_as_utc():
    # naive 历史值按 UTC 解释再换算（新旧数据同一把尺子）
    got = to_beijing("2026-10-24T02:00:00")
    assert got == datetime(2026, 10, 24, 10, 0, tzinfo=TZ8)


def test_to_beijing_aware_converted():
    got = to_beijing("2026-10-24T02:00:00+00:00")
    assert got == datetime(2026, 10, 24, 10, 0, tzinfo=TZ8)


def test_to_beijing_unchanged_when_already_bj():
    got = to_beijing("2026-10-24T10:00:00+08:00")
    assert got == datetime(2026, 10, 24, 10, 0, tzinfo=TZ8)


def test_to_beijing_garbage_is_none():
    assert to_beijing(None) is None
    assert to_beijing("不是时间") is None
    assert to_beijing(12345) is None


# ---------- 交易日 / 最近已完成交易日 ----------

def test_weekend_not_trading_day():
    assert is_trading_day(datetime(2026, 10, 10, 10, 0)) is False   # 周六
    assert is_trading_day(datetime(2026, 10, 11, 10, 0)) is False   # 周日


def test_holiday_not_trading_day():
    assert is_trading_day(datetime(2026, 10, 3, 10, 0)) is False    # 国庆 10-01~10-07
    assert is_trading_day(datetime(2026, 9, 26, 10, 0)) is False    # 中秋 9-25~9-27
    assert is_trading_day(datetime(2026, 1, 1, 10, 0)) is False     # 元旦


def test_weekday_is_trading_day():
    assert is_trading_day(datetime(2026, 10, 9, 10, 0)) is True     # 周五（非假期）


def test_latest_completed_trading_day_after_close_includes_today():
    # 周五 20:43（日批正常时点）→ 当天就是最近已完成交易日
    assert latest_completed_trading_day(datetime(2026, 10, 9, 20, 43)) == "2026-10-09"


def test_latest_completed_trading_day_intraday_goes_back():
    # 周五盘中/盘前 09:00 → 当日 K 线不存在，必须用上一交易日（周四）
    assert latest_completed_trading_day(datetime(2026, 10, 9, 9, 0)) == "2026-10-08"


def test_latest_completed_trading_day_weekend():
    # 周日晚 20:00 → 最近已完成交易日是周五
    assert latest_completed_trading_day(datetime(2026, 10, 11, 20, 0)) == "2026-10-09"


def test_latest_completed_trading_day_holiday_skips():
    # 国庆假期中（周六 10-03）→ 9-30（假期前最后一个交易日）
    assert latest_completed_trading_day(datetime(2026, 10, 3, 20, 0)) == "2026-09-30"


# ---------- A 股交易状态（含节假日 + 盘中时段）----------

def test_market_status_segments(monkeypatch):
    cases = [
        (datetime(2026, 10, 9, 9, 0, tzinfo=TZ8), False),    # 未开盘 9:15 前
        (datetime(2026, 10, 9, 9, 20, tzinfo=TZ8), True),    # 集合竞价
        (datetime(2026, 10, 9, 10, 30, tzinfo=TZ8), True),   # 上午
        (datetime(2026, 10, 9, 12, 0, tzinfo=TZ8), False),   # 午间休市
        (datetime(2026, 10, 9, 14, 0, tzinfo=TZ8), True),    # 下午
        (datetime(2026, 10, 9, 14, 58, tzinfo=TZ8), True),   # 收盘集合竞价
        (datetime(2026, 10, 9, 15, 30, tzinfo=TZ8), False),  # 已收盘
        (datetime(2026, 10, 10, 10, 30, tzinfo=TZ8), False), # 周末
        (datetime(2026, 10, 2, 10, 30, tzinfo=TZ8), False),  # 国庆假期
    ]
    for dt, want_open in cases:
        monkeypatch.setattr(rules, "beijing_now", lambda dt=dt: dt)
        st = rules.get_china_market_status()
        assert st["is_open"] is want_open, (dt, st)


# ---------- 板块级异动识别（快讯过滤）----------

def test_sector_move_detected():
    assert is_sector_move("半导体股集体走高") is True
    assert is_sector_move("港股午后涨超2%") is True
    assert is_sector_move("某板块涨幅扩大至5%") is True


def test_single_stock_news_is_not_sector_move():
    assert is_sector_move("某公司中标国家电网项目") is False
    assert is_sector_move("某公司一季度净利润10亿元") is False


def test_holidays_table_covers_2026():
    assert 2026 in HOLIDAYS
    assert any(name == "国庆" for _, _, name in HOLIDAYS[2026])
