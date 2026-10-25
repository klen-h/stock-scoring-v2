#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】等权锚历史回填：本地 zzshare_daily.db → 生产库 eq_anchor_history
================================================================================

背景（评分分市况重构 §7 判据冻结 v1.0 / H3 双锚）：
  eq_anchor_history 需要 2010 起的全池日收益序列才能在上线首日就具备 60 日窗口
  判态能力（不回填则要等 60 个交易日的时钟积累）。本脚本一次性灌历史：

  · 每日全池「中位日收益」（判态口径，终裁 §6.2/§7）+「均值日收益」（诊断列）
    + 样本数；个股日收益 = close×factor 环比（复权，§7 已核对 pct_chg ≡ close/
    pre_close 且除权损耗仅 0.02% 行），上市首日无前收 → 自然剔除（§6.1 新股
    首日爆拉正是均值口径污染源，中位口径 + 首日剔除后干净）。
  · 回填后调 eq_anchor.backfill_states() 用冻结分段规则（60 日 ≤−5%/≥+10%，
    窗口内中位 <−3% 天数 ≥3 = 急跌）回填全部 state。
  · 自检：分段段数对照终裁数字（阴跌 38 段 / 急跌 28 段）——对不上即口径漂移，
    必须先复现终裁口径再上线（judge 口径冻结，只许复现不许改）。

零 egress：只读本地 sqlite + 写中心库一张小表（~4000 行）；可重复跑（upsert）。

用法：
  python scripts/backfill_eq_anchor.py --stats-only   # 只算段数自检，不写库
  python scripts/backfill_eq_anchor.py --dry-run      # 算收益+段数，不写库
  python scripts/backfill_eq_anchor.py                # 写库（upsert 幂等）
================================================================================
"""
import argparse
import os
import sys
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception as _e:      # 老 Python/非 tty：不影响主流程，但要留痕（卫生门 #11）
    print(f"[warn] stdout reconfigure failed: {_e}", file=sys.stderr)

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)

ZZSHARE_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                          "data", "zzshare_daily.db")

# 终裁判态的自检基准（§6.2/§7：中位口径 2010 起 阴跌 38 段 / 急跌 28 段）
EXPECTED_SLOW, EXPECTED_CRASH = 38, 28


def load_daily_returns(db_path: str = ZZSHARE_DB):
    """全市场逐日「中位/均值日收益 + 样本数」（升序 DataFrame）。

    供本脚本与 scripts/insample_defensive_params.py（定参）复用同一口径。
    1674 万行一次读入 pandas 会 OOM（实测 383MB object 数组分配失败）⇒
    sqlite 流式游标逐行聚合，内存 O(股票数)；个股日收益 = 复权价相邻行环比
    （ORDER BY date, code ⇒ 与 groupby(code).pct_change() 严格等价，
    上市首日无前收自然剔除、停牌跨日环比）。结果缓存 data/eq_daily_cache.csv
    （以源库 MAX(date) 为失效键——zzshare 只增不改，键不匹配才重算）。
    """
    import math
    import sqlite3
    import pandas as pd
    cache_csv = os.path.join(os.path.dirname(db_path or ZZSHARE_DB),
                             "eq_daily_cache.csv")
    cache_meta = cache_csv + ".meta"
    conn = sqlite3.connect(db_path)
    # 失效键 = MAX(date)+行数（只看 MAX(date) 会漏"中间补洞"：日期上限没变、行数变了）
    src_max, src_cnt = conn.execute(
        "SELECT MAX(date), COUNT(*) FROM daily").fetchone()
    src_key = f"{src_max}|{src_cnt}"
    if os.path.exists(cache_csv) and os.path.exists(cache_meta):
        try:
            with open(cache_meta, encoding="utf-8") as f:
                if f.read().strip() == src_key:
                    cached = pd.read_csv(cache_csv, index_col=0).sort_index()
                    print(f"  [cache] 命中（源库 {src_key}）")
                    return cached
        except Exception as e:
            print(f"  [cache] 读取失败，改为重算: {str(e)[:80]}", file=sys.stderr)
    prev_adj = {}
    out = []          # [(date, median, mean, n), ...]
    cur_day = None
    rets: list = []
    ssum = 0.0

    def _flush():
        if cur_day is not None and rets:
            rs = sorted(rets)
            m = len(rs)
            med = rs[m // 2] if m % 2 else (rs[m // 2 - 1] + rs[m // 2]) / 2.0
            out.append((cur_day, med, ssum / m, m))

    try:
        cur = conn.execute(
            "SELECT date, code, close * factor FROM daily "
            "WHERE close IS NOT NULL AND factor IS NOT NULL AND factor > 0 "
            "AND close > 0 ORDER BY date, code")
        while True:
            rows = cur.fetchmany(100_000)
            if not rows:
                break
            for d, code, adj in rows:
                if d != cur_day:
                    _flush()
                    cur_day, rets, ssum = d, [], 0.0
                pa = prev_adj.get(code)
                if pa and adj and pa > 0:
                    r = adj / pa - 1.0
                    if math.isfinite(r):
                        rets.append(r)
                        ssum += r
                if adj:
                    prev_adj[code] = adj
        _flush()
    finally:
        conn.close()
    df = pd.DataFrame(out, columns=["date", "ret_median", "ret_mean", "n"]
                      ).set_index("date").sort_index()
    try:
        df.to_csv(cache_csv)
        with open(cache_meta, "w", encoding="utf-8") as f:
            f.write(src_key)
    except Exception as e:
        print(f"  [cache] 写缓存失败（忽略）: {e}")
    return df


def segment_counts(states):
    """state 序列 → 各态连续段数（段 = 连续同态 run）。"""
    runs = []
    for s in states:
        if s is None:
            continue
        if runs and runs[-1][0] == s:
            runs[-1][1] += 1
        else:
            runs.append([s, 1])
    return Counter(s for s, _ in runs), runs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zzshare", default=ZZSHARE_DB)
    ap.add_argument("--dry-run", action="store_true", help="不写库")
    ap.add_argument("--stats-only", action="store_true",
                    help="只算分段自检（等价 dry-run，省 upsert 打印）")
    args = ap.parse_args()

    print(f"[backfill_eq_anchor] 读 {args.zzshare} ...")
    daily = load_daily_returns(args.zzshare)
    print(f"  {len(daily)} 个交易日（{daily.index[0]} ~ {daily.index[-1]}），"
          f"最新样本数 n={int(daily['n'].iloc[-1])}")

    from app.backtest.eq_anchor import judge_states, ensure_table, backfill_states
    judged = judge_states([float(x) for x in daily["ret_median"]])
    dates = list(daily.index)
    states = [j["state"] if j else None for j in judged]
    counts, runs = segment_counts(states)
    print(f"  分段段数：{dict(counts)}")
    tail = [f"{d}:{s}" for d, s in zip(dates[-3:], states[-3:])]
    print(f"  最近 3 日：{tail}")
    ok_slow = counts.get("bear_slow", 0) == EXPECTED_SLOW
    ok_crash = counts.get("bear_crash", 0) == EXPECTED_CRASH
    print(f"  自检：阴跌 {counts.get('bear_slow', 0)} 段（终裁 {EXPECTED_SLOW}）"
          f" {'OK' if ok_slow else '⚠️ 不一致'}；"
          f"急跌 {counts.get('bear_crash', 0)} 段（终裁 {EXPECTED_CRASH}）"
          f" {'OK' if ok_crash else '⚠️ 不一致'}")

    if args.dry_run or args.stats_only:
        print("[dry-run] 未写库。")
        return 0

    from app.database import db
    ensure_table()
    rows = [{"anchor_date": str(d), "ret_median": round(float(r.ret_median), 6),
             "ret_mean": round(float(r.ret_mean), 6), "universe_n": int(r.n)}
            for d, r in daily.iterrows()]
    B = 500
    total = 0
    for i in range(0, len(rows), B):
        total += db.upsert_many("eq_anchor_history", rows[i:i + B],
                                ["anchor_date"])
    n_state = backfill_states()
    latest = [f"{d}:{s}" for d, s in zip(dates[-1:], states[-1:])]
    print(f"[apply] eq_anchor_history upsert {total} 行，回填 state {n_state} 行；"
          f"最新态 {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
