#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】技术面子项「负 IC 结构」检验：整条曲线 vs 尾部驱动（2026-09-20 重审）
================================================================================
背景（重审 `评分系统体检_子指标IC与优化建议_20260920.md` 时发现）：
  1. `subfactor_ic_backtest.py` 的 `quintile_table()` 原是**死代码**（键名 bug：
     取 `x_fwd` 而数据键是 `x_fwd{h}`）⇒ 0103/0148/0149 三份报告**均无五分位输出**
     ⇒ 「技术面负 IC 是整条曲线还是尾部驱动」从未被回答。
  2. 修复后重跑主脚本需要 Supabase 资金流（当时连接故障）⇒ 而本问题只涉及
     **技术面子项**，只依赖 OHLC（research_cache 本地包，零 Supabase）⇒ 独立成脚本。

要回答的唯一问题：
  **技术面 5 个强负子项（RSI/MA/MACD/布林/涨跌动量）的负 IC，
   是「整条曲线单调反向」（支持体检报告建议 1 方案 a：降权），
   还是「集中在最高分位（超买/强势尾部）」（支持方案 b 变体：尾部过滤/减分）？**

★ 判定标准**预先写死**（防事后挑格子）：
  对每个子项按分值五等分（Q1 低分 ~ Q5 高分），记去超额收益 x(Q)：
    · body = x(Q4) - x(Q1)   （中段跨度）
    · tail = x(Q5) - x(Q4)   （尾部增量）
  |tail| > |body| ⇒ **尾部驱动**（只砍头部即可，降权会误伤中段）
  否则 ⇒ **整条曲线**（降权/反向才是对的）

口径与 `subfactor_ic_backtest.py` **同源**：同 warmup=70 / 尾留 max_hold /
step=5 / 截面全池均值去超额 / `_calc_technical_fast` + `ScoreEngine._score_technical`
（生产锚点零偏差）。唯一差异：不做 `code in flow_map` 过滤（无资金流）——
原报告 n=749/截面，flow 覆盖≈全池，差异可忽略。

用法：python scripts/tech_quintile_check.py [--hold 5]
================================================================================
"""
import argparse
import os
import sys
from collections import defaultdict

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

from app import research_cache                       # noqa: E402
from app.scoring.engine import ScoreEngine           # noqa: E402
from app.routers.scoring import _calc_technical_fast  # noqa: E402
from mainforce_factor_backtest import spearman       # noqa: E402

TARGET_SUBS = ("RSI强弱", "MA趋势", "MACD动量", "布林带", "涨跌动量", "KDJ指标", "量价配合")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=5)
    ap.add_argument("--step", type=int, default=5,
                    help="截面采样步长（原报告 step=5；重审用 1 做密度敏感性）")
    ap.add_argument("--start", default=None, help="截面日起（含），YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="截面日止（含），YYYY-MM-DD")
    args = ap.parse_args()
    h = args.hold

    prices = research_cache.ohlc_all(force=False)
    print(f"[data] 日线覆盖 {len(prices)} 只（本地包，零 Supabase）")
    if len(prices) < 100:
        print("✗ OHLC 覆盖不足")
        return 1
    all_dates = sorted({b["date"] for bars in prices.values() for b in bars})
    sec_dates = all_dates[70:-h] if len(all_dates) > 70 + h else []
    sec_dates = sec_dates[::args.step]
    if args.start:
        sec_dates = [d for d in sec_dates if d >= args.start]
    if args.end:
        sec_dates = [d for d in sec_dates if d <= args.end]
    if len(sec_dates) < 3:
        print(f"✗ 截面日不足（{len(sec_dates)}）")
        return 1
    sec_set = set(sec_dates)
    print(f"[sections] 截面 {len(sec_dates)} 个：{sec_dates[0]} ~ {sec_dates[-1]}")

    eng = ScoreEngine()
    subs = defaultdict(list)      # {sub: [(date, score, fwd)]}
    for code, bars in sorted(prices.items()):
        if len(bars) < 100:
            continue
        dates_idx = {b["date"]: i for i, b in enumerate(bars)}
        tech = _calc_technical_fast(bars)
        for d in sec_set:
            i = dates_idx.get(d)
            if i is None or i + h >= len(bars):
                continue
            fwd = (bars[i + h]["close"] / bars[i]["close"] - 1) * 100
            try:
                dt_tech = eng._score_technical(tech[max(0, i - 59): i + 1])
            except Exception:
                continue
            for sub, detail in (dt_tech.details or {}).items():
                if isinstance(detail, dict) and detail.get("分值") is not None:
                    subs[sub].append((d, detail["分值"], fwd))

    # 去超额（截面均值）
    for sub in list(subs):
        by_date = defaultdict(list)
        for d, s, f in subs[sub]:
            by_date[d].append((s, f))
        out = []
        for d, grp in by_date.items():
            m = sum(f for _, f in grp) / len(grp)
            out.extend((d, s, f - m) for s, f in grp)
        subs[sub] = out

    print("=" * 96)
    print(f"技术面子项负 IC 结构检验（持有 {h} 日去超额，五分位按分值升序）")
    print("预注册判定：|tail = x(Q5)-x(Q4)| > |body = x(Q4)-x(Q1)| ⇒ 尾部驱动；否则整条曲线")
    print("=" * 96)
    for sub in TARGET_SUBS:
        rows = subs.get(sub) or []
        if len(rows) < 200:
            continue
        ic = spearman([s for _, s, _ in rows], [x for _, _, x in rows])
        srt = sorted(rows, key=lambda r: r[1])
        q = max(1, len(srt) // 5)
        buckets = []
        for k in range(5):
            chunk = srt[k * q: (k + 1) * q] if k < 4 else srt[4 * q:]
            xs = [x for _, _, x in chunk]
            buckets.append(sum(xs) / len(xs) if xs else float("nan"))
        body = buckets[3] - buckets[0]
        tail = buckets[4] - buckets[3]
        driven = "尾部驱动" if abs(tail) > abs(body) else "整条曲线"
        cells = " ｜ ".join(f"Q{k+1} {v:+.2f}%" for k, v in enumerate(buckets))
        print(f"\n  {sub}（n={len(rows)}）IC={ic['rho']:+.3f}")
        print(f"    {cells}")
        print(f"    body(Q4-Q1)={body:+.2f}pt  tail(Q5-Q4)={tail:+.2f}pt"
              f"  → **{driven}**")
    # ── 分时段 IC（2026-09-20 重审追加）────────────────────────────────────
    # 动机：长样本 IC（-0.03~-0.05）比原报告（6-8 月样本，-0.14~-0.22）弱一个量级
    #   ⇒ 检验「负 IC 是否样本期特定」—— 分『原报告窗（2026-06~08）』vs『其余时段』。
    #   （与今天 VIX 复核同一课：全局/拉长的 IC 会掩盖条件性结构。）
    print("\n" + "=" * 96)
    print("分时段 IC（检验：负 IC 是否集中在原报告样本窗 2026-06~08）")
    print("=" * 96)
    in_win = lambda d: "2026-06-01" <= d <= "2026-08-31"
    for sub in TARGET_SUBS:
        rows = subs.get(sub) or []
        if len(rows) < 200:
            continue
        a = [(s, x) for d, s, x in rows if in_win(d)]
        b = [(s, x) for d, s, x in rows if not in_win(d)]
        ica = spearman([s for s, _ in a], [x for _, x in a]) if len(a) > 500 else None
        icb = spearman([s for s, _ in b], [x for _, x in b]) if len(b) > 500 else None
        fa = f"{ica['rho']:+.3f}（n={ica['n']}）" if ica else "n少"
        fb = f"{icb['rho']:+.3f}（n={icb['n']}）" if icb else "n少"
        print(f"  {sub:<10}原报告窗(6-8月): {fa:<26}其余时段: {fb}")

    print("\n注：无 flow 过滤（Supabase 故障时的纯本地近似）；原报告 n=749/截面与此几乎相同。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
