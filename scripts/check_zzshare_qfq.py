#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zzshare 按天日线 vs `backtest_prices` 的**前复权口径**对照（只读，不写任何库）。

【为什么需要】
  想把"逐只补 900+ 只"换成"zzshare 按天一次拿全市场"（1 天 1 请求），
  但两者口径不同：
    · `backtest_prices` = **前复权 qfq**（东财 `fqt=1` / 腾讯 `qfq`）
    · zzshare 按天   = **原始 close + factor**（按天模式 `adj` 参数无效，已实测）
  ⇒ 必须先验证 `close_raw × factor / factor_最新日` 能否复现库内值，否则会造复权接缝。

用法：
  python scripts/check_zzshare_qfq.py
  python scripts/check_zzshare_qfq.py --codes 000002,600519,002452 --days 5
  python scripts/check_zzshare_qfq.py --days 10 --tolerance 0.002

判定：逐日相对误差 |zz_qfq/库close − 1|；窗口内最大误差 ≤ tolerance（默认 0.2%）
  ⇒ 口径一致，可以按天批量补；否则先查复权基准，**不要**接。
"""
import argparse
import os
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

# Actions 里没有 backend/.env（gitignored）⇒ 容错
_env = os.path.join(BACKEND, ".env")
if os.path.exists(_env):
    for _line in open(_env, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.zzshare_client import get_api   # noqa: E402
from app.database import db              # noqa: E402


def _norm_date(d) -> str:
    d = str(d).strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d[:10]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="zzshare 按天日线 vs backtest_prices 前复权口径对照")
    ap.add_argument("--codes", default="000002,600519,002452",
                    help="对照标的（逗号分隔，默认 000002,600519,002452）")
    ap.add_argument("--days", type=int, default=5, help="每只取最近 N 个有数据的交易日")
    ap.add_argument("--tolerance", type=float, default=0.002,
                    help="判定阈值（相对误差，默认 0.002 = 0.2%%）")
    args = ap.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    if not codes:
        print("::error::--codes 为空")
        return 2

    # ── 1) 库内：每只最近 N 天（只读）──
    local = {}
    dates = set()
    for code in codes:
        rows = db.fetch(
            "SELECT date, close FROM backtest_prices WHERE code = %s "
            "ORDER BY date DESC LIMIT %s", (code, args.days)) or []
        if not rows:
            print(f"⚠️ {code} 在 backtest_prices 无数据，跳过")
            continue
        local[code] = {_norm_date(r["date"]): _f(r["close"]) for r in reversed(rows)}
        dates.update(local[code])
    if not local:
        print("::error::库内无可用对照数据")
        return 3

    days = sorted(dates)
    print(f"=== zzshare 按天(qfq 换算) vs backtest_prices ===")
    print(f"标的 {', '.join(local)}   交易日 {len(days)} 个（{days[0]} ~ {days[-1]}）\n")

    # ── 2) zzshare：逐日拉全市场（只读，1 天 1 请求）──
    api = get_api()
    remote = {}
    failed_days = []
    for d in days:
        try:
            df = api.daily(trade_date=d.replace("-", ""), fields="all", limit=6000)
        except Exception as e:
            print(f"  [FAIL] {d}: {type(e).__name__}: {str(e)[:80]}")
            failed_days.append(d)
            continue
        if df is None or len(df) == 0:
            print(f"  [WARN] {d} 返回 0 行（当日日线可能尚未发布）")
            failed_days.append(d)
            continue
        for rec in df.to_dict("records"):
            ts = str(rec.get("ts_code") or "")
            code = ts.split(".")[0]
            if code not in local:
                continue
            remote.setdefault(code, {})[_norm_date(rec.get("trade_date"))] = (
                _f(rec.get("close")), _f(rec.get("factor")))
        time.sleep(0.2)

    if not remote:
        print("::error::zzshare 未返回任何对照数据（检查 ZZSHARE_TOKEN / 接口可用性）")
        return 4

    # ── 3) 逐只逐日对比（qfq 以窗口内最新 factor 为基准）──
    worst = 0.0
    worst_at = ""
    for code, series in local.items():
        rser = remote.get(code) or {}
        if not rser:
            print(f"{code}: zzshare 无数据（可能停牌/代码格式不符）")
            continue
        last_day = max(rser)
        base_factor = (rser[last_day][1] or 0.0)
        if base_factor <= 0:
            print(f"{code}: factor 缺失/为 0，无法换算")
            continue
        print(f"{code}")
        print(f"  {'日期':<12}{'库close':>9}{'zz_raw':>9}{'factor':>9}{'zz_qfq':>9}{'差%':>8}")
        for d in sorted(series):
            got = rser.get(d)
            if not got:
                print(f"  {d:<12}{'—':>9}{'—':>9}{'—':>9}{'缺':>9}{'':>8}")
                continue
            raw, fac = got
            lc = series[d]
            if raw is None or fac is None or lc in (None, 0):
                continue
            qfq = raw * fac / base_factor
            diff = (qfq / lc - 1) * 100
            flag = " ⚠" if abs(diff) > args.tolerance * 100 else ""
            print(f"  {d:<12}{lc:>9.3f}{raw:>9.3f}{fac:>9.4f}{qfq:>9.3f}{diff:>+8.2f}{flag}")
            if abs(diff) > worst:
                worst, worst_at = abs(diff), f"{code} {d}"
        print()

    print(f"窗口内最大绝对误差：{worst:.2f}%（{worst_at or '—'}）｜阈值 {args.tolerance*100:.2f}%")
    if worst <= args.tolerance * 100:
        print("✅ 判定：口径一致 —— 可以按天批量补（close×factor/factor_最新日）")
        return 0
    print("⚠️ 判定：口径**不一致**（>阈值）—— 先查复权基准，勿直接按天批量补")
    if failed_days:
        print(f"   注：以下日期未取到数据，结论仅基于其余交易日：{', '.join(failed_days)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
