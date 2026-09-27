#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动信号源 · 第 2 层】事件预测力检验（预登记判定标准）
================================================================================
背景：
  regime 引擎只用沪深300 均线，在 V 型反转处**系统性滞后**（2024-09-24 政策底、
  2024-09-30 涨停潮均被判 defensive，见 `_report_step1b_nb_serialization`§6）。
  事件驱动信号源的定位 = **用宽度/涨跌停等盘中即可观测的"事件"对冲均线滞后**。

数据：data/event_history.json（build_event_history.py 产出，全市场 21 年，
      涨跌停用**真实涨跌停价** high_limit/low_limit 精确判定）。

━━━━━━━━━━━━━━━━━━ 预登记（禁止事后调参）━━━━━━━━━━━━━━━━━━
事件定义（阈值基于**分布分位**选定，与收益无关；改阈值必须在本节留痕）：

  E1 capitulation（恐慌冰点）
      判据：limit_down >= 200  或  up_ratio <= 0.10
      依据：limit_down p99≈487 / up_ratio p5≈0.105 ⇒ 约取"最极端 3~5% 的日子"
      语义：抛售极值，抄底时点候选

  E2 policy_surge（政策脉冲）
      判据：up_ratio >= 0.95  且  limit_up >= 50
      依据：up_ratio p95≈0.948 ⇒ "极端普涨"；limit_up>=50 排除小样本日
      语义：政策性利好脉冲（924/930 式），反转启动候选

  E3 panic_reversal（恐慌后反转确认）
      判据：当日 up_ratio >= 0.80 且 前 5 个交易日内出现过 E1
      语义：冰点后的第一根大阳 = 反转确认信号（要交易的时点）

预登记判定（主判定 = T+20；次级 = T+1/T+5/T+10）：
  收益口径：**全市场等权**日收益（event_history.pct_mean）累乘，T+1..T+N。
    · 事件日 T 收盘后确认 ⇒ T+1 起算（近似"次日开盘买、持 N 日"）。
    · ⚠️ 已知偏差：等权未剔除"次日一字涨停买不进" ⇒ 事件后收益**偏乐观**
      （E2 尤甚）。故 E2 结论只作方向参考，须与 E1/E3 交叉。
  主判定通过条件（三条同时满足）：
    (a) 事件数 >= 15（否则 insufficient）
    (b) 事件后 T+20 均值 - 全样本 T+20 均值 >= +2.0 pct
    (c) block bootstrap P(事件均值 <= 0) < 0.05
  反向判定（无 edge）：
    差值 < +1.0 pct 且 bootstrap 不显著 ⇒ 记「无 edge」，归档不接入。
  其余 ⇒ 弱信号，归档。

bootstrap：对事件日 T+20 收益做**有放回重采样**（B=10000，固定种子）。
  注意：相邻事件高度重叠（同一次恐慌延续多日）⇒ 额外报告**独立事件簇**
  （相邻事件间隔 < 20 交易日归为同一簇），簇数才是真实独立观测数。

用法：python scripts/event_edge_check.py [--json data/event_history.json]
      [--horizons 1 5 10 20]
================================================================================
"""
import argparse
import json
import os
import random
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── 预登记阈值（改这里必须在上方文档留痕）──
E1_LIMIT_DOWN = 200
E1_UP_RATIO = 0.10
E2_UP_RATIO = 0.95
E2_LIMIT_UP = 50
# ★ 2026-09-27 P1-b：**比例口径**（涨停家数 / (涨家数+跌家数)）—— 消除"池子大小漂移"
#   （绝对家数阈值在缩池后触发天数偏离 62~82%，比例口径仅 2.5~3.0%）。
#   设了 E2_LIMIT_UP_RATIO 时优先于 E2_LIMIT_UP。
E2_LIMIT_UP_RATIO = None
E3_UP_RATIO = 0.80
E3_LOOKBACK = 5
CLUSTER_GAP = 20          # 相邻事件 < 20 交易日 ⇒ 视为同一事件簇

PASS_DIFF = 2.0           # 主判定收益差门槛（pct）
NOEDGE_DIFF = 1.0         # 反向判定门槛（pct）
MIN_EVENTS = 15


# ── 事件判定 ──
def is_capitulation(v) -> bool:
    return (v["limit_down"] >= E1_LIMIT_DOWN) or (v["up_ratio"] <= E1_UP_RATIO)


def is_policy_surge(v) -> bool:
    if v["up_ratio"] < E2_UP_RATIO:
        return False
    if E2_LIMIT_UP_RATIO:      # 比例口径（池子无关，见常量注释）
        denom = (v.get("up") or 0) + (v.get("down") or 0)
        return denom > 0 and (v["limit_up"] / denom) >= E2_LIMIT_UP_RATIO
    return v["limit_up"] >= E2_LIMIT_UP


def is_panic_reversal(v, seq, i) -> bool:
    if v["up_ratio"] < E3_UP_RATIO:
        return False
    for k in range(max(0, i - E3_LOOKBACK), i):
        if is_capitulation(seq[k]):
            return True
    return False


def collect_events(dates, seq):
    """→ {event_name: [index, ...]}（index 为 dates 下标）。"""
    ev = {"E1_capitulation": [], "E2_policy_surge": [], "E3_panic_reversal": []}
    for i, v in enumerate(seq):
        if is_capitulation(v):
            ev["E1_capitulation"].append(i)
        if is_policy_surge(v):
            ev["E2_policy_surge"].append(i)
        if is_panic_reversal(v, seq, i):
            ev["E3_panic_reversal"].append(i)
    return ev


# ── 收益 ──
def fwd_cum(rets, i, n):
    """事件日 i 之后 T+1..T+n 的等权累计收益（%）。**T 收盘起算**（乐观口径）。"""
    if i + 1 + n > len(rets):
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        cum *= (1 + rets[k] / 100.0)
    return (cum - 1) * 100


def fwd_cum_open(rets, gaps, i, n):
    """★ 可执行口径：**T+1 开盘买入** → T+n 收盘。
    事件日大涨 ⇒ T+1 往往高开，`fwd_cum` 会把这段跳空算成收益（买不到），
    故必须除以 T+1 开盘相对 T 收盘的跳空。"""
    if i + 1 + n > len(rets):
        return None
    entry = 1 + gaps[i + 1] / 100.0
    if entry <= 0.05:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        cum *= (1 + rets[k] / 100.0)
    return (cum / entry - 1) * 100


def _stats(vals):
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if not n:
        return None
    s = sorted(vals)
    return {"n": n, "mean": sum(vals) / n, "median": s[n // 2],
            "win": sum(1 for v in vals if v > 0) / n * 100}


def _fmt(st):
    if not st:
        return "     -        -       -  "
    return "%+6.2f%%  %+6.2f%%  %5.1f%%" % (st["mean"], st["median"], st["win"])


def block_bootstrap(vals, B=10000, seed=42):
    """有放回重采样事件收益 → (ci_lo, ci_hi, p_leq0)。"""
    rng = random.Random(seed)
    n = len(vals)
    if n == 0:
        return None, None, None
    boot = []
    for _ in range(B):
        s = sum(rng.choice(vals) for _ in range(n)) / n
        boot.append(s)
    boot.sort()
    return boot[int(B * 0.025)], boot[int(B * 0.975)], sum(1 for m in boot if m <= 0) / B


def count_clusters(idx_list):
    """相邻事件间隔 < CLUSTER_GAP 归为同一簇 → 簇数。"""
    if not idx_list:
        return 0
    clusters = 1
    for a, b in zip(idx_list, idx_list[1:]):
        if b - a >= CLUSTER_GAP:
            clusters += 1
    return clusters


def _load_regime_map():
    """读 nb_history.json（四态）→ {date: state}。缺失返回 {}（分组自动跳过）。"""
    p = os.path.join(ROOT, "data", "nb_history.json")
    if not os.path.exists(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            m = json.load(f)
        return {d: (v.get("state_4") or v.get("state_3")) for d, v in m.items()}
    except Exception:
        return {}


def main():
    global E2_UP_RATIO, E2_LIMIT_UP, E2_LIMIT_UP_RATIO

    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=os.path.join(ROOT, "data", "event_history.json"))
    ap.add_argument("--horizons", type=int, nargs="+", default=[1, 5, 10, 20])
    # ★ 2026-09-27：阈值可覆盖，用于 P1 敏感性 / 比例化分析复现（默认 = 原始预登记值）。
    ap.add_argument("--up-ratio", type=float, default=E2_UP_RATIO,
                    help=f"E2 up_ratio 阈值（默认 {E2_UP_RATIO}）")
    ap.add_argument("--limit-up", type=int, default=E2_LIMIT_UP,
                    help=f"E2 limit_up 绝对家数阈值（默认 {E2_LIMIT_UP}）")
    ap.add_argument("--limit-up-ratio", type=float, default=None,
                    help="E2 涨停**比例**阈值（涨停家数/涨跌家数合计），如 0.02；"
                         "给了则忽略 --limit-up")
    args = ap.parse_args()

    E2_UP_RATIO, E2_LIMIT_UP = args.up_ratio, args.limit_up
    E2_LIMIT_UP_RATIO = args.limit_up_ratio
    _desc = (f"limit_up 比例>={E2_LIMIT_UP_RATIO}"
             if E2_LIMIT_UP_RATIO else f"limit_up 绝对>={E2_LIMIT_UP} 家")
    print(f"E2 阈值：up_ratio>={E2_UP_RATIO} 且 {_desc}")

    with open(args.json, encoding="utf-8") as f:
        raw = json.load(f)
    dates = sorted(raw)
    seq = [raw[d] for d in dates]
    rets = [v["pct_mean"] for v in seq]
    gaps = [v.get("gap_mean", 0.0) for v in seq]

    print(f"数据：{len(dates)} 个交易日  {dates[0]} ~ {dates[-1]}")

    regime_map = _load_regime_map()
    if regime_map:
        cov = sum(1 for d in dates if d in regime_map)
        print(f"regime 覆盖 {cov}/{len(dates)} 天（{min(regime_map)} ~ {max(regime_map)}）")

    ev = collect_events(dates, seq)

    # 全样本基准（两种口径）
    base, base_open = {}, {}
    for h in args.horizons:
        base[h] = _stats([fwd_cum(rets, i, h) for i in range(len(dates))])
        base_open[h] = _stats([fwd_cum_open(rets, gaps, i, h) for i in range(len(dates))])

    print(f"\n全样本基准（等权）：")
    print(f"  {'H':>3} {'n':>6}  {'T收盘起算':>11}  {'T+1开盘买':>11}  {'跳空拖累':>10}")
    for h in args.horizons:
        b, bo = base[h], base_open[h]
        print(f"  {h:>3} {b['n']:>6}  {b['mean']:>+10.2f}%  {bo['mean']:>+10.2f}%  "
              f"{bo['mean'] - b['mean']:>+9.2f}pp")

    verdicts = {}
    for name in ("E1_capitulation", "E2_policy_surge", "E3_panic_reversal"):
        idxs = ev[name]
        print(f"\n{'='*78}")
        print(f"【{name}】事件 {len(idxs)} 天，独立簇 {count_clusters(idxs)}")
        print(f"{'='*78}")
        if not idxs:
            print("  （无事件）")
            verdicts[name] = "no_event"
            continue
        # 事件日期列表（前 12 个 + 末 3 个）
        show = [dates[i] for i in idxs]
        print("  日期: " + ", ".join(show[:12]) + (f" … {show[-1]}" if len(show) > 12 else ""))

        print(f"\n  {'H':>3}  {'事件(T收盘)':>12}  {'事件(T+1开盘)':>14}  "
              f"{'差vs基准':>10}  {'win%':>6}")
        for h in args.horizons:
            st = _stats([fwd_cum(rets, i, h) for i in idxs])
            sto = _stats([fwd_cum_open(rets, gaps, i, h) for i in idxs])
            if not st or not sto:
                continue
            diff = sto["mean"] - base_open[h]["mean"]
            print(f"  {h:>3}  {st['mean']:>+11.2f}%  {sto['mean']:>+13.2f}%  "
                  f"{diff:>+8.2f}pp  {sto['win']:>5.1f}%")

        # 主判定（T+20，若无则用最大 horizon）——★ 用**可执行口径**（T+1 开盘买）
        h_main = 20 if 20 in args.horizons else args.horizons[-1]
        main_vals = [v for v in (fwd_cum_open(rets, gaps, i, h_main) for i in idxs)
                     if v is not None]
        b_main = base_open[h_main]
        lo, hi, p = block_bootstrap(main_vals)
        diff = (sum(main_vals) / len(main_vals) - b_main["mean"]) if main_vals else None

        print(f"\n  ── 主判定（T+{h_main}，T+1 开盘买）──")
        if len(main_vals) < MIN_EVENTS:
            v = "insufficient"
            print(f"  事件数 {len(main_vals)} < {MIN_EVENTS} ⇒ insufficient（保留观察，不下结论）")
        else:
            print(f"  bootstrap 95% CI: [{lo:+.2f}%, {hi:+.2f}%]   P(mean<=0)={p:.3f}")
            print(f"  收益差 vs 基准: {diff:+.2f}pp")
            if diff >= PASS_DIFF and p < 0.05:
                v = "PASS"
                print("  ★ 判定：PASS（显著正 edge，具备接入价值）")
            elif abs(diff) < NOEDGE_DIFF and p >= 0.05:
                v = "NO_EDGE"
                print("  ★ 判定：NO EDGE（与基准无差异，归档不接入）")
            else:
                v = "WEAK"
                print("  ★ 判定：WEAK（不达标，归档，不调参重试）")
        verdicts[name] = v

        # ── regime 条件检验：事件能否在 defensive 期内提前确认反转（接入决策关键）──
        if regime_map:
            print(f"\n  ── 按 regime 分组（T+{h_main}，T+1 开盘买）──")
            # regime 内基准：同 regime 内**所有日**的 T+h 均值。
            # ⚠️ 必须用它做对照：defensive 期间整体本就可能有正收益（超跌反弹），
            #    直接与全局基准 (+3.15%) 比会把 regime 效应误读成事件 edge。
            rbase = {}
            for st_name in ("defensive", "neutral_bearish", "neutral", "offensive"):
                av = [x for x in (fwd_cum_open(rets, gaps, i, h_main)
                                  for i in range(len(dates))
                                  if regime_map.get(dates[i]) == st_name) if x is not None]
                rbase[st_name] = (sum(av) / len(av), len(av)) if av else (None, 0)
            print(f"    {'regime':<15}{'事件n':>6}{'事件mean':>10}{'win%':>7}"
                  f"{'该态基准':>10}{'增量':>9}{'簇':>4}")
            for st_name in ("defensive", "neutral_bearish", "neutral", "offensive"):
                sub = [i for i in idxs if regime_map.get(dates[i]) == st_name]
                subv = [x for x in (fwd_cum_open(rets, gaps, i, h_main) for i in sub)
                        if x is not None]
                rb, rn = rbase[st_name]
                if not subv or rb is None:
                    print(f"    {st_name:<15}{len(subv):>6}         -      -"
                          f"{'%+.2f%%' % rb if rb is not None else '        -':>10}"
                          f"{'':>9}{count_clusters(sub):>4}")
                    continue
                m = sum(subv) / len(subv)
                print(f"    {st_name:<15}{len(subv):>6}{m:>+9.2f}%"
                      f"{sum(1 for x in subv if x > 0)/len(subv)*100:>6.1f}%"
                      f"{rb:>+9.2f}%{m - rb:>+8.2f}pp{count_clusters(sub):>4}")

        # 分年稳健性（防止单一时段主导）
        by_year = defaultdict(list)
        for i in idxs:
            val = fwd_cum_open(rets, gaps, i, h_main)
            if val is not None:
                by_year[dates[i][:4]].append(val)
        if by_year:
            print(f"\n  分年（T+{h_main} 均收益 / 事件数）：")
            line = "    "
            for y in sorted(by_year):
                vv = by_year[y]
                line += f"{y}:{sum(vv)/len(vv):+.1f}%({len(vv)}) "
                if len(line) > 100:
                    print(line)
                    line = "    "
            if line.strip():
                print(line)
            pos_years = sum(1 for y in by_year if sum(by_year[y]) / len(by_year[y]) > 0)
            print(f"    正收益年份 {pos_years}/{len(by_year)}")

    # ── 汇总 ──
    print(f"\n{'='*78}")
    print("【汇总】预登记判定结果")
    print(f"{'='*78}")
    LABEL = {"PASS": "通过（可接入）", "NO_EDGE": "无 edge（归档）",
             "WEAK": "弱信号（归档）", "insufficient": "样本不足（观察）",
             "no_event": "无事件"}
    for name in ("E1_capitulation", "E2_policy_surge", "E3_panic_reversal"):
        print(f"  {name:<22} {LABEL.get(verdicts.get(name), verdicts.get(name))}")
    print()


if __name__ == "__main__":
    main()
