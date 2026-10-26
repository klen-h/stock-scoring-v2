#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】工作台盘中变化检测器 —— 命令行入口
================================================================================
回答的问题：盘中每 30 分钟，工作台到底有没有值得说一句的变化？
（先证明信息量，再决定要不要上 LLM 盘面分析）

用法：
  python scripts/workbench_scan_probe.py --preview
      采一档、与当日最近一档比，**不写库**（可反复跑，验证取数口径/阈值）

  python scripts/workbench_scan_probe.py --run --slot 10:15
      写一档（幂等：同 date|slot 已存在则跳过；--force 强制覆盖）
      ⚠️ 手动写点只建议用于本地/测试；生产由调度器 workbench_scan_loop 采

  python scripts/workbench_scan_probe.py --summary --days 7
      多日统计：worth_rate / 分数分布 / 各原因触发次数 + 每日明细

  python scripts/workbench_scan_probe.py --day 2026-10-21
      某日 8 档明细

判据：summary 的 worth_rate ≤ 0.25 ⇒ 8 档里 ≤2 档有料 ⇒ LLM 盘面分析
      应该"变化触发"，不要每 30 分钟固定生成。
================================================================================
"""
import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
BJ = timezone(timedelta(hours=8))

if hasattr(sys.stdout, "reconfigure"):
    with contextlib.suppress(Exception):      # 老终端不支持 reconfigure 时静默跳过（GUARDRAILS #11）
        sys.stdout.reconfigure(encoding="utf-8")


def _load_env():
    path = os.path.join(BACKEND, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()
sys.path.insert(0, BACKEND)


def _print_preview(r):
    b = (r.get("snapshot") or {}).get("breadth") or {}
    idx = (r.get("snapshot") or {}).get("indices") or {}
    sec = (r.get("snapshot") or {}).get("sectors") or {}
    print("=" * 72)
    print("时间 %s | 上一档 %s | 间隔 %s 交易分钟"
          % (r.get("slot"), r.get("prev_slot"), r.get("trading_min")))
    print("指数：" + "  ".join(
        "%s %.2f%%" % ((v.get("name") or k), (v.get("change_pct") or 0))
        for k, v in idx.items()))
    print("宽度：涨 %s / 跌 %s（比 %.3f） 涨停 %s 跌停 %s 成交 %.0f 亿"
          % (b.get("up"), b.get("down"), b.get("up_ratio") or 0,
             b.get("limit_up"), b.get("limit_down"), b.get("amount_yi") or 0))
    top = (sec.get("top") or [])[:3]
    if top:
        print("强势板块：" + "  ".join("%s %+.2f%%" % (x["industry"], x["avg_change_pct"])
                                  for x in top))
    news = (r.get("snapshot") or {}).get("news") or {}
    print("快讯：近 30 分钟 %s 条（高信号 %s）" % (news.get("n"), news.get("key_n")))
    print("-" * 72)
    print("分数 %s | worth=%s" % (r.get("score"), r.get("worth")))
    for x in (r.get("reasons") or []):
        print("  [%s] %+d  %s" % (x.get("tag"), x.get("pts", 0), x.get("text")))
    if not r.get("reasons"):
        print("  （无触发）")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true", help="采一档，不写库")
    ap.add_argument("--run", action="store_true", help="写一档（幂等）")
    ap.add_argument("--slot", default=None, help="采样点 HH:MM（默认取当前应采档）")
    ap.add_argument("--force", action="store_true", help="--run 时覆盖已有档")
    ap.add_argument("--summary", action="store_true", help="多日统计")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--day", default=None, help="某日明细 YYYY-MM-DD")
    args = ap.parse_args()

    from app import workbench_scan as ws

    if args.summary:
        print(json.dumps(ws.summary(args.days), ensure_ascii=False, indent=2))
        return
    if args.day:
        print(json.dumps(ws.day_detail(args.day), ensure_ascii=False, indent=2))
        return
    if args.run:
        r = ws.run_slot(args.slot, write=True, force=args.force)
        if r.get("skipped"):
            print("跳过：%s（%s）" % (r.get("key"), r.get("skipped")))
            return
        _print_preview(r)
        print("已写入：%s" % r.get("key"))
        return
    # 默认 = preview
    r = ws.preview()
    if r.get("ok") is False:
        print("跳过：%s" % r.get("note"))
        return
    _print_preview(r)


if __name__ == "__main__":
    main()
