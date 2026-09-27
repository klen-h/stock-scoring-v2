#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】春节效应的「可操作性」检验（P2-a 补充 2）—— 买入时点与成本
================================================================================

【为什么必须做】
  前面所有口径都是「**节前最后交易日收盘**买入」=> 意味着**持股过 9 天长假**
  （隔夜风险，如 2020 疫情：节后首日 up_ratio 仅 0.0437）。作为可执行方案，
  这个前提是不可接受的 —— 必须验证「**节后再进场**」是否还保留 edge。

【三个买入时点对比】（均持有至其后第 N 个交易日收盘）
  A) 节前最后交易日收盘买入（基线口径，需持股过节）
  B) 节后第 1 个交易日（T+1）收盘买入   <- **无需持股过节**
  C) 节后第 2 个交易日（T+2）收盘买入
  ★ 时点 B/C 是「人可以在节后开盘后决策」的真实口径。

【成本】
  中证500/1000 ETF：双边按 **0.10%**（佣金+滑点，较保守）扣除，输出净值版本。

【样本】
  中证500（2007~ ）/ 中证1000（2014~ ）。极端年（2020）单独列出。
================================================================================
"""
import os, sys, json, random
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EH = json.load(open(os.path.join(ROOT, 'data', 'event_history.json'), encoding='utf-8'))
EKS = sorted(EH)
IDX = json.load(open(os.path.join(ROOT, 'data', 'idx_daily.json'), encoding='utf-8'))
N_PERM = 20000
SEED = 20260927
COST = 0.10  # 双边 %


def _d(s):
    y, m, d = (int(x) for x in s.split('-'))
    return date(y, m, d)


def locate_gap(month):
    by_year = {}
    for k in EKS:
        dt = _d(k)
        if dt.month == month:
            by_year.setdefault(dt.year, []).append(dt)
    out = []
    for y in sorted(by_year):
        ds = by_year[y]
        if len(ds) < 2:
            continue
        _, a, b = sorted((((b - a).days, a, b) for a, b in zip(ds, ds[1:])),
                         key=lambda x: -x[0])[0]
        out.append((a.isoformat(), b.isoformat()))
    return out


def series(tag):
    rows = IDX.get(tag) or []
    return [x['date'][:10] for x in rows], [x['close'] for x in rows]


SPRING = locate_gap(2)


def ret_from(ds, cl, dpos, pre_k, post_k, entry_offset, hold):
    """
    entry_offset: 0=节前最后交易日, 1=节后第1日, 2=节后第2日
    hold: 从入场日算起的持有交易日数
    """
    if pre_k not in dpos or post_k not in dpos:
        return None
    if entry_offset == 0:
        i0 = dpos[pre_k]
    else:
        i0 = dpos[post_k] + (entry_offset - 1)
    i1 = i0 + hold
    if i0 >= len(cl) or i1 >= len(cl):
        return None
    return (cl[i1] / cl[i0] - 1) * 100


def perm_p(obs_list, base, seed=SEED):
    obs = sum(obs_list) / len(obs_list)
    rng = random.Random(seed)
    cnt = 0
    for _ in range(N_PERM):
        s = rng.sample(base, len(obs_list))
        if sum(s) / len(s) >= obs:
            cnt += 1
    return obs, (cnt + 1) / (N_PERM + 1)


for TAG, NAME in [('sh000905', '中证500'), ('sh000852', '中证1000'), ('sh000300', '沪深300')]:
    ds, cl = series(TAG)
    dpos = {d: i for i, d in enumerate(ds)}
    print("=" * 116)
    print(f"【{NAME}（{TAG}）】{ds[0]} ~ {ds[-1]}")
    print("=" * 116)
    print(f"{'买入时点':<26}{'持有':>5}{'n':>4}{'均值%':>9}{'中位%':>9}"
          f"{'扣成本%':>10}{'P值':>9}{'正收益':>9}")
    for entry, elabel in [(0, "A 节前最后日收盘（持股过节）"),
                          (1, "B 节后T+1收盘（无隔夜）"),
                          (2, "C 节后T+2收盘")]:
        for hold in [1, 2, 3, 5, 10]:
            rets = [r for pre_k, post_k in SPRING
                    if (r := ret_from(ds, cl, dpos, pre_k, post_k, entry, hold)) is not None]
            if not rets:
                continue
            base = [(cl[i + hold] / cl[i] - 1) * 100 for i in range(len(cl) - hold)]
            obs, p = perm_p(rets, base)
            wins = sum(1 for x in rets if x > 0)
            sig = 'SIG' if p < 0.05 else '-'
            print(f"{elabel if hold == 1 else '':<26}{'T+'+str(hold):>5}{len(rets):>4}"
                  f"{obs:>+9.2f}{sorted(rets)[len(rets)//2]:>+9.2f}"
                  f"{obs - COST:>+10.2f}{p:>9.4f}{wins:>5}/{len(rets):<3} {sig}")
        print()
    print()

# ── 2020 年单独列出（疫情极端值）──
print("=" * 116)
print("【极端年影响：2020（疫情）在「B 节后T+1收盘买入 / 持有3日」下的表现】")
print("=" * 116)
for TAG, NAME in [('sh000905', '中证500'), ('sh000852', '中证1000')]:
    ds, cl = series(TAG)
    dpos = {d: i for i, d in enumerate(ds)}
    print(f"\n{NAME}：")
    for pre_k, post_k in SPRING:
        r3 = ret_from(ds, cl, dpos, pre_k, post_k, 1, 3)
        if r3 is None:
            continue
        mark = "  <== 疫情" if pre_k.startswith('2020') else ""
        print(f"  {pre_k} 节后({post_k}) T+1买入持3日: {r3:+7.2f}%{mark}")
    rets = [r for pre_k, post_k in SPRING
            if (r := ret_from(ds, cl, dpos, pre_k, post_k, 1, 3)) is not None]
    if rets:
        no20 = [r for (pre_k, _), r in zip(SPRING, rets) if not pre_k.startswith('2020')]
        print(f"  ---> 含2020均值 {sum(rets)/len(rets):+.2f}%（n={len(rets)}）"
              f" ｜ 剔除2020 {sum(no20)/len(no20):+.2f}%（n={len(no20)}）")
