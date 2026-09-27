#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""事件驱动信号源 · 第 1 层：全市场事件代理原始序列构建（21 年）。

【为什么需要它】
  regime 引擎（`backtest/market_regime.py`）只用沪深300 的 MA20/MA60 + ADX + ATR，
  在 V 型反转处**系统性滞后**（实测 2024-09-24 政策底暴涨、2024-09-30 涨停潮
  均被判 defensive）。事件驱动信号源的定位就是**用宽度/情绪先行条件对冲均线滞后**，
  而不是再做一个独立策略。

【数据源】data/zzshare_daily.db（全市场 1672 万行，2005-01-04 ~ 今）
  字段：pct_chg（涨跌幅）、amount（成交额）、high_limit/low_limit（**真实涨跌停价**）、
        is_st（ST 5% 限幅）、is_paused（停牌）
  ★ 用 high_limit/low_limit 精确判定涨跌停 —— 解决 9.9 阈值对 20cm（创业板/科创板）
    与 5% ST 股的失真（生产 `_market_breadth_now` 用 9.9 近似，这里是更准口径）。

【产出】data/event_history.json
  {date: {n, up, down, up_ratio, limit_up, limit_down, limit_up_exact, limit_down_exact,
          amount, pct_mean, pct_median}}（升序日期）
  仅产出**客观原始序列**，不在本脚本定义事件阈值（阈值在检验脚本里预登记，
  防止"看着数据挑阈值"）。

用法：python scripts/build_event_history.py [--start 2005-01-01]
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
OUT_PATH = os.path.join(ROOT, "data", "event_history.json")

# 个股代码前缀（排除指数 000300.SH / 399xxx.SZ 等）
# zzshare daily.code 为后缀式：000001.SZ / 601155.SH / 688xxx.SH / 8xxxxx.BJ
STOCK_FILTER = ("(code LIKE '60%.SH' OR code LIKE '68%.SH' OR code LIKE '00%.SZ' "
                "OR code LIKE '30%.SZ' OR code LIKE '%.BJ')")


def build_raw(start: str) -> dict:
    """全市场逐日聚合（一次扫表）。"""
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("PRAGMA query_only = ON")
    # 9.9 口径（与生产同源，用于对照）+ high_limit 精确口径
    rows = conn.execute(f"""
        SELECT date,
               COUNT(*) AS n,
               SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END) AS up,
               SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END) AS down,
               SUM(CASE WHEN pct_chg >= 9.9 THEN 1 ELSE 0 END) AS limit_up_99,
               SUM(CASE WHEN pct_chg <= -9.9 THEN 1 ELSE 0 END) AS limit_down_99,
               SUM(CASE WHEN high_limit > 0 AND close >= high_limit * 0.9995
                        THEN 1 ELSE 0 END) AS limit_up_exact,
               SUM(CASE WHEN low_limit > 0 AND close <= low_limit * 1.0005
                        THEN 1 ELSE 0 END) AS limit_down_exact,
               SUM(CASE WHEN is_st = 1 THEN 1 ELSE 0 END) AS n_st,
               SUM(amount) AS amount,
               AVG(pct_chg) AS pct_mean,
               AVG(CASE WHEN pre_close > 0 THEN open / pre_close - 1 END)
                   * 100 AS gap_mean
        FROM daily
        WHERE {STOCK_FILTER} AND is_paused = 0 AND pct_chg IS NOT NULL
          AND date >= ?
        GROUP BY date ORDER BY date
    """, (start,)).fetchall()
    conn.close()

    out = {}
    for r in rows:
        (date, n, up, down, lu99, ld99, luex, ldex, n_st, amount,
         pct_mean, gap_mean) = r
        up = up or 0
        down = down or 0
        out[date] = {
            "n": n or 0,
            "up": up,
            "down": down,
            "up_ratio": round(up / (up + down), 4) if (up + down) > 0 else 0.5,
            "limit_up_99": lu99 or 0,
            "limit_down_99": ld99 or 0,
            "limit_up": luex or 0,        # ★ 精确口径（真实涨跌停价）
            "limit_down": ldex or 0,
            "n_st": n_st or 0,
            "amount": round((amount or 0) / 1e8, 1),   # 亿元
            "pct_mean": round(pct_mean or 0, 3),
            # ★ T+1 开盘相对 T 收盘的跳空（等权）。事件驱动检验必须扣掉它，
            #   否则「大涨日之后」的收益被系统性高估（实际开盘买不到昨收价）。
            "gap_mean": round(gap_mean or 0, 3),
        }
    return out


def _sanity(raw: dict, diag: bool = False) -> None:
    """关键案例核对（方向可信性），与 `_report_step1a_breadth` 同源案例。"""
    cases = [("2024-02-05", "流动性危机千股跌停"),
             ("2024-09-24", "924 政策底暴涨"),
             ("2024-09-30", "930 涨停潮"),
             ("2024-10-08", "1008 冲高回落"),
             ("2026-09-24", "阴跌日")]
    print("\n关键案例核对：")
    print(f"  {'日期':<12}{'事件':<16}{'n':>6}{'up_ratio':>10}"
          f"{'涨停(精)':>9}{'涨停(9.9)':>10}{'跌停(精)':>9}{'成交额(亿)':>11}")
    for d, label in cases:
        v = raw.get(d)
        if not v:
            print(f"  {d:<12}{label:<16}  （无数据）")
            continue
        print(f"  {d:<12}{label:<16}{v['n']:>6}{v['up_ratio']:>10.4f}"
              f"{v['limit_up']:>9}{v['limit_up_99']:>10}{v['limit_down']:>9}"
              f"{v['amount']:>11.0f}")

    if not diag:
        return
    # ★ 口径诊断：两种涨停/跌停口径差异大（2024-02-05 精确跌停 1415 vs 9.9口径 30；
    #   2024-09-30 精确涨停 941 vs 3095）⇒ 必须查清 high_limit/low_limit 是否与
    #   close 同口径（原始价 vs 复权价），否则事件代理建立在错误口径上。
    conn = sqlite3.connect(ZZSHARE_DB)
    conn.execute("PRAGMA query_only = ON")
    for d in ("2024-02-05", "2024-09-30", "2026-09-24"):
        print(f"\n--- {d} 字段明细 ---")
        print("  close<=low_limit 样本:",
              conn.execute("SELECT code, close, pre_close, pct_chg, low_limit, high_limit, factor "
                           "FROM daily WHERE date = ? AND close <= low_limit * 1.0005 LIMIT 4",
                           (d,)).fetchall())
        print("  close>=high_limit 样本:",
              conn.execute("SELECT code, close, pre_close, pct_chg, low_limit, high_limit, factor "
                           "FROM daily WHERE date = ? AND close >= high_limit * 0.9995 LIMIT 4",
                           (d,)).fetchall())
        print("  创业板样本:",
              conn.execute("SELECT code, close, pre_close, pct_chg, low_limit, high_limit "
                           "FROM daily WHERE date = ? AND code LIKE ? LIMIT 3",
                           (d, "30%.SZ")).fetchall())
        for thr in (-9.9, -19.9):
            n = conn.execute("SELECT COUNT(*) FROM daily WHERE date = ? AND pct_chg <= ?",
                             (d, thr)).fetchone()[0]
            print(f"  pct_chg<={thr}: {n}")
    conn.close()


def main():
    ap = argparse.ArgumentParser(description="事件代理原始序列构建")
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--diag", action="store_true", help="输出涨跌停口径诊断明细")
    args = ap.parse_args()

    print(f"[events] 扫 zzshare 全市场（>= {args.start}）…")
    raw = build_raw(args.start)
    print(f"[events] 产出 {len(raw)} 个交易日：{min(raw)} ~ {max(raw)}")

    _sanity(raw, diag=args.diag)

    # 分布概览（供检验脚本预登记阈值时参考）
    urs = sorted(v["up_ratio"] for v in raw.values())
    lds = sorted(v["limit_down"] for v in raw.values())
    lus = sorted(v["limit_up"] for v in raw.values())
    amt = sorted(v["amount"] for v in raw.values())

    def _pct(arr, p):
        return arr[min(len(arr) - 1, int(len(arr) * p))] if arr else 0

    print("\n分布（分位）：")
    print(f"  up_ratio : p1={_pct(urs,0.01):.3f} p5={_pct(urs,0.05):.3f} "
          f"p50={_pct(urs,0.50):.3f} p95={_pct(urs,0.95):.3f} p99={_pct(urs,0.99):.3f}")
    print(f"  跌停家数 : p50={_pct(lds,0.50):.0f} p90={_pct(lds,0.90):.0f} "
          f"p99={_pct(lds,0.99):.0f} max={lds[-1] if lds else 0}")
    print(f"  涨停家数 : p50={_pct(lus,0.50):.0f} p90={_pct(lus,0.90):.0f} "
          f"p99={_pct(lus,0.99):.0f} max={lus[-1] if lus else 0}")
    print(f"  成交额(亿): p50={_pct(amt,0.50):.0f} p95={_pct(amt,0.95):.0f}")

    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False)
    print(f"\n✓ 已存 {OUT_PATH}（{len(raw)} 天）")


if __name__ == "__main__":
    main()
