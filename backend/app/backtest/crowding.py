# -*- coding: utf-8 -*-
"""
================================================================================
【文件作用】拥挤度惩罚因子（纯函数，评分引擎与离线回测共用）
================================================================================

背景（PLAN_CROWDING_FACTOR.md）：
  A股大跌段「高质量/高成长股跌更惨」——机构重仓白马是拥挤交易，缺承接盘。
  用「价格代理」识别拥挤：前期涨幅越大、距年内高点越近 → 拥挤度越高。
  拥挤度只作风险调节（乘数，只降不升），不新增权重维度、不依赖财报/行业映射。

设计口径：
  - crowding_metrics(bars)：从日线计算拥挤度量
      bars: [{date, open, high, low, close}, ...] 升序，至少 21 根
  - crowding_score(m)：0~100，越低 = 越拥挤
  - crowding_multiplier(crowd_score)：惩罚乘数 ∈ [0.85, 1.0]，只降不升

与 PLAN 原稿的差异（评审已确认）：
  1. 去掉「超跌加分」分支（ret_20 < -10 → +20）：超跌反弹捕捉已由技术面/资金面承担，
     拥挤度加分=双计数且破坏「只惩罚高位」的单调性
  2. 补 ret_5：连涨后动能刚衰竭（5 日回落但 20 日仍高）是拥挤瓦解的更早信号
  3. dist_from_high 窗口截断 [-250:]，上市不足 250 日时不误用全历史起点
  4. high 缺失时用 close 近似（低精度降级，调用方应尽量提供真 high）
================================================================================
"""


def crowding_metrics(bars: list) -> dict | None:
    """
    从升序日线计算拥挤度量。
    bars: [{date, close, high?}, ...] 升序，至少 21 根；high 缺失降级为 close。
    返回 None：数据不足（<21 根）——调用方应视作「无拥挤信息 → mult=1.0」。
    """
    if not bars or len(bars) < 21:
        return None
    last = float(bars[-1]["close"] or 0)
    if last <= 0:
        return None

    def px(n: int) -> float:
        """第 n 个交易日前的收盘价（n=1 即当日）；不足则用最早一根。"""
        return float(bars[-n]["close"] or bars[0]["close"])

    highs = [float(b.get("high") or b["close"]) for b in bars]
    ret_5 = (last / px(5) - 1) * 100
    ret_20 = (last / px(21) - 1) * 100
    ret_60 = (last / px(61) - 1) * 100 if len(bars) >= 61 else None
    high_250 = max(highs[-250:]) if len(highs) >= 21 else max(highs)
    dist = (high_250 - last) / high_250 * 100 if high_250 > 0 else 100.0
    return {
        "ret_5": round(ret_5, 2),
        "ret_20": round(ret_20, 2),
        "ret_60": round(ret_60, 2) if ret_60 is not None else None,
        "dist_from_high": round(dist, 2),
    }


def crowding_score(m: dict) -> float:
    """
    拥挤度评分：0~100，越低 = 越拥挤（前期涨越多、距高点越近）。
    基础 100，按前期涨幅/距高点逐步扣减，单调惩罚高位。
    """
    s = 100.0
    r20 = float((m or {}).get("ret_20") or 0)
    if r20 > 30:
        s -= 50
    elif r20 > 20:
        s -= 40
    elif r20 > 10:
        s -= 25
    r60 = (m or {}).get("ret_60")
    if r60 is not None and float(r60) > 50:
        s -= 20
    d_high = (m or {}).get("dist_from_high")
    if d_high is not None and float(d_high) < 5:
        s -= 15
    return max(0.0, min(100.0, s))


def crowding_multiplier(crowd_score: float) -> float:
    """
    惩罚乘数 ∈ [0.85, 1.0]，只降不升（超跌奖励交给技术面/资金面，不在此加分）。
    规则：
      crowd < 30（极拥挤）   → ×0.85
      crowd 30~50（较拥挤）  → ×0.95
      crowd >= 50（中性以下） → ×1.00 不惩罚
    """
    if crowd_score < 30:
        return 0.85
    if crowd_score < 50:
        return 0.95
    return 1.00
