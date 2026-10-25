# -*- coding: utf-8 -*-
"""财报衰减重加权 app/scoring/decay.py 的业务语义测试。

业务语义（2026-09-17 事件研究结论）：
  财报公告后 0-20 天成长/质量因子横截面 IC 为负 → grad 口径按公告龄降权
  （0-5 天剔除、6-20 天减半、>20 天不罚）；zero 口径是分数×0 的对照。
  无财报（age=None）⇒ 一切口径都不衰减。
  注：decay_total 内部 round 到 1 位小数，断言用 abs=0.06 容差。
"""
import pytest

from app.scoring.decay import (
    VARIANT_BASE, VARIANT_GRAD, VARIANT_ZERO,
    decay_total, dim_weight_mult,
)

WEIGHTS = {"technical": 0.4, "capital": 0.3, "fundamental": 0.1,
           "growth": 0.15, "quality": 0.05}
DIMS = {"技术面": 80.0, "资金面": 60.0, "基本面": 50.0, "成长": 90.0, "质量": 100.0}
APPROX = dict(abs=0.06)


# ---------- dim_weight_mult：梯度乘数 ----------

def test_non_decay_dims_always_1():
    assert dim_weight_mult("技术面", 3) == 1.0
    assert dim_weight_mult("资金面", 0) == 1.0


def test_none_age_means_no_decay():
    assert dim_weight_mult("成长", None) == 1.0
    assert dim_weight_mult("质量", None) == 1.0


def test_gradient_bands():
    assert dim_weight_mult("成长", 0) == 0.0     # 0-5 天：剔除
    assert dim_weight_mult("成长", 5) == 0.0
    assert dim_weight_mult("成长", 6) == 0.5     # 6-20 天：减半
    assert dim_weight_mult("成长", 20) == 0.5
    assert dim_weight_mult("成长", 21) == 1.0    # >20 天：不罚
    assert dim_weight_mult("质量", 25) == 1.0


# ---------- decay_total：加权语义 ----------

def test_base_equals_plain_weighted_average():
    want = (80 * .4 + 60 * .3 + 50 * .1 + 90 * .15 + 100 * .05) / 1.0
    assert decay_total(DIMS, WEIGHTS, None, mode=VARIANT_BASE) == pytest.approx(want, **APPROX)


def test_grad_age0_excludes_growth_quality():
    # age=0：成长(90)/质量(100) 被剔除 → 只剩 技术/资金/基本面 加权
    got = decay_total(DIMS, WEIGHTS, 0, mode=VARIANT_GRAD)
    want = (80 * 0.4 + 60 * 0.3 + 50 * 0.1) / 0.8
    assert got == pytest.approx(want, **APPROX)


def test_grad_age10_halves_growth_quality_weight():
    got = decay_total(DIMS, WEIGHTS, 10, mode=VARIANT_GRAD)
    want = (80 * 0.4 + 60 * 0.3 + 50 * 0.1 +
            90 * 0.15 * 0.5 + 100 * 0.05 * 0.5) / (0.8 + 0.075 + 0.025)
    assert got == pytest.approx(want, **APPROX)


def test_grad_age_none_is_base():
    assert decay_total(DIMS, WEIGHTS, None, mode=VARIANT_GRAD) == \
        decay_total(DIMS, WEIGHTS, None, mode=VARIANT_BASE)


def test_zero_mode_zeroes_score_but_keeps_weight():
    # zero 对照：≤25 天成长/质量分×0（权重保留）→ 总分被显著拉低，但不剔除维度
    got = decay_total(DIMS, WEIGHTS, 10, mode=VARIANT_ZERO)
    want = (80 * 0.4 + 60 * 0.3 + 50 * 0.1) / 1.0
    assert got == pytest.approx(want, **APPROX)
    assert got < decay_total(DIMS, WEIGHTS, 10, mode=VARIANT_GRAD)


def test_zero_mode_beyond_25d_is_base():
    assert decay_total(DIMS, WEIGHTS, 26, mode=VARIANT_ZERO) == \
        decay_total(DIMS, WEIGHTS, 26, mode=VARIANT_BASE)


def test_missing_dim_and_none_scores_skipped():
    dims = {"技术面": None, "资金面": 60.0, "成长": 90.0}
    got = decay_total(dims, WEIGHTS, 0, mode=VARIANT_GRAD)
    # age=0 时成长剔除、技术面 None 跳过 → 只剩 资金面
    assert got == pytest.approx(60.0, **APPROX)


def test_all_invalid_returns_none():
    assert decay_total({"技术面": None}, WEIGHTS, 0) is None
    assert decay_total(DIMS, {}, 0) is None
    assert decay_total(None, WEIGHTS, 0) is None


def test_wrong_mode_name_silently_no_decay():
    # 2026-09-17 踩坑回归：mode 传 "gradient"（错名）行为 = base（不衰减）。
    # 本断言锁定该现状语义：改实现时必须让"拼错模式名"显式报错而不是静默不衰减。
    assert decay_total(DIMS, WEIGHTS, 3, mode="gradient") == \
        decay_total(DIMS, WEIGHTS, 3, mode=VARIANT_BASE)
