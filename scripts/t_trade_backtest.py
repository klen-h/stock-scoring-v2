#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】做T 策略回测 —— 用日K 验证「支撑买 / 阻力卖」有没有空间
================================================================================
【用户需求】（2026-11-15，确认"先回测验证再上线"）

【⚠️⚠️ 这个回测能证明什么、不能证明什么（读结论前必看）】

  ✅ 能证明：**这段行情里，日内振幅是否经常能覆盖"支撑→阻力"的空间**
     —— 也就是"有没有肉"。
  ❌ 不能证明：**实盘能不能吃到**。原因有三条硬约束：
     1. 日K 只有 low/high，**无法判断"先跌到支撑"还是"先涨到阻力"**
        ⇒ 本脚本按"low ≤ 支撑 且 high ≥ 阻力"就算可做，
          这是**乐观上界**（假设最低点买、最高点卖）；
     2. 项目没有逐笔/Level2，只有分钟线 ⇒ 无法模拟真实撮合；
     3. 涨停/跌停、流动性、滑点都未建模。
  ⇒ **结论只能当"上限估计"，不能当收益预期。** 输出里会重复这句。

【口径】
  · 信号用 **T-1 及之前** 的日线算（`build_plan(bars[:-1], price=T-1收盘)`），
    **绝不含 T 日**，避免未来函数；
  · 只有 `feasible=True` 的日子才计入分母（否则"没空间"的日子会稀释统计）；
  · 仓位按 `ratio`（默认 1/3，与 `t_trade.suggest_ratio` 同源）折算到总资产；
  · 成本按**做T 金额**双边扣（默认 0.10%，可 `--cost` 覆盖）。

【用法】
    python scripts/t_trade_backtest.py --code 000429
    python scripts/t_trade_backtest.py --code 000429 --days 250 --cost 0.1
    python scripts/t_trade_backtest.py --code 000429 --json
================================================================================
"""
import argparse
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))


def _load_bars(code: str, days: int = 400):
    """从 `backtest_prices` 取日线（个股用**裸码**，如 000429）。"""
    from app.database import db
    # ⚠️ 本表个股是裸 6 位码；指数才带前缀（sh000016）。先试裸码，再试带前缀兜底。
    for cand in (code, "sz" + code, "sh" + code):
        rows = db.fetch(
            "SELECT date, open, high, low, close, volume FROM backtest_prices "
            "WHERE code=%s ORDER BY date DESC LIMIT %s", (cand, days)) or []
        if rows:
            bars = [{"date": str(r["date"])[:10],
                     "open": r["open"], "high": r["high"],
                     "low": r["low"], "close": r["close"],
                     "volume": r["volume"]} for r in rows]
            bars.reverse()      # 转升序
            name = db.fetch_one(
                "SELECT name FROM backtest_prices WHERE code=%s LIMIT 1", (cand,)) or {}
            return bars, cand, str(name.get("name") or "")
    return [], code, ""


def _scan_market(args) -> int:
    """按 ATR% 分档统计（回答"哪些股票适合做T"）。

    ★ 为什么必须分档而不是看单只：
      2026-11-15 实测 68 只（随机抽样，seed=42）：
        ATR <2%   胜率 12.5%  净贡献 **-2.29%**   跑赢持有 33%
        ATR 2-4%  胜率 18.4%  净贡献 +2.24%       跑赢 65%
        ATR 4-7%  胜率 20.9%  净贡献 +2.95%       跑赢 **89%**
        ATR >7%   胜率 34.6%  净贡献 +3.69%       跑赢 17%（持有涨太多）
      ⇒ **低波动股做T 是净亏损**；中高波动才有正贡献。
      这正是"先做价值股、为高波动铺路"要拿数据确认的事。
    """
    import random
    from app import t_trade
    from app.database import db

    rows = db.fetch(
        "SELECT code, name, COUNT(1) AS n FROM backtest_prices "
        "WHERE code NOT LIKE 'sh%%' AND code NOT LIKE 'sz%%' "
        "GROUP BY code, name HAVING COUNT(1) >= 320 ORDER BY code") or []
    pool = [(str(r["code"]), str(r["name"])) for r in rows]
    random.seed(42)
    sample = random.sample(pool, min(args.sample, len(pool)))
    print("候选池 %d 只，抽样 %d 只" % (len(pool), len(sample)))

    min_len = max(t_trade.T_SUPPORT_LOOKBACK, t_trade.T_ATR_PERIOD) + 2
    res = []
    for code, name in sample:
        rs = db.fetch(
            "SELECT date, high, low, close FROM backtest_prices WHERE code=%s "
            "ORDER BY date DESC LIMIT %s", (code, args.days)) or []
        if len(rs) < min_len + 20:
            continue
        bars = [{"date": str(x["date"])[:10], "high": x["high"], "low": x["low"],
                 "close": x["close"]} for x in rs]
        bars.reverse()
        a0, px0 = t_trade.atr(bars[-80:]), (bars[-1]["close"] or 0)
        if not (a0 and px0):
            continue
        buys = wins = 0
        net = 0.0
        ratio = t_trade.T_RATIO_DEFAULT
        for i in range(min_len, len(bars)):
            hist, t = bars[:i], bars[i]
            prev = hist[-1]["close"]
            if not prev:
                continue
            p = t_trade.build_plan(code, name, hist, price=prev)
            if not p.get("feasible"):
                continue
            sup, res_p, lo, hi, cl = (p["support"], p["resistance"],
                                      t["low"], t["high"], t["close"])
            ratio = p["ratio"]
            if not (lo and hi and lo <= sup):
                continue
            buys += 1
            if hi >= res_p:
                wins += 1
                g = (res_p - sup) / sup * 100
            else:
                g = ((cl or sup) - sup) / sup * 100
            net += (g - args.cost) * p["ratio"]
        if buys < 5:
            continue
        base_c = bars[min_len - 1]["close"]
        hold = (bars[-1]["close"] / base_c - 1) * 100 if base_c else 0
        res.append({"code": code, "atr_pct": round(a0 / px0 * 100, 2), "buys": buys,
                    "win_rate": round(wins / buys * 100, 1), "net": round(net, 2),
                    "ratio": ratio, "hold_x_ratio": round(hold * ratio, 2)})

    print("有效样本 %d 只\n" % len(res))
    bands = [("<2%", 0, 2), ("2-4%", 2, 4), ("4-7%", 4, 7), (">7%", 7, 999)]
    print("%-7s %-5s %-9s %-11s %-13s %-9s %s"
          % ("ATR档", "样本", "平均胜率", "平均净贡献", "持有×ratio", "跑赢比例", "平均触发"))
    print("-" * 80)
    for label, lo, hi in bands:
        sub = [x for x in res if lo <= x["atr_pct"] < hi]
        if not sub:
            continue
        n = len(sub)
        wr = sum(x["win_rate"] for x in sub) / n
        nt = sum(x["net"] for x in sub) / n
        hd = sum(x["hold_x_ratio"] for x in sub) / n
        beat = sum(1 for x in sub if x["net"] > x["hold_x_ratio"]) / n * 100
        b = sum(x["buys"] for x in sub) / n
        print("%-7s %-5d %-9.1f %-+11.2f %-+13.2f %-8.0f%% %.0f"
              % (label, n, wr, nt, hd, beat, b))
    print()
    print("⚠️ 同 `--code` 口径：日K 只能验证\"当日是否触及\"，best 是乐观上界。")
    print("⚠️ 对照列 `持有×ratio` 是同一比例满仓持有的贡献，用来判断做T 是否跑赢\"躺着\"。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="000429", help="6 位代码")
    ap.add_argument("--days", type=int, default=400, help="取多少根日线")
    ap.add_argument("--cost", type=float, default=0.10, help="做T 双边成本 %%（按做T 金额）")
    ap.add_argument("--ratio", type=float, default=None, help="仓位比例（默认用 t_trade）")
    ap.add_argument("--scan-market", action="store_true",
                    help="按 ATR%% 分档扫描全市场（回答\"哪些股票适合做T\"）")
    ap.add_argument("--sample", type=int, default=70, help="--scan-market 的抽样只数")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.scan_market:
        return _scan_market(args)

    from app import t_trade

    bars, used_code, name = _load_bars(args.code, args.days)
    if not bars:
        print("✗ 取不到日线：%s（查 backtest_prices）" % args.code)
        return 1

    min_len = max(t_trade.T_SUPPORT_LOOKBACK, t_trade.T_ATR_PERIOD) + 2
    if len(bars) < min_len + 5:
        print("✗ 日线不足：%d 根 < %d 根" % (len(bars), min_len + 5))
        return 1

    trades = []          # 每笔"理论可做T"
    no_space_days = 0    # feasible=False 的日子
    scanned = 0

    # T 从第 min_len 根开始（保证前面有足够历史算 60 日前低/ATR）
    for i in range(min_len, len(bars)):
        hist = bars[:i]              # ★ 只到 T-1，绝不含 T
        t_bar = bars[i]
        prev_close = hist[-1]["close"]
        if not prev_close:
            continue
        scanned += 1

        plan = t_trade.build_plan(used_code, name, hist,
                                  price=prev_close, ratio=args.ratio)
        if not plan.get("feasible"):
            no_space_days += 1
            continue

        sup, res = plan["support"], plan["resistance"]
        lo, hi = t_bar.get("low"), t_bar.get("high")
        close = t_bar.get("close")
        if not (lo and hi):
            continue

        # ★★ 只统计"成功触及阻力"会严重高估（原版就犯了这个错）。
        #   改为**买入触发即计入**，出场分两种口径：
        #     · base（基准，可执行）：买在支撑、**收盘平仓**（若没到阻力）
        #     · best（乐观上界）：买在支撑、**卖在阻力**
        #   ⚠️ 两种都假设"能在支撑价买到"——日K 无法验证真实撮合。
        if lo <= sup:
            if hi >= res:
                gross, kind = (res - sup) / sup * 100, "hit_res"
            else:
                gross, kind = ((close or sup) - sup) / sup * 100, "flat_close"
            cost = args.cost
            ratio = plan["ratio"]
            trades.append({
                "date": t_bar["date"], "support": sup, "resistance": res,
                "low": lo, "high": hi, "close": close,
                "kind": kind,
                "gross_pct": round(gross, 2),                       # 基准（收盘平/到阻力）
                "best_pct": round((res - sup) / sup * 100, 2),      # 乐观上界
                "net_contrib_pct": round((gross - cost) * ratio, 3),
                "best_contrib_pct": round(((res - sup) / sup * 100 - cost) * ratio, 3),
                "ratio": ratio,
                "space_atr_mult": plan["space_atr_mult"],
            })

    wins = [x for x in trades if x["kind"] == "hit_res"]
    flats = [x for x in trades if x["kind"] == "flat_close"]
    total_net = sum(x["net_contrib_pct"] for x in trades)
    total_best = sum(x["best_contrib_pct"] for x in trades)
    # 对照：这段时间**一直持有**的收益（否则无法判断做T 是正贡献还是负贡献）
    hold_pct = None
    if len(bars) > min_len:
        base_c = bars[min_len - 1]["close"]
        if base_c:
            hold_pct = round((bars[-1]["close"] / base_c - 1) * 100, 2)

    result = {
        "code": used_code, "name": name,
        "bars": len(bars), "period": "%s ~ %s" % (bars[0]["date"], bars[-1]["date"]),
        "scanned": scanned,
        "feasible_days": scanned - no_space_days,
        "buy_days": len(trades),
        "hit_res_days": len(wins),
        "flat_close_days": len(flats),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else 0,
        "trade_rate_pct": round(len(trades) / scanned * 100, 1) if scanned else 0,
        "avg_gross_pct": round(st.mean([x["gross_pct"] for x in trades]), 2) if trades else 0,
        "median_gross_pct": round(st.median([x["gross_pct"] for x in trades]), 2) if trades else 0,
        "total_net_contrib_pct": round(total_net, 3),      # 基准：到阻力 / 收盘平
        "total_best_contrib_pct": round(total_best, 3),    # 乐观：全部卖在阻力
        "hold_pct": hold_pct,                              # 对照：一直持有
        "cost_pct": args.cost,
        "caveat": ("日K 口径：只能验证\"当日是否触及\"，无法验证撮合顺序与滑点；"
                   "best 列是乐观上界，不是收益预期"),
    }

    if args.json:
        print(json.dumps({**result, "trades": trades[-10:]}, ensure_ascii=False, indent=2))
        return 0

    print("=" * 76)
    print("做T 回测  %s %s" % (used_code, name))
    print("=" * 76)
    print("  区间        %s（%d 根日线）" % (result["period"], len(bars)))
    print("  扫描日数    %d（前 %d 根用于预热历史/ATR）" % (scanned, min_len))
    print("  有空间日数  %d（feasible=True）" % result["feasible_days"])
    print("  买入触发日  %d（%.1f%%）" % (result["buy_days"], result["trade_rate_pct"]))
    if trades:
        print("    其中到阻力 %d / 收盘平 %d  ⇒ **胜率 %.1f%%**"
              % (result["hit_res_days"], result["flat_close_days"], result["win_rate_pct"]))
        print("  单次幅度    平均 %.2f%% / 中位 %.2f%%"
              % (result["avg_gross_pct"], result["median_gross_pct"]))
        print()
        print("  仓位折算净贡献（扣 %.2f%% 成本，ratio 见下表）" % args.cost)
        print("    基准口径（到阻力/收盘平）  %+.2f%%" % result["total_net_contrib_pct"])
        print("    乐观上界（全部卖在阻力）    %+.2f%%" % result["total_best_contrib_pct"])
        if result["hold_pct"] is not None:
            print("    对照：一直持有            %+.2f%%" % result["hold_pct"])
        print()
        print("  最近 5 次：")
        for x in trades[-5:]:
            mark = "到阻力" if x["kind"] == "hit_res" else "收盘平"
            print("    %s  %s  支撑 %.2f / 阻力 %.2f（%.2f×ATR）  当日 %.2f~%.2f  "
                  "%+.2f%%（乐观 %+.2f%%）  净 %+.3f%%"
                  % (x["date"], mark, x["support"], x["resistance"], x["space_atr_mult"],
                     x["low"], x["high"], x["gross_pct"], x["best_pct"],
                     x["net_contrib_pct"]))
    print()
    print("  ⚠️ %s" % result["caveat"])
    print("  ⚠️ 未建模：涨跌停、滑点、流动性、真实撮合顺序。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
