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

from app.backtest import data
from app.database import db
from app.signals.tracker import HOLDINGS_MAP

RATE_LIMIT = 1.0   # 每请求间隔（秒），东财对连续请求会断连，放慢更稳

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


def _collect_strategy_codes() -> list:
    """从 strategy_results 提取全部出现过的个股代码（含名称，去重）。"""
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


def backfill_daily() -> dict:
    """每日增量回填（供调度器调用）：ETF 池 + 沪深300 + 战法新个股。
    已回填标的只补最新日期之后，新出现的个股全量。返回统计 dict。"""
    stats = {"codes": 0, "rows": 0}
    for name, code in HOLDINGS_MAP.items():
        stats["codes"] += 1
        stats["rows"] += backfill(code, name)
    stats["codes"] += 1
    stats["rows"] += backfill("sh000300", "沪深300指数")
    for code, name in _collect_strategy_codes():
        stats["codes"] += 1
        stats["rows"] += backfill(code, name)
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
