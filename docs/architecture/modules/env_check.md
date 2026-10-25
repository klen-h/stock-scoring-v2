# env_check.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

关键业务开关「生效值 vs 部署意图」自检（审查文档 §5.5，2026-09-14）。

为什么需要：**代码默认值兜底 ≠ 部署意图**。09-14 实测 GitHub Actions 的
`daily-batch.yml` 未配 `WHITELIST_CRITERION` → 日批用代码默认 `win_rate`
（该体系胜率天花板≈50% < 55% 门槛 → 白名单恒空 → 战法推送静默），而
Render 配了 `expectancy` → 两端行为分叉，此前**无任何可见性**。

做法：把「部署意图」固化为期望值表，运行时读取**实际生效值**逐一对比，
不符即在启动日志显著告警，并通过 `/api/health` 暴露（运维/监控可查）。
期望值可用 `EXPECT_<NAME>` 环境变量覆盖（应急/灰度，避免改代码）。

新增开关时：往 EXPECTED_SWITCHES + _actual 各加一项即可。

## 输入 / 输出

- 做法：把「部署意图」固化为期望值表，运行时读取**实际生效值**逐一对比，

## 依赖谁（本项目内 import）

- `app.backtest.strategies`
- `app.contradictions.risk_hook`
- `app.flash`
- `app.mainforce.gate`
- `app.mainforce.overlay`
- `app.mainforce.trade_gate`
- `app.sector_strength`
- `app.signals.tracker`
- `app.strategies.market_regime`
- `app.strategies.recommendation`

## 被谁依赖

- `backend/app/main.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- `ENV_CHECK_SKIP_LLM`

## 顶层结构（def/class，前 25 个）

`_on_off`, `_actual`, `switch_report`, `llm_chain_report`, `log_switch_report`

## 已知坑

- 环境变量核对（含 SECTOR_LINKAGE_MODE 等开关登记）；render.yaml 只是变量清单不是事实来源。

规模：193 行。
