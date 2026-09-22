#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】指数「冲高回落」形态的预测力检验（2026-09-23）
================================================================================
背景：
  2026-09-23 给 `flash/intraday_alerts` 新增了「指数冲高回落」盘中告警
  （`_REVERSAL_WATCH`：日内最高涨幅 ≥ 门槛 且 现价距日高 ≤ -门槛 ⇒ 推企微 +
  LLM 解读）。当时的阈值频率检验只回答了「会不会天天响」（答：约每月 2~4 条），
  但**没回答「响了有没有用」** —— 本脚本补这一问。

判定标准（预先定死，防止事后挑格子；与 flow5_pos_backtest 同纪律）：
  · 主判定（T+1）：A 组（冲高回落）均值 ≤ B 组（冲高守住）均值 − 0.30pct
    且 A 组 T+1 胜率 < 45%  ⇒ 支持「该形态携带弱势信息」（值得盘中提醒）
  · 反向判定：|A − B| < 0.15pct 且胜率差 < 3pct ⇒ 无预测力 ⇒ 功能保留但
    **只当情绪提示**（应回落期望值，不再暗示"该减仓"）
  · 其余 ⇒ 弱信号（结论归档，不调阈值不调语义）
  · 副判定（T+5）：同主判定标准，用于交叉验证（T+1 可能是噪声、T+5 才是趋势）

方法：
  - 数据：`backtest_prices` 的 sh000300 全历史日线（含 high/low）
    ★ 为什么用沪深300 而非上证：该表**没有 sh000001**（实测 bars=0）；
      两者形态特征同量级，结论可迁移（生产规则监控上证/创业板/科创50）
  - 信号（与生产同口径，日线模拟）：peak = (high − prev_close)/prev_close ≥ 0.8%
    且 low_drop = (low − high)/high ≤ −1.5%
    ⚠️ 生产是 3 分钟轮询、本脚本用日线 low ⇒ 本口径是**触发上限**（部分极短插针
      生产可能漏掉），故样本数略偏多
  - 入场点 = 触发日**收盘**（提醒在盘中发出，用户实际应对多在当天尾盘/次日）
  - 对照四组：
      A 冲高回落 (peak≥阈值 且 low_drop≤−阈值)   ← 信号
      B 冲高守住 (peak≥阈值 且 low_drop>−阈值)   ← **最相关对照**：同样冲过高，
                                                    唯一差别是守没守住
      D 没冲高   (peak<阈值)                      ← 平淡日
      C 全样本                                    ← 市场基准（控制样本期趋势）
  - 细分（副）：A 内部按触发日**收盘红绿**拆 A1（收绿）/A2（收红）—— 检验
    「跌破昨收的派发」是否比「红盘回踩」更弱

实测结论（2026-09-23 首跑；sh000300 748 根日线，2023-08-23 .. 2026-09-22）：
  group                        n |  T+1 mean   win% |  T+5 mean   win%
  A 冲高回落（触发）          110 |  +0.19%   51.8% |  +0.57%   50.0%
  B 冲高守住（对照）          127 |  +0.09%   52.4% |  +0.35%   54.4%
  D 没冲高                    510 |  -0.01%   50.2% |  +0.02%   49.9%
  C 全样本                    747 |  +0.03%   50.8% |  +0.16%   50.7%
  ⇒ 主判定 **NO EDGE**（A−B：T+1 +0.10pct / 胜率 −0.56pct；T+5 +0.22pct / −4.4pct）
  ⇒ 结论：**该形态本身不携带看跌信息**（与「冲高回落=派发」的直觉相反）。
     功能保留，但定位为「**盘面结构提示**」而非卖出信号 —— 已在
     `intraday_alerts._reversal_llm_note` 的 prompt 里**显式写入**此结论，
     禁止 LLM 据此给减仓/看跌建议（防「把无预测力的形态当交易信号用」）。
  副发现（样本小，只记录不行动）：A 组内**触发日收绿**的 16 个样本 T+1 +0.88% /
     胜率 81.2%（短线超跌反弹），但 T+5 转负（−0.13% / 37.5%）⇒ 疑似「反弹一天即
     衰竭」；n=16 噪声大，不设规则。
  局限（下结论时须记住）：①日线 low 是**触发上限**口径（3 分钟轮询实际触发更少）；
     ②相邻触发日样本重叠 ⇒ 独立样本 < 110；③样本期仅 3 年、未跨市况；
     ④多重比较（看了 4 个持有期）⇒ 故以**预登记**的 T+1 主判定为准。

用法：python scripts/reversal_edge_check.py [--code sh000300] [--peak 0.8]
      [--drop 1.5] [--hold 1 3 5]
================================================================================
"""

import argparse
import os
import sys
from typing import Dict, List, Optional

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()
sys.path.insert(0, BACKEND_DIR)


def load_bars(code: str) -> List[Dict]:
    """读指数日线（升序）。"""
    from app.database import db
    rows = db.fetch(
        "SELECT date, high, low, close FROM backtest_prices WHERE code = %s "
        "ORDER BY date ASC", (code,))
    out = []
    for r in rows or []:
        h, l, c = float(r.get("high") or 0), float(r.get("low") or 0), float(r.get("close") or 0)
        if h > 0 and c > 0:
            out.append({"date": str(r.get("date"))[:10], "high": h,
                        "low": l or h, "close": c})
    return out


def classify(bars: List[Dict], peak_need: float, drop_need: float) -> List[Dict]:
    """逐日打标（i 从 1 开始：需要前一交易日收盘价）。"""
    tagged = []
    for i in range(1, len(bars)):
        prev_c = bars[i - 1]["close"]
        cur = bars[i]
        if prev_c <= 0:
            continue
        peak = (cur["high"] - prev_c) / prev_c * 100          # 日内最高涨幅
        low_drop = (cur["low"] - cur["high"]) / cur["high"] * 100   # 盘中最低距日高
        close_pct = (cur["close"] - prev_c) / prev_c * 100
        if peak >= peak_need and low_drop <= -drop_need:
            group = "A"
        elif peak >= peak_need:
            group = "B"
        else:
            group = "D"
        tagged.append({"i": i, "date": cur["date"], "group": group,
                       "peak": round(peak, 2), "low_drop": round(low_drop, 2),
                       "close_pct": round(close_pct, 2)})
    return tagged


def fwd_ret(bars: List[Dict], i: int, n: int) -> Optional[float]:
    """触发日收盘 → 之后第 n 个交易日收盘的收益（%）。"""
    if i + n >= len(bars):
        return None
    c0, c1 = bars[i]["close"], bars[i + n]["close"]
    return (c1 / c0 - 1) * 100 if c0 else None


def stat(vals: List[Optional[float]]) -> Optional[Dict]:
    vals = [v for v in vals if v is not None]
    n = len(vals)
    if not n:
        return None
    s = sorted(vals)
    return {"n": n, "mean": sum(vals) / n,
            "median": s[n // 2],
            "win": sum(1 for v in vals if v > 0) / n * 100,
            "max": s[-1], "min": s[0]}


def _fmt(st: Optional[Dict]) -> str:
    if not st:
        return "      -        -     - "
    return "%+6.2f%%  %6.2f%%  %5.1f%%" % (st["mean"], st["median"], st["win"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", default="sh000300")
    ap.add_argument("--peak", type=float, default=0.8, help="冲高门槛（日内最高涨幅 %%）")
    ap.add_argument("--drop", type=float, default=1.5, help="回撤门槛（距日高 %%，正数）")
    ap.add_argument("--hold", type=int, nargs="+", default=[1, 3, 5])
    args = ap.parse_args()

    bars = load_bars(args.code)
    if len(bars) < 60:
        print("数据不足:", len(bars))
        return
    tagged = classify(bars, args.peak, args.drop)
    print("code=%s  bars=%d  %s .. %s" % (
        args.code, len(bars), bars[0]["date"], bars[-1]["date"]))
    print("signal: peak >= +%.2f%%  AND  low_drop <= -%.2f%%  (enter at close)" % (
        args.peak, args.drop))
    print()

    # ── 四组对照 ──
    groups = {
        "A  reversal (trigger)": [t for t in tagged if t["group"] == "A"],
        "B  held     (control)": [t for t in tagged if t["group"] == "B"],
        "D  no-surge": [t for t in tagged if t["group"] == "D"],
        "C  all days": tagged,
    }
    header = "%-24s %5s |" % ("group", "n")
    for h in args.hold:
        header += "  T+%-2d mean   median   win%% |" % h
    print(header)
    print("-" * len(header))
    stats = {}
    for name, rows in groups.items():
        line = "%-24s %5d |" % (name, len(rows))
        for h in args.hold:
            st = stat([fwd_ret(bars, t["i"], h) for t in rows])
            stats[(name, h)] = st
            line += " " + _fmt(st) + " |"
        print(line)
    print()

    # ── A − B 差值（主判定材料）──
    print("A - B  diff (reversal minus held):")
    for h in args.hold:
        a, b = stats.get(("A  reversal (trigger)", h)), stats.get(("B  held     (control)", h))
        if a and b:
            print("  T+%-2d  mean %+6.2f pct   win %+6.2f pct" % (
                h, a["mean"] - b["mean"], a["win"] - b["win"]))
    print()

    # ── A 内部分解：触发日收盘红/绿 ──
    a_rows = groups["A  reversal (trigger)"]
    a_green = [t for t in a_rows if t["close_pct"] < 0]   # A1 收绿（跌破昨收）
    a_red = [t for t in a_rows if t["close_pct"] >= 0]    # A2 收红（红盘回踩）
    print("A breakdown: A1 close<0 (n=%d) vs A2 close>=0 (n=%d)" % (len(a_green), len(a_red)))
    for nm, rows in (("A1 close<0", a_green), ("A2 close>=0", a_red)):
        line = "  %-12s %5d |" % (nm, len(rows))
        for h in args.hold:
            line += " " + _fmt(stat([fwd_ret(bars, t["i"], h) for t in rows])) + " |"
        print(line)
    print()

    # ── 主判定 ──
    h1 = args.hold[0]
    a, b = stats.get(("A  reversal (trigger)", h1)), stats.get(("B  held     (control)", h1))
    print("=== verdict (pre-registered) ===")
    if not a or not b:
        print("  insufficient samples")
        return
    dm, dw = a["mean"] - b["mean"], a["win"] - b["win"]
    if dm <= -0.30 and a["win"] < 45:
        print("  SUPPORT: reversal carries weakness (T+%d diff %+.2f pct, win %.1f%%)"
              % (h1, dm, a["win"]))
    elif abs(dm) < 0.15 and abs(dw) < 3:
        print("  NO EDGE: A ~= B (diff %+.2f pct, win diff %+.2f pct) => keep as "
              "sentiment alert only" % (dm, dw))
    else:
        print("  WEAK: diff %+.2f pct / win diff %+.2f pct => archive, no tuning"
              % (dm, dw))
    for h in args.hold[1:]:
        a2, b2 = stats.get(("A  reversal (trigger)", h)), stats.get(("B  held     (control)", h))
        if a2 and b2:
            print("  [cross-check T+%d] diff %+.2f pct / win %+.2f pct"
                  % (h, a2["mean"] - b2["mean"], a2["win"] - b2["win"]))


if __name__ == "__main__":
    main()
