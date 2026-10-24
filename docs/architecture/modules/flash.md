# flash/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】金十财经日历数据源（独立于快讯 source.py）

接口：https://rili-open-api.jin10.com/data/week_info
鉴权：x-app-id: fiXF2nOnDycGutVA + x-version: 2.0（★ 无需 Cookie，与快讯不同）
实测：缺 x-version 或 app-id 写错 → 网关返回 502（Gm-Error-Code: 81002），
且响应体为空、不报业务错 —— 排查时容易误判成"接口挂了"。

★ 接口硬限制：单次请求的日期区间不得超过 7 天，超过则**返回 0 条且不报错**
（实测 15 天/31 天区间均返回空数组）。因此长区间必须按 7 天分段请求后合并，
已封装在 fetch_range() 里，调用方无需感知。

三类数据（type 字段），归一化成统一结构后供前端/LLM 消费：
event   会议/讲话/事件：event

## 输入 / 输出

- 实测：缺 x-version 或 app-id 写错 → 网关返回 502（Gm-Error-Code: 81002），
- ★ 接口硬限制：单次请求的日期区间不得超过 7 天，超过则**返回 0 条且不报错**
- （实测 15 天/31 天区间均返回空数组）。因此长区间必须按 7 天分段请求后合并，

## 依赖谁（本项目内 import）

- `app`
- `app.backtest.market_regime`
- `app.backtest.run`
- `app.coach.monitor`
- `app.contradictions`
- `app.contradictions.labels`
- `app.contradictions.report`
- `app.contradictions.risk_hook`
- `app.contradictions.scanner`
- `app.contradictions.store`
- `app.contradictions.validator`
- `app.daily_report`
- `app.database`
- `app.eastmoney`
- `app.eastmoney_news`

## 被谁依赖

- `backend/app/backtest/market_regime.py`
- `backend/app/cache_release.py`
- `backend/app/coach/attribution.py`
- `backend/app/coach/audit.py`
- `backend/app/coach/explainer.py`
- `backend/app/coach/monitor.py`
- `backend/app/coach/regime_alert.py`
- `backend/app/coach/rules.py`
- `backend/app/contradictions/report.py`
- `backend/app/contradictions/risk_hook.py`
- `backend/app/contradictions/scanner.py`
- `backend/app/contradictions/store.py`
- `backend/app/contradictions/validator.py`
- `backend/app/daily_report.py`
- `backend/app/data_files.py`

## 关键阈值 / 环境变量

- `DEPLOY_READ_ONLY`、`ENABLE_HEAVY_JOBS`、`FLASH_COOKIE`、`FLASH_KEEP_DAYS`、`FLASH_KEEP_ROWS`、`FLASH_POLL_INTERVAL_SEC`、`JIN10_CALENDAR_APP_ID`、`JIN10_CALENDAR_URL`、`JIN10_CALENDAR_VERSION`、`JIN10_FLASH_URL`、`JIN10_MP_APP_ID`、`LLM_API_KEY`、`LLM_BASE_URL`、`LLM_DAILY_MAX_CALLS`、`LLM_FREE_API_KEY`

## 顶层结构（def/class，前 25 个）

`_fetch_week`, `fetch_range`, `_item_key`, `_norm_one`, `normalize`, `_ensure_table`, `_read_cache`, `_write_cache`, `refresh`, `load`, `get_items`, `upcoming`, `_fmt_num`, `format_for_llm`, `a_stock_holidays`, `_impact_of`, `_classify`, `_metric_key`, `ensure_table`, `upcoming`, `_short_date`, `run_alert`, `get_cb_gold`, `cb_gold_line`, `_alert_task`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：8025 行。
