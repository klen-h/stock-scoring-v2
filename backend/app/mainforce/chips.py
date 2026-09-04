# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】筹码分布引擎（CYQ 轮动模型）—— 主力思维的核心工具
================================================================================

主力思维的第一个问题不是"涨没涨"，而是"筹码在谁手里"：
  - 低位 + 筹码密集 + 获利盘低  → 主力吸筹区（散户割肉的筹码被主力接走）
  - 拉升后 + 高位密集 + 获利盘高 → 主力派发区（主力把筹码换给追高的散户）
  - 筹码集中度（90% 成本区间宽度）≈ 主力控盘度：越窄越集中

算法（经典 CYQ 近似，每日轮动）：
  1. 把每根K线的成交量均匀摊到 [low, high] 的价格网格上
  2. 每日按真实换手率衰减老筹码：chips *= (1 - turnover)，换手=量/流通股本
     （无流通股本时降级：量 / MA120量 × 0.02 基准换手，精度略降）
  3. 当日成交量摊入价格网格
  4. 在最新收盘价处读出：获利盘、平均成本、P5/P95 分位、集中度

输出指标（全部"越大越危险/越接近派发"或标注方向）：
  winner_ratio    获利盘比例 0~1：现价下方筹码占比
  cost_bias       现价相对平均成本溢价 %（>0 说明全市场平均浮盈）
  concentration   筹码集中度 0~1：(P95-P5)/(P95+P5)，越小越集中（主力控盘）
  price_pos       现价在 90% 筹码区间内的位置 0~1（0=区间底，1=区间顶）
  turnover_20     近 20 日累计换手（吸筹/派发的活跃度参照）

纯函数 + numpy：评分引擎、离线回测、脚本共用，不依赖网络/DB。
================================================================================
"""

import numpy as np

# 网格精度：150 档足够分辨密集/发散（计算量 750根×150档 每只股票毫秒级）
N_BINS = 150
# 无流通股本时的基准日换手（A股全市场中位数约 2%）；用相对量缩放
BASE_TURNOVER = 0.02
# 单日换手率截断：防止极端放量把筹码一次性清空（除权日/ inserts 亦被截断）
TURNOVER_CAP = 0.40
TURNOVER_FLOOR = 0.0005


def _to_bars_np(bars: list):
    """bars → (dates, open, high, low, close, volume) numpy 数组，升序有效行。"""
    ds, o, h, l, c, v = [], [], [], [], [], []
    for b in bars or []:
        try:
            vol = float(b.get("volume") or 0)
            close = float(b.get("close") or 0)
            high = float(b.get("high") or 0)
            low = float(b.get("low") or 0)
            if close <= 0 or high <= 0 or low <= 0 or vol < 0:
                continue
            ds.append(b.get("date"))
            o.append(float(b.get("open") or close))
            h.append(high)
            l.append(low)
            c.append(close)
            v.append(vol)
        except (TypeError, ValueError):
            continue
    if len(ds) < 30:
        return None
    return (ds, np.asarray(o), np.asarray(h), np.asarray(l),
            np.asarray(c), np.asarray(v))


def chip_series(bars: list, float_shares: float = None,
                n_bins: int = N_BINS, warmup: int = 60,
                dates_out: list = None) -> dict:
    """
    对整段日线滚动计算每日筹码指标。

    bars: [{date, open, high, low, close, volume}] 升序（建议 ≥120 根）
    float_shares: 流通股本（股）。缺省时用相对量代理换手。
    dates_out: 传入列表时，只对这些日期产出快照（省内存）；None=全部

    返回 {date: metrics_dict}；数据不足返回 {}。
    """
    parsed = _to_bars_np(bars)
    if parsed is None:
        return {}
    dates, _, highs, lows, closes, vols = parsed

    # 价格网格覆盖全历史（除权跳空会造成网格跨度大 → 精度下降，可接受）
    p_min = float(lows.min()) * 0.995
    p_max = float(highs.max()) * 1.005
    if p_max <= p_min:
        return {}
    edges = np.linspace(p_min, p_max, n_bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    bin_w = (p_max - p_min) / n_bins

    # 无流通股本 → 相对量代理：量/MA120量 × 基准换手
    ma_vol = None
    if not float_shares or float_shares <= 0:
        ma_vol = np.convolve(vols, np.ones(120) / 120.0, mode="same")

    chips = np.zeros(n_bins)
    out = {}
    want = set(dates_out) if dates_out is not None else None

    for i in range(len(dates)):
        vol = vols[i]
        if float_shares and float_shares > 0:
            tr = vol / float_shares
        else:
            base = ma_vol[i] if (ma_vol is not None and ma_vol[i] > 0) else vols[:i + 1].mean()
            tr = (vol / base * BASE_TURNOVER) if base > 0 else 0.0
        tr = min(max(tr, TURNOVER_FLOOR), TURNOVER_CAP) if vol > 0 else 0.0

        if vol > 0:
            chips *= (1.0 - tr)  # 老筹码按换手置换
            # 当日量摊入 [low, high]（均匀近似）
            lo = float(lows[i]); hi = float(highs[i])
            j_lo = int(np.clip((lo - p_min) / bin_w, 0, n_bins - 1))
            j_hi = int(np.clip((hi - p_min) / bin_w, 0, n_bins - 1))
            if j_hi > j_lo:
                per = vol / (j_hi - j_lo + 1)
                chips[j_lo:j_hi + 1] += per
            else:
                chips[j_lo] += vol

        if i < warmup:
            continue
        d = dates[i]
        if want is not None and d not in want:
            continue
        m = _metrics_at(chips, centers, float(closes[i]))
        if m:
            out[d] = m
    return out


def chip_metrics(bars: list, float_shares: float = None) -> dict | None:
    """单点口径：返回最新一根K线的筹码指标（供评分引擎实时用）。"""
    s = chip_series(bars, float_shares, dates_out=None)
    if not s:
        return None
    return s.get(bars[-1].get("date")) if isinstance(bars[-1], dict) else None


def _metrics_at(chips: np.ndarray, centers: np.ndarray, close: float) -> dict | None:
    """在收盘价处读筹码结构。"""
    total = chips.sum()
    if total <= 0 or close <= 0:
        return None

    winner = float(chips[centers < close].sum() / total)

    avg_cost = float((chips * centers).sum() / total)
    cost_bias = (close / avg_cost - 1.0) * 100 if avg_cost > 0 else 0.0

    # P5 / P95 分位（累计分布线性插值）
    cdf = np.cumsum(chips) / total
    def pct(q: float) -> float:
        idx = int(np.searchsorted(cdf, q))
        idx = min(max(idx, 1), len(centers) - 1)
        c0, c1 = cdf[idx - 1], cdf[idx]
        if c1 <= c0:
            return float(centers[idx])
        w = (q - c0) / (c1 - c0)
        return float(centers[idx - 1] + (centers[idx] - centers[idx - 1]) * w)

    p5, p95 = pct(0.05), pct(0.95)
    span = p95 - p5
    concentration = (p95 - p5) / (p95 + p5) if (p95 + p5) > 0 else 1.0
    price_pos = (close - p5) / span if span > 0 else 0.5

    return {
        "winner_ratio": round(winner, 4),
        "cost_bias": round(cost_bias, 2),
        "avg_cost": round(avg_cost, 3),
        "concentration": round(concentration, 4),
        "price_pos": round(min(max(price_pos, 0.0), 1.0), 4),
        "p5": round(p5, 3), "p95": round(p95, 3),
    }
