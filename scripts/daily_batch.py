#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】日批合并脚本：一次运行完成全部盘后重活（唯一计算入口）
================================================================================

为什么要有它（2026-09-07 事故复盘）：
  Render 免费实例 512MB 内存 + 临时磁盘，盘后有十几个定时任务各自回源拉数据
  → 内存爆 / 腾讯 WAF 满屏拦截 / Supabase 550MB 每天 → 崩溃重启死循环。

三条设计原则：
  1) ★ 输入复用（根治 WAF）：K线/指标/主力行为统一读 backend-pack.db（零回源）；
     全市场行情**只拉一次**并写进 tencent._cache，后续所有任务共享。
     网络请求从「每天几千次」降到个位数，限流问题自然消失。
  2) ★ 单一入口：所有盘后重活集中在这里（Actions 定时 or 手动 dispatch），
     Render 侧只做只读服务（RENDER_READ_ONLY=1）。
  3) ★ 容错：任一任务失败不中断整体，最后统一汇总并企微告警（可选）。

幂等：各任务写库均为 ON CONFLICT 幂等，重跑安全。

用法：
  python scripts/daily_batch.py --tasks all
  python scripts/daily_batch.py --tasks strategy_scan,contradiction_scan
  python scripts/daily_batch.py --tasks daily_report --force
  python scripts/daily_batch.py --list
================================================================================
"""

import argparse
import asyncio
import os
import sys
import time
import traceback
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)


def load_env():
    """加载 backend/.env（本地跑用；Actions 走 secrets 环境变量）。"""
    path = os.path.join(BACKEND, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _notify(text: str):
    """企微告警（未配置 WECHAT_WEBHOOK 则静默跳过）。"""
    hook = (os.environ.get("WECHAT_WEBHOOK") or "").strip()
    if not hook:
        return
    try:
        import requests
        requests.post(hook, json={
            "msgtype": "markdown",
            "markdown": {"content": f"### 日批任务告警\n{text}"},
        }, timeout=10)
    except Exception as e:
        print(f"  [notify] 告警发送失败: {e}")


# ── 任务实现 ────────────────────────────────────────────────────────────────

def task_backfill():
    """回测价格库增量回填（战法回测的数据底座）。"""
    from backfill_history import backfill_daily
    return f"回填完成: {backfill_daily()}"


def task_mainflow():
    """主力资金流回填（筹码/主力行为因子的输入）。逐股限速，全池约 7 分钟。"""
    from app.mainforce.flow import backfill_all
    return f"资金流回填: {backfill_all()}"


def task_market_snapshot():
    """全市场收盘行情快照（盘后/周末直接从快照恢复，首页秒开）。"""
    from app.tencent import save_market_snapshot
    ok = save_market_snapshot()
    if not ok:
        raise RuntimeError("save_market_snapshot 返回失败")
    return "行情收盘快照已保存"


def task_sector_snapshot():
    """板块每日快照（板块动量序列，非交易日自动跳过）。"""
    from app.sector_industry import take_snapshot
    return f"板块快照: {take_snapshot()}"


def task_strategy_scan():
    """战法全量扫描（各战法信号 + 共振验证 + 持久度）。"""
    from app.flash.scheduler import scan_all_strategies
    stats = scan_all_strategies()
    if stats.get("not_ready"):
        raise RuntimeError(f"当日K线未就绪: {stats.get('data_date')} < {stats.get('expect_date')}")
    return f"战法扫描: {stats}"


def task_contradiction_scan():
    """L2 行为背离扫描（指数vs宽度 / 板块vs资金流 / 北向vs指数）。"""
    from app.contradictions.scanner import scan_all
    from app.contradictions.store import save_contradictions, _today
    d = _today()
    items = scan_all(date=d)
    saved = save_contradictions(d, items)
    return f"矛盾扫描: {len(items)} 条，保存 {saved}"


def task_score_snapshot():
    """评分 Top50 快照（ranking_history，供胜率回查/权重优化复用）。"""
    from app.routers.scoring import score_top
    from app.scoring.ranking_history import record_daily_ranking
    from app.tencent import _cache

    result = asyncio.run(score_top(limit=50))
    data = result.get("data") or []
    if not data:
        raise RuntimeError("score_top 返回空（行情缓存可能为空）")
    stocks_map = _cache.get("stocks", {})
    stocks = [{
        "code": r["code"], "name": r["name"],
        "total_score": r["total_score"], "signal": r["signal"],
        "rank": i + 1, "dimensions": r.get("dimensions") or {},
        "price": (stocks_map.get(r["code"]) or {}).get("price") or 0,
    } for i, r in enumerate(data)]
    n = record_daily_ranking(stocks, False, True)
    return f"评分快照: {n} 条（Top {len(data)}）"


def task_lhb():
    """龙虎榜同步（日榜全量 + 池内个股席位明细）。"""
    from app.mainforce.lhb import backfill_days
    return f"龙虎榜: {backfill_days(10, 3)}"


def task_contradiction_report():
    """矛盾 LLM 报告（依赖上面的扫描结果）。"""
    from app.contradictions.report import run_report
    return f"矛盾报告: {run_report()}"


def task_daily_report():
    """A 股大盘日报（依赖上面的扫描结果，必须最后跑）。"""
    from app.daily_report import run_daily_report
    res = run_daily_report()
    if not res or not res.get("date"):
        raise RuntimeError("日报生成异常（返回空）")
    return f"日报已生成: {res['date']} len={res.get('len')}"


# 顺序 = 依赖顺序：行情 → 数据底座 → 扫描 → 汇总
TASKS = {
    "backfill": (task_backfill, "回测价格回填"),
    "mainflow": (task_mainflow, "主力资金流回填"),
    "market_snapshot": (task_market_snapshot, "全市场行情快照"),
    "sector_snapshot": (task_sector_snapshot, "板块快照"),
    "strategy_scan": (task_strategy_scan, "战法全量扫描"),
    "contradiction_scan": (task_contradiction_scan, "矛盾扫描"),
    "contradiction_report": (task_contradiction_report, "矛盾报告(LLM)"),
    "score_snapshot": (task_score_snapshot, "评分快照"),
    "lhb": (task_lhb, "龙虎榜同步"),
    "daily_report": (task_daily_report, "每日日报"),
}
DEFAULT_ORDER = ["backfill", "mainflow", "market_snapshot", "sector_snapshot",
                 "strategy_scan", "contradiction_scan", "score_snapshot", "lhb",
                 "daily_report"]


def ensure_quotes():
    """★ 拉一次全市场行情 → 写进 tencent._cache，后续所有任务共享（不再各自拉）。"""
    from app.tencent import _cache, refresh_all_stocks
    if _cache.get("stocks"):
        print(f"  行情缓存已有 {len(_cache['stocks'])} 只，跳过刷新")
        return
    print("  刷新全市场行情（唯一一次全市场请求）...")
    t0 = time.time()
    refresh_all_stocks()
    print(f"  行情就绪: {len(_cache.get('stocks') or {})} 只，耗时 {time.time() - t0:.0f}s")


def main():
    ap = argparse.ArgumentParser(description="日批合并（唯一计算入口）")
    ap.add_argument("--tasks", default="all",
                    help="逗号分隔的任务名，或 all")
    ap.add_argument("--force", action="store_true",
                    help="忽略'当日已完成'标记，强制重跑")
    ap.add_argument("--no-quotes", action="store_true",
                    help="跳过全市场行情刷新（调试用）")
    ap.add_argument("--list", action="store_true", help="列出所有任务")
    args = ap.parse_args()

    if args.list:
        for k in DEFAULT_ORDER:
            print(f"  {k:<20} {TASKS[k][1]}")
        return 0

    load_env()
    # ★ 关键：所有数据读取走数据包，杜绝回源拉 K 线（WAF 根治）
    os.environ["DATA_SOURCE"] = "pack"

    names = DEFAULT_ORDER if args.tasks.strip() == "all" \
        else [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        print(f"::error::未知任务: {unknown}；可选: {list(TASKS)}")
        return 2

    print(f"=== 日批合并 ===")
    print(f"  任务: {', '.join(names)}")
    print(f"  数据源: pack（backend-pack.db）  强制重跑: {args.force}")

    try:
        if not args.no_quotes:
            print("\n[0] 准备共享行情...")
            ensure_quotes()
    except Exception as e:
        print(f"  ⚠️ 行情刷新失败（后续任务可能受影响）: {e}")
        _notify(f"> 全市场行情刷新失败：{e}")

    ok_list, fail_list = [], []
    for i, name in enumerate(names, 1):
        fn, desc = TASKS[name]
        print(f"\n[{i}/{len(names)}] {desc}...", flush=True)
        t0 = time.time()
        try:
            summary = fn()
            cost = time.time() - t0
            ok_list.append((desc, cost))
            print(f"  ✅ {desc} 完成（{cost:.0f}s）: {summary}", flush=True)
        except Exception as e:
            cost = time.time() - t0
            fail_list.append((desc, str(e)[:200]))
            print(f"  ❌ {desc} 失败（{cost:.0f}s）: {e}", flush=True)
            traceback.print_exc()

    print(f"\n=== 汇总: 成功 {len(ok_list)} / 失败 {len(fail_list)} ===")
    for desc, cost in ok_list:
        print(f"  ✅ {desc} ({cost:.0f}s)")
    if fail_list:
        lines = "\n".join(f"> {d}：{e}" for d, e in fail_list)
        print("\n失败明细:\n" + lines)
        print("::error::日批存在失败任务，见上方明细")
        _notify(f"日批 {datetime.now():%m-%d %H:%M} 完成，失败 {len(fail_list)} 项：\n{lines}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
