# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】价格类宏观指标（CPI / PPI）—— **纯观察器，不判定、不出信号**
================================================================================
来源：`docs/plans/全球宏观周期判断与资产配置逻辑框架.md` 审查后的补充（2026-11-08）。
框架的 L1 只有美国 PCE 系指标，**没有 CPI、也没有中国通胀**——
但对 A 股项目来说，**中国 CPI/PPI 比美国 CPI 更直接**。
本模块把价格数据接进来，**只做展示、标签与剪刀差描述**，不参与任何判定。

--------------------------------------------------------------------------------
一、两个数据源（不同接口，别搞混）
--------------------------------------------------------------------------------
  **CPI**（中/美/欧，多国）：
    GET mp-api.jin10.com/api/dynamic-data/child
        ?tb_name=_vir_58&limit=100&search={"country":"中国"}&page=1
    headers: x-app-id: fiXF2nOnDycGutVA / x-version: 1.0   （**免 token**）
    ★★ `limit` 服务端**硬顶 100**；**必须按国家筛选**才有 8 年历史
       （不筛选时 100 条只够 20 国 × 5 个月）。

  **PPI**（中国，同比）：
    GET e0430d16720e4211b5e072c26205c890.z3c.jin10.com
        /calendarGetSiteChartByDateRange?dataId=1186780
    headers: x-app-id: sKKYe29sFuJaeOCJ / x-version: 2.0
    ⇒ 走 `app/jin10_calendar.py` 的通用层（revised 优先 / 统计月 / 缺口不插值）。

  ⚠️ `dynamic-data` 里**没试出 PPI 的 tb_name**（`_vir_57` 是信用评级、`_vir_59~61` 空），
     所以 PPI 只能走 calendar 接口拿**中国**的；其他国家的 PPI 暂无源。

--------------------------------------------------------------------------------
二、★ 为什么"只做标签，不做判定"
--------------------------------------------------------------------------------
  框架 L1 用的是**美联储口径（PCE）**：美国通胀 → 美联储加不加息。
  中国的逻辑完全不同：

    | 指标 | 逻辑 | 关键点 |
    |---|---|---|
    | 中国 CPI | **居民端 / 货币政策空间** | 0.9% 在中国是低通胀 |
    | 中国 PPI | **工业端 / 上游企业盈利** | 5.0% 是明显再通胀 |
    | 美国 CPI | 美联储参考 | 3.7% 是**偏高** |

  **同一把尺子会把结论判反**（中国 0.9% 低、美国 3.7% 高）。
  ⇒ 本模块只输出：最新值、统计月、**档位标签**、走势、以及 **PPI-CPI 剪刀差**。
    **不产出"达成/未达成"，不进 L1，不进任何决策链。**

--------------------------------------------------------------------------------
三、PPI-CPI 剪刀差（本模块最有信息量的产出）
--------------------------------------------------------------------------------
  `scissors = PPI − CPI`（两者都是同比，可直接相减）

  · `> 0` 上游价格强于下游 —— 成本在涨但下游传导有限
  · `> 0 且扩大` —— 上游挤压下游更明显
  · `< 0` 上游弱于下游

  ⚠️ **只描述这个事实，不给行业配置建议**。
     "利好资源股/利空消费"这类判断需要预登记验证，不属于观察器职责。

--------------------------------------------------------------------------------
四、红线
--------------------------------------------------------------------------------
  纯观察器：不改分数、不进闸门、不碰模拟盘、**不输出任何仓位或买卖建议**。
================================================================================
"""
import json as _json
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

from app.database import db

# ── CPI 源（dynamic-data）──────────────────────────────────────────────────
_CPI_API = "https://mp-api.jin10.com/api/dynamic-data/child"
_CPI_HEADERS = {
    "x-app-id": "fiXF2nOnDycGutVA",     # 与 jin10_tidy 同一个（**免 token**）
    "x-version": "1.0",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.jin10.com/",
}
_TB_CPI = "_vir_58"

_TABLE = "macro_price"
_READY = False

SCORER_VERSION = "2026-11-08.1"

DEFAULT_CPI_COUNTRIES = ("中国", "美国", "欧元区")

# 中国 CPI 档位（**仅描述数值区间，不含操作含义**）
_CN_CPI_BANDS = (
    (0.0, "通缩"),
    (1.0, "低通胀"),
    (3.0, "温和"),
    (99.0, "偏高"),
)
# 中国 PPI 档位（同比；负=工业品通缩）
_CN_PPI_BANDS = (
    (0.0, "工业品通缩"),
    (2.0, "温和回升"),
    (5.0, "明显上行"),
    (99.0, "高位"),
)

# ★ 剪刀差"大"的阈值（pp）。**经验值，无公认标准** —— 改这里，`scissors_text()`
#   与 `macro_cycle._narrative()` 会同时生效（原先是两处各写一个 3，改一处漏一处）。
SCISSORS_BIG = 3.0


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            country TEXT,
            indicator TEXT,
            date TEXT,
            value REAL,
            source TEXT,
            created_at TEXT,
            PRIMARY KEY (country, indicator, date)
        )
    """)
    _READY = True


# ── 纯函数 ─────────────────────────────────────────────────────────────────

def _band(v: Optional[float], bands) -> str:
    if v is None:
        return "无数据"
    x = float(v)
    for upper, name in bands:
        if x < upper:
            return name
    return bands[-1][1]


def band_cpi(cpi: Optional[float]) -> str:
    """CPI → 档位。只描述区间，**不暗示操作**。"""
    return _band(cpi, _CN_CPI_BANDS)


def band_ppi(ppi: Optional[float]) -> str:
    """PPI → 档位。负值即工业品通缩。"""
    return _band(ppi, _CN_PPI_BANDS)


def trend(values: List[float], n: int = 3) -> str:
    """最近 `n` 个月走势：上升 / 下降 / 走平（**纯描述**）。"""
    vals = [float(v) for v in (values or []) if v is not None]
    if len(vals) < 2:
        return "数据不足"
    tail = vals[-min(n, len(vals)):]
    delta = tail[-1] - tail[0]
    if delta > 0.15:
        return "上升"
    if delta < -0.15:
        return "下降"
    return "走平"


def scissors(ppi: Optional[float], cpi: Optional[float]) -> Optional[float]:
    """PPI − CPI 剪刀差（pp）。任一缺失返回 None（**不猜**）。"""
    if ppi is None or cpi is None:
        return None
    return round(float(ppi) - float(cpi), 4)


def scissors_text(sc: Optional[float]) -> str:
    """剪刀差的人话描述（**只说事实**）。"""
    if sc is None:
        return "剪刀差无数据"
    if sc > SCISSORS_BIG:
        return f"剪刀差 {sc:+.2f}pp：上游明显强于下游"
    if sc > 0:
        return f"剪刀差 {sc:+.2f}pp：上游略强于下游"
    if sc > -SCISSORS_BIG:
        return f"剪刀差 {sc:+.2f}pp：下游略强于上游"
    return f"剪刀差 {sc:+.2f}pp：下游明显强于上游"


# ── 取数与落库 ─────────────────────────────────────────────────────────────

def fetch_cpi(country: str, limit: int = 100,
              timeout: float = 20.0) -> Dict:
    """拉某国 CPI（单国筛选，最多 100 个月）。"""
    params = {
        "order": "date,desc|id,desc",
        "limit": min(100, max(1, int(limit))),   # 服务端硬顶 100
        "tb_name": _TB_CPI,
        "search": _json.dumps({"country": country}, ensure_ascii=False),
        "page": 1,
    }
    try:
        j = requests.get(_CPI_API, params=params, headers=_CPI_HEADERS,
                         timeout=timeout).json()
    except Exception as e:
        return {"ok": False, "points": [], "error": f"请求失败: {str(e)[:90]}"}
    if str(j.get("status")) not in ("200", "0"):
        return {"ok": False, "points": [],
                "error": f"status={j.get('status')} {str(j.get('message'))[:60]}"}

    pts: Dict[str, float] = {}
    for x in j.get("data") or []:
        d = str(x.get("date") or "")[:7]
        v = x.get("cpi")
        if len(d) != 7 or v is None:
            continue
        try:
            pts[d + "-01"] = float(v)
        except (TypeError, ValueError):
            continue
    points = sorted(pts.items(), key=lambda kv: kv[0])
    return {"ok": bool(points), "points": points, "error": ""}


def upsert_point(country: str, indicator: str, date: str, value: float,
                 source: str = "jin10") -> None:
    ensure_table()
    db.execute(
        f"INSERT INTO {_TABLE} (country, indicator, date, value, source, created_at) "
        f"VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (country, indicator, date) DO UPDATE SET "
        f"value=EXCLUDED.value, source=EXCLUDED.source, created_at=EXCLUDED.created_at",
        (str(country), str(indicator), str(date)[:10], float(value), str(source),
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )


def load_series(country: str, indicator: str = "cpi",
                months: int = 36) -> List[Tuple[str, float]]:
    ensure_table()
    rows = db.fetch(
        f"SELECT date, value FROM {_TABLE} WHERE country = %s AND indicator = %s "
        f"ORDER BY date DESC LIMIT %s",
        (country, indicator, max(1, int(months)))) or []
    out = [(str(r.get("date")), float(r.get("value")))
           for r in rows if r.get("value") is not None]
    out.sort(key=lambda x: x[0])
    return out


def refresh_cpi(countries: Tuple[str, ...] = DEFAULT_CPI_COUNTRIES) -> Dict:
    """拉 CPI 并落库（每国 1 请求）。失败不抛。"""
    detail, total = {}, 0
    for c in countries:
        r = fetch_cpi(c)
        if not r.get("ok"):
            detail[c] = {"ok": False, "saved": 0, "error": r.get("error")}
            continue
        saved = 0
        for iso, v in r["points"]:
            try:
                upsert_point(c, "cpi", iso, v, source="jin10_dynamic")
                saved += 1
            except Exception:
                continue
        detail[c] = {"ok": saved > 0, "saved": saved,
                     "latest": r["points"][-1][0] if r["points"] else None}
        total += saved
    return {"ok": total > 0, "saved": total, "detail": detail}


def refresh_ppi() -> Dict:
    """拉中国 PPI（金十日历 dataId=1186780，同比）并落库。"""
    from app.jin10_calendar import DATA_IDS, fetch_series

    r = fetch_series(DATA_IDS["cn_ppi_yoy"])
    if not r.get("ok"):
        return {"ok": False, "saved": 0, "error": r.get("error")}
    saved = 0
    latest = None
    for iso, v in r["points"]:
        if not (-30 < v < 30):          # PPI 同比合理性护栏
            continue
        try:
            upsert_point("中国", "ppi", iso, v, source="jin10_cal")
            saved += 1
            latest = iso
        except Exception:
            continue
    return {"ok": saved > 0, "saved": saved, "latest": latest,
            "rows": r.get("rows") or 0}


def refresh() -> Dict:
    """一次刷完 CPI（中/美/欧）+ 中国 PPI。"""
    c = refresh_cpi()
    p = refresh_ppi()
    return {"ok": bool(c.get("ok") or p.get("ok")),
            "cpi": c, "ppi": p,
            "saved": (c.get("saved") or 0) + (p.get("saved") or 0)}


# ── 视图 ───────────────────────────────────────────────────────────────────

def _series_view(country: str, indicator: str, bands,
                 months: int = 24) -> Dict:
    try:
        series = load_series(country, indicator, months)
    except Exception as e:
        return {"country": country, "indicator": indicator, "ok": False,
                "error": str(e)[:80]}
    if not series:
        return {"country": country, "indicator": indicator, "ok": False,
                "text": f"{country} {indicator.upper()} 无数据"}

    vals = [v for _, v in series]
    latest = vals[-1]
    return {
        "country": country,
        "indicator": indicator,
        "ok": True,
        "latest": round(latest, 4),
        "as_of": series[-1][0],
        "from": series[0][0],
        "n_loaded": len(series),
        "band": _band(latest, bands),
        "trend_3m": trend(vals, 3),
        "prev": round(vals[-2], 4) if len(vals) >= 2 else None,
        "series_tail": [{"date": d, "value": v} for d, v in series[-6:]],
        "text": (f"{country} {indicator.upper()} {latest:.2f}%"
                 f"（{series[-1][0][:7]}，{_band(latest, bands)}，"
                 f"近3月{trend(vals, 3)}）"),
    }


def view() -> Dict:
    """只读快照：中/美/欧 CPI + 中国 PPI + 剪刀差。**不含任何操作建议**。"""
    cn_cpi = _series_view("中国", "cpi", _CN_CPI_BANDS)
    us_cpi = _series_view("美国", "cpi", _CN_CPI_BANDS)
    eu_cpi = _series_view("欧元区", "cpi", _CN_CPI_BANDS)
    cn_ppi = _series_view("中国", "ppi", _CN_PPI_BANDS)

    sc = scissors(cn_ppi.get("latest") if cn_ppi.get("ok") else None,
                  cn_cpi.get("latest") if cn_cpi.get("ok") else None)

    return {
        "ok": any(x.get("ok") for x in (cn_cpi, us_cpi, eu_cpi, cn_ppi)),
        "cn_cpi": cn_cpi,
        "us_cpi": us_cpi,
        "eu_cpi": eu_cpi,
        "cn_ppi": cn_ppi,
        "scissors_pp": sc,
        "scissors_text": scissors_text(sc),
        "note": ("纯观察：CPI/PPI 只做档位标签与走势描述，**不参与 L1 判定**"
                 "（L1 用美国核心PCE，美联储口径）。"
                 "中国与美国通胀阈值不可混用；剪刀差也只描述事实，"
                 "不给行业配置建议。"),
        "scorer_version": SCORER_VERSION,
    }


def brief() -> str:
    """一行摘要（**不带"价格"前缀** —— 调用方自己加，避免"价格: 价格 …"重复）。"""
    v = view()
    cn = v.get("cn_cpi") or {}
    us = v.get("us_cpi") or {}
    ppi = v.get("cn_ppi") or {}
    parts = []
    parts.append(f"中CPI{cn['latest']:.1f}%" if cn.get("ok") else "中CPI无")
    parts.append(f"中PPI{ppi['latest']:.1f}%" if ppi.get("ok") else "中PPI无")
    parts.append(f"美CPI{us['latest']:.1f}%" if us.get("ok") else "美CPI无")
    return "/".join(parts)
