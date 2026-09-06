#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】评分引擎回归基线测试（防止"改引擎 → 分数悄悄漂移"）
================================================================================

原理：把一组固定的输入（pack 指标 _series + 固定 stock_info）与当时的评分结果
一起存成基线。此后每次改完评分引擎跑本脚本：**同样输入重算一遍**，与基线对比。

  - diff 全部 <0.1 → 引擎行为未漂移，通过
  - 有 diff → 要么是你有意改的（用 --update 刷新基线并在 commit 里说明），
              要么是不小心改坏了（回查代码）

注意：基线回放的是【存储的输入】，所以本测试只检测引擎漂移，不检测数据变化
（数据漂移由每日评分快照与排名对照观察）。

用法：
  python scripts/score_regression_check.py --update      # 首次/有意变更后刷新基线
  python scripts/score_regression_check.py               # 日常回归检查
基线：backend/tests/score_baseline.json
================================================================================
"""

import argparse
import json
import os
import sqlite3
import sys

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
PACK_DB = os.path.join(BACKEND_DIR, "data", "pack", "backend-pack.db")
BASELINE = os.path.join(BACKEND_DIR, "tests", "score_baseline.json")
N_CASES = 20

STOCK_INFO_TEMPLATE = {
    "pe": 18.5, "pb": 2.1, "turnover_rate": 2.35, "amplitude": 3.2,
    "change_pct": 1.05, "float_cap": 1200000,
}


def build_cases(n):
    """从本地 pack 构造固定用例（与基线生成时逐字节一致的输入）。"""
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
        info = dict(STOCK_INFO_TEMPLATE)
        info["name"] = name
        info["market_cap"] = cap
        cases.append({"code": code, "series": series, "stock_info": info})
    conn.close()
    return cases


def current_scores(cases):
    sys.path.insert(0, BACKEND_DIR)
    from app.scoring.engine import ScoreEngine
    eng = ScoreEngine()
    out = {}
    for c in cases:
        r = eng.score_stock(code=c["code"], name=c["stock_info"]["name"],
                            technical_data=c["series"],
                            stock_info=c["stock_info"], fundamental={})
        out[c["code"]] = r.total_score
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--update", action="store_true", help="用当前引擎结果刷新基线")
    ap.add_argument("--n", type=int, default=N_CASES)
    args = ap.parse_args()

    os.makedirs(os.path.dirname(BASELINE), exist_ok=True)

    if args.update or not os.path.exists(BASELINE):
        print("[1/2] 从本地 pack 构造固定用例并计算基线...")
        cases = build_cases(args.n)
        if len(cases) < 10:
            print(f"::error::有效用例不足（{len(cases)}）——本地 pack 缺失/太薄")
            sys.exit(1)
        scores = current_scores(cases)
        with open(BASELINE, "w", encoding="utf-8") as f:
            json.dump({"note": "评分引擎回归基线：固定输入+当时得分；改引擎跑 "
                               "score_regression_check.py 对比，有意变更才 --update",
                       "cases": cases, "scores": scores}, f, ensure_ascii=False)
        print(f"[2/2] 基线已写入 {BASELINE}（{len(cases)} 用例，"
              f"文件约 {os.path.getsize(BASELINE) // 1024} KB，含输入快照）")
        return

    print("[1/2] 回放基线输入 → 当前引擎重算...")
    with open(BASELINE, encoding="utf-8") as f:
        base = json.load(f)
    cases = base["cases"]
    now_scores = current_scores(cases)

    print("[2/2] 对比：")
    bad = []
    diffs = []
    for c in cases:
        old = base["scores"].get(c["code"])
        new = now_scores.get(c["code"])
        d = abs((old or 0) - (new or 0))
        diffs.append(d)
        flag = "OK " if d < 0.1 else "DRIFT"
        if d >= 0.1:
            bad.append(c["code"])
        print(f"  {flag} {c['code']} {c['stock_info']['name']:<8} "
              f"基线 {old} → 现在 {new}（diff {d:.2f}）")
    print(f"\nmax diff = {max(diffs):.2f}  通过(<0.1) {len(diffs) - len(bad)}/{len(diffs)}")
    if bad:
        print("\n评分引擎行为发生漂移：")
        print("  - 若是【有意】的引擎改动 → python scripts/score_regression_check.py --update "
              "刷新基线，并在提交说明里写明")
        print("  - 若是【无意】的 → 回查本次引擎改动")
        sys.exit(2)
    print("✅ 评分引擎无漂移")


if __name__ == "__main__":
    main()
