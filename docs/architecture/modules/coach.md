# coach/

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

交易员教练（Coach）—— W1 纯规则骨架（2026-09-13）

模块划分（对齐 PLAN §2.4 / 外部研判简报 §3.4）：
rules.yaml  # 阈值集中配置（可调、可回滚）
rules.py    # 规则求值器（纯代码、可单测、不依赖 LLM）
audit.py    # 建议/执行/放弃理由落库 + 执行一致性度量
monitor.py  # 持仓轮询（30s，读内存行情缓存）+ 硬警报 + 关键时点体检卡

★ W1 不含 LLM（explainer.py 属 W2）：本阶段全部输出都由代码生成，
"LLM 与规则冲突"在结构上不可能发生——即评审 ①「事前无权」而非「事后否决」。

## 输入 / 输出

- audit.py    # 建议/执行/放弃理由落库 + 执行一致性度量
- ★ W1 不含 LLM（explainer.py 属 W2）：本阶段全部输出都由代码生成，

## 依赖谁（本项目内 import）

- `app`
- `app.backtest`
- `app.backtest.data`
- `app.coach`
- `app.coach.position_sizing`
- `app.database`
- `app.flash`
- `app.flash.margin_sentiment`
- `app.flash.rules`
- `app.flash.wechat`
- `app.macro`
- `app.mainforce.flow`
- `app.mainforce.overlay`
- `app.mainforce.phases`
- `app.mainforce.state`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/routers/coach.py`
- `backend/app/routers/user.py`
- `backend/app/strategies/paper_trading.py`
- `backend/app/trader_brief.py`
- `backend/app/weekly_review.py`
- `backend/test_coach_rules.py`
- `backend/test_position_sizing.py`
- `scripts/coach_rule_health.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- `COACH_ENABLED`、`COACH_EXPLAINER`

## 顶层结构（def/class，前 25 个）

`_cutoff`, `_load_alerts`, `_load_phases`, `_load_bars`, `_pick_bench`, `_fwd`, `_bench_ret`, `_mean`, `_build_rows`, `_group`, `advice_attribution`, `_sentence`, `_now`, `_today`, `ensure_tables`, `_ensure_columns`, `close_plan`, `plan_execution_rate`, `get_plans`, `abandon_plan`, `record_advices`, `log_entry_candidates`, `write_back_execution`, `execution_consistency`, `abandon_reasons`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：2756 行。
