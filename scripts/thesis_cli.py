# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】Thesis Tracker 命令行入口（宏观剧本追踪器）
================================================================================
用法（在项目根目录执行）：
    python scripts/thesis_cli.py seed
    python scripts/thesis_cli.py register --claim "..." --p 0.6 --deadline "2026-12-10 03:00"
    python scripts/thesis_cli.py revise   --id 1 --p 0.55 --reason "..." --trigger "..."
    python scripts/thesis_cli.py evidence --id 1 --field dot_2027_median --value 4.1 \
                                          --source-url "https://..."
    python scripts/thesis_cli.py resolve [--force]
    python scripts/thesis_cli.py list [--status open]
    python scripts/thesis_cli.py show --id 1
    python scripts/thesis_cli.py stats

设计说明见 `backend/app/thesis_tracker.py` 模块文档头。
★ 本脚本只做参数解析与打印，**不含任何业务逻辑** —— 全部落在模块里，
  这样日批任务（后续 task_thesis_evidence）能复用同一套函数。
================================================================================
"""
import argparse
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "backend"))

# 加载 backend/.env（与其它 scripts 一致；已存在的环境变量不覆盖）
_ENV = os.path.join(_ROOT, "backend", ".env")
if os.path.exists(_ENV):
    with open(_ENV, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app import thesis_tracker as tt  # noqa: E402


# ══════════════════════════════════════════════════════════════════════════
# 种子数据
# ══════════════════════════════════════════════════════════════════════════
def _cmd_seed(args) -> int:
    """登记两条种子数据（幂等，可重复执行）。

    ① 首条真假设（backfilled=0）：12/9 FOMC +25bp 且 2027 点阵图中值 ≤4.1%
    ② 回填演示样本（backfilled=1）：9/16 FOMC 已兑现，仅作 schema 演示
    """
    tid = tt.register_thesis(
        claim="2026-12-09 FOMC 加息 25bp 且 2027 点阵图中值 ≤4.1%",
        nature="prediction",
        thesis_type="fed_event",
        p_mine=0.65,
        p_market=0.844,
        market_source="fedwatch",
        market_as_of="2026-10-28",
        deadline="2026-12-10 03:00",       # BJT，决议公布时刻
        resolution_source="manual",         # 点阵图无可自动采集通道
        resolution_rule={
            "logic": "AND",
            "conditions": [
                {"field": "rate_decision", "op": "eq",
                 "value": "+25bp", "source": "manual"},
                {"field": "dot_2027_median", "op": "le",
                 "value": 4.1, "source": "manual"},
            ],
        },
        invalidation_early="11 月中旬 CPI 后 FedWatch 跌破 65% → 转按兵不动分支",
        created_by="user",
    )
    print(f"[seed] 首条真假设 id={tid}（backfilled=0，参与统计）")

    bt = tt.register_thesis(
        claim="（回填演示）2026-09-16 FOMC 加息 25bp",
        nature="prediction",
        thesis_type="fed_event",
        p_mine=0.87,
        p_market=0.87,
        market_source="fedwatch",
        market_as_of="2026-09-15",
        deadline="2026-09-17 03:00",
        resolution_source="manual",
        resolution_rule={
            "logic": "AND",
            "conditions": [
                {"field": "rate_decision", "op": "eq",
                 "value": "+25bp", "source": "manual"},
            ],
        },
        created_by="user",
        backfilled=True,
        status="resolved_win",
    )
    print(f"[seed] 回填演示样本 id={bt}（backfilled=1，永久排除统计）")
    return 0


# ══════════════════════════════════════════════════════════════════════════
# 注册 / 修订 / 证据 / 裁决 / 查询
# ══════════════════════════════════════════════════════════════════════════
def _cmd_register(args) -> int:
    rule = json.loads(args.rule) if args.rule else None
    tid = tt.register_thesis(
        claim=args.claim, nature=args.nature, p_mine=args.p,
        p_market=args.p_market, market_source=args.market_source,
        deadline=args.deadline, resolution_rule=rule,
        resolution_source=args.resolution_source,
        invalidation_early=args.invalidation,
        created_by=args.created_by, thesis_type=args.type,
    )
    print(f"id={tid}")
    return 0


def _cmd_revise(args) -> int:
    ok = tt.revise_thesis(args.id, args.p, args.reason, args.trigger)
    print("ok" if ok else "失败（假设不存在或已非 open）")
    return 0 if ok else 1


def _cmd_evidence(args) -> int:
    ok = tt.add_manual_evidence(args.id, args.field, args.value, args.source_url)
    print("ok" if ok else "失败（假设不存在）")
    return 0 if ok else 1


def _cmd_resolve(args) -> int:
    r = tt.resolve_open_theses(force=args.force)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("ok") else 1


def _cmd_list(args) -> int:
    rows = tt.list_theses(args.status)
    if not rows:
        print("（无）")
        return 0
    for r in rows:
        flag = " [回填]" if r.get("backfilled") else ""
        pm = r.get("p_mine")
        pm_s = f"{pm:.2f}" if isinstance(pm, (int, float)) else "-"
        print(f"#{r['id']:<3} {r.get('status', ''):<14}{flag} "
              f"p={pm_s:<5} dl={r.get('deadline') or '-':<17} "
              f"{(r.get('claim') or '')[:56]}")
    return 0


def _cmd_show(args) -> int:
    d = tt.load_thesis(args.id)
    if not d:
        print(f"假设 #{args.id} 不存在")
        return 1
    print(json.dumps(d, ensure_ascii=False, indent=2, default=str))
    return 0


def _cmd_stats(args) -> int:
    print(json.dumps(tt.stats(), ensure_ascii=False, indent=2))
    return 0


# ══════════════════════════════════════════════════════════════════════════
# argparse
# ══════════════════════════════════════════════════════════════════════════
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Thesis Tracker（宏观剧本追踪器）CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("seed", help="登记种子数据（首条真假设 + 9/16 回填样本，幂等）")
    sp.set_defaults(func=_cmd_seed)

    sp = sub.add_parser("register", help="登记一条假设")
    sp.add_argument("--claim", required=True, help="可证伪的命题")
    sp.add_argument("--nature", default="prediction",
                    choices=["prediction", "decision"])
    sp.add_argument("--p", required=True, help="我的概率（0~1 或百分数，自动归一）")
    sp.add_argument("--p-market", dest="p_market", help="市场概率（同日快照）")
    sp.add_argument("--market-source", dest="market_source",
                    help="fedwatch / survey / futures")
    sp.add_argument("--deadline", help="BJT，如 '2026-12-10 03:00'")
    sp.add_argument("--rule", help="resolution_rule 的 JSON 字符串")
    sp.add_argument("--resolution-source", dest="resolution_source",
                    default="manual", choices=["auto", "manual"])
    sp.add_argument("--invalidation", help="中途失效条件")
    sp.add_argument("--type", dest="type", help="fed_event / regime / oil / a_share")
    sp.add_argument("--created-by", dest="created_by", default="user")
    sp.set_defaults(func=_cmd_register)

    sp = sub.add_parser("revise", help="修订概率（必须带 reason，append-only 留痕）")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--p", required=True)
    sp.add_argument("--reason", required=True, help="为什么改（不允许裸改）")
    sp.add_argument("--trigger", help="触发事件")
    sp.set_defaults(func=_cmd_revise)

    sp = sub.add_parser("evidence", help="录入人工证据（必须带原文出处链接）")
    sp.add_argument("--id", type=int, required=True)
    sp.add_argument("--field", required=True)
    sp.add_argument("--value", required=True)
    sp.add_argument("--source-url", dest="source_url", required=True)
    sp.set_defaults(func=_cmd_evidence)

    sp = sub.add_parser("resolve", help="跑裁决器（只处理已到期假设）")
    sp.add_argument("--force", action="store_true", help="忽略 deadline（调试用）")
    sp.set_defaults(func=_cmd_resolve)

    sp = sub.add_parser("list", help="列出假设")
    sp.add_argument("--status", help="open / resolved_win / resolved_lose / "
                                     "invalidated / expired")
    sp.set_defaults(func=_cmd_list)

    sp = sub.add_parser("show", help="查看单条假设 + 事件历史")
    sp.add_argument("--id", type=int, required=True)
    sp.set_defaults(func=_cmd_show)

    sp = sub.add_parser("stats", help="轻量统计（排除回填样本，不产出校准结论）")
    sp.set_defaults(func=_cmd_stats)

    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
