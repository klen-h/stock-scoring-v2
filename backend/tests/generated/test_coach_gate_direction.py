# -*- coding: utf-8 -*-
"""`gate_add_weak_direction` 回归：加仓的「方向」否决。

背景（2026-11-13，用户："新的加仓命中的规则加入了吗？"）：
  17 个 gate_add 触发日的 T+5 实测 —— 上证50 -0.83% / 沪深300 -1.85% /
  创业板 -4.36%。按「近 5 日 沪深300−创业板」分组：
    分化度 ≥ +3pp → 创业板 T+5 -2.46%（胜率 33%，n=6）
    分化度 <  +3pp → 创业板 T+5 -5.80%（胜率  0%，n=8）
  ⇒ 分化度不足时否决 gate_add（复用 _arbitrate 降级为"今日不执行"）。
"""
import pytest

from app.coach import rules as R
from app.coach.rules import Advice


def _adv(rid, msg='m', nums=None, code='', label='l', push=False, sev='info'):
    return Advice(rule_id=rid, label=label, severity=sev, push=push,
                  code=code, name='', message=msg, numbers=nums or {})


def _spread(v, days=5):
    return {'spread_pp': v, 'weight_ret_pct': 1.0, 'growth_ret_pct': 1.0 - v,
            'days': days, 'as_of': '2026-11-13'}


# ── 规则求值 ──────────────────────────────────────────────────────────────

def test_fires_when_spread_below_threshold():
    out = R._ev_gate_add_weak_direction(
        None, {'style_spread_min': 3.0}, {'style_spread': _spread(0.65)})
    assert out is not None
    assert '方向不对' in out['message']
    assert out['numbers']['style_spread_pp'] == 0.65


def test_not_fires_when_spread_enough():
    assert R._ev_gate_add_weak_direction(
        None, {'style_spread_min': 3.0}, {'style_spread': _spread(4.2)}) is None


def test_fail_open_on_missing_data():
    """缺数据不否决 —— 缺失 ≠ 方向不对（否则日批故障日会静默全面禁加仓）。"""
    p = {'style_spread_min': 3.0}
    assert R._ev_gate_add_weak_direction(None, p, {'style_spread': None}) is None
    assert R._ev_gate_add_weak_direction(None, p, {'style_spread': {}}) is None
    assert R._ev_gate_add_weak_direction(None, p, {}) is None


def test_default_threshold_is_3pp():
    """yaml 缺 `style_spread_min` 时默认 3.0，不能变成 0（否则几乎不触发）。"""
    assert R._ev_gate_add_weak_direction(
        None, {}, {'style_spread': _spread(2.9)}) is not None
    assert R._ev_gate_add_weak_direction(
        None, {}, {'style_spread': _spread(3.1)}) is None


# ── 消解层（关键：只加进 _BLOCKING_RULES 是不够的）──────────────────────────

def test_arbitrate_downgrades_gate_add():
    """两个规则并存 ⇒ gate_add 被降级为"今日不执行"，且写明方向原因。

    ⚠️ 第一版只把 rule_id 加进 `_BLOCKING_RULES`、没在 `_arbitrate` 里收集
       reason ⇒ `reasons` 为空 ⇒ 提前 return ⇒ 规则等于没生效。本用例锁死该行为。
    """
    reason = R._ev_gate_add_weak_direction(
        None, {'style_spread_min': 3.0}, {'style_spread': _spread(0.65)})
    advs = [
        _adv('gate_add', '【加仓条件确认】涨跌比 1.2',
             {'up_down_ratio': 1.2, 'limit_down': 3, 'confirmed': True}),
        _adv('gate_add_weak_direction', reason['message'], reason['numbers']),
    ]
    res = R._arbitrate(advs)
    ga = next(a for a in res if a.rule_id == 'gate_add')
    assert ga.numbers.get('blocked') is True
    assert 'gate_add_weak_direction' in ga.numbers.get('blocked_by', [])
    assert '方向不对' in ga.message
    # 收尾语应是"方向"而非"环境"（方向问题不会因环境转好而消失）
    assert '方向转好' in ga.message
    assert '环境转好' not in ga.message


def test_arbitrate_untouched_without_blocking_rule():
    """没有否决规则时，gate_add 原样输出。"""
    advs = [_adv('gate_add', '【加仓条件确认】',
                 {'up_down_ratio': 1.2, 'limit_down': 3, 'confirmed': True})]
    res = R._arbitrate(advs)
    ga = next(a for a in res if a.rule_id == 'gate_add')
    assert not ga.numbers.get('blocked')
    assert ga.message == '【加仓条件确认】'


# ── 配置 ──────────────────────────────────────────────────────────────────

def test_rule_registered_in_yaml_and_registry():
    assert 'gate_add_weak_direction' in set(R.EVALUATORS)
    assert 'gate_add_weak_direction' in R._BLOCKING_RULES
    cfg = R.load_config() or {}
    all_rules = cfg.get('rules') or []          # 顶层就是 rules 列表（无 groups 层）
    found = [r for r in all_rules if r.get('id') == 'gate_add_weak_direction']
    assert found, 'rules.yaml 缺少 gate_add_weak_direction'
    assert found[0].get('enabled') is True
    assert float(found[0]['params']['style_spread_min']) == 3.0
