#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】吸筹样本窗口专项（PLAN P1-1）——「吸筹阶段 × 低吸类战法」准入过滤验证
================================================================================
问题：phase=accumulation 样本 09-04→09-11 从 291 → 377（+30%），是吸筹形态
      样本最丰富的窗口。核心命题：在「吸筹阶段」上叠加「低吸类战法」（均线回踩/
      单阳不破）的信号，未来收益是否显著优于「非吸筹阶段的低吸信号」？

方法（复用 strategy_mainforce_filter_test 的数据路径，无前视）：
  1. 取 strategy_results 全部历史信号，筛出低吸类战法 {ma_pullback, single_yang_unbroken}
  2. 还原【信号日当日】主力阶段（phase，用信号日之前数据，无前视）
  3. 分组撮合（T+1 开盘 + 涨停一字剔除 + 退出 v2）：
     A  全部低吸信号（基线）
     B  低吸 × phase=accumulation（吸筹准入过滤）
     C  低吸 × phase≠accumulation
     D  低吸 × phase=accumulation 且 近窗口（>= 2026-09-01）
  4. 输出整体 + 分战法 + 分窗口 的 n/胜率/均收益/盈亏比

输出：backend/backtest_reports/accum_pullback_YYYYMMDD_HHMM.md

用法：python scripts/accum_pullback_backtest.py
================================================================================
"""

import datetime as dt
import os
import sys

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND_DIR)
sys.path.insert(0, SCRIPTS_DIR)
with open(os.path.join(BACKEND_DIR, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

from app.backtest import engine                                    # noqa: E402
from app.backtest.strategies import (_load_prices_map, exit_policy,  # noqa: E402
                                     apply_exit_policy)
import strategy_mainforce_filter_test as mft                       # noqa: E402

LOW_SUCK = {"ma_pullback", "single_yang_unbroken"}
WINDOW_START = "2026-09-01"


def _stat(trades):
    n = len(trades)
    if not n:
        return {"n": 0, "win": None, "avg": None, "pf": None}
    wins = [t for t in trades if t["pnl_pct"] > 0]
    profits = [t["pnl_pct"] for t in trades]
    gw = sum(p for p in profits if p > 0)
    gl = abs(sum(p for p in profits if p <= 0))
    return {"n": n,
            "win": round(len(wins) / n * 100, 1),
            "avg": round(sum(profits) / n, 3),
            "pf": round(gw / gl, 2) if gl > 0 else (999.0 if gw > 0 else 0.0)}


def _to_signal(s):
    """补齐 match_signals 所需字段（与 strategy_mainforce_filter_test.replay 同口径）。"""
    return {**s, "direction": "long", "hold_days": 5,
            "is_etf": False, "strategy": s["strategy_en"]}


def _replay(signals, prices_map, states, keep_fn):
    kept, skipped = [], []
    for s in signals:
        m = states.get((s["code"], s["date"]))
        ok = False
        if m:
            ok = keep_fn(m)
        if ok:
            kept.append(_to_signal(s))
        else:
            skipped.append(s)
    # 退出策略 v2：持有 3 日 + 信号日收盘 -7% 止损
    if exit_policy() == "v2":
        applied = []
        for s in kept:
            base = None
            for b in prices_map.get(s["code"]) or []:
                if b["date"] <= s["date"]:
                    base = b["close"]
                else:
                    break
            applied.append(apply_exit_policy(s, base_close=base))
        kept = applied
    trades = engine.match_signals(kept, prices_map)
    return _stat(trades), len(skipped)


def main():
    all_signals = mft.load_signals()
    print(f"[signals] 全量 {len(all_signals)} 条")

    low = [s for s in all_signals if s["strategy_en"] in LOW_SUCK]
    print(f"[signals] 低吸类战法（ma_pullback/single_yang_unbroken）{len(low)} 条")

    states = mft.mainforce_states(all_signals)
    covered = sum(1 for s in low if (s["code"], s["date"]) in states)
    print(f"[states] 低吸信号主力状态覆盖 {covered}/{len(low)}")

    prices_map = _load_prices_map({s["code"] for s in low})
    print(f"[prices] 价格覆盖 {len(prices_map)} 只")

    # 分组
    def keep_accum(m):
        return m["phase"] == "accumulation"

    def keep_not_accum(m):
        return m["phase"] != "accumulation"

    def keep_combo(m):
        """C_accum 组合：低位筹码密集 × 主力净流入（区别于 phase 形态标签）。"""
        chip = m["chip"]
        return (chip["price_pos"] < 0.35 and chip["concentration"] < 0.25
                and m["flow5"] is not None and m["flow5"] > 0)

    def keep_all(m):
        return True

    groups = [
        ("A_all", "全部低吸信号（基线）", keep_all, low),
        ("B_accum", "低吸 × 吸筹阶段(phase)", keep_accum, low),
        ("E_combo", "低吸 × 筹码吸筹组合(低位密集+净流入)", keep_combo, low),
        ("D_accum_recent", "低吸 × 吸筹阶段 × 近窗口(≥09-01)", keep_accum,
         [s for s in low if s["date"] >= WINDOW_START]),
        ("F_combo_recent", "低吸 × 筹码吸筹组合 × 近窗口(≥09-01)", keep_combo,
         [s for s in low if s["date"] >= WINDOW_START]),
    ]

    report = ["# 吸筹样本窗口专项：吸筹（阶段/筹码组合）× 低吸类战法 准入过滤验证", "",
              f"> 生成：{dt.datetime.now():%Y-%m-%d %H:%M} ｜ 低吸信号 {len(low)} 条，"
              f"主力状态覆盖 {covered} 条 ｜ 撮合：T+1 开盘 + 涨停一字剔除 + 退出 {exit_policy()}", ""]

    report += ["## 一、整体对照", "",
               "| 组 | 说明 | 保留 | 被滤 | 胜率% | 均收益% | 盈亏比 |",
               "|---|---|---|---|---|---|---|"]
    results = {}
    for key, desc, fn, sigs in groups:
        r, skipped = _replay(sigs, prices_map, states, fn)
        results[key] = r
        report.append(
            f"| {key} | {desc} | {r['n']} | {skipped} | "
            f"{r['win'] if r['win'] is not None else '-'} | "
            f"{r['avg'] if r['avg'] is not None else '-'} | "
            f"{r['pf'] if r['pf'] is not None else '-'} |")
        print(f"{key}: {r}")

    # 分战法
    report += ["", "## 二、分战法（基线 vs 吸筹过滤）", "",
               "| 战法 | 过滤 | n | 胜率% | 均收益% | 盈亏比 |",
               "|---|---|---|---|---|---|"]
    for st in sorted(LOW_SUCK):
        st_sigs = [s for s in low if s["strategy_en"] == st]
        for key, desc, fn in [("A", "全部", keep_all),
                              ("B", "吸筹阶段", keep_accum),
                              ("E", "筹码吸筹组合", keep_combo)]:
            r, _ = _replay(st_sigs, prices_map, states, fn)
            if r["n"] == 0:
                continue
            report.append(f"| {st} | {desc} | {r['n']} | {r['win']} | {r['avg']} | {r['pf']} |")

    # 结论
    report += ["", "## 三、初步结论", ""]
    a = results.get("A_all", {})
    b = results.get("B_accum", {})
    e = results.get("E_combo", {})
    d = results.get("D_accum_recent", {})
    f = results.get("F_combo_recent", {})

    def _judge(key, label, base):
        """准入判据（项目方法论）：战法体系是「低胜率+高盈亏比」型，
        看均收益/盈亏比，胜率仅作参考——记忆里白名单结论已固化此口径。"""
        r = results.get(key, {})
        if not (r.get("n") and base.get("n")):
            return
        diff_win = (r["win"] or 0) - (base["win"] or 0)
        diff_avg = (r["avg"] or 0) - (base["avg"] or 0)
        diff_pf = (r["pf"] or 0) - (base["pf"] or 0)
        report.append(
            f"- {label}（{r['n']} 条）胜率 {r['win']}% vs 基线 {base['win']}%（{diff_win:+.1f}pp），"
            f"均收益 {r['avg']}% vs {base['avg']}%（{diff_avg:+.3f}pt），"
            f"盈亏比 {r['pf']} vs {base['pf']}（{diff_pf:+.2f}）")
        if r["avg"] > base["avg"] and r["pf"] > base["pf"]:
            verdict = "**有效准入过滤**（均收益/盈亏比双改善，符合低胜率+高盈亏比型）"
            if diff_win < -5:
                verdict += "；但胜率降幅>5pp，需留意样本波动"
            report.append(f"  → {verdict}")
        elif r["avg"] < base["avg"] and r["pf"] < base["pf"]:
            report.append(f"  → **全面跑输基线**：不构成准入，且可能是下跌中继陷阱，勿接入。")
        else:
            report.append(f"  → 信号不明确（均收益/盈亏比未同步改善）：保持观望。")

    report.append("### phase 吸筹阶段 vs 筹码吸筹组合")
    _judge("B_accum", "phase 吸筹阶段", a)
    _judge("E_combo", "筹码吸筹组合", a)
    report.append("")

    report.append("### 近窗口（≥09-01）实战兑现")
    for key, label in [("D_accum_recent", "phase 吸筹 × 近窗口"),
                       ("F_combo_recent", "筹码吸筹组合 × 近窗口")]:
        r = results.get(key, {})
        if r.get("n"):
            report.append(f"- {label}：{r['n']} 条，胜率 {r['win']}% / 均收益 {r['avg']}% / "
                          f"盈亏比 {r['pf']}")
        if r.get("n", 0) < 20:
            report.append(f"  ⚠️ {label}样本 <20 条，结论仅方向参考。")

    out = os.path.join(BACKEND_DIR, "backtest_reports",
                       f"accum_pullback_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"[report] {out}")


if __name__ == "__main__":
    main()
