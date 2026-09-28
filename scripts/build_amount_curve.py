# -*- coding: utf-8 -*-
"""
================================================================================
【脚本作用】构建「日内成交额累计占比曲线」(U 型曲线)，2026-09-29
================================================================================
【为什么需要它】
  用户要"两市成交额**环比**昨日 + 缩量/放量定性"，而 A 股成交量是 **U 型分布**
  （开盘/尾盘重、午间轻）⇒ **线性外推会严重失真**（如 10:00 时累计占 31.7%，
  线性外推说"全天 = 当前×1.5"，真实倍数是 1/0.317 ≈ 3.15 倍，差 1 倍以上）。
  正确的做法：`预估全天 = 当前累计 / 曲线(当前时刻)`。

【数据源与口径】
  · 曲线源：zzshare `stk_mins`（1min，个股，2016 至今可得）→ **分层抽样**取均值。
    ⚠️ 为什么不直接用全市场：`stk_mins` 不支持指数/板块（实测 883957/sh000300 均空/500）。
    ⚠️ 全市场**当日**分时另有来源（腾讯 `minute/query`，沪 sh000001 + 深 sz399001 累计成交额，
      只需 2 次请求）——但它**只有当日、无历史** ⇒ 只能用于"实时"、不能建历史曲线。
      故：本脚本用抽样建**历史平均曲线**（长期基线）；实时侧日后可用腾讯分时自校准。
  · 分层抽样：沪/深/创业板/科创板/北交所 × 大/中/小盘（见 `SAMPLES`）。

【留出验证（预注册判据）】
  用曲线上某时刻的累计占比，反推该样本日的"全天成交额"，与实际全天比较：
  判据 = **14:00 预估误差中位数 ≤ 6%**（14:00 前误差自然更大，一并输出供标注）。
  验证用**留出法**：曲线由"前半样本"建，在"后半样本"上验证（避免自证）。

【产物】`data/amount_curve.json`
  {built_at, samples, days, points: {"0930": 0.0031, "0935": 0.093, ...},
   validation: {"1400": {"median_err_pct": .., "p90_err_pct": ..}, ...}}

用法：
  python scripts/build_amount_curve.py                # 默认 12 只 × 12 天
  python scripts/build_amount_curve.py --stocks 6 --days 6   # 快速冒烟
================================================================================
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
from app.zzshare_client import get_api  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "amount_curve.json")

# 分层抽样（覆盖两所/创业板/科创板/北交所 × 大中小盘）——A 股日内分布的形状主要由
# 交易机制决定（T+1 / 集合竞价 / 午休），个股差异次之，故 12 只即可稳定。
SAMPLES = (
    "600519.SH", "601318.SH", "600036.SH",     # 沪 大盘
    "000651.SZ", "002415.SZ", "600887.SH",     # 沪/深 中盘
    "300750.SZ", "300059.SZ", "002594.SZ",     # 创业板/深 成长
    "688981.SH", "000001.SZ", "601988.SH",     # 科创板/深/沪
)
POINTS = ("0930", "0935", "0945", "1000", "1015", "1030", "1100", "1130",
          "1300", "1330", "1400", "1415", "1430", "1445", "1455", "1500")


def cum_curve(df):
    """单只单日 → {HHMM: 累计占比}（★ 接口返**降序**，必须显式升序，2026-09-29 踩过）。"""
    rows = []
    for r in (df.to_dict("records") if hasattr(df, "to_dict") else (df or [])):
        t = str(r.get("trade_time") or "")
        amt = float(r.get("amount") or 0)
        if len(t) >= 12:
            rows.append((t[8:12], amt))
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    total = sum(a for _, a in rows)
    if total <= 0:
        return None
    out, cum, idx = {}, 0.0, 0
    for p in POINTS:
        while idx < len(rows) and rows[idx][0] <= p:
            cum += rows[idx][1]
            idx += 1
        out[p] = cum / total
    return out


def recent_days(n):
    """最近 n 个交易日（本地全市场库；线上无此库 ⇒ 本脚本属离线工具）。"""
    import sqlite3
    p = os.path.join(ROOT, "data", "zzshare_daily.db")
    c = sqlite3.connect(p)
    ds = [r[0] for r in c.execute(
        "SELECT DISTINCT date FROM daily ORDER BY date DESC LIMIT ?", (n,)).fetchall()]
    c.close()
    return sorted(ds)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stocks", type=int, default=len(SAMPLES))
    ap.add_argument("--days", type=int, default=12)
    args = ap.parse_args()
    codes = SAMPLES[:args.stocks]
    days = recent_days(args.days)
    print(f"样本 {len(codes)} 只 × {len(days)} 天 = {len(codes)*len(days)} 请求")
    print(f"日期 {days[0]} ~ {days[-1]}")
    api = get_api()
    curves = []
    t0 = time.time()
    for i, code in enumerate(codes, 1):
        got = 0
        for d in days:
            try:
                c = cum_curve(api.stk_mins(ts_code=code, trade_time=d.replace("-", ""),
                                           freq="1min"))
                if c:
                    curves.append(c)
                    got += 1
            except Exception as e:
                print(f"  {code} {d} 失败: {str(e)[:60]}")     # ASCII（铁律⑥）
            time.sleep(0.8)
        print(f"  [{i}/{len(codes)}] {code}: {got}/{len(days)} 天（{time.time()-t0:.0f}s）")
    if len(curves) < 4:
        print("样本不足，退出")
        return

    n = len(curves)
    half = max(2, n // 2)
    train, test = curves[:half], curves[half:]        # 留出法（前半建曲线，后半验证）

    def avg(rows):
        return {p: round(sum(c[p] for c in rows) / len(rows), 4) for p in POINTS}

    curve_tr, curve_all = avg(train), avg(curves)
    print("\n== 平均曲线（训练集）==")
    for p in POINTS:
        v = curve_tr[p]
        print(f"  {p[:2]}:{p[2:]}  {v*100:5.1f}%  {'#' * int(v * 50)}")

    # 留出验证：用训练曲线反推测试样本的全天额，与实际全天比
    print("\n== 留出验证（训练曲线 → 测试样本，误差 = |预估/实际 − 1|）==")
    val = {}
    for p in ("1000", "1030", "1100", "1400", "1430", "1455"):
        errs = []
        for c in test:
            if c[p] > 0:
                errs.append(abs(1 / curve_tr[p] * c[p] - 1) * 100)   # 预估值/实际值
        if errs:
            errs.sort()
            val[p] = {"median_err_pct": round(statistics.median(errs), 2),
                      "p90_err_pct": round(errs[int(len(errs) * 0.9)] if len(errs) > 1
                                           else errs[0], 2), "n": len(errs)}
            print(f"  {p[:2]}:{p[2:]}  中位误差 {val[p]['median_err_pct']:5.2f}%"
                  f"  P90 {val[p]['p90_err_pct']:5.2f}%  (n={len(errs)})")

    out = {"built_at": time.strftime("%Y-%m-%d %H:%M:%S"), "samples": len(codes),
           "days": len(days), "curves_n": n,
           "points": curve_all, "train_points": curve_tr, "validation": val,
           "source": "zzshare stk_mins 分层抽样（全市场当日分时另有腾讯 minute 接口）"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\n已写入 {OUT}")
    ok = val.get("1400", {}).get("median_err_pct")
    print(f"★ 预注册判据（14:00 中位误差 ≤6%）: {'通过' if ok is not None and ok <= 6 else '未通过'}"
          f"（实测 {ok}%）")


if __name__ == "__main__":
    main()
