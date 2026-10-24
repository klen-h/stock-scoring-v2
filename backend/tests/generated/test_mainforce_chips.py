# -*- coding: utf-8 -*-
"""筹码分布 CYQ 模型 app/mainforce/chips.py 的业务语义测试。

业务语义：
  - 数据不足（<30 根有效 K 线）⇒ 不产出（缺失 ≠ 编造指标）；
  - 换手率单位：volume 单位=手、float_shares=股 → tr = vol*100/float_shares
    （2026-09-16 修复：此前低估 100 倍 → 获利盘/price_pos 系统性虚高）；
  - 获利盘 winner_ratio = 现价下方筹码占比：价格一路上行的票应显著>0.5，
    一路下行的票应显著<0.5；price_pos ∈ [0,1]。
"""
import math

from app.mainforce.chips import chip_metrics, chip_series


def _bars(prices, vol=10000.0, start_day=1):
    out = []
    for i, p in enumerate(prices):
        out.append({"date": f"2026-{(start_day + i) // 28:02d}-{(start_day + i) % 28 + 1:02d}-{i}",
                    "open": p, "high": p * 1.01, "low": p * 0.99,
                    "close": p, "volume": vol})
    return out


def _ramp(n, lo, hi):
    return [lo + (hi - lo) * i / (n - 1) for i in range(n)]


def test_insufficient_bars_returns_empty():
    assert chip_series(_bars(_ramp(29, 10, 20)), 1e9) == {}
    assert chip_series([], 1e9) == {}
    assert chip_series(None) == {}


def test_invalid_rows_are_skipped_not_fatal():
    # 指标产出需要 warmup=60 根：用 70 根、剔 2 根坏行后 68 根 ⇒ 仍能产出（而不是整体放弃）
    bars = _bars(_ramp(70, 10, 12), vol=1000.0)
    bars[5]["close"] = 0          # 坏行：close<=0
    bars[7]["volume"] = -1        # 坏行：负量
    got = chip_series(bars, 1e8)
    assert got, "68 根有效 K 线（>warmup 60）应能产出筹码指标"
    assert len(got) == 8          # 70 根剔 2 根=68 根（下标 0..67），warmup 60 后产出下标 60..67 共 8 天
    assert all(0.0 <= m["price_pos"] <= 1.0 for m in got.values())


def test_below_warmup_yields_no_metrics():
    # <60 根（即使 ≥30）不产出指标：数据不足不编造（缺失 ≠ 指标）
    bars = _bars(_ramp(50, 10, 12), vol=1000.0)
    assert chip_series(bars, 1e8) == {}


def test_rising_stock_winner_ratio_high():
    bars = _bars(_ramp(130, 10.0, 20.0), vol=50000.0)
    m = chip_metrics(bars, 1e8)   # 小流通盘 → 换手率封顶 40%，老筹码快速置换
    assert m is not None
    assert m["winner_ratio"] > 0.7, f"一路上行且高换手 ⇒ 现价下方筹码应占多数，got {m}"


def test_falling_stock_winner_ratio_low():
    bars = _bars(_ramp(130, 20.0, 10.0), vol=50000.0)
    m = chip_metrics(bars, 1e8)
    assert m is not None
    assert m["winner_ratio"] < 0.3, f"一路下行且高换手 ⇒ 获利盘应极低，got {m}"


def test_metrics_keys_and_bounds():
    bars = _bars(_ramp(130, 10.0, 15.0), vol=10000.0)
    m = chip_metrics(bars, 1e9)
    assert m is not None
    for k in ("winner_ratio", "cost_bias", "avg_cost", "concentration",
              "price_pos", "p5", "p95"):
        assert k in m, f"缺关键指标 {k}"
    assert 0.0 <= m["winner_ratio"] <= 1.0
    assert 0.0 <= m["price_pos"] <= 1.0
    assert m["p5"] <= m["p95"]
    assert 0.0 <= m["concentration"] <= 1.0


def test_turnover_unit_fix_chips_follow_price():
    # 换手率单位语义（2026-09-16 修复回归）：volume=手、float_shares=股 ⇒ tr=vol*100/float_shares。
    # 构造 90 天横盘 10 元 + 40 天跳到 20 元：小流通盘下 tr 触顶 0.40 → 老筹码 130 天后
    # 基本置换完毕 → 筹码均值应跟住现价 20（若单位错 100 倍则 tr≈0.004，老筹码留存 → 均值≈10）。
    head = [10.0] * 90
    tail = [20.0] * 40
    bars = _bars(head + tail, vol=80000.0)
    m = chip_metrics(bars, 2e7)
    assert m is not None
    assert abs(m["avg_cost"] - 20.0) < 1.5, (
        f"高换手应把老筹码置换干净（avg_cost≈20），got {m['avg_cost']}")
    assert abs(m["cost_bias"]) < 10.0, (
        f"现价与筹码均值贴近（cost_bias≈0），got {m['cost_bias']}")


def test_chip_series_dates_out_filter():
    bars = _bars(_ramp(130, 10.0, 15.0), vol=10000.0)
    want_date = bars[-1]["date"]
    got = chip_series(bars, 1e9, dates_out=[want_date])
    assert list(got.keys()) == [want_date]
