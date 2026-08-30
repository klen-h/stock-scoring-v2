"""
================================================================================
【文件作用】回测撮合引擎：日线级信号 → 逐笔交易 → 组合净值 → 绩效指标
================================================================================
撮合规则（保守口径）：
  - 信号日 T+1 开盘价成交（信号多为盘前/午盘生成，当日买入不现实）
  - 止损/止盈：持仓期间 high/low 触及即平仓（同日触发按止损优先；
    跳空破止损按开盘价成交，否则按止损价）
  - 到期（hold_days）未触发 → 期末收盘价平仓
  - 成本模型：ETF 佣金+滑点；个股佣金+滑点+卖出印花税

组合层（等权近似，明确标注）：
  - 每笔交易按 position_ratio 占仓，日收益 = 当日持仓收益均值 × 总暴露
  - 净值曲线逐日累乘 → 回撤/夏普基于真实持仓路径
================================================================================
"""

from collections import defaultdict

TRADING_DAYS = 244            # 年化交易日
RISK_FREE = 0.02              # 无风险利率（年化）
DEFAULT_POSITION_RATIO = 0.2  # 无仓位字段时的默认单笔仓位

DEFAULT_COSTS = {
    "etf":   {"commission": 0.00025, "slippage": 0.0005, "stamp": 0.0},
    "stock": {"commission": 0.0005,  "slippage": 0.0005, "stamp": 0.001},
}


def match_signals(signals: list, prices_map: dict,
                  costs: dict = None, default_hold_days: int = 5) -> list:
    """
    逐信号撮合。signals: [{date, code, direction, stop_loss?, take_profit?,
                           hold_days?, position_ratio?, is_etf?}]
    prices_map: {code: [{date, open, high, low, close}]}（升序）
    返回 trades（含每日收益路径 daily）。
    """
    costs = costs or DEFAULT_COSTS
    trades = []
    for s in signals:
        code = s.get("code")
        bars = prices_map.get(code)
        if not bars:
            continue
        # 信号日之后的第一个交易日（T+1 开盘成交）
        entry_i = next((i for i, b in enumerate(bars) if b["date"] > s["date"]), None)
        if entry_i is None:
            continue
        entry_price = bars[entry_i]["open"]
        if entry_price <= 0:
            continue

        direction = 1 if s.get("direction", "long") == "long" else -1
        stop = s.get("stop_loss")
        tp = s.get("take_profit")
        hold = int(s.get("hold_days") or default_hold_days)
        ratio = float(s.get("position_ratio") or DEFAULT_POSITION_RATIO)
        c = costs["etf" if s.get("is_etf", True) else "stock"]
        buy_cost = c["commission"] + c["slippage"]
        sell_cost = c["commission"] + c["slippage"] + c["stamp"]

        # 持仓期逐日检查触发（含入场当日；同日止损优先）
        exit_i, exit_price, reason = None, None, "持有到期"
        last_i = min(entry_i + hold - 1, len(bars) - 1)
        if last_i == entry_i:
            # 信号日之后无足够 K 线完成持仓（数据只到入场日）→ 无法模拟持有期，跳过
            continue
        for j in range(entry_i, last_i + 1):
            bar = bars[j]
            if direction > 0:
                if stop and bar["low"] <= stop:
                    exit_i, exit_price, reason = j, min(bar["open"], stop), "止损"
                    break
                if tp and bar["high"] >= tp:
                    exit_i, exit_price, reason = j, tp, "止盈"
                    break
            else:
                if stop and bar["high"] >= stop:
                    exit_i, exit_price, reason = j, max(bar["open"], stop), "止损"
                    break
                if tp and bar["low"] <= tp:
                    exit_i, exit_price, reason = j, tp, "止盈"
                    break
        if exit_i is None:
            exit_i, exit_price, reason = last_i, bars[last_i]["close"], "持有到期"

        gross = (exit_price / entry_price - 1) * direction
        pnl = gross - buy_cost - sell_cost

        # 每日收益路径（结算日按 exit_price，其余按收盘）
        daily = []
        prev = entry_price
        for j in range(entry_i, exit_i + 1):
            bar = bars[j]
            settle = exit_price if j == exit_i else bar["close"]
            ret = (settle / prev - 1) * direction if prev > 0 else 0.0
            daily.append({"date": bar["date"], "ret": ret})
            prev = settle

        trades.append({
            "code": code,
            "name": s.get("name") or code,
            "strategy": s.get("strategy") or "",
            "strategy_en": s.get("strategy_en"),
            "signal_date": s.get("date"),
            "regime_state": s.get("regime_state"),
            "regime_vol": s.get("regime_vol"),
            "regime_score": s.get("regime_score"),
            "direction": direction,
            "entry_date": bars[entry_i]["date"],
            "entry_price": round(entry_price, 4),
            "exit_date": bars[exit_i]["date"],
            "exit_price": round(exit_price, 4),
            "pnl_pct": round(pnl * 100, 3),       # 百分数，含双边成本
            "hold_days": exit_i - entry_i + 1,
            "exit_reason": reason,
            "position_ratio": ratio,
            "daily": daily,
        })
    return trades


def build_equity_curve(trades: list) -> list:
    """
    等权组合净值曲线。日收益 = 当日持仓交易收益均值 × 总暴露（≤1）。
    返回 [{date, ret, nav}]（升序）。
    """
    by_date = defaultdict(list)
    for t in trades:
        for d in t["daily"]:
            by_date[d["date"]].append((d["ret"], t["position_ratio"]))
    dates = sorted(by_date)
    nav = 1.0
    curve = []
    for dt in dates:
        items = by_date[dt]
        ratios = [r for _, r in items]
        total = sum(ratios)
        exposure = min(1.0, total)
        day_ret = (sum(ret for ret, _ in items) / len(items)) * exposure if items else 0.0
        nav *= (1 + day_ret)
        curve.append({"date": dt, "ret": round(day_ret * 100, 4), "nav": round(nav, 6)})
    return curve


def compute_metrics(trades: list, curve: list, benchmark_ret: float = None) -> dict:
    """绩效指标：总收益/年化/最大回撤/夏普/胜率/盈亏比/超额。"""
    n = len(trades)
    wins = [t for t in trades if t["pnl_pct"] > 0]
    total_ret = curve[-1]["nav"] - 1 if curve else 0.0
    days = len(curve)
    years = days / TRADING_DAYS if days else 0
    annual = ((1 + total_ret) ** (1 / years) - 1) if (years > 0 and total_ret > -1) else -1.0

    peak, mdd = -1e9, 0.0
    for c in curve:
        peak = max(peak, c["nav"])
        mdd = min(mdd, c["nav"] / peak - 1)

    rets = [c["ret"] / 100 for c in curve]
    mean_r = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean_r) ** 2 for r in rets) / len(rets) if rets else 0.0
    sd = var ** 0.5
    sharpe = ((mean_r * TRADING_DAYS - RISK_FREE) / (sd * TRADING_DAYS ** 0.5)) if sd > 0 else 0.0

    gross_win = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in trades if t["pnl_pct"] <= 0))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)

    return {
        "trade_count": n,
        "win_rate": round(len(wins) / n * 100, 1) if n else None,
        "profit_factor": round(profit_factor, 2),
        "avg_pnl_pct": round(sum(t["pnl_pct"] for t in trades) / n, 3) if n else None,
        "avg_hold_days": round(sum(t["hold_days"] for t in trades) / n, 1) if n else None,
        "total_return": round(total_ret * 100, 2),
        "annual_return": round(annual * 100, 2),
        "max_drawdown": round(mdd * 100, 2),
        "sharpe": round(sharpe, 2),
        "excess_return": round((total_ret - (benchmark_ret or 0)) * 100, 2),
        "benchmark_return": round((benchmark_ret or 0) * 100, 2),
        "period_days": days,
        "exit_reasons": {r: sum(1 for t in trades if t["exit_reason"] == r)
                         for r in sorted({t["exit_reason"] for t in trades})},
    }
