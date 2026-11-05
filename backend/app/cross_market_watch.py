# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】跨市场异动观察器 —— **纯观察器，不产生买卖信号，不进决策链**
================================================================================
背景（2026-11-05，用户问）：
  "恒生科技 +5.55% / 日经 -1.79% / 韩国 KOSPI -5.28%，A股半导体 ETF 两天 +13%，
   这样的异常情况，如何解读？需要新加功能吗？"
  ⇒ 这组数据的价值不在单个涨跌幅，而在**方向相反**：
       美日韩半导体链在跌（费半 -3.13%、KOSPI -5.28%），
       中国科技资产在涨（恒科 +5.55%、中概金龙 +3.49%）。
     **如果真是"全球半导体景气上升"，两边该同向。反向只能说明是资金再分配。**
  ⇒ 系统此前**看不到这件事**：
     · `_external_panic()` 只看"日经 < -2%"，当天日经现货 -1.79% 差 0.21 个点没触发；
     · KOSPI 面板早就有（`100.KS11`），但**没有任何判定读它**；
     · 完全没有"区域背离"这个概念。

★ 为什么**不**改 `_external_panic`（重要）：
  那个函数的结果进 `market_regime` 判定，会连锁改**评分权重 / 准入闸门 / 模拟盘**。
  为一个新阈值就让整批股票分数漂移，风险与收益不对等。
  ⇒ 本模块**只输出事实**：涨跌幅、是否极端、背离差值。
     要不要据此行动，由人决定（与拥挤赛道/关键位/AI 篮子同级）。

口径（读 `app.macro.get_macro_panel()` 的**共享 60s 缓存**，零新增请求）：
  · 亚太（恒科/KOSPI/日经）= **T 日**；
  · 美股（费半/纳指）= **T-1 收盘**（A 股白天看到的美股必然是昨晚的）；
  · ⚠️ 因此**背离只算亚太同日**（中国 vs 日韩），美股单独展示并标注日期。
    拿 T 日和 T-1 相减是"写入时刻冒充数据时刻"的同类错误，这里刻意不做。
================================================================================
"""
from datetime import datetime
from typing import Dict, List, Optional

# 极端异动阈值：单日 |涨跌| ≥ 3% 才值得占一行（KOSPI -5.28% 属历史级）
EXTREME_PCT = 3.0
# 区域背离阈值：中国 vs 日韩 的日涨幅差 ≥ 8pp
DIVERGE_PP = 8.0

# key, 显示名, 分组, 口径说明, 是否参与背离
# 分组只用于展示：cn（中国资产）/ asia（日韩）/ us（美股 T-1，不参与背离）
#
# ★★ 2026-11-05 实测踩坑（必须写下来，否则下次还会犯）：
#   最初把**日经**也放进 `asia` 参与背离均值，实测当天：
#       恒科 +5.55% ｜ KOSPI -5.28% ｜ 日经 **-0.06%**（面板走新浪 `hf_NK` = **期货**）
#     ⇒ asia 均值被稀释成 -2.67%，gap 只剩 **8.22pp** —— 恰好在 8pp 阈值边上。
#   而用户看到、也是事实的那组是：日经**现货收盘 -1.79%**，KOSPI -5.28%。
#   根因：`hf_NK` 是 24h 交易的**期货**，夜盘会一路反弹/回落，
#        拿它当"当日收盘"去平均，等于让**夜盘噪声决定阈值是否触发**。
#   ⇒ 规矩：**参与背离的必须是"同日、已收盘"的口径**。
#     日经保留展示（它的夜盘变动本身有信息量），但不进均值。
_MARKETS = [
    ("hstech", "恒生科技", "cn", "港股现货收盘", True),
    ("kospi", "韩国KOSPI", "asia", "东财现货收盘", True),
    ("nikkei", "日经225", "asia", "期货（含夜盘，见时间）", False),
    ("sox", "费半SOX", "us", "美股 T-1 收盘", False),
    ("us_ixic", "纳指", "us", "美股 T-1 收盘", False),
]

_LEVEL_TEXT = {
    "extreme_up": "极端上涨",
    "extreme_down": "极端下跌",
    "normal": "正常",
    "none": "无数据",
}


def _num(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _level(chg: Optional[float]) -> str:
    if chg is None:
        return "none"
    if chg >= EXTREME_PCT:
        return "extreme_up"
    if chg <= -EXTREME_PCT:
        return "extreme_down"
    return "normal"


def snapshot() -> Dict:
    """跨市场异动快照（纯读面板缓存，零外部请求）。

    返回：
      ok / as_of / markets[] / divergence / alerts[] / summary / note
    """
    try:
        from app.macro import get_macro_panel
        panel = get_macro_panel() or {}
    except Exception as e:
        return {"ok": False, "error": f"宏观面板不可用: {str(e)[:80]}",
                "markets": [], "alerts": [], "divergence": None}

    rows: List[Dict] = []
    for key, name, group, caliber, in_div in _MARKETS:
        x = panel.get(key) or {}
        chg = _num(x.get("change_pct"))
        rows.append({
            "key": key, "name": name, "group": group,
            "price": _num(x.get("price")),
            "chg": chg,
            "time": str(x.get("time") or ""),
            "source": str(x.get("source") or ""),
            "caliber": caliber,
            "in_diverge": in_div,
            "level": _level(chg),
        })

    # ---- 区域背离：只比**同日、已收盘**的口径（中国 vs 韩国），
    #      美股 T-1、日经期货夜盘 都不参与（理由见 `_MARKETS` 上方注释）----
    cn = [r for r in rows if r["group"] == "cn" and r["in_diverge"] and r["chg"] is not None]
    asia = [r for r in rows if r["group"] == "asia" and r["in_diverge"] and r["chg"] is not None]
    divergence = None
    if cn and asia:
        cn_avg = sum(r["chg"] for r in cn) / len(cn)
        asia_avg = sum(r["chg"] for r in asia) / len(asia)
        gap = cn_avg - asia_avg
        divergence = {
            "cn_avg": round(cn_avg, 2),
            "asia_avg": round(asia_avg, 2),
            "gap": round(gap, 2),
            "on": abs(gap) >= DIVERGE_PP,
            "side": "cn_strong" if gap > 0 else "cn_weak",
            "cn_names": [r["name"] for r in cn],
            "asia_names": [r["name"] for r in asia],
            # 给前端直接用，避免前端硬编码"日韩"（日经已不参与，写"日韩"会说错）
            "asia_label": "、".join(r["name"] for r in asia),
        }

    # ---- 提醒文案 ----
    alerts = []
    for r in rows:
        if r["level"] in ("extreme_up", "extreme_down"):
            alerts.append(f"{r['name']} {r['chg']:+.2f}%")
    if divergence and divergence["on"]:
        alerts.append(f"区域背离 {divergence['gap']:+.1f}pp")

    # ---- 一句话结论（不解释原因，只陈述方向关系）----
    summary = ""
    if divergence and divergence["on"]:
        lbl = divergence["asia_label"]
        # "韩国KOSPI" 直接塞进"资金从__流向中国"读起来是生硬的
        # ⇒ 去掉指数代号，只说国家/地区
        lbl_s = lbl.replace("KOSPI", "").strip() or lbl
        if divergence["side"] == "cn_strong":
            summary = (f"中国资产 {divergence['cn_avg']:+.2f}% vs {lbl} "
                       f"{divergence['asia_avg']:+.2f}% ⇒ 差 {divergence['gap']:.1f}pp："
                       f"资金从{lbl_s}流向中国（方向相反 ≠ 行业景气上升，属筹码再分配）")
        else:
            summary = (f"中国资产 {divergence['cn_avg']:+.2f}% vs {lbl} "
                       f"{divergence['asia_avg']:+.2f}% ⇒ 差 {divergence['gap']:.1f}pp："
                       f"资金从中国流向{lbl_s}")
    elif alerts:
        summary = "；".join(alerts)
    elif all(r["level"] == "none" for r in rows):
        summary = "面板暂无外围数据"

    us_times = {r["time"] for r in rows if r["group"] == "us" and r["time"]}
    return {
        "ok": True,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "markets": rows,
        "divergence": divergence,
        "alerts": alerts,
        "summary": summary,
        "us_as_of": sorted(us_times)[-1] if us_times else "",
        "thresholds": {"extreme_pct": EXTREME_PCT, "diverge_pp": DIVERGE_PP},
        "note": ("观察器：只陈述涨跌幅与方向关系，不解释成因、不改分数、不进闸门。"
                 "背离只算**同日已收盘**的中国 vs 韩国；日经面板是期货（含夜盘）、"
                 "美股是 T-1 收盘，二者只展示、不进均值。"),
    }
