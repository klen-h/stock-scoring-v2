# -*- coding: utf-8 -*-
"""ETF 实时热度榜（盯盘计划 ETF 榜·方案 B，2026-11-08 拍板）——**行情展示非信号**（R2）。

口径（冻结 v1）：热度分 = volume_ratio × |change_pct| × sqrt(amount_wan)；
  白名单 **55 只**主流 ETF（`_DEFAULT_CODES` 内置冻结 + `data/etf_codes.json` **增补**，
  季度复核）；独立 60s 循环（仅交易时段），走腾讯同协议（ETF 代码直查，零新数据源）；
  无效代码（price≤0）自愈跳过。
**不可得（如实）**：IOPV/溢价率实时、ETF 主力资金流——榜单不含这两项。

★★ 2026-11-08 审查修正（数字与实现不符，同一文件里三种说法）：
  本注释原写「~120 只」、`_load_whitelist` 注释写「内置 120 只」、
  `etf_quote_loop` 写「200 只」，而 `len(_DEFAULT_CODES)` **实际是 55**。
  ⇒ 全部统一为实际值 55。**改白名单时请同步改这里**（三处曾各写一个数，改一处漏两处）。
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
    """内置白名单 + `data/etf_codes.json` **增补**（去重）。

    ★ 2026-11-08 审查修正：原实现 `codes = extra` 是**整表替换**，
      与文件头注释「可覆盖增补」不符 —— 一旦放了 override 文件，
      内置 55 只会被整体丢掉（只剩那份增补），且不会有任何提示。
      现改为**合并**（内置在前、增补在后、按代码去重）。
      ⚠️ 若确实需要"只用 override"（如临时缩到 10 只排查），
        请用空 list 之外的显式开关，不要靠"文件存在即替换"的隐式行为。
    """
    codes = list(_DEFAULT_CODES)
    try:
        if os.path.exists(_OVERRIDE):
            extra = json.load(open(_OVERRIDE, encoding="utf-8"))
            if isinstance(extra, list):
                codes = codes + [str(x).strip() for x in extra]
    except Exception as e:
        print(f"[etf_board] whitelist override failed: {e}")
    out, seen = [], set()
    for c in codes:
        c = str(c).strip()
        if len(c) == 6 and c.isdigit() and c not in seen:
            seen.add(c)
            out.append(_prefix(c))
    return out


def _prefix(code: str) -> str:
    """`510300` → `sh510300`；`159919` → `sz159919`（5/6 开头为沪市）。"""
    return ("sh" if code[0] in "56" else "sz") + code


_WH_CACHE = {"codes": None}


def _whitelist_codes() -> list:
    """白名单（进程内缓存；`board()` 每次请求都要给 `whitelist_n`，不必反复读文件）。"""
    if _WH_CACHE["codes"] is None:
        _WH_CACHE["codes"] = _load_whitelist()
    return _WH_CACHE["codes"]


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
    """榜单（读缓存；缓存空或过期 >90s 且交易中 ⇒ 现场补一轮）。

    ★ 2026-11-08 审查修正两处：
      1. **`as_of` 原来用 `datetime.now()`** —— 那是"接口被调用的时刻"，
         **不是数据时刻**。盘中上游挂了、缓存停在 4 小时前，`as_of` 仍显示"现在"，
         等于**对数据新鲜度撒谎**。改用缓存时间戳 `_TS`，并额外给出 `stale_sec`。
      2. **`refresh_once()` 的返回值原来被丢弃** —— 补数失败时静默用旧缓存。
         现记录失败原因，附在 `refresh_error` 里，前端/排障能看到。
    """
    global _TS
    refresh_error = ""
    if (not _QUOTES or time.time() - _TS > 90) and _trading_now():
        r = refresh_once()
        if not r.get("ok"):
            refresh_error = str(r.get("error") or "refresh failed")[:80]
    rows = []
    for q in _QUOTES.values():
        hot = q["volume_ratio"] * abs(q["change_pct"]) * math.sqrt(q["amount_wan"] or 1)
        rows.append({**q, "hot": round(hot, 2)})
    key = {"hot": lambda r: -r["hot"],
           "chg": lambda r: -r["change_pct"],
           "vr": lambda r: -r["volume_ratio"],
           "amt": lambda r: -r["amount_wan"]}.get(sort, lambda r: -r["hot"])
    rows.sort(key=key)
    stale = int(time.time() - _TS) if _TS else None
    return {"ok": bool(_QUOTES), "sort": sort,
            # as_of = **数据**时刻（最后一轮拉取成功的时间），不是请求时刻
            "as_of": (datetime.fromtimestamp(_TS, _BJ).strftime("%Y-%m-%d %H:%M")
                      if _QUOTES and _TS else ""),
            "stale_sec": stale,
            "is_stale": bool(stale is not None and stale > 180),
            "refresh_error": refresh_error,
            # ★ 2026-11-08：`n` 是"本次返回条数"，**不是白名单总数**。
            #   前端原来拿 `n` 显示"白名单 N 只"，周末/盘后缓存为空时会显示"0 只"，
            #   与事实（配置了 55 只）相反。这里单独给 `whitelist_n`。
            "whitelist_n": len(_whitelist_codes()),
            "n": len(rows), "rows": rows[:max(1, min(int(top or 20), 50))],
            "note": "热度分=量比×|涨跌|×√成交额（冻结 v1）；行情展示非信号；"
                    "不含溢价率/资金流（不可得，见方案文档）"}


def etf_quote_loop():
    """独立 60s 循环（仅交易时段；方案 B：白名单 **55 只**，不并入全市场刷新）。"""
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
