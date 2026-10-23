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
    r = backfill_lagging()
    # ★ 2026-10-20：显式回报滞后/STALE 数量（原先把整个 dict 塞进日志，
    #   747 只滞后时被淹没；而旧逻辑还会把"源限流"伪装成"已同步"）。
    miss = r.get("stock_missing") or []
    txt = (f"回填完成: {r.get('stock_rows')} 行，基准 {r.get('benchmark')}，"
           f"滞后 {len(miss)} 只，STALE {r.get('stale') or 0} 只")
    if miss:
        txt += f"（前5: {', '.join(miss[:5])}）"
    return txt


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


def task_regime_alert():
    """市况转档提醒（2026-09-22 新增）——状态切换时推企微，把"等待"自动化。

    ★ 用户痛点：买入闸门的 C 条件就是"市况允许"（defensive 禁买），此前
      `market_regime_history` 只落库、无任何推送 ⇒ 用户只能每天自己盯指数。
    设计见 `app/coach/regime_alert.py` 模块头：
      · 紧排 `task_market_regime` 之后（当日判定已落库才比对）
      · 同日去重（`regime_alert_log.date` 主键）⇒ 补跑安全
      · 转档 force 推送（穿透 WECHAT_BUSINESS_ALERTS，属关键通知）
      · 文案联动观察池（ready≥2 / 三绿计数）
    无切换时秒过（返回"无切换"，不计失败）。
    """
    from app.coach.regime_alert import run_alert
    return run_alert()


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


def task_gate_snapshot():
    """买入闸门就绪度落库（2026-09-22 新增）—— 攒样本以验证 gate 的预测力。

    ★ 缺口：`trade_gate`（9-17 上线）**只有实时视图、无历史** ⇒ 无法回答
      「ready 3/3 vs 2/3 vs 1/3 的后续收益是否真有差别」。它已承担决策重量
      （观察池/用户动线）⇒ 必须攒样本验证；且 3/3 在防御市常为 0 ⇒ 样本天然慢，
      有**时钟属性**（晚一天少一天），故早启动。
    ★ 时序：**必须在 `task_mainforce_state` 之后**（gate 依赖其 chip/flow5 快照）。
    幂等：`gate_snapshot_history.date` 主键；force 可覆盖重跑。
    口径：复用 `app.routers.scoring.gate_watch`（唯一事实源，防口径漂移）。
    """
    d = _batch_trading_day()
    from app.mainforce.gate_history import snapshot
    return snapshot(d.isoformat(), overwrite=_force_requested())


def task_entry_log():
    """买入入口（闸门 `ready==3`）**当日留痕**（2026-09-29，P1/P2 根因修复）。

    ★ 为什么：P2 归因实测发现 `coach_alerts` 里**买入/市场类建议无 `code`**
      ⇒「入场理由」的个股收益**永远无法评估**；而唯一买入入口
      `trader_brief._merged_buy_list` 此前**只实时展示、不留痕** ⇒ 一旦 regime 转好、
      `ready==3` 开始出现，**"买没买"将永久丢失**（时钟属性 ⇒ 越早留痕越好）。
      落库即自动进入：教练页「建议历史」执行回写（**零新增前端**）+ P2 归因 + T+5 回填。
    ★ 时序：**必须在 `gate_snapshot` 之后**（`_merged_buy_list` 读最新闸门快照）。
    ★ 纪律：**只落库、不推送**（《专业度缺口》P1：达标后才谈是否推送）；空清单不写占位。
    """
    from app.coach.audit import log_entry_candidates
    return log_entry_candidates(day=_batch_trading_day().isoformat())


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


def task_zz_daily():
    """zzshare 每日数据积累（涨停生态/情绪快照，2026-09-21 新增）。

    ★ 定位：**数据积累型**任务 —— 情绪趋势/连板分布/涨停题材归因这类数据
      「晚一天接入就永远少一天历史」，且每日落库本身就是 zzshare 选型原则
      要求的「30 天影子验证」载体。分析开发（情绪×战法交互回测 / 基本面
      PIT 体检）等 10 月体检收口后再排（见 zzshare 功能盘点 §7 路线图）。
    幂等：`zz_daily_snapshots` 表 date 主键，补跑安全；
    fail-open：单接口失败只记 `_error`，不阻断日批。
    ⚠️ 依赖 Actions secrets 配置 ZZSHARE_TOKEN（Render 已配；缺 token 会匿名
       调用 → 0 行/慢，快照里 `_error` 或空数据即此因）。
    """
    d = _batch_trading_day()
    from app.zzshare_daily import run_daily
    # force（日批 --force / Actions 的 force input）⇒ 覆盖重跑：
    #   唯一能纠正"部分接口失败时写入的不完整行"的通道（幂等默认会跳过）。
    return run_daily(d.isoformat(), overwrite=_force_requested())


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


def task_risk_calendar():
    """风险事件日历同步（限售解禁）—— C1 风险闸门的数据底座。

    ★ 与行情无关（解禁日期提前数月公开）⇒ 逻辑上**不受休市影响**；
      但当前日批在休市日整体不跑（见文件头关于"休市日该分 A/B 两类"的讨论）。
    ⚠️ `ratio`（解禁市值/流通市值）需要 `tencent._cache` 的 `float_cap`
      ⇒ 必须排在**行情刷新之后**（TASKS 的顺序已是"行情 → 数据底座 → 扫描 → 汇总"）。
    """
    from app.risk_events import sync_lift_calendar
    return f"风险日历: {sync_lift_calendar()}"


def task_sector_snapshot():
    """板块每日快照（板块动量序列，非交易日自动跳过）。

    ★ 2026-09-25：顺带落「行业成交额占比」（`sector_amount_daily`，供后续做"占比变化"）。
      ⚠️ 两者**数据源完全独立**（板块快照走东财接口；成交占比走腾讯内存行情 + 落库映射）
      ⇒ 必须**分别 try**：东财被封时板块快照会 skip，而成交占比照常落库
      （这正是它比板块快照更稳的原因，别让一个失败拖垮另一个）。
    ★★ 2026-09-30（P0 断档修复）：**并入 zzshare 板块快照**（`plate_daily_zz`）。
      事故：该表原先**只挂在 Render 的 `sector_snapshot_loop`（15:10-16:40）**上，
      循环一停摆就**静默断档 5 个交易日**（09-25 起；东财版照常写 ⇒ 无人发现），
      而前端「板块分化」用的正是它 ⇒ 用户看到的是 **09-24 的普跌画面**。
      ⇒ 处置与 `mainforce_state`(09-09) / `weekly_report`(09-11) / `zz_finance`(09-19)
        完全相同：**迁入日批**（日批是唯一有实际保障的执行通道）。
      ⚠️ 用 `_batch_trading_day()` **显式传日期**，不用 `latest_completed_trading_day()`：
         跨午夜补跑时后者会算成"上一交易日"，而日批语义是"**本轮**交易日"。
      ⚠️ zzshare 侧 **fail-open**（只记结果不抛）：源异常不能拖垮日批；
         真断档由同日批 `data_gap` 任务（**自愈 + 告警**）兜底。
    """
    from app.sector_industry import take_snapshot
    day = _batch_trading_day().isoformat()
    snap = take_snapshot()
    try:
        from app.sector_industry import save_amount_share
        amt = save_amount_share()
    except Exception as e:
        amt = f"失败（不影响板块快照）: {str(e)[:80]}"
        print(f"[sector] amount share save failed: {e}")
    try:
        from app.sector_zz import take_snapshot as take_snapshot_zz
        zz = take_snapshot_zz(day)
    except Exception as e:
        zz = f"失败: {str(e)[:80]}"
        print(f"[sector] zzshare snapshot failed: {e}")            # ASCII（铁律⑥）
    return f"板块快照: {snap} ｜ 成交占比: {amt} ｜ zzshare({day}): {zz}"


def task_data_gap():
    """数据底座断档自检 + 板块快照自愈（2026-09-30 P0）。

    ★ 为什么必须进日批：本次断档的根因是"**Render 循环停摆 ⇒ 数据静默缺日**"，
      而日批是**唯一有实际保障的执行通道**（同 `sector_snapshot` / `mainforce_state` /
      `zz_finance` 的处置）。更要紧的是：**检查本身也得挂在确定会跑的地方** ——
      挂在循环上的检查会跟着循环一起静默。
    ★ 顺序：排在**所有数据写入任务之后**（本处紧邻 `subfactor_ic`、在 LLM 类日报之前）
      —— 这样它看到的是本轮全部写入的最终状态，又不被日报/简报的慢与失败拖累。
    ★ 行为（`app.data_gaps`）：
      ① 先 `backfill_plate()` 自愈最近 5 个交易日板块缺口（幂等；无缺口时**零请求**）
      ② 再 `check_gaps()`：关键表"最新日期 vs 本轮应处理交易日"落后 >2 交易日 ⇒ 断档
      ③ 告警走 `push_markdown_batched(force=True)`（关键通知）+ 按日去重
         （`data_gap_alert_log`）⇒ 日批补跑不会重复推。
    ★ fail-open：本任务异常不让日批失败（返回值即摘要）；告警失败只打日志。
    """
    from app.data_gaps import run_gap_check
    return run_gap_check()


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
    from app.contradictions.store import save_contradictions
    # ★★ 2026-09-26：日期改用**本轮交易日**（原为 `store._today()` = 运行日）
    #   ⇒ 原先休市日/跨午夜会写出"键与内容不符"的记录（实测 09-25 休市仍留下 `date=09-25`
    #     的 2 条，而同一天 `ranking_history` 按交易日**不写** ⇒ 两表日期键对不上，
    #     连带通知判据失效）。口径详见 `_batch_trading_day()`。
    d = _batch_trading_day().isoformat()
    items = scan_all(date=d)
    saved = save_contradictions(d, items)
    # ★★ 2026-10-18（审计发现的真缺陷）：**验证闭环原本只挂在 Render 的
    #   `scheduler.contradiction_scan_loop`**，而该 loop 被 `_heavy(...)` 包裹
    #   ⇒ 生产 `RENDER_READ_ONLY=1` 下整条不跑；日批又只 scan+save
    #   ⇒ 实测 **09-16 之后 0 条被验证**，待验池堆到 75 条，而 `max_age_days=10`
    #     会让它们**永久失去资格**（`contradictions` 91 条里只有 8 条验证过）。
    #   ⇒ 验证必须挂在**真正每天运行的地方**（日批），与扫描同源同日。
    vinfo = ""
    try:
        from app.contradictions.validator import validate_yesterday
        vs = validate_yesterday()
        vinfo = (f"；验证 {vs.get('validated', 0)} 条"
                 f"（对 {vs.get('correct', 0)} / 错 {vs.get('wrong', 0)}"
                 f" / 平 {vs.get('flat', 0)}）"
                 f"，跳过：非方向性 {vs.get('skipped_non_directional', 0)}"
                 f" / 超龄作废 {vs.get('skipped_expired', 0)}")
    except Exception as e:
        # 验证失败不影响扫描（扫描是主产出），但必须显式留痕（原先连这层都没有）
        vinfo = f"；⚠ 验证失败（不影响扫描）: {e}"
        print(f"[daily-batch] 矛盾验证失败: {e}")
    return f"矛盾扫描: {len(items)} 条，保存 {saved}{vinfo}"


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
        # 主力行为标签（2026-09-09）：供吸筹/出货胜率验证（BucketStats）。
        # ★★ 2026-10-11 修（用户："score_snapshot 时序"）：原写的是
        #   `"mainforce_signal": ((r.get("mainforce") or {}).get("signal"))` —— 而落库函数
        #   `ranking_history.record_daily_ranking` 读的键是 **`mainforce`**
        #   （`mf = stock.get("mainforce")`，兼容 dict 与扁平串），**`mainforce_signal`
        #   这个键无人读** ⇒ 日批（9-11 起成为**线上唯一路径**）写出的 `mainforce_signal`
        #   列**恒为 NULL** ⇒ BucketStats 的吸筹/出货分桶长期无数据。
        #   （2026-09-13 修过"无标签覆盖"问题，但只补了 Render 循环 —— 而日批在该循环**之后**
        #    跑 ⇒ 线上仍是 NULL 版本；本次按同一口径传 `mainforce`（dict 原样，由落库函数
        #    自己取 signal），与 `scheduler.score_snapshot_loop` 完全一致。）
        "mainforce": r.get("mainforce"),
    } for i, r in enumerate(data)]
    # ★ 2026-10-11：显式传「本轮交易日」（同 `task_sector_snapshot` 的口径）
    #   —— 否则跨午夜补跑时落库函数会用自然日当键：周六 00:30 跑到 → 守卫判"非交易日"
    #   ⇒ 周五快照**直接丢失**；跑到周一 00:30 → 写成"键=周一、内容=周五"的错位行。
    n = record_daily_ranking(stocks, False, True,
                             date=_batch_trading_day().isoformat())
    return f"评分快照: {n} 条（Top {len(data)}）"


def task_sector_strength():
    """行业强度（板块联动筛选的数据底座；判据冻结于 `PLAN_SECTOR_LINKAGE.md` v1.0）。

    ★ 必须在 `rank_live` 之前（过滤版榜单要用它）、在 `backfill` 之后（价源）。
    ★ 只算一次、落 `sector_strength_daily`；榜单/接口只读表，不重算。
    """
    from app.sector_strength import compute, mode
    if mode() == "off":
        return "板块强度: mode=off，跳过"
    r = compute(day=_batch_trading_day().isoformat())
    if not r.get("ok"):
        raise RuntimeError(f"板块强度计算失败: {r.get('reason')}")
    return (f"板块强度: {r.get('rated_n')} 行业 / 强势 {r.get('strong_n')}"
            f"（{r.get('reason')}，上涨占比 {r.get('market_up_ratio')}%）")


def task_sector_linkage_snapshot():
    """过滤版 Top50 落**平行表** `ranking_history_sector`（原 `ranking_history` 一字不动）。

    ★ 依赖 `rank_live`（全量精算榜，约 1000 行 ⇒ 才能"排除后递补满员"）。
    ★ shadow/on 才写；强度表不可用 ⇒ 跳过（fail-open，不留空榜污染回查）。
    """
    from app.sector_strength import snapshot_from_live
    return snapshot_from_live(day=_batch_trading_day().isoformat())


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


def task_backtest_preheat():
    """回测缓存预热（2026-10-18，egress 治理）。

    ★ 为什么迁进日批：该预热原挂在 Render 的 `_heavy` 组，而线上 `RENDER_READ_ONLY=1`
      ⇒ **从未真正跑过** ⇒ `backtest_cache` 长期陈旧（实测 09-22/09-23/10-11）⇒
      每次访问回测页都退化成"重算"（单策略要拉几百只票日线，pg_stat_statements 里
      该批读累计 748 次 / 1890 万行 ≈ 1.4GB）。
    ★ 放在这里 = 每天一次、可观测、可失败告警；配合 `get_strategy` 的刷新冷却
      （30 分钟 + 6 小时陈旧门槛）⇒ 用户访问不再触发重算。
    """
    from app.routers.backtest import preheat_all
    r = preheat_all()
    return "回测预热: " + ", ".join(f"{k}={v}" for k, v in r.items())


def task_pattern_launch_scan():
    """「缩量调整→放量启动」形态每日扫描（2026-10-15，用户需求）。

    ★ 定位纪律：**候选清单 + 观察追踪**，不构成买卖建议 —— 研究三关已证该形态
      「正边际但不赚钱」（T+10 净超额 +1.8pp，但胜率 43~45%、中位收益为负、T+5 全负）。
    ★ 依赖：backtest_prices 已回填（task_backfill 排最前）⇒ 本任务排其后即可。
    ★ 产出：`pattern_scan_daily`（当日扫描）+ `pattern_watch`（观察中票的后续变化回填）。
    ★ 参数可重设（pattern_config，PUT /api/pattern/config）⇒ 未来重设回测条件免改代码。
    """
    from app import pattern_launch as pl
    pl.ensure_tables()
    result = pl.scan_latest()
    if not result.get("available"):
        return f"缩量启动扫描: 数据未就绪（{result.get('note') or 'backtest_prices 空'}）"
    n = pl.persist_scan(result)
    w = pl.update_watch_returns()
    return (f"缩量启动扫描: 命中 {result['n']} 只（落库 {n}）｜ 观察回填 {w} 只 "
            f"｜ 数据日 {result['date']} ｜ 参数 {result.get('params_hash')}")


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


def task_intraday_path():
    """指数日内分时路径归档（P2-1，PLAN_RESERVE_SIGNALS §6.5）。

    ★ **时钟属性**：腾讯分时**只有当日** ⇒ 晚一天接入就永远少一天历史
      （见 `app/intraday_path.py` 文件头）。这是"午盘前跌>1% → 午后拉升"这类
      **日内命题的唯一数据底座** —— 日线看不到"早盘杀跌 → 午后收回"的 V 型，
      这正是 P1-1 结论里最缺的拼图。
    ★ 幂等：UNIQUE(date, code) + upsert（重跑只覆盖同日）；
      **窗口保护**：非收盘窗口/周末 `_auto_day()` 直接返回 None ⇒ 拒绝自动落库
      （防把"上一交易日序列"写到今天名下，`market_amount` 吃过这个亏）。
    ★ 不抛异常：5 个标的各自记账，单个失败不拖累其余（失败清单体现在返回串）。
    """
    from app.intraday_path import save_day
    r = save_day()
    if r.get("error"):
        return f"分时归档: 跳过（{r['error']}）"
    tail = f" 失败 {r['fail']}" if r.get("fail") else ""
    return f"分时归档: {r['date']} 成功 {r['ok']}/{r['codes']}{tail}"


def task_earnings_pressure():
    """盈利承压监测（P2-2，PLAN_RESERVE_SIGNALS §6.6）—— 周度出摘要 + 企微推送。

    ★ 定位：**监测清单，不是信号**。只输出「毛利率同比下滑 + 业绩恶化」的清单与行业分布，
      不改评分/不排序/不进闸门（升级为信号须走预登记 + 预测力检验）。
      清单本身由 **`GET /api/macro/earnings-pressure`** 对外提供（随时可查，无需落库）。

    ★ 触发条件（**复用 `task_weekly_report` 同一套判据，不自己发明第二套**）：
      ① **本轮交易日 = 本周期最后一个交易日**（`_week_last_trading_day`）——
         兼容"长假前最后一个交易日不是周五"，且用 `_batch_trading_day()` 而非
         `datetime.now()` ⇒ **跨午夜补跑不误判**（这两条都是 2026-09-19 修过的坑）；
      ② 被 `--tasks earnings_pressure` 点名（人工补跑）。
      ⚠️ 刻意**不做"漏跑自愈"**：本模块数据是**季度**财报（报告期 4 个月才变一次），
         漏一周无实际损失；而自愈要读 report_store —— 见下一条，得不偿失。

    ★★ **为什么不写 report_store（重要，别改回去）**：`_weekly_due()` 的自愈判据是
      `report_store.list_reports(limit=1)`，即「**最新一份报告（不看 tag）**的日期」。
      本任务若落一份新报告，就会把"最新报告日期"顶成当天 ⇒ 让 `weekly_report` 的
      **漏跑自愈失效**（那正是 2026-09-19 修过的"整周漏报"）。⇒ 本任务**只推企微、不落库**。

    ★ 成本：`stock_finance` 窄列 ~1.9 万行（≈1MB）+ 行业映射 ~5900 行，进程内 6h 缓存 ⇒
      一周一次真读（且本任务本来就只在周度窗口跑）。
    """
    d = _batch_trading_day()
    if (d != _week_last_trading_day(d)
            and not _explicitly_requested("earnings_pressure")):
        return (f"盈利承压: 本轮交易日 {d:%Y-%m-%d} 非本周期最后交易日，跳过"
                f"（周度例行 / --tasks earnings_pressure 可强制）")
    from app.earnings_pressure import markdown, scan
    data = scan(limit=40, refresh=True)
    md = markdown(limit=40)
    lvl = (data.get("macro") or {}).get("level")
    head = (f"盈利承压: 报告期 {data.get('as_of')} 承压 {data.get('n_pressed')} 只"
            f"（扫描 {data.get('n_scanned')}，宏观 {lvl}）")
    if lvl != "high":
        return head + "，宏观未达高压 ⇒ 只出日志、不推企微"
    try:
        from app.flash import wechat
        if not wechat.WECHAT_WEBHOOK:
            return head + "（无 webhook，未推）"
        wechat.push_markdown_batched("⚠️ 盈利承压监测（高油+高利率）", md, force=False)
        return head + "，已推企微"
    except Exception as e:
        return head + f"，推送失败: {str(e)[:60]}"


def task_data_hygiene():
    """日期键卫生兜底闸（2026-10-11；用户："数据卫生"）。

    ★ 背景（"日期键两派"）：若干写入路径用**运行日**当键，而语义应是**交易日** ⇒ 日批/循环在
      周末·假日跑就写出「键 = 假日、正文 = 上一交易日」的**错位重复行**。2026-10-08 首次发现，
      10-11 系统盘点：**7 张分析表共 36 个假日键**（其中 `sector_daily` 单键就 996 行，
      `macro_daily_history` 48 行、`trader_briefs`/`contradiction_reports` 各 8~10 个键），
      且**根因仍在产**（10-10/10-11 周末还在写）。
    ★ 为什么用"兜底闸"而不是把 20+ 处守卫逐个改：`flash/scheduler.py` 里
      **20 多处用 `now.weekday() < 5`**（只看星期、不认 A 股节假日 —— 中秋 09-25 是周五却是
      休市日就是反例），逐个改风险大且必漏；本项目对这类"写入侧容易被绕过"的问题，
      既定范式是**兜底闸自愈**（同 `data_gap` 自愈板块缺口、`retention` 兜底清理）。
      已单独修掉最明确的 3 处（矛盾扫描/矛盾报告/午间雷达，见各自 docstring）。
    ★ 安全边界（`scripts/cleanup_holiday_keys.py` 内置，**别在这里放宽**）：
      · **只删「上一交易日键已存在」的重复行**（内容必有另一份）；
      · 孤儿行（可能是那天唯一记录）**只报告、不删**；
      · 运营计数表（`llm_usage_daily` / `llm_usage_by_provider` / `push_log`）按**自然日**
        统计**属设计** ⇒ 脚本显式跳过（删了反而错）。
    ★ 时序：纯清理 + 只读判定，排 `data_gap` 之后（看得到本轮全部写入的结果）。
    """
    import subprocess
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "cleanup_holiday_keys.py")
    try:
        r = subprocess.run([sys.executable, script, "--apply"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=600)
    except Exception as e:
        return f"日期键卫生: 执行失败 {str(e)[:80]}"
    out = r.stdout or ""
    tail = [l for l in out.splitlines() if l.startswith("汇总")]
    return "日期键卫生: " + (tail[0] if tail else f"（无汇总输出 rc={r.returncode}）")


def task_contradiction_report():
    """矛盾 LLM 报告（依赖上面的扫描结果）。"""
    from app.contradictions.report import run_report
    return f"矛盾报告: {run_report()}"


def task_daily_report():
    """A 股大盘日报（依赖上面的扫描结果，必须最后跑）。"""
    from app.daily_report import run_daily_report
    # ★★ 2026-09-26：传**本轮交易日**（原来不传 ⇒ 函数内部 `_today()` 用运行日）
    #   ⇒ 修掉"跨午夜/休市日写出次日键的日报"（实测 09-25 休市那次写成 09-26）。
    #   口径详见 `_batch_trading_day()`。
    res = run_daily_report(date=_batch_trading_day().isoformat())
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


def task_verify_monthly():
    """预登记验证脚本的**月度自动复核**（2026-09-30）。

    ★ 为什么必须做（本轮查出来的真缺口）：三个**预登记**验证脚本 ——
      `scripts/gate_ready_backtest.py`（闸门 ready==3 联合期望值）、
      `scripts/sector_momentum_edge_check.py`（板块动量延续性）、
      `scripts/event_live_review.py`（E2 实盘复核）—— 全项目**没有任何调用方**
      ⇒ 只能人工想起来跑。而它们的结论**只取决于样本**（"样本是时钟，开发加速不了"）
      ⇒ **样本够了却没人跑 = 白等**。接进日批即到点自动出结论 + 自动推送。
    ★ 幂等：由 `app.edge_verify` 把结果落 `report_store`（tag=`verify_monthly`，
      名字带月份）⇒ 复用 `_monthly_due()` 判"本月是否已复核"（且跨月漏跑会自愈）。
    ★ 推送分级：有结论（含运行异常）⇒ `force=True` 推企微；全 INSUFFICIENT ⇒
      **只落库不推**（月月推"还没样本"是噪音，同 `data_gap` 的口径）。
    ★ 时序：纯只读分析（三个脚本都不改生产表）⇒ 位置不敏感；与 `subfactor_ic`
      同放"周期性复核"区。
    """
    d = _batch_trading_day()
    if not _monthly_due("verify_monthly", d) and not _explicitly_requested("verify_monthly"):
        return (f"预登记复核: 本轮交易日 {d:%Y-%m-%d} 所在月份已复核，跳过"
                f"（每月例行 / --tasks verify_monthly 可强制）")
    from app.edge_verify import run_monthly
    return run_monthly()


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

    ★★ 2026-09-25（用户批准修 P0）—— 改用「**本轮交易日**」而非「此刻」：
      原写法 `if not is_trading_day(beijing_now()): return 跳过` 与已修的
      `task_zz_finance` 同型 ⇒ **日批跨午夜补跑（周六 00:30 才跑到这个任务）时一律跳过，
      且无自愈 ⇒ 周五盘后简报永远补不出来**，只能人工补。
      修法两处**必须同时改**（缺一即错）：
        ① 判据换成 `_batch_trading_day()`（= 最近一个已完成交易日），并把它**传给**
           `generate_trader_brief(date=...)` —— 否则简报会以"今天（周六）"为键落库，
           写出一条没有数据支撑的错日期记录（下面原注释担心的正是这个，
           但解法不是"跳过"，而是"**把正确日期传进去**"）。
        ② **命中已有简报时不再推送** —— `push_markdown_batched` **没有去重**
           （只往 signal_bus 记录，不做拦截）⇒ 原实现在重跑时会把同一条**再推一遍**。
           改①之后**休市日也会跑**（cron-job.org 是周一~周五按星期触发，不看节假日，
           而 `_batch_trading_day()` 会返回上一交易日）⇒ 若不加这条，
           **每个休市日都会把上一交易日的简报重推一次** ⇒ 刷屏。
           ★ 教训：**一处放行必须同步一处收口**。

    三条纪律（更新）：
      · **按本轮交易日判断**：由 `_batch_trading_day()` 决定处理哪个交易日（见上）
      · **幂等且不重复推送**：命中已有简报 ⇒ 不烧 LLM、**也不重推**（重生成/重推加 `--force`）
      · **推送失败不影响主流程**：正文已落库，前端 /report 照常可见
    """
    d = _batch_trading_day()
    from app.trader_brief import generate_trader_brief, mark_pushed
    force = _force_requested()
    res = generate_trader_brief(phase="postmarket", force=force, date=d.isoformat())

    # ★★ 2026-10-23：数据未就绪必须**显式失败**，不能静默"命中"。
    #   实测事故：用户在日批跑到 score_snapshot 之前打开页面 ⇒ 按需生成出
    #   "日期=今日、候选=昨日"的早产简报 ⇒ 日批 cached（1s）⇒ 这天既没有官方简报、也没推送。
    if res.get("premature"):
        raise RuntimeError(f"简报数据未就绪：{res.get('reason')}")

    md = res.get("markdown") or ""
    if not md:
        raise RuntimeError("交易员简报生成异常（正文为空）")

    cached = bool(res.get("cached"))
    head = "交易员简报:"
    if cached:
        # 命中已有：已推送过 ⇒ 收口；从未推送（页面按需生成）⇒ **补推**
        try:
            from app.database import db
            row = db.fetch_one("SELECT pushed_at FROM trader_briefs "
                               "WHERE date=%s AND phase=%s",
                               (res.get("date"), "postmarket"))
            if row and row.get("pushed_at"):
                return (f"交易员简报: {res.get('date')} postmarket 命中已有且已推送"
                        f"（{row['pushed_at']}；重生成请加 --force）")
        except Exception:
            pass
        head = "交易员简报: 补推"

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
            mark_pushed(res.get("date") or d.isoformat(), "postmarket")
    except Exception as e:
        push_note = f"推送失败（不影响简报）: {str(e)[:80]}"
        print(f"  企微推送失败（不影响简报）: {e}")
    flag = f"降级({res['degraded']})" if res.get("degraded") else "正常"
    return (f"{head} {res.get('date')} postmarket {len(md)} 字"
            f"（LLM {flag}；{push_note}）")


def task_retention():
    """数据库保留期清理（Supabase 500MB 上限治理）—— 2026-09-24 新增。

    ★ 必须排在**所有写入任务之后**：清理不该与写入竞争，更不该删掉刚写进去的数据。
    ★ 只清理「已逐一核实读取点只读最新」的表（清单与依据见 `app/db_retention.py` 文件头）
      ⇒ **零功能影响**；默认保留 **14 天**，而当前数据仅 7~11 天 ⇒ **首轮不删任何东西**。
      天数用环境变量 `RETENTION_DAYS` 覆盖。
    ⚠️ 别为了"看起来省"把天数设大：保留期是「**先涨到上限再平**」——`mainforce_state`
      现在 11 天/7.5MB，设 90 天反而会先涨到 ~61MB（涨 8 倍）。
    ⚠️ 安全阀：无论怎么设，至少保留最近 3 个不同日期（防日批连挂时把表删空）。
    """
    from app import db_retention
    rows = db_retention.cleanup()
    return "保留期清理: " + db_retention.summarize(rows)


def task_etf_share_watch():
    """宽基 ETF 份额异动（★ 2026-10-19，国家队行为的 T+1 证据）。

    ★ 纯观察器：结果只喂 `policy_bottom_watch` 第①条（连续 2 日 ⇒ **自动打勾**，
      人工可覆盖），不改分数/不进闸门/不碰模拟盘。
    ★ 数据源：**上交所官方** 份额披露（可查历史、一次 924 只），**不碰东财**。
      深市 159915 暂不纳入（深交所该报表固定 10 条/页 = 76 页/日，成本过高）。
    ★ 失败**不抛**：返回 WARN，清单侧显示"份额异动：检查不可用"（缺失 ≠ 没有异动）。
    ★ 必须排在 `policy_bottom_watch` **之前**（清单要读当天结果）。
    """
    from app import etf_share_watch as esw
    day = _batch_trading_day().isoformat()
    # ★ 2026-10-23：先补最近 5 个交易日的缺口（SSE 是 T+1，漏一天就永久缺行）
    try:
        bf = esw.backfill(5, end=day)
    except Exception as e:
        bf = {"filled": [], "error": str(e)[:60]}
    r = esw.scan_and_store(day, push=True)
    if not r.get("ok"):
        return (f"ETF 份额异动: WARN {str(r.get('error'))[:70]}"
                f"（补历史 {len(bf.get('filled') or [])} 天）"
                "⇒ 清单显示『检查不可用』（不等于没有异动）")
    sus = r.get("suspicious") or []
    extra = f"，疑似申购 {len(sus)} 只" if sus else ""
    if r.get("confirmed"):
        extra += "，★ 连续 2 日确认（第①条自动打勾）"
    if r.get("pushed"):
        extra += "，已推企微"
    bf_n = len(bf.get("filled") or [])
    if bf_n:
        extra += f"，补历史 {bf_n} 天"
    return f"ETF 份额异动: 窗口 {r.get('prev_date')}→{r.get('date')}{extra}"


def task_policy_announcement_watch():
    """国家队增持公告 **自动提示**（★ 2026-10-19，巨潮法定披露渠道）。

    ★ 只提示、不打勾：结果挂在市场底清单第①条下（标题+链接），
      打勾权仍在人（见 `app/policy_announcements.py` 模块头的红线）。
    ★ 失败**不抛**（网络/接口波动不能拖垮整批）：返回带 ⚠ 的文案，
      清单侧会显示"自动检查不可用"，**绝不等于"没有公告"**。
    ★ 排在 `policy_bottom_watch` **之前** —— 否则清单读到的是昨天的提示。
    ★ searchkey 是分词匹配，模块内部已做"粗召回 + 本地连续字串过滤"；别改成直接信 searchkey。
    """
    from app import policy_announcements as pa
    day = _batch_trading_day().isoformat()
    r = pa.scan_and_store(day, push=True)
    if not r.get("ok"):
        return (f"增持公告提示: WARN 抓取失败（{str(r.get('error'))[:70]}）"
                "⇒ 清单显示『自动检查不可用』（不等于没有公告）")
    news = r.get("new_hits") or []
    extra = f"，新增 {len(news)} 条已推企微" if news else ""
    if r.get("hit_count"):
        extra += f"；命中 {r['hit_count']} 条（见清单第①条）"
    return f"增持公告提示: 扫描 {r.get('scanned')} 条{extra}"


def task_crowding_watch():
    """拥挤赛道观察器（2026-10-23，用户 10-22 复盘）。**纯观察器，不进决策链**。

    ★ 读数据包算：行业 30 日收益中位数、120 日高位股占比、距 120 日高点回撤；
      等级 = 退潮（已回撤≥25%且近5日仍跌）/ 高拥挤 / 升温 / 正常。
    ★ 失败不抛（观察器缺一天不影响主链）。
    """
    try:
        from app import crowding_watch as cw
        r = cw.compute(day=_batch_trading_day().isoformat())
        if not r.get("ok"):
            return f"拥挤赛道: WARN {str(r.get('reason'))[:70]}"
        return (f"拥挤赛道: {r.get('n_industries')} 行业 / "
                f"高拥挤+升温 {r.get('high_n')} / 退潮 {r.get('ebb_n')}")
    except Exception as e:
        return f"拥挤赛道: WARN {str(e)[:70]}"


def task_scenario_watch():
    """磨底 2.0 推演证伪记分卡（2026-10-23）。**纯观察器**。

    ★ 组合 policy_bottom_watch / key_level_watch / etf_share_watch 的既有结果，
      把用户推演的验证信号（成交额/涨跌比/10Y/油价/银行补跌/创业板周线/上证位置/
      汇金ETF）逐条判多空，输出情景倾向（基准/熊市加深/提前修复）+ 日历倒计时。
    """
    try:
        from app import scenario_watch as sw
        r = sw.run_daily(day=_batch_trading_day().isoformat())
        if not r.get("ok"):
            return f"推演证伪: WARN {str(r.get('error'))[:70]}"
        return (f"推演证伪: {r.get('scenario')} / 偏多 {r.get('n_up')} / "
                f"偏空 {r.get('n_down')}")
    except Exception as e:
        return f"推演证伪: WARN {str(e)[:70]}"


def task_ai_basket_watch():
    """AI 硬件主线篮子回撤观察器（2026-10-23）。**纯观察器，不进决策链**。

    ★ 6 只龙头等权，读数据包；输出篮子与个股的 5/30 日收益、距 120 日高点回撤。
    """
    try:
        from app import ai_basket_watch as ab
        r = ab.compute(day=_batch_trading_day().isoformat())
        if not r.get("ok"):
            return f"AI硬件篮子: WARN {str(r.get('reason'))[:70]}"
        b = r.get("basket") or {}
        return (f"AI硬件篮子: {r.get('n')} 只 / 回撤 {b.get('dd120')}% / "
                f"5日 {b.get('ret5')}% / 30日 {b.get('ret30')}%")
    except Exception as e:
        return f"AI硬件篮子: WARN {str(e)[:70]}"


def task_key_level_watch():
    """指数关键位观察器（2026-10-23）。**纯观察器，不进决策链**。

    ★ 前低（60日）/ 整数位 / 年内低点；首次跌破才记 broke。
    ★ 腾讯指数 K 线 6 次请求；失败不抛（缺一天不影响主链）。
    """
    try:
        from app import key_level_watch as kl
        r = kl.compute(day=_batch_trading_day().isoformat())
        if not r.get("ok"):
            return f"关键位: WARN {str(r.get('reason'))[:70]}"
        return (f"关键位: {len(r.get('rows') or [])} 指数 / "
                f"失守 {r.get('broke_n')} / 年内新低 {r.get('year_low_n')}")
    except Exception as e:
        return f"关键位: WARN {str(e)[:70]}"


def task_policy_bottom_watch():
    """市场底确认清单（★ 2026-10-18，**纯观察器，非信号**）。

    ★ 依赖：`intraday_path`（② 成交额的二级源，同批更早跑）与 `market_emotion_daily`
      （④ 涨跌比，由 Render 盘后 loop 写）。任一缺数据不会失败，只把该条标 `insufficient`
      （缺失 ≠ 不满足）。
    ★ 推送只在"达成数首次跨过 4"当日一次；同日重跑不重复推（`run_daily` 内幂等）。
    ★ 不碰评分/排序/闸门/模拟盘准入 —— 见 `app/policy_bottom_watch.py` 模块头的预登记声明。
    """
    from app import policy_bottom_watch as pbw
    day = _batch_trading_day().isoformat()
    r = pbw.run_daily(day)
    if not r.get("ok"):
        raise RuntimeError(f"市场底清单计算失败: {r.get('error')}")
    extra = ""
    if r.get("crossed_4"):
        try:
            from app.flash import wechat
            md = pbw.render_markdown(pbw.load(day))
            wechat.push_markdown_batched("市场底确认清单达成 4/5", md,
                                         force=True, category="alert")
            extra = "，已推送企微"
        except Exception as e:
            print(f"[daily-batch] 清单达成推送失败: {e}")     # ASCII（铁律⑥）
            extra = f"，推送失败({str(e)[:40]})"
    elif r.get("already_crossed"):
        extra = "，今日已推送过（幂等）"
    return f"市场底清单: {r.get('n_checked')}/5{extra}"


# 顺序 = 依赖顺序：行情 → 数据底座 → 扫描 → 汇总
TASKS = {
    "backfill": (task_backfill, "回测价格回填"),
    # ★ 2026-09-11 迁入：原 Render 15:40 循环被只读模式关闭且日批漏配 → regime 停在 09-08。
    #   依赖 backfill 的沪深300当日数据，且被 strategy_scan/score_snapshot 消费 → 紧排其后。
    "market_regime": (task_market_regime, "市场状态判定（评分权重/准入/闸门来源）"),
    # ★ 2026-09-22 新增：市况转档提醒（把"等待"自动化）——紧排 regime 判定之后，
    #   保证当日状态已落库再比对；同日去重（regime_alert_log），切换日 force 推企微。
    "regime_alert": (task_regime_alert, "市况转档提醒（切换日推企微）"),
    "mainflow": (task_mainflow, "主力资金流回填"),
    "market_snapshot": (task_market_snapshot, "全市场行情快照"),
    # ★ 2026-09-12 迁入：原 Render 每日 07:00 循环被只读模式关闭 → 日历停在 09-04。
    #   放在 LLM 类任务（矛盾报告/日报）之前，保证复盘 prompt 里的事件排期是新的。
    "calendar": (task_calendar, "财经日历刷新（LLM 事件排期来源）"),
    # ★ 2026-09-25（用户需求 C1）：风险事件日历（限售解禁）—— 决策卡负面清单的数据底座。
    #   紧跟 calendar（同为"日历"类）；且必须在行情刷新之后（ratio 需 float_cap）。
    "risk_calendar": (task_risk_calendar, "风险事件日历（限售解禁，C1 闸门底座）"),
    # ★ 2026-09-09 迁入：Render 只读模式停掉了原 17:30 循环 → 表停在 09-04。
    #   依赖 mainflow（资金流）与 market_snapshot（流通股本快照），故置其后。
    "mainforce_state": (task_mainforce_state, "主力行为状态（排行榜标签/日报依赖）"),
    # ★ 2026-09-22 新增：闸门就绪度落库（**必须在 mainforce_state 之后**——
    #   gate 的 A/B 条件读它的 chip/flow5 快照）。时钟属性：晚一天少一天样本。
    "gate_snapshot": (task_gate_snapshot, "买入闸门就绪度落库（攒样本验证预测力）"),
    # ★ 2026-09-29（P1/P2 根因修复）：买入入口留痕 —— 必须在 gate_snapshot **之后**
    #   （`_merged_buy_list` 读最新快照）；只落库不推送；空清单不写行。
    "entry_log": (task_entry_log, "买入入口留痕（ready==3，只落库不推送）"),
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
    # ★ 2026-10-19 新增：板块联动筛选的数据底座（判据冻结于 PLAN_SECTOR_LINKAGE.md v1.0）。
    #   必须在 rank_live 之前（过滤版榜单要用它）、在 backfill 之后（价源）。
    "sector_strength": (task_sector_strength, "行业强度（板块联动筛选，预登记 v1.0）"),
    # ★ 2026-09-11 新增：全量精算榜（ranking_live）——后端 /batch/top 直接读。
    #   依赖 mainforce_state（资金面第 5 因子 flow5）与全市场行情缓存。
    "rank_live": (task_rank_live, "全量精算榜单（后端榜直接读）"),
    # ★ 2026-10-19 新增：过滤版 Top50 落平行表（ranking_history_sector）——
    #   必须紧跟 rank_live（才能从 ~1000 行里"排除后递补满员"）；原表一字不动。
    "sector_linkage": (task_sector_linkage_snapshot, "板块联动过滤榜（平行表，原榜不动）"),
    # ★ 2026-09-17：旁路衰减榜——依赖 rank_live 落库，读其精算五维分做公告后衰减
    "shadow_rank": (task_shadow_rank, "旁路衰减榜（生产 vs 衰减 top30 落库）"),
    # ★ 2026-10-15：缩量启动形态每日扫描（描述性观察，非信号）。依赖 backfill 已回填。
    "pattern_launch_scan": (task_pattern_launch_scan, "缩量启动形态扫描 + 观察回填"),
    # ★ 2026-10-18：回测缓存预热（原 Render _heavy 组，只读模式从未跑过 ⇒ 缓存长期陈旧）
    "backtest_preheat": (task_backtest_preheat, "回测缓存预热（用户访问不再触发重算）"),
    "lhb": (task_lhb, "龙虎榜同步"),
    # ★ 2026-10-10（P2-1）：指数日内分时路径归档 —— 与 lhb 同属"盘后数据同步"类，紧随其后。
    #   为什么必须进日批：分时**只有当日**（时钟属性），不每天归档就永远没有日内历史。
    "intraday_path": (task_intraday_path, "指数分时路径归档（日内研究底座）"),
    # ★ 2026-10-10（P2-2）：盈利承压监测清单（滞胀 → 盈利端）—— 纯只读分析，
    #   排进"周期性复核"区（在 data_gap 之前）；每日跑但**只在周五推企微**（周度摘要）。
    "earnings_pressure": (task_earnings_pressure, "盈利承压监测清单（监测口径，非信号）"),
    # ★ 2026-10-11：日期键卫生兜底闸 —— 清理"假日键错位行"（运行日/交易日两派问题）。
    #   排 data_gap 之后（看得到本轮全部写入的最终状态）；只删重复、孤儿只报告。
    "data_hygiene": (task_data_hygiene, "日期键卫生兜底（清假日键错位行）"),
    # ★ 2026-09-09 迁入：原 Render 周一 04:30 循环被只读模式关闭；任务内部
    #   判定仅周一执行，其余交易日秒过
    "zz_finance": (task_zz_finance, "财报扩展周同步（仅周一）"),
    # ★ 2026-09-21 新增：数据积累型（时钟属性——晚一天少一天历史）+ zzshare 影子验证载体。
    #   幂等靠 zz_daily_snapshots.date 主键；分析开发等 10 月体检收口（盘点文档 §7）。
    "zz_daily": (task_zz_daily, "zzshare 每日数据积累（情绪/连板/涨停归因快照）"),
    # ★ 2026-09-11 迁入：原 Render 周五循环被只读模式关闭 → 回测中心停在 09-05。
    #   任务内部判定仅周五执行，其余交易日秒过。
    "weekly_report": (task_weekly_report, "周度回测报告（仅周五，落库+推送）"),
    # ★ 2026-09-20 新增：子指标因子体检周期化（体检报告 §建议5）。
    #   任务内部判定「本月是否已体检」，其余交易日秒过；排在周期报告之后。
    "subfactor_ic": (task_subfactor_ic, "子指标因子体检（仅每月首个交易日）"),
    # ★ 2026-09-30：预登记验证脚本的月度自动复核 —— 此前三个脚本**全项目无调用方**，
    #   "样本是时钟"意味着"样本够了没人跑 = 白等"。结果落 report_store 复用 _monthly_due 幂等。
    "verify_monthly": (task_verify_monthly, "预登记验证月度复核（闸门/板块动量/E2）"),
    # ★ 2026-09-30（P0）：数据底座断档自检 + 板块快照自愈。排在所有**数据写入**任务
    #   之后、LLM 类日报之前 —— 看得到本轮全部写入的最终状态，又不被慢任务拖累。
    #   ⚠️ 这是"防止数据静默缺日"的兜底闸（本次 plate_daily_zz 断档 5 天没人发现）。
    "data_gap": (task_data_gap, "数据底座断档自检 + 板块快照自愈"),
    # ★ 2026-10-19：宽基 ETF 份额异动（上交所官方，行为级证据）——必须排在清单之前。
    #   连续 2 日 ≥8% ⇒ 第①条**自动打勾**（人工可覆盖）；失败不抛。
    "etf_share_watch": (task_etf_share_watch, "宽基 ETF 份额异动（第①条证据）"),
    # ★ 2026-10-19：国家队增持公告**自动提示**（巨潮）——必须排在清单之前，
    #   否则清单第①条读到的是"上一次"的提示。只提示不打勾，失败不抛。
    "policy_announcement_watch": (task_policy_announcement_watch,
                                  "国家队增持公告自动提示（巨潮，只提示不打勾）"),
    # ★ 2026-10-18：市场底确认清单（纯观察器）——必须在 intraday_path 之后（② 用它）、
    #   daily_report 之前（日报要渲染它）。不参与任何决策链。
    # ★ 2026-10-23：两个纯观察器（拥挤赛道 / 指数关键位），不进决策链
    "crowding_watch": (task_crowding_watch, "拥挤赛道观察器（行业30日/高位占比）"),
    "key_level_watch": (task_key_level_watch, "指数关键位观察器（前低/整数位）"),
    "ai_basket_watch": (task_ai_basket_watch, "AI硬件篮子回撤观察器"),
    "scenario_watch": (task_scenario_watch, "磨底2.0推演证伪记分卡"),
    "policy_bottom_watch": (task_policy_bottom_watch, "市场底确认清单（观察器，非信号）"),
    "daily_report": (task_daily_report, "每日日报"),
    # ★ 2026-09-12 新增：交易员决策简报（盘后）+ 企微推送 —— 此前只有前端按需生成，
    #   是 TRADER_WORKFLOW Phase 1 的收尾项。依赖前面全部任务产出，排在最后。
    "trader_brief": (task_trader_brief, "交易员决策简报（盘后，幂等+企微推送）"),
    # ★ 2026-09-24 新增：数据库保留期清理（Supabase 500MB 治理）——**排最后**，
    #   在所有写入任务之后跑（清理不该与写入竞争）。只清「读取点只读最新」的表，
    #   ⚠️ 反直觉：保留期是"先涨到上限再平"，天数宁短勿长（详见 app/db_retention.py）。
    "retention": (task_retention, "数据库保留期清理（Supabase 500MB 治理）"),
}
DEFAULT_ORDER = ["backfill", "market_regime", "regime_alert", "mainflow", "market_snapshot",
                 "calendar", "mainforce_state", "gate_snapshot", "entry_log",
                 "sector_snapshot", "strategy_scan", "contradiction_scan",
                 "contradiction_report", "score_snapshot", "mainline",
                 "news_snapshot", "sector_strength",
                 "crowding_watch", "key_level_watch", "ai_basket_watch",
                 "rank_live", "sector_linkage",
                 "shadow_rank", "pattern_launch_scan",
                 "backtest_preheat",
                 "lhb", "intraday_path", "zz_finance", "zz_daily",
                 "weekly_report", "subfactor_ic",
                 "verify_monthly", "earnings_pressure", "data_gap", "data_hygiene",
                 "etf_share_watch", "policy_announcement_watch", "policy_bottom_watch",
                 "scenario_watch",
                 "daily_report",
                 "trader_brief", "retention"]


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
    # ★★ 2026-09-23 新增第二判据：除「此刻本应可用的**下限**」(want) 外，还必须对照
    #   「**本轮应处理的交易日**」(need) —— need 与 `_batch_trading_day()` 同源
    #   （`rules.latest_completed_trading_day()`，15:00 分界）。
    #
    #   为什么两个都要：两者在 **15:00~22:00** 窗口会**分开** ——
    #     · want = 上一交易日（保守下限：包 19:00 才开始构建、~20:43 才发布；判早了会让
    #       读侧 `_is_stale()` 判陈旧 ⇒ 所有 K 线/指标读取**回退查 Supabase**，
    #       实测单日近 600MB egress ⇒ 宁可多半天用旧包）
    #     · need = 当天（15:00 后当天已收盘，数据侧**就应该**处理它）
    #   ⇒ 若此刻包尚未发布，则 got = 上一交易日：旧判据 `got >= want` **放行** ⇒ 日批
    #     用**昨天的 K 线**算「今天」的信号（`scan_date` 记为 need=今天）⇒ **日期与数据
    #     脱钩且静默**（`task_strategy_scan` 的即时对账也查不出 —— 它比的是 scan_date vs
    #     期望，而写进去的确实是 need）。
    #   ★ 实例（2026-09-23 **自动日批**实测撞到）：当日 backend-pack **20:00 左右即成功**
    #     并推 gh-pages，日批由 `workflow_run` **立即接棒**、日志时间戳显示 **20:01:09**
    #     开始下载包 ⇒ 却拿到 got=2026-09-22。日志「校验通过: 2026-09-22（此刻应可用
    #     2026-09-22）」两值相等、**看起来完全正常**。
    #     归因（已排除"包内容没更新"）：包内 `pack_date` 由生成脚本按 `datetime.now()`
    #     （Actions = UTC）写入，19:00 北京 = 11:00 UTC ⇒ 当日写出的就是 20260923；
    #     ⇒ 拿到 9-22 只能是**文件层面的旧包** —— 即 `pack_source` 已记录过的已知现象：
    #     「Pages 站点部署/CDN 有 **1~2 分钟空窗**，本地包 mtime 新鲜但内容是旧的」。
    #   ⚠️ 这条比"人工跑早了"更值得警惕：**自动日批紧贴 backend-pack 触发（差 0~2 分钟），
    #     天然游走在空窗边缘**（平时靠 daily-batch 启动 + checkout + 安装依赖那几分钟开销
    #     侥幸躲过，今天没躲过）⇒ 不能指望"晚点跑"，必须靠判据兜住。
    #   ✓ 本函数的等待逻辑（每 3 分钟 `redownload()`，最长 `--max-wait` 30 分钟）**恰好
    #     覆盖该空窗**：判据不过 ⇒ 重下 ⇒ 1~2 分钟后即拿到新包 ⇒ 自动通过，无需人工介入。
    #   恒有 need >= want（22:00 后两者相等；15:00~22:00 need 更晚；15:00 前与休市日
    #   两者相等）⇒ 直接用更严格的 `got >= need` 作唯一放行条件。
    need = _batch_trading_day()
    deadline = time.time() + max(0, max_wait_min) * 60
    while True:
        try:
            got = ps._parse_pack_date(ps._pack_date())
        except Exception as e:
            print(f"  读取数据包日期失败: {e}")
            got = None
        if got is not None and got >= need:
            print(f"  数据包日期校验通过: {got}（此刻应可用 {want}；本轮应处理交易日 {need}）")
            return True
        if got is not None and got >= want and got < need:
            print(f"  ⚠️ 数据包 {got} 落后于本轮应处理交易日 {need}（下限 {want} 已满足）"
                  f"——后端包 19:00 开始构建、约 20:43 才发布，此刻应仍在构建/未发布")
        if time.time() >= deadline:
            print(f"::error::数据包仍为 {got}（本轮应处理交易日 {need}）——后端包可能失败或延迟。"
                  f"**中止日批**：继续跑会用 {got} 的 K 线算出 scan_date={need} 的信号，"
                  f"日期与数据脱钩且静默。请检查 backend-pack workflow，就绪后重跑"
                  f"（确需强行继续：--no-pack-check）")
            return False
        print(f"  数据包日期 {got} < 应处理交易日 {need}，3 分钟后强制重下重试"
              f"（Pages 站点部署/CDN 有 1~2 分钟空窗，本地包 mtime 新鲜但内容是旧的）…")
        time.sleep(180)
        # ★ 必须强制重下（2026-09-10 事故）：只置 _ready_checked 不够——_ensure_ready
        #   用 _db_fresh()（文件 mtime）判新鲜，刚下载过的包永远算"新鲜"→ 重试 10 次
        #   一次都不会再下载，30 分钟白等，最终拿着昨天的包跑完全程。
        try:
            ps.redownload()
        except Exception as e:
            print(f"  强制重下数据包失败: {e}")


def _ensure_batch_runs():
    """日批**自己的**完成标记表（只由本脚本写）。"""
    from app.database import db
    db.execute("""
        CREATE TABLE IF NOT EXISTS batch_runs (
            data_day TEXT PRIMARY KEY,
            finished_at TEXT,
            n_ok INTEGER,
            n_fail INTEGER,
            source TEXT
        )
    """)


def _batch_already_done(day: str) -> bool:
    """该数据日是否**已完成过一次全量日批**（只认 `batch_runs`，由本脚本收尾时写入）。

    ★★ 2026-10-19 事故（**第一版守卫写错，当场吃掉当天日批**）：
      第一版拿 `daily_reports` 是否已有该日来判"日批已完成"。但 **Render 的日报兜底 loop
      在 19:30 就写了一条 `date=10-19` 的日报**（早于日批 20:17）⇒ 20:17 的日批被整批跳过。
      实测后果：10-19 除了那条 19:31 的日报，**`market_snapshot` / `contradictions` /
      `gate_snapshot_history` 全部停在 10-16** ⇒ "今天根本没跑"。
      ⇒ 判据必须用**只有日批会写**的表；`daily_reports` 有多个写入方（Render loop / 日批），
        拿它当"批次完成"标志是错的。

    ★ 本条守卫要解决的**原始**问题（别删）：
      `backend-pack.yml` 的兜底 `schedule: 0 14 * * 1-5`（UTC = 北京 22:00）常被 GitHub
      延迟 3~4 小时到次日 **01:50** 才启动；guard 判"今天已跑"而**秒跳过**（run 0 分钟），
      但 run 结论仍 `success` ⇒ `workflow_run: completed` 又把 daily-batch 全量拉起跑第二遍
      （实测 10-17 01:50→03:25，白烧 1.5h + LLM 重跑，并把当天产物时间戳改写成凌晨）。
    """
    try:
        _ensure_batch_runs()
        from app.database import db
        r = db.fetch_one(
            "SELECT 1 AS x FROM batch_runs WHERE data_day = %s AND COALESCE(n_fail, 0) = 0",
            (day,))
        return bool(r)
    except Exception:
        return False        # 查不到 ⇒ **不拦**（宁可多跑一次，不能因判据故障漏跑）


def _mark_batch_done(day: str, n_ok: int) -> None:
    """全量日批**成功跑完**才写标记（失败不写 ⇒ 后续触发仍会重跑）。"""
    try:
        _ensure_batch_runs()
        from app.database import db
        db.upsert("batch_runs", {
            "data_day": str(day)[:10],
            "finished_at": datetime.now().isoformat(),
            "n_ok": int(n_ok), "n_fail": 0, "source": "daily_batch",
        }, conflict_columns=["data_day"])
    except Exception as e:
        print(f"  [batch_runs] 写完成标记失败（忽略）: {e}")


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

    # ★★ 2026-10-19：**同日重复触发守卫**（成因见 `_batch_already_done` 注释）。
    #   只拦"全量 + 自动触发(workflow_run) + 非 force"；手动 dispatch / 指定任务 / --force 照常跑。
    if (args.tasks.strip() == "all" and not args.force
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_run"):
        _d = _batch_trading_day().isoformat()
        if _batch_already_done(_d):
            print(f"⏭ 跳过本次触发：{_d} 的日批已完成（daily_reports 已存在）。"
                  "如需重跑请手动 dispatch 或加 --force。")
            return 0

    if not args.no_pack_check:
        print("\n[0] 校验数据包新鲜度...")
        # ★ 2026-09-23：**接住返回值并中止**。原实现丢弃返回值 ⇒ 校验失败也只是打个
        #   `::error::` 就继续跑（注释里 2026-09-19 已记这个"待修"，一直没修）——
        #   而"继续跑"意味着用旧包算出新日期的信号（脱钩且静默），比失败更糟。
        if not ensure_pack_fresh(args.max_wait):
            print("::error::数据包未就绪 ⇒ 中止日批（避免日期错位数据落库）。"
                  "等 backend-pack 完成后重跑，或确认无碍后加 --no-pack-check。")
            return 3

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
    # ★ 全量且无失败 ⇒ 写"本数据日已完成"标记（只由本脚本写，供同日重复触发守卫用）。
    #   ⚠️ 只在**全量**时写：单任务重算不代表整批完成。
    if args.tasks.strip() == "all":
        _mark_batch_done(_batch_trading_day().isoformat(), len(ok_list))
    return 0


if __name__ == "__main__":
    sys.exit(main())
