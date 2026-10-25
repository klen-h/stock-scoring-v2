# strategies/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】战法选股模块

提供多种量化战法的选股扫描功能。

已实现战法：
- advance2retreat1: 进二退一（低位放量大阳后缩量回调）
- dragon_turnaround: 龙回头（龙头股第一波拉升后缩量回调，止跌反包时介入）
- limit_up_boomerang: 涨停回马枪（涨停突破后缩量回调不破实体，放量反包时介入）
- wizard_pointer: 仙人指路（强势股拉升途中长上影试盘，次日高开高走确认）
- double_cannon: 涨停双响炮（首板涨停后缩量回调，再度涨停启动主升浪）

震荡市备选战法：
- single_yang_unbroken: 单阳不破（一根大阳后横盘不破低点，缩量突破时介入）
- ma_pullback: 均线回踩（上升趋势中缩量回踩均线不破，放量反弹时低吸）
- old_duck_head: 老鸭头（5/10/60日均线多头排列后，缩量回调不破60日线，金叉放量启

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.backtest`
- `app.backtest.data`
- `app.backtest.engine`
- `app.backtest.market_regime`
- `app.backtest.strategies`
- `app.coach.audit`
- `app.coach.monitor`
- `app.contradictions.risk_hook`
- `app.database`
- `app.flash`
- `app.flash.intraday_alerts`
- `app.flash.rules`
- `app.flash.wechat`
- `app.mainforce.confluence`
- `app.mainforce.gate`

## 被谁依赖

- `backend/app/backtest/strategies.py`
- `backend/app/coach/audit.py`
- `backend/app/coach/monitor.py`
- `backend/app/coach/position_sizing.py`
- `backend/app/coach/rules.py`
- `backend/app/daily_report.py`
- `backend/app/env_check.py`
- `backend/app/exit_advice.py`
- `backend/app/flash/scheduler.py`
- `backend/app/main.py`
- `backend/app/routers/paper.py`
- `backend/app/trader_brief.py`
- `backend/test_grind_gate.py`
- `backend/tmp_archive/tmp_check_whitelist.py`
- `backend/tmp_archive/tmp_ingest_test.py`

## 关键阈值 / 环境变量

- `STRATEGY_GRIND_GATE`、`STRATEGY_WIDE_SCAN`、`WHITELIST_CRITERION`、`WHITELIST_HALF_LIFE_RATIO`、`WHITELIST_MIN_AVG_RET`、`WHITELIST_MIN_PROFIT_FACTOR`、`WHITELIST_ROLLING`、`WHITELIST_ROLLING_DAYS`

## 顶层结构（def/class，前 25 个）

`Advance2Retreat1Strategy`, `get_strategy`, `run_backtest`, `_enrich_klines`, `_backtest_stock`, `_track_trade`, `_calc_statistics`, `save_backtest_result`, `get_backtest_result`, `get_all_backtest_summary`, `_bj_today`, `filter_stock_pool`, `_expected_latest_trading_day`, `_zzshare_conn`, `_clear_kline_cache`, `_code_with_suffix`, `_get_klines_asof`, `_add_indicators`, `get_kline_with_indicators`, `calc_position_in_range`, `calc_volume_ratio`, `BaseStrategy`, `save_scan_result`, `get_scan_result`, `save_watch_pool`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：9002 行。
