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

import gzip
import json
import os
import time
from typing import Dict, Optional

from app.database import db

# ── 整表读的缓存（2026-09-25 egress 治理，探针实测驱动）─────────────────────────
# 问题：`_load_reports()` 整表读 `stock_finance_zz`（含 ind/bal/cf 三个大 JSON），
#   实测 ≈**10MB/次**；pg_stat_statements 里该语句 14 天 39 次 / ≤392MB —— 而财报是
#   **日频/季度**数据。原先**完全无缓存** ⇒ 每次扫描/每次新进程都重来一遍。
# 做法（照 `mainforce/flow.py` 的三层模式）：进程内存(10min) → **本机 gzip 持久缓存**
#   (12h，跨进程/跨重启) → 才回源。指纹 = (行数, MAX(updated_at))，是**单行**查询（几十字节）。
_L3_CACHE = {"ts": 0.0, "ver": None, "val": None}
_L3_MEM_TTL = 600                 # 进程内 10 分钟（同一进程内多次扫描零查询）
_L3_DISK_TTL = 12 * 3600          # 本机持久缓存 12 小时（财报不会一天内反复变）
_L3_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "l3_finance_src.json.gz")
# ⚠️ 路径层数：本文件在 `backend/app/contradictions/` 下，与 `mainforce/flow.py` 同深度
#   ⇒ 同样上溯 3 层（contradictions → app → backend）才是 backend（约定落 `backend/data/`）。


def _src_fingerprint():
    """`stock_finance_zz` 变更指纹（行数 + MAX(updated_at)）：单行查询，几十字节。

    取不到（异常/无表）返回 None ⇒ 调用方**不走缓存直接回源**（正确性优先：
    宁可多花一次流量，也不拿无法判新的缓存做排雷判断）。
    """
    try:
        r = db.fetch_one(
            "SELECT COUNT(*) AS n, MAX(updated_at) AS u FROM stock_finance_zz")
        return f"{int((r or {}).get('n') or 0)}|{(r or {}).get('u') or ''}"
    except Exception:
        return None


def _disk_load(ver):
    """读本机 gzip 缓存（零 egress）。指纹不符 / 过期 / 异常 ⇒ None（调用方回源）。"""
    if ver is None:
        return None
    try:
        if not os.path.exists(_L3_PATH):
            return None
        with gzip.open(_L3_PATH, "rt", encoding="utf-8") as f:
            d = json.load(f)
        if str(d.get("ver")) != str(ver):
            return None
        if time.time() - float(d.get("ts") or 0) > _L3_DISK_TTL:
            return None
        return d.get("rows") or None
    except Exception:
        return None


def _disk_save(ver, rows):
    """写本机 gzip 缓存（原子替换）。失败静默（不影响主流程）。"""
    if ver is None:
        return
    try:
        os.makedirs(os.path.dirname(_L3_PATH), exist_ok=True)
        tmp = _L3_PATH + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"ver": ver, "ts": time.time(), "rows": rows}, f,
                      ensure_ascii=False)
        os.replace(tmp, _L3_PATH)
    except Exception as e:
        print(f"[l3] 本机缓存写入失败（不影响扫描）: {e}")        # ASCII（铁律⑥）

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
    """读全池财报（含 ind/bal/cf 三个 JSON 字段的**解析结果**）。

    ★ 2026-09-25（egress 治理）：三层缓存 —— 进程内存(10min) → 本机 gzip(12h) → 才回源。
      见文件头 `_L3_*` 注释（整表 ≈10MB/次，原先每次扫描都重来）。缓存的是**解析后**
      的 dict 列表（JSON 兼容），顺带省掉每次重复 `json.loads` 大字段的开销。
    """
    now = time.time()
    c = _L3_CACHE["val"]
    if c is not None and now - _L3_CACHE["ts"] < _L3_MEM_TTL:
        return c
    ver = _src_fingerprint()
    cached = _disk_load(ver)
    if cached is not None:
        _L3_CACHE.update(ts=now, ver=ver, val=cached)
        print(f"[l3] 财报源命中本机缓存（{len(cached)} 行）-> 零 Supabase 流量")
        return cached
    rows = db.fetch(
        "SELECT code, report_date, ind_json, bal_json, cf_json FROM stock_finance_zz")
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
    _disk_save(ver, out)
    _L3_CACHE.update(ts=now, ver=ver, val=out)
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

    try:
        from app.mainforce.l3_history import bleeding_streak_map
        streak_map = bleeding_streak_map()
    except Exception:
        streak_map = {}

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
                gaps.append((rep["code"], name, ratio, rep["report_date"],
                             streak_map.get(rep["code"], 0)))

        # 商誉悬顶
        if equity and goodwill and float(equity) > 0:
            g_ratio = float(goodwill) / float(equity)
            if g_ratio > GOODWILL_EQUITY_WARN:
                goodwill_risk.append((rep["code"], name, g_ratio))

    # 连续失血（≥2 期 OCF 均为负）优先展示——结构性失血 vs 单季噪音
    gaps.sort(key=lambda x: (-(x[4] if len(x) > 4 else 0), x[2]))
    goodwill_risk.sort(key=lambda x: -x[1])
    # 大断层 = 现金失血型（OCF 为负）：这才是"利润没变成钱"的报警主体
    n_severe_gap = sum(1 for g in gaps if g[2] < 0)
    n_chronic = sum(1 for g in gaps if len(g) > 4 and g[4] >= 2)
    if n_severe_gap >= 8 or len(goodwill_risk) >= 8:
        severity = "severe"
    elif len(gaps) >= 3 or len(goodwill_risk) >= 3:
        severity = "obvious"
    else:
        severity = "minor"

    if not gaps and not goodwill_risk:
        return None

    gap_desc = "、".join(f"{nm}({r:.2f})" for _, nm, r, _x, _y in gaps[:6])
    gw_desc = "、".join(f"{nm}({r:.0%})" for _, nm, r in goodwill_risk[:6])
    summary = (f"全池 {checked} 只盈利公司中，**现金失血**（有利润但经营现金流为负）"
               f" **{n_severe_gap}** 只，其中**连续 ≥2 期失血 {n_chronic}** 只"
               f"（结构性失血，最高危），弱净现比 {len(gaps) - n_severe_gap} 只，"
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
                    {"code": g[0], "name": g[1], "ratio": round(g[2], 2),
                     "streak": g[4] if len(g) > 4 else 0} for g in gaps[:8]],
                "goodwill_risk_count": len(goodwill_risk),
                "goodwill_risk_samples": [
                    {"code": c, "name": nm, "ratio": round(r, 3)}
                    for c, nm, r in goodwill_risk[:8]],
            },
        },
        "signal": ("净现比<0.3 的公司财报季前后易爆雷（应收暴雷/减值），持仓排查重叠，"
                   "不抄底、不补仓；商誉悬顶股在年报季（1-4月）主动规避。"),
    }
