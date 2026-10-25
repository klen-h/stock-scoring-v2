#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】H3 市况锚双口径 —— 双锚并跑对比（机制指标，非收益指标）
================================================================================

判据（docs/plans/20261026_评分分市况重构方案_v0.md §三 H3 / §7 v1.0 冻结）：
  · 双锚：market_regime（沪深300 锚，现行评分权重切换挂点） vs eq_anchor
    （全池等权锚=与评分基准池同源，中位口径客观分段 → 映射 offensive/neutral/
    defensive）。
  · 指标①不一致率 = 交集日中映射态 ≠ regime 态的天数占比；
    指标②方向错配率 = 双方均为明确方向（offensive/defensive）且互斥的天数
      / 双方均有明确方向的天数（offensive↔defensive 直接对撞，最伤权重切换）。
  · 判定：交集 ≥60 日 且 方向错配率 ≥30% ⇒ PASS（=「切锚必要性成立」，
    仅机制结论，切锚仍须用户拍板）；交集不足 ⇒ INSUFFICIENT（继续并跑）。
  · 无 KILL 语义：H3 是持续跟踪的机制指标（错配率回落属正常波动，不判死）。

数据（全 PG）：market_regime_history + eq_anchor_history。
用法：python scripts/eq_anchor_check.py [--json]
================================================================================
"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

MIN_DAYS = 60          # 交集样本闸
MISMATCH_TH = 0.30     # 方向错配率 ≥30% ⇒ 切锚必要性成立
DIRECTIONAL = {"offensive", "defensive"}


def load_anchors():
    from app.database import db
    from app.backtest.eq_anchor import REGIME_MAP
    eq_rows = db.fetch(
        "SELECT anchor_date, state FROM eq_anchor_history "
        "WHERE state IS NOT NULL ORDER BY anchor_date") or []
    eq = {str(r["anchor_date"])[:10]: REGIME_MAP.get(r["state"])
          for r in eq_rows}
    rg_rows = db.fetch(
        "SELECT date, state FROM market_regime_history "
        "WHERE state IS NOT NULL ORDER BY date") or []
    rg = {str(r["date"])[:10]: r["state"] for r in rg_rows}
    return eq, rg


def compute(eq, rg):
    common = sorted(set(eq) & set(rg))
    mismatch = [d for d in common if eq[d] != rg[d]]
    both_dir = [d for d in common
                if eq.get(d) in DIRECTIONAL and rg.get(d) in DIRECTIONAL]
    clash = [d for d in both_dir if eq[d] != rg[d]]
    recent60 = common[-60:]
    r_mis = [d for d in recent60 if eq[d] != rg[d]]
    r_dir = [d for d in recent60
             if eq.get(d) in DIRECTIONAL and rg.get(d) in DIRECTIONAL]
    r_clash = [d for d in r_dir if eq[d] != rg[d]]
    return {
        "n": len(common), "span": (common[0], common[-1]) if common else None,
        "mismatch": mismatch,
        "mismatch_rate": len(mismatch) / len(common) if common else None,
        "n_dir": len(both_dir),
        "clash_rate": len(clash) / len(both_dir) if both_dir else None,
        "recent60_mismatch_rate": (len(r_mis) / len(recent60)) if recent60 else None,
        "recent60_clash_rate": (len(r_clash) / len(r_dir)) if r_dir else None,
        "recent60_clash_days": r_clash,
        "recent_eq_states": sorted({eq[d] for d in recent60}),
        "recent_rg_states": sorted({rg[d] for d in recent60}),
    }


def fmt_pct(x):
    return f"{x:.0%}" if x is not None else "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    eq, rg = load_anchors()
    st = compute(eq, rg)
    print(f"双锚交集 {st['n']} 日（{st['span'][0]}~{st['span'][1]}）" if st["span"]
          else "双锚交集 0 日")
    print(f"  指标① 不一致率（映射态≠regime态）: {fmt_pct(st['mismatch_rate'])}"
          f"（{len(st['mismatch'])} 日）")
    print(f"  指标② 方向错配率（offensive↔defensive 对撞）: "
          f"{fmt_pct(st['clash_rate'])}（方向日 {st['n_dir']}）")
    print(f"  近 60 交集日：不一致率 {fmt_pct(st['recent60_mismatch_rate'])} / "
          f"错配率 {fmt_pct(st['recent60_clash_rate'])}；"
          f"等权锚态 {st['recent_eq_states']} vs 300锚态 {st['recent_rg_states']}")
    if st["recent60_clash_days"]:
        print(f"  近期对撞日: {', '.join(st['recent60_clash_days'][-8:])}")

    clash = st["clash_rate"]
    if st["n"] < MIN_DAYS:
        verdict, detail = "INSUFFICIENT", (
            f"交集 {st['n']} 日(需{MIN_DAYS})——双锚继续并跑")
    elif clash is not None and clash >= MISMATCH_TH:
        verdict, detail = "PASS", (
            f"切锚必要性成立：方向错配率 {clash:.0%} ≥ 30%"
            f"（交集 {st['n']} 日）——切评分权重挂点须用户拍板")
    else:
        verdict, detail = "PARTIAL", (
            f"错配率 {fmt_pct(clash)} < 30%——当前无切锚必要，继续并跑跟踪")
    obj = {"script": "eq_anchor_check", "verdict": verdict, "n": st["n"],
           "need": MIN_DAYS, "unit": "双锚交集日",
           "mismatch_rate": st["mismatch_rate"],
           "clash_rate": clash, "detail": detail}
    if args.json:
        print(json.dumps(obj, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
