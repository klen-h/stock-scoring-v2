# -*- coding: utf-8 -*-
from __future__ import annotations
"""
================================================================================
【文件作用】快讯检索与分类（flash search）—— 两层分类 + 关键词搜索，**只读检索**
================================================================================
前端落点：`/monitor`（快讯监控）新增「检索」tab；路由 `GET /api/flash/search`、
`POST /api/flash/reclassify`（routers/flash.py）。

★ 语料（零新增数据源，全部已在库里）：
    · flash_news       金十"爆/沸/热"热榜（~44 条/天，保留 120 天/2 万行）
    · jin10_tidy_news  金十整理稿 4 栏目（~55 条/交易日）

★ 两层分类（职责不同，不合并）：
  1. `cluster` 事件簇 —— 写库时按 `rules.EVENT_CLUSTERS` 首个命中打标（store._cluster_of），
     查询时只读该列；改关键词口径后用 `reclassify()` 重算窗口内历史（幂等，留痕返回）。
  2. `category` 政策类别 —— **复用 `policy_tone_watch` 的冻结词表**（单一事实源，
     零 schema 变更、零口径漂移），查询时逐条计算。reversed 词表命中归 `reverse` 类。
     ★ 这是"检索工具"不是"信号"：命中只用于过滤/展示，不构成多空判断（R2 同款声明）。

★ 实现口径（诚实披露）：
  · 一次性取回窗口内全部语料（days ≤ 120 封顶，最坏 ~5.5k 行 ≈ 1.5MB），过滤/分类/
    facet/排序/分页全部在 Python 完成 —— 表小（<1 万行），不值得为此建全文索引；
    默认 7 天窗口 ≈ 90KB。**本接口供人工按需调用，禁止挂轮询**（会白白烧 DB egress）。
  · `cluster` 筛选只作用于 flash_news（tidy 表没有该列）——前端已注明。
  · q 搜索：子串包含、大小写不敏感（LOWER 后匹配）；tidy 表同时搜标题与正文。
  · 排序：按 (日期, 时刻) 倒序；整理稿排在同日热榜之后（按 seq）。
================================================================================
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db
from app.policy_tone_watch import KEYWORDS, NEGATIVE_KEYWORDS, CATEGORY_CN

_BJ = timezone(timedelta(hours=8))

# 全部合法类别（含反向），供路由层校验与前端 chips 使用
ALL_CATEGORIES = tuple(KEYWORDS) + tuple(NEGATIVE_KEYWORDS)


def match_categories(text: str) -> List[str]:
    """单条语料 → 命中的政策类别列表（纯函数，大小写不敏感）。"""
    low = str(text or "").lower()
    out = []
    for cat, words in {**KEYWORDS, **NEGATIVE_KEYWORDS}.items():
        for w in words:
            if w and str(w).lower() in low:
                out.append(cat)
                break
    return out


def match_keywords(text: str, cats: List[str] = None) -> List[str]:
    """单条语料 → 命中的具体关键词（供前端高亮；纯函数）。"""
    low = str(text or "").lower()
    pool = {**KEYWORDS, **NEGATIVE_KEYWORDS}
    out = []
    for cat, words in pool.items():
        if cats and cat not in cats:
            continue
        for w in words:
            if w and str(w).lower() in low and w not in out:
                out.append(w)
    return out


# ══════════════════════════════════════════════════════════════════════════
# 取数：窗口内全量语料（两源合并为统一条目）
# ══════════════════════════════════════════════════════════════════════════
def _window_from(days: int) -> str:
    now = datetime.now(_BJ)
    return (now - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")


def _fetch_items(source: str, days: int) -> List[Dict]:
    """窗口内语料 → 统一条目。source ∈ all/flash/tidy。"""
    frm = _window_from(days)
    items: List[Dict] = []
    if source in ("all", "flash"):
        try:
            rows = db.fetch(
                "SELECT id, time, cluster, content FROM flash_news "
                "WHERE time >= %s", (frm,)) or []
            for r in rows:
                items.append({
                    "src": "flash",
                    "time": str(r.get("time") or ""),
                    "date": str(r.get("time") or "")[:10],
                    "cluster": r.get("cluster") or "其他",
                    "title": "",
                    "content": str(r.get("content") or ""),
                    "sort": (str(r.get("time") or ""), 0, 0),
                })
        except Exception as e:
            print(f"[flash_search] flash_news 读取失败: {str(e)[:80]}")
    if source in ("all", "tidy"):
        try:
            rows = db.fetch(
                "SELECT date, seq, title, content FROM jin10_tidy_news "
                "WHERE date >= %s", (_window_from(days)[:10],)) or []
            for r in rows:
                d = str(r.get("date") or "")[:10]
                seq = int(r.get("seq") or 0)
                title = str(r.get("title") or "")
                body = str(r.get("content") or "")
                items.append({
                    "src": "tidy",
                    "time": f"{d} tidy#{seq:03d}",
                    "date": d,
                    "cluster": None,          # tidy 无簇列 ⇒ cluster 筛选不含它
                    "title": title,
                    "content": f"{title}｜{body}" if body else title,
                    "sort": (d, 1, seq),      # 同日排在热榜之后
                })
        except Exception as e:
            print(f"[flash_search] jin10_tidy_news 读取失败: {str(e)[:80]}")
    items.sort(key=lambda x: x["sort"], reverse=True)
    return items


# ══════════════════════════════════════════════════════════════════════════
# 检索主入口
# ══════════════════════════════════════════════════════════════════════════
def search(q: str = None, cluster: str = None, category: str = None,
           source: str = "all", days: int = 7,
           limit: int = 50, offset: int = 0) -> Dict:
    """分类 + 关键词检索。只读；参数不合法返回 ok=False + error。"""
    if category and category not in ALL_CATEGORIES:
        return {"ok": False,
                "error": f"category 必须是 {ALL_CATEGORIES} 之一，收到「{category}」"}
    if source not in ("all", "flash", "tidy"):
        return {"ok": False, "error": "source 必须是 all/flash/tidy"}
    days = max(1, min(int(days or 7), 120))
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))

    all_items = _fetch_items(source, days)

    # facet：窗口 + source 口径（不受 q / cluster / category 影响 ⇒ chips 数字稳定）
    cluster_facet: Dict[str, int] = {}
    category_facet: Dict[str, int] = {}
    for it in all_items:
        if it["src"] == "flash":
            cluster_facet[it["cluster"]] = cluster_facet.get(it["cluster"], 0) + 1
        for c in match_categories(it["content"]):
            category_facet[c] = category_facet.get(c, 0) + 1

    # 过滤
    needle = str(q or "").strip().lower()
    hits: List[Dict] = []
    for it in all_items:
        if cluster and (it["src"] != "flash" or it["cluster"] != cluster):
            continue
        cats = match_categories(it["content"])
        if category and category not in cats:
            continue
        if needle and needle not in it["content"].lower():
            continue
        hits.append({**it, "cats": cats,
                     "kw": match_keywords(it["content"], cats)[:6]})

    total = len(hits)
    page = hits[offset:offset + limit]
    items = [{
        "src": it["src"], "time": it["time"], "date": it["date"],
        "cluster": it["cluster"], "title": it["title"],
        "content": it["content"], "cats": it["cats"], "kw": it["kw"],
    } for it in page]

    return {
        "ok": True,
        "total": total,
        "items": items,
        "facets": {
            "clusters": sorted(
                [{"name": k, "n": v} for k, v in cluster_facet.items()],
                key=lambda x: -x["n"]),
            "categories": sorted(
                [{"name": k, "n": v, "label": CATEGORY_CN.get(k, k)}
                 for k, v in category_facet.items()],
                key=lambda x: -x["n"]),
        },
        "window": {"days": days},
        "note": ("只读检索工具：category 词表来自 policy_tone_watch 冻结判据；"
                 "命中不构成多空信号。cluster 筛选只作用于热榜（整理稿无簇列）。"),
    }


# ══════════════════════════════════════════════════════════════════════════
# cluster 重算（唯一写路径；幂等；只写 flash_news.cluster 一列）
# ══════════════════════════════════════════════════════════════════════════
def reclassify(days: int = 120) -> Dict:
    """按当前 EVENT_CLUSTERS 口径重算窗口内 flash_news 的 cluster。

    ★ 用途：EVENT_CLUSTERS 关键词放宽（2026-11-07 国内政策扩词）后，把历史行
      一次性拉齐口径；同口径重跑 ⇒ 0 变更（幂等）。tidy 表无簇列，不在范围。
    ★ 只 UPDATE 有变化的行；返回 from→to 迁移统计供披露。
    """
    days = max(1, min(int(days or 120), 120))
    from app.flash.store import _cluster_of
    rows = db.fetch("SELECT id, cluster, content FROM flash_news WHERE time >= %s",
                    (_window_from(days),)) or []
    changes: Dict[str, Dict[str, int]] = {}
    updated = 0
    for r in rows:
        old = r.get("cluster") or "其他"
        new = _cluster_of(str(r.get("content") or ""))
        if new == old:
            continue
        db.execute("UPDATE flash_news SET cluster=%s WHERE id=%s", (new, r["id"]))
        key = f"{old} → {new}"
        changes[key] = changes.get(key, 0) + 1
        updated += 1
    return {"ok": True, "scanned": len(rows), "updated": updated,
            "changes": dict(sorted(changes.items(), key=lambda kv: -kv[1])),
            "days": days}
