#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · 可执行性验证】E2 的 +2.47pp 里，有多少是"真能拿到"的？
================================================================================
动机：
  E2 之前的全部统计都建立在 **全市场等权 ~5000 只、每日再平衡** 的组合上 ——
  这是**不可执行**的（买不到 5000 只、无法日度再平衡）。且事件日涨停多，
  次日大量股票**一字板开盘**（根本买不进），而等权收益却把它们算进去了。
  ⇒ 必须回答：**换成你能真买的标的，edge 还剩多少？**

三个口径（同口径可比：事件日 T → **T+1 开盘买** → T+20 收盘卖）：
  A 全市场等权（原口径，不可执行）      —— 基准线
  B 剔除"次日开盘即涨停"后的等权        —— 近似"买得到的股票池"
  C 沪深300 指数（买 300ETF 即可复刻）  —— **真正可执行**

━━━━━━━━━━━━━━━━━━ 预登记判定 ━━━━━━━━━━━━━━━━━━
  (1) 「买不进」损耗：B 相对 A 的 edge 保留率
      保留率 < 50% ⇒ 判定「edge 主要靠买不进的涨停股支撑」（严重）
  (2) 指数可执行性：C 的 edge（事件后 T+20 相对 C 自身基准）
      >= +1.5pp 且 bootstrap P < 0.05 ⇒ **E2 可作为指数级择时（可执行）**
      < +0.5pp ⇒ E2 只存在于小盘/题材，**指数买不到**（不可执行）
  (3) 沪深300 数据自 2007 起 ⇒ 2005-2006 的 E2 事件自动跳过（需在结论中标注）

⚠️ B 口径为**近似**：逐日剔除"开盘涨停"股票 ⇒ 等价于"每天跳过一字板"的滚动组合，
   而非"T+1 买入后 20 日不动"。方向正确（量化买不进损耗），但不是精确复刻。
⚠️ 未扣交易成本 0.3% 双边。

用法：python scripts/event_executability_check.py
================================================================================
"""
import json
import os
import sqlite3
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
EVENT_JSON = os.path.join(ROOT, "data", "event_history.json")
IDX_JSON = os.path.join(ROOT, "data", "sh000300_daily.json")

E2_UP_RATIO = 0.90
E2_LU_RATIO = 0.020
H = 20
B_BOOT = 10000
STOCK_FILTER = ("(code LIKE '60%.SH' OR code LIKE '68%.SH' OR code LIKE '00%.SZ' "
                "OR code LIKE '30%.SZ' OR code LIKE '%.BJ')")


def load_event_dates():
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


def agg_executable():
    """每日「剔除开盘即涨停股」后的等权收益与跳空（近似可执行口径 B）。"""
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute(f"""
        SELECT date,
               AVG(CASE WHEN NOT (high_limit > 0 AND open >= high_limit*0.999)
                        THEN pct_chg END) AS pct_ok,
               AVG(CASE WHEN NOT (high_limit > 0 AND open >= high_limit*0.999)
                         AND pre_close > 0 THEN open/pre_close - 1 END) * 100 AS gap_ok,
               COUNT(*) AS n,
               SUM(CASE WHEN high_limit > 0 AND open >= high_limit*0.999
                        THEN 1 ELSE 0 END) AS n_open_limit
        FROM daily
        WHERE {STOCK_FILTER} AND is_paused = 0 AND pct_chg IS NOT NULL
        GROUP BY date ORDER BY date
    """).fetchall()
    conn.close()
    return {str(r[0]): {"pct": r[1], "gap": r[2], "n": r[3], "n_open_limit": r[4]}
            for r in rows}


def load_index():
    with open(IDX_JSON, encoding="utf-8") as f:
        bars = json.load(f)
    return {b["date"]: b for b in bars}


def fwd(rets, gaps, dates, i, n=H):
    """T+1 开盘买 → T+n 收盘（需 dates 与 rets/gaps 同索引）。"""
    if i + 1 + n > len(rets):
        return None
    e = 1 + (gaps[i + 1] or 0) / 100.0
    if e <= 0.05:
        return None
    cum = 1.0
    for k in range(i + 1, i + 1 + n):
        r = rets[k]
        if r is None:
            return None
        cum *= (1 + r / 100.0)
    return (cum / e - 1) * 100


def block_p(vals, base, B=B_BOOT, seed=42, chunk=2000):
    a = np.asarray([v for v in vals if v is not None], dtype=float)
    n = len(a)
    if n == 0:
        return None
    rng = np.random.default_rng(seed)
    cnt = tot = 0
    done = 0
    while done < B:
        m = min(chunk, B - done)
        means = a[rng.integers(0, n, size=(m, n))].mean(axis=1)
        cnt += int(np.sum(means <= base))
        tot += m
        done += m
    return cnt / tot


def _stat(vals):
    v = [x for x in vals if x is not None]
    if not v:
        return None
    s = sorted(v)
    return {"n": len(v), "mean": sum(v) / len(v), "median": s[len(s) // 2],
            "win": sum(1 for x in v if x > 0) / len(v) * 100}


def agg_board():
    """按板块分别聚合每日等权收益（定位 edge 落在哪一层 ⇒ 能买什么）。"""
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute(f"""
        SELECT date,
               AVG(CASE WHEN code LIKE '60%' THEN pct_chg END) AS sh_main,
               AVG(CASE WHEN code LIKE '00%' THEN pct_chg END) AS sz_main,
               AVG(CASE WHEN code LIKE '30%' THEN pct_chg END) AS chinext,
               AVG(CASE WHEN code LIKE '68%' THEN pct_chg END) AS star
        FROM daily
        WHERE {STOCK_FILTER} AND is_paused = 0 AND pct_chg IS NOT NULL
        GROUP BY date ORDER BY date
    """).fetchall()
    conn.close()
    return {str(r[0]): {"沪主板": r[1], "深主板": r[2], "创业板": r[3], "科创板": r[4]}
            for r in rows}


def main():
    e2 = load_event_dates()
    ex = agg_executable()
    idx = load_index()

    print("=" * 100)
    print("【E2 可执行性验证】事件日 T -> T+1 开盘买 -> T+20 收盘卖（未扣成本）")
    print("=" * 100)
    print(f"E2 事件 {len(e2)} 天；沪深300 覆盖 {len(idx)} 天"
          f"（{min(idx)} ~ {max(idx)}）")

    # ── A 全市场等权（原口径）──
    with open(EVENT_JSON, encoding="utf-8") as f:
        raw = json.load(f)
    adates = sorted(raw)
    a_rets, a_gaps = [raw[d]["pct_mean"] for d in adates], [raw[d].get("gap_mean", 0.0) for d in adates]
    a_idx = {d: i for i, d in enumerate(adates)}
    a_base = _stat([fwd(a_rets, a_gaps, adates, i) for i in range(len(adates))])
    a_ev = _stat([fwd(a_rets, a_gaps, adates, a_idx[d]) for d in e2 if d in a_idx])

    # ── B 剔除开盘涨停 ──
    bdates = sorted(ex)
    b_rets = [ex[d]["pct"] for d in bdates]
    b_gaps = [ex[d]["gap"] or 0.0 for d in bdates]
    b_idx = {d: i for i, d in enumerate(bdates)}
    b_base = _stat([fwd(b_rets, b_gaps, bdates, i) for i in range(len(bdates))])
    b_ev = _stat([fwd(b_rets, b_gaps, bdates, b_idx[d]) for d in e2 if d in b_idx])

    # ── C 沪深300 ──
    cdates = sorted(idx)
    c_rets, c_gaps = [], []
    for i, d in enumerate(cdates):
        b = idx[d]
        pc = idx[cdates[i - 1]]["close"] if i > 0 else b["close"]
        c_rets.append((b["close"] / pc - 1) * 100 if pc else 0.0)
        c_gaps.append((b["open"] / pc - 1) * 100 if pc else 0.0)
    c_idx = {d: i for i, d in enumerate(cdates)}
    c_base = _stat([fwd(c_rets, c_gaps, cdates, i) for i in range(len(cdates))])
    c_ev = _stat([fwd(c_rets, c_gaps, cdates, c_idx[d]) for d in e2 if d in c_idx])

    # ── 买不进规模 ──
    lim_ratios = []
    for d in e2:
        v = ex.get(d)
        if v and v["n"]:
            nxt = ex.get(bdates[b_idx[d] + 1]) if d in b_idx and b_idx[d] + 1 < len(bdates) else None
            if nxt and nxt["n"]:
                lim_ratios.append(nxt["n_open_limit"] / nxt["n"])

    print(f"\n{'口径':<28}{'事件n':>7}{'事件T+20':>11}{'基准T+20':>11}{'差':>10}{'胜率':>8}")
    print("-" * 100)
    rows = [("A 全市场等权(不可执行)", a_ev, a_base),
            ("B 剔除开盘涨停(近似可执行)", b_ev, b_base),
            ("C 沪深300(买300ETF可执行)", c_ev, c_base)]
    for name, ev, base in rows:
        if not ev or not base:
            print(f"{name:<28}{'—':>7}")
            continue
        print(f"{name:<28}{ev['n']:>7}{ev['mean']:>+10.2f}%{base['mean']:>+10.2f}%"
              f"{ev['mean']-base['mean']:>+9.2f}pp{ev['win']:>7.1f}%")

    if lim_ratios:
        print(f"\n「买不进」规模：E2 次日**开盘即涨停**的股票占比 "
              f"中位 {np.median(lim_ratios)*100:.1f}%（均值 {np.mean(lim_ratios)*100:.1f}%）")

    # ── 判定 ──
    print(f"\n{'='*100}")
    print("【预登记判定】")
    print(f"{'='*100}")
    if a_ev and b_ev:
        keep = ((b_ev["mean"] - b_base["mean"]) / (a_ev["mean"] - a_base["mean"])
                if (a_ev["mean"] - a_base["mean"]) else 0)
        print(f"(1) 「买不进」损耗：B 保留 A 的 edge {keep:.0%}"
              f"（门槛 >=50%）  => {'可接受' if keep >= 0.5 else '**严重：edge 主要靠买不进的涨停股**'}")
    if c_ev and c_base:
        cd = c_ev["mean"] - c_base["mean"]
        p = block_p([fwd(c_rets, c_gaps, cdates, c_idx[d]) for d in e2 if d in c_idx],
                    c_base["mean"])
        ok = cd >= 1.5 and p is not None and p < 0.05
        no = cd < 0.5
        print(f"(2) 指数可执行性：沪深300 差 {cd:+.2f}pp，P(mean<=基准)={p:.4f}"
              f"  => {'**可作指数级择时（可执行）**' if ok else ('**指数买不到（不可执行）**' if no else '弱')}")
    skipped = [d for d in e2 if d not in c_idx]
    if skipped:
        print(f"(3) 因沪深300 数据起点(2007)跳过的事件：{len(skipped)} 天"
              f"（{skipped[0]} ~ {skipped[-1]}），占 {len(skipped)/len(e2):.1%}")

    # ── 板块分层：edge 落在哪一层？（决定"买什么"）──
    print(f"\n{'='*100}")
    print("【板块分层】E2 后 T+20（各板块自身等权口径）")
    print(f"{'='*100}")
    bd = agg_board()
    bdd = sorted(bd)
    bi = {d: i for i, d in enumerate(bdd)}
    # 板块级跳空未单独聚合，用全市场（B 口径）近似
    b_gaps_s = [(ex[d]["gap"] or 0.0) if d in ex else 0.0 for d in bdd]
    print(f"{'板块':<10}{'事件n':>7}{'事件T+20':>11}{'基准T+20':>11}{'差':>10}{'胜率':>8}")
    print("-" * 100)
    for col in ("沪主板", "深主板", "创业板", "科创板"):
        rr = [bd[d][col] for d in bdd]
        ev = _stat([fwd(rr, b_gaps_s, bdd, bi[d]) for d in e2
                    if d in bi and rr[bi[d]] is not None])
        ba = _stat([fwd(rr, b_gaps_s, bdd, i) for i in range(len(bdd))])
        if not ev or not ba:
            print(f"{col:<10}{'—':>7}")
            continue
        print(f"{col:<10}{ev['n']:>7}{ev['mean']:>+10.2f}%{ba['mean']:>+10.2f}%"
              f"{ev['mean']-ba['mean']:>+9.2f}pp{ev['win']:>7.1f}%")

    print(f"\n注：B 为近似口径（逐日跳过一字板，非买入后不动）；未扣成本 0.3%。")


if __name__ == "__main__":
    main()
