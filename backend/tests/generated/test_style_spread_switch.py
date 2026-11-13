# -*- coding: utf-8 -*-
"""风格分化：权重端 沪深300 → 上证50、阈值 3.0 → 4.0（2026-11-14）。

背景：
  `style_spread` 此前用 **沪深300** 代表"权重"，但沪深300 混了中盘成长股，
  不是纯权重 —— 同批 gate_add 触发日实测 上证50 -0.83% vs 沪深300 -1.85%（差 1pp）。
  换成上证50 后**阈值必须重算**：3 年日线（721 个共同交易日）实测"近5日分化度"
  标准差 上证50 **3.79** vs 沪深300 **2.97**（大 27%，与直觉相反）。
  沪深300@3.0 历史触发率 21.8%；上证50 要达到同样 21.8% 需 **4.0**
  （沿用 3.0 则会跳到 31.8%，"明显分化"的日子凭空多 10pp）。

本测试锁三件事：
  1. 常量正确（指数 / 阈值 / 展示名）；
  2. **阈值真的生效**（3.0~4.0 之间的值在旧口径下有方向、新口径下没有）；
  3. `coach` 与 `style_spread` 是**同一事实源**（防"改一处忘另一处"漂移）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import style_spread


# ── 1. 常量 ────────────────────────────────────────────────────────────

def test_constants_switched():
    """权重端必须是上证50，阈值必须是重校准后的 4.0。"""
    assert style_spread.WEIGHT_IDX == "sh000016"
    assert style_spread.WEIGHT_LABEL == "上证50"
    assert style_spread.GROWTH_IDX == "sz399006"
    assert style_spread.SPREAD_THRESHOLD == 4.0


# ── 2. 阈值真的生效（核心）─────────────────────────────────────────────

def test_verdict_uses_new_threshold():
    """3.5pp：旧阈值(3.0)下是"权重主导"，新阈值(4.0)下必须是"无明显分化"。

    这个断言就是"阈值确实从 3.0 变成 4.0"的证据；
    若有人把阈值改回 3.0，本条立刻失败。
    """
    assert "无明显分化" in style_spread._verdict(3.5)
    assert "权重主导" in style_spread._verdict(4.5)
    # 负向对称
    assert "无明显分化" in style_spread._verdict(-3.5)
    assert "成长主导" in style_spread._verdict(-4.5)
    # 边界
    assert "权重主导" in style_spread._verdict(4.0)


def test_coach_hint_uses_new_threshold():
    """教练的方向提示必须用同一阈值：3.5pp 在旧口径下会提示"减成长"，新口径下不该提示。"""
    from app.coach import rules
    ctx = {"style_spread": {"spread_pp": 3.5}}
    # 显式传 4.0（= _STYLE_THR）
    hint_new = rules._direction_hint(ctx, "reduce", 4.0)
    assert "减成长" not in hint_new, f"4.0 阈值下 3.5pp 不该给方向，实际: {hint_new!r}"
    # 对照：旧阈值 3.0 会给方向 —— 证明这个改动是"有行为差异"的，不是纯注释
    hint_old = rules._direction_hint(ctx, "reduce", 3.0)
    assert "减成长" in hint_old, "旧阈值 3.0 下 3.5pp 应给出方向（对照用）"


# ── 3. 同一事实源（防漂移）─────────────────────────────────────────────

def test_coach_and_style_spread_share_one_source():
    """`coach.rules` 的阈值/指数/展示名必须全部来自 `style_spread`。

    本项目踩过"两处各写一份、换标的只改一处"的坑（`_bench_code` 同一份建议两个基准）
    ⇒ 这里用断言把同源关系钉死。
    """
    from app.coach import rules
    assert rules._STYLE_THR == style_spread.SPREAD_THRESHOLD
    assert rules._STYLE_WEIGHT_IDX == style_spread.WEIGHT_IDX
    assert rules._STYLE_WEIGHT_LABEL == style_spread.WEIGHT_LABEL
    # `_direction_hint` 的默认参数也必须跟着走（def 时求值，容易漏）
    import inspect
    assert inspect.signature(rules._direction_hint).parameters["thr"].default == 4.0


def test_yaml_threshold_matches_constant():
    """`rules.yaml` 是**实际生效值**，必须与常量一致。"""
    import yaml
    p = Path(style_spread.__file__).resolve().parent / "coach" / "rules.yaml"
    y = yaml.safe_load(p.read_text(encoding="utf-8"))
    rule = next(r for r in y["rules"] if r.get("id") == "gate_add_weak_direction")
    assert rule["params"]["style_spread_min"] == style_spread.SPREAD_THRESHOLD


# ── 4. 展示名下发 ──────────────────────────────────────────────────────

def test_build_reports_weight_label():
    """`build()` 必须带回 `weight_label`，前端据此渲染 —— 否则界面会写死"沪深300"。

    这里不依赖数据库：直接调 `_verdict` 同级的最小路径太窄，
    改为允许 `available=False`（无数据时也要带 label）。
    """
    d = style_spread.build(days=5, lookback=1)
    assert d.get("weight_label") == "上证50", "无数据时也必须下发展示名"
    if d.get("available"):
        # 有数据时阈值也要一并下发（前端用它判色）
        assert d.get("threshold") == 4.0
        assert "上证50" in (d.get("note") or "")
