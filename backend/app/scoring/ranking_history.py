"""
================================================================================
【文件作用】评分排行榜连续上榜追踪
================================================================================

追踪每天评分 Top N 的股票，计算连续上榜天数。
连续上榜天数越多 = 强者恒强 = 买入可信度越高。

可信度评级：
  - 连续 1 天：新上榜（观察期）
  - 连续 2 天：初步确认
  - 连续 3 天：持续强势（可轻仓）
  - 连续 5 天：极强共识（放心买）

综合可信度 = 评分(40%) + 连续天数(40%) + 信号强度(20%)

使用方式：
  from app.scoring.ranking_history import record_daily_ranking, get_ranking_persistence
  
  # 盘后记录当日排行
  record_daily_ranking(top_stocks)
  
  # 查询可信度
  result = get_ranking_persistence(["000001", "600519"])
================================================================================
"""

import json
import time
from collections import defaultdict
from typing import Dict, List
from datetime import datetime, timedelta, timezone

from app.database import db

# 排行日期统一用北京时间（UTC+8）：调度器的窗口判定（rules.beijing_now）也是北京时间，
# 若用 datetime.now() 会隐式依赖服务器 TZ 环境，部署环境不一致时 rank_date 会漂移。
_BEIJING_TZ = timezone(timedelta(hours=8))


def _today_str() -> str:
    return datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d")


# ── 数据库表初始化 ──
def init_ranking_history_table():
    """创建排行榜历史表"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS ranking_history (
            id SERIAL PRIMARY KEY,
            rank_date TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            rank_pos INTEGER,
            total_score REAL,
            signal TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rank_date, code)
        )
    """)
    db.execute("""
        CREATE INDEX IF NOT EXISTS idx_ranking_history_date_code 
        ON ranking_history(rank_date, code)
    """)
    # 迁移：权重优化分析需要的维度分 + 快照价格（旧库无此列，幂等加列）
    for col_sql in (
        "ALTER TABLE ranking_history ADD COLUMN IF NOT EXISTS dimensions_json TEXT",
        "ALTER TABLE ranking_history ADD COLUMN IF NOT EXISTS price REAL",
    ):
        try:
            db.execute(col_sql)
        except Exception as e:
            print(f"[ranking] 加列失败（可能已存在）: {e}")
    print("[ranking] ranking_history 表初始化完成")


# 启动时初始化
init_ranking_history_table()


def record_daily_ranking(top_stocks: List[Dict], only_if_empty: bool = False, replace_day: bool = False) -> int:
    """
    记录当日评分排行榜。
    
    参数：
      top_stocks: [{code, name, total_score, signal, rank, dimensions?, price?}, ...]
                  dimensions 为三维评分明细（供权重优化分析），price 为快照价格。
      only_if_empty: True 时当天已有任何记录则跳过（用于盘中兜底，防止反复追加膨胀每日快照）
      replace_day: True 时先清空当天记录再写入（用于盘后/手动权威快照，保证每天固定 Top50）
    
    返回：成功记录的条数
    """
    today = _today_str()
    if only_if_empty:
        exists = db.fetch_one(
            "SELECT 1 FROM ranking_history WHERE rank_date = %s LIMIT 1", (today,)
        )
        if exists:
            return 0
    if replace_day:
        db.execute("DELETE FROM ranking_history WHERE rank_date = %s", (today,))
    
    count = 0
    
    for i, stock in enumerate(top_stocks):
        code = stock.get("code")
        if not code:
            continue
        try:
            dims = stock.get("dimensions") or {}
            dims_json = json.dumps(dims, ensure_ascii=False) if dims else None
            price = stock.get("price") or 0
            db.execute("""
                INSERT INTO ranking_history 
                (rank_date, code, name, rank_pos, total_score, signal, dimensions_json, price)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (rank_date, code) DO UPDATE 
                SET name = EXCLUDED.name, rank_pos = EXCLUDED.rank_pos,
                    total_score = EXCLUDED.total_score, signal = EXCLUDED.signal,
                    dimensions_json = EXCLUDED.dimensions_json, price = EXCLUDED.price
            """, (
                today,
                code,
                stock.get("name"),
                stock.get("rank", i + 1),
                stock.get("total_score"),
                stock.get("signal"),
                dims_json,
                price,
            ))
            count += 1
        except Exception as e:
            print(f"[ranking] 记录排行失败 {code}: {e}")
    
    print(f"[ranking] 已记录 {count} 只股票的排行（{today}）")
    return count


def get_ranking_persistence(codes: List[str]) -> List[Dict]:
    """
    查询多只股票的连续上榜天数和可信度。

    批量查询（固定 3 次 DB 往返）：逐只查询时 Supabase 每次往返约 0.7s，
    50 只股票要 100+ 次查询 ≈ 70s，必然超过前端 30s 超时被静默 catch，
    表现为"连续/可信度一直是空的"。批量后稳定在 2s 内。

    返回：
      [{code, consecutive_days, trust_score, trust_grade, latest_score, latest_signal, advice}, ...]
    """
    if not codes:
        return []

    today = _today_str()
    days = [d for d in _trading_days(31) if d <= today]

    ph_codes = ",".join(["%s"] * len(codes))
    ph_days = ",".join(["%s"] * len(days)) if days else "NULL"

    # ① 这些股票在这些交易日的上榜记录 → {code: {rank_date, ...}}
    if days:
        on_rows = db.fetch(
            f"SELECT code, rank_date FROM ranking_history "
            f"WHERE code IN ({ph_codes}) AND rank_date IN ({ph_days})",
            (*codes, *days))
    else:
        on_rows = []
    on_map = defaultdict(set)
    for r in on_rows:
        on_map[r["code"]].add(r["rank_date"])

    # ② 每只股票的最新一条记录（一次查完）
    latest_rows = db.fetch(f"""
        SELECT code, name, total_score, signal, rank_pos, rank_date
        FROM ranking_history
        WHERE code IN ({ph_codes})
          AND (code, rank_date) IN (
              SELECT code, MAX(rank_date) FROM ranking_history
              WHERE code IN ({ph_codes}) GROUP BY code)
    """, (*codes, *codes))     # ph_codes 在 SQL 中出现两次，参数也要给两遍
    latest_map = {r["code"]: r for r in latest_rows}

    results = []
    for code in codes:
        # 从最近快照交易日往前数（周末/盘前不归零，按交易日历不中断）
        consecutive = 0
        for d in days:
            if d in on_map.get(code, ()):
                consecutive += 1
            else:
                break

        latest = latest_map.get(code)
        if latest:
            total_score = latest.get("total_score", 0) or 0
            signal = latest.get("signal", "观望") or "观望"
            name = latest.get("name", "") or ""
            rank_pos = latest.get("rank_pos", 0) or 0
        else:
            total_score, signal, name, rank_pos = 0, "观望", "", 0

        trust_score = _calc_trust_score(total_score, consecutive, signal)
        trust_grade = _trust_grade(trust_score)
        advice = _trust_advice(trust_grade, consecutive)

        results.append({
            "code": code,
            "name": name,
            "consecutive_days": consecutive,
            "trust_score": trust_score,
            "trust_grade": trust_grade,
            "latest_score": total_score,
            "latest_signal": signal,
            "rank_pos": rank_pos,
            "advice": advice,
        })

    return results


def _trading_days(limit: int = 31) -> List[str]:
    """全市场快照的交易日列表（降序）。

    ranking_history 每个交易日落一次 Top50，其去重日期即天然的交易日历。
    用交易日历而非自然日回溯，周末/节假日才不会把连续天数误判为中断。
    """
    rows = db.fetch("SELECT DISTINCT rank_date FROM ranking_history "
                    "ORDER BY rank_date DESC LIMIT %s", (limit,))
    return [r["rank_date"] for r in rows]


def _calc_consecutive_days(code: str, end_date: str) -> int:
    """计算连续上榜天数（按交易日回溯，最多 30 个交易日）。

    两个关键点（旧实现都踩了）：
      1. 不能从 end_date 当天起算：盘中/盘前/周末/节假日当天还没有快照，
         会第一天就 break 导致永远 0 天 → 改为从最近一个已有快照的交易日起算；
      2. 不能按自然日回溯（current -= 1 天）：周末没有快照，
         周一只能数到 1 天 → 改为按交易日历回溯。

    性能：旧实现每只股票最多 30 次查询（50 只 = 1500 次远程往返），
    现在全市场交易日 1 次 + 每只股票 1 次。
    """
    days = [d for d in _trading_days(31) if d <= end_date]
    if not days:
        return 0
    placeholders = ",".join(["%s"] * len(days))
    rows = db.fetch(
        f"SELECT DISTINCT rank_date FROM ranking_history "
        f"WHERE code = %s AND rank_date IN ({placeholders})", (code, *days))
    on_list = {r["rank_date"] for r in rows}

    consecutive = 0
    for d in days:          # days 已降序：从最近快照日往前
        if d in on_list:
            consecutive += 1
        else:
            break
    return consecutive


def _calc_trust_score(total_score: float, consecutive_days: int, signal: str) -> int:
    """
    计算综合可信度（0-100）。
    
    公式：
      可信度 = 评分(40%) + 连续天数(40%) + 信号强度(20%)
    
    - 评分：total_score 满分 100，取 40%
    - 连续天数：每天 20 分，最高 5 天满分（100），取 40%
    - 信号强度：强烈买入=100, 买入=70, 观望=30, 卖出=0
    """
    # 评分部分（40分满分）
    score_part = min(total_score * 0.4, 40)
    
    # 连续天数部分（40分满分，每天20分，最高5天满分）
    days_ratio = min(consecutive_days / 5.0, 1.0)
    persistence_part = days_ratio * 40
    
    # 信号强度部分（20分满分）
    signal_scores = {
        "强烈买入": 100, "买入": 70, "观望": 30, "卖出": 10, "强烈卖出": 0
    }
    signal_part = (signal_scores.get(signal, 30) / 100) * 20
    
    return int(score_part + persistence_part + signal_part)


def _trust_grade(trust_score: int) -> str:
    """根据可信度分数返回等级"""
    if trust_score >= 80:
        return "A+"  # 极强共识，放心买
    elif trust_score >= 65:
        return "A"   # 持续强势，可重仓
    elif trust_score >= 50:
        return "B"   # 初步确认，可轻仓
    elif trust_score >= 35:
        return "C"   # 观察期
    else:
        return "D"   # 不可信


def _trust_advice(trust_grade: str, consecutive_days: int) -> str:
    """根据可信度等级返回操作建议"""
    if trust_grade == "A+":
        return "放心买，极强共识"
    elif trust_grade == "A":
        return "可重仓，持续强势"
    elif trust_grade == "B":
        return "可轻仓，初步确认"
    elif trust_grade == "C":
        return "建议观望，观察期"
    else:
        return "不建议操作"


# ── 供调度器调用的自动记录函数 ──
async def auto_record_ranking():
    """
    自动记录当日评分 Top 50 排行。
    由调度器在盘后调用。
    """
    from app.tencent import _cache
    from app.scoring.engine import ScoreEngine
    
    stocks = _cache.get("stocks", {})
    if not stocks:
        print("[ranking] 行情缓存为空，跳过排行记录")
        return 0
    
    stock_list = list(stocks.values())
    valid = [s for s in stock_list if s.get("price", 0) > 0 and s.get("change_pct") is not None]
    
    # 用简化评分快速排序
    engine = ScoreEngine()
    results = engine.score_batch(valid[:200])  # 只算前200只
    results.sort(key=lambda r: r.total_score, reverse=True)
    
    top_stocks = [
        {
            "code": r.code,
            "name": r.name,
            "total_score": r.total_score,
            "signal": r.signal,
            "rank": i + 1,
            # 简化评分无维度分；快照价格用于权重分析的收益验证
            "price": (stocks.get(r.code) or {}).get("price") or 0,
        }
        for i, r in enumerate(results[:50])
    ]
    
    return record_daily_ranking(top_stocks)


# ── 快照读取（前端胜率回查 / 权重优化分析）──
def _parse_dims(row: Dict) -> Dict:
    try:
        return json.loads(row.get("dimensions_json") or "{}") or {}
    except Exception:
        return {}


def _current_prices(codes: List[str]) -> Dict[str, float]:
    """批量获取现价：内存实时行情 → backtest_prices 最新收盘 → kline_cache 末根收盘。

    不能只依赖内存行情缓存（tencent._cache["stocks"]）：服务重启或免费档休眠后
    缓存为空，所有快照的收益都会算不出来，表现为「查询当前收益无效」
    「权重优化提示已验证记录不足 0 条」。三级兜底后，只要库里存过这只股票的
    任意历史价格就能算出收益（批量查询，不随股票数增长远程往返）。
    """
    prices: Dict[str, float] = {}
    if not codes:
        return prices

    # ① 内存实时行情（最快，盘中最新）
    try:
        from app.tencent import _cache
        stocks = _cache.get("stocks", {}) or {}
        for c in codes:
            p = (stocks.get(c) or {}).get("price") or 0
            if p > 0:
                prices[c] = p
    except Exception:
        pass

    missing = [c for c in codes if c not in prices]
    if not missing:
        return prices

    # ② backtest_prices：每日回填的日线，取每只最新收盘价
    ph = ",".join(["%s"] * len(missing))
    try:
        rows = db.fetch(f"""
            SELECT code, close FROM backtest_prices
            WHERE code IN ({ph}) AND (code, date) IN (
                SELECT code, MAX(date) FROM backtest_prices
                WHERE code IN ({ph}) GROUP BY code)
        """, (*missing, *missing))
        for r in rows:
            if (r.get("close") or 0) > 0:
                prices[r["code"]] = r["close"]
    except Exception:
        pass

    # ③ kline_cache：DB 缓存的 K 线，取最后一根收盘价。
    #    必须限量：kline_data 每只几十 KB（500 根），服务重启后内存行情为空时
    #    missing 可能有几百只，全量拉取几十 MB 会把接口拖到超时（前端表现为 CORS 错误）。
    still = [c for c in missing if c not in prices][:50]
    if still:
        try:
            ph2 = ",".join(["%s"] * len(still))
            rows = db.fetch(
                f"SELECT code, kline_data FROM kline_cache WHERE code IN ({ph2})",
                (*still,))
            for r in rows:
                try:
                    kl = json.loads(r.get("kline_data") or "[]")
                    if kl:
                        prices[r["code"]] = kl[-1].get("close") or 0
                except Exception:
                    continue
        except Exception:
            pass
    return prices


def get_daily_rankings(days: int = 30) -> List[Dict]:
    """
    读取最近 N 天每日 Top 快照（含维度分、快照价、现价与收益）。
    返回结构对齐前端 score_snapshots 面板：按日期倒序。
      [{date, ts, stocks: [{code, name, rank, score, signal, dimensions,
                            price, currentPrice, returnPct}], verified, winRate, avgReturn, verifiedAt}]
    """
    since = (datetime.now(_BEIJING_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetch("""
        SELECT rank_date, code, name, rank_pos, total_score, signal,
               dimensions_json, price
        FROM ranking_history
        WHERE rank_date >= %s
        ORDER BY rank_date DESC, rank_pos ASC
    """, (since,))

    # 现价三级兜底：内存行情 / backtest_prices / kline_cache（重启后也能算出收益）
    now_prices = _current_prices(list({r["code"] for r in rows}))
    today = _today_str()

    by_date: Dict[str, list] = {}
    for r in rows:
        by_date.setdefault(r["rank_date"], []).append(r)

    snapshots = []
    for d, items in sorted(by_date.items(), reverse=True):
        stocks = []
        for r in items:
            price = r.get("price") or 0
            now_p = now_prices.get(r["code"]) or 0
            return_pct = None
            # 快照保存当天收益无验证意义（当日价格≈快照价），次日才计算
            if now_p > 0 and price > 0 and d < today:
                return_pct = round((now_p - price) / price * 100, 2)
            stocks.append({
                "code": r["code"],
                "name": r["name"],
                "rank": r.get("rank_pos"),
                "score": r.get("total_score"),
                "signal": r.get("signal"),
                "dimensions": _parse_dims(r),
                "price": price or None,
                "currentPrice": now_p or None,
                "returnPct": return_pct,
            })
        verified_stocks = [s for s in stocks if s["returnPct"] is not None]
        verified = len(verified_stocks) > 0
        wins = sum(1 for s in verified_stocks if s["returnPct"] > 0)
        snapshots.append({
            "date": d,
            "ts": 0,  # 占位，与本地快照结构一致（前端不依赖此字段）
            "stocks": stocks,
            "verified": verified,
            "verifiedAt": int(time.time() * 1000) if verified else None,
            "winRate": round(wins / len(verified_stocks) * 100) if verified_stocks else 0,
            "avgReturn": round(sum(s["returnPct"] for s in verified_stocks)
                               / len(verified_stocks), 2) if verified_stocks else 0,
        })
    return snapshots


def get_verified_records(min_age_days: int = 2) -> List[Dict]:
    """
    读取已验证的历史快照记录（保存 ≥ min_age_days 天 + 现价可算收益），
    供权重优化分析直接使用（免前端人工验证）。
    返回：[{date, code, name, score, signal, dimensions, returnPct}, ...]
    """
    cutoff = (datetime.now(_BEIJING_TZ) - timedelta(days=min_age_days)).strftime("%Y-%m-%d")
    rows = db.fetch("""
        SELECT rank_date, code, name, total_score, signal, dimensions_json, price
        FROM ranking_history
        WHERE rank_date <= %s AND dimensions_json IS NOT NULL AND dimensions_json != ''
          AND price IS NOT NULL AND price > 0
        ORDER BY rank_date DESC
    """, (cutoff,))

    # 现价三级兜底：内存行情 / backtest_prices / kline_cache（重启后也能算出收益）
    now_prices = _current_prices(list({r["code"] for r in rows}))

    records = []
    for r in rows:
        price = r.get("price") or 0
        now_p = now_prices.get(r["code"]) or 0
        if now_p <= 0 or price <= 0:
            continue
        dims = _parse_dims(r)
        if not dims:
            continue
        records.append({
            "date": r["rank_date"],
            "code": r["code"],
            "name": r["name"],
            "score": r.get("total_score"),
            "signal": r.get("signal"),
            "dimensions": dims,
            "returnPct": round((now_p - price) / price * 100, 2),
        })
    return records
