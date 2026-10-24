# -*- coding: utf-8 -*-
"""红线回归：缺失≠0（BK-34）+ 观察器/解读类无指令式文案（BK-42/BK-58，GUARDRAILS #5）。"""
import io
import os

from app.backtest.engine import compute_metrics
from app.backtest.strategies import _benchmark_ret
from app.scoring.ranking_history import _trust_advice

CURVE = [{"date": "2026-01-%02d" % d, "nav": 1 + 0.01 * d, "ret": 1.0} for d in range(1, 6)]
TRADES = [{"pnl_pct": 2.0, "hold_days": 3, "exit_reason": "target"}]


# ---------- BK-34：基准缺失 ⇒ None（缺失 ≠ 0）----------

def test_benchmark_ret_missing_data_returns_none():
    # prices_map 注入空序列 ⇒ 无远程查询的纯路径
    assert _benchmark_ret("2020-01-01", "2020-01-05",
                          prices_map={"sh000300": []}) is None


def test_compute_metrics_none_benchmark_yields_none_fields():
    m = compute_metrics(TRADES, CURVE, None)
    assert m["benchmark_return"] is None
    assert m["excess_return"] is None


def test_compute_metrics_with_benchmark_still_works():
    m = compute_metrics(TRADES, CURVE, 0.05)
    assert m["benchmark_return"] == 5.0
    assert abs(m["excess_return"] - (m["total_return"] - 5.0)) < 0.01


# ---------- BK-42：可信度建议只做事实描述 ----------

def test_trust_advice_has_no_directive_wording():
    for grade in ("A+", "A", "B", "C", "Z"):
        txt = _trust_advice(grade, 3)
        for banned in ("放心买", "重仓", "轻仓", "不建议操作", "建议观望"):
            assert banned not in txt, (grade, txt)
        assert "一致性" in txt, txt


# ---------- BK-58：教练文案无指令式祈使句（源码级禁词锁）----------

def test_coach_rules_no_imperative_clauses():
    src = io.open(os.path.join(os.path.dirname(__file__), "..", "..", "app", "coach", "rules.py"),
                  encoding="utf-8").read()
    for banned in ("按纪律离场", "优先在反弹中减仓", "只减不加", "从准空仓向中性加仓",
                   "准空仓运行"):
        assert banned not in src, "教练文案仍含指令式表述：%s" % banned
