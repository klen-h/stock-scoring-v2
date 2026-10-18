# -*- coding: utf-8 -*-
"""
================================================================================
【一次性】市场底确认清单 · 历史回放（**描述性披露，不作判据、不进决策链**）
================================================================================
用途：检验清单在"已知底部区间"里的行为是否合理（是否会在底部附近开始打勾）。

★ 诚实的边界（必须先读）：
  · 原计划想回放 **2024-09-24 / 2025-04-08** 两个"政策底=市场底"时点，但
    **回放不可行**：涨幅宽度历史表 `market_emotion_daily` 起于 **2026-09-25**、
    成交额历史（`market_amount_daily` 未建 / `intraday_path`）起于 **2026-10-09**
    ⇒ 那两段没有任何数据源。**不补拉、不猜**。
  · 因此本脚本只回放**有数据的区间**（默认 2026-09-25 ~ 最新），并逐日标注
    哪条是 `insufficient`（数据不足 ≠ 不满足）。

口径：对每个交日 D，只用 **D 及之前**的数据计算（`policy_bottom_watch.evaluate(D)`
      内部已按 `as_of=D` 截断）⇒ 无前视。

用法：
  python scripts/pbw_replay.py                 # 默认区间
  python scripts/pbw_replay.py --from 2026-09-28 --to 2026-10-16
================================================================================
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.database import db                     # noqa: E402
import app.policy_bottom_watch as pbw           # noqa: E402


def _dates(start: str, end: str) -> list:
    """可用交日集合：涨跌比表 ∪ 成交额表（两者谁有都算一天）。"""
    out = set()
    for sql in ("SELECT date FROM market_emotion_daily WHERE date >= %s AND date <= %s",
                "SELECT DISTINCT date FROM intraday_path WHERE date >= %s AND date <= %s"):
        try:
            for r in db.fetch(sql, (start, end)):
                out.add(str(r["date"])[:10])
        except Exception:
            pass
    return sorted(d for d in out if d)


def main():
    args = sys.argv[1:]
    start = "2026-09-25"
    end = datetime.now().strftime("%Y-%m-%d")
    if "--from" in args:
        start = args[args.index("--from") + 1]
    if "--to" in args:
        end = args[args.index("--to") + 1]

    days = _dates(start, end)
    print(f"[replay] 区间 {start} ~ {end}，可用交日 {len(days)} 个")
    rows = []
    for d in days:
        data = pbw.evaluate(d)
        items = data["items"]
        marks = "".join(
            ("Y" if items[k].get("checked") else ("?" if items[k].get("insufficient") else "."))
            for k in ("nation_team_ann", "amount_dry_up", "chinext_weekly",
                      "breadth_ratio_2d", "us10y_or_oil"))
        rows.append((d, data["n_checked"], marks, data["position_hint"]))
        print(f"  {d}  {data['n_checked']}/5  [{marks}]")

    # 落报告
    out = BACKEND / "backtest_reports"
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"pbw_replay_{datetime.now().strftime('%Y%m%d_%H%M')}.md"
    lines = [
        f"# 市场底确认清单 · 历史回放（{start} ~ {end}）\n",
        "> **描述性披露，不作判据、不进决策链。** 口径见 `app/policy_bottom_watch.py` 模块头。\n",
        "## 一、为什么没有 2024-09 / 2025-04 的回放\n",
        "- 涨跌比历史 `market_emotion_daily` 起于 **2026-09-25**；",
        "- 成交额历史：`market_amount_daily` 未建表（懒建），`intraday_path` 起于 **2026-10-09**；",
        "- ⇒ 那两段**没有任何数据源**，不补拉、不猜（补拉会引入「事后构造」的偏差）。\n",
        "## 二、逐日清单（Y=达成 / .=未达成 / ?=数据不足）\n",
        "列序：①国家队公告 ②成交地量 ③创业板周线 ④涨跌比 ⑤美债/油价\n",
        "| 日期 | 达成 | 五条 | 提示 |",
        "|---|---|---|---|",
    ]
    for d, n, marks, hint in rows:
        lines.append(f"| {d} | {n}/5 | `{marks}` | {hint} |")
    lines.append("")
    lines.append("## 三、观察（只描述，不推论）\n")
    if rows:
        best = max(rows, key=lambda r: r[1])
        lines.append(f"- 区间内最高达成数 **{best[1]}/5**（{best[0]}），"
                     f"门槛 {pbw.PBW_PASS} 条 ⇒ "
                     f"{'曾跨过门槛' if best[1] >= pbw.PBW_PASS else '**从未跨过门槛**'}。")
        lines.append(f"- 门槛：成交 <{pbw.PBW_AMOUNT_YI:.0f} 亿连续 {pbw.PBW_AMOUNT_DAYS} 日、"
                     f"涨跌比 >{pbw.PBW_RATIO:g} 连续 {pbw.PBW_RATIO_DAYS} 日、"
                     f"10Y <{pbw.PBW_US10Y:g}% 或 布伦特 <{pbw.PBW_BRENT:g}。")
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"[replay] 报告: {p}")


if __name__ == "__main__":
    main()
