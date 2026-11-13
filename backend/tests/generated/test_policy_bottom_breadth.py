# -*- coding: utf-8 -*-
"""涨跌比序列的冻结检测（含 2026-11-13 新增的「近似冻结」判据）。

背景事故：
  2026-11-06 = 638/4321、2026-11-09 = **639**/4321 —— up 只差 1 只，
  原判据要求 `prev == (up, down)` **完全相等** ⇒ 漏检 ⇒ 脏行（比值 0.15）
  进入判据④，把 11-10 的清单打钩判成"不满足"。
  真实值应为 2320/2520 = 0.92（`am_*` 11:31 定稿；`kline_cache` 重算 0.66）。
"""
import pytest

from app import policy_bottom_watch as P


def _rows(seq):
    """构造 db.fetch 的返回。

    ⚠️ 真实 SQL 是 `ORDER BY date DESC`（**降序**），`_breadth_series` 内部再
    `reversed(rows)` 转升序做冻结检测 ⇒ mock 必须同样返回**降序**，
    否则顺序反了、`prev` 永远比的是"后一天"（第一版这里写反导致 5 个用例全挂）。
    """
    return [{"date": d, "close_as_of": d, "close_up": str(u), "close_down": str(dn)}
            for d, u, dn in reversed(list(seq))]


def _run(monkeypatch, seq, limit=10):
    rows = _rows(seq)
    monkeypatch.setattr(P.db, "fetch", lambda *a, **k: list(rows))
    return P._breadth_series(limit=limit)


def test_identical_values_are_dropped(monkeypatch):
    """完全相同的两行 ⇒ 后一行剔除。"""
    series, bad = _run(monkeypatch, [
        ("2026-11-05", 4357, 587),
        ("2026-11-06", 638, 4321),
        ("2026-11-09", 638, 4321),      # 完全相同
    ])
    assert [d for d, _ in series] == ["2026-11-05", "2026-11-06"]
    assert any("完全相同" in b for b in bad)


def test_near_identical_values_are_dropped(monkeypatch):
    """差 1 只（638→639）且比值几乎不变 ⇒ 也剔除（2026-11-13 新增判据）。"""
    series, bad = _run(monkeypatch, [
        ("2026-11-05", 4357, 587),
        ("2026-11-06", 638, 4321),
        ("2026-11-09", 639, 4321),      # up 只差 1，down 相同
    ])
    assert [d for d, _ in series] == ["2026-11-05", "2026-11-06"]
    assert any("近似相同" in b for b in bad), bad


def test_close_counts_but_divergent_ratio_are_kept(monkeypatch):
    """数值接近但**比值差 > 0.02** ⇒ 不判冻结（防误杀真实数据）。"""
    series, bad = _run(monkeypatch, [
        ("2026-11-05", 100, 100),       # 比值 1.00
        ("2026-11-06", 103, 97),        # 比值 1.06，与上值差 3 只在容差内
    ])
    assert [d for d, _ in series] == ["2026-11-05", "2026-11-06"]
    assert not bad


def test_normal_days_are_kept(monkeypatch):
    """正常波动不应被剔除。"""
    series, bad = _run(monkeypatch, [
        ("2026-11-05", 4357, 587),
        ("2026-11-06", 638, 4321),
        ("2026-11-09", 2320, 2520),
        ("2026-11-10", 3989, 851),
    ])
    assert len(series) == 4
    assert not bad
    assert abs(series[2][1] - 0.921) < 0.001


def test_missing_close_is_skipped(monkeypatch):
    """close 缺失 ⇒ 剔除且不当作 0。"""
    series, bad = _run(monkeypatch, [
        ("2026-11-05", 4357, 587),
        ("2026-11-06", None, None),
        ("2026-11-09", 2320, 2520),
    ])
    assert [d for d, _ in series] == ["2026-11-05", "2026-11-09"]
    assert any("缺失" in b for b in bad)
