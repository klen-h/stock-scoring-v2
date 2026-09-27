#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""nb（neutral_bearish）态序列化，扩展到 10 年（全市场宽度）。

判据与生产 `_apply_bearish_refine` 同源（state==neutral 且 ma_trend==down，满足其一）：
  1. 宽度恶化：up_ratio < 0.40，或 跌停≥20 且 跌停>涨停
  2. 沪深300 近 2 个交易日累计下跌（降级判据）
  3. （外围恐慌历史不可得，省略 —— 已知缺口，与第 1 步 ② 一致）
再连续 2 日确认（raw_nb 连续 2 日 → 进入；连续 2 日不满足 → 退出）。

产出 data/nb_history.json：{date: {state_3, ma_trend, up_ratio, limit_up, limit_down,
raw_nb, nb, state_4}}。★ 宽度从 zzshare 全市场算（5919 只，比第 1 步 ① 的 839~3236
部分市场更准）；涨停阈值用 9.9 近似（与生产 _market_breadth_now 同口径，20cm/30cm 有偏差）。
"""
import json
import os
import sqlite3
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

ZZSHARE_DB = os.path.join(ROOT, "data", "zzshare_daily.db")
IDX_CACHE = os.path.join(ROOT, "data", "sh000300_daily.json")

from app.backtest.market_regime import detect_market_regime  # noqa: E402


def load_index_bars():
    with open(IDX_CACHE, encoding="utf-8") as f:
        return json.load(f)


def build_breadth(conn, start):
    """全市场每日宽度：up_ratio / limit_up / limit_down（9.9 阈值近似）。"""
    rows = conn.execute(
        "SELECT date, "
        "SUM(CASE WHEN pct_chg > 0 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN pct_chg < 0 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN pct_chg >= 9.9 THEN 1 ELSE 0 END), "
        "SUM(CASE WHEN pct_chg <= -9.9 THEN 1 ELSE 0 END) "
        "FROM daily WHERE date >= ? GROUP BY date ORDER BY date", (start,)).fetchall()
    m = {}
    for date, up, down, lu, ld in rows:
        m[date] = {"up_ratio": round(up / (up + down), 4) if (up + down) > 0 else 0.5,
                   "limit_up": lu, "limit_down": ld}
    return m


def _index_down_2d(idx_close, date):
    """沪深300 近 2 个交易日累计下跌（date 当日收盘 < 往前第 3 根收盘）。"""
    dates = sorted(d for d in idx_close if d <= date)
    if len(dates) < 3:
        return False
    return idx_close[dates[-1]] < idx_close[dates[-3]]


def main():
    bars = load_index_bars()
    states = detect_market_regime(bars)
    state_map = {s.date: {"state": s.state, "ma_trend": s.ma_trend,
                          "volatility_regime": s.volatility_regime,
                          "regime_score": s.regime_score} for s in states}
    idx_close = {b["date"]: b["close"] for b in bars}
    print(f"detect_market_regime 覆盖 {len(state_map)} 天")

    conn = sqlite3.connect(ZZSHARE_DB)
    breadth = build_breadth(conn, "2016-01-01")
    conn.close()
    print(f"宽度序列 {len(breadth)} 天")

    out = {}
    prev_raw = None
    prev_nb = False
    for date in sorted(state_map):
        s = state_map[date]
        br = breadth.get(date, {})
        up_ratio = br.get("up_ratio")
        limit_up = br.get("limit_up", 0) or 0
        limit_down = br.get("limit_down", 0) or 0

        raw_nb = False
        if s["state"] == "neutral" and s["ma_trend"] == "down":
            if up_ratio is not None and up_ratio < 0.40:
                raw_nb = True
            elif limit_down >= 20 and limit_down > limit_up:
                raw_nb = True
            elif _index_down_2d(idx_close, date):
                raw_nb = True

        # 连续 2 日确认（rolling，与 _apply_bearish_refine 同策略）
        nb = prev_nb
        if raw_nb and prev_raw:
            nb = True
        elif not raw_nb and not prev_raw:
            nb = False

        out[date] = {"state_3": s["state"], "ma_trend": s["ma_trend"],
                     "volatility_regime": s["volatility_regime"],
                     "regime_score": s["regime_score"],
                     "up_ratio": up_ratio, "limit_up": limit_up, "limit_down": limit_down,
                     "raw_nb": raw_nb, "nb": nb}
        prev_raw = raw_nb
        prev_nb = nb

    for date, d in out.items():
        d["state_4"] = "neutral_bearish" if d["nb"] else d["state_3"]

    out_path = os.path.join(ROOT, "data", "nb_history.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)

    c = Counter(d["state_4"] for d in out.values())
    print(f"四态分布: {dict(c)}")
    print(f"✓ 已存 {out_path}")


if __name__ == "__main__":
    main()
