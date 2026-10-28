# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】A 股交易日历 —— fuyao（同花顺）为权威源，手工 `HOLIDAYS` 降为兜底
================================================================================
【为什么单独一个模块】（2026-10-28 接入 fuyao）
  `flash/rules.py` 模块头写着「**纯函数、无 IO**」，而交易日历原来就是它里面的
  `HOLIDAYS` 手工字典（`{2026: [((1,1),(1,3),"元旦"), ...]}`）。问题：
    · 每年要人工录入，**漏一天就会让日批/信号打错日期**（调休年尤其容易错）；
    · 跨年时若不更新，`is_trading_day()` 会把节假日当交易日 ⇒ 日批空跑、数据缺日。
  现在把"取日历"的 IO 收在本模块，`rules.is_trading_day()` 只做**查询 + 回退**，
  仍然不破坏"rules 无 IO"的约定。
  fuyao 端点：`GET /api/a-share/calendar/trading-days`（单次最多 10 个自然年，
  实测默认返回近一年 243 个交易日）。

【纪律】
  1. **fail-open**：fuyao 不可用 / 返回条数异常 ⇒ 返回空集 ⇒ 调用方回退 `HOLIDAYS`；
  2. **异常护栏**：5 年窗口正常应有 ~1200 个交易日，**少于 800 视为异常**并丢弃
     （宁可回退手工日历，也不能拿一份残缺日历把交易日判成休市 —— 那会让日批整天不跑）；
  3. **进程内缓存 24h**：不落库（Render 重启不频繁；拉一次 ~0.2s，落库反而多一层迁移）；
  4. 只回答"是不是交易日"，**不判断盘中时段**（时段仍在 `rules.get_china_market_status`）。
================================================================================
"""
import threading
from datetime import timedelta
from typing import Optional, Set

from app.flash.rules import beijing_now

_LOCK = threading.Lock()
_CACHE = {"days": None, "ts": 0.0, "min": "", "max": ""}
_TTL = 86400.0          # 24h
_MIN_SANE = 800         # 5 年窗口的正常交易日下限（~1200），低于此判为异常
_YEARS_BACK = 5
_YEARS_AHEAD = 1


def _fetch() -> Set[str]:
    """拉一次日历（5 年前 ~ 1 年后）。返回空集表示不可用。"""
    from app import fuyao
    if not fuyao.enabled():
        return set()
    now = beijing_now()
    start = (now - timedelta(days=365 * _YEARS_BACK)).strftime("%Y%m%d")
    end = (now + timedelta(days=365 * _YEARS_AHEAD)).strftime("%Y%m%d")
    ds = set(fuyao.trading_days(start, end))
    if len(ds) < _MIN_SANE:
        print(f"[calendar] fuyao 交易日历异常（{len(ds)} 天 < {_MIN_SANE}），"
              f"回退手工 HOLIDAYS")           # ASCII（铁律⑥）
        return set()
    print(f"[calendar] 已加载 fuyao 交易日历: {len(ds)} 个交易日 "
          f"({min(ds)} ~ {max(ds)})")
    return ds


def days() -> Set[str]:
    """交易日集合（`YYYY-MM-DD`）。空集 = 不可用（调用方回退手工日历）。"""
    import time as _t
    with _LOCK:
        cached = _CACHE["days"]
        if cached is not None and _t.time() - _CACHE["ts"] < _TTL:
            return cached
    try:
        ds = _fetch()
    except Exception as e:
        print(f"[calendar] 拉取失败（回退手工 HOLIDAYS）: {str(e)[:80]}")
        ds = set()
    with _LOCK:
        _CACHE["days"] = ds
        _CACHE["ts"] = _t.time()
        _CACHE["min"] = min(ds) if ds else ""
        _CACHE["max"] = max(ds) if ds else ""
    return ds


def is_trading_day(d) -> Optional[bool]:
    """`True/False` 由 fuyao 日历给出；`None` = 无可用数据（调用方回退手工日历）。

    ⚠️ 落在日历覆盖范围之外（比如 10 年前的日期）也返回 `None` —— 不能因为
       "不在集合里"就断言它是休市日。
    """
    ds = str(d)[:10]
    cal = days()
    if not cal:
        return None
    if ds < _CACHE["min"] or ds > _CACHE["max"]:
        return None
    return ds in cal


def trading_days_between(start: str, end: str) -> list:
    """`[start, end]` 内的交易日列表（升序）。不可用返回 `[]`。"""
    cal = days()
    if not cal:
        return []
    s, e = str(start)[:10], str(end)[:10]
    return sorted(x for x in cal if s <= x <= e)


def reset_cache() -> None:
    """测试/运维用：清空进程内缓存。"""
    with _LOCK:
        _CACHE.update({"days": None, "ts": 0.0, "min": "", "max": ""})


def status() -> dict:
    """自检信息（供 `/system/fuyao-check` 类端点展示）。"""
    cal = _CACHE["days"]
    return {"loaded": cal is not None, "count": len(cal or []),
            "min": _CACHE["min"], "max": _CACHE["max"],
            "age_sec": (__import__("time").time() - _CACHE["ts"]) if _CACHE["ts"] else None}
