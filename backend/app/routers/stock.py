"""
================================================================================
【文件作用】个股数据路由（K线 / 实时行情 / 搜索 / 技术指标 / 基本面）
================================================================================

注册到 main.py 后，URL 前缀 /api/stock：
  GET /api/stock/kline/{symbol}          → 个股 K线
  GET /api/stock/realtime/{symbol}       → 个股实时行情
  GET /api/stock/search?keyword=xxx      → 股票搜索
  GET /api/stock/technical/{symbol}      → 技术指标（MA/MACD/KDJ/RSI/BOLL）★核心
  GET /api/stock/fundamental/{symbol}    → 基本面（PE/PB/市值）
  GET /api/stock/news/{symbol}           → 消息面（新闻情绪分 + 相关快讯）

★ 重点：/technical 接口计算了 5 大经典技术指标，这些指标的数学原理见下方注释。
       计算结果会被评分引擎 engine.py 消费。
================================================================================
"""

from fastapi import APIRouter, Query, Body
from datetime import datetime
import time
import numpy as np
from app.tencent import get_stock, get_kline, search_stocks, _CODE_TO_PREFIX, _cache

router = APIRouter()


# ★ 2026-09-06：以下接口全部是同步阻塞调用（腾讯 HTTP / numpy 重算），
#   原来声明成 async def 会直接占住事件循环——一个腾讯慢请求（实测可 90s）
#   把整个进程卡死，其它并发请求（含 preflight）全部 502，前端表现为
#   "CORS blocked"。改成普通 def，FastAPI 自动放线程池执行，事件循环不再被卡。
@router.get("/kline/{symbol}")
def stock_kline(symbol: str, period: str = "day"):
    """个股 K线。symbol=股票代码，period=day/week/month"""
    return get_kline(symbol, period=period)


@router.get("/realtime/{symbol}")
def stock_realtime(symbol: str):
    """个股实时行情"""
    return get_stock(symbol)


@router.get("/search")
def stock_search(keyword: str = Query(default="")):
    """
    股票搜索。
    Query(default="") 表示这是 URL 查询参数（?keyword=平安），默认空字符串。
    """
    if not keyword:
        return []
    return search_stocks(keyword)


@router.get("/technical/{symbol}")
def stock_technical(symbol: str, period: str = "day"):
    """
    ★ 技术指标计算接口：MA / EMA / MACD / RSI / KDJ / BOLL

    输入：拉取最近 500 根 K线
    输出：每天的 K线 + 各种技术指标值（数组形式，前端可直接画图/评分）

    下面会逐段解释每个指标的数学公式。
    （注：这些公式是金融领域的标准算法，前端工程师不用背，理解"输入→输出"即可）
    """
    klines = get_kline(symbol, period=period, count=500)
    # 数据太少算不出指标（至少要 30 根才能算 MA20/RSI14 等）
    if len(klines) < 30:
        return []

    # 把 K线数组的各字段拆成独立列表（方便按列计算）
    # 列表推导式：类比 JS 的 arr.map(k => k.close)
    closes = [k["close"] for k in klines]
    highs = [k["high"] for k in klines]
    lows = [k["low"] for k in klines]
    volumes = [k["volume"] for k in klines]
    dates = [k["date"] for k in klines]
    n = len(closes)

    # ──────────────────────────────────────────────
    # MA（简单移动平均线）
    # 公式：MA(N) = 最近 N 天收盘价的算术平均
    # 用途：判断趋势方向。价格在 MA 上方=强势
    # ──────────────────────────────────────────────
    def ma(data, window):
        """
        计算 N 日均线。
        返回与 data 等长的列表，前 window-1 个为 None（数据不够算不出来）。
        """
        result = [None] * (window - 1)   # 前 N-1 天没有足够数据
        for i in range(window - 1, len(data)):
            # data[i-window+1 : i+1] 取最近 window 个值，求平均
            result.append(round(sum(data[i - window + 1:i + 1]) / window, 3))
        return result

    # 4 条常用均线
    ma5 = ma(closes, 5)    # 5 日均线（短期）
    ma10 = ma(closes, 10)  # 10 日均线
    ma20 = ma(closes, 20)  # 20 日均线（中期）
    ma60 = ma(closes, 60)  # 60 日均线（长期，季线）

    # ──────────────────────────────────────────────
    # EMA（指数移动平均线）
    # 公式：EMA(今日) = 今日价×k + EMA(昨日)×(1-k)，k = 2/(N+1)
    # 与 MA 的区别：EMA 给最近的数据更大权重，反应更灵敏
    # ──────────────────────────────────────────────
    def ema(data, span):
        """
        计算 EMA。第一个值用 data[0] 作为初始值。
        """
        result = [data[0]]
        k = 2 / (span + 1)   # 平滑系数
        for i in range(1, len(data)):
            # 递推公式：今日 EMA = 今日价×k + 昨日EMA×(1-k)
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    # ──────────────────────────────────────────────
    # MACD（指数平滑异同移动平均线）—— 最经典的趋势/动量指标
    # 公式：
    #   DIF  = EMA(12) - EMA(26)         快慢线之差
    #   DEA  = EMA(DIF, 9)               DIF 的 9 日均线
    #   MACD = (DIF - DEA) × 2           柱状图（红绿柱）
    # 解读：
    #   DIF>0 多头；DIF<0 空头
    #   DIF 上穿 DEA = 金叉（买点）；下穿 = 死叉（卖点）
    # ──────────────────────────────────────────────
    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    # 列表推导式 + zip：同时遍历两个列表，逐元素相减
    dif = [round(ema12[i] - ema26[i], 4) for i in range(n)]
    dea_raw = ema(dif, 9)
    dea = [round(v, 4) for v in dea_raw]
    macd_hist = [round((dif[i] - dea[i]) * 2, 4) for i in range(n)]   # 红绿柱

    # ──────────────────────────────────────────────
    # RSI（相对强弱指数，14日）
    # 公式：
    #   RSI = 100 - 100/(1 + 平均涨幅/平均跌幅)
    # 解读：>70 超买；<30 超卖；50 中性
    # ──────────────────────────────────────────────
    # delta：每日涨跌（今日 - 昨日），长度 n-1
    delta = [closes[i] - closes[i - 1] for i in range(1, n)]
    rsi_vals = [None] * n
    # 从第 14 天开始才能算（需要 14 天数据）
    for i in range(14, n):
        # 取最近 14 天的涨跌（以当日变化结尾；delta[m] 对应第 m+1 根相对第 m 根的涨跌）
        gains = [d for d in delta[i - 14:i] if d > 0]    # 涨的天数
        losses = [-d for d in delta[i - 14:i] if d < 0]  # 跌的天数（取正值）
        avg_gain = sum(gains) / 14
        avg_loss = sum(losses) / 14
        # RSI 公式；跌幅为 0 时 RSI=100（全涨）
        rsi_vals[i] = round(100 - 100 / (1 + avg_gain / avg_loss), 2) if avg_loss > 0 else 100.0

    # ──────────────────────────────────────────────
    # KDJ（随机指标，参数 9,3,3）
    # 公式：
    #   RSV = (今收 - 9日最低) / (9日最高 - 9日最低) × 100
    #   K = 2/3 × 昨K + 1/3 × RSV
    #   D = 2/3 × 昨D + 1/3 × K
    #   J = 3K - 2D   （J 可超出 0~100，反映超买超卖极端）
    # 解读：K 上穿 D = 金叉（买点）；J>100 超买，J<0 超卖
    # ──────────────────────────────────────────────
    k_list, d_list = [None] * n, [None] * n
    k_list[18], d_list[18] = 50.0, 50.0   # 第 19 个位置初始化为 50（经验值）
    for i in range(19, n):
        low9 = min(lows[i - 8:i + 1])     # 最近 9 天最低价
        high9 = max(highs[i - 8:i + 1])   # 最近 9 天最高价
        # RSV：当前价在 9 日高低区间的相对位置（0~100）
        rsv = (closes[i] - low9) / (high9 - low9) * 100 if high9 != low9 else 50
        # K、D 递推（用昨日值平滑）
        k_list[i] = round(2 / 3 * (k_list[i - 1] or 50) + 1 / 3 * rsv, 2)
        d_list[i] = round(2 / 3 * (d_list[i - 1] or 50) + 1 / 3 * k_list[i], 2)
    # J 值（方向敏感线）
    j_list = [round(3 * (k_list[i] or 50) - 2 * (d_list[i] or 50), 2) for i in range(n)]

    # ──────────────────────────────────────────────
    # BOLL（布林带，20日，2倍标准差）
    # 公式：
    #   中轨 = MA(20)
    #   标准差 = std(最近20日收盘)
    #   上轨 = 中轨 + 2×标准差
    #   下轨 = 中轨 - 2×标准差
    # 解读：价格触及上轨=超买；触及下轨=超卖；带宽收窄=变盘前兆
    # ──────────────────────────────────────────────
    boll_mid_raw = ma(closes, 20)
    # 前 19 天 MA20 是 None，用当天收盘价填充（避免 None 影响后续计算）
    boll_mid = [v if v is not None else closes[i] for i, v in enumerate(boll_mid_raw)]
    boll_upper, boll_lower = [], []
    for i in range(n):
        if i >= 19:
            # 计算最近 20 天收盘价相对于中轨的标准差
            std = (sum((closes[j] - boll_mid[i]) ** 2 for j in range(i - 19, i + 1)) / 20) ** 0.5
            boll_upper.append(round(boll_mid[i] + 2 * std, 3))
            boll_lower.append(round(boll_mid[i] - 2 * std, 3))
        else:
            boll_upper.append(None)
            boll_lower.append(None)

    # 组装结果：每天一行，包含 K线 + 所有技术指标
    result = []
    for i in range(n):
        result.append({
            "date": dates[i],
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


@router.get("/fundamental/{symbol}")
def stock_fundamental(symbol: str):
    """
    基本面数据（从实时行情里提取估值指标）。

    注：这里没有独立的财务数据源，只用了实时行情里的 PE/PB/市值。
    """
    info = get_stock(symbol)
    if not info:
        return {"valuation": {}, "financial": {}}
    return {
        "valuation": {
            "市盈率(动态)": info.get("pe", 0),
            "市净率": info.get("pb", 0),
            # market_cap / float_cap 单位是万元，÷10000 转成亿元（与 scoring.py 一致）
            "总市值(亿)": round(info.get("market_cap", 0) / 10000, 2) if info.get("market_cap") else 0,
            "流通市值(亿)": round(info.get("float_cap", 0) / 10000, 2) if info.get("float_cap") else 0,
        },
        "financial": {
            "换手率": info.get("turnover_rate", 0),
        },
    }


# ── 财务数据（成长/质量因子数据源；东财 F10，季度更新，本地库查询）──

@router.get("/finance/{symbol}/history")
def stock_finance_history(symbol: str, limit: int = 12):
    """个股财报历史序列（最新在前）。看营收/利润增速、ROE 的趋势变化。"""
    from app.finance import get_history
    return {"code": symbol, "history": get_history(symbol, min(max(limit, 1), 40))}


@router.get("/finance/{symbol}")
def stock_finance(symbol: str, report_date: str = "", asof: str = ""):
    """
    个股财报：营收/利润增速、ROE、负债率、毛利率、净利率等。
      - 默认：最新一期
      - report_date=2026-06-30：指定报告期
      - asof=2026-07-01：取"该日期时点已公告"的最新一期（★ 回测/复盘必须用这个，
        按公告日而非报告期判断，防未来函数）
    部分字段可能为 null —— 一季报/三季报披露不全，"未披露"≠"值为0"。
    """
    from app.finance import get_finance, get_finance_asof
    if asof:
        return get_finance_asof(symbol, asof)
    return get_finance(symbol, report_date or None)


@router.get("/finance-stats")
def finance_stats():
    """财务数据表概况：覆盖股票数 / 各报告期条数 / 字段缺失率。"""
    from app.finance import stats
    return stats()


@router.post("/finance-refresh")
def finance_refresh(reports: int = 2):
    """
    手动刷新财务数据（正常由调度器每天凌晨自动检查，新报告期才拉取）。
    每期约 27 页 / 14 秒（含批量入库）。reports 为往前拉几期。
    """
    from app.finance import refresh
    return refresh(min(max(reports, 1), 8))


@router.post("/finance/batch")
def finance_batch(codes: list = Body(...)):
    """
    批量查财报（1 次 SQL + 30 分钟进程缓存），返回 {code: 财报行}。

    ★ 用途：前端本地评分引擎（utils/scoringEngine.js）算 top50 时，
      需要候选池的财报数据来算成长/质量维度。逐只调 /finance/{symbol}
      在远程库上要 0.5s/只，几百只就是几分钟；这里 1 次批量取回。
    """
    from app.finance import get_finance_batch
    codes = [c for c in (codes or []) if c][:1000]   # 上限 1000，防滥用
    return get_finance_batch(codes)


# 消息面结果缓存：{code: {data, ts}}，TTL 60s（防详情页重复请求重复打分）
_news_cache = {}
# 消息分历史缓存：TTL 300s（每日才更新一次，不需要频繁查库）
_news_history_cache = {}


# 注意：/news/{symbol}/history 必须定义在 /news/{symbol} 之前，
# 否则 "history" 会被 {symbol} 捕获。
@router.get("/news/{symbol}/history")
def stock_news_history(symbol: str, days: int = 30):
    """消息分历史快照（每日盘后落库，供详情页走势图与阶段 3 回测）。
    无数据返回空列表（首次快照在下一个工作日 15:20 后生成）。缓存 300s。"""
    key = f"{symbol}:{days}"
    now = time.time()
    c = _news_history_cache.get(key)
    if c and now - c["ts"] < 300:
        return c["data"]
    try:
        from app.news_history import get_news_history
        items = get_news_history(symbol, min(max(days, 1), 90))
    except Exception as e:
        print(f"[stock_news_history] {symbol} 读取失败: {e}")
        items = []
    result = {"code": symbol, "history": items}
    if items:
        _news_history_cache[key] = {"data": result, "ts": now}
    return result


@router.get("/news/{symbol}")
def stock_news(symbol: str):
    """
    消息面：个股新闻情绪分 + 相关快讯列表（阶段 1：东财快讯 + 关键词规则）。
    独立维度，不进入综合总分。结果缓存 60s。
    """
    now = time.time()
    c = _news_cache.get(symbol)
    if c and now - c["ts"] < 60:
        return c["data"]
    items = []
    try:
        from app.eastmoney_news import get_stock_news
        from app.news_sentiment import score_stock_news
        items = get_stock_news(symbol)
        result = score_stock_news(items)
    except Exception as e:
        print(f"[stock_news] {symbol} 消息面计算失败: {e}")
        result = {"score": 0, "level": 0, "level_text": "中性", "items": []}
    result["news_count"] = len(items)
    _news_cache[symbol] = {"data": result, "ts": now}
    return result


@router.get("/anomalies")
def stock_anomalies(
    watch_codes: str = Query(default="", description="逗号分隔的关注代码（优先检测）"),
):
    """
    异动监控：检测全市场的异常信号（放量/急涨急跌/涨停跌停）。
    优先返回 watch_codes 中的股票（用户持仓/自选）。
    """
    stocks = _cache.get("stocks", {})
    if not stocks:
        return {"data": [], "total": 0}

    watch_set = set(c.strip() for c in watch_codes.split(",") if c.strip())
    anomalies = []

    for code, s in stocks.items():
        price = s.get("price", 0) or 0
        change_pct = s.get("change_pct")
        if price <= 0 or change_pct is None:
            continue

        volume = s.get("volume", 0) or 0
        turnover_rate = s.get("turnover_rate", 0) or 0
        amplitude = s.get("amplitude", 0) or 0
        types = []

        # 1. 急涨：涨幅 >= 5%
        if change_pct >= 5:
            types.append({"type": "急涨", "severity": 2 if change_pct >= 8 else 1,
                          "desc": f"涨 {change_pct:.1f}%"})
        # 2. 急跌：跌幅 <= -5%
        elif change_pct <= -5:
            types.append({"type": "急跌", "severity": 2 if change_pct <= -8 else 1,
                          "desc": f"跌 {change_pct:.1f}%"})

        # 3. 涨停（涨幅接近 10% 且 <= 10.1%）
        if 9.8 <= change_pct <= 10.1:
            types.append({"type": "涨停", "severity": 3, "desc": f"涨停 {change_pct:.1f}%"})
        # 4. 跌停
        elif -10.1 <= change_pct <= -9.8:
            types.append({"type": "跌停", "severity": 3, "desc": f"跌停 {change_pct:.1f}%"})

        # 5. 高换手（>10% 异常活跃）
        if turnover_rate > 10:
            types.append({"type": "高换手", "severity": 1,
                          "desc": f"换手率 {turnover_rate:.1f}%"})

        # 6. 大振幅（>8%）
        if amplitude > 8:
            types.append({"type": "大振幅", "severity": 1,
                          "desc": f"振幅 {amplitude:.1f}%"})

        if types:
            max_severity = max(t["severity"] for t in types)
            anomalies.append({
                "code": code,
                "name": s.get("name", ""),
                "price": price,
                "change_pct": round(change_pct, 2),
                "turnover_rate": turnover_rate,
                "signals": types,
                "severity": max_severity,
                "is_watched": code in watch_set,
            })

    # 排序：关注股票优先 + 严重程度降序
    anomalies.sort(key=lambda x: (not x["is_watched"], -x["severity"], -abs(x["change_pct"])))
    return {"data": anomalies[:100], "total": len(anomalies)}
