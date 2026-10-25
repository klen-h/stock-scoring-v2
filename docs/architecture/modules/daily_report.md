# daily_report.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】每日 A 股大盘日报（盘后 16:20 自动生成）

定位：
与三段"快讯复盘"(run_review)互补——快讯复盘偏事件流；本日报聚焦
「全市场 + 系统状态 + 用户持仓 + 模拟盘」的体系化复盘。

组成（1-3 部分为硬数据直填 DB，LLM 不参与 → 保证准确；仅第 4 部分 LLM 解读）：
1. 今日市场脉冲：外围环境 + 指数结构 + ETF + 样本宽度（market_snapshot）
2. 系统状态：regime 判定与权重 / 评分榜 Top10 及较昨日变化 / 板块资金流 / 战法命中 / 模拟盘
3. 你的持仓：现价 / 浮盈亏（DB user_portfolio × 实时行情）
4. LLM 解读：今日主线 → 板块逻辑 → 明日预案 → 持仓观察（R1 单次调用，受日熔断保护）

评审驱动改进（2026-09-03）：
- 外围环境必须纳入（macro_snapshot 已有）
-

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.backtest.market_regime`
- `app.contradictions.store`
- `app.database`
- `app.eastmoney`
- `app.events.signal`
- `app.flash`
- `app.flash.margin_sentiment`
- `app.macro`
- `app.mainforce.confluence`
- `app.mainforce.state`
- `app.mainforce.trade_gate`
- `app.portfolio_scope`
- `app.signals`
- `app.strategies.recommendation`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/app/routers/stock.py`
- `scripts/daily_batch.py`
- `scripts/trigger_daily_report.py`

## 关键阈值 / 环境变量

- `DAILY_REPORT_PUSH`

## 顶层结构（def/class，前 25 个）

`ensure_table`, `_now`, `_today`, `_yesterday_trading_day`, `_macro_snapshot`, `_etf_pulse`, `_breadth`, `_regime`, `_ranking_top`, `_sector_flow`, `_strategy_scan`, `_push_status_md`, `_paper_summary`, `_portfolio`, `_fmt_money`, `_fmt_pct`, `_northbound`, `_mainforce_summary`, `_lhb_summary`, `_holdings_radar`, `build_data_md`, `_valid_holdings_codes`, `_render_llm_json`, `_llm_interpret`, `_llm_interpret_markdown`

## 已知坑

- 日报生成（"主力行为扫描"等章节）；战法推送状态 + 静默绩效行；不推企微只前端 /report。

规模：1142 行。
