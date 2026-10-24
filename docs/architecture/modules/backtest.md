# backtest/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

回测体系：历史数据（data）→ 撮合引擎（engine）→ 策略适配（strategies）→ CLI（run）。

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.backtest`
- `app.backtest.market_regime`
- `app.backtest.regime_review`
- `app.backtest.strategies`
- `app.database`
- `app.events.signal`
- `app.flash.rules`
- `app.macro`
- `app.mainforce.gate`
- `app.routers.market`
- `app.signals.tracker`
- `app.strategies.base`
- `app.wechat_fmt`

## 被谁依赖

- `backend/app/coach/rules.py`
- `backend/app/daily_report.py`
- `backend/app/edge_verify.py`
- `backend/app/env_check.py`
- `backend/app/events/signal.py`
- `backend/app/flash/scheduler.py`
- `backend/app/limit_stats.py`
- `backend/app/mainforce/confluence.py`
- `backend/app/mainforce/gate.py`
- `backend/app/mainforce/trade_gate.py`
- `backend/app/routers/backtest.py`
- `backend/app/routers/market.py`
- `backend/app/routers/performance.py`
- `backend/app/routers/scoring.py`
- `backend/app/routers/stock.py`

## 关键阈值 / 环境变量

- `WARFARE_EXIT_POLICY`

## 顶层结构（def/class，前 25 个）

`crowding_metrics`, `crowding_score`, `crowding_multiplier`, `to_secid`, `_tencent_code`, `_parse_klines`, `fetch_history_tencent`, `fetch_history`, `save_prices`, `invalidate_price_caches`, `_prices_cache_get`, `_prices_cache_put`, `load_prices`, `anomalies_in`, `has_anomaly`, `anomaly_index`, `window_has_anomaly`, `get_all_codes`, `_round_tick`, `_limit_pct`, `match_signals`, `build_equity_curve`, `compute_metrics`, `MarketState`, `_sma`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：3086 行。
