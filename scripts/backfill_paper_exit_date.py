# -*- coding: utf-8 -*-
"""
================================================================================
【一次性回填】paper_positions.exit_price_date —— 把"退出日"校正到"退出价所属交易日"
================================================================================
背景（2026-10-18，F1 对比中发现）：
  46 笔已平仓里 **9 笔（20%）** 的成交/退出价落在当日 `[low, high]` 之外 —— 物理上不可能成交。
  根因：`_close_position()` 把 `exit_date` 写成 `_bj_date()`（**平仓动作那天**），
  而价格常来自**别的交易日**的 K 线（盘后兜底取 `bars[-1].close`、安全网取"最新一根"）
  ⇒ 「退出日」与「退出价」不同源。**与 `market_snapshot.saved_at` 事故同型。**

本脚本做三件事（逐笔，幂等）：
  ① 若 `exit_price` 落在 `exit_date` 当日的 [low, high] → `exit_price_date = exit_date`（本来就对）；
  ② 否则在 `fill_date` 之后的日线里找 **open 或 close == exit_price**（容差 1 分）的那一天
     —— 取**离原 exit_date 最近**的一根，把 `exit_price_date` 与 `exit_date` 都改成它；
  ③ 都找不到 ⇒ `exit_price_date = NULL`（**不猜**），`exit_date` 保持原样，
     并列入"无法归属"清单 —— 这些笔的价格来源暂时无法解释，统计时应排除。

用法：
  python scripts/backfill_paper_exit_date.py --dry-run     # 只看会怎么改
  python scripts/backfill_paper_exit_date.py               # 真写库
================================================================================
"""

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app.database import db            # noqa: E402

TOL = 0.011          # 一分钱容差（价格是两位小数）
MAX_SHIFT_DAYS = 15  # 允许把退出日前后移动的最大天数（防"碰巧同价"的错误匹配）


def _ensure_col():
    for sql in ("ALTER TABLE paper_positions ADD COLUMN IF NOT EXISTS exit_price_date TEXT",
                "ALTER TABLE paper_positions ADD COLUMN exit_price_date TEXT"):
        try:
            db.execute(sql)
            return True
        except Exception:
            continue
    return False


def _bars(code: str, start: str):
    rows = db.fetch("SELECT date, open, high, low, close FROM backtest_prices "
                    "WHERE code=%s AND date >= %s ORDER BY date", (code, start))
    return [{**dict(r), "date": str(r["date"])[:10]} for r in rows]


def _dd(s: str):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d")
    except Exception:
        return None


def main():
    dry = "--dry-run" in sys.argv
    _ensure_col()
    rows = db.fetch("SELECT id, code, name, fill_date, exit_price, exit_date, exit_reason "
                    "FROM paper_positions WHERE status='closed' ORDER BY exit_date, id")
    print(f"已平仓 {len(rows)} 笔（{'DRY-RUN，不写库' if dry else '真写库'}）\n")

    n_ok = n_fixed = n_unknown = 0
    unknown = []
    for r in rows:
        px = float(r["exit_price"] or 0)
        old_d = str(r["exit_date"] or "")[:10]
        fill_d = str(r["fill_date"] or "")[:10]
        if px <= 0 or not old_d:
            n_unknown += 1
            unknown.append((r["code"], r["name"], old_d, px, "无退出价/日期"))
            continue
        bars = _bars(r["code"], fill_d)
        by = {b["date"]: b for b in bars}
        cur = by.get(old_d)
        # ① 当日区间内 → 本来就一致
        if cur and cur["low"] - TOL <= px <= cur["high"] + TOL:
            n_ok += 1
            if not dry:
                db.execute("UPDATE paper_positions SET exit_price_date=%s WHERE id=%s",
                           (old_d, r["id"]))
            continue
        # ② 找 open/close 命中那天（取离原日期最近）
        cands = []
        for b in bars:
            if abs(float(b["open"] or 0) - px) <= TOL or abs(float(b["close"] or 0) - px) <= TOL:
                cands.append(b["date"])
        target = None
        if cands:
            d0 = _dd(old_d)
            cands.sort(key=lambda d: abs((_dd(d) - d0).days) if d0 and _dd(d) else 999)
            best = cands[0]
            if d0 and _dd(best) and abs((_dd(best) - d0).days) <= MAX_SHIFT_DAYS:
                target = best
        if target:
            n_fixed += 1
            print(f"  修正 {r['code']} {r['name']}: 退出日 {old_d} → {target}"
                  f"（价 {px}，原日期区间 {cur['low'] if cur else '—'}~{cur['high'] if cur else '—'}）")
            if not dry:
                db.execute("UPDATE paper_positions SET exit_price_date=%s, exit_date=%s WHERE id=%s",
                           (target, target, r["id"]))
        else:
            n_unknown += 1
            unknown.append((r["code"], r["name"], old_d, px,
                            f"当日区间 {cur['low']}~{cur['high']}" if cur else "当日无K线"))
            if not dry:
                db.execute("UPDATE paper_positions SET exit_price_date=NULL WHERE id=%s", (r["id"],))

    print(f"\n汇总：本来就对 {n_ok} 笔 ｜ 已校正 {n_fixed} 笔 ｜ 无法归属 {n_unknown} 笔")
    if unknown:
        print("\n无法归属（价格在 fill_date 之后的任何 open/close 里都找不到）：")
        for c, n, d, p, why in unknown:
            print(f"  {c} {n} 退出日 {d} 价 {p} —— {why}")
        print("  ⚠️ 这些笔**不要**参与胜率/均收益统计（价格来源待查），"
              "`exit_price_date` 已置 NULL 作为标记。")


if __name__ == "__main__":
    main()
