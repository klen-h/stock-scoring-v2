#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法准入闸门单元测试（阴跌子档 + 扫描层放宽 B 方案）
================================================================================

覆盖两条 2026-09-13 新逻辑：
  1. 阴跌子档（`STRATEGY_GRIND_GATE`）：neutral + ma_trend=down → 入场层全禁
  2. 扫描层放宽（`STRATEGY_WIDE_SCAN`，B 方案）：扫描层只判 base 类型准入，
     阴跌闸门/高波白名单只作用于入场与推送层 → 阴跌段照常扫描攒样本

设计：**不依赖数据库/网络** —— monkeypatch 掉 `detect_market_regime`，
      纯逻辑验证。`is_strategy_admitted` 内部只读该函数的 regime/ma_trend/
      volatility_regime 三个字段，故 mock 这三个即可。

运行方式：cd backend && python test_grind_gate.py

★ 注意：标签一律用 ASCII（[1] 而非圈号）—— Windows GBK 控制台编码不了
  ⑪ 以上的圈号（U+246A+），会抛 UnicodeEncodeError 中断测试（已踩坑）。
================================================================================
"""

import os
import sys

# 兜底：控制台编码非 UTF-8（Windows GBK）时不因非 ASCII 输出崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import app.strategies.market_regime as mr        # noqa: E402

TREND = "dragon_turnaround"          # 趋势类
OSC = "single_yang_unbroken"         # 震荡类
OSC_NOT_WL = "ma_convergence_breakout"   # 震荡类，且不在 (neutral,high) 白名单
OSC_IN_WL = "morning_star"           # 震荡类，且在高波白名单内

_passed = 0
_failed = 0


def _set_regime(regime, ma_trend="flat", volatility="normal"):
    """替换 detect_market_regime，返回固定市况快照。"""
    mr.detect_market_regime = lambda index_code="sh000300": {
        "regime": regime, "ma_trend": ma_trend,
        "volatility_regime": volatility,
    }


def _admit(name, for_scan=False):
    return mr.is_strategy_admitted(name, for_scan=for_scan)


def check(label, name, for_scan, want):
    got, reason, _, _ = _admit(name, for_scan=for_scan)
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  [OK]   {label}\n         admitted={got}（{reason}）")
    else:
        _failed += 1
        print(f"  [FAIL] {label}\n         admitted={got}，期望 {want}（{reason}）")


def main():
    global _passed, _failed
    orig_grind, orig_wide = mr.STRATEGY_GRIND_GATE, mr.STRATEGY_WIDE_SCAN

    print("=== 一、阴跌子档：入场层（for_scan=False）===")
    _set_regime("neutral", "down", "normal")
    check("[1] 震荡+MA向下 → 入场层全禁", OSC, False, False)

    _set_regime("neutral", "up", "normal")
    check("[2] 震荡+MA向上 → 正常放行震荡类", OSC, False, True)

    _set_regime("neutral", "down", "normal")
    mr.STRATEGY_GRIND_GATE = False
    check("[3] 阴跌但 GRIND_GATE=off → 回滚放行", OSC, False, True)
    mr.STRATEGY_GRIND_GATE = orig_grind

    print("\n=== 二、扫描层放宽（B 方案，for_scan=True）===")
    _set_regime("neutral", "down", "normal")
    check("[4] 震荡+MA向下 → 扫描层照常（攒样本）", OSC, True, True)

    mr.STRATEGY_WIDE_SCAN = False
    check("[5] 扫描层但 WIDE_SCAN=off → 回退完整判定（禁）", OSC, True, False)
    mr.STRATEGY_WIDE_SCAN = orig_wide

    _set_regime("neutral", "up", "high")
    check("[6] 高波+非白名单震荡类 → 扫描层照常（不受白名单限制）",
          OSC_NOT_WL, True, True)

    print("\n=== 三、高波白名单仍作用于入场层 ===")
    _set_regime("neutral", "up", "high")
    check("[7] 高波+非白名单战法 → 入场层禁", OSC_NOT_WL, False, False)
    check("[8] 高波+白名单内(morning_star) → 入场层放行", OSC_IN_WL, False, True)

    print("\n=== 四、base 类型准入（扫描层也不放宽）===")
    _set_regime("offensive", "up", "normal")
    check("[9] 进攻市+趋势类 → 扫描层放行", TREND, True, True)
    check("[10] 进攻市+震荡类 → 类型不符，扫描层也禁", OSC, True, False)

    _set_regime("defensive", "down", "high")
    check("[11] 防御市 → 扫描层也全禁（base 层）", OSC, True, False)

    print("\n=== 五、边界 ===")
    _set_regime("unknown")
    check("[12] 市场状态未知 → 禁", OSC, True, False)
    _set_regime("neutral", "up", "normal")
    check("[13] 未分类战法 → 禁", "not_a_strategy", False, False)

    # 还原（脚本可被 import 而不污染）
    mr.STRATEGY_GRIND_GATE = orig_grind
    mr.STRATEGY_WIDE_SCAN = orig_wide

    print(f"\n{'=' * 60}\n通过 {_passed} / 失败 {_failed}")
    assert _failed == 0, f"有 {_failed} 个场景未通过"
    print("ASSERTION PASSED")


if __name__ == "__main__":
    main()
