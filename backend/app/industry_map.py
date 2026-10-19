# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】个股→行业映射的**唯一缓存入口**（egress 治理，2026-10-19）
================================================================================
问题（探针实测）：`SELECT code, main_industry FROM stock_industry`
（约 5.9 千行 ≈ 1MB/次）此前在 4 处各自整表读：
  · `mainline.compute_mainline`（日批 + 工作台主线卡）
  · `stock_moves.snapshot` 的行业聚合（日报/复盘）
  · `earnings_pressure._industry_of`（日批）
  · `sector_industry._industry_map`（板块成交额占比，**本来就有 1h 缓存**）
2026-10-18 13:11 → 10-19 21:28（32h）探针实测：**457 次调用 / 8.9 万行**
（≈11.6MB，按整行宽算的**上限**）——其中一半是重复的整表读。

口径：映射表由 `sector_industry.build_map*` **月级全量重建**，日内不变
⇒ 1 小时进程缓存足够；`invalidate()` 供同进程重建后立即失效。
失败返回空 dict（**不返回 None**，调用方都按空映射降级），且**不写缓存**
（否则一次抖动会把空映射钉住 1 小时）。
================================================================================
"""
import time
from typing import Dict

from app.database import db

_CACHE = {"ts": 0.0, "val": None}
_TTL = 3600.0


def get_industry_map() -> Dict[str, str]:
    """`{code: main_industry}`（最细子板块）。失败返回上次的/空 dict。"""
    now = time.time()
    cached = _CACHE.get("val")
    if cached is not None and now - _CACHE["ts"] < _TTL:
        return cached
    try:
        rows = db.fetch("SELECT code, main_industry FROM stock_industry") or []
        m = {str(r["code"]): str(r["main_industry"])
             for r in rows if r.get("code") and r.get("main_industry")}
        if m:
            _CACHE.update(ts=now, val=m)
        return m
    except Exception as e:
        print(f"[industry_map] load failed: {str(e)[:80]}")      # ASCII（铁律⑥）
        return cached or {}


def invalidate() -> None:
    """映射重建后调用（同进程立即生效）。"""
    _CACHE.update(ts=0.0, val=None)
