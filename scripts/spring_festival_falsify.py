#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】春节效应的证伪性检验（P2-a 对照）—— 三个必须排除的混杂因素
================================================================================

【为什么必须做】
  P2-a 主结果（预登记判据）：
    up_ratio  T+2 +18.59pp P=0.0003 18/22 ｜ T+3 +19.13pp P=0.0001 17/22  => 有效应
  机制确认（非预登记）：
    中证500  T+3 +2.35% P=0.0011 16/20 ｜ 中证1000 T+3 +2.76% P=0.0030 11/12 => 显著
    沪深300  T+3 +0.68% P=0.1747             => **不显著**
  => 指向「节后中小盘普涨」。

  ★ 但在下结论前，必须排除三个混杂 —— 否则结论只是「2月效应」「长假效应」的同义反复：

  对照 1【月份】**春节窗口落在 2 月，而 A 股有著名的「春季躁动」（1~2 月行情）**。
         检验：把窗口移到**同月（2 月）非春节的 3 日区间**，是否也有超额？
         若也有 => 这不是「春节效应」，是「2 月效应」。

  对照 2【其他长假】**国庆同为 7~9 天长假，同有「节前避险 / 节后回补」机制**。
         检验：国庆（10 月最长休市缺口）后 T+3 是否也有超额？
         若也有 => 这是「长假效应」（可推广到所有长假），不是春节特有。

  对照 3【年内位置】**把中证500 的「任意第 k 个交易日 → k+3」收益按年内序号铺开**，
         看春节窗口所在位置是否「孤峰突出」。
         若整条曲线在 1~3 月普遍偏高 => 季节性，非春节本身。

【定位方法】与主脚本一致：用「当月最长休市缺口」自动定位，不硬编码。
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


def locate_gap(month: int):
    """用「指定月最长休市缺口」定位长假：返回 [(节前最后交易日, 节后第一个交易日)]。"""
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


def idx_series(tag):
    rows = IDX.get(tag) or []
    return [x['date'][:10] for x in rows], [x['close'] for x in rows]


def cum_ret(ds, cl, dpos, pre_k, post_k, hold):
    if pre_k not in dpos or post_k not in dpos:
        return None
    i0, i1 = dpos[pre_k], dpos[post_k] + hold - 1
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


SPRING = locate_gap(2)
AUTUMN = locate_gap(10)
print("=" * 116)
print("【春节效应证伪性检验 P2-a 对照】")
print("=" * 116)
print(f"春节样本 {len(SPRING)} 个 ｜ 国庆样本 {len(AUTUMN)} 个")

TAG, NAME = 'sh000905', '中证500'
ds, cl = idx_series(TAG)
dpos = {d: i for i, d in enumerate(ds)}
HOLD = 3
base = [(cl[i + HOLD] / cl[i] - 1) * 100 for i in range(len(cl) - HOLD)]
print(f"指数：{NAME}（{ds[0]} ~ {ds[-1]}）｜ 持有 {HOLD} 日 ｜ 基准均值 {sum(base)/len(base):+.3f}%")


def run(name, fests, hold=HOLD):
    rets, yrs = [], []
    for pre_k, post_k in fests:
        r = cum_ret(ds, cl, dpos, pre_k, post_k, hold)
        if r is not None:
            rets.append(r)
            yrs.append(pre_k[:4])
    if not rets:
        return None
    obs, p = perm_p(rets, base)
    wins = sum(1 for x in rets if x > 0)
    print(f"  {name:<22} n={len(rets):>3}  均值 {obs:+.2f}%  中位 "
          f"{sorted(rets)[len(rets)//2]:+.2f}%  P={p:.4f}  正收益 {wins}/{len(rets)}"
          f"  {'SIG' if p < 0.05 else '-'}")
    return {"rets": rets, "yrs": yrs, "p": p, "mean": obs}


print(f"\n{'='*116}")
print(f"【对照 1+2】长假对比：春节（2月）vs 国庆（10月），持有 {HOLD} 日")
print(f"{'='*116}")
s = run("春节（2月缺口）", SPRING)
a = run("国庆（10月缺口）", AUTUMN)

# 五一（5月）作为「短假对照」
MAY = locate_gap(5)
s2 = run("五一（5月缺口）", MAY)

print(f"\n  解读：若春节 == 国庆（都显著的同一量级）=> 「长假效应」；"
      f"若仅春节显著 => 春节特有。")

print(f"\n{'='*116}")
print("【对照 3】年内位置分布：把任意「第 k 日 → k+3 日」收益按年内交易日序号铺开")
print(f"{'='*116}")
# 按「年内第几个交易日」分组
by_year = {}
for i, d in enumerate(ds):
    y = d[:4]
    by_year.setdefault(y, []).append(i)
buckets = {}
for y, idxs in by_year.items():
    for j, i in enumerate(idxs):
        if i + HOLD < len(cl):
            r = (cl[i + HOLD] / cl[i] - 1) * 100
            buckets.setdefault(j, []).append(r)

# 春节窗口在年内的位置（节后第一个交易日的年内序号）
spring_pos = {}
for pre_k, post_k in SPRING:
    y = pre_k[:4]
    if y not in by_year or post_k not in dpos:
        continue
    j = by_year[y].index(dpos[post_k])
    spring_pos[y] = j
pos_list = sorted(set(spring_pos.values()))
print(f"春节「节后第一个交易日」的年内序号：{pos_list}")
print(f"  => 中位序号 {sorted(spring_pos.values())[len(spring_pos)//2]}，"
      f"范围 {min(spring_pos.values())} ~ {max(spring_pos.values())}")

print(f"\n年内序号 → 平均 3 日收益（每 5 个序号取一个，★ 标记春节所在区间）：")
lo, hi = max(0, min(pos_list) - 6), min(len(buckets) - 1, max(pos_list) + 6)
print(f"{'序号':>5}{'n':>5}{'均值%':>9}{'':>2}{'序号':>5}{'n':>5}{'均值%':>9}")
ks = [k for k in sorted(buckets) if lo <= k <= hi]
half = (len(ks) + 1) // 2
for i in range(half):
    k1 = ks[i]
    m1 = sum(buckets[k1]) / len(buckets[k1])
    s1 = "*" if k1 in pos_list else " "
    a1 = f"{k1:>5}{len(buckets[k1]):>5}{m1:>+9.2f}{s1:>2}"
    j = i + half
    if j < len(ks):
        k2 = ks[j]
        m2 = sum(buckets[k2]) / len(buckets[k2])
        s2 = "*" if k2 in pos_list else " "
        b = f"{k2:>5}{len(buckets[k2]):>5}{m2:>+9.2f}{s2:>2}"
    else:
        b = ""
    print(f"{a1}{b}")

# 全年各序号均值的「春节区间 vs 其他」
sp_r = [r for k in pos_list for r in buckets.get(k, [])]
other_r = [r for k, v in buckets.items() if k not in pos_list for r in v]
print(f"\n春节序号区内 {HOLD} 日收益均值：{sum(sp_r)/len(sp_r):+.2f}%（n={len(sp_r)}）")
print(f"其他全部序号        均值：{sum(other_r)/len(other_r):+.2f}%（n={len(other_r)}）")
print(f"=> 若两者接近 => **春节并不特殊，是整段 2 月都偏高（季节性）**。")
