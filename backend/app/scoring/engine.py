"""
================================================================================
【文件作用】A股多因子评分引擎（整个项目的"大脑"）
================================================================================

这是纯业务逻辑文件，不涉及网络/数据库，可独立测试。
输入：股票的技术指标数据 + 实时行情 + 基本面数据
输出：一个 0~100 的综合评分 + 买卖信号（强烈买入/买入/观望/卖出/强烈卖出）

评分体系（三维度加权）：
  ┌──────────┬──────┬──────────────────────────────────────────────┐
  │ 维度     │ 权重 │ 包含的子指标                                 │
  ├──────────┼──────┼──────────────────────────────────────────────┤
  │ 技术面   │ 40%  │ MA均线 / MACD / RSI / KDJ / 布林带           │
  │ 资金面   │ 25%  │ 量价配合 / 涨跌动量 / 换手率 / 成交额强度    │
  │ 基本面   │ 35%  │ PE估值 / PB估值 / 市值规模 / 振幅            │
  └──────────┴──────┴──────────────────────────────────────────────┘

计算公式：
  综合分 = 技术面得分×0.40 + 资金面得分×0.25 + 基本面得分×0.35

信号判定（基于综合分）：
  ≥ 80 → 强烈买入   |   ≥ 65 → 买入   |   45~65 → 观望
  ≤ 35 → 卖出       |   ≤ 20 → 强烈卖出
================================================================================
"""

# from __future__ annotations：让类型注解可以"前向引用"，并且允许写 list|None 这种新语法
# 类比 TS：没有这个的话，旧版 Python 不能写 `def f(x: list | None)`
from __future__ import annotations
import math
# dataclass：Python 的数据类装饰器。类比 TS 的 interface + 构造函数
# 用 @dataclass 可以少写很多样板代码（自动生成 __init__ 等）
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DimensionScore:
    """
    单一维度（技术面/资金面/基本面）的评分结果。

    类比 TS interface：
      interface DimensionScore {
        name: string;
        score: number;          // 0~100
        weight: number;         // 0~1
        weighted_score: number; // score × weight
        details: Record<string, any>;
      }
    """
    name: str
    score: float           # 0~100
    weight: float          # 权重 0~1
    weighted_score: float  # 加权分（= score × weight）
    # field(default_factory=dict)：details 默认是一个新 dict
    # 注意：Python 中可变默认值必须用 default_factory，否则所有实例会共享同一个 dict
    details: dict = field(default_factory=dict)  # 各子项明细


@dataclass
class ScoreResult:
    """
    综合评分结果（最终返回给前端的数据结构）。
    前端拿到这个对象后展示评分卡、雷达图、买卖信号等。
    """
    code: str = ""
    name: str = ""
    total_score: float = 0.0       # 综合得分
    signal: str = "观望"           # 信号文字
    signal_level: int = 0          # 信号等级：-2(强烈卖出) ~ +2(强烈买入)
    dimensions: list = field(default_factory=list)  # 三个维度的明细
    summary: str = ""              # 人类可读的评分摘要
    factors_up: list = field(default_factory=list)    # 加分因素列表
    factors_down: list = field(default_factory=list)  # 扣分因素列表
    buy_point: dict = field(default_factory=dict)     # 买入时机指标（MA20偏离/布林位置/支撑位）


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """把数值限制在 [lo, hi] 区间。类比 JS 的 Math.max(lo, Math.min(hi, v))"""
    return max(lo, min(hi, v))


def _score_in_range(val: float, good_lo: float, good_hi: float,
                    bad_lo: float = None, bad_hi: float = None) -> float:
    """
    将一个值映射到 0~100 分。
    在 [good_lo, good_hi] 区间内得满分 100；越远离该区间得分越低。

    这个函数目前在引擎里没被直接调用，是预留的通用打分工具。
    """
    if good_lo <= val <= good_hi:
        return 100.0
    if val < good_lo:
        span = good_lo - (bad_lo if bad_lo is not None else good_lo - 50)
        if span <= 0:
            return 50.0
        return _clamp(round((val - (bad_lo if bad_lo is not None else good_lo - span)) / span * 100, 1))
    else:
        span = (bad_hi if bad_hi is not None else good_hi + 50) - good_hi
        if span <= 0:
            return 50.0
        return _clamp(round(((bad_hi if bad_hi is not None else good_hi + span) - val) / span * 100, 1))


class ScoreEngine:
    """多因子评分引擎（无状态，可单例复用）"""

    # ── 权重配置（类常量，所有实例共享）──
    # 三个权重加起来 = 1.0，确保综合分满分是 100
    W_TECHNICAL = 0.40    # 技术面权重 40%
    W_CAPITAL   = 0.25    # 资金面权重 25%
    W_FUNDAMENTAL = 0.35  # 基本面权重 35%

    def __init__(self):
        pass

    # ================================================================
    #  对外接口（路由层调用这两个方法）
    # ================================================================

    def score_stock(self, code: str, name: str = "",
                    technical_data: list | None = None,
                    stock_info: dict | None = None,
                    fundamental: dict | None = None) -> ScoreResult:
        """
        计算单只股票的综合评分（最完整的评分，需要技术指标数据）。

        参数：
            code:            股票代码
            name:            股票名称
            technical_data:  技术指标数组（由 /api/stock/technical/{code} 提供，含 MA/MACD/RSI...）
            stock_info:      实时行情 dict（含 price/pe/pb/换手率...）
            fundamental:     基本面 dict（含估值/财务指标）
        """
        # None 兜底成空集合，避免后面 .get() 报错
        technical_data = technical_data or []
        stock_info = stock_info or {}
        fundamental = fundamental or {}

        # 分别计算三个维度
        dim_tech = self._score_technical(technical_data)              # 技术面
        dim_cap  = self._score_capital(technical_data, stock_info)    # 资金面
        dim_fund = self._score_fundamental(stock_info, fundamental)   # 基本面

        # 三维度加权求和 → 综合分
        dimensions = [dim_tech, dim_cap, dim_fund]
        total = sum(d.weighted_score for d in dimensions)   # sum(生成器)：累加每个维度的加权分
        total = round(total, 1)   # 保留 1 位小数

        # 推导信号、提取因素、生成摘要
        signal, signal_level = self._derive_signal(total, dimensions)
        factors_up, factors_down = self._extract_factors(dimensions)
        summary = self._build_summary(name or code, total, signal, factors_up, factors_down)

        # 返回结果对象（dimensions 转成 dict 列表方便 JSON 序列化给前端）
        try:
            buy_point = self._calc_buy_point(technical_data) if technical_data else {}
        except Exception:
            buy_point = {}
        return ScoreResult(
            code=code, name=name,
            total_score=total,
            signal=signal,
            signal_level=signal_level,
            dimensions=[{
                "name": d.name, "score": d.score,
                "weight": d.weight, "weighted_score": d.weighted_score,
                "details": d.details,
            } for d in dimensions],
            summary=summary,
            factors_up=factors_up,
            factors_down=factors_down,
            buy_point=buy_point,
        )

    def score_batch(self, stocks: list[dict], technical_cache: dict | None = None) -> list[ScoreResult]:
        """
        批量评分（用于 /api/score/batch/* 接口，对几千只股票排序）。

        参数：
            stocks: 全量缓存里的股票列表，每个 dict 含 tencent.py 返回的字段
            technical_cache: 可选的技术指标缓存 {code: 技术指标数组}

        说明：
          批量模式下，几千只股票逐个拉技术指标太慢，所以：
          - 有技术指标的 → 走完整 score_stock
          - 没有的       → 走简化版 _score_from_realtime（只用实时行情）

        返回：按 total_score 降序排列的结果列表
        """
        technical_cache = technical_cache or {}
        results = []
        for s in stocks:
            code = s.get("code", "")
            name = s.get("name", "")
            tech = technical_cache.get(code, [])
            if not tech:
                # 没有技术指标 → 简化评分
                result = self._score_from_realtime(code, name, s)
            else:
                result = self.score_stock(code, name, tech, s)
            results.append(result)
        # 按综合分从高到低排序
        results.sort(key=lambda r: r.total_score, reverse=True)
        return results

    # ================================================================
    #  技术面评分 (40%)
    #  5 个子指标：MA趋势(25) + MACD(25) + RSI(20) + KDJ(15) + 布林带(15)
    # ================================================================

    def _score_technical(self, tech_data: list) -> DimensionScore:
        """
        技术面总评。tech_data 是技术指标数组，每个元素含 ma5/macd/rsi... 等字段。

        若数据不足 30 条（无法算指标），给中性分 50。
        """
        details = {}
        sub_scores = []

        # 数据不足，无法计算技术指标，给中性分
        if len(tech_data) < 30:
            return DimensionScore("技术面", 50.0, self.W_TECHNICAL,
                                  round(50.0 * self.W_TECHNICAL, 1),
                                  {"说明": "数据不足，中性评分"})

        latest = tech_data[-1]   # 最新一天
        prev   = tech_data[-2] if len(tech_data) >= 2 else latest   # 前一天（用于判断金叉/死叉）
        price  = latest.get("close", 0)

        # ── 1. MA 均线趋势 (满分 25) ──
        ma_score = self._score_ma(latest, tech_data)
        details["MA趋势"] = {"分值": ma_score, "满分": 25}
        sub_scores.append((ma_score, 25))

        # ── 2. MACD 动量 (满分 25) ──
        macd_score = self._score_macd(latest, prev, tech_data)
        details["MACD动量"] = {"分值": macd_score, "满分": 25}
        sub_scores.append((macd_score, 25))

        # ── 3. RSI 强弱 (满分 20) ──
        rsi_score = self._score_rsi(latest)
        details["RSI强弱"] = {"分值": rsi_score, "满分": 20}
        sub_scores.append((rsi_score, 20))

        # ── 4. KDJ (满分 15) ──
        kdj_score = self._score_kdj(latest, prev)
        details["KDJ指标"] = {"分值": kdj_score, "满分": 15}
        sub_scores.append((kdj_score, 15))

        # ── 5. BOLL 布林带 (满分 15) ──
        boll_score = self._score_boll(latest)
        details["布林带"] = {"分值": boll_score, "满分": 15}
        sub_scores.append((boll_score, 15))

        # 加权：每个子分 × (子满分/100)，再求和 → 技术面 0~100 分
        raw = sum(s * w / 100 for s, w in sub_scores)
        score = _clamp(round(raw, 1))
        # weighted_score = score × 维度权重（用于最终综合分累加）
        return DimensionScore("技术面", score, self.W_TECHNICAL,
                              round(score * self.W_TECHNICAL, 1), details)

    def _score_ma(self, latest: dict, tech_data: list) -> float:
        """
        MA 均线趋势评分（满分 25，0~100 尺度）。

        评分逻辑（多空对称）：
          - 价格在 MA5/MA20 上方 → 加分；下方 → 等额扣分
          - 多头排列（MA5>MA10>MA20）→ 加分；空头排列 → 等额扣分
          - MA60 上方（支撑）→ 加分；下方（压制）→ 扣分
          - MA5 上穿/下穿 MA10（金叉/死叉）→ 加/扣分

        说明：早期版本只奖励多头、不惩罚空头，导致下跌趋势股仍得中性分 50，
        无法与横盘股区分。这里补齐空头侧，使 MA 能正确反映方向。
        """
        price = latest.get("close", 0)
        ma5  = latest.get("ma5")
        ma10 = latest.get("ma10")
        ma20 = latest.get("ma20")
        ma60 = latest.get("ma60")

        # 缺少关键均线数据，无法判断
        if not all([ma5, ma10, ma20]):
            return 50.0

        score = 50.0
        # 价格 vs MA5
        if ma5 and price > ma5:
            score += 5
        elif ma5 and price < ma5:
            score -= 5
        # 价格 vs MA20
        if ma20 and price > ma20:
            score += 5
        elif ma20 and price < ma20:
            score -= 5
        # 多头 / 空头排列
        if ma5 and ma10 and ma20 and ma5 > ma10 > ma20:
            score += 8                      # 完美多头（最强形态）
        elif ma5 and ma10 and ma5 > ma10:
            score += 4                      # 部分多头
        elif ma5 and ma10 and ma20 and ma5 < ma10 < ma20:
            score -= 8                      # 完美空头（最弱形态）
        elif ma5 and ma10 and ma5 < ma10:
            score -= 4                      # 部分空头
        # MA60 支撑 / 压制
        if ma60 and price > ma60:
            score += 4
        elif ma60 and price > ma60 * 0.97:  # 接近 MA60（差 3% 以内）
            score += 2
        elif ma60 and price < ma60:
            score -= 4
        # MA5 金叉 / 死叉 MA10（看前一天 → 今天的变化）
        if len(tech_data) >= 2:
            prev = tech_data[-2]
            p_ma5, p_ma10 = prev.get("ma5"), prev.get("ma10")
            if p_ma5 and p_ma10 and ma5 and ma10:
                if p_ma5 <= p_ma10 and ma5 > ma10:
                    score += 3              # 金叉加分
                elif p_ma5 >= p_ma10 and ma5 < ma10:
                    score -= 3              # 死叉扣分

        return _clamp(score, 0, 100)

    def _score_macd(self, latest: dict, prev: dict, tech_data: list) -> float:
        """
        MACD 动量评分（满分 25）。

        MACD 基础知识：
          - DIF（快线）、DEA（慢线）
          - DIF > DEA → 多头；DIF < DEA → 空头
          - DIF 上穿 DEA = 金叉（看涨）；下穿 = 死叉（看跌）
          - MACD柱 = (DIF - DEA) × 2，红柱（正）看涨，绿柱（负）看跌
          - DIF 在 0 轴上方 = 中期多头
        """
        dif  = latest.get("dif")
        dea  = latest.get("dea")
        macd = latest.get("macd")

        if dif is None or dea is None:
            return 50.0

        score = 50.0

        # DIF 在零轴上方（中期多头）
        if dif > 0:
            score += 5
        else:
            score -= 5

        # MACD 柱状体（红绿柱）
        if macd is not None and macd > 0:
            score += 4
            # 红柱放大（动能增强）
            prev_macd = prev.get("macd")
            if prev_macd is not None and macd > prev_macd:
                score += 3
        elif macd is not None:
            score -= 4   # 绿柱扣分

        # DIF > DEA（短期多头）
        if dif > dea:
            score += 4
        else:
            score -= 4

        # 金叉/死叉判定（看前一天 → 今天的变化）
        p_dif, p_dea = prev.get("dif"), prev.get("dea")
        if p_dif is not None and p_dea is not None:
            if p_dif <= p_dea and dif > dea:
                score += 5   # 金叉
            elif p_dif >= p_dea and dif < dea:
                score -= 5   # 死叉

        # 连续 N 日 MACD 为正（趋势确认）
        if len(tech_data) >= 5:
            positive_days = 0
            for i in range(-5, 0):   # 最近 5 天
                m = tech_data[i].get("macd")
                if m is not None and m > 0:
                    positive_days += 1
            if positive_days >= 5:
                score += 4   # 连续 5 天红柱
            elif positive_days >= 3:
                score += 2

        return _clamp(score, 0, 100)

    def _score_rsi(self, latest: dict) -> float:
        """
        RSI 强弱评分（满分 20，0~100 尺度）—— 趋势跟随口径。

        RSI 反映多头力度的强弱，这里按「趋势强度」打分，与 MA/MACD 口径一致：
          - 70~80：最强（健康的多头动能，最佳）
          - 60~70 / ≥80：强势（≥80 略低于 70~80，避免追阶段性顶部）
          - 50~60：中性偏强；40~50：中性
          - <40：弱势（<30 明确扣分，体现下跌趋势）

        说明：旧版本按「均值回归」打分（超卖区给最高分、强势区扣分），
        与引擎「趋势强=高分」的整体定位冲突，会把强势股的技术面压低、
        把下跌股抬高。这里改为趋势跟随。
        """
        rsi = latest.get("rsi")
        if rsi is None:
            return 50.0

        # 按区间给分：强势区高分，弱势区低分
        if rsi >= 80:
            return 80.0   # 极强，但接近阶段性过热，略低于 70~80
        elif rsi >= 70:
            return 90.0   # 强势多头（最佳）
        elif rsi >= 60:
            return 78.0   # 偏强
        elif rsi >= 50:
            return 62.0   # 中性偏强
        elif rsi >= 40:
            return 50.0   # 中性
        elif rsi >= 30:
            return 38.0   # 偏弱
        elif rsi >= 20:
            return 22.0   # 弱势
        else:
            return 15.0   # 极弱

    def _score_kdj(self, latest: dict, prev: dict) -> float:
        """
        KDJ 评分（满分 15，0~100 尺度）—— 趋势跟随口径。

        以趋势方向 + 所在区间强弱打分，与 MA/MACD 口径一致：
          - K>D / 金叉 / 高位（K、D>80）→ 加分（多头强势）
          - K<D / 死叉 / 低位（K、D<20）→ 扣分（空头弱势）
          - J>100 强势上攻给小分（极端位避免重仓追高）；J<0 极弱扣分

        说明：旧版本按「均值回归」给超卖低位加分、超买高位扣分，
        会奖励下跌、惩罚上涨，与趋势定位冲突，已改为趋势跟随。
        """
        k = latest.get("k")
        d = latest.get("d")
        j = latest.get("j")

        if k is None or d is None:
            return 50.0

        score = 50.0

        # K vs D（趋势方向）
        if k > d:
            score += 3
        else:
            score -= 3

        # 金叉 / 死叉
        p_k, p_d = prev.get("k"), prev.get("d")
        if p_k is not None and p_d is not None:
            if p_k <= p_d and k > d:
                score += 5   # 金叉（多头）
            elif p_k >= p_d and k < d:
                score -= 5   # 死叉（空头）

        # K/D 绝对位置（趋势强度）：高位=强势，低位=弱势
        if k > 80 and d > 80:
            score += 3   # 强势区
        elif k < 20 and d < 20:
            score -= 3   # 弱势区

        # J 值极端：强势上攻给小分（避免在极端位追高），极弱扣分
        if j is not None:
            if j > 100:
                score += 2
            elif j < 0:
                score -= 2

        return _clamp(score, 0, 100)

    def _score_boll(self, latest: dict) -> float:
        """
        布林带评分（满分 15，0~100 尺度）—— 趋势跟随口径。

        价格在布林带中的相对位置（0=下轨，1=上轨）反映趋势强度：
          - 接近/突破上轨 → 强势 → 加分
          - 接近/跌破下轨 → 弱势 → 扣分
          - 价格在中轨上方 → 加分

        说明：旧版本按「均值回归」把触及下轨当支撑利好加分、触及上轨扣分，
        会奖励下跌、惩罚上涨，与趋势定位冲突，已改为趋势跟随。
        """
        price = latest.get("close", 0)
        upper = latest.get("boll_upper")
        mid   = latest.get("boll_mid")
        lower = latest.get("boll_lower")

        if not all([upper, mid, lower]) or price <= 0:
            return 50.0

        score = 50.0
        bandwidth = upper - lower   # 带宽（衡量波动）

        if bandwidth <= 0:
            return 50.0

        # 价格在布林带中的相对位置：0=下轨(弱), 1=上轨(强)
        position = (price - lower) / bandwidth

        # 接近上轨加分（强势），接近下轨扣分（弱势）
        if position > 0.8:
            score += 6    # 接近/突破上轨，强势
        elif position > 0.6:
            score += 4    # 偏强
        elif position > 0.4:
            score += 1    # 中轨附近
        elif position > 0.2:
            score -= 3    # 偏弱
        elif position > 0:
            score -= 5    # 接近下轨，弱势
        else:
            score -= 6    # 跌破下轨，破位

        # 价格在中轨上方 = 强势
        if price > mid:
            score += 3

        # 带宽收窄（变盘前兆）—— 占位，没有历史数据暂不计算
        if len(bw_history := [d.get("boll_upper", 0) - d.get("boll_lower", 0)
                              for d in [latest] if d.get("boll_upper")]) == 0:
            pass  # 无法判断

        return _clamp(score, 0, 100)

    # ================================================================
    #  资金面/量价评分 (25%)
    #  4 个子指标：量价配合(30) + 涨跌动量(25) + 换手率(20) + 成交额(25)
    # ================================================================

    def _score_capital(self, tech_data: list, stock_info: dict) -> DimensionScore:
        """资金面总评"""
        details = {}
        sub_scores = []

        # ── 1. 量价配合 (满分 30) ──
        vol_price_score = self._score_volume_price(tech_data, stock_info)
        details["量价配合"] = {"分值": vol_price_score, "满分": 30}
        sub_scores.append((vol_price_score, 30))

        # ── 2. 涨跌幅动量 (满分 25) ──
        momentum_score = self._score_momentum(tech_data, stock_info)
        details["涨跌动量"] = {"分值": momentum_score, "满分": 25}
        sub_scores.append((momentum_score, 25))

        # ── 3. 换手率活跃度 (满分 20) ──
        turnover_score = self._score_turnover(stock_info)
        details["换手率"] = {"分值": turnover_score, "满分": 20}
        sub_scores.append((turnover_score, 20))

        # ── 4. 成交额强度 (满分 25) ──
        amount_score = self._score_amount(tech_data, stock_info)
        details["成交额"] = {"分值": amount_score, "满分": 25}
        sub_scores.append((amount_score, 25))

        raw = sum(s * w / 100 for s, w in sub_scores)
        score = _clamp(round(raw, 1))
        return DimensionScore("资金面", score, self.W_CAPITAL,
                              round(score * self.W_CAPITAL, 1), details)

    def _score_volume_price(self, tech_data: list, stock_info: dict) -> float:
        """
        量价配合评分（满分 30）。

        核心逻辑："放量上涨、缩量下跌"是好形态（资金真在买入）。
          - 上涨日的成交量 > 下跌日的成交量 → 加分
          - 今天成交量 > 近 5 日均量（放量）→ 加分
        """
        if len(tech_data) < 10:
            return 50.0

        score = 50.0
        recent = tech_data[-5:]   # 最近 5 天

        up_vol = 0    # 上涨日成交量累计
        down_vol = 0  # 下跌日成交量累计
        up_days = 0
        down_days = 0

        # 统计最近 5 天的涨跌量
        for i, d in enumerate(recent):
            if i == 0:
                continue   # 第一个没有前一天可比，跳过
            chg = d["close"] - recent[i - 1]["close"]
            vol = d["volume"]
            if chg > 0:
                up_vol += vol
                up_days += 1
            elif chg < 0:
                down_vol += vol
                down_days += 1

        # 放量上涨判断（上涨日量 vs 下跌日量）
        if up_days > 0 and down_days > 0 and down_vol > 0:
            ratio = up_vol / down_vol
            if ratio > 2.0:
                score += 15   # 上涨日量是下跌日的 2 倍以上（强势）
            elif ratio > 1.5:
                score += 10
            elif ratio > 1.0:
                score += 5
            else:
                score -= 5    # 下跌日量更大（弱势）
        elif up_days > 0 and down_days == 0:
            score += 10   # 5 天全涨
        elif down_days > 0 and up_days == 0:
            score -= 10   # 5 天全跌

        # 今天是否放量（vs 近 5 日均量）
        if len(tech_data) >= 2:
            vol_today = tech_data[-1]["volume"]
            vol_avg5 = sum(d["volume"] for d in tech_data[-6:-1]) / 5   # 前 5 日均量
            if vol_avg5 > 0:
                vol_ratio = vol_today / vol_avg5
                if vol_ratio > 2.0:
                    score += 8   # 今天量是均量的 2 倍（爆量）
                elif vol_ratio > 1.5:
                    score += 5
                elif vol_ratio < 0.5:
                    score -= 3   # 严重缩量

        return _clamp(score, 0, 100)

    def _score_momentum(self, tech_data: list, stock_info: dict) -> float:
        """
        涨跌幅动量评分（满分 25）。

        综合 3 个时间维度的涨跌幅：
          - 今日涨跌幅（来自实时数据）
          - 近 5 日涨跌幅
          - 近 20 日涨跌幅
        """
        score = 50.0

        # 今日涨跌幅（来自实时数据）
        change_pct = stock_info.get("change_pct", 0)
        if change_pct > 0:
            score += min(change_pct * 2, 10)   # 涨幅加分，上限 +10
        elif change_pct < 0:
            score += max(change_pct * 2, -10)  # 跌幅扣分，下限 -10

        # 5 日涨跌幅
        if len(tech_data) >= 6:
            chg_5d = (tech_data[-1]["close"] - tech_data[-6]["close"]) / tech_data[-6]["close"] * 100
            if chg_5d > 5:
                score += 8
            elif chg_5d > 2:
                score += 5
            elif chg_5d > 0:
                score += 2
            elif chg_5d < -5:
                score -= 8
            elif chg_5d < -2:
                score -= 5

        # 20 日涨跌幅
        if len(tech_data) >= 21:
            chg_20d = (tech_data[-1]["close"] - tech_data[-21]["close"]) / tech_data[-21]["close"] * 100
            if chg_20d > 10:
                score += 7
            elif chg_20d > 5:
                score += 4
            elif chg_20d < -10:
                score -= 7
            elif chg_20d < -5:
                score -= 4

        return _clamp(score, 0, 100)

    def _score_turnover(self, stock_info: dict) -> float:
        """
        换手率评分（满分 20）。

        换手率 = 今日成交量 / 流通股本，反映交易活跃度：
          - 1%~3%：温和（最理想，资金有序介入）
          - 3%~8%：活跃
          - > 15%：异常（可能主力出货）
          - < 0.3%：极低（无人问津）
        """
        turnover = stock_info.get("turnover_rate", 0)
        if not turnover:
            return 50.0

        # 按区间直接返回固定分（不累加）
        if 1.0 <= turnover <= 3.0:
            return 90.0   # 温和换手（最佳）
        elif 3.0 < turnover <= 5.0:
            return 80.0   # 活跃
        elif 5.0 < turnover <= 8.0:
            return 65.0   # 较活跃
        elif 8.0 < turnover <= 15.0:
            return 45.0   # 偏高（注意风险）
        elif turnover > 15.0:
            return 25.0   # 异常换手（可能出货）
        elif 0.3 <= turnover < 1.0:
            return 60.0   # 偏低但可接受
        else:
            return 30.0   # 极低

    def _score_amount(self, tech_data: list, stock_info: dict) -> float:
        """
        成交额强度评分（满分 25）。

        成交额 = 价格 × 成交量。反映"真金白银"的关注度：
          - 今天成交额 vs 近 10 日均额（是否爆量）
          - 近 5 日均额 vs 前 5 日均额（趋势是否放大）
        """
        if len(tech_data) < 10:
            return 50.0

        score = 50.0
        # 近 10 日每日成交额（近似：close × volume）
        amounts = [d["close"] * d["volume"] for d in tech_data[-10:]]
        avg_amount = sum(amounts) / len(amounts)
        today_amount = amounts[-1]

        # 今天 vs 近 10 日均额
        if avg_amount > 0:
            ratio = today_amount / avg_amount
            if ratio > 2.0:
                score += 12
            elif ratio > 1.5:
                score += 8
            elif ratio > 1.0:
                score += 3
            elif ratio < 0.5:
                score -= 8

        # 金额趋势：近 5 日 vs 前 5 日
        if len(amounts) >= 10:
            recent_avg = sum(amounts[-5:]) / 5
            prev_avg = sum(amounts[-10:-5]) / 5
            if prev_avg > 0:
                trend_ratio = recent_avg / prev_avg
                if trend_ratio > 1.3:
                    score += 8    # 资金持续流入
                elif trend_ratio > 1.1:
                    score += 4
                elif trend_ratio < 0.7:
                    score -= 8    # 资金持续流出
                elif trend_ratio < 0.9:
                    score -= 3

        return _clamp(score, 0, 100)

    # ================================================================
    #  基本面评分 (35%)
    #  4 个子指标：PE(30) + PB(20) + 市值规模(25) + 振幅(25)
    # ================================================================

    def _score_fundamental(self, stock_info: dict, fundamental: dict) -> DimensionScore:
        """基本面总评"""
        details = {}
        sub_scores = []

        # ── 1. PE 估值 (满分 30) ──
        pe_score = self._score_pe(stock_info, fundamental)
        details["PE估值"] = {"分值": pe_score, "满分": 30}
        sub_scores.append((pe_score, 30))

        # ── 2. PB 估值 (满分 20) ──
        pb_score = self._score_pb(stock_info, fundamental)
        details["PB估值"] = {"分值": pb_score, "满分": 20}
        sub_scores.append((pb_score, 20))

        # ── 3. 市值规模 (满分 25) ──
        cap_score = self._score_market_cap(stock_info)
        details["市值规模"] = {"分值": cap_score, "满分": 25}
        sub_scores.append((cap_score, 25))

        # ── 4. 振幅/波动 (满分 25) ──
        vol_score = self._score_volatility(stock_info)
        details["振幅"] = {"分值": vol_score, "满分": 25}
        sub_scores.append((vol_score, 25))

        raw = sum(s * w / 100 for s, w in sub_scores)
        score = _clamp(round(raw, 1))
        return DimensionScore("基本面", score, self.W_FUNDAMENTAL,
                              round(score * self.W_FUNDAMENTAL, 1), details)

    def _score_pe(self, stock_info: dict, fundamental: dict) -> float:
        """
        PE（市盈率）估值评分（满分 30）—— 市值分层版本。

        PE = 股价 / 每股收益，反映"多少年回本"。
        但成长股（小中盘、科技/医药/新能源）本就享受增长溢价，PE 偏高是合理的；
        价值股（大盘、成熟行业）则应严格用 PE 衡量。
        所以这里按市值分层用两套曲线，避免一刀切惩罚成长股。

        市值分界：200 亿（< 200 亿按成长股对待，≥ 200 亿按价值股对待）
        注意：硬过滤亏损股（PE ≤ 0）在路由层 score_top 做，这里仍返回中性分，
              让详情页能查到这类股票的评分（只是不进买入榜单）。
        """
        pe = stock_info.get("pe", 0) or 0
        if pe <= 0:
            return 40.0  # 亏损或无数据，中性偏低（详情页可见，但路由层会过滤出买入榜）

        # 市值（万元 → 亿元），用市值作为成长/价值的代理
        cap_yi = (stock_info.get("market_cap", 0) or 0) / 10000
        is_growth_oriented = 0 < cap_yi < 200   # 中小盘（< 200 亿）按成长股对待

        if is_growth_oriented:
            # ── 成长股曲线：PE 中高段不重罚，认可增长溢价 ──
            # 关键差异：PE 40-60 从原来的 40 分提到 70 分（不再被重罚）
            if pe < 15:
                return 90.0   # 低 PE 成长股（少见，如被错杀）
            elif pe < 30:
                return 80.0   # 合理偏低
            elif pe < 60:
                return 70.0   # 合理（成长股常见区间，不再扣分）
            elif pe < 100:
                return 50.0   # 偏高但可接受（高增速支撑）
            else:
                return 30.0   # 仍属偏贵，但比价值股同区间温和
        else:
            # ── 价值股/大盘股曲线：维持严格标准 ──
            if pe < 10:
                return 95.0   # 极度低估
            elif pe < 15:
                return 85.0   # 低估
            elif pe < 25:
                return 75.0   # 合理
            elif pe < 40:
                return 55.0   # 偏高
            elif pe < 60:
                return 40.0   # 高估
            elif pe < 100:
                return 25.0   # 严重高估
            else:
                return 15.0   # 泡沫

    def _score_pb(self, stock_info: dict, fundamental: dict) -> float:
        """
        PB（市净率）估值评分（满分 20）。

        PB = 股价 / 每股净资产：
          - PB < 1：破净（股价低于净资产，极度低估）
          - PB 越低 = 越便宜
        """
        pb = stock_info.get("pb", 0) or 0
        if pb <= 0:
            return 40.0

        if pb < 1.0:
            return 95.0   # 破净
        elif pb < 1.5:
            return 80.0
        elif pb < 2.5:
            return 65.0
        elif pb < 4.0:
            return 50.0
        elif pb < 7.0:
            return 35.0
        else:
            return 20.0

    def _score_market_cap(self, stock_info: dict) -> float:
        """
        市值规模评分（满分 25）。

        市值 = 总股本 × 股价。这里偏好中小盘（弹性大、涨幅空间大）：
          - 20~50 亿：最佳（中小盘，弹性好）
          - > 1000 亿：大盘，弹性不足
        """
        cap = stock_info.get("market_cap", 0) or 0
        # market_cap 单位是万元，转为亿元（÷10000）
        cap_yi = cap / 10000

        if cap_yi <= 0:
            return 50.0

        if cap_yi < 20:
            return 75.0   # 小盘，弹性大（但风险也大）
        elif cap_yi < 50:
            return 85.0   # 中小盘（最佳区间）
        elif cap_yi < 200:
            return 70.0   # 中盘
        elif cap_yi < 1000:
            return 55.0   # 大盘
        else:
            return 40.0   # 超大盘，弹性不足

    def _score_volatility(self, stock_info: dict) -> float:
        """
        振幅评分（满分 25）。

        振幅 = (最高 - 最低) / 昨收 × 100%，反映当日波动程度：
          - 太低（<1%）：死气沉沉，无操作机会
          - 适中（2%~4%）：最佳（有机会且风险可控）
          - 太高（>9%）：风险大
        """
        amp = stock_info.get("amplitude", 0) or 0

        if amp <= 0:
            return 50.0

        if amp < 1.0:
            return 40.0   # 极低波动，缺乏机会
        elif amp < 2.0:
            return 65.0   # 低波动
        elif amp < 4.0:
            return 85.0   # 适中波动（最佳）
        elif amp < 6.0:
            return 70.0   # 偏高波动
        elif amp < 9.0:
            return 50.0   # 高波动
        else:
            return 30.0   # 极高波动，风险大

    # ================================================================
    #  简化评分（仅用实时数据，批量模式）
    # ================================================================

    def _score_from_realtime(self, code: str, name: str, info: dict) -> ScoreResult:
        """
        仅用实时行情做简化评分（无技术指标时使用，用于批量模式的兜底）。

        只看 3 个指标：涨跌幅 + 换手率 + PE，快速给出一个粗略分数。
        """
        details = {}
        sub_scores = []

        # 涨跌幅
        chg = info.get("change_pct", 0)
        if chg > 3:
            momentum_s = 70
        elif chg > 0:
            momentum_s = 60
        elif chg > -3:
            momentum_s = 40
        else:
            momentum_s = 25
        details["涨跌动量"] = {"分值": momentum_s, "满分": 50}
        sub_scores.append((momentum_s, 50))

        # 换手率（复用上面的方法）
        turnover_s = self._score_turnover(info)
        details["换手率"] = {"分值": turnover_s, "满分": 25}
        sub_scores.append((turnover_s, 25))

        # PE（复用上面的方法）
        pe_s = self._score_pe(info, {})
        details["PE估值"] = {"分值": pe_s, "满分": 25}
        sub_scores.append((pe_s, 25))

        raw = sum(s * w / 100 for s, w in sub_scores)
        score = _clamp(round(raw, 1))

        # 简化模式下只有一个维度，权重设为 1.0
        dim = DimensionScore("简化评分", score, 1.0, score, details)
        signal, signal_level = self._derive_signal(score, [dim])
        return ScoreResult(
            code=code, name=name, total_score=score,
            signal=signal, signal_level=signal_level,
            dimensions=[{
                "name": dim.name, "score": dim.score,
                "weight": dim.weight, "weighted_score": dim.weighted_score,
                "details": dim.details,
            }],
            summary=f"{name or code} 简化评分 {score}，信号：{signal}"
        )

    # ================================================================
    #  信号 & 报告生成
    # ================================================================

    def _derive_signal(self, total: float, dimensions: list[DimensionScore]) -> tuple[str, int]:
        """
        根据综合分生成买卖信号。

        额外规则：如果任意一个维度分数 < 20（极端差），
        即使综合分够高，也不能给"强烈买入"（防止某维度暴雷）。
        """
        any_extreme_low = any(d.score < 20 for d in dimensions)    # 有维度极差
        any_extreme_high = any(d.score > 85 for d in dimensions)   # 有维度极好（预留，暂未使用）

        # 按综合分区间给信号
        if total >= 80 and not any_extreme_low:
            return "强烈买入", 2
        elif total >= 65:
            return "买入", 1
        elif total >= 45:
            return "观望", 0
        elif total <= 20:
            return "强烈卖出", -2
        elif total <= 35:
            return "卖出", -1
        else:
            return "观望", 0

    def _extract_factors(self, dimensions: list[DimensionScore]) -> tuple[list[str], list[str]]:
        """
        提取主要加分/扣分因素（用于前端展示"为什么这个分数"）。

        规则：
          - 子项得分率 ≥ 75% → 加入加分因素
          - 子项得分率 ≤ 35% → 加入扣分因素
        最多各取 5 个。
        """
        ups = []
        downs = []

        # 加分因素的文案映射
        label_map = {
            "MA趋势": "均线多头排列",
            "MACD动量": "MACD金叉",
            "RSI强弱": "RSI处于强势区间",
            "KDJ指标": "KDJ金叉走强",
            "布林带": "价格突破布林上轨",
            "量价配合": "量价齐升",
            "涨跌动量": "短期趋势向好",
            "换手率": "换手率健康",
            "成交额": "资金持续流入",
            "PE估值": "估值合理偏低",
            "PB估值": "PB处于低位",
            "市值规模": "市值适中弹性好",
            "振幅": "波动率适中",
        }

        # 扣分因素的文案映射
        down_map = {
            "MA趋势": "均线空头排列",
            "MACD动量": "MACD死叉",
            "RSI强弱": "RSI处于弱势区间",
            "KDJ指标": "KDJ死叉走弱",
            "布林带": "价格跌破布林中轨",
            "量价配合": "量价背离",
            "涨跌动量": "短期趋势走弱",
            "换手率": "换手率异常偏高",
            "成交额": "资金持续流出",
            "PE估值": "估值偏高",
            "PB估值": "PB偏高",
            "市值规模": "市值过大弹性不足",
            "振幅": "波动率过高风险大",
        }

        # 遍历每个维度的每个子项，按得分率分类
        # 注意：子项「分值」统一在 0~100 尺度（满分字段仅作权重/分值占比，不再当上限），
        # 所以得分率直接用 分值/100。
        for dim in dimensions:
            for factor_name, detail in dim.details.items():
                if isinstance(detail, dict):
                    s = detail.get("分值", 50)
                    pct = s / 100   # 得分率：子项分统一 0~100 尺度
                    if pct >= 0.75:
                        ups.append(label_map.get(factor_name, factor_name))
                    elif pct <= 0.35:
                        downs.append(down_map.get(factor_name, factor_name))

        return ups[:5], downs[:5]   # 最多各 5 个

    def _build_summary(self, name: str, total: float, signal: str,
                       ups: list[str], downs: list[str]) -> str:
        """生成人类可读的评分摘要文字（前端可直接展示）"""
        parts = [f"{name} 综合评分 {total}，信号：{signal}。"]
        if ups:
            parts.append(f"加分因素：{'、'.join(ups)}。")
        if downs:
            parts.append(f"风险提示：{'、'.join(downs)}。")
        return " ".join(parts)

    # ================================================================
    #  买入时机指标（不影响评分，纯参考）
    # ================================================================

    def _calc_buy_point(self, tech_data: list) -> dict:
        """
        计算买入时机指标：MA20偏离度 + 布林带位置 + 支撑位。
        返回 {ma20_deviation, boll_position, support, buy_timing}。
        buy_timing: '适合介入' / '等回调' / '追高风险'
        """
        if len(tech_data) < 30:
            return {}

        latest = tech_data[-1]
        price = latest.get("close", 0)
        ma20 = latest.get("ma20")
        boll_upper = latest.get("boll_upper")
        boll_lower = latest.get("boll_lower")
        ma60 = latest.get("ma60")

        if not price or not ma20:
            return {}

        # 1. MA20 偏离度（%）
        ma20_dev = round((price - ma20) / ma20 * 100, 2)

        # 2. 布林带位置（0=下轨, 1=上轨）
        boll_pos = None
        if boll_upper and boll_lower:
            bw = boll_upper - boll_lower
            if bw > 0:
                boll_pos = round((price - boll_lower) / bw, 2)

        # 3. 支撑位参考（MA60 或 MA20）
        support = round(ma60, 2) if ma60 else round(ma20, 2)

        # 4. 买入时机判定
        if ma20_dev > 8 or (boll_pos is not None and boll_pos > 0.95):
            timing = "追高风险"
        elif ma20_dev > 3 or (boll_pos is not None and boll_pos > 0.8):
            timing = "等回调"
        else:
            timing = "适合介入"

        return {
            "ma20_deviation": ma20_dev,
            "boll_position": boll_pos,
            "support": support,
            "buy_timing": timing,
        }
