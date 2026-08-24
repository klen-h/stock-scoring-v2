"""
================================================================================
【文件作用】历史评分 × 市场状态 分层复盘
================================================================================
对应 CONTEXT.md「行动建议 1（立刻做）」：
  找出哪些市场状态下拉低了整体胜率（大概率是防御市被技术面/资金面带崩），
  为后续「只改权重不改指标」的动态权重实验提供依据。

流程：
  1. 读取 ranking_history（每日 Top N 评分记录）
  2. 加载沪深300日线 → detect_market_regime → {日期: 市场状态}
  3. 对每条记录，用 backtest_prices 计算信号日后 N 个交易日的收益
  4. 按市场状态分组统计：样本数、胜率、平均收益、平均评分、信号分布
     （同时统计"仅买入类信号"的胜率，买入信号才是真正会被执行的信号）

运行方式：
  python -m app.backtest.run --strategy review            # 生成复盘报告
  python -m app.backtest.run --strategy all               # 全量报告（含复盘）
================================================================================
"""

from collections import defaultdict
from datetime import datetime, timedelta

from app.backtest import data
from app.backtest.market_regime import detect_market_regime, get_regime_weights
from app.database import db

# 状态中文标签（与 market_regime 的常量对齐）
STATE_LABELS = {
    "offensive": "进攻",
    "neutral": "震荡",
    "defensive": "防御",
}

# 会被实际执行的"买入类"信号
BUY_SIGNALS = ("强烈买入", "买入")


def _label(state: str) -> str:
    return STATE_LABELS.get(state, state)


# ──────────────────────────────────────────────────────────────
#  数据加载
# ──────────────────────────────────────────────────────────────

def load_ranking(days: int = 120) -> list:
    """读取最近 days 天的排行榜历史记录。"""
    start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = db.fetch(
        "SELECT rank_date, code, name, rank_pos, total_score, signal "
        "FROM ranking_history WHERE rank_date >= %s ORDER BY rank_date, rank_pos",
        (start,),
    )
    return [dict(r) for r in rows]


def load_regime_map(index_code: str = "sh000300") -> dict:
    """沪深300 状态序列 → {date: state}。"""
    bars = data.load_prices(index_code)
    if len(bars) < 70:  # 需要足够数据计算 MA60 + ADX
        return {}
    states = detect_market_regime(bars)
    return {s.date: s.state for s in states}


def _nearest_regime(regime_map: dict, date: str):
    """查不到时向前找最近一个状态（评分日可能落在周末/节假日）。"""
    if date in regime_map:
        return regime_map[date]
    d = datetime.strptime(date, "%Y-%m-%d")
    for _ in range(10):
        d -= timedelta(days=1)
        key = d.strftime("%Y-%m-%d")
        if key in regime_map:
            return regime_map[key]
    return None


def _forward_return(prices: list, date: str, horizon: int):
    """
    信号日后第 horizon 个交易日的收益率（%）。
    返回 (return_pct, ok)；价格数据缺失时 ok=False。
    """
    for i, p in enumerate(prices):
        if p["date"] == date:
            if i + horizon >= len(prices):
                return None, False
            base = prices[i]["close"]
            target = prices[i + horizon]["close"]
            if not base or base <= 0:
                return None, False
            return (target / base - 1) * 100, True
    return None, False


# ──────────────────────────────────────────────────────────────
#  核心复盘
# ──────────────────────────────────────────────────────────────

def run_review(days: int = 120, horizon: int = 5, index_code: str = "sh000300") -> dict:
    """
    分层复盘主入口，返回结构化结果（供渲染与后续实验复用）。

    返回结构：
      {status, days, horizon, index_code, window:[start,end], regime_days:{state:天数},
       groups: {state: {total, buy, no_price, unclassified,
                        total_win_rate, total_avg_ret, total_avg_score, buy_win_rate,
                        buy_avg_ret, signals:{signal:个数}}}}
    """
    records = load_ranking(days)
    regime_map = load_regime_map(index_code)
    if not regime_map:
        return {
            "status": "error",
            "message": f"沪深300（{index_code}）历史数据不足（<70 条），无法判定市场状态。"
                       "请先回填指数日线：python -m app.backtest.fill",
        }

    price_cache = {}

    def _prices(code: str) -> list:
        if code not in price_cache:
            price_cache[code] = data.load_prices(code)
        return price_cache[code]

    # groups[state] = 统计桶
    groups = {}
    for st in STATE_LABELS:
        groups[st] = {
            "total": 0, "buy": 0, "no_price": 0, "unclassified": 0,
            "total_returns": [], "total_scores": [], "buy_returns": [],
            "signals": defaultdict(int),
        }

    dates_seen = set()
    for r in records:
        state = _nearest_regime(regime_map, r["rank_date"])
        if state is None or state not in groups:
            continue  # 状态未知，跳过
        g = groups[state]
        ret, ok = _forward_return(_prices(r["code"]), r["rank_date"], horizon)
        if not ok:
            g["no_price"] += 1
            continue
        g["total"] += 1
        g["total_returns"].append(ret)
        if r.get("total_score") is not None:
            g["total_scores"].append(float(r["total_score"]))
        g["signals"][r.get("signal") or "观望"] += 1
        if (r.get("signal") or "") in BUY_SIGNALS:
            g["buy"] += 1
            g["buy_returns"].append(ret)
        dates_seen.add(r["rank_date"])

    # 汇总成扁平指标
    result = {
        "status": "ok",
        "days": days,
        "horizon": horizon,
        "index_code": index_code,
        "window": [min(dates_seen) if dates_seen else "-", max(dates_seen) if dates_seen else "-"],
        "regime_days": {st: sum(1 for d, s in regime_map.items() if s == st) for st in STATE_LABELS},
        "groups": {},
    }
    for st, g in groups.items():
        def _stats(returns):
            if not returns:
                return {"n": 0, "win_rate": None, "avg_ret": None}
            wins = sum(1 for v in returns if v > 0)
            return {
                "n": len(returns),
                "win_rate": round(wins / len(returns) * 100, 1),
                "avg_ret": round(sum(returns) / len(returns), 2),
            }

        t = _stats(g["total_returns"])
        b = _stats(g["buy_returns"])
        result["groups"][st] = {
            "total": g["total"],
            "buy": g["buy"],
            "no_price": g["no_price"],
            "unclassified": g["unclassified"],
            "total_win_rate": t["win_rate"],
            "total_avg_ret": t["avg_ret"],
            "total_avg_score": round(sum(g["total_scores"]) / len(g["total_scores"]), 1) if g["total_scores"] else None,
            "buy_win_rate": b["win_rate"],
            "buy_avg_ret": b["avg_ret"],
            "signals": dict(g["signals"]),
        }
    return result


# ──────────────────────────────────────────────────────────────
#  markdown 渲染
# ──────────────────────────────────────────────────────────────

def render_review_markdown(result: dict) -> str:
    if result.get("status") == "error":
        return f"## 历史评分 × 市场状态 分层复盘\n\n> ⚠️ {result['message']}\n"

    lines = ["## 历史评分 × 市场状态 分层复盘", ""]
    lines.append(f"> 窗口：{result['window'][0]} ~ {result['window'][1]}（最近 {result['days']} 天）")
    lines.append(f"> 基准指数：{result['index_code']}（沪深300），状态机：MA20/MA60 + ADX + ATR")
    lines.append(f"> 后市观察期：{result['horizon']} 个交易日")
    lines.append(f"> 权重策略：{_weight_str()}")
    lines.append("")

    # 一、状态分布
    lines += ["### 一、统计窗口内市场状态分布", "",
              "| 状态 | 交易日数 | 占比 |", "|---|---|---|"]
    total_days = sum(result["regime_days"].values()) or 1
    for st in STATE_LABELS:
        n = result["regime_days"].get(st, 0)
        lines.append(f"| {_label(st)} | {n} | {n / total_days * 100:.0f}% |")
    lines.append("")

    # 二、全部记录胜率
    lines += ["### 二、各状态胜率（全部评分记录）", "",
              "| 状态 | 样本 | 胜率 | 平均收益% | 平均评分 | 强烈买入 | 买入 | 观望 |",
              "|---|---|---|---|---|---|---|---|"]
    for st in STATE_LABELS:
        g = result["groups"].get(st, {})
        sig = g.get("signals", {})
        lines.append(
            f"| {_label(st)} | {g.get('total', 0)} | "
            f"{_pct(g.get('total_win_rate'))} | {_num(g.get('total_avg_ret'))} | "
            f"{_num(g.get('total_avg_score'))} | {sig.get('强烈买入', 0)} | "
            f"{sig.get('买入', 0)} | {sig.get('观望', 0)} |"
        )
    lines.append("")

    # 三、买入信号胜率（真正会被执行的信号）
    lines += ["### 三、买入信号胜率（强烈买入 + 买入）", "",
              "| 状态 | 买入样本 | 胜率 | 平均收益% |", "|---|---|---|---|"]
    for st in STATE_LABELS:
        g = result["groups"].get(st, {})
        lines.append(
            f"| {_label(st)} | {g.get('buy', 0)} | {_pct(g.get('buy_win_rate'))} | "
            f"{_num(g.get('buy_avg_ret'))} |"
        )
    lines.append("")

    # 四、结论
    lines.append("### 四、初步结论", "")
    lines.append(_conclusion(result))
    lines.append("")
    return "\n".join(lines)


def _weight_str() -> str:
    """展示三状态的动态权重配置（与 CONTEXT.md 表对应）。"""
    parts = []
    for st, label in STATE_LABELS.items():
        w = get_regime_weights(st)
        parts.append(f"{label} 技{w['technical']:.0%}/资{w['capital']:.0%}/基{w['fundamental']:.0%}")
    return "；".join(parts) + "（默认静态 技40%/资25%/基35%）"


def _pct(v):
    return "-" if v is None else f"{v}%"


def _num(v):
    return "-" if v is None else f"{v}"


def _conclusion(result: dict) -> str:
    """自动给出结论：找出买入信号胜率最低的状态，给出权重建议。"""
    buy_rows = []
    for st in STATE_LABELS:
        g = result["groups"].get(st, {})
        n = g.get("buy", 0)
        if n >= 5:  # 样本太少不参与结论
            buy_rows.append((st, n, g.get("buy_win_rate"), g.get("buy_avg_ret")))
    if not buy_rows:
        return "买入信号样本过少（各状态 < 5 条），暂无法给出可靠结论。建议继续积累排行榜历史后再复盘。"

    worst = min(buy_rows, key=lambda r: r[2] or 0)
    best = max(buy_rows, key=lambda r: r[2] or 0)
    out = [
        f"- 买入信号胜率最低的状态：**{_label(worst[0])}**（{worst[2]}%，样本 {worst[1]} 条，"
        f"平均收益 {worst[3]}%）",
        f"- 买入信号胜率最高的状态：**{_label(best[0])}**（{best[2]}%，样本 {best[1]} 条，"
        f"平均收益 {best[3]}%）",
    ]
    if worst[0] != "defensive":
        out.append(f"- 当前拉低胜率的是 {_label(worst[0])} 市，而非防御市——先用动态权重对照验证，"
                   "不要盲目加大基本面权重。")
    else:
        out.append("- 防御市确实在拉低胜率，符合预期：建议按动态权重降低技术面/资金面影响，"
                   "同时考虑接入基本面排雷指标（现金流、负债率过滤）。")
    w = get_regime_weights(worst[0])
    out.append(f"- 建议实验权重（{_label(worst[0])} 市）：技术面 {w['technical']:.0%} / "
               f"资金面 {w['capital']:.0%} / 基本面 {w['fundamental']:.0%}，"
               "用 ScoreEngine(regime=...) 跑「只改权重不改指标」对照。")
    return "\n".join(out)
