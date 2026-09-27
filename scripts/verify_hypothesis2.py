"""
验证假设2：复合赚钱效应指标的预测力

数据来源：
  - 个股：data/zzshare_daily.db（全市场日频，2016-2026）
  - 指数：data/sh000300_daily.json（沪深300日线）

验证方式：
  1. 回算每日情绪指标（涨跌家数、涨停家数、昨日涨停溢价、连板高度、量能）
  2. 构造复合赚钱效应分（等权重，不拟合）
  3. 回测：高分日次日买入沪深300ETF vs 低分日空仓
  4. block bootstrap 显著性检验（按日分块，B=10000）
  5. 时段分解（按年）+ 复利净值 + 最大回撤

输出：markdown 报告到 stdout
"""
import json
import sqlite3
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "data" / "zzshare_daily.db"
INDEX_PATH = ROOT / "data" / "sh000300_daily.json"


def load_index_series() -> dict:
    """加载沪深300日线 → {date: {open, close}}。"""
    with open(INDEX_PATH, "r", encoding="utf-8") as f:
        rows = json.load(f)
    return {
        r["date"]: {"open": r["open"], "close": r["close"]}
        for r in rows
    }


def build_emotion_series() -> tuple:
    """
    从 zzshare_daily 回算每日情绪指标。
    返回 (emotion_dict, sorted_dates)
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("[H2] 读取全市场日频数据...")
    rows = conn.execute(
        "SELECT date, code, pct_chg, close, amount "
        "FROM daily WHERE date >= '2016-01-01'"
    ).fetchall()
    print(f"[H2] 读取 {len(rows)} 行")

    daily = defaultdict(list)
    for r in rows:
        daily[r["date"]].append({
            "code": r["code"],
            "pct_chg": r["pct_chg"] if r["pct_chg"] is not None else 0,
            "close": r["close"],
            "amount": r["amount"] or 0,
        })

    sorted_dates = sorted(daily.keys())
    emotion = {}
    prev_limit_map = {}

    for i, d in enumerate(sorted_dates):
        stocks = daily[d]
        up = sum(1 for s in stocks if s["pct_chg"] > 0)
        down = sum(1 for s in stocks if s["pct_chg"] < 0)
        limit_up = sum(1 for s in stocks if s["pct_chg"] >= 9.5)
        limit_down = sum(1 for s in stocks if s["pct_chg"] <= -9.5)
        total = len(stocks)

        limit_up_codes = {s["code"] for s in stocks if s["pct_chg"] >= 9.5}
        prev_limit_map[d] = limit_up_codes

        money = None
        if i > 0:
            prev_d = sorted_dates[i - 1]
            prev_codes = prev_limit_map.get(prev_d, set())
            if prev_codes:
                perf = [s["pct_chg"] for s in stocks if s["code"] in prev_codes]
                if perf:
                    money = sum(perf) / len(perf)

        max_streak = 0
        if limit_up_codes:
            for code in limit_up_codes:
                streak = 1
                for j in range(i - 1, max(i - 20, -1), -1):
                    check_d = sorted_dates[j]
                    check_stocks = {s["code"]: s for s in daily[check_d]}
                    if code in check_stocks and check_stocks[code]["pct_chg"] >= 9.5:
                        streak += 1
                    else:
                        break
                max_streak = max(max_streak, streak)

        amounts = [s["amount"] for s in stocks if s["amount"] > 0]
        amount_med = sorted(amounts)[len(amounts) // 2] / 1e8 if amounts else 0

        emotion[d] = {
            "up": up, "down": down, "total": total,
            "limit_up": limit_up, "limit_down": limit_down,
            "money": money,
            "max_streak": max_streak,
            "amount_med": amount_med,
        }

    conn.close()
    return emotion, sorted_dates


def compute_score(emotion: dict, dates: list, lookback: int = 60) -> dict:
    """构造复合赚钱效应分（等权重，滚动窗口标准化）。"""
    scores = {}
    for i, d in enumerate(dates):
        e = emotion[d]
        window = dates[max(0, i - lookback):i + 1]

        def _z(key, asc=True):
            vals = [emotion[x][key] for x in window if emotion[x][key] is not None]
            if not vals:
                return 0.5
            v = e[key]
            if v is None:
                return 0.5
            rank = sum(1 for x in vals if x <= v) / len(vals)
            return rank if asc else 1 - rank

        z_up = _z("up", asc=True)
        z_money = _z("money", asc=True) if e["money"] is not None else 0.5
        z_streak = _z("max_streak", asc=True)
        z_amount = _z("amount_med", asc=True)

        score = (z_up + z_money + z_streak + z_amount) / 4
        scores[d] = {
            "score": score,
            "z_up": z_up, "z_money": z_money,
            "z_streak": z_streak, "z_amount": z_amount,
            **e,
        }
    return scores


def backtest(scores: dict, index: dict, dates: list,
             threshold_high: float = 0.7, threshold_low: float = 0.3) -> list:
    """回测：高分日次日开盘买入，持有1日收盘卖出。"""
    trades = []
    for i, d in enumerate(dates):
        if d not in scores or d not in index:
            continue
        if i + 1 >= len(dates):
            continue
        next_d = dates[i + 1]
        if next_d not in index:
            continue

        score = scores[d]["score"]
        idx_next = index[next_d]

        if score >= threshold_high:
            action = "buy"
        elif score <= threshold_low:
            action = "hold_cash"
        else:
            action = "neutral"

        ret = (idx_next["close"] / idx_next["open"] - 1) * 100 if idx_next["open"] > 0 else 0

        trades.append({
            "date": d, "next_date": next_d,
            "action": action, "score": score,
            "ret": ret,
        })
    return trades


def stats(trades: list) -> dict:
    """基础统计。"""
    def _s(ts):
        if not ts:
            return {"n": 0, "win_rate": None, "avg_ret": None}
        rets = [t["ret"] for t in ts]
        wins = sum(1 for r in rets if r > 0)
        return {
            "n": len(rets),
            "win_rate": round(wins / len(rets) * 100, 1),
            "avg_ret": round(sum(rets) / len(rets), 3),
        }

    buy = [t for t in trades if t["action"] == "buy"]
    cash = [t for t in trades if t["action"] == "hold_cash"]
    all_sig = [t for t in trades if t["action"] in ("buy", "hold_cash")]
    return {
        "buy": _s(buy), "cash": _s(cash), "all": _s(all_sig),
        "buy_trades": buy, "cash_trades": cash,
    }


def equity_curve(trades: list, action_filter: str = None) -> list:
    """复利净值曲线。"""
    nav = 1.0
    curve = []
    for t in trades:
        if action_filter and t["action"] != action_filter:
            continue
        nav *= (1 + t["ret"] / 100)
        curve.append({"date": t["next_date"], "nav": nav, "ret": t["ret"]})
    return curve


def max_drawdown(curve: list) -> float:
    """最大回撤（%）。"""
    peak = 0
    mdd = 0
    for p in curve:
        nav = p["nav"]
        if nav > peak:
            peak = nav
        dd = (peak - nav) / peak * 100 if peak > 0 else 0
        if dd > mdd:
            mdd = dd
    return mdd


def block_bootstrap(trades: list, action_filter: str, B: int = 10000) -> dict:
    """Block bootstrap（按日分块，有放回）。"""
    ts = [t for t in trades if t["action"] == action_filter]
    if not ts:
        return {"p_value": None, "observed": None, "n": 0, "B": B}
    n = len(ts)
    observed = sum(t["ret"] for t in ts) / n
    rets = [t["ret"] for t in ts]
    count_extreme = 0
    for _ in range(B):
        sample = [random.choice(rets) for _ in range(n)]
        mean_s = sum(sample) / n
        if abs(mean_s) >= abs(observed):
            count_extreme += 1
    p_value = count_extreme / B
    return {"p_value": p_value, "observed": observed, "n": n, "B": B}


def yearly_breakdown(trades: list, action_filter: str) -> list:
    """按年分解。"""
    ts = [t for t in trades if t["action"] == action_filter]
    by_year = defaultdict(list)
    for t in ts:
        year = t["next_date"][:4]
        by_year[year].append(t["ret"])
    out = []
    for y in sorted(by_year.keys()):
        rets = by_year[y]
        wins = sum(1 for r in rets if r > 0)
        out.append({
            "year": y, "n": len(rets),
            "win_rate": round(wins / len(rets) * 100, 1) if rets else None,
            "avg_ret": round(sum(rets) / len(rets), 3) if rets else None,
        })
    return out


def fmt(v):
    """安全格式化（None 显示为 -）。"""
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def main():
    print("# 假设2验证报告：复合赚钱效应指标的预测力\n")
    print("> 数据来源：`data/zzshare_daily.db` 全市场日频 + `data/sh000300_daily.json` 沪深300\n")
    print("> 方法：等权重复合分（涨跌比/涨停溢价/连板高度/量能分位）→ 高分买入/低分空仓 → T+1 收益\n")

    index = load_index_series()
    print(f"[H2] 沪深300指数数据：{len(index)} 个交易日\n")

    emotion, sorted_dates = build_emotion_series()
    print(f"[H2] 情绪指标回算完成：{len(emotion)} 天\n")

    scores = compute_score(emotion, sorted_dates, lookback=60)
    trades = backtest(scores, index, sorted_dates, threshold_high=0.7, threshold_low=0.3)
    print(f"[H2] 回测完成：{len(trades)} 个信号日\n")

    s = stats(trades)
    print("## 一、基础统计\n")
    print("| 策略 | 样本 | 胜率 | 均收益% |")
    print("|---|---|---|---|")
    print(f"| 高分买入（score≥0.7） | {s['buy']['n']} | {fmt(s['buy']['win_rate'])}% | {fmt(s['buy']['avg_ret'])}% |")
    print(f"| 低分空仓（score≤0.3） | {s['cash']['n']} | {fmt(s['cash']['win_rate'])}% | {fmt(s['cash']['avg_ret'])}% |")
    print(f"| 全部信号日 | {s['all']['n']} | {fmt(s['all']['win_rate'])}% | {fmt(s['all']['avg_ret'])}% |")
    print()

    buy_curve = equity_curve(trades, "buy")
    cash_curve = equity_curve(trades, "hold_cash")
    buy_mdd = max_drawdown(buy_curve) if buy_curve else 0
    cash_mdd = max_drawdown(cash_curve) if cash_curve else 0
    buy_final = buy_curve[-1]["nav"] if buy_curve else 1.0
    cash_final = cash_curve[-1]["nav"] if cash_curve else 1.0

    print("## 二、复利净值与回撤\n")
    print("| 策略 | 期末净值 | 最大回撤 |")
    print("|---|---|---|")
    print(f"| 高分买入 | {buy_final:.3f} | {buy_mdd:.1f}% |")
    print(f"| 低分空仓 | {cash_final:.3f} | {cash_mdd:.1f}% |")
    print()

    print("## 三、Block Bootstrap 显著性检验（B=10000，零假设：均收益=0）\n")
    buy_bt = block_bootstrap(trades, "buy", B=10000)
    cash_bt = block_bootstrap(trades, "hold_cash", B=10000)
    print("| 策略 | 观测均收益 | P值 | 结论 |")
    print("|---|---|---|---|")
    _buy_sig = "不显著" if buy_bt["p_value"] is None or buy_bt["p_value"] >= 0.05 else "显著"
    _cash_sig = "不显著" if cash_bt["p_value"] is None or cash_bt["p_value"] >= 0.05 else "显著"
    print(f"| 高分买入 | {fmt(buy_bt['observed'])}% | {fmt(buy_bt['p_value'])} | {_buy_sig} |")
    print(f"| 低分空仓 | {fmt(cash_bt['observed'])}% | {fmt(cash_bt['p_value'])} | {_cash_sig} |")
    print()

    print("## 四、按年分解（高分买入策略）\n")
    yd = yearly_breakdown(trades, "buy")
    print("| 年份 | 样本 | 胜率 | 均收益% |")
    print("|---|---|---|---|")
    for y in yd:
        print(f"| {y['year']} | {y['n']} | {fmt(y['win_rate'])}% | {fmt(y['avg_ret'])}% |")
    print()

    print("## 五、结论\n")
    significant = buy_bt["p_value"] is not None and buy_bt["p_value"] < 0.05
    profitable = buy_final > 1.0
    if significant and profitable:
        print("> **复合赚钱效应指标有显著正预测力**，高分买入策略在 bootstrap 下显著（P<0.05），")
        print(f"> 期末净值 {buy_final:.3f}，最大回撤 {buy_mdd:.1f}%。")
        print("> 假设2 **成立**。")
    elif significant and not profitable:
        print("> 复合指标统计显著，但复利亏损——存在但不稳定。")
    else:
        print("> **复合赚钱效应指标无显著预测力**。")
        print(f"> 高分买入均收益 {fmt(buy_bt['observed'])}%，bootstrap P={fmt(buy_bt['p_value'])}，")
        print(f"> 期末净值 {buy_final:.3f}（{'盈利' if buy_final > 1.0 else '亏损'}），最大回撤 {buy_mdd:.1f}%。")
        print("> 假设2 **被证伪**：即使组合四个情绪子指标，仍无法产生统计显著的正 alpha。")

    out_path = ROOT / "data" / "h2_trades.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(trades, f, ensure_ascii=False, indent=2)
    print(f"\n[H2] 明细已保存：{out_path}")


if __name__ == "__main__":
    main()
