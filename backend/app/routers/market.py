"""
================================================================================
【文件作用】市场行情路由（大盘指数 + 全A股列表）
================================================================================

注册到 main.py 后，所有接口 URL 前缀是 /api/market：
  GET /api/market/overview          → 市场概览（指数 + 涨跌统计）
  GET /api/market/realtime          → 全A股实时行情（分页 + 排序）
  GET /api/market/index-kline/{代码} → 大盘指数 K线
  GET /api/market/refresh-status    → 缓存刷新状态
  GET /api/market/trigger-refresh   → 手动触发刷新

类比前端：
  - router 相当于 Vue Router 的路由配置 / Express 的 router.get(...)
  - @router.get("/xxx") 把下面的 async 函数绑定到 GET /xxx
  - 函数返回的 dict/list 会被 FastAPI 自动转成 JSON 响应
================================================================================
"""

from typing import Dict

from fastapi import APIRouter, Query, BackgroundTasks
from datetime import datetime, timedelta, timezone

def _bj_now():
    """北京时间（本文件局部助手；全项目时间源约定见 app/flash/rules.py）"""
    return datetime.now(timezone(timedelta(hours=8)))
import time
import threading
from app.tencent import (
    get_index, refresh_all_stocks, get_kline,
    _cache, BATCH_SIZE, _ALL_CODES, _is_trading_hours,
)

# 创建路由实例。类比 Express：const router = express.Router()
router = APIRouter()

# 6 个主要大盘指数：(市场前缀, 代码, 名称)
# 这些是 A股最重要的大盘风向标
MAIN_INDICES = [
    ("sh", "000001", "上证指数"),   # 上海证券交易所综合指数（最重要）
    ("sz", "399001", "深证成指"),   # 深圳成份指数
    ("sz", "399006", "创业板指"),   # 创业板（科技/成长股集中地）
    ("sh", "000300", "沪深300"),    # 沪深两市市值最大的 300 只
    ("sh", "000905", "中证500"),    # 中盘 500 只
    ("sh", "000688", "科创50"),     # 科创板 50 只
]


# ================================================================
#  市场环境温度（0~100）：独立的「大盘环境」信号，不进入个股评分
# ================================================================
# 设计理由：A 股个股收益高度由大盘 beta 主导，纯个股因子有盲区。
#   这里把「市场环境」单独量化成一个温度，和个股评分并列展示、并给出
#   「建议买入线」的参考，但【绝不】改个股分本身（避免混淆两个维度、
#   避免顺周期助涨助跌）。
#
# 组成（均为现有数据，无需新数据源）：
#   1. 市场宽度（涨跌家数比 + 平均涨幅 + 涨跌停）—— 来自全市场行情缓存
#   2. 大盘趋势（上证综指 vs MA20/MA60 + 动量）—— 来自指数 K线
#   3. 北向资金 —— 仅作盘后/次要参考（2024 起盘中不再实时披露，常为 0）
_temp_cache = {"data": None, "ts": 0}
_temp_lock = threading.Lock()
TEMP_TTL = 60   # 温度缓存 60 秒


def _breadth_score(up: int, down: int, limit_up: int, limit_down: int,
                   total: int, avg_chg: float) -> float:
    """市场宽度得分（0~100）。涨跌比=1 时为 50，普涨→高，普跌→低。"""
    ratio = up / max(down, 1)
    # ratio=1→50；ratio→∞→95；ratio→0→5（用 (r-1)/(r+1) 平滑映射）
    s = 50 + 45 * (ratio - 1) / (ratio + 1)
    # 全市场平均涨幅微调（±15 封顶）
    s += max(-15, min(15, avg_chg * 6))
    # 涨停/跌停净数量占比微调（极端情绪，±8 封顶）
    if total:
        s += max(-8, min(8, (limit_up - limit_down) / total * 100))
    return max(0, min(100, s))


def _index_trend_score(klines: list):
    """
    大盘趋势得分（0~100）：上证综指 vs MA20/MA60 + 5/20 日动量。
    返回 (score, info)。数据不足返回中性 50。
    """
    if not klines or len(klines) < 20:
        return 50.0, {}
    closes = [k["close"] for k in klines]
    price = closes[-1]
    ma20 = sum(closes[-20:]) / 20
    ma60 = (sum(closes[-60:]) / 60) if len(closes) >= 60 else None
    chg5 = (price - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
    chg20 = (price - closes[-21]) / closes[-21] * 100 if len(closes) >= 21 else 0

    s = 50.0
    s += 8 if price > ma20 else -8                      # 站上 MA20
    if ma60 is not None:
        s += 8 if price > ma60 else -8                  # 站上 MA60
    s += 6 if chg5 > 2 else (3 if chg5 > 0 else (-6 if chg5 < -2 else -3))
    s += 4 if chg20 > 3 else (2 if chg20 > 0 else (-4 if chg20 < -3 else -2))
    info = {
        "above_ma20": bool(price > ma20),
        "above_ma60": (bool(price > ma60)) if ma60 is not None else None,
        "chg5": round(chg5, 2),
        "chg20": round(chg20, 2),
    }
    return max(0, min(100, s)), info


def _level_advisory(t: float):
    """温度 → 等级 + 建议 + 建议买入线（冷市上调门槛）。"""
    if t >= 70:
        return "过热", "市场情绪亢奋、普涨，注意追高风险，可适当止盈锁定利润", 68
    if t >= 58:
        return "偏热", "多头主导，可按个股信号积极操作，但仍需控制仓位", 65
    if t >= 42:
        return "中性", "多空均衡，以个股自身信号为准", 65
    if t >= 28:
        return "偏冷", "空头占优，建议提高买入标准、轻仓观望为主", 70
    return "过冷", "市场恐慌普跌，宜观望；仅关注超跌反弹机会", 72


@router.get("/overview")
def market_overview(background_tasks: BackgroundTasks):
    """
    市场概览：返回大盘指数 + 全市场涨跌统计。

    BackgroundTasks 参数：FastAPI 特性，用于"返回响应后再异步执行任务"。
    这里用来在后台刷新缓存（不阻塞当前请求）。
    类比前端：相当于响应完后再 fire-and-forget 一个 fetch。
    """
    result = {"indices": [], "stats": {}}

    # ── 第一部分：主要指数实时数据 ──
    try:
        from app.tencent import _fetch_tencent
        # 一次性请求 6 个指数（用逗号拼接代码）
        codes_str = ",".join(f"{p}{c}" for p, c, _ in MAIN_INDICES)
        data = _fetch_tencent(codes_str)
        for prefix, code, name in MAIN_INDICES:
            qt_code = f"{prefix}{code}"
            info = data.get(qt_code)
            if info and info["price"] > 0:
                result["indices"].append({
                    "name": name,
                    "code": code,
                    "price": info["price"],
                    "change_pct": info["change_pct"],   # 涨跌幅
                    "change_amt": info["change_amt"],   # 涨跌点数
                    "volume": info["volume"],
                    "amount": info["amount"],
                })
    except Exception as e:
        print(f"指数数据失败: {e}")

    # ── 第二部分：全市场涨跌统计（从内存缓存读取）──
    stocks = _cache.get("stocks", {})
    if stocks:
        total = len(stocks)
        # 生成器表达式 + sum()：统计涨/跌/平的家数
        up = sum(1 for s in stocks.values() if s["change_pct"] > 0)     # 上涨家数
        down = sum(1 for s in stocks.values() if s["change_pct"] < 0)   # 下跌家数
        flat = total - up - down                                        # 平盘家数
        # 涨停（涨幅≥9.9%）/ 跌停（跌幅≤-9.9%）。注：科创板/创业板涨跌幅限制是 20%，这里用 9.9 是近似
        limit_up = sum(1 for s in stocks.values() if s["change_pct"] >= 9.9)
        limit_down = sum(1 for s in stocks.values() if s["change_pct"] <= -9.9)
        # 所有股票的涨跌幅列表，用于算平均/中位数
        changes = [s["change_pct"] for s in stocks.values()]
        result["stats"] = {
            "total": total,
            "up_count": up,
            "down_count": down,
            "flat_count": flat,
            "limit_up": limit_up,       # 涨停家数
            "limit_down": limit_down,   # 跌停家数
            "avg_change_pct": round(sum(changes) / len(changes), 2) if changes else 0,         # 平均涨跌幅
            "median_change_pct": round(sorted(changes)[len(changes) // 2], 2) if changes else 0,  # 中位数
            "total_amount": round(sum(s["amount"] for s in stocks.values()), 2),   # 总成交额
        }

    # ── 第三部分：仅盘中才触发后台刷新（盘后/周末数据静态，无需重拉）──
    # A 股数据仅盘中有时效性：盘后/周末启动时已从收盘快照恢复缓存（见 main.py），
    # 这里不再触发全量扫描，避免无意义的 2-4 分钟等待。
    if _is_trading_hours() and (not stocks or datetime.now().timestamp() - _cache.get("last_update", 0) > 120):
        background_tasks.add_task(refresh_all_stocks)   # 非阻塞，立即返回响应

    return result


@router.get("/temperature")
def market_temperature():
    """
    市场环境温度（0~100）：独立的「大盘环境」信号，用于和个股评分并列参考。

    组成：市场宽度(全市场涨跌) + 大盘趋势(上证综指) + 北向资金(次要/盘后)。
    返回：temperature、level(过冷/偏冷/中性/偏热/过热)、advisory(一句话建议)、
          buy_threshold(建议买入线，冷市上调)、breadth、index、northbound。

    说明：本接口【不改个股评分】，只量化市场环境。同步端点（含网络抓取），
    FastAPI 自动放线程池，不阻塞事件循环；结果缓存 60 秒。
    """
    # 命中缓存直接返回
    now = time.time()
    with _temp_lock:
        c = _temp_cache["data"]
        if c and now - _temp_cache["ts"] < TEMP_TTL:
            return c

    stocks = _cache.get("stocks", {})
    if not stocks:
        return {"temperature": None, "level": "加载中",
                "advisory": "行情数据加载中，请稍候", "cache_status": "loading"}

    # ── 1. 市场宽度（全市场涨跌）──
    total = len(stocks)
    up = sum(1 for s in stocks.values() if s.get("change_pct", 0) > 0)
    down = sum(1 for s in stocks.values() if s.get("change_pct", 0) < 0)
    limit_up = sum(1 for s in stocks.values() if s.get("change_pct", 0) >= 9.9)
    limit_down = sum(1 for s in stocks.values() if s.get("change_pct", 0) <= -9.9)
    chgs = [s.get("change_pct", 0) for s in stocks.values()]
    avg_chg = sum(chgs) / len(chgs) if chgs else 0
    breadth = _breadth_score(up, down, limit_up, limit_down, total, avg_chg)

    # ── 2. 大盘趋势（上证综指 vs MA + 动量）──
    try:
        klines = get_kline("000001", period="day", count=120)
        idx_score, idx_info = _index_trend_score(klines)
    except Exception as e:
        print(f"[temperature] 大盘趋势计算失败: {e}")
        idx_score, idx_info = 50.0, {}

    # ── 3. 北向资金（次要；2024 起盘中常为 0，仅盘后有意义）──
    nb_net = None
    try:
        from app.eastmoney import get_northbound
        nb = get_northbound()
        if nb and nb.get("total_net"):
            nb_net = nb["total_net"]
    except Exception:
        pass

    # 加权合成：有北向数据时计入（0.1），否则把权重补给宽度/趋势
    if nb_net is not None:
        nb_score = 50 + max(-20, min(20, nb_net / 1e8 * 0.4))   # ±100亿 → ±20
        temperature = breadth * 0.5 + idx_score * 0.4 + nb_score * 0.1
        nb_available = True
    else:
        temperature = breadth * 0.55 + idx_score * 0.45
        nb_available = False

    temperature = round(temperature, 1)
    level, advisory, buy_threshold = _level_advisory(temperature)

    result = {
        "temperature": temperature,
        "level": level,
        "advisory": advisory,
        "buy_threshold": buy_threshold,
        "breadth": {
            "up": up, "down": down,
            "limit_up": limit_up, "limit_down": limit_down,
            "ratio": round(up / max(down, 1), 2),
            "avg_change_pct": round(avg_chg, 2),
        },
        "index": idx_info,
        "northbound_net": nb_net,
        "northbound_available": nb_available,
        "cache_status": "ready",
    }
    with _temp_lock:
        _temp_cache["data"] = result
        _temp_cache["ts"] = now
    return result


@router.get("/realtime")
def market_realtime(
    page: int = 1, size: int = 50,
    sort_by: str = "change_pct", order: str = "desc"
):
    """
    全A股实时行情分页接口。

    参数（Query 参数，前端 URL 上拼接）：
      page:    页码，从 1 开始
      size:    每页条数
      sort_by: 排序字段（支持中文别名，如 "涨跌幅"）
      order:   "desc"降序 / "asc"升序

    响应示例：
      { data: [...], total: 4500, page: 1, size: 50, cache_status: "ready" }
    """
    stocks = _cache.get("stocks", {})
    if not stocks:
        # 缓存还没准备好（首次启动），返回空 + loading 状态，前端可据此显示加载中
        return {"data": [], "total": 0, "page": page, "size": size, "cache_status": "loading"}

    stock_list = list(stocks.values())

    # 排序字段中英文映射（前端可能传中文也可能传英文）
    sort_map = {
        "change_pct": "change_pct", "涨跌幅": "change_pct",
        "amount": "amount", "成交额": "amount",
        "turnover_rate": "turnover_rate", "换手率": "turnover_rate",
        "amplitude": "amplitude", "振幅": "amplitude",
        "price": "price", "最新价": "price",
        "volume": "volume", "成交量": "volume",
    }
    sort_key = sort_map.get(sort_by, "change_pct")   # 未知字段默认按涨跌幅
    reverse = order != "asc"                          # 默认降序（高的在前）
    # 按指定字段排序；x.get(sort_key) or 0 防止 None 报错
    stock_list.sort(key=lambda x: x.get(sort_key) or 0, reverse=reverse)

    total = len(stock_list)
    start = (page - 1) * size
    # 列表切片：取当前页的数据。类比 JS 的 arr.slice(start, start+size)
    page_data = stock_list[start:start + size]

    return {
        "data": page_data,
        "total": total,
        "page": page,
        "size": size,
        "cache_status": "ready",
    }


@router.get("/index-kline/{symbol}")
def index_kline(symbol: str, period: str = "day"):
    """
    大盘指数 K线。
    symbol: 指数代码如 "000001"（上证）。路径参数用 {symbol} 占位。
    """
    return get_kline(symbol, period=period, count=180)


@router.get("/refresh-status")
def refresh_status():
    """查询缓存刷新状态（前端轮询这个接口判断数据是否就绪）"""
    stocks = _cache.get("stocks", {})
    last = _cache.get("last_update", 0)
    return {
        "stock_count": len(stocks),
        # 时间戳转可读时间；last=0（未刷新）时显示"未刷新"
        "last_update": datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M:%S") if last else "未刷新",
        "total_codes": len(_ALL_CODES),
    }


@router.get("/trigger-refresh")
def trigger_refresh(background_tasks: BackgroundTasks):
    """手动触发缓存刷新（force=True，忽略 60 秒冷却）"""
    background_tasks.add_task(refresh_all_stocks, force=True)
    return {"status": "refreshing"}


# ══════════════════════════════════════════════════════════════════════════
#  情绪快照 v0（GET /api/market/emotion，2026-09-25 交易方法论落地计划 A3）
#  内容：涨停/跌停家数、昨日涨停股今日表现（赚钱效应）、连板高度（近似口径）
#  数据：全市场行情内存缓存（零额外请求）+ backtest_prices 近 8 个交易日收盘
#        （**每日一次**批量查询后进程内缓存，~32k 行一次性，egress 可控）
#  口径注：连板/涨停判定用 ≥9.5% 近似（10cm 主板口径，20cm/ST 未细分），
#          zzshare 官方口径接入后替换（Phase B1）。
# ══════════════════════════════════════════════════════════════════════════
_EMOTION_CLOSES = {"date": None, "closes": None}   # {code: {date: close}} 近 8 个交易日
_EMOTION_VERDICT_TTL = 120
_emotion_light = {"ts": 0.0, "val": None}


def _num_or_none(v):
    """安全转 float；**转不动就返回 None**（而不是 0）。

    ★ 2026-09-25（emotion 二次 500）：为什么必须区分 `None` 与 0 ——
      · `None` ⇒ 调用方走"**数据缺失，跳过**"（如 `if yc:`）
      · `0`    ⇒ 会被当成**真实数值**参与除法/比较 ⇒ 除零、或把"没有数据"误判成
                 "涨跌幅 0%"（脏数据伪装成有效值，比崩掉更难发现）。
    兼容 PG 的 TEXT 列、东财风格的 `'-'`/`''`、以及 `None`。
    """
    try:
        if v is None or v == "" or v == "-":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _emotion_closes_map():
    """近 8 个交易日全市场收盘（每日一次批量查询，进程内缓存）。"""
    from app.flash import rules as _rules
    today = _rules.beijing_now().strftime("%Y-%m-%d")
    if _EMOTION_CLOSES["date"] == today and _EMOTION_CLOSES["closes"] is not None:
        return _EMOTION_CLOSES["closes"]
    closes: Dict[str, Dict[str, float]] = {}
    # ★ 2026-09-25：rows 必须**预置** —— 原先只在 `if _max_d:` 分支内赋值，一旦取不到
    #   MAX(date)（空表/查询异常），下面的 `for r in rows or []` 会抛 UnboundLocalError；
    #   该异常被下方 except 吞掉（只 print）⇒ closes 为空 ⇒ 进而让 market_emotion 的
    #   `dates_all[-1]` 空列表索引崩成 500。两处都要修（这里是上游）。
    rows = []
    try:
        from app.database import db
        # date 列是 TEXT：先取最新交易日，再用 ISO 字符串比较取近 9 天
        _mx = db.fetch_one("SELECT MAX(date) AS d FROM backtest_prices")
        _max_d = str((_mx or {}).get("d") or "")
        if _max_d:
            _start = (datetime.strptime(_max_d, "%Y-%m-%d")
                      - timedelta(days=9)).strftime("%Y-%m-%d")
            rows = db.fetch(
                "SELECT code, date, close FROM backtest_prices "
                "WHERE date >= %s ORDER BY code, date LIMIT 60000", (_start,))
        for r in rows or []:
            # ★ 2026-09-25：逐行安全转换 —— 原先 `float(r.get("close") or 0)` 若撞上一个
            #   坏值（`'-'`/空串）会抛异常并被**外层 except 整批吞掉** ⇒ `closes` 只填到
            #   坏行就静默中断（缺数据不是崩，最容易被误当成"就是这样"）。
            #   现在：坏行只**跳过该行**（不填 0，免得被当成真实收盘价），其余继续。
            _c = _num_or_none(r.get("close"))
            if _c is None:
                continue
            closes.setdefault(str(r.get("code")), {})[str(r.get("date"))] = _c
    except Exception as e:
        print(f"[market] emotion closes load failed: {e}")
    _EMOTION_CLOSES.update({"date": today, "closes": closes})
    return closes


# ── ★ 2026-09-25：情绪快照每日落库（用户需求「成功返回了就入库」）────────────
#   目的：① 复盘/回放能看到历史情绪；② 不再只有当日值、历史不可追。
#   ⚠️ 为什么"每天只写一次"：market_emotion() 自带 120s 判读缓存 ⇒ 若每次算成都写库，
#      一天会写几百次（写放大 + 产生 dead tuple）。用进程内标记 + date 主键 upsert 兜底。
#   ⚠️ 为什么只在**交易日**写：休市日返回的是"最近交易日快照"（trading_day=False），
#      写进去会把真实交易日那天的数据覆盖成同一份 ⇒ 破坏历史口径（用户已在界面看到
#      "休市日·显示最近交易日数据"标注）。
_EMOTION_SAVED = {"date": None}
_EMOTION_TABLE_READY = False
_EMOTION_KEEP_DAYS = 400          # 保留约一年半够回放；同时防无限增长（同 db_retention 思路）


def _ensure_emotion_table() -> None:
    """建表（幂等）。★ 数值列一律 TEXT —— SQLite/PostgreSQL 双库零风险（同 memory_probe 做法）。"""
    global _EMOTION_TABLE_READY
    if _EMOTION_TABLE_READY:
        return
    from app.database import db
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_emotion_daily (
            date TEXT PRIMARY KEY,
            as_of TEXT,
            trading_day TEXT,
            up TEXT, down TEXT,
            limit_up TEXT, limit_down TEXT,
            prev_limit_count TEXT, prev_limit_today_pct TEXT,
            max_streak TEXT, leader TEXT, leader_name TEXT,
            verdict TEXT,
            created_at TEXT
        )
    """)
    _EMOTION_TABLE_READY = True


def _save_emotion_daily(val: Dict) -> None:
    """当天首次算成时落库（每日一次）。失败静默 —— 看护类写入绝不反噬接口。"""
    try:
        if not val.get("trading_day"):
            return                       # 休市日的值是"最近交易日回放"，不写（见上方注释）
        day = _bj_now().strftime("%Y-%m-%d")
        if _EMOTION_SAVED["date"] == day:
            return                       # 本进程今天已写过
        from app.database import db
        _ensure_emotion_table()
        db.upsert("market_emotion_daily", {
            "date": day, "as_of": val.get("as_of"),
            "trading_day": str(val.get("trading_day")),
            "up": str(val.get("up")), "down": str(val.get("down")),
            "limit_up": str(val.get("limit_up")), "limit_down": str(val.get("limit_down")),
            "prev_limit_count": str(val.get("prev_limit_count")),
            "prev_limit_today_pct": str(val.get("prev_limit_today_pct")),
            "max_streak": str(val.get("max_streak")),
            "leader": str(val.get("leader")), "leader_name": str(val.get("leader_name")),
            "verdict": val.get("verdict"),
            "created_at": _bj_now().isoformat(),
        }, conflict_columns=["date"])
        _EMOTION_SAVED["date"] = day
        try:                             # 保留期清理（与写入同频，一天一次）
            cutoff = (_bj_now() - timedelta(days=_EMOTION_KEEP_DAYS)).strftime("%Y-%m-%d")
            db.execute("DELETE FROM market_emotion_daily WHERE date < %s", (cutoff,))
        except Exception:
            pass
        print(f"[market] emotion 快照已落库 {day} verdict={val.get('verdict')}")
    except Exception as e:
        print(f"[market] emotion 落库失败（不影响接口）: {e}")


@router.get("/emotion")
def market_emotion():
    """情绪快照 v0：涨停/跌停家数 + 昨日涨停赚钱效应 + 连板高度近似 + 三档判读。

    判读阈值（v0 经验值，B1 官方口径接入后校准）：
      亢奋：涨停 ≥60 家或最高连板 ≥6；冰点：涨停 <20 且赚钱效应 <0；其余=分歧/常态。
    """
    import time as _t
    now = _t.time()
    if _emotion_light["val"] and now - _emotion_light["ts"] < _EMOTION_VERDICT_TTL:
        return _emotion_light["val"]

    stocks = _cache.get("stocks") or {}
    # ★★ 2026-09-25（用户："只有 A 股，真需要『今日』展示吗"）：
    #   **"今天涨停 0 家"** 与 **"今天没有行情数据"** 是两件完全不同的事，而原先两者都输出
    #   **0** ⇒ 休市日界面显示"涨停 0 / 跌停 0"，被读成"今天一只都没涨停"，实际是"今天没开盘"。
    #   ⇒ 无行情时这些"今日"字段一律返回 **None**（缺失），由前端显示「—」，
    #     并用 `data_date` 说明**价格数据截至哪天**。
    #   ★ 与本文件 `_num_or_none` 同一条纪律：**缺失就报缺失，不要伪装成 0**。
    has_live = bool(stocks)          # 行情缓存非空 ⇒ 有当日（或最近交易日）报价
    up = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) > 0) if has_live else None
    down = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) < 0) if has_live else None
    limit_up_codes = ([c for c, s in stocks.items() if (s.get("change_pct") or 0) >= 9.5]
                      if has_live else [])
    limit_down = (sum(1 for s in stocks.values() if (s.get("change_pct") or 0) <= -9.5)
                  if has_live else None)

    closes = _emotion_closes_map()
    dates_all = sorted({d for m in closes.values() for d in m})
    d_prev = dates_all[-2] if len(dates_all) >= 2 else None
    d_prev2 = dates_all[-3] if len(dates_all) >= 3 else None

    # 昨日涨停名单（昨日涨幅 ≥9.5%）与其今日表现（赚钱效应）
    prev_limit, perf = [], []
    if d_prev:
        for code, m in closes.items():
            c_prev, c_prev2 = m.get(d_prev), (m.get(d_prev2) if d_prev2 else None)
            if not c_prev or (c_prev2 and c_prev / c_prev2 - 1 < 0.095):
                continue
            if not c_prev2:
                continue
            prev_limit.append(code)
            q = stocks.get(code) or {}
            # ★ 2026-09-25：`q["price"]` 同样可能不是数字（行情源异常/字符串）⇒ 直接除会 TypeError
            _px = _num_or_none(q.get("price"))
            if _px:                       # 非数字或缺失 ⇒ 跳过该股（不参与赚钱效应均值）
                perf.append((_px / c_prev - 1) * 100)
    money = sum(perf) / len(perf) if perf else None

    # 连板高度（近似）：今日涨停股按近 8 日收盘连涨判定
    # ★ 2026-09-25：连板高度由"今日涨停名单"推导 ⇒ 无行情时它不是 0 而是**无意义** ⇒ None
    max_streak, leader = (0, None) if has_live else (None, None)
    # ★ 2026-09-25（500 事故）：`dates_all` 可能为空 —— `closes` 读取失败时上游异常被吞
    #   （见 _emotion_closes_map），此时原代码的 `dates_all[-1]` 对空列表抛 IndexError
    #   ⇒ 只要当天有涨停股（limit_up_codes 非空）接口必 500。整段用 if dates_all 保护。
    if dates_all:
        for code in limit_up_codes:
            m = closes.get(code) or {}
            streak = 1
            seq = sorted(m.items())
            for i in range(len(seq) - 1, 0, -1):
                if seq[i][0] > (dates_all[-1] or ""):
                    continue
                if seq[i - 1][1] and seq[i][1] / seq[i - 1][1] - 1 >= 0.095:
                    streak += 1
                else:
                    break
            if streak > max_streak:
                max_streak, leader = streak, code

    # ★ A4 竞价看板：昨日涨停股今日高开幅度（9:25 竞价定稿后有效）
    gaps = []
    for code in limit_up_codes:
        q = stocks.get(code) or {}
        o = _num_or_none(q.get("open"))
        # ★★ 2026-09-25（用户贴出 traceback，line 532）：`yc` 取自 `closes`，而 `closes` 只覆盖
        #   `backtest_prices` 回填过的股票；`limit_up_codes` 却来自**全市场行情缓存** ⇒
        #   **该股不在 closes 里时 `.get()` 返回 None** ⇒ 原 `if o > 0 and yc > 0` 直接
        #   `None > 0` ⇒ TypeError ⇒ 整个接口 500。
        #   ⚠️ 之所以平时不崩：多数交易日两者恰好能对上（或 `d_prev` 为 None 时走 `else 0`）。
        #   ⇒ 缺数据时**跳过该股**（`continue`），而不是填 0 参与除法（`o / 0` 会再炸一次）。
        yc = _num_or_none((closes.get(code) or {}).get(d_prev)) if d_prev else None
        if o and yc and o > 0 and yc > 0:
            gaps.append({"code": code, "name": (q.get("name") or code),
                         "gap_pct": round((o / yc - 1) * 100, 2)})
    gaps.sort(key=lambda x: x["gap_pct"], reverse=True)
    # ★ 2026-09-25 扩展（用户："竞价看板…（**竞价额、竞价涨幅榜**、昨日强势股溢价）"）：
    #   上面 `gaps` 只算「**昨日涨停股**」的高开（池子小、偏接力视角）⇒ 这里补**全市场**视角：
    #   全市场高开榜 / 低开榜 + 高开低开家数 + 两市累计成交额。
    #   ⚠️ **语义随时段变**：`amount_wan` 在竞价时段是竞价额，盘中/盘后就是**当日累计**成交额
    #      ⇒ 故字段命名为 `total_amount_wan` 并附 `amount_note`，**不叫"竞价额"**（避免误导）。
    market_gaps, up_open, down_open = [], 0, 0
    total_amount_wan = 0.0
    for _c, _s in (stocks or {}).items():
        _amt = _num_or_none(_s.get("amount_wan")) or 0.0
        total_amount_wan += _amt
        _pc = _num_or_none(_s.get("prev_close"))
        _op = _num_or_none(_s.get("open"))
        if not _pc or not _op or _pc <= 0 or _op <= 0:
            continue                      # ★ 缺数据就跳过（不填 0 参与统计）
        _g = (_op / _pc - 1) * 100
        if _g > 0:
            up_open += 1
        elif _g < 0:
            down_open += 1
        market_gaps.append({"code": _c, "name": _s.get("name") or _c,
                            "gap_pct": round(_g, 2), "amount_wan": round(_amt)})
    market_gaps.sort(key=lambda x: -x["gap_pct"])
    auction = {"count": len(gaps),
               "avg_gap": round(sum(g["gap_pct"] for g in gaps) / len(gaps), 2) if gaps else None,
               "top": gaps[:5],
               # ── 全市场视角（2026-09-25 新增）──
               "market_top": market_gaps[:20],
               "market_bottom": list(reversed(market_gaps[-20:])) if market_gaps else [],
               "market_count": len(market_gaps),
               "up_open": up_open, "down_open": down_open,
               "avg_gap_all": (round(sum(x["gap_pct"] for x in market_gaps) / len(market_gaps), 2)
                               if market_gaps else None),
               "total_amount_wan": (round(total_amount_wan) if total_amount_wan else None),
               "amount_note": "成交额为截至 as_of 的**累计值**（9:15-9:25 期间即竞价额）"}

    if not has_live:
        verdict = None       # ★ 2026-09-25：无行情 ⇒ 不判读（休市显示"分歧/常态"同样是误导）
    elif limit_up_codes.__len__() >= 60 or max_streak >= 6:
        verdict = "亢奋"
    elif len(limit_up_codes) < 20 and (money is not None and money < 0):
        verdict = "冰点"
    else:
        verdict = "分歧/常态"

    from app.flash.rules import is_trading_day as _is_tday
    val = {
        "as_of": _bj_now().strftime("%Y-%m-%d %H:%M"),
        # ★ 2026-09-25：休市时行情缓存可能是"最近交易日的静态数据" ⇒ 前端需知道
        #   "这不是当日实时"以免误读（与下面的 has_live / data_date 一起判断）。
        "trading_day": _is_tday(),
        # ★ `has_live=False` ⇒ 下面所有"今日"计数为 None（缺失，非 0）——见函数上方注释
        "has_live": has_live,
        # ★ 价格数据截至哪一天（休市时前端用它解释"—"的含义，而不是让人猜）
        "data_date": (dates_all[-1] if dates_all else None),
        "up": up, "down": down,
        "limit_up": (len(limit_up_codes) if has_live else None), "limit_down": limit_down,
        "prev_limit_count": len(prev_limit),
        "prev_limit_today_pct": round(money, 2) if money is not None else None,
        "max_streak": max_streak, "leader": leader,
        "auction": auction,
        "leader_name": (stocks.get(leader) or {}).get("name") if leader else None,
        "verdict": verdict,
        "note": "涨停/连板为 >=9.5% 近似口径（10cm 主板），官方口径待 zzshare 接入",
    }
    _emotion_light.update(ts=now, val=val)
    _save_emotion_daily(val)         # ★ 2026-09-25：当天首次算成时落库（每日一次，见其注释）
    return val


# ══════════════════════════════════════════════════════════════════════════
#  涨停复盘（GET /api/market/limit-review?date=，2026-09-25 Phase B2）
#  包装 zzshare review_uplimit_hot_step（连板梯队）+ uplimit_stocks（涨停清单）。
#  匿名可用但数据范围受限；失败返回空结构（工作台占位展示）。
# ══════════════════════════════════════════════════════════════════════════

@router.get("/limit-review")
def market_limit_review(date: str = None):
    from app.flash import rules as _rules
    d = date or _bj_now().strftime("%Y-%m-%d")
    out = {"date": d, "steps": [], "stocks": []}
    try:
        from app.zzshare_client import get_api
        api = get_api()
        df = api.review_uplimit_hot_step(date1=d)
        if df is not None and hasattr(df, "to_dict"):
            out["steps"] = df.to_dict("records")[:20]
        df2 = api.uplimit_stocks(date1=d)
        if df2 is not None and hasattr(df2, "to_dict"):
            out["stocks"] = df2.to_dict("records")[:50]
    except Exception as e:
        print(f"[market] limit-review failed (degrade to empty): {e}")
    return out
