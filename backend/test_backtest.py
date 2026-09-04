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
  8. 涨停一字买不进 → 信号剔除（skipped_out 收集）
  9. 开盘涨停但盘中开板 → 可按开盘价成交
  10. 跌停一字卖不出 → 顺延下一交易日开盘价成交
  11. 跌停一字次日继续跌停 → 顺延至可卖日
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
assert t["exit_reason"] == "持有到期", t
print("[OK] 测试1: T+1 开盘成交 + 到期收盘平仓")

# ── 测试 2：止盈触发（tp 设低） ──
sig = [{"date": "2024-01-01", "code": "TEST01", "direction": "long",
        "take_profit": 10.5, "hold_days": 10}]
t = match_signals(sig, prices)[0]
assert t["exit_reason"] == "止盈", t
assert abs(t["exit_price"] - 10.5) < 1e-6, t
print("[OK] 测试2: 止盈按 take_profit 价成交")

# ── 测试 3：同日止损优先（可卖日同一天既触止损又触止盈 → 按止损） ──
# ★ T+1：入场日（02-02）不可卖，当日触发的止损不检查（这是 T+1 修复后的语义）
bars3 = [
    {"date": "2024-02-01", "open": 10.0, "high": 10.2, "low": 9.8, "close": 10.0},
    {"date": "2024-02-02", "open": 10.0, "high": 10.6, "low": 9.9, "close": 10.3},  # 入场日（不可卖）
    {"date": "2024-02-03", "open": 10.3, "high": 10.6, "low": 9.9, "close": 10.4},  # 可卖日：同日触 stop(9.95) 与 tp(10.5)
]
sig = [{"date": "2024-02-01", "code": "TEST03", "direction": "long",
        "stop_loss": 9.95, "take_profit": 10.5, "hold_days": 5}]
t = match_signals(sig, {"TEST03": bars3})[0]
assert t["exit_reason"] == "止损", t
assert abs(t["exit_price"] - 9.95) < 1e-6, t
print("[OK] 测试3: 可卖日同日止损优先（stop 价成交）")

# ── 测试 4：跳空低开破止损 → 按开盘价成交 ──
bars4 = [
    {"date": "2024-03-01", "open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0},
    {"date": "2024-03-04", "open": 9.5, "high": 9.6, "low": 9.4, "close": 9.5},  # 跳空低开破 9.8 止损
    {"date": "2024-03-05", "open": 9.5, "high": 9.7, "low": 9.4, "close": 9.6},
]
sig = [{"date": "2024-03-01", "code": "TEST04", "direction": "long",
        "stop_loss": 9.8, "hold_days": 5}]
t = match_signals(sig, {"TEST04": bars4})[0]
assert t["exit_reason"] == "止损", t
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
assert t["exit_date"] == "2024-01-20" and t["exit_reason"] == "持有到期", t
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

# ── 测试 8：涨停一字买不进 → 信号剔除 ──
# 信号日 05-01 收盘 10.0 → 次日 05-02 涨停价 = round_tick(10*1.1) = 11.0；
# 05-02 开=高=低=11.0（一字板全天未开板）→ 买不进，剔除
bars8 = [
    {"date": "2024-05-01", "open": 9.9, "high": 10.1, "low": 9.8, "close": 10.0},
    {"date": "2024-05-02", "open": 11.0, "high": 11.0, "low": 11.0, "close": 11.0},
    {"date": "2024-05-03", "open": 12.1, "high": 12.1, "low": 11.5, "close": 12.1},
]
skipped = []
trades8 = match_signals(
    [{"date": "2024-05-01", "code": "TEST08", "direction": "long", "hold_days": 3}],
    {"TEST08": bars8}, skipped_out=skipped)
assert trades8 == [], trades8
assert len(skipped) == 1 and skipped[0]["reason"] == "涨停一字买不进", skipped
print("[OK] 测试8: 涨停一字买不进剔除（skipped_out 收集）")

# ── 测试 9：开盘涨停但盘中开板 → 可按开盘价成交 ──
bars9 = [
    {"date": "2024-05-01", "open": 9.9, "high": 10.1, "low": 9.8, "close": 10.0},
    # 开盘 11.0（涨停）但 low 10.5 < 11.0 → 盘中开过板，视为可成交
    {"date": "2024-05-02", "open": 11.0, "high": 11.2, "low": 10.5, "close": 10.8},
    {"date": "2024-05-03", "open": 10.9, "high": 11.2, "low": 10.7, "close": 11.0},
]
t9 = match_signals(
    [{"date": "2024-05-01", "code": "TEST09", "direction": "long", "hold_days": 2}],
    {"TEST09": bars9})[0]
assert t9["entry_date"] == "2024-05-02" and abs(t9["entry_price"] - 11.0) < 1e-6, t9
print("[OK] 测试9: 开盘涨停盘中开板仍可成交")

# ── 测试 10：跌停一字卖不出 → 顺延次日开盘价成交 ──
# 05-01 收盘 10.0 → 05-03 跌停价 = round_tick(10.0*0.9) = 9.0；
# 05-03 触发止损(9.5)但全天封死跌停（开=高=低=9.0）→ 卖不出；
# 顺延 05-04 开盘 8.8 成交（更差，符合实盘）
bars10 = [
    {"date": "2024-05-01", "open": 9.9, "high": 10.1, "low": 9.8, "close": 10.0},
    {"date": "2024-05-02", "open": 10.0, "high": 10.2, "low": 9.9, "close": 10.0},   # 入场日
    {"date": "2024-05-03", "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0},      # 跌停一字
    {"date": "2024-05-04", "open": 8.8, "high": 9.2, "low": 8.7, "close": 9.0},
]
t10 = match_signals(
    [{"date": "2024-05-01", "code": "TEST10", "direction": "long",
      "stop_loss": 9.5, "hold_days": 5}],
    {"TEST10": bars10})[0]
assert t10["exit_date"] == "2024-05-04", t10
assert abs(t10["exit_price"] - 8.8) < 1e-6, t10
assert "跌停顺延" in t10["exit_reason"], t10
print("[OK] 测试10: 跌停一字卖不出顺延次日开盘")

# ── 测试 11：连续跌停一字 → 顺延至第一个可卖日 ──
bars11 = bars10 + [
    # 05-05：跌停价 = round_tick(8.1*0.9) = 7.29，全天一字 → 仍卖不出
    {"date": "2024-05-05", "open": 7.29, "high": 7.29, "low": 7.29, "close": 7.29},
    {"date": "2024-05-06", "open": 7.0, "high": 7.6, "low": 6.9, "close": 7.4},      # 开板
]
# 把 05-04 也改成一字跌停（05-03 收 9.0 → 跌停价 8.1）：连续两天卖不出
bars11[3] = {"date": "2024-05-04", "open": 8.1, "high": 8.1, "low": 8.1, "close": 8.1}
# 05-03/05-04 连续一字跌停（9.0 → 8.1），05-06 开板 → 顺延至 05-06 开盘 7.0 成交
t11 = match_signals(
    [{"date": "2024-05-01", "code": "TEST11", "direction": "long",
      "stop_loss": 9.5, "hold_days": 6}],
    {"TEST11": bars11})[0]
assert t11["exit_date"] == "2024-05-06", t11
assert abs(t11["exit_price"] - 7.0) < 1e-6, t11
assert "跌停顺延" in t11["exit_reason"], t11
print("[OK] 测试11: 连续跌停一字顺延至开板日")

print("\nALL TESTS PASSED")
