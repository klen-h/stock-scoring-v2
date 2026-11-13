# -*- coding: utf-8 -*-
"""宏观规则指纹（`macro.rules_fingerprint`）。

背景（2026-11-14）：
  `RULES_VERSION` 是纯人工维护的字符串，有人调了阈值忘 bump 版本号，历史快照就会
  标着**同一版本、实际口径不同** ⇒ 回测被污染且看不出来。
  指纹对"影响分数的配置"算 sha256 前 12 位，随每日快照落库，
  能抓出「同版本号、不同指纹」的漂移。

  ★ 本文件最关键的**不是**"指纹能算出来"，而是**边界判对**：
    · 改**展示文案**（tag / why / 中文名）⇒ 指纹**不能变**（否则天天变，等于没有）
    · 改**阈值/强度/组权重/规则增删** ⇒ 指纹**必须变**
"""
import copy
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import macro


def _fp_with(monkeypatch, rules=None, weights=None):
    """在替换后的 RULES / GROUP_WEIGHTS 下算指纹。"""
    if rules is not None:
        monkeypatch.setattr(macro, "RULES", rules)
    if weights is not None:
        monkeypatch.setattr(macro, "GROUP_WEIGHTS", weights)
    return macro.rules_fingerprint()


# ── 基本形态 ───────────────────────────────────────────────────────────

def test_fingerprint_format():
    fp = macro.rules_fingerprint()
    assert isinstance(fp, str)
    assert re.fullmatch(r"[0-9a-f]{12}", fp), f"应为 12 位 hex，实际 {fp!r}"


def test_fingerprint_is_deterministic():
    """同一份配置必须每次算出同一个值（否则漂移检查全是噪音）。"""
    assert macro.rules_fingerprint() == macro.rules_fingerprint()


# ── ★ 边界：展示文案不参与 ────────────────────────────────────────────

def test_display_text_does_not_change_fingerprint(monkeypatch):
    """改 tag / why **不该**改变指纹 —— 这是整个机制能否可用的前提。

    若这里失败，说明 `_FP_FIELDS` 混进了展示字段，指纹会随每次文案修改而变，
    漂移告警将变成噪音（然后被所有人忽略，比没有更糟）。
    """
    base = macro.rules_fingerprint()
    rules = copy.deepcopy(macro.RULES)
    rules[0]["tag_bull"] = "（改过的多头说法）"
    rules[0]["tag_bear"] = "（改过的空头说法）"
    rules[0]["why"] = "（改过的解释文案，完全不涉及阈值）"
    assert _fp_with(monkeypatch, rules=rules) == base


# ── ★ 边界：影响分数的必须参与 ────────────────────────────────────────

def test_threshold_change_changes_fingerprint(monkeypatch):
    """改阈值 ⇒ 必须变（这是最常被偷偷改的东西）。"""
    base = macro.rules_fingerprint()
    rules = copy.deepcopy(macro.RULES)
    # 找一条带 bull 的规则改阈值
    for r in rules:
        if r.get("bull"):
            r["bull"] = dict(r["bull"], v=float(r["bull"]["v"]) + 0.1)
            break
    assert _fp_with(monkeypatch, rules=rules) != base


def test_strength_change_changes_fingerprint(monkeypatch):
    """改强度 ⇒ 必须变（强度直接进加权分）。"""
    base = macro.rules_fingerprint()
    rules = copy.deepcopy(macro.RULES)
    rules[0]["strength"] = float(rules[0]["strength"]) + 0.5
    assert _fp_with(monkeypatch, rules=rules) != base


def test_group_weight_change_changes_fingerprint(monkeypatch):
    """改组权重 ⇒ 必须变（组权重直接乘进总分）。"""
    base = macro.rules_fingerprint()
    w = dict(macro.GROUP_WEIGHTS)
    k = next(iter(w))
    w[k] = round(w[k] + 0.01, 4)
    assert _fp_with(monkeypatch, weights=w) != base


def test_rule_added_or_removed_changes_fingerprint(monkeypatch):
    """增删规则 ⇒ 必须变（分母变了，分数会变）。"""
    base = macro.rules_fingerprint()
    assert _fp_with(monkeypatch, rules=macro.RULES[:-1]) != base
    extra = copy.deepcopy(macro.RULES) + [{
        "id": "test_extra_rule", "group": "china", "metric": "a50.change_pct",
        "bull": {"op": ">", "v": 9.9}, "bear": None, "strength": 1.0,
    }]
    assert _fp_with(monkeypatch, rules=extra) != base


# ── 与快照的契约 ───────────────────────────────────────────────────────

def test_snapshot_declares_fingerprint_field():
    """`get_macro_snapshot` 的输出里必须有 `rules_fingerprint`。

    这里只查源码常量级的保证（真调 get_macro_snapshot 会打网络）：
    `macro_signal_log` 与 daily snapshot 都靠这个字段留痕。
    """
    import inspect
    src = inspect.getsource(macro.get_macro_snapshot)
    assert '"rules_fingerprint"' in src, "快照未声明 rules_fingerprint"
    assert '"rules_version"' in src


# ── ★ 漂移检测：光验证「没漂移」等于没验证 ─────────────────────────────

def _snap(ver, fp):
    return {"rules_version": ver, "rules_fingerprint": fp}


def test_drift_detected_when_same_version_has_two_fingerprints():
    """核心用例：同一 rules_version 下出现两个指纹 ⇒ **必须报漂移**。

    这正是 v4 那次（`us10y_high` 4.6→4.7）若忘了 bump 版本号该被抓到的情形。
    """
    rows = [
        ("2026-10-12", _snap("macro-rules-v4", "aaaaaaaaaaaa")),
        ("2026-10-13", _snap("macro-rules-v4", "aaaaaaaaaaaa")),
        ("2026-10-14", _snap("macro-rules-v4", "bbbbbbbbbbbb")),   # ← 偷偷改了阈值
        ("2026-10-15", _snap("macro-rules-v4", "bbbbbbbbbbbb")),
    ]
    rep = macro.fingerprint_drift(rows)
    assert "macro-rules-v4" in rep["drifts"]
    assert set(rep["drifts"]["macro-rules-v4"]) == {"aaaaaaaaaaaa", "bbbbbbbbbbbb"}
    assert rep["by_version"]["macro-rules-v4"]["aaaaaaaaaaaa"] == [
        "2026-10-12", "2026-10-13"]
    assert rep["by_version"]["macro-rules-v4"]["bbbbbbbbbbbb"] == [
        "2026-10-14", "2026-10-15"]


def test_version_bump_is_not_a_drift():
    """不同版本号、不同指纹 = **正常版本升级**，不该报漂移。"""
    rows = [
        ("2026-10-09", _snap("macro-rules-v3", "aaaaaaaaaaaa")),
        ("2026-10-12", _snap("macro-rules-v4", "bbbbbbbbbbbb")),
    ]
    rep = macro.fingerprint_drift(rows)
    assert rep["drifts"] == {}


def test_current_fp_different_from_latest_is_not_a_drift():
    """★ 误判边界：**当前指纹 ≠ 最新快照指纹** 不构成漂移。

    刚改完规则、日批还没重算时天天都会出现这个情况。
    如果拿它当判据，会产生大量误报，机制很快就会被忽略。
    判据只能是「**同一版本号下出现多个指纹**」。
    """
    rows = [
        ("2026-11-14", _snap("macro-rules-v4", "aaaaaaaaaaaa")),
    ]
    rep = macro.fingerprint_drift(rows)
    assert rep["drifts"] == {}, "单一历史指纹不该报漂移（哪怕与当前规则已不同）"


def test_legacy_snapshots_without_fingerprint_are_counted():
    """指纹机制上线前的快照没有该字段 ⇒ 归入 no_fingerprint，不报错也不算漂移。"""
    rows = [
        ("2026-08-24", {"rules_version": "macro-rules-v1"}),
        ("2026-09-21", {"rules_version": "macro-rules-v3"}),
        ("2026-11-14", _snap("macro-rules-v4", "cccccccccccc")),
    ]
    rep = macro.fingerprint_drift(rows)
    assert rep["no_fingerprint"] == ["2026-08-24", "2026-09-21"]
    assert rep["drifts"] == {}


def test_drift_report_handles_empty_and_malformed():
    """空输入 / 脏数据必须容错（审计脚本不该因为一行坏 JSON 就崩）。"""
    assert macro.fingerprint_drift([])["drifts"] == {}
    assert macro.fingerprint_drift(None)["drifts"] == {}
    rep = macro.fingerprint_drift([("2026-11-14", "not-a-dict"), ("bad", None)])
    assert rep["drifts"] == {}
