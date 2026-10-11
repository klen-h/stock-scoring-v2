#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】**market_regime 判市框架的增量价值** —— 预登记检验
（判据 = `PLAN_NEXT.md` §C1【附 A】**v1.0，2026-10-11 用户确认门槛后冻结**。
  本脚本只是判据的机械执行者，**不拥有判据**。）
================================================================================
【为什么有它】regime（ADX/MA 趋势规则）**门控权重、仓位、战法白名单**——评分 KILL 之后，
  它是全项目最大的未检验假设。且评分检验的窗口无进攻市，而本检验用**回填重算**的 regime
  覆盖 2023-08~2026-10（696 判定日，**offensive 206 天**）——能回答评分够不到的市况。

【主判据（唯一，冻结）】跟随 regime 的 20 日窗口**增量**
  · 仓位规则（冻结）：regime(t) ∈ {offensive, neutral} ⇒ 次日 1.0；defensive ⇒ 次日 0.5。
    ★ 防前视：regime 用 t 日收盘判 ⇒ 仓位作用于 t+1。
  · 市场收益：可取价池 A 股（0/3/6）等权日收益（SQL 侧 LAG，单源 `backtest_prices`）。
  · 增量1 = Σ (w−1)×r（vs 永远满仓）；增量2 = Σ (w−0.5)×r（vs 永远半仓）。
  · 单元 = **不重叠 20 日窗口**（时间不重叠 ⇒ 近似独立）；按段分解仅描述性。
  · **GO**(PASS)：窗口增量1 均值 ≥ +1.0pt 且 单侧 P ≤ 0.05 且 增量2 均值 > 0
    （必须同时跑赢满仓与半仓）；**KILL**(FAIL)：增量1 ≤ 0 且 95% 上界 ≤ +0.50pt
    （等效性式 —— 同评分 KILL 的教训）；**样本闸**：≥25 窗口 且 日均池 ≥ 500 只。
【已知局限】回填 regime 属"非实时口径"（与实时记录重叠段一致率 100% 已验证，但历史参数
  可能与当时不同）；仓位规则 0.5 是本预登记的选择（其它规则 = 探索性）；未扣分红复权。
================================================================================
"""
import argparse
import json
import os
import random
import sys
from typing import Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "backend"))
for _line in open(os.path.join(ROOT, "backend", ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# ── 预登记常量（PLAN_NEXT.md §C1【附 A】v1.0）──
HOLD = 20
GO_PT = 1.0
GO_P = 0.05
KILL_CI_PT = 0.50
MIN_WINDOWS = 25
MIN_POOL = 500
BOOT_N = 10000
BOOT_SEED = 20261011
COST_SWITCH_PT = 0.15      # 每次仓位切换成本（0.5 换手 × 0.30% 往返）
DEFENSIVE_W = 0.5          # 防御日仓位（冻结；其它规则 = 探索性）


def trading_days() -> List[str]:
    from app.database import db
    rows = db.fetch("SELECT DISTINCT date FROM backtest_prices WHERE code = 'sh000300' "
                    "ORDER BY date ASC") or []
    return [str(r["date"])[:10] for r in rows]


def regime_map() -> Dict[str, str]:
    """回填重算 regime（确定性规则 × 沪深300 bars）。★ 标注"非实时口径"（PLAN_NEXT 附 A）。"""
    from app.database import db
    from app.backtest.market_regime import detect_market_regime
    rows = db.fetch("SELECT date, open, high, low, close, volume FROM backtest_prices "
                    "WHERE code = 'sh000300' ORDER BY date ASC") or []
    bars = [{"date": str(r["date"])[:10], "open": r["open"], "high": r["high"],
             "low": r["low"], "close": r["close"], "volume": r["volume"] or 0}
            for r in rows]
    states = detect_market_regime(bars)
    return {s.date: s.state for s in states}


def pool_daily_returns(start: str) -> Dict[str, dict]:
    """每日可取价池 A 股等权收益 + 观测数（SQL 侧 LAG，egress ≈ 数百行）。"""
    from app.database import db
    rows = db.fetch("""
        SELECT date, AVG(ret) AS r, COUNT(ret) AS n FROM (
            SELECT date, code,
                   CASE WHEN LAG(close) OVER w > 0
                        THEN close / LAG(close) OVER w - 1.0 END AS ret
            FROM backtest_prices
            WHERE date >= %s AND LEFT(code, 1) IN ('0', '3', '6')
            WINDOW w AS (PARTITION BY code ORDER BY date)
        ) t WHERE ret IS NOT NULL GROUP BY date ORDER BY date
    """, (start,)) or []
    return {str(r["date"])[:10]: {"r": float(r["r"]), "n": int(r["n"])} for r in rows}


def _pctile(sorted_vals: List[float], q: float) -> float:
    return sorted_vals[min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))]


def bootstrap(excess: List[float]) -> Dict[str, float]:
    rng = random.Random(BOOT_SEED)
    n = len(excess)
    means = []
    for _ in range(BOOT_N):
        s = 0.0
        for _ in range(n):
            s += excess[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return {"p_pos": sum(1 for m in means if m <= 0.0) / BOOT_N,
            "up95": _pctile(means, 0.95),
            "lo2.5": _pctile(means, 0.025), "hi97.5": _pctile(means, 0.975)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="末行输出结构化结论（edge_verify 契约）")
    args = ap.parse_args()

    cal = trading_days()
    reg = regime_map()
    first_reg = min(reg)                                   # 回填可判定的第一天
    series: List[dict] = []                                # 有仓位定义的日期序列
    for i in range(1, len(cal)):
        d, prev = cal[i], cal[i - 1]
        if prev not in reg:
            continue
        w = DEFENSIVE_W if reg[prev] == "defensive" else 1.0
        series.append({"date": d, "w": w, "state_prev": reg[prev]})

    pool = pool_daily_returns(first_reg)
    for s in series:
        p = pool.get(s["date"])
        s["r"] = p["r"] if p else None
        s["n"] = p["n"] if p else 0
    series = [s for s in series if s["r"] is not None]
    if not series:
        print("[regime] 无可评估日期")
        return 0

    # 切成不重叠 20 日窗口（末尾不足一段丢弃）
    windows = [series[i:i + HOLD] for i in range(0, len(series) - HOLD + 1, HOLD)]
    w_ex1 = [sum((x["w"] - 1.0) * x["r"] for x in wd) * 100 for wd in windows]
    w_ex2 = [sum((x["w"] - 0.5) * x["r"] for x in wd) * 100 for wd in windows]
    pool_min = min(min(x["n"] for x in wd) for wd in windows)
    pool_avg = sum(x["n"] for wd in windows for x in wd) / sum(len(wd) for wd in windows)
    switches = sum(1 for a, b in zip(series, series[1:]) if a["w"] != b["w"])

    mean1 = sum(w_ex1) / len(w_ex1)
    mean2 = sum(w_ex2) / len(w_ex2)
    st = bootstrap(w_ex1)
    cost_total = switches * COST_SWITCH_PT
    net_mean = mean1 - cost_total / len(w_ex1)

    print("=== market_regime 判市框架 · 增量检验（判据 = PLAN_NEXT §C1 附 A v1.0）===")
    print("回填 regime：%d 个判定日（非实时口径，与实时记录重叠段一致率 100%% 已验证）"
          % len(reg))
    print("不重叠 %d 日窗口 %d 个（%.0f 天）｜池日均 %.0f 只（最小 %d）｜仓位切换 %d 次"
          % (HOLD, len(windows), len(windows) * HOLD, pool_avg, pool_min, switches))

    if len(w_ex1) < MIN_WINDOWS or pool_avg < MIN_POOL:
        print("\n[T+20 增量] INSUFFICIENT：%d/%d 窗口、日均池 %d/%d —— 不打印收益数字"
              % (len(w_ex1), MIN_WINDOWS, pool_avg, MIN_POOL))
        verdict = "INSUFFICIENT"
        detail = "样本不足（%d/%d 窗口）" % (len(w_ex1), MIN_WINDOWS)
    else:
        print("\n[增量检验] vs 永远满仓：均值 %+.3fpt/20日（净 %+.3fpt，扣切换成本）｜ "
              "单侧 P(μ>0) = %.4f ｜ 95%%上界 = %+.3fpt ｜ 95%%CI = [%+.3f, %+.3f]pt"
              % (mean1, net_mean, st["p_pos"], st["up95"], st["lo2.5"], st["hi97.5"]))
        print("           vs 永远半仓：均值 %+.3fpt/20日（GO 要求 > 0）" % mean2)
        same_dir = (mean1 > 0) == (mean2 > 0) or mean2 == 0
        is_go = mean1 >= GO_PT and st["p_pos"] <= GO_P and mean2 > 0
        is_kill = mean1 <= 0 and st["up95"] <= KILL_CI_PT
        verdict = "PASS" if is_go else ("FAIL" if is_kill else "PARTIAL")
        word = {"PASS": "GO", "FAIL": "KILL"}.get(verdict, "PARTIAL")
        print("  ⇒ 判定：**%s**（%s）" % (word, verdict))
        detail = ("跟随 regime 20 日增量 vs 满仓 %+.2fpt（P=%.4f，上界 %+.2f）vs 半仓 %+.2fpt ⇒ %s"
                  % (mean1, st["p_pos"], st["up95"], mean2, verdict))

        # 描述性：按 regime 状态分解（不产生结论）
        by_state = {}
        for x in series:
            by_state.setdefault(x["state_prev"], []).append((x["w"] - 1.0) * x["r"] * 100)
        print("  [描述性·不产生结论] 各状态下相对满仓的日贡献：")
        for k in sorted(by_state):
            v = by_state[k]
            print("    %-16s %4d 天  合计 %+.2fpt" % (k, len(v), sum(v)))
        if not same_dir:
            print("  [warn] 增量2 方向与增量1 不一致（GO 已按判据拦截）")
    print("（口径提醒：仓位规则 defensive⇒0.5 为预登记选择；回填 regime 属非实时口径；"
          "基准=可取价池等权。）")

    if args.json:
        print(json.dumps({"script": "regime_edge_check", "verdict": verdict,
                          "n": len(w_ex1), "need": MIN_WINDOWS, "unit": "20日窗口",
                          "detail": detail}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
