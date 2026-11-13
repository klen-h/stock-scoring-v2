# -*- coding: utf-8 -*-
"""风格分化观察器 + 方向提示 + 分化评价口径（2026-11-13 新增的 ①②③）。

背景：同一批 `gate_add` 触发日 T+5 —— 上证50 -0.83% vs 创业板 -4.36%，
方向错代价约 5 倍。三者共用一套「权重 − 成长」口径，必须保证：
  · 观察器（③）与教练规则（①）算出**同一个数**；
  · 文案（②）三档判断与阈值一致；
  · 评价（①）不走"基准涨跌"而是"分化"。
"""
import pytest

from app import style_spread as ss
from app.coach import audit as A
from app.coach import rules as R


# ── ③ 观察器 ──────────────────────────────────────────────────────────────

def test_verdict_three_tiers():
    thr = ss.SPREAD_THRESHOLD
    assert '权重主导' in ss._verdict(thr)
    assert '权重主导' in ss._verdict(thr + 5)
    assert '成长主导' in ss._verdict(-thr)
    assert '无明显分化' in ss._verdict(0)
    assert '无明显分化' in ss._verdict(thr - 0.1)


def test_build_marks_unavailable_without_zero(monkeypatch):
    """任一端缺数据 ⇒ available=False，**不能返回 0 冒充"无分化"**。"""
    monkeypatch.setattr(ss, '_closes', lambda code, limit=200: {})
    d = ss.build()
    assert d['available'] is False
    assert d['spread_pp'] is None
    assert '缺失' in d['note']


def test_build_rejects_misaligned_dates(monkeypatch):
    """两指数最新日期不齐 ⇒ 不出数（防"一只更新一只滞后"算出假分化）。"""
    def fake(code, limit=200):
        if code == ss.WEIGHT_IDX:
            return {f'2026-11-{d:02d}': 100 + d for d in range(1, 12)}
        return {f'2026-11-{d:02d}': 200 + d for d in range(1, 10)}   # 少两天
    monkeypatch.setattr(ss, '_closes', fake)
    d = ss.build(days=5, lookback=5)
    assert d['available'] is False
    assert '不齐' in d['note'] or '不足' in d['note']


def test_build_computes_spread(monkeypatch):
    """沪深300 涨 10%、创业板涨 4% ⇒ spread = +6pp（只看首尾，注意是复利口径）。"""
    def fake(code, limit=200):
        last = 110.0 if code == ss.WEIGHT_IDX else 104.0
        # 三天数据 + days=2 ⇒ 窗口取 11-09 → 11-11
        return {'2026-11-09': 100.0, '2026-11-10': 100.0, '2026-11-11': last}
    monkeypatch.setattr(ss, '_closes', fake)
    d = ss.build(days=2, lookback=1)
    assert d['available'] is True
    assert d['spread_pp'] == pytest.approx(6.0, abs=0.05)
    assert d['weight_ret_pct'] == pytest.approx(10.0, abs=0.05)
    assert d['growth_ret_pct'] == pytest.approx(4.0, abs=0.05)


def test_days_are_clamped():
    """内部调用绕过 Query 校验 ⇒ build 自己夹住，防止 days=500 这种无意义窗口。"""
    d = ss.build(days=500, lookback=5)
    assert d['days'] == 60


# ── ② 方向提示 ────────────────────────────────────────────────────────────

def _ctx(v):
    return {'style_spread': {'spread_pp': v}}


def test_direction_hint_three_tiers():
    assert '权重相对抗跌' in R._direction_hint(_ctx(5), 'reduce')
    assert '只考虑权重/红利' in R._direction_hint(_ctx(5), 'avoid_add')
    assert '无明显分化' in R._direction_hint(_ctx(0), 'reduce')
    # 负向：成长强于权重 ⇒ 减仓方向相反，不能笼统说"减成长"
    assert '先减权重' in R._direction_hint(_ctx(-5), 'reduce')
    assert '方向强 ≠ 可以加' in R._direction_hint(_ctx(-5), 'avoid_add')


def test_direction_hint_fail_open():
    """取不到分化度 ⇒ 空串（不臆造方向）。"""
    assert R._direction_hint({'style_spread': None}, 'reduce') == ''
    assert R._direction_hint({}, 'avoid_add') == ''
    assert R._direction_hint({'style_spread': {}}, 'reduce') == ''


def test_direction_hint_no_markdown():
    """文案会被前端 `{{ }}` 原样渲染 ⇒ 不能含 Markdown 星号。"""
    for v in (5, 0, -5):
        for k in ('reduce', 'avoid_add'):
            assert '**' not in R._direction_hint(_ctx(v), k)


# ── ① 评价口径 ────────────────────────────────────────────────────────────

def test_spread_rules_registered():
    assert 'gate_add_weak_direction' in A._SPREAD_RULES
    assert A._RULE_EXPECT.get('gate_add_weak_direction') == 'down'
    # 观察器与教练共用同一套指数（唯一事实源）
    assert R._STYLE_WEIGHT_IDX == ss.WEIGHT_IDX
    assert R._STYLE_GROWTH_IDX == ss.GROWTH_IDX


def test_spread_change_uses_two_indices(monkeypatch):
    """`_spread_change` 必须返回**两个指数之差**，不是单一基准涨跌。"""
    calls = []

    def fake_fetch(sql, params=None):
        calls.append(params[0] if params else None)
        code = params[0]
        if code == ss.WEIGHT_IDX:
            return [{'date': '2026-11-10', 'close': 100.0},
                    {'date': '2026-11-17', 'close': 110.0}]
        return [{'date': '2026-11-10', 'close': 100.0},
                {'date': '2026-11-17', 'close': 104.0}]
    monkeypatch.setattr(A.db, 'fetch', fake_fetch)
    sp, d = A._spread_change('2026-11-10', 1)
    assert sp == pytest.approx(6.0)
    assert d == '2026-11-17'
    assert ss.WEIGHT_IDX in calls and ss.GROWTH_IDX in calls


def test_spread_change_rejects_misaligned_end(monkeypatch):
    """两侧到期日不一致 ⇒ 返回 None（不能比不同长度的区间）。"""
    def fake_fetch(sql, params=None):
        code = params[0]
        if code == ss.WEIGHT_IDX:
            return [{'date': '2026-11-10', 'close': 100.0},
                    {'date': '2026-11-17', 'close': 110.0}]
        return [{'date': '2026-11-10', 'close': 100.0},
                {'date': '2026-11-18', 'close': 104.0}]
    monkeypatch.setattr(A.db, 'fetch', fake_fetch)
    sp, _ = A._spread_change('2026-11-10', 1)
    assert sp is None
