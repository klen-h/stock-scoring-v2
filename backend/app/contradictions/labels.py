# -*- coding: utf-8 -*-
"""
矛盾扫描中文字典：severity / level / type / metrics 键的中文映射。

后端数据字段保持英文（API 稳定 + 代码可读），仅【展示层】翻译：
  - report.py 渲染 markdown 报告时用
  - 前端 ContradictionsView.vue 用同样的映射渲染列表（前端自持一份）
新增扫描器时在此补登记，报告与页面即自动中文化。
"""

SEVERITY_CN = {
    "severe": "严重",
    "obvious": "明显",
    "minor": "轻微",
}

LEVEL_CN = {
    "L1": "L1 预期差",
    "L2": "L2 行为背离",
}

TYPE_CN = {
    "index_vs_breadth": "指数与个股结构背离",
    "sector_narrative_vs_flow": "板块叙事与资金流向背离",
    "price_vs_volume": "指数量价背离",
    "northbound_vs_index": "北向资金与指数背离",
    "index_vs_mainflow": "指数与主力资金流背离",
    "calendar_surprise": "宏观数据预期差",
    "today_calendar_focus": "今日重点关注数据",
}

# evidence.metrics 键 → 中文标签
METRIC_CN = {
    # 指数 vs 个股结构
    "sample_size": "样本数",
    "up": "上涨家数",
    "down": "下跌家数",
    "flat": "平盘家数",
    "ratio": "涨跌比",
    "avg_pct": "平均涨幅%",
    "red_indices": "收红指数",
    "green_indices": "收绿指数",
    # 板块叙事 vs 资金
    "up_but_outflow_count": "上涨但净流出板块数",
    "up_but_outflow_total": "上涨板块净流出合计",
    "up_but_outflow_samples": "上涨但净流出板块",
    "down_but_inflow_count": "下跌但净流入板块数",
    "down_but_inflow_total": "下跌板块净流入合计",
    "down_but_inflow_samples": "下跌但净流入板块",
    # 量价背离
    "index": "指数",
    "latest_close": "最新收盘",
    "high_20d": "20日高点",
    "latest_vol": "当日成交量",
    "vol_20d_avg": "20日均量",
    "vol_ratio": "量比",
    # 北向
    "northbound_net": "北向净流入",
    "northbound_sh": "沪股通净流入",
    "northbound_sz": "深股通净流入",
    # 主力资金流
    "pool_main_net_yi": "评分池主力净流入(亿)",
    "recent_5d_yi": "近5日主力净流入(亿)",
    # 财经日历
    "surprise_count": "超预期数据个数",
    "focus_count": "重点关注个数",
    "top_events": "重点事件",
}


def severity_cn(s) -> str:
    return SEVERITY_CN.get(s or "", s or "")


def level_cn(s) -> str:
    return LEVEL_CN.get(s or "", s or "")


def type_cn(s) -> str:
    return TYPE_CN.get(s or "", s or "")


def metric_cn(k) -> str:
    return METRIC_CN.get(k, k)


def _fmt_amount(v: float) -> str:
    """金额友好显示：绝对值 ≥百万按亿元显示（东财净额为元）。"""
    if abs(v) >= 1e6:
        return f"{v / 1e8:+.2f}亿"
    return f"{v:+,.0f}"


# 值为【元】金额的指标键：≥百万时按亿元渲染（成交量/家数等计数键不适用）
_AMOUNT_KEYS = {"northbound_net", "northbound_sh", "northbound_sz",
                "up_but_outflow_total", "down_but_inflow_total"}


def fmt_metric_value(v, key: str = None) -> str:
    """metrics 值的中文友好渲染：标量直接出；列表/字典拼摘要。"""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        if key in _AMOUNT_KEYS and abs(v) >= 1e6:
            return _fmt_amount(v)
        return f"{v:,}"
    if isinstance(v, str):
        return v
    if isinstance(v, list):
        parts = []
        for it in v[:6]:
            if isinstance(it, dict):
                name = it.get("name") or it.get("title") or ""
                pct = it.get("change_pct")
                extra = it.get("net_outflow", it.get("net_inflow"))
                seg = name or str(it)
                if isinstance(pct, (int, float)):
                    seg += f" {pct:+.2f}%"
                if isinstance(extra, (int, float)):
                    seg += f"（净额 {_fmt_amount(extra)}）"
                parts.append(seg)
            else:
                parts.append(str(it))
        more = f" 等{len(v)}项" if len(v) > 6 else ""
        return "、".join(parts) + more
    if isinstance(v, dict):
        return "、".join(f"{k}:{fmt_metric_value(x)}" for k, x in list(v.items())[:6])
    return str(v)
