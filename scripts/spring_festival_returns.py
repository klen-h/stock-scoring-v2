#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】春节效应机制确认（P2-a 补充）—— 「宽度高」是否等于「指数涨」？
================================================================================

【定位：★ 非预登记，仅用于理解机制】
  主脚本 `spring_festival_effect.py` 的判据（up_ratio + lu_ratio）已预登记并跑完：
    T+2  Δ=+18.59pp P=0.0003 18/22  => 有效应
    T+3  Δ=+19.13pp P=0.0001 17/22  => 有效应
  本脚本**不修改任何判据**，只回答一个必需的追问：
  **up_ratio 高（多数股票上涨）是否真的转化为「指数收益」？**
  （若只是小盘股普涨而权重不动，则对组合收益无意义）

【方法】
  指数：sh000300（沪深300，2005-04 起）/ sh000905（中证500，2007 起）
        / sh000852（中证1000，2014 起）
  收益：**节前最后交易日收盘 → 节后第 N 个交易日收盘**的累计收益
  对照：全样本中任意等长窗口的收益（置换检验，20000 次）
  ★ 同时给出**中位数** —— 22 个样本里有 2020（疫情）这类极端值，均值可能被拉动。
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


def _d(s):
    y, m, d = (int(x) for x in s.split('-'))
    return date(y, m, d)


def locate_festivals():
    by_year = {}
    for k in EKS:
        dt = _d(k)
        if dt.month in (1, 2):
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


FESTS = locate_festivals()


def idx_series(tag: str):
    rows = IDX.get(tag) or []
    return [x['date'][:10] for x in rows], [x['close'] for x in rows]


def analyze(tag: str, name: str, hold: int):
    """节前最后交易日收盘 → 节后第 hold 个交易日收盘 的累计收益。"""
    ds, cl = idx_series(tag)
    dpos = {d: i for i, d in enumerate(ds)}
    n = len(ds)
    rets, years = [], []
    for pre_k, post_k in FESTS:
        if pre_k not in dpos or post_k not in dpos:
            continue
        i0, i1 = dpos[pre_k], dpos[post_k] + hold - 1
        if i1 >= n:
            continue
        rets.append((cl[i1] / cl[i0] - 1) * 100)
        years.append(pre_k[:4])
    if not rets:
        return None
    # 全样本任意等长窗口
    base = [(cl[i + hold] / cl[i] - 1) * 100 for i in range(n - hold)]
    obs = sum(rets) / len(rets)
    med = sorted(rets)[len(rets) // 2]
    rng = random.Random(SEED)
    cnt = 0
    for _ in range(N_PERM):
        s = rng.sample(base, len(rets))
        if sum(s) / len(s) >= obs:
            cnt += 1
    p = (cnt + 1) / (N_PERM + 1)
    wins = sum(1 for r in rets if r > 0)
    return {"n": len(rets), "mean": obs, "med": med, "base_mean": sum(base) / len(base),
            "p": p, "wins": wins, "rets": rets, "years": years}


print("=" * 116)
print("【春节效应机制确认（P2-a 补充，非预登记）】宽度 → 指数收益")
print("=" * 116)
print(f"定义：节前最后交易日收盘买入 → 节后第 N 日收盘（累计收益）")
print(f"春节样本：{len(FESTS)} 个（指数覆盖不足的年份自动跳过）")
print()

for tag, name in [('sh000300', '沪深300'), ('sh000905', '中证500'), ('sh000852', '中证1000')]:
    ds, _ = idx_series(tag)
    print(f"\n{'-'*116}\n【{name}（{tag}）】数据从 {ds[0]} 起\n{'-'*116}")
    print(f"{'持有':>6}{'n':>4}{'均值%':>9}{'中位%':>9}{'全样本均值%':>13}{'超额pp':>9}{'P值':>9}"
          f"{'正收益年':>10}{'判定':>10}")
    for hold in [1, 2, 3, 4, 5, 10, 20]:
        r = analyze(tag, name, hold)
        if not r:
            continue
        ex = r['mean'] - r['base_mean']
        sig = 'SIG' if r['p'] < 0.05 else '-'
        print(f"{'T+'+str(hold):>6}{r['n']:>4}{r['mean']:>+9.2f}{r['med']:>+9.2f}"
              f"{r['base_mean']:>+13.2f}{ex:>+9.2f}{r['p']:>9.4f}"
              f"{r['wins']:>6}/{r['n']:<4}{sig:>6}")

# ── 逐年明细：沪深300 T+3 ──
print(f"\n{'='*116}")
print("【逐年明细：沪深300「节前最后交易日 → 节后第 3 日」】")
print(f"{'='*116}")
r = analyze('sh000300', '沪深300', 3)
if r:
    print(f"{'年份':>6}{'节前最后':>12}{'收益%':>10}{'':>4}{'年份':>6}{'节前最后':>12}{'收益%':>10}")
    half = (len(r['years']) + 1) // 2
    for i in range(half):
        a = f"{r['years'][i]:>6}{FESTS[i][0]:>12}{r['rets'][i]:>+10.2f}"
        j = i + half
        b = (f"{r['years'][j]:>6}{FESTS[j][0]:>12}{r['rets'][j]:>+10.2f}"
             if j < len(r['years']) else "")
        print(f"{a}{'':>4}{b}")
    print(f"\n均值 {r['mean']:+.2f}% ｜ 中位 {r['med']:+.2f}% ｜ "
          f"正收益 {r['wins']}/{r['n']} ｜ P={r['p']:.4f}")

# ── 极端值影响检查 ──
print(f"\n{'='*116}")
print("【极端值影响检查：去掉最好/最差各 2 年后，均值还剩多少？】")
print(f"{'='*116}")
for tag, name in [('sh000300', '沪深300'), ('sh000905', '中证500')]:
    print(f"\n{name}:")
    for hold in [3, 5]:
        r = analyze(tag, name, hold)
        if not r:
            continue
        s = sorted(r['rets'])
        trimmed = s[2:-2]
        print(f"  T+{hold}: 原始均值 {r['mean']:+.2f}%（n={r['n']}）"
              f" → 去两端各2年 {sum(trimmed)/len(trimmed):+.2f}%（n={len(trimmed)}）"
              f" ｜ 中位 {r['med']:+.2f}%")
