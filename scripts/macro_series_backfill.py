#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】宏观序列回填：拉取美债/美元等外部序列的**日线历史**，落本地缓存
================================================================================
为什么需要它（2026-09-20）：
  系统其实**早就有**外部输入的"传感器"，但**没有记忆** ——
    · `app/macro.py` 面板（新浪）只有**实时值**；
    · `store.save_macro_daily()` 每交易日只落**一份快照**（实测库中仅 11 行）。
  ⇒ 于是 `macro.py` 里写死的待办「阈值需用历史数据回测校准」**一直做不了**，
    也无法回答「美债/美元对 A 股是否**领先**」这个决定"能否前瞻"的关键问题。

  实测东财 `push2his` 直接给日线历史（2026-09-20 探测）：
    · `171.US10Y` → 美国10年期国债收益率   ✓
    · `171.US30Y` → 美国30年期国债收益率   ✓
    · `100.UDI`   → 美元指数               ✓
  与项目既有 `backend/app/eastmoney.py` **同域同 session 风格**（东财 push2 家族）。

⚠️ 东财对高频请求会 **RemoteDisconnected**（实测：连续请求被服务端断连；
  同理 `lmt=100000` 也会被断）。⇒ 本脚本**逐品种限速 + 重试**，别并发。

输出（本地文件，零 Supabase —— 与 `research_cache` 同模式，仅供离线回测）：
  `backend/data/macro_series.json`
    {"fetched_at": "...", "series": {"171.US10Y": {"name": "...",
       "rows": [["2026-09-14", 4.9731, 4.9957, 5.0142, 4.9363], ...]}}}
    rows 元素 = [date, open, close, high, low]（升序）

用法：
  python scripts/macro_series_backfill.py                 # 默认 3 个品种，lmt=1200
  python scripts/macro_series_backfill.py --lmt 3000      # 拉更长（≈12 年）
  python scripts/macro_series_backfill.py --secids 171.US10Y,100.UDI
================================================================================
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_PATH = os.path.join(ROOT, "backend", "data", "macro_series.json")

_KLINE_URL = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
# ★ 2026-09-20 实测：本机走 **HTTPS** 到 push2his 会被 `RemoteDisconnected`（ConnectionError），
#   换成 **http://** 立刻通（同参数、同头、同 secid）。
#   曾误判为"IP 限流"（因为首次探测 lmt=5 恰好成功、后续 https 全失败）——
#   真相是**协议**问题，不是限流。⇒ 不要因为 https 失败就以为被封 IP。
#   （公开行情数据走 http 明文，无敏感信息；如需 https 可另找域/加代理。）

# ── 源 A：新浪外盘期货族（★ 2026-09-20 实测全线可用，且与项目 macro.py 同源）──
#   `GlobalFuturesService.getGlobalFuturesDailyKLine` 对**外盘期货**有效，
#   返回 GBK 的 jsonp（`var _d=([{date,open,high,low,close,volume},...])`），
#   实测可用且历史长：CL(原油)1996 起、CHA50CFD(A50)2016 起、NQ/NK/VX 2018 起、DX 2019 起。
#   ⚠️ 但**美债不在期货族**（`symbol=globalbd_us10yt` 返回 `null`）⇒ 走源 B。
_SINA_FUT_URL = ("https://stock.finance.sina.com.cn/futures/api/jsonp.php/"
                 "var%20_d=/GlobalFuturesService.getGlobalFuturesDailyKLine?symbol=")
SINA_FUTURES = {
    "DX": "美元指数期货（ICE）",
    "VX": "VIX 恐慌指数期货",
    "NQ": "纳斯达克100期货",
    "NK": "日经225期货",
    "CHA50CFD": "富时中国A50期货",
}

# ── 源 B：东财 push2his（美债只有它能给）──
# 默认品种：本次要验证"外部流动性冲击"领先性所需的最小集合
DEFAULT_SECIDS = {
    "171.US10Y": "美国10年期国债收益率",
    "171.US30Y": "美国30年期国债收益率",
    "100.UDI": "美元指数",
}

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://quote.eastmoney.com/",
})

# 新浪 session（与项目 macro.py 同风格：Referer 必带，否则 2022 起被拒）
_sina = requests.Session()
_sina.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://finance.sina.com.cn",
})

# 限速：东财对连续请求敏感（实测被 RemoteDisconnected）
_INTERVAL = 1.5
_tries = 4


def fetch_series(secid: str, lmt: int = 1200) -> dict:
    """拉单个品种的日线序列。失败重试（递增退避）；全失败返回 {}。

    返回 {"name": str, "rows": [[date, open, close, high, low], ...]（升序）}
    """
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3",
        "fields2": "f51,f52,f53,f54,f55",   # date, open, close, high, low
        "klt": 101,                         # 101 = 日线
        "fqt": 0,                           # 不复权（收益率/指数无需复权）
        "end": "20500101",
        "lmt": lmt,
    }
    for attempt in range(1, _tries + 1):
        try:
            r = _session.get(_KLINE_URL, params=params, timeout=20)
            d = (r.json() or {}).get("data") or {}
            klines = d.get("klines") or []
            if not klines:
                print(f"  [{secid}] 第 {attempt} 次：无数据（name={d.get('name')}）")
                return {}
            rows = []
            for k in klines:
                p = str(k).split(",")
                if len(p) < 5:
                    continue
                try:
                    rows.append([p[0], float(p[1]), float(p[2]),
                                 float(p[3]), float(p[4])])
                except ValueError:
                    continue
            return {"name": d.get("name") or secid, "rows": rows}
        except Exception as e:
            wait = _INTERVAL * attempt
            print(f"  [{secid}] 第 {attempt}/{_tries} 次失败（{type(e).__name__}）"
                  f"，{wait:.1f}s 后重试")
            time.sleep(wait)
    return {}


def fetch_sina_futures(symbol: str, lmt: int = 1200) -> dict:
    """拉新浪外盘期货的历史日线（源 A）。返回 {name, rows}；失败返回 {}。

    返回体是 GBK 的 jsonp：`/*...*/ var _d=([{"date":"2026-09-18",...},...])`（**升序**）。
    """
    import re as _re
    url = _SINA_FUT_URL + symbol
    for attempt in range(1, _tries + 1):
        try:
            r = _sina.get(url, timeout=25)
            text = r.content.decode("gbk", "replace")
            m = _re.search(r"var\s+_d=\((\[.*\])\)", text, _re.S)
            if not m:
                print(f"  [{symbol}] 第 {attempt} 次：解析失败（非数组响应）")
                return {}
            arr = json.loads(m.group(1))
            rows = []
            for it in arr[-lmt:]:            # 取尾部 N 根
                try:
                    rows.append([str(it["date"])[:10], float(it["open"]),
                                 float(it["close"]), float(it["high"]),
                                 float(it["low"])])
                except (KeyError, TypeError, ValueError):
                    continue
            if not rows:
                print(f"  [{symbol}] 第 {attempt} 次：无有效行")
                return {}
            return {"name": SINA_FUTURES.get(symbol, symbol), "rows": rows}
        except Exception as e:
            wait = _INTERVAL * attempt
            print(f"  [{symbol}] 第 {attempt}/{_tries} 次失败（{type(e).__name__}）"
                  f"，{wait:.1f}s 后重试")
            time.sleep(wait)
    return {}


def load_existing() -> dict:
    if not os.path.exists(OUT_PATH):
        return {}
    try:
        with open(OUT_PATH, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("series") or {}
    except (ValueError, OSError):
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lmt", type=int, default=1200, help="每品种取最近 N 根（默认 1200≈5 年）")
    ap.add_argument("--secids", default="", help="东财品种（逗号分隔）；默认内置 3 个")
    ap.add_argument("--futures", default="", help="新浪期货符号（逗号分隔）；默认内置 5 个")
    args = ap.parse_args()

    futures = ([s.strip() for s in args.futures.split(",") if s.strip()]
               if args.futures else list(SINA_FUTURES))
    secids = ([s.strip() for s in args.secids.split(",") if s.strip()]
              or list(DEFAULT_SECIDS))
    print(f"宏观序列回填：新浪期货 {len(futures)} 个 + 东财 {len(secids)} 个 / "
          f"lmt={args.lmt} / 输出 {OUT_PATH}")
    print(f"（失败重试 {_tries} 次、递增退避；东财对高频请求敏感）")

    series = load_existing()

    # ── 源 A：新浪外盘期货（与项目 macro.py 同源，稳定、无严格限流）──
    print("\n── 源 A：新浪外盘期货 ──")
    for sym in futures:
        print(f"[{sym}] 拉取…")
        got = fetch_sina_futures(sym, lmt=args.lmt)
        if not got:
            print(f"[{sym}] ✗ 失败（保留旧数据）")
            continue
        rows = got["rows"]
        series[f"sina:{sym}"] = {"name": got["name"], "rows": rows}
        print(f"[{sym}] ✓ {got['name']}：{len(rows)} 根 "
              f"{rows[0][0]} ~ {rows[-1][0]}（末值 {rows[-1][2]}）")

    # ── 源 B：东财（美债只有它能给）──
    print("\n── 源 B：东财 ──")
    for i, secid in enumerate(secids):
        if i:
            time.sleep(_INTERVAL)
        print(f"[{secid}] 拉取…")
        got = fetch_series(secid, lmt=args.lmt)
        if not got:
            print(f"[{secid}] ✗ 失败（保留旧数据）")
            continue
        rows = got["rows"]
        series[secid] = {"name": got["name"], "rows": rows}
        print(f"[{secid}] ✓ {got['name']}：{len(rows)} 根 "
              f"{rows[0][0]} ~ {rows[-1][0]}（末值 {rows[-1][2]}）")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    payload = {"fetched_at": datetime.now(timezone(timedelta(hours=8)))
               .strftime("%Y-%m-%d %H:%M:%S"),
               "series": series}
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    print(f"\n[out] 已写入 {OUT_PATH}（{len(series)} 个品种）")
    for secid, v in series.items():
        n = len(v.get("rows") or [])
        rng = (f"{v['rows'][0][0]} ~ {v['rows'][-1][0]}" if n else "-")
        print(f"  {secid:<12}{v.get('name', ''):<18}n={n:<6}{rng}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
