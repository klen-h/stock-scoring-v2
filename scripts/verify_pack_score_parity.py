#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】评分口径对齐验证（PLAN_PACK_MIGRATION Phase 2 验收第 4 项）
================================================================================

命题：pack 指标成为两端统一事实源——同一份 _series 喂后端 engine.score_stock
与前端 scoringEngine.scoreStock，总分差必须 <1 分。

方法：
  1. 从本地 pack（data/pack/backend-pack.db）取 K 线 → 用与生成器完全相同的
     compute_latest_indicators 算出 _series（60 天指标数组，含 round2 舍入）
  2. 构造同一份 stock_info（双方完全相同的输入），后端打分
  3. 调 node scripts/pack_score_parity.mjs（前端 scoreStock，同一输入）
  4. 对比总分：|后端 - 前端| 的分布；<1 分 = 通过

用法：python scripts/verify_pack_score_parity.py [--n 30]
================================================================================
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
PACK_DB = os.path.join(BACKEND_DIR, "data", "pack", "backend-pack.db")
NODE_SCRIPT = os.path.join(SCRIPTS_DIR, "pack_score_parity.mjs")
OUT_JSON = os.path.join(BACKEND_DIR, "data", "pack_parity_input.json")

# 同一 stock_info 两侧共用（评分用到的字段全集；财报为空 → 成长/质量两侧同样跳过）
STOCK_INFO_TEMPLATE = {
    "pe": 18.5, "pb": 2.1, "turnover_rate": 2.35, "amplitude": 3.2,
    "change_pct": 1.05, "float_cap": 1200000,  # 万元
}


def load_cases(n):
    sys.path.insert(0, BACKEND_DIR)
    os.environ.setdefault("DATA_SOURCE", "local")
    from app.scoring.indicator_cache import compute_latest_indicators

    conn = sqlite3.connect(PACK_DB)
    rows = conn.execute(
        "SELECT code, name, market_cap FROM codes WHERE market_cap > 0 "
        "ORDER BY market_cap DESC LIMIT ?", (n,)).fetchall()
    cases = []
    for code, name, cap in rows:
        bars = conn.execute(
            "SELECT date, open, high, low, close, volume FROM klines "
            "WHERE code=? ORDER BY date ASC", (code,)).fetchall()
        if len(bars) < 300:
            continue
        dict_bars = [{"date": b[0], "open": b[1], "high": b[2], "low": b[3],
                      "close": b[4], "volume": b[5]} for b in bars]
        ind = compute_latest_indicators(dict_bars)
        series = ind.get("_series")
        if not series or len(series) < 40:
            continue
        stock_info = dict(STOCK_INFO_TEMPLATE)
        stock_info["name"] = name
        stock_info["market_cap"] = cap
        cases.append({"code": code, "name": name, "series": series,
                      "stock_info": stock_info})
    conn.close()
    return cases


def backend_scores(cases):
    from app.scoring.engine import ScoreEngine
    eng = ScoreEngine()
    out = []
    for c in cases:
        r = eng.score_stock(code=c["code"], name=c["name"],
                            technical_data=c["series"],
                            stock_info=c["stock_info"],
                            fundamental={})
        out.append({"code": c["code"], "name": c["name"],
                    "backend": r.total_score})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    args = ap.parse_args()

    print(f"[1/3] 从本地 pack 计算指标 + 后端评分（{args.n} 只）...")
    cases = load_cases(args.n)
    if len(cases) < 10:
        print(f"::error::有效样本不足（{len(cases)}）——pack K 线太少")
        sys.exit(1)
    results = backend_scores(cases)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False)
    print(f"  后端均分 {sum(r['backend'] for r in results) / len(results):.2f}")

    print("[2/3] Node 前端引擎评分（同一输入）...")
    subprocess.run(["node", NODE_SCRIPT, OUT_JSON], check=True)
    with open(OUT_JSON + ".out", encoding="utf-8") as f:
        front = {x["code"]: x["frontend"] for x in json.load(f)}

    print("[3/3] 对比：")
    diffs = []
    bad = []
    for r in results:
        d = abs(r["backend"] - front.get(r["code"], 999))
        diffs.append(d)
        flag = "OK " if d < 1 else "FAIL"
        if d >= 1:
            bad.append((r, front.get(r["code"])))
        print(f"  {flag} {r['code']} {r['name']:<6} 后端 {r['backend']:6.2f} | "
              f"前端 {front.get(r['code'], 0):6.2f} | diff {d:.3f}")
    print(f"\nmax diff = {max(diffs):.3f}  avg = {sum(diffs)/len(diffs):.3f}  "
          f"通过(<1分) {sum(1 for d in diffs if d < 1)}/{len(diffs)}")
    os.remove(OUT_JSON)
    if os.path.exists(OUT_JSON + ".out"):
        os.remove(OUT_JSON + ".out")
    sys.exit(0 if not bad else 2)


if __name__ == "__main__":
    main()
