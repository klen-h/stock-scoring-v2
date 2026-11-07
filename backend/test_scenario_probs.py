#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】情景概率归一化单元测试（llm.normalize_scenario_probs）
================================================================================
背景（2026-11-07）：
  诊断提示词原文写「所有情景之和在 85-115 之间即可（不必严格等于 100）」
  ⇒ 一份概率账连加总为 100 都不要求。实测存量 50 条：
      Σ=100 占 54%，85~115 占 30%，<85 或 >115 占 14%（最差 Σ=60），
      偏离超过 ±5 的占 34.7% ⇒ 拿它做任何校准都无效。
  现改为：能给出数字就强制 Σ=100（原值留 `probability_numeric_raw` 可追溯），
  给不出就整体不归一（**绝不拿 0 顶上** —— 0 会被读成"该情景不可能"）。

运行方式：cd backend && python test_scenario_probs.py
（纯函数、不连库、不调 LLM）
================================================================================
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.flash.llm import normalize_scenario_probs  # noqa: E402

_passed = 0
_failed = 0


def _check(label, scenarios, want_sum=None, want_normalized=None,
           want_invalid=False, want_values=None):
    global _passed, _failed
    a = {"scenarios": [dict(s) for s in scenarios]}
    normalize_scenario_probs(a)
    vals = [s.get("probability_numeric") for s in a["scenarios"]]
    meta = a.get("_scenario_prob") or {}
    errs = []

    if want_sum is not None:
        got = sum(v for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool))
        if abs(got - want_sum) > 0.05:
            errs.append(f"Σ={got} 期望 {want_sum}")
    if want_normalized is not None and meta.get("normalized") != want_normalized:
        errs.append(f"normalized={meta.get('normalized')} 期望 {want_normalized}")
    if want_invalid and not meta.get("invalid"):
        errs.append("应标记 invalid")
    if want_values is not None and vals != want_values:
        errs.append(f"值={vals} 期望 {want_values}")

    if errs:
        _failed += 1
        print(f"  [FAIL] {label}: " + "；".join(errs))
    else:
        _passed += 1
        print(f"  [OK]   {label}: {vals}  {meta}")


def main():
    print("\n=== 一、加总正确 → 不缩放 ===")
    _check("[1] 60/30/10 原样保留", [
        {"scenario_name": "A", "probability_numeric": 60},
        {"scenario_name": "B", "probability_numeric": 30},
        {"scenario_name": "C", "probability_numeric": 10},
    ], want_sum=100, want_normalized=False, want_values=[60, 30, 10])

    print("\n=== 二、加总偏离 → 按比例缩放 ===")
    _check("[2] Σ=115 → 52.2/30.4/17.4", [
        {"scenario_name": "A", "probability_numeric": 60},
        {"scenario_name": "B", "probability_numeric": 35},
        {"scenario_name": "C", "probability_numeric": 20},
    ], want_sum=100, want_normalized=True)

    _check("[3] Σ=85 → 58.8/41.2", [
        {"scenario_name": "A", "probability_numeric": 50},
        {"scenario_name": "B", "probability_numeric": 35},
    ], want_sum=100, want_normalized=True)

    _check("[4] Σ=60（存量最差值）→ 归一", [
        {"scenario_name": "A", "probability_numeric": 40},
        {"scenario_name": "B", "probability_numeric": 20},
    ], want_sum=100, want_normalized=True)

    print("\n=== 三、字符串数字 → 解析后写回数值 ===")
    _check("[5] '70%'/'30' → 70/30（不是原字符串）", [
        {"scenario_name": "A", "probability_numeric": "70%"},
        {"scenario_name": "B", "probability_numeric": "30"},
    ], want_sum=100, want_normalized=False, want_values=[70.0, 30.0])

    print("\n=== 四、缺失/非法 → 整体不归一，且不拿 0 顶替 ===")
    _check("[6] 少一个 → invalid", [
        {"scenario_name": "A", "probability_numeric": 60},
        {"scenario_name": "B"},
    ], want_invalid=True, want_normalized=False)

    _check("[7] 含 0 → invalid（0 不等于'不可能'）", [
        {"scenario_name": "A", "probability_numeric": 60},
        {"scenario_name": "B", "probability_numeric": 0},
    ], want_invalid=True, want_normalized=False)

    _check("[8] bool 挡掉", [
        {"scenario_name": "A", "probability_numeric": True},
        {"scenario_name": "B", "probability_numeric": 40},
    ], want_invalid=True, want_normalized=False)

    print("\n=== 五、无 scenarios → 原样返回，不报错 ===")
    a = {"market_mood": "x"}
    normalize_scenario_probs(a)
    if "scenarios" not in a:
        globals()["_passed"] += 1
        print("  [OK]   [9] 无 scenarios 不抛异常")
    else:
        globals()["_failed"] += 1
        print("  [FAIL] [9] 无 scenarios 时不应创建字段")

    print(f"\n{'=' * 60}\n通过 {_passed} / 失败 {_failed}")
    assert _failed == 0, f"有 {_failed} 个场景未通过"
    print("ASSERTION PASSED")


if __name__ == "__main__":
    main()
