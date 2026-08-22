"""
================================================================================
【文件作用】回测撮合引擎单元测试（手动验证撮合/成本/指标逻辑是否合理）
================================================================================
运行方式：python test_backtest.py（不依赖网络/数据库）

测试覆盖：
  1. T+1 开盘价成交
  2. 止盈高低价触发（exit_price = take_profit）
  3. 同日止损优先（同日既触止损又触止盈 → 按止损）
  4. 跳空低开破止损 → 按开盘价成交
  5. 手续费扣减（买入佣金+滑点，卖出佣金+滑点+印花税）
  6. 到期持有期末收盘价平仓
  7. 最大回撤计算
================================================================================
"""

from app.backtest.engine import match_signals, build_equity_curve, compute_metrics

# 20 天假 K 线：价格每天涨 1%，high=close+0.3, low=close-0.3, open=close*0.999
def make_bars(start_price=10.0, days=20, daily_chg=0.01):
    bars = []
    p = start_price
    for i in range(days):
        close = round(p, 3)
        bars.append({
            "date": f"2024-01-{i + 1:02d}",
            "open": round(close * 0.999, 3),
            "high": round(close + 0.3, 3),
            "low": round(close - 0.3, 3),
            "close": close,
        })
        p *= (1 + daily_chg)
    return bars


bars = make_bars()
prices = {"TEST01": bars}

# ── 测试 1：T+1 开盘成交 + 到期收盘平仓（hold 3 天） ──
sig = [{"date": "2024-01-01", "code": "TEST01", "direction": "long", "hold_days": 3}]
trades = match_signals(sig, prices)
t = trades[0]
# 信号日 01-01 之后的第一个交易日是 01-02 → 以 01-02 的 open（≈10.0*0.999*1.01）成交
assert t["entry_date"] == "2024-01-02", t
assert abs(t["entry_price"] - round(10.0 * 1.01 * 0.999, 3)) < 0.01, t
# hold 3 天：01-02 入场 + 2 个持仓日 → 01-04 收盘平仓
assert t["exit_date"] == "2024-01-04", t
assert t["exit_reason"] == "hold", t
print("[OK] 测试1: T+1 开盘成交 + 到期收盘平仓")

# ── 测试 2：止盈触发（tp 设低） ──
sig = [{"date": "2024-01-01", "code": "TEST01", "direction": "long",
        "take_profit": 10.5, "hold_days": 10}]
t = match_signals(sig, prices)[0]
assert t["exit_reason"] == "take_profit", t
assert abs(t["exit_price"] - 10.5) < 1e-6, t
print("[OK] 测试2: 止盈按 take_profit 价成交")

# ── 测试 3：同日止损优先（构造 stop 与 tp 同一天都触及） ──
bars3 = [
    {"date": "2024-02-01", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0},
    {"date": "2024-02-02", "open": 10.0, "high": 10.6, "low": 9.9, "close": 10.3},  # 同日触 tp(10.5) 与 stop(9.95)
    {"date": "2024-02-03", "open": 10.3, "high": 10.5, "low": 10.1, "close": 10.4},
]
sig = [{"date": "2024-02-01", "code": "TEST03", "direction": "long",
        "stop_loss": 9.95, "take_profit": 10.5, "hold_days": 5}]
t = match_signals(sig, {"TEST03": bars3})[0]
assert t["exit_reason"] == "stop", t
assert abs(t["exit_price"] - 9.95) < 1e-6, t
print("[OK] 测试3: 同日止损优先（stop 价成交）")

# ── 测试 4：跳空低开破止损 → 按开盘价成交 ──
bars4 = [
    {"date": "2024-03-01", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0},
    {"date": "2024-03-04", "open": 9.5, "high": 9.6, "low": 9.4, "close": 9.5},  # 跳空低开破 9.8 止损
    {"date": "2024-03-05", "open": 9.5, "high": 9.7, "low": 9.4, "close": 9.6},
]
sig = [{"date": "2024-03-01", "code": "TEST04", "direction": "long",
        "stop_loss": 9.8, "hold_days": 5}]
t = match_signals(sig, {"TEST04": bars4})[0]
assert t["exit_reason"] == "stop", t
assert abs(t["exit_price"] - 9.5) < 1e-6, t   # min(open 9.5, stop 9.8) = 9.5
print("[OK] 测试4: 跳空破止损按开盘价成交")

# ── 测试 5：手续费扣减 ──
# 无涨跌的假 K 线：gross=0，pnl 应 = -(买成本 + 卖成本)
bars5 = [{"date": d, "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0}
         for d in ["2024-04-01", "2024-04-02", "2024-04-03"]]
sig = [{"date": "2024-04-01", "code": "TEST05", "direction": "long", "hold_days": 3}]
t = match_signals(sig, {"TEST05": bars5})[0]
# ETF: 买入 (0.025%+0.05%) + 卖出 (0.025%+0.05%) = 0.15%
assert abs(t["pnl_pct"] - (-0.15)) < 1e-6, t
sig[0]["is_etf"] = False   # 个股：买(0.05%佣金+0.05%滑点) + 卖(0.05%佣金+0.05%滑点+0.1%印花税) = 0.3%
t2 = match_signals(sig, {"TEST05": bars5})[0]
assert abs(t2["pnl_pct"] - (-0.3)) < 1e-6, t2
print("[OK] 测试5: 手续费扣减（ETF 0.15% / 个股 0.3%）")

# ── 测试 6：到期持有期末收盘价平仓 ──
sig = [{"date": "2024-01-01", "code": "TEST01", "direction": "long", "hold_days": 20}]
t = match_signals(sig, prices)[0]
assert t["exit_date"] == "2024-01-20" and t["exit_reason"] == "hold", t
assert abs(t["exit_price"] - bars[-1]["close"]) < 1e-6, t
print("[OK] 测试6: 到期按期末收盘价平仓")

# ── 测试 7：最大回撤 ──
# 构造已知回撤的曲线：1.0 → 1.2 → 0.9（回撤 25%）→ 1.1
fake_curve = [
    {"date": "d1", "ret": 0.0, "nav": 1.0},
    {"date": "d2", "ret": 20.0, "nav": 1.2},
    {"date": "d3", "ret": -25.0, "nav": 0.9},
    {"date": "d4", "ret": 22.22, "nav": 1.1},
]
m = compute_metrics([], fake_curve)
assert abs(m["max_drawdown"] - (-25.0)) < 0.01, m
assert abs(m["total_return"] - 10.0) < 0.01, m
print("[OK] 测试7: 最大回撤与总收益计算")

print("\nALL TESTS PASSED")
