# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】矛盾验证闭环（先知雷达学习闭环的数据底座，PLAN 雷达第四章落地）
================================================================================

闭环：识别矛盾 → 隐含方向预判 → 次日市场验证 → 结果回写 → 准确率统计

方向规则（types 的"隐含次日方向"，与扫描器叙事一致）：
  index_vs_breadth      bearish（权重护盘假象 → 次日走弱）
  price_vs_volume       bearish（缩量新高 → 假突破回落）
  northbound_vs_index   bearish（外资离场 → 承压）
  index_vs_mainflow     按标题判：净流出→bearish（红盘出货）；
                        净流入→bullish（绿盘吸筹 → 次日反弹）
  其余类型（板块资金/日历类）方向不明确 → 不验证，避免污染统计

验证口径：矛盾日 T 的下一个交易日，上证指数涨跌幅：
  bearish 且 <0 → correct；bullish 且 >0 → correct；|chg| < 0.05% → flat；否则 wrong
这是最保守的"方向对不对"检验——不做幅度归因，排除运气成分需要积累样本后
按类型分桶看准确率（validation_stats）。

调度：contradiction_scan_loop 每日扫描后自动验证一次；路由 POST /validate 手动触发。
================================================================================
"""

from datetime import datetime, timedelta
from typing import Dict, Optional

from app.contradictions import store

# 固定方向规则
DIRECTION_RULES = {
    "index_vs_breadth": "bearish",
    "price_vs_volume": "bearish",
    "northbound_vs_index": "bearish",
}

FLAT_THRESHOLD = 0.05   # 次日涨跌幅 |%| < 0.05 视为平


def predicted_direction(item: Dict) -> Optional[str]:
    """推导矛盾的隐含次日方向；不可判类型返回 None。"""
    t = item.get("type")
    if t in DIRECTION_RULES:
        return DIRECTION_RULES[t]
    if t == "index_vs_mainflow":
        title = item.get("title") or ""
        if "净流出" in title:
            return "bearish"
        if "净流入" in title:
            return "bullish"
    return None


def _next_day_index_change(date_str: str) -> Optional[float]:
    """date_str 之后第一个交易日的上证指数涨跌幅（%）。无后续数据返回 None。"""
    from app.contradictions.scanner import _load_index_kline
    klines = _load_index_kline("000001", count=60)
    if not klines:
        return None
    for i, k in enumerate(klines):
        if str(k.get("date")) > date_str:
            # 涨跌幅优先取行情自带字段，否则用前收盘推算
            chg = k.get("change_pct")
            if chg is not None:
                return float(chg)
            if i > 0 and klines[i - 1].get("close"):
                prev = float(klines[i - 1]["close"])
                if prev > 0:
                    return (float(k["close"]) / prev - 1) * 100
            return None
    return None


def validate_yesterday(max_age_days: int = 10) -> Dict:
    """
    验证所有未验证且可判方向的历史矛盾（含更早遗漏的，最多回溯 max_age_days 天）。
    返回 {validated, correct, wrong, flat, skipped}。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    pending = store.load_unvalidated_before(today)
    stats = {"validated": 0, "correct": 0, "wrong": 0, "flat": 0, "skipped": 0}

    # 缓存次日涨跌（同一矛盾日复用）
    change_cache: Dict[str, Optional[float]] = {}

    for item in pending:
        d = item.get("date") or ""
        # 超龄未验证（数据已不可靠）→ 标记跳过，避免永远留在待验池
        try:
            age = (datetime.now() - datetime.strptime(d, "%Y-%m-%d")).days
        except ValueError:
            age = max_age_days + 1
        direction = predicted_direction(item)
        if direction is None:
            stats["skipped"] += 1
            continue
        if age > max_age_days:
            stats["skipped"] += 1
            continue
        if d not in change_cache:
            change_cache[d] = _next_day_index_change(d)
        actual = change_cache[d]
        if actual is None:
            continue                      # 后续交易日数据未就绪，下次再验
        if abs(actual) < FLAT_THRESHOLD:
            result = "flat"
        elif (direction == "bearish" and actual < 0) or \
             (direction == "bullish" and actual > 0):
            result = "correct"
        else:
            result = "wrong"
        store.update_validation(item["id"], direction, round(actual, 2),
                                result, today)
        stats["validated"] += 1
        stats[result] = stats.get(result, 0) + 1

    return stats


def render_stats_markdown(days: int = 30) -> str:
    """验证统计 → markdown（报告/日报复用）。"""
    from app.contradictions.labels import type_cn
    st = store.validation_stats(days=days)
    ov = st.get("overall") or {}
    if not ov.get("n"):
        return "暂无已验证矛盾（闭环自扫描日起 T+1 逐步积累）。"
    lines = [
        f"近 {days} 日已验证 **{ov['n']}** 条：预判正确 {ov['correct']} / "
        f"错误 {ov['wrong']} / 平 {ov['flat']}，**准确率 {ov['accuracy']}%**",
        "",
        "| 矛盾类型 | 样本 | 准确率% |",
        "|---|---|---|",
    ]
    for t, d in sorted((st.get("by_type") or {}).items(),
                       key=lambda kv: -(kv[1].get("n") or 0)):
        lines.append(f"| {type_cn(t)} | {d['n']} | {d['accuracy']} |")
    return "\n".join(lines)
