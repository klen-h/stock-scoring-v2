# -*- coding: utf-8 -*-
from __future__ import annotations  # 兼容 Python 3.9（Render/Docker）：允许 -> dict | None 注解
"""
================================================================================
【文件作用】主力阶段识别（吸筹/洗盘/拉升/出货/下跌）—— 量价行为规则引擎
================================================================================

主力运作一只股票的完整路径：吸筹 → 洗盘 → 拉升 → 出货。
散户追"涨了没"，主力看"现在走到哪一步"：
  - 吸筹段跟随买入（时间换空间，等拉升）
  - 洗盘段持有/加仓（缩量回调不破位 = 主力没走）
  - 拉升段顺势持有（放量上攻）
  - 出货段离场（放量滞涨/放量下跌 = 筹码换手给散户）
  - 下跌段回避（无主力接管）

规则（互斥，按证据强度从上往下判）：
  拉升  ret_20 > +8%  且 放量(量比>1.3) 且 多头排列(close>MA20>MA60)
  出货  ret_60 > +25%（已在高位）且 放量(量比>1.2) 且 近5日滞涨/下跌(ret_5 < +1%)
  洗盘  ret_20 > +8%（涨过一波）且 缩量回调(ret_5<0, 量比<0.85) 且 未破MA60
  吸筹  ret_60 < +12%（低位横盘）且 均线粘合(|MA20/MA60-1|<3%) 且 OBV 20日上行
  下跌  close < MA60 且 MA20 < MA60
  盘整  其余

阈值是"初始专家版"：与 ADMISSION_MATRIX 同一方法论，先跑通，
再由 scripts/mainforce_factor_backtest.py 的分阶段收益数据回调。

实现：指标全序列预计算一次（向量化），逐日只跑规则——
      544 只 × 全历史截面回测秒级完成；detect_phase 单点口径复用同一套规则。
================================================================================
"""

import numpy as np

PHASE_CN = {"accumulation": "吸筹", "shakeout": "洗盘", "markup": "拉升",
            "distribution": "出货", "decline": "下跌", "sideways": "盘整"}


def _sma(a: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(a), np.nan)
    if len(a) >= n:
        c = np.cumsum(np.insert(a, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def _precompute(bars: list):
    """日线 → (dates, closes, 指标序列)。无效行剔除；<70 根返回 None。"""
    ds, c, h, v = [], [], [], []
    for b in bars or []:
        try:
            close = float(b.get("close") or 0)
            if close <= 0:
                continue
            ds.append(b.get("date"))
            c.append(close)
            h.append(float(b.get("high") or close))
            v.append(float(b.get("volume") or 0))
        except (TypeError, ValueError):
            continue
    if len(ds) < 70:
        return None
    closes = np.asarray(c)
    highs = np.asarray(h)
    vols = np.asarray(v)

    ma20, ma60 = _sma(closes, 20), _sma(closes, 60)
    vol5 = _sma(vols, 5)
    vol60 = _sma(vols, 60)
    obv = np.cumsum(np.sign(np.diff(closes, prepend=closes[:1])) * vols)

    def ret(n):
        r = np.full(len(closes), np.nan)
        r[n:] = (closes[n:] / closes[:-n] - 1) * 100
        return r

    return {"dates": ds, "closes": closes, "vols": vols,
            "ma20": ma20, "ma60": ma60, "vol5": vol5, "vol60": vol60,
            "obv": obv, "ret5": ret(5), "ret20": ret(20), "ret60": ret(60)}


def _rule(px: float, ma20: float, ma60: float, vol_ratio: float,
          ret_5: float, ret_20: float, ret_60: float, obv_up: bool) -> str:
    """阶段判定规则（所有阈值集中在此，便于回测回调）。"""
    if np.isnan(ma20) or np.isnan(ma60) or not np.isfinite(ret_20):
        return "sideways"
    if ret_20 > 8 and vol_ratio > 1.3 and px > ma20 > ma60:
        return "markup"
    if ret_60 > 25 and vol_ratio > 1.2 and ret_5 < 1:
        return "distribution"
    if ret_20 > 8 and ret_5 < 0 and vol_ratio < 0.85 and px > ma60:
        return "shakeout"
    if ret_60 < 12 and abs(ma20 / ma60 - 1) < 0.03 and obv_up and vol_ratio < 1.2:
        return "accumulation"
    if px < ma60 and ma20 < ma60:
        return "decline"
    return "sideways"


def phase_at(pre: dict, i: int) -> dict | None:
    """在预计算结果上取第 i 日的阶段与明细。i < 69 返回 None。"""
    if pre is None or i < 69:
        return None
    closes, vols = pre["closes"], pre["vols"]
    ma20, ma60 = float(pre["ma20"][i]), float(pre["ma60"][i])
    v5, v60 = float(pre["vol5"][i]), float(pre["vol60"][i])
    if np.isnan(ma20) or np.isnan(ma60) or v60 <= 0 or np.isnan(v5):
        return None
    vol_ratio = v5 / v60
    obv_up = bool(pre["obv"][i] > pre["obv"][i - 20])
    ret_5 = float(pre["ret5"][i])
    ret_20 = float(pre["ret20"][i])
    ret_60 = float(pre["ret60"][i]) if not np.isnan(pre["ret60"][i]) else 0.0
    phase = _rule(float(closes[i]), ma20, ma60, vol_ratio,
                  ret_5, ret_20, ret_60, obv_up)
    return {
        "phase": phase, "phase_cn": PHASE_CN[phase],
        "ret_5": round(ret_5, 2), "ret_20": round(ret_20, 2), "ret_60": round(ret_60, 2),
        "vol_ratio": round(vol_ratio, 2), "obv_slope": int(obv_up),
        "ma20": round(ma20, 3), "ma60": round(ma60, 3),
    }


def detect_phase(bars: list, index: int = None) -> dict | None:
    """单点口径：判定第 index 根（缺省最后一根）K线的主力阶段。"""
    pre = _precompute(bars)
    if pre is None:
        return None
    i = len(pre["dates"]) - 1 if index is None else index
    return phase_at(pre, i)


def phase_series(bars: list, dates_out: list = None) -> dict:
    """整段历史逐日阶段（回测截面用）。dates_out 限采样日省算力。"""
    pre = _precompute(bars)
    if pre is None:
        return {}
    dates = pre["dates"]
    want = set(dates_out) if dates_out is not None else None
    out = {}
    for i in range(69, len(dates)):
        d = dates[i]
        if want is not None and d not in want:
            continue
        m = phase_at(pre, i)
        if m:
            out[d] = m
    return out
