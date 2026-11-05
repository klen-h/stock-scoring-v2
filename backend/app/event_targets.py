# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】事件 → 观察名单落点（Event → Targets）—— **只列状态，不给买卖建议**
================================================================================
背景（2026-11-05，用户）：
  "昨天 13:00 让 Kimi 分析特朗普讲话，得出重大利好，上证 +2.16%、创业板 +6.40%，
   我看了系统未到能买的条件，错过了。如果重头加功能你的方向是？"
  ⇒ 痛点不在"信息不够"：`flash_brief` / `jin10_tidy` / `thesis_tracker` /
    `scenario_watch` / `contradictions` 已经把信息层铺满了。
    缺的是**最后一跳**：从"伊朗缓和是利好"到"**今天具体看哪只、它现在什么状态**"。
    `flash_brief` 只说"影响 原油/军工/黄金" —— 这是**板块级**，落不到标的。

★ 本模块**刻意不做**的事（红线，改动前先读）：
  1. **不给买/卖/加仓/减仓**，只输出「代码/名称/评分/位置/主力状态」等事实。
     名单里有高分票 ≠ 推荐买入；`risks` 字段专门用来对冲这种误读。
  2. **不改评分、不进闸门、不碰模拟盘**（与拥挤赛道/关键位/跨市场异动同级）。
  3. **不烧 LLM**：主题→行业靠配置表，不做语义推断。

★ 为什么是"观察名单"而不是"信号"：
  事件驱动买入在 A 股**无法预登记**（同一事件每次的市场环境都不同），
  没有可证伪的规则就只能靠事后叙述 —— 那是 `thesis_tracker` 明令禁止的。
  但"事件影响哪些行业 → 这些行业里谁最强/谁最危险"是**纯客观查询**。

--------------------------------------------------------------------------------
★★ 行业口径的坑（第一版实测踩出来的，改动前必读）
--------------------------------------------------------------------------------
  第一版只用 `stock_facts_wide.industry`（**49 个粗行业**）做映射，实测结果：
      · "黄金贵金属" → 云铝股份 / 焦作万方 / 中国铝业        ← 全是铝，不是黄金
      · "原油能源"   → 融捷股份 / 天齐锂业 / 兴齐眼药        ← 锂矿和眼药水
  根因：粗行业里 "有色金属"(86 只) 铝占多数、"化工行业"(210 只) 混着锂电与医药。
  ⇒ 只按行业取 topN，**答非所问，比不做还糟**。

  试过的第二条路也不够：`stock_industry.main_industry_code`（申万细分）更细，
  但 A 股没有"黄金""石油"这两个细分名（黄金股落在"工业金属/小金属"），
  且约一半取值是 `new_dzxx` 这种**代码**而非中文 —— 混用两种口径更危险。

  ⇒ 最终方案：**粗行业（圈定范围）+ 名称关键词（精确收口）**。
     · `name_kw` 为空 ⇒ 该行业整体可用（如"石油行业"只有 10 只，本身就准）；
     · `name_kw` 非空 ⇒ 名称必须命中任一关键词（如"有色金属"里只要含"黄金"的）。
     ⚠️ 关键词是**人工维护的**，宁可窄不可宽：宽了会把不相干的票带进来，
       而这正是第一版失败的原因。

口径（全部读本地日批产物，**零新增外部请求**）：
  · 评分/主力/位置 ← `stock_facts_wide`（日批合成，字段见 `stock_facts._COLS`）
  · 事件 ← `flash_news.cluster`（近 7 天，与 `flash_brief` 同一套集群名）
  · 约束 ← `policy_bottom_watch`（清单 N/6 ⇒ 仓位提示，**不自动执行**）

⚠️ PostgreSQL 的 `ORDER BY x DESC` 默认 **NULLS FIRST** —— 直接用会把
   "没有评分的票"排在最前（实测：`LIMIT 5` 取到 5 只 score=None）。必须 `NULLS LAST`。
   另：`stock_facts_wide` 只有**精算榜 top1000** 有 `score_total`，其余为 NULL（不是 0）。
================================================================================
"""
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.database import db

_FACTS = "stock_facts_wide"
_NEWS = "flash_news"

# ================================================================
#  主题 → [(粗行业, 名称关键词)]  —— 关键词为空表示该行业整体可用
# ================================================================
# ⚠️ 行业名必须与 `stock_facts_wide.industry` 完全一致（49 个粗分类）。
#    改前先跑 `SELECT DISTINCT industry FROM stock_facts_wide`。
# ⚠️ 关键词宁可窄不可宽：`["化工"]` 这种会把锂电/医药全带进来（第一版事故）。
THEME_INDUSTRY: Dict[str, List[Tuple[str, List[str]]]] = {
    "伊朗局势": [
        ("石油行业", []),
        # 有色金属里铝占多数，只要黄金系（黄金/金矿/紫金）
        ("有色金属", ["黄金", "金矿", "紫金"]),
        ("煤炭行业", []),
        ("飞机制造", []),
    ],
    "中东战争": [
        ("石油行业", []),
        ("有色金属", ["黄金", "金矿", "紫金"]),
        ("船舶制造", []),
        ("飞机制造", []),
    ],
    "原油能源": [
        ("石油行业", []),
        # ★ 实测（2026-11-05）：**"化工行业"里一个"石化/炼化/油服/油气"都没有**
        #   —— 这类公司全被归进了"石油行业"（只有 10 只，且本来就准）。
        #   ⇒ 不再挂"化工行业"，否则永远是一个空组，反而让人以为数据缺失。
        #   交通运输保留：油价下行对航运是成本利好（港口与油价关系弱，已去掉）。
        ("交通运输", ["航运", "油运"]),
    ],
    "俄乌局势": [
        ("石油行业", []),
        ("农林牧渔", []),
        ("农药化肥", []),
    ],
    "黄金贵金属": [
        ("有色金属", ["黄金", "金矿", "紫金"]),
    ],
    "美联储利率": [
        ("金融行业", []),
        ("房地产", []),
        ("有色金属", ["黄金", "金矿", "紫金"]),
    ],
    "美联储人事": [
        ("金融行业", []),
        ("有色金属", ["黄金", "金矿", "紫金"]),
    ],
    "中美贸易": [
        ("电子信息", []),
        ("电子器件", []),
        ("物资外贸", []),
        ("纺织行业", []),
        ("商业百货", []),
    ],
    "国内政策": [
        ("建筑建材", []),
        ("房地产", []),
        ("机械行业", []),
        ("水泥行业", []),
    ],
    "农业粮食": [
        ("农林牧渔", []),
        ("农药化肥", []),
        ("食品行业", []),
    ],
    "港股/中概股": [
        ("传媒娱乐", []),
        ("商业百货", []),
    ],
}

# 主题 → 验证条件（**不写"该买"，只写"什么算兑现/什么算证伪"**）
THEME_VERIFY: Dict[str, List[str]] = {
    "伊朗局势": ["布伦特跌破 105 / 100 两个台阶（每破一级可信度升一档）",
                 "双方正式接触的公告（不是单方表态）",
                 "霍尔木兹通航数据"],
    "中东战争": ["布伦特台阶（105/100）", "美军部署/撤出公告"],
    "原油能源": ["布伦特与 WTI 价差", "OPEC+ 产量决议", "国内成品油调价窗口"],
    "俄乌局势": ["制裁/豁免清单", "黑海航运数据"],
    "黄金贵金属": ["实际利率（10Y − CPI）方向", "美元指数", "央行购金月度数据"],
    "美联储利率": ["11 月中旬 CPI", "12 月 FOMC 点阵图中值", "10Y 能否站稳 5% 下方"],
    "美联储人事": ["提名/听证进程", "官员讲话立场是否一致"],
    "中美贸易": ["关税清单/豁免公告", "出口数据", "企业订单口径"],
    "国内政策": ["部委文件落地", "专项债/财政投放进度"],
    "农业粮食": ["收储/拍卖公告", "期货近月合约"],
    "港股/中概股": ["南向资金净买入", "中概 ADR 折溢价"],
}

# 风险标注阈值（**主动提示，避免把名单读成推荐**）
RISK_NEAR_HIGH = -3.0      # 距 60 日高 > -3% ⇒ 贴近高点，追高风险
RISK_FLOW_OUT = -1.0       # 主力 5 日净流出 < 1 亿
RISK_CHIP_HIGH = 0.85      # 筹码位置 > 0.85（获利盘重）
RISK_PHASE = ("decline",)  # 主力阶段为 decline ⇒ 派发/下跌

_PHASE_CN = {
    "accumulation": "吸筹", "markup": "拉升",
    "distribution": "派发", "decline": "下跌", "sideways": "横盘",
}


def _f(v) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest_day() -> str:
    r = db.fetch_one(f"SELECT MAX(date) AS d FROM {_FACTS}")
    return str((r or {}).get("d") or "")[:10]


def list_themes() -> List[Dict]:
    """可用主题 + 近 7 天快讯条数（按条数倒序）+ 是否已配行业映射。"""
    out = []
    try:
        rows = db.fetch(
            f"SELECT cluster, COUNT(*) AS n FROM {_NEWS} "
            f"WHERE cluster IS NOT NULL AND cluster <> '' GROUP BY cluster ORDER BY n DESC")
    except Exception:
        rows = []
    seen = set()
    for r in rows:
        c = str(r.get("cluster") or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append({"theme": c, "news_count": int(r.get("n") or 0),
                    "mapped": c in THEME_INDUSTRY})
    for c in THEME_INDUSTRY:
        if c not in seen:
            out.append({"theme": c, "news_count": 0, "mapped": True})
    return out


def _news_of(theme: str, limit: int = 3) -> List[Dict]:
    """该主题最近几条快讯（`flash_news.cluster` 精确匹配）。"""
    try:
        rows = db.fetch(
            f"SELECT time, content FROM {_NEWS} WHERE cluster = %s "
            f"ORDER BY time DESC LIMIT %s", (theme, int(limit)))
    except Exception:
        return []
    return [{"time": str(r.get("time") or ""),
             "text": (r.get("content") or "").strip()[:160]} for r in rows or []]


def _risks(x: Dict) -> List[str]:
    """挑出**需要人自己判断的风险**（不是买卖建议）。"""
    out = []
    dh = _f(x.get("dist_from_60d_high"))
    if dh is not None and dh > RISK_NEAR_HIGH:
        out.append(f"距 60 日高仅 {dh:.1f}%（追高位置）")
    f5 = _f(x.get("flow5_amt_yi"))
    if f5 is not None and f5 < RISK_FLOW_OUT:
        out.append(f"主力 5 日净流出 {abs(f5):.2f} 亿")
    cp = _f(x.get("chip_price_pos"))
    if cp is not None and cp > RISK_CHIP_HIGH:
        out.append(f"筹码位置 {cp:.2f}（获利盘重）")
    ph = str(x.get("phase") or "")
    if ph in RISK_PHASE:
        out.append(f"主力阶段={_PHASE_CN.get(ph, ph)}")
    return out


def _targets_of(industry: str, kws: List[str], day: str, top: int) -> List[Dict]:
    """单个行业取评分前 N（可带名称关键词收口）。

    ⚠️ `NULLS LAST` 不可省（见文件头 PG 坑）。
    """
    sql = (
        f"SELECT code, name, industry, score_total, score_signal, rank_pos, "
        f"dist_from_60d_high, flow5_amt_yi, phase, gate_ready, chip_price_pos "
        f"FROM {_FACTS} WHERE date = %s AND industry = %s"
    )
    params: List = [day, industry]
    if kws:
        sql += " AND (" + " OR ".join(["name LIKE %s"] * len(kws)) + ")"
        params.extend([f"%{k}%" for k in kws])
    sql += (" ORDER BY score_total DESC NULLS LAST, rank_pos ASC NULLS LAST"
            f" LIMIT {int(top)}")
    try:
        rows = db.fetch(sql, tuple(params))
    except Exception as e:
        print(f"[evt] query failed ({industry}): {str(e)[:100]}")
        return []
    out = []
    for x in rows or []:
        gate = x.get("gate_ready")
        out.append({
            "code": str(x.get("code") or ""),
            "name": x.get("name") or "",
            "industry": x.get("industry") or "",
            "score": _f(x.get("score_total")),      # None = 不在精算榜（不是 0 分）
            "score_signal": x.get("score_signal") or "",
            "rank_pos": x.get("rank_pos"),
            "dist_high": _f(x.get("dist_from_60d_high")),
            "flow5": _f(x.get("flow5_amt_yi")),
            "phase": x.get("phase") or "",
            "phase_cn": _PHASE_CN.get(str(x.get("phase") or ""), ""),
            "gate_ready": int(gate) if gate is not None else None,
            "chip_pos": _f(x.get("chip_price_pos")),
            "risks": _risks(x),
        })
    return out


def _constraint(day: str) -> Dict:
    """仓位约束（来自磨底清单，**只提示不执行**）。失败不影响主流程。"""
    try:
        from app import policy_bottom_watch as pbw
        d = pbw.load(day) or {}
        n, total = d.get("n_checked"), d.get("n_total")
        if n is None:
            return {"n_checked": None, "n_total": total,
                    "position_hint": "仓位约束暂不可用（清单快照未生成）"}
        return {"n_checked": n, "n_total": total, "n_pass": d.get("n_pass"),
                "position_hint": d.get("position_hint") or ""}
    except Exception as e:
        print(f"[evt] pbw constraint unavailable: {str(e)[:80]}")
        return {"n_checked": None, "n_total": None, "position_hint": ""}


def build(day: str = None, themes: List[str] = None,
          top: int = 3, max_themes: int = 2) -> Dict:
    """事件 → 观察名单。

    参数：
      day        YYYY-MM-DD（默认 `stock_facts_wide` 最新日）
      themes     指定主题（不传 ⇒ 取快讯条数最多的 `max_themes` 个）
      top        每个行业取前几只
      max_themes 自动模式最多几个主题（避免一次铺满刷屏）
    """
    want = str(day or "")[:10]
    latest = _latest_day()
    data_day = latest or datetime.now().strftime("%Y-%m-%d")
    fallback = bool(want and latest and want != latest)

    all_themes = list_themes()
    by_name = {t["theme"]: t for t in all_themes}
    if themes:
        picked = [x for x in themes if x]
    else:
        # 自动模式只取"有条目 + 有映射"的主题：没映射的查不到票，展示只会困惑
        picked = [t["theme"] for t in all_themes
                  if t["mapped"] and t["news_count"] > 0][:max_themes]

    constraint = _constraint(data_day)
    items, skipped = [], []
    for th in picked:
        spec = THEME_INDUSTRY.get(th)
        if not spec:
            skipped.append({"theme": th, "reason": "未配置行业映射（见 THEME_INDUSTRY）"})
            continue
        groups = []
        for industry, kws in spec:
            tg = _targets_of(industry, kws, data_day, top)
            groups.append({
                "industry": industry,
                "name_kw": kws,
                "targets": tg,
                # 空组要显式说明：是"该行业整体没票"还是"关键词过滤后没命中"
                "empty_reason": ("" if tg else
                                 ("关键词过滤后无匹配（可放宽 name_kw）"
                                  if kws else "该行业当日无数据")),
            })
        items.append({
            "theme": th,
            "news_count": (by_name.get(th) or {}).get("news_count", 0),
            "news": _news_of(th),
            "groups": groups,
            "verify": THEME_VERIFY.get(th) or [],
        })

    return {
        "ok": True,
        "date": data_day,
        "requested_date": want or None,
        "fallback": fallback,
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "constraint": constraint,
        "themes": items,
        "skipped": skipped,
        "available_themes": [t["theme"] for t in all_themes if t["mapped"]],
        "note": ("观察名单：只列「评分 / 距 60 日高 / 主力 5 日 / 筹码位置」四项事实，"
                 "**不构成买入建议**。按评分排序 ≠ 该买 —— `risks` 标的是每只票当前的"
                 "位置与资金风险。事件驱动在 A 股无法预登记，本模块只做事实检索，"
                 "不改评分、不进闸门、不碰模拟盘。"),
    }
