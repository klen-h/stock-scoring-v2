#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】评分系统子指标因子体检（技术面 5 + 资金面 5，逐子项 IC/分位检验）
================================================================================
背景：
  五维评分的子指标锚点（MA/MACD/RSI/KDJ/布林/量价/动量/换手/成交额/主力净流入）
  自上线以来从未做过逐项预测力检验——flow5 倒U是第一个被回测检验的子项（09-13），
  其余子项的分数曲线仍是专家经验值。本脚本用与生产**完全同源**的评分引擎
  （ScoreEngine._score_technical / _score_capital，锚点零偏差）对每个子项做：
    - Spearman IC（去超额，5日/10日）
    - 五分位单调性（按子项分值分 5 档 → 去超额收益/胜率）
  输出：哪些子项有真实预测力（保留/权重候选），哪些是噪声（降权/移除候选）。

口径：
  - 数据：research_cache 本地 OHLC（包优先，零 Supabase）+ float_shares.json 文件缓存
  - 截面：资金流覆盖窗，前推 warmup 70 根，尾部留 max_hold，step=5（与 flow5 回测同源）
  - 样本：每股每截面喂引擎的指标数组（_calc_technical_fast 全量算一次、按日期切片）
  - 前瞻：fwd5/fwd10 + 截面全池均值去超额（x_fwd）
  - 换手率：volume(手) × 1e4 / 流通股本（与腾讯口径一致）
  - 局限：基本面/成长/质量子项依赖财报与估值快照，历史截面不可离线重建 → 不在本
    次体检范围（后续可在 ranking_history 快照积累后做）。
用法：python scripts/subfactor_ic_backtest.py [--hold 5 10] [--step 5]
"""

import argparse
import datetime as dt
import os
import sys
from collections import defaultdict

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

from mainforce_factor_backtest import load_float_shares, spearman  # noqa: E402
from app import research_cache  # noqa: E402
from app.scoring.engine import ScoreEngine  # noqa: E402
from app.routers.scoring import _calc_technical_fast  # noqa: E402


def quintile_table(rows):
    """按子项分值分 5 档 → 每档 n / x_fwd 均值 / 去超额胜率。"""
    if len(rows) < 100:
        return []
    srt = sorted(rows, key=lambda r: r["score"])
    q = max(1, len(srt) // 5)
    out = []
    for i in range(5):
        chunk = srt[i * q: (i + 1) * q] if i < 4 else srt[4 * q:]
        x = [r["x_fwd"] for r in chunk if r.get("x_fwd") is not None]
        if not x:
            continue
        out.append({"q": i + 1, "n": len(chunk), "x": sum(x) / len(x),
                    "win": sum(1 for v in x if v > 0) / len(x) * 100})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, nargs="+", default=[5, 10])
    ap.add_argument("--step", type=int, default=5)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    holds = args.hold
    max_hold = max(holds)

    prices = research_cache.ohlc_all(force=args.refresh)
    print(f"[data] 日线覆盖 {len(prices)} 只")
    flow_map = research_cache.flow_map(force=False)
    fs_map = load_float_shares()
    print(f"[data] 资金流覆盖 {len(flow_map)} 只 / 流通股本 {len(fs_map)} 只")

    common_dates = sorted({str(r["date"]) for rows in flow_map.values() for r in rows})
    sec_dates = common_dates[70:-max_hold] if len(common_dates) > 70 + max_hold else []
    sec_dates = sec_dates[::args.step]
    sec_set = set(sec_dates)
    print(f"[sections] 截面日 {len(sec_dates)} 个：{sec_dates[0]} ~ {sec_dates[-1]}")

    eng = ScoreEngine()
    subs = defaultdict(list)          # {sub_name: [{date, mkt20, score, fwd*, x_fwd*}]}

    # 市场状态代理：截面日全池等权 20 日动量（无前视，仅用截面日及以前数据）
    mkt20 = {}
    for d in sec_dates:
        rets20 = []
        for _c, bars in prices.items():
            ii = {b["date"]: j for j, b in enumerate(bars)}.get(d)
            if ii is None or ii < 20 or not bars[ii - 20]["close"]:
                continue
            rets20.append(bars[ii]["close"] / bars[ii - 20]["close"] - 1)
        if rets20:
            mkt20[d] = sum(rets20) / len(rets20)
    med_mkt20 = sorted(mkt20.values())[len(mkt20) // 2] if mkt20 else 0
    print(f"[regime-proxy] 截面 20 日动量: " + ", ".join(
        f"{d}:{mkt20.get(d, 0):+.3f}" for d in sec_dates))

    n_done = 0
    for code, bars in sorted(prices.items()):
        if code not in flow_map or len(bars) < 100:
            continue
        tech = _calc_technical_fast(bars)
        if not tech or len(tech) < 100:
            continue
        dates_idx = {b["date"]: i for i, b in enumerate(bars)}
        fs = fs_map.get(code)
        flow = [r for r in flow_map[code] if r["date"] in sec_set]

        for r in flow:
            d = r["date"]
            i = dates_idx.get(d)
            if i is None or i + max_hold >= len(bars):
                continue
            fwd_ok = True
            fwd = {}
            for h in holds:
                if i + h >= len(bars):
                    fwd_ok = False
                    break
                fwd[f"fwd{h}"] = (bars[i + h]["close"] / bars[i]["close"] - 1) * 100
            if not fwd_ok:
                continue
            k = next(kk for kk, rr in enumerate(flow_map[code]) if rr["date"] == d)
            w = flow_map[code][max(0, k - 4):k + 1]
            stock_info = {
                "price": bars[i]["close"],
                "change_pct": (bars[i]["close"] / bars[i - 1]["close"] - 1) * 100
                              if i >= 1 and bars[i - 1]["close"] else 0,
                "turnover_rate": (bars[i]["volume"] * 1e4 / fs) if fs else None,
                "flow5_amt": sum(x["main_pct"] or 0 for x in w),
            }
            tech_slice = tech[max(0, i - 59): i + 1]
            try:
                dt_tech = eng._score_technical(tech_slice)
                dt_cap = eng._score_capital(tech_slice, stock_info)
            except Exception:
                continue
            rec = {"date": d, "mkt20": mkt20.get(d, 0), **fwd}
            for sub, detail in (dt_tech.details or {}).items():
                if isinstance(detail, dict) and detail.get("分值") is not None:
                    subs[sub].append({"score": detail["分值"], **rec})
            for sub, detail in (dt_cap.details or {}).items():
                if isinstance(detail, dict) and detail.get("分值") is not None:
                    subs[sub].append({"score": detail["分值"], **rec})
        n_done += 1
        if n_done % 100 == 0:
            print(f"[score] {n_done} 只")

    # ── 截面去超额（截面全池均值，与 mainforce_factor_backtest 同口径）──
    for sub_rows in subs.values():
        by_date = defaultdict(list)
        for s in sub_rows:
            by_date[s["date"]].append(s)
        for d, grp in by_date.items():
            for h in holds:
                mkt = sum(g[f"fwd{h}"] for g in grp) / len(grp)
                for g in grp:
                    g[f"x_fwd{h}"] = g[f"fwd{h}"] - mkt

    # ── 报告 ──
    lines = []
    add = lines.append
    add("# 评分系统子指标因子体检（技术面/资金面逐子项 IC 与分位检验）\n")
    add(f"> 运行：subfactor_ic_backtest.py ｜ 生成：{dt.datetime.now():%Y-%m-%d %H:%M}"
        f" ｜ 截面 {len(sec_dates)} 个（{sec_dates[0]} ~ {sec_dates[-1]}，step={args.step}）\n")
    add("> 口径：生产 ScoreEngine 同源锚点（零偏差）；去超额=截面全池均值；"
        "分位=按子项分值 5 等分。基本面/成长/质量依赖财报截面，不在本次范围。\n")

    verdicts = []
    for sub, rows in sorted(subs.items()):
        if len(rows) < 200:
            continue
        add(f"\n## {sub}（n={len(rows)}）\n")
        add("| 持有 | IC(去超额) | n |")
        add("|---|---|---|")
        for h in holds:
            valid = [r for r in rows if r.get(f"x_fwd{h}") is not None]
            ic = spearman([r["score"] for r in valid], [r[f"x_fwd{h}"] for r in valid])
            add(f"| {h}日 | {ic['rho'] if ic else '-'} | {ic['n'] if ic else 0} |")
        for h in holds:
            qt = quintile_table([r for r in rows if r.get(f"x_fwd{h}") is not None])
            if qt:
                cells = [f"Q{b['q']}: n={b['n']} {b['x']:+.2f}%/{b['win']:.0f}%" for b in qt]
                mono = ("单调↑" if all(qt[i]["x"] <= qt[i + 1]["x"] + 0.15
                                       for i in range(len(qt) - 1)) else
                        ("单调↓" if all(qt[i]["x"] >= qt[i + 1]["x"] - 0.15
                                        for i in range(len(qt) - 1)) else "非单调"))
                add(f"\n- 持有{h}日五分位（按分值升序）：" + " ｜ ".join(cells) + f" → **{mono}**")
        # 结论汇总（以 5 日去超额 IC 为主）
        valid = [r for r in rows if r.get("x_fwd5") is not None]
        ic5 = spearman([r["score"] for r in valid], [r["x_fwd5"] for r in valid])
        rho = ic5["rho"] if ic5 else 0
        verdicts.append((sub, rho, len(valid)))

    add("\n\n## 汇总（按 |IC| 排序，5 日去超额）\n")
    add("| 子项 | IC(去超额,5日) | n | 体检意见 |")
    add("|---|---|---|---|")
    for sub, rho, n in sorted(verdicts, key=lambda x: -abs(x[1])):
        if abs(rho) >= 0.04:
            note = "预测力较强：保留"
        elif abs(rho) >= 0.02:
            note = "有弱预测力：保留，权重待优化"
        else:
            note = "接近噪声：降权/曲线重校准候选"
        add(f"| {sub} | {rho:+.3f} | {n} | {note} |")

    # ── 市场状态分层 IC：负 IC 是否集中在弱势截面 ──
    add("\n\n## 市场状态分层 IC（截面全池 20 日动量 >0 / <0 两组）\n")
    add("> 检验负 IC 是全时段效应还是弱势段反转效应——后者支持\"regime 权重\"而非\"改曲线\"。\n")
    add("| 子项 | IC(强市截面) | IC(弱市截面) | n强/n弱 |")
    add("|---|---|---|---|")
    for sub, rows in sorted(subs.items(), key=lambda x: -abs(x[1][0].get("mkt20", 0)) if x[1] else 0):
        if len(rows) < 200:
            continue
        for tag, cond in (("强", lambda m: m > med_mkt20), ("弱", lambda m: m <= med_mkt20)):
            pass
        up = [r for r in rows if r.get("mkt20") is not None and r["mkt20"] > med_mkt20]
        dn = [r for r in rows if r.get("mkt20") is not None and r["mkt20"] <= med_mkt20]
        ic_up = spearman([r["score"] for r in up], [r["x_fwd5"] for r in up]) if len(up) >= 100 else None
        ic_dn = spearman([r["score"] for r in dn], [r["x_fwd5"] for r in dn]) if len(dn) >= 100 else None
        add(f"| {sub} | {ic_up['rho'] if ic_up else '-'}（n={ic_up['n'] if ic_up else 0}） "
            f"| {ic_dn['rho'] if ic_dn else '-'}（n={ic_dn['n'] if ic_dn else 0}） |")

    out_dir = os.path.join(BACKEND_DIR, "backtest_reports")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"subfactor_ic_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[report] {out}")
    for sub, rho, n in sorted(verdicts, key=lambda x: -abs(x[1])):
        print(f"  {sub:<14s} IC={rho:+.3f} (n={n})")


if __name__ == "__main__":
    main()
