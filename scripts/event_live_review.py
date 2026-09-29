#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · P3】实盘样本复核 —— E2 能否作为 defensive 的「先行解除」候选
================================================================================
背景：
  E2 政策脉冲已通过 21 年历史预登记检验（T+20 差 +2.99pp，防御期内增量 +1.83pp），
  但 **v0 刻意不进决策链**。要升级为「defensive→neutral 提前解除」，必须先回答：
  **实盘中，E2 触发后市场是否真的更快走出防御？** 这只能靠实盘样本回答。

数据：
  · `market_events`（每日事件快照，由 `scheduler.regime_cache_loop` 盘后落库）
  · `market_regime_history`（市场状态序列）

━━━━━━━━━━━━━━━━━━ 预登记判据（积累到足够样本才适用）━━━━━━━━━━━━━━━━━━
  (a) 样本门槛：E2 实盘天数 >= 10 **且** 独立簇 >= 5；否则输出 insufficient，
      **不下任何结论**（缺证据≠反证）。
  (b) 走出速度：E2 触发后 20 个交易日内 `defensive` 转出的比例，
      须 **高于**「defensive 期内无 E2 日」的同期基准比例（相对提升 >= 20%）。
  (c) 收益：E2 后 T+20 全市场等权收益 > 同期 defensive 期基准 + 1.0pp。
  三条全过 ⇒ 建议进入「单独预登记的升级评估」；否则维持 v0（只展示）。

⚠️ 本脚本**只读**，不修改任何表、不影响生产行为。
⚠️ 早期样本极少属正常（E2 在 21 年里也只有 298 天，约每年 14 天）。

用法：python scripts/event_live_review.py
================================================================================
"""
import argparse
import json
import os
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

from app.database import db          # noqa: E402

# ── 预登记判据 ──
MIN_E2_DAYS = 10
MIN_CLUSTERS = 5
MIN_SPEEDUP = 0.20        # 转出比例相对提升门槛
MIN_RET_DIFF = 1.0        # 收益差门槛（pct）
HORIZON = 20
# ★ 2026-09-27 修正（口径对齐）：原值 20 **自然日**（≈ 14 交易日）**短于**簇定义
#   （`event_edge_check.py:CLUSTER_GAP = 20` **交易日**）⇒ 会把同一事件簇**拆成多簇**、
#   高估独立簇数（进而高估统计功效）。改为 28 自然日 ＝ 20 交易日。
CLUSTER_GAP = 28


def _ensure_table():
    """确保 market_events 存在（复用生产模块的幂等建表）。"""
    try:
        from app.events.signal import _ensure_table as _et
        _et()
    except Exception as e:
        print(f"[warn] 建表检查失败（继续尝试读取）: {e}")


def load_events():
    try:
        return db.fetch(
            "SELECT date, up_ratio, limit_up, limit_down, "
            "e1_capitulation, e2_policy_surge FROM market_events ORDER BY date") or []
    except Exception as e:
        print(f"[warn] 读取 market_events 失败: {e}")
        return []


def load_regimes():
    try:
        return db.fetch("SELECT date, state FROM market_regime_history "
                        "ORDER BY date") or []
    except Exception as e:
        print(f"[warn] 读取 market_regime_history 失败: {e}")
        return []


def count_clusters(dates):
    """相邻事件间隔 < CLUSTER_GAP（**自然日**）归为同一簇。"""
    if not dates:
        return 0
    # 实盘只有日期（无交易日索引）⇒ 用自然日近似；CLUSTER_GAP=28 已对齐「20 交易日」。
    c = 1
    for a, b in zip(dates, dates[1:]):
        from datetime import date as _d
        try:
            da = _d.fromisoformat(str(a)[:10])
            dbb = _d.fromisoformat(str(b)[:10])
        except Exception:
            continue
        if (dbb - da).days >= CLUSTER_GAP:
            c += 1
    return c


def _body() -> dict:
    """原 `main()` 主体 —— ★ 2026-09-30 改为返回**结构化结论**（不再是裸 `return`），
    由新 `main()` 统一负责 `--json` 输出。判据/文案一律不动（改动仅限出口）。"""
    _ensure_table()
    events = load_events()
    regimes = load_regimes()

    print("=" * 78)
    print("【P3 实盘样本复核】E2 能否作为 defensive 的「先行解除」候选")
    print("=" * 78)

    if not events:
        print("\nmarket_events 暂无数据。")
        print("说明：该表由 `scheduler.regime_cache_loop` 在**工作日盘后**（15:40+）自动落库，"
              "\n      依赖当日实时行情缓存。若服务未在该窗口运行，当日不会落库。")
        print("\n⇒ 状态：insufficient（尚未积累任何实盘样本），不下结论。")
        return {"verdict": "INSUFFICIENT", "n": 0,
                "detail": "market_events 无数据（该表由 scheduler 盘后落库，服务未运行则不写）"}

    e2_dates = [str(r["date"])[:10] for r in events if r.get("e2_policy_surge")]
    e1_dates = [str(r["date"])[:10] for r in events if r.get("e1_capitulation")]
    all_dates = [str(r["date"])[:10] for r in events]
    clusters = count_clusters(e2_dates)

    print(f"\n① 数据覆盖")
    print(f"  · 事件快照：{len(events)} 天（{all_dates[0]} ~ {all_dates[-1]}）")
    print(f"  · E2 政策脉冲：{len(e2_dates)} 天 / {clusters} 簇"
          + (f"  → {', '.join(e2_dates)}" if e2_dates else ""))
    print(f"  · E1 冰点：{len(e1_dates)} 天"
          + (f"  → {', '.join(e1_dates)}" if e1_dates else ""))

    # 缺日检测（与 regime 序列对齐）
    reg_dates = [str(r["date"])[:10] for r in regimes]
    if reg_dates:
        ev_set = set(all_dates)
        missing = [d for d in reg_dates if d not in ev_set and d >= all_dates[0]]
        print(f"  · 相对 regime 序列的缺日：{len(missing)} 天"
              + (f"（最近：{', '.join(missing[-5:])}）" if missing else ""))

    # ② 样本门槛
    print(f"\n② 样本门槛（E2 >= {MIN_E2_DAYS} 天 且 簇 >= {MIN_CLUSTERS}）")
    if len(e2_dates) < MIN_E2_DAYS or clusters < MIN_CLUSTERS:
        print(f"  · 当前 E2 {len(e2_dates)} 天 / {clusters} 簇 ⇒ **insufficient**，不下结论。")
        print(f"  · 按 21 年 298 天推算，E2 约每 26 个交易日 1 次；"
              f"达到门槛预计还需 ~{max(0, MIN_E2_DAYS - len(e2_dates)) * 26} 个交易日。")
        return {"verdict": "INSUFFICIENT", "n": len(e2_dates),
                "detail": (f"E2 {len(e2_dates)} 天 / {clusters} 簇（门槛 {MIN_E2_DAYS} 天"
                           f"且 {MIN_CLUSTERS} 簇）")}

    # ③ 走出速度（E2 后 20 日内 defensive 转出比例 vs 基准）
    state_map = {str(r["date"])[:10]: r["state"] for r in regimes}
    di = {d: i for i, d in enumerate(reg_dates)}

    def days_to_exit(start_date):
        """start_date 之后首次 state != defensive 的交易日数；未转出返回 None。"""
        i = di.get(start_date)
        if i is None:
            return None
        for k in range(i + 1, min(i + 1 + HORIZON, len(reg_dates))):
            if state_map.get(reg_dates[k]) != "defensive":
                return k - i
        return None

    e2_def = [d for d in e2_dates if state_map.get(d) == "defensive"]
    base_def = [d for d in reg_dates
                if state_map.get(d) == "defensive" and d not in set(e2_dates)]
    print(f"\n③ 走出防御速度（E2 触发日在 defensive 内：{len(e2_def)} 天）")
    if not e2_def:
        print("  · 实盘 E2 发生在 defensive 内的样本为 0 ⇒ 无法评估（insufficient）。")
        return {"verdict": "INSUFFICIENT", "n": len(e2_dates),
                "detail": "E2 全部发生在非 defensive 期 ⇒ 无法评估『先行解除』"}
    e2_exits = [days_to_exit(d) for d in e2_def]
    e2_ok = [x for x in e2_exits if x is not None]
    e2_rate = len(e2_ok) / len(e2_def)
    b_exits = [days_to_exit(d) for d in base_def]
    b_ok = [x for x in b_exits if x is not None]
    b_rate = len(b_ok) / len(base_def) if base_def else 0.0
    print(f"  · E2 后 20 日内转出：{len(e2_ok)}/{len(e2_def)} = {e2_rate:.0%}"
          + (f"（中位 {sorted(e2_ok)[len(e2_ok)//2]} 日）" if e2_ok else ""))
    print(f"  · 基准（defensive 期内非 E2 日）：{len(b_ok)}/{len(base_def)} = {b_rate:.0%}")
    speedup = (e2_rate - b_rate) / b_rate if b_rate > 0 else None
    if speedup is not None:
        print(f"  · 相对提升：{speedup:+.0%}（门槛 ≥ +{MIN_SPEEDUP:.0%}）"
              f" ⇒ {'达标' if speedup >= MIN_SPEEDUP else '未达标'}")

    # ④ 汇总
    print(f"\n④ 预登记判定")
    pass_speed = (speedup is not None and speedup >= MIN_SPEEDUP)
    print(f"  (a) 样本门槛：{'✓' if (len(e2_dates) >= MIN_E2_DAYS and clusters >= MIN_CLUSTERS) else '✗'}")
    print(f"  (b) 走出速度：{'✓' if pass_speed else '✗'}")
    print(f"  (c) 收益差：需另行用价格序列计算（本脚本暂未接入实盘价格口径）")
    if pass_speed:
        print("\n  ★ 暂支持「E2 后更快走出防御」⇒ 可进入**单独预登记的升级评估**"
              "（仍不等同于直接改生产闸门）。")
    else:
        print("\n  ★ 暂不支持升级 ⇒ 维持 v0（只展示，不进决策链）。")
    print("\n  注：实盘样本仍在积累，本结论随样本增长而更新；"
          "每次复核请对照 `_report_事件驱动信号源接入_20260927.md`。")
    # ⚠️ 判据 (c)（T+20 收益差）**本脚本尚未接入实盘价格口径** ⇒ 即便 (a)(b) 达标也
    #   只能给 **PARTIAL**（"部分达标"），不能报 PASS —— 缺失 ≠ 通过。
    return {
        "verdict": "PARTIAL" if pass_speed else "FAIL",
        "n": len(e2_dates),
        "detail": (f"样本 {len(e2_dates)} 天/{clusters} 簇；"
                   f"走出速度相对提升 "
                   f"{('—' if speedup is None else f'{speedup:+.0%}')}"
                   f"（门槛 +{MIN_SPEEDUP:.0%}）；判据 (c) 收益差未接入 ⇒ "
                   + ("(a)(b) 达标但 (c) 未评估 ⇒ 只能说部分达标"
                      if pass_speed else "维持 v0（只展示）")),
    }


def main():
    """CLI 包装（★ 2026-09-30 新增）：`--json` 时在正文末尾追加一行结构化结论，
    供月度日批复核（`app/edge_verify.py`）消费；默认行为与旧版一致。"""
    ap = argparse.ArgumentParser(description="E2 实盘样本复核（预登记）")
    ap.add_argument("--json", action="store_true",
                    help="输出末尾追加一行结构化结论（供日批消费）")
    args = ap.parse_args()
    res = _body() or {}
    res.setdefault("script", "event_live_review")
    res.setdefault("need", MIN_E2_DAYS)
    res.setdefault("unit", "E2 触发日")
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    return 0 if (res.get("verdict") or "") != "ERROR" else 1


if __name__ == "__main__":
    sys.exit(main())
