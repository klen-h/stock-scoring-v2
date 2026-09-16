#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】验证「防御市里质量高 = 抗跌」这一权重信仰（2026-09-17）
================================================================================

背景：`get_regime_weights` 的 defensive 档把 quality 拉到 0.35、growth 0.13
  （合计 48%），依据是一句断言：「下跌市里财务健康、低负债的公司抗跌性最强」。
  但该断言**没有任何分层回测背书**，且有三条反向证据：
    · 近期横截面 IC：growth -0.170 / quality -0.079（双负，08-31~09-09）；
    · 000567 反例：质量 91.3 分，在 defensive 市况 5 日跌 10.6%；
    · market_regime_history 只有 20 行、defensive 仅 2 天，从未被验证过。
→ 本脚本直接回答：**在 defensive 市况下，质量分高的股票未来收益真的更好吗？**

方法：
  1. 市况序列：sh000300 全历史 → `detect_market_regime`（纯价格函数、无前视、
     hysteresis 只用过去窗口）→ 得到远超 market_regime_history 的历史样本。
  2. 截面日：每 `--step` 个交易日一个，尾部留 max_hold 根算未来收益。
  3. 质量分：`ScoreEngine._score_quality`（与生产同源）；财报按**公告日**过滤
     （notice_date ≤ 截面日）→ 防未来函数。
  4. 分组：按截面日市况分组，组内按质量分五分位 → 未来 5/10 日收益 + 胜率。
  5. 判据（预注册）：defensive 组内 **Q5(高质量) − Q1(低质量)** 的收益差
       · 显著为正（t > 2）→ 「质量抗跌」成立，0.35 权重有据；
       · ≈0 或为负 → 证伪，weight=0.35 无依据（应回到回测再定）。
     另做**前后半段分段**稳健性：两段同向才算稳（防单一时段驱动）。

⚠️ 已知口径局限（判读结论时必须带上）：
  · 行业分位 `_IND_DIST` 用**当前** stock_finance 分布 → 历史截面存在轻微前视
    （行业分布变化慢，影响有限）；`--no-industry` 可只用绝对曲线做稳健性对照。
  · 质量分只依赖财报（季度频率），同一报告期内恒定 —— 这正是"跌 16.8% 总分不动"
    的结构性来源，本脚本只验证它的**方向有效性**，不解决该钝感问题。

用法：
  python scripts/quality_defense_backtest.py
  python scripts/quality_defense_backtest.py --step 3 --hold 5 10
  python scripts/quality_defense_backtest.py --no-industry
================================================================================
"""
import argparse
import math
import os
import sys
from collections import defaultdict
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

from app import research_cache                       # noqa: E402
from app.backtest.market_regime import detect_market_regime   # noqa: E402
from app.database import db                          # noqa: E402
from app.scoring.engine import ScoreEngine           # noqa: E402


def load_index_bars():
    rows = db.fetch("SELECT date, open, high, low, close, volume FROM backtest_prices "
                    "WHERE code = 'sh000300' ORDER BY date ASC") or []
    return [{"date": str(r["date"]), "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"]}
            for r in rows]


def load_finance():
    """{code: [(notice_date, roe, debt, gross, revenue_yoy, profit_yoy), ...]} 升序。"""
    rows = db.fetch("SELECT code, notice_date, roe, debt_ratio, gross_margin, "
                    "revenue_yoy, profit_yoy "
                    "FROM stock_finance WHERE notice_date IS NOT NULL "
                    "ORDER BY code, notice_date ASC") or []
    out = defaultdict(list)
    for r in rows:
        out[r["code"]].append((str(r["notice_date"])[:10], r["roe"],
                               r["debt_ratio"], r["gross_margin"],
                               r["revenue_yoy"], r["profit_yoy"]))
    return out


def finance_asof(fin_list, d):
    """截至 d 已公告的最新一期（notice_date ≤ d）——防未来函数。"""
    hit = None
    for rec in fin_list:
        if rec[0] <= d:
            hit = rec
        else:
            break
    return hit


def tstat(a, b):
    """Welch t（a vs b）。样本不足返回 None。"""
    na, nb = len(a), len(b)
    if na < 30 or nb < 30:
        return None
    ma, mb = sum(a) / na, sum(b) / nb
    va = sum((x - ma) ** 2 for x in a) / (na - 1)
    vb = sum((x - mb) ** 2 for x in b) / (nb - 1)
    se = math.sqrt(va / na + vb / nb)
    return (ma - mb) / se if se > 0 else None


def spearman(xs, ys):
    """带 tie 处理的 Spearman（口径同 scripts/rate_hike_weight_backtest._spearman）。"""
    n = len(xs)
    if n < 10:
        return None

    def _ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = _ranks(xs), _ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    vx = sum((a - mx) ** 2 for a in rx) ** 0.5
    vy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (vx * vy) if vx > 0 and vy > 0 else None


def rank_norm(vals):
    """0~1 分位归一化（口径同 rate_hike_weight_backtest._rank_norm）。"""
    n = len(vals)
    order = sorted(range(n), key=lambda i: vals[i])
    r = [0.0] * n
    for pos, i in enumerate(order):
        r[i] = pos / (n - 1) if n > 1 else 0.5
    return r


def evaluate(factor, rets):
    """IC + top30/bottom30 分离 + 胜率（口径同 rate_hike_weight_backtest._evaluate）。"""
    k = len(factor)
    ic = spearman(factor, rets)
    order = sorted(range(k), key=lambda i: factor[i])
    m = max(1, k * 3 // 10)
    lo_i, hi_i = order[:m], order[k - m:]
    lo = sum(rets[i] for i in lo_i) / m
    hi = sum(rets[i] for i in hi_i) / m
    lo_wr = sum(1 for i in lo_i if rets[i] > 0) / m * 100
    hi_wr = sum(1 for i in hi_i if rets[i] > 0) / m * 100
    return {"ic": ic, "lo": lo, "hi": hi, "spread": hi - lo,
            "lo_wr": lo_wr, "hi_wr": hi_wr}


def _optimize(grp, hold, steps=20, label=""):
    """网格寻优：组合分 = w·rank_norm(成长) + (1−w)·rank_norm(质量)。

    ★ 两个口径同时给出，避免单一口径误导：
      · **分日口径（主）**：每个截面日内先 rank 归一化（不同日才可比），
        逐日算 IC / 分离度再取均值 —— 不混入"日间水平差异"；
      · 混合口径（对照）：与脚本/项目既有口径一致，便于横向对齐。
    ★ 判读要点：看**曲线形状**而非峰值 —— 6 个日块下峰值极可能是过拟合；
      单调上升 = 成长权重越高越稳；尖峰 = 不可用。
    """
    grp = [x for x in grp
           if x.get("q") is not None and x.get("g") is not None
           and x.get(f"f{hold}") is not None]
    if len(grp) < 200:
        print(f"\n[{label}] 样本不足（{len(grp)}），跳过寻优")
        return
    by_date = defaultdict(list)
    for x in grp:
        by_date[x["date"]].append(x)
    dates = sorted(by_date)
    for d in dates:                       # 截面内 rank（关键）
        g = by_date[d]
        rq = rank_norm([x["q"] for x in g])
        rg = rank_norm([x["g"] for x in g])
        for i, x in enumerate(g):
            x["rq"], x["rg"] = rq[i], rg[i]

    print(f"\n[{label}] 寻优（持有 {hold} 日）  n={len(grp)} / {len(dates)} 个截面日")
    print(f"  {'w(成长)':>8s}{'分日IC':>9s}{'分日分离':>10s}{'胜率差':>9s}"
          f"{'混合IC':>9s}{'混合分离':>10s}")
    print("  " + "-" * 56)
    rows = []
    for k in range(steps + 1):
        wg = k / steps
        ics, sps, wrs, af, ar = [], [], [], [], []
        for d in dates:
            g = by_date[d]
            f = [wg * x["rg"] + (1 - wg) * x["rq"] for x in g]
            r = [x[f"f{hold}"] for x in g]
            ic = spearman(f, r)
            if ic is not None:
                ics.append(ic)
            ev = evaluate(f, r)
            sps.append(ev["spread"])
            wrs.append(ev["hi_wr"] - ev["lo_wr"])
            af += f
            ar += r
        aic = sum(ics) / len(ics) if ics else None
        asp = sum(sps) / len(sps)
        awr = sum(wrs) / len(wrs)
        mev = evaluate(af, ar)
        rows.append((wg, aic, asp, awr, mev["ic"], mev["spread"]))
        print(f"  {wg:>8.2f}{fmt(aic, 3):>9s}{asp:>+10.2f}{awr:>+9.1f}"
              f"{fmt(mev['ic'], 3):>9s}{mev['spread']:>+10.2f}")

    ok = [z for z in rows if z[1] is not None]
    if ok:
        b_ic = max(ok, key=lambda z: z[1])
        b_sp = max(rows, key=lambda z: z[2])
        print(f"  → 分日IC 峰值:  w={b_ic[0]:.2f}（IC {fmt(b_ic[1], 3)}）")
        print(f"  → 分日分离峰值: w={b_sp[0]:.2f}（分离 {b_sp[2]:+.2f}pt）")
    cur = 0.13 / (0.13 + 0.35)
    print(f"  现役 defensive 在两维内部折算 w(成长) = {cur:.3f}"
          f"（growth 0.13 / quality 0.35）")

    # 分段稳健性：关键 w 值在前/后半段的分日 IC
    if len(dates) >= 4:
        mid = len(dates) // 2
        print(f"  分段（分日 IC，前 {mid} 日 / 后 {len(dates) - mid} 日）：")
        for wg in (0.0, round(cur, 2), 0.5, 0.75, 1.0):
            seg = []
            for part in (dates[:mid], dates[mid:]):
                ics = []
                for d in part:
                    g = by_date[d]
                    f = [wg * x["rg"] + (1 - wg) * x["rq"] for x in g]
                    r = [x[f"f{hold}"] for x in g]
                    ic = spearman(f, r)
                    if ic is not None:
                        ics.append(ic)
                seg.append(sum(ics) / len(ics) if ics else None)
            print(f"    w={wg:<5.2f} 前半 {fmt(seg[0], 3)} / 后半 {fmt(seg[1], 3)}")


AGE_BINS = [(0, 5), (6, 20), (21, 60), (61, 120), (121, 10 ** 9)]
AGE_LABELS = ["0-5天", "6-20天", "21-60天", "61-120天", "120天+"]


def _event_study(grp, hold, label=""):
    """按「距财报公告日天数」分桶，验证「拉升出货」假设。

    ★ 若「财报利好 → 拉升 → 兑现 → 出货」成立，则刚公告（0-5 天）桶里
      成长/质量因子的 IC 应显著为负（利好已兑现、正要出货），陈旧桶转正。
    ★ 口径：按（截面日 × 年龄桶）分组 → 桶内截面 IC → 跨日平均
      （与 _optimize 同源，避免日间水平差异混入）。
    """
    grp = [x for x in grp if x.get("age") is not None
           and x.get(f"f{hold}") is not None]
    if len(grp) < 500:
        print(f"\n[{label}] n={len(grp)} —— 样本不足，跳过事件研究")
        return
    by_date = defaultdict(list)
    for x in grp:
        by_date[x["date"]].append(x)
    print(f"\n[{label}] 财报公告事件研究（持有 {hold} 日）"
          f"  n={len(grp)} / {len(by_date)} 个截面日")
    print(f"  {'年龄桶':<10}{'n':>7}{'截面日':>6}"
          f"{'质量IC':>9}{'成长IC':>9}{'质量分离':>10}{'成长分离':>10}")
    print("  " + "-" * 62)
    for bi, (lo, hi) in enumerate(AGE_BINS):
        q_ics, g_ics, q_sps, g_sps, n = [], [], [], [], 0
        days_used = set()
        for d in by_date:
            day = [x for x in by_date[d] if lo <= x["age"] <= hi]
            if not day:
                continue
            days_used.add(d)
            n += len(day)
            for dim, acc_ic, acc_sp in (("q", q_ics, q_sps),
                                        ("g", g_ics, g_sps)):
                g_ok = [x for x in day if x[dim] is not None]
                if len(g_ok) < 10:
                    continue
                vals = [x[dim] for x in g_ok]
                rets = [x[f"f{hold}"] for x in g_ok]
                ic = spearman(vals, rets)
                if ic is not None:
                    acc_ic.append(ic)
                acc_sp.append(evaluate(vals, rets)["spread"])
        qic = sum(q_ics) / len(q_ics) if q_ics else None
        gic = sum(g_ics) / len(g_ics) if g_ics else None
        qsp = sum(q_sps) / len(q_sps) if q_sps else None
        gsp = sum(g_sps) / len(g_sps) if g_sps else None
        print(f"  {AGE_LABELS[bi]:<10}{n:>7}{len(days_used):>6}"
              f"{fmt(qic, 3):>9}{fmt(gic, 3):>9}"
              f"{fmt(qsp, 2):>10}{fmt(gsp, 2):>10}")


def _daily_ic(by_date, dates, dim, hold, T, a):
    """给定公告后阈值 T、衰减系数 α，算该因子的分日平均 IC。

    age ≤ T 的样本因子值 ×α、age > T 的 ×1.0 —— 模拟"公告后新鲜度衰减"，
    压低刚公告（出货窗口）样本的因子贡献，看截面 IC 是否改善。
    """
    ics = []
    for d in dates:
        day = by_date[d]
        vals, rets = [], []
        for x in day:
            if x[dim] is None:
                continue
            coef = a if x["age"] <= T else 1.0
            vals.append(x[dim] * coef)
            rets.append(x[f"f{hold}"])
        if len(vals) < 10:
            continue
        ic = spearman(vals, rets)
        if ic is not None:
            ics.append(ic)
    return sum(ics) / len(ics) if ics else None


def _decay_study(grp, hold, label=""):
    """衰减寻优：扫描（公告后阈值 T，衰减系数 α）网格，对比 α=1（不衰减）基线。

    ★ 事件研究已证「公告后 0-20 天因子方向为负（拉升出货）」；本寻优回答：
      把刚公告样本的因子压低（衰减）后，截面 IC 能否从负转正、最优窗口/力度。
    """
    grp = [x for x in grp if x.get("age") is not None
           and x.get(f"f{hold}") is not None]
    if len(grp) < 500:
        print(f"\n[{label}] n={len(grp)} —— 样本不足，跳过衰减寻优")
        return
    by_date = defaultdict(list)
    for x in grp:
        by_date[x["date"]].append(x)
    dates = sorted(by_date)

    T_CAND = [5, 10, 20, 30]
    A_CAND = [0.0, 0.3, 0.5, 0.8]

    for dim, dname in (("q", "质量"), ("g", "成长")):
        base = _daily_ic(by_date, dates, dim, hold, 10 ** 9, 1.0)
        print(f"\n[{label}] 衰减寻优 · {dname}（持有 {hold} 日）"
              f"  基线(不衰减) IC = {fmt(base, 3)}")
        print(f"  {'阈值T':>6}" + "".join(f"{'α=' + str(a):>10}" for a in A_CAND))
        print("  " + "-" * 46)
        best = None
        for T in T_CAND:
            cells = []
            for a in A_CAND:
                ic = _daily_ic(by_date, dates, dim, hold, T, a)
                cells.append(ic)
                if ic is not None and (best is None or ic > best[1]):
                    best = (T, a, ic)
            print(f"  {T:>6}" + "".join(f"{fmt(v, 3):>10}" for v in cells))
        if best:
            d = (best[2] - base) if base is not None else None
            print(f"  → 最优 T={best[0]} 天, α={best[1]}：IC {fmt(best[2], 3)}"
                  f"（Δ {fmt(d, 3)} vs 基线）")


def fmt(v, p=2):
    return f"{v:+.{p}f}" if isinstance(v, (int, float)) else "n/a"


def quantile_report(tag, grp, hold, dim="q"):
    """组内因子五分位 → 未来收益；dim: 'q'=质量 / 'g'=成长。返回 (hi, lo, t)。"""
    grp = [x for x in grp if x.get(f"f{hold}") is not None
           and x.get(dim) is not None]
    if len(grp) < 150:
        print(f"\n[{tag}] n={len(grp)} —— 样本不足，跳过")
        return None
    vals = sorted(x[dim] for x in grp)
    cuts = [vals[int(len(vals) * k / 5)] for k in range(1, 5)]
    buckets = defaultdict(list)
    for x in grp:
        b = 0
        for k, cut in enumerate(cuts):
            if x[dim] >= cut:
                b = k + 1
        buckets[b].append(x)
    days = len({x["date"] for x in grp})
    print(f"\n[{tag}] n={len(grp)}（{days} 个截面日）  持有 {hold} 日")
    print(f"  {'分位':<8}{'分区间':<18}{'n':>7}{'均收益%':>10}{'胜率%':>8}")
    print("  " + "-" * 52)
    lo_b = buckets[min(buckets)]
    hi_b = buckets[max(buckets)]
    for b in sorted(buckets):
        g = buckets[b]
        rets = [x[f"f{hold}"] for x in g]
        lo = "-inf" if b == 0 else f"{cuts[b-1]:.1f}"
        hi = "+inf" if b == 4 else f"{cuts[b]:.1f}"
        print(f"  Q{b+1:<7}[{lo}, {hi})".ljust(26)
              + f"{len(g):>7}{sum(rets)/len(rets):>+10.2f}"
              + f"{sum(1 for r in rets if r > 0)/len(rets)*100:>8.1f}")
    lo_r = [x[f"f{hold}"] for x in lo_b]
    hi_r = [x[f"f{hold}"] for x in hi_b]
    t = tstat(hi_r, lo_r)
    print(f"  → Q5(高) − Q1(低) = {fmt(sum(hi_r)/len(hi_r) - sum(lo_r)/len(lo_r))}pt"
          f"   t = {fmt(t, 2)}")
    return (sum(hi_r) / len(hi_r), sum(lo_r) / len(lo_r), t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=5, help="截面间隔（交易日）")
    ap.add_argument("--hold", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--no-industry", action="store_true",
                    help="禁用行业分位（只用绝对曲线）做稳健性对照")
    ap.add_argument("--optimize", action="store_true",
                    help="跑 growth/quality 权重网格寻优（分市况，仅这两维）")
    ap.add_argument("--event", action="store_true",
                    help="财报公告事件研究：按距公告日天数分桶看成长/质量 IC")
    ap.add_argument("--decay", action="store_true",
                    help="衰减寻优：扫描公告后阈值×衰减系数，对比不衰减基线")
    args = ap.parse_args()

    idx = load_index_bars()
    if len(idx) < 200:
        print(f"sh000300 数据不足（{len(idx)} 根）")
        return 1
    states = detect_market_regime(idx)
    state_by_date = {s.date: s.state for s in states}
    dist = defaultdict(int)
    for v in state_by_date.values():
        dist[v] += 1
    print("=" * 78)
    print("「防御市里质量高=抗跌」验证")
    print("=" * 78)
    print(f"[regime] sh000300 {idx[0]['date']} ~ {idx[-1]['date']}"
          f"（{len(idx)} 根），判定 {len(states)} 日")
    print(f"[regime] 分布 {dict(dist)}")

    prices = research_cache.ohlc_all()
    fin = load_finance()
    print(f"[data] 日线 {len(prices)} 只 | 财报 {len(fin)} 只")

    eng = ScoreEngine()
    if args.no_industry:
        from app.scoring import engine as eng_mod
        eng_mod._IND_DIST.update({"dist": {}, "code_ind": {}, "code_sub": {},
                                  "ts": 9e9})
        print("[mode] 已禁用行业分位（只用绝对曲线）")

    max_hold = max(args.hold)
    all_dates = [b["date"] for b in idx]
    sec_dates = all_dates[70:-max_hold:args.step] if len(all_dates) > 70 + max_hold else []
    if not sec_dates:
        print("截面日为空")
        return 1
    print(f"[sections] {len(sec_dates)} 个截面日：{sec_dates[0]} ~ {sec_dates[-1]}")

    samples = []
    for ci, (code, bars) in enumerate(sorted(prices.items()), 1):
        if code == "sh000300":
            continue
        d_idx = {b["date"]: i for i, b in enumerate(bars)}
        fl = fin.get(code)
        if not fl:
            continue
        for d in sec_dates:
            i = d_idx.get(d)
            if i is None or i + max_hold >= len(bars):
                continue
            rec = finance_asof(fl, d)
            if not rec:
                continue
            nd, roe, debt, gross, rev, prof = rec
            age = (date.fromisoformat(d) - date.fromisoformat(nd)).days
            dq = eng._score_quality(
                {"quality": {"roe": roe, "debt_ratio": debt,
                             "gross_margin": gross}}, code)
            dg = eng._score_growth(
                {"growth": {"revenue_yoy": rev, "profit_yoy": prof}})
            q = dq.score if (dq is not None and dq.score is not None) else None
            g = dg.score if (dg is not None and dg.score is not None) else None
            if q is None and g is None:
                continue
            s = {"code": code, "date": d, "state": state_by_date.get(d, "?"),
                 "q": q, "g": g, "age": age}
            for h in args.hold:
                s[f"f{h}"] = (bars[i + h]["close"] / bars[i]["close"] - 1) * 100
            samples.append(s)
        if ci % 200 == 0:
            print(f"[calc] {ci}/{len(prices)} 样本 {len(samples)}")
    print(f"[samples] 共 {len(samples)} 条")

    by_state = defaultdict(list)
    for s in samples:
        by_state[s["state"]].append(s)

    if args.event:
        _event_study(samples, args.hold[0], label="全样本")
        for st in ("defensive", "neutral", "offensive"):
            g = by_state.get(st) or []
            if not g:
                continue
            _event_study(g, args.hold[0], label=st)
        return 0

    if args.decay:
        _decay_study(samples, args.hold[0], label="全样本")
        for st in ("defensive", "neutral", "offensive"):
            g = by_state.get(st) or []
            if not g:
                continue
            _decay_study(g, args.hold[0], label=st)
        return 0

    if args.optimize:
        for st in ("defensive", "neutral", "offensive"):
            g = by_state.get(st) or []
            if not g:
                continue
            for h in args.hold:
                _optimize(g, h, label=st)
        return 0

    for st in ("defensive", "neutral", "offensive"):
        grp = by_state.get(st) or []
        if not grp:
            continue
        for dim, dname in (("q", "质量"), ("g", "成长")):
            for h in args.hold:
                quantile_report(f"{st}/{dname}", grp, h, dim)

    # ── 结论判读（预注册判据）──
    print("\n" + "=" * 78)
    print("结论判读（预注册判据）")
    print("=" * 78)
    dgrp = by_state.get("defensive") or []
    h0 = args.hold[0]
    for dim, dname in (("q", "质量"), ("g", "成长")):
        print(f"\n--- {dname}（defensive 主检验，持有 {h0} 日）---")
        r = (quantile_report(f"defensive/主检验·{dname}", dgrp, h0, dim)
             if dgrp else None)
        if r is None:
            print("  样本不足，无法判定 → 暂不可检验（勿据此改权重）")
            continue
        hi, lo, t = r
        diff = hi - lo
        if t is not None and t > 2 and diff > 0:
            print(f"  → 「{dname}高更好」**成立**（Q5−Q1 {fmt(diff)}pt，t={t:.2f}）")
        elif t is not None and t < -2:
            print(f"  → **反向显著**（Q5−Q1 {fmt(diff)}pt，t={t:.2f}）："
                  f"高分反而更差，权重方向错了")
        else:
            print(f"  → **不显著**（Q5−Q1 {fmt(diff)}pt，t={fmt(t, 2)}）："
                  f"拿不到统计支持 → 属信仰")

    # ── 分段稳健性（防单一时段驱动）──
    if dgrp:
        ds = sorted({x["date"] for x in dgrp})
        if len(ds) >= 4:
            mid = ds[len(ds) // 2]
            for tag, part in (("前半段", [x for x in dgrp if x["date"] < mid]),
                              ("后半段", [x for x in dgrp if x["date"] >= mid])):
                if len({x["date"] for x in part}) < 2:
                    print(f"  [{tag}] 截面日不足")
                    continue
                for dim in ("q", "g"):
                    quantile_report(f"defensive/{tag}", part, h0, dim)
    return 0


if __name__ == "__main__":
    sys.exit(main())
