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
          ★ 2026-09-13：**拥挤主线不给 +1**（只减不加）—— 回测显示拥挤主线候选
             T+5 均收益 -1.41% vs 不拥挤 -0.29%，是"涨太多"的末端陷阱
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
            "regime": None, "sector_flow": {}, "mainlines": set(),
            "crowded_mainlines": set()}

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
        from app.sector_industry import _normalize_industry
        rows = db_fetch("SELECT name, net_inflow FROM sector_daily "
                        "WHERE date=(SELECT MAX(date) FROM sector_daily) AND kind='industry'")
        # ★ 2026-09-13 修复（第二处）：sector_daily 的 name 是**东财细分板块名**
        #   （如"通信线缆及配套/地面兵装Ⅲ"），而传给本函数的 industry 来自
        #   stock_industry.main_industry（已归一化为**新浪一级**"电子信息/飞机制造"）
        #   → 直接用东财名做 key 永远匹配不上、资金层恒为"未知"。这里统一归一化，
        #   同一新浪一级下的多个细分板块净流入**累加**。
        # ★ 2026-09-13 走查修复（第三处）：「其它行业」是兜底桶——聚合了 156 个无法识别
        #   的东财板块（实测净流入 -250 亿，占 |总额| 16%），而 stock_industry 里有 687 只
        #   个股归在该桶 → 会给它们**系统性打 -1**（兜底副作用，非真实信号）。跳过它，
        #   这些个股退回"资金未知"（sector_score=0 中性），与 industry_mainline 的
        #   "其它行业不参与主线判定"同一惯例。
        agg = {}
        for r in rows or []:
            if r.get("net_inflow") is None:
                continue
            nf = _normalize_industry(r["name"])
            if nf == "其它行业":
                continue
            agg[nf] = agg.get(nf, 0.0) + float(r["net_inflow"]) / 1e8
        snap["sector_flow"] = agg
    except Exception:
        pass
    try:
        rows = db_fetch("SELECT industry, crowded FROM industry_mainline "
                        "WHERE date=(SELECT MAX(date) FROM industry_mainline)")
        # ★ 2026-09-13 走查修复：「其它行业」是兜底桶（687 只未识别个股），
        #   `get_mainline_summary` 已明确"不参与主线判定"；这里同样排除，
        #   否则这 687 只个股会因"在主线"白拿 +1（实测 301035 即 sector=+1）。
        snap["mainlines"] = {r["industry"] for r in (rows or [])
                             if r.get("industry") and r["industry"] != "其它行业"}
        # ★ 2026-09-13 P1-3：拥挤主线单独标记（只减不加，不给传导链 +1）
        snap["crowded_mainlines"] = {
            r["industry"] for r in (rows or [])
            if (r.get("industry") and r["industry"] != "其它行业"
                and r.get("crowded"))}
    except Exception:
        pass

    _env_cache["ts"] = now
    _env_cache["snap"] = snap
    return snap


def db_fetch(sql, params=None):
    """延迟导入 db（避免模块级循环导入），供 _env_snapshot 的列表查询使用。

    ★ 2026-09-13 修复：此前 _env_snapshot 直接写 `db.fetch(...)`，但本模块顶部
      从未 `import db` → 每次调用都抛 `NameError` 被 `except Exception: pass` 吞掉
      → **板块资金流表与主线名单长期恒为空**（板块层实际上从未生效：
      flow 永远是"未知"、in_mainline 永远 False）。加本函数并替换调用点。
    """
    from app.database import db
    return db.fetch(sql, params)


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
    # ★ 2026-09-13 P1-3：拥挤主线（涨太多=末端）不给 +1（回测：T+5 均收益 -1.41% vs
    #   不拥挤 -0.29%）——"只减不加"：资金流出仍照常 -1，仅去掉主线加分
    crowded_ml = (industry in (snap.get("crowded_mainlines") or set())
                  if industry else False)
    sector_score = 0
    if flow is not None and flow != 0:
        sector_score += 1 if flow > 0 else -1
    if in_mainline and not crowded_ml:
        sector_score += 1
    layers["sector"] = sector_score
    flow_txt = f"{flow:+.1f}亿" if flow is not None else "未知"
    if crowded_ml:
        ml_txt = "、拥挤主线(不加分)"
    elif in_mainline:
        ml_txt = "、在主线"
    else:
        ml_txt = ""
    reasons.append(f"板块[{industry or '未知'}]资金{flow_txt}{ml_txt}")

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
        "crowded_mainline": crowded_ml,
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
