#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】主线拥挤度否决 离线验证（PLAN P1-3 / §4b#8）
================================================================================
问题：主线候选（当日 Top50 中所属行业扎堆 ≥2 只）会被传导链 +1 分
      （confluence.py：in_mainline → sector_score += 1）。但"涨太多"的主线
      可能是拥挤末端——元件 30 日 +38% 仍被推荐为主线，是最易误导开仓的输出口。

假设：主线候选命中「拥挤」条件（ret20>30% / ret60>50% / 距 250 日高点<5%
      任一）后，T+1/T+5 收益显著低于不拥挤的主线候选。

方法（离线、无前视）：
  1. 主线候选 = industry_mainline 当日 stock_count>=2 的股票（与 mainline.py 同口径）
  2. 对每个候选，用**信号日之前**的价格算 ret20/ret60/距250日高点
  3. 分组对比 T+1/T+5 收益：拥挤组 vs 不拥挤组 vs 全体主线候选
  4. 同时给一版宽松阈值（ret20>20% / ret60>30%）做敏感性

★ 口径说明：PLAN 的阈值原指行业维度，但 sector_daily 仅 10 天历史（无法算
  20/60 日行业涨幅）→ 改用个股维度（主线候选股自身涨幅）。个股是行业拥挤的
  载体，且 backtest_prices 有 3 年数据，样本充足。

输出：backend/backtest_reports/mainline_crowding_YYYYMMDD_HHMM.md
用法：python scripts/mainline_crowding_backtest.py [--days 30]
================================================================================
"""

import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND_DIR)
with open(os.path.join(BACKEND_DIR, ".env"), encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k, v.strip())

from app.database import db  # noqa: E402

# PLAN §4b#8 阈值（个股维度沿用）
CROWD_RET20 = 30.0      # 20 日涨幅 > 30%
CROWD_RET60 = 50.0      # 60 日涨幅 > 50%
CROWD_NEAR_HIGH = 5.0   # 距 250 日高点 < 5%
# 宽松敏感性阈值
LOOSE_RET20 = 20.0
LOOSE_RET60 = 30.0
LOOSE_NEAR_HIGH = 10.0


def _load_mainline_pairs(since: str) -> dict:
    """{(date, code): industry}——当日行业扎堆 >=2 只的主线候选。"""
    rows = db.fetch("SELECT date, industry, stock_count, stocks_json "
                    "FROM industry_mainline WHERE date >= %s", (since,))
    pairs = {}
    for r in rows or []:
        if (r.get("stock_count") or 0) < 2:
            continue
        try:
            stocks = json.loads(r.get("stocks_json") or "[]")
        except (json.JSONDecodeError, TypeError):
            continue
        for s in stocks:
            c = s.get("code")
            if c:
                pairs[(r["date"], c)] = r["industry"]
    return pairs


def _load_prices(codes: list, since: str) -> dict:
    """{code: [(date, close)]} 升序，批量 IN 查询（100 只/批）。"""
    out = defaultdict(list)
    codes = [c for c in codes if c]
    for i in range(0, len(codes), 100):
        chunk = codes[i:i + 100]
        ph = ",".join(["%s"] * len(chunk))
        rows = db.fetch(
            f"SELECT code, date, close FROM backtest_prices "
            f"WHERE code IN ({ph}) AND date >= %s ORDER BY code, date",
            (*chunk, since))
        for r in rows or []:
            if (r.get("close") or 0) > 0:
                out[r["code"]].append((str(r["date"]), float(r["close"])))
    return {c: sorted(v) for c, v in out.items()}


def _crowding(bars: list, i: int) -> dict:
    """信号日 i 的拥挤指标（全部用 i 及之前的数据，无前视）。"""
    close = bars[i][1]
    out = {"ret20": None, "ret60": None, "dist_high250": None}

    def _ret(n):
        if i - n < 0:
            return None
        base = bars[i - n][1]
        return (close / base - 1) * 100 if base > 0 else None

    out["ret20"] = _ret(20)
    out["ret60"] = _ret(60)
    lo = max(0, i - 249)
    hi = max(b[1] for b in bars[lo:i + 1])
    if hi > 0:
        out["dist_high250"] = (close / hi - 1) * 100      # 距高点，0=就在高点，负=低于高点
    return out


def _is_crowd(c: dict, ret20, ret60, near_high) -> bool:
    if c["ret20"] is not None and c["ret20"] > ret20:
        return True
    if c["ret60"] is not None and c["ret60"] > ret60:
        return True
    if c["dist_high250"] is not None and c["dist_high250"] > -near_high:
        return True
    return False


def _stat(rets: list) -> dict:
    n = len(rets)
    if not n:
        return {"n": 0, "win": None, "avg": None, "median": None}
    s = sorted(rets)
    return {"n": n,
            "win": round(sum(1 for r in rets if r > 0) / n * 100, 1),
            "avg": round(sum(rets) / n, 3),
            "median": round(s[n // 2], 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()

    since = (dt.date.today() - dt.timedelta(days=args.days)).strftime("%Y-%m-%d")
    pairs = _load_mainline_pairs(since)
    if not pairs:
        print("[warn] 窗口内无主线候选（industry_mainline 数据不足）")
        return
    codes = sorted({c for (_d, c) in pairs})
    print(f"[mainline] 主线候选 {len(pairs)} 条 / {len(codes)} 只；窗口 since={since}")

    # 价格：多加载 400 自然日（覆盖 250 交易日）用于算拥挤指标
    price_since = (dt.date.today() - dt.timedelta(days=args.days + 400)).strftime("%Y-%m-%d")
    prices = _load_prices(codes, price_since)
    print(f"[prices] 覆盖 {len(prices)}/{len(codes)} 只")

    horizons = [1, 5]
    groups = {"all": {h: [] for h in horizons},
              "crowd": {h: [] for h in horizons},
              "nocrowd": {h: [] for h in horizons},
              "loose_crowd": {h: [] for h in horizons}}
    detail = []

    for (d, code), ind in pairs.items():
        bars = prices.get(code)
        if not bars:
            continue
        idx = {b[0]: i for i, b in enumerate(bars)}
        i = idx.get(d)
        if i is None or i < 60:
            continue
        c = _crowding(bars, i)
        crowd = _is_crowd(c, CROWD_RET20, CROWD_RET60, CROWD_NEAR_HIGH)
        loose = _is_crowd(c, LOOSE_RET20, LOOSE_RET60, LOOSE_NEAR_HIGH)
        rec = {"date": d, "code": code, "industry": ind, "crowd": crowd, "loose": loose,
               **c, "rets": {}}
        for h in horizons:
            if i + h >= len(bars):
                continue
            base = bars[i][1]
            tgt = bars[i + h][1]
            if base <= 0:
                continue
            r = (tgt / base - 1) * 100
            rec["rets"][h] = r
            groups["all"][h].append(r)
            (groups["crowd"] if crowd else groups["nocrowd"])[h].append(r)
            if loose:
                groups["loose_crowd"][h].append(r)
        detail.append(rec)

    lines = ["# 主线拥挤度否决 离线验证（PLAN P1-3）", "",
             f"> 生成：{dt.datetime.now():%Y-%m-%d %H:%M} ｜ 主线候选 {len(detail)} 条"
             f"（{len({r['code'] for r in detail})} 只）｜ 拥挤阈值：ret20>{CROWD_RET20}% "
             f"或 ret60>{CROWD_RET60}% 或 距250日高点<{CROWD_NEAR_HIGH}%", ""]

    lines += ["## 一、拥挤组 vs 不拥挤组（T+1 / T+5 收益）", "",
              "| 分组 | 持有 | n | 胜率% | 均收益% | 中位% |", "|---|---|---|---|---|---|"]
    for key, label in [("all", "全体主线候选"), ("crowd", "拥挤主线"),
                       ("nocrowd", "不拥挤主线"), ("loose_crowd", "宽松口径拥挤")]:
        for h in horizons:
            s = _stat(groups[key][h])
            lines.append(f"| {label} | T+{h} | {s['n']} | {s['win']} | {s['avg']} | {s['median']} |")

    # 结论
    lines += ["", "## 二、初步结论", ""]
    for h in horizons:
        cr, nc = _stat(groups["crowd"][h]), _stat(groups["nocrowd"][h])
        if cr["n"] and nc["n"]:
            dw = (cr["win"] or 0) - (nc["win"] or 0)
            da = (cr["avg"] or 0) - (nc["avg"] or 0)
            lines.append(f"- T+{h}：拥挤组 {cr['n']} 条 胜率 {cr['win']}% / 均 {cr['avg']}%；"
                         f"不拥挤组 {nc['n']} 条 胜率 {nc['win']}% / 均 {nc['avg']}% "
                         f"→ 差 {dw:+.1f}pp / {da:+.3f}pt")
            if da < 0 and dw < 0:
                lines.append(f"  → **拥挤显著更差**：否决拥挤主线（降级为「只减不加」）成立 ✓")
            elif da < 0 or dw < 0:
                lines.append(f"  → 拥挤略差（单指标），证据偏弱，可降级但不强制。")
            else:
                lines.append(f"  → 拥挤并不更差，否决依据不足。")
        else:
            lines.append(f"- T+{h}：分组样本不足（拥挤 {cr['n']} / 不拥挤 {nc['n']}），无法判断。")

    # 拥挤样本明细（前 15）
    def _f(v, nd=1):
        return "-" if v is None else f"{v:.{nd}f}"

    crowd_rows = [r for r in detail if r["crowd"] and r.get("rets")]
    if crowd_rows:
        lines += ["", "## 三、拥挤主线样本明细（前 15）", "",
                  "| 日期 | 代码 | 行业 | ret20% | ret60% | 距高% | T+1% | T+5% |",
                  "|---|---|---|---|---|---|---|---|"]
        for r in sorted(crowd_rows, key=lambda x: x["date"])[:15]:
            lines.append(
                f"| {r['date']} | {r['code']} | {r['industry']} | "
                f"{_f(r['ret20'])} | {_f(r['ret60'])} | {_f(r['dist_high250'])} | "
                f"{_f(r['rets'].get(1), 2)} | {_f(r['rets'].get(5), 2)} |")

    out = os.path.join(BACKEND_DIR, "backtest_reports",
                       f"mainline_crowding_{dt.datetime.now():%Y%m%d_%H%M}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[report] {out}")
    for k in groups:
        print(f"  {k}: " + " | ".join(f"T+{h} {_stat(groups[k][h])}" for h in horizons))


if __name__ == "__main__":
    main()
