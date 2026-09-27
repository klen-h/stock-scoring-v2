#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · 补全】持有期 / 交易成本 / 止损 的完整画像
================================================================================
背景：
  §8.4 已确认 E2 在中证1000 ETF 上有 +2.76pp（T+20，未扣成本）。但「怎么拿这笔 edge」
  还缺三件事：① 扣成本后还剩多少；② 持有期是否合适；③ 止损是否有帮助。

数据：`data/idx_daily.json`（`event_realindex_check.py` 拉的腾讯指数日线缓存）。

━━━━━━━━━━━━━━━━━━ 预登记（防 forking paths）━━━━━━━━━━━━━━━━━━
  【主口径固定为 T+20、无止损】—— 与 §8.1~§8.4 全程一致，**不因本脚本结果而改**。
    · 持有期 1/3/5/10/20/40 与止损 -7% 一律作为**描述性**信息，
      **不作为"挑最优"的依据**（多重比较会选出噪音）。
    · 唯一用于决策的是：**主口径扣成本后的 edge 是否仍为正**。
  成本：双边 **0.3%**（万5佣金 + 0.05% 滑点 + 千1 印花税单边 —— 与项目既有口径一致）。
  止损：**-7%**（与项目 `WARFARE_HOLD_DAYS` 退出策略同值），按**收盘价**判定
    （T+1 制度下盘中不可追价，项目纪律）。

判定：
  (a) 主口径（T+20）扣 0.3% 成本后，首选标的 edge 仍 >= +1.5pp ⇒ **可执行结论成立**
  (b) < 0.5pp ⇒ edge 被成本吃掉，结论需降级

用法：python scripts/event_hold_cost_check.py
================================================================================
"""
import json
import os
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX_CACHE = os.path.join(ROOT, "data", "idx_daily.json")
EVENT_JSON = os.path.join(ROOT, "data", "event_history.json")

E2_UP_RATIO = 0.90
E2_LU_RATIO = 0.020
COST = 0.3              # 双边总成本（%）
STOP = -7.0             # 止损（%，收盘价判定）
MAIN_H = 20             # 主口径持有期（固定）
HORIZONS = [1, 3, 5, 10, 20, 40]

TARGETS = [
    ("sz399006", "创业板指", "159915"),
    ("sh000905", "中证500", "510500"),
    ("sh000852", "中证1000", "512100"),
    ("sh000300", "沪深300", "510300"),
]


def load_e2():
    with open(EVENT_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    out = []
    for d in sorted(raw):
        v = raw[d]
        denom = (v.get("up") or 0) + (v.get("down") or 0)
        if v["up_ratio"] >= E2_UP_RATIO and denom > 0 \
                and v["limit_up"] / denom >= E2_LU_RATIO:
            out.append(d)
    return out


def series_of(bars):
    ds = [b["date"] for b in bars]
    rets, gaps = [], []
    for i, b in enumerate(bars):
        pc = bars[i - 1]["close"] if i > 0 else b["close"]
        rets.append((b["close"] / pc - 1) * 100 if pc else 0.0)
        gaps.append((b["open"] / pc - 1) * 100 if pc else 0.0)
    return ds, rets, gaps


def fwd(rets, gaps, i, n):
    """T+1 开盘买 → T+n 收盘（相对买入价的收益 %）。"""
    if i + 1 + n > len(rets):
        return None
    e = 1 + (gaps[i + 1] or 0) / 100.0
    if e <= 0.05:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        cum *= (1 + rets[k] / 100.0)
    return (cum / e - 1) * 100


def fwd_stop(rets, gaps, i, n, stop=STOP):
    """同上，但逐日按**收盘价**检查止损（越线即出场）。"""
    if i + 1 + n > len(rets):
        return None
    e = 1 + (gaps[i + 1] or 0) / 100.0
    if e <= 0.05:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        cum *= (1 + rets[k] / 100.0)
        cur = (cum / e - 1) * 100
        if cur <= stop:
            return cur
    return (cum / e - 1) * 100


def _stat(v):
    v = [x for x in v if x is not None]
    if not v:
        return None
    return {"n": len(v), "mean": sum(v) / len(v),
            "win": sum(1 for x in v if x > 0) / len(v) * 100}


def main():
    with open(IDX_CACHE, encoding="utf-8") as f:
        cache = json.load(f)
    e2 = load_e2()
    print("=" * 104)
    print(f"【E2 持有期画像】指数（T+1 开盘买，未扣成本）；事件 {len(e2)} 天")
    print("=" * 104)
    print(f"{'指数':<10}{'ETF':<10}" + "".join(f"{'T+'+str(h):>11}" for h in HORIZONS))
    print("-" * 104)
    series = {}
    for code, name, etf in TARGETS:
        bars = cache.get(code) or []
        if len(bars) < 300:
            continue
        ds, rets, gaps = series_of(bars)
        di = {d: i for i, d in enumerate(ds)}
        series[name] = (ds, rets, gaps, di)
        line = f"{name:<10}{etf:<10}"
        for h in HORIZONS:
            ev = _stat([fwd(rets, gaps, di[d], h) for d in e2 if d in di])
            ba = _stat([fwd(rets, gaps, i, h) for i in range(len(ds))])
            line += f"{(ev['mean']-ba['mean'] if ev and ba else 0):>+10.2f}p"
        print(line)
    print("  （表内为「事件后 T+N 收益 - 同指数全样本 T+N 收益」的差，单位 pp）")

    print(f"\n{'='*104}")
    print(f"【主口径 T+{MAIN_H}】成本/止损 敏感性（★ 唯一用于决策的一行）")
    print(f"{'='*104}")
    print(f"{'指数':<10}{'ETF':<10}{'未扣成本':>11}{'扣0.3%':>10}"
          f"{'扣成本+止损-7%':>16}{'胜率(扣成本)':>13}")
    print("-" * 104)
    verdict = []
    for code, name, etf in TARGETS:
        if name not in series:
            continue
        ds, rets, gaps, di = series[name]
        ev = _stat([fwd(rets, gaps, di[d], MAIN_H) for d in e2 if d in di])
        ba = _stat([fwd(rets, gaps, i, MAIN_H) for i in range(len(ds))])
        if not ev or not ba:
            continue
        raw = ev["mean"] - ba["mean"]
        net = raw - COST
        evs = _stat([fwd_stop(rets, gaps, di[d], MAIN_H) for d in e2 if d in di])
        bas = _stat([fwd_stop(rets, gaps, i, MAIN_H) for i in range(len(ds))])
        net_stop = (evs["mean"] - bas["mean"] - COST) if (evs and bas) else None
        verdict.append((name, etf, raw, net, net_stop))
        print(f"{name:<10}{etf:<10}{raw:>+10.2f}pp{net:>+9.2f}pp"
              f"{(net_stop if net_stop is not None else 0):>+15.2f}pp{ev['win']:>12.1f}%")

    print(f"\n{'='*104}")
    print("【判定】")
    print(f"{'='*104}")
    best = max(verdict, key=lambda x: x[3]) if verdict else None
    if best:
        print(f"  ★ 扣 0.3% 成本后最优：{best[0]}（{best[1]}）{best[3]:+.2f}pp"
              f"  => {'**可执行结论成立**' if best[3] >= 1.5 else ('降级' if best[3] < 0.5 else '弱')}")
    print(f"  · 主口径固定 T+{MAIN_H}（与 §8.1~§8.4 一致）；其他持有期/止损仅为描述，**不作挑选依据**。")
    print(f"  · 成本 {COST}%（双边）；止损 {STOP}% 按收盘价判定。")


if __name__ == "__main__":
    main()
