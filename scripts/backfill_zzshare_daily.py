#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全市场日线回填（zzshare `daily` 按天路径）→ **本地 SQLite**（绝不进 Supabase）。

用途：为「战法体系重构 · 第 1 步 ③ 历史重扫」提供全市场长历史行情（含退市股、含复权因子）。

用法：
  python scripts/backfill_zzshare_daily.py --smoke          # 冒烟：最近 3 个交易日
  python scripts/backfill_zzshare_daily.py --years 3        # 近 3 年（验证链路）
  python scripts/backfill_zzshare_daily.py --start 2005-01-01 --end 2026-09-26   # 全 20 年（过夜）

关键决策（见 _report_zzshare_全市场可行性_20260926.md）：
  · 按天拉（`daily(trade_date=D, fields='all', limit=6000)` 一次拿全市场一天），
    而非逐股（单股有 1000 条硬上限，且 5000 只 × 分页 = 请求爆炸）
  · `fields='all'` 取 **factor**（复权因子）—— 因为按天模式下 `adj` 参数无效（已实测）
  · 只存原始 close + factor，**复权收益在分析时算**：ret=(close_t×factor_t)/(close_{t-1}×factor_{t-1})−1
  · 断点续传（meta 表）+ 每日期 3 次重试 + 失败清单，任一天中断都不需从头再来
存储：默认 `<项目根>/data/zzshare_daily.db`（SQLite），约 5~6 GB（20 年全市场）。
⚠️ 数据量：3 年 ≈ 420 万行；20 年 ≈ 2800 万行 ⇒ 只能本地，绝不能进 Supabase（500MB 硬限）。
"""
import argparse
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.zzshare_client import get_api  # noqa: E402

DEFAULT_DB = os.path.join(ROOT, "data", "zzshare_daily.db")

# fields='all' 返回的 18 字段中，我们需要的（其余丢弃以控体积）
KEEP = {
    "ts_code": "code", "trade_date": "date", "open": "open", "high": "high",
    "low": "low", "close": "close", "prev_close": "pre_close",
    "quote_rate": "pct_chg", "volume": "volume", "turnover": "amount",
    "factor": "factor", "high_limit": "high_limit", "low_limit": "low_limit",
    "turnover_rate": "turnover_rate", "amp_rate": "amp_rate",
    "is_st": "is_st", "is_paused": "is_paused",
}


def _norm_date(d: str) -> str:
    """'20260924' → '2026-09-24'；已是横杠格式则原样返回。"""
    d = str(d).strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d


def init_db(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    # ★★ 2026-09-26：busy_timeout=30s —— 此前 commit 遇锁（默认 5s）立即抛
    #   `database is locked` 崩溃（VACUUM INTO 撞写锁已引发一次）。现在等 30s 再报错。
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL, high REAL, low REAL, close REAL, pre_close REAL,
            pct_chg REAL, volume REAL, amount REAL, factor REAL,
            high_limit REAL, low_limit REAL, turnover_rate REAL, amp_rate REAL,
            is_st INTEGER, is_paused INTEGER,
            PRIMARY KEY (date, code)
        )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS meta (
            date TEXT PRIMARY KEY, done_at TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_code ON daily(code)")
    # ★★ 2026-09-26：复合索引 (code, date) —— 重扫逐只查询提速 ~300 倍。
    #   单列 code 索引下 `WHERE code=? AND date<=? ORDER BY date DESC LIMIT 60`
    #   需回表排序整只股票全部行（~40ms）；复合索引后范围扫描直接取（~0.07ms）。
    conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_code_date ON daily(code, date DESC)")
    conn.commit()
    return conn


def done_dates(conn) -> set:
    return {r[0] for r in conn.execute("SELECT date FROM meta")}


def save_day(conn, day: str, df) -> int:
    """把某天的 DataFrame 写入 daily 表，返回写入行数。幂等（INSERT OR REPLACE）。"""
    if df is None or len(df) == 0:
        return 0
    rows = []
    for rec in df.to_dict("records"):
        row = {}
        for src, dst in KEEP.items():
            v = rec.get(src)
            if v is None:
                row[dst] = None
            elif dst in ("code", "date"):
                row[dst] = str(v)          # ★ 字符串字段，绝不能走 float（否则 '000001.SZ' 变 None 被吞）
            elif dst in ("is_st", "is_paused"):
                try:
                    row[dst] = 1 if float(v) else 0
                except (TypeError, ValueError):
                    row[dst] = None
            else:
                try:
                    row[dst] = float(v)
                except (TypeError, ValueError):
                    row[dst] = None
        if row.get("code") and row.get("date"):
            row["date"] = _norm_date(row["date"])
            rows.append(row)
    if not rows:
        return 0
    cols = list(KEEP.values())
    sql = ("INSERT OR REPLACE INTO daily (" + ",".join(cols) + ") VALUES ("
           + ",".join(["?"] * len(cols)) + ")")
    conn.executemany(sql, [[r.get(c) for c in cols] for r in rows])
    conn.commit()
    return len(rows)


def fetch_day(api, day8: str, retries: int = 3):
    """拉某天全市场，带重试。返回 DataFrame 或 None。"""
    for attempt in range(retries):
        try:
            df = api.daily(trade_date=day8, fields="all", limit=6000)
            return df
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            print(f"    [FAIL] {day8}: {type(e).__name__}: {str(e)[:80]}")
            return None
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="只拉最近 3 个交易日")
    ap.add_argument("--start", help="起始日 YYYY-MM-DD（含）")
    ap.add_argument("--end", help="结束日 YYYY-MM-DD（含），默认今天")
    ap.add_argument("--years", type=int, help="最近 N 年（--start/--end 未给时）")
    ap.add_argument("--db", default=DEFAULT_DB, help="本地 SQLite 路径")
    args = ap.parse_args()

    api = get_api()
    conn = init_db(args.db)

    if args.smoke:
        days = api.trade_days(days=3)
        days = [_norm_date(d) for d in days]
    elif args.start or args.end:
        start = args.start or "2005-01-01"
        end = args.end or time.strftime("%Y-%m-%d")
        days = [_norm_date(d) for d in api.trade_days(
            day_start=start.replace("-", ""), day_end=end.replace("-", ""))]
    elif args.years:
        end = time.strftime("%Y-%m-%d")
        start = f"{int(end[:4]) - args.years}-{end[5:]}"
        days = [_norm_date(d) for d in api.trade_days(
            day_start=start.replace("-", ""), day_end=end.replace("-", ""))]
    else:
        days = [_norm_date(d) for d in api.trade_days(
            day_start="20050101", day_end=time.strftime("%Y%m%d"))]

    done = done_dates(conn)
    todo = [d for d in days if d not in done]
    print(f"交易日总数 {len(days)}；已完成 {len(done)}；待拉 {len(todo)}")
    print(f"存储：{args.db}")

    t_start = time.time()
    failed = []
    written_total = 0
    for i, day in enumerate(todo, 1):
        df = fetch_day(api, day.replace("-", ""))
        if df is None:
            failed.append(day)
            continue
        n = save_day(conn, day, df)
        written_total += n
        conn.execute("INSERT OR REPLACE INTO meta (date, done_at) VALUES (?, ?)",
                     (day, time.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        if i % 20 == 0 or i == len(todo):
            el = time.time() - t_start
            rate = el / i
            remain = rate * (len(todo) - i)
            print(f"  [{i}/{len(todo)}] {day} 行数={n}  | "
                  f"累计行={written_total} | 已耗时 {el/60:.1f}min | 预计剩余 {remain/60:.1f}min")
        time.sleep(0.5)   # 轻度限速保护

    print(f"\n完成：成功 {len(todo) - len(failed)}/{len(todo)} 天，"
          f"累计写入 {written_total} 行，耗时 {(time.time()-t_start)/60:.1f} 分钟")
    if failed:
        print(f"失败 {len(failed)} 天（重跑命令同上，断点续传会自动补）：")
        print("  ", failed)
    conn.close()


if __name__ == "__main__":
    main()
