"""
================================================================================
【文件作用】市场状态判定引擎：基于沪深300指数判定当前处于进攻/震荡/防御
================================================================================
判定逻辑（多层过滤器，避免状态频繁跳动）：
  1. 趋势方向：MA20 vs MA60 交叉 + 价格在均线上方/下方
  2. 趋势强度：ADX(14) 过滤弱趋势（ADX<20 视为无趋势→震荡）
  3. 波动率：ATR(14)/Close 相对历史分位，识别极端波动
  4. 状态平滑：Hysteresis（滞后切换），避免在边界反复横跳

输出状态：
  - "offensive"  进攻：趋势向上 + 强度足够 + 波动正常
  - "neutral"    震荡：无明确趋势（ADX低）或价格在均线夹层
  - "defensive"  防御：趋势向下 或 波动极端 或 跌破关键支撑
================================================================================
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

# 状态常量
OFFENSIVE = "offensive"
NEUTRAL = "neutral"
DEFENSIVE = "defensive"
# 震荡偏空（2026-09-03 新增）：ADX 弱趋势 + MA 向下 + 宽度恶化/连续阴跌。
# ★ 仅作状态标签与仓位警示，权重与 neutral 相同 —— 防御化权重已被回测否决
#   （450 条快照双持有期口径，现震荡档全面最优，见 PLAN_REGIME_BEARISH.md）
NEUTRAL_BEARISH = "neutral_bearish"

# 判定参数（可调）
ADX_THRESHOLD = 20.0          # ADX < 20 视为无趋势（震荡）
ADX_STRONG = 35.0             # ADX > 35 视为强趋势
VOLATILITY_HIGH_PCT = 80.0    # ATR分位 > 80% 视为高波动
VOLATILITY_LOW_PCT = 20.0     # ATR分位 < 20% 视为低波动
HYSTERESIS_DAYS = 3           # 状态切换最少持续天数（防抖）


@dataclass
class MarketState:
    """单日市场状态快照"""
    date: str
    state: str                      # offensive / neutral / defensive
    regime_score: float             # -100 ~ +100，连续值（用于更细粒度权重插值）
    adx: float
    ma20: float
    ma60: float
    atr14: float
    atr_percentile: float           # ATR 在历史窗口中的分位
    price_vs_ma20: float            # 价格偏离 MA20 百分比
    price_vs_ma60: float            # 价格偏离 MA60 百分比
    ma_trend: str                   # "up" / "down" / "flat"
    volatility_regime: str          # "high" / "normal" / "low"


# ------------------------------------------------------------------------------
#  技术指标计算（纯 NumPy，不依赖 TA-Lib）
# ------------------------------------------------------------------------------

def _sma(arr: np.ndarray, period: int) -> np.ndarray:
    """简单移动平均，前 period-1 个位置填 nan"""
    if len(arr) < period:
        return np.full_like(arr, np.nan, dtype=float)
    weights = np.ones(period) / period
    return np.concatenate((np.full(period - 1, np.nan), np.convolve(arr, weights, mode='valid')))


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """指数移动平均"""
    if len(arr) < period:
        return np.full_like(arr, np.nan, dtype=float)
    alpha = 2.0 / (period + 1)
    result = np.empty_like(arr, dtype=float)
    result[:period] = np.nan
    result[period - 1] = np.mean(arr[:period])
    for i in range(period, len(arr)):
        result[i] = alpha * arr[i] + (1 - alpha) * result[i - 1]
    return result


def _atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> np.ndarray:
    """Average True Range"""
    if len(close) < 2:
        return np.full_like(close, np.nan, dtype=float)
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.maximum(np.maximum(tr1, tr2), tr3)
    tr_full = np.concatenate(([np.nan], tr))
    return _ema(tr_full, period)


def _adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int = 14) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    返回 (ADX, +DI, -DI)。
    算法：Wilder 平滑版。
    """
    n = len(close)
    if n < period + 1:
        return (np.full(n, np.nan), np.full(n, np.nan), np.full(n, np.nan))

    # True Range
    tr1 = high[1:] - low[1:]
    tr2 = np.abs(high[1:] - close[:-1])
    tr3 = np.abs(low[1:] - close[:-1])
    tr = np.concatenate(([np.nan], np.maximum(np.maximum(tr1, tr2), tr3)))

    # +DM / -DM
    up = high[1:] - high[:-1]
    down = low[:-1] - low[1:]
    plus_dm = np.concatenate(([np.nan], np.where((up > down) & (up > 0), up, 0)))
    minus_dm = np.concatenate(([np.nan], np.where((down > up) & (down > 0), down, 0)))

    # Wilder smoothing
    atr = _wilder_smooth(tr, period)
    plus_di = 100.0 * _wilder_smooth(plus_dm, period) / atr
    minus_di = 100.0 * _wilder_smooth(minus_dm, period) / atr

    dx = np.where((plus_di + minus_di) > 0, 100.0 * np.abs(plus_di - minus_di) / (plus_di + minus_di), 0)
    adx = _wilder_smooth(dx, period)
    return adx, plus_di, minus_di


def _wilder_smooth(arr: np.ndarray, period: int) -> np.ndarray:
    """Wilder 平滑：首值 = SMA，后续 = 前值 * (period-1)/period + 今日值/period"""
    n = len(arr)
    out = np.full(n, np.nan, dtype=float)
    # 找到第一个非 nan 位置
    valid_start = 0
    while valid_start < n and np.isnan(arr[valid_start]):
        valid_start += 1
    if valid_start + period > n:
        return out
    first_val = np.nanmean(arr[valid_start:valid_start + period])
    out[valid_start + period - 1] = first_val
    for i in range(valid_start + period, n):
        if not np.isnan(arr[i]):
            out[i] = out[i - 1] * (period - 1) / period + arr[i] / period
    return out


def _rolling_percentile(arr: np.ndarray, window: int) -> np.ndarray:
    """滚动历史分位（0~100），每个位置看过去 window 天的数据（含自己）"""
    n = len(arr)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        start = max(0, i - window + 1)
        window_data = arr[start:i + 1]
        valid = window_data[~np.isnan(window_data)]
        if len(valid) < window // 2:
            continue
        out[i] = 100.0 * np.sum(valid < arr[i]) / len(valid) if len(valid) > 0 else 50.0
    return out


# ------------------------------------------------------------------------------
#  状态判定核心
# ------------------------------------------------------------------------------

def detect_market_regime(bars: List[dict]) -> List[MarketState]:
    """
    输入：沪深300日线 bars，按日期升序排列
          [{date, open, high, low, close, volume}, ...]
    输出：每日 MarketState 列表
    """
    if len(bars) < 70:  # 需要足够数据计算 MA60 + ADX
        return []

    dates = [b["date"] for b in bars]
    opens = np.array([b["open"] for b in bars], dtype=float)
    highs = np.array([b["high"] for b in bars], dtype=float)
    lows = np.array([b["low"] for b in bars], dtype=float)
    closes = np.array([b["close"] for b in bars], dtype=float)

    # 技术指标
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    atr14 = _atr(highs, lows, closes, 14)
    adx, plus_di, minus_di = _adx(highs, lows, closes, 14)

    # ATR 百分位（60 日窗口）
    atr_pct = _rolling_percentile(atr14, 60)

    # 价格偏离均线
    price_vs_ma20 = np.where(ma20 > 0, (closes - ma20) / ma20 * 100, np.nan)
    price_vs_ma60 = np.where(ma60 > 0, (closes - ma60) / ma60 * 100, np.nan)

    states = []
    raw_states = []

    for i in range(len(bars)):
        # 基础数据不足时跳过
        if np.isnan(ma20[i]) or np.isnan(ma60[i]) or np.isnan(adx[i]):
            continue

        # --- 第一层：MA 趋势方向 ---
        if ma20[i] > ma60[i] * 1.005:
            ma_trend = "up"
        elif ma20[i] < ma60[i] * 0.995:
            ma_trend = "down"
        else:
            ma_trend = "flat"

        # --- 第二层：ADX 趋势强度 ---
        trend_strength = "weak"
        if adx[i] >= ADX_STRONG:
            trend_strength = "strong"
        elif adx[i] >= ADX_THRESHOLD:
            trend_strength = "moderate"

        # --- 第三层：波动率 ---
        vol_pct = atr_pct[i] if not np.isnan(atr_pct[i]) else 50.0
        if vol_pct >= VOLATILITY_HIGH_PCT:
            vol_regime = "high"
        elif vol_pct <= VOLATILITY_LOW_PCT:
            vol_regime = "low"
        else:
            vol_regime = "normal"

        # --- 综合判定（状态机）---
        # 默认：震荡
        state = NEUTRAL
        regime_score = 0.0

        # 高波动 + 趋势向下 → 防御（恐慌/暴跌）
        if vol_regime == "high" and ma_trend == "down":
            state = DEFENSIVE
            regime_score = -70.0 - min(30.0, vol_pct - VOLATILITY_HIGH_PCT)
        # 强趋势向上 + 波动正常 → 进攻
        elif ma_trend == "up" and trend_strength in ("strong", "moderate") and vol_regime != "high":
            state = OFFENSIVE
            regime_score = 50.0 + min(50.0, adx[i])
        # 强趋势向下 + 波动正常 → 防御
        elif ma_trend == "down" and trend_strength in ("strong", "moderate"):
            state = DEFENSIVE
            regime_score = -50.0 - min(50.0, adx[i])
        # ADX 极弱 → 震荡（无论 MA 方向）
        elif trend_strength == "weak":
            state = NEUTRAL
            regime_score = 0.0
        # 价格在 MA 夹层（MA20>MA60 但价格跌破 MA20；或 MA20<MA60 但价格站上 MA20）
        elif ma_trend == "up" and price_vs_ma20[i] < -2:
            state = NEUTRAL  # 多头趋势中的回调，先观望
            regime_score = 10.0
        elif ma_trend == "down" and price_vs_ma20[i] > 2:
            state = NEUTRAL  # 空头趋势中的反弹，先观望
            regime_score = -10.0
        # 低波动 + 价格贴近均线 → 震荡
        elif vol_regime == "low" and abs(price_vs_ma20[i]) < 1.5:
            state = NEUTRAL
            regime_score = 0.0

        raw_states.append({
            "date": dates[i],
            "state": state,
            "regime_score": round(regime_score, 2),
            "adx": round(adx[i], 2),
            "ma20": round(ma20[i], 2),
            "ma60": round(ma60[i], 2),
            "atr14": round(atr14[i], 4) if not np.isnan(atr14[i]) else None,
            "atr_percentile": round(vol_pct, 1),
            "price_vs_ma20": round(price_vs_ma20[i], 2) if not np.isnan(price_vs_ma20[i]) else None,
            "price_vs_ma60": round(price_vs_ma60[i], 2) if not np.isnan(price_vs_ma60[i]) else None,
            "ma_trend": ma_trend,
            "volatility_regime": vol_regime,
        })

    # --- Hysteresis 平滑：防止状态在边界高频切换 ---
    smoothed = _apply_hysteresis(raw_states, min_days=HYSTERESIS_DAYS)

    for s in smoothed:
        states.append(MarketState(
            date=s["date"],
            state=s["state"],
            regime_score=s["regime_score"],
            adx=s["adx"],
            ma20=s["ma20"],
            ma60=s["ma60"],
            atr14=s["atr14"] or 0.0,
            atr_percentile=s["atr_percentile"],
            price_vs_ma20=s["price_vs_ma20"] or 0.0,
            price_vs_ma60=s["price_vs_ma60"] or 0.0,
            ma_trend=s["ma_trend"],
            volatility_regime=s["volatility_regime"],
        ))

    return states


def _apply_hysteresis(raw_states: List[dict], min_days: int = 3) -> List[dict]:
    """
    滞后平滑：状态切换后至少维持 min_days 天，除非连续 min_days 都是新状态。
    简单实现： majority vote 在 min_days 滑动窗口。
    """
    if not raw_states:
        return []
    if len(raw_states) < min_days:
        return raw_states

    n = len(raw_states)
    # 先做一次滑动窗口多数决
    smoothed_states = []
    for i in range(n):
        if i < min_days - 1:
            smoothed_states.append(raw_states[i]["state"])
            continue
        window = [raw_states[j]["state"] for j in range(i - min_days + 1, i + 1)]
        # 统计各状态出现次数
        counts = defaultdict(int)
        for s in window:
            counts[s] += 1
        # 取最多，平局保持当前
        max_state = max(counts, key=counts.get)
        smoothed_states.append(max_state)

    # 再合并：合并连续相同状态
    result = []
    for i, rs in enumerate(raw_states):
        rs_copy = dict(rs)
        rs_copy["state"] = smoothed_states[i]
        result.append(rs_copy)
    return result


def _market_breadth_now() -> Optional[dict]:
    """当日市场宽度（涨跌家数/涨跌停）。

    取自行情内存缓存（主服务常驻，盘后仍持有当日收盘快照）；
    独立脚本/缓存为空时返回 None，由调用方降级处理。
    """
    try:
        from app.routers.market import _cache
        stocks = _cache.get("stocks") or {}
        if not stocks:
            return None
        up = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) > 0)
        down = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) < 0)
        limit_up = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) >= 9.9)
        limit_down = sum(1 for s in stocks.values() if (s.get("change_pct") or 0) <= -9.9)
        return {"up": up, "down": down, "limit_up": limit_up,
                "limit_down": limit_down, "up_ratio": up / max(1, up + down)}
    except Exception:
        return None


def _external_panic() -> tuple:
    """外围恐慌（三选二，2026-09-03 新增：外部冲击主导的震荡偏空识别）。

    数据源：宏观面板缓存（get_macro_panel，异常时返回不触发）：
      - 日经单日 < -2%（亚太风险资产共振下跌）
      - 布伦特 > +2% 且 黄金 < -1%（滞胀型紧缩：油胀金跌 = 实际利率上行）
      - 美债 10Y 单日上行 > 5bp（全球贴现率冲击）
    """
    try:
        from app.macro import get_macro_panel
        p = get_macro_panel() or {}
        d = p.get("derived") or {}
        hits = []
        nk = p.get("nikkei") or {}
        if (nk.get("change_pct") or 0) < -2:
            hits.append(f"日经{nk['change_pct']:.1f}%")
        oil, gold = p.get("brent") or {}, p.get("gold") or {}
        if (oil.get("change_pct") or 0) > 2 and (gold.get("change_pct") or 0) < -1:
            hits.append(f"油{oil['change_pct']:.1f}%/金{gold['change_pct']:.1f}%")
        bp = d.get("us10y_bp_change") or p.get("us10y_bp_change")
        if bp is not None and bp > 5:
            hits.append(f"美债10Y+{bp:.0f}bp")
        return (len(hits) >= 2, "、".join(hits))
    except Exception:
        return (False, "")


def _market_shrink_ratio() -> Optional[float]:
    """市场缩量度：沪深300 当日额 / 20 日均额（<0.7 视为极端缩量）。

    用 close×volume 作代理（比值与量纲无关）。仅供警示（detail 输出 +
    日志），不改个股打分 —— _score_amount 本就是自相对指标，无需调整。
    """
    try:
        from app.database import db
        rows = db.fetch("SELECT close, volume FROM backtest_prices "
                        "WHERE code='sh000300' ORDER BY date DESC LIMIT 21")
        if not rows or len(rows) < 21:
            return None
        amounts = [r["close"] * r["volume"] for r in rows]
        avg20 = sum(amounts[1:]) / 20
        return round(amounts[0] / avg20, 2) if avg20 > 0 else None
    except Exception:
        return None


def _apply_bearish_refine(state: str, ma_trend: str = "") -> str:
    """neutral + 重心下移 → neutral_bearish（仅状态标签，权重与 neutral 相同）。

    判据（前提：state==neutral 且 ma_trend==down，满足其一即命中）：
      1. 宽度恶化：上涨占比 < 0.40，或 跌停 ≥ 20 且跌停 > 涨停
      2. 外围恐慌（三选二）：日京 -2% / 油胀金跌 / 美债 10Y +5bp
      3. 降级判据（宽度缓存不可用）：沪深300 近 2 个交易日累计下跌
    """
    if state != NEUTRAL or ma_trend != "down":
        return state
    br = _market_breadth_now()
    if br and (br["up_ratio"] < 0.40
               or (br["limit_down"] >= 20 and br["limit_down"] > br["limit_up"])):
        print(f"[market_regime] 宽度恶化（涨{br['up']}/跌{br['down']} "
              f"涨停{br['limit_up']}/跌停{br['limit_down']}）→ neutral_bearish")
        return NEUTRAL_BEARISH
    panic, why = _external_panic()
    if panic:
        print(f"[market_regime] 外围恐慌触发（{why}）→ neutral_bearish")
        return NEUTRAL_BEARISH
    try:
        from app.database import db
        rows = db.fetch("SELECT close FROM backtest_prices "
                        "WHERE code='sh000300' ORDER BY date DESC LIMIT 3")
        if rows and len(rows) >= 3 and rows[0]["close"] < rows[2]["close"]:
            print("[market_regime] 沪深300 近2日累计下跌（宽度缓存不可用，降级判据）"
                  " → neutral_bearish")
            return NEUTRAL_BEARISH
    except Exception:
        pass
    return state


def get_regime_weights(state: str) -> dict:
    """
    根据市场状态返回五维度权重（和 = 1.0）。
    返回值: {"technical", "capital", "fundamental", "growth", "quality"}

    各状态的侧重逻辑：
      - 进攻市（牛市）：重技术动量与资金追逐，估值不重要 → 技术/资金占 75%
      - 震荡市：★ 2026-09-02 权重优化对照落地 —— 基于 350 条已验证快照
        （2026-08-20~08-31，7 个交易日全为震荡市）的"只改权重不改指标"对照：
        每日 Top10 模拟选股，本档（27/23/23/17/11）胜率 64.3%/+1.42%，
        优于静态默认 32/20/18/18/12（60.0%/+1.24%）与旧震荡档 25/30/15/18/12
        （62.9%/+1.19%）。核心变化：技术面 25→27 之外的相对下调、基本面 15→23
        （震荡市技术面预测力≈0、基本面最强 +0.109）。★ 仅覆盖震荡市，
        趋势/防御档未验证保持原样；待样本覆盖其他市况后复核。
      - 防御市（熊市）：★ 质量权重拉到 35% —— 下跌市里财务健康、低负债的公司
        抗跌性最强；同时估值安全边际（基本面）权重提高，成长权重相应降低
        （熊市市场不为成长故事付费）。
    """
    weights = {
        OFFENSIVE:  {"technical": 0.50, "capital": 0.25, "fundamental": 0.08,
                     "growth": 0.12, "quality": 0.05},
        NEUTRAL:    {"technical": 0.27, "capital": 0.23, "fundamental": 0.23,
                     "growth": 0.17, "quality": 0.11},
        # 与 neutral 相同：2026-09-03 回测（450 条快照，持有 2/5 日两口径）
        # 防御化权重均跑输现震荡档，标签仅用于展示/仓位警示
        NEUTRAL_BEARISH: {"technical": 0.27, "capital": 0.23, "fundamental": 0.23,
                          "growth": 0.17, "quality": 0.11},
        DEFENSIVE:  {"technical": 0.12, "capital": 0.15, "fundamental": 0.25,
                     "growth": 0.13, "quality": 0.35},
    }
    return weights.get(state, weights[NEUTRAL])


def get_regime_description(state: str) -> str:
    """市场状态中文描述"""
    desc = {
        OFFENSIVE: "进攻型市场（牛市/强势上涨）",
        NEUTRAL:   "震荡型市场（盘整/无方向）",
        NEUTRAL_BEARISH: "震荡偏空（无强趋势但重心下移，反弹宜减不宜追）",
        DEFENSIVE: "防御型市场（熊市/弱势下跌）",
    }
    return desc.get(state, "未知")


# ------------------------------------------------------------------------------
#  辅助：从数据库加载沪深300并判定（供后端调用）
# ------------------------------------------------------------------------------

def load_regime_history() -> List[MarketState]:
    """从 backtest_prices 加载沪深300历史数据并判定状态序列。"""
    from app.backtest import data
    bars = data.load_prices("sh000300")
    if not bars:
        return []
    return detect_market_regime(bars)


def get_current_regime() -> Optional[MarketState]:
    """返回最新一日的市场状态。"""
    states = load_regime_history()
    return states[-1] if states else None


# ------------------------------------------------------------------------------
#  当日状态缓存（生产评分动态权重 + 周报摘要共用）
# ------------------------------------------------------------------------------
_REGIME_CACHE: dict = {"date": None, "state": None, "weights": None, "detail": None}


def _ensure_history_table() -> None:
    """确保市场状态追踪表存在（幂等，兼容 PostgreSQL / SQLite）。"""
    from app.database import db
    db.execute("""
        CREATE TABLE IF NOT EXISTS market_regime_history (
            id SERIAL PRIMARY KEY,
            date TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            regime_score REAL,
            adx REAL,
            ma_trend TEXT,
            volatility_regime TEXT,
            weights_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)


def refresh_regime_cache() -> Optional[dict]:
    """
    盘后计算最新市场状态并缓存（同步函数，供调度器线程池调用）。

    数据源：backtest_prices 中的沪深300（由回测价格回填任务先写入当日数据）；
    数据未就绪时返回 None 并保留旧缓存，由调度器窗口内重试。
    成功时同步落库 market_regime_history（按日期覆盖），供状态切换追踪。
    """
    states = load_regime_history()
    if not states:
        return None
    latest = states[-1]
    # neutral + 重心下移 + 宽度恶化 → neutral_bearish（仅标签，权重同 neutral）
    refined_state = _apply_bearish_refine(latest.state, latest.ma_trend)
    _REGIME_CACHE["date"] = latest.date
    _REGIME_CACHE["state"] = refined_state
    _REGIME_CACHE["weights"] = get_regime_weights(refined_state)
    _REGIME_CACHE["detail"] = {
        "regime_score": latest.regime_score,
        "adx": latest.adx,
        "ma_trend": latest.ma_trend,
        "volatility_regime": latest.volatility_regime,
    }
    # 极端缩量警示（仅 detail 输出 + 日志，不改打分）：sh000300 当日额 < 20日均额 70%
    shrink = _market_shrink_ratio()
    if shrink is not None:
        _REGIME_CACHE["detail"]["volume_ratio_20d"] = shrink
        if shrink < 0.7:
            print(f"[market_regime] ⚠️ 极端缩量（沪深300 当日额=20日均额的 "
                  f"{shrink:.0%}）：技术面信号可信度下降，建议降低仓位")
    try:
        _ensure_history_table()
        from app.database import db
        db.upsert("market_regime_history", {
            "date": latest.date,
            "state": refined_state,
            "regime_score": latest.regime_score,
            "adx": latest.adx,
            "ma_trend": latest.ma_trend,
            "volatility_regime": latest.volatility_regime,
            "weights_json": str(_REGIME_CACHE["weights"]),
        }, conflict_columns=["date"])
    except Exception as e:
        print(f"[market_regime] 状态落库失败: {e}")
    print(f"[market_regime] 市场状态 {latest.date}: {refined_state} "
          f"权重={_REGIME_CACHE['weights']}")
    return dict(_REGIME_CACHE)


def get_regime_cache() -> dict:
    """读取当日市场状态缓存（未生成时返回空 dict，调用方回退默认/静态权重）。"""
    return dict(_REGIME_CACHE)
