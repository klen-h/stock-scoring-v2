# mainforce/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

主力行为分析模块（mainforce）

散户思维问"这只股票好不好"；主力思维问"筹码在谁手里、主力现在在做什么"。
本模块从三个角度还原主力行为，全部基于可回测验证的数据：

chips.py   筹码分布（获利盘/成本集中度/价格位置）—— 纯 OHLCV + 流通股本
flow.py    主力资金流（东财超大单/大单净流入，120 日历史回填落库）
phases.py  主力阶段标签（吸筹/洗盘/拉升/出货/下跌）—— 量价行为规则

设计约束（与 PLAN_CROWDING_FACTOR 一致）：
- 纯函数计算与网络/DB 分离，评分引擎与离线回测共用一套实现
- 所有因子先经 scripts/mainforce_factor_backtest.py 全池截面验证，
IC/分桶有边缘才接入评分（带开关），不达标只留报告

## 输入 / 输出

- flow.py    主力资金流（东财超大单/大单净流入，120 日历史回填落库）

## 依赖谁（本项目内 import）

- `app`
- `app.backtest.data`
- `app.backtest.market_regime`
- `app.backtest.strategies`
- `app.contradictions.store`
- `app.database`
- `app.flash`
- `app.flash.rules`
- `app.mainforce.chips`
- `app.mainforce.flow`
- `app.mainforce.gate`
- `app.mainforce.overlay`
- `app.mainforce.phases`
- `app.mainforce.state`
- `app.routers`

## 被谁依赖

- `backend/app/backtest/strategies.py`
- `backend/app/coach/attribution.py`
- `backend/app/coach/position_sizing.py`
- `backend/app/coach/regime_alert.py`
- `backend/app/coach/rules.py`
- `backend/app/contradictions/l3_scanner.py`
- `backend/app/daily_report.py`
- `backend/app/env_check.py`
- `backend/app/flash/scheduler.py`
- `backend/app/flash/store.py`
- `backend/app/portfolio_radar.py`
- `backend/app/research_cache.py`
- `backend/app/routers/performance.py`
- `backend/app/routers/scoring.py`
- `backend/app/routers/stock.py`

## 关键阈值 / 环境变量

- `MAINFORCE_MODE`、`STRATEGY_MAINFORCE_GATE`、`TRADE_GATE`

## 顶层结构（def/class，前 25 个）

`_to_bars_np`, `chip_series`, `chip_metrics`, `_metrics_at`, `_verdict`, `_env_snapshot`, `db_fetch`, `db_fetch_one`, `_industry_of`, `confluence_for_stock`, `confluence_line`, `_sina_code`, `fetch_flow_history_sina`, `to_secid`, `ensure_table`, `fetch_flow_history`, `fetch_flow_history_em`, `save_flow`, `get_all_codes`, `backfill_all`, `invalidate_fs_cache`, `get_float_shares_from_snapshot`, `get_float_shares`, `_cache_fresh`, `_ver_now`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：3272 行。
