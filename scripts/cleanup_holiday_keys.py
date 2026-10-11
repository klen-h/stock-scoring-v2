#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】清理「日期键错位」脏行：把**非交易日键**（假日/周末）上的分析产物删掉
================================================================================
背景（2026-10-08 首次发现，2026-10-11 系统盘点）：
  项目的"日期键两派"问题 —— 部分日批任务用**运行日**（`datetime.now()`/`_today()`）当键，
  而语义应是**交易日**（`rules.latest_completed_trading_day()`）。日批在周末/假日也会被
  触发（cron-job.org 每日 + GitHub Mon-Fri 兜底）⇒ 于是写出「**键 = 假日、正文 = 上一交易日**」
  的错位行（同一份内容既在交易日键下、又在假日键下 ⇒ 重复）。
  ★ 教训原文在 `MEMORY.md`「日期键"两派" + 循环守卫铁律」条目；已修 `daily_report_loop`，
  但**本脚本盘点发现残留远比当时记录的多**（当时只记了 3 张表）。

【★★ 关键区分：不是所有非交易日键都是脏数据（本脚本的核心纪律）】
  ① **交易日键类（分析产物）⇒ 非交易日键 = 脏**：`sector_daily` / `trader_briefs` /
     `contradiction_reports` / `contradictions` / `daily_reports` / `radar_analysis` /
     `macro_daily_history`（内容描述的是"某个交易日"）。
  ② **自然日键类（运营计数）⇒ 非交易日键 = 设计**：`llm_usage_daily` /
     `llm_usage_by_provider`（"这天调用了多少 LLM"）/ `push_log`（"这天推了什么"）
     —— **按自然日统计才是对的**，删了反而错 ⇒ 本脚本**显式跳过**并打印理由。
  ③ **运行日键类 ⇒ 属设计、不动**：`ranking_history`（读侧按自然日判"是否今天"，
     见 `routers/scoring.py`）等。

【安全规则（宁可漏删，绝不误删）】
  · **只删「重复行」**：该非交易日键的**上一交易日键在同表存在** ⇒ 内容必有另一份 ✓。
  · **孤儿行（上一交易日键不存在）默认只报告、不删** —— 那可能是那天**唯一**的记录；
    需要迁移时显式加 `--rekey-orphans`（把它**改键**到上一交易日，而不是删除）。
  · `--since` 限定扫描范围（默认 2026-08-01），`--dry-run` 是默认行为，`--apply` 才落库。
  · 每步打印将动/已动的键与行数；删除前先 SELECT COUNT(*) 复核。

用法：
  python scripts/cleanup_holiday_keys.py                 # 干跑（只报告）
  python scripts/cleanup_holiday_keys.py --apply         # 删除重复的假日键行
  python scripts/cleanup_holiday_keys.py --apply --rekey-orphans   # 并把孤儿行改键到上一交易日
================================================================================
"""
import argparse
import os
import sys
from datetime import datetime, timedelta

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend")
sys.path.insert(0, BACKEND)
for _line in open(os.path.join(BACKEND, ".env"), encoding="utf-8"):
    _line = _line.strip()
    if _line and not _line.startswith("#") and "=" in _line:
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.database import db          # noqa: E402
import app.flash.rules as rules      # noqa: E402

# (表, 日期列, 策略, 说明)
TABLES = (
    # ── ① 交易日键（分析产物）：非交易日键 = 错位行 ⇒ 清理 ──
    ("sector_daily", "date", "trading", "东财板块快照（日批 task_sector_snapshot）"),
    ("contradiction_reports", "date", "trading", "矛盾日报（LLM，日批 contradiction_report）"),
    ("contradictions", "date", "trading", "矛盾扫描（日批 contradiction_scan）"),
    ("daily_reports", "date", "trading", "每日日报（LLM；写入侧 2026-10-08 已修）"),
    ("trader_briefs", "date", "trading", "交易员简报（(date,phase) 幂等）"),
    ("radar_analysis", "date", "trading", "先知雷达分析"),
    ("macro_daily_history", "date", "trading", "宏观日频序列（15:03 快照；周末美股休市=重复值）"),
    # ── ② 自然日键（运营计数）：非交易日键 = **设计**，显式跳过 ──
    ("llm_usage_daily", "day", "natural", "LLM 用量按**自然日**统计（周末也有调用）"),
    ("llm_usage_by_provider", "day", "natural", "LLM 分站用量，同上"),
    ("push_log", "date", "natural", "推送流水：记录推送**发生**在哪个自然日"),
    # ── ③ 运行日键 / 结构特殊：不动（仅登记理由，避免下次误判）──
    ("ranking_history", "rank_date", "skip", "运行日键**属设计**：读侧按自然日判「是否今天」"),
    ("ranking_live", "rank_date", "skip", "同 ranking_history"),
    ("plate_daily_zz", "date", "skip", "zzshare 板块，本就用交易日键且支持回填"),
    ("mainforce_state", "date", "skip", "日批用 _batch_trading_day()（交易日键 ✓）"),
    ("market_regime_history", "date", "skip", "日批用交易日键 ✓"),
    ("gate_snapshot_history", "date", "skip", "日批 snapshot(d.isoformat()) ✓"),
    ("sector_amount_daily", "date", "skip", "日批用交易日键 ✓"),
    ("shadow_rank_daily", "rank_date", "skip", "日批用交易日键 ✓"),
    ("macro_daily", "date", "skip", "历史表（斜杠日期格式），未接日批"),
    ("etf_close", "date", "skip", "斜杠日期格式，另一套写入方"),
)


def is_td(d: str) -> bool:
    try:
        return bool(rules.is_trading_day(datetime.strptime(d[:10], "%Y-%m-%d")))
    except Exception:
        return True          # 解析不了 ⇒ 不当脏数据（宁可漏删）


def prev_td(d: str) -> str:
    x = datetime.strptime(d[:10], "%Y-%m-%d")
    for _ in range(15):
        x -= timedelta(days=1)
        if rules.is_trading_day(x):
            return x.strftime("%Y-%m-%d")
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-08-01", help="只扫这个日期之后的键")
    ap.add_argument("--apply", action="store_true", help="真删（默认 dry-run）")
    ap.add_argument("--rekey-orphans", action="store_true",
                    help="孤儿行（上一交易日无键）改键到上一交易日，而不是留着")
    args = ap.parse_args()
    mode = "APPLY" if args.apply else "DRY-RUN"
    print("=" * 78)
    print("清理假日键错位行  ［%s］  since=%s  rekey_orphans=%s"
          % (mode, args.since, args.rekey_orphans))
    print("=" * 78)

    tot_del = tot_rekey = tot_skip = 0
    for t, col, policy, note in TABLES:
        try:
            rows = db.fetch('SELECT "%s" AS k, COUNT(*) AS n FROM %s WHERE "%s" >= %%s '
                            'GROUP BY "%s" ORDER BY "%s"' % (col, t, col, col, col),
                            (args.since,)) or []
        except Exception as e:
            print("\n[跳过] %s.%s —— 查询失败: %s" % (t, col, str(e)[:60]))
            continue
        bad = [r for r in rows if not is_td(str(r["k"]))]
        if not bad:
            continue
        print("\n▸ %s.%s  （%s；%s）" % (t, col, policy, note))
        if policy in ("natural", "skip"):
            tot_skip += len(bad)
            why = "自然日键属设计" if policy == "natural" else "运行日键属设计"
            print("    **不动**（%s）：%s" % (why, ", ".join(str(r["k"])[:10] for r in bad)))
            continue
        allk = {str(r["k"])[:10] for r in rows}
        for r in bad:
            k = str(r["k"])[:10]
            n = r["n"]
            p = prev_td(k)
            if not p:
                print("    %s  %d 行 —— 找不到上一交易日，跳过" % (k, n))
                continue
            if p in allk:
                print("    %s  %d 行 ⇒ **重复**（%s 键存在）⇒ 删除" % (k, n, p))
                if args.apply:
                    db.execute('DELETE FROM %s WHERE "%s" = %%s' % (t, col), (k,))
                    left = db.fetch_one('SELECT COUNT(*) AS c FROM %s WHERE "%s" = %%s'
                                        % (t, col), (k,)) or {}
                    print("        已删；剩余 %s 行" % left.get("c"))
                tot_del += 1
            else:
                print("    %s  %d 行 ⇒ **孤儿**（无 %s 键）" % (k, n, p))
                if args.rekey_orphans:
                    print("        ⇒ 改键 %s → %s（不删数据）" % (k, p))
                    if args.apply:
                        db.execute('UPDATE %s SET "%s" = %%s WHERE "%s" = %%s' % (t, col, col),
                                   (p, k))
                    tot_rekey += 1
                else:
                    print("        ⇒ 保留（要迁移请加 --rekey-orphans）")
    print()
    print("-" * 78)
    print("汇总：将删/已删重复键 %d 个；将改键/已改键孤儿 %d 个；按设计跳过 %d 个"
          % (tot_del, tot_rekey, tot_skip))
    if not args.apply:
        print("※ 这是 dry-run。确认无误后加 --apply 落库。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
