#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】萨姆规则 · 纯函数单测（offline，不连库/不调外部接口）
================================================================================
覆盖 `sahm_rule.compute_sahm()` 与 `_iso_month()`：
  · 触发 / 不触发 / 恰好 0.50 边界
  · 数据不足 15 个月 ⇒ `insufficient` 且 `triggered is None`（**三态**）
  · 含 None / 非数值 ⇒ `invalid` 且 `triggered is None`（缺口不插值）
  · MA3 与"过去 12 月最低 MA3"的取值正确（手算对照）
  · 纯函数性：同输入两次结果相同、且不修改入参
  · `_iso_month()` 归一（`2026-11` / `2026/11/5` / 非法输入）

★ 本单测**只测纯函数**，不碰 `upsert_rate` / `load_series` / `evaluate`（那些要连库，
  项目离线单测的既有约定同 `test_macro_regime_class.py`）。

★★ 三态语义是本模块最容易被写错的地方：
  `triggered is None` 表示**无法判定**，调用方不得当成 False。
  下面 [6][7][8] 三条专门锁这个。

运行：cd backend && python test_sahm_rule.py
================================================================================
"""
import copy
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.sahm_rule import (  # noqa: E402
    COMPARE_WINDOW, MIN_POINTS, SAHM_THRESHOLD, _iso_month, compute_sahm,
)

_passed = 0
_failed = 0


def check(label, cond, detail=""):
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [OK]   {label}{(' | ' + detail) if detail else ''}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}{(' | ' + detail) if detail else ''}")


def main():
    print("=" * 78)
    print("萨姆规则 · 纯函数单测")
    print(f"常量：阈值={SAHM_THRESHOLD}pp  比较窗口={COMPARE_WINDOW}月  "
          f"最少点数={MIN_POINTS}")
    print("=" * 78)

    # ---------------------------------------------------------------- 1
    print("\n[1] 常量自洽")
    check("MIN_POINTS == 3 + 12", MIN_POINTS == 15, f"实际 {MIN_POINTS}")

    # ---------------------------------------------------------------- 2
    print("\n[2] 典型触发：12 个月 4.0% + 最近 3 个月 5.0%")
    rates = [4.0] * 12 + [5.0] * 3
    r = compute_sahm(rates)
    check("status == ok", r["status"] == "ok", r.get("detail", ""))
    check("current_ma3 == 5.0", abs(r["current_ma3"] - 5.0) < 1e-9,
          f"实际 {r.get('current_ma3')}")
    check("low_ma3 == 4.0", abs(r["low_ma3"] - 4.0) < 1e-9,
          f"实际 {r.get('low_ma3')}")
    check("sahm ≈ 1.0", abs(r["sahm"] - 1.0) < 1e-9, f"实际 {r.get('sahm')}")
    check("triggered is True", r["triggered"] is True)

    # ---------------------------------------------------------------- 3
    print("\n[3] 恰好 0.50 边界 ⇒ 触发（>= 而非 >）")
    r = compute_sahm([4.0] * 12 + [4.5] * 3)
    check("sahm == 0.50", abs(r["sahm"] - 0.5) < 1e-9, f"实际 {r.get('sahm')}")
    check("triggered is True", r["triggered"] is True)

    # ---------------------------------------------------------------- 4
    print("\n[4] 差一点不触发：0.49")
    r = compute_sahm([4.0] * 12 + [4.49] * 3)
    check("sahm ≈ 0.49", abs(r["sahm"] - 0.49) < 1e-6, f"实际 {r.get('sahm')}")
    check("triggered is False", r["triggered"] is False)

    # ---------------------------------------------------------------- 5
    print("\n[5] 完全平稳 ⇒ 0，不触发")
    r = compute_sahm([4.0] * 15)
    check("sahm == 0.0", abs(r["sahm"]) < 1e-9, f"实际 {r.get('sahm')}")
    check("triggered is False", r["triggered"] is False)

    # ---------------------------------------------------------------- 6
    print("\n[6] 数据不足（14 个月）⇒ insufficient + triggered is None")
    r = compute_sahm([4.0] * 14)
    check("status == insufficient", r["status"] == "insufficient")
    check("triggered is None（不是 False）", r["triggered"] is None)
    check("have == 14", r["have"] == 14, f"实际 {r.get('have')}")
    check("need == 15", r["need"] == 15)

    # ---------------------------------------------------------------- 7
    print("\n[7] 序列含 None ⇒ invalid + triggered is None（不插值）")
    r = compute_sahm([4.0] * 10 + [None] + [4.0] * 5)
    check("status == invalid", r["status"] == "invalid")
    check("triggered is None", r["triggered"] is None)

    # ---------------------------------------------------------------- 8
    print("\n[8] 空序列 ⇒ insufficient + triggered is None")
    r = compute_sahm([])
    check("status == insufficient", r["status"] == "insufficient")
    check("triggered is None", r["triggered"] is None)

    # ---------------------------------------------------------------- 9
    print("\n[9] 非数值项 ⇒ invalid")
    r = compute_sahm([4.0] * 14 + ["4.1x"])
    check("status == invalid", r["status"] == "invalid")

    # ---------------------------------------------------------------- 10
    print("\n[10] 纯函数性：两次结果一致且不改入参")
    src = [4.0] * 12 + [5.0] * 3
    snapshot = copy.deepcopy(src)
    a = compute_sahm(src)
    b = compute_sahm(src)
    check("两次 sahm 相同", a["sahm"] == b["sahm"])
    check("入参未被修改", src == snapshot)

    # ---------------------------------------------------------------- 11
    print("\n[11] 比较窗口取的是「过去 12 月最低 MA3」，不是单月最低")
    # 前 2 个月 3.0（低点），之后稳定 4.2：单月最低=3.0，MA3 最低≈3.47
    rates2 = [3.0, 3.0] + [4.2] * 13
    r = compute_sahm(rates2)
    check("status == ok", r["status"] == "ok")
    # MA3 最低出现在 i=2：(3+3+4.2)/3 = 3.4
    check("low_ma3 ≈ 3.4（=MA3 最低，非单月 3.0）",
          abs(r["low_ma3"] - 3.4) < 1e-6, f"实际 {r.get('low_ma3')}")

    # ---------------------------------------------------------------- 12
    print("\n[12] _iso_month 归一")
    cases = [("2026-11", "2026-11-01"), ("2026/11/5", "2026-11-01"),
             ("2026-11-01", "2026-11-01"), ("2026/11", "2026-11-01")]
    for raw, want in cases:
        got = _iso_month(raw)
        check(f"_iso_month({raw!r}) == {want!r}", got == want, f"实际 {got!r}")

    try:
        _iso_month("garbage")
        check("非法输入抛 ValueError", False, "没有抛异常")
    except ValueError:
        check("非法输入抛 ValueError", True)

    # ---------------------------------------------------------------- 汇总
    print("\n" + "=" * 78)
    print(f"通过 {_passed} / 失败 {_failed}")
    print("=" * 78)
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
