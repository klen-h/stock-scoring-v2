#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【新机制候选】「多段阴跌」状态本身的 T+20 超额（**独立预登记**）
================================================================================
来源（诚实声明）：
  本假设是 P1-e（`scripts/rebound_leading_indicators.py`）的**副产品** ——
  该轮检验的 6 个「反弹前兆变量」**全部失败（ARCHIVE）**，但诊断段显示：
    · 沪深300：阴跌期 T+20 −0.07% vs 全样本 +0.96%（**14/18 年跑赢同年**）
    · 中证1000：阴跌期 **+3.45%** vs 全样本 +0.63%（**8/11 年**）
  ⇒ **属于「样本内发现」** ⇒ 本脚本按纪律做**独立预登记 + 三重加固**：
      ① 聚类（T+20 高度重叠 ⇒ 必须按**簇**重采样，不能用日样本）；
      ② 分年一致性；
      ③ **可执行性**（只做多是否优于一直持有 —— 这是「小盘偏好」栽过的地方）。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死）━━━━━━━━━━━━━━━━━━
【事件定义】`阴跌启动日` T：
      T 满足 前 20 日累计收益 ≤ **−8%**，且 **T−1 不满足**（＝状态**进入点**，非"持续期中"）
      —— 无前视（只用 T 及之前）；用"进入点"而非"每日"是为了**消除重叠**。
【目标】T+1 开盘买 → T+20 收盘卖
【对照】该指数全样本 T+20 均值（无条件基准）

【判据】（三条同时满足 ⇒ **PASS**，可进入"新机制"设计）
  (a) 超额 ≥ **+2.0pp**
  (b) **簇级** bootstrap P(均值 ≤ 基准) < **0.05**（对事件簇有放回重采样，簇内成员一并纳入）
  (c) 分年正超额年份 ≥ **60%**
【附加（可执行性，不作判据但必须看）】
  · 若"只做阴跌启动"的累计净值 **不优于**"一直持有" ⇒ 记 **不可执行**（同「小盘偏好」结局）

⚠️ 样本内发现 + 单一机制来源 ⇒ **本结果即便 PASS 也只是"候选"**，须样本外/实盘复核。

用法：python scripts/drawdown_rebound_check.py [--h 20]
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

# ── 预登记 ──
LOOKBACK = 20
DD_TH = -8.0
H_DEFAULT = 20
MIN_DIFF = 2.0
ALPHA = 0.05
MIN_POS_YEAR_RATIO = 0.60
B_BOOT = 10000

CODES = ["sh000300", "sh000905", "sh000852", "sz399006"]


def load_index(code):
    with open(os.path.join(ROOT, "data", "idx_daily.json"), encoding="utf-8") as f:
        d = json.load(f)
    rows = sorted(d[code], key=lambda x: x["date"])
    return ([r["date"] for r in rows],
            np.array([r["open"] for r in rows], float),
            np.array([r["close"] for r in rows], float))


def cluster_ids(idxs, gap=20):
    if not idxs:
        return []
    out = [[idxs[0]]]
    for a, b in zip(idxs, idxs[1:]):
        if b - a >= gap:
            out.append([])
        out[-1].append(b)
    return out


def boot_cluster_p(clusters, fwd, base, B=B_BOOT, seed=42):
    """对**簇**有放回重采样（簇内成员一并纳入）⇒ 避免日样本重叠导致的假显著。"""
    if not clusters:
        return None, None
    mem = [np.array([fwd[i] for i in c], float) for c in clusters]
    k = len(mem)
    rng = np.random.default_rng(seed)
    means = np.empty(B)
    for b in range(B):
        pick = rng.integers(0, k, size=k)
        vals = np.concatenate([mem[j] for j in pick])
        means[b] = vals.mean()
    return float(np.mean(means <= base)), float(means.mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=H_DEFAULT)
    args = ap.parse_args()
    h = args.h

    print(f"预登记：阴跌启动日 = 前 {LOOKBACK} 日 ≤ {DD_TH}% 且前一日不满足；"
          f"T+1 开盘买 → T+{h} 收盘")
    print(f"判据：超额 ≥ +{MIN_DIFF}pp 且 簇级 bootstrap P < {ALPHA} 且 正超额年 ≥ {MIN_POS_YEAR_RATIO:.0%}")

    summary = []
    for code in CODES:
        try:
            dts, opens, closes = load_index(code)
        except Exception as e:
            print(f"\n{code}: 跳过（{e}）")
            continue
        n = len(closes)
        cum = np.full(n, np.nan)
        cum[LOOKBACK:] = (closes[LOOKBACK:] / closes[:-LOOKBACK] - 1) * 100
        fwd = np.full(n, np.nan)
        for i in range(n - h - 1):
            if opens[i + 1] > 0:
                fwd[i] = (closes[i + h] / opens[i + 1] - 1) * 100

        is_dd = np.zeros(n, dtype=bool)
        for i in range(LOOKBACK, n):
            if not np.isnan(cum[i]) and cum[i] <= DD_TH:
                is_dd[i] = True

        # 进入点：T 阴跌 且 T-1 非阴跌
        entries = [i for i in range(LOOKBACK + 1, n - h - 1)
                   if is_dd[i] and not is_dd[i - 1] and not np.isnan(fwd[i])]
        pool = [i for i in range(n) if not np.isnan(fwd[i])]
        base = float(np.mean([fwd[i] for i in pool]))
        if len(entries) < 10:
            print(f"\n{code}: 进入点不足（{len(entries)}）")
            continue
        vals = [fwd[i] for i in entries]
        mean = float(np.mean(vals))
        diff = mean - base
        clusters = cluster_ids(entries)
        p, bmean = boot_cluster_p(clusters, fwd, base)
        # 分年
        by_year, all_year = defaultdict(list), defaultdict(list)
        for i in entries:
            by_year[dts[i][:4]].append(fwd[i])
        for i in pool:
            all_year[dts[i][:4]].append(fwd[i])
        yrs = [y for y in sorted(by_year) if len(by_year[y]) >= 2]
        pos = sum(1 for y in yrs if np.mean(by_year[y]) > np.mean(all_year[y]))
        pos_ratio = pos / len(yrs) if yrs else 0

        ok = (diff >= MIN_DIFF and p is not None and p < ALPHA
              and pos_ratio >= MIN_POS_YEAR_RATIO)
        summary.append((code, len(entries), len(clusters), mean, diff, p, pos, len(yrs), ok))

        print(f"\n{'='*110}")
        print(f"【{code}】{dts[0]} ~ {dts[-1]}（{n} 天）")
        print(f"{'='*110}")
        print(f"  阴跌启动（进入点）：**{len(entries)} 次 / {len(clusters)} 簇**"
              f"（占样本 {len(entries)/n*100:.1f}%）")
        print(f"  T+{h}：事件 {mean:+.2f}%  vs  无条件基准 {base:+.2f}%"
              f"  ⇒ 超额 **{diff:+.2f}pp**")
        print(f"  簇级 bootstrap：P(均值 ≤ 基准) = **{p:.4f}**，重采样均值 {bmean:+.2f}%")
        print(f"  分年：正超额 **{pos}/{len(yrs)} 年**（{pos_ratio*100:.0f}%）")
        print(f"  判定：{'★PASS（新机制候选）' if ok else '-（不满足三条）'}")

        # ── 可执行性（附加，非判据）──
        # 策略：每次"阴跌启动"买入并持 h 日；区间不重叠（进入点之间天然间隔 ≥1，
        #       但可能重叠 ⇒ 这里按"不重叠"贪心选，模拟单账户）
        picked, last_end = [], -1
        for i in entries:
            if i > last_end:
                picked.append(i)
                last_end = i + h
        if picked:
            r = np.prod([1 + fwd[i] / 100 for i in picked]) - 1
            years = (dts[picked[-1]][:4] and (int(dts[-1][:4]) - int(dts[picked[0]][:4]) + 1)) or 1
            hold_ratio = len(picked) * h / n
            buy_hold = closes[-1] / closes[picked[0]] - 1
            print(f"  可执行性（贪心不重叠：{len(picked)} 次交易，占时 {hold_ratio*100:.0f}%）：")
            print(f"    策略累计 {r*100:+.1f}%（约 {years} 年，年化 {((1+r)**(1/max(years,1))-1)*100:+.1f}%）"
                  f"  vs  同期买入持有 {buy_hold*100:+.1f}%"
                  f"（年化 {((1+buy_hold)**(1/max(years,1))-1)*100:+.1f}%）")
            print(f"    ⇒ {'轮动有超额' if r > buy_hold else '**轮动不优于买入持有**'}")

    # ── 汇总 ──
    print(f"\n{'='*110}")
    print("【汇总】")
    print(f"{'='*110}")
    print(f"  {'指数':<10}{'进入点':>7}{'簇':>5}{'事件T+20':>11}{'基准':>9}{'超额':>10}{'P':>9}{'正年':>8}  判定")
    for code, ne, nc, mean, diff, p, pos, ny, ok in summary:
        print(f"  {code:<10}{ne:>7}{nc:>5}{mean:>+10.2f}%{mean-diff:>+8.2f}%"
              f"{diff:>+9.2f}pp{p:>9.4f}{pos:>4}/{ny:<3}  {'★PASS' if ok else '-'}")
    passes = [s for s in summary if s[8]]
    print()
    if passes:
        print(f"  ★ 通过判据的指数：{[s[0] for s in passes]}")
        print("    ⇒ 「多段阴跌」在中小盘上是**可执行的择时候选**；但**样本内发现**，")
        print("      须先做样本外/实盘复核，再进决策链（延续 E2 的 v0 边界纪律）。")
    else:
        print("  ★ 无指数通过 ⇒ 归档。")


if __name__ == "__main__":
    main()
