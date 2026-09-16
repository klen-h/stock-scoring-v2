# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】财报公告后的「衰减重加权」——旁路灰度专用，**不改生产排序**（2026-09-17）
================================================================================

背景：
  事件研究（scripts/quality_defense_backtest.py --event）证明——财报公告后
  0-20 天，成长/质量因子横截面 IC 为负（利好兑现/拉升出货）；21-60 天转正。
  即「公告后短窗口，这两维信号反向」。

两种口径（**单一事实源**，供后端 shadow-rank 接口与 scripts/shadow_decay_ranking.py 共用）：

  · gradient（默认，2026-09-17 二版）：按公告龄**梯度**调整成长/质量的**权重乘数**——
      0-5 天 → 0.0（剔除，信号最强反向）
      6-20 天 → 0.5（降权一半，信号中等）
      >20 天 → 1.0（不罚，信号已转正）
    语义中性（"信号不可信 → 不看这两维"，权重分摊给其余维度），分数尺度连续。

  · zero（初版，仅作对照）：公告后 ≤25 天成长/质量分 ×0（**权重保留**）。
    语义是"判 0 分"，会让总分暴跌约 40 分、把刚公告股全压到 30-40 分档、尺度割裂。
    实测 000612（age 19、成长 76.7/质量 86.3、技术 53/资金 54）：base 73.7 →
    zero 33.4（暴跌）vs gradient 70.4（温和）。切换 2026-09-17：主版本由 zero 改 gradient。
================================================================================
"""
from typing import Dict, Optional

# 中文维度名 → get_regime_weights 英文键
DIM_TO_KEY = {"技术面": "technical", "资金面": "capital", "基本面": "fundamental",
              "成长": "growth", "质量": "quality"}
DECAY_DIMS = ("成长", "质量")

# gradient 口径：公告龄上限 → 成长/质量维度权重乘数
_DECAY_GRADIENT = ((5, 0.0), (20, 0.5))
# zero 口径（对照）：公告后 ≤ 该天数，成长/质量分 ×0
_ZERO_DAYS = 25

# variant 名（shadow_rank_daily.variant 取值）
VARIANT_BASE = "base"     # 不衰减（生产口径）
VARIANT_ZERO = "zero"     # ×0 归零（初版，对照）
VARIANT_GRAD = "grad"     # 梯度剔除（二版，主）


def resolve_weights():
    """当前市况的五维权重（英文键）。返回 (state, weights)，供接口与脚本共用。

    ★ 优先级：regime 内存缓存 → market_regime_history 最新行 → 引擎默认权重。
      独立进程（日批/脚本）的 regime 缓存常为空，必须回退历史表——否则会静默
      用默认权重（growth 0.18/quality 0.12）算出与生产（defensive 0.13/0.35）
      不同的榜单（2026-09-17 实测踩坑：接口与脚本权重不一致 → 榜单对不上）。
    """
    state = ""
    try:
        from app.backtest.market_regime import get_regime_cache, get_regime_weights
        state = (get_regime_cache() or {}).get("state") or ""
        if not state:
            from app.database import db
            row = db.fetch_one(
                "SELECT state FROM market_regime_history ORDER BY date DESC LIMIT 1")
            state = (row or {}).get("state") or ""
        w = get_regime_weights(state) if state else None
        if w:
            return state, w
    except Exception:
        pass
    from app.scoring.engine import ScoreEngine
    return state, dict(ScoreEngine.DEFAULT_WEIGHTS)


def dim_weight_mult(name: str, age: Optional[int]) -> float:
    """成长/质量维度在给定公告龄下的权重乘数（其它维度恒 1.0）。"""
    if name not in DECAY_DIMS or age is None:
        return 1.0
    for hi, mult in _DECAY_GRADIENT:
        if age <= hi:
            return mult
    return 1.0


def decay_total(dims: Dict, weights: Dict, age: Optional[int],
                mode: str = VARIANT_GRAD) -> Optional[float]:
    """衰减版总分：复刻 engine._combine 的加权求和，按口径调整成长/质量。

    参数：
      dims    五维分 {中文名: 0-100}（缺失维度值为 None）
      weights 当前市况权重 {英文键: float}（get_regime_weights）
      age     财报公告后天数（None=无财报 → 不衰减）
      mode    "grad"（默认，权重乘数梯度）| "zero"（分数 ×0 对照）| "base"（不衰减）

    ★ mode 取值与 variant 名一致（VARIANT_*），避免字符串错配导致静默不衰减
      （2026-09-17 实测踩坑：曾用 "gradient" 与 VARIANT_GRAD="grad" 不匹配）。

    返回衰减后总分（0-100 同尺度）；无有效维度则 None。
    """
    d = dict(dims or {})
    if mode == VARIANT_ZERO and age is not None and age <= _ZERO_DAYS:
        for k in DECAY_DIMS:
            if d.get(k) is not None:
                d[k] = 0.0
    valid = []
    for name, score in d.items():
        if score is None:
            continue
        w = (weights or {}).get(DIM_TO_KEY.get(name, ""))
        if w is None or w <= 0:
            continue
        if mode == VARIANT_GRAD:
            w = w * dim_weight_mult(name, age)
            if w <= 0:
                continue                      # 乘数 0 = 剔除（权重不分摊给该维）
        valid.append((score, w))
    if not valid:
        return None
    w_sum = sum(w for _, w in valid)
    return round(sum(s * w / w_sum for s, w in valid), 1)
