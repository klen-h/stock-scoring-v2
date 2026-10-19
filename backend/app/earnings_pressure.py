# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】盈利承压监测清单（P2-2，PLAN_RESERVE_SIGNALS §6.6）
================================================================================
【命题（为什么是"盈利端"而不是"估值端"）】
  高油（输入性通胀）+ 高利率（融资成本）最先挤压的是**毛利/净利**，而不是估值倍数；
  盈利下修一旦发生，估值再便宜也拦不住杀业绩。故本模块盯"**成本端挤压**"的可监测清单。

【★ 定位：监测清单，不是买卖信号（不预设方向）】
  · **不进决策链**：不改评分、不参与排序、不拦闸门、不推"卖出建议"。
  · 只回答"**哪些票的毛利率正在被挤**、集中在哪些行业" ⇒ 供人工/LLM 参考。
  · 要升级为信号必须走预登记 + 预测力检验（项目铁律，见 PLAN §6.3 的范式）。
  · ⚠️ 高油/高利率**不等于**这些票一定跌（部分公司是**受益方**，如上游资源/油服）——
    本模块**不做行业方向的先验假设**，只列"毛利在挤"的事实 + 行业分布，让读者自己判断。

【数据源（都是既有的，零新增依赖）】
  · `stock_finance`（本项目已有表，含**多报告期**）：gross_margin / net_margin /
    revenue_yoy / profit_yoy / roe —— 实测覆盖 3 个报告期（2025-06-30 / 2025-09-30 /
    **2026-06-30**），共 1.87 万行 / 6584 只 ⇒ **可做同期同比**（中报 vs 中报，口径一致 ✓）。
  · `stock_industry`（49 个行业）：算**行业中位毛利率**，避免"银行 92% vs 制造 15%"的
    跨行业误判（同 `scoring/engine._industry_score` 的既有取舍）。
  · `macro_daily_history`（P0-1 建）：布伦特 / 美债 10Y —— 用**分位口径**给宏观压力定级
    （复用 P0-1 的重标定思路，不拍绝对阈值）。
  · 次选（本版未用，留作交叉验证）：`stock_finance_zz.ind_json.gross_profit_margin`
    —— 实测**只有最新一期**（5389 只 / 单期）⇒ 算不了同比，故主口径用 `stock_finance`。

【承压分（0~100，规则透明、可复现）】
  Δgm = 毛利率(最新报告期) − 毛利率(去年同期同口径)          ← **必备项：必须下滑**
  · Δgm ≤ −5pt：+40（显著下滑）；Δgm < 0：+20
  · 毛利率低于行业中位数：+20（相对弱势）
  · 净利同比 < 0：+20 ；营收同比 < 0：+20
  入清单门槛：**质量闸通过 且 Δgm < 0 且 总分 ≥ 60**；按分数降序、同分按 Δgm 升序。

  ★★ **质量闸（2026-10-10 首跑实测后补 —— 这条比打分本身更重要）**：
    第一版没有它，榜单前几名是：金迪克（毛利率 **−279%**、同比 −373.8pt）、*ST岭南（−161.9%）、
    *ST仕净（−124.8%）、云南旅游（−142.8%）—— 全是**已经崩掉**的壳/深亏公司。那是
    "经营已经崩了"，**不是**本模块要盯的"正常经营的公司正被成本端挤"；且毛利率为负时
    "同比变化/低于行业中位"这类**比值口径本身失效**（负数比较无意义）。四条闸：
      ① 当期毛利率 > 0；② 当期净利率 ≥ −5%；③ 名称不含 ST / 退（ST 有独立风险制度且会霸榜）；
      ④ **幅度闸** Δgm ≥ −25pt（更深的跌幅多为数据异常/主营崩坏）。
  ⚠️ 为什么门槛里强制"毛利率同比下滑"：只靠"低毛利 + 差的业绩"会把大量**本来就在亏的**
     公司捞进来 ⇒ 必须有"**正在变化**"的证据（本模块只报**边际恶化**）。

【口径局限（下结论前必读）】
  1. `stock_finance` 只有 3 个报告期（不是完整季度序列）⇒ 同比是"最新 vs 去年同期"，
     **无法看更长的趋势**；数据齐不齐取决于上游同步（`scripts/zz_finance_sync.py`）。
  2. 毛利率受**行业结构/会计口径/一次性项目**影响大 ⇒ 只做**行业内相对**与**同比边际**，
     不做跨行业绝对比较；**不做**"高油必然挤压某行业"的方向断言。
  3. 未接市值/流动性过滤（避免多读一份全市场快照）⇒ 清单里可能有小票；
     名称含「退」的退市整理股已剔除。
  4. 只读、不写任何生产表；整表读 `stock_finance`（窄列 ~1.9 万行 ≈ 1MB）+ 行业映射
     （~5900 行）⇒ 一周一次可忽略（带进程内缓存）。
================================================================================
"""
import time
from typing import Dict, List, Optional

from app.database import db

_CACHE = {"ts": 0.0, "val": None}
_CACHE_TTL = 6 * 3600          # 进程内 6 小时（财报季度更新，日频扫描也够）
MIN_SCORE = 60                 # 入清单门槛
MIN_BASE_DAYS = 300            # 同比基线：报告期相隔 ≥300 天（容节假日/口径微差）

# ── ★★ 质量闸（2026-10-10 首跑实测后补；**为什么必须加**）───────────────────────
#   第一版没有这三条，结果榜单前几名是：金迪克（毛利率 **−279%**，同比 −373.8pt）、
#   *ST岭南（−161.9%）、*ST仕净（−124.8%）、云南旅游（−142.8%）—— 全是**已经崩掉**的
#   壳/深亏公司。那是"经营已经崩了"，**不是**本模块要盯的"正常经营的公司正被成本端挤"；
#   而且毛利率为负时"同比变化/低于行业中位"这些**比值口径本身就失效**（负数比较无意义）。
#   ⇒ 三条闸：① 当期毛利率 > 0；② 当期净利率 ≥ −5%（排除严重亏损）；
#     ③ 名称不含 ST / 退（ST 有独立风险制度，且会霸榜）。
#   ⇒ 另加**幅度闸** `MAX_DROP`：Δgm < −25pt 的多为数据异常/主营崩坏（同上），剔除。
MIN_GM = 0.0                   # ① 当期毛利率必须 > 0
MIN_NET_MARGIN = -5.0          # ② 当期净利率 ≥ −5%
MAX_DROP = 25.0                # 幅度闸：Δgm 不低于 −25pt

# 行业压力的"关注度"（仅用于报告排序提示，**不参与个股打分**、不做方向断言）
_KEY_INDUSTRIES = ("化学", "化工", "交运", "交通", "建材", "建筑", "电力", "有色",
                   "钢铁", "汽车", "家电", "纺织", "轻工", "食品", "农林")


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None        # 去 NaN


def _macro_level() -> Dict:
    """宏观压力定级（分位口径，复用 P0-1 思路；取不到 ⇒ unknown，不猜）。"""
    out = {"brent": None, "brent_pct": None, "us10y": None, "us10y_pct": None,
           "level": "unknown", "note": ""}
    try:
        from app import macro_daily
        for code, key, pkey in (("brent", "brent", "brent_pct"),
                                ("us10y", "us10y", "us10y_pct")):
            s = macro_daily.load(code) or []
            vals = [_num(r.get("close")) for r in s]
            vals = [v for v in vals if v is not None]
            if not vals:
                continue
            cur = vals[-1]
            out[key] = round(cur, 2)
            out[pkey] = round(100.0 * sum(1 for v in vals if v <= cur) / len(vals), 1)
    except Exception as e:
        out["note"] = "宏观读取失败: %s" % str(e)[:60]
    hi = [out["brent_pct"], out["us10y_pct"]]
    hi = [h for h in hi if h is not None]
    if len(hi) >= 2 and all(h >= 85 for h in hi):
        out["level"] = "high"
        out["note"] = out["note"] or "油价与利率同时处于 5.5 年 **≥85 分位** ⇒ 成本端双重挤压"
    elif hi and any(h >= 85 for h in hi):
        out["level"] = "mid"
        out["note"] = out["note"] or "油价或利率其一 ≥85 分位 ⇒ 成本端单向挤压"
    elif hi:
        out["level"] = "low"
        out["note"] = out["note"] or "油价与利率均未到 85 分位 ⇒ 成本端压力暂不突出"
    return out


def _periods() -> List[str]:
    try:
        rows = db.fetch("SELECT report_date, COUNT(*) AS n FROM stock_finance "
                        "GROUP BY report_date ORDER BY report_date DESC")
    except Exception as e:
        print(f"[earnings] 报告期读取失败: {str(e)[:80]}")       # ASCII（铁律⑥）
        return []
    return [str(r["report_date"])[:10] for r in (rows or [])]


def _same_period_last_year(cur: str, allp: List[str]) -> Optional[str]:
    """去年同期同口径报告期（如 2026-06-30 → 2025-06-30）。"""
    from datetime import datetime
    try:
        d0 = datetime.strptime(cur, "%Y-%m-%d")
    except ValueError:
        return None
    for p in allp:
        try:
            d1 = datetime.strptime(p, "%Y-%m-%d")
        except ValueError:
            continue
        if (d0 - d1).days >= MIN_BASE_DAYS and d1.month == d0.month and d1.day == d0.day:
            return p
    return None


def _load_rows(periods: List[str]) -> List[Dict]:
    rows = db.fetch(
        "SELECT code, name, report_date, gross_margin, net_margin, revenue_yoy, "
        "profit_yoy, roe FROM stock_finance WHERE report_date = ANY(%s)",
        (periods,)) or []
    return rows


def _industry_of() -> Dict[str, str]:
    # ★ 2026-10-19（egress）：共享 1h 缓存（原每次整表读 ≈1MB；映射为月级重建）。
    from app.industry_map import get_industry_map
    return get_industry_map()


def _trim(d: Dict, limit: int) -> Dict:
    """按 limit 截断**返回副本**（缓存里始终存全量）。"""
    out = dict(d)
    out["list"] = (d.get("list") or [])[:max(1, int(limit))]
    return out


def scan(limit: int = 30, refresh: bool = False) -> Dict:
    """盈利承压清单（**监测口径，不预设方向**）。返回 dict（可直接 JSON 化）。

    ★ 缓存的是**全量清单**，`limit` 只在返回时截断 —— 首跑实测踩到：第一版把 limit 也
      缓存进去了，先来一个 `limit=12` 的调用会把缓存写成 12 条，之后 `/api` 不论传多少
      都只拿到那 12 条（**参数被缓存静默吃掉**）。
    """
    now = time.time()
    if not refresh and _CACHE["val"] is not None and now - _CACHE["ts"] < _CACHE_TTL:
        return _trim(_CACHE["val"], limit)
    macro = _macro_level()
    allp = _periods()
    if not allp:
        return {"as_of": "", "macro": macro, "n_scanned": 0, "n_pressed": 0,
                "list": [], "industries": [], "note": "无财报数据"}
    cur = allp[0]
    base = _same_period_last_year(cur, allp)
    need = [p for p in (cur, base) if p]
    rows = _load_rows(need)
    ind_of = _industry_of()

    gm_now: Dict[str, float] = {}
    recs: Dict[str, Dict] = {}
    for r in rows:
        code = r.get("code")
        if not code:
            continue
        gm = _num(r.get("gross_margin"))
        d = {
            "code": code, "name": (r.get("name") or "").strip(),
            "gm": gm, "net_margin": _num(r.get("net_margin")),
            "revenue_yoy": _num(r.get("revenue_yoy")),
            "profit_yoy": _num(r.get("profit_yoy")), "roe": _num(r.get("roe")),
            "gm_prev": None, "d_gm": None,
            "industry": ind_of.get(code, ""),
        }
        if str(r.get("report_date"))[:10] == cur:
            recs[code] = d
            if gm is not None:
                gm_now[code] = gm
        elif base and str(r.get("report_date"))[:10] == base:
            old = recs.get(code)
            if old is not None:            # 顺序不定：同行可能先到
                old["gm_prev"] = gm
            else:
                recs.setdefault(code, {"code": code, "name": (r.get("name") or "").strip(),
                                       "industry": ind_of.get(code, ""), "gm": None,
                                       "gm_prev": gm})

    # 行业中位毛利率（用**当期**数据，样本 ≥8 只才算，避免小行业噪音）
    by_ind: Dict[str, List[float]] = {}
    for code, gm in gm_now.items():
        ind = ind_of.get(code) or ""
        if ind:
            by_ind.setdefault(ind, []).append(gm)
    med: Dict[str, float] = {}
    for ind, vals in by_ind.items():
        if len(vals) >= 8:
            vals = sorted(vals)
            n = len(vals)
            med[ind] = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2

    pressed, scanned = [], 0
    for code, d in recs.items():
        gm, gm_prev = d.get("gm"), d.get("gm_prev")
        if gm is None or gm_prev is None:
            continue
        scanned += 1
        # ★★ 质量闸（见文件头 `MIN_GM` 处的实测说明）：先排除"已崩的壳/深亏公司"，
        #    否则榜首全是负毛利率（比值口径失效）的票。
        _nm = d.get("net_margin")
        _name = d.get("name") or ""
        if gm <= MIN_GM or (_nm is not None and _nm < MIN_NET_MARGIN):
            continue
        if "ST" in _name or "退" in _name:
            continue
        d_gm = gm - gm_prev
        d["d_gm"] = round(d_gm, 2)
        # ★ 只报**边际恶化**（必备条件）；跌幅超 `MAX_DROP` 的多为数据异常/主营崩坏 ⇒ 剔除
        if d_gm >= 0 or d_gm < -MAX_DROP:
            continue
        score, reasons = 0, []
        if d_gm <= -5:
            score += 40
            reasons.append("毛利率同比 %+.1fpt（显著下滑）" % d_gm)
        else:
            score += 20
            reasons.append("毛利率同比 %+.1fpt" % d_gm)
        ind = d.get("industry") or ""
        if ind in med and gm < med[ind]:
            score += 20
            reasons.append("低于行业中位 %.1fpt" % (gm - med[ind]))
        if d.get("profit_yoy") is not None and d["profit_yoy"] < 0:
            score += 20
            reasons.append("净利同比 %.1f%%" % d["profit_yoy"])
        if d.get("revenue_yoy") is not None and d["revenue_yoy"] < 0:
            score += 20
            reasons.append("营收同比 %.1f%%" % d["revenue_yoy"])
        d["score"] = score
        d["reasons"] = reasons
        d["ind_median"] = round(med[ind], 2) if ind in med else None
        d["key_industry"] = any(k in ind for k in _KEY_INDUSTRIES)
        if score >= MIN_SCORE and "退" not in (d.get("name") or ""):
            pressed.append(d)

    pressed.sort(key=lambda x: (-x["score"], x.get("d_gm") or 0))
    cnt: Dict[str, int] = {}
    for d in pressed:
        cnt[d.get("industry") or "（未映射）"] = cnt.get(d.get("industry") or "（未映射）", 0) + 1
    inds = sorted(({"industry": k, "n": v} for k, v in cnt.items()),
                  key=lambda x: -x["n"])
    out = {
        "as_of": cur, "base_period": base or "", "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "macro": macro, "n_scanned": scanned, "n_pressed": len(pressed),
        "industries": inds, "list": pressed,          # ★ 缓存**全量**；截断在 `_trim` 里做
        "threshold": {"min_score": MIN_SCORE, "must_decline": True,
                      "min_gm": MIN_GM, "min_net_margin": MIN_NET_MARGIN,
                      "max_drop": MAX_DROP, "exclude_st": True},
        "note": ("监测清单，**不是卖出信号**：只报「毛利率同比下滑 + 业绩恶化」的事实，"
                 "不改评分/不参与排序/不进闸门。高油高利率同时是部分行业（上游资源/油服）的"
                 "**受益**环境 ⇒ 本模块不做方向断言，请结合行业分布自行判断。"
                 "已剔除毛利率≤0 / 净利率<−5% / ST / 退市整理 / 同比跌幅 >25pt 的票"
                 "（那是「已经崩」，不是「正在被挤」）。"),
    }
    _CACHE.update(ts=now, val=out)
    return _trim(out, limit)


def markdown(limit: int = 30) -> str:
    """承压清单 markdown（供日批落库 / 日报引用）。"""
    d = scan(limit=limit)
    m = d.get("macro") or {}
    lines = ["### 盈利承压监测（滞胀挤压）", "",
             "> **监测口径，不是卖出信号**（不改评分/不排序/不进闸门）；"
             "判据：毛利率同比下滑 + 总分 ≥%d。" % (d.get("threshold") or {}).get("min_score", 60),
             ""]
    lines.append("**宏观背景**：油 %s（%s 分位）｜10Y %s（%s 分位）⇒ **%s**"
                 % (m.get("brent"), m.get("brent_pct"), m.get("us10y"),
                    m.get("us10y_pct"), {"high": "双重挤压", "mid": "单向挤压",
                                         "low": "压力不突出"}.get(m.get("level"), "未知")))
    lines.append("")
    lines.append("**报告期**：%s（同比基线 %s）｜扫描 %s 只（有同比可比）⇒ **承压 %s 只**"
                 % (d.get("as_of"), d.get("base_period") or "-", d.get("n_scanned"),
                    d.get("n_pressed")))
    lines.append("")
    if d.get("industries"):
        top = "、".join("%s %d" % (x["industry"], x["n"]) for x in d["industries"][:8])
        lines.append("**行业分布（前 8）**：%s" % top)
        lines.append("")
    lines.append("| 代码 | 名称 | 行业 | 毛利率 | 同比 | 行业中位 | 净利同比 | 分 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in d.get("list") or []:
        lines.append("| %s | %s | %s | %s | %+.1f | %s | %s | %d |"
                     % (r.get("code"), r.get("name"), r.get("industry") or "-",
                        ("%.1f" % r["gm"]) if r.get("gm") is not None else "-",
                        r.get("d_gm") or 0,
                        ("%.1f" % r["ind_median"]) if r.get("ind_median") is not None else "-",
                        ("%.1f%%" % r["profit_yoy"]) if r.get("profit_yoy") is not None else "-",
                        r.get("score") or 0))
    lines.append("")
    lines.append("> %s" % (d.get("note") or ""))
    return "\n".join(lines)
