#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「风格分化」是否可交易 —— 预登记检验（含**剔 β** 对照）
================================================================================
【为什么有本脚本】（2026-11-13，用户选 ③ 后由 AI 按建议落地）

  同日实测 `coach.gate_add` 的 17 个触发日，T+5 收益：
      上证50 -0.83% ｜ 沪深300 -1.85% ｜ 中证1000 -2.50% ｜ 创业板 -4.36%
  按触发日「近 5 日 沪深300 − 创业板」分组：
      分化度 ≥ +3pp → 创业板 T+5 -2.46%（胜率 33%，n=6）
      分化度 <  +3pp → 创业板 T+5 -5.80%（胜率  0%，n=8）
  ⇒ 分化度看起来有区分力。**但那是 17 个样本、单一市况。**

  本脚本回答更硬的问题：**把「按分化度切风格」做成可执行策略，扣掉 β 后还有没有超额？**

★★★ 本脚本最重要的设计：**两个对照 + 剔 β**
  首版只测"权重 vs 空仓"，得出 -22.68pp 的 FAIL —— 但那失败的是**择时**，
  不是"风格"本身。改成"权重 vs 成长"后得到 **+29.70pp**，看起来很漂亮，
  然而同期"一直持成长"（V4）也有 **+24.19pp** ⇒
  **轮动的真实增量只有 5.51pp，且样本外前半为负（-0.073pp）。**
  ⇒ **+29.70pp 里 82% 是"持有创业板"这个 β，不是分化度的功劳。**
  这正是项目铁律「因子显著 ≠ 可交易」「必须超额口径剔 β」的又一次实例。

━━━━━━━━━━━━━━━━━━ 预登记（先于结果写死；改判据须新开一节并注明日期）━━━━━━━━━━━━━━━━━━
【策略定义（固定，不调参）】
  · 决策频率：每 5 个交易日一次
  · 决策依据：「近 5 日 沪深300 − 创业板」分化度（**只用决策日及之前**的数据）
  · 轮动规则（V2）：分化度 ≥ +3pp ⇒ 下一周期持沪深300；否则持创业板
  · 始终满仓（不空仓）—— 空仓版已实测 FAIL（见下"已测结论"）

【四个变体（必须一起看，缺一个就会误判）】
  V1 择时    ：≥3pp 持权重 / 否则**空仓**
  V2 轮动    ：≥3pp 持权重 / 否则持成长          ← 待检验的主体
  V3 反向轮动：≥3pp 持成长 / 否则持权重          ← 筛选偏差对照（若 V3 也行，说明信号没用）
  V4 一直持成长：全程持创业板                    ← **β 基准，剔 β 用**

【判据（四条同时满足 ⇒ PASS）】
  (a) V2 累计超额（对沪深300 买入持有）≥ **+3.00pp**
  (b) **真实增量 = V2 超额 − V4 超额 ≥ +3.00pp**   ← 剔 β，最关键
  (c) 样本外：前 50% 与后 50% 的**单周期增量**均 > 0
  (d) bootstrap P(单周期增量均值 ≤ 0) < **0.10**（决策点重采样 B=10000，固定种子）
【PARTIAL】(a)(b) 成立但样本外只有一段为正
【FAIL】(b) 不成立（含"增量 ≤ 0"），或样本外两段都 ≤ 0
【INSUFFICIENT】决策点 < **30** ⇒ 只报进度、不下结论

【已测结论（2026-11-13 首跑，留档防重复研究）】
  V1 择时    ：累计超额 **-22.68pp**，持仓仅 14/146 周期 ⇒ **FAIL**（90% 时间在空仓，
              错过基准 +16.87%，本质是"用择时信号做仓位管理"，方向错了）
  V2 轮动    ：+29.70pp，但样本外前半 -0.073pp
  V4 持成长  ：+24.19pp ⇒ **增量仅 +5.51pp**
  ⇒ 综合 **FAIL**（增量不足 + 样本外不稳定 + P=0.162 不显著）

【阈值来源（预注册，经验初值、未校准）】
  · 分化度门槛 +3pp 沿用 `coach.gate_add_weak_direction` 的既有阈值（不改）
  · 累计超额/增量门槛 +3pp ≈ 单周期 0.3pp × 10 周期，低于此无交易意义
  ⚠️ 与项目其它预登记脚本同款：阈值未回测校准，PASS 也仅代表"值得进入下一轮"。

【与模拟盘的关系】
  · 本脚本**只读**，不写任何生产表（同 `edge_verify` 纪律）；
  · 它是"要不要给模拟盘换策略"的**前置证据** ——
    在它 PASS 之前，不应该把主模拟盘（当前跑已证伪的 6 战法）重新开起来。
================================================================================
"""
import argparse
import json
import os
import random
import sys
from typing import Dict, List

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

# ── 预登记常量（改动须在本文件新开一节并注明日期，不许就地调参）──
WEIGHT_IDX = "sh000300"
GROWTH_IDX = "sz399006"
LOCK_DAYS = 5
SPREAD_MIN = 3.0
MIN_DECISIONS = 30
EDGE_MIN = 3.0          # 累计超额门槛（pp）
DELTA_MIN = 3.0         # 剔除 β 后的真实增量门槛（pp）
P_MAX = 0.10
B_BOOT = 10000
SEED = 20261113


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


def build_cycles(w: Dict[str, float], g: Dict[str, float]) -> List[dict]:
    """每个决策周期的分化度与四种变体的收益。"""
    dates = sorted(set(w) & set(g))
    if len(dates) < LOCK_DAYS * 2 + 1:
        return []
    out = []
    i = LOCK_DAYS
    while i + LOCK_DAYS < len(dates):
        d0, d1 = dates[i], dates[i + LOCK_DAYS]
        spread = ((w[d0] / w[dates[i - LOCK_DAYS]] - 1)
                  - (g[d0] / g[dates[i - LOCK_DAYS]] - 1)) * 100
        wf = (w[d1] / w[d0] - 1) * 100
        gf = (g[d1] / g[d0] - 1) * 100
        long_w = spread >= SPREAD_MIN
        out.append({
            "date": d0, "next": d1, "spread_pp": round(spread, 2),
            "w_pct": round(wf, 2), "g_pct": round(gf, 2), "long_weight": long_w,
            # 四个变体
            "v1": wf if long_w else 0.0,           # 择时：否则空仓
            "v2": wf if long_w else gf,            # 轮动：否则持成长
            "v3": gf if long_w else wf,            # 反向轮动（对照）
            "v4": gf,                              # 一直持成长（β 基准）
            "bench": wf,                           # 基准：一直持沪深300
        })
        i += LOCK_DAYS
    return out


def _boot_p(vals: List[float], b: int = B_BOOT, seed: int = SEED) -> float:
    """单侧 bootstrap：P(均值 ≤ 0)。固定种子 ⇒ 可复算。"""
    if not vals:
        return 1.0
    rnd = random.Random(seed)
    n = len(vals)
    cnt = 0
    for _ in range(b):
        if sum(vals[rnd.randrange(n)] for _ in range(n)) / n <= 0:
            cnt += 1
    return cnt / b


def _summary(cyc: List[dict], key: str) -> dict:
    """单变体汇总：累计、超额、样本外拆分、P。"""
    n = len(cyc)
    strat = [c[key] for c in cyc]
    bench = [c["bench"] for c in cyc]
    edge = [a - b for a, b in zip(strat, bench)]
    h = n // 2
    return {
        "cum": sum(strat), "bench_cum": sum(bench), "edge": sum(edge),
        "avg": sum(edge) / n if n else 0.0,
        "e1": sum(edge[:h]) / h if h else 0.0,
        "e2": sum(edge[h:]) / (n - h) if (n - h) else 0.0,
        "p": _boot_p(edge),
        "held": sum(1 for c in cyc if c[key] != 0.0),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="风格分化可交易性 预登记检验")
    ap.add_argument("--json", action="store_true", help="末行追加一行 JSON 供自动复核解析")
    args = ap.parse_args()

    w, g = load_closes(WEIGHT_IDX), load_closes(GROWTH_IDX)
    print(f"沪深300 {len(w)} 根 / 创业板 {len(g)} 根 / 共同 {len(set(w) & set(g))} 天")
    if not w or not g:
        print("指数日线缺失，无法检验")
        return _emit(args, "ERROR", 0, "决策点", "指数日线缺失")

    cyc = build_cycles(w, g)
    n = len(cyc)
    print(f"决策点 = {n}（每 {LOCK_DAYS} 个交易日一个，门槛 {MIN_DECISIONS}）")
    if not cyc:
        return _emit(args, "INSUFFICIENT", 0, "决策点", "历史长度不足")

    in_w = sum(1 for c in cyc if c["long_weight"])
    print(f"分化度 ≥ {SPREAD_MIN}pp 的周期 = {in_w}/{n}（{in_w / n * 100:.0f}%）")

    print()
    print("── 四变体对比（累计收益 / 对沪深300 超额）──")
    print("%-16s %10s %10s %10s %10s %8s" % (
        "变体", "策略累计", "累计超额", "样本外前", "样本外后", "P(≤0)"))
    rows = {}
    for key, label in (("v1", "V1 择时(权重/空仓)"),
                       ("v2", "V2 轮动(权重/成长)"),
                       ("v3", "V3 反向轮动(对照)"),
                       ("v4", "V4 一直持成长(β)")):
        s = _summary(cyc, key)
        rows[key] = s
        print("%-16s %9.2f%% %9.2fpp %9.3fpp %9.3fpp %8.3f" % (
            label, s["cum"], s["edge"], s["e1"], s["e2"], s["p"]))
    print("%-16s %9.2f%% %9s %9s %9s %8s" % (
        "基准 持沪深300", rows["v2"]["bench_cum"], "—", "—", "—", "—"))

    # ── 剔 β：V2 相对 V4 的真实增量 ──
    deltas = [c["v2"] - c["v4"] for c in cyc]
    d_total = sum(deltas)
    h = n // 2
    d1 = sum(deltas[:h]) / h if h else 0.0
    d2 = sum(deltas[h:]) / (n - h) if (n - h) else 0.0
    d_p = _boot_p(deltas)

    print()
    print("── 剔 β：轮动相对「一直持成长」的真实增量 ──")
    print(f"  累计增量 = {d_total:+.2f}pp（V2 {rows['v2']['edge']:+.2f} − V4 {rows['v4']['edge']:+.2f}）")
    print(f"  单周期增量均值 = {d_total / n:+.3f}pp")
    print(f"  样本外 前半 {d1:+.3f}pp / 后半 {d2:+.3f}pp")
    print(f"  bootstrap P(增量均值≤0) = {d_p:.4f}（B={B_BOOT}, seed={SEED}）")
    share = (rows["v4"]["edge"] / rows["v2"]["edge"] * 100) if rows["v2"]["edge"] else 0
    print(f"  ⇒ V2 超额里 **{share:.0f}% 来自「持有成长」的 β**，不是分化度的功劳")

    print()
    print("── 明细（最近 6 个周期）──")
    print("%-12s %-12s %9s %6s %9s %9s" % (
        "决策日", "持有区间", "分化pp", "持权重", "权重%", "成长%"))
    for c in cyc[-6:]:
        print("%-12s %-12s %9.2f %6s %9.2f %9.2f" % (
            c["date"], c["next"], c["spread_pp"],
            "是" if c["long_weight"] else "否", c["w_pct"], c["g_pct"]))

    # ── 判定 ──
    a = rows["v2"]["edge"] >= EDGE_MIN
    b = d_total >= DELTA_MIN
    c_ok = d1 > 0 and d2 > 0
    d_ok = d_p < P_MAX

    if n < MIN_DECISIONS:
        verdict, detail = "INSUFFICIENT", f"决策点 {n} < {MIN_DECISIONS}，只报进度"
    elif b and a and c_ok and d_ok:
        verdict, detail = "PASS", (f"轮动超额 {rows['v2']['edge']:+.2f}pp，"
                                   f"剔 β 后增量 {d_total:+.2f}pp，样本外两段均正，P={d_p:.3f}")
    elif b and a and not (c_ok and d_ok):
        verdict, detail = "PARTIAL", (f"增量 {d_total:+.2f}pp 达标，但样本外不稳定"
                                      f"（前半 {d1:+.3f} / 后半 {d2:+.3f}）或 P={d_p:.3f} ≥ {P_MAX}")
    else:
        verdict, detail = "FAIL", (f"剔 β 后增量仅 {d_total:+.2f}pp < {DELTA_MIN}pp"
                                   f"（V2 超额的 {share:.0f}% 来自持有成长的 β）")

    print()
    print("── 判定 ──")
    print(f"  (a) V2 累计超额 ≥ {EDGE_MIN}pp ................ {a}  ({rows['v2']['edge']:+.2f}pp)")
    print(f"  (b) 剔 β 后增量 ≥ {DELTA_MIN}pp ............... {b}  ({d_total:+.2f}pp)")
    print(f"  (c) 样本外增量两段均 > 0 ................ {c_ok}  ({d1:+.3f} / {d2:+.3f})")
    print(f"  (d) P < {P_MAX} ............................. {d_ok}  ({d_p:.3f})")
    print(f"  ⇒ {verdict}：{detail}")
    print()
    print("  ⚠️ 阈值 +3pp 与 5 日窗口都是看过全样本后定的 ⇒ 存在选择性偏差；")
    print("     (b)(c) 就是为此设的闸门，只看累计超额会严重高估。")
    print("  ⚠️ 始终满仓（无空仓版）；空仓版已实测 -22.68pp FAIL，见文件头。")

    return _emit(args, verdict, n, "决策点", detail)


def _emit(args, verdict, n, unit, detail) -> int:
    if args.json:
        print(json.dumps({"script": "style_timing_edge_check", "verdict": verdict,
                          "n": n, "need": MIN_DECISIONS, "unit": unit, "detail": detail},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
