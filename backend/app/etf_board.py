# -*- coding: utf-8 -*-
"""ETF 实时热度榜（盯盘计划 ETF 榜·方案 B，2026-11-08 拍板）——**行情展示非信号**（R2）。

口径（冻结 v1）：热度分 = volume_ratio × |change_pct| × sqrt(amount_wan)；
  白名单 ~120 只主流 ETF（内置冻结 + `data/etf_codes.json` 可覆盖增补，季度复核）；
  独立 60s 循环（仅交易时段），走腾讯同协议（ETF 代码直查，零新数据源）；
  无效代码（price≤0）自愈跳过。
**不可得（如实）**：IOPV/溢价率实时、ETF 主力资金流——榜单不含这两项。
"""
import json
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone

from app.database import db  # noqa: F401  (保持与其它观察器同形；当前零库依赖)

_BJ = timezone(timedelta(hours=8))

# 内置白名单 v1（主流宽基/行业/跨境/商品；无效代码由循环自愈跳过；data/etf_codes.json 可覆盖）
_DEFAULT_CODES = [
    # 宽基
    "510050", "510300", "510310", "510330", "510500", "510550", "510880",
    "588000", "588080", "588200", "159915", "159949", "159952", "159922",
    "159919", "159901", "512100", "159781",
    # 行业/主题
    "512000", "512880", "512800", "512690", "512170", "512660", "512480",
    "159995", "515790", "515030", "512400", "515220", "515210", "515880",
    "159870", "515875", "159996", "512760", "512010", "515700", "159806",
    "512290", "516160", "515050", "512720", "159605", "561980",
    # 跨境/商品/债券
    "513100", "513050", "513180", "513500", "513030", "518880", "511260",
    "511010", "511090", "511380",
]
_OVERRIDE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "data", "etf_codes.json")

_QUOTES = {}          # {code: {code,name,price,change_pct,volume_ratio,turnover_rate,amount_wan}}
_TS = 0.0
_LOCK = threading.Lock()
_READY = False


def _load_whitelist() -> list:
    codes = list(_DEFAULT_CODES)
    try:
        if os.path.exists(_OVERRIDE):
            extra = json.load(open(_OVERRIDE, encoding="utf-8"))
            if isinstance(extra, list):
                codes = extra
    except Exception as e:
        print(f"[etf_board] whitelist override failed: {e}")
    out, seen = [], set()
    for c in codes:
        c = str(c).strip()
        if len(c) == 6 and c.isdigit() and c not in seen:
            seen.add(c)
            out.append(("sh" if c[0] in "56" else "sz") + c)
    return out


def _prefix(code: str) -> str:
    return ("sh" if code[0] in "56" else "sz") + code


def refresh_once() -> dict:
    """拉一轮白名单行情（腾讯同协议，2~3 批）；写入 _QUOTES。失败保留旧值。"""
    global _QUOTES, _TS
    with _LOCK:
        codes = _load_whitelist()
        out = {}
        try:
            from app.tencent import _fetch_tencent
            B = 80
            for i in range(0, len(codes), B):
                batch = codes[i:i + B]
                data = _fetch_tencent(",".join(batch), timeout=15) or {}
                for qt, s in data.items():
                    c = str(s.get("code") or "")[-6:]
                    price = float(s.get("price") or 0)
                    if not c or price <= 0:
                        continue                    # 无效代码自愈跳过
                    out[c] = {"code": c, "name": s.get("name") or c,
                              "price": price,
                              "change_pct": float(s.get("change_pct") or 0),
                              "volume_ratio": float(s.get("volume_ratio") or 0),
                              "turnover_rate": float(s.get("turnover_rate") or 0),
                              "amount_wan": float(s.get("amount_wan") or 0)}
        except Exception as e:
            print(f"[etf_board] refresh failed (keep old): {e}")
            return {"ok": False, "n": len(_QUOTES), "error": str(e)[:80]}
        if out:
            _QUOTES = out
            _TS = time.time()
        return {"ok": True, "n": len(_QUOTES), "ts": _TS}


def _trading_now() -> bool:
    try:
        from app.flash import rules
        return bool(rules.get_china_market_status().get("is_open"))
    except Exception:
        return False


def board(sort: str = "hot", top: int = 20) -> dict:
    """榜单（读缓存；缓存空或过期 >90s 且交易中 ⇒ 现场补一轮）。"""
    global _TS
    if (not _QUOTES or time.time() - _TS > 90) and _trading_now():
        refresh_once()
    rows = []
    for q in _QUOTES.values():
        hot = q["volume_ratio"] * abs(q["change_pct"]) * math.sqrt(q["amount_wan"] or 1)
        rows.append({**q, "hot": round(hot, 2)})
    key = {"hot": lambda r: -r["hot"],
           "chg": lambda r: -r["change_pct"],
           "vr": lambda r: -r["volume_ratio"],
           "amt": lambda r: -r["amount_wan"]}.get(sort, lambda r: -r["hot"])
    rows.sort(key=key)
    return {"ok": bool(_QUOTES), "sort": sort, "as_of": datetime.now(_BJ).strftime(
        "%Y-%m-%d %H:%M") if _QUOTES else "",
        "n": len(rows), "rows": rows[:max(1, min(int(top or 20), 50))],
        "note": "热度分=量比×|涨跌|×√成交额（冻结 v1）；行情展示非信号；"
                "不含溢价率/资金流（不可得，见方案文档）"}


def etf_quote_loop():
    """独立 60s 循环（仅交易时段；方案 B：白名单 200 只，不并入全市场刷新）。"""
    while True:
        try:
            if _trading_now():
                t0 = time.time()
                r = refresh_once()
                if not r.get("ok"):
                    print(f"[etf_board] refresh failed: {r.get('error')}")
                elif time.time() - t0 > 20:
                    print(f"[etf_board] refresh slow: {time.time() - t0:.1f}s")
        except Exception as e:
            print(f"[etf_board] loop: {e}")
        time.sleep(60)
