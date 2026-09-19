#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】宽度崩塌钩子「范围扩展」评估：把趋势类保护扩到震荡类，值不值？
================================================================================
背景（2026-09-20）：
  `strategy_decay_diagnosis.py` 定位到 `ma_convergence_breakout` / `ma_pullback` 的
  崩坏拐点 = **2026-09-07/08**，而崩坏的正是**震荡类**战法；而
  `contradictions/risk_hook.py` 的 `TREND_STRATEGIES` **只保护趋势类** ⇒
  **震荡类在普跌市完全裸奔**。
  更关键的是：09-08 当天 `market_regime_history` 标注的是 `neutral`（不是 bearish），
  却恰是最差的一天之一 ⇒ 指数 regime（MA/ADX，滞后）**看不见个股普跌**，
  而宽度崩塌钩子本就是为"普跌日"设计的，**比 regime 更对症**。

要回答的问题：**把钩子范围从趋势类扩展到震荡类，有没有正期望？**
  —— 不能只凭"拐点日吻合"就改生产行为（项目铁律：改行为先回测）。

方法（复用生产逐笔管线，不另起一套）：
  `load_trades()` 直接复用 `strategy_decay_diagnosis`（读 strategy_results → 主力闸门
  → 退出策略 v2 → `engine.match_signals` 撮合），本脚本只做**分组**：
    1. 重建钩子历史：对每个扫描日 d，钩子生效 ⟺ `contradictions` 在 **[d-7, d)** 内
       存在 severe 的宽度类矛盾（与 `risk_hook.breadth_collapse_active` 同判据、
       同 `_MAX_AGE_DAYS=7`、同样"date < 今天"）——**判据同源，不自己发明**；
    2. 逐笔按「战法类别（趋势/震荡）× 钩子是否生效」四格分组，输出 n / 胜率 / 均收益 /
       盈亏比；
    3. **判定标准预先写死**（防事后挑格子，见 `verdict()`）。

零 egress：DATA_SOURCE=local（K 线走本机数据包），只回源
  strategy_results / mainfactors / market_regime_history / contradictions 四张表。

用法：
  python scripts/breadth_hook_review.py
  python scripts/breadth_hook_review.py --min-n 30
================================================================================
"""
import argparse
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from strategy_decay_diagnosis import db, load_trades            # noqa: E402
from app.contradictions.risk_hook import (                       # noqa: E402
    TREND_STRATEGIES, _BREADTH_TYPES, _MAX_AGE_DAYS)
from app.strategies.recommendation import STRATEGY_ZH            # noqa: E402


def load_severe_days():
    """所有 severe 宽度类矛盾的日期（升序，YYYY-MM-DD）—— 钩子的触发源。"""
    rows = db.fetch(
        "SELECT DISTINCT date FROM contradictions "
        "WHERE type IN (%s, %s) AND severity = 'severe' ORDER BY date",
        (_BREADTH_TYPES[0], _BREADTH_TYPES[1]))
    return sorted({str(r["date"])[:10] for r in (rows or [])})


def hook_active_map(scan_days, severe_days):
    """{扫描日: 钩子是否生效} —— 复刻 `risk_hook.breadth_collapse_active` 的判据。

    生效 ⟺ 存在 severe 日 s 满足 `d-7 <= s < d`（**严格小于 d**：
    钩子在"次日全天"生效，当日自己不算）。
    """
    out = {}
    for d in scan_days:
        lo = (datetime.strptime(d, "%Y-%m-%d")
              - timedelta(days=_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
        out[d] = any(lo <= s < d for s in severe_days)
    return out


def _stat(pnl):
    n = len(pnl)
    if not n:
        return None
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) else None
    return {"n": n, "win": len(wins) / n * 100, "avg": sum(pnl) / n,
            "pf": pf}


def _fmt(s):
    if not s:
        return "n=0"
    return (f"n={s['n']:<4} 胜率{s['win']:>5.1f}% 均{s['avg']:>+6.2f}% "
            f"PF{(s['pf'] if s['pf'] is not None else float('nan')):>5.2f}")


def verdict(a, b, min_n):
    """**预先写死的判定标准**（防事后挑格子）。

    a = 钩子生效日的样本（若扩展范围**会被拦掉**的那批），b = 非生效日样本。
    返回 (结论标签, 说明)。
    """
    if not a or not b:
        return "样本不足", "任一组为空 ⇒ 无法比较"
    if a["n"] < min_n:
        return "样本不足", f"A 组 n={a['n']} < {min_n}（标准预先要求）"
    diff = b["avg"] - a["avg"]        # 正数 = A 组确实更差
    if a["avg"] > b["avg"]:
        return "扩展有害", (f"A 组均收益 {a['avg']:+.2f}% **优于** B 组 {b['avg']:+.2f}%"
                          f" ⇒ 拦掉 A 会**损失**收益，明确否决扩展")
    if diff >= 1.0:
        return "扩展有价值", (f"A 组比 B 组差 {diff:.2f}pt（≥1.0 门槛）"
                            f" ⇒ 拦掉 A 能抬升整体，建议扩展范围")
    return "证据不足", (f"A 组仅比 B 组差 {diff:.2f}pt（< 1.0 门槛）"
                      f" ⇒ 不足以支撑改生产行为，维持现状")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-n", type=int, default=30,
                    help="A 组最少笔数（低于此判「样本不足」，默认 30）")
    args = ap.parse_args()

    trades, strat_of = load_trades()
    if not trades:
        print("无撮合结果（信号为空 / 全被闸门过滤 / K线不可用）")
        return 1

    severe_days = load_severe_days()
    scan_days = sorted({str(t.get("signal_date"))[:10] for t in trades})
    hooks = hook_active_map(scan_days, severe_days)

    print("=" * 94)
    print(f"宽度崩塌钩子 · 范围扩展评估   逐笔 {len(trades)} 笔 / 扫描日 {len(scan_days)} 个")
    print(f"severe 宽度崩塌日 {len(severe_days)} 天：{severe_days or '（无）'}")
    active_days = [d for d in scan_days if hooks[d]]
    print(f"钩子生效的扫描日 {len(active_days)} 天：{active_days or '（无）'}")
    print(f"⚠️ 生效判据 = [d-{_MAX_AGE_DAYS}, d) 内有 severe（同 risk_hook，不自己发明）")
    if not active_days:
        # ★ 2026-09-20 实测就是这种情况：severe 日与扫描日**错配** ⇒ 评估不可行。
        #   必须显著提示，否则容易被误读成"钩子起了作用但没效果"。
        print("\n" + "!" * 94)
        print("⚠️ 钩子生效日为 **0** ⇒ **本次评估不可行**（A 组无样本）。可能原因：")
        print(f"  · severe 日 {severe_days or '（无）'} 落在扫描日范围 "
              f"[{scan_days[0]} ~ {scan_days[-1]}] 之外/之后；")
        print("  · 或 `strategy_results` 扫描断档（缺日）⇒ 先查 daily_batch 的 "
              "strategy_scan 是否正常写库；")
        print("  · 注：这**不代表钩子配置错误** —— 判据正常，只是恰好没被触发过。")
        print("!" * 94)
    print("=" * 94)

    groups = defaultdict(list)
    for t in trades:
        st = (t.get("strategy")
              or strat_of.get((t.get("code"), t.get("signal_date")), "?"))
        cls = "趋势类" if st in TREND_STRATEGIES else "震荡类"
        d = str(t.get("signal_date"))[:10]
        groups[(cls, bool(hooks.get(d)))].append(float(t.get("pnl_pct") or 0))

    print(f"\n{'类别':<8}{'钩子':<8}{'说明':<34}统计")
    print("-" * 94)
    stats = {}
    for cls, hooked in (("趋势类", False), ("趋势类", True),
                        ("震荡类", False), ("震荡类", True)):
        s = _stat(groups.get((cls, hooked), []))
        stats[(cls, hooked)] = s
        note = ("现行钩子保护范围（已在生产启用）" if cls == "趋势类" and hooked
                else "**扩展后会被拦掉**" if hooked else "不受钩子影响")
        print(f"{cls:<8}{'生效' if hooked else '不生效':<8}{note:<34}{_fmt(s)}")

    print("\n" + "=" * 94)
    print("判定（标准预先写死，见模块 docstring）")
    print("=" * 94)
    results = []
    for cls in ("趋势类", "震荡类"):
        a, b = stats.get((cls, True)), stats.get((cls, False))
        tag, why = verdict(a, b, args.min_n)
        results.append((cls, tag))
        star = " ★★" if cls == "震荡类" else "（现行范围，作对照）"
        print(f"\n【{cls}】{star} → **{tag}**")
        print(f"  A（钩子生效）{_fmt(a)}")
        print(f"  B（非生效）  {_fmt(b)}")
        print(f"  {why}")

    # 逐战法细看（仅震荡类，附最大差者）
    print("\n" + "-" * 94)
    print("附：震荡类逐战法（A/B 均收益差 ≥1.0pt 且两组各 ≥15 笔才列出）")
    by_st = defaultdict(lambda: defaultdict(list))
    for t in trades:
        st = (t.get("strategy")
              or strat_of.get((t.get("code"), t.get("signal_date")), "?"))
        if st in TREND_STRATEGIES:
            continue
        d = str(t.get("signal_date"))[:10]
        by_st[st][bool(hooks.get(d))].append(float(t.get("pnl_pct") or 0))
    shown = 0
    for st in sorted(by_st, key=lambda k: -len(by_st[k].get(True, []))):
        a, b = _stat(by_st[st].get(True, [])), _stat(by_st[st].get(False, []))
        if not a or not b or a["n"] < 15 or b["n"] < 15:
            continue
        diff = b["avg"] - a["avg"]
        mark = "← 差≥1.0pt" if diff >= 1.0 else ""
        print(f"  {st:<28}{STRATEGY_ZH.get(st, ''):<12}"
              f"A {_fmt(a)} | B {_fmt(b)} 差{diff:>+5.2f}pt {mark}")
        shown += 1
    if not shown:
        print("  （无战法满足列出条件）")

    print("\n注：样本仍在校准（断档 4 天刚恢复、severe 日样本少）⇒ 结论需在样本增长后复核；"
          "本脚本可重复运行，判定门槛固定不变。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
