#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】事件情景复盘：FOMC 决议落地前后 A 股走势规律（总结过去）
================================================================================

背景（2026-09-15，用户需求）：市场处于加息前夕、阴跌观望期，需要"总结过去
  类似情景 → 支撑当下备案"，落地时不慌乱、不迷茫、能识别并跟随主力建仓信号。

★ 日期口径（关键，务必先读；2026-09-15 修正过一次标签偏移）：
  美东周三 14:00 公布决议 → 北京周四凌晨 02:00 → A股周四开盘反应。
  本脚本以 **A股反应日 = D0**（北京决议日当天或之后第一个交易日）为锚：
    D-1 = D0 前一个交易日（**决议前最后交易日**，A股收盘时决议尚未公布）
    D0  = 决议后第一个交易日（A股对决议的反应日）
    D+h = 反应日后 h 个交易日
  收益基准 = **D-1 收盘**；D0 收益即"决议反应日当天涨跌幅"。

方法：用 backtest_prices 沪深300 日线，回溯 2023-08 以来 24 次 FOMC
  （北京时间决议日，美联储官网核实 = 美东决议日+1）前后走势，
  按「决议前（D-1）市场状态 regime」分组统计。

★ 为什么按 regime 分组、不按"加息/降息"分组：
  ① regime 完全由指数计算，不依赖外部数据（本项目无历史美债序列）；
  ② 用户真正关心的是"当前处境（阴跌观望）下历史怎么走"，regime 更贴合。

★ 样本诚实说明：
  · 指数 3 年（2023-08-23~今）覆盖 **24 次** FOMC → 分组统计可靠；
  · 主力资金流 mainflow_history 仅半年（2026-03~今）只覆盖 **4 次** →
     单独列出、明确标注样本少，仅作方向性参考。

用法：
  python scripts/event_playbook_backtest.py
================================================================================
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

BACKEND = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
sys.path.insert(0, BACKEND)
_env = os.path.join(BACKEND, ".env")
if os.path.exists(_env):
    for _l in open(_env, encoding="utf-8"):
        _l = _l.strip()
        if _l and not _l.startswith("#") and "=" in _l:
            _k, _v = _l.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.database import db                                    # noqa: E402
from app.backtest.market_regime import detect_market_regime    # noqa: E402

# ── FOMC 决议公布日（北京时间 = 美东决议日 +1，美联储官网核实）──
# 仅纳入 ≥2023-08-23（沪深300 数据起点）；2026-09-17 为本次（未来，不纳入）
FOMC_BJ = [
    "2023-09-21", "2023-11-02", "2023-12-14",
    "2024-02-01", "2024-03-21", "2024-05-02", "2024-06-13", "2024-08-01",
    "2024-09-19", "2024-11-08", "2024-12-19",
    "2025-01-30", "2025-03-20", "2025-05-08", "2025-06-19", "2025-07-31",
    "2025-09-18", "2025-10-30", "2025-12-11",
    "2026-01-29", "2026-03-19", "2026-04-30", "2026-06-18", "2026-07-30",
]
HORIZONS = [0, 1, 3, 5, 10, 20]      # D0, D+1, ...
REGIME_ZH = {"offensive": "进攻", "neutral": "震荡", "defensive": "防御",
             "neutral_bearish": "震荡偏空"}


def _load_index() -> list:
    # detect_market_regime 需要 open/high/low/close/volume 全字段
    return db.fetch(
        "SELECT date, open, high, low, close, volume FROM backtest_prices "
        "WHERE code='sh000300' ORDER BY date ASC"
    ) or []


def _flow_by_date() -> dict:
    """全池主力净流入合计（亿元）按日。样本仅近半年。"""
    try:
        rows = db.fetch(
            "SELECT date, SUM(main_net) AS net, COUNT(*) AS n FROM mainflow_history "
            "GROUP BY date ORDER BY date ASC") or []
        return {str(r["date"]): float(r["net"] or 0) / 1e8 for r in rows}
    except Exception as e:
        print(f"[警告] mainflow_history 读取失败: {e}")
        return {}


def _stats(vals: list) -> dict:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    n = len(vals)
    return {"n": n, "avg": round(sum(vals) / n, 2),
            "win_rate": round(sum(1 for v in vals if v > 0) / n * 100, 1),
            "best": round(max(vals), 2), "worst": round(min(vals), 2)}


def main() -> int:
    bars = _load_index()
    if len(bars) < 100:
        print("沪深300 数据不足（<100 根），无法复盘")
        return 1
    dates = [str(b["date"]) for b in bars]
    close = {str(b["date"]): float(b["close"]) for b in bars}
    idx_of = {d: i for i, d in enumerate(dates)}
    state_map = {str(s.date): s.state for s in detect_market_regime(bars)}
    flow = _flow_by_date()

    print("=" * 76)
    print(f"FOMC 决议落地前后 A 股走势复盘（沪深300，{dates[0]} ~ {dates[-1]}）")
    print("口径：D0 = 决议后 A 股第一个交易日（反应日）；收益基准 = D-1 收盘")
    print("=" * 76)

    events = []
    for ed in FOMC_BJ:
        if ed < dates[0]:
            continue
        d0 = next((d for d in dates if d >= ed), None)   # D0 = 反应日
        if not d0 or idx_of[d0] < 20:
            continue
        i0 = idx_of[d0]
        i_prev = i0 - 1                                   # D-1 = 决议前最后交易日
        base = close[dates[i_prev]]
        rec = {"event": ed, "d0": d0, "prev": dates[i_prev],
               "regime": state_map.get(dates[i_prev], "?"),
               "pre20": round((base / close[dates[i_prev - 19]] - 1) * 100, 2)}
        for h in HORIZONS:
            j = i0 + h
            rec["ret%d" % h] = (round((close[dates[j]] / base - 1) * 100, 2)
                                if j < len(dates) else None)
        events.append(rec)
    print(f"\n有效事件 {len(events)} 次（基准 = D-1 收盘 {events[0]['prev']} 起）")

    # ── 明细 ──
    print(f"\n{'决议公布(京)':<13}{'D0(反应日)':<13}{'D-1状态':>8}{'前20日':>8}"
          f"{'D0':>8}{'D+1':>8}{'D+3':>8}{'D+5':>8}{'D+10':>8}{'D+20':>8}")
    print("-" * 90)
    for r in events:
        cells = ""
        for h in HORIZONS:
            v = r["ret%d" % h]
            cells += ("       —" if v is None else "%+8.2f" % v)
        print(f"{r['event']:<13}{r['d0']:<13}"
              f"{REGIME_ZH.get(r['regime'], r['regime']):>8}"
              f"{r['pre20']:>+8.2f}{cells}")

    # ── 按决议前市场状态分组 ──
    print("\n【按「决议前(D-1)市场状态」分组统计】")
    for state in ("neutral", "offensive", "defensive"):
        grp = [r for r in events if r["regime"] == state]
        if not grp:
            continue
        print(f"\n  [{REGIME_ZH.get(state, state)}] n={len(grp)}"
              f"{'  ← 当前处境（neutral_bearish 归属此档）' if state == 'neutral' else ''}")
        for h in HORIZONS:
            st = _stats([r["ret%d" % h] for r in grp])
            if st["n"]:
                tag = "D0 " if h == 0 else "D+%-2d" % h
                print(f"    {tag} 均值 {st['avg']:+.2f}%  上涨 {st['win_rate']:.0f}%  "
                      f"[{st['worst']:+.2f}% ~ {st['best']:+.2f}%]")

    # ── 最像"现在"的子集 ──
    print("\n【最像当前处境：D-1=震荡 且 前20日累积下跌（阴跌观望）】")
    like_now = [r for r in events
                if r["regime"] == "neutral" and (r["pre20"] or 0) < 0]
    if like_now:
        print("  n=%d：%s" % (len(like_now), "、".join(r["event"] for r in like_now)))
        for h in HORIZONS:
            st = _stats([r["ret%d" % h] for r in like_now])
            if st["n"]:
                tag = "D0 " if h == 0 else "D+%-2d" % h
                print(f"    {tag} 均值 {st['avg']:+.2f}%  上涨 {st['win_rate']:.0f}%  "
                      f"[{st['worst']:+.2f}% ~ {st['best']:+.2f}%]")
    else:
        print("  无匹配样本")

    # ── 主力资金流（以 D0 为锚！样本少，单列）──
    print("\n【主力资金流：以 D0（反应日）为锚的逐日净流入】")
    print("  （mainflow_history 仅半年 → 只覆盖近 4 次；D0=决议后第一个交易日）")
    covered = [r for r in events if r["d0"] in flow or r["prev"] in flow]
    if not covered:
        print("  区间内无资金流覆盖的事件")
    else:
        print(f"  {'决议公布日':<13}{'D-1':>10}{'D0':>10}{'D+1':>10}{'D+2':>10}{'D+3':>10}")
        for r in covered:
            i0 = idx_of[r["d0"]]
            cells = ""
            for k in (-1, 0, 1, 2, 3):
                j = i0 + k
                v = flow.get(dates[j]) if 0 <= j < len(dates) else None
                cells += ("%10s" % ("—" if v is None else "%+.0f亿" % v))
            print(f"  {r['event']:<13}{cells}")
        print("  ★ 净流入 = 评分池内合计（非全市场）；样本 4 次，仅作方向参考")

    print("\n【结论判读】")
    neu = [r for r in events if r["regime"] == "neutral"]
    if neu:
        st0 = _stats([r["ret0"] for r in neu])
        st5 = _stats([r["ret5"] for r in neu])
        print(f"  震荡市（≈当前）{len(neu)} 次：反应日 D0 均值 {st0['avg']:+.2f}%、"
              f"上涨 {st0['win_rate']:.0f}%；D+5 均值 {st5['avg']:+.2f}%、上涨 {st5['win_rate']:.0f}%")
    print("  注：样本有限、且含加息/降息/按兵不动混合情景；用于「心里有数」，")
    print("      不构成方向预测。备案脚本再结合当前宏观实值细化。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
