# -*- coding: utf-8 -*-
"""权重试跑开关（2026-11-15 评分诊断）。

背景：实测 `DEFENSIVE` 档把 **质量 0.35**（最高）给了区分力 -2.42pp 的维度、
把 **技术面 0.12**（最低）给了 +1.93pp 的维度 ⇒ 总分区分力 -2.44pp。

这里只测**开关本身**（默认关闭 / 覆盖生效 / 非法回退 / 权重和为 1），
不测"哪个方案更好"——那是数据结论，不是单元测试能断言的。
"""
import pytest

from app.backtest import market_regime as MR


def _w(state):
    return MR.get_regime_weights(state)


def test_default_off_when_no_env(monkeypatch):
    """★ 不设 `SCORE_WEIGHT_PRESET` ⇒ **行为必须与改动前完全一致**。

    这是本次改动最重要的保证：线上不设 env 时，榜单口径一个字都不能变。
    """
    monkeypatch.delenv("SCORE_WEIGHT_PRESET", raising=False)
    w = _w(MR.DEFENSIVE)
    assert w["technical"] == 0.12
    assert w["quality"] == 0.35
    assert MR.active_weight_preset() == ""


def test_neutral_guard_borrows_neutral(monkeypatch):
    """`neutral_guard` ⇒ defensive 借用 neutral（neutral 是验证过的档）。

    ⚠️ `quality` 是 **0.10** 而不是 neutral 原表的 **0.11**：
      原表之和是 **1.01**（既有偏差），试跑口径里归一了。
    """
    monkeypatch.setenv("SCORE_WEIGHT_PRESET", "neutral_guard")
    d = _w(MR.DEFENSIVE)
    n = _w(MR.NEUTRAL)
    assert d["technical"] == n["technical"] == 0.27
    assert d["fundamental"] == n["fundamental"] == 0.23
    assert d["quality"] == 0.10 and n["quality"] == 0.11   # 差 0.01 = 归一
    # 其它市况不受影响
    assert _w(MR.OFFENSIVE)["technical"] == 0.50


def test_power_v2_reweights(monkeypatch):
    """`power_v2` ⇒ 正向维度各 0.30、负向各 0.05。"""
    monkeypatch.setenv("SCORE_WEIGHT_PRESET", "power_v2")
    d = _w(MR.DEFENSIVE)
    assert d["technical"] == 0.30
    assert d["quality"] == 0.05
    assert d["growth"] == 0.05


def test_invalid_preset_falls_back(monkeypatch):
    """非法 preset 名 ⇒ 静默回退默认表（不能因为拼错就让选股失效）。"""
    monkeypatch.setenv("SCORE_WEIGHT_PRESET", "typo_xxx")
    w = _w(MR.DEFENSIVE)
    assert w["technical"] == 0.12 and w["quality"] == 0.35


@pytest.mark.parametrize("preset", ["", "neutral_guard", "power_v2"])
def test_preset_weights_sum_to_one(monkeypatch, preset):
    """★ 权重和必须 = 1.0，否则总分尺度漂移（60 分不再是 60 分）。

    ⚠️ 已知遗留：**默认 `NEUTRAL` 表本身就是 1.01**（0.27+0.23+0.23+0.17+0.11），
      是 2026-09-02 权重优化时留下的，本次不动（改它会动到已上线的榜单）。
      所以这里只校验**预设覆盖后**的 defensive 档。
    """
    if preset:
        monkeypatch.setenv("SCORE_WEIGHT_PRESET", preset)
    else:
        monkeypatch.delenv("SCORE_WEIGHT_PRESET", raising=False)
    assert abs(sum(_w(MR.DEFENSIVE).values()) - 1.0) < 1e-9
