#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】板块动量的延续性检验（**预登记**，2026-09-30，P1）
================================================================================
【为什么有本脚本】
  2026-09-30 交付了 `app/sector_momentum.py`（板块动量/异动侦测，描述层）。
  它现在只进日报/复盘、**不推送、不进决策链** —— 这是项目纪律：
  「新信号上线先做预测力检验（①会不会过吵 ②响了有没有用）」，模板
  `scripts/reversal_edge_check.py` / `scripts/gate_ready_backtest.py`。
  出处：`项目专业审视_不足与优化建议_20260920.md` A2 ——「**缺"板块动量排序"**：
  A 股超额集中在主线板块内。实验：板块 5/10/20 日动量 → 板块内个股次日/5 日收益 IC
  （**数据可得：sector_daily 已有**）」。本脚本是该实验的**板块层**第一段。

【样本是「时钟」不是「开发」】板块序列自 2026-08-21（zzshare 接入）起积累 ⇒
  现在（09-30）只有 ~27 个交易日 ⇒ **必然 INSUFFICIENT**。脚本先就位、样本自己长，
  **建议每月首个交易日随日批重跑**（同 `subfactor_ic` 的周期化做法）。

━━━━━━━━━━━━━━ 预登记（先于结果写死；改判据须新开一节并注明日期）━━━━━━━━━━━━━━
【事件】`app.sector_momentum.moves_by_date()` 里 `kind == "strong"` 的**首日命中**
  （= 3 日累计 ≥+5% 或 5 日累计 ≥+8%；连续命中只报首日）。
  ⚠️ 复用该模块的判定函数 ⇒ **改了那边的阈值就会让本预登记失效**（须重跑并注明）。
【入场】**T+1 收盘**；【出场】**T+5 收盘**。
  为什么不用 T+1 开盘：① 板块指数不可开盘成交；② 本项目板块数据**只有收盘价**
     （`plate_daily_zz.change_pct`）⇒ 假装有开盘价就是编造数据。口径保守 ⇒ 结论偏不乐观，可接受。
【主判据（去市场因子）】命中板块 T+1→T+5 收益 − **同日全部板块等权**同窗口收益 ≥ **+0.50pp**。
  ⚠️ 必须减同日全板块均值：板块动量命中的日子**本身常是普涨日**，不减基准就等于
     "证明普涨日买什么都赚"（与 `mainline_performance` 的 baseline 设计同理）。
【辅判据】(b) 相对**中证1000**（风格基准）超额同号且 ≥ +0.50pp；
  (c) 按**交易日**聚簇的 bootstrap（B=10000，有放回）**P(均值 ≤ 0) < 0.05**；
  (d) 前后半段（各占一半）超额**同号**。
【最小样本】**30 个"有事件的交易日"**（独立单位 = **交易日**，不是"板块×日"：
  同一天命中的多个板块**共享同一段行情**，横截面相关没被消除 —— 与
  `gate_ready_backtest.py` 把独立单位从"个股"改成"快照日"是同一个教训）。
【判定】不足 30 ⇒ **INSUFFICIENT**，只报描述统计、**不下结论**（缺失 ≠ 反证）。

⚠️ **可执行性（重要，勿越界）**：板块指数**不可直接交易**（zzshare 板块无对应 ETF 映射）
  ⇒ 本检验只回答"板块动量是否有**延续性**"（因子层证据），**不构成交易信号**。
  若将来要落到交易，须另开一节预登记（含 ETF 映射与成本/滑点约束）。
================================================================================
"""

import argparse
import json
import os
import random
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    """加载 backend/.env（本地跑用；CI 走环境变量）。"""
    if not os.path.exists(ENV_PATH):
        return
    for line in open(ENV_PATH, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
sys.path.insert(0, BACKEND_DIR)

MIN_DAYS = 30                  # 最小独立簇数（= 有事件的交易日数）
MIN_EDGE = 0.50                # 主判据门槛（pp，相对同日全板块等权）
B_BOOT = 10000
SEED = 20260930                # 固定种子 ⇒ 结果可复现
ENTRY_LAG, HOLD = 1, 5         # T+1 收盘入场，T+5 收盘出场
BENCH = "sh000852"             # 风格基准：中证1000（缺口 1 定下的口径）


def _series() -> Dict[str, List[dict]]:
    from app.database import db
    rows = db.fetch("SELECT date, name, change_pct FROM plate_daily_zz "
                    "WHERE kind = 'industry' ORDER BY date")
    by: Dict[str, List[dict]] = {}
    for r in rows or []:
        by.setdefault(str(r["name"]).strip(), []).append(
            {"date": str(r["date"])[:10], "chg": float(r["change_pct"] or 0.0)})
    return by


def _ret(seq: List[dict], i0: int, i1: int) -> Optional[float]:
    """板块指数 [i0, i1] 区间的复利收益（%）；越界返回 None（缺失 ≠ 0）。"""
    if i0 < 0 or i1 >= len(seq) or i1 <= i0:
        return None
    v = 1.0
    for x in seq[i0 + 1:i1 + 1]:
        v *= (1 + x["chg"] / 100.0)
    return (v - 1) * 100.0


def _bench_closes(code: str) -> Dict[str, float]:
    """基准指数 {date: close}；表不可用/无数据返回 {}。"""
    from app.database import db
    try:
        rows = db.fetch("SELECT date, close FROM backtest_prices WHERE code = %s "
                        "ORDER BY date", (code,))
        return {str(r["date"])[:10]: float(r["close"]) for r in rows if r.get("close")}
    except Exception as e:
        print(f"[edge] bench load failed: {str(e)[:70]}")            # ASCII（铁律⑥）
        return {}


def _bench_ret(closes: Dict[str, float], d0: str, d1: str) -> Optional[float]:
    """基准 d0→d1 收益（%）；两日都必须**同日有价**（不猜最近日 ⇒ 缺失 ≠ 0）。"""
    c0, c1 = closes.get(d0), closes.get(d1)
    if not c0 or not c1 or c0 <= 0:
        return None
    return (c1 / c0 - 1) * 100.0


def boot_p(vals: List[float], b: int = B_BOOT, seed: int = SEED) -> Optional[float]:
    """按「交易日」有放回重采样 ⇒ P(均值 ≤ 0)。簇数 < 3 返回 None（无统计意义）。"""
    if len(vals) < 3:
        return None
    rng = random.Random(seed)
    k, hit = len(vals), 0
    for _ in range(b):
        s = sum(vals[rng.randrange(k)] for _ in range(k)) / k
        if s <= 0:
            hit += 1
    return hit / b


def _fmt(v, unit="pp", nd=2):
    return "—" if v is None else f"{v:+.{nd}f}{unit}"


def main():
    # ★ 2026-09-30：加 `--json` —— 在人类可读输出的**末尾追加一行结构化结论**，
    #   供月度日批复核（`app/edge_verify.py`）解析；默认不输出 JSON、正文不受影响。
    #   【为什么要有它】本项目"样本是时钟"，脚本没人跑就等于白等 ⇒ 已接进月度日批。
    ap = argparse.ArgumentParser(description="板块动量延续性检验（预登记）")
    ap.add_argument("--json", action="store_true",
                    help="输出末尾追加一行结构化结论（供日批消费）")
    args = ap.parse_args()

    def _done(verdict: str, n=None, detail: str = "", code: int = 0) -> int:
        if args.json:
            print(json.dumps({"script": "sector_momentum_edge_check", "verdict": verdict,
                              "n": n, "need": MIN_DAYS, "unit": "交易日",
                              "detail": detail}, ensure_ascii=False))
        return code

    from app import sector_momentum as sm
    by = _series()
    if not by:
        print("板块序列为空（plate_daily_zz 无数据）—— 等日批积累")
        return _done("ERROR", None, "板块序列为空（等日批积累）", 2)
    dates = sorted({x["date"] for s in by.values() for x in s})
    idx = {n: {x["date"]: i for i, x in enumerate(s)} for n, s in by.items()}
    closes = _bench_closes(BENCH)

    print("=" * 96)
    print("板块动量延续性检验（**预登记**：事件=3日≥+5%/5日≥+8% 首日；"
          f"T+{ENTRY_LAG} 收盘入 → T+{HOLD} 收盘出；基准 {BENCH}）")
    print("=" * 96)
    print(f"板块序列：{len(by)} 个板块 / {len(dates)} 个交易日（{dates[0]} ~ {dates[-1]}）"
          f"｜基准 {BENCH} 可用 {len(closes)} 根")

    moves = sm.moves_by_date(999)
    events: List[Tuple[str, str]] = [(d, m["industry"]) for d, ms in moves.items()
                                     for m in ms if m.get("kind") == "strong"]
    print(f"强势异动首日：{len(events)} 个事件，分布在 {len({d for d, _ in events})} 个交易日")

    # ── 逐事件算 T+lag → T+hold；同日多板块取均值（独立单位 = 交易日）──
    per_day: Dict[str, List[float]] = defaultdict(list)        # 相对全板块等权
    per_day_raw: Dict[str, List[float]] = defaultdict(list)    # 板块自身
    per_day_b: Dict[str, List[float]] = defaultdict(list)      # 相对中证1000
    miss = 0
    for d, name in events:
        seq, i = by.get(name), idx.get(name, {}).get(d)
        if not seq or i is None:
            miss += 1
            continue
        r_sec = _ret(seq, i + ENTRY_LAG - 1, i + HOLD - 1)
        if r_sec is None:                                      # 窗口未走完 ⇒ 跳过不填 0
            miss += 1
            continue
        # ⚠️ 基准日期必须取**该板块自身序列**的日期，不能写 `dates[i]`：
        #   板块若中途新增/缺某天，自身索引与全局日期列表会**错位** ⇒ 基准对错日
        #   （静默错，且看起来一切正常 —— 这类 bug 最难发现）。
        d0 = (seq[i + ENTRY_LAG - 1]["date"]
              if i + ENTRY_LAG - 1 < len(seq) else None)
        d1 = seq[i + HOLD - 1]["date"] if i + HOLD - 1 < len(seq) else None
        peers = []
        for other, oseq in by.items():
            j = idx[other].get(d)
            if j is None:
                continue
            rr = _ret(oseq, j + ENTRY_LAG - 1, j + HOLD - 1)
            if rr is not None:
                peers.append(rr)
        if not peers:
            miss += 1
            continue
        per_day[d].append(r_sec - sum(peers) / len(peers))
        per_day_raw[d].append(r_sec)
        rb = _bench_ret(closes, d0, d1) if (d0 and d1) else None
        if rb is not None:
            per_day_b[d].append(r_sec - rb)

    day_vals = [sum(v) / len(v) for v in per_day.values()]
    raw_vals = [sum(v) / len(v) for v in per_day_raw.values()]
    b_vals = [sum(v) / len(v) for v in per_day_b.values()]
    n_days = len(day_vals)
    print(f"有效 → {n_days} 个交易日（跳过 {miss}：窗口未走完 / 无数据，**不填 0**）"
          f"｜可算基准超额的 {len(b_vals)} 天")

    mean_exc = mean_raw = win = h1 = h2 = p = mean_b = None
    if n_days:
        mean_exc = sum(day_vals) / n_days
        mean_raw = sum(raw_vals) / n_days
        win = sum(1 for v in day_vals if v > 0) / n_days * 100
        half = n_days // 2
        h1 = sum(day_vals[:half]) / max(1, half)
        h2 = sum(day_vals[half:]) / max(1, n_days - half)
        p = boot_p(day_vals)
        mean_b = (sum(b_vals) / len(b_vals)) if b_vals else None
        print("\n描述统计（**未达样本门槛时仅供观察，不得当结论**）：")
        print(f"  · 主判据（− 同日全板块等权）：**{_fmt(mean_exc)}**"
              f"（门槛 ≥ +{MIN_EDGE:.2f}pp）")
        print(f"  · 板块自身 T+{ENTRY_LAG}→T+{HOLD} 平均：{_fmt(mean_raw, '%')}")
        print(f"  · 辅判据 (b) 相对 {BENCH}：{_fmt(mean_b)}（{len(b_vals)} 天）")
        if not b_vals:
            print(f"    ⚠️ 基准 {BENCH} 在 `backtest_prices` 里**无数据** ⇒ (b) 暂不可算："
                  f"中证1000 由 `backfill_daily` 指数段回填（2026-09-30 起）—— "
                  f"**缺失 ≠ 该判据不通过**，回填后重跑")
        print(f"  · 正超额交易日占比：{win:.1f}%")
        print(f"  · (d) 前后半段：{_fmt(h1)} / {_fmt(h2)}（需同号）")
        print(f"  · (c) 交易日级 bootstrap P(均值≤0)："
              f"{'—（簇<3）' if p is None else f'{p:.4f}'}（门槛 < 0.05）")

    print("\n" + "=" * 96)
    if n_days < MIN_DAYS:
        remain = MIN_DAYS - n_days
        print(f"判定：**INSUFFICIENT** —— 只有 {n_days} 个独立交易日，需 ≥ {MIN_DAYS}"
              f"（还差 {remain} 个）")
        print("  · 独立单位是**交易日**：同日命中的多个板块共享同一段行情，不是独立样本")
        print(f"  · 板块序列自 {dates[0]} 起 ⇒ 约需再积累 {remain} 个交易日"
              f"（≈ {remain / 21:.1f} 个月；样本是**时钟**，开发加速不了）")
        print("  · **不下结论**：缺证据 ≠ 反证据。本检验不影响任何线上行为"
              "（板块侦测只进日报/复盘，不推送、不进决策链）")
        print("  · 建议每月首个交易日随日批重跑（同 subfactor_ic 的周期化做法）")
        verdict = "INSUFFICIENT"
        detail = (f"只有 {n_days} 个独立交易日，需 ≥ {MIN_DAYS}"
                  f"（还差 {MIN_DAYS - n_days}）")
    else:
        ok_b = (mean_b is not None and mean_b >= MIN_EDGE)
        ok = (mean_exc >= MIN_EDGE and p is not None and p < 0.05 and h1 * h2 > 0)
        print(f"判定：{'**通过**' if ok else '**未通过**'}（n={n_days}）")
        print(f"  (a) 主判据 {_fmt(mean_exc)} ≥ +{MIN_EDGE:.2f}pp ？"
              f"{mean_exc >= MIN_EDGE}")
        print(f"  (b) 相对基准 {_fmt(mean_b)} ≥ +{MIN_EDGE:.2f}pp ？{ok_b}")
        print(f"  (c) P={p if p is None else round(p, 4)} < 0.05 ？"
              f"{(p is not None and p < 0.05)}")
        print(f"  (d) 前后半同号 ？{(h1 * h2 > 0)}")
        print("  ⚠️ 即便通过也只说明「板块动量有延续性」—— **板块指数不可交易**，"
              "落地须另开预登记（ETF 映射 + 成本/滑点）")
        verdict = "PASS" if ok else "FAIL"
        detail = (f"n={n_days}；主判据 {_fmt(mean_exc)}（门槛 ≥+{MIN_EDGE:.2f}pp）；"
                  f"相对基准 {_fmt(mean_b)}；P={p if p is None else round(p, 4)}；"
                  f"(d) 前后半 {_fmt(h1)}/{_fmt(h2)}")
    print("=" * 96)
    return _done(verdict, n_days, detail, 0)


if __name__ == "__main__":
    sys.exit(main())
