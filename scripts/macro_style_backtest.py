#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】「宏观分数符号 → 切风格」回测（用户指定策略，非预登记检验）
================================================================================
【用户需求】（2026-11-14）
  "需要加入上证50，回测一下国家公告自打钩以来，宏观分数是正的买入成长，
   负的买入权重。"

【策略定义（照用户原意，未做任何优化）】
  · 窗口：从「国家公告自动打钩」起点（`2026-10-26`）起，到数据最新日
  · 信号：`macro_daily.data_json.direction.score`（T 日 08:55 盘前生成）
  · 规则：score > 0 ⇒ 持有**成长**（创业板 `sz399006`）
          score < 0 ⇒ 持有**权重**（上证50 `sh000016`）
  · 调仓：每个交易日收盘按当日分数切换，持有到下一交易日
  · 收益：指数收盘 → 收盘，**不含成本**（另附成本敏感度）

【为什么用上证50 而不是沪深300】
  实测同批 `gate_add` 触发日：上证50 -0.83% ｜ 沪深300 -1.85% ⇒
  沪深300 混了中盘，**不是纯权重**；上证50 才是。"加入上证50"正是为此。

【⚠️ 关键局限（结论前必读）】
  1. **样本只有十余个决策日**（打钩期本身只有两周多）——这是"这段时间发生了什么"，
     **不是统计结论**；
  2. 单一行情片段（震荡下行 + 权重护盘），换市况可能完全反过来；
  3. 切换极频繁（实测 8 次 / 14 日）⇒ 成本会吃掉大部分差异，故输出成本敏感度；
  4. 宏观分数的**设计目的是"描述当前宏观环境"，不是"预测明日风格"** —
     若回测显示无效，首先该怀疑的是用途错配，而不是分数算错。
================================================================================
"""
import argparse
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

ANCHOR = "2026-10-26"      # 国家公告自动打钩起点
WEIGHT_IDX = "sh000016"    # 上证50（纯权重）
GROWTH_IDX = "sz399006"    # 创业板（成长）
BENCH_IDX = "sh000300"     # 沪深300（参考）


def load_closes(code):
    from app.database import db
    out = {}
    for r in (db.fetch("SELECT date, close FROM backtest_prices WHERE code=%s "
                       "ORDER BY date", (code,)) or []):
        try:
            c = float(r["close"] or 0)
        except (TypeError, ValueError):
            continue
        if c > 0:
            out[str(r["date"])[:10]] = c
    return out


def load_scores():
    """{date: (score, level)} —— 来自 macro_daily 的 direction 段。"""
    from app.database import db
    out = {}
    for r in (db.fetch("SELECT date, data_json FROM macro_daily ORDER BY date") or []):
        try:
            d = json.loads(r["data_json"] or "{}")
            dr = d.get("direction") or {}
            s = dr.get("score")
            if s is not None:
                out[str(r["date"])[:10]] = (float(s), dr.get("level") or "")
        except Exception:
            continue
    return out


def build_pairs(w, g, scores, anchor=ANCHOR):
    """每个决策日：分数 + 权重/成长的下日收益。"""
    dates = [d for d in sorted(set(w) & set(g)) if d >= anchor]
    pairs = []
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        sc = scores.get(d0)
        if sc is None:
            continue
        rw = (w[d1] / w[d0] - 1) * 100
        rg = (g[d1] / g[d0] - 1) * 100
        pairs.append({
            "date": d0, "next": d1, "score": sc[0], "level": sc[1],
            "w_pct": round(rw, 2), "g_pct": round(rg, 2),
            "rel_pp": round(rg - rw, 2),      # 成长 − 权重
            "hold": "创业板" if sc[0] > 0 else "上证50",
            "ret": round(rg if sc[0] > 0 else rw, 2),
        })
    return pairs


def main():
    ap = argparse.ArgumentParser(description="宏观分数符号 → 切风格 回测")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--anchor", default=ANCHOR, help="起始日（默认国家公告打钩日）")
    args = ap.parse_args()

    w, g, b = load_closes(WEIGHT_IDX), load_closes(GROWTH_IDX), load_closes(BENCH_IDX)
    scores = load_scores()
    print(f"上证50 {len(w)} 根 / 创业板 {len(g)} 根 / 沪深300 {len(b)} 根")
    print(f"宏观分数可用 {len(scores)} 天（{min(scores) if scores else '-'} ~ "
          f"{max(scores) if scores else '-'}）")
    if not w or not g or not scores:
        print("数据不足")
        return _emit(args, "ERROR", 0, "决策日", "指数或分数缺失")

    pairs = build_pairs(w, g, scores, args.anchor)
    n = len(pairs)
    print(f"打钩窗口 {args.anchor} 起，有效决策日 = {n}")
    if not pairs:
        return _emit(args, "INSUFFICIENT", 0, "决策日", "窗口内无可用决策日")

    print()
    print("── 逐日明细 ──")
    print("%-12s %8s %-6s %-8s %9s %9s %9s" % (
        "决策日", "score", "level", "持有", "该指数%", "上证50%", "创业板%"))
    for p in pairs:
        print("%-12s %+8.1f %-6s %-8s %+9.2f %+9.2f %+9.2f" % (
            p["date"], p["score"], p["level"], p["hold"],
            p["ret"], p["w_pct"], p["g_pct"]))

    # ── 分组：分数符号是否真的预测了"谁更强" ──
    pos = [p["rel_pp"] for p in pairs if p["score"] > 0]
    neg = [p["rel_pp"] for p in pairs if p["score"] < 0]
    print()
    print("── 分组命中率（rel = 创业板 − 上证50，pp）──")
    if pos:
        hit = sum(1 for x in pos if x > 0)
        print(f"  score > 0（预测成长更强） n={len(pos)}  rel 均值 {sum(pos) / len(pos):+.3f}pp  "
              f"命中 {hit}/{len(pos)} = {hit / len(pos) * 100:.0f}%")
    if neg:
        hit = sum(1 for x in neg if x < 0)
        print(f"  score < 0（预测权重更强） n={len(neg)}  rel 均值 {sum(neg) / len(neg):+.3f}pp  "
              f"命中 {hit}/{len(neg)} = {hit / len(neg) * 100:.0f}%")

    # ── 累计 + 成本敏感度 ──
    strat = sum(p["ret"] for p in pairs)
    w_cum = sum(p["w_pct"] for p in pairs)
    g_cum = sum(p["g_pct"] for p in pairs)
    switches, prev = 0, None
    for p in pairs:
        cur = p["score"] > 0
        if prev is not None and prev != cur:
            switches += 1
        prev = cur

    print()
    print("── 累计与成本敏感度（切换 %d 次 / %d 日）──" % (switches, n))
    print("%-24s %10s %12s %12s" % ("方案", "策略%", "vs持权重pp", "vs持成长pp"))
    print("-" * 62)
    print("%-24s %+9.2f %+11.2f %+11.2f" % ("不计成本", strat,
                                            strat - w_cum, strat - g_cum))
    for label, c in (("单边 0.05%（ETF）", 0.05), ("单边 0.10%（股票）", 0.10)):
        # 每次切换 = 卖 + 买 = 2 次单边；首次建仓 1 次
        total = (switches * 2 + 1) * c
        s = strat - total
        print("%-24s %+9.2f %+11.2f %+11.2f" % (label, s, s - w_cum, s - g_cum))
    print()
    print("  对照：全程持上证50 = %+.2f%% ／ 全程持创业板 = %+.2f%%" % (w_cum, g_cum))

    # ── 判定 ──
    pos_ok = bool(pos) and (sum(pos) / len(pos)) > 0
    neg_ok = bool(neg) and (sum(neg) / len(neg)) < 0
    beats_w = strat > w_cum
    print()
    print("── 判定 ──")
    print(f"  (a) 分数>0 时成长确实强 ....... {pos_ok}"
          f"  ({'均值 %+.3fpp' % (sum(pos) / len(pos)) if pos else '无样本'})")
    print(f"  (b) 分数<0 时权重确实强 ....... {neg_ok}"
          f"  ({'均值 %+.3fpp' % (sum(neg) / len(neg)) if neg else '无样本'})")
    print(f"  (c) 策略跑赢「一直持上证50」... {beats_w}"
          f"  ({strat:+.2f}% vs {w_cum:+.2f}%)")

    if n < 10:
        verdict = "INSUFFICIENT"
        detail = f"决策日仅 {n} 个，不足以判断"
    elif beats_w and pos_ok and neg_ok:
        verdict = "PASS"
        detail = f"两侧均成立且跑赢权重基准（{strat - w_cum:+.2f}pp）"
    elif beats_w or (pos_ok and neg_ok):
        verdict = "PARTIAL"
        detail = (f"部分成立：score>0 {'成立' if pos_ok else '不成立'} / "
                  f"score<0 {'成立' if neg_ok else '不成立'} / "
                  f"跑赢权重 {'是' if beats_w else '否'}")
    else:
        # ⚠️ 不要笼统写"两侧均不成立" —— 实测 (b) 可以成立而 (a) 不成立。
        verdict = "FAIL"
        detail = (f"score>0 {'成立' if pos_ok else '不成立'} / "
                  f"score<0 {'成立' if neg_ok else '不成立'}；"
                  f"策略 {strat:+.2f}% 跑输「一直持上证50」{w_cum:+.2f}%")
    print(f"  ⇒ {verdict}：{detail}")
    print()
    print("  ⚠️ 样本仅 %d 个决策日、单一市况 ⇒ 只能当『这段时间发生了什么』，不是统计结论。" % n)
    print("  ⚠️ 宏观分数的设计目的是描述当前环境，不是预测明日风格；")
    print("     若无效，先怀疑用途错配，而非分数算错。")

    return _emit(args, verdict, n, "决策日", detail)


def _emit(args, verdict, n, unit, detail):
    if args.json:
        print(json.dumps({"script": "macro_style_backtest", "verdict": verdict,
                          "n": n, "need": 10, "unit": unit, "detail": detail},
                         ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
