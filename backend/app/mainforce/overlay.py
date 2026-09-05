# -*- coding: utf-8 -*-
from __future__ import annotations  # 兼容 Python 3.9（Render/Docker）：允许 -> dict | None 注解
"""
================================================================================
【文件作用】主力行为叠加器：把筹码结构 + 阶段 + 资金流合成可执行信号
================================================================================

依据（scripts/mainforce_factor_backtest.py，10,744 全池截面样本，2026-09-05）：

  1. 「高位高获利 × 主力流出」组合 = **regime 稳健的卖出/风控信号**
     5 日 -4.3pt / 10 日 -7.5pt（n=440，胜率 37.5%/39.5%）
  2. 「低位筹码密集 × 主力净流入」= 正向但 regime 依赖（上涨段 IC 反号）
     10 日 +1.08pt、胜率 66.9%（n=1086）→ 只做展示标签，v1 不加分
  3. 主力资金流与未来收益呈倒 U：温和净流入最优、极端流入最差
     → 单看"大单流入"选股是散户陷阱（去超额 IC -0.05~-0.13）

落地方式（与 PLAN_CROWDING_FACTOR 阶段 2 同一套不变量）：
  - 乘数只降不升：出货嫌疑 → total × 0.85（信号判定仍用 raw total）
  - 环境开关 MAINFORCE_MODE=off|auto（默认 off；auto 只在
    regime ∈ {neutral, neutral_bearish, defensive} 生效，offensive 段
    拥挤/出货惩罚方向不确定 → 恒不惩罚）
  - 买侧组合仅输出标签（mainforce.signal = accumulate/watch），不参与排序

数据注入：engine.py 保持零网络/零 DB，本模块接收 bars + flow_rows，
由调用方（routers/scoring、批量任务）从 backtest_prices/mainflow_history 读取。
================================================================================
"""

import os

from app.mainforce.chips import chip_metrics
from app.mainforce.phases import detect_phase

# 出货嫌疑乘数（与 crowding_multiplier 同量级，保守）
DISTRIBUTION_MULT = 0.85
# 生效 regime（offensive 段不惩罚——上涨段"高位"常常继续涨）
GATE_REGIMES = {"neutral", "neutral_bearish", "defensive"}


def _mode() -> str:
    return (os.environ.get("MAINFORCE_MODE") or "off").strip().lower()


def mainforce_overlay(bars: list, flow_rows: list = None,
                      float_shares: float = None, regime: str = None) -> dict | None:
    """
    计算单只股票的主力行为叠加。

    bars:        [{date, open, high, low, close, volume}] 升序（≥120 根，含今日）
    flow_rows:   [{date, main_net, main_pct, super_pct, ...}] 升序（可缺——缺时
                 出货组合退化为「高位高获利 × 近5日缩量滞涨」弱口径）
    float_shares: 流通股本（可缺，缺时筹码用相对量代理换手）
    regime:      市场状态（None 视为不满足闸门 → 只出标签不调分）

    返回 {
      phase, phase_cn,          # 主力阶段标签
      chip: {...},              # 筹码结构
      flow5_amt,                # 近5日主力净流入占成交额%（缺数据 None）
      signal: 'accum' | 'distribution' | None,
      signal_cn, reason,
      mult: 1.0 | 0.85,         # 排序乘数（MAINFORCE_MODE=auto + regime 闸门内才可能 <1）
      active: bool,             # 开关+闸门是否放行乘数
    }
    """
    # ★ CPU/内存护栏：筹码/阶段计算只需 ~120-260 根（chip warmup 60 / MA120 量 /
    #   ret60 / phase ≥70），更深的 K 线对结果无增益。
    #   Render 0.1CPU 上曾因「500 根 × 150 档滚动 + 详情页并发」把实例打挂（502）。
    if bars and len(bars) > 260:
        bars = bars[-260:]

    chip = chip_metrics(bars, float_shares=float_shares)
    if not chip:
        return None
    phase = detect_phase(bars)

    # 近 5 日主力净流入占额%（新浪口径 main_pct 已是占成交额百分比）
    flow5_amt = None
    consec = 0
    if flow_rows:
        recent = flow_rows[-5:]
        flow5_amt = round(sum(float(r.get("main_pct") or 0) for r in recent), 2)
        for r in reversed(flow_rows):
            if (r.get("main_net") or 0) > 0:
                consec += 1
            else:
                break

    # ── 组合判定 ──────────────────────────────────────────────
    high_pos = chip["price_pos"] > 0.75
    high_winner = chip["winner_ratio"] > 0.7
    low_pos_dense = chip["price_pos"] < 0.35 and chip["concentration"] < 0.25

    flow_out = flow5_amt is not None and flow5_amt < 0
    flow_in = flow5_amt is not None and flow5_amt > 0
    # flow 缺数据时的弱口径：阶段=出货/拉升 且 现价在筹码区间顶部 ≈ 高位滞涨
    weak_high = phase and phase["phase"] in ("distribution",) and chip["price_pos"] > 0.75

    distribution = high_pos and high_winner and (flow_out or (flow5_amt is None and weak_high))
    accumulation = low_pos_dense and flow_in

    if distribution:
        signal, signal_cn = "distribution", "出货嫌疑"
        reason = (f"现价处于筹码区间顶部（price_pos {chip['price_pos']:.2f}）、"
                  f"获利盘 {chip['winner_ratio'] * 100:.0f}%、"
                  f"近5日主力净流入 {flow5_amt}%"
                  if flow5_amt is not None else
                  f"高位高获利（price_pos {chip['price_pos']:.2f}、获利盘 "
                  f"{chip['winner_ratio'] * 100:.0f}%）且主力阶段=出货")
    elif accumulation:
        signal, signal_cn = "accum", "主力吸筹区"
        reason = (f"低位筹码密集（price_pos {chip['price_pos']:.2f}、集中度 "
                  f"{chip['concentration']:.2f}）且近5日主力净流入 {flow5_amt}%")
    else:
        signal, signal_cn, reason = None, "", ""

    # ── 乘数（只降不升；开关 + regime 闸门） ────────────────────
    active = (_mode() == "auto" and (regime or "") in GATE_REGIMES)
    mult = DISTRIBUTION_MULT if (active and distribution) else 1.0

    return {
        "phase": (phase or {}).get("phase"),
        "phase_cn": (phase or {}).get("phase_cn"),
        "chip": chip,
        "flow5_amt": flow5_amt,
        "flow_consec": consec,
        "signal": signal,
        "signal_cn": signal_cn,
        "reason": reason,
        "mult": mult,
        "active": active,
    }
