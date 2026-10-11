#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】龙虎榜信号的**可执行性**终验（A 步；PLAN §6.4 定案后的唯一关门环节）
================================================================================
背景：`lhb_edge_check.py` v2 定案 **PASS**（`big_buy − big_sell` 的 T+5 = **+4.29pct**，
  P=0.0000）—— 但那是**「上榜日收盘 → T+5 收盘」**口径。而龙虎榜次日**普遍高开**
  （追榜资金抢筹），⇒ **你不可能在"收盘价"上车**（收盘时才知道上榜，次日开盘已经跳空）。
  本脚本回答**唯一还能推翻它的问题**：**把入场点换成"次日开盘"，效应还剩多少？**
  项目先例：`scripts/event_executability_check.py`（E2 的"T+1 开盘买持 20 日"终验）—— 同思路。

【数据与口径（★ **复用 `lhb_edge_check` 的取数与分档，避免两份实现漂移**）】
  · 信号/强度分档：**完全沿用**（`big_buy` > +1% / `big_sell` < −1% 占流通市值）
  · 价格源：优先本地全市场库 `data/zzshare_daily.db`（零 egress）⇒ 退回数据包（覆盖低、无 VWAP）
  · 收益用**后复权价**（含开盘价 `open×factor`）；**强度用未复权真实价**（市值口径）

【入场点（本轮核心；三档并列）】
  · `open1`：**T+1 开盘买入** → T+5 收盘卖出    ← **主判定口径**（最保守、最可执行）
  · `vwap1`：T+1 **全天均价**买入（`amount/volume`，单位自动识别）→ T+5 收盘
  · `close0`：上榜日收盘买入 → T+5 收盘（= 定案口径，**仅作对照**，实际不可实现）
  另报两个**诊断量（本脚本真正的价值所在）**：
  · `gap` = (T+1 开盘 / T 收盘 − 1)×100   ← **次日跳空**：量化"高开吃掉多少"
  · `d1`  = (T+1 收盘 / T+1 开盘 − 1)×100 ← **高开后的日内衰减**（为负 ⇒ 高开即回落）

【预登记判据（2026-10-11 写死；改判据须在文件末尾新开一节并注明日期）】
  · 主判定：`big_buy − big_sell` 在 **`open1`（T+1 开盘）** 口径下的 T+5 差 ≥ **+1.00pct**
    且按上榜日聚类 bootstrap（B=10000，seed 固定）单侧 **P ≤ 0.05**
    （**沿用定案的 GO_DIFF/GO_P** ⇒ 与 +4.29pct 的结论可直接对照）
  · 成本 **0.3% 往返**（沿用 E2 口径）。★ 注意：成本对两臂**同为 −0.3%** ⇒ **不影响差值**，
    只影响**绝对收益** ⇒ 故必须另立"绝对收益判读"（见下）。
  · **绝对收益判读（决定"能不能当多头用"，这是本步的关键）**：
      `big_buy` 在 `open1` 口径、扣 0.3% 后**绝对**均值 > 0 **且** 胜率 ≥ 50% ⇒ 「可作多头候选」；
      否则 ⇒ 「**只能作风险/回避标签**」（v2 已提示 v2 口径下 big_buy 绝对仅 +0.53%/胜率 44.9%）。
  · KILL：`open1` 差 ≤ 0 **且** P ≥ 0.20（双条件 —— P1-2 反省后的口径）。
  · 样本门槛：≥300 事件且两臂各 ≥50（沿用定案）。

【口径局限（下结论前必读）】
  1. **开盘价近似"次日开盘一次性买入"**：未计竞价冲击/滑点；且 **T+1 开盘即涨停实际买不到**
     （本表有 `high_limit`）⇒ 主口径**不剔除**（保持与原判据同族、避免选择性偏差），
     但报 `unbuyable` 占比 + **剔除后的敏感性行**（描述性，不作判定）。
  2. T+5 收盘按**可成交**处理（未考虑 T+5 跌停卖不出）。
  3. 窗口 **2026-05-06~09-24**（受 `lhb_history` 起点限制，见 `lhb_edge_check` v2 节）、
     单一市况、96 个聚类日 ⇒ 结论是"这 5 个月里可执行"，不是"永远"。
  4. `vwap1` 依赖本地库的 `amount/volume` 且**单位需自适应**（见 `lhb_edge_check` 取价器注释）；
     备源（pack）没有 VWAP ⇒ 该列显示 `-`。

────────────────────────────────────────────────────────────────────────────────
【★ A 步首跑结论（2026-10-11）：**收盘口径的效应在可执行口径下不成立** ⇒ verdict = PARTIAL】
  覆盖 95.4% ｜ 7,428 条样本（与定案同源）
  ── T+5 收益（三档入场点）──
  bucket         n  | T+1开盘(主判定)     | T+1均价    | T日收盘(对照)
  big_buy     1186  | **−0.50%** 42.0%   | −0.69%     | +0.53%
  small_buy   2271  | −0.78%             | −1.11%     | −0.30%
  small_sell  1975  | −1.25%             | −1.75%     | −2.64%
  big_sell     814  | **−1.95%** 37.6%   | −2.35%     | −3.76%
  ── 诊断（**这一步真正的价值**）──
  次日跳空 gap：big_buy **+0.97%**（52.3% 上涨） vs big_sell **−1.86%**（仅 21.9% 上涨）
    ⇒ **两臂差距（2.83pt）在开盘那一刻就已被定价**；`d1`（T+1 开→收）四档都只有
      +0.2~+0.6% ⇒ **开盘后没有可赚的单边**。
  主判定 `big_buy − big_sell`：**close0 +4.29pct → open1 +1.44pct**（**掉 67%**），
    聚类 bootstrap 95%CI **[−0.33, +3.39]**（**跨 0**）、P=**0.0603** > 0.05 ⇒ **未达 GO**。
  **绝对收益判读（扣 0.3% 往返）**：big_buy open1 毛 −0.50% ⇒ 净 **−0.80%**、胜率 **42.0%**
    ⇒ **多头侧彻底不成立**（不重叠复利净值 open1 扣成本 = **−14.02%**）；
    big_sell open1 ⇒ 净 **−2.25%**、胜率 37.6% ⇒ **空头侧明确**。
  敏感性（描述性）：剔除「T+1 开盘即涨停（买不到；big_buy 占 **4.4%**）」后 diff 降到
    **+0.85pt**；T+1 全天均价口径 diff **+1.66pt**（略优于开盘 ⇒ 盘中确有回落）。
  ⇒★ **结论（落地形态）**：本信号**不是买入信号**，只能作**风险/回避标签**
    （「上榜且大额净卖 ⇒ 次日显著低开 −1.86%」）；**多头侧在可执行口径下为负 ⇒ 禁止按多头上线**。
  ⇒★ **方法论价值（已写进 PLAN 与长期记忆）**：这是「预登记 + 可执行性终验」拦住一个
    **看似 +4.29pct / P=0.0000、实盘必亏**的信号的真实案例 —— **A 股事件研究里「收盘价入场」
    几乎系统性高估**（收盘时才知道事件，次日开盘已跳空）⇒
    **任何事件类信号进决策链前必须过"次日开盘"这一关**。
  ★ 为何仍注册进 `edge_verify`：`lhb_history` 只有 5 个月/单一市况 ⇒ 窗口增长后**自动复检**
    （判定门槛沿用 300 事件）；当前结论已固化在本节。
================================================================================
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, HERE)

from lhb_edge_check import (BOOT_N, BOOT_SEED, GO_DIFF, GO_P, KILL_P,  # noqa: E402
                            MIN_ARM, MIN_EVENTS, _fmt, bucket_of,
                            cluster_boot, load_env, load_events,
                            load_prices_auto, stat)

load_env()

# ── 预登记常量（本脚本）──
COST_PCT = 0.30            # 往返成本（沿用 E2 口径）
HOLD = 5                   # 买入后持到 T+5 收盘
ABS_MIN_WIN = 50.0         # "可作多头候选"的胜率下限
ENTRIES = (("open1", "T+1开盘(主判定)"), ("vwap1", "T+1均价"), ("close0", "T日收盘(对照)"))


def _ret(bars: List[Dict], i_in: int, k_in: str, i_out: int, k_out: str) -> Optional[float]:
    if i_out >= len(bars) or i_out < 0 or i_in < 0:
        return None
    a, b = bars[i_in].get(k_in), bars[i_out].get(k_out)
    if not a or not b or a <= 0:
        return None
    return (b / a - 1.0) * 100.0


def build(events: List[Dict], px: Dict, fs_map: Dict) -> List[Dict]:
    out = []
    for e in events:
        bars = px.get(e["code"])
        if not bars:
            continue
        idx = {b["date"]: i for i, b in enumerate(bars)}
        i = idx.get(e["date"])
        if i is None or i + HOLD >= len(bars):
            continue
        c0 = bars[i]["close"]
        fs = fs_map.get(e["code"])
        st = (e["net_buy"] / (c0 * fs) * 100.0) if (fs and fs > 0 and c0 > 0) else None
        r = dict(e)
        r["strength"] = st
        r["bucket"] = bucket_of(st)
        r["close0"] = _ret(bars, i, "adj_close", i + HOLD, "adj_close")
        r["open1"] = _ret(bars, i + 1, "adj_open", i + HOLD, "adj_close")
        r["vwap1"] = _ret(bars, i + 1, "adj_vwap", i + HOLD, "adj_close")
        b1 = bars[i + 1]
        a0 = bars[i]["adj_close"]
        o1, c1 = b1.get("adj_open"), b1.get("adj_close")
        r["gap"] = ((o1 / a0 - 1.0) * 100.0) if (o1 and a0) else None
        r["d1"] = ((c1 / o1 - 1.0) * 100.0) if (o1 and c1) else None
        lim, ro = b1.get("limit_up"), b1.get("open")
        r["unbuyable"] = bool(lim and ro and ro >= lim * 0.999)
        out.append(r)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="末尾追加一行结构化结论（日批契约）")
    args = ap.parse_args()

    def emit(verdict: str, n: int, detail: str):
        if args.json:
            print(json.dumps({"script": "lhb_executability_check", "verdict": verdict,
                              "n": n, "need": MIN_EVENTS, "unit": "上榜事件",
                              "detail": detail}, ensure_ascii=False))

    try:
        events = load_events()
    except Exception as e:
        print("[数据] lhb_history 读取失败: %s" % str(e)[:120])
        emit("INSUFFICIENT", 0, "lhb_history 读取失败")
        return
    if not events:
        print("[数据] lhb_history 为空")
        emit("INSUFFICIENT", 0, "lhb_history 为空")
        return

    codes = sorted({e["code"] for e in events})
    px, source, err = load_prices_auto(codes, min(e["date"] for e in events))
    if not px:
        print("[数据] %s" % err)
        emit("INSUFFICIENT", 0, err)
        return
    hit = [c for c in codes if c in px]
    cov = len(hit) / float(max(1, len(codes)))
    try:
        from app.mainforce.flow import get_float_shares_from_snapshot
        fs_map = get_float_shares_from_snapshot() or {}
    except Exception as e:
        print("[数据] 流通股本读取失败: %s" % str(e)[:100])
        emit("INSUFFICIENT", 0, "流通股本缺失")
        return
    samples = build(events, px, fs_map)

    print("=" * 84)
    print("龙虎榜信号**可执行性**终验（预登记）—— 入场点从「收盘」换成「次日开盘」还剩多少")
    print("=" * 84)
    print("价格源 = %s ｜ 覆盖 %.1f%% ｜ 有效样本 %d 条（需 T+1 与 T+%d 都有价）"
          % (source, cov * 100, len(samples), HOLD))
    if source != "zzshare_daily":
        print("⚠️ 备源（无 VWAP）⇒ vwap1 列为空；结论以脚本头记录的定案为准")
    print()

    arms = ("big_buy", "small_buy", "small_sell", "big_sell")
    print("── T+%d 收益：三档入场点 × 四档强度（未扣成本）──" % HOLD)
    hdr = "%-11s %5s |" % ("bucket", "n")
    for k, cn in ENTRIES:
        hdr += "  %-18s |" % cn
    print(hdr)
    print("-" * len(hdr))
    stats = {}
    for b in arms:
        rows = [s for s in samples if s["bucket"] == b]
        line = "%-11s %5d |" % (b, len(rows))
        for k, _cn in ENTRIES:
            st = stat([s.get(k) for s in rows])
            stats[(b, k)] = st
            line += "  " + _fmt(st) + " |"
        print(line)
    print()

    print("── 诊断：次日跳空 gap 与日内衰减 d1（解释「高开吃掉多少」）──")
    print("%-11s %5s |  gap(次日跳空)          |  d1(T+1开→收)        | 开盘即涨停占比"
          % ("bucket", "n"))
    print("-" * 92)
    for b in arms:
        rows = [s for s in samples if s["bucket"] == b]
        g, d = stat([s.get("gap") for s in rows]), stat([s.get("d1") for s in rows])
        ub = sum(1 for s in rows if s.get("unbuyable"))
        print("%-11s %5d | %s | %s | %5.1f%%（%d 条）"
              % (b, len(rows), _fmt(g), _fmt(d),
                 100.0 * ub / max(1, len(rows)), ub))
    print()

    # ── 主判定：open1 口径的 big_buy − big_sell ──
    a_rows = [s for s in samples if s["bucket"] == "big_buy"]
    b_rows = [s for s in samples if s["bucket"] == "big_sell"]
    a_vals = [s["open1"] for s in a_rows if s.get("open1") is not None]
    b_vals = [s["open1"] for s in b_rows if s.get("open1") is not None]
    sa, sb = stat(a_vals), stat(b_vals)
    print("=== 主判定（预登记：open1 口径，big_buy − big_sell，T+%d）===" % HOLD)
    verdict, detail, diff = "INSUFFICIENT", "", None
    if len(samples) < MIN_EVENTS or len(a_vals) < MIN_ARM or len(b_vals) < MIN_ARM:
        detail = ("样本不足：%d(需%d) / big_buy %d / big_sell %d（各需%d）"
                  % (len(samples), MIN_EVENTS, len(a_vals), len(b_vals), MIN_ARM))
        print("  => %s" % detail)
    else:
        diff = sa["mean"] - sb["mean"]
        a_by, b_by = defaultdict(list), defaultdict(list)
        for s in a_rows:
            if s.get("open1") is not None:
                a_by[s["date"]].append(s["open1"])
        for s in b_rows:
            if s.get("open1") is not None:
                b_by[s["date"]].append(s["open1"])
        boot = cluster_boot(a_by, b_by, BOOT_N, BOOT_SEED)
        print("  big_buy  %s" % _fmt(sa))
        print("  big_sell %s" % _fmt(sb))
        print("  diff(open1) = %+.2f pct   （对照：close0 口径 %+.2f pct）"
              % (diff, (stats.get(("big_buy", "close0")) or {}).get("mean", 0)
                 - (stats.get(("big_sell", "close0")) or {}).get("mean", 0)))
        if boot:
            print("  按上榜日聚类 bootstrap(B=%d, 簇=%d 日): 95%%CI [%+.2f, %+.2f] P(diff<=0)=%.4f"
                  % (boot["boot"], len(set(a_by) | set(b_by)), boot["lo"], boot["hi"],
                     boot["p"]))
        p = boot.get("p", 1.0)
        if diff >= GO_DIFF and p <= GO_P:
            verdict = "PASS"
            detail = "GO: open1 差 %+.2f pct, P=%.4f（收盘口径的效应在次日开盘口径下仍成立）" % (
                diff, p)
        elif diff > 0:
            verdict = "PARTIAL"
            why = []
            if diff < GO_DIFF:
                why.append("差值 < +%.2f" % GO_DIFF)
            if p > GO_P:
                why.append("P=%.4f > %.2f（不显著）" % (p, GO_P))
            detail = "DOWNGRADE: open1 差 %+.2f pct，未达 GO（%s）" % (
                diff, "；".join(why) or "门槛")
        elif p >= KILL_P:
            verdict = "FAIL"
            detail = "KILL: open1 差 %+.2f pct 且 P=%.4f ≥ %.2f（双条件）⇒ 不可交易" % (
                diff, p, KILL_P)
        else:
            verdict = "PARTIAL"
            detail = "不为 KILL：open1 差 %+.2f pct 但 P=%.4f < %.2f（证据不足以判死）" % (
                diff, p, KILL_P)
    print("  ⇒ verdict = %s" % verdict)
    print("     %s" % detail)
    print()

    # ── 绝对收益判读（决定"能不能当多头用"）──
    print("=== 绝对收益判读（扣 %.2f%% 往返成本；★ 成本对两臂相同 ⇒ 不影响差值）===" % COST_PCT)
    long_ok = False
    if sa:
        net = sa["mean"] - COST_PCT
        long_ok = net > 0 and sa["win"] >= ABS_MIN_WIN
        print("  big_buy  open1: 毛 %+.2f%% → 净 %+.2f%%（胜率 %.1f%%）⇒ %s"
              % (sa["mean"], net, sa["win"],
                 "**可作多头候选**" if long_ok else "**只能作风险/回避标签**（多头侧不成立）"))
    if sb:
        print("  big_sell open1: 毛 %+.2f%% → 净 %+.2f%%（胜率 %.1f%%）⇒ %s"
              % (sb["mean"], sb["mean"] - COST_PCT, sb["win"],
                 "**空头侧明确**（回避价值）" if sb["mean"] < 0 else "空头侧不成立"))
    print()

    # ── 敏感性（描述性，不作判定）──
    print("=== 敏感性（描述性，不作判定）===")
    a2 = [s["open1"] for s in a_rows if s.get("open1") is not None and not s.get("unbuyable")]
    b2 = [s["open1"] for s in b_rows if s.get("open1") is not None and not s.get("unbuyable")]
    s2a, s2b = stat(a2), stat(b2)
    if s2a and s2b:
        print("  剔除「T+1 开盘即涨停（实际买不到）」后: big_buy %+.2f%% vs big_sell %+.2f%%"
              "  diff %+.2f pct" % (s2a["mean"], s2b["mean"], s2a["mean"] - s2b["mean"]))
    v_stats = {b: stat([s.get("vwap1") for s in samples if s["bucket"] == b]) for b in arms}
    if v_stats["big_buy"] and v_stats["big_sell"]:
        print("  T+1 全天均价口径: big_buy %+.2f%% vs big_sell %+.2f%%  diff %+.2f pct"
              % (v_stats["big_buy"]["mean"], v_stats["big_sell"]["mean"],
                 v_stats["big_buy"]["mean"] - v_stats["big_sell"]["mean"]))

    # ── 复利净值（open1，事件不重叠）──
    cal = sorted({s["date"] for s in samples})
    ci = {d: i for i, d in enumerate(cal)}
    for b in ("big_buy", "big_sell"):
        rows = sorted([s for s in samples if s["bucket"] == b and s.get("open1") is not None],
                      key=lambda x: x["date"])
        net, used, last_exit = 1.0, 0, -1
        for s in rows:
            i = ci.get(s["date"], -1)
            if i < 0 or i <= last_exit:
                continue
            net *= (1.0 + (s["open1"] - COST_PCT) / 100.0)
            used += 1
            last_exit = i + HOLD
        print("  复利净值（open1，不重叠，扣成本）: %-9s %+8.2f%%（%d 事件）"
              % (b, (net - 1.0) * 100.0, used))
    print()

    flag = "多头可用" if long_ok else "仅空头/回避"
    emit(verdict, len(samples), "[源=%s 覆盖%.0f%%｜%s] %s"
         % (source, cov * 100, flag, detail))


if __name__ == "__main__":
    main()
