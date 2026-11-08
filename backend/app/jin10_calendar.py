# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】金十日历图表接口 · 通用取数层（宏观指标序列）
================================================================================
只做一件事：**按 dataId 拉取某个宏观指标的月度序列**，解析成 `(统计月, 值)`。
落库与判定由各业务模块自己做（`sahm_rule` / `macro_inflation` …）。

--------------------------------------------------------------------------------
一、接口（2026-11-08 实测）
--------------------------------------------------------------------------------
  GET https://e0430d16720e4211b5e072c26205c890.z3c.jin10.com
      /calendarGetSiteChartByDateRange?dataId={id}&gteDate={YYYYMMDD}&lteDate={YYYYMMDD}
    headers: x-app-id: sKKYe29sFuJaeOCJ   /  x-version: 2.0

  响应 `data` 是列表，单条：
    {"id":…, "time_period":"9月", "xx":"2025年 9月",
     "actual":"4.4", "revised":"4.5", "previous":"…",
     "pub_time":"2025-11-20 21:30:00", "year":"2025年"}

  实测耗时 0.1s；失业率 47 条 / 核心PCE 33 条（3 年窗口）。

★★ 两个已知陷阱（都会算错序列，务必按这里的做法）
  1. **`xx` 是统计月，`pub_time` 是发布时刻** —— 两者差约 1 个月。
     萨姆规则/PCE 趋势都必须用**统计月**，用 pub_time 会让整条序列平移。
  2. **`revised` 优先于 `actual`** —— 这些指标会被官方回溯修正。
     用 actual 会让同一个月在不同时点算出不同结果，**违反可复算**。
     （实测：失业率 2025-12 actual=4.4 / revised=4.5；核心PCE月率 2026-09 revised=0.3）
  3. **偶有月份真的空缺** —— 失业率 2025-10 因美国政府停摆未发布，**不是拉取失败**。
     这里如实返回，**不插值**；缺口由调用方决定如何处理并报告。

--------------------------------------------------------------------------------
二、已知的 dataId（按需扩充，改这里同时更新调用方）
--------------------------------------------------------------------------------
  | code | dataId | 指标 | 频率 |
  |---|---|---|---|
  | unemployment | 1178885 | 美国失业率（U3，季调） | 月 |
  | core_pce_yoy | 1145458 | 美国核心PCE物价指数**年率** | 月 |
  | core_pce_mom | 1145457 | 美国核心PCE物价指数**月率** | 月 |
  | pce_yoy      | 1145456 | 美国PCE物价指数年率（总量，非核心） | 月 |

  ⚠️ **别拿这些**（都是季度口径，不能做月度趋势）：
     1145454 / 1184925 / 1184108（核心PCE年化季率 初/终/修正）、
     1149747 / 1153682 / 115787x（核心PCE年率 终/初值，也是季度）。
     `title` 里带"年化季率"或"第N季度"的一律跳过。
================================================================================
"""
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests

_BASE = ("https://e0430d16720e4211b5e072c26205c890.z3c.jin10.com"
         "/calendarGetSiteChartByDateRange")
_HEADERS = {
    "x-app-id": "sKKYe29sFuJaeOCJ",     # ⚠️ 与 flash/jin10_tidy 的 fiXF2n… 不是同一套
    "x-version": "2.0",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.jin10.com/",
}

# 已核准的 dataId（见文件头 §二）
DATA_IDS = {
    "unemployment": 1178885,
    "core_pce_yoy": 1145458,
    "core_pce_mom": 1145457,
    "pce_yoy": 1145456,
}

_RE_XX = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月")


def period_iso(x: Dict) -> Optional[str]:
    """金十条目的统计月 → `YYYY-MM-01`。

    优先解析 `xx`（"2025年 9月"）；退到 `full_time_period`；再退到
    `year` + `time_period`。都失败返回 None（**不猜**）。
    """
    for field in ("xx", "full_time_period"):
        m = _RE_XX.search(str(x.get(field) or ""))
        if m:
            mo = int(m.group(2))
            if 1 <= mo <= 12:
                return "%s-%02d-01" % (m.group(1), mo)
    my = re.search(r"(\d{4})", str(x.get("year") or ""))
    mt = re.search(r"(\d{1,2})", str(x.get("time_period") or ""))
    if my and mt:
        mo = int(mt.group(1))
        if 1 <= mo <= 12:
            return "%s-%02d-01" % (my.group(1), mo)
    return None


def pick_value(x: Dict) -> Optional[float]:
    """取 `revised`（修正值）优先，无则 `actual`；无效返回 None。"""
    raw = x.get("revised")
    if raw in (None, "", "-"):
        raw = x.get("actual")
    if raw in (None, ""):
        return None
    try:
        v = float(str(raw).replace("%", "").strip())
    except ValueError:
        return None
    return v


def fetch_series(data_id: int, gte: str = None, lte: str = None,
                 timeout: float = 20.0) -> Dict:
    """拉取某指标 3 年序列，返回 `{ok, points:[(iso, value)], rows, error}`。

    `points` 已按日期**正序**、已做"revised 优先"，但**不做任何插值**。
    """
    if not gte:
        gte = (datetime.now() - timedelta(days=365 * 3)).strftime("%Y%m%d")
    if not lte:
        lte = datetime.now().strftime("%Y%m%d")

    try:
        r = requests.get(_BASE, params={"dataId": data_id,
                                        "gteDate": gte, "lteDate": lte},
                         headers=_HEADERS, timeout=timeout)
        j = r.json()
    except Exception as e:
        return {"ok": False, "points": [], "rows": 0,
                "error": f"请求失败: {str(e)[:90]}"}

    if str(j.get("status")) not in ("200", "0"):
        return {"ok": False, "points": [], "rows": 0,
                "error": f"status={j.get('status')} {str(j.get('message'))[:60]}"}

    rows = j.get("data") or []
    pts: List[Tuple[str, float]] = []
    for x in rows:
        iso = period_iso(x)
        if not iso:
            continue
        val = pick_value(x)
        if val is None:
            continue
        pts.append((iso, val))

    # 同月重复（修订版本）时保留最后一条
    dedup: Dict[str, float] = {}
    for iso, v in pts:
        dedup[iso] = v
    points = sorted(dedup.items(), key=lambda kv: kv[0])

    return {"ok": bool(points), "points": points, "rows": len(rows), "error": ""}
