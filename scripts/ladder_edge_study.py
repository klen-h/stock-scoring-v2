# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】事件研究：连板梯队「断层 / 高度塌陷」是否真是退潮前兆？（2026-09-25）
================================================================================
用户建议（review 意见三，建议插队）：
  > 历史快照里已有全部日期的 ban_info，项目里也有自算的"昨涨停溢价"⇒
  > 直接做事件研究：「出现断层（或高度 ≤2）的日期，次日昨涨停溢价均值」vs
  > 「无断层日期」——给"断层是退潮前兆"这个经验判断一个量化历史胜率，
  > 也是情绪模型校准最缺的一环，且零新数据源。

【为什么用 backtest_prices 自算而不用落库的 ban_info】
  `zz_daily_snapshots` 只积累了 5 天（2026-09-18 起）⇒ 样本不足以做事件研究。
  `backtest_prices` 有 ~750 个交易日 ⇒ **自算连板**（9.5% 近似口径，与
  `routers/market.market_emotion` 完全同源）才能拿到足够样本。

⚠️ 口径局限（必须随结论一起读，否则会误用）：
  · `backtest_prices` 只覆盖**回填过的股票**（实测约 839 只，非全市场）
    ⇒ 绝对家数会少于官方口径；**但两支样本同一口径 ⇒ 组间对比仍有效**（这是本研究的用途）；
  · 涨停用 **≥9.5% 近似**（10cm 主板口径，20cm/30cm/ST 未细分）⇒ 会混入
    创业板涨 9.6% 之类非涨停股（与线上情绪卡同一近似，故结论可迁移到线上）；
  · "次日溢价" = **前一交易日涨停股在当日**的平均涨跌幅（= 赚钱效应口径）。

用法：python scripts/ladder_edge_study.py [--days 60]
================================================================================
"""
import argparse
import io
import os
import statistics
import sys
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

from app.database import db            # noqa: E402

LIMIT_APPR = 0.095          # 涨停近似阈值（与线上市场情绪同口径）
COLLAPSE_MAX = 1            # 高度塌陷：最高板 ≤ 1（无连板）


def load_series(days: int):
    """取最近 days 个交易日的 (date -> {code: close})。"""
    ds = db.fetch("SELECT DISTINCT date FROM backtest_prices ORDER BY date DESC LIMIT %s",
                  (days + 12,)) or []
    dates = sorted(str(x["date"])[:10] for x in ds)
    if len(dates) < 5:
        return [], {}
    d0 = dates[0]
    rows = db.fetch("SELECT date, code, close FROM backtest_prices WHERE date >= %s",
                    (d0,)) or []
    by_date = defaultdict(dict)
    for r in rows:
        try:
            by_date[str(r["date"])[:10]][str(r["code"])] = float(r["close"] or 0)
        except (TypeError, ValueError):
            continue
    return dates, by_date


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60, help="回看交易日数（默认 60）")
    args = ap.parse_args()

    dates, by_date = load_series(args.days)
    if not dates:
        print("backtest_prices 数据不足")
        return 1
    print("样本期 %s ~ %s（%d 个交易日）" % (dates[0], dates[-1], len(dates)))
    print("每日股票数 ≈ %d（注意：非全市场，绝对家数不可与官方口径对比）"
          % (len(by_date.get(dates[-1]) or {}) or 0))

    # ── 逐日计算：涨停名单 / 连板高度 / 断层 / 塌陷 ──
    day_info = {}
    streak_codes = {}            # code -> 当前连续涨停天数
    prev_limit = []              # 前一日涨停名单
    for i, d in enumerate(dates):
        cur = by_date.get(d) or {}
        prev_map = by_date.get(dates[i - 1]) if i > 0 else None
        limit_today, levels = [], defaultdict(int)
        for code, close in cur.items():
            if not prev_map:
                break
            pc = prev_map.get(code)
            if not pc or pc <= 0 or close <= 0:
                continue
            if close / pc - 1 < LIMIT_APPR:
                streak_codes[code] = 0
                continue
            limit_today.append(code)
            n = streak_codes.get(code, 0) + 1
            streak_codes[code] = n
            levels[n] += 1
        # 溢价：**前一日**涨停股在**当日**的平均涨跌幅
        gains = []
        if prev_map and prev_limit:
            for c in prev_limit:
                pc = prev_map.get(c)
                cl = cur.get(c)
                if pc and cl and pc > 0:
                    gains.append((cl / pc - 1) * 100)
        max_lvl = max(levels) if levels else 0
        real_max = max((k for k, v in levels.items() if v > 0), default=0)
        gaps = [k for k in range(1, real_max + 1)
                if levels.get(k, 0) == 0 and any(levels.get(j, 0) > 0
                                                 for j in range(k + 1, real_max + 1))]
        day_info[d] = {
            "limit_n": len(limit_today),
            "max_lvl": real_max,
            "gaps": gaps,
            "broken": bool(gaps),
            "collapsed": (real_max <= COLLAPSE_MAX and len(limit_today) >= 15),
            "premium": (round(statistics.fmean(gains), 3) if gains else None),
            "premium_n": len(gains),
        }
        prev_limit = limit_today

    # ── 分组对比：条件日（当天）→ 次日溢价 ──
    #   注意方向：**当天**出现断层/塌陷 ⇒ 看**次日**的溢价（前瞻，非同期）
    def compare(label, pred):
        a = [day_info[dates[i + 1]]["premium"] for i in range(len(dates) - 1)
             if pred(day_info[dates[i]]) and day_info[dates[i + 1]]["premium"] is not None]
        b = [day_info[dates[i + 1]]["premium"] for i in range(len(dates) - 1)
             if not pred(day_info[dates[i]]) and day_info[dates[i + 1]]["premium"] is not None]
        if not a or not b:
            print("  %-16s 样本不足（命中 %d / 对照 %d）" % (label, len(a), len(b)))
            return None
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        wa = sum(1 for x in a if x > 0) / len(a) * 100
        wb = sum(1 for x in b if x > 0) / len(b) * 100
        print("  %-16s 命中 n=%-3d 次日溢价 %+6.3f%%（胜率 %4.1f%%） | "
              "对照 n=%-3d %+6.3f%%（胜率 %4.1f%%） | 差 %+6.3fpt"
              % (label, len(a), ma, wa, len(b), mb, wb, ma - mb))
        return {"n": len(a), "mean": ma, "win": wa, "base_n": len(b), "base_mean": mb,
                "base_win": wb, "diff": ma - mb}

    print("\n=== 事件研究：条件日 → **次日**昨涨停溢价（前瞻口径）===")
    print("（溢价 = 前一交易日涨停股在次日的平均涨跌幅；单位 %）")
    res = {}
    res["broken"] = compare("梯队断层", lambda x: x["broken"])
    res["collapse"] = compare("高度塌陷(≤1板)", lambda x: x["collapsed"])
    res["low(≤2板)"] = compare("高度≤2板", lambda x: x["max_lvl"] <= 2 and x["limit_n"] >= 15)
    res["high(≥4板)"] = compare("高度≥4板", lambda x: x["max_lvl"] >= 4)

    print("\n=== 逐日明细（最近 25 天）===")
    print("  日期        涨停 最高板 断层 塌陷  次日溢价")
    for i, d in enumerate(dates[-26:-1]):
        nxt = day_info[dates[dates.index(d) + 1]]["premium"] if d in dates else None
        j = day_info[d]
        print("  %s  %3d   %2d    %-4s %-4s  %s"
              % (d, j["limit_n"], j["max_lvl"], j["gaps"] or "-",
                 "是" if j["collapsed"] else "-",
                 ("%+.3f%%" % nxt) if nxt is not None else "—"))
    print("\n⚠️ 样本期 %d 天、覆盖率仅回填股票（~839 只）⇒ 结论是**提示性**的，"
          "不要当强证据；样本 >120 天后应重跑复核。" % len(dates))
    return 0


if __name__ == "__main__":
    sys.exit(main())
