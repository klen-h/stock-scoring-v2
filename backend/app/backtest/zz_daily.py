"""
================================================================================
【文件作用】zzshare「按天」日线 → `backtest_prices` 的**快速补洞通道**
================================================================================
为什么存在（2026-10-27 实测）：
  · 逐只补 1056 只 × `RATE_LIMIT` 1s + 东财断连 + 腾讯 WAF 冷却
    ⇒ 日批 2936 秒、904 只仍然补不上（且当日数据被老缺口挤到队尾）；
  · zzshare `daily(trade_date=D, fields='all', limit=6000)` **1 天 1 请求拿全市场**
    ⇒ 补最近 5 个交易日只要 5 次请求。

口径（已用 `scripts/check_zzshare_qfq.py` 实测，000002/600519/002452 × 5 日）：
  · `close` 与库内**前复权**值 **0.00% 一致**（无除权窗口）；
  · `volume` 是**股**，库内是**手** ⇒ 写入时 `/100`（实测恰好 100 倍）。

四条安全约束（缺一不可）：
  1. **只 INSERT 不 UPDATE**：走 `save_prices(replace=False)`（`ON CONFLICT DO NOTHING`）
     ⇒ 只补缺失日期，绝不改写已有历史（复权基准仍由逐只链路负责）；
  2. **窗口内 factor 不唯一 ⇒ 跳过该只**（期间有分红送转，qfq 换算不可简单照搬）
     ⇒ 交回逐只链路全量重拉，避免造复权接缝；
  3. 只写**回填池内**的代码（其余丢弃），控制写入量；
  4. 任何异常 **fail-open**（返回 `ok=False`），逐只链路照常跑。

对外函数：
  fill_missing(stock_items, benchmark, days=5) -> dict
================================================================================
"""
import time
from typing import Dict, List, Optional, Tuple

from app.backtest import data

_FETCH_RETRIES = 3
_FETCH_SLEEP = 2.0
_RATE_SLEEP = 0.2          # 每天一次请求，20 次/分限流下无需更慢


def _norm_date(d) -> str:
    """'20261024' → '2026-10-24'；已带横杠则原样。"""
    d = str(d).strip()
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return d[:10]


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_day(api, day8: str):
    """拉某天全市场（带重试）。失败返回 None（fail-open）。"""
    for attempt in range(_FETCH_RETRIES):
        try:
            return api.daily(trade_date=day8, fields="all", limit=6000)
        except Exception as e:
            if attempt < _FETCH_RETRIES - 1:
                time.sleep(_FETCH_SLEEP * (attempt + 1))
                continue
            print(f"  [WARN] zzshare {day8} 拉取失败: {type(e).__name__}: {str(e)[:80]}")
            return None
    return None


def fetch_recent(day_list: List[str], want: set) -> Tuple[Dict[str, Dict[str, tuple]], List[str]]:
    """逐日拉全市场，只保留 `want` 里的代码。

    返回 `({code: {date: (o,h,l,c,volume,factor)}}, failed_days)`。
    拆出来便于单测（不打网络）。
    """
    from app.zzshare_client import get_api
    api = get_api()
    per_code: Dict[str, Dict[str, tuple]] = {}
    failed: List[str] = []
    for d in day_list:
        df = _fetch_day(api, d.replace("-", ""))
        if df is None or len(df) == 0:
            failed.append(d)
            continue
        for rec in df.to_dict("records"):
            code = str(rec.get("ts_code") or "").split(".")[0]
            if code not in want:
                continue
            fac = _f(rec.get("factor"))
            if fac is None or fac <= 0:
                continue
            per_code.setdefault(code, {})[d] = (
                _f(rec.get("open")), _f(rec.get("high")), _f(rec.get("low")),
                _f(rec.get("close")), _f(rec.get("volume")), fac)
        time.sleep(_RATE_SLEEP)
    return per_code, failed


def build_rows(by_day: Dict[str, tuple]) -> Optional[List[dict]]:
    """把一天的 {date: (o,h,l,c,vol,factor)} 转成 `save_prices` 需要的行。

    返回 None 表示**该只必须跳过**（窗口内 factor 不唯一 = 期间有除权，
    qfq 换算不可照搬，交回逐只链路）。
    """
    factors = {v[5] for v in by_day.values()}
    if len(factors) > 1:
        return None
    rows = []
    for d in sorted(by_day):
        o, h, l, c, v, _fac = by_day[d]
        if None in (o, h, l, c) or not v:
            continue
        rows.append({
            "date": d, "open": o, "high": h, "low": l, "close": c,
            "volume": v / 100.0,        # zzshare=股，库内=手（实测恰好 100 倍）
        })
    return rows or None


def fill_missing(stock_items: List[tuple], benchmark, days: int = 5) -> Dict:
    """用 zzshare 按天数据补齐 `stock_items` 在最近 `days` 个交易日的缺失。

    `stock_items`: [(code, name), ...]（回填池）
    返回 `{ok, days, rows, codes_filled, exright_skipped, failed_days, reason}`；
    `ok=False` 表示本次没补（不抛异常，调用方继续走逐只）。
    """
    out = {"ok": False, "days": 0, "rows": 0, "codes_filled": 0,
           "exright_skipped": 0, "failed_days": [], "reason": ""}
    if not stock_items:
        out["reason"] = "回填池为空"
        return out
    bench = str(benchmark)[:10] if benchmark else ""
    if not bench:
        out["reason"] = "无基准日"
        return out

    from app.zzshare_client import get_api
    try:
        day_list = [_norm_date(d) for d in get_api().trade_days(days=days)]
    except Exception as e:
        out["reason"] = f"trade_days 失败: {str(e)[:80]}"
        return out
    day_list = sorted(d for d in day_list if d and d <= bench)
    if not day_list:
        out["reason"] = "无可用交易日（或全部晚于基准）"
        return out

    want = {c for c, _ in stock_items}
    name_of = dict(stock_items)
    try:
        per_code, failed = fetch_recent(day_list, want)
    except Exception as e:
        out["reason"] = f"拉取异常: {str(e)[:80]}"
        return out

    rows_total = 0
    filled = 0
    exright = 0
    for code, by_day in per_code.items():
        rows = build_rows(by_day)
        if rows is None:
            exright += 1            # 窗口内有除权 ⇒ 交回逐只
            continue
        try:
            n = data.save_prices(code, name_of.get(code) or code, rows)
        except Exception as e:
            print(f"  [WARN] zzshare 写入 {code} 失败（跳过该只）: {str(e)[:80]}")
            continue
        rows_total += n
        filled += 1

    out.update(ok=True, days=len(day_list) - len(failed), rows=rows_total,
               codes_filled=filled, exright_skipped=exright, failed_days=failed)
    if failed:
        out["reason"] = f"{len(failed)} 个交易日未取到"
    return out
