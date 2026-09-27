#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】春节效应的分时段稳健性（P2-a 补充 3）
================================================================================

【为什么必须做】
  20 年样本里，若效应只集中在某一段（如 2007~2015 的牛熊剧烈期），
  则「近十年已失效」—— 这是时间维度的最大混杂。

  同时检查：
  · 节后首日**跳空**（是否已被开盘价吃掉 => 决定"节前买"还是"节后买"）
  · 逐年滚动（近 5 年 / 近 10 年）

【口径】中证500 / 中证1000 ｜ 节后 T+1 收盘买入，持有 3 日
        （前序已验证：该口径**无需持股过节**且显著）
================================================================================
"""
import os, sys, json
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EH = json.load(open(os.path.join(ROOT, 'data', 'event_history.json'), encoding='utf-8'))
EKS = sorted(EH)
IDX = json.load(open(os.path.join(ROOT, 'data', 'idx_daily.json'), encoding='utf-8'))


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


SPRING = locate_gap(2)
print("=" * 110)
print("【春节效应分时段稳健性】中证500 / 中证1000 ｜ 节后T+1收盘买入持3日")
print("=" * 110)
print("春节样本：", [p[:4] for p, _ in SPRING])

for TAG, NAME in [('sh000905', '中证500'), ('sh000852', '中证1000')]:
    rows = IDX.get(TAG) or []
    ds = [x['date'][:10] for x in rows]
    cl = [x['close'] for x in rows]
    has_open = 'open' in (rows[0] if rows else {})
    op = [x.get('open') for x in rows] if has_open else None
    dpos = {d: i for i, d in enumerate(ds)}

    print(f"\n{'-'*110}\n【{NAME}】字段含 open? {has_open}\n{'-'*110}")
    print(f"{'春节':>6}{'节后首日':>12}{'首日涨%':>10}{'T+1买持3日%':>14}")
    per = []
    for pre_k, post_k in SPRING:
        if pre_k not in dpos or post_k not in dpos:
            continue
        i0, i1 = dpos[pre_k], dpos[post_k]
        if i1 + 3 >= len(cl):
            continue
        first = (cl[i1] / cl[i0] - 1) * 100
        b = (cl[i1 + 3] / cl[i1] - 1) * 100
        per.append((pre_k[:4], first, b))
        print(f"{pre_k[:4]:>6}{post_k:>12}{first:>+10.2f}{b:>+14.2f}")

    # 分时段
    print()
    for label, lo, hi in [("全部", 0, 9999), ("2007~2015", 2007, 2015),
                          ("2016~2026", 2016, 2026), ("近10年", 2017, 2026),
                          ("近5年", 2022, 2026)]:
        sub = [b for y, f, b in per if lo <= int(y) <= hi]
        if not sub:
            continue
        w = sum(1 for x in sub if x > 0)
        print(f"  {label:<12} n={len(sub):>3}  均值 {sum(sub)/len(sub):+.2f}%  "
              f"中位 {sorted(sub)[len(sub)//2]:+.2f}%  正收益 {w}/{len(sub)}")

    # 跳空检查
    if has_open:
        gaps = []
        for pre_k, post_k in SPRING:
            if pre_k not in dpos or post_k not in dpos:
                continue
            i0, i1 = dpos[pre_k], dpos[post_k]
            if op[i1] and cl[i0]:
                gaps.append((op[i1] / cl[i0] - 1) * 100)
        if gaps:
            print(f"\n  节后首日**开盘跳空**（前收->首日开）：均值 {sum(gaps)/len(gaps):+.2f}%  "
                  f"中位 {sorted(gaps)[len(gaps)//2]:+.2f}%  (n={len(gaps)})")
            print(f"  => 该跳空幅度即「节前买入 vs 节后买入」的收益差。")
    else:
        print(f"\n  （idx_daily 无 open 字段 => 无法直接算跳空，"
              f"但可用「节前最后日->节后首日收盘」{sum(f for _, f, _ in per)/len(per):+.2f}% 近似）")

    # 逐年胜负（T+1买持3日 vs 全样本均值）
    base3 = [(cl[i + 3] / cl[i] - 1) * 100 for i in range(len(cl) - 3)]
    bm = sum(base3) / len(base3)
    print(f"\n  基准（任意3日）均值 {bm:+.3f}%")
    print(f"  逐年超额: ", end="")
    for y, f, b in per:
        print(f"{y}:{b-bm:+.1f} ", end="")
    print()
