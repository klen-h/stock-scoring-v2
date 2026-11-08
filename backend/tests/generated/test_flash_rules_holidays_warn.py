# -*- coding: utf-8 -*-
"""BK-54b 跨年 HOLIDAYS 覆盖告警：**每进程（每年）只打一次**。

2026-11-09 实测事故：原实现
    `_done = getattr(fn, "_done", set())`
属性不存在时返回的是**临时空 set**，`_done.add(yr)` 从未写回函数对象
⇒ 每次调用都拿到新 set ⇒ 限频完全失效。`is_trading_day` 被调度器高频调用，
2027 年起会每次都打告警、日志刷爆。本测试锁死这个行为。
"""
import contextlib
import io
from datetime import datetime

from app.flash import rules

_FN = rules._warn_holidays_coverage


def _reset():
    """每个用例前清掉模块级限频状态。"""
    if hasattr(_FN, "_done"):
        del _FN._done


def _capture(year):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _FN(datetime(year, 1, 4, 10, 0))
    return buf.getvalue()


def test_missing_year_warns_only_once():
    _reset()
    assert "WARNING" in _capture(2027)
    assert _capture(2027) == ""      # 第二次必须静默
    assert _capture(2027) == ""      # 第三次也必须静默


def test_limit_state_is_persisted_on_function():
    """★ 回归核心：状态必须真的挂在函数对象上，不能只是临时 set。"""
    _reset()
    assert not hasattr(_FN, "_done")
    _capture(2027)
    assert hasattr(_FN, "_done"), "限频状态没写回 ⇒ 每次都会重新告警"
    assert 2027 in _FN._done


def test_each_missing_year_warns_once():
    _reset()
    assert "WARNING" in _capture(2027)
    assert "WARNING" in _capture(2028)   # 另一个缺年份仍应告警
    assert _capture(2028) == ""


def test_covered_year_never_warns():
    _reset()
    assert _capture(2026) == ""       # HOLIDAYS 已维护到 2026
    assert _capture(2026) == ""


def test_invalid_day_str_does_not_warn():
    _reset()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _FN(None)
        _FN("")
    assert buf.getvalue() == ""
