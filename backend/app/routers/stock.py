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
import json
import time
import numpy as np
from app.database import db
from app.tencent import get_stock, get_kline, search_stocks, _CODE_TO_PREFIX, _cache
# ★ 2026-10-08（P1）：单票异动判定要用"涨速"。腾讯快照**没有**这个字段，
#   由 `tencent.speed_pct()` 搭车采样自算（详见 tencent.py 的 `_record_speed` 注释）。
from app.tencent import speed_pct

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


# ══════════════════════════════════════════════════════════════════════════
#  异动判定（唯一口径）—— 2026-10-08 P1 重构
# ══════════════════════════════════════════════════════════════════════════
# 【为什么抽成单票函数】"单股异动 LLM 分析"（P2）要先问"这只票此刻**值不值得**分析"，
#   而原实现把判定写死在"全市场 for 循环"里 ⇒ 单票无法复用（复制一份必然漂移，
#   本项目"同一数据两处渲染=必然漂移"的同款纪律，见 MEMORY）。
# 【P1 新增两个维度：量比 / 涨速】
#   涨幅是**结果**（可能已经涨完了），"涨速 + 量比"才是**此刻正在发生**的证据 ——
#   这正是用户诉求"突发拉升"的直接度量。
# 【涨跌停改用真值价 `limit_up/limit_down`】
#   原先 `9.8 <= 涨跌幅 <= 10.1` 判涨停：**ST(±5%) 与创业板/科创板(±20%) 判不出**
#   （真涨停 19.9% 落不进 9.8~10.1）。现在直接比价，天然覆盖全板块。
#   ⚠️ 兼容：`restore_market_snapshot` 可能恢复**本次改动前保存的旧快照**（无新字段）
#   ⇒ 取不到真值价时**退回旧启发式**，不让判定凭空消失。
# 【抗过吵（本项目纪律：新信号先问"会不会过吵"）】
#   · `放量` 要求"量比≥3 **且** |涨跌幅|≥2"：单纯量比大不是异动，且会灌满列表；
#   · 给它的 severity 只 1 ⇒ 排序天然排在急涨/涨停(2/3)之后，不挤掉重要条目。
# ══════════════════════════════════════════════════════════════════════════

def _detect_anomaly(code: str, s: dict) -> list:
    """单票异动判定 → signal 列表 `[{type, severity, desc}]`（无异动返回空表）。

    ★ 口径唯一：`stock_anomalies`（全市场扫描）与 `detect_single_anomaly`（单票）共用。
    """
    price = s.get("price", 0) or 0
    change_pct = s.get("change_pct")
    if price <= 0 or change_pct is None:
        return []

    turnover_rate = s.get("turnover_rate", 0) or 0
    amplitude = s.get("amplitude", 0) or 0
    volume_ratio = s.get("volume_ratio") or 0
    limit_up = s.get("limit_up") or 0
    limit_down = s.get("limit_down") or 0
    types = []

    # 1. 急涨：涨幅 >= 5%
    if change_pct >= 5:
        types.append({"type": "急涨", "severity": 2 if change_pct >= 8 else 1,
                      "desc": f"涨 {change_pct:.1f}%"})
    # 2. 急跌：跌幅 <= -5%
    elif change_pct <= -5:
        types.append({"type": "急跌", "severity": 2 if change_pct <= -8 else 1,
                      "desc": f"跌 {change_pct:.1f}%"})

    # 3/4. 涨停 / 跌停（真值价优先；无真值价退回旧启发式）
    up_hit = (price >= limit_up - 0.005) if limit_up else (9.8 <= change_pct <= 10.1)
    dn_hit = (price <= limit_down + 0.005) if limit_down else (-10.1 <= change_pct <= -9.8)
    if up_hit:
        types.append({"type": "涨停", "severity": 3, "desc": f"涨停 {change_pct:.1f}%"})
    elif dn_hit:
        types.append({"type": "跌停", "severity": 3, "desc": f"跌停 {change_pct:.1f}%"})

    # 5. 高换手（>10% 异常活跃）
    if turnover_rate > 10:
        types.append({"type": "高换手", "severity": 1,
                      "desc": f"换手率 {turnover_rate:.1f}%"})

    # 6. 大振幅（>8%）
    if amplitude > 8:
        types.append({"type": "大振幅", "severity": 1,
                      "desc": f"振幅 {amplitude:.1f}%"})

    # 7. 放量（P1 新增）：量比≥3 且有方向 ⇒ "放量拉升/放量下杀"才算异动
    if volume_ratio >= 3 and abs(change_pct) >= 2:
        types.append({"type": "放量", "severity": 1,
                      "desc": f"量比 {volume_ratio:.1f}，{change_pct:+.1f}%"})

    # 8. 急拉升 / 急跳水（P1 新增）：近 5 分钟涨速 ≥3%
    #    ⚠️ `speed_pct` 采样不足时返回 **None**（需 ≥3 分钟历史）⇒ 那时**不判定**，
    #       绝不把 None 当 0、更不当"没涨"（与 tencent.speed_pct 的纪律一致）。
    try:
        sp5 = speed_pct(code, 5)
    except Exception:
        sp5 = None
    if sp5 is not None and sp5 >= 3:
        types.append({"type": "急拉升", "severity": 2, "desc": f"5分钟涨速 {sp5:+.1f}%"})
    elif sp5 is not None and sp5 <= -3:
        types.append({"type": "急跳水", "severity": 2, "desc": f"5分钟涨速 {sp5:+.1f}%"})
    return types


@router.get("/anomalies")
def stock_anomalies(
    watch_codes: str = Query(default="", description="逗号分隔的关注代码（优先检测）"),
):
    """
    异动监控：检测全市场的异常信号
    （急涨急跌/涨停跌停/高换手/大振幅 + 2026-10-08 新增 放量/急拉升/急跳水）。
    优先返回 watch_codes 中的股票（用户持仓/自选）。

    ★ 2026-10-08（P1）三件事：
      ① 判定抽到 `_detect_anomaly()`（单票可复用 ⇒ 供"单股异动 LLM 分析"当闸门）
      ② 新增 量比/涨速 两维；涨跌停改**真值价**比对（ST/创业板不再漏判）
      ③ **补 `tags` 字段** —— 前端 `ScoreRank.vue` 的异动标签一直是
         `v-for="tag in a.tags"`，而后端只返回 `signals`（结构体）⇒ `tags` 恒 undefined
         ⇒ **标签从来没渲染出来过**（`v-for` over undefined 静默、构建与日志均无痕）
         ⇒ 面板只剩"名称+价格"、看不到异动原因。与同文件 2026-09-25 修过的
         "调了未 import 的函数被 try/catch 吞掉"属**同一类静默失效**。
    """
    stocks = _cache.get("stocks", {})
    if not stocks:
        return {"data": [], "total": 0}

    watch_set = set(c.strip() for c in watch_codes.split(",") if c.strip())
    anomalies = []

    for code, s in stocks.items():
        types = _detect_anomaly(code, s)
        if not types:
            continue
        change_pct = s.get("change_pct") or 0
        anomalies.append({
            "code": code,
            "name": s.get("name", ""),
            "price": s.get("price", 0) or 0,
            "change_pct": round(change_pct, 2),
            "turnover_rate": s.get("turnover_rate", 0) or 0,
            "volume_ratio": s.get("volume_ratio") or 0,
            "signals": types,
            "tags": [t["type"] for t in types],     # ★ 前端消费的就是这个（原来漏了）
            "severity": max(t["severity"] for t in types),
            "is_watched": code in watch_set,
        })

    # 排序：关注股票优先 + 严重程度降序
    anomalies.sort(key=lambda x: (not x["is_watched"], -x["severity"], -abs(x["change_pct"])))
    return {"data": anomalies[:100], "total": len(anomalies)}


def detect_single_anomaly(code: str) -> dict:
    """单票异动快照 —— 「单股异动 LLM 分析」的**闸门**（值不值得分析），不调 LLM。

    ★ 优先读全市场缓存（盘中实时）；不在缓存里（盘后/池外代码）才单拉一次，
      且单拉**不写缓存**（避免用一只票的数据污染全市场快照）。
    ★ `speed_5min` 可能为 `None`（采样不足 3 分钟）—— **如实返回 None**，
      调用方必须自己区分"没法算"与"没涨"（别把 None 当 0 用）。
    ★ `data_ts` 是本项目"数据时点"的唯一口径（**绝不用 `last_update`**：
      快照恢复会把它故意设成"现在"⇒ 显示出来就是谎报数据新鲜度）。
    """
    stocks = _cache.get("stocks", {}) or {}
    s = stocks.get(code)
    source = "cache"
    data_ts = _cache.get("data_ts") or None
    from_snapshot = bool(_cache.get("from_snapshot"))
    if not s:
        try:
            s = get_stock(code) or {}
            source = "single_fetch"
            data_ts = time.time()      # 单拉 = 此刻实时抓取
            from_snapshot = False
        except Exception as e:
            return {"code": code, "error": f"行情获取失败: {str(e)[:80]}",
                    "signals": [], "tags": []}
    if not s or not (s.get("price") or 0):
        return {"code": code, "error": "无行情数据（代码可能有误，或该时段无数据）",
                "signals": [], "tags": []}

    types = _detect_anomaly(code, s)
    try:
        sp5 = speed_pct(code, 5)       # 可能 None（如实）
    except Exception:
        sp5 = None
    return {
        "code": code,
        "name": s.get("name", ""),
        "price": s.get("price", 0) or 0,
        "change_pct": round(s.get("change_pct") or 0, 2),
        "volume_ratio": s.get("volume_ratio") or 0,
        "speed_5min": sp5,
        "turnover_rate": s.get("turnover_rate", 0) or 0,
        "limit_up": s.get("limit_up") or 0,
        "limit_down": s.get("limit_down") or 0,
        # 流通市值（万元）—— L2 现算主力行为时算流通股本要用它（口径同详情页：
        #   `float_shares = float_cap × 1e4 ÷ 现价`）
        "float_cap": s.get("float_cap") or 0,
        "signals": types,
        "tags": [t["type"] for t in types],
        "severity": max([t["severity"] for t in types], default=0),
        "should_analyze": bool(types),
        "source": source,
        "data_ts": data_ts,
        "from_snapshot": from_snapshot,
    }


@router.get("/anomaly-signals/{symbol}")
def stock_anomaly_signals(symbol: str):
    """单票异动**闸门**（轻量、**不调 LLM**）—— 前端据此决定「要不要亮分析入口」。

    ★ 为什么单开一个只读端点：用户的原始诉求是"**项目察觉不到**异动"——
      只做成"用户自己点"是不够的，**页面必须主动把异动标出来**。
      而 `/stock/anomalies` 是**全市场扫描**（要遍历 4000+ 只），对单票页太重；
      本端点复用**同一口径**的 `detect_single_anomaly`，毫秒级、无外部请求
      （命中行情缓存时）、无 LLM 成本 ⇒ 可随页面加载安全调用。
    """
    return detect_single_anomaly(symbol)


# ══════════════════════════════════════════════════════════════════════════
#  P2（2026-10-08）：单股异动 LLM 分析 —— 判读「拉高出货 / 诱多上套 / 真实突破」
# ══════════════════════════════════════════════════════════════════════════
# 【用户诉求】观察到某只票突发拉升，但分不清是拉高出货、诱多上套还是真突破。
# 【五条纪律（每条都对应一个本项目已踩过的坑）】
#   ① **按需烧 LLM**：点一下才调一次；结果按 (code, 交易日) 落库，但**复用窗口只有
#      30 分钟** —— 盘中情况在变，把一小时前的结论当"此刻"就是误导。
#   ② **结论不进决策链**：只做解读，不产生信号（与 `stock_moves`/板块动量同纪律）。
#   ③ **四分类而非三选一**：证据不足必须能落 "暂无定论" —— 逼三选一正是误导之源。
#   ④ **代码层护栏**（不依赖 LLM 自觉）：`_ORDER_WORDS` 剔指令式措辞；
#      结论与主力资金标签**互相矛盾**时强制降置信度。
#   ⑤ **成本与时效可见**：返回 items_used / cached / as_of / data_ts，
#      前端如实标注"分析于 X、数据截至 Y"。
# 【与「系统自洽性审查」的区别】那个扫"系统自己前后发的提示是否矛盾"（`/flash/radar-analysis`）；
#   本端点是**单票**此刻的盘口/资金/位置解读。两者共用 `call_llm`，数据源与提示词完全不同。
_STOCK_ANOMALY_TABLE = "stock_anomaly_analysis"
_STOCK_ANOMALY_READY = False
_STOCK_ANOMALY_REUSE_MIN = 30          # 结论复用窗口（分钟）：超过即失效重算
_STOCK_ANOMALY_CONCLUSIONS = ("拉高出货", "诱多上套", "真实突破", "暂无定论")

_DISCLAIMER = "本分析为数据解读，不构成任何投资建议或操作指令。"

_ANOMALY_SYSTEM = (
    "你是 A 股个股盘中异动的解读助手，不是交易顾问。\n"
    "【数据铁律】\n"
    "1. 只能使用输入数据中的具体数值推理，禁止编造任何价格、涨跌幅、量能、历史事件或时间。\n"
    "2. 输入中标为「N/A」「缺失」的维度，禁止基于假设继续推理；必须把它列进 missing，"
    "并以「因该维度缺失，置信度降级」的方式体现在 confidence 上。\n"
    "3. 禁止使用「通常/历史上/一般」等模糊表述，必须基于本次输入的具体数据做因果推导。\n"
    "【角色铁律】\n"
    "绝不给出「买入/加仓/卖出/减仓/补仓/清仓/止损/止盈/追涨/杀跌/抄底/建仓」等指令式建议；"
    "只给判断、证据、观察价位与风险提示。观察位只描述价格行为"
    "（如「跌破 9.18 则当前形态被破坏」），不描述「该怎么操作」。\n"
    "【诚实铁律】\n"
    "证据不足时结论必须写「暂无定论」，不要为了给结论硬凑因果；"
    "support 与 against 都必须输出（一方确实没有就写「无明显反证」）。"
)

# 用普通字符串（非 f-string）——JSON 里的大括号太多，塞进 f-string 极易踩 `{` 转义坑
_ANOMALY_SCHEMA = """{
  "conclusion": "拉高出货 | 诱多上套 | 真实突破 | 暂无定论",
  "confidence": "高 | 中 | 低",
  "support": ["支持证据，每条注明来自哪个数据字段"],
  "against": ["反对证据，同上；确实没有就写 无明显反证"],
  "watch_levels": {"resistance": 数字或null, "support": 数字或null},
  "risk": "一句话风险提示（不得含操作指令措辞）",
  "missing": ["本次缺失的关键维度"]
}"""


def _anomaly_ensure():
    global _STOCK_ANOMALY_READY
    if _STOCK_ANOMALY_READY:
        return
    db.execute(f"""
        CREATE TABLE IF NOT EXISTS {_STOCK_ANOMALY_TABLE} (
            code TEXT NOT NULL,
            date TEXT NOT NULL,
            result_json TEXT,
            created_at TEXT,
            PRIMARY KEY (code, date)
        )
    """)
    _STOCK_ANOMALY_READY = True


def _market_desc():
    """当前市场时段描述（必须喂给 LLM：收盘后还在说"此刻正在拉升"就是幻觉）。"""
    try:
        from app.flash import rules as _r
        st = _r.get_china_market_status() or {}
        return str(st.get("reason") or ("盘中" if st.get("is_open") else "非交易时段"))
    except Exception:
        return "未知"


def _latest_regime():
    try:
        r = db.fetch_one("SELECT state FROM market_regime_history ORDER BY date DESC LIMIT 1")
        return (r or {}).get("state")
    except Exception:
        return None


def _stock_history(code: str, days: int = 61):
    """L4 历史位置：只取**价格**（避开跨源量纲坑），返回 dict 或 None。

    ⚠️ 三条诚实前提：
      ① 日线**收盘后 15:40 才回填**（见 backtest 链路）⇒ 盘中最后一根是**昨日**
         ⇒ 必须标注"截至上一交易日"，否则 LLM 会把昨日当今日、与 L1 实时涨幅打架。
      ② `backtest_prices` 只有**池内 ~800 只**（非全市场）⇒ 池外返回 None（如实列 missing）。
      ③ **不跨源算量比**：腾讯 `volume`（手）与 `backtest_prices.volume` 量纲未核对，
         混算必然错——量能归一化一律用交易所口径的 `volume_ratio`（L1，无量纲）。
    """
    try:
        rows = db.fetch("SELECT date, high, low, close FROM backtest_prices "
                        "WHERE code = %s ORDER BY date DESC LIMIT %s", (code, int(days)))
    except Exception as e:
        print(f"[anomaly] history query failed: {e}")
        return None
    if not rows or len(rows) < 6:
        return None
    rows = list(reversed(rows))
    closes = [float(r.get("close") or 0) for r in rows]
    if len(closes) < 6 or any(c <= 0 for c in closes):
        return None
    rets = [round((closes[i] / closes[i - 1] - 1) * 100, 2)
            for i in range(len(closes) - 5, len(closes))]
    highs = [float(r.get("high") or 0) for r in rows if (r.get("high") or 0) > 0]
    lows = [float(r.get("low") or 0) for r in rows if (r.get("low") or 0) > 0]
    cur = closes[-1]
    return {
        "last_date": str(rows[-1].get("date"))[:10],
        "rets5": rets,
        "off_high": round((cur / max(highs) - 1) * 100, 2) if highs else None,
        "off_low": round((cur / min(lows) - 1) * 100, 2) if lows else None,
    }


def _mainforce_for(symbol: str, snap: dict):
    """L2 主力行为：**口径照抄** `routers/scoring.py` 的详情页（优先日批表，再现算）。

    ★ 为什么优先日批表：`mainforce_state` 是日批算好的（快）；但**必须日期与最新K线一致**
      才算新鲜（否则拿来的是别的交易日的标签 —— "缓存写入时刻≠数据时刻"的同款坑）。
    ★ 现算路径的 `float_shares` 用同一算式：`float_cap(万元) × 1e4 ÷ 现价`。
    返回 (mf_dict|None, 来源标记)。
    """
    bars = None
    try:
        from app.backtest.data import load_prices
        bars = load_prices(symbol)
    except Exception as e:
        print(f"[anomaly] load_prices failed {symbol}: {e}")
    last_date = str(bars[-1].get("date"))[:10] if bars else ""
    try:
        from app.mainforce.state import load_latest as _mf_load
        mf = (_mf_load([symbol]) or {}).get(symbol)
        if mf and last_date and str(mf.get("date"))[:10] == last_date:
            return mf, "daily_snapshot"
    except Exception as e:
        print(f"[anomaly] mainforce_state read failed {symbol}: {e}")
    if not bars or len(bars) < 120:
        return None, "unavailable"
    try:
        from app.mainforce.flow import load_flow
        from app.mainforce.overlay import mainforce_overlay
        fs = None
        fc = snap.get("float_cap") or 0
        px = snap.get("price") or 0
        if fc > 0 and px > 0:
            fs = fc * 1e4 / px
        mf = mainforce_overlay(bars, flow_rows=load_flow(symbol),
                               float_shares=fs, regime=_latest_regime())
        return mf, "computed"
    except Exception as e:
        print(f"[anomaly] mainforce compute failed {symbol}: {e}")
        return None, "unavailable"


def _anomaly_data_block(snap: dict, hist, mf, market: str):
    """组装 L1/L2/L4 提示词文本。返回 (文本, items_used 计数, missing 列表)。

    ★ 缺的维度**显式写 N/A**并计入 missing（铁律：缺数据不填 0、0 与缺失必须可区分）。
    """
    L = []
    missing = []
    used = 0

    # ── L1 盘口快照（实时） ──
    price = snap.get("price") or 0
    L.append("### L1 盘中快照（实时）")
    L.append(f"- 现价 {price}（涨跌幅 {snap.get('change_pct')}%）")
    used += 1
    avg = snap.get("avg_price") or 0
    if avg > 0 and price > 0:
        off = (price / avg - 1) * 100
        L.append(f"- 当日均价 {avg}（现价相对均价 {off:+.2f}%；为负=冲高回落/尾盘走弱）")
        used += 1
    else:
        L.append("- 当日均价：N/A")
        missing.append("成交均价")
    vr = snap.get("volume_ratio") or 0
    if vr > 0:
        L.append(f"- 量比 {vr}（1=与近5日同期均量持平）")
        used += 1
    else:
        L.append("- 量比：N/A（该字段未取到，量能维度缺失）")
        missing.append("量比")
    sp5 = snap.get("speed_5min")
    if sp5 is not None:
        L.append(f"- 5 分钟涨速 {sp5:+.2f}%")
        used += 1
    else:
        L.append("- 5 分钟涨速：N/A（采样不足 3 分钟，**不是**「没涨」）")
        missing.append("5分钟涨速")
    lu, ld = snap.get("limit_up") or 0, snap.get("limit_down") or 0
    if lu and ld:
        L.append(f"- 涨停价 {lu} ｜ 跌停价 {ld}（真值限价，含 ST/创业板口径）")
        used += 1
    bd = snap.get("bid_diff")
    if bd is not None:
        L.append(f"- 委差 {float(bd):+.0f} 手（买五档−卖五档：正=买盘挂单占优）")
        used += 1
    turn = snap.get("turnover_rate")
    if turn:
        L.append(f"- 换手率 {turn}% ｜ 振幅 {snap.get('amplitude')}%")
        used += 1
    tags = snap.get("tags") or []
    L.append(f"- 系统异动标签：{'、'.join(tags) if tags else '无'}")

    # ── L2 主力行为 / 资金 ──
    L.append("### L2 主力行为 / 资金流（日批口径）")
    if mf:
        L.append(f"- 主力阶段：{mf.get('phase_cn') or mf.get('phase') or 'N/A'}"
                 f" ｜ 信号：{mf.get('signal_cn') or '无'}")
        f5 = mf.get("flow5_amt_yuan")
        if f5 is not None:
            L.append(f"- 近5日主力净流入 {float(f5) / 1e8:+.2f} 亿元"
                     f"（占成交额 {mf.get('flow5_amt')}%）")
            used += 1
        else:
            L.append("- 近5日主力净流入：N/A")
            missing.append("主力净流入")
        chip = mf.get("chip") or {}
        if chip:
            L.append(f"- 筹码：获利盘 {chip.get('winner_ratio')} ｜ "
                     f"价位位置 {chip.get('price_pos')}（0=区间底 1=区间顶）｜ "
                     f"集中度 {chip.get('concentration')}（越小越集中）")
            used += 1
        if mf.get("reason"):
            L.append(f"- 判定理由：{mf['reason']}")
    else:
        L.append("- 无（该股不在主力快照池内或K线不足 120 根）")
        missing.append("主力行为")

    # ── L4 历史位置 ──
    L.append("### L4 历史位置（日线，截至**上一交易日**，当日线在收盘 15:40 后才回填）")
    if hist:
        L.append(f"- 近5日涨跌幅（{hist['last_date']} 为最新一根）："
                 f"{'、'.join(f'{x:+.2f}%' for x in hist['rets5'])}")
        if hist.get("off_high") is not None:
            L.append(f"- 距 60 日最高 {hist['off_high']:+.2f}% ｜ "
                     f"距 60 日最低 {hist['off_low']:+.2f}%")
        used += 1
    else:
        L.append("- 无（该股不在 backtest_prices 池内，约 800 只；或样本不足）")
        missing.append("历史日线")

    L.append(f"### 市场时段\n- {market}")
    return "\n".join(L), used, missing


def _bj_ts(ts):
    """Unix 秒 → 北京时区 'YYYY-MM-DD HH:MM:SS'（无法解析返回 'N/A'）。

    ⚠️ 必须显式带 tz：`datetime.fromtimestamp(ts)` 走**服务器本地时区**
      （生产 = UTC）⇒ 时间会差 8 小时。本项目反复踩过（见 MEMORY「时点」条）。
    """
    try:
        from datetime import timedelta, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone(timedelta(hours=8))
                                      ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return "N/A"


def _build_anomaly_user(snap: dict, data_text: str, env_text: str, missing: list) -> str:
    miss_txt = "、".join(missing) if missing else "无"
    return (
        f"## 个股异动数据\n标的：{snap.get('name')}（{snap.get('code')}）\n"
        f"数据时点：{_bj_ts(snap.get('data_ts'))}"
        f"｜ 数据来源：{'收盘快照（非实时）' if snap.get('from_snapshot') else '实时抓取'}\n"
        f"{data_text}\n\n"
        f"{env_text}\n"
        f"## 本次缺失维度（必须体现到 missing 与 confidence 上）\n- {miss_txt}\n\n"
        "## 任务\n判断这次异动更接近哪种情形，按下方格式输出**合法 JSON**（不要 markdown 围栏、"
        "不要额外解释）：\n"
        f"{_ANOMALY_SCHEMA}\n\n"
        "【判断口径：按此推理，不要自创逻辑】\n"
        "- 拉高出货：主力净流出/出货嫌疑 **且** 筹码位置偏高 **且**（放量滞涨 或 现价跌破均价）\n"
        "- 诱多上套：量比不高（未持续放量）**且** 无主力资金配合 **且** 接近上方压力/密集成交区\n"
        "- 真实突破：放量（量比≥2）**且** 主力净流入/吸筹标签 **且** 站上关键压力且未回落\n"
        "- 关键维度（资金流 / 量比 / 涨速）任一缺失 ⇒ **优先落「暂无定论」**\n"
        "- confidence：量价与资金方向互相印证 → 高；存在明显反证或维度缺失 → 低；其余 → 中\n"
        "- watch_levels 必须基于**输入里出现过的价格**（现价/均价/涨跌停价/近5日区间）给出，"
        "无依据填 null\n"
        "- risk 只描述价格风险，不得出现任何操作指令措辞"
    )


def _strip_order_words(text):
    """命中买卖指令词 ⇒ 返回 None（调用方决定剔除或替换）。口径复用 daily_report。"""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    try:
        from app.daily_report import _ORDER_WORDS    # 唯一事实源，别在本文件重抄一份
    except Exception:
        return s
    return None if any(w in s for w in _ORDER_WORDS) else s


def _consistency_warnings(conclusion: str, mf) -> list:
    """结论 vs 主力资金标签的**代码层**矛盾检测（LLM 说什么不算，证据说了算）。

    ⚠️ 为什么必须有：LLM 完全可能一边看到"出货嫌疑 + 近5日净流出"、一边给出「真实突破」。
      prompt 里写了判断口径，但**不能只靠它自觉**（本项目纪律：护栏要落在代码层）。
    """
    w = []
    if not mf or not conclusion:
        return w
    dist = (mf.get("signal") == "distribution") or (mf.get("signal_cn") == "出货嫌疑")
    accum = (mf.get("signal") == "accum")
    f5 = mf.get("flow5_amt_yuan")
    out = (f5 is not None and float(f5) < 0)
    if conclusion == "真实突破" and (dist or out):
        w.append("结论=真实突破，但主力资金为净流出/出货嫌疑 ⇒ 与突破口径矛盾")
    if conclusion == "拉高出货" and accum and not out:
        w.append("结论=拉高出货，但主力为吸筹/净流入 ⇒ 证据方向相反")
    if conclusion == "真实突破" and not accum and f5 is None:
        w.append("结论=真实突破，但无主力资金数据佐证 ⇒ 证据不足")
    return w


def _pos_num(v):
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


@router.get("/anomaly-analysis/{symbol}")
def stock_anomaly_analysis(symbol: str, refresh: bool = False):
    """单股异动 LLM 分析（**按需触发**；结果 30 分钟内复用）。

    ★ 成本：每次**最多**一次 LLM 调用（`tier="fast"`），且受全局日熔断
      `LLM_DAILY_MAX_CALLS` 与 6s 间隔节流约束；命中复用窗口则零调用。
    ★ 与 `detect_single_anomaly` 共用异动口径；本端点**只解读、不产生信号**。
    ★ 失败**不写缓存**（否则 30 分钟内会一直返回失败结论）。
    """
    from app.flash import rules as _rules
    day = _rules.beijing_now().strftime("%Y-%m-%d")

    # ① 复用窗口（30 分钟）：注意用 **beijing_now()** 算年龄 ——
    #    库里的时间列按北京时间写，用服务器本地 now()（生产 UTC）会差 8 小时
    if not refresh:
        try:
            _anomaly_ensure()
            row = db.fetch_one(
                f"SELECT result_json, created_at FROM {_STOCK_ANOMALY_TABLE} "
                f"WHERE code = %s AND date = %s", (symbol, day))
            if row and row.get("result_json"):
                try:
                    age = (_rules.beijing_now()
                           - datetime.fromisoformat(str(row["created_at"]))).total_seconds() / 60
                except Exception:
                    age = 1e9
                if age <= _STOCK_ANOMALY_REUSE_MIN:
                    res = json.loads(row["result_json"])
                    res["cached"] = True
                    res["cached_age_min"] = round(age, 1)
                    return res
        except Exception as e:
            print(f"[anomaly] cache read failed (recompute): {e}")

    # ② 组装数据（L1 直接复用闸门 ⇒ 与"要不要分析"同口径）
    snap = detect_single_anomaly(symbol)
    if snap.get("error"):
        return {"code": symbol, "date": day, "cached": False, "error": snap["error"]}
    mf, mf_src = _mainforce_for(symbol, snap)
    hist = _stock_history(symbol)
    try:
        from app.flash.llm import format_a_share_context
        env_text = format_a_share_context()
    except Exception as e:
        env_text = ""
        print(f"[anomaly] env context failed: {e}")
    data_text, items_used, missing = _anomaly_data_block(snap, hist, mf, _market_desc())
    user = _build_anomaly_user(snap, data_text, env_text, missing)

    # ③ 调 LLM（失败只回一行错误，不抛）
    parsed = {}
    llm_err = ""
    try:
        from app.flash import llm as _llm
        blocked = _llm.llm_blocked_reason()
        if blocked:
            llm_err = blocked
        else:
            parsed = _llm._call_json(_ANOMALY_SYSTEM, user, temperature=0.2) or {}
    except Exception as e:
        llm_err = f"LLM 调用异常: {str(e)[:160]}"
    if not parsed:
        return {"code": symbol, "date": day, "cached": False,
                "error": llm_err or "LLM 返回空（可稍后重试）",
                "name": snap.get("name"), "price": snap.get("price"),
                "change_pct": snap.get("change_pct"), "tags": snap.get("tags") or [],
                "items_used": items_used, "missing": missing,
                "data_ts": snap.get("data_ts")}

    # ④ 代码层护栏：枚举白名单 + 指令词过滤 + 结论自洽
    conclusion = str(parsed.get("conclusion") or "").strip()
    if conclusion not in _STOCK_ANOMALY_CONCLUSIONS:
        conclusion = "暂无定论"
    confidence = str(parsed.get("confidence") or "").strip()
    if confidence not in ("高", "中", "低"):
        confidence = "低"
    warns = _consistency_warnings(conclusion, mf)
    if warns and confidence != "低":
        confidence = "低"          # ★ 不跟 LLM 争辩，直接降级
    dropped = 0
    support = []
    for x in (parsed.get("support") or [])[:6]:
        t = _strip_order_words(x)
        if t:
            support.append(t[:200])
        else:
            dropped += 1
    against = []
    for x in (parsed.get("against") or [])[:6]:
        t = _strip_order_words(x)
        if t:
            against.append(t[:200])
        else:
            dropped += 1
    risk = _strip_order_words(parsed.get("risk"))
    if risk is None and parsed.get("risk"):
        dropped += 1
        risk = "（原风险提示含操作指令措辞，已按纪律剔除）"
    wl = parsed.get("watch_levels") or {}
    as_of = _rules.beijing_now().isoformat(timespec="seconds")
    result = {
        "code": symbol, "name": snap.get("name"), "date": day,
        "as_of": as_of,
        "cached": False,
        "price": snap.get("price"), "change_pct": snap.get("change_pct"),
        "volume_ratio": snap.get("volume_ratio") or 0,
        "speed_5min": snap.get("speed_5min"),
        "turnover_rate": snap.get("turnover_rate") or 0,
        "tags": snap.get("tags") or [],
        "conclusion": conclusion, "confidence": confidence,
        "support": support, "against": against,
        "watch_levels": {"resistance": _pos_num(wl.get("resistance")),
                         "support": _pos_num(wl.get("support"))},
        "risk": risk or "",
        "missing": (list(parsed.get("missing") or [])[:6] or missing),
        "consistency_warnings": warns,
        "filtered_fields": dropped,
        "disclaimer": _DISCLAIMER,
        "items_used": items_used,
        "mf_source": mf_src,
        "data_ts": snap.get("data_ts"),
        "from_snapshot": snap.get("from_snapshot"),
    }

    # ⑤ 落库（失败不影响本次返回）
    try:
        _anomaly_ensure()
        db.upsert(_STOCK_ANOMALY_TABLE,
                  {"code": symbol, "date": day,
                   "result_json": json.dumps(result, ensure_ascii=False),
                   "created_at": as_of},
                  conflict_columns=["code", "date"])
    except Exception as e:
        print(f"[anomaly] cache write failed (still returned): {e}")
    return result
