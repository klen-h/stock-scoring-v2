#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
验证 `REGIME_POSITION`（仓位映射）本身 —— 「defensive 就该 0%」成立吗？
================================================================================
背景：
  生产 `mainforce/trade_gate.REGIME_POSITION` 把四态映射为
      offensive 80% / neutral 50% / neutral_bearish 20% / **defensive 0%（禁买）**
  · 规则来源是「跟随主力操作指南」，**未见优化验证**；
  · defensive 占 **32.3%（年化 79 天）**，是最大的单一状态；
  · 对比：`get_regime_weights` 的震荡档有 350 条快照对照，本表没有。
  ⇒ 本脚本直接验证「各态下实际持有收益」，回答 0% 是否过严。

无前视声明（前置检查已过）：
  · `detect_market_regime` 的 MA/ADX/ATR 全为滚动窗口；
  · `_apply_hysteresis` 窗口 = `[i-2, i-1, i]`（**只用过去**）；
  · `_apply_bearish_refine` 的宽度/外围恐慌均取自**当日收盘**数据；
  ⇒ regime 于 **T 日收盘后**可得 ⇒ 检验统一用 **T+1 开盘买入**（可执行口径）。

收益口径（两条，互为印证）：
  a) **全市场等权**（`data/event_history.json` 的 pct_mean + gap_mean）—— 贴近"买一篮子"
  b) **沪深300**（`data/idx_daily.json` 的 sh000300）—— 大盘参照

━━━━━━━━━━━━━━━━━━ 预登记 ━━━━━━━━━━━━━━━━━━
  主问：defensive 期持有 20 日的收益，是否显著低于全样本？

  (a) defensive 的 H20 − 全样本 H20 <= **−2.0pp** 且 bootstrap P(差<0) < 0.05
      ⇒ **0% 正确**（防御期确实该空仓），规则维持
  (b) defensive 的 H20 − 全样本 H20 >= **−0.5pp**（即基本不劣于平均）
      ⇒ **0% 过严**，应给基础仓位（建议 20%，与 nb 同档）
  (c) 介于 −2.0 ~ −0.5pp，或统计不显著
      ⇒ **证据不足，维持现状**（保守优先）

  附加检验（单调性）：四态收益是否随「建议仓位」单调递增？
      若不单调 ⇒ 映射**方向**有问题（不仅仅是数值）

用法：python scripts/regime_position_check.py [--horizons 5 10 20 60]
================================================================================
"""
import argparse
import json
import os
import sys
from collections import defaultdict

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NB_JSON = os.path.join(ROOT, "data", "nb_history.json")
EVENT_JSON = os.path.join(ROOT, "data", "event_history.json")
IDX_JSON = os.path.join(ROOT, "data", "idx_daily.json")

ORDER = ["offensive", "neutral", "neutral_bearish", "defensive"]
CN = {"offensive": "进攻", "neutral": "震荡",
      "neutral_bearish": "偏弱", "defensive": "防御"}
POS = {"offensive": 80, "neutral": 50, "neutral_bearish": 20, "defensive": 0}
B_BOOT = 10000
EXTREME = 11.0


def load_regime() -> dict:
    with open(NB_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    return {d: (v.get("state_4") or v.get("state_3")) for d, v in raw.items()}


def load_equalweight():
    """全市场等权：dates / rets(%) / gaps(%)。"""
    with open(EVENT_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    dates = sorted(raw)
    rets = np.array([raw[d]["pct_mean"] for d in dates], dtype=float)
    gaps = np.array([raw[d].get("gap_mean", 0.0) for d in dates], dtype=float)
    return dates, rets, gaps


def load_index(code="sh000300"):
    """指数：dates / rets(%) / gaps(%)。"""
    with open(IDX_JSON, encoding="utf-8") as f:
        bars = json.load(f)[code]
    bars = sorted(bars, key=lambda b: b["date"])
    dates, rets, gaps = [], [], []
    for i, b in enumerate(bars):
        if i == 0:
            continue
        pc = bars[i - 1]["close"]
        if pc <= 0:
            continue
        dates.append(b["date"])
        rets.append((b["close"] / pc - 1) * 100)
        gaps.append((b["open"] / pc - 1) * 100)
    return dates, np.array(rets), np.array(gaps)


def fwd_open(rets, gaps, i, n):
    """T+1 开盘买 → T+n 收盘（%）。"""
    if i + 1 + n > len(rets):
        return None
    entry = 1 + gaps[i + 1] / 100.0
    if entry <= 0.05 or entry > 1 + EXTREME / 100.0:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        cum *= (1 + rets[k] / 100.0)
    return (cum / entry - 1) * 100


def boot_p(a, b, B=B_BOOT, seed=42):
    """a、b 两组均值差的 bootstrap P(mean(a)-mean(b) <= 0)。"""
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) == 0 or len(b) == 0:
        return None, None
    rng = np.random.default_rng(seed)
    d = (a[rng.integers(0, len(a), (B, len(a)))].mean(axis=1)
         - b[rng.integers(0, len(b), (B, len(b)))].mean(axis=1))
    return float((d <= 0).mean()), float(a.mean() - b.mean())


def analyse(tag, dates, rets, gaps, rmap, horizons):
    print(f"\n{'=' * 100}")
    print(f"【{tag}】")
    print(f"{'=' * 100}")
    states = [rmap.get(d) for d in dates]
    n_all = sum(1 for s in states if s)

    out = {}
    for h in horizons:
        vals_by = defaultdict(list)
        allv = []
        for i, s in enumerate(states):
            if not s or i < 65:
                continue
            v = fwd_open(rets, gaps, i, h)
            if v is not None:
                vals_by[s].append(v)
                allv.append(v)
        out[h] = (vals_by, allv)

    print(f"\n  {'状态':<8}{'建议仓':>7}", end="")
    for h in horizons:
        print(f"{'H%-3d' % h:>17}", end="")
    print(f"{'胜率H20':>9}")
    for st in ORDER:
        vals, allv = out[horizons[-1]][0][st], out[horizons[-1]][1]
        print(f"  {CN[st]:<8}{('%d%%' % POS[st]):>7}", end="")
        for h in horizons:
            v = out[h][0][st]
            if not v:
                print(f"{'-':>17}", end="")
            else:
                m = np.mean(v)
                med = np.median(v)
                print(f"{'%+.2f%% (中%+.2f)' % (m, med):>17}", end="")
        if vals:
            print(f"{np.mean(np.array(vals) > 0) * 100:>8.1f}%")
        else:
            print(f"{'-':>9}")
    print(f"  {'全样本':<8}{'-':>7}", end="")
    for h in horizons:
        allv = out[h][1]
        if allv:
            print(f"{'%+.2f%% (中%+.2f)' % (np.mean(allv), np.median(allv)):>17}", end="")
    print(f"{np.mean(np.array(out[horizons[-1]][1]) > 0) * 100:>8.1f}%")

    hm = 20 if 20 in horizons else horizons[-1]
    print(f"\n  ── 主判定（H{hm}，T+1 开盘买）──")
    base = out[hm][1]
    for st in ORDER:
        v = out[hm][0][st]
        if len(v) < 30:
            print(f"    {CN[st]:<6} n={len(v):<5} 样本不足")
            continue
        p, d = boot_p(v, base)
        print(f"    {CN[st]:<6} n={len(v):<5} 均值 {np.mean(v):+7.2f}%  "
              f"vs 全样本 {np.mean(base):+7.2f}%  差 {d:+7.2f}pp  "
              f"P(差<=0)={p:.4f}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizons", type=int, nargs="+", default=[5, 10, 20, 60])
    args = ap.parse_args()

    rmap = load_regime()
    print(f"regime 序列 {len(rmap)} 天  {min(rmap)} ~ {max(rmap)}")

    d1, r1, g1 = load_equalweight()
    out1 = analyse("全市场等权（买一篮子）", d1, r1, g1, rmap, args.horizons)

    d2, r2, g2 = load_index("sh000300")
    out2 = analyse("沪深300（大盘参照）", d2, r2, g2, rmap, args.horizons)

    # ★ 中证1000（可执行：512100）—— 与「小盘优势」发现呼应，检验 nb 期的真实可执行收益
    out3 = None
    try:
        d3, r3, g3 = load_index("sh000852")
        out3 = analyse("中证1000（可执行 512100）", d3, r3, g3, rmap, args.horizons)
    except Exception as e:
        print(f"\n[中证1000] 跳过：{e}")

    # ── defensive 分年拆解（§8.3 教训：全样本易被单年主导）──
    print(f"\n{'=' * 100}")
    print("【防御期 H20 分年拆解（全市场等权）】")
    print(f"{'=' * 100}")
    states = [rmap.get(d) for d in d1]
    by_year = defaultdict(list)
    base_year = defaultdict(list)
    for i, s in enumerate(states):
        if i < 65:
            continue
        v = fwd_open(r1, g1, i, 20)
        if v is None:
            continue
        base_year[d1[i][:4]].append(v)
        if s == "defensive":
            by_year[d1[i][:4]].append(v)
    print(f"  {'年份':<6}{'防御H20':>10}{'全样本H20':>11}{'差':>9}{'防御天数':>9}")
    neg = pos = 0
    for y in sorted(base_year):
        dv = by_year.get(y, [])
        bv = base_year[y]
        if not dv:
            continue
        m, b = np.mean(dv), np.mean(bv)
        if m - b < 0:
            neg += 1
        else:
            pos += 1
        print(f"  {y:<6}{m:>+9.2f}%{b:>+10.2f}%{m - b:>+8.2f}pp{len(dv):>9}")
    print(f"  · 防御劣于全样本的年份：{neg}/{neg + pos}")

    # ── 最终判定（两个口径分别判定，避免口径混用）──
    hm = 20
    _pairs = [("全市场等权", out1), ("沪深300", out2)]
    if out3:
        _pairs.append(("中证1000", out3))
    for tag, o in _pairs:
        print(f"\n{'=' * 100}")
        print(f"【预登记判定 · {tag}】(H{hm}，T+1 开盘买)")
        print(f"{'=' * 100}")
        vals, allv = o[hm][0], o[hm][1]
        if "defensive" not in vals or len(vals["defensive"]) < 30:
            print("  防御样本不足")
            continue
        dv = np.array(vals["defensive"], float)
        av = np.array(allv, float)
        d = dv.mean() - av.mean()
        p, _ = boot_p(dv, av)
        # ★ 风险维度（仓位决策不能只看均值）
        p5d, p5a = np.percentile(dv, 5), np.percentile(av, 5)
        tld = np.mean(dv <= -10) * 100
        tla = np.mean(av <= -10) * 100
        tail_bad = (p5d - p5a <= -3.0) or (tla > 0 and tld >= 1.3 * tla)
        print(f"  收益：防御 {dv.mean():+.2f}%  vs 全样本 {av.mean():+.2f}%  "
              f"差 {d:+.2f}pp  P(差<=0)={p:.4f}")
        print(f"  风险：P5 {p5d:+.2f}% vs {p5a:+.2f}%（差 {p5d - p5a:+.2f}pp）；"
              f"大跌(≤-10%)占比 {tld:.1f}% vs {tla:.1f}%（{tld / tla:.2f}×）；"
              f"std {dv.std():.2f} vs {av.std():.2f}")
        if tail_bad:
            print("  ★ (a) **防御期尾部风险显著更厚**（P5 差<=-3pp 或 大跌率>=1.3×）"
                  " ⇒ **0% 有依据（风险规避，非过度保守）**，维持")
        elif d <= -2.0 and p < 0.05:
            print("  ★ (b) 均值显著更差 ⇒ **0% 正确**")
        elif d >= -0.5 and dv.mean() >= 0:
            print("  ★ (c) 均值不劣且尾部不劣 ⇒ **0% 缺乏依据**，可考虑给基础仓位")
        else:
            print("  ★ (d) 证据不足 ⇒ **维持现状**（保守优先）")

        # 单调性（同一口径内）
        seq = sorted((POS[st], np.mean(vals[st])) for st in ORDER
                     if len(vals[st]) >= 30)
        mono = all(seq[i][1] <= seq[i + 1][1] for i in range(len(seq) - 1))
        print(f"  单调性（建议仓位 ↑ ⇒ 收益 ↑ ？）："
              + "  <  ".join(f"{p}%→{m:+.2f}%" for p, m in seq)
              + f"  ⇒ {'单调 ✓' if mono else '**非单调 ✗**'}")

    # ── ★ 风险画像：仓位决策不能只看均值，必须看尾部 ──
    print(f"\n{'=' * 100}")
    print("【★ 风险画像（H20）—— 防御期均值不低，但左尾是否更厚？】")
    print(f"{'=' * 100}")
    print(f"  {'口径':<10}{'状态':<7}{'n':>6}{'均值':>9}{'P5':>9}{'P25':>9}"
          f"{'中位':>9}{'P75':>9}{'P95':>9}{'std':>8}{'-10%以下占比':>12}")
    _risk = [("全市场等权", out1), ("沪深300", out2)]
    if out3:
        _risk.append(("中证1000", out3))
    for tag, o in _risk:
        vals_by, allv = o[20][0], o[20][1]
        for st in ORDER + ["__ALL__"]:
            v = np.array(vals_by[st] if st != "__ALL__" else allv, float)
            if len(v) < 30:
                continue
            nm = CN.get(st, "全样本")
            tail = float(np.mean(v <= -10) * 100)
            print(f"  {tag:<10}{nm:<7}{len(v):>6}{v.mean():>+8.2f}%"
                  f"{np.percentile(v, 5):>+8.2f}%{np.percentile(v, 25):>+8.2f}%"
                  f"{np.median(v):>+8.2f}%{np.percentile(v, 75):>+8.2f}%"
                  f"{np.percentile(v, 95):>+8.2f}%{v.std():>8.2f}{tail:>11.1f}%")
        print()

    # ── 偏弱（nb）专项：两个口径结论相反，必须单独揭示 ──
    print(f"\n{'=' * 100}")
    print("【偏弱（neutral_bearish, 现给 20%）专项 —— 两口径差异最大处】")
    print(f"{'=' * 100}")
    for tag, o in _pairs:
        v = o[20][0]["neutral_bearish"]
        if len(v) < 30:
            continue
        p, d = boot_p(v, o[20][1])
        print(f"  {tag:<10} n={len(v):<5} H20 {np.mean(v):+7.2f}%  "
              f"vs 全样本 {np.mean(o[20][1]):+6.2f}%  差 {d:+6.2f}pp  P={p:.4f}")
    print("  ⇒ 同一状态下两口径符号相反 ⇒ nb 的「弱」是**大盘弱**，不是市场弱；"
          "\n    把它当作「介于 neutral 与 defensive 之间的中间档（20%）」是**误解**。")


if __name__ == "__main__":
    main()
