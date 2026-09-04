#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
【文件作用】H6 撮合现实化对照：涨停一字买不进过滤对既有战法回测的影响。

新口径（engine.py 2026-09-05 起）：
  - T+1 开盘涨停且全天未开板 → 信号剔除（实盘买不进）
  - 触发卖出日跌停一字 → 顺延次日开盘成交
旧口径：一切信号按 T+1 开盘价假设可成交。

用法：python scripts/limitup_filter_compare.py
"""

import json
import os
import sys

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)
# 复用 backend/.env
with open(os.path.join(BACKEND_DIR, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

from app.backtest import engine, strategies     # noqa: E402
from app.database import db                     # noqa: E402
import time                                     # noqa: E402

# Supabase pooler 空闲断连 SSL：给 db.fetch 包一层重试
_orig_fetch = db.fetch


def _retry_fetch(sql, params=None, retries=4):
    last = None
    for i in range(retries):
        try:
            return _orig_fetch(sql, params)
        except Exception as e:
            last = e
            try:
                db._reset_pg_conn()
            except Exception:
                pass
            time.sleep(1.5 * (i + 1))
    raise last


db.fetch = _retry_fetch


def run():
    signals = strategies._warfare_signal_stream()
    print(f"signals: {len(signals)}")
    prices_map = strategies._load_prices_map({s["code"] for s in signals})

    # 旧口径：把涨跌停幅度整体关掉（_limit_pct→0 时买入过滤与跌停顺延都不生效）
    orig = engine._limit_pct
    engine._limit_pct = lambda *a, **k: 0
    old_trades = engine.match_signals(signals, prices_map)
    engine._limit_pct = orig

    # 新口径：真实涨跌停约束 + 收集被剔除信号
    skipped = []
    new_trades = engine.match_signals(signals, prices_map, skipped_out=skipped)

    def stat(trades):
        n = len(trades)
        if not n:
            return {"n": 0}
        wins = [t for t in trades if t["pnl_pct"] > 0]
        return {"n": n, "win": round(len(wins) / n * 100, 1),
                "avg": round(sum(t["pnl_pct"] for t in trades) / n, 3)}

    print("\n== 全部战法 ==")
    print("旧口径:", json.dumps(stat(old_trades), ensure_ascii=False))
    print("新口径:", json.dumps(stat(new_trades), ensure_ascii=False))
    print(f"剔除（涨停一字买不进）: {len(skipped)} 条 "
          f"({len(skipped) / max(1, len(old_trades)) * 100:.1f}% of 旧口径成交)")

    # 被剔除信号若按旧口径的假想收益（排队买进会怎样）
    if skipped:
        sk_keys = {(s["code"], s["signal_date"]) for s in skipped}
        would_have = [t for t in old_trades
                      if (t["code"], t["signal_date"]) in sk_keys]
        print("被剔除信号的假想收益:",
              json.dumps(stat(would_have), ensure_ascii=False))

    # 分战法
    by = {}
    for t in old_trades:
        by.setdefault(t["strategy"], {"old": [], "new": []})["old"].append(t)
    for t in new_trades:
        by.setdefault(t["strategy"], {"old": [], "new": []})["new"].append(t)
    print("\n== 分战法（旧 → 新） ==")
    for name, g in sorted(by.items()):
        so, sn = stat(g["old"]), stat(g["new"])
        skipped_n = sum(1 for s in skipped
                        if s["strategy"] == name)
        print(f"{name}: n {so.get('n')}→{sn.get('n')} (剔{skipped_n}) "
              f"胜率 {so.get('win')}%→{sn.get('win')}% "
              f"均 {so.get('avg')}%→{sn.get('avg')}%")


if __name__ == "__main__":
    run()
