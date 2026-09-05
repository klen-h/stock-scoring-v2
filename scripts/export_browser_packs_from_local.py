#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【文件作用】从本地 backend-pack.db 导出浏览器消费的两个包（Phase 2 本地验收用）：
  - kline-pack-latest.json.gz   {version:2, date, stocks:{code:{name,market_cap,klines[150]}}}
  - indicators-pack.json.gz     {version:1, date, indicators:{code:{...,_series}}}
  正式包由 GitHub Actions 产出（generate_backend_pack.py / generate-kline-pack.py）；
  本脚本仅用于 Pages 包尚未更新时的本地端到端验收。
用法：python scripts/export_browser_packs_from_local.py [--n 120]
"""

import argparse
import gzip
import json
import os
import sqlite3
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
PACK_DB = os.path.join(BACKEND_DIR, "data", "pack", "backend-pack.db")
OUT_DIR = os.path.join(BACKEND_DIR, "data", "kline")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=120, help="导出市值前 N 只（浏览器测试池）")
    args = ap.parse_args()

    sys.path.insert(0, BACKEND_DIR)
    os.environ.setdefault("DATA_SOURCE", "local")
    from app.scoring.indicator_cache import compute_latest_indicators

    conn = sqlite3.connect(PACK_DB)
    codes = conn.execute(
        "SELECT code, name, market_cap FROM codes WHERE market_cap > 0 "
        "ORDER BY market_cap DESC LIMIT ?", (args.n,)).fetchall()
    date_str = conn.execute(
        "SELECT value FROM meta WHERE key='pack_date'").fetchone()[0]

    stocks, indicators = {}, {}
    for code, name, cap in codes:
        bars = conn.execute(
            "SELECT date, open, high, low, close, volume FROM klines "
            "WHERE code=? ORDER BY date ASC", (code,)).fetchall()
        if len(bars) < 60:
            continue
        stocks[code] = {
            "name": name, "market_cap": cap,
            "klines": [list(b) for b in bars[-150:]],
        }
        dict_bars = [{"date": b[0], "open": b[1], "high": b[2], "low": b[3],
                      "close": b[4], "volume": b[5]} for b in bars]
        ind = compute_latest_indicators(dict_bars)
        if ind and ind.get("_series"):
            indicators[code] = ind

    os.makedirs(OUT_DIR, exist_ok=True)

    kline_pack = {"version": 2, "date": date_str, "stocks": stocks}
    with gzip.open(os.path.join(OUT_DIR, "kline-pack-latest.json.gz"), "wt",
                   encoding="utf-8") as f:
        json.dump(kline_pack, f, ensure_ascii=False, separators=(",", ":"))

    ind_pack = {"version": 1, "date": date_str, "indicators": indicators}
    with gzip.open(os.path.join(OUT_DIR, "indicators-pack.json.gz"), "wt",
                   encoding="utf-8") as f:
        json.dump(ind_pack, f, ensure_ascii=False, separators=(",", ":"))

    print(f"导出完成（pack 日期 {date_str}）：K线 {len(stocks)} 只 / 指标 {len(indicators)} 只")
    for fn in ("kline-pack-latest.json.gz", "indicators-pack.json.gz"):
        p = os.path.join(OUT_DIR, fn)
        print(f"  {p} ({os.path.getsize(p) / 1048576:.1f} MB)")


if __name__ == "__main__":
    main()
