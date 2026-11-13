# -*- coding: utf-8 -*-
"""收益率品种「日变化(bp)」的不可信数据拦截（`macro._yield_bp`）。

背景（2026-11-14，用户："中国 10Y 国债变动(bp) = 0 · 当前 0.0，
多需 < -2 / 空需 > 3 ⇒ 未达阈值是正常的吗"）：

  **不正常，是真 bug。** 库里 2026-11-04 ~ 11-13 连续 8 个交易日
  `cn10y.price == prev_close` ⇒ `cn10y_bp_change` 恒为 0.00。

  根因：那几天新浪 `globalbd_cn10yt` 拉不到（快照 `source="eastmoney"`），
  东财兜底返回**「全字段同值」的残缺包**（`f2=f15=f16=f18`，`f3≈±0.01`），
  代码把它当成"收益率没变"。

  为什么危险：`cn10y_liquidity` 的区间是 `-2 < bp < 3`，假 0 正好落在
  "未达阈值" ⇒ **界面看起来完全正常**，8 天无人发现；同时它让这条规则
  "有数据但中性"，**稀释 china 组的分母**（组得分 = Σ(方向×强度)/Σ强度）。

  修法：`prev_close == price` ⇒ 返回 `None` ⇒ 规则引擎走
  "无数据（该条不计分，也不计入分母）"，**宁缺勿假**。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import macro


# ── `_yield_bp` 单元 ────────────────────────────────────────────────────

def test_normal_yield_bp():
    """正常数据：算得出 bp（东财 11-13 实测 1.6181 / 1.6077 ⇒ 1.04bp）。"""
    assert macro._yield_bp({"price": 1.6181, "prev_close": 1.6077}) == 1.04
    assert macro._yield_bp({"price": 1.619, "prev_close": 1.631}) == -1.2
    assert macro._yield_bp({"price": 5.465, "prev_close": 5.337}) == 12.8


def test_degenerate_packet_returns_none():
    """★ 核心用例：`prev_close == price` 的残缺包必须返回 None，不能返回 0。

    这两个点是**真实快照值**（11-04 / 11-13 库里存的就是这样）。
    """
    assert macro._yield_bp({"price": 1.608, "prev_close": 1.608}) is None
    assert macro._yield_bp({"price": 1.627, "prev_close": 1.627}) is None
    # 美债同源兜底，一并保护
    assert macro._yield_bp({"price": 5.337, "prev_close": 5.337}) is None


def test_missing_or_invalid_fields_return_none():
    """缺字段 / 类型不对 / 空值 ⇒ None（不是 0）。"""
    assert macro._yield_bp(None) is None
    assert macro._yield_bp({}) is None
    assert macro._yield_bp({"price": 1.6}) is None
    assert macro._yield_bp({"prev_close": 1.6}) is None
    # 东财停牌时给 "-"，转 float 前必须过类型检查
    assert macro._yield_bp({"price": 1.6, "prev_close": "-"}) is None
    assert macro._yield_bp({"price": "-", "prev_close": 1.6}) is None


def test_real_unchanged_is_distinguishable():
    """⚠️ 边界：真的"微小变动"不该被误杀——只要数值不相等就照常给 bp。

    这是**为什么不判 `|chg| 极小`** 的原因：只判"完全相等"（残缺包的确定特征）。
    """
    assert macro._yield_bp({"price": 1.6001, "prev_close": 1.6}) == 0.01
    assert macro._yield_bp({"price": 1.6000, "prev_close": 1.60001}) == 0.0


# ── 规则层：缺失 ⇒ "无数据"而不是"未达阈值" ──────────────────────────

def _panel(bp_change_marker=None):
    """构造最小 panel。`bp_change_marker` 为 `None` 表示**没有**该派生量。"""
    p = {"cn10y": {"price": 1.608, "prev_close": 1.608, "kind": "yield"},
         "_derived": {}}
    if bp_change_marker is not None:
        p["_derived"]["cn10y_bp_change"] = bp_change_marker
    return p


def test_rule_reports_no_data_when_bp_missing():
    """不可用 ⇒ `cn10y_liquidity` 的 note 必须是「无数据」，且**不计入分母**。"""
    _, _, _, _, details = macro.evaluate_rules(_panel(None), None, None)
    row = next(x for x in details if x["id"] == "cn10y_liquidity")
    assert row["value"] is None
    assert row["direction"] == 0
    assert "无数据" in row["note"]


def test_rule_scored_when_bp_available():
    """可用 ⇒ 正常参与计分（这里 4.0bp > 3 ⇒ 空）。"""
    _, _, _, _, details = macro.evaluate_rules(_panel(4.0), None, None)
    row = next(x for x in details if x["id"] == "cn10y_liquidity")
    assert row["value"] == 4.0
    assert row["direction"] == -1


def test_missing_bp_does_not_dilute_group_denominator():
    """★ 这个 bug 的真实代价：假 0 会**稀释 china 组分母**。

    组得分 = Σ(方向×强度) / Σ强度。少一条"虚假的中性规则"，
    其余规则的方向表达得更充分 ⇒ 分数**不同**是关键，不是"差不多"。
    """
    from copy import deepcopy
    base = _panel(None)
    base["_derived"].update({"cny_cnh_basis_pips": 10.0})   # 方向 +1，strength 1.0

    with_fake_zero = deepcopy(base)
    with_fake_zero["_derived"]["cn10y_bp_change"] = 0.0     # 修复前的"假 0"
    without = deepcopy(base)                                # 修复后：无数据

    _, _, dir_zero, _, _ = macro.evaluate_rules(with_fake_zero, None, None)
    _, _, dir_none, _, _ = macro.evaluate_rules(without, None, None)
    assert dir_zero != dir_none, (
        "若两者相等说明分母没受影响，本用例的前提就错了 —— "
        "请检查 _resolve_metric / group_sum 的 null 分支")
