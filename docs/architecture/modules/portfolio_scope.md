# portfolio_scope.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】「本部署在服务哪个用户」的唯一口径 —— 供 user_portfolio 的全局读取使用

背景（2026-09-17 实测）：
项目从单用户演进到多用户（`migrate_user_isolation.py` 给 user_portfolio 加了
`user_id`），**前端接口全部按 user_id 过滤**，但**后台全局组件仍在全表读** →
会把别的账号的持仓当成本部署用户的：

· `coach/rules.py::_real_holdings()`      → 教练盯**所有账号**的持仓
· `coach/position_sizing.py`              → 仓位建议含所有账号
· `daily_report.py::_portfolio()`         → 日报含所有账号
· `flash/scheduler.py`（舆情 / 快照池 ×2） → 推**别人**

## 输入 / 输出

- 【文件作用】「本部署在服务哪个用户」的唯一口径 —— 供 user_portfolio 的全局读取使用

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- `backend/app/coach/position_sizing.py`
- `backend/app/coach/rules.py`
- `backend/app/daily_report.py`
- `backend/app/flash/llm.py`
- `backend/app/flash/scheduler.py`
- `backend/app/portfolio_radar.py`
- `backend/app/routers/market.py`

## 关键阈值 / 环境变量

- `PRIMARY_USER_ID`

## 顶层结构（def/class，前 25 个）

`primary_user_id`, `portfolio_where`

## 已知坑

- 持仓范围界定。

规模：75 行。
