# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】全球 CPI 数据（金十 dynamic-data）—— **纯观察器，不做判定、不出信号**
================================================================================
来源：`docs/plans/全球宏观周期判断与资产配置逻辑框架.md` 审查后的补充（2026-11-08）。
框架的 L1 只有美国 PCE 系指标，**没有 CPI、也没有中国通胀**——
但对 A 股项目来说，**中国 CPI 比美国 CPI 更直接**（决定国内货币政策空间）。
本模块把 CPI 数据接进来，**只做展示与档位标签**，不参与任何判定。

--------------------------------------------------------------------------------
一、接口（2026-11-08 实测）
--------------------------------------------------------------------------------
  GET https://mp-api.jin10.com/api/dynamic-data/child
      ?order=date,desc|id,desc&limit=100&tb_name=_vir_58&search={"country":"中国"}&page=1
    headers: x-app-id: fiXF2nOnDycGutVA  /  x-version: 1.0   （**免 token**）

  单条：`{"id":6813, "date":"2026-09", "country":"美国", "cpi":3.7, "_date":"..."}`

  ★★ 两个实测要点（不按这个写会少拿很多数据）：
    1. **`limit` 服务端硬顶 100**（传 300/1000 都只回 100）——
       不筛选国家时 100 条只够 20 国 × 5 个月；
    2. **必须用 `search={"country":"中国"}` 单国筛选** ——
       这样 100 条能覆盖 **2018-06 ~ 2026-09（8 年）**，历史深度完全不同。

  ⚠️ `tb_name` 是内部表号：`_vir_58` = CPI。
     `_vir_57` = 信用评级（agency/rating/outlook），`_vir_59/60/61` 空。
     **PPI 没试出对应表**（试错成本高，不盲试）——有 tb_name 再加。

--------------------------------------------------------------------------------
二、★ 为什么"只做标签、不做判定"
--------------------------------------------------------------------------------
  框架的 L1 用的是**美联储口径（PCE）**，那是"美国通胀 → 美联储加不加息"的逻辑。
  中国 CPI 的逻辑完全不同：它是"**国内通胀 → 货币政策空间**"。
  两者阈值不可混用（中国 0.9% 是低通胀，美国 3.7% 是高通胀，用同一把尺子会判反）。

  ⇒ 本模块只输出：最新值、统计月、**档位标签**、以及最近 N 月走势。
    **不产出"达成/未达成"，不进 L1，不进任何决策链。**
    档位标签只描述事实（通缩 / 低通胀 / 温和 / 偏高），**不带操作含义**。

--------------------------------------------------------------------------------
三、红线
--------------------------------------------------------------------------------
  纯观察器：不改分数、不进闸门、不碰模拟盘、**不输出任何仓位或买卖建议**。
================================================================================
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import requests

from app.database import db

_API = "https://mp-api.jin10.com/api/dynamic-data/child"
_HEADERS = {
    "x-app-id": "fiXF2nOnDycGutVA",     # 与 jin10_tidy 同一个（**免 token**）
    "x-version": "1.0",
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.jin10.com/",
}
_TB_CPI = "_vir_58"

_TABLE = "macro_cpi"
_READY = False

SCORER_VERSION = "2026-11-08.1"

# 默认关注的 3 个经济体（中国最优先，美国用于与 PCE 对照，欧元区做外部参照）
DEFAULT_COUNTRIES = ("中国", "美国", "欧元区")

# 中国 CPI 档位（**仅描述事实，不含操作含义**；改这里要同步改文档）
_CN_BANDS = (
    (0.0, "通缩"),
    (1.0, "低通胀"),
    (3.0, "温和"),
    (99.0, "偏高"),
)


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            country TEXT,
            date TEXT,
            cpi REAL,
            source TEXT,
            created_at TEXT,
            PRIMARY KEY (country, date)
        )
    """)
    _READY = True


# ── 纯函数 ─────────────────────────────────────────────────────────────────

def band(cpi: Optional[float]) -> str:
    """CPI → 档位标签。只描述数值区间，**不暗示任何操作**。"""
    if cpi is None:
        return "无数据"
    v = float(cpi)
    for upper, name in _CN_BANDS:
        if v < upper:
            return name
    return "偏高"


def trend(values: List[float], n: int = 3) -> str:
    """最近 `n` 个月的走势：上升 / 下降 / 走平（**纯描述**）。"""
    vals = [float(v) for v in (values or []) if v is not None]
    if len(vals) < 2:
        return "数据不足"
    tail = vals[-min(n, len(vals)):]
    delta = tail[-1] - tail[0]
    if delta > 0.1:
        return "上升"
    if delta < -0.1:
        return "下降"
    return "走平"


# ── 取数与落库 ─────────────────────────────────────────────────────────────

def fetch_country(country: str, limit: int = 100,
                  timeout: float = 20.0) -> Dict:
    """拉某个国家/地区的 CPI 序列（单国筛选，最多 100 个月）。"""
    import json as _json

    params = {
        "order": "date,desc|id,desc",
        "limit": min(100, max(1, int(limit))),   # 服务端硬顶 100
        "tb_name": _TB_CPI,
        "search": _json.dumps({"country": country}, ensure_ascii=False),
        "page": 1,
    }
    try:
        r = requests.get(_API, params=params, headers=_HEADERS, timeout=timeout)
        j = r.json()
    except Exception as e:
        return {"ok": False, "points": [], "rows": 0,
                "error": f"请求失败: {str(e)[:90]}"}
    if str(j.get("status")) not in ("200", "0"):
        return {"ok": False, "points": [], "rows": 0,
                "error": f"status={j.get('status')} {str(j.get('message'))[:60]}"}

    rows = j.get("data") or []
    pts: Dict[str, float] = {}
    for x in rows:
        d = str(x.get("date") or "")[:7]        # "2026-09"
        v = x.get("cpi")
        if len(d) != 7 or v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        pts[d + "-01"] = fv

    points = sorted(pts.items(), key=lambda kv: kv[0])
    return {"ok": bool(points), "points": points, "rows": len(rows), "error": ""}


def upsert_point(country: str, date: str, cpi: float,
                 source: str = "jin10_dynamic") -> None:
    ensure_table()
    db.execute(
        f"INSERT INTO {_TABLE} (country, date, cpi, source, created_at) "
        f"VALUES (%s,%s,%s,%s,%s) ON CONFLICT (country, date) DO UPDATE SET "
        f"cpi=EXCLUDED.cpi, source=EXCLUDED.source, "
        f"created_at=EXCLUDED.created_at",
        (str(country), str(date)[:10], float(cpi), str(source),
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )


def refresh(countries: Tuple[str, ...] = DEFAULT_COUNTRIES) -> Dict:
    """拉取并落库（每国 1 请求，幂等）。失败不抛。"""
    detail = {}
    total = 0
    for c in countries:
        r = fetch_country(c)
        if not r.get("ok"):
            detail[c] = {"ok": False, "saved": 0, "error": r.get("error")}
            continue
        saved = 0
        for iso, v in r["points"]:
            try:
                upsert_point(c, iso, v)
                saved += 1
            except Exception:
                continue
        detail[c] = {"ok": saved > 0, "saved": saved,
                     "latest": r["points"][-1][0] if r["points"] else None}
        total += saved
    return {"ok": total > 0, "saved": total, "detail": detail}


def load_series(country: str, months: int = 36) -> List[Tuple[str, float]]:
    ensure_table()
    rows = db.fetch(
        f"SELECT date, cpi FROM {_TABLE} WHERE country = %s "
        f"ORDER BY date DESC LIMIT %s", (country, max(1, int(months)))) or []
    out = [(str(r.get("date")), float(r.get("cpi")))
           for r in rows if r.get("cpi") is not None]
    out.sort(key=lambda x: x[0])
    return out


def _one(country: str, months: int = 24) -> Dict:
    try:
        series = load_series(country, months)
    except Exception as e:
        return {"country": country, "ok": False, "error": str(e)[:80],
                "text": f"{country} CPI 读取失败"}
    if not series:
        return {"country": country, "ok": False,
                "text": f"{country} CPI 无数据（未接入或未刷新）"}
    vals = [v for _, v in series]
    latest = vals[-1]
    return {
        "country": country,
        "ok": True,
        "latest": round(latest, 4),
        "as_of": series[-1][0],
        "from": series[0][0],
        "n_loaded": len(series),
        "band": band(latest),
        "trend_3m": trend(vals, 3),
        "prev": round(vals[-2], 4) if len(vals) >= 2 else None,
        "series_tail": [{"date": d, "cpi": v} for d, v in series[-6:]],
        "text": (f"{country} CPI {latest:.2f}%（{series[-1][0][:7]}，"
                 f"{band(latest)}，近3月{trend(vals, 3)}）"),
    }


def view() -> Dict:
    """只读快照：中国 / 美国 / 欧元区 CPI。**不含任何操作建议**。"""
    cn = _one("中国")
    us = _one("美国")
    eu = _one("欧元区")
    return {
        "ok": any(x.get("ok") for x in (cn, us, eu)),
        "cn": cn,
        "us": us,
        "eu": eu,
        "note": ("纯观察：CPI 只做档位标签与走势描述，**不参与 L1 判定**"
                 "（L1 用美国核心PCE，美联储口径）。"
                 "中国与美国的通胀阈值不可混用——0.9% 在中国是低通胀、在美国是过低。"),
        "scorer_version": SCORER_VERSION,
    }


def brief() -> str:
    v = view()
    parts = []
    for k, label in (("cn", "中"), ("us", "美")):
        x = v.get(k) or {}
        parts.append(f"{label}{x['latest']:.1f}%" if x.get("ok") else f"{label}无")
    return "CPI " + "/".join(parts)
