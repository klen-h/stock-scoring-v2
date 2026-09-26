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

from typing import Dict, Optional

from fastapi import APIRouter, Query, BackgroundTasks
from datetime import datetime, timedelta, timezone

def _bj_now():
    """北京时间（本文件局部助手；全项目时间源约定见 app/flash/rules.py）"""
    return datetime.now(timezone(timedelta(hours=8)))
import gzip
import json
import os
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


# ══════════════════════════════════════════════════════════════════════════
#  大小盘风格 / 黄白线背离 —— 2026-09-25（用户需求 1：看盘序的"定方向"层）
# ══════════════════════════════════════════════════════════════════════════
# 【回答什么问题】交易方法论落地计划 C5 原话：
#   "盘中：9:30-10:00 定方向（黄白线/权重护盘）| 大市值组 vs 全市场涨幅对比 [可算]"
#   翻译成人话：**今天是权重在拉指数（个股不跟），还是个股自己的行情（题材扩散）？**
#   这决定了"看指数做个股"还是"看个股做个股"——是盘中最先要分清的一件事。
#
# 【口径 —— 关键在"加权 vs 等权"】
#   · **白线（加权）** = 按流通市值加权平均涨幅。权重股（银行/白酒/两桶油）主导，
#     等价于「指数涨幅」——真实指数涨跌就是被这些票决定的。
#   · **黄线（等权）**  = 算术平均涨幅 ⇒ 每只票一票，即 `stats.avg_change_pct`。
#   · **背离 spread = 加权 − 等权**：
#        spread > 0 ⇒ 权重强于个股（白线在黄线之上）⇒ **指数好看，多数个股没跟上**
#        spread < 0 ⇒ 个股强于权重（黄线在上）⇒ **题材活跃，赚钱效应在个股**
#   再叠加「大/中/小三组等权涨幅差」看**风格偏离**（big − small）。
#
# ⚠️⚠️ 三条诚实标注（都写进返回值的 note，不让人误读）：
#   ① 加权用的是**流通市值**（腾讯 `float_cap`），而真实指数权重是**自由流通市值 +
#      分级靠档 + 新股计入规则** ⇒ 这是**近似**，不等于精确复现上证指数
#      （故返回值同时带上真实指数涨幅供对照，两者不一致时以指数为准）。
#   ② `change_pct` **未剔除停牌股**（停牌记 0），此处与 `stats.avg_change_pct`
#      **口径完全一致** —— ★ 这是必须的：若两处口径不同，页面上会出现两个不同的
#      "平均涨幅"，用户会立刻不信任整个页面。
#   ③ 非交易时段缓存是**上一交易日收盘快照**（见 main.py 收盘恢复）⇒ 带 `as_of`
#      标注时刻，不假装是实时数据（"权重护盘"在盘后看就是在复盘昨天）。
_SIZE_BIG_N = 100          # "大市值组"= 流通市值前 N（≈权重股骨架；全市场约 4000+ 只）
_STYLE_BAND = 0.5          # 大小盘风格判定带宽（%）：|大−小| 未超此值视为"风格均衡"


def _grp(label: str, key: str, sub: list) -> Optional[Dict]:
    """一组的等权统计（avg / 上涨占比 / 家数）。空组返回 None。"""
    if not sub:
        return None
    vals = [v for _, v in sub]
    return {
        "key": key, "label": label, "n": len(sub),
        "avg": round(sum(vals) / len(vals), 2),
        # 上涨占比：比 avg 更抗极端值（一只涨停能显著抬高 avg，但只贡献 1/N 的占比）
        "up_ratio": round(sum(1 for v in vals if v > 0) / len(vals) * 100, 1),
    }


def _size_style(stocks: Dict, index_pct: Optional[float]) -> Dict:
    """大小盘风格 + 黄白线背离。**纯内存计算、零网络**；数据不足返回 available=False。

    ⚠️ 本函数被 `market_overview` 每个请求调用一次（该接口前端 30s 轮询）
       ⇒ 只遍历一次内存字典（~4000 行）+ 一次排序，无网络、无 DB，开销可忽略。
    """
    try:
        rows = []
        for s in stocks.values():
            chg = s.get("change_pct")
            if chg is None:
                continue
            cap = float(s.get("float_cap") or s.get("market_cap") or 0)
            rows.append((cap, float(chg)))
        # 太少 ⇒ 缓存尚未就绪（首次启动全量扫描需 2-4 分钟）：宁可不显示，也不给错结论
        if len(rows) < 200:
            return {"available": False, "note": "行情缓存未就绪（首次全量扫描需 2-4 分钟）"}
        total_cap = sum(cap for cap, _ in rows)
        if total_cap <= 0:
            return {"available": False, "note": "市值字段缺失"}

        equal = sum(chg for _, chg in rows) / len(rows)                  # 黄线（等权）
        weighted = sum(cap * chg for cap, chg in rows) / total_cap       # 白线（流通市值加权）
        spread = round(weighted - equal, 2)

        rows.sort(key=lambda x: -x[0])                                   # 市值降序
        n = len(rows)
        cut_mid = max(_SIZE_BIG_N, n // 2)     # ★ max 兜底：小市场（n<200）时中/小盘不重叠
        g_big = _grp("大市值", "big", rows[:_SIZE_BIG_N])
        g_mid = _grp("中市值", "mid", rows[_SIZE_BIG_N:cut_mid])
        g_small = _grp("小市值", "small", rows[cut_mid:])

        diff = round(g_big["avg"] - g_small["avg"], 2) if (g_big and g_small) else None
        if diff is None:
            verdict, label = "unknown", "数据不足"
        elif diff >= _STYLE_BAND:
            verdict, label = "weight_support", "权重护盘"
        elif diff <= -_STYLE_BAND:
            verdict, label = "small_active", "小盘活跃"
        else:
            verdict, label = "balanced", "风格均衡"

        # ── 人话解释（⚠️ 不能含 Markdown 标记：前端是纯文本插值，星号会原样显示）──
        notes = []
        if verdict == "weight_support":
            notes.append("大盘明显强于小盘：指数靠权重撑着，个股没跟上"
                         "——别被指数红盘误导，先看小票是否补涨")
        elif verdict == "small_active":
            notes.append("小盘强于大盘：题材在扩散，赚钱效应在个股"
                         "——个股信号的可信度相对更高")
        else:
            notes.append("大小盘基本同步，无明显风格偏离")
        if spread >= 0.8:
            notes.append(f"指数口径（加权 {weighted:+.2f}%）明显强于等权体感（{equal:+.2f}%）"
                         "——权重在拉抬指数，多数个股体感偏弱")
        elif spread <= -0.8:
            notes.append(f"指数口径（加权 {weighted:+.2f}%）弱于等权（{equal:+.2f}%）"
                         "——权重拖累指数，而个股相对活跃")
        # ⚠️ 必须用 `data_ts`（数据自身时刻）而**不是** `last_update`（缓存填充时刻）：
        #   休市日恢复 09-24 收盘快照时，`last_update` 被刻意设成"现在"
        #   （见 tencent._cache 结构注释）⇒ 用它会把昨天的数据标成今天，**谎报新鲜度**。
        data_ts = _cache.get("data_ts") or 0
        return {
            "available": True, "verdict": verdict, "label": label,
            "groups": [g for g in (g_big, g_mid, g_small) if g],
            "weighted": round(weighted, 2),      # 白线（流通市值加权）
            "equal": round(equal, 2),            # 黄线（等权）
            "spread": spread,                    # 黄白线背离（加权 − 等权）
            "big_minus_small": diff,             # 风格差（大市值 − 小市值）
            "index_pct": index_pct,              # 真实指数涨幅（对照用）
            "note": "；".join(notes),
            "as_of": (datetime.fromtimestamp(data_ts).strftime("%m-%d %H:%M")
                      if data_ts else None),
            # True = 数据来自收盘快照（盘后/周末）⇒ 前端应标注"上一交易日"，别当成实时
            "from_snapshot": bool(_cache.get("from_snapshot")),
        }
    except Exception as e:
        print(f"[market] size style failed: {e}")          # ASCII（铁律⑥）
        return {"available": False, "note": "计算失败"}


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
        # ★ 2026-09-25（用户需求 1）：大小盘风格 / 黄白线背离 —— 复用**同一份**内存缓存
        #   （零新增请求、零新增数据源）。看盘序"9:30-10:00 定方向"缺的那一层。
        #   真实指数涨幅取自上面已拉到的 indices（对照用，见 `_size_style` 注释①）。
        _idx_pct = next((i["change_pct"] for i in result["indices"]
                         if i["name"] == "上证指数"), None)
        result["style"] = _size_style(stocks, _idx_pct)
    else:
        result["style"] = {"available": False, "note": "行情缓存未就绪"}

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

# ★ 2026-09-25（egress 治理，探针实测驱动）：`_emotion_closes_map()` 的**本机 gzip 持久缓存**。
#   原先只有**进程内**缓存 ⇒ 每重启一个新进程就整批重读一次
#   （实测 46 分钟内 4 次 / 2.68 万行 / ≤2.15MB；本地 `run.py --reload` 改代码即重启 ⇒ 一天可几十次）。
#   指纹 = `backtest_prices` 的 `MAX(date)`（**单行**查询、几十字节；该表日更 ⇒ 每天失效一次）。
#   ⚠️ 与 l3 / flow 同款纪律：指纹取不到 ⇒ **不走缓存、直接回源**（正确性优先）。
_EMOTION_CLOSES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "emotion_closes.json.gz")
_EMOTION_CLOSES_DISK_TTL = 12 * 3600


def _closes_fingerprint():
    """`backtest_prices` 最新交易日（单行查询）。取不到返回 None（⇒ 调用方回源）。"""
    try:
        from app.database import db
        r = db.fetch_one("SELECT MAX(date) AS d FROM backtest_prices")
        return str((r or {}).get("d") or "") or None
    except Exception:
        return None


def _closes_disk_load(ver):
    """本机缓存（零 egress）。指纹不符 / 过期 / 异常 ⇒ None。"""
    if ver is None:
        return None
    try:
        if not os.path.exists(_EMOTION_CLOSES_PATH):
            return None
        with gzip.open(_EMOTION_CLOSES_PATH, "rt", encoding="utf-8") as f:
            obj = json.load(f)
        if str(obj.get("ver")) != str(ver):
            return None
        if time.time() - float(obj.get("ts") or 0) > _EMOTION_CLOSES_DISK_TTL:
            return None
        return obj.get("closes") or None
    except Exception:
        return None


def _closes_disk_save(ver, closes):
    """写本机缓存（原子替换）。空结果 / 指纹缺失不写（下次仍会重试）。"""
    if ver is None or not closes:
        return
    try:
        os.makedirs(os.path.dirname(_EMOTION_CLOSES_PATH), exist_ok=True)
        tmp = _EMOTION_CLOSES_PATH + ".tmp"
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump({"ver": ver, "ts": time.time(), "closes": closes}, f,
                      ensure_ascii=False)
        os.replace(tmp, _EMOTION_CLOSES_PATH)
    except Exception as e:
        print(f"[market] emotion closes 本机缓存写入失败: {e}")    # ASCII（铁律⑥）


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
    # ★ 本机持久缓存（跨进程 / 跨重启）：指纹没变就别再整批读一次（见上方常量注释）
    ver = _closes_fingerprint()
    disk = _closes_disk_load(ver)
    if disk is not None:
        print(f"[market] emotion closes 命中本机缓存（{len(disk)} 只）-> 零 Supabase 流量")
        _EMOTION_CLOSES.update({"date": today, "closes": disk})
        return disk
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
    _closes_disk_save(ver, closes)
    _EMOTION_CLOSES.update({"date": today, "closes": closes})
    return closes


# ── ★★ 2026-09-26：「昨涨停」口径的**日期锚**（用户提问推动，见 market_emotion 内注释）──
def _price_date() -> str:
    """当前**行情价格所属的交易日** P（YYYY-MM-DD）。

    【为什么需要它】`stocks`（内存行情）与 `closes`（`backtest_prices`，**日批回填**）
      的时点不同步，而"昨涨停表现"= `price / close(名单日)` ⇒ 必须先知道 `price` 是哪天的。
    【规则】交易日且已过 **9:15** ⇒ 今天（竞价/盘中/午休/收盘后价格都是今天的）；
      否则（<9:15 的盘前、周末、节假日）⇒ `latest_completed_trading_day()`
      —— 此时手里的价格本就是"最近已完成交易日"的收盘。
    【与 `latest_completed_trading_day()` 的区别】那个是"**已完成**日"（15:00 分界）：
      盘中它给昨天，而盘中价格是今天 ⇒ **两者不可互换**（详见下方 `d_prev` 注释）。
    """
    from app.flash import rules as _rules
    now = _rules.beijing_now()
    if _rules.is_trading_day(now) and (now.hour * 100 + now.minute) >= 915:
        return now.strftime("%Y-%m-%d")
    return _rules.latest_completed_trading_day(now)


def _prev_trading_day(day: str) -> Optional[str]:
    """`day` 的**前一个交易日**（跳过周末 + `HOLIDAYS`）。无则 None。

    ⚠️ 与 `_shift_days`（自然日近似）**不同** —— 这里是真交易日回退；
      循环上限 15 天足以跨过春节/国庆连休（与 `latest_completed_trading_day` 同策）。
    """
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from app.flash import rules as _rules
    try:
        d = _dt.strptime(str(day)[:10], "%Y-%m-%d") - _td(days=1)
    except (TypeError, ValueError):
        return None
    for _ in range(15):
        if _rules.is_trading_day(d):
            return d.strftime("%Y-%m-%d")
        d -= _td(days=1)
    return None


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

# ★ 2026-09-25：收盘后补记的「实际结果」列（P3 对账用）。列名集中一处 ⇒ 建表/加列/读取不漂移。
_EMOTION_CLOSE_COLS = ("close_as_of", "close_up", "close_down", "close_limit_up",
                       "close_limit_down", "close_max_streak",
                       "close_prev_limit_today_pct", "close_verdict")

# ★★ 2026-09-25（竞价段评估 #1）：竞价窗口（9:15-9:25）的快照字段。
#   【为什么必须落库】竞价数据**只在 9:15-9:25 这 10 分钟存在**（内存行情里的 open/amount），
#     不落库就**永久丢失** ⇒ ① 没有参照系：看不出"高开 47 家、平均 +0.8%"算强还是弱；
#     ② 永远无法回测"竞价强度 → 当日走势"（A4 竞价看板的校准依据）。
#     同库已有同款模式可照抄（`close_*` 双字段、`market_tail_snapshot` 尾盘基线）。
#   【与 close_* 的区别】`close_*` = 收盘实际；`auction_*` = **开盘前的定调**。
#     ⚠️ 语义边界：`auction_*` **只在 9:15-9:25 窗口内写**，且**允许窗口内重复覆盖**
#       （9:25 定稿值最重要）；窗口外一律不写 —— 9:25 后的 `amount` 已含连续竞价成交，
#       写成"竞价额"会失真（与后端 `amount_note` 的诚实标注同一条纪律）。
_EMOTION_AUCTION_COLS = ("auction_as_of", "auction_count", "auction_avg_gap",
                         "auction_up_open", "auction_down_open",
                         "auction_avg_gap_all", "auction_total_amount_wan")


def _ensure_emotion_table() -> None:
    """建表（幂等）+ **幂等加列**。★ 数值列一律 TEXT —— SQLite/PostgreSQL 双库零风险。"""
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
            created_at TEXT,
            close_as_of TEXT,
            close_up TEXT, close_down TEXT,
            close_limit_up TEXT, close_limit_down TEXT,
            close_max_streak TEXT, close_prev_limit_today_pct TEXT,
            close_verdict TEXT,
            auction_as_of TEXT,
            auction_count TEXT, auction_avg_gap TEXT,
            auction_up_open TEXT, auction_down_open TEXT,
            auction_avg_gap_all TEXT, auction_total_amount_wan TEXT
        )
    """)
    # ★ 2026-09-25：`CREATE TABLE IF NOT EXISTS` **不会**给已存在的表加列 ⇒ 老库需补 ALTER。
    #   逐列 `try/except` 忽略"列已存在"（双库的报错文案不同，故不匹配具体错误文本）。
    for _c in (_EMOTION_CLOSE_COLS + _EMOTION_AUCTION_COLS):
        try:
            db.execute(f"ALTER TABLE market_emotion_daily ADD COLUMN {_c} TEXT")
        except Exception:
            pass
    _EMOTION_TABLE_READY = True


def _load_emotion_daily(day: str) -> Dict:
    """读 `market_emotion_daily` 当天那行（无则返回 `{}`）。失败静默。"""
    try:
        _ensure_emotion_table()
        from app.database import db
        r = db.fetch_one("SELECT * FROM market_emotion_daily WHERE date = %s", (day,))
        return dict(r) if r else {}
    except Exception:
        return {}


def _emotion_daily_row(day: str, val: Dict, after_close: bool, old: Dict,
                       auction: bool = False) -> Dict:
    """把一次情绪快照组装成"整行"（**双库安全的写法**）。

    ⚠️⚠️ 为什么必须组装成**整行**：`db.upsert` 在 PG 下只更新传入的列，但在 **SQLite 分支
    实际是 `INSERT OR REPLACE` —— 整行替换，未提供的列会被清空**。只传 `close_*` 会在本地
    把主字段抹掉（而线上是好的）⇒ 这种"双库行为不一致"最难查。
    ⇒ 一律：**先读当天已有行 → 合并 → 写全字段**。

    三组字段（各自独立、互不覆盖）：
      · 主字段（盘前/首次算出）= 当日**预判**
      · `close_*`（收盘后算出）  = 当日**实际结果**（对账用）
      · `auction_*`（**仅 9:15-9:25 窗口**，`auction=True` 时写）= 开盘前**定调**
        ⇒ ⚠️ 竞价组**允许窗口内重复覆盖**（9:25 定稿值最重要），其余两组保持"写一次"语义。
    """

    def _s(v):
        return None if v is None else str(v)

    base = {
        "date": day, "as_of": old.get("as_of"),
        "trading_day": old.get("trading_day"),
        "up": old.get("up"), "down": old.get("down"),
        "limit_up": old.get("limit_up"), "limit_down": old.get("limit_down"),
        "prev_limit_count": old.get("prev_limit_count"),
        "prev_limit_today_pct": old.get("prev_limit_today_pct"),
        "max_streak": old.get("max_streak"),
        "leader": old.get("leader"), "leader_name": old.get("leader_name"),
        "verdict": old.get("verdict"),
        "created_at": old.get("created_at"),
        "close_as_of": old.get("close_as_of"),
        "close_up": old.get("close_up"), "close_down": old.get("close_down"),
        "close_limit_up": old.get("close_limit_up"),
        "close_limit_down": old.get("close_limit_down"),
        "close_max_streak": old.get("close_max_streak"),
        "close_prev_limit_today_pct": old.get("close_prev_limit_today_pct"),
        "close_verdict": old.get("close_verdict"),
        # ★ 竞价组（默认从已有行继承 —— 缺了这几行，SQLite 的整行替换会把竞价数据抹掉）
        "auction_as_of": old.get("auction_as_of"),
        "auction_count": old.get("auction_count"),
        "auction_avg_gap": old.get("auction_avg_gap"),
        "auction_up_open": old.get("auction_up_open"),
        "auction_down_open": old.get("auction_down_open"),
        "auction_avg_gap_all": old.get("auction_avg_gap_all"),
        "auction_total_amount_wan": old.get("auction_total_amount_wan"),
    }
    if base["created_at"] is None:
        base["created_at"] = _bj_now().isoformat()

    if auction:
        # 竞价窗口（9:15-9:25）⇒ 写 `auction_*`（**允许覆盖**：9:25 的定稿值才是最终口径）
        a = val.get("auction") or {}
        base.update({
            "auction_as_of": _s(val.get("as_of")),
            "auction_count": _s(a.get("count")),
            "auction_avg_gap": _s(a.get("avg_gap")),
            "auction_up_open": _s(a.get("up_open")),
            "auction_down_open": _s(a.get("down_open")),
            "auction_avg_gap_all": _s(a.get("avg_gap_all")),
            "auction_total_amount_wan": _s(a.get("total_amount_wan")),
        })
    elif after_close:
        # 收盘后 ⇒ 只补 `close_*`（**主字段保留盘前预判的原值**，这是对账的前提）
        base.update({
            "close_as_of": val.get("as_of"),
            "close_up": _s(val.get("up")), "close_down": _s(val.get("down")),
            "close_limit_up": _s(val.get("limit_up")),
            "close_limit_down": _s(val.get("limit_down")),
            "close_max_streak": _s(val.get("max_streak")),
            "close_prev_limit_today_pct": _s(val.get("prev_limit_today_pct")),
            "close_verdict": val.get("verdict"),
        })
    elif not old.get("verdict"):
        # 盘前/盘中且**尚无预判** ⇒ 记为首个"预判"（已存在则不动，保持"第一次算成"的口径）
        base.update({
            "as_of": val.get("as_of"),
            "trading_day": _s(val.get("trading_day")),
            "up": _s(val.get("up")), "down": _s(val.get("down")),
            "limit_up": _s(val.get("limit_up")), "limit_down": _s(val.get("limit_down")),
            "prev_limit_count": _s(val.get("prev_limit_count")),
            "prev_limit_today_pct": _s(val.get("prev_limit_today_pct")),
            "max_streak": _s(val.get("max_streak")),
            "leader": _s(val.get("leader")), "leader_name": _s(val.get("leader_name")),
            "verdict": val.get("verdict"),
        })
    return base


def _save_emotion_daily(val: Dict) -> None:
    """落库：**盘前/盘中**记「预判」，**收盘后**补记「实际结果」。失败静默。

    ★★ 2026-09-25 改造（用户需求 P3：「盘前预判 vs 实际走势」对账闭环）——
      【为什么改】原实现是"当天首次算成时写一次" ⇒ 一天只有一条 ⇒ **无法自对账**：
        收盘后想看"我盘前判的『分歧/常态』兑现了吗"，却**没有收盘那条可比**
        （主键是 date，同一天写第二次会覆盖第一次）。
      【怎么改】同一条记录里放两组字段，**纯新增列、老数据天然兼容**：
        · 主字段 `up/down/limit_up/.../verdict` = **盘前/盘中首次**算出的「预判」
        · `close_*` 字段                        = **收盘后**算出的「实际结果」
        ⚠️ 为什么不用"一天两条"：`date` 是主键，改主键=数据迁移（老库有风险），
           而加列是纯新增；`close_*` 为 NULL ⇒ 对账时**跳过该日**（不会误判成"预判错了"）。
      【时刻口径要诚实】「预判」= **首次算成的时刻**（可能是 09:10 盘前，也可能是用户
        中午才第一次打开页面）⇒ 对账输出里带 `as_of` 让人自己看时刻，别假装都是盘前。
      ⚠️ 仍只在**交易日**写：休市日返回的是"最近交易日回放"，写进去会覆盖真实交易日口径。
    """
    try:
        # ★★ 2026-09-26：补建表 —— 原先**只**在读取端（`emotion_review`）调 `_ensure_emotion_table()`。
        #   若写入先于任何读取发生 ⇒ `db.upsert` 撞"表不存在" ⇒ 异常被下面的 except **静默吞掉**
        #   （只 print 到日志）⇒ 该表可能一直空着且无人察觉（本轮审计正是先看到"0 行"）。
        #   对齐同类实现：`_save_tail_baseline()` 开头就调 `_ensure_tail_table()`。
        #   ⚠️ 本函数幂等（`_EMOTION_TABLE_READY` 缓存 + `CREATE TABLE IF NOT EXISTS`）⇒ 无额外开销。
        _ensure_emotion_table()
        if not val.get("trading_day"):
            return                       # 休市日的值是"最近交易日回放"，不写（见上方注释）
        day = _bj_now().strftime("%Y-%m-%d")
        now = _bj_now()
        hhmm = now.hour * 100 + now.minute
        after_close = (now.hour * 60 + now.minute) >= 900      # 15:00 之后
        from app.database import db
        old = _load_emotion_daily(day)
        # 幂等：盘前已记过预判就不再写；盘后同一时刻已记过就不再写（省掉无意义的写放大）
        # ★★ 2026-09-25（竞价段评估 #1）：**9:15-9:25 窗口单独落库竞价字段**。
        #   【为什么必须单独分支】主字段的幂等是"盘前写过就不再动"（保持"第一次算成"口径），
        #     而竞价数据**只在窗口内有效、且逐分钟变化**（9:25 定稿值最重要）⇒ 语义冲突。
        #     ⇒ 竞价组独立、窗口内**允许覆盖**（取最新）；窗口外**一律不写**
        #       （9:25 之后的 amount 已含连续竞价成交，写成"竞价额"会失真 —— 与后端
        #        `amount_note` 同一条诚实纪律）。
        #   ⚠️ 刻意**不设** `_EMOTION_SAVED`：那个标记的语义是"当日主字段已记过"，
        #      竞价分支只写了 auction 列 ⇒ 设了会让后来者误判主字段也写过。
        if 915 <= hhmm < 925:
            if old.get("auction_as_of") == val.get("as_of"):
                return                       # 同一时刻已记过（省写放大）
            row = _emotion_daily_row(day, val, after_close, old, auction=True)
            db.upsert("market_emotion_daily", row, conflict_columns=["date"])
            print(f"[market] auction snapshot saved {day} "
                  f"count={(val.get('auction') or {}).get('count')}")   # ASCII（铁律⑥）
            return
        if after_close and old.get("close_as_of") == val.get("as_of"):
            return
        if not after_close and old.get("verdict"):
            return
        row = _emotion_daily_row(day, val, after_close, old)
        db.upsert("market_emotion_daily", row, conflict_columns=["date"])
        _EMOTION_SAVED["date"] = day
        try:                             # 保留期清理（与写入同频，一天一次）
            cutoff = (_bj_now() - timedelta(days=_EMOTION_KEEP_DAYS)).strftime("%Y-%m-%d")
            db.execute("DELETE FROM market_emotion_daily WHERE date < %s", (cutoff,))
        except Exception:
            pass
        print(f"[market] emotion snapshot saved {day} "
              f"{'close' if after_close else 'open'} verdict={val.get('verdict')}")
    except Exception as e:
        print(f"[market] emotion save failed (non-fatal): {e}")


# ══════════════════════════════════════════════════════════════════════════
#  情绪对账（GET /api/market/emotion-review?days=）—— 2026-09-25（用户需求 P3）
# ══════════════════════════════════════════════════════════════════════════
# 【回答的问题】用户原话："复盘（19:30）：建议做『盘前预判 vs 实际走势』对账 ——
#   预测『分歧/常态』，实际是否退潮？对错了要回溯修正，形成闭环，
#   **否则情绪模型永远校准不了**。"
#
# 【对什么】同一天记录里的两组字段（见 `_emotion_daily_row`）：
#   · 预判 = 主字段 `verdict`（盘前/盘中**首次**算成）
#   · 实际 = `close_*`（**收盘后**算出）
#
# 【怎么判"对"】`verdict` 是**状态档位**（冰点 < 分歧/常态 < 亢奋），不是概率预测
#   ⇒ 用"起终档位差"比二值对/错更诚实：
#     · 一致   ⇒ 收盘仍同档（状态稳定 ⇒ 判读可信）
#     · 偏保守 ⇒ 实际更热（预判没看到升温）
#     · 偏乐观 ⇒ 实际更冷（预判没看到退潮）
#   ⇒ 命中率 = 一致 / **可对账天数**（两侧都有值才算；只有单侧的跳过，**不算错**）
#
# ⚠️ 两条诚实标注（写进 note，别让人误判）：
#   ① 「预判」的时刻是 `as_of` —— 可能是 09:10 盘前，也可能是你中午才第一次打开页面，
#      所以每条都带上时刻，别假装都是盘前；
#   ② 本表 **2026-09-25 才开始落库**（且只在交易日写）⇒ **历史无法补算**
#      （用 backtest_prices 回算的口径与实时口径不一致，回算等于污染数据）⇒ 需自然积累。
_VERDICT_ORDER = {"冰点": 0, "分歧/常态": 1, "亢奋": 2}


def _delta(cur, prev):
    """变化量 (实际 - 预判)，任一缺失返回 None（**不填 0** —— 0 会被读成"没变化"）。"""
    try:
        if cur is None or prev is None:
            return None
        return round(float(cur) - float(prev), 2)
    except (TypeError, ValueError):
        return None


@router.get("/emotion-review")
def emotion_review(days: int = Query(30, ge=3, le=200)):
    """「盘前预判 vs 当日实际」情绪对账（P3 闭环）。只读；失败返回空结构不抛错。"""
    out = {"days": days, "items": [], "summary": {}, "note": None}
    try:
        _ensure_emotion_table()
        from app.database import db
        rows = db.fetch("SELECT * FROM market_emotion_daily ORDER BY date DESC LIMIT %s",
                        (days,)) or []
    except Exception as e:
        print(f"[market] emotion review failed: {e}")        # ASCII（铁律⑥）
        out["note"] = "读取失败"
        return out

    items, n_ok, n_hit = [], 0, 0
    for r in rows:
        pre_v, act_v = r.get("verdict"), r.get("close_verdict")
        comparable = bool(pre_v and act_v)
        rel = None
        if comparable:
            n_ok += 1
            a, b = _VERDICT_ORDER.get(pre_v), _VERDICT_ORDER.get(act_v)
            if a is not None and a == b:
                rel, n_hit = "一致", n_hit + 1
            elif a is not None and b is not None:
                rel = "偏保守" if b > a else "偏乐观"
        items.append({
            "date": str(r.get("date") or ""),
            "pre_as_of": r.get("as_of"), "pre_verdict": pre_v,
            "pre_limit_up": _num_or_none(r.get("limit_up")),
            "pre_max_streak": _num_or_none(r.get("max_streak")),
            "pre_money": _num_or_none(r.get("prev_limit_today_pct")),
            "act_as_of": r.get("close_as_of"), "act_verdict": act_v,
            "act_limit_up": _num_or_none(r.get("close_limit_up")),
            "act_max_streak": _num_or_none(r.get("close_max_streak")),
            "act_money": _num_or_none(r.get("close_prev_limit_today_pct")),
            "d_limit_up": _delta(_num_or_none(r.get("close_limit_up")),
                                 _num_or_none(r.get("limit_up"))),
            "d_max_streak": _delta(_num_or_none(r.get("close_max_streak")),
                                   _num_or_none(r.get("max_streak"))),
            "d_money": _delta(_num_or_none(r.get("close_prev_limit_today_pct")),
                              _num_or_none(r.get("prev_limit_today_pct"))),
            "relation": rel, "comparable": comparable,
        })
    out["items"] = items
    out["summary"] = {"total": len(items), "comparable": n_ok, "hit": n_hit,
                      "hit_rate": (round(n_hit / n_ok * 100, 1) if n_ok else None)}
    if not n_ok:
        out["note"] = ("暂无可对账数据：需同一天既有「预判」又有「收盘实际」。"
                       "本表自 2026-09-25 起落库且只在交易日写，"
                       "历史无法补算（回算口径与实时不一致）⇒ 需自然积累几天。")
    return out


# ══════════════════════════════════════════════════════════════════════════
#  尾盘承接（14:30 窗口）—— 2026-09-25（用户需求 3，框架 C2 附带）
# ══════════════════════════════════════════════════════════════════════════
# 【回答什么问题】框架原话："盘中：14:30 承接（回封/抢筹/跳水）"。
#   尾盘半小时的**承接强度**决定**是否持仓过夜**：
#     · 走强（涨停增加 / 指数翘尾 / 量能跟上）⇒ 资金愿意持股过夜；
#     · 走弱（炸板增多 / 指数跳水）      ⇒ 有资金在尾盘撤退。
#   这正是"隔夜仓"的决策依据，也是原看盘序里唯一缺的**尾段**维度。
#
# 【★★ 口径的关键：必须有一个"14:30 的基线"】
#   内存行情只有**当前值**，没有"14:30 那一刻" ⇒ 若不在 14:30 记录，事后**无法还原**
#   （本项目没有 A 股分时数据，`get_kline` 只有日线）。
#   ⇒ 由 `scheduler.intraday_alert_loop`（3 分钟一轮、覆盖 13:00-15:00）在 **14:30-14:36**
#      落一条基线（一天一次）到 `market_tail_snapshot`；本函数对比"基线 vs 现在"。
#   ⚠️ 与 `market_emotion_daily` 同款：**当天没开机/没访问就没有基线** ⇒ 返回空 + 说明原因。
#
# 【指标与判定】框架要求"影子运行先看数据质量" ⇒ 阈值取**保守初值** + 落库攒样本 + 标注待校准：
#   Δ涨停（核心，±5 家记 ±2 分）｜指数尾段涨跌（±0.3% 记 ±1）｜Δ上涨家数（±200 记 ±1）
#   综合分 ≥ +2 ⇒ 尾盘走强（可持股过夜）｜≤ −2 ⇒ 尾盘走弱（减仓过夜）｜其余 ⇒ 平稳
#
# ⚠️ 两条诚实标注（写进返回值）：
#   ① 只对比"14:30 → 现在"**两个时点**，看不出"先炸板后回封"的**路径**
#      （要路径需连续采样，成本高；先用两点法攒样本，不够再升级）；
#   ② 阈值是**经验初值**，需累积样本后校准（本表每交易日一行）。
_TAIL_KEEP_DAYS = 400
_TAIL_TABLE_READY = False
# 尾盘基线要记的指数（与 `intraday_alerts._INDEX_WATCH` 同集：上证/创业板指/科创50）
_TAIL_INDICES = (("000001", "上证指数"), ("399006", "创业板指"), ("000688", "科创50"))


def _ensure_tail_table() -> None:
    """建表（幂等）。★ 数值列一律 TEXT —— SQLite/PostgreSQL 双库零风险。"""
    global _TAIL_TABLE_READY
    if _TAIL_TABLE_READY:
        return
    from app.database import db
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_tail_snapshot (
            date TEXT PRIMARY KEY,
            as_of TEXT,
            limit_up TEXT, limit_down TEXT,
            up TEXT, down TEXT,
            amount TEXT,
            idx_json TEXT,
            created_at TEXT
        )
    """)
    _TAIL_TABLE_READY = True


def _breadth_now() -> Dict:
    """当前市场宽度（**纯内存缓存、零网络**）。口径与 `market_overview.stats` 逐字一致
    （全部股票的 `change_pct`，含停牌记 0、不剔除）—— ★ 两处口径必须一致，
    否则页面上会出现两个不同的"涨停家数"。"""
    stocks = _cache.get("stocks") or {}
    if not stocks:
        return {}
    changes = [s.get("change_pct") for s in stocks.values()
               if s.get("change_pct") is not None]
    if not changes:
        return {}
    return {
        "total": len(stocks),
        "up": sum(1 for c in changes if c > 0),
        "down": sum(1 for c in changes if c < 0),
        "limit_up": sum(1 for c in changes if c >= 9.9),
        "limit_down": sum(1 for c in changes if c <= -9.9),
        "amount": round(sum(float(s.get("amount") or 0) for s in stocks.values()), 2),
    }


def _index_snapshot() -> Dict[str, Dict]:
    """一次请求取 `_TAIL_INDICES` 全部指数（`{code: {name, price, change_pct}}`）。失败返回 {}。"""
    try:
        from app.tencent import _fetch_tencent
        codes = ",".join(("sh" if c.startswith("0") else "sz") + c for c, _ in _TAIL_INDICES)
        data = _fetch_tencent(codes)
        out = {}
        for code, name in _TAIL_INDICES:
            prefix = "sh" if code.startswith("0") else "sz"
            info = data.get(f"{prefix}{code}") or {}
            if info.get("price"):
                out[code] = {"name": name, "price": float(info["price"]),
                             "change_pct": float(info.get("change_pct") or 0)}
        return out
    except Exception as e:
        print(f"[market] tail index snapshot failed: {e}")          # ASCII（铁律⑥）
        return {}


def _save_tail_baseline() -> Dict:
    """记录当天的「尾盘基线」（14:30 窗口，**一天一次**）。供调度器调用；失败静默。

    幂等：`market_tail_snapshot.date` 主键，已有则跳过（不覆盖 —— 基线必须保持"14:30 那一刻"）。
    ⚠️ 若那一刻内存行情缓存为空（首次全量扫描未完成/刚重启）⇒ **不记**，
       否则会写一个全 0 的基线、之后所有对比都失真。
    """
    try:
        _ensure_tail_table()
        # ⚠️ 非交易日**不写**：休市日内存缓存是**上一交易日收盘快照** ⇒ 写进去会造成
        #   "日期=休市日、数据=上一交易日"的错位记录（与 `_save_emotion_daily` 同款防线）。
        try:
            from app.flash.rules import is_trading_day
            if not is_trading_day(_bj_now()):
                return {"saved": False, "reason": "not trading day"}
        except Exception:
            pass                      # 日历判断失败不阻塞（宁可多写一条也不静默失效）
        day = _bj_now().strftime("%Y-%m-%d")
        from app.database import db
        if db.fetch_one("SELECT date FROM market_tail_snapshot WHERE date=%s", (day,)):
            return {"saved": False, "reason": "already"}
        b = _breadth_now()
        if not b:
            return {"saved": False, "reason": "no market cache"}
        idx = _index_snapshot()
        now = _bj_now()
        db.upsert("market_tail_snapshot", {
            "date": day, "as_of": now.isoformat(),
            "limit_up": str(b["limit_up"]), "limit_down": str(b["limit_down"]),
            "up": str(b["up"]), "down": str(b["down"]),
            "amount": str(b["amount"]),
            "idx_json": json.dumps(idx, ensure_ascii=False),
            "created_at": now.isoformat(),
        }, conflict_columns=["date"])
        try:                             # 保留期清理（与写入同频，一天一次）
            cutoff = (_bj_now() - timedelta(days=_TAIL_KEEP_DAYS)).strftime("%Y-%m-%d")
            db.execute("DELETE FROM market_tail_snapshot WHERE date < %s", (cutoff,))
        except Exception:
            pass
        print(f"[market] tail baseline saved {day} limit_up={b['limit_up']}")   # ASCII
        return {"saved": True, "date": day, "limit_up": b["limit_up"]}
    except Exception as e:
        print(f"[market] tail baseline failed (non-fatal): {e}")     # ASCII（铁律⑥）
        return {"saved": False, "reason": str(e)[:80]}


def tail_review(date: str = None) -> Dict:
    """尾盘承接：对比「14:30 基线」与「现在（收盘后即收盘值）」。只读；失败返回空结构。"""
    out = {"available": False, "date": None, "as_of": None, "verdict": None, "label": None,
           "score": None, "advice": None, "note": None, "deltas": {}, "baseline": {},
           "now": {}, "indices": []}
    try:
        _ensure_tail_table()
        from app.database import db
        day = date or _bj_now().strftime("%Y-%m-%d")
        row = db.fetch_one("SELECT * FROM market_tail_snapshot WHERE date=%s", (day,))
        if not row:
            out["note"] = (f"{day} 无尾盘基线（14:30 窗口未被记录）——"
                           "该基线需 14:30 时后端在线且行情缓存已就绪，历史无法补算")
            return out
        base = {
            "limit_up": _num_or_none(row.get("limit_up")),
            "limit_down": _num_or_none(row.get("limit_down")),
            "up": _num_or_none(row.get("up")),
            "down": _num_or_none(row.get("down")),
            "amount": _num_or_none(row.get("amount")),
        }
        now = _breadth_now()
        if not now:
            out["note"] = "行情缓存未就绪，无法对比（稍后重试）"
            return out
        b_idx = json.loads(row.get("idx_json") or "{}")
        n_idx = _index_snapshot() or b_idx           # 取不到实时就退化为基线（Δ=0）
        indices = []
        for code, name in _TAIL_INDICES:
            bp = (b_idx.get(code) or {}).get("price")
            np_ = (n_idx.get(code) or {}).get("price")
            d = (round((np_ / bp - 1) * 100, 2) if bp and np_ else None)
            indices.append({"code": code, "name": name, "price": np_,
                            "d_pct": d,
                            "change_pct": (n_idx.get(code) or {}).get("change_pct")})
        d_lu = now["limit_up"] - (base["limit_up"] or 0)
        d_up = now["up"] - (base["up"] or 0)
        d_amt = round((now["amount"] - (base["amount"] or 0)) / 1e8, 0)   # 亿元
        # 指数尾段：取跌幅最大者代言（走弱时它最能说明问题；走强时取涨幅最大者）
        ds = [i["d_pct"] for i in indices if i["d_pct"] is not None]
        idx_worst = min(ds) if ds else None
        idx_best = max(ds) if ds else None
        score = 0
        if d_lu >= 5:
            score += 2
        elif d_lu <= -5:
            score -= 2
        ref = idx_worst if (idx_worst is not None and idx_worst < 0) else idx_best
        if ref is not None:
            if ref >= 0.3:
                score += 1
            elif ref <= -0.3:
                score -= 1
        if d_up >= 200:
            score += 1
        elif d_up <= -200:
            score -= 1
        if score >= 2:
            verdict, label = "strong", "尾盘走强"
            advice = ("尾盘承接强（涨停增加/指数翘尾）⇒ 资金愿意持股过夜，"
                      "已有仓位可持有；不追高，隔夜仓按原计划")
        elif score <= -2:
            verdict, label = "weak", "尾盘走弱"
            advice = ("尾盘承接弱（涨停减少/指数跳水）⇒ 有资金在尾盘撤退，"
                      "按纪律**减仓过夜**（尤其高位/浮盈大的），不新开仓")
        else:
            verdict, label = "flat", "尾盘平稳"
            advice = "尾盘承接中性，无明确方向；隔夜仓按原计划，不因尾盘临时加仓"
        out.update({
            "available": True, "date": day, "as_of": row.get("as_of"),
            "verdict": verdict, "label": label, "score": score, "advice": advice,
            "baseline": base, "now": now, "indices": indices,
            "deltas": {"limit_up": d_lu, "up": d_up, "amount_yi": d_amt,
                       "idx_worst": idx_worst, "idx_best": idx_best},
        })
        # ⚠️ 给前端的解释文案：**不能含 Markdown 标记**（纯文本插值）
        out["note"] = ("口径：14:30 基线 → 现在（两点法，非路径）· 阈值 ±5 家 / ±0.3% / ±200 家"
                       " 为经验初值，影子运行期持续校准（本表每交易日一行）")
    except Exception as e:
        print(f"[market] tail review failed: {e}")                  # ASCII（铁律⑥）
        out["note"] = "读取失败"
    return out


@router.get("/tail-review")
def market_tail_review(date: str = Query(None)):
    """尾盘承接（14:30 → 现在/收盘）。只读；无基线时返回解释而不是错误。"""
    return tail_review(date)


# ══════════════════════════════════════════════════════════════════════════
#  竞价判读 —— 2026-09-25（用户："这些结论可不可以生成在页面里，用户就不需要思考太多"）
# ══════════════════════════════════════════════════════════════════════════
# 【回答什么】把竞价看板的**数字**翻成两句人话：**接力意愿**（昨日涨停股今天还接不接）
#   与**全市场开局**（普遍高开还是普跌）。框架原话："溢价为正 = 情绪没退"。
#
# ⚠️⚠️ 三条边界（都写进返回值，避免被当成预测）：
#   ① **这是"开盘前强度"的描述，不是涨跌预测** —— 高开只是起点，方向由 9:30-10:00 的
#      冲高回落/回封确认（框架本身就这么要求）。
#   ② **阈值是经验初值、未经回测**（项目纪律：不造无依据的"闸门"）⇒ 故本判读
#      **纯展示、不进任何信号或仓位**；且**只落库事实**（`auction_*`），判读由规则实时算
#      ⇒ 将来校准阈值后，历史数据自动受益（"存事实、不存结论"）。
#   ③ 数据缺失（休市 / 行情源未就绪）⇒ 返回 None，前端**不显示**（不假装有判读）。
_AUCTION_GAP_STRONG = 1.0     # 昨涨停股平均高开 ≥ +1.0% ⇒ 接力强（经验初值）
_AUCTION_GAP_WEAK = -1.0      # ≤ -1.0% ⇒ 接力弱
_AUCTION_RATIO_UP = 1.5       # 高开:低开 ≥1.5 且全市场平均 ≥ +0.1% ⇒ 普遍高开
_AUCTION_RATIO_DOWN = 0.67    # ≤0.67 且全市场平均 ≤ -0.1% ⇒ 普遍低开
_AUCTION_ALL_BAND = 0.1

# ★★ 2026-09-25（A 档 1：竞价看板"图表化"）：全市场涨幅**六档**分档。
#   【为什么需要】用户看板的"高开 643 : 低开 1915"只是两个**总数**，看不出"跌得多深" ——
#     同样 1915 家低开，"全在 -0.5% 内"（阴跌磨人）与"一半跌超 3%"（恐慌盘）含义天差地别。
#     ⇒ 分布才是"直观"的前提（两行文字永远读不出形状）。
#   ⚠️ 六档**必须覆盖全部取值（含平开 0）** ⇒ 合计恒等于全市场家数（否则前端占比之和
#     凑不满 100%，又是一处"数字对不上"，比没有图更糟）。
#   ⚠️ 与既有 `up_open/down_open` 的口径差异（**不改**，仅在此声明）：那两个计数
#     **不含平开**（`_g == 0` 两边都不计）⇒ `up_open + down_open` 可能 < 家数，属既有语义。
_AUCTION_BIN_LABELS = ("≥+5%", "+2~+5%", "0~+2%", "0~-2%", "-2~-5%", "≤-5%")
_AUCTION_BIN_DIRS = ("up", "up", "up", "down", "down", "down")


def _auction_bin(g: float) -> int:
    """涨幅(%) → 档位下标 0..5（**逐档左闭右开**判定）。

    ⚠️ 抽成纯函数是为了可打桩：边界值（0 / ±2 / ±5）是这类分档最容易错的地方，
       而错误的分档**不会报错**、只会安静地把图画歪 —— 必须能单独测。
    """
    if g >= 5:
        return 0
    if g >= 2:
        return 1
    if g >= 0:
        return 2
    if g >= -2:
        return 3
    if g >= -5:
        return 4
    return 5


def _auction_read(a: Dict, has_live: bool) -> Optional[Dict]:
    """竞价数字 → 「接力意愿 + 全市场开局」两句判读。数据不足返回 None（不硬编）。"""
    if not has_live or not a:
        return None
    gap = a.get("avg_gap")           # 昨日涨停股平均高开（接力意愿）
    all_gap = a.get("avg_gap_all")   # 全市场平均高开
    up, down = a.get("up_open"), a.get("down_open")
    if gap is None and all_gap is None:
        return None

    # ① 接力意愿
    if gap is None:
        relay, relay_cn = None, None
    elif gap >= _AUCTION_GAP_STRONG:
        relay, relay_cn = "strong", f"接力意愿强（昨涨停股今均高开 {gap:+.2f}%，仍有人接）"
    elif gap <= _AUCTION_GAP_WEAK:
        relay, relay_cn = "weak", f"接力转弱（昨涨停股今均 {gap:+.2f}%，追涨者平均亏钱）"
    else:
        relay, relay_cn = "neutral", f"接力中性（昨涨停股今均 {gap:+.2f}%）"

    # ② 全市场开局
    ratio = (up / max(down or 0, 1)) if (up is not None and down is not None) else None
    if ratio is None or all_gap is None:
        market, market_cn = None, None
    elif ratio >= _AUCTION_RATIO_UP and all_gap >= _AUCTION_ALL_BAND:
        market, market_cn = "bullish", f"普遍高开（{up} : {down}，平均 {all_gap:+.2f}%）"
    elif ratio <= _AUCTION_RATIO_DOWN and all_gap <= -_AUCTION_ALL_BAND:
        market, market_cn = "bearish", f"普遍低开（{up} : {down}，平均 {all_gap:+.2f}%）"
    else:
        market, market_cn = "mixed", f"开局分化（{up} : {down}，平均 {all_gap:+.2f}%）"

    # ③ 组合结论 + 动作（克制：只给"该注意什么"，不给买卖指令）
    combo = (relay, market)
    if combo == ("strong", "bullish"):
        level, text = "good", "顺风开局：接力未退 + 普遍高开"
        action = "按盘前计划执行；⚠️ 高开只是起点，9:30-10:00 快速回落按「冲高回落」纪律处理"
    elif combo == ("strong", "bearish"):
        level, text = "mixed", "背离：强势股有人接，但大盘整体低开"
        action = "资金只在少数强势股抱团 ⇒ 只做有信号的标的，别用普涨思维"
    elif combo == ("weak", "bullish"):
        level, text = "mixed", "背离：指数高开（权重/普涨），但强势股接力退潮"
        action = "谨防「指数红、个股绿」⇒ 降低追涨标准，看个股别只看指数"
    elif combo == ("weak", "bearish"):
        level, text = "bad", "退潮开局：接力弱 + 普遍低开"
        action = "以防守为主：不加仓、不追高，持仓按止损纪律执行"
    else:
        level, text = "neutral", "开局一般（无明显接力强/弱或普涨/普跌）"
        action = "按盘前判断执行，等 9:30-10:00 的方向确认再动作"

    return {
        "level": level, "text": text, "action": action,
        "relay": relay, "relay_cn": relay_cn,
        "market": market, "market_cn": market_cn,
        "note": ("阈值为经验初值（未回测）⇒ 纯展示、不进信号与仓位；且只描述**开盘前强度**，"
                 "不预测涨跌 —— 方向由 9:30-10:00 验证"),
    }


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
    # ★★ 2026-09-26（用户提问："9:15 前涨跌幅不都是昨天的吗？昨日涨停表现统计的是昨天和
    #   前天吗？" → "上个交易日会不会更简单合理"）：
    #   【原实现的问题】`d_prev = dates_all[-2]`（`dates_all` 来自 `backtest_prices`，**日批回填**）
    #     **隐含假设"closes 最新日 == 今天"** —— 该假设**只在收盘后成立**：
    #       · 盘中：closes 最新=昨天 ⇒ d_prev=前天，而 `price` 是今天实时
    #         ⇒ `price / close(前天)` **跨了两天**（假的"昨涨停今表现"）
    #       · 盘前/休市：d_prev 数值恰好对（`price` 就是那天的收盘），但**文案写"今日"**会误导
    #   【改法】名单日锚在「**价格所属日 P** 的前一交易日」——`price` 是哪天的，
    #     "昨日"就相对哪天算 ⇒ **三个时段同时正确**（P 的定义见 `_price_date()`），
    #     且**不再依赖日批是否及时回填**（改查交易日历，与全项目"数据所属日期"口径一致）。
    _p_day = _price_date()
    d_prev = _prev_trading_day(_p_day)
    d_prev2 = _prev_trading_day(d_prev) if d_prev else None

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
    # ★ 2026-09-25（竞价段评估 #3）：给全市场榜单加"**与我有关**"的标记 ——
    #   原先只有名字+幅度，看不出这 20 只高开里有没有**池内/战法信号/我的持仓**
    #   （看榜单的目的正是"里面有没有我能接的"）。
    #   ⚠️ 零新增重查询：`signal_industry_cached()` 有 30 分钟进程缓存（读 strategy_results 最新日，
    #      与决策卡的「信号×行业」同源）；持仓只取 user_portfolio 的 code 集合（小表 + scope 过滤）。
    #   ⚠️ 失败静默（辅助标记绝不拖垮竞价看板）。
    sig_codes, held_codes = set(), set()
    try:
        from app.trader_brief import signal_industry_cached
        for _r in ((signal_industry_cached() or {}).get("rows") or []):
            sig_codes.update(str(c) for c in (_r.get("codes") or []))
    except Exception as e:
        print(f"[market] auction sig marks failed: {e}")        # ASCII（铁律⑥）
    try:
        from app.database import db as _db
        from app.portfolio_scope import portfolio_where
        _w, _p = portfolio_where()
        held_codes = {str(r.get("code")) for r in
                      (_db.fetch(f"SELECT code FROM user_portfolio {_w}", _p) or [])}
    except Exception as e:
        print(f"[market] auction held marks failed: {e}")       # ASCII（铁律⑥）

    market_gaps, up_open, down_open = [], 0, 0
    bin_counts = [0] * 6          # ★ A 档 1：六档分布计数（见 `_AUCTION_BIN_LABELS`）
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
        # ★ A 档 1：顺手分档 —— 与 market_gaps **同一个循环**，零新增遍历/查询（见上方常量注释）
        bin_counts[_auction_bin(_g)] += 1
        market_gaps.append({"code": _c, "name": _s.get("name") or _c,
                            "gap_pct": round(_g, 2), "amount_wan": round(_amt),
                            # ★ "与我有关"标记（见上方 sig_codes/held_codes 注释）
                            "sig": _c in sig_codes, "held": _c in held_codes})
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
               # ★ A 档 1：分布（前端画条目 % —— 只给 count 与标签，**占比由前端就地算**，
               #   避免同一口径在两处各算一遍、日后改档位时只改一处）
               "histogram": [{"label": _AUCTION_BIN_LABELS[i], "count": bin_counts[i],
                              "dir": _AUCTION_BIN_DIRS[i]} for i in range(6)],
               "total_amount_wan": (round(total_amount_wan) if total_amount_wan else None),
               "amount_note": "成交额为截至 as_of 的**累计值**（9:15-9:25 期间即竞价额）"}
    # ★ 2026-09-25：把数字翻成两句人话（接力意愿 / 全市场开局）—— 见 `_auction_read` 的边界声明
    auction["verdict"] = _auction_read(auction, has_live)

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
        # ★ 2026-09-26：口径日期**显式外露** —— 前端文案据此写成
        #   「09-23 涨停 11 只 · 09-24 均 -1.16%」，彻底消除"昨日/今日"歧义。
        #   `price_date` = 价格所属日 P；`prev_limit_date` = 名单所属日（= P 的前一交易日）
        "price_date": _p_day,
        "prev_limit_date": d_prev,
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

def _build_ladder(uh: Dict) -> Optional[Dict]:
    """把 `uplimit_hot.ban_info`（连板分布）转成"梯队结构 + 断层"判读。

    ★★ 2026-09-25（用户批准做「唤醒闲置数据」）—— `ban_info` 是 zzshare 每日落库的
      **连板梯度分布**（`{"1":{"count":37},"2":{"count":9},…,"6":{"count":1}}`），
      此前**只落库、从未展示**。它的价值在**梯队完整性**：
        · 各级别连续 ⇒ 梯队健康（有承接、有补涨）；
        · 中间某级为 0 而更高级别非 0 ⇒ **断层** ⇒ 高标孤立无承接，
          是短线圈公认的退潮前兆（例：实测 09-23 为 `1:38/2:3/3:7/4:2/**5:0/6:0**/7:1`
          ⇒ 5、6 板空档却有 7 板 ⇒ 典型"高标孤零零"）。
    ⚠️ 口径：`count` 是"当日**封住** N 连板的**家数**"（收盘口径，非盘中）。
    """
    bi = (uh or {}).get("ban_info")
    if not isinstance(bi, dict) or not bi:
        return None
    dist = []
    for k, v in bi.items():
        try:
            lvl = int(str(k))
        except (TypeError, ValueError):
            continue
        cnt = (v or {}).get("count") if isinstance(v, dict) else v
        try:
            cnt = int(cnt or 0)
        except (TypeError, ValueError):
            cnt = 0
        dist.append({"level": lvl, "count": cnt})
    if not dist:
        return None
    dist.sort(key=lambda x: x["level"])
    max_lvl = max(x["level"] for x in dist)
    real_max = max((x["level"] for x in dist if x["count"] > 0), default=0)
    # 断层 = 该级别为 0 且**存在更高且非 0** 的级别（"上面还有人不算断层"要排除）
    gaps = [x["level"] for x in dist
            if x["count"] == 0 and any(y["level"] > x["level"] and y["count"] > 0
                                       for y in dist)]
    note = None
    if gaps:
        note = ("连板梯队断层："
                + "、".join(f"{g}板" for g in gaps)
                + f"为 0，但存在 {real_max} 板 —— 高标孤立、缺少中位承接"
                  "（短线退潮的常见前兆）")
    # ★ 2026-09-25（用户 review 意见①）：补一个**相邻信号**。
    #   `{1:38, 2:0, 3:0}`（全首板、无任何连板）按上面的"断层"定义**不算断层**
    #   （因为不存在"更高且非零"的级别），但它是**另一种冰点**：
    #   **高度塌陷** —— 没人敢做二板，接力意愿缺失。
    #   ⇒ 两个信号并列，情绪刻画才完整（断层 = 有高标但缺承接；塌陷 = 连高度都没有）。
    total_cnt = sum(x["count"] for x in dist)
    collapsed = real_max <= 1 and total_cnt >= 15
    if collapsed:
        _cnote = (f"高度塌陷：最高仅 {real_max} 板、无连板（当日涨停 {total_cnt} 只）"
                  "—— 接力意愿缺失，情绪处于冰点档")
        note = (_cnote if not note else note + "；" + _cnote)
    return {
        "max_count": real_max,
        "dist": dist,
        "levels": max_lvl,
        "total": total_cnt,
        "gaps": gaps,
        "broken": bool(gaps),
        "collapsed": collapsed,
        "note": note,
    }


# ══════════════════════════════════════════════════════════════════════════
#  炸板率（A）+ 大面率（B）—— 2026-09-25（用户 review 意见落地）
# ══════════════════════════════════════════════════════════════════════════
# 【为什么拆成两个指标】A 与 B 衡量的**不是同一件事**：
#   · A 炸板率 = **接力意愿**（资金愿不愿意把板封死）—— 同花顺/通达信标准口径；
#   · B 大面率 = **亏钱烈度**（炸了之后砸多深）—— 对打板客的实际伤害。
#   混在一起会产生歧义：一只票摸板后收 +7% ⇒ A 判「炸」（对，接力确实失败了），
#   B 判「不炸」（但它对打板客仍是 -3% 的面）⇒ 两者都对，因为是两回事。
#   ★ 组合读法（比单一数字信息量大得多）：
#     **炸板率高 + 大面率低** = 分歧大但亏钱效应温和；
#     **双高** = 真正的退潮信号。
# 【四条口径细节（决定数据质量，比选 A/B 更重要）】
#   ① **收盘判定用阈值缓冲**，不比精确涨停价：`涨幅 ≥ 板幅 − 0.2%` 才算封住
#      （避免一分钱误差/复权问题把**回封**票误判成炸板）；
#   ② **按板幅分桶**：20cm 的炸板与 10cm 完全不是一个情绪含义，混在一起会稀释信号
#      ⇒ 主板 10% / 双创 20% / 北交所 30% / ST 5% 分桶，ST 单列；
#   ③ **剔除新股**（上市不足 60 个交易日，无涨跌幅或规则特殊）**且一字板不计入分母**
#      —— 一字板不可能炸，留在分母会**系统性压低**炸板率；
#   ④ 分母与口径写进返回的 `meta`，前端标注，否则日后自己都会怀疑数字。
# 【数据源】`backtest_prices`（`high/low/close/name` 全有）⇒ **零新增数据源**。
#   板幅与涨停价**复用 `backtest.engine` 的 `_limit_pct` / `_round_tick`**
#   （项目唯一实现，避免多处口径漂移）。
_NEW_STOCK_MIN_DAYS = 60          # 上市不足 60 个交易日视为新股
_BREAK_TOL = 0.002                # ① 阈值缓冲：板幅 −0.2%
_SEAL_TOL = 0.001                 # 曾涨停判定容差（最高价触价即可）
_BIG_LOSS_PCT = 5.0               # ② 大面率定义：炸板且收盘涨幅 < 5%
_first_seen_cache = {"ts": 0.0, "map": None}   # 每只最早出现日期（日级缓存，防重复扫表）


def _first_seen_map() -> Dict:
    """`{code: 最早交易日}` —— 用于剔除新股。**日级进程缓存**（该查询扫全表，不能每次做）。"""
    import time as _t
    now = _t.time()
    if _first_seen_cache["map"] is not None and now - _first_seen_cache["ts"] < 3600:
        return _first_seen_cache["map"]
    out = {}
    try:
        from app.database import db
        for r in db.fetch("SELECT code, MIN(date) AS d0 FROM backtest_prices "
                          "GROUP BY code") or []:
            out[str(r.get("code"))] = str(r.get("d0") or "")
    except Exception as e:
        print(f"[market] first-seen map failed: {e}")          # ASCII（铁律⑥）
    _first_seen_cache.update(ts=now, map=out)
    return out


def _bucket_of(code: str, name: str) -> str:
    """按板幅分桶（②）：主板10 / 双创20 / 北交所30 / ST5。"""
    from app.backtest.engine import _limit_pct
    lp = _limit_pct(str(code), str(name or ""))
    if abs(lp - 0.05) < 1e-9:
        return "ST5"
    if abs(lp - 0.20) < 1e-9:
        return "20cm"
    if abs(lp - 0.30) < 1e-9:
        return "30cm"
    return "主板"


def _limit_stats(day: str) -> Optional[Dict]:
    """炸板率(A) + 大面率(B)，按板幅分桶。失败/数据不足返回 None。"""
    try:
        from app.backtest.engine import _limit_pct, _round_tick
        from app.database import db
        prev = (db.fetch_one("SELECT MAX(date) AS d FROM backtest_prices "
                             "WHERE date < %s", (day,)) or {}).get("d")
        if not prev:
            return None
        rows = db.fetch("SELECT t.code, t.name, t.high, t.low, t.close, "
                        "p.close AS prev_close "
                        "FROM backtest_prices t JOIN backtest_prices p "
                        "  ON p.code = t.code AND p.date = %s "
                        "WHERE t.date = %s", (str(prev), day)) or []
        seen = _first_seen_map()
        # 每个桶：曾涨停(不含一字) / 一字 / 炸板 / 大面
        buckets = {}
        skip_new = skip_bad = 0
        for r in rows:
            code = str(r.get("code") or "")
            name = str(r.get("name") or "")
            try:
                high = float(r.get("high") or 0)
                low = float(r.get("low") or 0)
                close = float(r.get("close") or 0)
                pc = float(r.get("prev_close") or 0)
            except (TypeError, ValueError):
                continue
            if high <= 0 or low <= 0 or close <= 0 or pc <= 0:
                skip_bad += 1
                continue
            d0 = seen.get(code) or ""
            if d0 and d0 > _shift_days(day, -_NEW_STOCK_MIN_DAYS * 2):
                skip_new += 1                       # ③ 剔除新股
                continue
            lp = _limit_pct(code, name)
            up_price = _round_tick(pc * (1 + lp))
            chg = close / pc - 1
            touched = high >= up_price * (1 - _SEAL_TOL)          # 曾触及涨停
            if not touched:
                continue
            sealed = chg >= lp - _BREAK_TOL                       # ① 缓冲后判封住
            is_oneword = (abs(high - low) < 1e-9) and sealed      # ③ 一字板
            b = buckets.setdefault(_bucket_of(code, name),
                                   {"touched": 0, "sealed": 0, "oneword": 0,
                                    "broken": 0, "big_loss": 0})
            b["touched"] += 1
            # ⚠️ 口径说明：`sealed` **包含一字板**（一字当然也是"封住了"）
            #   ⇒ 自洽式是 `sealed + broken == touched`，不是 `== touched - oneword`。
            #   `oneword` 是 `sealed` 的**子集**、也是炸板率分母里的**扣除项**。
            if sealed:
                b["sealed"] += 1
                if is_oneword:
                    b["oneword"] += 1
            else:
                b["broken"] += 1
                if (close / pc - 1) * 100 < _BIG_LOSS_PCT:        # ② 大面
                    b["big_loss"] += 1
        if not buckets:
            return None
        # 炸板率分母 = 曾涨停 **− 一字板**（一字不可能炸，留在分母会系统性压低炸板率）
        out_buckets = {}
        tot = {"touched": 0, "sealed": 0, "oneword": 0, "broken": 0, "big_loss": 0}
        for k, b in buckets.items():
            den = max(0, b["touched"] - b["oneword"])
            out_buckets[k] = {
                **b, "denom": den,
                "break_rate": (round(b["broken"] / den * 100, 1) if den else None),
                "big_loss_rate": (round(b["big_loss"] / den * 100, 1) if den else None),
            }
            for kk in tot:
                tot[kk] += b[kk]
        den_all = max(0, tot["touched"] - tot["oneword"])
        return {
            "date": day, "prev_date": str(prev),
            "break_rate": (round(tot["broken"] / den_all * 100, 1) if den_all else None),
            "big_loss_rate": (round(tot["big_loss"] / den_all * 100, 1) if den_all else None),
            "seal_rate": (round(tot["sealed"] / tot["touched"] * 100, 1)
                          if tot["touched"] else None),
            "buckets": out_buckets, "total": tot,
            "skipped": {"new_stock": skip_new, "bad_data": skip_bad},
            "meta": (f"口径：曾触及涨停（最高价≥涨停价）为分母，"
                     f"剔除一字板（{tot['oneword']} 只，一字不会炸，留在分母会压低炸板率）"
                     f"与新股（上市<{_NEW_STOCK_MIN_DAYS}交易日，剔除 {skip_new} 只）；"
                     f"封住判定用板幅−{_BREAK_TOL * 100:.1f}% 缓冲（防一分钱误差误判回封）；"
                     f"大面率 = 炸板且收盘涨幅<{_BIG_LOSS_PCT:.0f}% 的占比；"
                     f"按板幅分桶（主板10/cm 双创20cm 北交所30cm ST5）；"
                     f"sealed 含一字板（自洽式 sealed+broken=touched）。"
                     f"⚠️ **样本局限**：数据源 backtest_prices 只覆盖回填过的股票"
                     f"（当日 {len(rows)} 只，非全市场）⇒ 本组绝对家数会**少于**官方口径"
                     f"（如 zzshare 的涨停清单），**比率可用、家数不可直接对比**"),
        }
    except Exception as e:
        print(f"[market] limit stats failed: {e}")             # ASCII（铁律⑥）
        return None


def _shift_days(day: str, delta: int) -> str:
    """日期加减（自然日，仅用于"上市满 N 个交易日"的宽松近似）。"""
    try:
        from datetime import datetime as _dt, timedelta as _td
        return (_dt.strptime(str(day)[:10], "%Y-%m-%d") + _td(days=delta)).strftime("%Y-%m-%d")
    except Exception:
        return str(day)[:10]


def _zz_snapshot(day: str) -> Dict:
    """读 `zz_daily_snapshots` 某日 payload（**零外部请求**；无则 `{}`）。失败静默。"""
    try:
        from app.zzshare_daily import load_range
        rows = load_range(day, day) or []
        return rows[0] if rows else {}
    except Exception as e:
        print(f"[market] zz snapshot read failed: {e}")        # ASCII（铁律⑥）
        return {}


def _dedup_uplimit_stocks(rows) -> list:
    """`uplimit_stocks` 去重（按 `stock_code`，保留首条）。

    ★★ 2026-09-25（用户贴出"金辰股份 ×6"问这是否正常）—— **上游本身返回重复行**：
      实测 2026-09-24 该表 22 行，**去重后只有 3 只票**（金辰股份 ×6 / 上工申贝 ×9 /
      福建水泥 ×7），**只有 `id` 不同**，其余字段（代码/名称/连板/涨停时间/成交额）完全一致
      ⇒ 前端按行渲染必然出现"同一只票刷屏"。
    ⚠️ 口径提醒（调用方必读）：该表**不是当日全量涨停清单**（当日涨停 52 只，此表仅 3 只）
      ⇒ 展示时必须如实标注"非全量"，**权威家数以 `uplimit_hot.ban_info` 为准**（见 `ladder.total`）。
      宁可标"明细（3 只）"，也不能让用户以为"今天只有 3 只涨停"。
    """
    out, seen = [], set()
    for r in (rows or []):
        if not isinstance(r, dict):
            continue
        c = str(r.get("stock_code") or "")
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(r)
    return out


@router.get("/limit-review")
def market_limit_review(date: str = None):
    from app.flash import rules as _rules
    d = date or _bj_now().strftime("%Y-%m-%d")
    # ★ 2026-09-25（用户 review 意见②）：**双数据源的时点标注**。
    #   快照是**收盘定稿**（日批写），直连 zzshare 是**动态值** ⇒ 两者语义不同，
    #   必须让消费方知道"这份数字是什么时候的"：
    #     · `as_of`  = 数据时点（快照 ⇒ 快照日期；直连 ⇒ 现在）
    #     · `stale`  = 直连失败后**退回旧快照**的标记（而不是让整块数据消失 ——
    #                  盘前页面最怕某块突然空白）
    _now_str = _bj_now().strftime("%Y-%m-%d %H:%M")
    out = {"date": d, "steps": [], "stocks": [], "ladder": None, "stats": None,
           "source": None, "as_of": _now_str, "stale": False}
    # ★★ 2026-09-25：**优先读已落库快照**（`zz_daily_snapshots`，日批写入）
    #   为什么改：原实现**每次都直连 zzshare** ⇒ ① 匿名调用数据范围受限（本函数原注释
    #   自己写着"匿名可用但数据范围受限"）；② 依赖 token 与网络、盘中易失败；
    #   ③ 明明每天已经落库了一份完整 payload，却不用。
    #   ⇒ 先读快照（零请求、稳定），**读不到再回退直连**（保持原行为兜底）。
    snap = _zz_snapshot(d)
    if snap:
        uh = snap.get("uplimit_hot") or {}
        if not uh.get("_error"):
            out["ladder"] = _build_ladder(uh)
        us = snap.get("uplimit_stocks")
        if isinstance(us, dict):
            us = us.get("data") or us.get("items")
        if isinstance(us, list) and us:
            out["stocks"] = _dedup_uplimit_stocks(us)[:50]   # ★ 上游有重复行，见函数注释
        rs = snap.get("review_uplimit_reason")
        if isinstance(rs, list) and rs:
            out["steps"] = rs[:20]
        if out["ladder"] or out["stocks"]:
            out["source"] = "snapshot"
            out["as_of"] = str(snap.get("date") or d)[:10]      # 快照 = 收盘定稿时刻
    if out["ladder"] or out["stocks"]:
        out["stats"] = _limit_stats(d)          # ★ 炸板率/大面率（来自 backtest_prices）
        return out
    # ── 回退：直连 zzshare（快照缺失时，拿到的是**动态值**）──
    try:
        from app.zzshare_client import get_api
        api = get_api()
        df = api.review_uplimit_hot_step(date1=d)
        if df is not None and hasattr(df, "to_dict"):
            out["steps"] = df.to_dict("records")[:20]
        df2 = api.uplimit_stocks(date1=d)
        if df2 is not None and hasattr(df2, "to_dict"):
            out["stocks"] = _dedup_uplimit_stocks(df2.to_dict("records"))[:50]   # ★ 同上
        if out["steps"] or out["stocks"]:
            out["source"] = "live"
            out["as_of"] = _now_str
    except Exception as e:
        print(f"[market] limit-review live failed: {e}")        # ASCII（铁律⑥）
    if out["ladder"] or out["stocks"]:
        out["stats"] = _limit_stats(d)
        return out
    # ── ★★ 用户 review 意见②：双源都失败时**降级为最后可用快照 + stale 标记**，
    #   而不是让整块数据消失（盘前页面最怕某块突然空白）。
    try:
        from app.zzshare_daily import load_range
        older = load_range(_shift_days(d, -30), d) or []
        if older:
            last = older[-1]
            uh = last.get("uplimit_hot") or {}
            if not uh.get("_error"):
                out["ladder"] = _build_ladder(uh)
            us = last.get("uplimit_stocks")
            if isinstance(us, dict):
                us = us.get("data") or us.get("items")
            if isinstance(us, list) and us:
                out["stocks"] = _dedup_uplimit_stocks(us)[:50]   # ★ 上游有重复行，见函数注释
            # ★ 2026-09-25 修复：降级分支**漏读了同一份快照里的 `review_uplimit_reason`**
            #   ⇒ `steps`（题材归因）在最常见的场景（"今天日批还没跑 ⇒ 退回昨日快照"）
            #   **永远是空的** —— 数据就在手上却没读（实测：stale=True 时 steps=0）。
            #   与上面的主分支口径对齐（同样 `[:20]`）。
            rs = last.get("review_uplimit_reason")
            if isinstance(rs, list) and rs:
                out["steps"] = rs[:20]
            if out["ladder"] or out["stocks"]:
                out["source"] = "snapshot"
                out["as_of"] = str(last.get("date") or "")[:10]
                out["stale"] = True          # ★ 明确标注"这是旧数据"
    except Exception as e:
        print(f"[market] limit-review stale fallback failed: {e}")   # ASCII（铁律⑥）
    out["stats"] = _limit_stats(d)
    return out
