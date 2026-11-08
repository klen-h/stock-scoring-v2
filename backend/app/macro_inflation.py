# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】L1 通胀层判定（核心PCE）—— **纯观察器，不产生信号、不给仓位建议**
================================================================================
来源：`docs/plans/全球宏观周期判断与资产配置逻辑框架.md` §4.2 `inflation_layer`：
    `core_pce`: 「连续 2-3 个月回落，趋势指向 ≤2.8%」

数据源：金十日历接口，`dataId=1145458`（核心PCE物价指数**年率**，月频）。
  ⚠️ **不能用季度口径**（"年化季率"初/终/修正值、"年率初值/终值"都是季度）——
     见 `app/jin10_calendar.py` 文件头 §二的对照表。

--------------------------------------------------------------------------------
一、判定口径（把框架的自然语言落成可复算的规则）
--------------------------------------------------------------------------------
  `falls`    = 从最新月往回数，**连续下降**的月数
  `slope`    = 近 3 个月的月均变化（(最新 − 3个月前) / 3）
  `proj_2m`  = 最新 + slope × 2   ← "趋势指向"的落地：按当前斜率再走 2 个月
  `reached`  = `latest ≤ 2.8` **或** `proj_2m ≤ 2.8`
  判定通过   = `falls ≥ 2` **且** `reached`

  ★ 为什么"趋势指向"要外推 2 个月：框架原文是"趋势指向 ≤2.8%"，不是"已经 ≤2.8%"。
    若直接要求 latest≤2.8，则永远要等通胀**已经**达标才亮灯，失去前瞻性；
    但外推太远又会把"一路下行但离得很远"算成达标。取 2 个月是折中，且**写在明处可复核**。

--------------------------------------------------------------------------------
二、铁律：**"数据不足" ≠ "未达标"**
--------------------------------------------------------------------------------
  序列 < 4 个月 / 读表失败 ⇒ `status != "ok"`、`ok=False` 但**理由写明是"不足"**
  而不是"通胀没回落"。消费方不得把两者混为一谈（同 `sahm_rule` / `macro_regime_class`）。

--------------------------------------------------------------------------------
三、红线
--------------------------------------------------------------------------------
  纯观察器：不改分数、不进闸门、不碰模拟盘、**不输出任何仓位建议**。
================================================================================
"""
from datetime import datetime, timezone
from typing import Dict, List, Tuple

from app.database import db
from app.jin10_calendar import DATA_IDS, fetch_series

_TABLE = "macro_inflation"
_READY = False

SCORER_VERSION = "2026-11-08.1"

CORE_PCE_TARGET = 2.8          # 框架阈值
MIN_FALLS = 2                  # 「连续 2-3 个月回落」取下限 2
PROJECT_MONTHS = 2             # 「趋势指向」外推月数
MIN_POINTS = 4                 # falls 需要 ≥2 个月差 ⇒ 至少 3 个点；留 1 个冗余

# code → 中文名（进结果，便于前端/日志直接显示）
_CODE_CN = {
    "core_pce_yoy": "核心PCE年率",
    "core_pce_mom": "核心PCE月率",
}


def ensure_table() -> None:
    global _READY
    if _READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_TABLE} (
            code TEXT,
            date TEXT,
            value REAL,
            source TEXT,
            created_at TEXT,
            PRIMARY KEY (code, date)
        )
    """)
    _READY = True


# ── 纯函数核心（单测锚点）──────────────────────────────────────────────────

def evaluate_series(values: List[float],
                    target: float = CORE_PCE_TARGET,
                    min_falls: int = MIN_FALLS,
                    project_months: int = PROJECT_MONTHS) -> Dict:
    """对一条**升序**的月度数值序列做「连续回落 + 趋势指向」判定（纯函数）。

    返回：
      status      : `ok` | `insufficient`
      ok          : 是否判定通过（= 通胀层为真）—— **仅在 status=="ok" 时有意义**
      falls       : 连续回落月数
      latest      : 最新值
      slope       : 近 3 月月均变化
      proj        : 按斜率外推 `project_months` 个月后的值
      reached     : 是否"趋势指向 ≤ target"
      detail      : 说明文本
    """
    vals = [float(v) for v in (values or []) if v is not None]
    if len(vals) < MIN_POINTS:
        return {"status": "insufficient", "ok": False, "falls": 0,
                "latest": vals[-1] if vals else None,
                "detail": f"需要至少 {MIN_POINTS} 个月数据，当前 {len(vals)} 个月"}

    latest = vals[-1]

    falls = 0
    for i in range(len(vals) - 1, 0, -1):
        if vals[i] < vals[i - 1]:
            falls += 1
        else:
            break

    slope = (vals[-1] - vals[-4]) / 3.0
    proj = latest + slope * project_months
    reached = (latest <= target) or (proj <= target)
    passed = (falls >= min_falls) and reached

    if passed:
        detail = (f"核心PCE {latest:.2f}%，连续回落 {falls} 个月，"
                  f"按斜率 {slope:+.2f}/月 外推 {project_months} 月为 {proj:.2f}% "
                  f"（目标 ≤{target}）⇒ 通胀层达成")
    else:
        reasons = []
        if falls < min_falls:
            reasons.append(f"连续回落仅 {falls} 个月（需 ≥{min_falls}）")
        if not reached:
            reasons.append(f"最新 {latest:.2f}%、外推 {proj:.2f}% 均未指向 ≤{target}%")
        detail = "核心PCE " + f"{latest:.2f}%，" + "；".join(reasons) + " ⇒ 通胀层未达成"

    return {"status": "ok", "ok": bool(passed), "falls": falls,
            "latest": round(latest, 4), "slope": round(slope, 4),
            "proj": round(proj, 4), "reached": bool(reached),
            "target": target, "detail": detail,
            "scorer_version": SCORER_VERSION}


# ── 库读写 ─────────────────────────────────────────────────────────────────

def upsert_point(code: str, date: str, value: float,
                 source: str = "jin10_cal") -> None:
    ensure_table()
    db.execute(
        f"INSERT INTO {_TABLE} (code, date, value, source, created_at) "
        f"VALUES (%s,%s,%s,%s,%s) ON CONFLICT (code, date) DO UPDATE SET "
        f"value=EXCLUDED.value, source=EXCLUDED.source, "
        f"created_at=EXCLUDED.created_at",
        (str(code), str(date)[:10], float(value), str(source),
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )


def load_series(code: str = "core_pce_yoy", limit: int = 36
                ) -> List[Tuple[str, float]]:
    ensure_table()
    rows = db.fetch(
        f"SELECT date, value FROM {_TABLE} WHERE code = %s "
        f"ORDER BY date DESC LIMIT %s", (code, max(1, int(limit)))) or []
    out = [(str(r.get("date")), float(r.get("value")))
           for r in rows if r.get("value") is not None]
    out.sort(key=lambda x: x[0])
    return out


def refresh(codes: Tuple[str, ...] = ("core_pce_yoy", "core_pce_mom")) -> Dict:
    """从金十日历拉取并落库（幂等）。返回每码的落库条数。"""
    detail = {}
    total = 0
    for code in codes:
        did = DATA_IDS.get(code)
        if not did:
            detail[code] = {"ok": False, "saved": 0, "error": "未登记 dataId"}
            continue
        r = fetch_series(did)
        if not r.get("ok"):
            detail[code] = {"ok": False, "saved": 0, "error": r.get("error")}
            continue
        saved = 0
        for iso, v in r["points"]:
            try:
                upsert_point(code, iso, v)
                saved += 1
            except Exception as e:
                detail.setdefault(code, {})["error"] = str(e)[:60]
        detail[code] = {"ok": saved > 0, "saved": saved,
                        "latest": r["points"][-1][0] if r["points"] else None}
        total += saved
    return {"ok": total > 0, "saved": total, "detail": detail}


def view() -> Dict:
    """只读快照：核心PCE年率的判定结果。**不含任何操作建议**。"""
    try:
        series = load_series("core_pce_yoy")
    except Exception as e:
        return {"ok": False, "status": "invalid", "layer_ok": False,
                "detail": f"读取 {_TABLE} 失败: {str(e)[:80]}",
                "text": "通胀层不可用（读表失败）", "scorer_version": SCORER_VERSION}

    res = evaluate_series([v for _, v in series])
    res["as_of"] = series[-1][0] if series else None
    res["from"] = series[0][0] if series else None
    res["n_loaded"] = len(series)
    res["code"] = "core_pce_yoy"
    res["name"] = _CODE_CN["core_pce_yoy"]
    res["layer_ok"] = bool(res.get("ok"))
    res["text"] = ("通胀层达成：" if res.get("ok") else "通胀层未达成：") + res["detail"]
    return res


def brief() -> str:
    v = view()
    if v.get("status") == "ok":
        return (f"核心PCE {v['latest']:.2f}% 回落{v['falls']}月 "
                f"{'✅' if v['ok'] else '✗'}")
    return str(v.get("text"))[:40]
