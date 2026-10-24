# contradictions/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

矛盾扫描引擎（Contradiction Scanner）

基于《Agent知识库_完整操作手册_v3.1》中的三层矛盾模型：
L1 预期差（Expected vs Actual）
L2 行为背离（Said vs Done / Narrative vs Flow）
L3 信息断层（Public vs Private）

本模块先落地 L2 行为背离的纯规则扫描，作为 MVP。

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.contradictions`
- `app.contradictions.l3_scanner`
- `app.contradictions.labels`
- `app.contradictions.scanner`
- `app.contradictions.store`
- `app.contradictions.validator`
- `app.database`
- `app.eastmoney`
- `app.flash`
- `app.flash.margin_sentiment`
- `app.flash.rules`
- `app.mainforce.l3_history`
- `app.tencent`

## 被谁依赖

- `backend/app/daily_report.py`
- `backend/app/env_check.py`
- `backend/app/flash/intraday_alerts.py`
- `backend/app/flash/scheduler.py`
- `backend/app/key_level_watch.py`
- `backend/app/mainforce/trade_gate.py`
- `backend/app/policy_bottom_watch.py`
- `backend/app/portfolio_radar.py`
- `backend/app/routers/contradictions.py`
- `backend/app/strategies/paper_trading.py`
- `scripts/breadth_hook_review.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- `CONTRADICTION_RISK_HOOK`

## 顶层结构（def/class，前 25 个）

`_src_fingerprint`, `_disk_load`, `_disk_save`, `_is_financial`, `_period_scale`, `_load_reports`, `scan_financial_gap`, `severity_cn`, `level_cn`, `type_cn`, `metric_cn`, `_fmt_amount`, `fmt_metric_value`, `_fmt_money`, `_render_markdown`, `_llm_summary`, `generate_report`, `run_report`, `_hook_enabled`, `breadth_collapse_active`, `risk_multiplier`, `trend_paused`, `reset_cache`, `_today`, `set_realtime_mode`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：2097 行。
