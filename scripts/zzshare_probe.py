#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】zzshare 接口可用性探测（token 级复测，2026-09-21）
================================================================================
背景：`zzshare功能盘点与接入建议_20260920.md` §3 路线图 ①——9-20 探测时是周日
（匿名 + 非交易日 ⇒ T0 情绪套件全 0 行）。用户已领 ZZSHARE_TOKEN（本地 .env +
Render 已配），本脚本用 token 对 T0~T3 关键接口做全面复测，输出可用性汇总，
作为后续接入排期（尤其 T0 情绪套件、T3 finance_pit）的依据。

用途（可复跑）：
  · 本轮：token 级复测（工作日 + token，两条件首次同时满足）
  · 后续：接入前小样本实测 / 定期影子验证（选型原则 1：先 30 天影子验证再转正）

用法：python scripts/zzshare_probe.py [--group T0 T1 T2 T3 MISC]
================================================================================
"""
import argparse
import json
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.zzshare_client import get_api  # noqa: E402

# 上一个交易日（周一探测时，当日数据可能盘后才出 → 先用上周五保证有收盘数据）
D = "2026-09-18"

# (分组, 名称, 调用) —— 参数来自 pip 包 SHORTCUTS 源码核对（0.4.11）：
#   market_sentiment/market_style/sentiment_* 需要**日期参数**（首轮无参调用是 0 行主因）；
#   sentiment_timing 描述明写「需 sentiment_vip 权限」；
#   finance_pit(table, trade_date) / finance_valuation(date_value) / stock_uplimit_reason(stock_code, date)
PROBES = [
    ("T0", "sentiment_timing",        lambda a: a.sentiment_timing(date1=D, date2="2026-09-21")),
    ("T0", "sentiment_trend",         lambda a: a.sentiment_trend(model=1, date1=D)),
    ("T0", "sentiment_trend_range",   lambda a: a.sentiment_trend_range(model=1, date1="2026-09-07", date2=D)),
    ("T0", "market_sentiment",        lambda a: a.market_sentiment(date1=D, date2="2026-09-21")),
    ("T0", "market_hot_sentiment",    lambda a: a.market_hot_sentiment(date1=D)),
    ("T0", "market_style",            lambda a: a.market_style(date1=D)),
    ("T0", "uplimit_trend",           lambda a: a.uplimit_trend(date1=D)),
    ("T0", "updown_distribution",     lambda a: a.updown_distribution(date1=D)),
    ("T1", "review_uplimit_hot_step", lambda a: a.review_uplimit_hot_step(date1=D)),
    ("T1", "uplimit_stocks",          lambda a: a.uplimit_stocks(date1=D)),
    ("T1", "uplimit_hot",             lambda a: a.uplimit_hot(date1=D)),
    ("T1", "review_uplimit_reason",   lambda a: a.review_uplimit_reason(date1=D)),
    ("T1", "stock_uplimit_reason",    lambda a: a.stock_uplimit_reason(stock_code="000001", date=D)),
    ("T2", "lhb_list",                lambda a: a.lhb_list(date1=D)),
    ("T2", "lhb_detail",              lambda a: a.lhb_detail(date1="20260918", stock_code="000001")),
    ("T2", "lhb_trader_history",      lambda a: a.lhb_trader_history(trader_name="量化基金")),
    ("T3", "finance_pit",             lambda a: a.finance_pit(table="income", trade_date=D)),
    ("T3", "finance_pit(indicator)",  lambda a: a.finance_pit(table="indicator", trade_date=D)),
    ("T3", "finance_valuation",       lambda a: a.finance_valuation(date_value=D)),
    ("MISC", "query stock/moneyflow", lambda a: a.query("stock/moneyflow", {"ts_code": "000001.SZ"})),
]


def _rows(res):
    """统一取行数（list / dict / DataFrame 兼容）。"""
    if res is None:
        return 0
    if isinstance(res, list):
        return len(res)
    if isinstance(res, dict):
        return len(res)
    rows = getattr(res, "to_dict", None)
    if rows:                                   # pandas DataFrame
        return len(res)
    try:
        return len(res)
    except TypeError:
        return 1


def _sample(res, limit=140):
    try:
        if isinstance(res, list) and res:
            return json.dumps(res[0], ensure_ascii=False, default=str)[:limit]
        if isinstance(res, dict):
            return json.dumps(res, ensure_ascii=False, default=str)[:limit]
        return str(res)[:limit]
    except Exception:
        return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", nargs="+", default=None)
    args = ap.parse_args()

    api = get_api()
    print("=" * 96)
    print(f"zzshare token 级复测   token={'已配置' if os.environ.get('ZZSHARE_TOKEN') else '匿名!'}"
          f"   探测基准日={D}")
    print("=" * 96)
    ok = zero = err = 0
    for grp, name, fn in PROBES:
        if args.group and grp not in args.group:
            continue
        t0 = time.time()
        try:
            res = fn(api)
            n = _rows(res)
            dt = time.time() - t0
            tag = "✓" if n else "⚠ 0行"
            if n:
                ok += 1
            else:
                zero += 1
            print(f"  [{grp}] {name:<26}{tag} n={n:<5} {dt:4.1f}s  样本: {_sample(res)}")
        except Exception as e:
            err += 1
            print(f"  [{grp}] {name:<26}✗ 异常  {time.time()-t0:4.1f}s  {str(e)[:100]}")
        time.sleep(0.5)                        # 温和限流
    print("\n" + "=" * 96)
    print(f"汇总：可用 {ok} ｜ 0 行 {zero} ｜ 异常 {err}")
    print("注：0 行 ≠ 接口坏 —— 可能参数不对/无当日数据/数据范围不含；异常多为参数签名问题，")
    print("    需对照 zzshare README 核参后复测（本脚本只做可用性普查）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
