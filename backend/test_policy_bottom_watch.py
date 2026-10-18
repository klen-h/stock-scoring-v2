#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】市场底确认清单 单元测试（五条判据 + insufficient 语义 + 跨阈幂等）
================================================================================
设计：**不依赖数据库/网络** —— 直接 monkeypatch 掉四个取数函数与两个 DB 辅助函数，
      纯逻辑验证判定与"数据不足 ≠ 不满足"的语义（与 `test_grind_gate.py` 同一做法）。

运行：cd backend && python test_policy_bottom_watch.py

★ 标签一律 ASCII（[1] 而非圈号）：Windows GBK 控制台编码不了高圈号，会中断测试。
================================================================================
"""

import os
import sys
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.policy_bottom_watch as pbw      # noqa: E402

FAILS = []
PASSES = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASSES
    if cond:
        PASSES += 1
        print(f"  [OK] {name}")
    else:
        FAILS.append(f"{name} :: {detail}")
        print(f"  [FAIL] {name} :: {detail}")


class Patch:
    """临时替换模块属性（with 退出自动还原）。"""

    def __init__(self, **kv):
        self.kv = kv

    def __enter__(self):
        self.old = {k: getattr(pbw, k) for k in self.kv}
        for k, v in self.kv.items():
            setattr(pbw, k, v)
        return self

    def __exit__(self, *a):
        for k, v in self.old.items():
            setattr(pbw, k, v)
        return False


@contextmanager
def scenario(amount, breadth, weekly, macro, prev=None, manual=None):
    """把四个取数函数 + 两个 DB 辅助替换成固定值，跑完自动还原。"""
    with Patch(
        _amount_series=lambda limit=12, as_of=None: (amount, "test"),
        _breadth_series=lambda limit=10, as_of=None: (breadth, []),
        _chinext_weekly=lambda as_of=None: weekly,
        _macro_snapshot=lambda as_of=None: macro,
        _prev_checked=lambda day: prev,
        _manual_of=lambda day: (manual or {}),
    ):
        yield


GOOD_AMOUNT = [("d1", 12000.0), ("d2", 12500.0), ("d3", 12800.0)]
GOOD_BREADTH = [("d2", 0.9), ("d3", 0.95)]
UP_WEEKLY = {"checked": True, "insufficient": False, "detail": "周线转涨"}
CALM_MACRO = {"ok": True, "us10y": 4.8, "brent": 100.0}


def case_all_pass():
    print("\n[1] 五条全达标 -> 5/5，且首次跨过 4（prev=None）")
    with scenario(GOOD_AMOUNT, GOOD_BREADTH, UP_WEEKLY, CALM_MACRO,
                  manual={"checked": True, "note": "汇金公告"}):
        d = pbw.evaluate("2026-10-16")
    check("n_checked == 5", d["n_checked"] == 5, str(d["n_checked"]))
    check("crossed_4 == True", d["crossed_4"] is True, str(d["crossed_4"]))
    check("五条全 checked", all(v["checked"] for v in d["items"].values()), "")


def case_partial():
    print("\n[2] 只有 3 条达标 -> 3/5，不跨阈，且给出仓位提示")
    with scenario(
        amount=[("d1", 14000.0), ("d2", 14500.0), ("d3", 15000.0)],   # 不达标
        breadth=[("d2", 0.5), ("d3", 0.6)],                            # 不达标
        weekly=UP_WEEKLY,
        macro=CALM_MACRO,
        manual={"checked": True, "note": "公告"},
    ):
        d = pbw.evaluate("2026-10-16")
    check("n_checked == 3", d["n_checked"] == 3, str(d["n_checked"]))
    check("crossed_4 == False", d["crossed_4"] is False, "")
    check("提示含 30%", "30" in (d["position_hint"] or ""), d["position_hint"])


def case_insufficient_amount():
    print("\n[3] 成交额历史只有 2 天（需 3）-> insufficient=True 且 checked=False")
    with scenario(amount=[("d2", 12000.0), ("d3", 12500.0)],
                  breadth=GOOD_BREADTH, weekly=UP_WEEKLY, macro=CALM_MACRO):
        d = pbw.evaluate("2026-10-16")
    it = d["items"]["amount_dry_up"]
    check("insufficient == True", it.get("insufficient") is True, str(it))
    check("checked == False", it["checked"] is False, "")
    check("detail 含'历史不足'", "历史不足" in (it.get("detail") or ""), it.get("detail"))


def case_insufficient_breadth():
    print("\n[4] 涨跌比有效日只有 1 天（需 2）-> insufficient=True")
    with scenario(amount=GOOD_AMOUNT, breadth=[("d3", 0.9)],
                  weekly=UP_WEEKLY, macro=CALM_MACRO):
        d = pbw.evaluate("2026-10-16")
    it = d["items"]["breadth_ratio_2d"]
    check("insufficient == True", it.get("insufficient") is True, str(it))
    check("checked == False", it["checked"] is False, "")


def case_macro_or():
    print("\n[5] ⑤ 是 OR 逻辑：10Y 5.2% + 油 90 -> 达标（油价缓和）")
    with scenario(GOOD_AMOUNT, GOOD_BREADTH, UP_WEEKLY,
                  {"ok": True, "us10y": 5.2, "brent": 90.0}):
        d = pbw.evaluate("2026-10-16")
    check("⑤ checked == True", d["items"]["us10y_or_oil"]["checked"] is True,
          str(d["items"]["us10y_or_oil"]))

    with scenario(GOOD_AMOUNT, GOOD_BREADTH, UP_WEEKLY,
                  {"ok": True, "us10y": 5.2, "brent": 100.0}):
        d2 = pbw.evaluate("2026-10-16")
    check("双高 -> checked == False", d2["items"]["us10y_or_oil"]["checked"] is False, "")

    with scenario(GOOD_AMOUNT, GOOD_BREADTH, UP_WEEKLY,
                  {"ok": False, "detail": "无数据"}):
        d3 = pbw.evaluate("2026-10-16")
    check("无数据 -> insufficient", d3["items"]["us10y_or_oil"].get("insufficient") is True, "")


def case_crossed_edge():
    print("\n[6] 跨阈边界：昨日已 4 条 -> 今日再 4 条不算'首次跨过'（不重复推）")
    # 4 条达标：成交地量 ✓ / 周线 ✓ / ④ 不达标 / ⑤ ✓ / ① 人工 ✓
    with scenario(GOOD_AMOUNT, [("d2", 0.5), ("d3", 0.6)], UP_WEEKLY, CALM_MACRO,
                  manual={"checked": True}, prev=4):
        d = pbw.evaluate("2026-10-16")
    check("n_checked == 4", d["n_checked"] == 4, str(d["n_checked"]))
    check("crossed_4 == False（昨日已跨过）", d["crossed_4"] is False, str(d["crossed_4"]))


def case_weekly_flag():
    print("\n[7] 创业板周线：下跌 -> checked=False（且 detail 带涨跌幅）")
    with scenario(GOOD_AMOUNT, GOOD_BREADTH,
                  {"checked": False, "insufficient": False, "detail": "本周 -8.90%"},
                  CALM_MACRO):
        d = pbw.evaluate("2026-10-16")
    it = d["items"]["chinext_weekly"]
    check("checked == False", it["checked"] is False, "")
    check("detail 保留", "-8.90%" in (it.get("detail") or ""), it.get("detail"))


def main():
    print("=" * 66)
    print("市场底确认清单 单元测试")
    print("=" * 66)
    case_all_pass()
    case_partial()
    case_insufficient_amount()
    case_insufficient_breadth()
    case_macro_or()
    case_crossed_edge()
    case_weekly_flag()
    print("\n" + "=" * 66)
    if FAILS:
        print(f"失败 {len(FAILS)} / 通过 {PASSES}")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print(f"[OK] 全部通过（{PASSES} 项断言）")


if __name__ == "__main__":
    main()
