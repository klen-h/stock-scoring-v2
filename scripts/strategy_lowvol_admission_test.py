#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】战法准入矩阵「阴跌子档」回测背书（PLAN P2② / Coach 上游前置）
================================================================================

背景：准入矩阵只有「震荡+高波只放行 morning_star/ma_pullback」一条波动子档，
      MA 向下的阴跌段全量放行震荡类。PLAN 原假设是"低波阴跌"（ATR 分位<20%），
      但实测 regime 序列显示：strategy_results 有史以来（20 个交易日）的信号
      **全部**落在 (neutral, normal, down)——低波子桶 0 样本，"低波"维度无法
      回测背书；可背书的是 **MA 向下（阴跌）维度**。故子档 key 定为
      (neutral, ma_trend=down)，低波只是其子集，未来有样本再细分。

方法（与生产白名单重放 _recompute_whitelist 完全同口径）：
  1. strategy_results 全部历史信号 → 信号日 regime 快照分桶（无前视）
  2. 现行基本准入过滤（进攻→趋势类、震荡→震荡类、防御→已禁）
  3. G 主力闸门 + 退出 v2（apply_exit_policy，止损基准=信号日收盘）+ 撮合
     （T+1 开盘 + 涨停一字剔除），_load_prices_map 传 start 限流
  4. 对阴跌桶模拟准入方案：W0 现行全放行 / W1 高波同款白名单 /
     W2 期望判据达标者 / W3 全禁，输出分战法明细

输出：backend/backtest_reports/strategy_lowvol_admission_YYYYMMDD.md
用法：python scripts/strategy_lowvol_admission_test.py
================================================================================
"""

import datetime as dt
import os
import sys
import time
from collections import defaultdict

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)
with open(os.path.join(BACKEND_DIR, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

from app.database import db                                    # noqa: E402
from app.backtest import engine                                # noqa: E402
from app.backtest import data as bt_data                       # noqa: E402
from app.backtest.market_regime import detect_market_regime    # noqa: E402
from app.backtest.strategies import (                          # noqa: E402
    WARFARE_HOLD_DAYS, _load_prices_map, apply_exit_policy, exit_policy,
)
from app.mainforce.gate import gate_states_for_signals         # noqa: E402
from app.strategies.market_regime import (                     # noqa: E402
    TRENDING_STRATEGIES, OSCILLATING_STRATEGIES,
)

_orig = db.fetch


def q(sql, params=None, retries=4):
    last = None
    for i in range(retries):
        try:
            return _orig(sql, params)
        except Exception as e:
            last = e
            try:
                db._reset_pg_conn()
            except Exception:
                pass
            time.sleep(1.5 * (i + 1))
    raise last


def load_signals() -> list:
    rows = q("SELECT strategy_name, scan_date, results_json FROM strategy_results "
             "WHERE count > 0 ORDER BY scan_date ASC")
    signals = []
    for r in rows or []:
        import json as _json
        try:
            items = _json.loads(r["results_json"] or "[]")
        except (ValueError, TypeError):
            continue
        for it in items or []:
            code = str(it.get("code") or "").strip()
            if len(code) != 6:
                continue
            signals.append({
                "date": str(r["scan_date"]), "code": code,
                "name": str(it.get("name") or code),
                "stop_loss": it.get("stop_loss"),
                "take_profit": it.get("target_price"),
                "strategy_en": r["strategy_name"],
            })
    return signals


def regime_series() -> dict:
    """沪深300 → {date: (state, ma_trend, volatility)}。"""
    bars = bt_data.load_prices("sh000300")
    snaps = detect_market_regime(bars)
    return {s.date: (s.state, s.ma_trend, s.volatility_regime) for s in snaps}


def lookup(regime_map: dict, date: str):
    d = dt.datetime.strptime(date, "%Y-%m-%d")
    for _ in range(10):
        key = d.strftime("%Y-%m-%d")
        if key in regime_map:
            return regime_map[key]
        d -= dt.timedelta(days=1)
    return None


def base_admit(strategy: str, state: str) -> bool:
    """现行基本准入：进攻→趋势类、震荡→震荡类、防御→禁。"""
    if state == "offensive":
        return strategy in TRENDING_STRATEGIES
    if state in ("neutral", "neutral_bearish"):
        return strategy in OSCILLATING_STRATEGIES
    return False


def stats_of(trades: list) -> dict:
    n = len(trades)
    if not n:
        return {"n": 0, "win": None, "avg": None, "pf": None}
    wins = [t for t in trades if t["pnl_pct"] > 0]
    gw = sum(t["pnl_pct"] for t in wins)
    gl = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0))
    return {"n": n, "win": round(len(wins) / n * 100, 1),
            "avg": round(sum(t["pnl_pct"] for t in trades) / n, 3),
            "pf": round(gw / gl, 2) if gl > 0 else 999.0}


def row(label, st: dict) -> str:
    if not st["n"]:
        return f"| {label} | 0 | — | — | — |"
    return f"| {label} | {st['n']} | {st['win']} | {st['avg']} | {st['pf']} |"


def main():
    t0 = time.time()
    signals = load_signals()
    print(f"signals: {len(signals)}")
    rmap = regime_series()
    dates = sorted(rmap)
    print(f"regime 覆盖 {dates[0]} ~ {dates[-1]}（{len(dates)} 交易日）")

    # 1) 信号 → 桶（现行基本准入过滤）
    tagged = []
    for s in signals:
        info = lookup(rmap, s["date"])
        if not info:
            continue
        state, ma_trend, vol = info
        if not base_admit(s["strategy_en"], state):
            continue
        tagged.append({**s, "state": state, "ma_trend": ma_trend, "vol": vol})

    # 2) 与生产同口径：G 闸门 + 退出 v2，全部一次性算好挂到信号上
    # ★ 2026-09-13 审查 P1-10：生产 `_recompute_whitelist` 的 G 闸门已解耦——
    #   跟随运行时开关 STRATEGY_MAINFORCE_GATE（gate._mode_on），不再挂靠
    #   exit_policy。此处同步，保证回测脚本与生产重放口径一致。
    from app.mainforce.gate import _mode_on   # noqa: E402
    gate_on = _mode_on()
    gate = {}
    if gate_on:
        try:
            gate = gate_states_for_signals(tagged)
        except Exception as e:
            print(f"[gate] 闸门状态计算失败（不过滤）: {e}")
    else:
        print(f"[gate] STRATEGY_MAINFORCE_GATE=off → 按生产口径跳过 G 闸门")
    start = min(s["date"] for s in tagged)
    codes = {s["code"] for s in tagged}
    prices_map = _load_prices_map(codes, start=start)
    prepared = []
    for s in tagged:
        # gate_on=False 时 gate 为空 dict → 默认 True（不过滤），与生产一致
        ok = (gate.get((s["code"], s["date"])) or {}).get("ok", True)
        base = None
        for b in prices_map.get(s["code"]) or []:
            if b["date"] <= s["date"]:
                base = b["close"]
            else:
                break
        v2 = apply_exit_policy(
            {**s, "direction": "long", "hold_days": WARFARE_HOLD_DAYS, "is_etf": False,
             "strategy": s["strategy_en"]}, base_close=base)
        prepared.append({**s, "gate_ok": ok, "v2": v2})

    def replay(subset) -> dict:
        kept = [s["v2"] for s in subset if s["gate_ok"]]
        return stats_of(engine.match_signals(kept, prices_map, skipped_out=[]))

    # 3) 分桶
    def seg(s):
        return f"{s['state']}|{s['vol']}|{s['ma_trend']}"

    buckets = defaultdict(list)
    for s in prepared:
        buckets[seg(s)].append(s)

    down = [s for s in prepared if s["state"] == "neutral" and s["ma_trend"] == "down"]
    down_days = sorted({s["date"] for s in down})

    report = ["# 战法准入矩阵：阴跌子档（MA 向下）回测背书", "",
              f"> 生成：{dt.datetime.now():%Y-%m-%d %H:%M} ｜ 信号 {len(signals)} 条，"
              f"基本准入过滤后 {len(prepared)} 条 ｜ regime 覆盖 {dates[0]}~{dates[-1]}",
              "> 口径：G 主力闸门 + 退出 v2 + T+1 开盘/涨停一字剔除，"
              "与 _recompute_whitelist 完全同源", ""]
    # 低波维度的诚实说明
    low_down = [s for s in prepared if s["vol"] == "low" and s["ma_trend"] == "down"]
    report.append(f"> **低波维度说明**：信号全史上 volatility=low 的阴跌信号 {len(low_down)} 条"
                  f"——PLAN 的“低波”假设无样本可背书；实测阴跌段波动率为 normal。"
                  f"子档因此 key 在 **ma_trend=down**（低波是其子集，有样本后再细分）。")
    report += [f"**阴跌桶 (neutral, *, down)**：{len(down)} 条信号 / {len(down_days)} 个交易日"
               f"（{down_days[0]}~{down_days[-1]}）" if down else "**阴跌桶：0 条**", ""]

    # 一、分桶概览
    report += ["## 一、分桶概览（state | volatility | ma_trend）", "",
               "| 桶 | 信号数 | 交易日数 | 成交n | 胜率% | 均收益% | 盈亏比 |",
               "|---|---|---|---|---|---|---|"]
    for k in sorted(buckets):
        ss = buckets[k]
        st = replay(ss)
        report.append(f"| {k} | {len(ss)} | {len({s['date'] for s in ss})} | "
                      f"{st['n']} | {st['win'] if st['win'] is not None else '—'} | "
                      f"{st['avg'] if st['avg'] is not None else '—'} | "
                      f"{st['pf'] if st['pf'] is not None else '—'} |")

    # 二、阴跌桶分战法明细（选白名单的依据）
    report += ["", "## 二、阴跌桶：分战法明细（G+v2 同口径）", "",
               "| 战法 | 成交n | 胜率% | 均收益% | 盈亏比 |", "|---|---|---|---|---|"]
    bys = defaultdict(list)
    for s in down:
        bys[s["strategy_en"]].append(s)
    per = {}
    for k in sorted(bys, key=lambda k: -len(bys[k])):
        st = replay(bys[k])
        per[k] = st
        report.append(row(k, st))

    # 三、准入方案对照
    w2 = [k for k, v in per.items()
          if (v["n"] or 0) >= 20 and (v["avg"] or 0) > 0 and (v["pf"] or 0) >= 1.2]
    report += ["", "## 三、阴跌桶：准入方案对照", "",
               "| 方案 | 成交n | 胜率% | 均收益% | 盈亏比 | 放行战法 |", "|---|---|---|---|---|---|"]
    for name, wl in [("W0 现行（全量震荡类）", None),
                     ("W1 高波同款 morning_star/ma_pullback", ["morning_star", "ma_pullback"]),
                     (f"W2 期望判据达标者 {w2}", w2),
                     ("W3 全禁", [])]:
        subset = down if wl is None else [s for s in down if s["strategy_en"] in wl]
        st = replay(subset)
        report.append(row(name, st) + f" | {','.join(wl) if wl else '（无）'} |")

    out_dir = os.path.join(BACKEND_DIR, "backtest_reports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"strategy_lowvol_admission_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(report) + "\n")
    print(f"报告: {out}  用时 {time.time()-t0:.0f}s")
    idx = report.index("## 三、阴跌桶：准入方案对照")
    print("\n".join(report[idx - 1:]))


if __name__ == "__main__":
    main()
