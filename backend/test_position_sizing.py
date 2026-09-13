#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】仓位建议引擎单元测试（W1.5）
================================================================================

覆盖市场层（总仓位上限 + 降档因子）与组合层（min(个股档位, 总上限)）。
不依赖 DB/网络：`_market_total` 是纯函数（直接传 ctx），组合层 mock 掉
`gate_evaluate` / `load_latest`。

运行方式：cd backend && python test_position_sizing.py
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

import app.coach.position_sizing as ps        # noqa: E402
import app.mainforce.state as mf_state        # noqa: E402

_passed = 0
_failed = 0


def check(label, got, want):
    global _passed, _failed
    if got == want:
        _passed += 1
        print(f"  [OK]   {label}: {got}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}: got {got}, 期望 {want}")


def main():
    global _passed, _failed
    print("=== 一、档位向下取整 ===")
    check("[1] 17 → 10", ps._snap(17), 10)
    check("[2] 20 → 20", ps._snap(20), 20)
    check("[3] 64 → 60", ps._snap(64), 60)
    check("[4] 3 → 0", ps._snap(3), 0)

    print("\n=== 二、市场层：基准（regime）===")
    t, r = ps._market_total({"regime": {"state": "offensive"}})
    check("[5] offensive 基准 80", t, 80)
    t, r = ps._market_total({"regime": {"state": "neutral"}})
    check("[6] neutral 基准 50", t, 50)
    t, r = ps._market_total({"regime": {"state": "neutral_bearish"}})
    check("[7] nb 基准 20", t, 20)
    t, r = ps._market_total({"regime": {"state": "defensive"}})
    check("[8] defensive 基准 0", t, 0)

    print("\n=== 三、市场层：降档因子 ===")
    t, r = ps._market_total({"regime": {"state": "neutral_bearish"},
                             "margin": {"chg5": -102.0}})
    check("[9] nb + 两融净减 → 20×0.85=17→10", t, 10)
    t, r = ps._market_total({"regime": {"state": "neutral"},
                             "macro": {"us10y": 5.5}})
    check("[10] neutral + 10Y>5% → 50×0.8=40", t, 40)
    t, r = ps._market_total({"regime": {"state": "neutral"},
                             "margin": {"score": 85.0}})
    check("[11] neutral + 情绪过热 → 50×0.7=35→30", t, 30)
    t, r = ps._market_total({"regime": {"state": "neutral"},
                             "breadth": {"limit_down": 150}})
    check("[12] neutral + 跌停150 → 50×0.7=35→30", t, 30)
    # 多重降档叠加
    t, r = ps._market_total({"regime": {"state": "neutral_bearish"},
                             "margin": {"chg5": -102.0},
                             "macro": {"us10y": 5.5}})
    check("[13] nb + 净减 + 10Y>5% → 20×0.85×0.8=13.6→10", t, 10)

    print("\n=== 四、组合层：suggested = min(个股, 总上限) ===")
    mf_state.load_latest = lambda codes: {}
    ps.gate_evaluate = lambda code, mf=None: {
        "position_pct": 50, "position_label": "半仓", "reasons": ["B✓ 不拥挤"]}
    r = ps.position_sizing(["000001", "000002"],
                           ctx={"regime": {"state": "neutral"}})
    check("[14] 总上限 50、个股 50 → suggested 50", r["total_limit_pct"], 50)
    check("[15] 两只都建议 50（不稀释）", all(p["suggested_pct"] == 50 for p in r["positions"]), True)

    # 个股意愿 > 总上限 → 被 cap
    ps.gate_evaluate = lambda code, mf=None: {
        "position_pct": 80, "position_label": "积极", "reasons": []}
    r = ps.position_sizing(["000001"], ctx={"regime": {"state": "neutral_bearish"}})
    check("[16] 总上限 20、个股 80 → suggested cap 到 20",
          r["positions"][0]["suggested_pct"], 20)

    print(f"\n{'=' * 60}\n通过 {_passed} / 失败 {_failed}")
    assert _failed == 0, f"有 {_failed} 个场景未通过"
    print("ASSERTION PASSED")


if __name__ == "__main__":
    main()
