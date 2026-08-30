"""
================================================================================
【文件作用】美国财政部财政数据（Treasury Fiscal Data API，免认证）
================================================================================

数据源：美国财政部公开 API https://api.fiscaldata.treasury.gov（无需 API key）
  - debt_to_penny（v2）  每日未偿还国债总额（日度，1993 起）
  - mts_table_1          财政部月度收支表（line 110 = 当前财年最新月的赤字/盈余）

计算口径（单位：万亿美元）：
  - 美债净融资 = 最新月末未偿还总额(tot_pub_debt_out_amt) - 上月末（≈当月国债净发行）
  - 月度赤字/盈余 = mts_table_1.current_month_dfct_sur_amt（正数=赤字，负数=盈余）

用途：LLM 宏观分析的"财政/供给轨"——赤字扩张 + 净融资上升 → 长端供给压力 →
      收益率易升难降 → 压制黄金/科技、支撑美元。
月度为主 → 缓存 6h；拉取失败返回 None（调用方静默降级）。
================================================================================
"""

import time
import requests

_DEBT_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
             "/v2/accounting/od/debt_to_penny")
_MTS_URL = ("https://api.fiscaldata.treasury.gov/services/api/fiscal_service"
            "/v1/accounting/mts/mts_table_1")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_CACHE = {"data": None, "ts": 0.0}
_TTL = 21600          # 月度为主，缓存 6 小时足够


def _get(url: str, params: dict) -> list:
    """拉取 API 并返回 data 列表；失败返回空列表。"""
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=20)
        if r.status_code == 200:
            return r.json().get("data") or []
        print(f"[treasury] HTTP {r.status_code} {url}")
    except Exception as e:
        print(f"[treasury] 拉取失败 {url}: {e}")
    return []


def _month_last(rows: list) -> dict:
    """rows 已按 record_date desc 排序；返回 {YYYY-MM: row}，每月的第一条即该月最后一天。"""
    out = {}
    for row in rows:
        d = row.get("record_date") or ""
        if len(d) >= 7 and d[:7] not in out:
            out[d[:7]] = row
    return out


def get_treasury() -> dict:
    """
    美国财政数据（最新月份，单位万亿美元）。

    返回：
      {month, debt_total_t, net_financing_t, deficit_t, deficit_month}
      拉取/解析失败返回 None（调用方静默降级）。
    """
    now = time.time()
    if _CACHE["data"] and now - _CACHE["ts"] < _TTL:
        return _CACHE["data"]
    debt = _get(_DEBT_URL, {"sort": "-record_date", "page[size]": "700"})
    if not debt:
        print("[treasury] 债务数据为空，放弃")
        return None
    by_month = _month_last(debt)
    months = sorted(by_month.keys(), reverse=True)
    latest, prev = months[0], (months[1] if len(months) > 1 else None)
    data = {
        "month": latest,
        "debt_total_t": round(float(by_month[latest]["tot_pub_debt_out_amt"]) / 1e12, 3),
        "net_financing_t": None,
        "deficit_t": None,
        "deficit_month": None,
    }
    if prev:
        d1 = float(by_month[latest]["tot_pub_debt_out_amt"])
        d0 = float(by_month[prev]["tot_pub_debt_out_amt"])
        data["net_financing_t"] = round((d1 - d0) / 1e12, 3)
    # 月度赤字/盈余：line 110 = 当前财年最新月（正数=赤字）
    mts = _get(_MTS_URL, {"filter": "line_code_nbr:eq:110", "sort": "-record_date", "page[size]": "1"})
    if mts and mts[0].get("current_month_dfct_sur_amt"):
        data["deficit_t"] = round(float(mts[0]["current_month_dfct_sur_amt"]) / 1e12, 3)
        data["deficit_month"] = (mts[0].get("record_date") or "")[:7]
    _CACHE["data"] = data
    _CACHE["ts"] = now
    return data


def treasury_line() -> str:
    """格式化为 LLM prompt 的一行；失败返回空字符串（调用方跳过该段）。"""
    d = get_treasury()
    if not d or d.get("debt_total_t") is None:
        return ""
    parts = ["美国财政"]
    nf = d.get("net_financing_t")
    if nf is not None:
        parts.append(f"美债净融资+{nf * 10000:.0f}亿美元({d.get('month')})")
    df = d.get("deficit_t")
    if df is not None:
        tag = "赤字" if df >= 0 else "盈余"
        parts.append(f"月度{tag}{abs(df) * 10000:.0f}亿美元({d.get('deficit_month')})")
    parts.append("(供给/财政轨)")
    return "、".join(parts)
