# -*- coding: utf-8 -*-
"""
================================================================================
龙虎榜（LHB）历史回填 —— 实施规格 = `PLAN_LHB_BACKFILL.md` §5.5 v1.0（已冻结）
================================================================================
【范围】回填 2019-01-01 ~ 2026-05-05（不含已有窗口）；gate 模式只读比对
       已有窗口最近 N 个交易日（**不写入**）。
【纪律】
  · 解析一律用生产函数 `mainforce.lhb.fetch_day_list`（禁止另写）；`date` 由脚本补
    （与生产 `refresh()` 的 `it["date"] = d` 一致）；`seats=None`（不触 lhb_detail）。
  · 写入 = `lhb_history` **批量 upsert**（50 行/语句，`ON CONFLICT (code,date)`），
    **不写 seats_json / buy_total / sell_total**（保持既有富化不被抹掉）。
  · 进度表 `lhb_backfill_progress(date, n, done_at)`：0 行也记（与失败区分）。
  · 每请求间隔 0.5s；单日 3 次重试（退避 2s/4s）；每 20 日打印；结束打失败清单。
【模式】
  python scripts/lhb_backfill.py --mode smoke            # 2019 年 3 个交易日冒烟
  python scripts/lhb_backfill.py --mode gate  --days 12  # 一致性闸（只读，门 ≥95%）
  python scripts/lhb_backfill.py --mode backfill         # 全量回填（可断点续跑）
================================================================================
"""
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.database import db                                   # noqa: E402
from app.mainforce.lhb import ensure_table, fetch_day_list     # noqa: E402

GAP = 0.5                      # 请求间隔（秒）
CHUNK = 50                     # 批量 upsert 行数/语句
START = "2025-01-02"           # ★ §5.5 v1.1：源覆盖起点（实测；2019/2023/2024 均 0 行）
END = "2026-05-05"
GATE_MIN = 0.95                # 一致性闸门槛（规格 §5.5-6）


def norm_date(d: str) -> str:
    d = str(d).strip()
    return f"{d[:4]}-{d[4:6]}-{d[6:]}" if len(d) == 8 and d.isdigit() else d


def trade_days(start: str, end: str) -> list:
    from app.zzshare_client import get_api
    days = get_api().trade_days(day_start=start.replace("-", ""),
                                day_end=end.replace("-", ""))
    return [norm_date(x) for x in (days or [])]


def ensure_progress() -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS lhb_backfill_progress (
            date TEXT PRIMARY KEY, n INTEGER NOT NULL, done_at TEXT)""")


def done_dates() -> set:
    ensure_progress()
    return {str(r["date"])[:10] for r in
            (db.fetch("SELECT date FROM lhb_backfill_progress") or [])}


def save_batch(day: str, rows: list) -> int:
    """批量 upsert（50 行/语句 + 3 次重试）。不触碰 seats/buy_total/sell_total。"""
    wrote = 0
    for i in range(0, len(rows), CHUNK):
        chunk = rows[i:i + CHUNK]
        vals, params = [], []
        for r in chunk:
            vals.append("(%s,%s,%s,%s,%s,%s,%s,%s,%s)")
            params += [r["code"], r["name"], day, r["net_buy"], r["quote_change"],
                       r["up_reason"], r["concepts"], r["turnover_ratio"],
                       r["circ_mktcap"]]
        sql = ("INSERT INTO lhb_history (code, name, date, net_buy, quote_change, "
               "up_reason, concepts, turnover_ratio, circ_mktcap) VALUES "
               + ",".join(vals) +
               " ON CONFLICT (code, date) DO UPDATE SET "
               "name = EXCLUDED.name, net_buy = EXCLUDED.net_buy, "
               "quote_change = EXCLUDED.quote_change, up_reason = EXCLUDED.up_reason, "
               "concepts = EXCLUDED.concepts, turnover_ratio = EXCLUDED.turnover_ratio, "
               "circ_mktcap = COALESCE(EXCLUDED.circ_mktcap, lhb_history.circ_mktcap)")
        for attempt in (1, 2, 3):
            try:
                db.execute(sql, tuple(params))
                wrote += len(chunk)
                break
            except Exception as e:
                if attempt == 3:
                    raise
                print(f"    [retry] {day} 写入第 {attempt} 次失败（{type(e).__name__}），"
                      f"{2 * attempt}s 后重试")
                time.sleep(2 * attempt)
    return wrote


def mark_done(day: str, n: int) -> None:
    db.execute("INSERT INTO lhb_backfill_progress (date, n, done_at) VALUES (%s, %s, %s) "
               "ON CONFLICT (date) DO UPDATE SET n = EXCLUDED.n, done_at = EXCLUDED.done_at",
               (day, n, time.strftime("%Y-%m-%d %H:%M:%S")))


def fetch_day(day: str, retries: int = 3) -> list:
    for attempt in range(1, retries + 1):
        rows = fetch_day_list(day)
        if rows:
            return rows
        if attempt < retries:
            time.sleep(2 * attempt)
    return []


# ── 主流程 ────────────────────────────────────────────────────────────────────
def run_backfill(start: str, end: str) -> int:
    ensure_table()
    days = trade_days(start, end)
    done = done_dates()
    todo = [d for d in days if d not in done]
    print(f"[LHB回填] 区间 {start} ~ {end}：交易日 {len(days)}，已完成 {len(days) - len(todo)}"
          f"（断点续传），待跑 {len(todo)}")
    t0 = time.time()
    failed = []
    total = 0
    for i, d in enumerate(todo, 1):
        rows = fetch_day(d)
        if not rows:
            # ★ 0 行 = 空日/源异常：记 progress（n=0）但**列入观察清单**
            mark_done(d, 0)
            print(f"  [{i}/{len(todo)}] {d}: 0 行（记 empty，需人工看清单）")
            time.sleep(GAP)
            continue
        for r in rows:
            r["date"] = d                      # 与生产 refresh() 同款
            r["seats"] = None
        try:
            n = save_batch(d, rows)
        except Exception as e:
            failed.append(d)
            print(f"  [{i}/{len(todo)}] {d}: ★ 写入失败（{type(e).__name__}）⇒ 不记进度，可续跑")
            time.sleep(GAP)
            continue
        mark_done(d, n)
        total += n
        if i % 20 == 0 or i == len(todo):
            el = time.time() - t0
            print(f"  [{i}/{len(todo)}] {d}: {n} 行 ｜ 累计 {total} 行 ｜ "
                  f"已耗时 {el / 60:.1f}min ｜ 预计剩余 {(el / i) * (len(todo) - i) / 60:.1f}min")
        time.sleep(GAP)
    print(f"\n[LHB回填] 结束：写入 {total} 行，耗时 {(time.time() - t0) / 60:.1f} 分钟")
    if failed:
        print(f"  ★ 失败 {len(failed)} 日（重跑同命令即自动补）：{failed[:20]}")
    return 0


def run_gate(days_n: int) -> int:
    """一致性闸：重叠段逐日逐票比 net_buy / quote_change / circ_mktcap（只读）。"""
    rows = db.fetch("SELECT DISTINCT date FROM lhb_history ORDER BY date DESC LIMIT %s",
                    (days_n,)) or []
    days = sorted(str(r["date"])[:10] for r in rows)
    print(f"[一致性闸] 重叠交易日 {len(days)} 个：{days[0]} ~ {days[-1]}（只读，不写入）")
    tot_ok = tot_n = 0
    for d in days:
        fresh = {r["code"]: r for r in fetch_day(d)}
        old = {r["code"]: r for r in
               (db.fetch("SELECT code, net_buy, quote_change, circ_mktcap FROM lhb_history "
                         "WHERE date = %s", (d,)) or [])}
        if not fresh:
            print(f"  {d}: 新拉 0 行（源异常）⇒ 跳过该日")
            time.sleep(GAP)
            continue
        ok = n = 0
        bad_samples = []
        for code, f in fresh.items():
            o = old.get(code)
            if not o:
                continue
            n += 1
            same = (int(f["net_buy"] or 0) == int(o.get("net_buy") or 0)
                    and abs(float(f["quote_change"] or 0)
                            - float(o.get("quote_change") or 0)) < 1e-6
                    and (f["circ_mktcap"] is None or o.get("circ_mktcap") is None
                         or abs(float(f["circ_mktcap"])
                                - float(o["circ_mktcap"])) < 1.0))
            if same:
                ok += 1
            elif len(bad_samples) < 5:
                bad_samples.append((code, f["net_buy"], o.get("net_buy"),
                                    f["circ_mktcap"], o.get("circ_mktcap")))
        tot_ok += ok
        tot_n += n
        rate = ok / n if n else 0
        print(f"  {d}: 可比 {n} ｜ 一致 {ok}（{rate:.1%}）" +
              ("" if rate >= GATE_MIN else f"  ★样本 {bad_samples}"))
        time.sleep(GAP)
    rate = tot_ok / tot_n if tot_n else 0
    print(f"\n[一致性闸] 总字段级一致率 = {rate:.1%}（门 ≥ {GATE_MIN:.0%}）⇒ "
          f"{'✅ 通过：回填数据可用' if rate >= GATE_MIN else '❌ 未过闸：不得用于检验'}")
    return 0 if rate >= GATE_MIN else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["backfill", "gate", "smoke"], required=True)
    ap.add_argument("--start", default=START)
    ap.add_argument("--end", default=END)
    ap.add_argument("--days", type=int, default=12, help="gate 模式比对的交易日数")
    args = ap.parse_args()
    if args.mode == "smoke":
        days = trade_days("2019-01-01", "2019-01-15")
        print(f"[冒烟] 用 {len(days)} 个 2019-01 交易日试写：{days}")
        return run_backfill(days[0], days[-1])
    if args.mode == "gate":
        return run_gate(args.days)
    return run_backfill(args.start, args.end)


if __name__ == "__main__":
    sys.exit(main())
