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

# ★ 2026-09-19：Windows 控制台默认 **GBK**，而本脚本的汇总行用了 ✅/❌ 等非 GBK 字符
#   ⇒ print 时抛 UnicodeEncodeError；更糟的是 **except 分支里的 ❌ 也会抛**，
#   导致「连失败明细都打不出来，整个进程直接退出」（2026-09-19 本地实测踩到）。
#   这里把 stdout/stderr 的解码错误策略改成 replace —— 保留 GBK 编码（中文照常），
#   仅把该编码不支持的字符退化成 `?`，**进程绝不会因编码崩**。
#   Actions / Linux（UTF-8）下 reconfigure 不改变任何行为 ⇒ 线上日志不受影响。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except Exception:
        pass

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
    """回测价格库增量回填（战法回测 / 快照 T+N 收益 / 模拟盘结算的数据底座）。

    ★ 2026-09-16：quota 150 → 不限量（backfill_lagging）。
      根因：回填的 Render 常驻循环被 RENDER_READ_ONLY=1 关闭（scheduler._heavy），
      只剩本日批一天一次供给；而 `_collect_rank_codes(7)` 近 7 天上榜股去重
      ≈157 只 > 配额 150 → 插队机制占满配额仍不够，普通股几乎补不到。
      实测代价：09-09 快照的 T+5（目标日 09-16）覆盖仅 25/50。
      `backfill_daily(quota=None)` 会**跳过已同步到基准的股票** → 无积压时零开销，
      仅积压时一次性追平（全池 ~700 只 ≈ 12 分钟；日批 74→86min，远低于 Actions
      180min 上限）。幂等，可重复跑。
    """
    from backfill_history import backfill_lagging
    return f"回填完成: {backfill_lagging()}"


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


def task_calendar():
    """财经日历刷新（金十）——原 Render 每日 07:00 循环（calendar_loop）被只读模式关闭，
    缓存文件停在 09-04；而它是 LLM 复盘的「事件排期」输入（非农/CPI/FOMC 的时间与前值/预期），
    也是前端日历页的数据源。

    失败不抛异常（返回 0 条 + 保留旧缓存，日历是低频静态数据，过期一天远好于没有）；
    连续失败由 health.record("jin10_calendar") 的既有机制告警。
    """
    from app.flash.calendar import refresh
    n = refresh()
    return f"财经日历: {n} 条" + ("" if n else "（⚠️ 本次未取到，保留旧缓存）")


def task_market_regime():
    """市场状态判定（评分动态权重 / 战法准入 / trade_gate / 主力乘数的事实来源）。

    ★ 2026-09-11：该判定原为 Render 每日 15:40 循环（regime_cache_loop），只读模式
      下被关，而日批任务清单漏了它 → market_regime_history 停在 09-08，日志里
      「应用市场状态权重 2026-09-08 neutral」，评分权重/战法准入/闸门全是 3 天前的。
      依赖 backfill 先写入沪深300当日数据，故紧排其后（strategy_scan/score_snapshot 之前）。
    ★ 2026-09-13：refresh_regime_cache 已改同日幂等（当日已有判定行即跳过），
      与 Render 15:40 常驻循环双跑不再互相污染"昨日"（两日确认不再退化单日）。
    """
    from app.backtest.market_regime import refresh_regime_cache
    cache = refresh_regime_cache()
    if not cache or not cache.get("state"):
        raise RuntimeError("市场状态判定失败（沪深300当日数据未就绪？）")
    return (f"市场状态: {cache.get('date')} {cache['state']} "
            f"权重={cache.get('weights')}")


def task_mainforce_state():
    """主力行为状态日批（mainforce_state 表，原 Render 17:30 循环）。

    ★ 2026-09-09 迁移：Render 只读模式（RENDER_READ_ONLY=1）下该循环在
      _heavy() 里被关闭 → 表停更（09-04 起落后）。日批环境数据齐全：
      bars 走 pack（零回源）+ mainflow_history（本日批 mainflow 任务产出）
      + float_shares（market_snapshot 快照，故本任务必须排在它之后）。
    消费方：后端排行榜主力标注 / trade_gate / confluence 吸筹+2出货-2 /
    每日日报的主力汇总与 L3 交叉扫描。
    """
    from app.mainforce.state import refresh_all
    regime = None
    try:
        from app.backtest.market_regime import (
            get_regime_cache, restore_regime_cache_from_db)
        regime = (get_regime_cache() or {}).get("state")
        if not regime:
            # ★ 审查 P1-5：独立进程缓存为空，先从历史表恢复（task_market_regime
            #   在本批更早处已写入当日缓存，此处只是兜底）
            restore_regime_cache_from_db()
            regime = (get_regime_cache() or {}).get("state")
    except Exception:
        pass  # regime 缺省 None = 乘数闸门不生效，与 scheduler 行为一致
    return f"主力行为状态: {refresh_all(None, regime)}"


def _batch_trading_day():
    """本轮日批**所属交易日**（date）—— 星期判定/交易日判定都必须以它为准。

    ★ 2026-09-19：跨午夜补跑的坑。日批语义是"处理**最近一个已完成交易日**"，但它可能
      因延迟（pack 卡住被 timeout 砍后重跑、人工补跑）**跨过午夜**才跑到后面的任务
      ⇒ 那时 `datetime.now().weekday()` 已变成次日：
        · `task_zz_finance`（周一任务）若在**周二 00:30** 跑到 ⇒ `weekday()!=0` ⇒ 跳过，
          而它**没有自愈机制**（对比 `task_weekly_report` 有 `_weekly_due()`）
          ⇒ **本周财报扩展彻底不跑**（L3 财报断层扫描的数据底座失联）。
      口径与 `rules.latest_completed_trading_day()` 同源（15:00 分界 + 跳周末/节假日）。
    """
    import app.flash.rules as _rules
    return datetime.strptime(_rules.latest_completed_trading_day(), "%Y-%m-%d").date()


def task_zz_finance():
    """zzshare 财报扩展周同步（原 Render 周一 04:30 循环，只读模式已停摆）。

    仅周一执行（其余交易日直接跳过，返回即不计失败）——季度数据周更保活，
    是 L3 财报断层扫描（contradictions/l3_scanner）的数据底座。
    """
    # ★ 2026-09-19：判「**本轮交易日**」的星期，不用 `now()` —— 跨午夜补跑时
    #   `now().weekday()` 已是次日 ⇒ 周任务被永久跳过且无自愈（详见 `_batch_trading_day()`）。
    d = _batch_trading_day()
    if d.weekday() != 0:
        return f"财报扩展: 周任务，本轮交易日 {d:%Y-%m-%d} 为周{d.weekday() + 1} 跳过"
    from app.zzshare_finance import sync_latest_finance
    from app.database import db
    rows = db.fetch(
        "SELECT DISTINCT code FROM stock_finance "
        "WHERE length(code) = 6 AND substr(code, 1, 1) IN ('0', '3', '6') "
        "ORDER BY code")
    stats = sync_latest_finance([r["code"] for r in (rows or [])])
    try:
        # L3 连续失血跟踪的数据底座：近 4 期全市场 OCF 历史
        from app.mainforce.l3_history import sync_ocf_history
        stats["ocf_hist"] = sync_ocf_history(4)
    except Exception as oe:
        print(f"[zzshare] OCF 历史同步失败（不影响主表）: {oe}")
    return f"财报扩展: {stats}"


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
    # ★ 2026-09-19：**「零落库」不再算成功**。
    #   事故：2026-09-15~09-18 连续 **4 个交易日**，10 个战法全部被准入拦下
    #   （防御市，根因见 272ed31 的 scan 层豁免）⇒ `scanned=0 / failed=0` ⇒
    #   这里照样返回成功、`strategy_results` **一行未写**，而 `/strategies` 页
    #   一直显示 9-14 的旧快照（用户肉眼发现的，不是告警发现的）。
    #   ⇒ 只要**零落库**就显式失败（日批汇总 → 企微告警），把静默断档变成可见故障。
    if stats.get("scanned", 0) == 0:
        if stats.get("failed", 0) == 0:
            raise RuntimeError(
                f"战法扫描零落库（全部被准入跳过？）: {stats} —— "
                f"strategy_results 未更新，战法页会停留在旧快照")
        raise RuntimeError(f"战法扫描全部失败: {stats}")
    # ★ 即时对账（原实现只看返回值、不看库）：确认**当日结果真的写进去了**
    import app.flash.rules as _rules
    from app.database import db
    row = db.fetch_one("SELECT MAX(scan_date) AS d FROM strategy_results")
    latest = (row or {}).get("d")
    want = _rules.latest_completed_trading_day()
    if str(latest) != str(want):
        raise RuntimeError(
            f"战法扫描落库核对失败：strategy_results 最新 scan_date={latest}，"
            f"应为 {want} —— 扫描结果没有写进库")
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
        # 主力行为标签（2026-09-09）：供吸筹/出货胜率验证（BucketStats）
        "mainforce_signal": ((r.get("mainforce") or {}).get("signal")),
    } for i, r in enumerate(data)]
    n = record_daily_ranking(stocks, False, True)
    return f"评分快照: {n} 条（Top {len(data)}）"


def task_rank_live():
    """全量精算榜单（ranking_live）：后端 /batch/top 直接读，与前端本地榜同口径。

    ★ 2026-09-11（002452 案例）：后端原榜是「简化分筛候选池 → 只精算候选」，
      而简化分只看 动量+换手+PE —— 基本面/质量型股票（002452 简化 61.6/第352名、
      精算 69.8）永远进不了候选池，导致"本地榜有、后端榜没有"。
      这里在 Actions（4 核 + pack 预计算指标，零腾讯请求）做全量精算并落库：
        · 收盘后后端直接读这张表（零计算、口径一致）
        · 盘中后端把表内代码并入候选池（昨日上榜股今日必被重算，盲区消失）
    依赖：market_snapshot（行情/Top池）+ mainforce_state（资金面第 5 因子 flow5），
    故排在 mainforce_state 之后。
    """
    from app.scoring.live_ranking import compute_and_store
    # ★ 2026-09-15：300 → 1000。nb 市组合分排序需要更大池（覆盖基本面强但总分中游的票）
    return compute_and_store(limit=1000)


def task_shadow_rank():
    """影子榜（shadow_rank_daily）：生产 / 公告后衰减 两套 top50 落库（3 variant）。

    ★ 2026-09-17 新增并二版：灰度验证「财报公告后短窗口成长/质量因子反向 → 该衰减」。
      落 base（生产）、zero（公告后≤25天成长/质量×0，初版对照）、grad（梯度：0-5天
      剔除、6-20天权重×0.5，主版本）三套，两周后用 compare_shadow_rank.py 对比收益。
      本任务**不改生产主排序**。依赖 rank_live 先落库（读其精算五维分），故排在其后。
    """
    from shadow_decay_ranking import run
    return run(top=50, apply=True)


def task_mainline():
    """行业主线/共振分析（industry_mainline，原 Render 16:05 循环）。

    ★ 2026-09-09 迁移：Render 只读模式关闭了 mainline_loop（_heavy）→ 09-09 起断档。
    依赖 score_snapshot（Top50 快照）先落库，故排在其后。
    产出：主线榜（Top50 扎堆行业）+ 风格切换信号 + 企微日报。
    """
    from app.mainline import compute_mainline
    r = compute_mainline()
    if not r.get("ok"):
        raise RuntimeError(f"主线分析失败: {r.get('error')}")
    note = ""
    try:
        from app.mainline import push_mainline_report
        p = push_mainline_report()
        note = f"（企微 {p.get('mainlines', 0)} 条 / 切换 {p.get('switches', 0)}）"
    except Exception as e:
        note = f"（企微推送失败: {e}）"
    return f"主线分析: {r['industries']} 行业 / 未知 {r['unknown_stocks']} 只 {note}"


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


def task_news_snapshot():
    """消息分每日快照（news_history 表，消息面因子回测的数据积累）。

    ★ 2026-09-11 迁入：原为 Render 19:20 循环（scheduler.news_history_loop），
      RENDER_READ_ONLY=1 后落在 _heavy() 里被关闭 → 表停在 09-08（落后 3 天，
      前端「消息分快照」卡片直接暴露）。日批侧依赖当日 ranking_history 的
      Top50（score_snapshot 产出）+ user_portfolio，故必须排在 score_snapshot 之后。
      零数据库重读：池子只有「持仓 + Top50」约 50-80 只，逐只拉东财搜索接口。
    """
    from app.flash.scheduler import take_news_snapshot_once
    n = take_news_snapshot_once()
    if not n:
        raise RuntimeError("消息分快照写入 0 条（ranking_history 当日为空？）")
    return f"消息分快照: {n} 只"


def _explicitly_requested(task: str) -> bool:
    """本任务是否被 --tasks 点名（点名 = 人工补跑，绕过时间窗守卫）。

    ★ 2026-09-11：日批只在工作日跑（周末无触发源），若周五因故没跑成，
      下周一日批必须能自动补——见 _weekly_due()；而人工 `--tasks weekly_report`
      任何时候都该照跑。
    """
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--tasks" and i + 1 < len(args):
            return task in [t.strip() for t in args[i + 1].split(",")]
        if a.startswith("--tasks="):
            return task in [t.strip() for t in a.split("=", 1)[1].split(",")]
    return False


def _week_last_trading_day(any_day):
    """`any_day` 所在 ISO 周的**最后一个交易日**（通常是周五；节前周可能是周四等）。

    ★ 2026-09-19：不再用 `weekday() == 4` 判"周五" —— 长假前最后一个交易日往往不是
      周五（如 2026 国庆前是 9-30 周三 / 中秋周是 9-24 周四），只看周五会漏掉那一周。
    """
    from datetime import timedelta
    import app.flash.rules as _rules
    d = any_day + timedelta(days=6 - any_day.weekday())     # 该周周日
    for _ in range(7):
        if _rules.is_trading_day(datetime(d.year, d.month, d.day)):
            return d
        d -= timedelta(days=1)
    return None                                             # 整周无交易日（长假）


def _weekly_due() -> bool:
    """**上一个已结束的周**缺少「覆盖到其最后交易日」的报告 → 需要补（漏跑自愈）。

    ★ 2026-09-19 修正（原实现有两处语义错位）：
      原判据 =「最近周报**生成日**所在的 ISO 周 != **本周**」，但：
        ① **生成日 ≠ 覆盖的数据周** —— 9-14(周一) 生成的那份覆盖的是**上一周**
           （数据截至 9-11），它是"第 37 周的周报、生成在第 38 周" ⇒ 旧判据把它当成
           "本周已有" ⇒ **第 38 周（9-15~9-18）永久缺报**（9-19 才发现）；
        ② 拿**本周**比 ⇒ 周中恒为"本周还没生成" ⇒ 让**周一生成**成为常态 ⇒ 又回到 ①。
      新判据：看**上一个完整周** —— 最近周报的日期是否 ≥ 该周的最后交易日。
      （既补得上"周五漏跑、下一工作日补"，也不会让周中误生成。）
    """
    try:
        from app.backtest import report_store
        rows = report_store.list_reports(limit=1)
    except Exception:
        return True
    if not rows:
        return True
    try:
        last = datetime.strptime((rows[0].get("mtime") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    from datetime import timedelta
    prev_last = _week_last_trading_day(_batch_trading_day() - timedelta(days=7))
    if prev_last is None:
        return False
    return last < prev_last


def task_weekly_report():
    """周度回测报告（报告落库 + 企微推送摘要）。

    ★ 2026-09-11 迁入：原为 Render 周五 16:00/周六的循环（scheduler.backtest_report_loop），
      只读模式后同样被关闭 → 前端「回测中心」最新报告停在 09-05。
      另外 Render 免费实例文件系统是临时的（每次部署/重启清空），报告只写文件
      必丢 → save_report 现在同时落库 backtest_reports，前端从库里读。
      报告生成只读数据包（DATA_SOURCE=pack），不产生 Supabase 流量。

    触发条件（任一成立即生成，否则跳过）：
      1) 周五（原设计的常规窗口）
      2) 本周尚无报告（漏跑自愈——周末没有日批，周五失败只能靠下一个工作日补）
      3) 被 --tasks 明确点名（人工补跑）
    """
    import datetime as _dt
    bj = _dt.datetime.now(_dt.timezone(_dt.timedelta(hours=8)))
    # ★ 2026-09-19：常规窗口从「今天是周五」改为「**本轮交易日是本周期最后一个交易日**」：
    #   · 修跨午夜补跑：9-18(周五) 23:18 启动的日批跑到这里已跨到 9-19(周六) ⇒
    #     旧判据 `weekday() != 4` 成立，叠加 `_weekly_due()` 的语义错位 ⇒ **整周漏报**
    #     （实测第 38 周缺失）。改用「本轮交易日」后，跨午夜时它仍是 9-18 ⇒ 正常生成。
    #   · 兼容节前：长假前最后一个交易日往往不是周五（中秋周 9-24 周四、国庆前 9-30 周三）。
    d = _batch_trading_day()
    if (d != _week_last_trading_day(d) and not _weekly_due()
            and not _explicitly_requested("weekly_report")):
        return (f"周度回测报告: 本周已生成，本轮交易日 {d:%Y-%m-%d} 为周{d.weekday() + 1} 跳过"
                f"（本周期最后交易日例行 / 上周缺报自愈 / --tasks weekly_report 可强制）")
    from app.backtest.run import generate_report, save_report, generate_summary
    content = generate_report("all")
    path = save_report(content, tag="weekly")
    summary = ""
    try:
        summary = generate_summary()
    except Exception as e:
        print(f"  摘要生成失败（不影响报告）: {e}")
    if summary:
        try:
            from app.flash.wechat import push_markdown_batched
            push_markdown_batched("📊 周度回测报告", summary)
        except Exception as e:
            print(f"  企微推送失败（不影响报告）: {e}")
    return f"周度回测报告: {os.path.basename(path)}（{len(content)} 字，已落库+推送）"


def _monthly_due(tag: str, batch_day) -> bool:
    """**本轮交易日所在月份**尚无该 `tag` 的报告 → 需要跑（漏跑自愈）。

    ★ 2026-09-20：与 `_weekly_due()` 同思路，但**必须读库** —— 日批跑在 Actions，
      工作区每次全新（checkout），文件级判据恒为"没有报告" ⇒ 会天天跑。
      `report_store.list_reports` 返回库中报告（含 created_at），前端「回测中心」同源。
    """
    try:
        from app.backtest import report_store
        rows = report_store.list_reports(limit=120)
    except Exception as e:
        print(f"  [月度判据] 报告清单读取失败（保守按『需要跑』处理）: {e}")
        return True
    mine = [r for r in (rows or []) if (r.get("tag") or "") == tag]
    if not mine:
        return True
    try:
        last = datetime.strptime((mine[0].get("mtime") or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return True
    return (last.year, last.month) != (batch_day.year, batch_day.month)


def _push_ic_summary(res) -> str:
    """把体检摘要推企微，返回**状态文案**（开关关掉/推送失败都如实写出，不静默）。

    ★ 分级（2026-09-20）：**有告警 ⇒ `force=True`** —— 倒U背书失效、因子方向翻转
      属**关键通知**（`push_markdown_batched` 的 force 语义即"不受业务推送开关限制"），
      必须送达；**无告警 ⇒ 普通推送**，尊重 `WECHAT_BUSINESS_ALERTS`（默认关，
      否则月报会打扰）。且普通推送被开关拦掉时返回值**明确写出原因** ——
      不能出现"以为挂了推送、其实静默没发"（今天 env_check 的同类教训）。
    """
    from subfactor_ic_backtest import summary_markdown
    alerts = res.get("alerts") or []
    try:
        from app.flash import wechat
        if not wechat.WECHAT_WEBHOOK:
            return "未推送（未配置 WECHAT_WEBHOOK）"
        title = "⚠️ 因子体检告警" if alerts else "📊 因子体检（月度）"
        if alerts:
            wechat.push_markdown_batched(title, summary_markdown(res), force=True)
            return "已推送企微（force：关键告警）"
        if not wechat.BUSINESS_ALERTS_ENABLED:
            return "未推送（业务推送开关已关 WECHAT_BUSINESS_ALERTS≠1）"
        wechat.push_markdown_batched(title, summary_markdown(res))
        return "已推送企微"
    except Exception as e:
        print(f"  [警告] 因子体检企微推送失败: {e}")
        return f"推送失败（不影响体检）: {str(e)[:80]}"


def task_subfactor_ic():
    """子指标因子体检（**每月一次**）：逐子项 IC + 滚动复核上轮结论 + 落库。

    ★ 2026-09-20 落地（见 `评分系统体检_子指标IC与优化建议_20260920.md` §建议5）：
      09-20 首次体检发现「技术面 8/8 子项 5-10 日负 IC」（A 股短窗截面反转），
      并把「周报买入信号 5 日胜率 41.5%」的长期困惑**定位到子项级** ⇒
      结论必须**周期化复核**，否则会随样本期漂移而无人察觉（这正是白名单
      `_half_life_alert` 当初要解决的问题：全历史 replay 被旧战绩撑住）。
      机制与半衰期告警同构：定期算 + 结构化告警 + 落库留痕。

    触发条件（任一成立，否则跳过）：
      1) 本轮交易日所属**月份**尚无体检报告（常规窗口 = 每月首个交易日）
      2) `--tasks subfactor_ic` 明确点名（人工补跑 / 强制）
    数据：全程本地或数据包（`research_cache` + pack，零 Supabase 回源）。
    ★ 2026-09-21 默认 step 5→1（截面 10→~50 个，密度敏感性检验结论，见体检报告
      §6.2）⇒ 耗时约 10~15 分钟（原 3 分钟）—— 月度任务，可接受。
    """
    d = _batch_trading_day()
    if not _monthly_due("subfactor_ic", d) and not _explicitly_requested("subfactor_ic"):
        return (f"因子体检: 本轮交易日 {d:%Y-%m-%d} 所在月份已体检，跳过"
                f"（每月例行 / --tasks subfactor_ic 可强制）")
    from subfactor_ic_backtest import run as run_ic
    res = run_ic()
    if res.get("skipped"):
        return f"因子体检: 跳过（{res.get('reason')}）"
    alerts = res.get("alerts") or []
    for a in alerts:
        print(f"  [警告] 因子体检 {a}")
    push_note = _push_ic_summary(res)
    rev = res.get("review") or {}
    return (f"因子体检: {res['sections']} 截面 {res.get('section_first')}~"
            f"{res.get('section_last')}｜{len(res.get('verdicts') or {})} 子项"
            f"（负 IC {rev.get('neg')} 项/{rev.get('verdict')}；"
            f"告警 {len(alerts)} 条；{res.get('report_name')}；{push_note}）")


def _force_requested() -> bool:
    """命令行是否带 --force（语义=忽略"当日已完成"标记、强制重跑）。"""
    return "--force" in sys.argv[1:]


def task_trader_brief():
    """交易员决策简报（盘后）+ 企微推送。

    ★ 2026-09-12 迁入日批：此前简报只有「打开前端 /report 页才生成」
      （`GET /api/system/trader-brief` 按需触发），计划里承诺的「盘后 19:35 推送」
      一直缺 → 这是 PLAN_TRADER_WORKFLOW Phase 1 的收尾项。
      日批由后端包完成接棒、19:xx 开跑，正好在盘后窗口，故固定生成 postmarket。
      依赖：market_regime / contradictions / ranking_history(Top10) / mainline /
            strategy_results / user_portfolio，全部是前面任务写好的 → 排在最后。

    三条纪律：
      · **非交易日直接跳过**：generate_trader_brief 以"当天日期"为键，周末跑会写出一条
        没有数据支撑的错日期简报，推了只会刷屏
      · **幂等**：命中当日已有简报就不再调 LLM（要重生成加 `--force`）
      · **推送失败不影响主流程**：正文已落库，前端 /report 照常可见
    """
    from app.flash.rules import beijing_now, is_trading_day
    bj = beijing_now()
    if not is_trading_day(bj):
        return f"交易员简报: {bj:%Y-%m-%d} 非交易日跳过"
    from app.trader_brief import generate_trader_brief
    res = generate_trader_brief(phase="postmarket", force=_force_requested())
    md = res.get("markdown") or ""
    if not md:
        raise RuntimeError("交易员简报生成异常（正文为空）")
    push_note = "未推送"
    try:
        from app.flash import wechat
        if not wechat.WECHAT_WEBHOOK:
            push_note = "未推送（未配置 WECHAT_WEBHOOK）"
        elif not wechat.BUSINESS_ALERTS_ENABLED:
            push_note = "未推送（业务推送开关已关）"
        else:
            wechat.push_markdown_batched("🧭 交易员决策简报（盘后）", md)
            push_note = "已推送企微"
    except Exception as e:
        push_note = f"推送失败（不影响简报）: {str(e)[:80]}"
        print(f"  企微推送失败（不影响简报）: {e}")
    flag = f"降级({res['degraded']})" if res.get("degraded") else "正常"
    cached = "，命中当日已有简报未重烧 LLM" if res.get("cached") else ""
    return (f"交易员简报: {res.get('date')} postmarket {len(md)} 字"
            f"（LLM {flag}{cached}；{push_note}）")


# 顺序 = 依赖顺序：行情 → 数据底座 → 扫描 → 汇总
TASKS = {
    "backfill": (task_backfill, "回测价格回填"),
    # ★ 2026-09-11 迁入：原 Render 15:40 循环被只读模式关闭且日批漏配 → regime 停在 09-08。
    #   依赖 backfill 的沪深300当日数据，且被 strategy_scan/score_snapshot 消费 → 紧排其后。
    "market_regime": (task_market_regime, "市场状态判定（评分权重/准入/闸门来源）"),
    "mainflow": (task_mainflow, "主力资金流回填"),
    "market_snapshot": (task_market_snapshot, "全市场行情快照"),
    # ★ 2026-09-12 迁入：原 Render 每日 07:00 循环被只读模式关闭 → 日历停在 09-04。
    #   放在 LLM 类任务（矛盾报告/日报）之前，保证复盘 prompt 里的事件排期是新的。
    "calendar": (task_calendar, "财经日历刷新（LLM 事件排期来源）"),
    # ★ 2026-09-09 迁入：Render 只读模式停掉了原 17:30 循环 → 表停在 09-04。
    #   依赖 mainflow（资金流）与 market_snapshot（流通股本快照），故置其后。
    "mainforce_state": (task_mainforce_state, "主力行为状态（排行榜标签/日报依赖）"),
    "sector_snapshot": (task_sector_snapshot, "板块快照"),
    "strategy_scan": (task_strategy_scan, "战法全量扫描"),
    "contradiction_scan": (task_contradiction_scan, "矛盾扫描"),
    "contradiction_report": (task_contradiction_report, "矛盾报告(LLM)"),
    "score_snapshot": (task_score_snapshot, "评分快照"),
    # ★ 2026-09-09 迁入：依赖 score_snapshot 的 Top50，排其后
    "mainline": (task_mainline, "行业主线/共振分析"),
    # ★ 2026-09-11 迁入：原 Render 19:20 循环被只读模式关闭 → news_history 停在 09-08。
    #   依赖 score_snapshot 写入的当日 ranking_history Top50。
    "news_snapshot": (task_news_snapshot, "消息分每日快照（消息面回测底座）"),
    # ★ 2026-09-11 新增：全量精算榜（ranking_live）——后端 /batch/top 直接读。
    #   依赖 mainforce_state（资金面第 5 因子 flow5）与全市场行情缓存。
    "rank_live": (task_rank_live, "全量精算榜单（后端榜直接读）"),
    # ★ 2026-09-17：旁路衰减榜——依赖 rank_live 落库，读其精算五维分做公告后衰减
    "shadow_rank": (task_shadow_rank, "旁路衰减榜（生产 vs 衰减 top30 落库）"),
    "lhb": (task_lhb, "龙虎榜同步"),
    # ★ 2026-09-09 迁入：原 Render 周一 04:30 循环被只读模式关闭；任务内部
    #   判定仅周一执行，其余交易日秒过
    "zz_finance": (task_zz_finance, "财报扩展周同步（仅周一）"),
    # ★ 2026-09-11 迁入：原 Render 周五循环被只读模式关闭 → 回测中心停在 09-05。
    #   任务内部判定仅周五执行，其余交易日秒过。
    "weekly_report": (task_weekly_report, "周度回测报告（仅周五，落库+推送）"),
    # ★ 2026-09-20 新增：子指标因子体检周期化（体检报告 §建议5）。
    #   任务内部判定「本月是否已体检」，其余交易日秒过；排在周期报告之后。
    "subfactor_ic": (task_subfactor_ic, "子指标因子体检（仅每月首个交易日）"),
    "daily_report": (task_daily_report, "每日日报"),
    # ★ 2026-09-12 新增：交易员决策简报（盘后）+ 企微推送 —— 此前只有前端按需生成，
    #   是 TRADER_WORKFLOW Phase 1 的收尾项。依赖前面全部任务产出，排在最后。
    "trader_brief": (task_trader_brief, "交易员决策简报（盘后，幂等+企微推送）"),
}
DEFAULT_ORDER = ["backfill", "market_regime", "mainflow", "market_snapshot",
                 "calendar", "mainforce_state",
                 "sector_snapshot", "strategy_scan", "contradiction_scan",
                 "contradiction_report", "score_snapshot", "mainline",
                 "news_snapshot", "rank_live", "shadow_rank",
                 "lhb", "zz_finance", "weekly_report", "subfactor_ic", "daily_report",
                 "trader_brief"]


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


def ensure_pack_fresh(max_wait_min: float):
    """★ 校验数据包日期，避免日批跑在昨天的包上。

    daily-batch 排在 backend-pack 之后 30 分钟，但后端包要拉 2081 只×749 根
    + 算指标 + 发布 Pages。若它延迟，日批就会拿着昨天的包跑战法扫描 →
    信号日期错位（且静默，不易发现）。这里等包更新到「**此刻本应可用**」为止。

    ★ 2026-09-19 改造：判据从「北京**自然日**」换成
      `pack_source._latest_available_pack_day()`（**交易日历感知 + 22:00 时间分界**）。

      原实现 `want = 北京今天的 YYYYMMDD` 只在「当天 19:00 包发布之后」成立：
      **0:00~19:00 之间跑（凌晨补跑 / 白天补跑）永远 `want=今天` vs `包=昨天`**
      ⇒ 白等 `--max-wait`（默认 30 分钟）、每 3 分钟 `redownload()` 重下一次 127MB 包，
        最后打 `::error::` 却**因返回值未被调用方使用而继续跑**（2026-09-19 凌晨实测踩到）。
      更糟的是它按**自然日**算：遇到**周末/长假**（中秋 9-25~9-27、国庆 10-01~10-07）
      同样永远等不到——**休市日根本不会生成新包**。

      新判据与读侧 `pack_source._is_stale()` / `_pack_outdated()` **完全同源**，
      三处判定终于一致：
        · 交易日 22:00 之后 → 当天（包已发布）
        · 其余情况        → 上一个交易日（跳过周末 + `rules.HOLIDAYS`，带自然日兜底）
      ★ 显式传北京时区：`_latest_available_pack_day()` 默认用 `datetime.now()`，
        在 UTC 环境（Actions / Render）会整体差一天。
      ★ 用 `got >= want` 而非 `==`：包比预期更新时也算通过（原 `==` 会误杀）。
    """
    import app.pack_source as ps
    from app.flash.rules import beijing_now

    want = ps._latest_available_pack_day(beijing_now())
    deadline = time.time() + max(0, max_wait_min) * 60
    while True:
        try:
            got = ps._parse_pack_date(ps._pack_date())
        except Exception as e:
            print(f"  读取数据包日期失败: {e}")
            got = None
        if got is not None and got >= want:
            print(f"  数据包日期校验通过: {got}（此刻应可用 {want}）")
            return True
        if time.time() >= deadline:
            print(f"::error::数据包仍为 {got}（应可用 {want}）——后端包可能失败或延迟，"
                  f"日批将基于旧数据运行，请检查 backend-pack workflow")
            return False
        print(f"  数据包日期 {got} < 应可用 {want}，3 分钟后强制重下重试"
              f"（Pages 站点部署/CDN 有 1~2 分钟空窗，本地包 mtime 新鲜但内容是旧的）…")
        time.sleep(180)
        # ★ 必须强制重下（2026-09-10 事故）：只置 _ready_checked 不够——_ensure_ready
        #   用 _db_fresh()（文件 mtime）判新鲜，刚下载过的包永远算"新鲜"→ 重试 10 次
        #   一次都不会再下载，30 分钟白等，最终拿着昨天的包跑完全程。
        try:
            ps.redownload()
        except Exception as e:
            print(f"  强制重下数据包失败: {e}")


def main():
    ap = argparse.ArgumentParser(description="日批合并（唯一计算入口）")
    ap.add_argument("--tasks", default="all",
                    help="逗号分隔的任务名，或 all")
    ap.add_argument("--force", action="store_true",
                    help="忽略'当日已完成'标记，强制重跑")
    ap.add_argument("--no-quotes", action="store_true",
                    help="跳过全市场行情刷新（调试用）")
    ap.add_argument("--no-pack-check", action="store_true",
                    help="跳过数据包日期校验（调试用）")
    ap.add_argument("--max-wait", type=float, default=30,
                    help="等待数据包更新到今天的最长分钟数（默认 30）")
    ap.add_argument("--list", action="store_true", help="列出所有任务")
    args = ap.parse_args()

    if args.list:
        for k in DEFAULT_ORDER:
            print(f"  {k:<20} {TASKS[k][1]}")
        return 0

    load_env()
    # ★ 关键：所有数据读取走数据包，杜绝回源拉 K 线（WAF 根治）
    os.environ["DATA_SOURCE"] = "pack"

    # ★ 2026-09-14：关键开关生效值自检（审查文档 §5.5）——Actions 的
    #   daily-batch.yml 此前漏配业务开关（用代码默认值）就是漂移发生地，
    #   这里把生效值打进日批日志，与期望不符立即可见。
    try:
        from app.env_check import log_switch_report
        log_switch_report()
    except Exception as e:
        print(f"  [env_check] 自检失败（不影响日批）: {e}")

    names = DEFAULT_ORDER if args.tasks.strip() == "all" \
        else [t.strip() for t in args.tasks.split(",") if t.strip()]
    unknown = [n for n in names if n not in TASKS]
    if unknown:
        print(f"::error::未知任务: {unknown}；可选: {list(TASKS)}")
        return 2

    print(f"=== 日批合并 ===")
    print(f"  任务: {', '.join(names)}")
    print(f"  数据源: pack（backend-pack.db）  强制重跑: {args.force}")

    if not args.no_pack_check:
        print("\n[0] 校验数据包新鲜度...")
        ensure_pack_fresh(args.max_wait)

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

    # ★ 2026-09-13（时序审计）：LLM 影子线程是 daemon——日批最后一个任务
    #   （trader_brief）的影子调用会在进程退出时被直接杀死，盘后简报的新模型
    #   对比记录必丢。退出前有界等待在飞影子线程结束。
    try:
        from app.flash.llm import wait_shadow_threads
        wait_shadow_threads(timeout=180)
    except Exception as e:
        print(f"  ⚠️ 影子线程等待失败（忽略）: {e}")

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
