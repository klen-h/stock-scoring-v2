# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】洗盘 vs 破位 打分（Washout Score）—— **只给证据，不给结论**
================================================================================
背景（2026-11-05/06，用户："底部震荡常匪夷所思、散户易被洗下车"）：
  功能 1（`range_watch`）解决了"这个跌幅在箱体什么位置"；
  但仍有一个问题：**到了区间下沿，怎么知道是"洗"还是"破"？**

  而系统现状是：`−8%` 触发止损时，给用户的只有一句"止损位已破（以上为事实）"，
  **没有任何关于"这个位置破位可信度"的信息** —— 68% 的止损可能是误伤，
  但用户看到的是同一个红字。

★ 本模块只做一件事：把"洗盘 vs 破位"这个判断拆成 **5 个可观测的分项**，
  各自给证据，最后汇总成一个 −4 ~ +5 的分数。**不输出"该卖/该拿"。**

  为什么坚持不给结论：
    · "洗盘"与"刚开始下跌"在头 3~5 天**数据上无法区分**（这是形态的本质，不是模型不行）；
    · 任何声称能即时识别洗盘的规则，都是事后叙述 —— 与 `thesis_tracker`
      拒绝 LLM 裁决是同一条原则；
    · 项目一贯立场：事实摆清楚，判断权归人（同 `flash_brief` 的"不给买卖建议"红线）。

--------------------------------------------------------------------------------
五个分项（每项 −2 ~ +1，0 表示数据不足/中性）
--------------------------------------------------------------------------------
  ① 量能     缩量（vol_ratio < 0.85）+1     放量（> 1.3）−1
               洗盘靠"磨"不靠"砸"，主力不出货就不会放量。
  ② 收盘位置 收盘仍在区间内（pos > 0）+1     已跌穿（pos < 0）−2
               ★ 权重最高：盘中刺破后收回是洗盘，收在下沿之下是共识。
               ★ 用收盘不用最低价 —— 盘中价格是噪音，收盘价才是共识。
  ③ 主力 5 日 净流出 < 1 亿（> −1）+1        净流出 > 3 亿（< −3）−1
               震荡期单日资金流噪声大，所以看 5 日累计，不看单日。
  ④ 行业同步 同行业破位率 < 30% +1           > 60% −1
               个股洗盘 vs 系统性下跌。行业样本 < 5 只时该分项**跳过**（不给分）。
  ⑤ 跌幅形态 5 日急杀（≤ −8%）+1            20 日阴跌（≤ −20%）−1
               洗盘是快速下杀后横住；破位是逐日新低。
               ⚠️ 两者互斥判断：**先判阴跌**（趋势性），阴跌成立就不再给急杀的分。

★ 分数只是"证据条数"的汇总，不是概率。`+3` 不等于"70% 是洗盘"。
================================================================================
"""
from typing import Dict, List, Optional

from app.database import db

# 行业同步率的最小样本数：低于此人数的行业不给比例（噪音大于信号）
CTX_MIN_MEMBERS = 5

# 阈值（集中在此，便于回测校准；改动必须同步 docstring）
#
# ★★ 2026-11-06 首版实测校准（重要）：初版阈值太松，132 只样本里 **118 只(89%)**
#   被判"偏洗盘"，分数集中在 +3/+4 —— **完全没有区分度，等于没做**。
#   根因是把"正常"当成了"证据"：
#     · `收盘位置 pos >= 0` 给 +1 ⇒ 只要没跌穿就得分（这是**前提**，不是证据）
#     · 量比 <0.85 / 主力 >−1亿 / 行业 <30% 都太容易满足
#   ⇒ 收紧：缩量要 <0.75、主力要 >−0.5亿、行业要 <20%，
#     且"收盘仍在区间内"**改为 0 分**（只在跌穿时扣分）。
#   ⚠️ 这只是"让分布散开"的初校准，**不是经过回测验证的最优值** ——
#     真正确认要看"washout 高分组的 10 日收回率是否显著高于低分组"（见方案 §5.2）。
VOL_SHRINK = 0.75        # 量比 < 此值 ⇒ 明显缩量
VOL_EXPAND = 1.3         # 量比 > 此值 ⇒ 放量
FLOW_OK = -0.5           # 主力 5 日净流出小于 0.5 亿 ⇒ 主力几乎没走
FLOW_BAD = -3.0          # 净流出大于 3 亿 ⇒ 持续流出
IND_CALM = 0.20          # 行业破位率 < 20% ⇒ 个股行为
IND_STORM = 0.60         # 行业破位率 > 60% ⇒ 系统性下跌
DROP_FAST = -8.0         # 5 日跌 ≥8% ⇒ 急杀（洗盘特征）
DROP_SLOW = -20.0        # 20 日跌 ≥20% ⇒ 阴跌（破位特征）

VERDICT_WASH = 3         # 总分 ≥ 此值 ⇒ 偏洗盘（满分 4）
VERDICT_BREAK = -2       # 总分 ≤ 此值 ⇒ 偏破位


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_context(day: str) -> Dict[str, float]:
    """行业破位比例 {industry: broke_ratio}（一次聚合，供 ④ 用）。"""
    try:
        rows = db.fetch(
            "SELECT industry, COUNT(*) AS n, "
            "SUM(CASE WHEN state = '破位' THEN 1 ELSE 0 END) AS b "
            "FROM range_watch WHERE date = %s AND industry <> '' "
            "GROUP BY industry", (day,))
    except Exception as e:
        print(f"[washout] context failed: {str(e)[:80]}")
        return {}
    out: Dict[str, float] = {}
    for r in rows or []:
        n = int(r.get("n") or 0)
        b = int(r.get("b") or 0)
        if n >= CTX_MIN_MEMBERS:
            out[str(r.get("industry"))] = round(b / n, 3)
    return out


def load_stats(codes: List[str]) -> Dict[str, Dict]:
    """从 `stock_facts_wide` 取 flow5 / ret5 / ret20（只读最新一天）。

    ⚠️ `range_watch` 与 `stock_facts_wide` 的日期可能不同（数据包滞后），
       这里取 stock_facts 自己的最新日，并在返回值里带 `_stats_date` 供调用方标注。
    """
    codes = [str(c) for c in (codes or []) if c]
    if not codes:
        return {}
    try:
        d = str((db.fetch_one(
            "SELECT MAX(date) AS d FROM stock_facts_wide") or {}).get("d") or "")[:10]
    except Exception:
        return {}
    if not d:
        return {}
    ph = ",".join(["%s"] * len(codes))
    try:
        rows = db.fetch(
            f"SELECT code, flow5_amt_yi, ret5, ret20 FROM stock_facts_wide "
            f"WHERE date = %s AND code IN ({ph})", tuple([d] + codes))
    except Exception as e:
        print(f"[washout] stats failed: {str(e)[:80]}")
        return {}
    return {str(r["code"]): {
        "flow5": _f(r.get("flow5_amt_yi")),
        "ret5": _f(r.get("ret5")),
        "ret20": _f(r.get("ret20")),
        "_stats_date": d,
    } for r in rows or []}


def score_row(it: Dict, ctx: Dict[str, float],
              st: Optional[Dict] = None) -> Dict:
    """单只票打分。返回 {score, signals, verdict, n_signals}。

    `signals` 每项：{key, label, value, side(washout|break|neutral|na), delta, why}
    """
    st = st or {}
    sigs: List[Dict] = []
    total = 0

    def add(key, label, value, side, delta, why):
        nonlocal total
        total += delta
        sigs.append({"key": key, "label": label, "value": value,
                     "side": side, "delta": delta, "why": why})

    # ① 量能
    vr = it.get("vol_ratio")
    if vr is None:
        add("vol", "量能", "—", "na", 0, "无成交量数据")
    elif vr < VOL_SHRINK:
        add("vol", "量能", f"量比 {vr:.2f}", "washout", 1, "缩量下跌，主力未出")
    elif vr > VOL_EXPAND:
        add("vol", "量能", f"量比 {vr:.2f}", "break", -1, "放量下跌，出货特征")
    else:
        add("vol", "量能", f"量比 {vr:.2f}", "neutral", 0, "量能中性")

    # ② 收盘位置（**惩罚项，不是加分项**）
    #   ★ 2026-11-06 校准：初版 pos >= 0 给 +1，导致"没跌穿"被当成洗盘证据 ——
    #     而它只是**前提条件**（都到下沿了当然还没穿）。给分会让几乎所有票白拿 1 分。
    pos = it.get("pos")
    if pos is None:
        add("close", "收盘位置", "—", "na", 0, "无区间数据")
    elif pos < 0:
        add("close", "收盘位置", f"pos {pos:.2f}", "break", -2,
            "收盘已跌穿区间下沿（不是盘中刺破）")
    else:
        add("close", "收盘位置", f"pos {pos:.2f}", "neutral", 0,
            "收盘仍在区间内（前提，不计分）")

    # ③ 主力 5 日
    f5 = st.get("flow5")
    if f5 is None:
        add("flow", "主力5日", "—", "na", 0, "无资金流数据")
    elif f5 > FLOW_OK:
        add("flow", "主力5日", f"{f5:+.2f}亿", "washout", 1, "主力未持续流出")
    elif f5 < FLOW_BAD:
        add("flow", "主力5日", f"{f5:+.2f}亿", "break", -1, "主力持续净流出")
    else:
        add("flow", "主力5日", f"{f5:+.2f}亿", "neutral", 0, "资金流入流出温和")

    # ④ 行业同步
    ind = str(it.get("industry") or "")
    ratio = ctx.get(ind)
    if ratio is None:
        add("industry", "行业同步", "—", "na", 0,
            f"同行业样本不足 {CTX_MIN_MEMBERS} 只")
    elif ratio < IND_CALM:
        add("industry", "行业同步", f"破位 {ratio*100:.0f}%", "washout", 1,
            "个股行为，非系统性下跌")
    elif ratio > IND_STORM:
        add("industry", "行业同步", f"破位 {ratio*100:.0f}%", "break", -1,
            "行业普遍破位，系统性下跌")
    else:
        add("industry", "行业同步", f"破位 {ratio*100:.0f}%", "neutral", 0,
            "行业分化")

    # ⑤ 跌幅形态（先判阴跌，阴跌成立就不再给急杀的分）
    r5, r20 = st.get("ret5"), st.get("ret20")
    if r20 is not None and r20 <= DROP_SLOW:
        add("drop", "跌幅形态", f"20日 {r20:.1f}%", "break", -1,
            "20 日阴跌，逐日新低")
    elif r5 is not None and r5 <= DROP_FAST:
        add("drop", "跌幅形态", f"5日 {r5:.1f}%", "washout", 1,
            "5 日急杀，横住后可观察承接")
    elif r5 is None and r20 is None:
        add("drop", "跌幅形态", "—", "na", 0, "无涨跌幅数据")
    else:
        _v = f"5日 {r5:.1f}%" if r5 is not None else f"20日 {r20:.1f}%"
        add("drop", "跌幅形态", _v, "neutral", 0, "跌幅未到极值")

    n_valid = sum(1 for s in sigs if s["side"] != "na")
    n_wash = sum(1 for s in sigs if s["side"] == "washout")
    n_break = sum(1 for s in sigs if s["side"] == "break")

    if total >= VERDICT_WASH:
        verdict = "偏洗盘"
    elif total <= VERDICT_BREAK:
        verdict = "偏破位"
    elif total >= 2:
        # ★ 2026-11-06 校准：`+2` 是最大群体（样本中 47%），叫"证据不足"不准确 ——
        #   它有 2 项确凿证据，只是没到 3 项门槛。措辞要如实。
        verdict = "部分洗盘证据"
    else:
        verdict = "证据不足"

    return {
        "score": total,
        "verdict": verdict,
        "n_valid": n_valid,
        "n_washout": n_wash,
        "n_break": n_break,
        "signals": sigs,
        # ★ 这句是给用户看的免责声明，必须随分数一起出现
        #   ⚠️ 内部用「」而不是 ASCII 双引号：后者会截断外层字符串（实测踩过）
        "caveat": ("分数只是「证据条数」的汇总，不是概率；洗盘与刚开始下跌在头 3~5 天"
                   "数据上无法区分。要不要动手由你判断。"),
    }


def annotate(items: List[Dict], day: str) -> str:
    """给 `range_watch.load()` 的 items 批量补 `washout` 字段。

    只对"到下沿或已破位"的票打分 —— 区间中部/上沿没有"该不该割"的问题，
    给它们打分只会制造噪音。返回用到的 `stock_facts` 日期（供前端标注）。

    ★ 静默降级：任何异常都不影响主表展示（`washout` 字段缺失即可）。
    """
    if not items:
        return ""
    targets = [x for x in items if (x.get("pos") is None or x.get("pos") <= 0.25
                                    or x.get("state") in ("下沿", "破位"))]
    if not targets:
        return ""
    try:
        ctx = build_context(day)
    except Exception:
        ctx = {}
    try:
        stats = load_stats([x["code"] for x in targets])
    except Exception:
        stats = {}
    stats_date = ""
    for x in targets:
        st = stats.get(str(x.get("code"))) or {}
        if not stats_date:
            stats_date = str(st.get("_stats_date") or "")
        try:
            x["washout"] = score_row(x, ctx, st)
        except Exception as e:
            print(f"[washout] score failed {x.get('code')}: {str(e)[:60]}")
    return stats_date
