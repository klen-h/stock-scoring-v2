#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】每日盘后刷新「指标缓存」（供 GitHub Actions 定时调用）
================================================================================

背景：指标缓存刷新是纯 CPU 活（300 只 × 500 根 K 线算 MA/MACD/RSI/KDJ/BOLL），
在 Render 0.1 CPU 上非常吃力，且它**不需要访问外网行情源**（只读数据库里的
kline_cache）。所以把它从后端常驻调度器搬到 GitHub Actions（4 核 / 16G）跑，
后端只负责读表算分。

三道护栏（缺一不可，否则会静默污染数据）：
  1. DATABASE_URL 必须存在 —— 否则 app.database 会回退本地 SQLite，
     容器一销毁全部丢失，且 job 还显示"成功"。
  2. K 线缓存必须是"今日"的 —— 否则会用昨天的 K 线算出指标、盖上今天的
     时间戳，读侧看 updated_at 以为很新鲜，实际是错的（静默污染排行榜）。
  3. 覆盖率校验 —— 抄自 scripts/generate-kline-pack.py 的完整性校验：
     刷 0 只判失败；少于上次存量 80% 发 warning。

退出码：0 成功 / 2 无 DATABASE_URL / 3 K线不是今日 / 4 股票池为空 / 5 刷新 0 只

用法：
  python scripts/refresh_indicators.py                # 正式刷新
  python scripts/refresh_indicators.py --dry-run      # 只做前置检查，不写库
  python scripts/refresh_indicators.py --limit 300 --min-coverage 0.8
================================================================================
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

BACKEND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
ENV_PATH = os.path.join(BACKEND_DIR, ".env")

_BEIJING = timezone(timedelta(hours=8))


def load_env():
    """本地手动跑时读 backend/.env；CI 上该文件不存在，直接用 secrets 注入的环境变量。"""
    if not os.path.exists(ENV_PATH):
        return
    with open(ENV_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k, v.strip())


load_env()
sys.path.insert(0, BACKEND_DIR)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="刷新股票数（默认取 indicator_cache.INDICATOR_POOL_SIZE）")
    ap.add_argument("--min-coverage", type=float, default=0.8,
                    help="少于上次存量的该比例则告警（默认 0.8）")
    ap.add_argument("--dry-run", action="store_true", help="只做前置检查，不写库")
    args = ap.parse_args()

    # ── 护栏 1：必须连云数据库 ──
    if not (os.environ.get("DATABASE_URL") or "").strip():
        print("::error::DATABASE_URL 未设置 —— 拒绝运行。"
              "（不设会静默回退本地 SQLite，容器销毁即丢，job 却显示成功）")
        return 2

    from app.scoring import kline_cache, indicator_cache
    from app.database import db

    # ── 护栏 2：K 线必须是今日的 ──
    ks = kline_cache.get_cache_status()
    newest = ks.get("newest_update") or ""
    today = datetime.now(_BEIJING).strftime("%Y-%m-%d")
    total_cached = ks.get("total_cached", 0)
    print(f"[kline_cache] 存量 {total_cached} 只，最新 {newest or '空'}（今日 {today}）")
    if not newest.startswith(today):
        print(f"::error::K线缓存不是今日数据（最新={newest or '空'}）——跳过指标刷新。"
              f"请先确认 K 线刷新（Render 15:30）已跑完，避免用昨日 K 线算指标盖今日时间戳")
        return 3

    # ── 股票池：与 K 线缓存"同频"取 —— 36h 内有新鲜 K 线的股票 ──
    # ★ 不能用 kline_cache.get_cache_codes(按 market_cap 排序)：实测表内 2197 行
    #   有 1787 行 market_cap=0（历史回写没带市值），排序严重失真，取到的"前 N"
    #   大半是过期数据（实测 300 只里 279 只因 >36h 被读侧判过期）。
    # ★ 也不能留空让 refresh_indicator_cache 自己取：Actions 冷启动时
    #   tencent._cache 为空 → 它 fallback 成 "LIMIT N" 任意行，同样跑偏。
    #   与 K 线缓存同频（谁有新鲜 K 线就给谁算指标）才是正确语义。
    limit = args.limit or indicator_cache.INDICATOR_POOL_SIZE
    cutoff = (datetime.now(_BEIJING).replace(tzinfo=None)
              - timedelta(hours=kline_cache.MAX_CACHE_AGE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    rows = db.fetch(
        "SELECT code FROM kline_cache WHERE updated_at >= %s "
        "ORDER BY COALESCE(market_cap, 0) DESC LIMIT %s",
        (cutoff, limit),
    )
    codes = [r["code"] for r in (rows or [])]
    if not codes:
        print("::error::kline_cache 里 36h 内没有任何新鲜 K 线 —— K 线刷新未跑成，"
              "指标刷新无从谈起（请先修 K 线刷新）")
        return 4
    print(f"[pool] 待刷新 {len(codes)} 只（36h 内有新鲜 K 线，limit={limit}）")

    before = indicator_cache.get_indicator_cache_status()
    print(f"[indicator_cache] 刷新前存量 {before.get('total_cached')} 只，"
          f"最新 {before.get('newest_update') or '空'}")

    if args.dry_run:
        print("[dry-run] 前置检查全部通过，跳过实际刷新")
        return 0

    # ── 正式刷新（批量版）──
    # ★ 为什么不走 refresh_indicator_cache() 逐只版：它每只要"读一次 + 写一次"远程库，
    #   Supabase 东京节点实测 ~2.4s/往返，300 只 = 600 次往返 ≈ 24 分钟（Actions 会撞
    #   timeout）。批量版把往返压成 3 次，计算逻辑与 save_indicator_cache 完全同源
    #   （compute_latest_indicators / json.dumps(ensure_ascii=False)），结果等价。
    import json
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    kmap = kline_cache.get_cached_klines_batch(codes)   # 1 次往返读全部 K 线
    print(f"[batch] 命中新鲜 K 线 {len(kmap)}/{len(codes)} 只")

    # 已有行保留 name/market_cap（批量 upsert 整行覆盖，避免把它们冲成空值）
    ph = ",".join(["%s"] * len(codes))
    old = {r["code"]: r for r in db.fetch(
        f"SELECT code, name, market_cap FROM indicator_cache WHERE code IN ({ph})",
        tuple(codes))}

    rows = []
    for code in codes:
        klines = kmap.get(code)
        if not klines or len(klines) < 30:
            continue
        try:
            ind = indicator_cache.compute_latest_indicators(klines)
        except Exception as e:
            print(f"[compute] {code} 计算失败: {e}")
            continue
        if not ind or ind.get("ma5") is None:
            continue
        o = old.get(code) or {}
        rows.append({
            "code": code,
            "name": o.get("name") or "",
            "indicators": json.dumps(ind, ensure_ascii=False),
            "kline_count": len(klines),
            "updated_at": now_str,
            "market_cap": o.get("market_cap") or 0,
        })

    if not rows:
        print(f"::error::池 {len(codes)} 只一只都没算出来 —— 判定失败")
        return 5
    refreshed = db.upsert_many("indicator_cache", rows, ["code"])   # 批量写
    print(f"[result] {refreshed} 成功 / {len(codes) - len(rows)} 失败"
          f"（池 {len(codes)} 只）")

    # ── 护栏 3：覆盖率校验 ──
    # ★ 基线必须用「本次股票池大小 len(codes)」，不能用表内总行数：
    #   表里还混着评分时单只回写的中小盘（本次池外的），拿它比会永远误报。
    if refreshed == 0:
        print(f"::error::本次刷新 0 只成功（池 {len(codes)} 只）—— 判定失败")
        return 5
    if refreshed < len(codes) * args.min_coverage:
        print(f"::warning::本次仅刷新 {refreshed} 只，池 {len(codes)} 只"
              f"（{refreshed / len(codes):.0%} < {args.min_coverage:.0%}）——"
              f"可能部分 K 线数据异常，检查 kline_cache")
    return 0


if __name__ == "__main__":
    sys.exit(main())
