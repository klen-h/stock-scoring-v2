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
# ★ 2026-11-14：阈值断言改为引用**事实源**，不在这里硬编码数字
#   （口径已从 沪深300@3.0 切到 上证50@4.0）。
from app.style_spread import SPREAD_THRESHOLD


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


def test_message_is_neutral_and_does_not_recommend_reverse():
    """★ 文案三处纠错（2026-11-16 用户："这个文案是不是搞反了？"）。

    ① 规则名原写"权重弱于成长"，但触发条件 `spread < 4pp` 包含
       "权重仍跑赢、只是优势不足"（实例 +1.14pp）⇒ label 说反了。
    ② 文案原写"资金在抱权重、成长在失血"—— 写死正负号；
       但负分化度（成长跑赢）时这句完全错误 ⇒ 改中性表述。
    ③ 文案原写"若一定要加，只考虑权重/红利方向"—— 这是**反向加仓指令**，
       与本规则注释的设计原则"只否决、不反向加仓"直接冲突；
       且历史数字 -5.8% 是 v1（沪深300@3pp），当前阈值是上证50@4pp
       ⇒ 新口径应报 -6.13%（n=5）。
    """
    cfg = R.load_config() or {}
    rule = next(r for r in cfg.get('rules') or []
                if r.get('id') == 'gate_add_weak_direction')
    assert '弱于成长' not in rule['label']
    assert '优势不足' in rule['label']

    out = R._ev_gate_add_weak_direction(
        None, {}, {'style_spread': _spread(1.14)})
    m = out['message']
    # 中性：不写死"跑赢"，正负分化度都能用
    assert '权重相对成长 +1.14pp' in m
    assert '资金在抱权重' not in m
    # 只否决，不给反向指令
    assert '若一定要加' not in m
    assert '不构成反向加仓建议' in m
    # 数字与当前阈值口径一致（上证50@4pp）
    assert '-6.13%' in m
    assert '-5.8%' not in m

    # 负分化度也必须能正常出文案（旧文案在这里会说反话）
    neg = R._ev_gate_add_weak_direction(
        None, {}, {'style_spread': _spread(-2.0)})
    assert '权重相对成长 -2.00pp' in neg['message']


def test_not_fires_when_spread_enough():
    assert R._ev_gate_add_weak_direction(
        None, {'style_spread_min': 3.0}, {'style_spread': _spread(4.2)}) is None


def test_fail_open_on_missing_data():
    """缺数据不否决 —— 缺失 ≠ 方向不对（否则日批故障日会静默全面禁加仓）。"""
    p = {'style_spread_min': 3.0}
    assert R._ev_gate_add_weak_direction(None, p, {'style_spread': None}) is None
    assert R._ev_gate_add_weak_direction(None, p, {'style_spread': {}}) is None
    assert R._ev_gate_add_weak_direction(None, p, {}) is None


def test_default_threshold_matches_source():
    """yaml 缺 `style_spread_min` 时走兜底默认值，且**必须等于事实源常量**。

    ★ 2026-11-14 口径切换（沪深300→上证50）后此值 3.0 → **4.0**。
      本条**不硬编码 4.0**，而是引用 `style_spread.SPREAD_THRESHOLD`：
        · 换标的时这里自动跟随（不会再误报）；
        · 但若 `rules.py` 的兜底值与事实源脱钩，本条立刻失败（这才是要防的漂移）。
    测试意图不变：兜底值不能塌成 0（否则规则几乎不触发）。
    """
    thr = SPREAD_THRESHOLD
    assert R._ev_gate_add_weak_direction(
        None, {}, {'style_spread': _spread(thr - 0.1)}) is not None
    assert R._ev_gate_add_weak_direction(
        None, {}, {'style_spread': _spread(thr + 0.1)}) is None


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
    # ★ 2026-11-14：yaml 是**实际生效值**，必须与事实源一致（此处曾是 3.0）
    assert float(found[0]['params']['style_spread_min']) == SPREAD_THRESHOLD
