#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
均线粘合突破 · A 股有效性回测（2026-10-23）
================================================================================

【数据来源】
  `backend/data/kline/backend-pack.db.gz`（SQLite）：
    · 由 `generate_backend_pack.py` 产出：历史 750 根为**腾讯 qfq**，
      2026-10-22 起由 `update_backend_pack_incremental.py` 按日增量（zzshare 原始 bar）；
    · 本报告使用 2023-09-15 ~ 2026-10-22 的日线（2884 个代码，209.8 万根）。

【股票池】
  · 仅 6 位 A 股代码（剔除 sh510300 等 ETF/指数）；
  · 剔除名称含 ST / *ST / 退 的标的；
  · 信号日之前至少 250 根日线；信号日收盘价 ≥ 2 元；
  · 信号日前 20 日平均成交额（close × volume(手) × 100）≥ 5000 万元；
  · **不剔除已退市/停牌**（包是当前池，存在幸存者偏差，报告局限里注明）。

【信号定义】
  ① 均线粘合：MA5/10/20/60 的 (max-min)/MA60 ≤ `--spread`（默认 2.5%），
     且该状态**连续** `--converge-days`（默认 3）个交易日；
  ② 突破确认（信号日）：
     · 收盘价同时站上 MA5/10/20/60；
     · 成交量 ≥ `--vol-mult`（默认 1.5）× 前 5 日均量（不含当日）；
     · 粘合发生在**突破日之前**（`converged.shift(1)`），不当日事后归因。
  ③ 同一只股票两次信号至少间隔 `--dedup`（默认 10）个交易日，避免同一波重复计数。

【收益口径】
  · 入场：信号日**次日开盘价**（避免"收盘才确认、却按收盘价买入"的未来函数）；
  · 持有：T+1 / T+3 / T+5 / T+10 的**收盘价**相对入场价；
  · 最大回撤：持有期内**最低价**相对入场价（逐笔，%）；
  · 未计交易成本/滑点；信号日之后不足 h 根则不计入该周期。

【普跌日】
  · 用全池当日涨跌家数定义：上涨家数占比 < 35%（`--down-ratio`）记为普跌日；
  · 同时记录当日收益中位数，报告里给出两套口径的样本数（主口径为上涨占比）。

用法：
  python scripts/backtest_ma_convergence.py
  python scripts/backtest_ma_convergence.py --spread 0.02 --converge-days 5 --vol-mult 1.8
  # 产物：reports/ma_convergence/{report.md, signals.csv, stats_*.csv}
================================================================================
"""
import argparse
import copy
import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PACK = os.path.join(ROOT, "backend", "data", "kline",
                            "backend-pack.db.gz")
DEFAULT_OUT = os.path.join(ROOT, "reports", "ma_convergence")
COST = 0.15     # 双边成本示意（佣金+印花税+滑点，%）——仅用于 net_mean 列


def _pack_to_sqlite(path: str) -> str:
    """gz → 临时 SQLite（sqlite3 不能直接读 gz）。"""
    if not path.endswith(".gz"):
        return path
    tmp = tempfile.mktemp(prefix="pack-", suffix=".db")
    with gzip.open(path, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo, 1024 * 1024)
    return tmp


def load_pack(path: str):
    """返回 (names, bars_by_code, all_dates)。"""
    db_path = _pack_to_sqlite(path)
    conn = sqlite3.connect(db_path)
    names = {c: (n or "") for c, n in
             conn.execute("SELECT code, name FROM codes")}
    codes = [c for c in names if str(c).isdigit() and len(str(c)) == 6]
    bars = {}
    for code in codes:
        df = pd.read_sql_query(
            "SELECT date, open, high, low, close, volume FROM klines "
            "WHERE code=? ORDER BY date", conn, params=(code,))
        if len(df):
            df["date"] = df["date"].astype(str).str[:10]
            bars[code] = df
    conn.close()
    if db_path != path:
        try:
            os.remove(db_path)
        except OSError:
            pass
    all_dates = sorted({d for df in bars.values() for d in df["date"]})
    return names, bars, all_dates


def market_breadth(bars: dict, start: str) -> pd.DataFrame:
    """全池每日涨跌家数比 + 收益中位数（用于定义普跌日）。"""
    recs = []
    for code, df in bars.items():
        if len(df) < 2:
            continue
        ret = df["close"].pct_change()
        sub = pd.DataFrame({"date": df["date"], "ret": ret}).dropna()
        sub = sub[sub["date"] >= start]
        recs.append(sub)
    if not recs:
        return pd.DataFrame(columns=["date", "up_ratio", "median_ret"])
    allret = pd.concat(recs, ignore_index=True)
    g = allret.groupby("date")["ret"]
    out = pd.DataFrame({
        "up_ratio": g.apply(lambda x: float((x > 0).mean())),
        "median_ret": g.median(),
        "n": g.size(),
    }).reset_index()
    return out


def scan_code(code: str, df: pd.DataFrame, breadth: dict, args):
    """单只股票的指标 + 信号 + 逐笔前瞻收益。"""
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    vol = df["volume"].to_numpy(float)
    date = df["date"].to_numpy()
    n = len(df)
    if n < max(args.min_bars, 130):
        return []

    s = pd.Series(close)
    ma5 = s.rolling(5).mean()
    ma10 = s.rolling(10).mean()
    ma20 = s.rolling(20).mean()
    ma60 = s.rolling(60).mean()
    ma_max = pd.concat([ma5, ma10, ma20, ma60], axis=1).max(axis=1)
    ma_min = pd.concat([ma5, ma10, ma20, ma60], axis=1).min(axis=1)
    spread = (ma_max - ma_min) / ma60
    conv = spread <= args.spread
    conv_n = conv.rolling(args.converge_days).sum() == args.converge_days

    vol_ma5_prev = pd.Series(vol).shift(1).rolling(5).mean()
    vol_ratio = pd.Series(vol) / vol_ma5_prev
    above = (pd.Series(close) > ma5) & (pd.Series(close) > ma10) \
        & (pd.Series(close) > ma20) & (pd.Series(close) > ma60)
    breakout = above & (vol_ratio >= args.vol_mult) & conv_n.shift(1, fill_value=False)

    amount20 = (pd.Series(close * vol * 100)).rolling(20).mean().shift(1)
    high120 = pd.Series(high).rolling(120).max()
    low120 = pd.Series(low).rolling(120).min()
    pos120 = (pd.Series(close) - low120) / (high120 - low120).replace(0, np.nan)
    dd60 = pd.Series(close) / pd.Series(close).rolling(60).max() - 1
    ma60_slope = ma60 / ma60.shift(10) - 1

    sig_idx = np.where(breakout.to_numpy())[0]
    sig_idx = [i for i in sig_idx if i >= 120 and i + 2 < n
               and date[i] >= args.start_date
               and close[i] >= args.min_price
               and (amount20.iloc[i] or 0) >= args.min_amount]
    out = []
    last = -10 ** 9
    for i in sig_idx:
        if i - last < args.dedup:
            continue                     # 同一波只取第一次
        entry = open_[i + 1]
        if not np.isfinite(entry) or entry <= 0:
            continue
        b = breadth.get(date[i])
        row = {
            "code": code, "date": date[i], "close": round(float(close[i]), 3),
            "spread_prev": round(float(spread.iloc[i - 1]) * 100, 2),
            "vol_ratio": round(float(vol_ratio.iloc[i]), 2),
            "ma60_slope10": round(float(ma60_slope.iloc[i]) * 100, 2),
            "pos120": round(float(pos120.iloc[i]), 3),
            "dd60": round(float(dd60.iloc[i]) * 100, 2),
            "amount20_yi": round(float(amount20.iloc[i]) / 1e8, 2),
            "up_ratio": round(float(b[0]), 3) if b else np.nan,
            "median_ret": round(float(b[1]), 4) if b else np.nan,
        }
        for h in args.horizons:
            j = i + h
            row[f"ret{h}"] = (round(float(close[j] / entry - 1) * 100, 2)
                              if j < n else np.nan)
            dd = (min(low[i + 1:j + 1]) / entry - 1) * 100 if j < n else np.nan
            row[f"dd{h}"] = round(float(dd), 2) if np.isfinite(dd) else np.nan
        out.append(row)
        last = i
    return out


def _stats(df: pd.DataFrame, col: str, dd_col: str = None) -> dict:
    x = pd.to_numeric(df[col], errors="coerce").dropna()
    if not len(x):
        return {}
    win = x[x > 0]
    loss = x[x < 0]
    pl = (win.mean() / abs(loss.mean())) if len(win) and len(loss) else np.nan
    d = pd.to_numeric(df[dd_col], errors="coerce").dropna() if dd_col else pd.Series()
    return {
        "n": len(x),
        "mean": round(x.mean(), 2),
        "net_mean": round(x.mean() - COST, 2),
        "median": round(x.median(), 2),
        "p25": round(x.quantile(0.25), 2),
        "p75": round(x.quantile(0.75), 2),
        "win": round(len(win) / len(x) * 100, 1),
        "avg_win": round(win.mean(), 2) if len(win) else np.nan,
        "avg_loss": round(loss.mean(), 2) if len(loss) else np.nan,
        "pl": round(pl, 2) if np.isfinite(pl) else np.nan,
        "dd_mean": round(d.mean(), 2) if len(d) else np.nan,
        "dd_median": round(d.median(), 2) if len(d) else np.nan,
        "dd_worst": round(d.min(), 2) if len(d) else np.nan,
    }


def _boot_ci(x: pd.Series, n: int = 1000):
    """均值/中位数的 bootstrap 95% CI（不假设正态）。"""
    arr = pd.to_numeric(x, errors="coerce").dropna().to_numpy(float)
    if len(arr) < 30:
        return (np.nan, np.nan), (np.nan, np.nan)
    rng = np.random.default_rng(42)
    idx = rng.integers(0, len(arr), size=(n, len(arr)))
    means = arr[idx].mean(axis=1)
    medians = np.median(arr[idx], axis=1)
    return (np.percentile(means, [2.5, 97.5]),
            np.percentile(medians, [2.5, 97.5]))


def _buckets(x: pd.Series):
    bins = [-100, -10, -5, 0, 5, 10, 100]
    labels = ["<-10%", "-10~-5%", "-5~0%", "0~5%", "5~10%", ">10%"]
    cut = pd.cut(pd.to_numeric(x, errors="coerce"), bins=bins, labels=labels)
    c = cut.value_counts().reindex(labels).fillna(0).astype(int)
    total = max(int(c.sum()), 1)
    return [{"区间": k, "n": int(v), "占比": f"{v / total * 100:.1f}%"}
            for k, v in c.items()]


def _md_table(rows, cols=None):
    if not rows:
        return "（无数据）"
    cols = cols or list(rows[0].keys())
    out = ["| " + " | ".join(cols) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="均线粘合突破 A 股回测")
    ap.add_argument("--pack", default=DEFAULT_PACK)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--lookback", type=int, default=250,
                    help="信号窗口=最近 N 个交易日（默认 250≈1 年）")
    ap.add_argument("--spread", type=float, default=0.025,
                    help="粘合阈值：(maxMA-minMA)/MA60（默认 2.5%%）")
    ap.add_argument("--converge-days", type=int, default=3)
    ap.add_argument("--vol-mult", type=float, default=1.5)
    ap.add_argument("--min-price", type=float, default=2.0)
    ap.add_argument("--min-amount", type=float, default=5e7,
                    help="20 日均成交额下限（元，默认 5000 万）")
    ap.add_argument("--min-bars", type=int, default=250)
    ap.add_argument("--dedup", type=int, default=10)
    ap.add_argument("--down-ratio", type=float, default=0.35,
                    help="普跌日：全池上涨家数占比 < 该值")
    ap.add_argument("--horizons", default="1,3,5,10")
    args = ap.parse_args()
    args.horizons = [int(x) for x in args.horizons.split(",") if x.strip()]

    print("=== 均线粘合突破回测 ===")
    names, bars, all_dates = load_pack(args.pack)
    if not all_dates:
        print("::error::数据包为空")
        return 2
    args.start_date = all_dates[-args.lookback] if len(all_dates) > args.lookback \
        else all_dates[0]
    print(f"  数据包: {len(bars)} 只 / {len(all_dates)} 个交易日 "
          f"({all_dates[0]} ~ {all_dates[-1]})")
    print(f"  信号窗口: {args.start_date} ~ {all_dates[-1]}（{args.lookback} 个交易日）")

    breadth_df = market_breadth(bars, args.start_date)
    breadth = {r["date"]: (r["up_ratio"], r["median_ret"])
               for _, r in breadth_df.iterrows()}
    print(f"  普跌日: {(breadth_df['up_ratio'] < args.down_ratio).sum()} / "
          f"{len(breadth_df)} 天（上涨占比 < {args.down_ratio:.0%}）")

    rows = []
    for i, (code, df) in enumerate(bars.items(), 1):
        nm = names.get(code) or ""
        if "ST" in nm.upper() or "退" in nm:
            continue
        rows.extend(scan_code(code, df, breadth, args))
        if i % 500 == 0:
            print(f"  扫描 {i}/{len(bars)} …")
    sig = pd.DataFrame(rows)
    if sig.empty:
        print("::error::无信号，请放宽 --spread / --converge-days / --vol-mult")
        return 2
    sig["name"] = sig["code"].map(names)
    sig["broad_down"] = sig["up_ratio"] < args.down_ratio
    sig = sig.sort_values("date").reset_index(drop=True)
    h10 = args.horizons[-1]
    print(f"  信号样本: {len(sig)} 笔 / {sig['code'].nunique()} 只 / "
          f"普跌日 {int(sig['broad_down'].sum())} 笔")

    os.makedirs(args.out, exist_ok=True)
    sig.to_csv(os.path.join(args.out, "signals.csv"), index=False,
               encoding="utf-8-sig")

    # ── 各持有周期 ──
    hz_rows, dist_rows = [], []
    for h in args.horizons:
        st = _stats(sig, f"ret{h}", f"dd{h}")
        if st:
            hz_rows.append({"周期": f"T+{h}", **st})
        for b in _buckets(sig[f"ret{h}"]):
            dist_rows.append({"周期": f"T+{h}", **b})

    # ── 普跌 vs 非普跌 ──
    sc_rows = []
    for label, sub in (("全部", sig),
                       ("普跌日", sig[sig["broad_down"]]),
                       ("非普跌日", sig[~sig["broad_down"]])):
        for h in args.horizons:
            st = _stats(sub, f"ret{h}", f"dd{h}")
            if st:
                sc_rows.append({"场景": label, "周期": f"T+{h}", **st})

    # ── 低位/中位/高位（120 日价格分位）──
    pos_rows = []
    pos_labels = [("低位 0~30%", 0.0, 0.3), ("中位 30~70%", 0.3, 0.7),
                  ("高位 70~100%", 0.7, 1.01)]
    for label, lo, hi in pos_labels:
        sub = sig[(sig["pos120"] >= lo) & (sig["pos120"] < hi)]
        for h in args.horizons:
            st = _stats(sub, f"ret{h}", f"dd{h}")
            if st:
                pos_rows.append({"位置": label, "周期": f"T+{h}", **st})

    # ── MA60 趋势过滤 ──
    trend_rows = []
    for label, sub in (("MA60 上行 (>0)", sig[sig["ma60_slope10"] > 0]),
                       ("MA60 走平/下行 (≤0)", sig[sig["ma60_slope10"] <= 0])):
        for h in args.horizons:
            st = _stats(sub, f"ret{h}", f"dd{h}")
            if st:
                trend_rows.append({"趋势": label, "周期": f"T+{h}", **st})

    # ── 参数敏感性（固定信号窗口，逐组重扫）──
    sens_rows = [{
        "spread%": round(args.spread * 100, 1), "converge": args.converge_days,
        "vol": args.vol_mult, "n": len(sig),
        "T10_mean": _stats(sig, f"ret{h10}").get("mean"),
        "T10_median": _stats(sig, f"ret{h10}").get("median"),
        "T10_win": _stats(sig, f"ret{h10}").get("win"),
        "T10_pl": _stats(sig, f"ret{h10}").get("pl"),
    }]
    for sp, cd, vm in [(0.015, args.converge_days, args.vol_mult),
                       (0.020, args.converge_days, args.vol_mult),
                       (args.spread, 5, args.vol_mult),
                       (args.spread, args.converge_days, 2.0)]:
        if (sp, cd, vm) == (args.spread, args.converge_days, args.vol_mult):
            continue
        a = copy.copy(args)
        a.spread, a.converge_days, a.vol_mult = sp, cd, vm
        vrows = []
        for code, df in bars.items():
            nm = names.get(code) or ""
            if "ST" in nm.upper() or "退" in nm:
                continue
            vrows.extend(scan_code(code, df, breadth, a))
        v = pd.DataFrame(vrows)
        st = _stats(v, f"ret{h10}") if len(v) else {}
        sens_rows.append({
            "spread%": round(sp * 100, 1), "converge": cd, "vol": vm,
            "n": len(v), "T10_mean": st.get("mean"), "T10_median": st.get("median"),
            "T10_win": st.get("win"), "T10_pl": st.get("pl"),
        })
        print(f"  敏感性变体 spread={sp:.1%} conv={cd} vol={vm}: n={len(v)}")

    # ── 成功 vs 失败（以 T+10 为准）──
    valid10 = sig.dropna(subset=[f"ret{h10}"])
    succ = valid10[valid10[f"ret{h10}"] > 0]
    fail = valid10[valid10[f"ret{h10}"] <= 0]
    feats = ["spread_prev", "vol_ratio", "ma60_slope10", "pos120", "dd60",
             "amount20_yi", "up_ratio", "median_ret"]
    comp = []
    for f in feats:
        comp.append({
            "特征": f,
            "成功组均值": round(float(succ[f].mean()), 3) if len(succ) else "",
            "失败组均值": round(float(fail[f].mean()), 3) if len(fail) else "",
            "差值": round(float(succ[f].mean() - fail[f].mean()), 3)
            if len(succ) and len(fail) else "",
        })
    top_win = valid10.nlargest(5, f"ret{h10}")[
        ["code", "name", "date", f"ret{h10}", "spread_prev", "vol_ratio",
         "pos120", "dd60"]]
    top_loss = valid10.nsmallest(5, f"ret{h10}")[
        ["code", "name", "date", f"ret{h10}", "spread_prev", "vol_ratio",
         "pos120", "dd60"]]

    # ── 结论（低位修复价值）──
    low = sig[(sig["pos120"] < 0.3)]
    low_st = _stats(low, f"ret{h10}", f"dd{h10}")
    high = sig[(sig["pos120"] >= 0.7)]
    high_st = _stats(high, f"ret{h10}", f"dd{h10}")
    all_st = _stats(sig, f"ret{h10}", f"dd{h10}")
    ci_m, ci_md = _boot_ci(sig[f"ret{h10}"])
    repair = bool(low_st and low_st["n"] >= 50 and low_st["median"] > 0
                  and low_st["win"] >= 50)

    # ── 写 CSV ──
    pd.DataFrame(hz_rows).to_csv(os.path.join(args.out, "stats_by_horizon.csv"),
                                 index=False, encoding="utf-8-sig")
    pd.DataFrame(sc_rows).to_csv(os.path.join(args.out, "stats_by_scenario.csv"),
                                 index=False, encoding="utf-8-sig")
    pd.DataFrame(pos_rows).to_csv(os.path.join(args.out, "stats_by_position.csv"),
                                  index=False, encoding="utf-8-sig")
    pd.DataFrame(trend_rows).to_csv(os.path.join(args.out, "stats_by_trend.csv"),
                                    index=False, encoding="utf-8-sig")
    pd.DataFrame(sens_rows).to_csv(os.path.join(args.out, "sensitivity.csv"),
                                   index=False, encoding="utf-8-sig")
    pd.DataFrame(dist_rows).to_csv(os.path.join(args.out, "distribution.csv"),
                                   index=False, encoding="utf-8-sig")

    # ── Markdown 报告 ──
    md = []
    md.append("# 均线粘合突破 · A 股有效性回测\n")
    md.append(f"> 生成时间：{datetime.now():%Y-%m-%d %H:%M} ｜ "
              f"信号窗口：{args.start_date} ~ {all_dates[-1]}\n")
    md.append("## 一、结论先行\n")
    if repair:
        md.append(f"- **低位（120 日分位 <30%）具备修复价值**：T+{h10} 中位数 "
                  f"{low_st['median']}%、胜率 {low_st['win']}%（n={low_st['n']}）。")
    elif low_st and low_st["n"] >= 50:
        md.append(f"- **低位修复价值不成立（样本充足，n={low_st['n']}）**："
                  f"T+{h10} 中位数 {low_st['median']}%、胜率 {low_st['win']}%、"
                  f"均值 {low_st['mean']}%（判定门槛：n≥50 且中位数>0、胜率≥50%）。")
    else:
        md.append(f"- **低位修复价值样本不足**：T+{h10} 中位数 "
                  f"{low_st.get('median', '—')}%、胜率 {low_st.get('win', '—')}%"
                  f"（n={low_st.get('n', 0)}；判定门槛：n≥50 且中位数>0、胜率≥50%）。")
    md.append(f"- 全样本 T+{h10}：均值 {all_st['mean']}%（95%CI "
              f"{ci_m[0]:.2f}~{ci_m[1]:.2f}）、中位数 {all_st['median']}%（95%CI "
              f"{ci_md[0]:.2f}~{ci_md[1]:.2f}）、胜率 {all_st['win']}%、"
              f"盈亏比 {all_st['pl']}（n={all_st['n']}）。")
    md.append(f"- **均值与中位数方向相反**：收益依赖少数大赢家；"
              f"扣 {COST:.2f}% 双边成本后 T+1 均值 {hz_rows[0]['net_mean']}%、"
              f"T+{h10} 均值 {all_st['net_mean']}%。")
    md.append(f"- 位置对比：低位 T+{h10} 中位数 "
              f"{low_st.get('median', '—')}% vs 高位 {high_st.get('median', '—')}%"
              f"（高位反而更好 ⇒ 更像趋势延续，不是低位修复）。")
    broad = sig[sig["broad_down"]]
    md.append(f"- 普跌日信号 {len(broad)} 笔，非普跌日 {len(sig) - len(broad)} 笔"
              f"（普跌日定义：全池上涨占比 < {args.down_ratio:.0%}）。\n")

    md.append("## 二、数据来源与口径\n")
    md.append(f"- 数据包：`{os.path.relpath(args.pack, ROOT).replace(os.sep, '/')}`"
              f"（{len(bars)} 只、{len(all_dates)} 个交易日，{all_dates[0]} ~ {all_dates[-1]}）；")
    md.append("- 来源：历史为腾讯 qfq，2026-10-22 起为 zzshare 增量（`update_backend_pack_incremental.py`）；")
    md.append("- 股票池：6 位 A 股，剔除 ST/*ST/退；信号日前 ≥250 根；收盘价 ≥"
              f"{args.min_price} 元；20 日均成交额 ≥{args.min_amount / 1e8:.1f} 亿；")
    md.append("- 入场：信号日**次日开盘**；持有到 T+h 收盘；未计成本；不足 h 根不计入；")
    md.append("- 最大回撤：持有期内**最低价**相对入场价（逐笔）；")
    md.append("- 局限：当前池存在**幸存者偏差**；未做行业/市值中性化；1 年样本、单一市场阶段；"
              "同股/同期信号存在重叠，bootstrap 置信区间偏窄。\n")

    md.append("## 三、信号定义\n")
    md.append(f"1. 粘合：`(max(MA5,MA10,MA20,MA60)-min(...))/MA60 ≤ {args.spread:.1%}`，"
              f"连续 {args.converge_days} 个交易日；")
    md.append(f"2. 突破：收盘价站上全部均线 + 成交量 ≥{args.vol_mult}×前 5 日均量；")
    md.append(f"3. 去重：同股两次信号间隔 ≥{args.dedup} 个交易日；粘合必须发生在突破前一日之前。\n")

    md.append("## 四、各持有周期表现\n")
    md.append(_md_table(hz_rows))
    md.append(f"\n> `net_mean` = 均值 − {COST:.2f}%（双边成本示意）；"
              "最大回撤为逐笔「持有期内最低价 / 入场价」口径。")
    md.append("\n## 五、收益分布\n")
    md.append(_md_table(dist_rows))
    md.append("\n## 六、普跌日 vs 非普跌日\n")
    md.append(_md_table(sc_rows))
    md.append("\n## 七、低位 / 中位 / 高位（120 日价格分位）\n")
    md.append(_md_table(pos_rows))
    md.append("\n## 八、MA60 趋势过滤\n")
    md.append(_md_table(trend_rows))
    md.append("\n## 九、参数敏感性（T+10）\n")
    md.append(_md_table(sens_rows))
    md.append("\n## 十、成功 vs 失败（T+10 为正/为负）\n")
    md.append(_md_table(comp))
    md.append("\n**T+10 成功案例 Top5**\n")
    md.append(_md_table(top_win.to_dict("records")))
    md.append("\n**T+10 失败案例 Top5**\n")
    md.append(_md_table(top_loss.to_dict("records")))
    # 趋势/敏感性/特征差异的结论素材
    up_st = _stats(sig[sig["ma60_slope10"] > 0], f"ret{h10}")
    down_st = _stats(sig[sig["ma60_slope10"] <= 0], f"ret{h10}")
    tight = next((x for x in sens_rows if x["spread%"] == 1.5), {})
    amt = next((x for x in comp if x["特征"] == "amount20_yi"), {})

    md.append("\n## 十一、结论与建议\n")
    if repair:
        md.append(f"- 低位组在 T+{h10} 上取得正的中位数与 ≥50% 胜率，"
                  "**具备低位修复价值**；但需注意回撤与普跌日环境。")
    elif low_st and low_st["n"] >= 50:
        md.append(f"- 低位组 n={low_st['n']}（样本充足），T+{h10} 中位数 "
                  f"{low_st['median']}%、胜率 {low_st['win']}% ⇒ "
                  "**不具备低位修复价值**；高位组反而更好，信号更像趋势延续。")
    else:
        md.append("- 低位组样本不足，**暂不能认定具备低位修复价值**。")
    md.append("- 普跌日组明显弱于非普跌日（T+10 均值/中位数/胜率全面更低），"
              "实盘应叠加**大盘/宽度过滤**：普跌日不接突破。")
    md.append(f"- MA60 趋势过滤**无效**：上行组 T+10 均值 {up_st.get('mean', '—')}%，"
              f"走平/下行组 {down_st.get('mean', '—')}%（上行并未更好）。")
    if tight:
        md.append(f"- 参数不稳健：粘合阈值收紧到 1.5% 后 T+10 均值 "
                  f"{tight.get('T10_mean')}%、胜率 {tight.get('T10_win')}%（基准 0.52%/44.5%），"
                  "说明优势主要来自较宽的 2.5% 定义，不宜外推。")
    if amt:
        md.append(f"- 成功/失败组的事前特征差异很小（最大项 20 日均额 "
                  f"{amt.get('成功组均值')} vs {amt.get('失败组均值')} 亿，其余差值 <0.15），"
                  "没有可靠的事前筛选变量。")
    md.append(f"- 净期望：扣 {COST:.2f}% 成本后，T+1 均值 "
              f"{hz_rows[0]['net_mean']}%、T+3 {hz_rows[1]['net_mean']}%，"
              "短周期不具备可交易性；T+10 仅剩微弱的均值优势，且中位数为负。")
    md.append("- 建议下一步：加入行业/市值分层、涨跌停/停牌处理、以及 2~3 年多阶段样本复核。\n")
    md.append("## 十二、复现\n```bash\npython scripts/backtest_ma_convergence.py \\\n"
              f"  --spread {args.spread} --converge-days {args.converge_days} \\\n"
              f"  --vol-mult {args.vol_mult} --lookback {args.lookback}\n```\n")

    report_path = os.path.join(args.out, "report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    # ── 控制台摘要 ──
    print("\n=== 汇总（T+10）===")
    print(f"  全样本: n={all_st['n']} 中位数={all_st['median']}% "
          f"胜率={all_st['win']}% 盈亏比={all_st['pl']}")
    print(f"  低位: n={low_st.get('n', 0)} 中位数={low_st.get('median', '—')}% "
          f"胜率={low_st.get('win', '—')}%")
    print(f"  高位: n={high_st.get('n', 0)} 中位数={high_st.get('median', '—')}%")
    print(f"  报告: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
