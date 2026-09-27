#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""尾盘定盘 · 预测力检验：「尾盘走弱」是否预示次日走弱？

判据（尾盘 = 14:30~15:00，6 根 5min）：
  · 尾盘跌幅 = (close_1500 / close_1430 - 1) * 100
  · 尾盘放量 = 尾盘 30min 成交量 / 当日每 30min 均量
信号：尾盘跌幅 ≤ 阈值 且 尾盘放量 ≥ 阈值

检验：对比「尾盘走弱」组 vs 全样本的**次日表现**（次日开盘缺口 / 次日涨跌幅）。
样本：zzshare 日线选活跃股 N 只 × 最近 M 个交易日（分钟数据逐股逐日拉）。
"""
import argparse
import os
import sqlite3
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
for _line in open(os.path.join(ROOT, "backend", ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
from app.zzshare_client import get_api  # noqa: E402


def tail_metrics(df):
    """从 5min K 线（降序）算尾盘指标。返回 (tail_chg, tail_vol_ratio, day_high, close_1500)。"""
    if df is None or len(df) < 8:
        return None
    rows = df.to_dict("records")
    rows.sort(key=lambda r: str(r["trade_time"]))          # 升序
    # 当日均量（每 30min = 6 根）
    vols = [float(r["vol"] or 0) for r in rows]
    total_vol = sum(vols)
    avg_30min = total_vol / (len(rows) / 6) if rows else 0
    # 尾盘 6 根（14:35~15:00）
    tail = rows[-6:]
    t_str = str(tail[0]["trade_time"])[-4:]                # 起始时间 HHMM
    t_1430 = rows[-7] if len(rows) >= 7 else rows[0]
    close_1500 = float(tail[-1]["close"])
    close_1430 = float(t_1430["close"])
    if close_1430 <= 0:
        return None
    tail_chg = (close_1500 / close_1430 - 1) * 100
    tail_vol = sum(float(r["vol"] or 0) for r in tail)
    tail_vol_ratio = (tail_vol / avg_30min) if avg_30min > 0 else 1.0
    day_high = max(float(r["high"]) for r in rows)
    return {"tail_chg": round(tail_chg, 2), "tail_vol_ratio": round(tail_vol_ratio, 2),
            "day_high": day_high, "close_1500": close_1500, "tail_start": t_str}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=20)
    ap.add_argument("--days", type=int, default=40)
    ap.add_argument("--start", default="2026-05-01")
    ap.add_argument("--tail-chg", type=float, default=-1.0, help="尾盘跌幅阈值 %")
    ap.add_argument("--tail-vol", type=float, default=1.2, help="尾盘放量阈值")
    args = ap.parse_args()

    conn = sqlite3.connect(ZZSHARE_DB)
    # 样本：最近交易日成交额 top-N 的非 ST 股
    codes = [r[0] for r in conn.execute(
        "SELECT code FROM daily WHERE date='2026-09-24' AND is_st=0 "
        "ORDER BY amount DESC LIMIT ?", (args.stocks,))]
    days = [r[0] for r in conn.execute(
        "SELECT DISTINCT date FROM daily WHERE date >= ? AND date <= '2026-09-24' "
        "ORDER BY date DESC LIMIT ?", (args.start, args.days))]
    print(f"样本：{len(codes)} 只 × {len(days)} 天 = {len(codes)*len(days)} 次请求")

    # 日线：当日 close、次日 open/close（算次日表现）
    day_close = {}
    for c in codes:
        rows = conn.execute("SELECT date, close FROM daily WHERE code=? ORDER BY date", (c,)).fetchall()
        day_close[c] = {d: v for d, v in rows}
    code_dates = {c: sorted(day_close[c]) for c in codes}
    idx_of = {c: {d: i for i, d in enumerate(code_dates[c])} for c in codes}

    api = get_api()
    records = []
    t0 = time.time()
    for i, code in enumerate(codes):
        for d in days:
            if d not in idx_of[code]:
                continue
            try:
                df = api.stk_mins(ts_code=code, trade_time=d.replace("-", ""), freq="5min")
            except Exception:
                continue
            m = tail_metrics(df)
            if not m:
                continue
            # 次日表现
            i0 = idx_of[code][d]
            if i0 + 1 >= len(code_dates[code]):
                continue
            nd = code_dates[code][i0 + 1]
            nxt = day_close[code][nd]
            cur = day_close[code][d]
            next_ret = (nxt / cur - 1) * 100 if cur else None
            records.append({"code": code, "date": d, **m, "next_ret": next_ret,
                            "is_tail_weak": m["tail_chg"] <= args.tail_chg
                                            and m["tail_vol_ratio"] >= args.tail_vol})
        if (i + 1) % 5 == 0:
            print(f"  {i+1}/{len(codes)} 只, 已 {len(records)} 条, 耗时 {(time.time()-t0)/60:.1f}min")
    conn.close()

    # 检验
    weak = [r for r in records if r["is_tail_weak"]]
    other = [r for r in records if not r["is_tail_weak"]]
    print(f"\n总样本 {len(records)} 条；尾盘走弱 {len(weak)} 条（{len(weak)/len(records)*100 if records else 0:.1f}%）")
    if records:
        chgs = sorted(r["tail_chg"] for r in records)
        vr = sorted(r["tail_vol_ratio"] for r in records)
        m = len(chgs)
        print(f"tail_chg 分布(%): min={chgs[0]} p25={chgs[m//4]} p50={chgs[m//2]} p75={chgs[3*m//4]} max={chgs[-1]}")
        print(f"tail_vol_ratio: min={vr[0]} p50={vr[m//2]} max={vr[-1]}")

    def _stat(rs):
        if not rs:
            return "无样本"
        rets = [r["next_ret"] for r in rs if r["next_ret"] is not None]
        n = len(rets)
        if not n:
            return "无有效次日"
        win = sum(1 for x in rets if x > 0) / n * 100
        return f"n={n}, 次日均收益 {sum(rets)/n:+.2f}%, 胜率 {win:.1f}%"

    print(f"尾盘走弱组: {_stat(weak)}")
    print(f"其余组:     {_stat(other)}")
    ex_w = [r["next_ret"] for r in weak if r["next_ret"] is not None]
    ex_o = [r["next_ret"] for r in other if r["next_ret"] is not None]
    if ex_w and ex_o:
        print(f"★ 差值（走弱 − 其余）= {sum(ex_w)/len(ex_w) - sum(ex_o)/len(ex_o):+.2f}%"
              f"（若显著为负 ⇒ 尾盘走弱预示次日更差，判据有效）")


if __name__ == "__main__":
    main()
