# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】环境传导链（宏观 → 市场 → 板块 → 个股 的自上而下合成）
================================================================================

解决的问题：五层数据（宏观/市场/板块/个股/战法）各自为政——
  战法推送"均线回踩买入 XX"时，不知道当日宏观方向利空、
  不知道该股所属板块资金正在流出。信号在各说各的。

本模块把四层各折成一个方向分并合成"环境一致度"：

  宏观层  direction.score ≥+30 利多 +2 ｜ ≤-30 利空 -2 ｜ 其余中性 0
          （macro_daily 08:55 锁定的规则引擎方向分）
  市场层  offensive +2 ｜ neutral +1 ｜ neutral_bearish -1 ｜ defensive -2
  板块层  所属行业当日主力净流入 >0 → +1 ｜ <0 → -1；在当日主线名单 → 额外 +1
          （sector_daily 15:10 板块快照 + industry_mainline 主线识别）
  个股层  mainforce_state 信号：吸筹 +2 ｜ 出货 -2 ｜ 其余 0

  合成 sum(-7~+7)：
    ≥+4  顺流共振（四层同向，"大胆"的最高依据）
    +2~+3 偏多   |  -1~+1 中性拉锯  |  -2~-3 偏逆
    ≤-4  逆流共振（四层同向向下，信号一律降权）

数据全部为盘后落库（板块资金 15:10、宏观 8:55、状态 15:40）→ 推送/日报场景
天然同日同口径。环境快照 30 分钟进程缓存。
================================================================================
"""

import json
import time
from typing import Dict, Optional

_VERDICTS = [
    (4, "顺流共振"), (2, "偏多"), (-1, "中性拉锯"),
    (-3, "偏逆"), (-99, "逆流共振"),
]


def _verdict(total: int) -> str:
    for th, name in _VERDICTS:
        if total >= th:
            return name
    return "中性拉锯"


_env_cache = {"ts": 0.0, "snap": None}


def _env_snapshot(max_age: float = 1800) -> dict:
    """环境快照（30min 缓存）：宏观方向分 / regime / 板块资金表 / 主线名单。"""
    now = time.time()
    if _env_cache["snap"] and now - _env_cache["ts"] < max_age:
        return _env_cache["snap"]

    snap = {"macro_score": None, "macro_level": None,
            "regime": None, "sector_flow": {}, "mainlines": set()}

    # 1. 宏观方向分
    try:
        r = db_fetch_one("SELECT data_json FROM macro_daily ORDER BY date DESC LIMIT 1")
        if r and r.get("data_json"):
            d = json.loads(r["data_json"]) if isinstance(r["data_json"], str) else r["data_json"]
            direction = d.get("direction") or {}
            snap["macro_score"] = direction.get("score")
            snap["macro_level"] = direction.get("level")
    except Exception:
        pass

    # 2. 市场状态
    try:
        from app.backtest.market_regime import get_regime_cache, restore_regime_cache_from_db
        cache = get_regime_cache() or {}
        if not cache.get("state"):
            restore_regime_cache_from_db()
            cache = get_regime_cache() or {}
        snap["regime"] = cache.get("state")
    except Exception:
        pass

    # 3. 板块当日主力净流入（亿元）+ 主线名单
    try:
        rows = db.fetch("SELECT name, net_inflow FROM sector_daily "
                        "WHERE date=(SELECT MAX(date) FROM sector_daily) AND kind='industry'")
        for r in rows or []:
            if r.get("net_inflow") is not None:
                snap["sector_flow"][r["name"]] = float(r["net_inflow"]) / 1e8
    except Exception:
        pass
    try:
        rows = db.fetch("SELECT industry FROM industry_mainline "
                        "WHERE date=(SELECT MAX(date) FROM industry_mainline)")
        snap["mainlines"] = {r["industry"] for r in (rows or []) if r.get("industry")}
    except Exception:
        pass

    _env_cache["ts"] = now
    _env_cache["snap"] = snap
    return snap


def db_fetch_one(sql, params=None):
    from app.database import db
    return db.fetch_one(sql, params)


def _industry_of(code: str) -> Optional[str]:
    try:
        r = db_fetch_one("SELECT main_industry FROM stock_industry WHERE code = %s",
                         (code,))
        return (r or {}).get("main_industry")
    except Exception:
        return None


def confluence_for_stock(code: str, mf: Dict = None, fresh: dict = None) -> Dict:
    """
    个股环境传导链。
    mf: mainforce_state.load_latest([code])[code]（调用方可批量传入）。
    返回 {
      layers: {macro: ±2/0, market: ±2/±1/0, sector: ±1/0, stock: ±2/0},
      total, verdict(顺流共振/偏多/中性拉锯/偏逆/逆流共振),
      reasons: [每层一句], sector(行业名), in_mainline
    }
    """
    snap = fresh if fresh is not None else _env_snapshot()
    if mf is None:
        try:
            from app.mainforce.state import load_latest
            mf = load_latest([code]).get(code) or {}
        except Exception:
            mf = {}

    layers, reasons = {}, []

    # 宏观
    ms = snap.get("macro_score")
    if ms is not None:
        layers["macro"] = 2 if ms >= 30 else (-2 if ms <= -30 else 0)
        reasons.append(f"宏观{snap.get('macro_level') or '中性'}({ms:+.0f})")
    else:
        layers["macro"] = 0
        reasons.append("宏观未知")

    # 市场
    regime = snap.get("regime")
    market_map = {"offensive": 2, "neutral": 1, "neutral_bearish": -1, "defensive": -2}
    layers["market"] = market_map.get(regime, 0)
    regime_cn = {"offensive": "进攻", "neutral": "震荡", "neutral_bearish": "震荡偏空",
                 "defensive": "防御"}
    reasons.append(f"市场{regime_cn.get(regime, regime or '未知')}")

    # 板块
    industry = _industry_of(code)
    flow = snap["sector_flow"].get(industry) if industry else None
    in_mainline = industry in snap["mainlines"] if industry else False
    sector_score = 0
    if flow is not None and flow != 0:
        sector_score += 1 if flow > 0 else -1
    if in_mainline:
        sector_score += 1
    layers["sector"] = sector_score
    flow_txt = f"{flow:+.1f}亿" if flow is not None else "未知"
    reasons.append(f"板块[{industry or '未知'}]资金{flow_txt}"
                   + ("、在主线" if in_mainline else ""))

    # 个股
    signal = mf.get("signal")
    stock_score = 2 if signal == "accum" else (-2 if signal == "distribution" else 0)
    layers["stock"] = stock_score
    reasons.append("个股吸筹" if signal == "accum"
                   else ("个股出货" if signal == "distribution" else "个股中性"))

    total = sum(layers.values())
    return {
        "code": code,
        "layers": layers,
        "total": total,
        "verdict": _verdict(total),
        "sector": industry,
        "in_mainline": in_mainline,
        "reasons": reasons,
    }


def confluence_line(result: Dict) -> str:
    """合成一行（推送/日报复用）：环境链 宏观↓市场→板块↑个股↑ = 偏多(2/4 顺向)"""
    layers = result["layers"]
    arrow = {2: "↑↑", 1: "↑", 0: "→", -1: "↓", -2: "↓↓"}
    chain = "｜".join(f"{name}{arrow.get(layers.get(k, 0), '→')}"
                      for name, k in [("宏观", "macro"), ("市场", "market"),
                                      ("板块", "sector"), ("个股", "stock")])
    if result["verdict"] == "中性拉锯":
        return f"环境链：{chain} = 中性拉锯"
    aligned = sum(1 for v in layers.values()
                  if (v > 0) == (result["total"] > 0) and v != 0)
    return f"环境链：{chain} = {result['verdict']}（{aligned}/4 层同向）"
