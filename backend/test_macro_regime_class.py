#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】宏观状态概率账 · 纯函数单测（offline，不连库/不调 LLM）
================================================================================
覆盖 `macro_regime_class.compute()`：
  · 四种典型宏观场景各判对（滞胀 / 过热 / 衰退 / 软着陆）
  · 缺失证据「不进分母」（缺一半仍能算，且不改变已有判断方向）
  · 可评估类别 < 2 ⇒ insufficient（不给分布）
  · 全类得分 0 ⇒ insufficient（不拿均匀分布冒充结论）
  · Σdist = 1（容差 1e-6）
  · 纯函数性：同样输入两次结果相同、且不修改入参

运行：cd backend && python test_macro_regime_class.py
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

from app.macro_regime_class import CLASS_CN, compute  # noqa: E402

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


def top(res):
    d = res.get("dist") or {}
    if not d:
        return None
    return max(d, key=d.get)


def main():
    print("\n=== 一、四种典型场景 ===")

    stag = {"brent_chg5": 6, "us10y_chg5_bp": 20, "dxy_chg5": 1.5,
            "copper_chg5": -3, "nasdaq_chg5": -3, "vix": 24}
    r = compute(stag)
    check("[1] 油涨+长端涨+商品跌+股跌 → 滞胀",
          top(r) == "stag", f"{CLASS_CN.get(top(r))}  dist={r['dist']}")

    over = {"brent_chg5": 6, "us10y_chg5_bp": 20, "dxy_chg5": -1.5,
            "copper_chg5": 4, "nasdaq_chg5": 4, "vix": 15}
    r = compute(over)
    check("[2] 油涨+铜涨+股涨+美元跌 → 过热",
          top(r) == "overheat", f"{CLASS_CN.get(top(r))}  dist={r['dist']}")

    rec = {"brent_chg5": -6, "us10y_chg5_bp": -20, "dxy_chg5": 2,
           "copper_chg5": -6, "nasdaq_chg5": -6, "vix": 35}
    r = compute(rec)
    check("[3] 油跌+铜崩+股杀+VIX高 → 衰退",
          top(r) == "rec", f"{CLASS_CN.get(top(r))}  dist={r['dist']}")

    soft = {"brent_chg5": -2, "us10y_chg5_bp": -5, "dxy_chg5": 0.2,
            "copper_chg5": 1, "nasdaq_chg5": 2, "vix": 16}
    r = compute(soft)
    check("[4] 油稳+长端降+股涨+VIX低 → 软着陆",
          top(r) == "soft", f"{CLASS_CN.get(top(r))}  dist={r['dist']}")

    print("\n=== 二、缺失证据不进分母 ===")
    half = dict(stag)
    half["copper_chg5"] = None
    half["vix"] = None
    half["nasdaq_chg5"] = None
    r2 = compute(half)
    check("[5] 缺 3 项仍有分布", r2["dist"] is not None, str(r2["dist"]))
    check("[6] 缺 3 项方向不变（仍滞胀）", top(r2) == "stag",
          CLASS_CN.get(top(r2)))
    check("[7] 缺失被如实记入 missing",
          all("copper_chg5" in r2["per_class"][c]["missing"] for c in r2["per_class"]),
          "per_class[*].missing")

    print("\n=== 三、证据不足 → 不给分布 ===")
    none_ev = {k: None for k in ("brent_chg5", "us10y_chg5_bp", "dxy_chg5",
                                 "copper_chg5", "nasdaq_chg5", "vix")}
    r3 = compute(none_ev)
    check("[8] 全缺 → insufficient", r3["evidence_status"] == "insufficient"
          and r3["dist"] is None, r3.get("note", ""))

    only_one = {"brent_chg5": 6, "us10y_chg5_bp": 20}   # 只有 2 项 ⇒ 各类 avail=2
    r4 = compute(only_one)
    # 这两项对 stag/overheat 都是同一阈值 ⇒ 两类的分相同、都可用 ⇒ 会给分布
    check("[9] 仅 2 项证据（两类可用）仍给分布",
          r4["evidence_status"] == "ok", str(r4["dist"]))

    one_ev = {"brent_chg5": 6}
    r5 = compute(one_ev)
    check("[10] 仅 1 项证据 → 不足（每类需 ≥2）",
          r5["evidence_status"] == "insufficient" and r5["dist"] is None,
          r5.get("note", ""))

    print("\n=== 四、全 0 分不冒充均匀 ===")
    # 构造"各类都不得分"：油 +3%（2<3<5 ⇒ soft/stag/overheat 都不中）、10Y +10bp（5<10<15）
    zero = {"brent_chg5": 3, "us10y_chg5_bp": 10}
    r6 = compute(zero)
    check("[11] 可用类全 0 分 → insufficient（不是 25%/25%/25%/25%）",
          r6["dist"] is None and r6["evidence_status"] == "insufficient",
          str(r6.get("dist")))

    print("\n=== 五、归一性与纯函数性 ===")
    for name, ev in (("滞胀", stag), ("过热", over), ("衰退", rec), ("软着陆", soft)):
        res = compute(ev)
        s = sum((res.get("dist") or {}).values())
        check(f"[Σ=1] {name}", abs(s - 1.0) < 1e-6, f"Σ={s}")

    before = copy.deepcopy(stag)
    a = compute(stag)
    b = compute(stag)
    check("[12] 同样输入两次结果相同（可复算）", a["dist"] == b["dist"])
    check("[13] 不修改入参", stag == before)

    print("\n=== 六、默认日期口径 ===")
    from app.macro_regime_class import _default_day
    d = _default_day()
    ok_fmt = isinstance(d, str) and len(d) == 10 and d[4] == "-" and d[7] == "-"
    check("[14] 默认日期是 YYYY-MM-DD", ok_fmt, str(d))
    # ★ 关键：不能是"今天"这种可能落在周末/节假日的口径 —— 这里只做弱断言，
    #   因为 `latest_completed_trading_day()` 依赖交易日历，可能在无 DB 环境下 fallback。
    import datetime as _dt
    try:
        wd = _dt.date.fromisoformat(d).weekday()
        check("[15] 默认日期落在工作日（交易日历可用时）", wd < 5,
              f"{d} weekday={wd}")
    except Exception as e:
        check("[15] 默认日期可解析", False, str(e))

    print(f"\n{'=' * 60}\n通过 {_passed} / 失败 {_failed}")
    assert _failed == 0, f"有 {_failed} 个场景未通过"
    print("ASSERTION PASSED")


if __name__ == "__main__":
    main()
