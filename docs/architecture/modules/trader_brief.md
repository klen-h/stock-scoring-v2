# trader_brief.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】交易员决策简报（PLAN_TRADER_WORKFLOW.md Phase 1）

核心思想：以资深交易员的视角，按一天的时间线（盘前/盘中/盘后）聚合系统已有
数据（矛盾/主线/主力/持仓/候选信号），由 LLM 生成三段式决策简报：
「该关注 / 该做 / 该防」——每条判断必须引用具体数据出处。

原则（见 PLAN_TRADER_WORKFLOW.md，v2 采纳两份外部评审）：
1. 只聚合已有表的数据，不为简报新建采集任务
2. AI 出观点，数据给出处（引用校验器对 6 位代码做成员检查，未命中标「待核实」）
3. LLM 不可用/失败 → 规则骨架简报，绝不用 LLM 常识补写
4. 「该做」段 = 确定性规则引擎渲染（LLM 永不发明动作），每条带 rule_id
5. 形态定死：每段 ≤3 条 + 严重度排序；边界：简报=面向动作，日报=面向复盘

调度：Phase 1 先做 API 按需生成（前端加载时触发/手

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.backtest.market_regime`
- `app.backtest.strategies`
- `app.coach`
- `app.coach.position_sizing`
- `app.database`
- `app.flash`
- `app.flash.llm`
- `app.flash.margin_sentiment`
- `app.flash.rules`
- `app.macro`
- `app.mainline`
- `app.portfolio_radar`
- `app.risk_events`
- `app.routers.market`

## 被谁依赖

- `backend/app/coach/audit.py`
- `backend/app/flash/scheduler.py`
- `backend/app/routers/market.py`
- `backend/app/routers/system.py`
- `scripts/daily_batch.py`
- `scripts/test_decision_card_sf.py`

## 关键阈值 / 环境变量

- `MARGIN_DRAIN_ALERT_YI`

## 顶层结构（def/class，前 25 个）

`mark_pushed`, `current_phase`, `init_trader_brief_table`, `collect_brief_data`, `_data_to_markdown`, `_strategy_cn`, `_cons_tag`, `_render_actions_md`, `_render_funding_md`, `_render_pbw_md`, `_fallback_skeleton`, `_validate_refs`, `generate_trader_brief`, `_position_plan`, `_raw_industry_label`, `signal_industry_cross`, `signal_industry_cached`, `strategy_quality`, `strategy_quality_cached`, `_merged_buy_list`, `build_decision_card`

## 已知坑

- 早产简报坑（2026-10-23 修）：盘前页面按需生成会把"日期=今日、数据窗口=昨日"落库 ⇒ 日批 cached 命中后永不推送；GET /system/trader-brief 原本只写库不推送，现 refresh=true 带 push=true 盘后补推一次/天。

规模：1229 行。
