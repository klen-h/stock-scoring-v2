#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】用 `macro_daily_history` 的分位数，体检并重标定 `macro.py` 规则表的阈值
================================================================================

背景（见 `PLAN_RESERVE_SIGNALS.md` §6.1）：
  `macro.py` 规则表里少数**水平型**阈值是"拍"出来的——`us10y_high` 的注释写
  "4.6% 是 2024-2026 箱体上沿"，而 2026-10 的 10Y 已到 5.2% ⇒ 水位制度一变，
  这类阈值要么**天天触发**（失去信息量）要么**永不触发**。本脚本给出**分位数依据**，
  供人工确认后再改 `macro.py`（阈值是"平台"不是"尖峰"，项目纪律：保留中性带防噪声）。

用法：python scripts/recalibrate_macro_thresholds.py
输出：每个指标在 **全样本 / 近 252 日 / 近 60 日** 三个窗口的分位数 + 当前值所处百分位
      ＋**候选阈值在历史上的触发频率**（关键：评估"改完还会不会天天响"）。

★ 只读脚本：不修改任何配置。改规则表需人工确认并在 `why`/注释里写明数据依据。
★ 变化率型规则（`*_change_pct` / `*_bp_change`）本身自我归一、不易漂移 ⇒ 这里只做体检，
  除非分位数显示阈值明显失衡才建议改。
================================================================================
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
env = os.path.join(ROOT, "backend", ".env")
if os.path.exists(env):
    for line in open(env, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import numpy as np  # noqa: E402

from app import macro_daily  # noqa: E402

QS = (50, 75, 80, 85, 90, 95, 99)


def _series(code):
    return macro_daily.load(code) or []


def _pct(vals, q):
    return float(np.percentile(vals, q)) if len(vals) else float("nan")


def _freq_above(vals, thr):
    """vals 中 > thr 的占比（%）。"""
    if not len(vals):
        return float("nan")
    return 100.0 * float(np.sum(np.asarray(vals) > thr)) / len(vals)


def _num(x, nd=2):
    return "—" if x != x else f"{x:.{nd}f}"


def _diffs(vals, scale=1.0):
    a = np.asarray(vals, dtype=float)
    return (np.diff(a) * scale).tolist()


def report_level(label, vals, rule_id, cur_thr, nd=2):
    """水平型指标：给分位数 + 当前值百分位 + 候选阈值触发频率。"""
    if not vals:
        print(f"  [{label}] 无数据（{rule_id}）")
        return
    a = np.asarray(vals, dtype=float)
    w_all = a.tolist()
    w_1y = a[-252:].tolist()
    w_60 = a[-60:].tolist()
    cur = float(a[-1])
    print(f"\n◆ {label}（规则 {rule_id}，当前阈值 {cur_thr}）")
    print(f"  当前值 {_num(cur, nd)}"
          f"   | 百分位: 全样本 {100.0 * (a <= cur).mean():.1f}%"
          f" / 近252日 {100.0 * (a[-252:] <= cur).mean():.1f}%"
          f" / 近60日 {100.0 * (a[-60:] <= cur).mean():.1f}%")
    line = "  分位: " + "  ".join(f"P{q}={_num(_pct(w_all, q), nd)}" for q in QS)
    print(line)
    print("        " + "  ".join(f"P{q}1y={_num(_pct(w_1y, q), nd)}" for q in QS))
    # 候选阈值（含现阈值）的触发频率 —— 这才是"改了会不会天天响"的答案
    cands = sorted({round(_pct(w_all, q), nd) for q in (85, 90, 95)} | {cur_thr})
    print(f"  触发频率(>阈值, 全样本 / 近252日 / 近60日):")
    for t in cands:
        tag = " ←当前" if abs(t - cur_thr) < 1e-9 else ""
        print(f"    > {_num(t, nd)}:  {_freq_above(w_all, t):5.1f}% / "
              f"{_freq_above(w_1y, t):5.1f}% / {_freq_above(w_60, t):5.1f}%{tag}")


def report_change(label, vals, rule_ids, thr_hi=None, thr_lo=None, nd=2):
    """变化率型指标：给分位数 + 现阈值触发频率。"""
    if not vals:
        print(f"\n◆ {label}（{rule_ids}）无数据")
        return
    a = np.asarray(vals, dtype=float)
    w_all, w_1y, w_60 = a.tolist(), a[-252:].tolist(), a[-60:].tolist()
    print(f"\n◆ {label}（规则 {rule_ids}）")
    print("  分位: " + "  ".join(f"P{q}={_num(_pct(w_all, q), nd)}" for q in QS))
    print("        " + "  ".join(f"P{q}1y={_num(_pct(w_1y, q), nd)}" for q in QS))
    if thr_hi is not None:
        print(f"  > {thr_hi}: 全样本 {_freq_above(w_all, thr_hi):5.1f}%"
              f" / 近252日 {_freq_above(w_1y, thr_hi):5.1f}%"
              f" / 近60日 {_freq_above(w_60, thr_hi):5.1f}%  ←当前(空头侧)")
    if thr_lo is not None:
        lo_all = 100.0 - _freq_above(w_all, thr_lo)
        lo_1y = 100.0 - _freq_above(w_1y, thr_lo)
        lo_60 = 100.0 - _freq_above(w_60, thr_lo)
        print(f"  < {thr_lo}: 全样本 {lo_all:5.1f}% / 近252日 {lo_1y:5.1f}%"
              f" / 近60日 {lo_60:5.1f}%  ←当前(多头侧)")


def main() -> None:
    codes = {c: _series(c) for c in
             ("us10y", "us2y", "us30y", "brent", "wti", "gold_com", "gold_spot", "silver")}
    for c, s in codes.items():
        print(f"  {c:10s} {len(s):5d} 行  {s[0]['date'] if s else '-':10s}"
              f"..{s[-1]['date'] if s else '-'}")

    def closes(code):
        return [r["close"] for r in codes[code]]

    def dates(code):
        return [r["date"] for r in codes[code]]

    print("\n" + "=" * 78)
    print("一、水平型阈值（会随水位制度漂移 —— 本次重标定的重点）")
    print("=" * 78)
    report_level("美债10Y（%）", closes("us10y"), "us10y_high", 4.6)
    report_level("美债30Y（%）", closes("us30y"), "us30y_high", 5.0)
    print("\n  · VIX 三条（14/20/25）**不在本次标定范围**：规则注释写明它是「真实分界线」，"
          "属语义阈值而非水位阈值（且 macro_daily_history 无 VIX 列）。")
    print("  · cny_cnh_basis（30/120 pips）与 internal_temp（28/58）为结构性/归一化阈值，"
          "不随水位漂移 ⇒ 不动。")

    print("\n" + "=" * 78)
    print("二、变化率型阈值（自我归一，一般不必改；此处只做体检）")
    print("=" * 78)
    # 美债 bp 变化（日环比 ×100）
    u2 = [_p for _p in _diffs(closes("us2y"), 100)]
    u10 = _diffs(closes("us10y"), 100)
    # 曲线 10Y-2Y 的 bp 变化
    n = min(len(closes("us10y")), len(closes("us2y")))
    curve = [c * 100 for c in _diffs([a - b for a, b in
                                      zip(closes("us10y")[-n:], closes("us2y")[-n:])], 1.0)]
    report_change("美债2Y 日变化（bp）", u2, "us2y_surge", thr_hi=8, thr_lo=-5)
    report_change("美债10Y 日变化（bp）", u10, "us10y_surge", thr_hi=6)
    report_change("美债曲线(10Y-2Y) 日变化（bp）", curve, "us_curve_flatten",
                  thr_hi=5, thr_lo=-5)

    # 布伦特 日涨跌 %
    def _pct_change(cl):
        a = np.asarray(cl, dtype=float)
        r = (a[1:] / a[:-1] - 1.0) * 100.0
        return r.tolist()

    brent_pct = _pct_change(closes("brent"))
    report_change("布伦特 日涨跌（%）", brent_pct, "oil_band(bear_above/below)",
                  thr_hi=3, thr_lo=-4)

    # 金银比 日变化 %
    gd, sd = {r["date"]: r["close"] for r in codes["gold_com"]}, \
             {r["date"]: r["close"] for r in codes["silver"]}
    common = sorted(set(gd) & set(sd))
    ratio = [gd[d] / sd[d] for d in common]
    gs_pct = _pct_change(ratio)
    report_change("金银比 日变化（%）", gs_pct, "goldsilver_ratio", thr_hi=1, thr_lo=-1)

    print("\n" + "=" * 78)
    print("三、结论口径提醒")
    print("=" * 78)
    print("  · 水平阈值的取舍是**制度选择**：取全样本 P90 = 「只在最贵的 10% 时段报警」；")
    print("    取近 252 日 P90 = 「相对当下水位报警」（更敏感但会追随水位）。")
    print("  · 改完必须看**触发频率**：若近 60 日触发率 >80%，说明该规则已退化为\"常数\"、")
    print("    不再提供信息（此时应上移阈值或改为变化率口径）。")


if __name__ == "__main__":
    main()
