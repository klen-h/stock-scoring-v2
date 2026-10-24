# routers/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】routers 包的初始化文件（空文件，声明这是包）

让 from app.routers import market, stock, ... 能正常工作。

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.auth`
- `app.backtest`
- `app.backtest.data`
- `app.backtest.engine`
- `app.backtest.market_regime`
- `app.backtest.run`
- `app.backtest.strategies`
- `app.coach`
- `app.coach.position_sizing`
- `app.contradictions`
- `app.contradictions.report`
- `app.contradictions.scanner`
- `app.contradictions.store`
- `app.contradictions.validator`

## 被谁依赖

- `backend/app/backtest/market_regime.py`
- `backend/app/cache_release.py`
- `backend/app/coach/regime_alert.py`
- `backend/app/coach/rules.py`
- `backend/app/flash/llm.py`
- `backend/app/flash/scheduler.py`
- `backend/app/flash_brief.py`
- `backend/app/limit_stats.py`
- `backend/app/macro.py`
- `backend/app/main.py`
- `backend/app/mainforce/gate_history.py`
- `backend/app/memory_watch.py`
- `backend/app/portfolio_radar.py`
- `backend/app/realtime_uplimit.py`
- `backend/app/scoring/indicator_cache.py`

## 关键阈值 / 环境变量

- `BATCH_CALLBACK_TOKEN`、`BATCH_DONE_WINDOW_HOURS`、`DAILY_BATCH_WORKFLOW`、`DB_LIMIT_MB`、`GATE_SNAPSHOT_MAX_AGE_DAYS`、`GITHUB_REF_NAME`、`GITHUB_REPO`、`GITHUB_TOKEN`、`MEM_LIMIT_MB`、`NB_COMPOSITE_RANK`、`RENDER_INSTANCE_ID`、`WECHAT_WECOM_AES_KEY`、`WECHAT_WECOM_TOKEN`

## 顶层结构（def/class，前 25 个）

`RegisterRequest`, `LoginRequest`, `register`, `login`, `get_current`, `_init_cache_table`, `_cache_save`, `_cache_load`, `_trade_light`, `_curve_summary`, `_build_response`, `get_strategy`, `_compute_and_cache`, `_refresh_background`, `preheat_all`, `list_reports`, `get_report_content`, `capital_root`, `northbound`, `main_flow`, `northbound_holdings`, `_seats_of`, `dragon_tiger`, `get_alerts`, `execute_alert`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：9676 行。
