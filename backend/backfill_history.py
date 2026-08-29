"""
================================================================================
【文件作用】回测历史数据回填：ETF 池 + 沪深300基准 + 战法历史个股 → backtest_prices
================================================================================
运行方式：
  python backfill_history.py --all         # 全部（ETF + 指数 + 个股）
  python backfill_history.py --etf         # 仅 ETF 池（28 只）
  python backfill_history.py --index       # 仅沪深300 基准
  python backfill_history.py --stocks      # 仅战法历史出现过的个股

幂等：按 code+date 去重，已回填的代码跳过（断点续传）。
限速 2 req/s（每请求间隔 0.5s），避免东财限流。
================================================================================
"""

import argparse
import json
import time
from datetime import timedelta

from app.backtest import data
from app.database import db
from app.flash import rules
from app.signals.tracker import HOLDINGS_MAP

RATE_LIMIT = 1.0   # 每请求间隔（秒），东财对连续请求会断连，放慢更稳
DAILY_STOCK_QUOTA = 150  # 每晚个股回填上限：数据源限流下长任务易被打断，分批推进更稳

_EM_FAIL_STREAK = 0   # 东财连续失败计数，>=3 后全局切换腾讯源（东财可能被临时封 IP）


def backfill(code: str, name: str) -> int:
    """增量回填单只：已有数据只补最新日期之后，无数据全量。返回写入行数。"""
    global _EM_FAIL_STREAK
    latest = db.fetch_one(
        "SELECT MAX(date) AS d FROM backtest_prices WHERE code = %s", (code,))
    start = (latest or {}).get("d")
    if start:
        print(f"  增量 {code} {name}（已有数据至 {start}）")
    rows = data.fetch_history(code, start=start)
    if not rows:
        _EM_FAIL_STREAK += 1
        if _EM_FAIL_STREAK >= 3 and not data.DISABLE_EASTMONEY:
            data.DISABLE_EASTMONEY = True
            print("  [WARN] 东财连续失败，后续全部切换腾讯源")
        print(f"  [FAIL] {code} {name} 无数据")
        return 0
    _EM_FAIL_STREAK = 0
    n = data.save_prices(code, name, rows)
    print(f"  [OK] {code} {name}: {n} 条 ({rows[0]['date']} ~ {rows[-1]['date']})")
    time.sleep(RATE_LIMIT)
    return n


def backfill_etf() -> int:
    print("── ETF 池 ──")
    total = 0
    for name, code in HOLDINGS_MAP.items():
        total += backfill(code, name)
    print(f"ETF 完成，共写入 {total} 条\n")
    return total


def backfill_index() -> int:
    print("── 沪深300 基准 ──")
    n = backfill("sh000300", "沪深300指数")
    print("")
    return n


def _collect_strategy_codes(days: int = 30) -> list:
    """提取需回填的个股代码（含名称，去重）：
    1. strategy_results 中全部战法选股；
    2. 近 days 天 ranking_history 中出现过的评分股票。

    评分 TopN 每日变动，很多不在战法池中；若只回填战法个股，
    regime_review 分层复盘会因无行情跳过 90%+ 的评分记录、结论失真，
    因此必须把评分股票一并纳入回填池。"""
    codes = {}
    rows = db.fetch("SELECT results_json FROM strategy_results WHERE count > 0")
    for r in rows:
        try:
            items = json.loads(r["results_json"])
        except (json.JSONDecodeError, KeyError):
            continue
        for it in items or []:
            c = str(it.get("code") or "").strip()
            if len(c) == 6:
                codes[c] = it.get("name") or c
    since = (rules.beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetch(
        "SELECT code, name FROM ranking_history WHERE rank_date >= %s", (since,))
    for r in rows:
        c = str(r["code"] or "").strip()
        if len(c) == 6:
            codes.setdefault(c, r.get("name") or c)
    return list(codes.items())


def backfill_stocks() -> int:
    print("── 战法历史个股 ──")
    items = _collect_strategy_codes()
    print(f"  共 {len(items)} 只个股")
    total = 0
    for code, name in items:
        total += backfill(code, name)
    print(f"个股完成，共写入 {total} 条\n")
    return total


def _latest_date_map(codes: list) -> dict:
    """一次查完池内所有 code 的最新日期（避免逐只查询，远程库往返很慢）。"""
    if not codes:
        return {}
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.fetch(
        f"SELECT code, MAX(date) AS d FROM backtest_prices "
        f"WHERE code IN ({placeholders}) GROUP BY code", tuple(codes))
    return {r["code"]: r["d"] for r in rows}


def backfill_daily() -> dict:
    """每日增量回填（供调度器调用）：ETF 池 + 沪深300 + 战法新个股。
    已回填标的只补最新日期之后，新出现的个股全量。返回统计 dict。

    额外输出 stock_missing：战法个股最新行情日期落后于沪深300基准的清单——
    增量回填对"数据源无返回"只是打印 [FAIL] 不抛异常，若不检测，
    个股行情会静默停更、战法回测无法撮合且无人知晓。

    调度策略（关键）：数据源（东财/腾讯）对连续请求会限流，单晚能成功写入的
    只数有限。若每晚都从池头顺序扫描，前面的股票会占完请求配额，
    池尾股票永远轮不到（实测 400 只池子里 334 只长期 0 数据）。
    因此：①已同步到基准的股票直接跳过不发请求；②无数据的排最前、滞后越久越前；
    ③每晚限量 DAILY_STOCK_QUOTA 只，分批推进，几天内即可补齐。
    """
    stats = {"codes": 0, "rows": 0}
    for name, code in HOLDINGS_MAP.items():
        stats["codes"] += 1
        stats["rows"] += backfill(code, name)
    stats["codes"] += 1
    stats["rows"] += backfill("sh000300", "沪深300指数")

    # 基准日期：个股应同步到该日期
    benchmark = (db.fetch_one(
        "SELECT MAX(date) AS d FROM backtest_prices WHERE code='sh000300'") or {}).get("d")
    stats["benchmark"] = benchmark

    stock_items = _collect_strategy_codes()
    stats["stock_pool"] = len(stock_items)
    codes = [c for c, _ in stock_items]
    name_of = dict(stock_items)
    latest_map = _latest_date_map(codes)

    # ①+② 优先队列：跳过已同步的，无数据排最前
    pending, skipped = [], 0
    for c in codes:
        latest = latest_map.get(c)
        if latest and benchmark and latest >= benchmark:
            skipped += 1
            continue
        pending.append((c, name_of.get(c) or c, latest or ""))
    pending.sort(key=lambda x: x[2])  # ""（无数据）排最前，滞后越久越靠前
    stats["stock_skipped"] = skipped
    stats["stock_pending"] = len(pending)

    # ③ 每晚限量推进
    quota = pending[:DAILY_STOCK_QUOTA]
    stats["stock_quota"] = DAILY_STOCK_QUOTA
    stats["stock_rows"] = 0
    for code, name, _ in quota:
        stats["codes"] += 1
        stats["stock_rows"] += backfill(code, name)

    # 缺失检测：回填后重新取最新日期，仍落后基准的进告警清单
    stats["stock_missing"] = []
    if benchmark:
        after_map = _latest_date_map([c for c, _, _ in pending])
        for c, name, _ in pending:
            latest = after_map.get(c)
            if not latest or latest < benchmark:
                stats["stock_missing"].append(f"{c}({name})")
    print(f"[backfill_daily] 完成: {stats}")
    return stats


def main():
    parser = argparse.ArgumentParser(description="回测历史数据回填")
    parser.add_argument("--all", action="store_true", help="全部回填")
    parser.add_argument("--etf", action="store_true", help="仅 ETF 池")
    parser.add_argument("--index", action="store_true", help="仅沪深300 基准")
    parser.add_argument("--stocks", action="store_true", help="仅战法个股")
    args = parser.parse_args()

    do_all = args.all or not (args.etf or args.index or args.stocks)
    if do_all or args.etf:
        backfill_etf()
    if do_all or args.index:
        backfill_index()
    if do_all or args.stocks:
        backfill_stocks()


if __name__ == "__main__":
    main()
