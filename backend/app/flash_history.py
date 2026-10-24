# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】历史快讯检索（异动分析的「历史依据」数据源）
================================================================================
背景（2026-10-24，用户）：
  「异动分析中增加对历史快讯的数据库检索能力：检测到异动时，先在数据库里搜索
    与该异动相关的历史快讯，作为异动说明的补充依据，让说明更可信、更具体。」

★★ 一、存储结构（复用既有表，不新建数据源）
  表 `flash_news`（由 `app/flash/store.save_raw_data` 写入）：
    · id        TEXT PK   —— 金十条目 id（幂等去重）
    · time      TEXT      —— 'YYYY-MM-DD HH:MM:SS'（**北京时间**，字符串可直接比较）
    · content   TEXT      —— 原文（含标题/正文，金十格式）
    · cluster   TEXT      —— 事件簇（`rules.EVENT_CLUSTERS` 首命中，写入时补算）
    · is_pushed INTEGER   —— 是否已推送过（本模块不用）
  保留策略（2026-10-24 调整）：
    · `FLASH_KEEP_DAYS` 默认 **120 天**（原 7 天 —— 7 天撑不起"历史依据"）
    · `FLASH_KEEP_ROWS` 默认 20000 行兜底（实测 ~38 条/日 ⇒ 120 天约 4.5k 行，几 MB）
  ⚠️ 不新增 `codes` 列：金十原文通常写**股票名称**而不是代码，命中率靠名称匹配更高；
     且避免在写入热路径上再维护一张 name→code 表（多一处必然漂移的映射）。

★★ 二、检索匹配逻辑（四路匹配 + 打分，全部在 SQL 时间窗内做）
  A **代码**：`content LIKE '%600519%'`            → +4（最精确，但原文少写代码）
  B **名称**：`content LIKE '%贵州茅台%'`          → +3（主路径）
  C **行业**：`content LIKE '%白酒%'`（来自 `industry_map`）→ +1.5（弱证据）
  D **关键词**：按本次异动的规则标签映射词表（涨停/跌停/放量/中标/减持/回购/业绩…）
                                                      → 每个 +1，最多 +2
  E **事件簇**：簇属于高信息簇（政策/中美/原油/美联储…）→ +0.5
  F **时间衰减**：≤3 天 +1.5；≤7 天 +0.5；>30 天 −1
  过滤：`金十数据整理 / 重要新闻汇总 / 早间要闻` 等聚合稿剔除（一条汇总会污染全部异动）；
        得分 < 2 不入围（只有"行业+簇"这种弱命中不给依据）。
  去重：正文前 30 字相同视为同一条（金十会重复推送同稿）。
  排序：得分降序 → 时间降序；取 Top N（默认 6）。
  缓存：进程内 (code, 交易日) 10 分钟 —— 异动分析本身有 30 分钟复用窗口，这里足够。

★★ 三、如何进入异动说明
  ① `routers/stock.stock_anomaly_analysis` 在调 LLM **之前**调用 `search()`；
  ② 命中条目以 `[1] 10-22 21:03 [原油能源] 原文…` 形式拼进 prompt 的「L5 历史快讯」段，
     并在 system 里要求「只可引用给定编号，禁止编造历史事件」；
  ③ 返回体新增 `news_evidence`（结构化，含 time/cluster/text/match/score/age_days）
     与 `news_query`（窗口天数、命中数、检索方式）⇒ 前端渲染「历史快讯依据」列表，
     每条可展开原文；0 命中时如实显示「未检索到相关历史快讯」（不假装有依据）。

★ 红线：历史快讯是**证据**，不是信号；本模块不改分数、不进闸门、不产生交易动作。
================================================================================
"""
import os
import re
import time
from typing import Dict, List, Optional

from app.database import db

_SCAN_DAYS = int(os.environ.get("FLASH_HISTORY_DAYS") or 90)
_LIMIT = int(os.environ.get("FLASH_HISTORY_LIMIT") or 6)
_CACHE_TTL = 600.0
_CACHE: Dict[str, tuple] = {}

# 异动标签 → 检索关键词（与 routers/stock._detect_anomaly 的 type 文案对齐）
TAG_KEYWORDS = {
    "涨停": ["涨停", "封板", "涨停潮"],
    "跌停": ["跌停", "跌停潮"],
    "急涨": ["利好", "中标", "订单", "并购", "重组", "业绩预增", "涨价"],
    "急跌": ["利空", "减持", "立案", "问询", "亏损", "业绩预减", "处罚"],
    "放量": ["成交额", "放量", "换手"],
    "高换手": ["换手", "龙虎榜", "游资"],
    "大振幅": ["停牌", "复牌", "异动公告"],
}
NOISE = ("金十数据整理", "重要新闻汇总", "早间要闻", "隔夜要闻")
STRONG_CLUSTERS = {"中东战争", "伊朗局势", "美联储利率", "国内政策",
                   "中美贸易", "原油能源", "黄金贵金属"}


def _cutoff(days: int) -> str:
    from app.flash.rules import beijing_now
    from datetime import timedelta
    return (beijing_now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _age_days(t: str) -> Optional[float]:
    try:
        from datetime import datetime
        from app.flash.rules import beijing_now
        dt = datetime.strptime(str(t)[:19], "%Y-%m-%d %H:%M:%S")
        return round((beijing_now().replace(tzinfo=None) - dt).total_seconds() / 86400, 1)
    except (TypeError, ValueError):
        return None


def _industry(code: str) -> str:
    try:
        from app.industry_map import get_industry_map
        return (get_industry_map() or {}).get(str(code), "") or ""
    except Exception:
        return ""


def search(code: str, name: str = "", tags: Optional[List[str]] = None,
           days: int = _SCAN_DAYS, limit: int = _LIMIT) -> Dict:
    """检索与本次异动相关的历史快讯 → `{items, query, note}`（永不抛异常）。"""
    code = str(code or "").strip()
    name = str(name or "").strip()
    tags = list(tags or [])
    if not code and not name:
        return {"items": [], "query": {}, "note": "缺少代码/名称，未检索"}

    key = f"{code}|{tags[:2]}|{days}|{limit}"
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < _CACHE_TTL:
        return hit[1]

    industry = _industry(code) if code else ""
    cutoff = _cutoff(days)

    # ── SQL：时间窗 + 任一维度命中（参数化，避免拼接注入）──
    like, params = [], [cutoff]
    if name:
        like.append("content LIKE %s")
        params.append(f"%{name}%")
    if code:
        like.append("content LIKE %s")
        params.append(f"%{code}%")
    if industry and len(industry) >= 2:
        like.append("content LIKE %s")
        params.append(f"%{industry}%")
    if not like:
        return {"items": [], "query": {"days": days}, "note": "缺少检索条件"}

    sql = ("SELECT id, time, content, cluster FROM flash_news "
           f"WHERE time >= %s AND ({' OR '.join(like)}) "
           "ORDER BY time DESC LIMIT 800")
    try:
        rows = db.fetch(sql, tuple(params)) or []
    except Exception as e:
        return {"items": [], "query": {"days": days, "error": str(e)[:80]},
                "note": "历史快讯检索失败（不影响本次分析）"}

    kws = []
    for t in tags:
        kws.extend(TAG_KEYWORDS.get(str(t), []))
    kws = list(dict.fromkeys(kws))[:6]

    scored: List[Dict] = []
    for r in rows:
        content = (r.get("content") or "").replace("\n", " ").strip()
        if not content or any(n in content for n in NOISE):
            continue
        match, score = [], 0.0
        if code and code in content:
            match.append("代码")
            score += 4
        if name and name in content:
            match.append("名称")
            score += 3
        if industry and len(industry) >= 2 and industry in content:
            match.append(f"行业·{industry}")
            score += 1.5
        hits = [k for k in kws if k in content][:2]
        if hits:
            match.append("关键词·" + "/".join(hits))
            score += len(hits)
        cluster = r.get("cluster") or "其他"
        if cluster in STRONG_CLUSTERS:
            score += 0.5
            if "簇" not in "".join(match):
                match.append(f"簇·{cluster}")
        age = _age_days(r.get("time"))
        if age is not None:
            if age <= 3:
                score += 1.5
            elif age <= 7:
                score += 0.5
            elif age > 30:
                score -= 1.0
        if score < 2:
            continue
        scored.append({"id": r.get("id"), "time": str(r.get("time") or ""),
                       "cluster": cluster, "text": content[:200],
                       "match": match, "score": round(score, 1),
                       "age_days": age, "_dedup": content[:30]})

    # 去重（同稿重复推送）+ 排序（分高优先，同分取更新的）
    scored.sort(key=lambda x: (x["score"], x["time"]), reverse=True)
    out, seen = [], set()
    for x in scored:
        if x["_dedup"] in seen:
            continue
        seen.add(x["_dedup"])
        out.append({k: v for k, v in x.items() if k != "_dedup"})
        if len(out) >= limit:
            break

    res = {"items": out,
           "query": {"days": days, "code": code, "name": name,
                     "industry": industry, "keywords": kws,
                     "scanned": len(rows), "matched": len(out),
                     "by": "代码/名称/行业/关键词/簇 + 时间衰减"},
           "note": ("" if out else "未检索到相关历史快讯")}
    _CACHE[key] = (now, res)
    return res


def prompt_block(hit: Dict) -> str:
    """把命中结果拼成 prompt 片段（编号引用，禁止 LLM 编造）。"""
    items = (hit or {}).get("items") or []
    if not items:
        return "### L5 历史快讯（数据库检索）\n- 未检索到相关历史快讯（不要虚构历史事件）"
    lines = ["### L5 历史快讯（数据库检索，仅可引用下列编号）"]
    for i, x in enumerate(items, 1):
        lines.append(f"[{i}] {x['time']} [{x['cluster']}] {x['text']}"
                     f"（匹配：{'、'.join(x.get('match') or [])}）")
    return "\n".join(lines)
