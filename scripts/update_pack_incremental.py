#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】前端 K 线包「增量更新」：zzshare 只补新 bar，历史仍用原腾讯包

背景（2026-10-22 实测）：
  · 腾讯 fqkline **一票一请求**（多股合一实测 param error）⇒ 2144 只 ≈ 2.5h；
  · zzshare `daily(trade_date=...)` **一天一请求**拿全市场；
  · 但两者**前复权口径不同**：用 zzshare 的 factor 自算 qfq，与腾讯包在
    近 150 根内仍差 0.8~4%（多头/加法复权差异）⇒ **不能整包替换**。
  ⇒ 正确用法：**历史保留腾讯包，只把「新交易日」的原始 bar 追加进去**。
    无除权日时，qfq 序列的最后一天 = 原始收盘价，追加即等价于腾讯当天返回；
    若 zzshare 的 factor 在缺口内发生变化（除权），该只**回退腾讯**重拉（通常 0~20 只/日）。

安全校验（缺一不可）：
  ① 重叠校验：上一份包末日收盘 vs zzshare 同日的原始 close（容差 0.5%）；
  ② 因子校验：缺口内 factor 不得变化（变化 = 除权，qfq 会整段重算）；
  ③ 停牌/零成交量不追加；④ 任何异常该只回退腾讯，不产假 bar。

用法：
  python scripts/update_pack_incremental.py                # 更新 backend/data/kline 下的包
  python scripts/update_pack_incremental.py --dry-run      # 只看统计，不写文件
  python scripts/update_pack_incremental.py --backfill     # 先把 zzshare 补到最近交易日
  # 完成后：python scripts/publish_packs_local.py
================================================================================
"""
import argparse
import gzip
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))

PACK_URL = ("https://klen-h.github.io/stock-scoring-v2/data/"
            "kline-pack-latest.json.gz")
DEFAULT_DIR = os.path.join(ROOT, "backend", "data", "kline")
DEFAULT_ZZ = os.path.join(ROOT, "data", "zzshare_daily.db")


def _load_gkp():
    spec = importlib.util.spec_from_file_location(
        "gkp", os.path.join(ROOT, "scripts", "generate-kline-pack.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _zz_code(code):
    c = str(code or "").strip().lower()
    if len(c) != 6 or not c.isdigit():
        return ""
    if c[0] in "03":
        return c + ".SZ"
    if c[0] == "6":
        return c + ".SH"
    if c[0] in "48":
        return c + ".BJ"
    return ""


def _load_prev(pack_dir, url):
    """本地 latest 优先；缺失/可疑（<100KB 或空符号链接）才从 Pages 下 1 次。"""
    path = os.path.join(pack_dir, "kline-pack-latest.json.gz")
    if os.path.exists(path) and os.path.getsize(path) > 100 * 1024:
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                d = json.load(f)
            # ★ 本地可能残留 export_browser_packs_from_local 的小样本包（118 只），
            #   必须拒绝：否则会拿 118 只的包"增量"出 118 只的正式包。
            if len(d.get("stocks") or {}) >= 500:
                print(f"  上一份包: 本地 {path}（{len(d['stocks'])} 只，"
                      f"date={d.get('date')}）")
                return d
            print(f"  本地包只有 {len(d.get('stocks') or {})} 只（小样本）→ 改用 Pages")
        except Exception as e:
            print(f"  本地包不可用（{e}）→ 从 Pages 下载")
    import requests
    print(f"  下载上一份包: {PACK_URL}")
    r = requests.get(PACK_URL, timeout=90)
    r.raise_for_status()
    os.makedirs(pack_dir, exist_ok=True)
    with open(path, "wb") as f:
        f.write(r.content)
    with gzip.open(path, "rt", encoding="utf-8") as f:
        d = json.load(f)
    print(f"  上一份包: Pages（{len(d.get('stocks') or {})} 只，"
          f"date={d.get('date')}）")
    return d


def _zz_max_date(db_path):
    if not os.path.exists(db_path):
        return ""
    c = sqlite3.connect(db_path)
    try:
        return str(c.execute("SELECT MAX(date) FROM daily").fetchone()[0] or "")[:10]
    finally:
        c.close()


def _merge_one(prev_bars, zrows, days):
    """校验重叠 + 因子，返回 (merged_bars, status)。

    status ∈ ok / no_new / mismatch / exdiv / no_overlap
    """
    if not prev_bars or not zrows:
        return prev_bars, "no_overlap"
    last_d, last_c = prev_bars[-1][0], float(prev_bars[-1][4])
    by_d = {str(d)[:10]: (o, h, l, c, v, f, p) for d, o, h, l, c, v, f, p in zrows}
    if last_d not in by_d:
        return prev_bars, "no_overlap"
    o0, h0, l0, c0, v0, f0, _ = by_d[last_d]
    if abs(float(c0) - last_c) / max(last_c, 0.01) > 0.005:
        return prev_bars, "mismatch"          # 上一份包不是腾讯 qfq / 基准不同
    f_last = float(f0 or 1.0)
    added = 0
    merged = list(prev_bars)
    for d, o, h, l, c, v, f, paused in zrows:
        d = str(d)[:10]
        if d <= last_d or paused or not v:
            continue
        if f and abs(float(f) / f_last - 1.0) > 1e-4:
            return prev_bars, "exdiv"         # 缺口内除权 ⇒ 必须腾讯重拉
        merged.append([d, round(float(o), 2), round(float(h), 2),
                       round(float(l), 2), round(float(c), 2),
                       float(v) / 100.0])     # 股 → 手
        added += 1
    return (merged[-days:], "ok" if added else "no_new")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack-dir", default=DEFAULT_DIR)
    ap.add_argument("--zz-db", default=DEFAULT_ZZ)
    ap.add_argument("--days", type=int, default=150)
    ap.add_argument("--url", default=PACK_URL)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backfill", action="store_true",
                    help="先跑 backfill_zzshare_daily.py 补到最近交易日")
    args = ap.parse_args()

    print("=== 前端 K 线包增量更新（zzshare 只补新 bar）===")
    prev = _load_prev(args.pack_dir, args.url)
    prev_date = str(prev.get("date") or "").replace("-", "")
    stocks = prev.get("stocks") or {}

    # 目标交易日
    from app.flash.rules import beijing_now, latest_completed_trading_day
    expected = latest_completed_trading_day(beijing_now())
    target = _zz_max_date(args.zz_db)
    target_ymd = target.replace("-", "")
    print(f"  上一份包 date={prev_date} / zzshare 最新={target or '无'} / "
          f"应处理交易日={expected}")
    if args.backfill and target_ymd < expected.replace("-", ""):
        start = target or "2005-01-01"
        print(f"  回填 zzshare {start} ~ {expected} …")
        subprocess.run([sys.executable, os.path.join(
            ROOT, "scripts", "backfill_zzshare_daily.py"),
            "--start", start, "--end", expected], check=False)
        target = _zz_max_date(args.zz_db)
        target_ymd = target.replace("-", "")
        print(f"  回填后 zzshare 最新={target}")
    if not target or target_ymd <= prev_date:
        print("  已是最新，无需更新。")
        return 0

    gkp = _load_gkp()
    gkp.PACK_QPS = 0.6            # 回退腾讯的少量股票：慢一点，避免撞墙
    conn = sqlite3.connect(args.zz_db)
    updated = no_new = mismatch = exdiv = no_overlap = 0
    fallback = []
    new_stocks = {}
    for code, info in stocks.items():
        bars = info.get("klines") or []
        ts = _zz_code(code)
        if not ts or not bars:
            fallback.append(code)
            continue
        last_d = str(bars[-1][0])[:10]
        rows = conn.execute(
            "SELECT date, open, high, low, close, volume, factor, is_paused "
            "FROM daily WHERE code=? AND date>=? AND date<=? ORDER BY date",
            (ts, last_d, target)).fetchall()
        merged, st = _merge_one(bars, rows, args.days)
        if st == "ok":
            updated += 1
            new_stocks[code] = {"name": info.get("name"),
                                "market_cap": info.get("market_cap"),
                                "klines": merged}
        elif st == "no_new":
            no_new += 1
        elif st == "mismatch":
            mismatch += 1
            fallback.append(code)
        elif st == "exdiv":
            exdiv += 1
            fallback.append(code)
        else:
            no_overlap += 1
            fallback.append(code)
    conn.close()

    print(f"  增量: 更新 {updated} / 无新 bar {no_new} / 除权 {exdiv} / "
          f"重叠不符 {mismatch} / 无重叠 {no_overlap} / 回退腾讯 {len(fallback)}")
    if no_new:
        print("  （无新 bar 多为停牌股；保留原末根，不造假 bar）")

    # 回退腾讯（通常 0~20 只）
    for i, code in enumerate(fallback, 1):
        try:
            kl = gkp.fetch_kline(code, args.days)
        except Exception as e:
            kl = None
            print(f"  腾讯回退异常 {code}: {e}")
        if kl:
            info = stocks.get(code) or {}
            new_stocks[code] = {"name": info.get("name"),
                                "market_cap": info.get("market_cap"),
                                "klines": kl}
        if i % 10 == 0:
            print(f"  回退腾讯 {i}/{len(fallback)} …")

    # 未处理的代码沿用上一份（停牌等）
    for code, info in stocks.items():
        if code not in new_stocks:
            new_stocks[code] = info
    if args.dry_run:
        print(f"  [dry-run] 不写文件；样本 000001 末根: "
              f"{(new_stocks.get('000001') or {}).get('klines', [['-']])[-1]}")
        return 0

    date_str = beijing_now().strftime("%Y%m%d")
    gkp.generate_packs(new_stocks, args.pack_dir, date_str, prev)
    print(f"  已写出 {args.pack_dir}（{len(new_stocks)} 只，date={date_str}）")
    print("  下一步: python scripts/publish_packs_local.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
