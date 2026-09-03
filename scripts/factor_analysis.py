#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】因子分析 + 拥挤度惩罚回顾性对照（PLAN_CROWDING_FACTOR.md 阶段 0）
================================================================================

输出（写入 backend/backtest_reports/factor_YYYYMMDD_HHMM.md）：
  1. 五维度（技术/资金/基本面/成长/质量）对未来 2/5 日收益的 IC（Spearman）
     口径：整体 / 早段(8/17-8/27) / 震荡偏空段(8/28-9/2)；
           原始收益 / 沪深300 同期超额收益（去 beta）两套
  2. A/B 对照：每日 Top10（total vs total×crowd_mult）的持有 2/5 日胜率/均收益
  3. C 交互矩阵：「质量高/低 × 拥挤高/低」4 组的未来收益

数据源：
  - ranking_history（含 dimensions_json 中文维度分 + 快照价，每日 Top50）
  - backtest_prices（OHLC 日线，快照日前的历史切片算拥挤度；快照日后定长收益）

口径对齐既有回测：快照日收盘买入，持有 N 个交易日按收盘卖出（按个股自身交易日行号）。
注意：9/2 快照的 fwd2 要等 9/4（周五）收盘才满，本次输出会标注「样本不足」的格。

用法：python scripts/factor_analysis.py [--since 2026-08-17]
================================================================================
"""

import argparse
import json
import os
import sys
from collections import defaultdict

# ── 路径配置（与 scripts/ 下其他脚本一致）────────────────────────────
BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")


def load_env():
    """加载 backend/.env 到环境变量（不覆盖已存在的值）"""
    if not os.path.exists(ENV_PATH):
        print(f"[warn] 未找到 {ENV_PATH}，将使用系统环境变量")
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


load_env()
sys.path.insert(0, BACKEND_DIR)

from app.database import db                                            # noqa: E402
from app.backtest.crowding import (                                  # noqa: E402
    crowding_metrics, crowding_score, crowding_multiplier)

DIMS = ["技术面", "资金面", "基本面", "成长", "质量"]
MKT_CODE = "sh000300"
MKT_LABEL = "沪深300"
HORIZONS = (2, 5)
W1 = ("2026-08-17", "2026-08-27")   # 早段
W2 = ("2026-08-28", "2026-09-02")   # 震荡偏空段（含 9/2 大跌，fwd 部分未满）
SINCE_OHLC = "2025-06-01"           # 拥挤度需回溯 250 个交易日 → 拉约 1.4 年


# ──────────────────────────────────────────────────────────────
#  小工具
# ──────────────────────────────────────────────────────────────
def parse_dims(s: str) -> dict:
    try:
        d = json.loads(s or "{}") or {}
        return {k: (float(v) if v is not None else None) for k, v in d.items()}
    except Exception:
        return {}


def _rank(values: list) -> list:
    """平均秩（含并列）。"""
    n = len(values)
    order = sorted(range(n), key=lambda i: (values[i], i))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(xs: list, ys: list) -> dict | None:
    """Spearman 相关 + 样本数；不足 5 返回 None。"""
    pairs = [(float(a), float(b)) for a, b in zip(xs, ys)
             if a is not None and b is not None]
    if len(pairs) < 5:
        return None
    xs2 = [p[0] for p in pairs]
    ys2 = [p[1] for p in pairs]
    rx, ry = _rank(xs2), _rank(ys2)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = sum((a - mx) ** 2 for a in rx) ** 0.5
    dy = sum((b - my) ** 2 for b in ry) ** 0.5
    if dx == 0 or dy == 0:
        return None
    r = num / (dx * dy)
    return {"rho": round(r, 4), "n": len(pairs)}


# ──────────────────────────────────────────────────────────────
#  数据加载
# ──────────────────────────────────────────────────────────────
def load_snapshots(since: str) -> list:
    rows = db.fetch(
        "SELECT rank_date, code, name, rank_pos, total_score, dimensions_json, price "
        "FROM ranking_history "
        "WHERE dimensions_json IS NOT NULL AND dimensions_json != '' AND rank_date >= %s "
        "ORDER BY rank_date, rank_pos", (since,))
    out = []
    for r in rows or []:
        dims = parse_dims(r.get("dimensions_json"))
        if not dims:
            continue
        out.append({
            "date": r["rank_date"], "code": r["code"], "name": r.get("name") or r["code"],
            "rank": r.get("rank_pos"), "total": float(r.get("total_score") or 0),
            "snap_price": r.get("price"), "dims": dims,
        })
    return out


def load_ohlc(codes: list) -> dict:
    """批量加载 {code: [{date, open, high, low, close}...]}（升序）。"""
    series: dict = {}
    codes = list(dict.fromkeys(codes))
    for i in range(0, len(codes), 200):
        chunk = codes[i:i + 200]
        ph = ",".join(["%s"] * len(chunk))
        rows = db.fetch(
            f"SELECT code, date, open, high, low, close FROM backtest_prices "
            f"WHERE code IN ({ph}) AND date >= %s ORDER BY code, date",
            (*chunk, SINCE_OHLC))
        tmp = defaultdict(list)
        for r in rows or []:
            try:
                tmp[r["code"]].append({
                    "date": r["date"], "open": float(r["open"] or 0),
                    "high": float(r["high"] or 0), "low": float(r["low"] or 0),
                    "close": float(r["close"] or 0),
                })
            except (TypeError, ValueError):
                continue
        for c, lst in tmp.items():
            series[c] = lst
    return series


# ──────────────────────────────────────────────────────────────
#  单条快照 → 特征行
# ──────────────────────────────────────────────────────────────
def enrich(snap: dict, price_map: dict, mkt_series: dict, mkt_dates: dict) -> dict | None:
    bars = price_map.get(snap["code"])
    if not bars:
        return None
    dates = [b["date"] for b in bars]
    try:
        idx = dates.index(snap["date"])
    except ValueError:
        return None   # 快照日该股无日线（停牌/未回填）→ 弃
    closes = [b["close"] for b in bars]

    row = dict(snap)
    # 次日收益（D2 预检用：快照日 → 次日实跌）
    row["ret_next"] = None
    if idx + 1 < len(closes) and closes[idx] > 0:
        row["ret_next"] = round((closes[idx + 1] / closes[idx] - 1) * 100, 2)
    # 定长未来收益（按个股自身交易日）
    for h in HORIZONS:
        row[f"fwd{h}"] = None
        if idx + h < len(closes) and closes[idx] > 0:
            row[f"fwd{h}"] = round((closes[idx + h] / closes[idx] - 1) * 100, 2)
            # 市场超额（沪深300 同期）
            if mkt_dates and snap["date"] in mkt_dates and closes[idx] > 0:
                midx = mkt_dates[snap["date"]]
                if midx + h < len(mkt_series) and mkt_series[midx] > 0:
                    mkt_ret = (mkt_series[midx + h] / mkt_series[midx] - 1) * 100
                    row[f"exc{h}"] = round(row[f"fwd{h}"] - mkt_ret, 2)
    # 拥挤度：快照日及之前切片（防未来函数）
    m = crowding_metrics(bars[:idx + 1])
    if m:
        row["crowd_score"] = round(crowding_score(m), 1)
        row["crowd_mult"] = crowding_multiplier(row["crowd_score"])
        row["crowd"] = m
    else:
        row["crowd_score"], row["crowd_mult"], row["crowd"] = None, 1.0, None
    return row


# ──────────────────────────────────────────────────────────────
#  分析
# ──────────────────────────────────────────────────────────────
def in_window(d: str, w: tuple) -> bool:
    return w[0] <= d <= w[1]


def ic_table(rows: list, horizon: int, exc: bool) -> dict:
    """每维度对某持有期收益的 Spearman IC（按窗口分组）。"""
    key = f"exc{horizon}" if exc else f"fwd{horizon}"
    out = {}
    for wname, w in (("整体", None), ("早段", W1), ("震荡偏空段", W2)):
        pool = rows if w is None else [r for r in rows if in_window(r["date"], w)]
        vals = [r for r in pool if r.get(key) is not None and r["total"] is not None]
        per_dim = {}
        for dim in DIMS:
            dxy = [(r["dims"].get(dim), r[key]) for r in vals
                   if r["dims"].get(dim) is not None]
            sp = spearman([a for a, _ in dxy], [b for _, b in dxy])
            per_dim[dim] = sp
        out[wname] = {"n": len(vals), "dims": per_dim}
    return out


def topn_stats(rows: list, sort_key: callable, horizon: int, topn: int = 10) -> dict:
    """按 sort_key 对每日期取 TopN，返回持有 horizon 的合并胜率/均收益。"""
    by_date = defaultdict(list)
    for r in rows:
        if r.get(f"fwd{horizon}") is None:
            continue
        by_date[r["date"]].append(r)
    wins, rets, cells, dates = 0, [], 0, 0
    for d in sorted(by_date):
        pool = sorted(by_date[d], key=sort_key, reverse=True)[:topn]
        for r in pool:
            wins += 1 if r[f"fwd{horizon}"] > 0 else 0
            rets.append(r[f"fwd{horizon}"])
            cells += 1
        dates += 1
    return {
        "dates": dates, "n": cells,
        "win_rate": round(wins / cells * 100, 1) if cells else None,
        "avg_ret": round(sum(rets) / len(rets), 2) if rets else None,
    }


def pseudo_ic(rows: list, field: str, horizon: int, exc: bool) -> dict:
    """对任意字段（total_score / crowd_score）的 IC（按窗口分组）。"""
    key = f"exc{horizon}" if exc else f"fwd{horizon}"
    out = {}
    for wname, w in (("整体", None), ("早段", W1), ("震荡偏空段", W2)):
        pool = rows if w is None else [r for r in rows if in_window(r["date"], w)]
        dxy = [(r[field], r[key]) for r in pool
               if r.get(field) is not None and r.get(key) is not None]
        out[wname] = {"n": len(dxy),
                      "ic": spearman([a for a, _ in dxy], [b for _, b in dxy])}
    return out


def bucket_stats(rows: list, horizon: int) -> list:
    """拥挤度分桶 → 未来收益（n / 胜率 / 均收益）。拥挤越低分越高。"""
    key = f"fwd{horizon}"
    pool = [r for r in rows if r.get(key) is not None and r.get("crowd_score") is not None]
    buckets = {"高拥挤(<30)": [], "中拥挤(30-50)": [], "低拥挤(≥50)": []}
    for r in pool:
        c = r["crowd_score"]
        b = "高拥挤(<30)" if c < 30 else ("中拥挤(30-50)" if c < 50 else "低拥挤(≥50)")
        buckets[b].append(r[key])
    out = []
    for b in ("高拥挤(<30)", "中拥挤(30-50)", "低拥挤(≥50)"):
        v = buckets[b]
        if not v:
            out.append({"bucket": b, "n": 0, "win_rate": None, "avg_ret": None})
            continue
        wins = sum(1 for x in v if x > 0)
        out.append({"bucket": b, "n": len(v),
                    "win_rate": round(wins / len(v) * 100, 1),
                    "avg_ret": round(sum(v) / len(v), 2)})
    return out


# ── D2：大跌前一日快照预检（"瓦解前"识别能力） ──
D2_SNAP = "2026-09-01"      # 大跌(9/2)前一日快照
D2_CRASH = "2026-09-02"     # 大跌日


def _grp_stats(values: list) -> str:
    """n / 胜率 / 均收益 摘要串。"""
    if not values:
        return "-"
    wins = sum(1 for v in values if v > 0)
    return f"{len(values)} / {wins / len(values) * 100:.1f}% / {sum(values) / len(values):+.2f}%"


def d2_precheck(rows: list) -> list:
    """
    取大跌前一日快照，检验"拥挤度高（瓦解前）→ 大跌日更惨"的预检能力。
    输出：当日 Top10 明细 + 全池分桶次日收益 + 拥挤分与次日收益 Spearman。
    """
    pool = [r for r in rows if r["date"] == D2_SNAP
            and r.get("crowd_score") is not None and r.get("ret_next") is not None]
    if not pool:
        return ["（无该日可算样本，跳过）"]
    lines = [f"共 {len(pool)} 只（{D2_SNAP} 快照，含拥挤分与 {D2_CRASH} 次日收益）。\n",
             "**当日 total Top10 明细**（观察高分池是否已混入高拥挤）：\n",
             "| 代码 | 名称 | total | 拥挤分 | 20日涨% | mult | 次日(9/2)涨跌% |",
             "|---|---|---|---|---|---|---|"]
    topn = sorted(pool, key=lambda r: r["total"], reverse=True)[:10]
    for r in topn:
        m = r.get("crowd") or {}
        lines.append(f"| {r['code']} | {r['name']} | {r['total']} | {r['crowd_score']} | "
                     f"{m.get('ret_20', '-')} | {r.get('crowd_mult', '-')} | {r['ret_next']} |")
    lines.append("")
    lines.append("**全池按拥挤分桶 → 大跌日收益**（n / 胜率 / 均收益）：\n")
    lines.append("| 拥挤桶 | 次日(9/2)收益 |")
    lines.append("|---|---|")
    for lo, hi, lb in ((None, 30, "高拥挤(<30)"), (30, 50, "中拥挤(30-50)"), (50, None, "低拥挤(≥50)")):
        vals = [r["ret_next"] for r in pool
                if (lo is None or r["crowd_score"] >= lo)
                and (hi is None or r["crowd_score"] < hi)]
        lines.append(f"| {lb} | {_grp_stats(vals)} |")
    sp = spearman([r["crowd_score"] for r in pool], [r["ret_next"] for r in pool])
    lines.append("")
    lines.append(f"拥挤分 vs 次日收益 Spearman：{sp['rho']}(n={sp['n']})"
                 if sp else "拥挤分 vs 次日收益 Spearman：样本不足")
    return lines


def build_report(rows: list) -> str:
    L = []
    add = L.append
    add("# 因子分析 + 拥挤度回顾性对照（阶段 0）\n")
    add(f"> 运行：{os.path.basename(__file__)} ｜ 快照 {rows[0]['date']} ~ {rows[-1]['date']} ｜ "
        f"样本 {len(rows)} 条（有日线可算）\n")

    # ── 0 覆盖 ──
    add("## 0. 数据覆盖\n")
    per_date = defaultdict(int)
    for r in rows:
        per_date[r["date"]] += 1
    add("| 日期 | 有效样本 |")
    add("|---|---|")
    for d in sorted(per_date):
        add(f"| {d} | {per_date[d]} |")
    add("")

    # ── 1 维度 IC ──
    add("## 1. 五维度 IC（对持有收益的 Spearman，rho / n）\n")
    for horizon in HORIZONS:
        add(f"### 1.{horizon}. 持有 {horizon} 日\n")
        for exc in (False, True):
            label = "去市场超额" if exc else "原始收益"
            add(f"**{label}**\n")
            add("| 窗口(样本) | " + " | ".join(DIMS) + " |")
            add("|---|" + "---|" * len(DIMS))
            tb = ic_table(rows, horizon, exc)
            for wname, info in tb.items():
                cells = []
                for dim in DIMS:
                    sp = info["dims"].get(dim)
                    cells.append(f"{sp['rho']:.3f}({sp['n']})" if sp else "-")
                add(f"| {wname}({info['n']}) | " + " | ".join(cells) + " |")
            add("")
    # ── 1.7 综合分 / 拥挤分 IC（阶段 0 关键判据） ──
    add("### 1.7. 综合分 total_score / 拥挤分 crowd_score 的 IC\n")
    add("| 字段 | 口径 | 整体 | 早段 | 震荡偏空段 |")
    add("|---|---|---|---|")
    for field, label in (("total", "综合分 total"), ("crowd_score", "拥挤分 crowd")):
        for horizon in HORIZONS:
            for exc in (False, True):
                tag = "去超额" if exc else "原始"
                cells = []
                tb = pseudo_ic(rows, field, horizon, exc)
                for wname in ("整体", "早段", "震荡偏空段"):
                    info = tb.get(wname) or {}
                    ic = info.get("ic")
                    cells.append(f"{ic['rho']:.3f}({ic['n']})" if ic else "-")
                add(f"| {label} | 持{horizon}日/{tag} | " + " | ".join(cells) + " |")
    add("")

    # ── 2 A/B 对照 ──
    add("## 2. A/B 对照：每日 TopN（原 total vs total × 拥挤惩罚）\n")
    add("> B 组在全池（含无惩罚信息 mult=1 的股票）上按 total×mult 重排。"
        "若 TopN 内几乎没有拥挤股，B 与 A 将相同——这本身是诊断信号。\n")
    add("| 持有 | 候选 | 池 | 日数 | 个股数 | 胜率 | 均收益 |")
    add("|---|---|---|---|---|---|---|")
    for horizon in HORIZONS:
        a10 = topn_stats(rows, lambda r: r["total"], horizon)
        b10 = topn_stats(rows, lambda r: r["total"] * (r.get("crowd_mult") or 1.0), horizon)
        add(f"| {horizon}日 | **A 原 total** | Top10 | {a10['dates']} | {a10['n']} | "
            f"{a10['win_rate']}% | {a10['avg_ret']}% |")
        add(f"| {horizon}日 | B total×crowd | Top10 | {b10['dates']} | {b10['n']} | "
            f"{b10['win_rate']}% | {b10['avg_ret']}% |")
        a20 = topn_stats(rows, lambda r: r["total"], horizon, topn=20)
        b20 = topn_stats(rows, lambda r: r["total"] * (r.get("crowd_mult") or 1.0), horizon, topn=20)
        add(f"| {horizon}日 | **A 原 total** | Top20 | {a20['dates']} | {a20['n']} | "
            f"{a20['win_rate']}% | {a20['avg_ret']}% |")
        add(f"| {horizon}日 | B total×crowd | Top20 | {b20['dates']} | {b20['n']} | "
            f"{b20['win_rate']}% | {b20['avg_ret']}% |")
        a50 = topn_stats(rows, lambda r: r["total"], horizon, topn=50)
        b50 = topn_stats(rows, lambda r: r["total"] * (r.get("crowd_mult") or 1.0), horizon, topn=50)
        add(f"| {horizon}日 | **A 原 total** | Top50 | {a50['dates']} | {a50['n']} | "
            f"{a50['win_rate']}% | {a50['avg_ret']}% |")
        add(f"| {horizon}日 | B total×crowd | Top50 | {b50['dates']} | {b50['n']} | "
            f"{b50['win_rate']}% | {b50['avg_ret']}% |")
    add("")

    # ── 2.5 拥挤度分桶收益（全池截面，阶段 0 主判据） ──
    add("## 2.5 拥挤度分桶收益（全池截面，阶段 0 主判据）\n")
    add("| 持有 | 拥挤桶 | n | 胜率 | 均收益 |")
    add("|---|---|---|---|---|")
    for horizon in HORIZONS:
        for bs in bucket_stats(rows, horizon):
            add(f"| {horizon}日 | {bs['bucket']} | {bs['n']} | "
                f"{bs['win_rate']}% | {bs['avg_ret']}% |")
    add("")

    # ── 3 C 交互矩阵 ──
    add("## 3. C 交互矩阵：质量 × 拥挤度\n")
    add("质量高=质量分≥60；拥挤高=拥挤分<50（该组受 0.95/0.85 惩罚）。"
        "样本随日期累积，fwd 未满的格自动为空。\n")
    for horizon in HORIZONS:
        add(f"### 3.{horizon}. 持有 {horizon} 日（n / 胜率 / 均收益）\n")
        add("| | 低拥挤(≥50) | 高拥挤(<50) |")
        add("|---|---|---|")
        for q_hi in (True, False):
            ql = "质量高(≥60)" if q_hi else "质量低(<60)"
            cells = []
            for c_hi in (True, False):
                crowd_cond = lambda r: (r["crowd_score"] >= 50) if not c_hi else (r["crowd_score"] < 50)
                pool = [r for r in rows
                        if r.get(f"fwd{horizon}") is not None
                        and r["crowd_score"] is not None
                        and r["dims"].get("质量") is not None
                        and ((r["dims"]["质量"] >= 60) == q_hi)
                        and crowd_cond(r)]
                if pool:
                    wins = sum(1 for r in pool if r[f"fwd{horizon}"] > 0)
                    avg = sum(r[f"fwd{horizon}"] for r in pool) / len(pool)
                    cells.append(f"{len(pool)} / {wins / len(pool) * 100:.1f}% / {avg:+.2f}%")
                else:
                    cells.append("-")
            add(f"| {ql} | {cells[0]} | {cells[1]} |")
        add("")

    # ── 4 D2 预检 ──
    add("## 4. D2：大跌前一日快照预检（瓦解前识别）\n")
    for line in d2_precheck(rows):
        add(line)
    add("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-17")
    args = ap.parse_args()

    snaps = load_snapshots(args.since)
    print(f"[snapshots] {len(snaps)} 条（{args.since} 起）")
    if not snaps:
        print("无快照数据，退出")
        return

    codes = [s["code"] for s in snaps] + [MKT_CODE]
    price_map = load_ohlc(codes)

    mkt_series = [b["close"] for b in price_map.get(MKT_CODE, [])]
    mkt_dates = {b["date"]: i for i, b in enumerate(price_map.get(MKT_CODE, []))}

    rows, missing = [], 0
    for s in snaps:
        r = enrich(s, price_map, mkt_series, mkt_dates)
        if r:
            rows.append(r)
        else:
            missing += 1
    rows.sort(key=lambda r: (r["date"], r["rank"] or 999))
    print(f"[enriched] {len(rows)} 可算，{missing} 缺日线/停牌弃")

    if not rows:
        print("无可算样本，退出")
        return

    md = build_report(rows)
    outdir = os.path.join(BACKEND_DIR, "backtest_reports")
    os.makedirs(outdir, exist_ok=True)
    fname = os.path.join(outdir, "factor_" + datetime_now_str() + ".md")
    with open(fname, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[report] 已写入 {fname}")

    # 摘要
    print("\n=== 摘要（完整报告见文件）===")
    n_crowd = sum(1 for r in rows if r.get("crowd_mult") is not None and r["crowd_mult"] < 1.0)
    print(f"拥挤度可算样本 {sum(1 for r in rows if r.get('crowd_score') is not None)}，"
          f"其中触发惩罚(mult<1) {n_crowd}")
    for horizon in HORIZONS:
        a = topn_stats(rows, lambda r: r["total"], horizon)
        b = topn_stats(rows, lambda r: r["total"] * (r.get("crowd_mult") or 1.0), horizon)
        print(f"持有{horizon}日  A原Total(Top10): {a['dates']}日/n={a['n']} 胜率{a['win_rate']}% 均{a['avg_ret']}%")
        print(f"          B×crowd(Top10) : {b['dates']}日/n={b['n']} 胜率{b['win_rate']}% 均{b['avg_ret']}%")
    print("拥挤度分桶（全池）:")
    for horizon in HORIZONS:
        for bs in bucket_stats(rows, horizon):
            print(f"  持{horizon}日 {bs['bucket']}: n={bs['n']} 胜率{bs['win_rate']}% 均{bs['avg_ret']}%")


def datetime_now_str() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d_%H%M")


if __name__ == "__main__":
    main()
