# signals/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】ETF 信号跟踪（移植自 flash-monitor 的 tracker.js + pro-trader.js + data-layer.js）

包含：
- ETF 持仓池（22 只，宏观周期映射）+ 行情获取（复用 tencent.py）
- 风控门槛：持仓数 / 相关性组 / 总风险 / 技术分（RSI+均线）
- 信号状态机：waiting → active → closed（入场/止损/止盈/触阻力，含上升沿检测）
- 绩效统计（胜率/盈亏比/回撤/夏普）+ 专业交易员报告（Markdown）

【修复原项目缺陷】信号不再来自正则解析 LLM 的 Markdown，而是消费
llm.extract_structured_signals() 输出的结构化信号（build_signal_from_llm）。

数据文件 tracking.json 与原项目 schema 兼容——旧项目的 public/data/tracking.

## 输入 / 输出

- llm.extract_structured_signals() 输出的结构化信号（build_signal_from_llm）。

## 依赖谁（本项目内 import）

- `app`
- `app.backtest.market_regime`
- `app.database`
- `app.flash`
- `app.macro`
- `app.tencent`

## 被谁依赖

- `backend/app/backtest/strategies.py`
- `backend/app/daily_report.py`
- `backend/app/env_check.py`
- `backend/app/flash/scheduler.py`
- `backend/app/flash/service.py`
- `backend/app/routers/flash.py`
- `backend/backfill_history.py`
- `scripts/generate_backend_pack.py`

## 关键阈值 / 环境变量

- `MIDDAY_REGIME_GATE`

## 顶层结构（def/class，前 25 个）

`_midday_regime_gate`, `holdings_text`, `get_etf_quotes`, `get_market_data`, `calculate_rsi`, `check_trend`, `filter_signal_by_technical`, `calculate_position_size`, `_check_position_count`, `_check_correlation`, `_check_total_risk`, `load_tracking`, `save_tracking`, `build_signal_from_llm`, `add_signal_with_validation`, `record_price_history`, `update_signals`, `get_active_signals`, `get_history`, `get_performance`, `calculate_advanced_metrics`, `generate_pro_trader_report`, `_safe_dt`, `build_audit`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：884 行。
