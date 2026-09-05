#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】生成"后端数据包"backend-pack.db.gz（SQLite，DATA_SOURCE=pack/local 用）
================================================================================

为什么是 SQLite 而不是 JSON：
  45MB 的 JSON 在 Python 里解析成 dict 后常驻 ~150-200MB（Render 512MB 直接 OOM）。
  SQLite 落在磁盘上按需查单只（<5ms），常驻内存 ≈0。前端不读这个文件
  （前端另有 indicators-pack.json.gz + kline-pack）。

三张表：
  klines      (code, date, open, high, low, close, volume)  PK(code, date)
  indicators  (code PRIMARY KEY, json)                      ← 含 _series
  codes       (code PRIMARY KEY, name, market_cap)
  meta        (key PRIMARY KEY, value)                      ← pack_date

运行（GitHub Actions 的 kline-data 工作流调用；也可本地手动）：
  python scripts/generate_backend_pack.py --output-dir ./data/kline
产出（只发 latest，避免历史版本在 Pages 上堆积）：
  backend-pack.db.gz + indicators-pack.json.gz
================================================================================
"""

import argparse
import gzip
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.join(SCRIPTS_DIR, "..", "backend")
# ★ fetch 的 days 语义是"日历天"（起点 now-(days+30)，再 [-days:] 截交易日根）：
#   传 500 实际只得到 ~356 根交易日（起点被卡）。1100 日历天 ≈ 750 根交易日，
#   与 DB backtest_prices 的深度对齐（手册回测 2 年目标 ≈ 500 交易日，留余量）。
BACKEND_KLINE_CAL_DAYS = 1100
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


def write_sqlite(path: str, date_str: str, quotes: dict, klines_raw: dict,
                 ind_out: dict) -> None:
    """K 线 + 指标 + 代码清单写 SQLite（PK 自带索引）。"""
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE klines (
            code TEXT NOT NULL, date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            PRIMARY KEY (code, date)
        )""")
    conn.execute("CREATE TABLE codes (code TEXT PRIMARY KEY, name TEXT, market_cap REAL)")
    conn.execute("CREATE TABLE indicators (code TEXT PRIMARY KEY, json TEXT)")
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")
    rows = []
    for code, bars in klines_raw.items():
        for b in bars:
            rows.append((code, b[0], b[1], b[2], b[3], b[4], b[5]))
    conn.executemany("INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT OR REPLACE INTO codes VALUES (?,?,?)",
        [(c, (quotes.get(c) or {}).get("name", ""),
          (quotes.get(c) or {}).get("market_cap", 0) or 0) for c in klines_raw])
    conn.executemany(
        "INSERT OR REPLACE INTO indicators VALUES (?,?)",
        [(c, json.dumps(ind, ensure_ascii=False)) for c, ind in ind_out.items()])
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('pack_date', ?)", (date_str,))
    conn.commit()
    conn.close()
    print(f"  SQLite: {path} ({os.path.getsize(path) / 1048576:.1f} MB, "
          f"bars={len(rows)}, indicators={len(ind_out)})")


def write_indicators_pack(path: str, date_str: str, ind_out: dict) -> None:
    """前端评分 Worker 用的小包（只含指标，~几 MB）。"""
    pack = {"version": 1, "date": date_str, "indicators": ind_out}
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(pack, f, ensure_ascii=False, separators=(",", ":"))
    print(f"  指标包: {path} ({os.path.getsize(path) / 1048576:.1f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output-dir", default="./data/kline")
    ap.add_argument("--cap-top", type=int, default=CAP_TOP_N)
    args = ap.parse_args()

    print("=== 后端数据包生成（backend-pack.db，SQLite）===")
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

    # 3. 拉 ~750 根交易日线（复用两轮重试 + WAF 退避）
    #    ★ fetch 的 days 参数是"日历天"（起点 now-(days+30)），500 会被起点卡成
    #      ~356 根交易日 —— 传 1100 日历天才能拿到与 DB backtest_prices 对齐的
    #      ~750 根（手册回测 2 年目标 ≈ 500 交易日，留足余量）
    print("\n[3/4] 拉取 K 线（~750 根/只，含 WAF 退避）...")
    klines_raw = gkp.fetch_all_klines(pool, BACKEND_KLINE_CAL_DAYS)

    # 4. 计算指标（复用后端引擎，与 indicator_cache 同口径）
    print("\n[4/4] 计算预计算指标...")
    try:
        from app.scoring.indicator_cache import compute_latest_indicators
    except Exception as e:
        print("::error::指标引擎导入失败（%s）—— 依赖缺失，检查工作流 pip install "
              "（需含 fastapi 等完整后端依赖）" % e)
        sys.exit(6)

    ind_out = {}
    for i, code in enumerate(klines_raw):
        try:
            dict_bars = [{"date": b[0], "open": b[1], "high": b[2],
                          "low": b[3], "close": b[4], "volume": b[5]}
                         for b in klines_raw[code]]
            ind = compute_latest_indicators(dict_bars)
            if ind and ind.get("ma5") is not None:
                ind_out[code] = ind
        except Exception as e:
            print(f"  指标计算失败 {code}: {e}")
        if (i + 1) % 200 == 0:
            print(f"  指标进度: {i + 1}/{len(klines_raw)}")

    # 完整性护栏：K 线不足池的 80% 或指标启用却 0 只成功 → 报错中止（不发出残缺包）
    if len(klines_raw) < len(pool) * 0.8:
        print(f"::error::K线仅拉到 {len(klines_raw)}/{len(pool)} 只（<80%）"
              f"—— 可能被腾讯限流，中止不发包")
        sys.exit(5)
    if klines_raw and not ind_out:
        print("::error::K线拉取成功但指标计算 0 只成功 —— 通常是依赖缺失，"
              "检查工作流 pip install（需含 fastapi 等后端依赖）")
        sys.exit(6)

    date_str = datetime.now().strftime("%Y%m%d")
    os.makedirs(args.output_dir, exist_ok=True)
    db_path = os.path.join(args.output_dir, "backend-pack.db")
    write_sqlite(db_path, date_str, quotes, klines_raw, ind_out)

    gz_path = db_path + ".gz"
    with open(db_path, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
        f_out.write(f_in.read())
    os.remove(db_path)
    print(f"  压缩包: {gz_path} ({os.path.getsize(gz_path) / 1048576:.1f} MB)")

    write_indicators_pack(os.path.join(args.output_dir, "indicators-pack.json.gz"),
                          date_str, ind_out)
    print(f"\n=== 完成: K线 {len(klines_raw)} 只 / 指标 {len(ind_out)} 只 ===")


if __name__ == "__main__":
    main()

