#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】政策定调关键词账本 · 命令行入口
================================================================================
用法（在项目根目录执行）：
    python scripts/tone_cli.py show                      # 最近 7 日命中摘要 + 基调确认
    python scripts/tone_cli.py show --days 14
    python scripts/tone_cli.py show --day 2026-12-11     # 某日明细（含样例原句）
    python scripts/tone_cli.py scan                      # 试跑今日（只算不写）
    python scripts/tone_cli.py scan --day 2026-12-11     # 试跑某日
    python scripts/tone_cli.py rescan                    # 重算并落库当日（幂等覆盖）
    python scripts/tone_cli.py confirm --tag 宽松延续 --note "通稿原样保留"
    python scripts/tone_cli.py confirm --tag 宽松升级 --note "出现超常规表述"

★ 何时用 confirm：12 月政治局会议 / 中央经济工作会议通稿出现当日，人工读通稿要点
  后按计划文档 §3.1「基调裁决表」打标。**当日盘面涨跌不作为打标依据**（观察纪律 1）。

★ 非法标签会被拒绝；合法标签 = 宽松延续 / 宽松升级 / 宽松回退 / 未定 / 无会议。

设计说明见 `backend/app/policy_tone_watch.py` 模块头与
`docs/plans/20261107_政策定调窗口观察计划_v1.md`。
★ 本脚本只做参数解析与打印，**不含任何业务逻辑** —— 全部落在模块里。
================================================================================
"""
import argparse
import json
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "backend"))

# 加载 backend/.env（与 thesis_cli 同法；已存在的环境变量不覆盖）
_ENV = os.path.join(_ROOT, "backend", ".env")
if os.path.exists(_ENV):
    with open(_ENV, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

sys.path.insert(0, os.path.join(_ROOT, "backend"))

from app import policy_tone_watch as ptw  # noqa: E402


def cmd_show(args):
    r = ptw.load(day=args.day, days=args.days)
    if not r.get("ok"):
        print(f"[tone] 读取失败: {r.get('error')}")
        return 1
    rows = r["rows"]
    if not rows:
        print("[tone] 尚无落库记录（等日批或先 rescan）")
        return 0
    if args.day:
        # 单日明细：全部关键词（含 0），带样例
        print(f"\n== {args.day} 命中明细（0=预留词未翻正）==")
        for x in rows:
            mark = f"{x['hit_count']:>3}"
            print(f"  [{ptw.CATEGORY_CN.get(x['category'], x['category'])}] "
                  f"{mark}  {x['keyword']}")
            for s in (x.get("samples") or []):
                print(f"          └ ({s['src']} {s['time']}) {s['head']}")
    else:
        # 多日摘要：只看命中的
        by_day = {}
        for x in rows:
            if (x.get("hit_count") or 0) > 0:
                by_day.setdefault(x["date"], []).append(x)
        print(f"\n== 最近 {args.days} 日命中摘要（只列命中>0）==")
        for d in sorted(by_day, reverse=True):
            hits = by_day[d]
            grouped = {}
            for x in hits:
                grouped.setdefault(x["category"], []).append(
                    f"{x['keyword']}×{x['hit_count']}")
            parts = " · ".join(
                f"{ptw.CATEGORY_CN.get(c, c)}: {'，'.join(ws)}"
                for c, ws in sorted(grouped.items()))
            print(f"  {d}  {parts}")
    for c in r["confirms"]:
        tag = c.get("tone_tag") or "-"
        src = c.get("source") or "-"
        note = c.get("note") or ""
        print(f"  基调 {c['date']}: {tag}（{src}）{('｜' + note) if note else ''}")
    print(f"\n（{r['note']}）")
    return 0


def cmd_scan(args):
    r = ptw.snapshot(day=args.day, write=False)
    bc = r.get("by_category") or {}
    print(f"\n== 试跑（不写库）{r['date']} · 语料 {r.get('n_items', 0)} 条 ==")
    for cat in ("tone", "funds", "fundamental", "reverse"):
        hits = [x for x in r["rows"] if x["category"] == cat and x["hit_count"] > 0]
        names = "，".join(f"{h['keyword']}×{h['hit_count']}" for h in hits)
        print(f"  [{ptw.CATEGORY_CN.get(cat, cat)}] {names or '（无命中）'}")
    print(f"  分类合计: {bc}")
    return 0


def cmd_rescan(args):
    r = ptw.snapshot(day=args.day, write=True)
    bc = r.get("by_category") or {}
    print(f"\n== 已落库 {r['date']} · 语料 {r.get('n_items', 0)} 条 · 合计 {bc} ==")
    print("（同日重跑幂等覆盖；跨日历史行不受影响）")
    return 0


def cmd_confirm(args):
    day = args.day or ptw._default_day()
    r = ptw.confirm(day, args.tag, note=args.note or "",
                    suggested_tag=args.suggested, source="manual")
    if not r.get("ok"):
        print(f"[tone] 拒绝: {r.get('error')}")
        return 1
    print(f"[tone] 已确认 {r['date']}: {r['tone_tag']}"
          f"{('｜' + args.note) if args.note else ''}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="政策定调关键词账本 CLI（纯事实日志）")
    sp = ap.add_subparsers(dest="cmd", required=True)

    p = sp.add_parser("show", help="读账本（默认最近 7 日摘要）")
    p.add_argument("--day", help="看某日明细（含样例原句）")
    p.add_argument("--days", type=int, default=7)
    p.set_defaults(fn=cmd_show)

    p = sp.add_parser("scan", help="试跑（只算不写）")
    p.add_argument("--day", help="默认=最近已完成交易日")
    p.set_defaults(fn=cmd_scan)

    p = sp.add_parser("rescan", help="重算并落库（幂等）")
    p.add_argument("--day", help="默认=最近已完成交易日（必须是交易日）")
    p.set_defaults(fn=cmd_rescan)

    p = sp.add_parser("confirm", help="人工基调确认（12 月会议周主通道）")
    p.add_argument("--tag", required=True,
                   help="宽松延续 / 宽松升级 / 宽松回退 / 未定 / 无会议")
    p.add_argument("--note", help="依据（通稿要点摘录）")
    p.add_argument("--suggested", help=argparse.SUPPRESS)
    p.add_argument("--day", help="默认=最近已完成交易日")
    p.set_defaults(fn=cmd_confirm)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
