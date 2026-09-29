# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】教练建议**事后归因**（P2 / 缺口 3）—— 补 `audit.py` 自陈的
           「5 日结果**只回填不评估**」那一半
================================================================================
【为什么需要】
  复盘要区分**逻辑错 / 时机错 / 执行错**（用户框架原话），而现有 `execution_consistency`
  只覆盖「执行错」一种（执行率）。缺的是「**哪条建议的期望最差**」「**放弃的建议事后怎样**」
  「**同一逻辑在不同主力阶段表现差多少**」。
  出处：《专业度缺口与优先级建议_20260929》缺口 3。

【★ 基于实测的方案修正（2026-09-29，务必先读，防误读本模块的口径）】
  建议书原文是「按**入场理由**分组」。实测 `coach_alerts`（80 行 / 09-14~09-29）后修正为
  「按**建议类型**分组」，理由：
  ① `rule_id` 分布 = loss_over_7pct 19 / stop_loss_hit 17 / hold_3d_review 14 /
     gate_no_add 11 / gate_reduce 5 / external_lead_no_rebound 5 / **gate_add 仅 3** /
     emotion_fuse 2 / reversal_no_panic 2 / no_chase_rally 2
     ⇒ **绝大多数是"风控/持仓管理"建议，真正"买入建议"只有 3 条**。
     **更强的事实（首跑实测）**：**只有** loss_over_7pct / stop_loss_hit /
     hold_3d_review 三类**带 `code`**（19+17+14 = 50 = 全部带 code 的行数），
     `gate_add` 等买入/市场类建议**无 `code`** ⇒ **「入场理由」的个股收益无法评估**
     （不是"样本少"，是"**根本没有可评估对象**"）⇒ 「按入场理由归因」= **数据缺口**，
     已写进输出 `note` 与文档，避免以后有人以为本模块"偷懒没做"。
  ② `numbers_json` 的键实测只有 pnl_pct/threshold/source/stop_loss/price/regime/
     hold_days/us10y/dxy/margin_chg5… ⇒ **不含主力阶段、不含闸门 ready**
     ⇒ "阶段"维度只能**实时 join `mainforce_state`**（`UNIQUE(code,date)` ⇒ 可按
     alert_date 取**当日**状态，语义正确 ✓）。
  ③ 闸门 `ready 1/2/3` 维度**不可做**：`gate_snapshot_history` 仅 6 行、`ready==3` 零样本
     （见 P1 `scripts/gate_ready_backtest.py` 实测）⇒ 本模块**不含**该维度。
  ⇒ 本模块 = 可做的两个维度（**建议类型**、**主力阶段**）+ 执行/放弃 + 放弃理由成因。

【收益口径（★ 与 `audit.backfill_outcome` 同源，不另立第二套）】
  · T+N 收益 = **alert 日收盘 → 其后第 N 个交易日收盘**的涨跌幅（%），
    取 `backtest_prices` 中该股 `date >= alert_date` 的前 N+1 根（同 `backfill_outcome`）。
  · 超额 = 个股收益 − **同起止日**的基准收益（同一首末日去基准里查 ⇒ 真同期对齐）。
    基准 = **中证1000 `sh000852`**（缺口 1 口径）；无数据时退回沪深300 并**显式标注**。
  · 缺失（未到期/无柱）**一律跳过，绝不填 0**。
  · ⚠️ **一致性核对**：T+5 会与库内 `coach_alerts.outcome_pct` 逐条比对并输出
    match/mismatch —— 口径漂移要可见（当前阈值 0.5pt 容差）。

【样本纪律】
  · 组内 n < `MIN_N`(5) ⇒ 标注 `insufficient=True`，**不参与「期望最差」排行**；
  · 全部 n 都 < 30 ⇒ 输出里 `sentence` 会**显式声明"仅方向参考"**（项目纪律：
    小样本不给结论）。这不是保守，是 `strategy_whitelist` 那一轮的教训。

【定位】纯只读聚合。**不改任何闸门/权重/准入**（E2 v0 纪律）；结论文案由**规则**生成。
================================================================================
"""

import json
import time
from collections import Counter
from datetime import timedelta
from typing import Dict, List, Optional

from app.database import db

# ── 口径常量 ──────────────────────────────────────────────────────────────
HOLDS = (5, 20)                 # 评估持有期（交易日）
MIN_N = 5                       # 组内最小样本（低于此 ⇒ 标注不足、不参与排行）
SMALL_N = 30                    # "统计上有意义"的门槛（低于此 ⇒ 整体降级为方向参考）
BENCH_PRIMARY = "sh000852"      # 中证1000（缺口 1 口径）
BENCH_FALLBACK = "sh000300"     # 沪深300（缺中证1000 时的**退路**，必须标注）
OUTCOME_TOL = 0.5               # 与库内 outcome_pct 的一致性容差（pt）
_CACHE = {"ts": 0.0, "key": "", "val": None}
_CACHE_TTL = 300.0              # 5 分钟（数据日频，同其它教练模块惯例）


def _cutoff(days: int) -> str:
    from app.flash import rules as _rules
    return (_rules.beijing_now() - timedelta(days=max(1, int(days)))).strftime("%Y-%m-%d")


# ══════════════════════════════════════════════════════════════════════════
#  一、取数（全部批量，控制 egress）
# ══════════════════════════════════════════════════════════════════════════

def _load_alerts(days: int) -> List[Dict]:
    """有标的的教练建议（近 days 天）。无 code 的建议（如纯市场级）无法算个股收益 ⇒ 排除。"""
    rows = db.fetch(
        "SELECT id, alert_date, rule_id, label, severity, code, name, executed, "
        "abandon_reason, outcome_pct FROM coach_alerts "
        "WHERE alert_date >= %s AND COALESCE(code,'') <> '' "
        "ORDER BY alert_date ASC", (_cutoff(days),))
    return [dict(r) for r in rows or []]


def _load_phases(alerts: List[Dict]) -> Dict:
    """join `mainforce_state` 取**当日**主力阶段 ⇒ {(code, date): phase_cn}（时机维度）。"""
    out: Dict = {}
    codes = sorted({a["code"] for a in alerts})
    if not codes:
        return out
    try:
        from app.mainforce.phases import PHASE_CN
    except Exception:
        PHASE_CN = {}
    for k in range(0, len(codes), 200):      # 分片（同 P1 脚本 egress 纪律）
        chunk = codes[k:k + 200]
        ph = ",".join(["%s"] * len(chunk))
        try:
            rows = db.fetch(
                f"SELECT code, date, phase FROM mainforce_state WHERE code IN ({ph})",
                tuple(chunk))
        except Exception as e:
            print(f"[attribution] 阶段读取失败（该维度留空）: {str(e)[:80]}")
            return out
        for r in rows or []:
            key = (str(r["code"]), str(r["date"])[:10])
            out[key] = PHASE_CN.get(r.get("phase") or "", r.get("phase") or "")
    return out


def _load_bars(codes: List[str], d0: str) -> Dict[str, List[Dict]]:
    """{code: [{date, close}…]}（升序，`date >= d0`）。"""
    out: Dict[str, List[Dict]] = {}
    for k in range(0, len(codes), 200):
        chunk = codes[k:k + 200]
        ph = ",".join(["%s"] * len(chunk))
        rows = db.fetch(
            f"SELECT code, date, close FROM backtest_prices "
            f"WHERE code IN ({ph}) AND date >= %s ORDER BY code, date ASC",
            tuple(chunk) + (d0,))
        for r in rows or []:
            c = float(r.get("close") or 0)
            if c > 0:
                out.setdefault(str(r["code"]), []).append(
                    {"date": str(r["date"])[:10], "close": c})
    return out


def _pick_bench() -> tuple:
    """选基准：优先中证1000；无数据退回沪深300（返回 (code, 中文名, 是否退路)）。"""
    for code, label, fb in ((BENCH_PRIMARY, "中证1000", False),
                            (BENCH_FALLBACK, "沪深300", True)):
        n = (db.fetch_one("SELECT COUNT(*) AS n FROM backtest_prices WHERE code=%s",
                          (code,)) or {}).get("n") or 0
        if n:
            return code, label, fb
    return BENCH_FALLBACK, "沪深300", True


# ══════════════════════════════════════════════════════════════════════════
#  二、收益（与 audit.backfill_outcome 同口径）
# ══════════════════════════════════════════════════════════════════════════

def _fwd(seq: List[Dict], alert_date: str, n: int) -> Optional[tuple]:
    """alert 日收盘 → 其后第 n 个交易日收盘。返回 (收益%, 起始日, 结束日) 或 None。

    `seq` = 该股升序日线（真实用时按 `date >= alert_date` 截取 —— 与
    `audit.backfill_outcome` 的 `WHERE date >= alert_date ORDER BY date ASC LIMIT n+1` 同源）。
    未到期（不足 n+1 根）返回 None ⇒ 调用方跳过（**不填 0**）。
    """
    win = [b for b in seq if b["date"] >= alert_date][: n + 1]
    if len(win) < n + 1 or win[0]["close"] <= 0:
        return None
    return ((win[n]["close"] / win[0]["close"] - 1) * 100, win[0]["date"], win[n]["date"])


def _bench_ret(bench_bars: Dict[str, Dict], d0: str, d1: str) -> Optional[float]:
    """基准在 [d0, d1] 的收益 %（用**个股的首末日**去基准里查 ⇒ 真同期对齐）。"""
    a, b = bench_bars.get(d0), bench_bars.get(d1)
    if not a or not b or a <= 0:
        return None
    return (b / a - 1) * 100


# ══════════════════════════════════════════════════════════════════════════
#  三、聚合
# ══════════════════════════════════════════════════════════════════════════

def _mean(v: List[float]) -> Optional[float]:
    return round(sum(v) / len(v), 2) if v else None


def _build_rows(alerts: List[Dict], bars: Dict, bench_bars: Dict,
                phases: Dict) -> List[Dict]:
    """逐条建议算 T+5 / T+20 的绝对收益与超额（可为 None）。"""
    out = []
    for a in alerts:
        seq = bars.get(a["code"]) or []
        row = {"id": a["id"], "date": str(a["alert_date"])[:10], "rule_id": a["rule_id"],
               "label": a.get("label") or a["rule_id"], "code": a["code"],
               "name": a.get("name") or a["code"], "executed": a.get("executed") or "",
               "abandon_reason": (a.get("abandon_reason") or "").strip(),
               "phase": phases.get((a["code"], str(a["alert_date"])[:10]), "未知"),
               "db_outcome": a.get("outcome_pct")}
        for n in HOLDS:
            f = _fwd(seq, row["date"], n)
            if not f:
                row[f"ret{n}"] = None
                row[f"exc{n}"] = None
                continue
            r, d0, d1 = f
            b = _bench_ret(bench_bars, d0, d1)
            row[f"ret{n}"] = round(r, 2)
            row[f"exc{n}"] = round(r - b, 2) if b is not None else None
        out.append(row)
    return out


def _group(rows: List[Dict], key_fn, label_fn=None) -> List[Dict]:
    """按维度分组：每组 n / 各持有期超额均值 / 胜率 / 样本是否足够。

    `label_fn` **只对"规则"维度传** —— 早前版本给每组取 `items[-1].label`，导致
    `by_executed` 的 "yes" 组显示成某条规则的 label（垃圾信息）⇒ 改为 opt-in。
    """
    buckets: Dict[str, List[Dict]] = {}
    for r in rows:
        buckets.setdefault(str(key_fn(r)), []).append(r)
    out = []
    for key, items in buckets.items():
        g = {"key": key, "n": len(items), "insufficient": len(items) < MIN_N,
             "label": label_fn(items) if label_fn else None}
        for n in HOLDS:
            vals = [r[f"exc{n}"] for r in items if r.get(f"exc{n}") is not None]
            g[f"n{n}"] = len(vals)
            g[f"exc{n}"] = _mean(vals)
            g[f"win{n}"] = (round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1)
                            if vals else None)
        out.append(g)
    # 排序：n 多的在前（可读性），同 n 按 T+5 超额升序（差的在前 —— 复盘要看坏的）
    out.sort(key=lambda x: (-x["n"], x.get("exc5") if x.get("exc5") is not None else 999))
    return out


def advice_attribution(days: int = 60) -> dict:
    """教练建议事后归因（详见模块头）。失败返回 {"available": False, ...} 不抛异常。"""
    days = min(max(int(days or 60), 7), 365)
    key = f"d{days}"
    now = time.time()
    if _CACHE["val"] and _CACHE["key"] == key and now - _CACHE["ts"] < _CACHE_TTL:
        return _CACHE["val"]
    try:
        alerts = _load_alerts(days)
        total_all = (db.fetch_one(
            "SELECT COUNT(*) AS n FROM coach_alerts WHERE alert_date >= %s",
            (_cutoff(days),)) or {}).get("n") or 0
        if not alerts:
            return {"available": False,
                    "reason": f"近 {days} 天无带标的的教练建议（无法算个股收益）"}

        bcode, blabel, bfb = _pick_bench()
        d0 = str(min(a["alert_date"] for a in alerts))[:10]
        # ⚠️ 基准代码**必须并入**同一批查询，否则 `bars.get(bcode)` 恒空 ⇒ 超额永远 None
        codes = sorted({a["code"] for a in alerts} | {bcode})
        bars = _load_bars(codes, d0)
        bench_bars = {b["date"]: b["close"] for b in (bars.get(bcode) or [])}
        phases = _load_phases(alerts)
        rows = _build_rows(alerts, bars, bench_bars, phases)

        # ── 一致性核对：T+5 实时值 vs 库内 outcome_pct（口径漂移要可见）──
        checked = match = 0
        diffs = []
        for r in rows:
            if r["db_outcome"] is None or r.get("ret5") is None:
                continue
            checked += 1
            d = round(float(r["ret5"]) - float(r["db_outcome"]), 2)
            if abs(d) <= OUTCOME_TOL:
                match += 1
            else:
                diffs.append({"date": r["date"], "code": r["code"],
                              "recomputed": r["ret5"], "stored": r["db_outcome"]})

        def _rule_label(items):
            """组内出现最多的 `label` 作组名（同一 rule_id 的 label 固定，取众数防个别脏数据）。"""
            c = Counter((x.get("label") or "").strip() for x in items)
            top = c.most_common(1)[0][0] if c else ""
            return top or items[-1]["rule_id"]

        groups = {
            # 「逻辑错」：哪条规则的期望最差（唯一的 label 维度）
            "by_rule": _group(rows, lambda r: r["rule_id"], _rule_label),
            # 「执行错」：照做 vs 放弃，事后谁更好（放弃代价）
            "by_executed": _group(rows, lambda r: r["executed"] or "未回写"),
            # 「时机错」：同一逻辑在不同主力阶段的表现差异
            "by_phase": _group(rows, lambda r: r["phase"] or "未知"),
            # 「执行错」的成因：放弃理由的事后表现（⚠️ 仅覆盖 executed='no' 子集）
            "by_abandon": _group([r for r in rows if r["executed"] == "no"],
                                 lambda r: r["abandon_reason"] or "（未填理由）"),
        }
        # 自检（验收标准）：按 rule_id 的分组 n 之和 == 总条数
        selftest = {
            "total": len(rows),
            "sum_by_rule": sum(g["n"] for g in groups["by_rule"]),
            "sum_by_executed": sum(g["n"] for g in groups["by_executed"]),
            "sum_by_phase": sum(g["n"] for g in groups["by_phase"]),
        }
        selftest["ok"] = all(selftest[k] == selftest["total"]
                             for k in ("sum_by_rule", "sum_by_executed", "sum_by_phase"))

        # ── 「期望最差」排行（只收 n 足够的组）──
        worst = [g for g in groups["by_rule"] if not g["insufficient"]
                 and g.get("exc5") is not None]
        worst.sort(key=lambda x: x["exc5"])

        n_with_exc = sum(1 for r in rows if r.get("exc5") is not None)
        val = {
            "available": True,
            "window": {"days": days, "start": d0,
                       "end": str(max(a["alert_date"] for a in alerts))[:10]},
            "bench": {"code": bcode, "label": blabel, "fallback": bfb},
            "holds": list(HOLDS),
            "coverage": {
                "alerts_all": total_all,            # 该窗口全部建议（含无标的）
                "with_code": len(alerts),           # 有标的（可算收益）
                "with_exc5": n_with_exc,            # 已到期可评
                "evaluable_pct": (round(n_with_exc / len(alerts) * 100, 1)
                                  if alerts else None),
            },
            "groups": groups,
            "worst": worst[:5],
            "consistency": {"checked": checked, "match": match,
                            "mismatch": len(diffs), "samples": diffs[:5],
                            "note": (f"复核口径：T+5 重算值 vs 库内 outcome_pct，"
                                     f"容差 {OUTCOME_TOL}pt")},
            "selftest": selftest,
            "sentence": _sentence(groups, rows, n_with_exc, blabel, bfb),
            "note": (
                "口径：T+N 收益 = **alert 日收盘 → 其后第 N 个交易日收盘**（与 "
                "`audit.backfill_outcome` 同源）；超额 = 减**同起止日**基准收益；"
                f"基准 {blabel}{'（⚠️ 中证1000 无数据，暂用沪深300 退路）' if bfb else ''}；"
                "缺失（未到期/无柱）跳过、**不填 0**。"
                "⚠️ 本表是**建议类型**归因：实测 `coach_alerts` 里**只有** "
                "loss_over_7pct / stop_loss_hit / hold_3d_review 三类带 `code`"
                "（合计 50 条），而 `gate_add` 等**买入/市场类建议无 `code`** ⇒ "
                "**「入场理由」的个股收益根本无法评估**（数据缺口，不是本模块的取舍）。"
                "闸门 ready 维度亦因零样本不含。"
                "⚠️ **T+20 普遍未到期**（需 21 根 K 线）⇒ 近期为空属正常，随日批推进出现。"
                "⚠️ 阶段「未知」= `mainforce_state` 无该 (code, date) 记录（该表 2026-09-15 起）。"
                f"⚠️ 组内 n<{MIN_N} 标 insufficient、不参与排行；全部 n<{SMALL_N} ⇒ "
                "结论仅**方向参考**（项目纪律：小样本不给结论）。"
                "纯只读聚合、不改任何闸门/权重/准入。"),
        }
        _CACHE.update({"ts": now, "key": key, "val": val})
        return val
    except Exception as e:
        print(f"[attribution] 归因失败: {str(e)[:100]}")          # ASCII（铁律⑥）
        return {"available": False, "reason": f"归因失败：{str(e)[:80]}"}


def _sentence(groups: Dict, rows: List[Dict], n_ok: int, blabel: str, bfb: bool) -> str:
    """规则生成结论（不用 LLM）：分别对应「逻辑错 / 时机错 / 执行错」。

    ⚠️ **执行维度刻意不做方向判定**：本表统计的是**标的自身收益**，而"执行/放弃"对
      **卖出类**建议的实际影响方向相反（执行=不再持有该段收益）⇒ 系统不替用户下结论
      （同 `position_sizing`/`paper_attribution` 的"只给纪律不给指令"惯例）。
    """
    if not n_ok:
        return "样本均未到期（T+5 窗口未走完）⇒ 暂无可评估的建议。"
    parts = []
    # 逻辑错：最差规则
    worst = [g for g in groups["by_rule"] if not g["insufficient"] and g.get("exc5") is not None]
    worst.sort(key=lambda x: x["exc5"])
    if worst:
        w = worst[0]
        parts.append(f"**逻辑**：最差建议类型「{w.get('label') or w['key']}」"
                     f"T+5 超额 {w['exc5']}pt（n={w['n']}）")
    # 执行错：放弃 vs 已执行（只报事实 + 提醒方向）
    ex = {g["key"]: g for g in groups["by_executed"]}
    ey, en = ex.get("yes"), ex.get("no")
    if ey and en and ey.get("exc5") is not None and en.get("exc5") is not None:
        d = round(en["exc5"] - ey["exc5"], 2)
        parts.append(f"**执行**：已执行组标的 T+5 超额 {ey['exc5']}pt（n={ey['n']}）／"
                     f"放弃组 {en['exc5']}pt（n={en['n']}）⇒ 差 {d:+}pt"
                     f"（⚠️ 这是**标的自身收益**：减/清类建议「执行」＝不再持有该段收益，"
                     f"方向与「放弃」相反 ⇒ 须按建议方向解读，本表不替你下判）")
    # 时机错：阶段极差
    ph = [g for g in groups["by_phase"] if not g["insufficient"] and g.get("exc5") is not None]
    if len(ph) >= 2:
        ph.sort(key=lambda x: x["exc5"])
        parts.append(f"**时机**：阶段间极差 {round(ph[-1]['exc5'] - ph[0]['exc5'], 2)}pt"
                     f"（最差 {ph[0]['key']} {ph[0]['exc5']}pt / 最好 {ph[-1]['key']} "
                     f"{ph[-1]['exc5']}pt）")
    tail = (f"⚠️ 全部组 n<{SMALL_N} ⇒ **仅方向参考，不给结论**。"
            if all(g["n"] < SMALL_N for g in groups["by_rule"]) else "")
    return "；".join(parts) + f"。{tail}"
