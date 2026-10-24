# weekly_review.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】周复盘（用户视角的「本周回顾」）

URL：GET /api/report/weekly?days=7&end=YYYY-MM-DD

【为什么做】复盘层此前只有**当日闭环**（日报 + 今日执行回看 + 执行一致性），缺**周期视角**。
投资纪律的通行习惯是"周日晚上看周度"：本周市场怎么走、我执行得怎么样、系统说了什么。
用户框架原话——"复盘要记录状态/理由/执行偏差，区分逻辑错/时机错/执行错"：
日度做不到这件事，周度才可以。

【口径（★ 必须与展示一起披露）】
· 窗口 = `end`（默认今天，北京时间）往前 `days` 个**自然日**（默认 7），闭区间。
用**自然日**而非交易日：纪律是"按周看"的，跳过周末会把"7 天"变成"9 天"。
· regime 轨迹取自 `market_regime_history`（**日批落库**）⇒ 只含**已落库**的交易日，
可能不连续（日批失败/

## 输入 / 输出

- · regime 轨迹取自 `market_regime_history`（**日批落库**）⇒ 只含**已落库**的交易日，

## 依赖谁（本项目内 import）

- `app`
- `app.coach`
- `app.database`
- `app.flash.calendar_ahead`
- `app.flash.rules`

## 被谁依赖

- `backend/app/routers/daily_report.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_beijing_today`, `_window`, `_regime_track`, `_regime_sentence`, `_exec_sentence`, `_alerts_breakdown`, `_abandon_reasons`, `_reports`, `_push_summary`, `_next_week`, `_perf_sentence`, `_performance_block`, `_sector_block`, `build_weekly_review`

## 已知坑

- 周复盘；报告措辞注意月度复检口径（备源 vs 全口径）。

规模：377 行。
