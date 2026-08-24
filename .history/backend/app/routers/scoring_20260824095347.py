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

from fastapi import APIRouter, Query, BackgroundTasks, Body
import asyncio
from types import SimpleNamespace
from typing import Optional
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

# ── 股票池质量门槛（与前端/数据包生成脚本一致）──
# 流通市值 > 50 亿、股价 > 3 元（成交额门槛已移除：盘中早盘时段当日成交额未累积到位，会误杀大量股票）
MIN_FLOAT_CAP = 50 * 10000   # 单位：万元（50 亿）
MIN_PRICE = 3.0              # 单位：元


def _pool_quality_filter(stock: dict) -> bool:
    """质量门槛过滤：流通市值/股价"""
    if (stock.get("float_cap", 0) or 0) < MIN_FLOAT_CAP:
        return False
    if (stock.get("price", 0) or 0) < MIN_PRICE:
        return False
    return True


def _compute_top5_extras(code: str) -> dict:
    """
    为 Top 5 股票单独计算买入时机 + 趋势健康度。
    批量精算时跳过了这两项（避免100只并发拉K线触发WAF）。
    排序确定后单独拉K线计算，此时并发压力小，成功率高。
    如果 K 线已在缓存中（批量精算时拉过），直接复用，无需重新请求。
    """
    # ★★★ 优先读预计算指标数组（零网络 + 零指标计算）
    from app.scoring.indicator_cache import get_cached_technical
    tech = get_cached_technical(code)
    
    if tech is None:
        # 回退到 K 线缓存 + numpy 计算
        from app.scoring.kline_cache import get_cached_klines
        klines = get_cached_klines(code)
        if not klines:
            klines = get_kline(code, period="day", count=500)
        if len(klines) >= 30:
            tech = _calc_technical_fast(klines)
    
    if tech and len(tech) >= 30:
        return {
            "buy_point": engine._calc_buy_point(tech),
            "trend_health": engine._calc_trend_health(tech),
        }
    return {"buy_point": {}, "trend_health": {}}


def _precise_score_sync(stock_info: dict, preloaded: Optional[list] = None) -> dict:
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
    
    # ★★★ 终极快速路径：优先用预加载的指标数组（批量读入，零 DB 往返）
    technical_data = preloaded
    
    if technical_data is None:
        # 回退：逐只读预计算指标缓存
        from app.scoring.indicator_cache import get_cached_technical
        technical_data = get_cached_technical(code)
    
    if technical_data is None:
        # 指标缓存未命中，回退到 K 线缓存 + numpy 计算
        from app.scoring.kline_cache import get_cached_klines
        klines = get_cached_klines(code)
        if not klines:
            klines = get_kline(code, period="day", count=500)
        if len(klines) >= 30:
            technical_data = _calc_technical_fast(klines)
        else:
            technical_data = []
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
        "change_pct": stock_info.get("change_pct", 0),
        # 买入原因（加分因素），供 Top 50 列表展示。最多 5 个，如「均线多头排列」「量价齐升」
        "factors_up": result.factors_up,
        # 批量模式跳过买入时机计算（避免100只股票同时拉K线触发WAF）
        # Top 5 的 buy_point 会在排序后单独计算
        "buy_point": {},
        # 趋势健康度（5维度诊断：洗盘 vs 真跌）
        "trend_health": result.trend_health,
        # 各维度得分（用于权重优化分析）
        "dimensions": {d["name"]: d["score"] for d in result.dimensions},
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

    # ★★★ 批量预加载指标缓存：一条 SQL 读取全部候选股（1 次 DB 往返，而非 N 次）
    from app.scoring.indicator_cache import get_cached_technical_batch_sql
    try:
        preloaded_map = await asyncio.to_thread(
            get_cached_technical_batch_sql, list(candidate_codes)
        )
    except Exception as e:
        print(f"指标批量预加载失败: {e}")
        preloaded_map = {}

    # 限流并发精算：最多 3 个并发（降低对腾讯接口的压力，避免触发WAF）
    # 如果K线数据库缓存已启用，可以提高并发数并跳过延迟
    from app.scoring.kline_cache import get_cache_status
    cache_status = get_cache_status()
    use_db_cache = cache_status.get("total_cached", 0) > 50 or len(preloaded_map) > len(candidate_codes) // 2
    
    sem = asyncio.Semaphore(10 if use_db_cache else 3)  # DB缓存时提高并发

    async def precise_one(code):
        info = info_map.get(code)
        if not info:
            return None
        async with sem:
            try:
                # 传入预加载的指标数组（命中则零 DB 往返）
                result = await asyncio.to_thread(
                    _precise_score_sync, info, preloaded_map.get(code)
                )
                # 只有使用腾讯API时才需要延迟（DB缓存无需延迟）
                if not use_db_cache:
                    await asyncio.sleep(0.3)  # 避免突发流量触发WAF
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
            change_pct=r.get("change_pct", 0),
            factors_up=r.get("factors_up", []),
            buy_point=r.get("buy_point", {}),
            trend_health=r.get("trend_health", {}),
            dimensions=r.get("dimensions", {}),
        )
        for r in results if r
    ]
    final.sort(key=lambda r: r.total_score, reverse=True)

    # ── Top 5 单独计算买入时机 + 趋势健康度 ──
    # 批量精算时跳过了这两项（避免100只并发拉K线触发WAF）
    # 排序确定后，只对 Top 5 单独拉K线计算，此时并发压力小，成功率高
    top_n_for_extras = 5
    for item in final[:top_n_for_extras]:
        try:
            extras = await asyncio.to_thread(_compute_top5_extras, item.code)
            item.buy_point = extras["buy_point"]
            # 仅当批量精算时 trend_health 为空才覆盖（避免重复计算）
            if not getattr(item, 'trend_health', None):
                item.trend_health = extras["trend_health"]
        except Exception as e:
            print(f"Top5 extras 计算失败 {item.code}: {e}")

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


@router.post("/weight-advice")
async def weight_advice(data: dict):
    """
    权重优化分析：根据历史快照+实际收益，分析各维度预测力，建议权重调整。
    前端传入已验证的快照数据（含维度分+实际收益）。
    """
    snapshots = data.get("snapshots", [])
    if len(snapshots) < 3:
        return {"error": "快照数据不足，至少需要 3 天快照才能分析（当前 %d 天）" % len(snapshots)}

    # 收集所有已验证的记录
    records = []
    for snap in snapshots:
        for s in snap.get("stocks", []):
            if s.get("returnPct") is not None and s.get("dimensions"):
                records.append(s)

    if len(records) < 20:
        return {"error": "已验证记录不足（需要至少 20 条，当前 %d 条）。继续积累快照并点击「查询当前收益」验证。" % len(records)}

    # ── 1. 按信号等级统计胜率 ──
    signal_stats = {}
    for r in records:
        sig = r.get("signal", "观望")
        if sig not in signal_stats:
            signal_stats[sig] = {"wins": 0, "total": 0, "sum_return": 0}
        signal_stats[sig]["total"] += 1
        signal_stats[sig]["sum_return"] += r["returnPct"]
        if r["returnPct"] > 0:
            signal_stats[sig]["wins"] += 1

    signal_analysis = {}
    for sig, s in signal_stats.items():
        if s["total"] > 0:
            signal_analysis[sig] = {
                "count": s["total"],
                "win_rate": round(s["wins"] / s["total"] * 100),
                "avg_return": round(s["sum_return"] / s["total"], 2),
            }

    # ── 2. 按维度分析预测力（维度分与实际收益的相关性）──
    dim_names = ["技术面", "资金面", "基本面"]
    dim_correlation = {}
    for dim in dim_names:
        pairs = []
        for r in records:
            dim_score = r.get("dimensions", {}).get(dim)
            ret = r.get("returnPct")
            if dim_score is not None and ret is not None:
                pairs.append((dim_score, ret))

        if len(pairs) >= 10:
            # 计算皮尔逊相关系数
            n = len(pairs)
            sum_x = sum(p[0] for p in pairs)
            sum_y = sum(p[1] for p in pairs)
            sum_xy = sum(p[0] * p[1] for p in pairs)
            sum_x2 = sum(p[0] ** 2 for p in pairs)
            sum_y2 = sum(p[1] ** 2 for p in pairs)
            denom = math.sqrt((n * sum_x2 - sum_x ** 2) * (n * sum_y2 - sum_y ** 2))
            corr = (n * sum_xy - sum_x * sum_y) / denom if denom > 0 else 0
            dim_correlation[dim] = round(corr, 3)
        else:
            dim_correlation[dim] = None

    # ── 3. 建议权重 ──
    current_weights = {"技术面": 0.40, "资金面": 0.25, "基本面": 0.35}
    suggested_weights = dict(current_weights)  # 默认不变

    # 如果有足够相关性数据，按相关性比例调整
    valid_corrs = {k: v for k, v in dim_correlation.items() if v is not None}
    if len(valid_corrs) >= 2:
        # 将相关性转为正数（取绝对值 + 0.1 保底）
        abs_corrs = {k: abs(v) + 0.1 for k, v in valid_corrs.items()}
        total_corr = sum(abs_corrs.values())
        if total_corr > 0:
            # 按相关性比例分配权重，但与当前权重做加权平均（避免样本少时剧变）
            confidence = min(len(records) / 100, 1.0)  # 样本越多越敢调
            optimal = {k: v / total_corr for k, v in abs_corrs.items()}
            for dim in optimal:
                # 新旧加权平均：70% 当前 + 30% 最优（受置信度调节）
                blended = current_weights.get(dim, 0.33) * (1 - 0.3 * confidence) + optimal[dim] * (0.3 * confidence)
                suggested_weights[dim] = round(blended, 2)
            # 归一化确保总和 = 1
            total_w = sum(suggested_weights.values())
            if total_w > 0:
                suggested_weights = {k: round(v / total_w, 2) for k, v in suggested_weights.items()}

    # ── 4. 生成建议文字 ──
    advice = []
    for dim in dim_names:
        cur = current_weights[dim]
        sug = suggested_weights.get(dim, cur)
        corr = dim_correlation.get(dim)
        if corr is not None and abs(sug - cur) >= 0.03:
            direction = "↑" if sug > cur else "↓"
            advice.append(f"{dim}：当前 {cur:.0%} → 建议 {sug:.0%} {direction}（预测力相关系数 {corr:+.3f}）")
        elif corr is not None:
            advice.append(f"{dim}：当前 {cur:.0%} 合理（相关系数 {corr:+.3f}）")
        else:
            advice.append(f"{dim}：数据不足，暂无法分析")

    return {
        "signal_analysis": signal_analysis,
        "dim_correlation": dim_correlation,
        "current_weights": current_weights,
        "suggested_weights": suggested_weights,
        "advice": advice,
        "sample_size": len(records),
        "snapshot_count": len(snapshots),
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
        "trend_health": result.trend_health,
    }


def _record_ranking(result_data: list):
    """后台记录当日排行数据（用于计算连续上榜天数）"""
    try:
        from app.scoring.ranking_history import record_daily_ranking
        stocks = [
            {"code": r["code"], "name": r["name"], "total_score": r["total_score"], 
             "signal": r["signal"], "rank": i + 1}
            for i, r in enumerate(result_data)
        ]
        record_daily_ranking(stocks)
    except Exception as e:
        print(f"[ranking] 后台记录排行失败: {e}")


# ★ 排行榜结果短期缓存（防重复计算：多次访问直接返回缓存）
_rank_result_cache = {
    "top": {"data": None, "ts": 0},
    "computing": False,
}
_rank_cache_lock = asyncio.Lock()
_RANK_CACHE_TTL = 180  # 缓存有效期 3 分钟


@router.get("/batch/top")
async def score_top(
    limit: int = Query(default=50, ge=10, le=200),   # ge/le 限制取值范围 10~200
    background_tasks: BackgroundTasks = None,
):
    """
    评分最高的 N 只股票（用于“推荐榜单”）。

    两阶段策略：先简化评分排序几千只，再对候选池用完整算法精算，
    保证 Top N 的分数与详情页 /api/score/{symbol} 一致。
    
    性能优化：结果短期缓存 3 分钟，多次访问直接返回缓存，避免重复精算。
    """
    stocks = _cache.get("stocks", {})
    if not stocks:
        # 缓存未就绪 → 后台触发刷新，先返回 loading
        if background_tasks:
            from app.tencent import refresh_all_stocks
            background_tasks.add_task(refresh_all_stocks)
        return {"data": [], "total": 0, "cache_status": "loading"}
    
    # ★★★ 结果短期缓存：3 分钟内直接返回，避免重复精算
    import time as _time
    entry = _rank_result_cache["top"]
    if entry["data"] is not None and _time.time() - entry["ts"] < _RANK_CACHE_TTL:
        cached_data = entry["data"]
        # 若请求的 limit 不同，截取前 limit 只
        sliced = cached_data[:limit]
        return {
            "data": sliced,
            "total": entry.get("total", len(cached_data)),
            "cache_status": "ready",
            "cached": True,
            "cache_age_seconds": int(_time.time() - entry["ts"]),
        }
    
    # 防并发重复计算：若已在计算中，直接返回旧缓存（即使过期）
    async with _rank_cache_lock:
        if _rank_result_cache["computing"]:
            if entry["data"]:
                return {"data": entry["data"][:limit], "total": entry.get("total", 0),
                        "cache_status": "ready", "cached": True, "stale": True}
            return {"data": [], "total": 0, "cache_status": "computing"}
        _rank_result_cache["computing"] = True
    
    try:
        stock_list = list(stocks.values())
        # 过滤掉停牌/异常（price<=0 或 change_pct 为 None）
        valid = [s for s in stock_list if s.get("price", 0) > 0 and s.get("change_pct") is not None]
        # 过滤亏损股（PE ≤ 0）：买入推荐榜不应包含无盈利能力的公司
        if EXCLUDE_LOSS_MAKING:
            valid = [s for s in valid if (s.get("pe", 0) or 0) > 0]
        # 质量门槛：流通市值 > 50 亿、股价 > 3 元
        valid = [s for s in valid if _pool_quality_filter(s)]

        top = await _batch_with_precise_top(
            valid, lambda results: results[:limit], limit=limit, side="top",
        )

        result_data = [{
            "code": r.code,
            "name": r.name,
            "total_score": r.total_score,
            "signal": r.signal,
            "signal_level": r.signal_level,
            "change_pct": getattr(r, 'change_pct', 0) or 0,
            # Top 50 专属：买入原因（加分因素标签），其他批量接口不返回此字段
            "factors_up": getattr(r, 'factors_up', []) or [],
            # 买入时机指标
            "buy_point": getattr(r, 'buy_point', {}) or {},
            # 各维度得分（用于权重优化分析）
            "dimensions": getattr(r, 'dimensions', {}) or {},
        } for r in top]

        # ★ 写入短期缓存
        _rank_result_cache["top"] = {
            "data": result_data,
            "ts": _time.time(),
            "total": len(valid),
        }

        # 后台记录当日排行（用于计算连续上榜天数）
        if background_tasks:
            background_tasks.add_task(_record_ranking, result_data)

        return {
            "data": result_data,
            "total": len(valid),
            "cache_status": "ready",
        }
    finally:
        _rank_result_cache["computing"] = False


@router.get("/batch/bottom")
async def score_bottom(
    limit: int = Query(default=50, ge=10, le=200),
):
    """
    评分最低的 N 只（适合做空/回避）。
    
    优化：使用简化评分（不精算 K 线指标），速度提升 10 倍+。
    倒数股票用户不关心精确分数，只需大致排序即可。
    """
    stocks = _cache.get("stocks", {})
    if not stocks:
        return {"data": [], "total": 0, "cache_status": "loading"}

    stock_list = [s for s in stocks.values() if s.get("price", 0) > 0]
    # 质量门槛：流通市值 > 50 亿、股价 > 3 元（与推荐榜一致）
    stock_list = [s for s in stock_list if _pool_quality_filter(s)]
    
    # ★ 使用简化评分（不拉 K 线，只用动量+换手+PE 快速排序）
    rough = engine.score_batch(stock_list)
    rough.sort(key=lambda r: r.total_score)
    
    # 取倒数 limit 只，反转顺序（最差的在前面）
    bottom = rough[:limit][::-1]

    return {
        "data": [{
            "code": r.code,
            "name": r.name,
            "total_score": r.total_score,
            "signal": r.signal,
            "signal_level": r.signal_level,
            "change_pct": getattr(r, 'change_pct', 0) or 0,
        } for r in bottom],
        "total": len(stock_list),
        "cache_status": "ready",
        "note": "简化评分（未精算 K 线指标）",
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
            "change_pct": getattr(r, 'change_pct', 0) or 0,
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
        # 窗口 = delta[i-14 : i]，即以当日变化结尾的 14 个变化量（与 _calc_technical_fast / 前端一致）
        gains = [d for d in delta[i - 14:i] if d > 0]
        losses = [-d for d in delta[i - 14:i] if d < 0]
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


def _calc_technical_fast(klines: list) -> list:
    """
    Numpy 向量化版本，速度提升 50-100 倍。
    
    优化点：
      - MA: cumsum 差分，O(n) C 速度
      - EMA: 仍需循环但用 numpy 数组原地操作
      - RSI: 向量化 diff + where
      - KDJ: 滑动窗口用 numpy 的 stride_tricks
      - BOLL: 向量化 std
    """
    import numpy as np
    
    n = len(klines)
    if n < 30:
        return klines
    
    # 一次性转 numpy 数组（O(n) 但 C 速度）
    closes = np.array([k["close"] for k in klines], dtype=np.float64)
    highs = np.array([k["high"] for k in klines], dtype=np.float64)
    lows = np.array([k["low"] for k in klines], dtype=np.float64)
    volumes = np.array([k["volume"] for k in klines], dtype=np.float64)
    
    # ── MA：cumsum 差分，O(n) C 速度 ──
    def np_ma(data, window):
        cumsum = np.cumsum(np.insert(data, 0, 0))
        ma_vals = (cumsum[window:] - cumsum[:-window]) / window
        # 前面补 NaN 对齐原数组长度
        return np.concatenate([np.full(window - 1, np.nan), ma_vals])
    
    ma5 = np_ma(closes, 5)
    ma10 = np_ma(closes, 10)
    ma20 = np_ma(closes, 20)
    ma60 = np_ma(closes, 60)
    
    # ── EMA：向量化递推（仍需循环但用 numpy 数组） ──
    def np_ema(data, span):
        alpha = 2.0 / (span + 1)
        ema = np.empty_like(data)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = data[i] * alpha + ema[i-1] * (1 - alpha)
        return ema
    
    ema12 = np_ema(closes, 12)
    ema26 = np_ema(closes, 26)
    dif = ema12 - ema26
    dea = np_ema(dif, 9)
    macd_hist = (dif - dea) * 2
    
    # ── RSI：向量化 ──
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    # 用 convolve 计算滑动平均
    rsi_vals = np.full(n, np.nan)
    if len(gains) >= 14:
        kernel = np.ones(14) / 14.0
        avg_gains = np.convolve(gains, kernel, mode='valid')
        avg_losses = np.convolve(losses, kernel, mode='valid')
        # 避免除零：当 avg_losses 为 0 时，RSI 设为 100
        with np.errstate(divide='ignore', invalid='ignore'):
            rs = np.where(avg_losses > 0, avg_gains / avg_losses, 100.0)
        rsi_vals[14:] = 100.0 - 100.0 / (1.0 + rs)
    
    # ── KDJ：滑动窗口 + 递推 ──
    k_vals = np.full(n, np.nan)
    d_vals = np.full(n, np.nan)
    k_vals[18] = 50.0
    d_vals[18] = 50.0
    
    # 预计算 9 日滑动窗口的高低点
    for i in range(19, n):
        low9 = np.min(lows[i-8:i+1])
        high9 = np.max(highs[i-8:i+1])
        rsv = (closes[i] - low9) / (high9 - low9) * 100.0 if high9 != low9 else 50.0
        k_vals[i] = 2.0/3.0 * k_vals[i-1] + 1.0/3.0 * rsv
        d_vals[i] = 2.0/3.0 * d_vals[i-1] + 1.0/3.0 * k_vals[i]
    j_vals = 3.0 * k_vals - 2.0 * d_vals
    
    # ── BOLL：向量化 std ──
    boll_mid = ma20.copy()
    boll_upper = np.full(n, np.nan)
    boll_lower = np.full(n, np.nan)
    
    # 用 stride_tricks 创建滑动窗口视图
    if n >= 20:
        window_shape = (n - 19, 20)
        strides = (closes.strides[0], closes.strides[0])
        windows = np.lib.stride_tricks.as_strided(closes, shape=window_shape, strides=strides)
        stds = np.std(windows, axis=1)
        boll_upper[19:] = boll_mid[19:] + 2 * stds
        boll_lower[19:] = boll_mid[19:] - 2 * stds
    
    # ── 组装结果 ──
    # 将 NaN 转为 None（与原实现兼容）
    def to_py_list(arr):
        return [None if np.isnan(v) else round(float(v), 4) for v in arr]
    
    result = []
    ma5_list = to_py_list(ma5)
    ma10_list = to_py_list(ma10)
    ma20_list = to_py_list(ma20)
    ma60_list = to_py_list(ma60)
    dif_list = to_py_list(dif)
    dea_list = to_py_list(dea)
    macd_list = to_py_list(macd_hist)
    rsi_list = to_py_list(rsi_vals)
    k_list = to_py_list(k_vals)
    d_list = to_py_list(d_vals)
    j_list = to_py_list(j_vals)
    boll_upper_list = to_py_list(boll_upper)
    boll_mid_list = to_py_list(boll_mid)
    boll_lower_list = to_py_list(boll_lower)
    
    for i in range(n):
        result.append({
            "date": klines[i]["date"],
            "close": closes[i],
            "open": klines[i]["open"],
            "high": highs[i],
            "low": lows[i],
            "volume": volumes[i],
            "ma5": ma5_list[i], "ma10": ma10_list[i], "ma20": ma20_list[i], "ma60": ma60_list[i],
            "dif": dif_list[i], "dea": dea_list[i], "macd": macd_list[i],
            "rsi": rsi_list[i],
            "k": k_list[i], "d": d_list[i], "j": j_list[i],
            "boll_upper": boll_upper_list[i], "boll_mid": boll_mid_list[i], "boll_lower": boll_lower_list[i],
        })
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 排行榜可信度（连续上榜天数）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.post("/ranking-persistence")
async def ranking_persistence(codes: list = Body(...)):
    """
    查询多只股票的排行榜连续上榜天数 + 可信度。
    
    请求体：["000001", "600519", ...]
    返回：[{code, consecutive_days, trust_score, trust_grade, advice}, ...]
    """
    from app.scoring.ranking_history import get_ranking_persistence
    result = get_ranking_persistence(codes)
    return {"data": result}


@router.post("/ranking-record")
async def ranking_record(stocks: list = Body(...)):
    """
    手动记录当日排行榜（供调试或定时任务调用）。
    
    请求体：[{code, name, total_score, signal, rank}, ...]
    """
    from app.scoring.ranking_history import record_daily_ranking
    count = record_daily_ranking(stocks)
    return {"recorded": count}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# K线数据库缓存
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/kline-cache/status")
async def kline_cache_status():
    """
    获取K线数据库缓存状态。
    
    返回：缓存股票数、最新/最旧更新时间、过期数量等。
    """
    from app.scoring.kline_cache import get_cache_status
    return {"data": get_cache_status()}


@router.post("/kline-cache/refresh")
async def kline_cache_refresh(background_tasks: BackgroundTasks):
    """
    触发K线缓存刷新（后台执行）。
    
    从腾讯API拉取市值前200只股票的K线数据，存入数据库。
    每天盘后调用一次即可，后续排行榜直接从数据库读取。
    """
    background_tasks.add_task(_do_refresh_kline_cache)
    return {"message": "K线缓存刷新已启动，请稍后查询状态"}


def _do_refresh_kline_cache():
    """后台执行 K 线缓存刷新"""
    try:
        from app.scoring.kline_cache import refresh_kline_cache
        result = refresh_kline_cache()
        print(f"[kline_cache] 后台刷新完成: {result}")
    except Exception as e:
        print(f"[kline_cache] 后台刷新失败: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 指标层缓存（预计算技术指标）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/indicator-cache/status")
async def indicator_cache_status():
    """
    获取指标数据库缓存状态。
    
    返回：缓存股票数、最新/最旧更新时间、过期数量等。
    """
    from app.scoring.indicator_cache import get_indicator_cache_status
    return {"data": get_indicator_cache_status()}


@router.post("/indicator-cache/refresh")
async def indicator_cache_refresh(background_tasks: BackgroundTasks):
    """
    触发指标缓存刷新（后台执行）。
    
    从 K 线缓存计算技术指标（MA/MACD/RSI/KDJ/BOLL），存入数据库。
    每天盘后调用一次即可，评分时直接从数据库读取预计算指标。
    """
    background_tasks.add_task(_do_refresh_indicator_cache)
    return {"message": "指标缓存刷新已启动，请稍后查询状态"}


def _do_refresh_indicator_cache():
    """后台执行指标缓存刷新"""
    try:
        from app.scoring.indicator_cache import refresh_indicator_cache
        result = refresh_indicator_cache()
        print(f"[indicator_cache] 后台刷新完成: {result}")
    except Exception as e:
        print(f"[indicator_cache] 后台刷新失败: {e}")


@router.post("/indicator-cache/incremental")
async def indicator_incremental_update(data: dict = Body(...)):
    """
    增量更新指标（盘中只拉最新价，无需重算 500 根 K 线）。
    
    请求体：{code, price, high?, low?}
    返回：更新后的指标值
    
    用于盘中实时更新指标，速度比全量计算快 100 倍+。
    """
    code = data.get("code", "")
    price = data.get("price", 0)
    high = data.get("high")
    low = data.get("low")
    
    if not code or not price:
        return {"error": "missing code or price"}
    
    from app.scoring.indicator_cache import incremental_update, save_incremental_update
    
    # 增量更新
    indicators = incremental_update(code, price, high, low)
    if not indicators:
        return {"error": "no cached indicators found, need full refresh first"}
    
    # 保存到数据库
    save_incremental_update(code, indicators)
    
    # 返回指标值（去掉内部状态）
    result = {k: v for k, v in indicators.items() if k != "_state"}
    return {"data": result}
