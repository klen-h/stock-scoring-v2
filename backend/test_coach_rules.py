#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】Coach 规则求值器单元测试（W1）
================================================================================

覆盖 9 条规则的触发/不触发，**不依赖 DB / 网络**：
  - 通过 monkeypatch `app.coach.rules.build_context` 的替代品——直接传 ctx，
    因此不读行情缓存、不读持仓表、不连数据库。
  - `distribution_cut` 依赖 K 线，测试里替换 `_distribution` 的打桩。

运行方式：cd backend && python test_coach_rules.py

★ 标签一律 ASCII（[n]），并兜底 stdout 编码 —— Windows GBK 控制台编码不了
  圈号 ⑪+（U+246A+），会抛 UnicodeEncodeError 中断测试。
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

import app.coach.rules as cr        # noqa: E402

_passed = 0
_failed = 0


def _pos(**kw) -> dict:
    base = {"code": "000001", "name": "平安银行", "fill_price": 10.0,
            "stop_loss": 9.3, "price": 10.0, "pnl_pct": 0.0, "hold_days": 0,
            "ret20": 0.0, "dist_stop_pct": 7.5, "shares": 1000, "source": "paper"}
    base.update(kw)
    return base


def _ctx(positions=None, regime=None, breadth=None, macro=None,
         margin=None, day_pnl=None) -> dict:
    return {"positions": positions or [], "regime": regime or {},
            "breadth": breadth or {}, "macro": macro or {},
            "margin": margin or {}, "day_pnl_pct": day_pnl}


def _run(rule_id: str, ctx: dict, params=None):
    cfg = {"rules": [{"id": rule_id, "label": rule_id, "enabled": True,
                      "push": True, "severity": "warn", "params": params or {}}]}
    return cr.evaluate_all("all", config=cfg, ctx=ctx)


def check(label: str, hit: bool, want: bool, advices=None):
    global _passed, _failed
    if hit == want:
        _passed += 1
        detail = f" | {advices[0].message.splitlines()[0][:60]}" if advices else ""
        print(f"  [OK]   {label}: 触发={hit}{detail}")
    else:
        _failed += 1
        print(f"  [FAIL] {label}: 触发={hit}，期望 {want}")


def main():
    global _passed, _failed
    orig_dist = cr._distribution

    print("=== 一、持仓纪律（逐持仓）===")
    a = _run("stop_loss_hit", _ctx(positions=[_pos(price=9.20)]))
    check("[1] 现价破止损 → 触发", bool(a), True, a)
    a = _run("stop_loss_hit", _ctx(positions=[_pos(price=10.0)]))
    check("[2] 现价在止损上方 → 不触发", bool(a), False)

    a = _run("hold_3d_review", _ctx(positions=[_pos(hold_days=3)]))
    check("[3] 持有满3日 → 触发", bool(a), True, a)
    a = _run("hold_3d_review", _ctx(positions=[_pos(hold_days=1)]))
    check("[4] 持有1日 → 不触发", bool(a), False)

    a = _run("loss_over_7pct", _ctx(positions=[_pos(pnl_pct=-8.0)]))
    check("[5] 浮亏8% → 触发", bool(a), True, a)
    a = _run("loss_over_7pct", _ctx(positions=[_pos(pnl_pct=-5.0)]))
    check("[6] 浮亏5% → 不触发", bool(a), False)

    a = _run("crowded_trim", _ctx(positions=[_pos(ret20=35.0)]))
    check("[7] 近20日涨35% → 触发", bool(a), True, a)
    a = _run("crowded_trim", _ctx(positions=[_pos(ret20=10.0)]))
    check("[8] 近20日涨10% → 不触发", bool(a), False)

    cr._distribution = lambda code: (True, {"price_pos": 0.90, "reason": "高位+主力出货"})
    a = _run("distribution_cut", _ctx(positions=[_pos()]))
    check("[9] 高位+主力出货 → 触发（不等7%）", bool(a), True, a)
    cr._distribution = lambda code: (False, {})
    a = _run("distribution_cut", _ctx(positions=[_pos()]))
    check("[10] 非出货 → 不触发", bool(a), False)

    print("\n=== 二、市场闸门（市场级）===")
    a = _run("gate_no_add", _ctx(regime={"state": "neutral_bearish", "ma_trend": "down"}))
    check("[11] regime=nb → 不支持加仓", bool(a), True, a)
    a = _run("gate_no_add", _ctx(regime={"state": "offensive"}))
    check("[12] regime=offensive → 不触发", bool(a), False)

    a = _run("gate_reduce", _ctx(macro={"us10y": 5.2, "dxy": 101.0}))
    check("[13] 10Y>5% 且 DXY>100 → 减仓", bool(a), True, a)
    a = _run("gate_reduce", _ctx(macro={"us10y": 4.3, "dxy": 97.0}))
    check("[14] 10Y/DXY 未达 → 不触发", bool(a), False)
    a = _run("gate_reduce", _ctx(margin={"chg5": -150.0, "score": 22.0}))
    check("[15] 两融净减+情绪寒冷 → 减仓", bool(a), True, a)
    a = _run("gate_reduce", _ctx(margin={"chg5": -150.0, "score": 55.0}))
    check("[16] 两融净减但情绪不冷 → 不触发", bool(a), False)

    orig_prev = cr._gate_add_prev_day_hit
    cr._gate_add_prev_day_hit = lambda today: True
    a = _run("gate_add", _ctx(breadth={"up_down_ratio": 0.85, "limit_down": 5}))
    check("[17] 命中+昨日命中 → 连续2日确认", bool(a), True, a)
    if a and "确认（连续 2 日）" in a[0].message:
        print("      文案含「确认（连续 2 日）」 ✓")
        _passed += 1
    cr._gate_add_prev_day_hit = lambda today: False
    a = _run("gate_add", _ctx(breadth={"up_down_ratio": 0.85, "limit_down": 5}))
    check("[17b] 命中+昨日未命中 → 第1日提示", bool(a), True, a)
    if a and "第 1 日" in a[0].message:
        print("      文案含「第 1 日」 ✓")
        _passed += 1
    cr._gate_add_prev_day_hit = orig_prev
    a = _run("gate_add", _ctx(breadth={"up_down_ratio": 0.60, "limit_down": 30}))
    check("[18] 涨跌比0.60且跌停30家 → 不触发", bool(a), False)

    print("\n=== 三、情绪熔断 + 开关/边界 ===")
    a = _run("emotion_fuse", _ctx(day_pnl=-2.5))
    check("[19] 当日浮亏2.5% → 熔断标记", bool(a), True, a)
    a = _run("emotion_fuse", _ctx(day_pnl=-0.5))
    check("[20] 当日浮亏0.5% → 不触发", bool(a), False)

    cfg = {"rules": [{"id": "stop_loss_hit", "label": "x", "enabled": False,
                      "push": True, "severity": "warn", "params": {}}]}
    a = cr.evaluate_all("all", config=cfg, ctx=_ctx(positions=[_pos(price=9.0)]))
    check("[21] enabled=false → 不评估", bool(a), False)

    a = cr.evaluate_all("light", ctx=_ctx(positions=[_pos()]))
    check("[22] light 层不含 heavy 规则（ret20 缺失也不报错）", bool(a), False)

    print("\n=== 四、真实持仓（user_portfolio 来源）===")
    a = _run("stop_loss_hit", _ctx(positions=[_pos(price=9.20, source="real")]))
    check("[23] 真实持仓破默认止损 → 触发", bool(a), True, a)
    if a:
        assert "真实持仓" in a[0].message, "真实持仓文案应带「真实持仓」前缀"
        print("      文案带「真实持仓」前缀 ✓")
        _passed += 1
    a = _run("stop_loss_hit", _ctx(positions=[_pos(price=9.20)]))
    check("[24] 模拟盘破止损 → 文案不带「真实持仓」", bool(a), True, a)
    if a:
        assert "真实持仓" not in a[0].message, "模拟盘文案不应带「真实持仓」"
        print("      模拟盘文案无前缀 ✓")
        _passed += 1

    cr._distribution = orig_dist
    print(f"\n{'=' * 60}\n通过 {_passed} / 失败 {_failed}")
    assert _failed == 0, f"有 {_failed} 个场景未通过"
    print("ASSERTION PASSED")


if __name__ == "__main__":
    main()
