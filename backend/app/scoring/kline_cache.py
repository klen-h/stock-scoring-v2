"""
================================================================================
【文件作用】K线数据数据库缓存
================================================================================

将K线数据存储在 PostgreSQL 中，每天盘后更新一次。
评分时直接从数据库读取，避免实时调用腾讯API。

解决问题：
  - Render 免费层 0.1 CPU + 512MB RAM，实时拉K线太慢
  - 腾讯API有WAF限流，并发拉取容易被封
  - 排行榜每次加载需要 100+ 只股票的K线数据

方案：
  - kline_cache 表：每只股票一行，K线存为 JSON
  - 每天盘后 15:30 自动刷新（覆盖 Top 200 市值股）
  - 评分时优先从 DB 读取，命中缓存则零网络请求

使用方式：
  from app.scoring.kline_cache import get_cached_klines, refresh_kline_cache
  
  # 评分时读取（优先DB缓存）
  klines = get_cached_klines("000001")
  
  # 盘后批量刷新
  refresh_kline_cache()
================================================================================
"""

import json
import time
from typing import Dict, List, Optional
from datetime import datetime

from app.database import db


# ── 配置 ──
CACHE_POOL_SIZE = 500       # 缓存股票池大小（按市值排序前N只）
CACHE_KLINE_COUNT = 500     # 每只股票缓存多少根K线
MAX_CACHE_AGE_HOURS = 36    # 缓存最大有效期（小时），超过则强制刷新


# ── 数据库表初始化 ──
def init_kline_cache_table():
    """创建K线缓存表"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS kline_cache (
            code TEXT PRIMARY KEY,
            name TEXT,
            kline_data TEXT NOT NULL,
            kline_count INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL,
            market_cap REAL DEFAULT 0
        )
    """)
    print("[kline_cache] kline_cache 表初始化完成")


# 启动时初始化
init_kline_cache_table()


def get_cached_klines(code: str) -> Optional[List[Dict]]:
    """
    从数据库获取K线缓存。
    
    返回：
      - K线列表 [{date, open, close, high, low, volume}, ...]
      - None 表示缓存不存在或已过期
    """
    row = db.fetch_one("""
        SELECT kline_data, updated_at, kline_count 
        FROM kline_cache WHERE code = %s
    """, (code,))
    
    if not row:
        return None
    
    # 检查缓存是否过期
    updated_at = row.get("updated_at", "")
    if updated_at:
        try:
            update_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
            age_hours = (datetime.now() - update_time).total_seconds() / 3600
            if age_hours > MAX_CACHE_AGE_HOURS:
                return None  # 缓存过期
        except ValueError:
            return None
    
    # 解析JSON
    kline_json = row.get("kline_data", "[]")
    try:
        klines = json.loads(kline_json)
        if len(klines) >= 30:  # 至少30根K线才能评分
            return klines
        return None
    except (json.JSONDecodeError, TypeError):
        return None


def get_cached_klines_batch(codes: List[str]) -> Dict[str, List[Dict]]:
    """
    批量获取K线缓存。
    
    返回：{code: [klines...], ...}
    """
    if not codes:
        return {}
    
    # 构建 IN 查询
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.fetch(f"""
        SELECT code, kline_data, updated_at, kline_count 
        FROM kline_cache WHERE code IN ({placeholders})
    """, tuple(codes))
    
    result = {}
    for row in rows:
        code = row["code"]
        kline_json = row.get("kline_data", "[]")
        try:
            klines = json.loads(kline_json)
            if len(klines) >= 30:
                result[code] = klines
        except (json.JSONDecodeError, TypeError):
            pass
    
    return result


def save_kline_cache(code: str, name: str, klines: List[Dict], market_cap: float = 0):
    """
    保存K线数据到数据库缓存。
    """
    if not klines:
        return
    
    kline_json = json.dumps(klines, ensure_ascii=False)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        db.execute("""
            INSERT INTO kline_cache (code, name, kline_data, kline_count, updated_at, market_cap)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                name = CASE WHEN kline_cache.name IS NULL OR kline_cache.name = ''
                            THEN EXCLUDED.name ELSE kline_cache.name END,
                kline_data = EXCLUDED.kline_data,
                kline_count = EXCLUDED.kline_count,
                updated_at = EXCLUDED.updated_at,
                market_cap = EXCLUDED.market_cap
        """, (code, name, kline_json, len(klines), now, market_cap))
    except Exception as e:
        print(f"[kline_cache] 保存缓存失败 {code}: {e}")


def refresh_kline_cache(codes: List[str] = None, progress_callback=None) -> Dict:
    """
    批量刷新K线缓存（从腾讯API拉取并写入数据库）。
    
    参数：
      codes: 要刷新的股票代码列表。为空则自动取市值前 CACHE_POOL_SIZE 只。
      progress_callback: 可选的进度回调函数 callback(current, total, code)
    
    返回：
      {"refreshed": N, "failed": M, "total": T, "duration_seconds": S}
    """
    from app.tencent import _cache as tencent_cache, get_kline, refresh_all_stocks
    
    start_time = time.time()
    
    # 如果没指定代码，自动取市值前N只
    if not codes:
        # 先确保行情缓存已加载（盘后可能未加载）
        stocks = tencent_cache.get("stocks", {})
        if not stocks or len(stocks) < 100:
            print("[kline_cache] 行情缓存为空，先刷新行情...")
            try:
                refresh_all_stocks(force=True)
                stocks = tencent_cache.get("stocks", {})
            except Exception as e:
                print(f"[kline_cache] 行情刷新失败: {e}")
        
        if not stocks:
            print("[kline_cache] 行情缓存为空，无法刷新K线缓存")
            return {"refreshed": 0, "failed": 0, "total": 0, "duration_seconds": 0}
        
        stock_list = sorted(
            stocks.values(),
            key=lambda s: s.get("market_cap", 0) or 0,
            reverse=True
        )
        codes = [s.get("code") for s in stock_list[:CACHE_POOL_SIZE] if s.get("code")]
    
    total = len(codes)
    refreshed = 0
    failed = 0
    
    print(f"[kline_cache] 开始刷新 {total} 只股票的K线缓存...")
    
    for i, code in enumerate(codes):
        try:
            # 从腾讯API拉取K线
            klines = get_kline(code, period="day", count=CACHE_KLINE_COUNT)
            
            if klines and len(klines) >= 30:
                # 获取股票名称和市值
                stock_info = tencent_cache.get("stocks", {}).get(code, {})
                name = stock_info.get("name", "")
                market_cap = stock_info.get("market_cap", 0) or 0
                
                save_kline_cache(code, name, klines, market_cap)
                refreshed += 1
            else:
                failed += 1
                
        except Exception as e:
            failed += 1
            if failed <= 5:  # 只打印前5个错误
                print(f"[kline_cache] 刷新失败 {code}: {e}")
        
        # 进度回调
        if progress_callback:
            progress_callback(i + 1, total, code)
        
        # 每20只打印一次进度
        if (i + 1) % 20 == 0:
            print(f"[kline_cache] 进度: {i+1}/{total} (成功{refreshed} 失败{failed})")
    
    duration = time.time() - start_time
    print(f"[kline_cache] 刷新完成: {refreshed}成功 {failed}失败 耗时{duration:.1f}s")
    
    return {
        "refreshed": refreshed,
        "failed": failed,
        "total": total,
        "duration_seconds": round(duration, 1),
    }


def get_cache_status() -> Dict:
    """
    获取K线缓存状态。
    """
    row = db.fetch_one("""
        SELECT COUNT(*) as total,
               MIN(updated_at) as oldest,
               MAX(updated_at) as newest
        FROM kline_cache
    """)
    
    total = row.get("total", 0) if row else 0
    oldest = row.get("oldest", "") if row else ""
    newest = row.get("newest", "") if row else ""
    
    # 统计过期数量
    cutoff = (datetime.now() - __import__('datetime').timedelta(hours=MAX_CACHE_AGE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    expired_row = db.fetch_one("""
        SELECT COUNT(*) as cnt FROM kline_cache WHERE updated_at < %s
    """, (cutoff,))
    expired = expired_row.get("cnt", 0) if expired_row else 0
    
    return {
        "total_cached": total,
        "oldest_update": oldest,
        "newest_update": newest,
        "expired_count": expired,
        "pool_size": CACHE_POOL_SIZE,
        "kline_count": CACHE_KLINE_COUNT,
        "max_age_hours": MAX_CACHE_AGE_HOURS,
    }
