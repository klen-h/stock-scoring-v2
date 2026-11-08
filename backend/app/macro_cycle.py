# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】宏观周期四层观察卡（S3）—— **只读观察器，不判阶段、不出仓位建议**
================================================================================
来源：`docs/plans/全球宏观周期判断与资产配置逻辑框架.md`（v1.1 审查件）§4.1 四层条件系统。

--------------------------------------------------------------------------------
一、★★ 为什么**不判阶段**（这是本模块与框架原文最大的差异，务必先读）
--------------------------------------------------------------------------------
  框架原文的四阶段状态机要求四层齐备才能推进（P1→P2 需 L1∧L2）。
  **但项目里 L1（通胀层）完全没有数据**：

    实测（2026-11-08）：`macro_daily.panel` 共 26 个品种，全是**行情类**
    （us2y/us10y/us30y/brent/gold/copper/dxy/vix/汇率/股指/商品），
    **没有任何 PCE / CPI / supercore** ⇒ L1 恒为"不可用" ⇒ **P1→P2 永远不成立**。

  ⇒ 若硬做状态机，结果必然是"永远停在 P0"，那不是观察，是摆设。
    所以本模块**只输出四层的当前读数与缺口**，`phase` 恒为 None 并说明原因。
    **不假装能判周期。**

--------------------------------------------------------------------------------
二、四层的数据现状（诚实标注）
--------------------------------------------------------------------------------
  | 层 | 数据 | 状态 |
  |---|---|---|
  | L1 通胀（核心PCE/supercore/预期） | **无** | `unavailable` |
  | L2 就业（失业率/萨姆规则）      | ✅ 金十日历（`app/sahm_rule.py`） | `ok` |
  | L3 市场（30Y美债/布伦特/DXY）    | ✅ `macro_daily.panel` | `ok` |
  | L4 语言（沃什措辞分期）          | 需人工/LLM 判读 | `manual` |

  ⚠️ **L3 在框架里是 `boost`（只加置信度、不触发跳转）**，见框架 §4.1 修订。
     本模块同样只做"是否越阈"的事实呈现，不给任何加权结论。

--------------------------------------------------------------------------------
三、红线
--------------------------------------------------------------------------------
  1. **纯观察器**：不改分数、不进闸门、不碰模拟盘、**不输出任何仓位建议**。
     框架 §5.2 的配置配比表已按项目红线降级为"参考叙事"，本模块**不引用它**。
  2. 缺失 ≠ 未触发：数据取不到时状态是 `unavailable`/`insufficient`，
     调用方不得当 False（同 `macro_regime_class` / `policy_bottom_watch` 纪律）。
================================================================================
"""
import json
from datetime import datetime, timezone
from typing import Dict, List, Optional

from app.database import db

LAYER_CN = {
    "L1_inflation": "通胀层",
    "L2_employment": "就业层",
    "L3_market": "市场层",
    "L4_language": "语言层",
}

# L3 三个阈值（框架 §4.2；阈值是"观察标尺"，改这里需同步改文档）
L3_RULES = (
    # (panel key, 中文名, 比较符, 阈值, 说明)
    ("us30y", "美债30Y", "gt", 5.25, "金融条件自动收紧"),
    ("brent", "布伦特原油", "lt", 85.0, "战事溢价消退"),
    ("dxy", "美元指数", "gt", 108.0, "极端走强倒逼转向"),
)


def _latest_panel() -> Dict:
    """读 `macro_daily` 最新快照的 panel（date 已于 2026-11-08 迁移为 ISO，可直接排序）。"""
    row = db.fetch_one("SELECT date, data_json FROM macro_daily "
                       "ORDER BY date DESC LIMIT 1") or {}
    date = row.get("date")
    try:
        d = json.loads(row.get("data_json") or "{}")
    except (json.JSONDecodeError, TypeError):
        d = {}
    return {"date": date, "panel": (d.get("panel") or {})}


def _layer_l3() -> Dict:
    """L3 市场层：逐项与阈值比较，输出事实（不含加权/结论）。"""
    try:
        snap = _latest_panel()
    except Exception as e:
        return {"layer": "L3_market", "name": LAYER_CN["L3_market"],
                "status": "unavailable", "reason": f"读 macro_daily 失败: {str(e)[:70]}",
                "metrics": []}
    panel = snap.get("panel") or {}
    metrics: List[Dict] = []
    for key, name, op, thr, note in L3_RULES:
        v = (panel.get(key) or {}).get("price")
        if v is None:
            metrics.append({"key": key, "name": name, "value": None,
                            "threshold": thr, "op": op, "hit": None,
                            "note": "无数据"})
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            metrics.append({"key": key, "name": name, "value": v,
                            "threshold": thr, "op": op, "hit": None,
                            "note": "非数值"})
            continue
        hit = fv > thr if op == "gt" else fv < thr
        metrics.append({"key": key, "name": name, "value": round(fv, 4),
                        "threshold": thr, "op": op, "hit": bool(hit),
                        "note": note})
    return {"layer": "L3_market", "name": LAYER_CN["L3_market"],
            "status": "ok" if any(m["value"] is not None for m in metrics) else "unavailable",
            "as_of": snap.get("date"), "metrics": metrics,
            "role": "boost（只提升置信度，不触发阶段跳转）"}


def _layer_l2() -> Dict:
    """L2 就业层：直接复用 `sahm_rule.view()`。"""
    try:
        from app import sahm_rule as sr
        v = sr.view()
    except Exception as e:
        return {"layer": "L2_employment", "name": LAYER_CN["L2_employment"],
                "status": "unavailable", "reason": f"{str(e)[:70]}", "metrics": []}
    return {
        "layer": "L2_employment",
        "name": LAYER_CN["L2_employment"],
        "status": v.get("status"),
        "triggered": v.get("triggered"),
        "sahm_pp": v.get("sahm_pp"),
        "threshold": v.get("threshold"),
        "as_of": v.get("as_of"),
        "gaps": v.get("gaps") or [],
        "text": v.get("text"),
        "metrics": [{
            "key": "sahm_rule", "name": "萨姆规则",
            "value": v.get("sahm_pp"), "threshold": v.get("threshold"),
            "op": "gte", "hit": v.get("triggered"),
            "note": "失业率 3 月均值 vs 过去 12 月最低 MA3",
        }],
        "role": "gate（P1→P2 的确认条件之一；触发即硬切衰退剧本）",
    }


def _layer_l1() -> Dict:
    """L1 通胀层：核心PCE 年率（金十日历 dataId=1145458，月频）。

    ★ 2026-11-08 接入（用户提供 dataId 清单后确认）：
      用**月度同比**（年率），不用"年化季率"那些季度口径 —— 见 `app/jin10_calendar.py` §二。
      判定逻辑见 `app/macro_inflation.py`：连续回落 ≥2 月 **且** 趋势指向 ≤2.8%。
    """
    try:
        from app import macro_inflation as mi
        v = mi.view()
    except Exception as e:
        return {"layer": "L1_inflation", "name": LAYER_CN["L1_inflation"],
                "status": "unavailable", "reason": str(e)[:80], "metrics": []}

    st = v.get("status")
    metrics = [{
        "key": "core_pce_yoy", "name": "核心PCE年率",
        "value": v.get("latest"),
        "threshold": f"连续回落≥{mi.MIN_FALLS}月 且 趋势≤{mi.CORE_PCE_TARGET}%",
        "op": "rule", "hit": v.get("layer_ok") if st == "ok" else None,
        "note": (f"回落{v.get('falls')}月｜斜率{v.get('slope'):+.2f}/月"
                 f"｜外推{v.get('proj')}%" if st == "ok" else "数据不足"),
    }, {
        "key": "supercore_pce", "name": "超级核心PCE",
        "value": None, "threshold": "3月年化<3.0%", "hit": None,
        "note": "金十无该口径（需另行计算或他源），暂缺",
    }]
    return {
        "layer": "L1_inflation",
        "name": LAYER_CN["L1_inflation"],
        "status": st,
        "as_of": v.get("as_of"),
        "from": v.get("from"),
        "n_loaded": v.get("n_loaded"),
        "metrics": metrics,
        "text": v.get("text"),
        "detail": v.get("detail"),
        "role": "gate（P1→P2 的必要条件）",
    }


def _layer_l4() -> Dict:
    """L4 语言层：需人工/LLM 判读，本模块只给"去读什么"。"""
    return {
        "layer": "L4_language",
        "name": LAYER_CN["L4_language"],
        "status": "manual",
        "reason": "沃什措辞分期需人工或 NLP 判读（S4，未实现）",
        "metrics": [
            {"key": "warsh_rhetoric", "name": "沃什措辞", "value": None,
             "threshold": "「保持耐心 / 政策处于良好位置」", "hit": None,
             "note": "人工判读；可参考 jin10_tidy 的 50022/50023 政策栏目"},
        ],
        "role": "gate（P2→P3 的必要条件之一）",
    }


def snapshot() -> Dict:
    """四层观察快照。**`phase` 恒为 None** —— 见模块头"为什么不判阶段"。"""
    layers = [_layer_l1(), _layer_l2(), _layer_l3(), _layer_l4()]

    # ★ 2026-11-08：L1 通胀层接入后，**下一道缺口是 FOMC 点阵图**。
    #   P1→P2 的完整条件是 `L1 ∧ L2 ∧ 点阵图远端点冻结`（框架 §4.1 修订表），
    #   前两者现在都有数据，但**点阵图项目没有数据源**（需接 FOMC 声明/SEP 文本）。
    #   ⇒ 仍然不判阶段，但要如实说明"卡在哪"，而不是笼统说"缺数据"。
    dot_plot_available = False
    l1 = next(x for x in layers if x["layer"] == "L1_inflation")
    if dot_plot_available:
        phase_reason = "四层可评估，可进入状态机（未实现）"
    else:
        phase_reason = (
            "**不判阶段**：P1→P2 需「L1通胀 ∧ L2就业 ∧ 点阵图远端点冻结」，"
            f"L1={l1.get('status')}、L2 有数据，但**项目无 FOMC 点阵图数据源** ⇒ "
            "该门永不成立。当前只报四层读数与缺口。"
        )

    hit_count = sum(1 for x in layers if x.get("status") == "ok")

    # ★ 2026-11-08：**框架外的补充观察** —— 全球 CPI。
    #   框架 L1 只有美国 PCE 系指标，没有 CPI、也没有中国通胀。
    #   但中国 CPI 决定国内货币政策空间，对 A 股比美国 CPI 更直接。
    #   ⚠️ 它**不进 L1**（阈值口径完全不同：中国 0.9% 是低通胀、美国 3.7% 是偏高）。
    try:
        from app import macro_cpi as _cpi
        cpi = _cpi.view()
    except Exception as e:
        cpi = {"ok": False, "note": f"CPI 模块不可用: {str(e)[:70]}"}

    return {
        "ok": True,
        "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "phase": None,
        "phase_reason": phase_reason,
        "layers": layers,
        "layers_ok": hit_count,
        "layers_total": len(layers),
        "cpi": cpi,
        "note": ("纯观察器：不产生买卖信号、不给仓位建议（框架 §5.2 的配比表"
                 "已按项目红线降级为参考叙事）。数据缺失一律标 unavailable，"
                 "**不得当作未触发**。`cpi` 为框架外补充，**不参与 L1 判定**。"),
        "doc": "docs/plans/全球宏观周期判断与资产配置逻辑框架.md",
    }


def brief() -> str:
    """一行摘要（日批/日志用）。"""
    s = snapshot()
    parts = []
    for x in s["layers"]:
        st = x.get("status")
        if st == "ok":
            hits = [m for m in (x.get("metrics") or []) if m.get("hit") is True]
            parts.append(f"{x['name']}{len(hits)}/{len(x.get('metrics') or [])}")
        elif st == "manual":
            parts.append(f"{x['name']}待人工")
        else:
            parts.append(f"{x['name']}无数据")
    return "｜".join(parts)
