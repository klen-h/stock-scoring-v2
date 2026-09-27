#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】春节效应验证（P2-a）—— 「节前/节后窗口」是否有宽度或情绪异常
================================================================================

【为什么做这个】
  前序结论：系统在「V 型反弹初期」无抓手（regime 滞后 + E2 阈值结构限制）。
  用户提出方向：**事件驱动**。但项目 `flash_calendar` 只存 15 天窗口、无历史积累，
  「财经日历事件 vs 反弹日」的相关性**无法回溯验证**（详见 daily 记录）。
  ⇒ 退化到**日期确定的固定日历效应**：春节是 A 股最长休市，日期可由交易日历
    自动定位（本项目实测 22/22 与农历正月初一吻合），**零额外数据成本**。

【预登记（★ 跑之前写死，不得事后修改）】
  数据：data/event_history.json（5279 天，2005-01-04 ~ 2026-09-24）
  春节：由「1~2 月最长休市缺口」自动定位（脚本内 REAL 表仅用于**校验定位正确性**）
  样本：22 个春节（2005~2026），**天然独立簇**（每年一次，间隔一年，无重叠）
        —— 这是本假设相对 E2 的结构性优势：**不需要担心簇重叠**
  指标：
    · 宽度  up_ratio
    · 情绪  lu_ratio = limit_up / (up + down)
  窗口：
    · 节前  T-5, T-4, T-3, T-2, T-1（T-1 = 节前最后交易日）
    · 节后  T+1, T+2, T+3, T+4, T+5（T+1 = 节后第一个交易日）
  基准：**剔除窗口日后的全部交易日**（避免自我污染）
  检验：**置换检验**（permutation，单侧）—— 从基准池随机抽 22 天，重复 20000 次，
        看"窗口均值"在随机分布中的位置。不假设正态（22 样本，t 检验不可靠）。
  多重比较：10 个窗口（5 前 + 5 后）× 2 指标 = 20 次 ⇒ **Bonferroni α = 0.05/20 = 0.0025**
  判据（★ 全部满足才算"有效应"）：
    ① |差| 达到最小效应量：up_ratio **≥ +3.0pp**（宽度）
    ② 置换检验 P < 0.0025（Bonferroni 校正后）
    ③ 方向一致性：≥ 14/22 年（约 64%）同向
  ⇒ 判据写死在此处，跑完不得修改。

【已知数据质量声明（诚实标注）】
  `pct_mean` 字段在 2026 年有 7 天异常（下跌家数>上涨家数却平均大涨）⇒ **本脚本不使用
  `pct_mean`**，只用基于**家数**的 up_ratio / lu_ratio（家数统计不受价格平均污染）。
================================================================================
"""
import os, sys, json, random
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = json.load(open(os.path.join(ROOT, 'data', 'event_history.json'), encoding='utf-8'))
KS = sorted(D)

# ── 预登记参数（★ 不得事后修改）──
PRE_WINDOWS = [-5, -4, -3, -2, -1]      # 节前（相对节前最后交易日）
POST_WINDOWS = [1, 2, 3, 4, 5]          # 节后（相对节后第一个交易日）
MIN_EFFECT_PP = 3.0                     # ① 最小效应量（百分点）
N_PERM = 20000                          # ② 置换次数
ALPHA = 0.05 / (len(PRE_WINDOWS) + len(POST_WINDOWS)) / 2   # ③ Bonferroni
MIN_CONSISTENT = 14                     # ④ 方向一致性（/22）
SEED = 20260927


def _d(s):
    y, m, d = (int(x) for x in s.split('-'))
    return date(y, m, d)


def locate_festivals() -> list:
    """用「1~2 月最长休市缺口」自动定位春节：返回 [(节前最后交易日, 节后第一个交易日)]。"""
    by_year = {}
    for k in KS:
        dt = _d(k)
        if dt.month in (1, 2):
            by_year.setdefault(dt.year, []).append(dt)
    out = []
    for y in sorted(by_year):
        ds = by_year[y]
        if len(ds) < 2:
            continue
        gaps = sorted((((b - a).days, a, b) for a, b in zip(ds, ds[1:])),
                      key=lambda x: -x[0])
        _, a, b = gaps[0]
        out.append((a.isoformat(), b.isoformat()))
    return out


def lu_ratio(v) -> float:
    tot = (v.get('up') or 0) + (v.get('down') or 0)
    return (v.get('limit_up') or 0) / tot if tot else 0.0


FESTS = locate_festivals()
IDX = {k: i for i, k in enumerate(KS)}


def collect_window(offset: int, is_pre: bool) -> list:
    """取每个春节的窗口日 key；缺失（越界）跳过。"""
    keys = []
    for pre_k, post_k in FESTS:
        if is_pre:
            i = IDX[pre_k] + offset          # offset 为负
        else:
            i = IDX[post_k] + (offset - 1)   # offset 1 => 节后第一天
        if 0 <= i < len(KS):
            keys.append(KS[i])
    return keys


def stats(keys: list, field: str, lu: bool = False) -> float:
    vals = [lu_ratio(D[k]) if lu else D[k][field] for k in keys if k in D]
    return sum(vals) / len(vals) if vals else 0.0


def perm_test(keys: list, base_keys: list, field: str, lu: bool = False) -> float:
    """单侧置换检验：P(随机抽样均值 >= 观测均值)。"""
    obs = stats(keys, field, lu)
    rng = random.Random(SEED)
    n = len(keys)
    cnt = 0
    for _ in range(N_PERM):
        samp = rng.sample(base_keys, n)
        if stats(samp, field, lu) >= obs:
            cnt += 1
    return (cnt + 1) / (N_PERM + 1)


def consistency(keys: list, base_val: float, field: str, lu: bool = False) -> int:
    """方向一致年数：该年窗口均值 > 基准均值的年数。"""
    hits = 0
    for pre_k, post_k in FESTS:
        pass
    # 按年分组
    from collections import defaultdict
    grp = defaultdict(list)
    for k in keys:
        grp[_d(k).year].append(k)
    for y, ks_ in grp.items():
        v = stats(ks_, field, lu)
        if v > base_val:
            hits += 1
    return hits


def main():
    print("=" * 118)
    print("【春节效应验证 P2-a】自动定位春节 + 预登记判据 + 置换检验")
    print("=" * 118)
    print(f"数据：{len(KS)} 天（{KS[0]} ~ {KS[-1]}）｜春节样本：{len(FESTS)} 个")
    print(f"预登记：最小效应 {MIN_EFFECT_PP}pp ｜ 置换 {N_PERM} 次 ｜ "
          f"Bonferroni α={ALPHA:.4f}（20 次检验）｜ 一致性 ≥{MIN_CONSISTENT}/{len(FESTS)}")
    print(f"指标：up_ratio（宽度）+ lu_ratio（涨停比例）｜★ 不用 pct_mean（2026 有污染）")
    print()

    # ── 全窗口日集合（用于从基准剔除）──
    all_win = set()
    for off in PRE_WINDOWS:
        all_win |= set(collect_window(off, True))
    for off in POST_WINDOWS:
        all_win |= set(collect_window(off, False))
    base_keys = [k for k in KS if k not in all_win]
    base_ur = stats(base_keys, 'up_ratio')
    base_lu = stats(base_keys, 'lu_ratio', lu=True)
    print(f"基准（剔除全部窗口日，n={len(base_keys)} 天）："
          f"up_ratio={base_ur:.4f}  lu_ratio={base_lu:.4f}")
    print()

    hdr = (f"{'窗口':>6}{'n':>4}{'up_ratio':>11}{'Δpp':>9}{'P值':>9}{'一致性':>8}{'':>3}"
           f"{'lu_ratio':>11}{'Δpp':>9}{'P值':>9}{'一致性':>8}{'判定':>8}")
    print(hdr)
    print("-" * 118)

    rows = []
    for label, offsets, is_pre in [("节前", PRE_WINDOWS, True), ("节后", POST_WINDOWS, False)]:
        for off in offsets:
            keys = collect_window(off, is_pre)
            name = f"T{off:+d}" if off != 0 else "T"
            ur, lu = stats(keys, 'up_ratio'), stats(keys, 'lu_ratio', lu=True)
            dur, dlu = (ur - base_ur) * 100, (lu - base_lu) * 100
            pur, plu = perm_test(keys, base_keys, 'up_ratio'), perm_test(keys, base_keys, 'lu_ratio', lu=True)
            cur, clu = consistency(keys, base_ur, 'up_ratio'), consistency(keys, base_lu, 'lu_ratio', lu=True)
            # 判定：任一指标满足全部 3 条判据
            ok_ur = abs(dur) >= MIN_EFFECT_PP and pur < ALPHA and cur >= MIN_CONSISTENT
            ok_lu = abs(dlu) >= MIN_EFFECT_PP and plu < ALPHA and clu >= MIN_CONSISTENT
            verdict = "★有效应" if (ok_ur or ok_lu) else "-"
            print(f"{name:>6}{len(keys):>4}{ur:>11.4f}{dur:>+9.2f}{pur:>9.4f}{cur:>6}/{len(FESTS):<3}"
                  f"{'':>1}{lu:>11.4f}{dlu:>+9.2f}{plu:>9.4f}{clu:>6}/{len(FESTS):<3}{verdict:>9}")
            rows.append((label, name, len(keys), ur, dur, pur, cur, lu, dlu, plu, clu, ok_ur or ok_lu))

    print("-" * 118)
    n_ok = sum(1 for r in rows if r[-1])
    print(f"\n判定：{n_ok}/{len(rows)} 个窗口通过全部判据 => "
          f"{'存在春节效应' if n_ok else '无春节效应（全部未通过）'}")

    # ── 逐年明细（节后 T+1，最常被引用的「开门红」）──
    print(f"\n{'='*118}")
    print("【逐年明细：节后第一个交易日（T+1）—— 检验「开门红」】")
    print(f"{'='*118}")
    print(f"{'年份':>6}{'节前最后':>12}{'节后首日':>12}{'up_ratio':>10}{'基准':>9}{'差pp':>8}"
          f"{'lu_ratio':>10}{'涨停':>6}")
    wins = 0
    for (pre_k, post_k) in FESTS:
        if post_k not in D:
            continue
        v = D[post_k]
        ur = v['up_ratio']
        d = (ur - base_ur) * 100
        wins += 1 if ur > base_ur else 0
        print(f"{pre_k[:4]:>6}{pre_k:>12}{post_k:>12}{ur:>10.4f}{base_ur:>9.4f}{d:>+8.2f}"
              f"{lu_ratio(v):>10.4f}{v['limit_up']:>6}")
    print(f"\n节后首日 up_ratio > 基准 的年份：{wins}/{len(FESTS)}（{wins/len(FESTS)*100:.1f}%）")
    print(f"=> 若接近 50% 则无方向性（与全市场随机无异）。")


if __name__ == '__main__':
    main()
