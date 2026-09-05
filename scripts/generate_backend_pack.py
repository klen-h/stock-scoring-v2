#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】生成"后端数据包"（backend-pack，供 DATA_SOURCE=pack/local 读取层用）
================================================================================

与 generate-kline-pack.py（前端 IndexedDB 用）并行的另一条分发线：
后端不再读 Supabase 的 kline_cache/indicator_cache/backtest_prices（egress 大头），
改读本脚本产出的数据包。

包内容：
  codes      {code: {name, market_cap}}
  klines     {code: [[date, o, h, l, c, v], ...]}  ← 腾讯 qfq 日线，≤500 根
  indicators {code: {ma5..., "_series": [...]}}   ← 复用后端 compute_latest_indicators
  date       数据日期

股票池（取并集）：
  1. Supabase kline_cache 现有代码清单（只读 code 列 ~11KB，egress 可忽略；
     需要 DATABASE_URL，读不到就跳过）
  2. 实时行情按市值前 800 只

运行（GitHub Actions 的 kline-data 工作流调用；也可本地手动）：
  python scripts/generate_backend_pack.py --output-dir ./data/kline
产出：
  backend-pack-YYYYMMDD.json.gz + backend-pack-latest.json.gz（随 Pages 发布）
================================================================================
"""

import argparse
import gzip
import importlib.util
import json
import os
import sys
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
BACKEND_KLINE_COUNT = 500          # 与后端 CACHE_KLINE_COUNT 对齐
CAP_TOP_N = 800                    # 市值兜底池上限

sys.path.insert(0, BACKEND_DIR)


def load_gkp():
    """加载 generate-kline-pack.py（文件名带连字符，需 importlib）。
    复用其成熟的：股票池/批量行情/两轮重试+WAF 退避的 K 线拉取。"""
    spec = importlib.util.spec_from_file_location(
        "gkp", os.path.join(SCRIPTS_DIR, "generate-kline-pack.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def supabase_kline_codes() -> list:
    """读 Supabase kline_cache 的代码清单（只取 code 列 ~11KB）。失败返回 []。"""
    url = (os.environ.get("DATABASE_URL") or "").strip()
    if not url:
        print("  未配置 DATABASE_URL，跳过 Supabase 代码清单（仅用市值池）")
        return []
    try:
        import psycopg2
        conn = psycopg2.connect(url, connect_timeout=10)
        cur = conn.cursor()
        cur.execute("SELECT code FROM kline_cache")
        out = [r[0] for r in cur.fetchall() if r[0] and len(r[0]) == 6]
        conn.close()
        print(f"  Supabase kline_cache 代码清单: {len(out)} 只")
        return out
    except Exception as e:
        print(f"  ⚠️ 读取 Supabase 代码清单失败（忽略，仅用市值池）: {e}")
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="./data/kline")
    ap.add_argument("--cap-top", type=int, default=CAP_TOP_N)
    args = ap.parse_args()

    print("=== 后端数据包生成（backend-pack）===")
    gkp = load_gkp()

    # 1. 实时行情（拿名称/市值 + 市值池）
    print("\n[1/4] 拉取实时行情...")
    quotes = gkp.fetch_realtime_batch(gkp.build_stock_pool())
    by_cap = sorted(quotes.items(),
                    key=lambda kv: kv[1].get("market_cap", 0) or 0, reverse=True)
    cap_codes = [c for c, _ in by_cap[:args.cap_top]]

    # 2. 股票池 = Supabase 现有清单 ∪ 市值前 N
    sb_codes = [c for c in supabase_kline_codes() if c in quotes]
    pool = list(dict.fromkeys(sb_codes + cap_codes))
    print(f"\n[2/4] 股票池: Supabase 清单 {len(sb_codes)} ∪ 市值前 "
          f"{len(cap_codes)} = {len(pool)} 只")

    # 3. 拉 500 根日线（复用两轮重试 + WAF 退避）
    print("\n[3/4] 拉取 K 线（500 根/只，含 WAF 退避）...")
    klines_raw = gkp.fetch_all_klines(pool, BACKEND_KLINE_COUNT)

    # 4. 计算指标（复用后端引擎，与 indicator_cache 同口径）
    print("\n[4/4] 计算预计算指标...")
    try:
        from app.scoring.indicator_cache import compute_latest_indicators
    except Exception as e:
        print(f"  ⚠️ 指标引擎不可用（{e}），包内将不含 indicators")
        compute_latest_indicators = None

    codes_meta, klines_out, ind_out = {}, {}, {}
    for code in pool:
        bars = klines_raw.get(code)
        if not bars or len(bars) < 30:
            continue
        q = quotes.get(code) or {}
        codes_meta[code] = {"name": q.get("name", ""),
                            "market_cap": q.get("market_cap", 0) or 0}
        klines_out[code] = bars
        if compute_latest_indicators:
            try:
                dict_bars = [{"date": b[0], "open": b[1], "high": b[2],
                              "low": b[3], "close": b[4], "volume": b[5]}
                             for b in bars]
                ind = compute_latest_indicators(dict_bars)
                if ind and ind.get("ma5") is not None:
                    ind_out[code] = ind
            except Exception as e:
                print(f"  指标计算失败 {code}: {e}")

    # 完整性护栏 #2：指标计算启用却一只都没算出来 → 中止（发出无指标包 = 静默降级）
    if compute_latest_indicators and klines_out and not ind_out:
        print("::error::K线拉取成功但指标计算 0 只成功 —— 通常是依赖缺失，"
              "检查工作流 pip install（需含 fastapi 等后端依赖）")
        sys.exit(6)

    _write_packs(args.output_dir, codes_meta, klines_out, ind_out)
    print(f"\n=== 完成: K线 {len(klines_out)} 只 / 指标 {len(ind_out)} 只 ===")


def _write_packs(output_dir, codes_meta, klines_out, ind_out):
    date_str = datetime.now().strftime("%Y%m%d")
    os.makedirs(output_dir, exist_ok=True)
    pack = {"version": 1, "date": date_str,
            "codes": codes_meta, "klines": klines_out, "indicators": ind_out}

    dated = os.path.join(output_dir, f"backend-pack-{date_str}.json.gz")
    with gzip.open(dated, "wt", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  完整包: {dated} ({os.path.getsize(dated) / 1048576:.1f} MB)")

    latest = os.path.join(output_dir, "backend-pack-latest.json.gz")
    if os.path.exists(latest):
        os.remove(latest)
    try:
        os.symlink(os.path.basename(dated), latest)
    except OSError:
        import shutil
        shutil.copy2(dated, latest)

    # 完整性护栏（与 generate-kline-pack 同思路）：本次显著少于上次时醒目警告
    try:
        with gzip.open(latest, "rt", encoding="utf-8") as f:
            prev_n = len(json.load(f).get("klines") or {})
        cur_n = len(klines_out)
        if prev_n and cur_n < prev_n * 0.8:
            print(f"\n  ⚠️ 警告: 本次仅 {cur_n} 只，上次 {prev_n} 只"
                  f"（{cur_n / prev_n:.0%}）—— 可能被腾讯限流")
    except Exception:
        pass


if __name__ == "__main__":
    main()
