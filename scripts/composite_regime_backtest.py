#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】组合分排序的市况分层回测（回答"defensive 市况要不要开组合分排序"）
================================================================================

背景（2026-09-16）：组合分排序（基本面+资金面−技术面，秩归一化）2026-09-15 上线，
  闸门只在 neutral_bearish 生效（routers/scoring.py 的 `_use_composite_rank`）。
  市况切到 defensive 后排行榜自动回落总分 → 页面组合分消失（设计如此，非 bug）。
  是否把 defensive 也纳入闸门？属**同日切换排序键**的决策，必须先回测背书，
  不能从 nb 外推（nb 结论样本仅 ~250 条 / ~5 个快照日，本就薄）。

本脚本回答：**defensive 市况下，组合分排序相比总分排序是否仍有正向分离？**

方法（与 09-15 nb 结论同源，保证可比）：
  样本 = ranking_history 已验证快照（固定 T+H 交易日收盘价收益）
  市况 = market_regime_history.state（**细化态**，含 neutral_bearish）
  因子：
    total_score      入榜总分（现行排序键）
    composite        三因子秩归一化：(rank(基本面)+rank(资金面)−rank(技术面))/3
    权重档            现行nb(=neutral) / defensive 两档原始分加权（对照）
  口径（两套都给，避免"组合用秩比总分用绝对分"的不公平比较）：
    A. 池内口径（**与生产同源**）：每个快照日**日内**秩归一化 → 逐日 IC/分离 → 取均值
       （生产 `_sort_by_composite` 就是当日候选池内秩归一化）
    B. 合并口径（**与 09-15 结论同源**）：跨样本秩归一化 → 一次性算 IC/分离
       （rate_hike_weight_backtest.py 的 组合因子 IC=+0.259 即此口径）
  显著性：按**快照日整块** bootstrap。同日个股高度相关（同涨同跌），
          按个股重采样会严重高估显著性。

市况对齐口径（易错点）：
  调度器每日 15:40 用当日收盘数据判定并落库 state(D)，而当日快照 15:10 落库，
  **盘中排行榜读到的其实是 state(D−1)**。故本脚本默认按 D−1 对齐（因果），
  同时打印 D 同日对齐结果供对照；`--align same|prev|both` 可切换。

用法：
  python scripts/composite_regime_backtest.py                      # horizon=5, 关注 defensive
  python scripts/composite_regime_backtest.py --regime neutral_bearish   # 复现 09-15 结论
  python scripts/composite_regime_backtest.py --align same --bootstrap 2000
  python scripts/composite_regime_backtest.py --out 组合分市况分层回测_20260916.md

前置：需要能连生产库（backend/.env 里 DATABASE_URL，或环境变量）。
================================================================================
"""
import argparse
import os
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

# 注意：app.* 的 import 延后到函数内 —— 缺 DATABASE_URL 时要在**碰库之前**就中止，
# 否则 `app.scoring.ranking_history` 的模块级 init 会往本地 SQLite 建表（脏写）。

_BEIJING_TZ = timezone(timedelta(hours=8))
_DAYS = ("技术面", "资金面", "基本面", "成长", "质量")
_COMPOSITE_DIMS = ("基本面", "资金面", "技术面")

# 权重候选（和 = 1.0），与 rate_hike_weight_backtest.py / market_regime.get_regime_weights 同源
WEIGHTS = {
    "现行nb(=neutral)": {"技术面": 0.27, "资金面": 0.23, "基本面": 0.23,
                         "成长": 0.17, "质量": 0.11},
    "defensive 档":     {"技术面": 0.12, "资金面": 0.15, "基本面": 0.25,
                         "成长": 0.13, "质量": 0.35},
}

# 结论门槛（预注册，避免看到数字后调整叙事）：
_MIN_DAYS_HARD = 3     # 少于 3 个快照日 → 直接判定"样本不足，不得拍板"
_MIN_DAYS_SOFT = 6     # 达到 6 个快照日以上，统计才具备"可落地"的参考力
_MIN_N_DAY = 10        # 单日少于 10 条样本 → 该日不参与逐日 IC 统计


# ── 统计工具 ────────────────────────────────────────────────────────────────
def _spearman(xs, ys):
    n = len(xs)
    if n < _MIN_N_DAY:
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


def _rank_norm(vals):
    """池内秩归一化到 [0,1]（与生产 _sort_by_composite 同式）。"""
    n = len(vals)
    if n < 2:
        return [0.5] * n
    order = sorted(range(n), key=lambda i: vals[i])
    r = [0.0] * n
    for pos, i in enumerate(order):
        r[i] = pos / (n - 1)
    return r


def _buckets(factor, rets, frac=0.3):
    """按 factor 切 低frac / 高frac 两桶，返回 (低均收, 高均收, 低胜率, 高胜率, 分离)。"""
    k = len(factor)
    if k < _MIN_N_DAY:
        return None
    order = sorted(range(k), key=lambda i: factor[i])
    m = max(1, int(k * frac))
    lo_i, hi_i = order[:m], order[k - m:]
    lo_r = sum(rets[i] for i in lo_i) / m
    hi_r = sum(rets[i] for i in hi_i) / m
    lo_wr = sum(1 for i in lo_i if rets[i] > 0) / m * 100
    hi_wr = sum(1 for i in hi_i if rets[i] > 0) / m * 100
    return lo_r, hi_r, lo_wr, hi_wr, hi_r - lo_r


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _fmt(v, spec="+.3f", dash="  —  "):
    return dash if v is None else format(v, spec)


# ── 市况对齐 ────────────────────────────────────────────────────────────────
def _load_regime_map():
    """date → state（细化态）。表不存在/为空时返回空 dict。"""
    from app.database import db
    try:
        rows = db.fetch("SELECT date, state FROM market_regime_history ORDER BY date")
    except Exception as e:
        print(f"[warn] 读取 market_regime_history 失败: {e}")
        return {}
    return {str(r["date"])[:10]: r["state"] for r in (rows or []) if r.get("state")}


def _prev_state_map(regime_map):
    """date → 上一个已落库市况日的 state（排行榜盘中实际读到的口径）。"""
    dates = sorted(regime_map)
    out = {}
    for i, d in enumerate(dates):
        out[d] = regime_map[dates[i - 1]] if i > 0 else None
    return out


def _align_state(date, regime_map, prev_map, align):
    """分层用的市况标签：same=同日落库态；prev=上一落库态（因果口径）。"""
    if align == "same":
        return regime_map.get(date)
    return prev_map.get(date)


# ── 因子构造 ────────────────────────────────────────────────────────────────
def _composite_pooled(recs):
    """跨样本秩归一化（口径 B）。"""
    n = len(recs)
    ranks = {}
    for c in _COMPOSITE_DIMS:
        ranks[c] = _rank_norm([float(r["dimensions"][c]) for r in recs])
    return [(ranks["基本面"][i] + ranks["资金面"][i] - ranks["技术面"][i]) / 3.0
            for i in range(n)]


def _weight_score(recs, w):
    return [sum(w[k] * float(r["dimensions"][k]) for k in _DAYS) for r in recs]


def _composite_by_day(day_idx, recs):
    """口径 A：日内秩归一化，返回与 recs 等长的因子向量（跨日不可比，仅用于逐日统计）。"""
    out = [None] * len(recs)
    for _d, idxs in day_idx.items():
        for c in _COMPOSITE_DIMS:
            rn = _rank_norm([float(recs[i]["dimensions"][c]) for i in idxs])
            for pos, i in enumerate(idxs):
                out[i] = out[i] or {}
                out[i][c] = rn[pos]
    return [((o["基本面"] + o["资金面"] - o["技术面"]) / 3.0) if o else None
            for o in out]


def _daily_metrics(day_idx, values, rets):
    """逐日 IC / 分离 → 取均值。values 可为 None（缺值日跳过）。"""
    ics, spreads = [], []
    for _d, idxs in day_idx.items():
        v = [values[i] for i in idxs]
        rr = [rets[i] for i in idxs]
        if any(x is None for x in v):
            continue
        ic = _spearman(v, rr)
        if ic is not None:
            ics.append(ic)
        b = _buckets(v, rr)
        if b:
            spreads.append(b[4])
    return _mean(ics), _mean(spreads), len(ics)


def _pooled_metrics(values, rets):
    ic = _spearman(values, rets)
    b = _buckets(values, rets)
    return ic, b


# ── bootstrap（按快照日整块重采样）────────────────────────────────────────
def _block_bootstrap(day_idx, recs, n_boot, seed):
    """返回 (comp−total) 分离差的 bootstrap 分布（合并口径）。"""
    days = list(day_idx)
    if len(days) < _MIN_DAYS_HARD:
        return None
    rnd = random.Random(seed)
    diffs, comp_sp, tot_sp = [], [], []
    for _ in range(n_boot):
        pick = [days[rnd.randrange(len(days))] for _ in range(len(days))]
        idxs = [i for d in pick for i in day_idx[d]]
        sub = [recs[i] for i in idxs]
        rets = [float(recs[i]["returnPct"]) for i in idxs]
        comp = _composite_pooled(sub)
        tot = [float(r["score"]) for r in sub]
        bc, bt = _buckets(comp, rets), _buckets(tot, rets)
        if not bc or not bt:
            continue
        comp_sp.append(bc[4])
        tot_sp.append(bt[4])
        diffs.append(bc[4] - bt[4])
    if len(diffs) < n_boot * 0.8:
        return None
    diffs.sort()
    lo = diffs[int(len(diffs) * 0.025)]
    hi = diffs[int(len(diffs) * 0.975)]
    return {"mean": _mean(diffs), "lo": lo, "hi": hi,
            "p_gt0": sum(1 for d in diffs if d > 0) / len(diffs) * 100,
            "comp_spread": _mean(comp_sp), "total_spread": _mean(tot_sp),
            "n": len(diffs)}


# ── 样本装载 ────────────────────────────────────────────────────────────────
def load_samples(horizon, align):
    from app.scoring.ranking_history import get_verified_records
    records = get_verified_records(min_age_days=2, horizon_days=horizon)
    recs = []
    for r in records:
        dims = r.get("dimensions") or {}
        # 组合分只需三因子；但权重档对照需五维齐全 → 统一要求五维
        if all(isinstance(dims.get(k), (int, float)) for k in _DAYS):
            recs.append(r)
    regime_map = _load_regime_map()
    prev_map = _prev_state_map(regime_map)
    strata = defaultdict(list)
    unlabeled = []
    for r in recs:
        st = _align_state(str(r["date"])[:10], regime_map, prev_map, align)
        (strata[st] if st else unlabeled).append(r)
    return recs, strata, unlabeled, regime_map, prev_map


def _day_index(recs):
    di = defaultdict(list)
    for i, r in enumerate(recs):
        di[str(r["date"])[:10]].append(i)
    return dict(di)


# ── 报表 ────────────────────────────────────────────────────────────────────
def _stratum_table(name, recs):
    if not recs:
        return None
    rets = [float(r["returnPct"]) for r in recs]
    di = _day_index(recs)
    total = [float(r["score"]) for r in recs]
    comp_pooled = _composite_pooled(recs)
    comp_day = _composite_by_day(di, recs)

    rows = []
    # 单因子 IC（诊断用）
    for c in _DAYS:
        vals = [float(r["dimensions"][c]) for r in recs]
        ic_pool, _ = _pooled_metrics(vals, rets)
        ic_day, _, _ = _daily_metrics(di, vals, rets)
        rows.append((f"单因子·{c}", ic_pool, ic_day, None, None, None, None))
    # 总分 / 组合分 / 权重档
    factors = [
        # (显示名, 合并口径因子, 逐日口径因子)
        ("总分(现行排序键)", total, total),
        ("组合分(秩归一化)", comp_pooled, comp_day),
        ("权重档（原始分加权，与上两行同池对比）", None, None),
    ]
    for wname, w in WEIGHTS.items():
        vals = _weight_score(recs, w)
        factors.append((f"权重档·{wname}", vals, vals))

    out_rows = []
    for fname, pooled_vals, day_vals in factors:
        if pooled_vals is None and day_vals is None:
            out_rows.append((fname, None, None, None, None, None, None))
            continue
        ic_pool = _pooled_metrics(pooled_vals, rets)[0] if pooled_vals is not None else None
        ic_day, sp_day, ndays = _daily_metrics(di, day_vals, rets) if day_vals is not None \
            else (None, None, 0)
        b = _buckets(pooled_vals, rets) if pooled_vals is not None else None
        out_rows.append((fname, ic_pool, ic_day,
                         b[0] if b else None, b[1] if b else None,
                         b[4] if b else None, sp_day))
    return {"name": name, "n": len(recs), "days": len(di), "rows": out_rows + rows}


def _stratum_lines(tbl):
    """渲染单个市况分层表（返回行列表，由 emit 统一打印/落盘）。"""
    if not tbl:
        return []
    out = [f"\n── 市况「{tbl['name']}」  样本 {tbl['n']} 条 / {tbl['days']} 个快照日 ──",
           f"  {'因子':<20s}{'合并IC':>9s}{'日均IC':>9s}{'低30%收':>10s}"
           f"{'高30%收':>10s}{'合并分离':>10s}{'日均分离':>10s}",
           "  " + "-" * 78]
    for name, ic_pool, ic_day, lo, hi, spread, sp_day in tbl["rows"]:
        if ic_pool is None and ic_day is None and lo is None:
            out.append(f"  ── {name} ──")
            continue
        if lo is None:      # 单因子：只报 IC，不切桶
            out.append(f"  {name:<20s}{_fmt(ic_pool):>9s}{_fmt(ic_day):>9s}"
                       f"{'—':>10s}{'—':>10s}{'—':>10s}{'—':>10s}")
            continue
        out.append(f"  {name:<20s}{_fmt(ic_pool):>9s}{_fmt(ic_day):>9s}"
                   f"{format(lo, '+.2f'):>9s}%{format(hi, '+.2f'):>9s}%"
                   f"{format(spread, '+.2f'):>9s}"
                   f"{_fmt(sp_day, '+.2f', '   —  '):>10s}")
    return out


def _detail_lines(name, recs, regime_map, prev_map):
    """逐日明细：看样本日构成（结论到底压在几天上）。"""
    di = _day_index(recs)
    rets = [float(r["returnPct"]) for r in recs]
    total = [float(r["score"]) for r in recs]
    comp_day = _composite_by_day(di, recs)
    out = [f"\n── 逐日明细（{name}）──",
           f"  {'日期':<12s}{'同日态':<16s}{'前日态':<16s}{'条数':>5s}"
           f"{'总分IC':>9s}{'组合IC':>9s}{'总分分离':>10s}{'组合分离':>10s}",
           "  " + "-" * 84]
    for d in sorted(di):
        idxs = di[d]
        t_ic = _spearman([total[i] for i in idxs], [rets[i] for i in idxs])
        c_ic = _spearman([comp_day[i] for i in idxs], [rets[i] for i in idxs])
        bt = _buckets([total[i] for i in idxs], [rets[i] for i in idxs])
        bc = _buckets([comp_day[i] for i in idxs], [rets[i] for i in idxs])
        out.append(f"  {d:<12s}{str(regime_map.get(d)):<16s}{str(prev_map.get(d)):<16s}"
                   f"{len(idxs):>5d}{_fmt(t_ic):>9s}{_fmt(c_ic):>9s}"
                   f"{_fmt(bt[4] if bt else None, '+.2f', '   —  '):>10s}"
                   f"{_fmt(bc[4] if bc else None, '+.2f', '   —  '):>10s}")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=5, help="持有 T+N 交易日（默认 5）")
    ap.add_argument("--regime", default="defensive", help="目标市况（默认 defensive）")
    ap.add_argument("--align", choices=("same", "prev", "both"), default="prev",
                    help="市况对齐：prev=前一日落库态（因果，默认）；same=同日；both=两种都算")
    ap.add_argument("--bootstrap", type=int, default=1000, help="bootstrap 次数（0=关闭）")
    ap.add_argument("--seed", type=int, default=20260916)
    ap.add_argument("--out", default=None, help="同时把报告写入该 markdown 路径")
    ap.add_argument("--selftest", action="store_true",
                    help="不连库：用合成样本自检管线（组合分有效/总分无效的构造数据）")
    args = ap.parse_args()

    if args.selftest:
        recs, strata, unlabeled, regime_map, prev_map = _selftest_samples()
        _report(args, recs, strata, unlabeled, regime_map, prev_map, synthetic=True)
        return 0

    if not os.environ.get("DATABASE_URL"):
        print("[中止] 未检测到 DATABASE_URL —— 本脚本需连生产库读 ranking_history/"
              "market_regime_history。\n"
              "  方式一：在 backend/.env 写入 DATABASE_URL=postgresql://...（app/__init__ "
              "会自动加载，已被 .gitignore 忽略）\n"
              "  方式二：命令行先设置环境变量再运行。")
        return 2

    recs, strata, unlabeled, regime_map, prev_map = load_samples(args.horizon, args.align)
    return _report(args, recs, strata, unlabeled, regime_map, prev_map)


def _selftest_samples():
    """合成样本自检：构造成交收益只由「基本面+资金面−技术面」驱动、与总分无关，
    用于验证管线（秩归一化 → IC → 分桶分离 → bootstrap）方向正确。"""
    rnd = random.Random(20260916)
    recs = []
    for d in range(8):
        date = f"2026-08-{10 + d:02d}"
        for i in range(40):
            f, c, t = (rnd.uniform(0, 100) for _ in range(3))
            ret = (0.40 * (f / 100) + 0.35 * (c / 100) - 0.30 * (t / 100)
                   + rnd.gauss(0, 0.02))
            recs.append({
                "date": date, "code": f"{i:06d}", "name": f"SYN{i:03d}",
                "score": rnd.uniform(45, 80),      # 与收益无关 → 总分应无 IC
                "dimensions": {"基本面": f, "资金面": c, "技术面": t,
                               "成长": rnd.uniform(0, 100), "质量": rnd.uniform(0, 100)},
                "returnPct": round(ret * 100, 2),
            })
    dates = [r["date"] for r in recs]
    regime_map = {d: "defensive" for d in sorted(set(dates))}
    prev_map = {d: "defensive" for d in sorted(set(dates))}
    return recs, {"defensive": recs}, [], regime_map, prev_map


def _report(args, recs, strata, unlabeled, regime_map, prev_map, synthetic=False):
    lines = []

    def emit(s=""):
        print(s)
        lines.append(s)

    hdr = (f"组合分排序 · 市况分层回测（持有 {args.horizon} 交易日，"
           f"市况对齐={args.align}）")
    emit("=" * 84)
    emit(hdr)
    emit(f"生成时间 {datetime.now(_BEIJING_TZ):%Y-%m-%d %H:%M} (北京时间)")
    if synthetic:
        emit("★ 合成样本自检模式（--selftest）：非真实数据，仅验证管线方向")
    emit("=" * 84)

    emit(f"\n库内已落库市况日 {len(regime_map)} 天；已验证快照（五维齐全）{len(recs)} 条")
    if recs:
        ds = sorted({str(r['date'])[:10] for r in recs})
        emit(f"快照日期范围 {ds[0]} ~ {ds[-1]}（{len(ds)} 个快照日）")
    if not recs:
        emit("\n[中止] 无可用样本：ranking_history 缺 dimensions/price，"
             "或 backtest_prices 缺 T+N 收盘价。")
        return 1

    emit("\n【样本按市况分布】（口径 A：各态样本量与天数）")
    emit(f"  {'市况':<18s}{'条数':>7s}{'快照日':>8s}")
    emit("  " + "-" * 34)
    for st in sorted(strata, key=lambda s: -len(strata[s])):
        days = len({str(r['date'])[:10] for r in strata[st]})
        emit(f"  {str(st):<18s}{len(strata[st]):>7d}{days:>8d}")
    if unlabeled:
        days = len({str(r['date'])[:10] for r in unlabeled})
        emit(f"  {'(无市况落库)':<18s}{len(unlabeled):>7d}{days:>8d}")
    emit("  注：市况分层用细化态（neutral_bearish 仅在两日确认后出现）。")

    # 全量分层表
    for st in sorted(strata, key=lambda s: -len(strata[s])):
        for ln in _stratum_lines(_stratum_table(st, strata[st])):
            emit(ln)

    tgt = args.regime
    tgt_recs = strata.get(tgt) or []
    tgt_days = len({str(r['date'])[:10] for r in tgt_recs})

    emit("\n" + "=" * 84)
    emit(f"【目标市况：{tgt}】样本 {len(tgt_recs)} 条 / {tgt_days} 个快照日")
    emit("=" * 84)
    if not tgt_recs:
        emit(f"\n无 {tgt} 市况样本 —— 无法判断。维持现状（仅 neutral_bearish 开组合分排序）。")
        _flush_out(args, lines)
        return 1

    for ln in _detail_lines(tgt, tgt_recs, regime_map, prev_map):
        emit(ln)

    # 显著性：组合分 − 总分 的分离差
    if args.bootstrap:
        di = _day_index(tgt_recs)
        bs = _block_bootstrap(di, tgt_recs, args.bootstrap, args.seed)
        emit(f"\n【显著性 · 按快照日整块 bootstrap（{args.bootstrap} 次）】")
        if not bs:
            emit("  样本日不足或有效重采样过少，跳过。")
        else:
            emit(f"  组合分分离 {bs['comp_spread']:+.2f}  vs  总分分离 "
                 f"{bs['total_spread']:+.2f}")
            emit(f"  差值 (组合−总分) 均值 {bs['mean']:+.2f}，95% CI "
                 f"[{bs['lo']:+.2f}, {bs['hi']:+.2f}]，P(差值>0)={bs['p_gt0']:.0f}%")

    # 对照：把 neutral_bearish 的旧结论一起打出来
    nb = strata.get("neutral_bearish") or []
    if nb and tgt != "neutral_bearish":
        emit("\n【对照组 · neutral_bearish（09-15 上线依据）】")
        for ln in _stratum_lines(_stratum_table("neutral_bearish", nb)):
            emit(ln)

    # 结论
    emit("\n【结论判读（预注册门槛）】")
    if tgt_days < _MIN_DAYS_HARD:
        emit(f"  ✗ {tgt} 快照日仅 {tgt_days} 天（< {_MIN_DAYS_HARD}）→ 样本不足，"
             f"**不得拍板纳入闸门**，维持现状。")
    elif tgt_days < _MIN_DAYS_SOFT:
        emit(f"  ⚠ {tgt} 快照日 {tgt_days} 天（< {_MIN_DAYS_SOFT}）→ 有方向性参考，"
             f"但不足以作为排序键切换的依据；建议继续观察积累。")
    else:
        emit(f"  ✓ {tgt} 快照日 {tgt_days} 天，达到参考门槛，看下方分离/IC 对比结论。")
    emit("  判读要点：① 组合分分离 > 总分分离 且同向为正；"
         "② 组合分 IC 显著 > 总分 IC；③ 与 nb 对照组量级可比。")
    emit("  三者同时成立 → 才考虑把该市况加入 `_use_composite_rank`；"
         "否则维持 nb-only（零沉没成本）。")

    _flush_out(args, lines)
    return 0


def _flush_out(args, lines):
    """把已收集的报告行落盘（--out 未指定时静默跳过）。"""
    if not args.out:
        return
    try:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\n[已写出] {args.out}")
    except OSError as e:
        print(f"[warn] 写报告失败: {e}")


if __name__ == "__main__":
    sys.exit(main())
