# -*- coding: utf-8 -*-
"""广度背离：纯函数 compute / 分档 classify（2026-11-22）。

为什么用纯函数测：`snapshot()` 依赖库，而口径正确性全在 compute/classify 里。
11-20 的实测值（等权 -4.18 / 加权 -1.44 / gap 2.74 / 92% 下跌 ⇒ severe）
用构造数据复现，防止阈值或加权公式被改坏。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app import breadth_divergence as bd


def _rows(*pairs):
    return [{"chg_pct": c, "float_cap_yi": k} for c, k in pairs]


def test_compute_equal_vs_cap_weighted():
    """加权必须被大市值主导：1 只大盘 +1%、9 只小盘 -5%。"""
    rows = _rows((1.0, 1000), *[(-5.0, 1) for _ in range(9)])
    s = bd.compute(rows)
    assert s["ok"] is True
    assert s["n"] == 10
    # 等权 = (1 - 45) / 10 = -4.4
    assert s["equal_pct"] == -4.4
    # 加权 ≈ (1000 - 45) / 1009 ≈ 0.95（大市值把它拉回正区间）
    assert 0.9 <= s["cap_pct"] <= 1.0
    # gap = 加权 - 等权 ≈ 5.35pp
    assert s["gap_pp"] >= 5.0
    assert s["up"] == 1 and s["down"] == 9
    assert s["down_ratio"] == 0.9


def test_compute_skips_invalid_rows():
    """chg_pct 缺失 / 市值为 0 的行必须剔除，不能按 0 参与（0=持平会稀释均值）。"""
    rows = [
        {"chg_pct": -4.0, "float_cap_yi": 100},
        {"chg_pct": None, "float_cap_yi": 100},
        {"chg_pct": -2.0, "float_cap_yi": 0},
        {"chg_pct": -6.0, "float_cap_yi": None},
        {"chg_pct": -2.0, "float_cap_yi": 100},
    ]
    s = bd.compute(rows)
    assert s["n"] == 2
    assert s["equal_pct"] == -3.0
    assert s["cap_pct"] == -3.0
    assert s["gap_pp"] == 0.0


def test_compute_empty_is_not_zero():
    """空数据必须 ok=False —— 返回 0 会被读成"完全一致"。"""
    s = bd.compute([])
    assert s["ok"] is False
    assert "error" in s


def test_classify_severe_requires_both_gap_and_breadth():
    """severe 需要「gap 大 **且** 普遍下跌」；只有 gap 大不算。"""
    sev = bd.classify({"gap_pp": 2.5, "down_ratio": 0.9})
    assert sev["level"] == "severe"
    # 同样 gap 但只一半下跌 ⇒ 只是 mild（不能夸大"个股在杀"）
    mild = bd.classify({"gap_pp": 2.5, "down_ratio": 0.5})
    assert mild["level"] == "mild"


def test_classify_reverse_and_none():
    assert bd.classify({"gap_pp": -2.0, "down_ratio": 0.1})["level"] == "reverse"
    assert bd.classify({"gap_pp": 0.3, "down_ratio": 0.5})["level"] == "none"


def test_classify_missing_gap_is_unknown():
    assert bd.classify({"gap_pp": None})["level"] == "unknown"


def test_reproduces_1120_shape():
    """复现 11-20 的形状：少数大市值抗跌 + 绝大多数下跌 ⇒ severe。"""
    # 30 只：1 只 +1.5%（市值 2000），29 只 -4.5%（各 10）
    rows = _rows((1.5, 2000), *[(-4.5, 10) for _ in range(29)])
    s = bd.compute(rows)
    cls = bd.classify(s)
    assert s["down_ratio"] > 0.9
    assert s["cap_pct"] > s["equal_pct"]          # 加权明显好于等权
    assert cls["level"] == "severe"
