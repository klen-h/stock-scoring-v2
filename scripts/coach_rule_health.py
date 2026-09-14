#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】教练规则健康度周报（只读，零行情回源）
================================================================================

背景（2026-09-15）：
  Coach 系统 09-13 上线，9 条规则里 7 条仍「只落库不推送」——处于**攒样本**阶段。
  此时细化规则/调阈值 = 在零数据上拍脑袋（同类教训：白名单用「胜率≥55%」算出空集）。
  本脚本不产生任何建议，只把「该不该调参」的**证据**摊开，供 W3 末复盘时决策。

盯三个信号（用户 2026-09-15 认可）：
  【信号 1】规则触发率异常 —— 某条长期零触发（阈值太松/上游数据问题）
            或天天触发（阈值太紧 → 警报疲劳 → 用户免疫）
  【信号 2】放弃理由高频项 —— 用户反复在同一纪律点失守，最该被关注的规则
  【信号 3】守纪律 vs 不守纪律 —— 「未按剧本」反而更赚 → 说明止损位/到期天数定错了

另附执行一致性 / 预承诺执行率两个 KPI 作为对照基准。

★ 只读：全部读 coach_alerts / coach_plans / paper_positions 三张小表，
  不读 K 线、不回源行情。可直接在本地或 Render 上跑。

用法：
  python scripts/coach_rule_health.py                # 默认近 30 天
  python scripts/coach_rule_health.py --days 7       # 近 7 天
  python scripts/coach_rule_health.py --json         # 输出 JSON（供自动化/日报引用）

解读纪律：
  样本不足时（< 5 个有记录交易日）脚本会明确标注「样本不足，勿据此调参」——
  此时任何触发率/收益差都不具统计意义。
================================================================================
"""
import argparse
import json
import os
import sys
from datetime import timedelta

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, BACKEND)

# 读 backend/.env 注入 DATABASE_URL 等（与其它 scripts 一致）
_env = os.path.join(BACKEND, ".env")
if os.path.exists(_env):
    for _line in open(_env, encoding="utf-8"):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from app.database import db                      # noqa: E402
from app.coach import audit                      # noqa: E402
from app.coach import rules as coach_rules       # noqa: E402
from app.strategies import paper_trading         # noqa: E402

MIN_ACTIVE_DAYS = 5          # 有记录交易日 < 此值 → 样本不足，勿调参


def _fmt(v, suffix="", nd=2):
    if v is None:
        return "—"
    return f"{v:.{nd}f}{suffix}" if isinstance(v, (int, float)) else str(v)


def _load_rules():
    """读 rules.yaml 全量规则清单（含未触发的，用于发现「零触发」）。"""
    cfg = coach_rules.load_config() or {}
    out = []
    for r in cfg.get("rules") or []:
        out.append({
            "id": r.get("id"),
            "label": r.get("label") or r.get("id"),
            "push": bool(r.get("push")),
            "enabled": r.get("enabled", True) is not False,
        })
    return out


def _active_days(days: int) -> int:
    """近 N 天内「教练实际有产出」的交易日数（以 coach_alerts 的 distinct alert_date 近似）。"""
    r = db.fetch_one(
        "SELECT COUNT(DISTINCT alert_date) AS d FROM coach_alerts WHERE alert_date >= %s",
        (audit._days_ago(days),))
    return int((r or {}).get("d") or 0)


def _trigger_stats(days: int) -> dict:
    """每条规则：触发次数 / 触发天数 / 已推送数。"""
    rows = db.fetch(
        "SELECT rule_id, COUNT(*) AS cnt, COUNT(DISTINCT alert_date) AS d, "
        "SUM(CASE WHEN pushed=1 THEN 1 ELSE 0 END) AS pushed_cnt "
        "FROM coach_alerts WHERE alert_date >= %s GROUP BY rule_id",
        (audit._days_ago(days),)) or []
    return {r["rule_id"]: r for r in rows}


def _reason_rank(days: int, limit: int = 10):
    """放弃理由聚合（高频 = 最易失守的纪律点）。"""
    rows = audit.abandon_reasons(limit=200) or []
    cutoff = audit._days_ago(days)
    agg = {}
    for r in rows:
        if (r.get("alert_date") or "") < cutoff:
            continue
        reason = (r.get("abandon_reason") or "").strip()
        if not reason:
            continue
        a = agg.setdefault(reason, {"count": 0, "last": "", "rules": set()})
        a["count"] += 1
        a["last"] = max(a["last"], r.get("alert_date") or "")
        if r.get("label"):
            a["rules"].add(r["label"])
    order = sorted(agg.items(), key=lambda kv: -kv[1]["count"])
    return [{"reason": k, "count": v["count"], "last": v["last"],
             "rules": sorted(v["rules"])} for k, v in order[:limit]]


def build_report(days: int) -> dict:
    active = _active_days(days)
    stats = _trigger_stats(days)
    rules = _load_rules()
    sufficient = active >= MIN_ACTIVE_DAYS

    # ── 信号 1：规则触发率 ────────────────────────────────────────────────
    rule_rows = []
    for rule in rules:
        st = stats.get(rule["id"]) or {}
        cnt = int(st.get("cnt") or 0)
        d = int(st.get("d") or 0)
        pushed = int(st.get("pushed_cnt") or 0)
        if not rule["enabled"]:
            status = "已停用"
        elif cnt == 0:
            # ★ 样本不足时零触发不具统计意义（09-13 才上线，多数规则还没轮到）
            status = "零触发" if sufficient else "未判定"
        elif sufficient and d >= active:
            status = "天天触发"
        else:
            status = "正常"
        rule_rows.append({**rule, "count": cnt, "days": d,
                          "pushed": pushed, "status": status})
    anomalies = [r for r in rule_rows if r["status"] in ("零触发", "天天触发")]

    # ── 信号 2：放弃理由 ─────────────────────────────────────────────────
    reasons = _reason_rank(days)

    # ── 信号 3：守纪律 vs 不守纪律 ───────────────────────────────────────
    attr = paper_trading.paper_attribution() or {}
    by_plan = attr.get("by_plan_followed") or {}
    plan_cmp = None
    followed = by_plan.get("按剧本离场")
    broken = by_plan.get("未按剧本/放弃")
    if followed and broken and followed.get("avg_pnl_pct") is not None \
            and broken.get("avg_pnl_pct") is not None:
        diff = round(broken["avg_pnl_pct"] - followed["avg_pnl_pct"], 2)
        plan_cmp = {
            "followed_avg": followed["avg_pnl_pct"],
            "broken_avg": broken["avg_pnl_pct"],
            "diff": diff,
            # 未按剧本反而更好 → 规则阈值可能定错（这是该调参的硬证据）
            "warn_rule_misconfigured": diff > 0,
            "note": ("「未按剧本」收益更高 → 止损位/到期天数可能定错了"
                     if diff > 0 else
                     "「按剧本」收益不低于不守纪律 → 纪律有效"),
        }

    # ── 辅助 KPI ─────────────────────────────────────────────────────────
    consistency = audit.execution_consistency(days=days)
    exec_rate = audit.plan_execution_rate(days=days)

    # ── 结论 ─────────────────────────────────────────────────────────────
    if not sufficient:
        verdict = (f"样本不足（有记录交易日 {active} < {MIN_ACTIVE_DAYS}）："
                   "任何触发率/收益差都不具统计意义，**勿据此调参**，继续攒样本。")
    elif anomalies or reasons or (plan_cmp and plan_cmp["warn_rule_misconfigured"]):
        verdict = "出现可跟进信号（见下）——按「哪个信号 → 动哪条规则」逐条核对。"
    else:
        verdict = "无异常信号：触发率分布正常、无高频放弃理由、纪律有效。维持现状。"

    return {
        "window_days": days,
        "active_days": active,
        "sample_sufficient": sufficient,
        "signals": {
            "rule_triggers": rule_rows,
            "anomalies": anomalies,
            "abandon_reasons": reasons,
            "plan_compare": plan_cmp,
        },
        "kpi": {"execution_consistency": consistency, "plan_execution_rate": exec_rate},
        "verdict": verdict,
    }


def render(r: dict) -> None:
    days = r["window_days"]
    print("=" * 72)
    print(f"教练规则健康度周报（近 {days} 天）  生成于 {audit._today()}")
    print("=" * 72)
    print(f"有教练记录交易日 = {r['active_days']}   "
          f"样本判定 = {'充足' if r['sample_sufficient'] else '不足（勿据此调参）'}")

    print("\n【信号 1】规则触发率（异常检测）")
    print(f"  {'规则id':<20}{'中文':<18}{'次数':>5}{'天数':>5}{'已推':>5}  状态")
    for x in r["signals"]["rule_triggers"]:
        print(f"  {x['id']:<20}{x['label']:<18}{x['count']:>5}{x['days']:>5}"
              f"{x['pushed']:>5}  {x['status']}")
    an = r["signals"]["anomalies"]
    if an:
        for a in an:
            if a["status"] == "零触发":
                print(f"    [零触发] {a['id']}：可能阈值太松，或上游数据/市场条件未满足")
            else:
                print(f"    [天天触发] {a['id']}：可能阈值太紧 → 警报疲劳（用户会免疫）")
    elif not r["sample_sufficient"]:
        print("    未判定异常（样本不足）：零触发/高频统计此时不具意义")
    else:
        print("    无异常")

    print("\n【信号 2】放弃理由高频项（高频 = 最易失守的纪律点）")
    rs = r["signals"]["abandon_reasons"]
    if rs:
        print(f"  {'次数':>4}  最近        理由")
        for x in rs:
            print(f"  {x['count']:>4}  {x['last']}  {x['reason']}")
            if x["rules"]:
                print(f"        触发规则：{'、'.join(x['rules'])}")
    else:
        print("    窗口内无放弃理由（可能是还没人回写，或用户都照做了）")

    print("\n【信号 3】守纪律 vs 不守纪律（按剧本执行归因）")
    pc = r["signals"]["plan_compare"]
    if pc:
        print(f"  按剧本离场   平均 {_fmt(pc['followed_avg'], '%')}")
        print(f"  未按剧本/放弃 平均 {_fmt(pc['broken_avg'], '%')}")
        print(f"  差值（未按剧本 − 按剧本）= {pc['diff']:+.2f}pp")
        print(f"  → {pc['note']}")
    else:
        print("    数据不足：需要同时存在「按剧本离场」与「未按剧本/放弃」两类平仓样本")
        print("    （当前 by_plan_followed 多为「无剧本」——剧本 09-13 才上线，属正常历史缺口）")

    print("\n【辅助 KPI】")
    c = r["kpi"]["execution_consistency"]
    print(f"  执行一致性：已决策 {c.get('decided', 0)} / 未响应 {c.get('ignored', 0)} / "
          f"执行率 {_fmt(c.get('exec_rate_pct'), '%', 1)} / 放弃率 {_fmt(c.get('abandon_rate_pct'), '%', 1)}")
    e = r["kpi"]["plan_execution_rate"]
    print(f"  预承诺执行率：已结算 {e.get('plans_settled', 0)}"
          f"（平仓 {e.get('plans_closed', 0)} / 放弃 {e.get('plans_abandoned', 0)}）"
          f" / 持仓中 {e.get('plans_open', 0)}"
          f" / 按剧本离场率 {_fmt(e.get('follow_rate_pct'), '%', 1)}")

    print("\n【结论】")
    print("  " + r["verdict"])
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="教练规则健康度周报（只读）")
    ap.add_argument("--days", type=int, default=30, help="统计窗口天数（默认 30）")
    ap.add_argument("--json", action="store_true", help="输出 JSON 而非文本")
    args = ap.parse_args()
    days = max(1, min(args.days, 365))

    audit.ensure_tables()
    r = build_report(days)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        render(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
