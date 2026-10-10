#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】一次性回填「外盘日频收盘」到 `macro_daily_history`（akshare）
================================================================================

背景：见 `backend/app/macro_daily.py` 头部注释（黄金/商品联动研究的数据前置）。

用法：python scripts/backfill_macro_daily.py [--years 5]

数据源（akshare，本机已装 1.18.78；**不在** backend/requirements.txt，避免拖慢 Actions）：
  · COMEX金(GC) / 伦敦金(XAU) / WTI(CL) / 布伦特(OIL) —— ak.futures_foreign_hist（新浪源）
  · 美债 2Y/10Y —— ak.bond_zh_us_rate()
  ★ 美元指数(DXY) 无稳定免费日频源 ⇒ 不回填，由 macro_history 的 15:03 快照逐日累积
    （见 store.append_macro_history 的转存钩子）；这是**已知数据缺口**。

幂等：ON CONFLICT (code,date) 覆盖，可重复跑。
⚠️ 本脚本连 Supabase 写库；写的是**新增表**（macro_daily_history），不影响任何现有表。
================================================================================
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
env = os.path.join(ROOT, "backend", ".env")
if os.path.exists(env):
    for line in open(env, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

import akshare as ak  # noqa: E402

from app import macro_daily  # noqa: E402


def _fut(symbol: str, code: str, start: str) -> None:
    df = ak.futures_foreign_hist(symbol=symbol)
    pairs = []
    for _, row in df.iterrows():
        d = str(row["date"])[:10]
        if d < start:
            continue
        c = row.get("close")
        if c and float(c) > 0:
            pairs.append((d, float(c)))
    macro_daily.bulk_upsert(code, pairs)
    print(f"[backfill] {code:10s} ({symbol}): +{len(pairs)} 行")


def _bond(start: str) -> None:
    df = ak.bond_zh_us_rate()
    date_col = "日期" if "日期" in df.columns else df.columns[0]
    cols = {"us2y": "美国国债收益率2年", "us10y": "美国国债收益率10年"}
    for code, cn in cols.items():
        if cn not in df.columns:
            print(f"[backfill] {code:10s}: 缺列 {cn}，跳过")
            continue
        pairs = []
        for _, row in df.iterrows():
            d = str(row[date_col])[:10]
            if d < start:
                continue
            c = row.get(cn)
            if c is None or str(c) in ("", "nan"):
                continue
            if float(c) > 0:
                pairs.append((d, float(c)))
        macro_daily.bulk_upsert(code, pairs)
        print(f"[backfill] {code:10s} ({cn}): +{len(pairs)} 行")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    args = ap.parse_args()
    start = f"{2026 - args.years}-01-01"
    print(f"回填起点 {start} ...")
    for sym, code in [("GC", "gold_com"), ("XAU", "gold_spot"),
                      ("CL", "wti"), ("OIL", "brent")]:
        try:
            _fut(sym, code, start)
        except Exception as e:
            print(f"[FAIL] {sym}: {type(e).__name__}: {str(e)[:120]}")
    try:
        _bond(start)
    except Exception as e:
        print(f"[FAIL] bond: {type(e).__name__}: {str(e)[:120]}")
    print("\n=== 当前覆盖（macro_daily_history）===")
    for code, st in sorted(macro_daily.stats().items()):
        print(f"  {code:10s} {st['n']:5d} 行  {st['mn']}..{st['mx']}")


if __name__ == "__main__":
    main()
