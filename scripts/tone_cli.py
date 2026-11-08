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

# Windows GBK 控制台下打印中文不炸
# ★ 2026-11-08：`try/except: pass` 改为 hasattr（reconfigure 是 3.7+ 方法），
#   避免被 code_hygiene 的 except_pass 计入违规，语义也更直白。
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


def cmd_watch(args):
    if args.cmd_word_action == "list":
        rows = ptw.watch_list()
        print(f"\n== 观察词表（enabled=1 参与提醒）==")
        for w in rows:
            mark = "✓" if w.get("enabled") else "✗"
            lp = str(w.get("last_pushed_at") or "-")[:10]
            print(f"  [{mark}] {w['word']}{('｜' + w['note']) if w.get('note') else ''}"
                  f"  最近推送 {lp}")
        if not rows:
            print("  （空——watch add --word 互换便利 添加）")
        print(f"\n（命中即推企微，每词每日至多 1 条；提示层非信号——计划 §3.1a）")
        return 0
    if args.cmd_word_action == "add":
        r = ptw.watch_add(args.word, note=args.note or "")
    else:
        r = ptw.watch_remove(args.word)
    if not r.get("ok"):
        print(f"[tone] 拒绝: {r.get('error')}")
        return 1
    print(f"[tone] 已{'新增/启用' if args.cmd_word_action == 'add' else '停用'}: {r.get('word')}")
    return 0


def cmd_lag(args):
    r = ptw.lag_report()
    if not r.get("ok"):
        print(f"[tone] 读取失败: {r.get('error')}")
        return 1
    print(f"\n== 三底时序（政策底 → 市场底 → 经济底，2008 复盘同款三层）==")
    print(f"  政策底（基调确认首个非未定日，人工）: {r.get('tone_date') or '—'}")
    print(f"  市场底（市场底清单首次 4/5 日，自动）: {r.get('pbw_date') or '—'}")
    print(f"  第一段时滞: {r.get('lag_days') if r.get('lag_days') is not None else '—'} 天（2008 参照 ≈ 42 天）")
    if r.get("economy_period"):
        print(f"  经济底（人工月粒度标注）: {r['economy_period']}"
              f" · 第二段时滞约 {r.get('economy_lag_months')} 个月（2008 参照 ≈ 3 个月）")
    else:
        print(f"  经济底: 未标注（月度复盘时 tone_cli economy mark --period YYYY-MM）")
    print(f"  {r.get('note')}")
    return 0


def cmd_economy(args):
    if args.econ_action == "mark":
        r = ptw.economy_mark(args.period, note=args.note or "")
    elif args.econ_action == "remove":
        r = ptw.economy_remove(args.period)
    else:
        rows = ptw.economy_list()
        print(f"\n== 经济底标注历史（月粒度，当前生效=最近 marked_at）==")
        for x in rows:
            print(f"  {x['period']}  标注于 {str(x.get('marked_at'))[:16]}"
                  f"{('｜' + x['note']) if x.get('note') else ''}")
        if not rows:
            print("  （空——mark --period 2027-01 --note 'PMI 连续 3 月回上'）")
        return 0
    if not r.get("ok"):
        print(f"[tone] 拒绝: {r.get('error')}")
        return 1
    print(f"[tone] 已{'标注' if args.econ_action == 'mark' else '撤销'}经济底: {r.get('period')}")
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

    # ── 观察词（2026-11-07 §3.1a：用户自管词表，命中推企微）──
    p = sp.add_parser("watch", help="观察词管理（add/remove；list 只读）")
    p.add_argument("cmd_word_action", choices=["add", "remove", "list"])
    p.add_argument("--word", help="观察词（2~30 字）")
    p.add_argument("--note", help="备注（为什么盯这个词）")
    p.set_defaults(fn=cmd_watch)

    p = sp.add_parser("lag", help="三底时序仪表（政策底 → 市场底 → 经济底）")
    p.set_defaults(fn=cmd_lag)

    # ── 经济底人工标注（2026-11-08 三底时序第三层，月粒度）──
    p = sp.add_parser("economy", help="经济底人工标注（mark/list/remove；月粒度 YYYY-MM）")
    p.add_argument("econ_action", choices=["mark", "list", "remove"])
    p.add_argument("--period", help="YYYY-MM（经济底所在月）")
    p.add_argument("--note", help="依据（如 'PMI 连续 3 月回荣枯线上'）")
    p.set_defaults(fn=cmd_economy)

    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
