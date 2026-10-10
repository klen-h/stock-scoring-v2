#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】回填央行储备月度面板（`reserve_monthly`）
================================================================================

数据源：akshare `macro_china_foreign_exchange_gold()`（外管局/央行月度口径）
  列 = ['统计时间', '黄金储备', '国家外汇储备']；统计时间形如 '2026.9'
  单位 = 黄金储备**万盎司** / 外储**亿美元**（与官方发布逐值吻合，2026.9 = 7747 / 34002.51）

用法：python scripts/backfill_reserve_monthly.py [--months 24]
      （默认回填源里全部月份；--months N 只取最近 N 个月）

幂等：ON CONFLICT (month) 覆盖，可重复跑。
⚠️ 源自身存在缺号（如 2025.10~12）⇒ **不插值、不填补**。
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

from app import reserve_monthly  # noqa: E402


def _month(v) -> str:
    """'2026.9' / '2026.09' / '2026-09' → '2026-09'。解析不了返回 ''。"""
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return ""
    if "." in s:
        parts = s.split(".")
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            return "%04d-%02d" % (int(parts[0]), int(parts[1]))
        return ""
    if "-" in s and len(s) >= 7:
        return s[:7]
    return ""


def _num(v):
    """过滤 NaN/空 → float 或 None（NaN 的唯一可靠判据是 v != v）。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--months", type=int, default=0)
    args = ap.parse_args()

    df = ak.macro_china_foreign_exchange_gold()
    print("源行数: %d，列: %s" % (len(df), list(df.columns)))
    rows = []
    for _, r in df.iterrows():
        m = _month(r.get("统计时间"))
        fx = _num(r.get("国家外汇储备"))
        g = _num(r.get("黄金储备"))
        if not m or (fx is None and g is None):
            continue
        rows.append((m, fx, g))
    if args.months:
        rows = rows[-args.months:]
    reserve_monthly.bulk_upsert(rows)
    print("[backfill] reserve_monthly: +%d 行（幂等）" % len(rows))

    st = reserve_monthly.stats()
    print("=== 覆盖: %d 行, %s..%s ===" % (st["n"], st["mn"], st["mx"]))
    for it in reserve_monthly.series_with_share(8):
        print("  %s  外储 %s 亿美元 | 黄金 %s 万盎司 = %s 吨 | 占比 %s%%"
              % (it["month"], it["fx_reserve_100m_usd"], it["gold_reserve_wan_oz"],
                 it["gold_reserve_tonnes"], it["gold_share_pct"]))


if __name__ == "__main__":
    main()
