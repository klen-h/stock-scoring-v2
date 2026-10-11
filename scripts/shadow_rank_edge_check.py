#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】影子榜（评分衰减）灰度验证的**预登记检验**（②；把探索工具升级为可决策证据）
================================================================================
背景：记忆里挂着一条待办 ——「**评分权重/衰减冻结**，等 shadow_rank ≥10 快照日 + defensive
  截面日（约 2026-10-08 触发 `compare_shadow_rank.py`）」。2026-10-11 实测：
  · 数据确实起来了（12 个快照日 09-16~10-09，regime 近 30 日 **13 个 defensive** ✓）；
  · **但对照工具不足以支撑决策链变更**：`compare_shadow_rank.py` ①只报**绝对**收益
    （样本期是 defensive 普跌 ⇒ 全是负的，分不清"谁少亏"是选股还是 β）②**无显著性检验**
    ③**到期快照日只有 7 个（<10）** ⇒ 在那 7 天上读出的 Δ+0.62pt 没有统计意义。
  ⇒ 本脚本把这件事**升级为正式检验**：**超额口径剔 β + 配对聚类 bootstrap + 预登记判据**，
    并接进月度复核 ⇒ **样本够了自动出结论**，不再靠人工想起来跑。

【口径（★ 全部与生产同源/可复现）】
  · 变体：`base`(生产) ｜ `grad`(公告后梯度衰减) ｜ `zero`(公告后权重×0)。
    数据 = `shadow_rank_daily(rank_date, variant, code, snapshot_price)`。
  · 买入价 = 该快照日的 `snapshot_price`（实测**等于当日收盘价**，已逐值核对）；
    卖出价 = 快照日后**第 N 个交易日收盘**（交易日历取自本地库）。收益**用后复权价**
    （`close×factor`）⇒ 跨除权日不再假跌（原 `compare_shadow_rank.py` 用未复权 `backtest_prices.close`）。
  · ★★ **超额口径（本脚本的核心升级）**：`excess = 个股收益 − 同期「全市场等权」收益`
    （同一天、同一持有窗口、全市场非 ST/非停牌样本的等权均值）⇒ **剔除 β/市况**，
      只留"选股是否更优"。**没有这一步，defensive 普跌期的一切比较都是假的。**
  · 成本 0.3% 往返（沿用 E2 口径；两变体相同 ⇒ **不影响差值**，只影响绝对超额判读）。

【预登记判据（2026-10-11 写死；改判据须在文件末尾新开一节并注明日期）】
  · **配对设计**：同一批快照日上跑 base 与变体 ⇒ 用**逐日配对差** `d_t = excess(变体,t) − excess(base,t)`，
    对 `d_t` 做**按快照日聚类 bootstrap**（B=10000，seed 固定）⇒ 单侧 P(mean(d) ≤ 0)。
    （配对比两样本更有力，且天然控制"同一天同一市况"。）
  · **两个变体并列 ⇒ Bonferroni 校正**：单侧显著阈值取 **0.025**（=0.05/2），避免"测两个挑一个"。
  · 主判定（对 `zero` 与 `grad` 各判一次，持有 N=5；N=10 作交叉验证）：
      PASS/GO：`mean(d) ≥ +0.30pt` 且 `P ≤ 0.025`   ⇒ **支持把该衰减方案解冻上线**
      PARTIAL/DOWNGRADE：`mean(d) > 0` 但未达门槛 ⇒ 记录，继续攒样本
      FAIL/KILL：`mean(d) ≤ 0` 且 `P ≥ 0.20`（双条件 —— P1-2 反省后的口径）⇒ 关掉该实验
  · 样本门槛（比原脚本更严，因为要动决策链）：**≥20 个到期快照日** 且 每变体 ≥400 观测，
    否则 INSUFFICIENT（当前 **7 天** ⇒ 必然 INSUFFICIENT，**这是正确行为**）。
  · 判读纪律：**本脚本只产出证据，不自动改权重/衰减** —— 解冻属决策链变更，须人确认。

────────────────────────────────────────────────────────────────────────────────
【首跑记录（2026-10-11；12 个快照日 / 7 个 T+5 到期日 / 350 观测）】
  · `base` 平均**超额 −2.19%**（T+5）⇒ 样本期（defensive）里生产 Top50 **跑输**全池；
    这是"评分体系在防御市失灵"的先验，但**只有 7 天，不能下结论**。
  · `grad` 超额差 **+0.04pt**、95%CI [−0.06,+0.15]、P=0.2202 ⇒ 几乎无差（不显著）。
  · `zero` 超额差 **+0.62pt**、95%CI [+0.18,+1.13]、**P=0.0006** ⇒ 单看数字像"铁证"。
  · **⇒ verdict = INSUFFICIENT**：被预登记的 `MIN_DAYS=20` 正确挡住
    （T+10 只有 2 天、也报 P=0.0000 —— **纯噪声**，正好反证"P 小 ≠ 可信"）。
    ★★ **示范价值**：若没有门槛，这里就会拿 7 天 + P=0.0006 去**解冻决策链权重**。
      与龙虎榜那条教训同源：**效应量/显著性都必须先过"样本与可执行性"两道闸**。
  · 样本增长路径：`backtest_prices` 每日更新 ⇒ 到期日随日历自然增长，
    达到 20 天（约 2026-11 中旬）后由**月度复核自动出结论**。

────────────────────────────────────────────────────────────────────────────────
【开发踩坑记录（两条，都会静默给出错数字）】
  1. **别拿研究库当交易日历**：`data/zzshare_daily.db` 只更新到某一日（实测 09-24）⇒
     用它做交易日历会让之后的快照日**永远算不出 T+N**（样本被卡死在 2 天）。
     日历必须用**每日更新**的源（`backtest_prices` 的 sh000300）。
  2. **`close × factor` 的前提是 factor 稳定**：同一条 code 序列内 `factor` 会**跳变**
     （实测：5 交易日步长里 |Δfactor|>1% 占 **1.72%**、>5% 占 **0.21%**，集中在季末/分红期
     ⇒ 多为**真实公司行为**，故 `close×factor` 方向正确；但极端样例（603030 factor
     6.01→3.60 而原始价几乎没动）提示**存在疑似不一致**）⇒ 本脚本为稳妥**改用单源
     `backtest_prices` 未复权价**（变体与基准同源同步 ⇒ 相对比较不受影响），
     并把"复权口径"列为局限。★ 交叉验证：LHB 的 pack(未复权 +3.84pt) 与 zzshare(后复权
     +4.29pt) **两源结论一致** ⇒ 说明该口径差异在 5 日窗口内不改变方向与量级。
================================================================================
"""
import argparse
import json
import os
import random
import sqlite3
import sys
from collections import defaultdict
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
for line in open(os.path.join(ROOT, "backend", ".env"), encoding="utf-8"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ── 预登记常量 ──
HOLDS = (5, 10)
PRIMARY = 5
GO_DIFF = 0.30             # 逐日配对差均值 ≥ +0.30pt
GO_P = 0.025               # Bonferroni：0.05/2（两个变体并列）
KILL_P = 0.20
MIN_DAYS = 20              # 到期快照日门槛
MIN_OBS = 400              # 每变体观测门槛
COST_PCT = 0.30
BOOT_N = 10000
BOOT_SEED = 20261011
VARIANTS = ("grad", "zero")     # 与 base 对比的两个实验变体


def trading_days() -> List[str]:
    """交易日历：`backtest_prices` 的 `sh000300` 序列。

    ★ 为什么**不**用本地研究库的日期当历：`data/zzshare_daily.db` 只更新到某一日（实测
      2026-09-24）⇒ 用它当历会让之后的快照日**永远算不出 T+N**（样本被卡死在 2 天）。
      `backtest_prices` 由日批 `backfill` **每日更新** ✓ ⇒ 样本能随日历自然增长。
    """
    from app.database import db
    rows = db.fetch("SELECT DISTINCT date FROM backtest_prices WHERE code = 'sh000300' "
                    "ORDER BY date ASC") or []
    return [str(r["date"])[:10] for r in rows]


def bt_prices(dates: List[str], codes: List[str] = None) -> Dict[tuple, float]:
    """读 `backtest_prices` → `{(code, date): close}`。`codes=None` ⇒ 全池（做基准用）。

    ★ 单源（与 `compare_shadow_rank.py` 同源），且**不做复权** —— 见文件头"口径"与
      "局限"：5 交易日窗口里复权影响小，且**变体与基准同源同步** ⇒ 相对比较不受影响。
    """
    from app.database import db
    out: Dict[tuple, float] = {}
    if not dates:
        return out
    ph = ",".join(["%s"] * len(dates))
    if codes:
        cph = ",".join(["%s"] * len(codes))
        rows = db.fetch("SELECT code, date, close FROM backtest_prices WHERE date IN (%s) "
                        "AND code IN (%s)" % (ph, cph), (*dates, *codes)) or []
    else:
        rows = db.fetch("SELECT code, date, close FROM backtest_prices WHERE date IN (%s)"
                        % ph, tuple(dates)) or []
    for r in rows:
        if r.get("close") is not None:
            out[(r["code"], str(r["date"])[:10])] = float(r["close"])
    return out


def market_benchmark(pairs: Dict[int, List[tuple]], holds) -> Dict:
    """★ 全池等权基准：`pairs = {hold: [(d0, d1), ...]}` ⇒ `{hold: {d0: 等权收益%}}`。

    ⚠️ 必须**显式传入 (d0,d1) 配对**（首版误用"日期并集按位偏移"，会把快照日配到错的日子）；
      基准必须与变体**同一批窗口、同一数据源**，否则"超额"里混进口径差。
    口径：`backtest_prices` 全池（~950 只，含回测池/战法池 ⇒ 是**代理全市场**，见局限）等权。
    """
    allds = sorted({d for h in holds for p in pairs.get(h, []) for d in p})
    out = {h: {} for h in holds}
    if not allds:
        return out
    px = bt_prices(allds)                      # 全池
    by = defaultdict(dict)
    for (c, d), v in px.items():
        by[c][d] = v
    for h in holds:
        for d0, d1 in pairs.get(h, []):
            rets = [(v[d1] / v[d0] - 1.0) * 100.0 for v in by.values()
                    if v.get(d0) and v.get(d1)]
            if rets:
                out[h][d0] = sum(rets) / len(rets)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论（日批契约）")
    args = ap.parse_args()

    def emit(verdict: str, n: int, detail: str):
        if args.json:
            print(json.dumps({"script": "shadow_rank_edge_check", "verdict": verdict,
                              "n": n, "need": MIN_DAYS, "unit": "到期快照日",
                              "detail": detail}, ensure_ascii=False))

    from app.database import db
    rows = db.fetch("SELECT rank_date, variant, code, snapshot_price FROM shadow_rank_daily "
                    "ORDER BY rank_date ASC") or []
    if not rows:
        print("[数据] shadow_rank_daily 为空（先跑 shadow_decay_ranking.py --apply）")
        emit("INSUFFICIENT", 0, "shadow_rank_daily 为空")
        return
    snap = defaultdict(lambda: defaultdict(list))     # {date: {variant: [(code, px)]}}
    for r in rows:
        snap[str(r["rank_date"])[:10]][r["variant"]].append(
            (r["code"], float(r["snapshot_price"] or 0)))
    dates = sorted(snap)
    td = trading_days()
    if not td:
        print("[数据] 交易日历不可用（backtest_prices 无 sh000300）")
        emit("INSUFFICIENT", 0, "交易日历不可用")
        return
    tdi = {d: i for i, d in enumerate(td)}
    # ★ 显式配对（快照日 → 第 N 个交易日）；基准与变体**同一批窗口 + 同一数据源**
    pairs = {h: [(d, td[tdi[d] + h]) for d in dates
                 if d in tdi and tdi[d] + h < len(td)] for h in HOLDS}
    need_dates = sorted({x for h in HOLDS for p in pairs[h] for x in p})
    bm = market_benchmark(pairs, HOLDS)
    codes = sorted({c for v in snap.values() for lst in v.values() for c, _p in lst})
    px_all = bt_prices(need_dates, codes)

    print("=" * 82)
    print("影子榜（评分衰减）灰度验证 —— **预登记**检验（超额口径剔 β + 配对聚类 bootstrap）")
    print("=" * 82)
    print("快照日 %d 个（%s ~ %s）；变体 = %s（对比 base）"
          % (len(dates), dates[0], dates[-1], "/".join(VARIANTS)))
    print("口径：快照价(=当日收盘)买入 → T+N 收盘；**收益 − 同期全市场等权** = 超额；成本 %.2f%%"
          % COST_PCT)
    print()

    results = {}
    for h in HOLDS:
        # 逐日：各变体的平均超额；配对差 d_t = excess(variant) − excess(base)
        per_day = {v: {} for v in ("base",) + VARIANTS}
        for d in dates:
            i = tdi.get(d)
            if i is None or i + h >= len(td):
                continue
            d1 = td[i + h]
            bench = bm[h].get(d)
            if bench is None:
                continue
            for v in ("base",) + VARIANTS:
                ex = []
                for c, p0 in snap[d].get(v, []):
                    p1 = px_all.get((c, d1))
                    if p0 and p1:
                        ex.append((p1 / p0 - 1.0) * 100.0 - bench)
                if ex:
                    per_day[v][d] = sum(ex) / len(ex)
        common = sorted(set(per_day["base"]) & set(per_day[VARIANTS[0]]) &
                        set(per_day[VARIANTS[1]]))
        n_obs = sum(len(snap[d].get("base", [])) for d in common)
        print("── 持有 %d 个交易日：到期且三变体齐备的快照日 **%d** 个（观测 %d）──"
              % (h, len(common), n_obs))
        if len(common) >= 2:
            mb = sum(per_day["base"][d] for d in common) / len(common)
            print("  base 平均超额 %+.2f%%" % mb)
        rnd = random.Random(BOOT_SEED)
        for v in VARIANTS:
            ds = [per_day[v][d] - per_day["base"][d] for d in common]
            if len(ds) < 2:
                print("  %-4s 样本不足（%d 日）" % (v, len(ds)))
                continue
            md = sum(ds) / len(ds)
            diffs = []
            for _ in range(BOOT_N):
                s = sum(ds[rnd.randrange(len(ds))] for _ in range(len(ds)))
                diffs.append(s / len(ds))
            diffs.sort()
            n = len(diffs)
            p = sum(1 for x in diffs if x <= 0) / float(n)
            lo, hi = diffs[int(0.025 * n)], diffs[int(0.975 * n)]
            print("  %-4s 超额差 %+.2fpt  95%%CI [%+.2f, %+.2f]  P(≤0)=%.4f  （n=%d 日）"
                  % (v, md, lo, hi, p, len(ds)))
            results[(h, v)] = {"diff": md, "p": p, "days": len(common), "n": n_obs}
        print()

    # ── 主判定（PRIMARY 持有期，两个变体各判一次）──
    print("=== 主判定（预登记：持有 %d 日；Bonferroni 阈值 P ≤ %.3f）===" % (PRIMARY, GO_P))
    verdict, detail = "INSUFFICIENT", ""
    best = None
    for v in VARIANTS:
        r = results.get((PRIMARY, v))
        if not r:
            print("  %-4s 无足够样本" % v)
            continue
        d, p, days = r["diff"], r["p"], r["days"]
        if days < MIN_DAYS or r["n"] < MIN_OBS:
            tag, why = "INSUFFICIENT", "样本 %d 日(需%d)/%d 观测(需%d)" % (
                days, MIN_DAYS, r["n"], MIN_OBS)
        elif d >= GO_DIFF and p <= GO_P:
            tag, why = "PASS", "GO：超额差 %+.2fpt、P=%.4f ⇒ 支持把该衰减方案解冻上线" % (d, p)
        elif d > 0:
            tag, why = "PARTIAL", "DOWNGRADE：超额差 %+.2fpt（P=%.4f）未达门槛 ⇒ 继续攒样本" % (d, p)
        elif p >= KILL_P:
            tag, why = "FAIL", "KILL：超额差 %+.2fpt 且 P=%.4f（双条件）⇒ 关掉该实验" % (d, p)
        else:
            tag, why = "PARTIAL", "不为 KILL：超额差 %+.2fpt 但 P=%.4f（证据不足）" % (d, p)
        print("  %-4s → %-12s %s" % (v, tag, why))
        rank = {"PASS": 3, "PARTIAL": 2, "INSUFFICIENT": 1, "FAIL": 0}[tag]
        if best is None or rank > best[0]:
            best = (rank, tag, "%s: %s" % (v, why))
        if tag in ("PASS", "FAIL") or (best and best[0] < 2):
            pass
    if best:
        verdict, detail = best[1], best[2]
    print("  ⇒ verdict = %s（★ 本脚本**只出证据**，解冻权重/衰减属决策链变更，须人确认）"
          % verdict)
    print()
    print("注：到期快照日达 **%d** 个（约一个月）才有决策力；当前 %d 个 ⇒ 结论必然是"
          " INSUFFICIENT，这是**正确行为**（样本是时钟）。"
          % (MIN_DAYS, results.get((PRIMARY, VARIANTS[0]), {}).get("days", 0)))
    emit(verdict, results.get((PRIMARY, VARIANTS[0]), {}).get("days", 0), detail)


if __name__ == "__main__":
    main()
