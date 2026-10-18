# -*- coding: utf-8 -*-
"""
================================================================================
【用途】回填 `lhb_history.seats_json`（龙虎榜买卖前五席位明细）—— **仅池内个股**
================================================================================
背景（2026-10-18 口径澄清）：
  席位明细**只对"池内个股 + 近 10 天"富化**（`mainforce/lhb.py: backfill_days(detail_recent=10)`，
  逐股 `lhb_detail` 慢）。实测池内上榜 4,458 条中只有 267 条有席位（6.0%），且
  **≤2026-07 全为 0**。要做"游资席位 → 未来收益"这类回溯研究，必须先补历史。

本脚本两种模式：
  --probe N    **默认**。从"缺席位的池内记录"里**按月均匀采样** N 条，测成功率/耗时，
               据此给出全量回填的预计时长（**先探路再决定**，避免盲目跑几千次请求）。
  --all        全量回填（默认 dry-run，加 `--apply` 才写库）。

★ 限速是硬要求：zzshare 客户端**没有内置节流**，本脚本自带 `--sleep`（默认 0.35s/次）
  与 `--max-fail`（连续失败即中止，防止把匿名/免费额度打到封禁）。
★ 只回填**池内**（`backtest_prices` 覆盖的代码）—— 池外个股本来就不富化（设计如此）。

用法：
  python scripts/lhb_seats_backfill.py --probe 30
  python scripts/lhb_seats_backfill.py --all               # dry-run，只统计
  python scripts/lhb_seats_backfill.py --all --apply       # 真写库
================================================================================
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.database import db                 # noqa: E402
from app.zzshare_client import get_api      # noqa: E402

REPORT_DIR = BACKEND / "backtest_reports"


def _missing_pool_rows() -> list:
    """池内、且还没有席位的上榜记录（按日期升序，便于按时间推进补）。"""
    rows = db.fetch(
        "SELECT l.code, l.date FROM lhb_history l "
        "WHERE l.seats_json IS NULL AND EXISTS "
        "(SELECT 1 FROM backtest_prices p WHERE p.code = l.code) "
        "ORDER BY l.date ASC")
    return [{"code": r["code"], "date": str(r["date"])[:10]} for r in rows or []]


def _spread(rows: list, n: int) -> list:
    """按月均匀采样：跨越整个时间范围，避免"只测最近两月"导致成功率虚高。"""
    if not rows or n <= 0:
        return []
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[min(int(i * step), len(rows) - 1)] for i in range(n)]


def _fetch(api, date: str, code: str) -> tuple:
    """返回 (status, ms, seats|err)。status: ok / empty / error。"""
    t0 = time.time()
    try:
        resp = api.lhb_detail(date, code)
        ms = (time.time() - t0) * 1000
        traders = (resp or {}).get("traders") if isinstance(resp, dict) else None
        if not traders:
            return "empty", ms, None
        seats = [{
            "name": t.get("trader_name") or "",
            "buy": float(t.get("buy_amount") or 0),
            "sell": float(t.get("sell_amount") or 0),
            "youzi": bool(t.get("youzi_icon")),
            "rank": t.get("rank"),
        } for t in traders]
        return "ok", ms, seats
    except Exception as e:
        return "error", (time.time() - t0) * 1000, f"{type(e).__name__}: {str(e)[:100]}"


def _save(code: str, date: str, seats: list) -> bool:
    try:
        db.execute("UPDATE lhb_history SET seats_json=%s WHERE code=%s AND date=%s",
                   (json.dumps(seats, ensure_ascii=False), code, date))
        return True
    except Exception as e:
        print(f"[lhb-seats] 写库失败 {code} {date}: {str(e)[:90]}")
        return False


def main():
    args = sys.argv[1:]
    probe_n = 30
    if "--probe" in args:
        i = args.index("--probe")
        probe_n = int(args[i + 1]) if i + 1 < len(args) else 30
    do_all = "--all" in args
    apply_ = "--apply" in args
    sleep_s = 0.35
    if "--sleep" in args:
        sleep_s = float(args[args.index("--sleep") + 1])
    max_fail = 5
    if "--max-fail" in args:
        max_fail = int(args[args.index("--max-fail") + 1])

    missing = _missing_pool_rows()
    print(f"[lhb-seats] 池内缺席位记录: {len(missing)} 条"
          f"（{'全量' if do_all else f'采样 {probe_n} 条'}，"
          f"{'写库' if apply_ else 'dry-run'}，sleep={sleep_s}s）")
    if not missing:
        print("[lhb-seats] 无需回填")
        return

    todo = missing if do_all else _spread(missing, probe_n)
    api = get_api()
    stat = {"ok": 0, "empty": 0, "error": 0, "saved": 0}
    lat, fails_in_row, first_err = [], 0, None
    t_start = time.time()
    for idx, row in enumerate(todo, 1):
        status, ms, payload = _fetch(api, row["date"], row["code"])
        lat.append(ms)
        stat[status] += 1
        if status == "error":
            fails_in_row += 1
            first_err = first_err or payload
            if fails_in_row >= max_fail:
                print(f"[lhb-seats] ⛔ 连续 {fails_in_row} 次失败（{payload}）⇒ 中止，"
                      "疑似限频/凭证失效；降低 --sleep 或检查 ZZSHARE_TOKEN")
                break
        else:
            fails_in_row = 0
            if status == "ok" and apply_ and _save(row["code"], row["date"], payload):
                stat["saved"] += 1
        if idx % 10 == 0 or idx == len(todo):
            el = time.time() - t_start
            print(f"  [{idx}/{len(todo)}] ok={stat['ok']} empty={stat['empty']} "
                  f"error={stat['error']} ｜ {el:.1f}s ｜ 均 {el / idx * 1000:.0f}ms/次")
        time.sleep(sleep_s)

    el = time.time() - t_start
    n = max(len(todo), 1)
    avg_ms = sum(lat) / len(lat)
    print(f"\n[lhb-seats] 完成 {len(todo)} 条 / {el:.1f}s"
          f"（均 {avg_ms:.0f}ms/次，含 sleep {sleep_s * 1000:.0f}ms）")
    print(f"  成功 {stat['ok']} ｜ 空返回 {stat['empty']} ｜ 异常 {stat['error']}"
          f"{' ｜ 已写库 ' + str(stat['saved']) if apply_ else ''}")
    if stat["error"] and first_err:
        print(f"  首个错误样本: {first_err}")

    # 全量预计
    if not do_all:
        per = (avg_ms / 1000) + sleep_s
        print(f"\n[lhb-seats] 全量预计: {len(missing)} 条 × {per:.2f}s ≈ "
              f"{len(missing) * per / 60:.0f} 分钟"
              f"（按当前成功率 {stat['ok'] / n * 100:.0f}%，预计可补 "
              f"{int(len(missing) * stat['ok'] / n)} 条）")

    # 落一份报告（可审计）
    try:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        p = REPORT_DIR / f"lhb_seats_{'all' if do_all else 'probe'}_{stamp}.md"
        p.write_text(
            f"# 龙虎榜席位回填 {'全量' if do_all else '探针'} · {datetime.now():%Y-%m-%d %H:%M}\n\n"
            f"- 池内缺席位总数：**{len(missing)}** 条\n"
            f"- 本次处理：{len(todo)} 条（{'写库' if apply_ else 'dry-run'}，sleep={sleep_s}s）\n"
            f"- 结果：成功 **{stat['ok']}** ／ 空返回 {stat['empty']} ／ 异常 {stat['error']}"
            f"{' ／ 已写库 ' + str(stat['saved']) if apply_ else ''}\n"
            f"- 耗时：{el:.1f}s（均 {avg_ms:.0f}ms/次）\n"
            f"- 全量预计：{len(missing) * ((avg_ms / 1000) + sleep_s) / 60:.0f} 分钟\n"
            + (f"- 首个错误：`{first_err}`\n" if first_err else ""),
            encoding="utf-8")
        print(f"[lhb-seats] 报告: {p}")
    except Exception as e:
        print(f"[lhb-seats] 报告写入失败（不影响结果）: {e}")


if __name__ == "__main__":
    main()
