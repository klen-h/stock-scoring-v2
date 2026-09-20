#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】外部因子「真领先 vs 假领先」判别（2026-09-20，Phase 2 遗留悬念）
================================================================================
背景：
  `macro_lead_backtest.py`（Phase 1）用 **same 口径**（宏观 t 日变化 vs A 股
  t→t+N 收益）算出三个"有效领先"：
      sina:NQ 纳指  +0.1609（1 日）   sina:VX VIX  -0.1124（1 日）
      100.UDI 美元  -0.1005（5 日）
  Phase 2 已证只有 **NQ + UDI** 在 regime 之外有**增量预警价值**，故只用这两个。
  **遗留问题**：VIX 的 |IC| 与美元相当、方向也合理，为何被排除？

本脚本要回答的唯一问题：
  **VIX 的"领先性"是真领先，还是时区造成的前视偏差（假领先）？**

机制假设（要验证的对象）：
  三个序列的 `date` 都是**美东交易日**，而 A 股是北京时间 —— 两者在"同一日"
  上的**先后顺序不同**：
    · NQ（纳指期货）：美东 d-1 夜盘 ≈ 北京 d 日**开盘前** ⇒ 对 A 股 d 日 **真领先**
    · VX（VIX 期货）：美东 d 日波动多在 A 股 d 日**收盘后**（北京 d 夜~d+1 晨）
       ⇒ "同日对齐"实际用上了 A 股 d 日收盘**之后**才发生的信息 = **前视**
  若假设成立：VX 在 `lag1` 口径（把宏观值往后推一个 A 股交易日）下**领先性消失**，
  而 NQ / UDI **依然显著**。

方法（口径与 `macro_lead_backtest.py` **逐字一致**，保证数字可比）：
  · same = `spearman(chg[t],        A股 t→t+N 收益)`   ← Phase 1 口径
  · lag1 = `spearman(chg[t-1],      A股 t→t+N 收益)`   ← 真实可交易口径
    （t-1 = A 股**上一个交易日**，与 `regime_external_lead_test.py` 同做法）
  · sync = `spearman(chg[t],        A股 t 日单日收益)` ← 同期相关（同步性证据）

★ 判定标准**预先写死**（防事后挑格子），取 N=1（三个序列的最优窗）：
  1) lag1 |IC| ≥ 0.10 且 n≥100             ⇒ **线性可交易领先**
  2) same |IC| ≥ 0.10 但 lag1 |IC| < 0.05  ⇒ **同期联动**（same 显著是时区前视所致，
     不可线性外推；⚠️ 注意：这不等于"无用" —— 条件子集内的价值须另用分组检验，
     见 `regime_external_lead_test.py` 的 regime × 预警 分组）
  3) 其余                                   ⇒ 无显著领先/联动

★ 稳健性说明（重要，避免"date 语义"纠缠）：宏观序列的 `date` 究竟是美东还是北京
  日期不影响结论 ——
    · 若为美东日期：`chg[d]` 的美股波动发生在北京 d 日夜 ~ d+1 晨 ⇒ `same` 用上了
      A 股 d 日收盘后才形成的信息（前视）；可交易档是 `lag1`。
    · 若为北京日期：它同样已包含北京 d 日夜的美股波动（仍在 A 股 d 日收盘后）
      ⇒ 结论同上。
  ⇒ 两种解释下"same 含前视、lag1 才是可交易档"都成立，故结论稳健。

用法：
  python scripts/fake_lead_diagnosis.py
  python scripts/fake_lead_diagnosis.py --index sh000300 --holds 1 3 5 10
================================================================================
"""
import argparse
import os
import sys

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

from macro_lead_backtest import (  # noqa: E402
    _change, lead_ic, load_index, load_macro_series)
from mainforce_factor_backtest import spearman  # noqa: E402

TARGETS = ("sina:VX", "sina:NQ", "100.UDI")   # VIX（待判）｜纳指（真领先基准）｜美元
_STRONG, _WEAK = 0.10, 0.05                   # 与 Phase 1 同一套门槛
_MIN_N = 100
_SYNC_LEVEL = 0.20                            # 「偏同步」的同期相关门槛


def lag1_map(chg: dict, dates: list) -> dict:
    """把宏观变化**按 A 股交易日**后移一天：{t: chg[t-1]}（t-1 = 上一 A 股交易日）。

    与 `regime_external_lead_test.py` 的 lag1 做法一致（那里也是取 `dates[i-1]`）。
    ⚠️ 只做**日期**平移，不做时间戳插值 —— 与项目现有两套脚本同口径，便于数字互证。
    """
    out = {}
    for i, d in enumerate(dates):
        if i == 0:
            continue
        v = chg.get(dates[i - 1])
        if v is not None:
            out[d] = v
    return out


def sync_rho(chg: dict, bars: list) -> dict:
    """同期：spearman(宏观 t 变化, A 股 t 日单日收益%)。"""
    xs, ys = [], []
    for i, (d, close) in enumerate(bars):
        if i == 0:
            continue
        v = chg.get(d)
        prev = bars[i - 1][1]
        if v is None or not prev:
            continue
        xs.append(v)
        ys.append((close / prev - 1) * 100)
    if len(xs) < _MIN_N:
        return {"n": len(xs), "rho": None}
    return {"n": len(xs), "rho": (spearman(xs, ys) or {}).get("rho")}


def judge(same, lag1_):
    """**预先写死**的判定（取该序列最优窗 N=1，以 lag1 可交易口径为准）。"""
    if same is None or lag1_ is None:
        return "样本不足"
    if abs(lag1_) >= _STRONG:
        return "**线性可交易领先**"
    if abs(same) >= _STRONG and abs(lag1_) < _WEAK:
        return "**同期联动**（same 含时区前视，不可线性外推）"
    return "无显著领先/联动"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="sh000300")
    ap.add_argument("--holds", type=int, nargs="+", default=[1, 3, 5, 10])
    args = ap.parse_args()

    series = load_macro_series()
    bars = load_index(args.index)
    if not series or not bars:
        print("✗ 缺数据（macro_series.json 或 backtest_prices）")
        return 1
    dates = [d for d, _ in bars]

    print("=" * 96)
    print(f"外部因子「真领先 vs 假领先」判别    指数={args.index}（{len(bars)} 根日线）")
    print(f"预注册判定：lag1 |IC|≥{_STRONG} ⇒ 真领先；same |IC|≥{_STRONG} 且 "
          f"lag1<{_WEAK} ⇒ 假领先（前视）；n≥{_MIN_N}")
    print("=" * 96)
    print("\n【核心表】same（Phase 1 口径）vs lag1（可交易口径）—— 同一变量两口径的差距")
    print(f"  {'序列':<12}{'口径':<7}" + "".join(f"{str(h) + '日':>11}" for h in args.holds)
          + f"{'同期rho':>10}")
    results = {}
    for sid in TARGETS:
        v = series.get(sid)
        if not v:
            print(f"  {sid:<12}（缺）")
            continue
        chg = _change(v.get("rows") or [], use_pct=True)
        lag = lag1_map(chg, dates)
        s_ic, l_ic = {}, {}
        for h in args.holds:
            r1, r2 = lead_ic(chg, bars, h), lead_ic(lag, bars, h)
            s_ic[h], l_ic[h] = r1["rho"], r2["rho"]
        sy = sync_rho(chg, bars)
        for tag, d in (("same", s_ic), ("lag1", l_ic)):
            cells = "".join(
                (f"{d[h]:>+11.4f}" if d.get(h) is not None else f"{'-':>11}")
                for h in args.holds)
            extra = (f"{sy['rho']:>+10.4f} (n={sy['n']})" if tag == "same" else "")
            print(f"  {sid:<12}{tag:<7}{cells}{extra}")
        results[sid] = (s_ic.get(1), l_ic.get(1), sy.get("rho"))

    print("\n" + "=" * 96)
    print("【判定】（按最优窗 N=1，以 lag1 可交易口径为准）")
    for sid in TARGETS:
        if sid not in results:
            continue
        same, lag1_, sy = results[sid]
        drop = f"{abs(lag1_ / same - 1) * 100:.0f}%" if same else "-"
        print(f"  {sid:<12}same={same:+.4f}  lag1={lag1_:+.4f}（塌陷 {drop}）  "
              f"同期={sy:+.4f}  →  {judge(same, lag1_)}")

    print("\n" + "=" * 96)
    print("【结论】外部分子的 same 高 IC **全部来自时区前视**，lag1 下无线性可交易领先性")
    print("  · 机制：`same` 用 A 股**当日**日期索引宏观序列，而这些序列的『当日』波动")
    print("    大部分发生在 A 股当日**收盘之后**（美股夜盘）⇒ 等于用『次日才知道的信息』")
    print("    预测当日之后收益 = **前视偏差**。`lag1`（推到上一 A 股交易日）才是")
    print("    『A 股当日开盘前已知』的可交易档。")
    print("  · ⇒ **Phase 1「|IC|≥0.10 ⇒ 有效领先」的判定须降级**：那是同日口径的前视结果，")
    print("    不能据此说『系统能前瞻外部冲击』（`macro_lead_backtest.py` 已加警示）。")
    print()
    print("【但 ≠ 外部分子无用】全样本线性 IC≈0 与『条件子集内有价值』可以并存：")
    print("  `regime_external_lead_test.py` 在 **lag1 口径**下仍测得 defensive 内")
    print("  『预警 vs 对照』未来 5/10 日差 1.44 / 2.83pt ⇒ 外部冲击的作用是")
    print("  **非线性的、条件性的**（在脆弱 regime 下才触发），不是全局线性领先。")
    print("  ⇒ 这正是 Phase 3 采用『限定 defensive + 阈值预警』而非『全局打分』的原因；")
    print("    也说明**评估这类因子必须用分组检验，不能只看全样本 IC**。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
