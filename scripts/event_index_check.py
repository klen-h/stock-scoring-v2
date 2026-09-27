#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【事件驱动 · 可执行性补验】市值加权（近指数）口径能否吃到 E2 的 edge？
================================================================================
背景（承接 `event_executability_check.py` §8.2）：
  板块分层显示 edge 随弹性递增（创业板等权 +3.70pp、沪深300 **+0.29pp 不显著**），
  但**分层是等权口径**，而真实 ETF 跟踪**市值加权指数** —— 两者可能差很远。

★ 为什么不用真实指数数据：
  本机对外网 HTTP 受限（东财 `RemoteDisconnected`、系统代理不可用；腾讯源
  `count` 上限远小于全历史所需 5000+ 根）⇒ 改为**用 zzshare 本地数据估算市值加权**：
      流通市值 ≈ 成交额 / (换手率/100)      （数学上成立：换手率=成交量/流通股）
  实测校验：2024-09-30 创业板市值加权 = **+15.85%** vs 真实创业板指 **+15.36%**
  （差 0.5pp，因口径含全部创业板股而非指数 100 只）⇒ 方法可靠。

标的（板块 = 可买宽基 ETF 的近似）：
  创业板 / 科创板 / 沪市 / 深市 / 全市场 —— 各算 **市值加权** 与 **等权** 两口径

━━━━━━━━━━━━━━━━━━ 预登记判定 ━━━━━━━━━━━━━━━━━━
  口径同 §8.2：事件日 T → **T+1 开盘买** → T+20 收盘卖，未扣成本。
  (1) 某板块**市值加权** edge >= +1.5pp 且 bootstrap P < 0.05 ⇒ 该宽基可执行
  (2) 全部市值加权 < +0.5pp ⇒ 小盘 edge 是**等权现象**，市值加权吃不到 ⇒ 无可用标的
  (3) 量化「等权 - 市值加权」的差距（edge 有多少依赖小票暴露）

用法：python scripts/event_index_check.py
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

E2_UP_RATIO = 0.90
E2_LU_RATIO = 0.020
H = 20
B_BOOT = 10000
STOCK_FILTER = ("(code LIKE '60%.SH' OR code LIKE '68%.SH' OR code LIKE '00%.SZ' "
                "OR code LIKE '30%.SZ' OR code LIKE '%.BJ')")

BOARDS = [
    ("全市场", ""),
    ("沪市", "AND (code LIKE '60%.SH' OR code LIKE '68%.SH')"),
    ("深市", "AND (code LIKE '00%.SZ' OR code LIKE '30%.SZ')"),
    ("创业板", "AND code LIKE '30%.SZ'"),
    ("科创板", "AND code LIKE '68%.SH'"),
]


def agg_board(board_filter=""):
    """按日聚合：市值加权 / 等权 的涨跌幅与开盘跳空。

    权重 w = 成交额 / 换手率 ≈ 流通市值（换手率<=0 的行剔除）。
    """
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("PRAGMA query_only = ON")
    extra = f" {board_filter}" if board_filter else ""
    rows = conn.execute(f"""
        SELECT date,
               SUM(pct_chg * w) / SUM(w)                        AS pct_mv,
               SUM(gap * w) / SUM(w)                            AS gap_mv,
               AVG(pct_chg)                                     AS pct_eq,
               AVG(gap)                                         AS gap_eq,
               COUNT(*)                                         AS n
        FROM (
            SELECT date, pct_chg, open / pre_close - 1 AS gap,
                   CASE WHEN turnover_rate > 0 THEN amount / turnover_rate END AS w
            FROM daily
            WHERE {STOCK_FILTER} AND is_paused = 0 AND pct_chg IS NOT NULL
                  AND pre_close > 0{extra}
        )
        WHERE w IS NOT NULL
        GROUP BY date ORDER BY date
    """).fetchall()
    conn.close()
    out = {}
    for date, pct_mv, gap_mv, pct_eq, gap_eq, n in rows:
        out[str(date)] = {
            "pct_mv": pct_mv, "gap_mv": (gap_mv or 0) * 100,
            "pct_eq": pct_eq, "gap_eq": (gap_eq or 0) * 100, "n": n,
        }
    return out


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


def fwd(rets, gaps, i, n=H):
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


def _stat(v):
    v = [x for x in v if x is not None]
    if not v:
        return None
    return {"n": len(v), "mean": sum(v) / len(v),
            "win": sum(1 for x in v if x > 0) / len(v) * 100}


def main():
    e2 = load_e2()
    print("=" * 108)
    print("【市值加权 vs 等权】E2 后 T+20（T+1 开盘买 -> T+20 收盘卖，未扣成本）")
    print("=" * 108)
    print(f"E2 事件 {len(e2)} 天；权重 = 成交额/换手率（≈流通市值）")

    print(f"\n{'板块':<8}{'口径':<10}{'事件n':>7}{'事件T+20':>11}{'基准T+20':>11}"
          f"{'差':>10}{'胜率':>8}{'P':>9}  判定")
    print("-" * 108)
    mv_results = []
    for name, bf in BOARDS:
        ag = agg_board(bf)
        ds = sorted(ag)
        di = {d: i for i, d in enumerate(ds)}
        for label, pk, gk in (("市值加权", "pct_mv", "gap_mv"), ("等权", "pct_eq", "gap_eq")):
            rets = [ag[d][pk] for d in ds]
            gaps = [ag[d][gk] for d in ds]
            ev = _stat([fwd(rets, gaps, di[d]) for d in e2 if d in di])
            ba = _stat([fwd(rets, gaps, i) for i in range(len(ds))])
            if not ev or not ba:
                continue
            p = block_p([fwd(rets, gaps, di[d]) for d in e2 if d in di], ba["mean"])
            diff = ev["mean"] - ba["mean"]
            v = ("可执行" if (diff >= 1.5 and p is not None and p < 0.05)
                 else ("不可执行" if diff < 0.5 else "弱"))
            if pk == "pct_mv":
                mv_results.append((name, diff, p, ev["n"], v))
            print(f"{name:<8}{label:<10}{ev['n']:>7}{ev['mean']:>+10.2f}%{ba['mean']:>+10.2f}%"
                  f"{diff:>+9.2f}pp{ev['win']:>7.1f}%{(p if p is not None else 1):>9.4f}  {v}")

    print(f"\n{'='*108}")
    print("【预登记判定】")
    print(f"{'='*108}")
    ok = [r for r in mv_results if r[4] == "可执行"]
    if ok:
        for r in ok:
            print(f"  ★ 可执行：{r[0]} 市值加权 {r[1]:+.2f}pp（P={r[2]:.4f}，事件 {r[3]} 天）")
    else:
        print("  ★ 没有任何板块的**市值加权**达到 +1.5pp 显著门槛 ⇒ "
              "**市值加权吃不到 E2 的 edge**。")
        print("    ⇒ edge 依赖**等权暴露**（大量小票），宽基 ETF（市值加权）不可执行。")
    print(f"\n  注：市值用「成交额/换手率」估算；未扣成本 0.3%。")


if __name__ == "__main__":
    main()
