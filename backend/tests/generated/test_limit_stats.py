# -*- coding: utf-8 -*-
"""涨跌停口径唯一实现 app/limit_stats.py 的业务语义测试。

背景（2026-10-09 事故）：同一"跌停家数"曾有 3 套阈值实现（-9.7 固定 / -19.9+-9.9 两档 /
+-9.9 一刀切），20cm 股跌 10%~13% 被误判成跌停，盘中误推「跌停 123 只」而真值 11~13 只。
本文件断言的是**业务语义**（板幅差异、真值优先、宁少报不误报），不是当前实现细节：
  1. 有真值限价时按真值判（涨停有 0.2% 缓冲，跌停严格）；
  2. 无真值时按该票板幅（主板 10 / 双创 20 / 北交 30 / ST 5）± 0.3pt 容差；
  3. 涨跌幅**超出**板幅 ⇒ 不受该板幅约束（新股 C / 退整期）⇒ 不计数（宁少报）；
  4. 数据缺失（无价格 / 无涨跌幅）⇒ 不计数（缺失 ≠ 涨跌停）。
"""
import sys
import types

import pytest

from app import limit_stats

BREAK_TOL = 0.002   # 与 routers/market._BREAK_TOL 同源（若实现改动需同步，此处是口径锚点）
TOL = 0.3           # 板幅容差（百分点）


def s(code="600000", name="浦发银行", price=10.0, limit_up=0.0, limit_down=0.0, chg=None):
    return {"code": code, "name": name, "price": price,
            "limit_up": limit_up, "limit_down": limit_down, "change_pct": chg}


# ---------- ① 真值限价优先 ----------

def test_true_limit_price_up_hit_with_buffer():
    # 涨停价 10.00，收盘 9.99 在缓冲内（>= 10*(1-0.002)=9.98）⇒ 涨停
    assert limit_stats.is_limit_up(s(price=9.99, limit_up=10.0)) is True


def test_true_limit_price_up_miss():
    assert limit_stats.is_limit_up(s(price=9.97, limit_up=10.0)) is False


def test_true_limit_price_down_is_strict():
    # 跌停判据是严格 <= low_limit，没有缓冲（口径不对称是项目既有决定）
    assert limit_stats.is_limit_down(s(price=9.00, limit_down=9.00)) is True
    assert limit_stats.is_limit_down(s(price=9.01, limit_down=9.00)) is False


# ---------- ② 无真值 → 板幅 ± 容差（事故回归核心）----------

def test_chinext_20cm_up_10pct_is_NOT_limit_up():
    # 2026-10-09 事故回归：创业板 20cm 股涨 10% 绝不能算涨停
    assert limit_stats.is_limit_up(s(code="300001", name="X", chg=10.0)) is False


def test_chinext_20cm_up_19_8_is_limit_up():
    assert limit_stats.is_limit_up(s(code="300001", name="X", chg=19.8)) is True


def test_star_20cm_up_10pct_is_NOT_limit_up():
    assert limit_stats.is_limit_up(s(code="688001", name="X", chg=10.0)) is False


def test_main_board_up_9_8_limit_9_6_not():
    assert limit_stats.is_limit_up(s(code="600000", chg=9.8)) is True
    assert limit_stats.is_limit_up(s(code="600000", chg=9.6)) is False


def test_st_board_5pct():
    assert limit_stats.is_limit_up(s(code="600000", name="ST 华仪", chg=4.85)) is True
    assert limit_stats.is_limit_up(s(code="600000", name="ST 华仪", chg=4.0)) is False


def test_bse_30cm():
    assert limit_stats.is_limit_up(s(code="830001", name="X", chg=29.8)) is True
    assert limit_stats.is_limit_up(s(code="830001", name="X", chg=10.0)) is False


def test_down_20cm_minus_15pct_NOT_limit_down():
    # 创业板跌 15% 不算跌停（20cm 板幅 -19.7~-20.3 之外按"不受约束/未达"处理）
    assert limit_stats.is_limit_down(s(code="300001", name="X", chg=-15.87)) is False


def test_down_beyond_board_is_not_counted():
    # 新股 C 上市首 5 日不受板幅约束：主板跌 15.87% 超 ±10 板幅 ⇒ 不计数（宁少报不误报）
    assert limit_stats.is_limit_down(s(code="001246", name="C力勤", chg=-15.87)) is False
    assert limit_stats.is_limit_up(s(code="001246", name="C力勤", chg=15.87)) is False


# ---------- ③ 数据缺失 ----------

def test_missing_price_or_chg_not_counted():
    assert limit_stats.is_limit_up(s(price=0, chg=None)) is False
    assert limit_stats.is_limit_down(s(price=0, chg=None)) is False
    assert limit_stats.is_limit_up(s(price=0, chg=9.8)) is False   # 有涨跌幅但无价格 ⇒ 不判
    assert limit_stats.is_limit_up(s(chg=None)) is False


# ---------- ④ 家数统计 ----------

def test_counts_from_quotes_mixed():
    stocks = {
        "a": s(code="600000", chg=9.8),          # 涨停（主板）
        "b": s(code="300001", name="X", chg=19.9),  # 涨停（20cm）
        "c": s(code="000001", chg=-9.8),         # 跌停
        "d": s(code="600001", chg=3.0),          # 普通
    }
    lu, ld = limit_stats.counts_from_quotes(stocks)
    assert (lu, ld) == (2, 1)


def test_counts_from_quotes_empty():
    assert limit_stats.counts_from_quotes({}) == (0, 0)
    assert limit_stats.counts_from_quotes(None) == (0, 0)


# ---------- ⑤ counts_best：rt_k 精确优先 + 用途收紧 ----------

@pytest.fixture()
def fake_rt_k(monkeypatch):
    """给 limit_stats 注入一个假的 realtime_uplimit（避免真缓存的不可控状态）。"""
    holder = {}

    def install(payload):
        stub = types.SimpleNamespace(peek=lambda: payload)
        import app
        monkeypatch.setattr(app, "realtime_uplimit", stub, raising=False)
        monkeypatch.setitem(sys.modules, "app.realtime_uplimit", stub)
        holder["payload"] = payload

    return install


def test_counts_best_prefers_rt_k(fake_rt_k):
    fake_rt_k({"limit_up": 96, "limit_down": 11, "trading": 5400,
               "is_intraday": True, "age": 30, "data_date": "2026-10-23"})
    lu, ld, src, meta = limit_stats.counts_best({})
    assert (lu, ld, src) == (96, 11, "rt_k")
    assert meta["data_date"] == "2026-10-23"


def test_counts_best_require_intraday_rejects_stale_rtk(fake_rt_k):
    # 盘前/休市 rt_k 给的是上一交易日收盘定稿；告警用途必须拒绝（时点诚实）
    fake_rt_k({"limit_up": 96, "limit_down": 11, "trading": 5400,
               "is_intraday": False, "age": 3600, "data_date": "2026-10-23"})
    lu, ld, src, meta = limit_stats.counts_best({}, require_intraday=True)
    assert src == "board_approx"


def test_counts_best_min_trading_guards_failopen_leak(fake_rt_k):
    # snapshot() 是 fail-open 会静默漏计：trading 明显偏小时宁可用内存回退
    fake_rt_k({"limit_up": 3, "limit_down": 1, "trading": 500,
               "is_intraday": True, "age": 10, "data_date": "2026-10-23"})
    stocks = {"a": s(code="600000", chg=9.8), "b": s(code="000001", chg=-9.8)}
    lu, ld, src, meta = limit_stats.counts_best(stocks, min_trading=2000)
    assert src == "board_approx"
    assert (lu, ld) == (1, 1)


def test_counts_best_falls_back_when_no_rtk():
    lu, ld, src, meta = limit_stats.counts_best(
        {"a": s(code="600000", chg=9.8)})
    assert src == "board_approx"
    assert lu == 1
    assert meta["data_date"] is None   # 内存口径没有"数据截至"——调用方必须如实披露
