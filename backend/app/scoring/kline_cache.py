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
# 评分链路可采信的最小K线数。战法扫描（count=30/60/120）、持仓撤退提醒（count=30）
# 等短拉取曾把 kline_cache 覆盖截断，排行精算读到截断数据导致指标失真
# （000833 实测：30根 → 技术面 57.3；全量 → 60.3，KDJ 未收敛 / MA60 缺失）。
# 低于此数的缓存：写侧不允许短拉取覆盖（tencent.get_kline），
# 读侧评分兜底不直接采信（scoring._precise_score_sync / indicator_cache）。
MIN_SCORING_KLINE_COUNT = 250


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


# ── K 线日期新鲜度（2026-09-03 新增，根治"缓存是新的、数据是旧的"）──
# 2026-09-02 案例：15:30 刷新时腾讯日线尚未更新当日 bar（更新滞后数小时是
# 常态，ETF 案例已证实），"截至 9/1 的 K 线"被打上 9/2 15:30 的新鲜
# updated_at；旧逻辑只看 updated_at（36h）和条数，不看最后 K 线日期 →
# 16:30 战法扫描推送的全是 9/1 收盘数据，且 9/3 白天仍被采信。
def expected_kline_date() -> Optional[str]:
    """当前时点 K 线应到达的最后交易日（仅交易日下午严格校验）。

    - 交易日 15:00 之后 → 返回今天（收盘后当日 bar 应存在；腾讯日线滞后
      时由 _append_today_bar 用实时行情合成）
    - 盘中 / 盘前 / 非交易日 → None（不强校验：当日 bar 尚未生成，
      昨日 K 线就是最新，属正常状态）
    """
    try:
        from app.flash import rules
        now = rules.beijing_now()
        if now.hour >= 15 and rules.is_trading_day(now):
            return now.strftime("%Y-%m-%d")
    except Exception:
        return None
    return None


def _prev_trading_date(date_str: str) -> str:
    """date_str 的上一个交易日（最多回溯 15 天，覆盖长假）。"""
    from datetime import datetime, timedelta
    try:
        from app.flash import rules
        d = datetime.strptime(date_str, "%Y-%m-%d")
        for i in range(1, 16):
            cand = d - timedelta(days=i)
            if rules.is_trading_day(cand):
                return cand.strftime("%Y-%m-%d")
    except Exception:
        pass
    return ""


def _tencent_cache_key(code: str) -> str:
    """腾讯行情缓存键（sh600906 / sz000001 / bj920xxx）。"""
    if code.startswith("92") or code[0] in "48":
        return f"bj{code}"
    if code[0] in "569":
        return f"sh{code}"
    return f"sz{code}"


def _append_today_bar(klines: List[Dict], code: str, expect_date: str) -> List[Dict]:
    """K 线缺当日 bar 时，用腾讯实时行情快照合成（盘后价格已定格收盘）。

    这是对"腾讯日线更新滞后"的根本兜底：日线接口当日 bar 常晚出数小时，
    而 15:30 缓存刷新 / 15:40 战法扫描等不及。合成失败时原样返回，
    由调用方决定降级策略（读侧视为过期走实时拉取）。
    """
    try:
        if klines and (klines[-1].get("date") or "")[:10] == expect_date:
            return klines
        # ★ 连续性校验：只有缓存停在"前一交易日"才允许合成一根补齐。
        #   缓存若停在更早（如 8/26 vs 期望 9/3，中间缺 8/27~9/2 多日），
        #   补一根也无法修复序列的洞 —— 返回原数据，由读侧判过期走实时
        #   拉取拿全量（宁可慢，不可序列有洞）。
        prev_expect = _prev_trading_date(expect_date)
        last_date = (klines[-1].get("date") or "")[:10]
        if prev_expect and last_date < prev_expect:
            print(f"[kline_cache] {code} K线停在 {last_date}，与 {expect_date} "
                  f"之间缺多日，不合成（走实时拉取补全量）")
            return klines
        from app.tencent import _cache as tencent_cache
        s = (tencent_cache.get("stocks") or {}).get(_tencent_cache_key(code)) or {}
        price = s.get("price") or 0
        if price <= 0:
            return klines
        bar = {
            "date": expect_date,
            "open": s.get("open") or price,
            "close": price,
            "high": max(s.get("high") or price, price),
            "low": min(s.get("low") or price, price),
            "volume": s.get("volume") or 0,
        }
        return list(klines) + [bar]
    except Exception:
        return klines


def get_cached_klines(code: str) -> Optional[List[Dict]]:
    """
    从数据库获取K线缓存。
    
    返回：
      - K线列表 [{date, open, close, high, low, volume}, ...]
      - None 表示缓存不存在或已过期
    """
    # ★ DATA_SOURCE=pack/local：优先读数据包（GitHub Pages/本地文件，零 Supabase 流量）；
    #   pack 未命中的代码回退 DB 兜底（只对未覆盖代码产生少量流量）
    try:
        from app import pack_source
        if pack_source.enabled():
            _k = pack_source.get_klines(code)
            if _k is not None:
                return _k
    except Exception:
        pass

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
            # ★ 读侧日期校验：盘后缓存最后 K 线必须到达期望交易日，落后则
            #   用实时行情合成当日 bar 兜底；合成失败（行情缓存为空）视为
            #   过期返回 None，上层走实时拉取 —— 宁可慢，不可旧。
            expect = expected_kline_date()
            if expect and (klines[-1].get("date") or "")[:10] < expect:
                klines = _append_today_bar(klines, code, expect)
                if (klines[-1].get("date") or "")[:10] < expect:
                    print(f"[kline_cache] {code} 缓存停在"
                          f"{(klines[-1].get('date') or '')[:10]}且无法合成当日bar，"
                          f"判过期走实时拉取")
                    return None
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
    expect = expected_kline_date()
    for row in rows:
        code = row["code"]
        kline_json = row.get("kline_data", "[]")
        try:
            klines = json.loads(kline_json)
            if len(klines) >= 30:
                # ★ 读侧日期校验（同 get_cached_klines）：落后则合成当日 bar，
                #   合成失败不返回该股（宁缺毋旧）
                if expect and (klines[-1].get("date") or "")[:10] < expect:
                    klines = _append_today_bar(klines, code, expect)
                    if (klines[-1].get("date") or "")[:10] < expect:
                        continue
                result[code] = klines
        except (json.JSONDecodeError, TypeError):
            pass

    return result


def get_cache_codes(limit: int = 100) -> List[str]:
    """有 K 线缓存的股票代码（按市值降序）。

    用途：服务重启/休眠后内存行情缓存为空时，用它兜底构建回测股票池，
    避免直接报「行情数据未就绪」（免费档重启频繁，等内存缓存就绪要很久）。
    """
    try:
        rows = db.fetch("SELECT code FROM kline_cache "
                        "ORDER BY market_cap DESC LIMIT %s", (limit,))
        return [r["code"] for r in rows]
    except Exception:
        return []


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
      {"refreshed": N, "failed": M, "total": T, "duration_seconds": S,
       "success_rate": 0~1}

    ★ 两轮重试 + 失败退避（照搬 scripts/generate-kline-pack.py 的成熟策略）：
      2026-09-02/09/03 实测 15:30 定时刷新只写入 18/22 只（应 ~500）——
      腾讯 WAF（HTTP 501）触发 120s 全局冷却后，单轮循环剩下全部瞬间判失败，
      表现为"起了个头就断"。pack 脚本用「两轮 + 轮间停 8s + 连续失败递增冷却」
      在同样数据源上每天稳定成功，故照搬。
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
    refreshed_set = set()
    pending = list(codes)
    consec_fail = 0

    print(f"[kline_cache] 开始刷新 {total} 只股票的K线缓存...")

    for round_no in (1, 2):
        if not pending:
            break
        failed_round = []
        n = len(pending)
        print(f"[kline_cache] 第{round_no}轮：{n} 只待刷新")

        for i, code in enumerate(pending):
            try:
                ok = _refresh_one(code, tencent_cache, get_kline)
            except Exception as e:
                ok = False
                if len(failed_round) <= 5:  # 只打印前5个错误
                    print(f"[kline_cache] 刷新失败 {code}: {e}")

            if ok:
                refreshed_set.add(code)
                consec_fail = 0
            else:
                failed_round.append(code)
                consec_fail += 1
                # 连续失败 → 递增冷却：WAF 冷却期里每只都会"秒失败"，
                # 不退避就是纯撞墙（2026-09-02/03 只写入 18/22 只的根因）
                if consec_fail >= 10:
                    wait = min(60, consec_fail * 5)
                    print(f"[kline_cache] 连续失败 {consec_fail} 次，"
                          f"暂停 {wait}s 等 WAF 冷却")
                    time.sleep(wait)
                    consec_fail = 0

            # 进度回调
            if progress_callback:
                progress_callback(i + 1, n, code)

            # 每20只打印一次进度
            if (i + 1) % 20 == 0:
                print(f"[kline_cache] 进度(第{round_no}轮): {i + 1}/{n} "
                      f"(累计成功{len(refreshed_set)} 待重试{len(failed_round)})")

        pending = failed_round
        if round_no == 1 and pending:
            print(f"[kline_cache] 第1轮结束：成功{len(refreshed_set)} / 失败{len(pending)}，"
                  f"停 8s 等 WAF 松弛后重试")
            time.sleep(8)

    refreshed = len(refreshed_set)
    failed = total - refreshed
    duration = time.time() - start_time
    rate = (refreshed / total) if total else 0.0
    print(f"[kline_cache] 刷新完成: {refreshed}成功 {failed}失败 "
          f"成功率{rate:.0%} 耗时{duration:.1f}s")

    return {
        "refreshed": refreshed,
        "failed": failed,
        "total": total,
        "duration_seconds": round(duration, 1),
        "success_rate": round(rate, 3),
    }


def _refresh_one(code: str, tencent_cache: dict, get_kline) -> bool:
    """刷新单只：拉取 → 合成当日 bar → 落库。成功 True，失败 False。

    ★ 失败不再落库（原实现会照写）：缺当日 bar 且实时合成失败时，若照常
      save_kline_cache，会把"截至昨日"的 K 线盖上新鲜 updated_at —— 读侧
      36h 内都当它新鲜，实为旧数据（静默污染）。计为失败留给第二轮重试。
    """
    klines = get_kline(code, period="day", count=CACHE_KLINE_COUNT)
    if not klines or len(klines) < 30:
        return False

    # 写侧日期保障：腾讯日线当日 bar 更新滞后是常态，15:30 刷到的可能停在昨日
    expect = expected_kline_date()
    if expect:
        klines = _append_today_bar(klines, code, expect)
        if (klines[-1].get("date") or "")[:10] < expect:
            return False

    # 获取股票名称和市值（stocks 键为 sh600906 格式，需转换——
    # 原代码 get(code) 纯 6 位永远取不到，name/market_cap 恒为空）
    stock_info = tencent_cache.get("stocks", {}).get(_tencent_cache_key(code), {})

    save_kline_cache(code, stock_info.get("name", ""), klines,
                     stock_info.get("market_cap", 0) or 0)
    return True


def get_cache_status() -> Dict:
    """
    获取K线缓存状态。
    """
    # ★ DATA_SOURCE=pack/local：用数据包的覆盖情况伪装状态
    try:
        from app import pack_source
        if pack_source.enabled():
            st = pack_source.status()
            return {"total_cached": st.get("total", 0),
                    "oldest_update": "",
                    "newest_update": st.get("date", ""),
                    "expired_count": 0,
                    "pool_size": CACHE_POOL_SIZE,
                    "kline_count": CACHE_KLINE_COUNT,
                    "max_age_hours": MAX_CACHE_AGE_HOURS,
                    "source": "pack"}
    except Exception:
        pass

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
