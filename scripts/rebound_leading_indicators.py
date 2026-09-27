#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【新机制探索】「多段阴跌」后的**反弹前兆**（先行变量）检验
================================================================================
问题（用户提出）：
  马后炮看 V 型反弹，发现 regime（均线）**系统性滞后**。问：
  **多段阴跌后的「前几天变量」与反弹启动有无共性？能否据此提前识别？**
  ⇒ 若成立，可引入一个**领先于均线**的新机制（补 C 条件的结构性滞后）。

⚠️ 本类研究的最大风险 = **多重比较 + 事后选择偏差**（变量多、事件少、且"反弹"是
   事后标签）。故本脚本：
    1. **特征与方向先于结果写死**（见下），方向必须有**独立理论依据**，不做数据挖掘；
    2. 判据含 **Bonferroni 校正**；
    3. 报告**独立簇数**（连续阴跌日的未来 20 日收益高度重叠，簇数才是真实观测数）；
    4. 报告**分年一致性**（防"全靠某一轮牛市"）。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死）━━━━━━━━━━━━━━━━━━
【样本】指数前 20 日累计收益 ≤ **−8%** 的交易日 T（"多段阴跌"）
  —— 无前视：只用 T 日及之前的数据。
【目标】T+1 **开盘**买 → T+20 **收盘**卖（指数口径，可直接执行）

【特征 × 预期方向】（6 个，N=6 ⇒ Bonferroni α = 0.05/6 = 0.0083）
  #  特征          定义（T 日可观测）              预期收益更高的一档   理论依据
  1  量能比        amount / MA20(amount)          **低**（< 0.85）     地量见地价
  2  跌停比例      limit_down / n                 **高**（≥ 0.5%）     恐慌宣泄见底
  3  涨停比例      limit_up / n                   **高**（≥ 0.5%）     情绪回暖先行
  4  宽度          up_ratio                       **高**（≥ 0.30）     承接力恢复
  5  跳空          gap_mean                       **高**（≥ 0）        低开消失＝卖压衰竭
  6  波动衰减      近5日 std ÷ 近60日 std         **低**（< 0.90）     波动收敛＝变盘前兆

【判据】某特征"预期档"同时满足三条 ⇒ 记为 **HIT**（新机制候选）：
  (a) 相对**全部阴跌样本**均值差 ≥ **+2.0pp**
  (b) block bootstrap P(该档均值 ≤ 阴跌样本均值) < **0.0083**（Bonferroni）
  (c) 独立簇 ≥ **15**
【KEEP/ARCHIVE】无 HIT ⇒ **归档**（承认「反弹前兆」在这 6 个直觉变量上不成立）。

⚠️ 口径说明：`event_history.json` 的 `pct_mean` 存在**已知异常值**（2026 年 7 天
   "下跌家数>上涨家数却均值 +10%"，见 memory 2026-09-27）⇒ **不用于收益计算**，
   仅取其中的**宽度字段**（up_ratio / limit_* / amount / gap_mean）作特征。

用法：python scripts/rebound_leading_indicators.py [--code sh000300] [--h 20]
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

# ── 预登记参数（改这里必须在文档留痕）──
LOOKBACK = 20
DRAWDOWN_TH = -8.0
H_DEFAULT = 20
MIN_DIFF = 2.0
N_TESTS = 6
BONFERRONI = 0.05 / N_TESTS          # 0.00833
MIN_CLUSTERS = 15
CLUSTER_GAP = 20
B_BOOT = 10000

# (显示名, 内部键, 阈值, 预期档, 理论依据)
FEATURES = [
    ("量能比", "vol_ratio", 0.85, "low", "地量见地价"),
    ("跌停比例", "lu_down", 0.005, "high", "恐慌宣泄见底"),
    ("涨停比例", "lu_up", 0.005, "high", "情绪回暖先行"),
    ("宽度", "up_ratio", 0.30, "high", "承接力恢复"),
    ("跳空", "gap_mean", 0.0, "high", "低开消失=卖压衰竭"),
    ("波动衰减", "vol_decay", 0.90, "low", "波动收敛=变盘前兆"),
]


def load_index(code):
    with open(os.path.join(ROOT, "data", "idx_daily.json"), encoding="utf-8") as f:
        d = json.load(f)
    if code not in d:
        raise SystemExit(f"指数 {code} 不存在；可选 {list(d)}")
    rows = sorted(d[code], key=lambda x: x["date"])
    return ([r["date"] for r in rows],
            np.array([r["open"] for r in rows], dtype=float),
            np.array([r["close"] for r in rows], dtype=float))


def load_breadth():
    with open(os.path.join(ROOT, "data", "event_history.json"), encoding="utf-8") as f:
        return json.load(f)


def boot_p(vals, base, B=B_BOOT, seed=42, chunk=2000):
    a = np.asarray(vals, dtype=float)
    n = len(a)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)
    c = tot = 0
    done = 0
    while done < B:
        m = min(chunk, B - done)
        idx = rng.integers(0, n, size=(m, n))
        c += int(np.sum(a[idx].mean(axis=1) <= base))
        tot += m
        done += m
    return c / tot


def count_clusters(idxs):
    if not idxs:
        return 0
    c = 1
    for a, b in zip(idxs, idxs[1:]):
        if b - a >= CLUSTER_GAP:
            c += 1
    return c


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="sh000300")
    ap.add_argument("--h", type=int, default=H_DEFAULT)
    args = ap.parse_args()
    h = args.h

    dts, opens, closes = load_index(args.code)
    ev = load_breadth()
    n_idx = len(dts)

    # 指数自身的日收益（用于波动衰减特征）
    idx_ret = np.full(n_idx, np.nan)
    idx_ret[1:] = (closes[1:] / closes[:-1] - 1) * 100
    prev_vol = {}
    for w in (5, 60):
        s = np.full(n_idx, np.nan)
        for i in range(w, n_idx):
            s[i] = np.std(idx_ret[i - w + 1:i + 1])
        prev_vol[w] = s

    # 全市场成交额（event_history.amount），用于量能比
    amt = np.array([ev.get(d, {}).get("amount") or np.nan for d in dts], dtype=float)
    amt_ma = np.full(n_idx, np.nan)
    for i in range(LOOKBACK, n_idx):
        seg = amt[i - LOOKBACK + 1:i + 1]
        if not np.isnan(seg).any():
            amt_ma[i] = seg.mean()

    # 前 LOOKBACK 日累计收益
    cum = np.full(n_idx, np.nan)
    cum[LOOKBACK:] = (closes[LOOKBACK:] / closes[:-LOOKBACK] - 1) * 100

    # 未来收益：T+1 开盘买 → T+h 收盘
    fwd = np.full(n_idx, np.nan)
    for i in range(n_idx - h - 1):
        if opens[i + 1] > 0:
            fwd[i] = (closes[i + h] / opens[i + 1] - 1) * 100

    # ── 阴跌样本 ──
    pool = [i for i in range(n_idx)
            if not np.isnan(cum[i]) and cum[i] <= DRAWDOWN_TH and not np.isnan(fwd[i])]
    if len(pool) < 30:
        raise SystemExit(f"阴跌样本不足（{len(pool)} 天）")
    base_mean = float(np.mean([fwd[i] for i in pool]))
    base_clusters = count_clusters(pool)

    print(f"指数 {args.code}：{n_idx} 天 {dts[0]} ~ {dts[-1]}")
    print(f"样本：前 {LOOKBACK} 日累计 ≤ {DRAWDOWN_TH}% ⇒ **{len(pool)} 天 / {base_clusters} 簇**")
    print(f"阴跌样本 T+{h}（T+1 开盘买）均值：**{base_mean:+.2f}%**")
    full = [x for x in fwd if not np.isnan(x)]
    print(f"（参照）全样本 T+{h} 均值：{np.mean(full):+.2f}%"
          f"（阴跌期 {'更差' if base_mean < np.mean(full) else '更好'}）")
    print(f"预登记：{N_TESTS} 个特征 × 预设方向，Bonferroni α = {BONFERRONI:.4f}，"
          f"判据：差 ≥ +{MIN_DIFF}pp 且 P < {BONFERRONI:.4f} 且 簇 ≥ {MIN_CLUSTERS}")

    # ── 逐特征检验 ──
    print(f"\n{'='*112}")
    print(f"【先行变量检验】样本 = {len(pool)} 天阴跌日（基准 {base_mean:+.2f}%）")
    print(f"{'='*112}")
    print(f"{'特征':<10}{'预期档':<22}{'档内n':>6}{'档内T+20':>11}{'差':>9}"
          f"{'P(≤基准)':>10}{'簇':>5}  判定")

    hits, results = [], []
    for name, key, th, side, why in FEATURES:
        sel = []
        for i in pool:
            v = ev.get(dts[i], {})
            n_all = (v.get("up") or 0) + (v.get("down") or 0)
            if key == "vol_ratio":
                x = (amt[i] / amt_ma[i]) if (amt_ma[i] and not np.isnan(amt_ma[i])) else None
            elif key == "lu_down":
                x = (v.get("limit_down", 0) / n_all) if n_all else None
            elif key == "lu_up":
                x = (v.get("limit_up", 0) / n_all) if n_all else None
            elif key == "up_ratio":
                x = v.get("up_ratio")
            elif key == "gap_mean":
                x = v.get("gap_mean")
            elif key == "vol_decay":
                a, b = prev_vol[5][i], prev_vol[60][i]
                x = (a / b) if (b and not np.isnan(a) and not np.isnan(b) and b > 0) else None
            else:
                x = None
            if x is None:
                continue
            ok = (x < th) if side == "low" else (x >= th)
            if ok:
                sel.append(i)
        if len(sel) < 15:
            print(f"{name:<10}{'（样本不足）':<22}{len(sel):>6}")
            continue
        vals = [fwd[i] for i in sel]
        mean = float(np.mean(vals))
        diff = mean - base_mean
        p = boot_p(vals, base_mean)
        cl = count_clusters(sel)
        hit = (diff >= MIN_DIFF and p is not None and p < BONFERRONI and cl >= MIN_CLUSTERS)
        if hit:
            hits.append((name, why, len(sel), mean, diff, p, cl))
        results.append((name, len(sel), mean, diff, p, cl, hit))
        label = (f"< {th}" if side == "low" else f"≥ {th}")
        print(f"{name:<10}{label:<22}{len(sel):>6}{mean:>+10.2f}%{diff:>+8.2f}pp"
              f"{p:>10.4f}{cl:>5}  {'★HIT' if hit else '-'}  （{why}）")

    # ── 分年一致性（仅对 HIT 特征）──
    if hits:
        print(f"\n{'='*112}")
        print("【分年一致性】（HIT 特征）")
        print(f"{'='*112}")
        for name, key, th, side, why in [(h[0], None, None, None, h[1]) for h in hits]:
            pass
        for name, why, n_sel, mean, diff, p, cl in hits:
            key = next(f[1] for f in FEATURES if f[0] == name)
            th = next(f[2] for f in FEATURES if f[0] == name)
            side = next(f[3] for f in FEATURES if f[0] == name)
            sel = []
            for i in pool:
                v = ev.get(dts[i], {})
                na = (v.get("up") or 0) + (v.get("down") or 0)
                if key == "vol_ratio":
                    x = (amt[i] / amt_ma[i]) if (amt_ma[i] and not np.isnan(amt_ma[i])) else None
                elif key == "lu_down":
                    x = (v.get("limit_down", 0) / na) if na else None
                elif key == "lu_up":
                    x = (v.get("limit_up", 0) / na) if na else None
                elif key == "up_ratio":
                    x = v.get("up_ratio")
                elif key == "gap_mean":
                    x = v.get("gap_mean")
                else:
                    a, b = prev_vol[5][i], prev_vol[60][i]
                    x = (a / b) if (b and not np.isnan(a) and not np.isnan(b) and b > 0) else None
                if x is None:
                    continue
                if ((x < th) if side == "low" else (x >= th)):
                    sel.append(i)
            by_year = defaultdict(list)
            allv = defaultdict(list)
            for i in sel:
                by_year[dts[i][:4]].append(fwd[i])
            for i in pool:
                allv[dts[i][:4]].append(fwd[i])
            good = sum(1 for y in by_year
                       if np.mean(by_year[y]) > np.mean(allv[y]))
            yrs = sorted(by_year)
            print(f"  {name}：跑赢同年阴跌基准 {good}/{len(yrs)} 年 — "
                  + "  ".join(f"{y}:{np.mean(by_year[y]) - np.mean(allv[y]):+.1f}" for y in yrs))

    # ── 诊断（非判据）：阴跌状态本身 vs 同年全样本 ──
    print(f"\n{'='*112}")
    print("【诊断·非判据】「阴跌状态本身」vs 同年全样本（T+20，仅观察）")
    print(f"{'='*112}")
    pool_set = set(pool)
    by_year, all_year = defaultdict(list), defaultdict(list)
    for i in range(n_idx):
        if not np.isnan(fwd[i]):
            all_year[dts[i][:4]].append(fwd[i])
        if i in pool_set:
            by_year[dts[i][:4]].append(fwd[i])
    print(f"  {'年份':<7}{'阴跌n':>6}{'阴跌T+20':>11}{'全年T+20':>11}{'差':>10}")
    pos = tot_y = 0
    for y in sorted(by_year):
        if len(by_year[y]) < 3:
            continue
        a, b = float(np.mean(by_year[y])), float(np.mean(all_year[y]))
        tot_y += 1
        pos += 1 if a > b else 0
        print(f"  {y:<7}{len(by_year[y]):>6}{a:>+10.2f}%{b:>+10.2f}%{a-b:>+9.2f}pp")
    print(f"  ⇒ 阴跌期跑赢同年全样本：**{pos}/{tot_y} 年**")
    print("  ⚠️ 本段为**探索性观察**，不参与判定；若要采纳须独立预登记验证。")

    # ── 汇总 ──
    print(f"\n{'='*112}")
    print("【预登记判定汇总】")
    print(f"{'='*112}")
    if hits:
        print(f"  ★ **{len(hits)} 个特征 HIT** ⇒ 存在「反弹前兆」候选（须再做独立样本复核）：")
        for name, why, n_sel, mean, diff, p, cl in hits:
            print(f"    · {name}（{why}）：n={n_sel}/{cl}簇，T+{h} {mean:+.2f}%"
                  f"（差 {diff:+.2f}pp，P={p:.4f}）")
        print("\n  ⚠️ 6 个特征中命中若干个本属**预期内**（Bonferroni 已校正，但方向是"
              "\n     先验设定的最佳情形）⇒ **必须**在独立时段/独立指数上复核后才可引入。")
    else:
        print("  ★ **ARCHIVE：无特征 HIT** ⇒ 「多段阴跌后的反弹前兆」在这 6 个直觉变量上**不成立**。")
        print("    即：缩量/跌停潮/涨停回暖/宽度回升/跳空收敛/波动衰减 —— 都无法在阴跌期内")
        print("    显著提升 T+20 收益 ⇒ **regime 的滞后无法用这几个宽度变量提前弥补**。")


if __name__ == "__main__":
    main()
