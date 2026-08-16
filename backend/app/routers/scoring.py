"""
================================================================================
【文件作用】评分路由（核心业务接口）
================================================================================

注册到 main.py 后，URL 前缀 /api/score：
  GET /api/score/{symbol}           → 单只股票完整评分 ★
  GET /api/score/batch/top          → 评分最高的 N 只（推荐买入）
  GET /api/score/batch/bottom       → 评分最低的 N 只（建议回避）
  GET /api/score/batch/signal       → 按信号筛选（强烈买入/买入/观望/卖出/强烈卖出）

这个文件是"数据层 → 评分引擎 → 接口"的组装层：
  从 tencent.py 拿数据 → 喂给 engine.py 算分 → 包装成 JSON 返回前端
================================================================================
"""

from fastapi import APIRouter, Query, BackgroundTasks
import asyncio
from types import SimpleNamespace
from app.scoring.engine import ScoreEngine
from app.tencent import get_stock, get_kline, _cache, get_stocks_batch

router = APIRouter()
# 全局单例：评分引擎只创建一次，复用（它内部无状态）
engine = ScoreEngine()

# ── 亏损股过滤开关 ──
# True：PE ≤ 0 的股票（亏损/无盈利）不进入「买入推荐榜单」(score_top)。
#       注意：仅在 Top 榜单过滤，Bottom(回避榜) 和按信号筛选不过滤
#       （亏损股本就该出现在回避榜；详情页仍可查评分，只是不推荐买入）。
# 设为 False 即可恢复，无需改其他代码。
EXCLUDE_LOSS_MAKING = True


def _compute_buy_point_single(code: str) -> dict:
    """
    为单只股票计算买入时机指标（MA20偏离/布林位置/支撑位）。
    用于 Top 5 排序后单独拉K线计算，避免批量精算时100只并发触发WAF。
    如果K线已在缓存中（批量精算时拉过），直接复用，无需重新请求。
    """
    klines = get_kline(code, period="day", count=500)
    if len(klines) >= 30:
        tech = _calc_technical(klines)
        return engine._calc_buy_point(tech)
    return {}


def _precise_score_sync(stock_info: dict) -> dict:
    """
    对单只股票执行「完整评分」的同步实现（与 /api/score/{symbol} 完全一致）。
    用于批量列表的 Top N 精算，保证列表分数 == 详情页分数。

    注意：本函数是同步的（内部 get_kline 是阻塞网络调用）。
    在 _batch_with_precise_top 里通过 asyncio.to_thread 放到线程池并发执行，
    并用 Semaphore 限制并发数，避免压垮腾讯接口。

    输入：stock_info（来自实时缓存的行情 dict）
    返回：与 /api/score/{symbol} 同结构的 dict；若数据不足则回退到简化分。
    """
    code = stock_info.get("code", "")
    name = stock_info.get("name", "")
    # 拉 K线 + 技术指标（与 score_single 完全相同的流程）
    technical_data = get_kline(code, period="day", count=500)
    if len(technical_data) >= 30:
        technical_data = _calc_technical(technical_data)
    # 组装基本面（与 score_single 一致）
    fundamental = {
        "valuation": {
            "市盈率(动态)": stock_info.get("pe", 0),
            "市净率": stock_info.get("pb", 0),
            "总市值(亿)": round(stock_info.get("market_cap", 0) / 10000, 2),
            "流通市值(亿)": round(stock_info.get("float_cap", 0) / 10000, 2),
        },
        "financial": {"换手率": stock_info.get("turnover_rate", 0)},
    }
    result = engine.score_stock(
        code=code, name=name,
        technical_data=technical_data,
        stock_info=stock_info,
        fundamental=fundamental,
    )
    return {
        "code": result.code, "name": result.name,
        "total_score": result.total_score,
        "signal": result.signal, "signal_level": result.signal_level,
        # 买入原因（加分因素），供 Top 50 列表展示。最多 5 个，如「均线多头排列」「量价齐升」
        "factors_up": result.factors_up,
        # 批量模式跳过买入时机计算（避免100只股票同时拉K线触发WAF）
        # Top 5 的 buy_point 会在排序后单独计算
        "buy_point": {},
    }


async def _batch_with_precise_top(
    stocks: list,
    pick_indices,
    limit: int = 50,
    side: str = "both",
    margin: int = 50,
) -> list:
    """
    批量评分的两阶段策略，【保证】返回的每一行都与详情页 /api/score/{symbol}
    评分完全一致（列表分 == 详情分）。

      阶段1：用简化算法对全部股票快速评分排序（几千只，毫秒级）
      阶段2：取候选池（覆盖要展示的范围 + 安全余量），对池内每只用完整算法精算
      最终：只从【精算结果】里取要展示的子集——绝不把简化分混进返回值

    为什么不能混入简化分：简化分（单维度：动量+换手+PE）与完整分（三维度：
    技术40%+资金25%+基本面35%）尺度完全不同，混在一起排序会导致「排行页分数
    ≠ 详情页分数」。所以这里只返回精算过的股票。

    参数：
      stocks:       全量股票列表
      pick_indices: 函数，入参为「精算后并按分排序的 results」，返回要展示的子集
      limit:        要展示的行数（与候选池大小直接相关）
      side:         候选池取哪一段：'top'(头部) / 'bottom'(尾部) / 'both'(头尾都取)
                    top 榜只需头部候选、bottom 榜只需尾部候选，可少算一半；signal 取两端
      margin:       候选池在 limit 之外的安全余量，吸收「精算后排名重排」导致的偏移

    并发策略：Semaphore(5) 限流，配合 KLINE_CACHE（5分钟TTL），兼顾速度与接口压力。
    """
    if not stocks:
        return []

    # 阶段1：简化评分 + 排序（简化分只用于挑选候选池，绝不直接展示）
    rough = engine.score_batch(stocks)

    # 阶段2：候选池 = 简化榜头部 / 尾部，覆盖「要展示的范围 + 余量」
    pool_size = limit + margin
    candidate_codes = set()
    if side in ("top", "both"):
        candidate_codes |= {r.code for r in rough[:pool_size]}
    if side in ("bottom", "both") and len(rough) > pool_size:
        candidate_codes |= {r.code for r in rough[-pool_size:]}

    # 候选池的 stock_info 映射
    info_map = {s.get("code"): s for s in stocks}

    # 限流并发精算：最多 3 个并发（降低对腾讯接口的压力，避免触发WAF）
    sem = asyncio.Semaphore(3)

    async def precise_one(code):
        info = info_map.get(code)
        if not info:
            return None
        async with sem:
            try:
                # _precise_score_sync 内部是同步的 get_kline（网络IO），用 to_thread 放线程池
                # 这样 3 个并发能真正并行等待网络，而非阻塞事件循环
                result = await asyncio.to_thread(_precise_score_sync, info)
                # 每个请求完成后间隔 0.3s，避免突发流量触发WAF
                await asyncio.sleep(0.3)
                return result
            except Exception as e:
                print(f"精算失败 {code}: {e}")
                return None

    tasks = [precise_one(c) for c in candidate_codes]
    results = await asyncio.gather(*tasks)

    # ★ 只用精算结果构造返回列表，不再混入简化分——这是保证「列表分==详情分」的关键
    final = [
        SimpleNamespace(
            code=r["code"], name=r["name"],
            total_score=r["total_score"],
            signal=r["signal"], signal_level=r["signal_level"],
            factors_up=r.get("factors_up", []),
            buy_point=r.get("buy_point", {}),
        )
        for r in results if r
    ]
    final.sort(key=lambda r: r.total_score, reverse=True)

    # ── Top 5 单独计算买入时机 ──
    # 批量精算时跳过了 buy_point（避免100只并发拉K线触发WAF）
    # 排序确定后，只对 Top 5 单独拉K线计算买入时机，此时并发压力小，成功率高
    top_n_for_buy_point = 5
    for item in final[:top_n_for_buy_point]:
        try:
            bp = await asyncio.to_thread(_compute_buy_point_single, item.code)
            item.buy_point = bp
        except Exception as e:
            print(f"买入时机计算失败 {item.code}: {e}")

    return pick_indices(final)


@router.get("/batch-prices")
async def batch_prices(codes: str = Query(..., description="逗号分隔的股票代码")):
    """
    批量获取股票当前价格（用于前端快照/胜率回查）。
    返回：{code, name, price, change_pct} 列表。
    """
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return []
    stocks = get_stocks_batch(code_list)
    return [{
        "code": s.get("code", c),
        "name": s.get("name", ""),
        "price": s.get("price", 0),
        "change_pct": s.get("change_pct", 0),
    } for c, s in zip(code_list, stocks)]


@router.get("/backtest")
async def backtest(
    top_n: int = Query(default=10, ge=5, le=30),
    days: int = Query(default=60, ge=20, le=120),
    hold_periods: str = Query(default="1,3,5,10"),
):
    """
    历史回测：用过去 N 天的技术面评分模拟选股，计算持有 M 天后的收益。
    仅基于技术面（40%权重），不含基本面（历史数据不可得）。
    返回：各持有期的胜率、平均收益、总回测天数。
    """
    periods = [int(p) for p in hold_periods.split(",") if p.strip().isdigit()]
    if not periods:
        periods = [1, 3, 5, 10]

    # 取市值前 100 只股票作为回测池
    stocks = list(_cache.get("stocks", {}).values())
    if len(stocks) < 20:
        return {"error": "行情数据未就绪，请稍后再试"}
    stocks.sort(key=lambda s: s.get("market_cap", 0) or 0, reverse=True)
    pool = stocks[:100]

    # 并发拉取 K线（限制 3 并发 + 请求间隔，避免触发WAF）
    sem = asyncio.Semaphore(3)

    async def fetch_kline(code):
        async with sem:
            try:
                result = await asyncio.to_thread(get_kline, code, "day", 500)
                await asyncio.sleep(0.3)  # 请求间隔，降低WAF风险
                return result
            except Exception:
                return None

    klines_map = {}
    tasks = [fetch_kline(s["code"]) for s in pool]
    results = await asyncio.gather(*tasks)
    for s, kl in zip(pool, results):
        if kl and len(kl) >= 30:
            klines_map[s["code"]] = kl

    codes = list(klines_map.keys())
    if len(codes) < 10:
        return {"error": "有效K线数据不足，请稍后再试"}

    # 确定回测窗口
    min_len = min(len(klines_map[c]) for c in codes)
    bt_days = min(days, min_len - max(periods) - 35)  # 30 指标预热 + 前瞻天数
    if bt_days < 10:
        return {"error": "历史数据不足以完成回测"}
    start_idx = min_len - bt_days - max(periods)

    # 逐日回测
    period_stats = {p: {"wins": 0, "total_return": 0.0, "count": 0, "returns": []}
                    for p in periods}
    daily_records = []

    for day_offset in range(bt_days):
        idx = start_idx + day_offset
        day_scores = []

        for code in codes:
            kl = klines_map[code]
            if idx >= len(kl) - 1:
                continue
            hist = kl[:idx + 1]
            tech = _calc_technical(hist) if len(hist) >= 30 else hist
            if len(tech) < 30:
                continue
            # 仅技术面评分（回测无基本面数据）
            dim = engine._score_technical(tech)
            day_scores.append({"code": code, "score": dim.score})

        if len(day_scores) < top_n:
            continue

        day_scores.sort(key=lambda x: x["score"], reverse=True)
        top = day_scores[:top_n]

        # 计算各持有期收益
        day_result = {"day": day_offset, "stocks": []}
        for stock in top:
            code = stock["code"]
            kl = klines_map[code]
            buy_price = kl[idx]["close"]
            entry = {"code": code, "score": stock["score"], "price": buy_price}

            for p in periods:
                fwd_idx = idx + p
                if fwd_idx < len(kl):
                    sell_price = kl[fwd_idx]["close"]
                    ret = round((sell_price - buy_price) / buy_price * 100, 2)
                    entry[f"r{p}"] = ret
                    period_stats[p]["returns"].append(ret)
                    period_stats[p]["count"] += 1
                    period_stats[p]["total_return"] += ret
                    if ret > 0:
                        period_stats[p]["wins"] += 1

            day_result["stocks"].append(entry)
        daily_records.append(day_result)

    # 汇总统计
    summary = {}
    for p in periods:
        s = period_stats[p]
        if s["count"] > 0:
            summary[p] = {
                "win_rate": round(s["wins"] / s["count"] * 100),
                "avg_return": round(s["total_return"] / s["count"], 2),
                "total": s["count"],
            }
        else:
            summary[p] = {"win_rate": 0, "avg_return": 0, "total": 0}

    return {
        "summary": summary,
        "backtest_days": bt_days,
        "stock_pool_size": len(codes),
        "top_n": top_n,
        "periods": periods,
    }


@router.get("/{symbol}")
async def score_single(symbol: str):
    """
    ★ 单只股票综合评分（最完整的评分，前端"个股详情页"调用）。

    流程：
      1. 拉取实时行情 stock_info
      2. 拉 K线 → 算技术指标 technical_data
      3. 组装基本面 fundamental
      4. 喂给 engine.score_stock() 算综合分
      5. 返回完整评分结果（含三维度明细、加分扣分因素、摘要）
    """
    # 1. 实时行情
    stock_info = get_stock(symbol)
    if not stock_info:
        return {"error": f"未找到股票 {symbol}"}

    # 2. K线 + 技术指标
    technical_data = get_kline(symbol, period="day", count=500)
    # 数据足够时，复用技术指标计算逻辑（_calc_technical 在本文件底部）
    if len(technical_data) >= 30:
        technical_data = _calc_technical(technical_data)

    # 3. 基本面（从实时数据提取估值指标）
    fundamental = {
        "valuation": {
            "市盈率(动态)": stock_info.get("pe", 0),
            "市净率": stock_info.get("pb", 0),
            # market_cap 单位万元，÷10000 转亿元
            "总市值(亿)": round(stock_info.get("market_cap", 0) / 10000, 2),
            "流通市值(亿)": round(stock_info.get("float_cap", 0) / 10000, 2),
        },
        "financial": {
            "换手率": stock_info.get("turnover_rate", 0),
        },
    }

    # 4. 调用引擎算分
    result = engine.score_stock(
        code=symbol,
        name=stock_info.get("name", ""),
        technical_data=technical_data,
        stock_info=stock_info,
        fundamental=fundamental,
    )

    # 5. 返回（把 dataclass 字段拍平成 dict 给前端）
    return {
        "code": result.code,
        "name": result.name,
        "total_score": result.total_score,
        "signal": result.signal,
        "signal_level": result.signal_level,
        "dimensions": result.dimensions,
        "factors_up": result.factors_up,
        "factors_down": result.factors_down,
        "summary": result.summary,
        "buy_point": result.buy_point,
    }


@router.get("/batch/top")
async def score_top(
    limit: int = Query(default=50, ge=10, le=200),   # ge/le 限制取值范围 10~200
    background_tasks: BackgroundTasks = None,
):
    """
    评分最高的 N 只股票（用于"推荐榜单"）。

    两阶段策略：先简化评分排序几千只，再对候选池用完整算法精算，
    保证 Top N 的分数与详情页 /api/score/{symbol} 一致。
    """
    stocks = _cache.get("stocks", {})
    if not stocks:
        # 缓存未就绪 → 后台触发刷新，先返回 loading
        if background_tasks:
            from app.tencent import refresh_all_stocks
            background_tasks.add_task(refresh_all_stocks)
        return {"data": [], "total": 0, "cache_status": "loading"}

    stock_list = list(stocks.values())
    # 过滤掉停牌/异常（price<=0 或 change_pct 为 None）
    valid = [s for s in stock_list if s.get("price", 0) > 0 and s.get("change_pct") is not None]
    # 过滤亏损股（PE ≤ 0）：买入推荐榜不应包含无盈利能力的公司
    if EXCLUDE_LOSS_MAKING:
        valid = [s for s in valid if (s.get("pe", 0) or 0) > 0]

    top = await _batch_with_precise_top(
        valid, lambda results: results[:limit], limit=limit, side="top",
    )

    return {
        "data": [{
            "code": r.code,
            "name": r.name,
            "total_score": r.total_score,
            "signal": r.signal,
            "signal_level": r.signal_level,
            # Top 50 专属：买入原因（加分因素标签），其他批量接口不返回此字段
            "factors_up": getattr(r, 'factors_up', []) or [],
            # 买入时机指标
            "buy_point": getattr(r, 'buy_point', {}) or {},
        } for r in top],
        "total": len(valid),
        "cache_status": "ready",
    }


@router.get("/batch/bottom")
async def score_bottom(
    limit: int = Query(default=50, ge=10, le=200),
):
    """评分最低的 N 只（适合做空/回避）。候选池用完整算法精算，分数与详情页一致。"""
    stocks = _cache.get("stocks", {})
    if not stocks:
        return {"data": [], "total": 0, "cache_status": "loading"}

    stock_list = [s for s in stocks.values() if s.get("price", 0) > 0]
    bottom = await _batch_with_precise_top(
        stock_list, lambda results: results[-limit:][::-1], limit=limit, side="bottom",
    )

    return {
        "data": [{
            "code": r.code,
            "name": r.name,
            "total_score": r.total_score,
            "signal": r.signal,
            "signal_level": r.signal_level,
        } for r in bottom],
        "total": len(stock_list),
        "cache_status": "ready",
    }


@router.get("/batch/signal")
async def score_by_signal(
    # description 会显示在 /docs 接口文档里
    signal: str = Query(default="买入", description="信号类型：强烈买入/买入/观望/卖出/强烈卖出"),
    limit: int = Query(default=50, ge=10, le=200),
):
    """
    按信号类型筛选股票（如只看"强烈买入"的股票）。

    注意：信号依赖完整评分，但全量精算太慢。这里对候选池（top+bottom 各 limit+margin 只）
    精算后筛选；若目标信号集中在中间分段，可能漏掉部分，因此 total 取精算池内匹配数。
    返回结果全部为精算分，与详情页 /api/score/{symbol} 一致。
    """
    stocks = _cache.get("stocks", {})
    if not stocks:
        return {"data": [], "total": 0, "signal": signal}

    stock_list = [s for s in stocks.values() if s.get("price", 0) > 0]
    all_precise = await _batch_with_precise_top(
        stock_list, lambda results: results, limit=limit, side="both",
    )
    filtered = [r for r in all_precise if r.signal == signal][:limit]

    return {
        "data": [{
            "code": r.code,
            "name": r.name,
            "total_score": r.total_score,
            "signal": r.signal,
            "signal_level": r.signal_level,
        } for r in filtered],
        "total": len([r for r in all_precise if r.signal == signal]),   # 该信号的总数
        "signal": signal,
    }


# ================================================================
#  内部技术指标计算（复用 stock.py 的逻辑）
# ================================================================
# 注意：这段代码和 stock.py 的 /technical/ 端点逻辑几乎完全一致。
# 理想做法是抽成公共函数共享，但这里为了"减少跨模块依赖"复制了一份。
# 详细的指标公式解释请参考 stock.py 的注释。

def _calc_technical(klines: list) -> list:
    """从K线计算技术指标（与 stock.py 的 /technical/ 端点逻辑一致）"""
    if len(klines) < 30:
        return klines

    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]
    n = len(closes)

    def ma(data, window):
        result = [None] * (window - 1)
        for i in range(window - 1, len(data)):
            result.append(round(sum(data[i - window + 1:i + 1]) / window, 3))
        return result

    def ema(data, span):
        result = [data[0]]
        k = 2 / (span + 1)
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    ma5 = ma(closes, 5)
    ma10 = ma(closes, 10)
    ma20 = ma(closes, 20)
    ma60 = ma(closes, 60)

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    dif = [round(ema12[i] - ema26[i], 4) for i in range(n)]
    dea_raw = ema(dif, 9)
    dea = [round(v, 4) for v in dea_raw]
    macd_hist = [round((dif[i] - dea[i]) * 2, 4) for i in range(n)]

    delta = [closes[i] - closes[i - 1] for i in range(1, n)]
    rsi_vals = [None] * n
    for i in range(14, n):
        gains = [d for d in delta[i - 13:i + 1] if d > 0]
        losses = [-d for d in delta[i - 13:i + 1] if d < 0]
        avg_gain = sum(gains) / 14
        avg_loss = sum(losses) / 14
        rsi_vals[i] = round(100 - 100 / (1 + avg_gain / avg_loss), 2) if avg_loss > 0 else 100.0

    k_list, d_list = [None] * n, [None] * n
    k_list[18], d_list[18] = 50.0, 50.0
    for i in range(19, n):
        low9 = min(lows[i - 8:i + 1])
        high9 = max(highs[i - 8:i + 1])
        rsv = (closes[i] - low9) / (high9 - low9) * 100 if high9 != low9 else 50
        k_list[i] = round(2 / 3 * (k_list[i - 1] or 50) + 1 / 3 * rsv, 2)
        d_list[i] = round(2 / 3 * (d_list[i - 1] or 50) + 1 / 3 * k_list[i], 2)
    j_list = [round(3 * (k_list[i] or 50) - 2 * (d_list[i] or 50), 2) for i in range(n)]

    boll_mid_raw = ma(closes, 20)
    boll_mid = [v if v is not None else closes[i] for i, v in enumerate(boll_mid_raw)]
    boll_upper, boll_lower = [], []
    for i in range(n):
        if i >= 19:
            std = (sum((closes[j] - boll_mid[i]) ** 2 for j in range(i - 19, i + 1)) / 20) ** 0.5
            boll_upper.append(round(boll_mid[i] + 2 * std, 3))
            boll_lower.append(round(boll_mid[i] - 2 * std, 3))
        else:
            boll_upper.append(None)
            boll_lower.append(None)

    result = []
    for i in range(n):
        result.append({
            "date": klines[i]["date"],
            "close": closes[i],
            "open": klines[i]["open"],
            "high": highs[i],
            "low": lows[i],
            "volume": volumes[i],
            "ma5": ma5[i], "ma10": ma10[i], "ma20": ma20[i], "ma60": ma60[i],
            "dif": dif[i], "dea": dea[i], "macd": macd_hist[i],
            "rsi": rsi_vals[i],
            "k": k_list[i], "d": d_list[i], "j": j_list[i],
            "boll_upper": boll_upper[i], "boll_mid": boll_mid[i], "boll_lower": boll_lower[i],
        })
    return result
