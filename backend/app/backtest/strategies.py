"""
================================================================================
【文件作用】三类策略回测适配器：LLM 信号绩效追踪 / 战法选股回测 / 宏观方向分回测
================================================================================
  LLM 信号：已落盘交易统计（无法历史重放，样本=真实运行期，结论仅供参考）
  战法选股：strategy_results 扫描日 → T+1 开盘买入 → 持有 5 日（前70%/后30%切分防过拟合）
  宏观方向：macro_daily 方向分 → 沪深300ETF 全仓/空仓日频调仓（T+1 生效）
================================================================================
"""

import json
import time
from collections import defaultdict

from app.backtest import data, engine
from app.database import db

WARFARE_HOLD_DAYS = 5      # 战法默认持有交易日数
MACRO_ETF = "sh510300"     # 宏观方向分回测标的：沪深300ETF
MACRO_ETF_NAME = "沪深300ETF"

# ================================================================
#  一、LLM 信号绩效追踪（已发生交易统计，与 audit 同口径）
# ================================================================

def backtest_llm_signals() -> dict:
    """统计已平仓交易：总体 + 按来源分组胜率/盈亏比。
    口径与 flash audit（tracker.build_audit）完全一致：
    status='closed' 全部纳入，profit 为空按 0 计，保证交叉验证误差 < 0.01%。"""
    rows = db.fetch("SELECT * FROM etf_signals WHERE status = 'closed'")
    closed = rows

    def _stat(rs):
        n = len(rs)
        wins = [r for r in rs if r["is_win"]]
        profits = [float(r["profit"] or 0) for r in rs]  # 与 audit 同口径：null 按 0
        gross_win = sum(p for p in profits if p > 0)
        gross_loss = abs(sum(p for p in profits if p < 0))
        return {
            "closed": n,
            "win_rate": round(len(wins) / n * 100, 1) if n else None,
            "avg_profit_pct": round(sum(profits) / n, 3) if n else None,
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0
                             else (999.0 if gross_win > 0 else 0.0),
        }

    phase_names = {"premarket": "盘前", "lunchbreak": "午盘", "postmarket": "盘后"}
    by_source = {}
    for key, cname in phase_names.items():
        rs = [r for r in closed if (r.get("source") or "") == key]
        if rs:
            s = _stat(rs)
            s["name"] = cname
            by_source[key] = s
    # 其他来源（事件分析等）
    others = [r for r in closed if (r.get("source") or "") not in phase_names]
    if others:
        s = _stat(others)
        s["name"] = "其他"
        by_source["other"] = s

    total = _stat(closed)
    total["proposed"] = db.fetch_one(
        "SELECT COUNT(*) AS c FROM etf_signals")["c"]
    return {
        "type": "signals",
        "label": "LLM 信号绩效追踪",
        "sample_note": f"样本=真实运行期（{len(closed)} 笔平仓），结论仅供参考",
        "total": total,
        "by_source": by_source,
    }


# ================================================================
#  二、战法选股回测（strategy_results → 撮合）
# ================================================================

def _warfare_signal_stream(strategy_name: str = None) -> list:
    """strategy_results → 信号流（按 scan_date 升序）。"""
    sql = ("SELECT strategy_name, scan_date, results_json FROM strategy_results "
           "WHERE count > 0")
    params = []
    if strategy_name:
        sql += " AND strategy_name = %s"
        params.append(strategy_name)
    sql += " ORDER BY scan_date ASC"
    signals = []
    for r in db.fetch(sql, tuple(params)):
        try:
            items = json.loads(r["results_json"])
        except (json.JSONDecodeError, KeyError):
            continue
        for it in items or []:
            code = str(it.get("code") or "").strip()
            if len(code) != 6:
                continue
            pos = float(it.get("position_pct") or 20) / 100.0
            signals.append({
                "date": r["scan_date"],
                "code": code,
                "direction": "long",
                "stop_loss": it.get("stop_loss"),
                "take_profit": it.get("target_price"),
                "hold_days": WARFARE_HOLD_DAYS,
                "position_ratio": min(max(pos, 0.05), 1.0),
                "is_etf": False,
                "strategy": r["strategy_name"],
            })
    return signals


_PRICES_CACHE = {}        # {(code, start): (ts, bars)} 进程内缓存，TTL 5 分钟
_PRICES_TTL = 300

def _load_prices_map(codes: set, start: str = None) -> dict:
    """一次 IN 查询加载多只股票日线。

    远程 Supabase 传输是瓶颈（每 ~700 行需数秒），因此：
      - start 过滤：只取信号日之后的 K 线（撮合只需这些），传输量降 95%+
      - 进程内缓存：5 分钟内重复调用零查询（API 多次访问 / 三段切分复用）
    """
    codes = [c for c in codes if c]
    if not codes:
        return {}
    now = time.time()
    m = {}
    miss = []
    for c in codes:
        hit = _PRICES_CACHE.get((c, start))
        if hit and now - hit[0] <= _PRICES_TTL:
            m[c] = hit[1]
        else:
            miss.append(c)
    if miss:
        sql = ("SELECT * FROM backtest_prices WHERE code IN (%s) "
               % ",".join(["%s"] * len(miss)))
        params = list(miss)
        if start:
            sql += " AND date >= %s"
            params.append(start)
        tmp = defaultdict(list)
        for r in db.fetch(sql, tuple(params)):
            tmp[r["code"]].append({
                "date": r["date"], "open": r["open"], "high": r["high"],
                "low": r["low"], "close": r["close"], "volume": r["volume"],
            })
        for c in miss:
            bars = tmp.get(c, [])
            bars.sort(key=lambda b: b["date"])
            _PRICES_CACHE[(c, start)] = (now, bars)
            m[c] = bars
    return m

def _run_warfare(signals: list, label: str, prices_map: dict = None) -> dict:
    """撮合一组战法信号 → 结果 dict。prices_map 可复用（避免重复查库）。"""
    if prices_map is None:
        prices_map = _load_prices_map({s["code"] for s in signals})
    trades = engine.match_signals(signals, prices_map)
    if not trades:
        return {"type": "warfare", "label": label, "trades": [], "metrics": None,
                "sample_note": "无成交"}
    curve = engine.build_equity_curve(trades)
    start_d, end_d = curve[0]["date"], curve[-1]["date"]
    bench = _benchmark_ret(start_d, end_d, prices_map)
    metrics = engine.compute_metrics(trades, curve, bench)
    return {"type": "warfare", "label": label, "trades": trades,
            "metrics": metrics, "curve": curve,
            "sample_note": f"{start_d} ~ {end_d}"}


def backtest_warfare(strategy_name: str = None) -> dict:
    """战法回测：全体（或指定战法），含前 70% / 后 30% 切分防过拟合。"""
    signals = _warfare_signal_stream(strategy_name)
    if not signals:
        return {"type": "warfare", "label": strategy_name or "全部战法",
                "trades": [], "metrics": None, "sample_note": "无信号"}
        # 价格一次加载，整体/样本内/样本外三段复用
    prices_map = _load_prices_map({s["code"] for s in signals} | {"sh000300"},
                                  start=min(x["date"] for x in signals))
    result = _run_warfare(signals, strategy_name or "全部战法", prices_map)
    # 时间切分：按信号日 70/30
    n = len(signals)
    split_i = int(n * 0.7)
    head, tail = signals[:split_i], signals[split_i:]
    if len(head) >= 3:
        result["in_sample"] = _run_warfare(head, "前70%样本", prices_map)
    if len(tail) >= 3:
        result["out_sample"] = _run_warfare(tail, "后30%样本", prices_map)
    return result


# ================================================================
#  三、宏观方向分回测（macro_daily → 沪深300ETF 全仓/空仓）
# ================================================================

def backtest_macro() -> dict:
    """方向分 score>0 全仓沪深300ETF、score≤0 空仓；信号日 T+1 生效。"""
    items = db.fetch("SELECT date, data_json FROM macro_daily ORDER BY date ASC")
    score_map = {}
    for r in items:
        try:
            snap = json.loads(r["data_json"])
            sc = (snap.get("direction") or {}).get("score")
            if sc is not None:
                score_map[r["date"]] = float(sc)
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    bars = data.load_prices(MACRO_ETF)
    if not bars or not score_map:
        return {"type": "macro", "label": "宏观方向分回测", "trades": [],
                "metrics": None, "sample_note": "数据不足（需 macro_daily + ETF 日线）"}
    dates = sorted(score_map)
    first_signal = dates[0]

    # 逐日：当天 exposure 由「该日之前的最近一次信号」决定（T+1 生效）
    curve, nav = [], 1.0
    last_score, idx, prev_close = None, 0, None
    for bar in bars:
        if bar["date"] <= first_signal:
            prev_close = bar["close"]
            continue
        while idx < len(dates) and dates[idx] < bar["date"]:
            last_score = score_map[dates[idx]]
            idx += 1
        if last_score is None or prev_close is None or prev_close <= 0:
            prev_close = bar["close"]
            continue
        exposure = 1.0 if last_score > 0 else 0.0
        ret = (bar["close"] / prev_close - 1) * exposure
        nav *= (1 + ret)
        curve.append({"date": bar["date"], "ret": round(ret * 100, 4),
                      "nav": round(nav, 6)})
        prev_close = bar["close"]

    if not curve:
        return {"type": "macro", "label": "宏观方向分回测", "trades": [],
                "metrics": None, "sample_note": "无持仓区间"}
    bench = _benchmark_ret(curve[0]["date"], curve[-1]["date"])
    metrics = engine.compute_metrics([], curve, bench)
    return {"type": "macro", "label": "宏观方向分回测", "trades": [],
            "metrics": metrics, "curve": curve,
            "sample_note": f"{curve[0]['date']} ~ {curve[-1]['date']}（样本期短，结论仅供参考）"}


# ================================================================
#  工具：基准区间收益
# ================================================================

def _benchmark_ret(start: str, end: str, prices_map: dict = None) -> float:
    """沪深300 区间收益（start 日开盘 → end 日收盘）。prices_map 含 sh000300 时直接复用，免远程查询。"""
    if prices_map is not None and "sh000300" in prices_map:
        bars = [b for b in prices_map["sh000300"] if start <= b["date"] <= end]
    else:
        bars = data.load_prices("sh000300", start, end)
    if len(bars) < 2:
        return 0.0
    return bars[-1]["close"] / bars[0]["open"] - 1
