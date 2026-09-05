# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】L3 财报断层扫描（先知雷达第三层：信息断层 / 盈利质量）
================================================================================

命题：净利润是"会计数字"，经营现金流才是"真金白银"。
  净现比 = 经营现金流净额 / 扣非净利润：
    ≈1 健康；<0.5 利润没变成钱（应收堆积/确认激进）；<0.3 大断层（暴雷前兆）
  商誉/净资产 >30%：悬顶之剑（减值一次炸掉多年利润）

数据源：stock_finance_zz（zzshare 现金流量表/资产负债表/指标表，季度更新，
  scripts/zz_finance_sync.py 同步，全池 1,991 只）。

输出：单条 L3 矛盾（聚合口径）——断层数量 + 最危险样本 + 商誉悬顶名单。
  severity：大断层 ≥8 只或商誉悬顶 ≥8 只 → severe；≥3 → obvious；否则 minor。
  每季度财报更新后结论变化，扫描幂等覆盖当日。

注意：净现比天然有行业差异（建筑/地产垫资模式普遍低），跨行业比较时
  看绝对阈值即可（<0.3 才报），不做行业内分位——暴雷信号本就稀有。
================================================================================
"""

from typing import Dict, Optional

from app.database import db

OCF_NI_SEVERE = 0.30      # 净现比低于此 = 大断层（年报口径）
OCF_NI_WARN = 0.50        # 净现比低于此 = 中断层（年报口径）
GOODWILL_EQUITY_WARN = 0.30   # 商誉/净资产 30% 警戒线

MIN_NET_PROFIT = 5e7      # 扣非净利润 ≥5000 万才参与（排除微利公司的比值噪音）
RATIO_ANOMALY = 10.0      # |净现比|>10 视为数据异常剔除（残值倒挂等）

# 非银金融 OCF 含客户资金存款变动，净现比指标不适用（华林证券 -46 的教训）
FINANCIAL_NAME_KEYWORDS = ("银行", "证券", "保险", "信托", "期货", "金融", "财务")


def _is_financial(name: str) -> bool:
    return any(k in name for k in FINANCIAL_NAME_KEYWORDS)


def _period_scale(report_date: str) -> float:
    """报告期季节性缩放：净现比是累计口径，半年报天然低于年报
    （回款集中在 Q4）→ 阈值按报告期占全年的比例缩放。"""
    month = report_date[5:7] if len(report_date) >= 7 else "12"
    return {"03": 0.25, "06": 0.5, "09": 0.75}.get(month, 1.0)


def _load_reports() -> list:
    rows = db.fetch(
        "SELECT code, report_date, ind_json, bal_json, cf_json FROM stock_finance_zz")
    import json
    out = []
    for r in rows or []:
        try:
            ind = json.loads(r.get("ind_json") or "{}")
            bal = json.loads(r.get("bal_json") or "{}")
            cf = json.loads(r.get("cf_json") or "{}")
        except (ValueError, TypeError):
            continue
        out.append({"code": r["code"], "report_date": str(r.get("report_date") or ""),
                    "ind": ind, "bal": bal, "cf": cf})
    return out


def scan_financial_gap(date: Optional[str] = None) -> Optional[Dict]:
    """L3 财报断层扫描：净现比断层 + 商誉悬顶，聚合为一条矛盾。"""
    try:
        reports = _load_reports()
    except Exception as e:
        print(f"[contradiction] L3 财报数据读取失败: {e}")
        return None
    if not reports:
        return None

    # 名称映射（stock_finance 有中文名）
    name_map = {}
    try:
        for r in db.fetch("SELECT DISTINCT code, name FROM stock_finance WHERE name IS NOT NULL"):
            name_map[r["code"]] = r["name"]
    except Exception:
        pass

    gaps, goodwill_risk, checked, anomalies = [], [], 0, 0
    for rep in reports:
        ni = rep["ind"].get("adjusted_profit") or rep["ind"].get("operating_profit")
        ocf = rep["cf"].get("net_operate_cash_flow")
        equity = rep["bal"].get("total_owner_equities")
        goodwill = rep["bal"].get("goodwill") or 0
        name = name_map.get(rep["code"], rep["code"])

        # 净现比（只对盈利公司有意义；亏损公司的现金流分析另属专题）
        if ni and ocf and float(ni) > MIN_NET_PROFIT:
            if _is_financial(name):
                continue          # 非银金融 OCF 含客户资金变动，指标不适用
            checked += 1
            ratio = float(ocf) / float(ni)
            if abs(ratio) > RATIO_ANOMALY:
                anomalies += 1
                continue
            scale = _period_scale(rep["report_date"])
            # 两档：现金失血（OCF 为负 = 报警主体）；弱净现比（只计数，不点名）
            if ratio < 0 or ratio < OCF_NI_SEVERE * scale:
                gaps.append((rep["code"], name, ratio, rep["report_date"]))

        # 商誉悬顶
        if equity and goodwill and float(equity) > 0:
            g_ratio = float(goodwill) / float(equity)
            if g_ratio > GOODWILL_EQUITY_WARN:
                goodwill_risk.append((rep["code"], name, g_ratio))

    gaps.sort(key=lambda x: x[1])
    goodwill_risk.sort(key=lambda x: -x[1])
    # 大断层 = 现金失血型（OCF 为负）：这才是"利润没变成钱"的报警主体
    n_severe_gap = sum(1 for g in gaps if g[2] < 0)
    if n_severe_gap >= 8 or len(goodwill_risk) >= 8:
        severity = "severe"
    elif len(gaps) >= 3 or len(goodwill_risk) >= 3:
        severity = "obvious"
    else:
        severity = "minor"

    if not gaps and not goodwill_risk:
        return None

    gap_desc = "、".join(f"{nm}({r:.2f})" for _, nm, r, _ in gaps[:6])
    gw_desc = "、".join(f"{nm}({r:.0%})" for _, nm, r in goodwill_risk[:6])
    summary = (f"全池 {checked} 只盈利公司中，**现金失血**（有利润但经营现金流为负）"
               f" **{n_severe_gap}** 只，弱净现比 {len(gaps) - n_severe_gap} 只，"
               f"商誉/净资产>30% 悬顶 **{len(goodwill_risk)}** 只"
               + (f"，异常值剔除 {anomalies} 只" if anomalies else "") + "。"
               + (f" 失血最重：{gap_desc}。" if gap_desc else "")
               + (f" 商誉悬顶：{gw_desc}。" if gw_desc else ""))

    return {
        "level": "L3",
        "type": "financial_report_gap",
        "severity": severity,
        "title": "财报断层：利润没有变成现金",
        "summary": summary,
        "evidence": {
            "narrative": "财报净利润高增长，表面盈利质量无忧",
            "actual": "经营现金流与利润严重背离，或商誉占比触及警戒线",
            "metrics": {
                "checked_count": checked,
                "ocf_ni_gap_count": len(gaps),
                "ocf_ni_severe_count": n_severe_gap,
                "ocf_ni_gap_samples": [
                    {"code": c, "name": nm, "ratio": round(r, 2)}
                    for c, nm, r, _ in gaps[:8]],
                "goodwill_risk_count": len(goodwill_risk),
                "goodwill_risk_samples": [
                    {"code": c, "name": nm, "ratio": round(r, 3)}
                    for c, nm, r in goodwill_risk[:8]],
            },
        },
        "signal": ("净现比<0.3 的公司财报季前后易爆雷（应收暴雷/减值），持仓排查重叠，"
                   "不抄底、不补仓；商誉悬顶股在年报季（1-4月）主动规避。"),
    }
