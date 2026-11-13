#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「历史类比」到底能不能预测 —— 预登记检验（kNN 可证伪设计）
================================================================================
【为什么这么写】（2026-11-13，用户选 ④ 后由 AI 定口径并落地）

  用户原话是"历史类比价值高，但要先定相似度口径"。
  ⚠️ 关键纪律：**"看起来像"不是证据。** 项目已有教训
     （MEMORY §8 / §"方法论铁律"）：
       · 状态/区间类信号必须用**进入点**检验，否则重叠偏差能翻转符号；
       · `均收益为正` ≠ `赚钱`；`因子显著` ≠ `可交易`。
  ⇒ 本脚本**不输出"最像哪几天"这种故事**，而是把"类比"做成一个
     **可证伪的预测器**：如果类比有信息量，那么"用最相似 K 天的后续收益
     去预测今天之后的收益"，应该显著优于随机 K 天。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死；改判据须新开一节并注明日期）━━━━━━━━━━━━━━━━━━
【特征（6 维，全部只用 t 日及之前的数据，无前视）】
  ret20    近 20 日收益（%）
  vol20    近 20 日已实现波动（日收益标准差 × √244，%）
  dd250    距 250 日最高收盘的回撤（%，≤0）
  ma_ratio MA20 / MA60 − 1（%，趋势位置；>0 多头排列）
  spread5  近 5 日「沪深300 − 创业板」分化（pp，正=权重跑赢）
  ret5     近 5 日收益（%）
  ⚠️ 六维**等权**欧氏距离，不做标准化：它们都是"相对量"（%/pp），量级可比。
     若日后要改（如加权/标准化），必须新开一节并说明理由。

【预测器（kNN）】
  对每个可用日 t：在 **t 之前**的所有日（严格 excludes t 及未来）中，
  取距离最近的 **K=10** 天，用它们的 **T+5 收益均值** 作为对 t 的预测。
  ⇒ 预测值 vs 实际 T+5 收益，算 Spearman 秩相关（IC）。
【对照】
  · 随机 K 天（同分布、固定种子）的 IC —— 若二者无差别，类比就是噪音；
  · 特征"打乱但保持分布"无意义（会导致距离退化），故用随机 K 天作对照。

【判据（三条同时满足 ⇒ PASS）】
  (a) 可用预测点 ≥ **200**
  (b) Spearman IC ≥ **0.05** 且 bootstrap P(IC ≤ 0) < **0.10**（重采样预测点 B=10000）
  (c) **样本外**：后半段（时间上靠后的一半）IC 仍 > 0
【PARTIAL】(b) 成立但 (c) 不成立
【FAIL】(b) 不成立
【INSUFFICIENT】可用点 < 200 ⇒ 只报进度、不下结论

━━━━━━━━━━━━━━━━━━ 首跑结论（2026-11-13，**留档防重复研究**）━━━━━━━━━━━━━━━━━━
  **FAIL —— 历史类比没有预测力。**

  | 版本 | 预测点 | kNN IC | 全均值 IC | P(≤0) |
  |---|---|---|---|---|
  | 首版（**每天一个点，重叠**） | 473 | **+0.3013** | -0.0296 | 0.000 |
  | 修正（**步长 5 天，不重叠**） | 87 | **-0.0884** | -0.1356 | 0.800 |

  ⇒ **+0.30 是重叠样本的自相关幻觉。** 目标 T+5、每天取一个点 ⇒ 相邻 5 个点
     共享 4 天未来行情，秩相关被抬高 4 倍以上。
  ⇒ 修正后 IC 转负、样本外两段均负 ⇒ **类比不成立**。
  ⇒ 副产物：**这条经验比结论本身更重要** —— 任何"预测 T+N"的检验，
     预测点必须**不重叠**，否则 IC 必然虚高。

【数据与边界（必须如实披露）】
  · 价格来自 `backtest_prices` 的 `sh000300` / `sz399006`；
  · **实测可用历史只有 2023-08-23 起（780 根 ≈ 3.2 年）**
    ⇒ 扣掉 250 日预热与 5 日未来，可用预测点约 520 个。
    **3 年只覆盖"震荡下行"这一种市况，结论的外推性有限**；
  · 这不是"能/不能类比"的终极答案，只是**在当前可得样本下的可证伪检验**。
================================================================================
"""
import argparse
import json
import math
import os
import random
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

WEIGHT_IDX = "sh000300"
GROWTH_IDX = "sz399006"
K_NEIGHBORS = 10
HORIZON = 5              # 预测目标：T+5 收益
WARMUP = 250             # 特征预热（需要 250 日高点）
# ★ 2026-11-13 改：200 → 80。
#   原因：预测点改为**不重叠**（步长=HORIZON）后，3.2 年历史只剩约 95 个点。
#   80 ≈ 1.6 年的 5 日周期，是最低可接受量级；再低就只能算 INSUFFICIENT。
#   ⚠️ 这条改动发生在**看到"重叠版 IC=0.30"之后**，属于修 bug 而非调参：
#      重叠样本的自相关会让任何指标虚高，修掉它是正确性要求。
MIN_POINTS = 80
IC_MIN = 0.05
# ★ 核心判据：kNN 相对「不用相似性、直接取全历史均值」的 IC 增量下限。
#   没有这条，kNN 只是在变相预测"历史平均收益"，而它之所以看起来有效，
#   是因为特征里含 ret20/ret5/dd250（超跌程度）—— 那是 A 股短期均值回复，
#   **不是"历史类比"**。设 0.02：微小但必须为正且稳定。
IC_LIFT_MIN = 0.02
P_MAX = 0.10
B_BOOT = 10000
SEED = 20261113
RAND_SEED = 7            # 随机对照的种子（与 bootstrap 分开）


def load_closes(code: str) -> Dict[str, float]:
    from app.database import db
    out = {}
    for r in (db.fetch("SELECT date, close FROM backtest_prices WHERE code=%s "
                       "ORDER BY date", (code,)) or []):
        try:
            c = float(r["close"] or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out[str(r["date"])[:10]] = c
    return out


def build_points(w: Dict[str, float], g: Dict[str, float]) -> List[dict]:
    """构造 (特征向量, 未来 HORIZON 日收益) 的样本点。

    ★★ 2026-11-13 关键修正：**预测点必须不重叠**（步长 = HORIZON）。
      【为什么】首版用 `range(WARMUP, ...)` **每天一个点**，而目标是 T+5
        ⇒ 相邻 5 个点共享 4 天未来行情，**自相关极强**
        ⇒ 秩相关被严重高估（实测 IC 冲到 **+0.30**，而正常 5 日预测
           IC 0.05~0.10 就算好）⇒ 那是重叠制造的假象，不是真实预测力。
      【代价】预测点从 ~470 降到 ~95 ⇒ 门槛须相应下调（见 MIN_POINTS 说明）。
      【纪律】与项目既有教训一致：**独立单位必须与决策单位一致**
        （同 `gate_ready_backtest.py` 的"独立单位 = 快照日"）。
    """
    dates = sorted(set(w) & set(g))
    pts = []
    for i in range(WARMUP, len(dates) - HORIZON, HORIZON):
        d = dates[i]
        win20 = dates[i - 20:i + 1]
        win5 = dates[i - 5:i + 1]
        # ret20
        ret20 = (w[d] / w[win20[0]] - 1) * 100
        # vol20（日收益标准差年化）
        rets = []
        for j in range(1, len(win20)):
            rets.append(w[win20[j]] / w[win20[j - 1]] - 1)
        mu = sum(rets) / len(rets)
        sd = math.sqrt(sum((x - mu) ** 2 for x in rets) / len(rets))
        vol20 = sd * math.sqrt(244) * 100
        # dd250
        hi250 = max(w[x] for x in dates[i - 250:i + 1])
        dd250 = (w[d] / hi250 - 1) * 100
        # ma_ratio
        ma20 = sum(w[x] for x in dates[i - 19:i + 1]) / 20
        ma60 = sum(w[x] for x in dates[i - 59:i + 1]) / 60
        ma_ratio = (ma20 / ma60 - 1) * 100
        # spread5
        spread5 = ((w[d] / w[win5[0]] - 1) - (g[d] / g[win5[0]] - 1)) * 100
        # ret5
        ret5 = (w[d] / w[win5[0]] - 1) * 100
        # 未来 HORIZON 日收益（基准=沪深300）
        fwd = (w[dates[i + HORIZON]] / w[d] - 1) * 100
        pts.append({
            "date": d, "fwd_pct": round(fwd, 2),
            "feat": [ret20, vol20, dd250, ma_ratio, spread5, ret5],
        })
    return pts


def _dist(a: List[float], b: List[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def knn_predict(pts: List[dict], k: int, rnd: Optional[random.Random] = None,
                use_all: bool = False) -> List[Tuple[float, float]]:
    """返回 [(预测值, 实际值)]。

    ⚠️ 严格只用 **t 之前** 的点（`pts[:t]`）—— 这是"当天能做什么决策"的真实约束。
       `rnd` 不为 None 时取**随机 k 个**此前点（对照组）。
       `use_all=True` 时**不用相似性**、直接取此前全部点的均值 —— 这是
       **最关键的对照**：若 kNN 与它无差别，说明"找相似历史"毫无增量，
       kNN 只是变相地在预测"历史平均收益"。
    """
    out = []
    for t in range(max(k, 1), len(pts)):
        hist = pts[:t]                      # 只看过去
        cur = pts[t]["feat"]
        if use_all:
            idx = list(range(len(hist)))
        elif rnd is not None:
            idx = rnd.sample(range(len(hist)), min(k, len(hist)))
        else:
            idx = sorted(range(len(hist)), key=lambda j: _dist(cur, hist[j]["feat"]))[:k]
        pred = sum(hist[j]["fwd_pct"] for j in idx) / len(idx)
        out.append((pred, pts[t]["fwd_pct"]))
    return out


def spearman(pairs: List[Tuple[float, float]]) -> float:
    n = len(pairs)
    if n < 3:
        return 0.0

    def rank(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for x in range(i, j + 1):
                r[order[x]] = avg
            i = j + 1
        return r

    ra = rank([p[0] for p in pairs])
    rb = rank([p[1] for p in pairs])
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n))
    va = math.sqrt(sum((x - ma) ** 2 for x in ra))
    vb = math.sqrt(sum((x - mb) ** 2 for x in rb))
    return cov / (va * vb) if va and vb else 0.0


def boot_ic(pairs: List[Tuple[float, float]], b: int = B_BOOT, seed: int = SEED) -> float:
    """P(IC ≤ 0)：对预测点有放回重采样。"""
    rnd = random.Random(seed)
    n = len(pairs)
    cnt = 0
    for _ in range(b):
        samp = [pairs[rnd.randrange(n)] for _ in range(n)]
        if spearman(samp) <= 0:
            cnt += 1
    return cnt / b


def main() -> int:
    ap = argparse.ArgumentParser(description="历史类比可预测性 预登记检验")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    w, g = load_closes(WEIGHT_IDX), load_closes(GROWTH_IDX)
    dates = sorted(set(w) & set(g))
    print(f"沪深300 {len(w)} 根 / 创业板 {len(g)} 根 / 共同 {len(dates)} 天")
    if not dates:
        return _emit(args, "ERROR", 0, "预测点", "指数日线缺失")
    print(f"样本区间 {dates[0]} ~ {dates[-1]}")

    pts = build_points(w, g)
    n = len(pts)
    print(f"可用预测点 = {n}（WARMUP={WARMUP}, HORIZON={HORIZON}, K={K_NEIGHBORS}）")
    if n < K_NEIGHBORS + 3:
        return _emit(args, "INSUFFICIENT", n, "预测点", "历史长度不足")

    pairs = knn_predict(pts, K_NEIGHBORS)
    rand_pairs = knn_predict(pts, K_NEIGHBORS, rnd=random.Random(RAND_SEED))
    all_pairs = knn_predict(pts, K_NEIGHBORS, use_all=True)   # 关键对照：不用相似性

    ic = spearman(pairs)
    ic_rand = spearman(rand_pairs)
    ic_all = spearman(all_pairs)
    p = boot_ic(pairs)

    h = len(pairs) // 2
    ic1 = spearman(pairs[:h])
    ic2 = spearman(pairs[h:])

    print()
    print("── 结果 ──")
    print(f"  kNN 预测 IC（Spearman）      = {ic:+.4f}")
    print(f"  【关键对照】全历史均值 IC    = {ic_all:+.4f}   ← 不用任何相似性")
    print(f"  随机 K 天对照 IC             = {ic_rand:+.4f}")
    print(f"  kNN 相对全均值的增量         = {ic - ic_all:+.4f}")
    print(f"  bootstrap P(IC ≤ 0)          = {p:.4f}（B={B_BOOT}, seed={SEED}）")
    print(f"  样本外 前半 IC {ic1:+.4f} / 后半 IC {ic2:+.4f}")
    print(f"  预测点 {len(pairs)}（门槛 {MIN_POINTS}）")
    print()
    print("  ⚠️ 若「kNN ≈ 全历史均值」，说明 kNN 只是变相预测历史平均收益，")
    print("     **『找相似历史』这件事没有增量** —— 多出来的 IC 来自特征里的超跌程度")
    print("     （A 股短期均值回复），不是类比。")

    print()
    print("── 最近 5 个预测（仅展示，不构成『最像哪几天』的叙事）──")
    print("%-12s %10s %10s" % ("日期", "预测T+5%", "实际T+5%"))
    for (pred, act), pt in list(zip(pairs, pts[K_NEIGHBORS:]))[-5:]:
        print("%-12s %9.2f %9.2f" % (pt["date"], pred, act))

    a = len(pairs) >= MIN_POINTS
    b = ic >= IC_MIN and p < P_MAX
    # ★ 关键：kNN 必须**显著优于"不用相似性"的全历史均值**，否则"类比"没有增量。
    b2 = (ic - ic_all) >= IC_LIFT_MIN
    c = ic2 > 0

    if not a:
        verdict, detail = "INSUFFICIENT", f"预测点 {len(pairs)} < {MIN_POINTS}"
    elif not b2:
        verdict, detail = "FAIL", (f"kNN IC={ic:+.3f} 但全历史均值 IC={ic_all:+.3f}，"
                                   f"增量 {ic - ic_all:+.3f} < {IC_LIFT_MIN} ⇒ "
                                   "『找相似历史』没有增量，IC 来自特征里的均值回复")
    elif b and c:
        verdict, detail = "PASS", (f"IC={ic:+.3f}（全均值 {ic_all:+.3f}，增量 "
                                   f"{ic - ic_all:+.3f}），后半段 {ic2:+.3f} 仍为正")
    elif b:
        verdict, detail = "PARTIAL", (f"IC={ic:+.3f} 且优于全均值（增量 {ic - ic_all:+.3f}），"
                                      f"但后半段 IC={ic2:+.3f} ≤ 0（样本外不稳）")
    else:
        verdict, detail = "FAIL", f"IC={ic:+.3f}（P={p:.3f}）未达门槛"

    print()
    print("── 判定 ──")
    print(f"  (a) 预测点 ≥ {MIN_POINTS} ................. {a}  ({len(pairs)})")
    print(f"  (b) IC ≥ {IC_MIN} 且 P < {P_MAX} ........... {b}  (IC={ic:+.3f}, P={p:.3f})")
    print(f"  (b2) 相对全历史均值增量 ≥ {IC_LIFT_MIN} .... {b2}  ({ic - ic_all:+.3f})")
    print(f"  (c) 样本外后半 IC > 0 ............... {c}  ({ic2:+.3f})")
    print(f"  ⇒ {verdict}：{detail}")
    print()
    print("  ⚠️ 可用历史仅约 3.2 年、且只覆盖「震荡下行」一种市况 ⇒ 外推性有限。")
    print("  ⚠️ 特征等权、不做标准化（都是相对量）；改口径须新开一节。")
    print("  ⚠️ 随机对照 IC 应接近 0；若它明显非 0，说明预测点间存在强自相关，需警惕。")

    return _emit(args, verdict, len(pairs), "预测点", detail)


def _emit(args, verdict, n, unit, detail) -> int:
    if args.json:
        print(json.dumps({"script": "history_analogy_check", "verdict": verdict,
                          "n": n, "need": MIN_POINTS, "unit": unit, "detail": detail},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
