#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF / 指数日线补齐（2026-10-22）

背景：zzshare `daily` 只有个股，不含 ETF（510300/159915…）与指数（000300/399001…）。
后端包里有 39 个这类代码，增量脚本原先只能"保留旧 bar"，每天落后一天。

数据源优先级：
  ① 腾讯**批量行情** `qt.gtimg.cn/q=sh510300,sh000300,...` —— **1 次请求拿全部**，
     返回当日 open/high/low/close/volume（手）+ 昨收，与包内口径**逐位一致**
     （实测 10-22：510300 4.107/35419780、000300 4036.30/178043852，与东财完全相同）；
  ② 东财 push2his（`klt=101&fqt=1`）—— 逐只请求，用于「缺口 > 1 天」时补历史；
     实测 10-19 的 OHLC/量与腾讯包逐位一致。

安全闸：
  · 用行情的「昨收」与包内最后一根 close 对齐（指数 0.5%、ETF 放宽到 1%，防分红）；
  · 缺口 > 10 个自然日不拼单根（会漏中间交易日），改走东财历史；
  · 任何异常都返回原 bars（保留旧数据，绝不造假/清空）。
"""
import time

import requests

_TX_URL = "https://qt.gtimg.cn/q="
_TX_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"}
_EM_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EM_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Referer": "https://quote.eastmoney.com/",
}


def _secid(code: str):
    c = str(code or "").strip().lower()
    if c.startswith("sh"):
        return "1." + c[2:]
    if c.startswith(("sz", "bj")):
        return "0." + c[2:]
    return None


def _ymd(s: str) -> str:
    s = str(s or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) >= 8 else ""


def fetch_quotes(codes):
    """批量行情（50 只/请求）→ {code: {date,open,high,low,close,volume,prev_close}}。"""
    out = {}
    codes = [c for c in codes if _secid(c)]
    for i in range(0, len(codes), 50):
        batch = codes[i:i + 50]
        try:
            r = requests.get(_TX_URL + ",".join(batch), timeout=15,
                             headers=_TX_HEADERS)
            r.encoding = "gbk"
            text = r.text
        except Exception:
            continue
        for line in text.strip().split("\n"):
            if "~" not in line:
                continue
            p = line.split("~")
            if len(p) < 35:
                continue
            prefix = "sh" if "v_sh" in p[0] else ("sz" if "v_sz" in p[0] else "bj")
            code = prefix + str(p[2])
            try:
                close = float(p[3])
                if close <= 0:
                    continue
                out[code] = {
                    "date": _ymd(p[30]),
                    "open": float(p[5]),
                    "high": float(p[33]),
                    "low": float(p[34]),
                    "close": close,
                    "volume": float(p[6]),
                    "prev_close": float(p[4]),
                }
            except (TypeError, ValueError, IndexError):
                continue
    return out


def _fetch_sina_history(code: str, last_date: str, datalen: int = 60,
                        timeout: int = 15):
    """新浪日线（多日缺口兜底）：返回 (rows, base) 或 None。

    volume 单位是**股**（包内是手）⇒ 由调用方按重叠日比例换算，实测 ≈0.01。
    """
    u = ("https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
         "CN_MarketData.getKLineData")
    sym = str(code or "").strip().lower()
    if not sym.startswith(("sh", "sz", "bj")):
        return None
    try:
        r = requests.get(u, params={"symbol": sym, "scale": 240, "ma": "no",
                                    "datalen": max(10, min(int(datalen), 120))},
                         headers={"User-Agent": "Mozilla/5.0",
                                  "Referer": "https://finance.sina.com.cn/"},
                         timeout=timeout)
        r.raise_for_status()
        arr = r.json() or []
    except Exception:
        return None
    rows = []
    for x in arr:
        try:
            rows.append([str(x["day"])[:10], float(x["open"]), float(x["high"]),
                         float(x["low"]), float(x["close"]), float(x["volume"])])
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None
    base = next((x for x in rows if x[0] == str(last_date)[:10]), None)
    return (rows, base) if base else None


def _fetch_em_history(code: str, last_date: str, days: int = 750,
                      timeout: int = 15):
    """东财历史（逐只）：返回 (rows, base) 或 None。rows 含 last_date 那根。"""
    sid = _secid(code)
    if not sid:
        return None
    params = {
        "secid": sid, "klt": 101, "fqt": 1,
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "beg": 0, "end": 20500101, "lmt": max(10, min(int(days), 60)),
    }
    try:
        r = requests.get(_EM_URL, params=params, headers=_EM_HEADERS,
                         timeout=timeout)
        r.raise_for_status()
        klines = ((r.json().get("data") or {}).get("klines") or [])
    except Exception:
        return None
    rows = []
    for line in klines:
        p = line.split(",")
        if len(p) < 6:
            continue
        try:
            rows.append([p[0], float(p[1]), float(p[3]), float(p[4]),
                         float(p[2]), float(p[5])])
        except (TypeError, ValueError):
            continue
    if not rows:
        return None
    base = next((x for x in rows if x[0] == str(last_date)[:10]), None)
    return (rows, base) if base else None


def merge_with_pack(prev_bars, code: str, days: int = 750, quote=None):
    """把当日行情/东财新 bar 拼到 prev_bars 后；返回 (bars, status)。

    优先级：批量行情（1 请求/全部）→ 东财历史（缺口>1天）。
    status ∈ ok / no_new / no_overlap
    """
    if not prev_bars:
        return prev_bars, "no_overlap"
    last_d = str(prev_bars[-1][0])[:10]
    last_c = float(prev_bars[-1][4])
    tol = 0.01 if str(code).lower().startswith(("sh51", "sz15", "sh58", "sz16")) else 0.005

    # ① 当日行情（只有一根；昨收必须与包内末根对齐）
    if quote and quote.get("date") and quote["date"] > last_d:
        try:
            gap = (__import__("datetime").date.fromisoformat(quote["date"])
                   - __import__("datetime").date.fromisoformat(last_d)).days
        except ValueError:
            gap = 99
        if 0 < gap <= 10 and abs(quote["prev_close"] - last_c) / max(last_c, 0.01) <= tol:
            out = list(prev_bars) + [[
                quote["date"], round(quote["open"], 3), round(quote["high"], 3),
                round(quote["low"], 3), round(quote["close"], 3),
                round(quote["volume"], 2)]]
            return out[-days:], "ok"

    # ② 历史（缺失多天时）：新浪优先（东财本机 IP 已被限流），东财兜底
    got = _fetch_sina_history(code, last_d, max(20, min(days, 120)))
    if got is None:
        got = _fetch_em_history(code, last_d, days)
    if got is None:
        return prev_bars, "no_overlap"
    rows, base = got
    ec, ev = base[4], base[5]
    if abs(ec - last_c) / max(last_c, 0.01) > tol:
        return prev_bars, "no_overlap"
    ratio = (float(prev_bars[-1][5]) / ev) if ev else 1.0
    out = list(prev_bars)
    added = 0
    for d, o, h, l, c, v in rows:
        if d <= last_d:
            continue
        out.append([d, round(o, 3), round(h, 3), round(l, 3),
                    round(c, 3), round(v * ratio, 2)])
        added += 1
    return (out[-days:], "ok" if added else "no_new")


def batch(codes, prev_bars: dict, days: int = 750, pause: float = 0.15):
    """批量补齐（先 1 次批量行情，再对缺口逐只走东财）。

    返回 ({code: bars}, ok_n, fail_n)；失败保持原 bars。
    """
    q = fetch_quotes(codes)
    out, ok_n, fail_n = {}, 0, 0
    for i, code in enumerate(codes, 1):
        bars, st = merge_with_pack(prev_bars.get(code) or [], code, days,
                                   quote=q.get(code))
        out[code] = bars
        if st == "ok":
            ok_n += 1
        elif st == "no_overlap":
            fail_n += 1
        if pause and i % 10 == 0:
            time.sleep(pause)
    return out, ok_n, fail_n
