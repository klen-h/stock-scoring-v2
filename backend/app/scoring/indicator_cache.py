"""
================================================================================
【文件作用】技术指标数据库缓存（指标层缓存）
================================================================================

在 K 线缓存（kline_cache.py）的基础上，进一步缓存计算好的技术指标。

架构层次：
  原始数据层：kline_cache.py    → 存 K 线（OHLCV）
  指标计算层：indicator_cache.py → 存 MA/EMA/MACD/RSI/KDJ/BOLL
  评分层：    engine.py          → 用指标算分

解决问题：
  - 评分时不再需要重新计算 500 根 K 线的指标
  - 盘中只需用最新价增量更新指标（EMA/RSI 都有增量公式）
  - 100 只候选股精算从 "重算 500 根 K 线" 变成 "读 1 行预计算指标"

使用方式：
  from app.scoring.indicator_cache import get_cached_indicators, refresh_indicator_cache
  
  # 评分时读取（优先 DB 缓存）
  indicators = get_cached_indicators("000001")
  
  # 盘后批量刷新（计算并存储指标）
  refresh_indicator_cache()
================================================================================
"""

import json
import time
from typing import Dict, List, Optional
from datetime import datetime

from app.database import db
from app.scoring.kline_cache import MIN_SCORING_KLINE_COUNT


# ── 配置 ──
INDICATOR_POOL_SIZE = 300   # 预计算股票池大小（按市值排序前 N 只）
MAX_INDICATOR_AGE_HOURS = 36  # 缓存最大有效期（小时）
SERIES_DAYS = 60            # 存储最近 N 天指标数组（评分最多需要 60 天高低点）


# ── 数据库表初始化 ──
def init_indicator_cache_table():
    """创建指标缓存表"""
    db.execute("""
        CREATE TABLE IF NOT EXISTS indicator_cache (
            code TEXT PRIMARY KEY,
            name TEXT,
            indicators TEXT NOT NULL,
            kline_count INTEGER DEFAULT 0,
            updated_at TEXT NOT NULL,
            market_cap REAL DEFAULT 0
        )
    """)
    print("[indicator_cache] indicator_cache 表初始化完成")


# 启动时初始化
init_indicator_cache_table()


def get_cached_indicators(code: str) -> Optional[Dict]:
    """
    从数据库获取指标缓存。
    
    返回：
      - 指标字典 {ma5, ma10, ..., _series: [近80天指标数组], _state: {...}}
      - None 表示缓存不存在或已过期
    """
    # ★ DATA_SOURCE=pack/local：优先读数据包；未命中回退 DB（仅未覆盖代码有流量）
    try:
        from app import pack_source
        if pack_source.enabled():
            ind = pack_source.get_indicators(code)
            if ind and (ind.get("_series") or ind.get("ma5") is not None):
                return ind
    except Exception:
        pass

    row = db.fetch_one("""
        SELECT indicators, updated_at, kline_count 
        FROM indicator_cache WHERE code = %s
    """, (code,))
    
    if not row:
        return None
    
    # 检查缓存是否过期
    updated_at = row.get("updated_at", "")
    if updated_at:
        try:
            update_time = datetime.strptime(updated_at, "%Y-%m-%d %H:%M:%S")
            age_hours = (datetime.now() - update_time).total_seconds() / 3600
            if age_hours > MAX_INDICATOR_AGE_HOURS:
                return None  # 缓存过期
        except ValueError:
            return None

    # ★ 截断缓存门槛：kline_count 在 (0, 250) 区间的行来自短拉取覆盖的 K 线，
    #   指标严重失真（KDJ 未收敛/MA60 缺失），评分不可采信。
    #   kline_count=0 是盘中增量更新的合法标记，放行。
    kline_count = row.get("kline_count") or 0
    if 0 < kline_count < MIN_SCORING_KLINE_COUNT:
        return None

    # 解析 JSON
    indicators_json = row.get("indicators", "{}")
    try:
        indicators = json.loads(indicators_json)
        # 兼容新旧格式：新版有 _series，旧版有 ma5
        if indicators.get("_series") or indicators.get("ma5") is not None:
            return indicators
        return None
    except (json.JSONDecodeError, TypeError):
        return None


def get_cached_technical(code: str) -> Optional[List[Dict]]:
    """
    ★ 评分专用：从数据库直接获取近 80 天指标数组（可直接喂给 engine.score_stock）。
    
    这是评分的终极快速路径：零网络 + 零指标计算，只需一次 DB 查询 + JSON 解析。
    
    返回：
      - 指标数组 [{date, close, open, high, low, volume, ma5, ..., boll_lower}, ...]
      - None 表示缓存不可用
    """
    cached = get_cached_indicators(code)
    if not cached:
        return None
    
    series = cached.get("_series")
    if series and len(series) >= 30:
        return series
    return None


def get_cached_technical_batch(codes: List[str]) -> Dict[str, List[Dict]]:
    """批量获取指标数组（评分候选池预热用）"""
    result = {}
    for code in codes:
        series = get_cached_technical(code)
        if series:
            result[code] = series
    return result


def get_cached_technical_batch_sql(codes: List[str]) -> Dict[str, List[Dict]]:
    """
    ★ 评分加速核心：一条 SQL 批量读取多只股票的指标数组。
    
    相比逐只查询（N 次远程 DB 往返），这里只需 1 次往返，
    100 只候选股的指标读取从 ~50s 降到 < 2s。
    """
    if not codes:
        return {}

    # ★ DATA_SOURCE=pack/local：从数据包批量取（零 DB 往返）；未命中的代码
    #   不返回，由 _precise_score_sync 的逐只兜底路径处理（同 DB 未命中语义）
    try:
        from app import pack_source
        if pack_source.enabled():
            out = {}
            for _c in codes:
                ind = pack_source.get_indicators(_c)
                if ind:
                    series = ind.get("_series")
                    if series and len(series) >= 30:
                        out[_c] = series
            return out
    except Exception:
        pass

    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=MAX_INDICATOR_AGE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.fetch(f"""
        SELECT code, indicators, kline_count
        FROM indicator_cache
        WHERE code IN ({placeholders}) AND updated_at >= %s
    """, tuple(codes) + (cutoff,))

    result = {}
    for row in rows or []:
        # ★ 截断缓存门槛（与 get_cached_indicators 同口径）：
        #   kline_count 在 (0, 250) 区间的行来自短拉取覆盖的 K 线，指标不可信
        kline_count = row.get("kline_count") or 0
        if 0 < kline_count < MIN_SCORING_KLINE_COUNT:
            continue
        try:
            indicators = json.loads(row.get("indicators", "{}"))
            series = indicators.get("_series")
            if series and len(series) >= 30:
                result[row["code"]] = series
        except (json.JSONDecodeError, TypeError):
            pass

    return result


def get_cached_indicators_batch(codes: List[str]) -> Dict[str, Dict]:
    """
    批量获取指标缓存。
    
    返回：{code: {indicators...}, ...}
    """
    if not codes:
        return {}
    
    # 构建 IN 查询
    placeholders = ",".join(["%s"] * len(codes))
    rows = db.fetch(f"""
        SELECT code, indicators, updated_at, kline_count 
        FROM indicator_cache WHERE code IN ({placeholders})
    """, tuple(codes))
    
    result = {}
    for row in rows:
        code = row["code"]
        indicators_json = row.get("indicators", "{}")
        try:
            indicators = json.loads(indicators_json)
            if indicators.get("ma5") is not None:
                result[code] = indicators
        except (json.JSONDecodeError, TypeError):
            pass
    
    return result


def save_indicator_cache(code: str, name: str, indicators: Dict, kline_count: int = 0, market_cap: float = 0):
    """
    保存指标数据到数据库缓存。
    """
    if not indicators:
        return
    
    indicators_json = json.dumps(indicators, ensure_ascii=False)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        db.execute("""
            INSERT INTO indicator_cache (code, name, indicators, kline_count, updated_at, market_cap)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                indicators = EXCLUDED.indicators,
                kline_count = EXCLUDED.kline_count,
                updated_at = EXCLUDED.updated_at,
                market_cap = EXCLUDED.market_cap
        """, (code, name, indicators_json, kline_count, now, market_cap))
    except Exception as e:
        print(f"[indicator_cache] 保存缓存失败 {code}: {e}")


def compute_latest_indicators(klines: list) -> Dict:
    """
    从 K 线计算技术指标，返回：
      - 近 80 天完整指标数组（_series，可直接喂给评分引擎）
      - 最新一行的指标值（顶层字段）
      - 增量更新状态（_state）
    """
    from app.routers.scoring import _calc_technical_fast
    import numpy as np
    
    if len(klines) < 30:
        return {}
    
    # 用向量化版本计算全量指标
    technical = _calc_technical_fast(klines)
    if not technical:
        return {}
    
    # ★ 取最近 60 天完整指标数组（评分引擎最多需要 60 天高低点）
    series = technical[-SERIES_DAYS:]
    
    # 保留评分所需字段（去掉多余字段减小 JSON 体积）
    series_fields = [
        "date", "close", "open", "high", "low", "volume",
        "ma5", "ma10", "ma20", "ma60",
        "dif", "dea", "macd", "rsi", "k", "d", "j",
        "boll_upper", "boll_mid", "boll_lower"
    ]
    
    def _round_val(v):
        """四舍五入减小 JSON 体积（评分不需要高精度）"""
        if v is None or isinstance(v, str):
            return v
        try:
            import math
            if math.isnan(v):
                return None
            return round(float(v), 2)
        except (TypeError, ValueError):
            return v
    
    slim_series = [
        {k: (_round_val(row.get(k)) if k != "date" else row.get("date")) for k in series_fields}
        for row in series
    ]
    
    # 最新一行的指标值（顶层字段）
    latest = series[-1]
    indicator_fields = [
        "ma5", "ma10", "ma20", "ma60",
        "dif", "dea", "macd", "rsi", "k", "d", "j",
        "boll_upper", "boll_mid", "boll_lower"
    ]
    result = {k: latest.get(k) for k in indicator_fields}
    
    # ★ 存入完整指标数组
    result["_series"] = slim_series
    
    # ── 计算增量更新状态 ──
    closes = np.array([k["close"] for k in klines], dtype=np.float64)
    highs = np.array([k["high"] for k in klines], dtype=np.float64)
    lows = np.array([k["low"] for k in klines], dtype=np.float64)
    n = len(closes)
    
    # EMA 状态：计算最后的 EMA 值
    def calc_ema_final(data, span):
        alpha = 2.0 / (span + 1)
        ema = data[0]
        for i in range(1, len(data)):
            ema = data[i] * alpha + ema * (1 - alpha)
        return ema
    
    ema12 = calc_ema_final(closes, 12)
    ema26 = calc_ema_final(closes, 26)
    dif_val = ema12 - ema26
    
    # DEA 是 DIF 序列的 EMA9（直接对最后一段 DIF 序列递推，避免 O(n²)）
    ema12_arr = np.empty(n)
    ema26_arr = np.empty(n)
    a12, a26 = 2.0/13, 2.0/27
    ema12_arr[0], ema26_arr[0] = closes[0], closes[0]
    for i in range(1, n):
        ema12_arr[i] = closes[i] * a12 + ema12_arr[i-1] * (1 - a12)
        ema26_arr[i] = closes[i] * a26 + ema26_arr[i-1] * (1 - a26)
    dif_arr = ema12_arr - ema26_arr
    dea_val = calc_ema_final(dif_arr, 9)
    
    # RSI 状态：计算最后的 avg_gain, avg_loss
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains[-14:])) if len(gains) >= 14 else 0.0
    avg_loss = float(np.mean(losses[-14:])) if len(losses) >= 14 else 0.0
    
    # MA 状态：保存最近的价格窗口和
    ma_sums = {}
    for window in [5, 10, 20, 60]:
        if n >= window:
            ma_sums[window] = float(np.sum(closes[-window:]))
    
    # KDJ 状态
    k_val = latest.get("k") or 50.0
    d_val = latest.get("d") or 50.0
    
    # 保存最近价格（用于增量更新 MA/BOLL）
    last_prices = closes[-60:].tolist()
    last_highs = highs[-9:].tolist()
    last_lows = lows[-9:].tolist()
    
    result["_state"] = {
        "ema12": float(ema12),
        "ema26": float(ema26),
        "dea": float(dea_val),
        "avg_gain": avg_gain,
        "avg_loss": avg_loss,
        "ma_sums": ma_sums,
        "k": float(k_val),
        "d": float(d_val),
        "last_prices": last_prices,
        "last_highs": last_highs,
        "last_lows": last_lows,
        "last_close": float(closes[-1]),
    }
    
    return result


def refresh_indicator_cache(codes: List[str] = None, progress_callback=None) -> Dict:
    """
    批量刷新指标缓存（从 K 线缓存计算并写入数据库）。
    
    参数：
      codes: 要刷新的股票代码列表。为空则自动取市值前 INDICATOR_POOL_SIZE 只。
      progress_callback: 可选的进度回调函数 callback(current, total, code)
    
    返回：
      {"refreshed": N, "failed": M, "total": T, "duration_seconds": S}
    """
    from app.scoring.kline_cache import get_cached_klines, get_cache_status
    from app.tencent import _cache as tencent_cache
    
    start_time = time.time()
    
    # 如果没指定代码，自动取市值前 N 只
    if not codes:
        stocks = tencent_cache.get("stocks", {})
        if stocks:
            stock_list = sorted(
                stocks.values(),
                key=lambda s: s.get("market_cap", 0) or 0,
                reverse=True
            )
            codes = [s.get("code") for s in stock_list[:INDICATOR_POOL_SIZE] if s.get("code")]
        else:
            # 行情缓存为空（非交易时段/重启后），回退到 K 线缓存中已有的代码
            print("[indicator_cache] 行情缓存为空，回退到 K 线缓存中的代码")
            rows = db.fetch("SELECT code FROM kline_cache LIMIT %s", (INDICATOR_POOL_SIZE,))
            codes = [r["code"] for r in (rows or [])]
            if not codes:
                print("[indicator_cache] K 线缓存也为空，无法刷新指标缓存")
                return {"refreshed": 0, "failed": 0, "total": 0, "duration_seconds": 0}
    
    total = len(codes)
    refreshed = 0
    failed = 0
    
    print(f"[indicator_cache] 开始刷新 {total} 只股票的指标缓存...")
    
    for i, code in enumerate(codes):
        try:
            # 从 K 线缓存读取
            klines = get_cached_klines(code)
            
            if klines and len(klines) >= 30:
                # 计算指标
                indicators = compute_latest_indicators(klines)
                
                if indicators and indicators.get("ma5") is not None:
                    # 获取股票名称和市值
                    stock_info = tencent_cache.get("stocks", {}).get(code, {})
                    name = stock_info.get("name", "")
                    market_cap = stock_info.get("market_cap", 0) or 0
                    
                    save_indicator_cache(code, name, indicators, len(klines), market_cap)
                    refreshed += 1
                else:
                    failed += 1
            else:
                failed += 1
                
        except Exception as e:
            failed += 1
            if failed <= 5:  # 只打印前 5 个错误
                print(f"[indicator_cache] 刷新失败 {code}: {e}")
        
        # 进度回调
        if progress_callback:
            progress_callback(i + 1, total, code)
        
        # 每 50 只打印一次进度
        if (i + 1) % 50 == 0:
            print(f"[indicator_cache] 进度: {i+1}/{total} (成功{refreshed} 失败{failed})")
    
    duration = time.time() - start_time
    print(f"[indicator_cache] 刷新完成: {refreshed}成功 {failed}失败 耗时{duration:.1f}s")
    
    return {
        "refreshed": refreshed,
        "failed": failed,
        "total": total,
        "duration_seconds": round(duration, 1),
    }


def get_indicator_cache_status() -> Dict:
    """
    获取指标缓存状态。
    """
    row = db.fetch_one("""
        SELECT COUNT(*) as total,
               MIN(updated_at) as oldest,
               MAX(updated_at) as newest
        FROM indicator_cache
    """)
    
    total = row.get("total", 0) if row else 0
    oldest = row.get("oldest", "") if row else ""
    newest = row.get("newest", "") if row else ""
    
    # 统计过期数量
    cutoff = (datetime.now() - __import__('datetime').timedelta(hours=MAX_INDICATOR_AGE_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    expired_row = db.fetch_one("""
        SELECT COUNT(*) as cnt FROM indicator_cache WHERE updated_at < %s
    """, (cutoff,))
    expired = expired_row.get("cnt", 0) if expired_row else 0
    
    return {
        "total_cached": total,
        "oldest_update": oldest,
        "newest_update": newest,
        "expired_count": expired,
        "pool_size": INDICATOR_POOL_SIZE,
        "max_age_hours": MAX_INDICATOR_AGE_HOURS,
    }


def incremental_update(code: str, new_price: float, new_high: float = None, new_low: float = None) -> Optional[Dict]:
    """
    增量更新指标（盘中只拉最新价，无需重算 500 根 K 线）。
    
    增量公式：
      - EMA: EMA_new = price * alpha + EMA_old * (1 - alpha)
      - RSI: 用新的 price change 更新 avg_gain, avg_loss
      - MA: MA_new = (sum_old + new_price - old_price) / window
      - MACD: 基于 EMA 增量
      - KDJ: 用新的 9 日窗口高低点计算 RSV，然后增量更新 K, D
    
    参数：
      code: 股票代码
      new_price: 最新价
      new_high: 当日最高价（可选，用于 KDJ）
      new_low: 当日最低价（可选，用于 KDJ）
    
    返回：
      - 更新后的指标字典
      - None 表示缓存不存在，无法增量更新
    """
    # 读取缓存的指标 + 状态
    cached = get_cached_indicators(code)
    if not cached or "_state" not in cached:
        return None
    
    state = cached["_state"]
    
    # 提取状态
    ema12_old = state.get("ema12", 0)
    ema26_old = state.get("ema26", 0)
    dea_old = state.get("dea", 0)
    avg_gain_old = state.get("avg_gain", 0)
    avg_loss_old = state.get("avg_loss", 0)
    k_old = state.get("k", 50)
    d_old = state.get("d", 50)
    last_close = state.get("last_close", new_price)
    last_prices = state.get("last_prices", [])
    last_highs = state.get("last_highs", [])
    last_lows = state.get("last_lows", [])
    ma_sums = state.get("ma_sums", {})
    
    # ── EMA 增量更新 ──
    alpha12 = 2.0 / 13
    alpha26 = 2.0 / 27
    alpha9 = 2.0 / 10
    
    ema12_new = new_price * alpha12 + ema12_old * (1 - alpha12)
    ema26_new = new_price * alpha26 + ema26_old * (1 - alpha26)
    dif_new = ema12_new - ema26_new
    dea_new = dif_new * alpha9 + dea_old * (1 - alpha9)
    macd_new = (dif_new - dea_new) * 2
    
    # ── RSI 增量更新 ──
    delta = new_price - last_close
    gain = max(delta, 0)
    loss = max(-delta, 0)
    # Wilder 平滑：avg = (avg_old * 13 + new) / 14
    avg_gain_new = (avg_gain_old * 13 + gain) / 14
    avg_loss_new = (avg_loss_old * 13 + loss) / 14
    if avg_loss_new > 0:
        rs = avg_gain_new / avg_loss_new
        rsi_new = 100 - 100 / (1 + rs)
    else:
        rsi_new = 100
    
    # ── MA 增量更新 ──
    ma_new = {}
    for window in [5, 10, 20, 60]:
        if window in ma_sums:
            old_sum = ma_sums[window]
            # 移除最老的价格，加入新价格
            if len(last_prices) >= window:
                oldest_price = last_prices[-window]
                new_sum = old_sum - oldest_price + new_price
                ma_new[window] = new_sum / window
            else:
                ma_new[window] = cached.get(f"ma{window}")
        else:
            ma_new[window] = cached.get(f"ma{window}")
    
    # ── KDJ 增量更新 ──
    # 更新 9 日窗口
    if new_high is not None:
        last_highs = last_highs[-8:] + [new_high] if len(last_highs) >= 8 else last_highs + [new_high]
    if new_low is not None:
        last_lows = last_lows[-8:] + [new_low] if len(last_lows) >= 8 else last_lows + [new_low]
    
    high9 = max(last_highs) if last_highs else new_price
    low9 = min(last_lows) if last_lows else new_price
    
    if high9 != low9:
        rsv = (new_price - low9) / (high9 - low9) * 100
    else:
        rsv = 50
    
    k_new = 2/3 * k_old + 1/3 * rsv
    d_new = 2/3 * d_old + 1/3 * k_new
    j_new = 3 * k_new - 2 * d_new
    
    # ── BOLL 增量更新 ──
    # 需要重新计算最近 20 个价格的 std
    boll_mid_new = ma_new.get(20, cached.get("boll_mid"))
    boll_upper_new = cached.get("boll_upper")
    boll_lower_new = cached.get("boll_lower")
    
    if len(last_prices) >= 19:
        # 更新价格列表
        new_last_prices = last_prices[-19:] + [new_price]
        import numpy as np
        prices_arr = np.array(new_last_prices)
        std = np.std(prices_arr)
        boll_upper_new = boll_mid_new + 2 * std if boll_mid_new else None
        boll_lower_new = boll_mid_new - 2 * std if boll_mid_new else None
    
    # ── 更新状态 ──
    new_state = {
        "ema12": ema12_new,
        "ema26": ema26_new,
        "dea": dea_new,
        "avg_gain": avg_gain_new,
        "avg_loss": avg_loss_new,
        "ma_sums": {w: (ma_sums[w] - (last_prices[-w] if len(last_prices) >= w else 0) + new_price) for w in ma_sums},
        "k": k_new,
        "d": d_new,
        "last_prices": last_prices[-59:] + [new_price] if len(last_prices) >= 59 else last_prices + [new_price],
        "last_highs": last_highs,
        "last_lows": last_lows,
        "last_close": new_price,
    }
    
    # 构造新的指标字典
    result = {
        "ma5": ma_new.get(5),
        "ma10": ma_new.get(10),
        "ma20": ma_new.get(20),
        "ma60": ma_new.get(60),
        "dif": dif_new,
        "dea": dea_new,
        "macd": macd_new,
        "rsi": rsi_new,
        "k": k_new,
        "d": d_new,
        "j": j_new,
        "boll_upper": boll_upper_new,
        "boll_mid": boll_mid_new,
        "boll_lower": boll_lower_new,
        "_state": new_state,
    }
    
    return result


def save_incremental_update(code: str, indicators: Dict, name: str = "", market_cap: float = 0):
    """
    保存增量更新后的指标到数据库。
    """
    if not indicators:
        return
    
    # 分离指标值和状态
    state = indicators.get("_state", {})
    indicator_fields = {
        "ma5": indicators.get("ma5"),
        "ma10": indicators.get("ma10"),
        "ma20": indicators.get("ma20"),
        "ma60": indicators.get("ma60"),
        "dif": indicators.get("dif"),
        "dea": indicators.get("dea"),
        "macd": indicators.get("macd"),
        "rsi": indicators.get("rsi"),
        "k": indicators.get("k"),
        "d": indicators.get("d"),
        "j": indicators.get("j"),
        "boll_upper": indicators.get("boll_upper"),
        "boll_mid": indicators.get("boll_mid"),
        "boll_lower": indicators.get("boll_lower"),
        "_state": state,
    }
    
    indicators_json = json.dumps(indicator_fields, ensure_ascii=False)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    try:
        db.execute("""
            INSERT INTO indicator_cache (code, name, indicators, kline_count, updated_at, market_cap)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (code) DO UPDATE SET
                name = EXCLUDED.name,
                indicators = EXCLUDED.indicators,
                updated_at = EXCLUDED.updated_at,
                market_cap = EXCLUDED.market_cap
        """, (code, name, indicators_json, 0, now, market_cap))
    except Exception as e:
        print(f"[indicator_cache] 保存增量更新失败 {code}: {e}")
