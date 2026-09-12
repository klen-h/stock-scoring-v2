# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】全量精算榜单（日批产出 → 落库 → 后端直接读）
================================================================================

背景（2026-09-11，002452 长高电气案例）：
  `/api/score/batch/top` 是「简化分选候选池（limit+margin）→ 只精算候选」，
  而简化分只看 动量+换手+PE。结果「基本面/质量型」股票被系统性漏掉：
  002452 简化分 61.6（第 352/1561 名，进不了前 100 候选池）→ 从未被精算，
  而它真实精算分 69.8（前端本地是全量精算，所以它上了本地榜）→ 两边榜单不一致。

本模块把「全量精算」搬到 Actions 日批（4 核 + pack 预计算指标，零腾讯请求，
零回传流量），结果落库 ranking_live（当日整体替换）：
  · 收盘后：后端 /batch/top 直接读这张表 —— 与前端本地榜同口径，零计算
  · 盘中/次日：后端把这张表的代码并入候选池 —— 「昨日上榜股今日必被重算」，
    简化分盲区消失

表结构：ranking_live(rank_date, rank_pos, code, name, total_score, signal,
                    signal_level, pool_total, payload(JSON), created_at)
        UNIQUE(rank_date, code)；每次写入先删当日再批量插入。
"""

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.database import db

_BJ = timezone(timedelta(hours=8))

_TABLE_READY = False
_load_cache: Dict = {"ts": 0.0, "date": None, "data": None, "pool_total": 0}
_LOAD_TTL = 300          # 读取缓存 5 分钟（榜单每日一变，够用且省往返）


def _today_bj() -> str:
    return datetime.now(_BJ).strftime("%Y-%m-%d")


def init_table() -> None:
    """建表（幂等；database.execute 内建 DDL 去重，不会刷 schema cache）。"""
    global _TABLE_READY
    if _TABLE_READY:
        return
    try:
        db.execute("""
            CREATE TABLE IF NOT EXISTS ranking_live (
                id SERIAL PRIMARY KEY,
                rank_date TEXT NOT NULL,
                rank_pos INTEGER NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                total_score REAL,
                signal TEXT,
                signal_level INTEGER,
                pool_total INTEGER,
                payload TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(rank_date, code)
            )
        """)
        _TABLE_READY = True
    except Exception as e:
        print(f"[live_ranking] 建表失败: {e}")


# ────────────────────────── 写入（日批调用） ──────────────────────────

def _build_pool() -> List[dict]:
    """复刻 score_top 的池子与过滤 + flow5 注入（保证榜单与在线榜同口径）。"""
    from app.routers.scoring import EXCLUDE_LOSS_MAKING, _pool_quality_filter
    from app.tencent import _cache

    stocks = list((_cache.get("stocks") or {}).values())
    if not stocks:
        raise RuntimeError("行情缓存为空（日批启动时应已刷新全市场行情）")

    valid = [s for s in stocks
             if s.get("price", 0) > 0 and s.get("change_pct") is not None]
    if EXCLUDE_LOSS_MAKING:
        valid = [s for s in valid if (s.get("pe", 0) or 0) > 0]
    valid = [s for s in valid if _pool_quality_filter(s)]

    # 资金面第 5 因子：主力 5 日净流入（与 score_top 一致）
    try:
        from app.mainforce.state import load_latest as _mf_load
        mf = _mf_load([s["code"] for s in valid])
        for s in valid:
            m = mf.get(s["code"])
            if m and m.get("flow5_amt") is not None:
                s["flow5_amt"] = m["flow5_amt"]
    except Exception as e:
        print(f"[live_ranking] flow5 注入失败（资金面退回 4 因子）: {e}")
    return valid


def compute_full_ranking(limit: int = 300, pool_cap: Optional[int] = None) -> tuple:
    """全量精算榜单。返回 (rank_date, rows, pool_total)。

    rows 元素 = _precise_score_sync 的 dict（code/name/total_score/signal/
    signal_level/change_pct/factors_up/buy_point/trend_health/dimensions），
    已按总分降序。
    """
    from app.finance import get_finance_batch
    from app.routers.scoring import _precise_score_sync
    from app.scoring.indicator_cache import get_cached_technical_batch_sql

    # ★ 2026-09-13 P1-4：确保引擎持有当日 regime（日批独立进程需先从库恢复）——
    #   「换手率 nb 钳制」等市况规则依赖 engine.regime，否则日批全量精算榜不生效。
    try:
        from app.routers.scoring import _sync_regime_weights
        _sync_regime_weights()
    except Exception as e:
        print(f"[live_ranking] regime 同步失败（市况规则不生效）: {e}")

    t0 = time.time()
    valid = _build_pool()
    # pool_cap：只精算市值最大的 N 只（冒烟测试/资源兜底用；None = 全量）
    if pool_cap:
        valid.sort(key=lambda s: (s.get("market_cap") or 0), reverse=True)
        valid = valid[:pool_cap]
    codes = [s["code"] for s in valid]
    print(f"[live_ranking] 精算池: {len(valid)} 只（PE>0 + 流通市值≥50亿 + 价≥3元）")

    # 指标批量预载（pack 模式 = 读数据包，零 Supabase 流量；一条 SQL 批量）
    pre = {}
    try:
        pre = get_cached_technical_batch_sql(codes) or {}
    except Exception as e:
        print(f"[live_ranking] 指标批量预载失败: {e}")
    print(f"[live_ranking] 指标命中: {len(pre)}/{len(codes)}")

    # 财务（成长/质量维度；失败则该维度不参与加权，与在线口径一致）
    fin_map = {}
    try:
        fin_map = get_finance_batch(codes) or {}
        print(f"[live_ranking] 财务数据: {len(fin_map)} 只")
    except Exception as e:
        print(f"[live_ranking] 财务数据加载失败: {e}")

    info_map = {s["code"]: s for s in valid}
    rows: List[dict] = []
    skipped = 0
    for code in codes:
        if code not in pre:
            # 无预计算指标 = 该股不在数据包/缓存里。此处绝不做实时拉取
            # （1561 只逐个拉腾讯必撞 WAF，且日批不该有这种网络依赖）。
            skipped += 1
            continue
        try:
            r = _precise_score_sync(info_map[code], pre.get(code), fin_map.get(code))
            if r:
                rows.append(r)
        except Exception as e:
            print(f"[live_ranking] 精算失败 {code}: {str(e)[:80]}")
    rows.sort(key=lambda r: r["total_score"], reverse=True)
    print(f"[live_ranking] 精算完成: {len(rows)} 只（跳过无指标 {skipped} 只），"
          f"耗时 {time.time() - t0:.1f}s")
    return _today_bj(), rows[:limit], len(valid)


def compute_and_store(limit: int = 300) -> str:
    """日批任务入口：算全量榜单 → 落库。返回摘要字符串。"""
    init_table()
    rank_date, rows, pool_total = compute_full_ranking(limit=limit)
    if not rows:
        raise RuntimeError("全量精算榜为空（指标预载可能全失败）")
    saved = save(rank_date, rows, pool_total)
    top1 = rows[0]
    return (f"全量精算榜: {saved} 行落库（date={rank_date}, 池={pool_total}, "
            f"榜首 {top1['code']} {top1['name']} {top1['total_score']}）")


def save(rank_date: str, rows: List[dict], pool_total: int = 0) -> int:
    """整体替换当日榜单（先删后批量插）。"""
    init_table()
    try:
        db.execute("DELETE FROM ranking_live WHERE rank_date = %s", (rank_date,))
    except Exception as e:
        print(f"[live_ranking] 清理当日旧榜失败（继续插入）: {e}")
    payload = []
    for i, r in enumerate(rows):
        payload.append({
            "rank_date": rank_date,
            "rank_pos": i + 1,
            "code": r.get("code"),
            "name": r.get("name") or "",
            "total_score": r.get("total_score") or 0,
            "signal": r.get("signal") or "",
            "signal_level": r.get("signal_level") or 0,
            "pool_total": pool_total,
            "payload": json.dumps({
                "change_pct": r.get("change_pct") or 0,
                "factors_up": r.get("factors_up") or [],
                "buy_point": r.get("buy_point") or {},
                "trend_health": r.get("trend_health") or {},
                "dimensions": r.get("dimensions") or {},
            }, ensure_ascii=False),
        })
    try:
        return db.upsert_many("ranking_live", payload, ["rank_date", "code"])
    except Exception as e:
        print(f"[live_ranking] 批量写入失败（逐行兜底）: {e}")
        n = 0
        for item in payload:
            try:
                db.upsert("ranking_live", item, ["rank_date", "code"])
                n += 1
            except Exception:
                pass
        return n


# ────────────────────────── 读取（后端 /batch/top 调用） ──────────────────────────

def load(limit: int = 300, use_cache: bool = True) -> Dict:
    """读最新一份榜单。返回 {"date", "data": [...], "pool_total"}；无则 data=[]。

    data 元素字段与 /batch/top 返回同构（不含 mainforce/news_score —— 由调用方
    在服务端现挂，与现有后处理保持一致）。
    """
    now = time.time()
    if use_cache and _load_cache["data"] is not None and now - _load_cache["ts"] < _LOAD_TTL:
        return {"date": _load_cache["date"],
                "data": _load_cache["data"][:limit],
                "pool_total": _load_cache["pool_total"]}
    try:
        init_table()
        row = db.fetch_one("SELECT MAX(rank_date) AS d FROM ranking_live")
        latest = (row or {}).get("d")
        if not latest:
            _load_cache.update({"ts": now, "date": None, "data": [], "pool_total": 0})
            return {"date": None, "data": [], "pool_total": 0}
        rows = db.fetch("""
            SELECT rank_pos, code, name, total_score, signal, signal_level,
                   pool_total, payload
            FROM ranking_live WHERE rank_date = %s ORDER BY rank_pos ASC
        """, (latest,)) or []
        data = []
        for r in rows:
            try:
                pl = json.loads(r.get("payload") or "{}")
            except Exception:
                pl = {}
            data.append({
                "code": r["code"], "name": r.get("name") or "",
                "total_score": r.get("total_score") or 0,
                "signal": r.get("signal") or "",
                "signal_level": r.get("signal_level") or 0,
                "change_pct": pl.get("change_pct") or 0,
                "factors_up": pl.get("factors_up") or [],
                "buy_point": pl.get("buy_point") or {},
                "trend_health": pl.get("trend_health") or {},
                "dimensions": pl.get("dimensions") or {},
            })
        pool_total = (rows[0].get("pool_total") if rows else 0) or 0
        _load_cache.update({"ts": now, "date": latest, "data": data,
                            "pool_total": pool_total})
        return {"date": latest, "data": data, "pool_total": pool_total}
    except Exception as e:
        print(f"[live_ranking] 读取失败: {e}")
        return {"date": None, "data": [], "pool_total": 0}


def load_codes(limit: int = 500) -> List[str]:
    """榜单代码（供盘中候选池并入，修掉简化分盲区）。"""
    return [r["code"] for r in load(limit=limit).get("data") or []]
