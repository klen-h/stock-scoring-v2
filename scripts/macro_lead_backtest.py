#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】外部冲击「领先性」验证：美债/美元的变化能否预示 A 股未来收益
================================================================================
要回答的问题（2026-09-20，用户提问引发）：
  「美联储加息这类全球流动性事件，**系统能否察觉出痕迹**？」
  —— 系统有传感器（`macro.py` 面板：美债/美元/纳指/日经…）且已接入三处
     （`regime._external_panic` / coach 仓位 / 日报外围段），但那些都是**当日反应**。
     真正的问题是：**它们有没有"领先性"** —— 即"美元/美债先动，A 股后动"？
     若有且显著，系统就能**前瞻**；若没有，它就只是"同时反应"，谈不上察觉痕迹。

方法（数据全本地，零 Supabase）：
  1. 读 `backend/data/macro_series.json`（东财回填的宏观日线，见 macro_series_backfill.py）
  2. 读 `backtest_prices` 的沪深300（sh000300）日线
  3. **领先性 IC**：Spearman corr( 宏观变量当日变化 , A 股 **未来 N 日**收益 )
       · N = 1 / 3 / 5 / 10 个 A 股交易日
       · **预期为负**（美元↑/美债收益率↑ → A 股承压）
       · 用"最近可用值"对齐（美债按美国交易日，与 A 股交易日取交集）
  4. **事件窗口**：指定事件日（如 2026-09-16 美联储加息）打印前后各 N 日的
     宏观变量与 A 股序列 —— 直观看"谁先动"。

★ 判定标准**预先写死**（防事后挑格子）：
  |IC| ≥ 0.10 且 n ≥ 100 ⇒ 该变量对 A 股有**有效领先性**（系统可前瞻）
  |IC| ≥ 0.05 且 n ≥ 100 ⇒ 弱领先（仅可作参考）
  否则 ⇒ 无领先性（系统只能"同时反应"，不能前瞻）

⚠️⚠️ **重大修正（2026-09-20 晚，`fake_lead_diagnosis.py` 实测）**：
  上面的判定是在 **same 口径**（宏观 t 日变化 vs A 股 t→t+N）下做的，而该口径有
  **系统性的时区前视偏差**，故**结论须降级**：
    `same` 用 A 股**当日**日期去索引宏观序列，而这些序列的『当日』波动大部分发生在
    A 股当日**收盘之后**（美股夜盘）⇒ 等于用『次日才可知的信息』预测未来 = 前视。
  改按 `lag1`（推到上一 A 股交易日 = A 股开盘前已知的可交易档）重算，实测：
    · NQ +0.1609 → **+0.0115**（塌陷 93%）
    · VX -0.1124 → **-0.0052**（塌陷 95%）
    · UDI -0.0972 → **-0.0230**（塌陷 76%）
  ⇒ **外部因子对 A 股次日并无线性可交易预测力**；本脚本的高 IC 是前视的产物。
  ⇒ 它们的**真实价值是条件性的**（只在 defensive 脆弱 regime 下有效，须用分组检验，
    见 `regime_external_lead_test.py` 的 regime × 预警 分组，lag1 口径差 1.44/2.83pt）。
  ⇒ 本脚本结论**只能用于同口径横向比较**（变量间谁更"同步"），
    **不可用于「系统能前瞻外部冲击」的论证**。

用法：
  python scripts/macro_lead_backtest.py
  python scripts/macro_lead_backtest.py --event 2026-09-16 --window 5
  python scripts/macro_lead_backtest.py --index sh000001
================================================================================
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.database import db                                  # noqa: E402
from mainforce_factor_backtest import spearman                # noqa: E402

SERIES_PATH = os.path.join(BACKEND, "data", "macro_series.json")
HOLDS = (1, 3, 5, 10)
_MIN_N = 100
_STRONG, _WEAK = 0.10, 0.05


def load_macro_series() -> dict:
    if not os.path.exists(SERIES_PATH):
        return {}
    try:
        with open(SERIES_PATH, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("series") or {}
    except (ValueError, OSError):
        return {}


def load_index(code: str) -> list:
    """沪深300/上证日线（date, close 升序）。"""
    rows = db.fetch("SELECT date, close FROM backtest_prices "
                    "WHERE code = %s AND close IS NOT NULL ORDER BY date", (code,)) or []
    out = []
    for r in rows:
        try:
            out.append((str(r["date"])[:10], float(r["close"])))
        except (TypeError, ValueError):
            continue
    return out


def _change(series_rows: list, use_pct: bool = False) -> dict:
    """{date: 当日变化}（绝对值或百分比）。"""
    out = {}
    prev = None
    for d, o, c, h, l in series_rows:
        if prev is not None:
            if use_pct:
                out[d] = (c / prev - 1) * 100 if prev else 0.0
            else:
                out[d] = c - prev
        prev = c
    return out


def lead_ic(chg: dict, bars: list, hold: int) -> dict:
    """Spearman corr(宏观变化[t], A股 t→t+hold 收益)。"""
    idx = {d: i for i, (d, _) in enumerate(bars)}
    xs, ys = [], []
    for i, (d, close) in enumerate(bars):
        v = chg.get(d)
        if v is None or i + hold >= len(bars) or not close:
            continue
        fwd = (bars[i + hold][1] / close - 1) * 100
        xs.append(v)
        ys.append(fwd)
    if len(xs) < _MIN_N:
        return {"n": len(xs), "rho": None}
    r = spearman(xs, ys)
    return {"n": len(xs), "rho": (r or {}).get("rho")}


def verdict(rho):
    """⚠️ 本判定基于 **same（同日）口径** —— 含时区前视、**不可交易**（见模块 docstring
    的「重大修正」）。措辞已相应改为"同口径强度"，避免被读成"可前瞻"。
    真实可交易性见 `scripts/fake_lead_diagnosis.py`（lag1：三者 |IC| 均 ≤0.04）。"""
    if rho is None:
        return "样本不足"
    a = abs(rho)
    if a >= _STRONG:
        return "同口径强（⚠️含前视，不可交易）"
    if a >= _WEAK:
        return "同口径中等（⚠️同上）"
    return "同口径亦弱"


def event_window(series: dict, bars: list, event: str, k: int):
    """打印事件日前后各 k 日的宏观变量 + A股序列（看谁先动）。"""
    idx = {d: i for i, (d, _) in enumerate(bars)}
    if event not in idx:
        cands = [d for d in idx if d <= event]
        if not cands:
            print(f"  （事件日 {event} 之后才有 A 股数据，跳过）")
            return
        event = max(cands)
        print(f"  （{event} 非交易日，取最近交易日）")
    i0 = max(0, idx[event] - k)
    i1 = min(len(bars) - 1, idx[event] + k)
    win = [bars[i][0] for i in range(i0, i1 + 1)]
    print(f"\n  事件窗口：{win[0]} ~ {win[-1]}（★ = 事件日 {event}）")
    print(f"  {'日期':<12}{'沪深300':>10}{'涨跌%':>9}   " +
          "  ".join(f"{sid[-5:]:>8}" for sid in series))
    prev = None
    for d in win:
        idxd = idx.get(d)
        close = bars[idxd][1] if idxd is not None else None
        chg = f"{(close / prev - 1) * 100:+.2f}" if (prev and close) else "  -  "
        cells = []
        for sid in series:
            m = {r[0]: r[2] for r in (series[sid].get("rows") or [])}
            v = m.get(d)
            if v is None:      # 取 ≤d 最近值
                for dd in sorted(m, reverse=True):
                    if dd <= d:
                        v = m[dd]
                        break
            cells.append(f"{v:>8.2f}" if v is not None else f"{'-':>8}")
        star = " ★" if d == event else ""
        print(f"  {d:<12}{close if close else 0:>10.1f}{chg:>9}   " +
              "  ".join(cells) + star)
        if close:
            prev = close


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="sh000300", help="A 股指数代码（默认沪深300）")
    ap.add_argument("--event", default="2026-09-16", help="事件日（默认本次美联储加息日）")
    ap.add_argument("--window", type=int, default=5, help="事件窗口前后天数")
    args = ap.parse_args()

    series = load_macro_series()
    bars = load_index(args.index)
    print("=" * 92)
    print(f"外部冲击领先性验证   指数={args.index}（{len(bars)} 根日线）")
    if not series:
        print("✗ 宏观序列缺失 —— 先跑 scripts/macro_series_backfill.py")
        return 1
    for sid, v in series.items():
        rows = v.get("rows") or []
        print(f"  宏观 {sid:<12}{v.get('name', ''):<20}{len(rows)} 根"
              f"  {rows[0][0] if rows else '-'} ~ {rows[-1][0] if rows else '-'}")
    if not bars:
        print(f"✗ 指数 {args.index} 无日线")
        return 1

    print(f"\n判定标准（预先写死）：|IC|≥{_STRONG} 且 n≥{_MIN_N} ⇒ 有效领先；"
          f"≥{_WEAK} ⇒ 弱领先；否则无")
    print("=" * 92)
    print("\n【领先性 IC】Spearman corr( 宏观变量当日变化 , A股未来 N 日收益 )"
          "  —— 负值 = 该变量上行预示 A 股下跌")
    print("⚠️ 本表为 **same（同日）口径**，含时区前视（美股夜盘在 A 股收盘之后）"
          "⇒ 数值**不可交易**；")
    print("   可交易口径见 `scripts/fake_lead_diagnosis.py`（lag1），实测三者 |IC| 均 ≤0.04。")
    summary = {}
    for sid, v in series.items():
        rows = v.get("rows") or []
        if len(rows) < _MIN_N:
            continue
        # 变化口径：**收益率类**（美债 xxY）用绝对值（bp/点）；
        # 其余（指数/价格/期货）用百分比 —— 量纲差异巨大，混用会让 IC 失真。
        pct = not (sid.upper().endswith("Y") and "US" in sid.upper())
        chg = _change(rows, use_pct=pct)
        print(f"\n  ### {sid}（{v.get('name', '')}）"
              f"  变化口径={'%' if pct else '绝对(收益率 bp/点)'}")
        print(f"    {'持有':<6}{'n':>6}{'IC':>10}   判定")
        verdicts = []
        for h in HOLDS:
            r = lead_ic(chg, bars, h)
            rho = r["rho"]
            vt = verdict(rho)
            verdicts.append((h, rho))
            rs = f"{rho:+.4f}" if rho is not None else "-"
            print(f"    {h}日{'':<3}{r['n']:>6}{rs:>10}   {vt}")
        best = max(((h, abs(r_) if r_ is not None else 0) for h, r_ in verdicts),
                   key=lambda x: x[1], default=(None, 0))
        summary[sid] = (best[0], best[1])

    print("\n" + "=" * 92)
    print("【结论】最强领先窗口（按 |IC| 取各变量最优持有期）")
    if summary:
        for sid, (h, a) in sorted(summary.items(), key=lambda x: -x[1][1]):
            print(f"  {sid:<12}最优 {h}日 |IC|={a:.4f} → "
                  + ("同口径强（⚠️含前视不可交易）" if a >= _STRONG else
                     "同口径中等" if a >= _WEAK else "同口径亦弱"))
    else:
        print("  （无足够样本）")

    print("\n" + "=" * 92)
    print(f"【事件窗口】{args.event}（本次美联储加息 25bp）前后 {args.window} 日")
    event_window(series, bars, args.event, args.window)

    print("\n注：宏观序列为东财日线（美国交易日），与 A 股交易日按日期取交集；"
          "领先性用同一日对齐（保守口径，未做滞后平移）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
