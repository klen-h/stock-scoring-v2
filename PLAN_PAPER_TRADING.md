# 模拟盘/纸面交易模块实施计划

> 创建时间：2026-08-31
> 状态：规划中，未实施

## 设计目标

把盘后扫描的战法信号自动转为模拟持仓 → 次日开盘确认价成交 → 盘中/盘后自动跟踪止损止盈 → 平仓后回填真实胜率，反哺 `PUSH_STRATEGY_WHITELIST` 白名单刷新（`recommendation.py` 注释已预留此意图）。

## 核心设计原则

- **撮合口径与回测引擎完全同源**：复用 `backtest/engine.match_signals` 的 T+1 开盘成交、`low<=stop` 止损（按 `min(open,stop)` 出）、`high>=tp` 止盈、含双边成本的逻辑，保证模拟盘胜率与回测胜率可比。
- **默认全自动 + 可手动**：白名单战法信号自动入池；Strategies.vue 信号行也提供手动"转模拟持仓"按钮。
- **与真实持仓体系隔离**：不复用 `user_portfolio`/localStorage 体系，独立建表走后端持久化。

---

## Phase 1：数据层（建表 + 信号入池）

**新建表 `paper_positions`**（追加到 `schema.sql`，参照 `etf_signals` 生命周期结构）：

| 字段 | 说明 |
|---|---|
| id, code, name, strategy_name | 基础信息 |
| signal_date, confirmation_json | 信号来源（复用 `strategy_results` 的信号字段） |
| entry_price | 参考介入价（信号价） |
| fill_price, fill_date | **实际成交价/日**（次日开盘价） |
| stop_loss, target_price | 止损/止盈位 |
| shares, cost | 模拟股数（按 position_pct + 固定虚拟本金计算） |
| status | `pending → holding → closed / cancelled` |
| exit_price, exit_date, exit_reason | 平仓信息（stop_loss / take_profit / manual / expire / fill_rejected） |
| pnl_pct, pnl_amount, is_win | 平仓后计算（含双边成本，与 `match_signals` 相同参数） |
| fill_note | 五档指引 + 量比验证结果快照 |

约束：`UNIQUE(strategy_name, code, signal_date)` 保证幂等。

**新建表 `paper_account`**：虚拟总本金、已实现盈亏、当前占用仓位（单账户，无 user 维度，与现有单机架构一致）。

**入池逻辑（新文件 `backend/app/strategies/paper_trading.py`）：**
- `auto_ingest_signals()`：盘后扫描推送完成后调用，把白名单战法（`get_push_whitelist()`）+ 准入（`is_strategy_admitted`）+ 高/中置信度的当日信号写入 `paper_positions(status=pending)`，与企微推送同一批信号，幂等。
- 防重复持仓：同一 code 已有 holding 状态仓位则跳过。
- 池容量上限（如 20 个 pending），按 confidence 排序截断。

## Phase 2：成交确认（次日开盘）

**挂进现有 `open_confirmation_loop`（09:35-11:20 窗口）**，或新增 `paper_fill_loop` 同窗口执行：
- 复用 `get_stocks_batch` 拉实时行情 + `_calc_vol_ratios` 量比。
- 复用 `build_open_confirmation` 五档指引：
  - **放弃档**（高开>3% 或已破止损）→ `status=cancelled`，`exit_reason=fill_rejected`（也算信号质量数据）。
  - **可成交档**（正常/低吸/回踩）→ 按**实时开盘价** `fill_price` 成交 → `status=holding`，`fill_note` 存量比验证结果（高开缩量防诱多时降级为半仓）。
- 幂等：`is_schedule_done('paper_fill')`，错过窗口可补跑（用当日开盘价从 `backtest_prices` 兜底）。
- 成交后推企微摘要（复用 `push_strategy_signals` 管道）。

## Phase 3：持仓跟踪（止损/止盈）

**新增 `paper_track_loop`**，照 `track_loop` 模式：
- 盘中每 5 分钟拉持仓股实时行情：
  - `price <= stop_loss` → 止损平仓
  - `price >= target_price` → 止盈平仓
  - 可选增强：浮盈 >5% 后止损上移至成本价（移动止损，与回测口径需对齐确认）
- 盘后兜底：`backtest_prices_refresh_loop`（15:40）跑完后用日线 `low/high` 校验一遍，防止盘中循环漏判（对齐回测"日内先触发止损"规则）。
- 平仓时计算 `pnl_pct`（含成本参数，与 `match_signals` 相同）、`is_win`，写 `exit_*` 字段，推企微平仓通知。
- 超期强平：持有 N 日（如 20 日）未触发则按收盘价离场（对齐回测持有到期口径）。

## Phase 4：胜率回填

- 新增 `paper_stats()`：复用 `_strategy_stat` 口径（trades / win_rate / avg_pnl_pct / profit_factor / avg_hold_days），按 `strategy_name` 分组已平仓交易。
- **白名单自动刷新**：样本 ≥30 且胜率 ≥55% 的战法自动进入推送白名单，跌破则移除——实现 `recommendation.py` 注释中预留的意图，`PUSH_STRATEGY_WHITELIST` 改为"人工初始名单 ∪ 模拟盘自动名单"。
- 回写 `STRATEGY_STATS`（推荐话术中的历史胜率背书改用模拟盘实测值，标注数据来源）。
- 每日盘后把统计快照写入 `paper_stats_history`（可选表），观察胜率漂移。

## Phase 5：API + 前端

**后端新路由 `backend/app/routers/paper.py`**（前缀 `/api/paper`）：
- `GET /positions?status=`、`POST /positions/manual`（手动转仓）、`POST /positions/{id}/close`（手动平仓）、`DELETE /positions/{id}`（pending 取消）
- `GET /stats`（分战法胜率统计）、`GET /account`（账户总览）
- 注册进 `main.py`

**前端：**
- 新视图 `PaperTrading.vue`（或并入 Portfolio.vue 加 Tab）：pending（待确认）/ holding（持仓中，实时盈亏）/ closed（平仓记录）/ 统计面板（分战法胜率 vs 回测胜率对比）。
- `Strategies.vue` 信号行加"转模拟持仓"按钮（挂现有 addWatch/addPlan 旁）。
- `api/index.js` 增加对应封装。

## Phase 6：调度注册

`scheduler.start()` 新增两个 loop（每个 1 行）：
- `paper_fill_loop`（09:35-11:20）—— 或并入 `open_confirmation_loop`
- `paper_track_loop`（盘中每 5 分钟 + 盘后兜底一次）

---

## 实施顺序与工作量

| 阶段 | 内容 | 依赖 |
|---|---|---|
| 1 | 建表 + 入池逻辑 + 手动转仓 API | 无 |
| 2 | 开盘成交确认 loop | P1 |
| 3 | 止损止盈跟踪 loop | P2 |
| 4 | 统计回填 + 白名单自动刷新 | 有平仓数据后（约 2-4 周积累） |
| 5 | 前端页面 | P2 后即可开发 |

## 关键校验点

跑 1-2 周后，用同批信号对比"模拟盘实测胜率 vs 回测引擎胜率"，两者应高度接近（差异来自盘中实时触发 vs 日线回放的粒度差），这本身就是对整个回测体系的验证。

## 可复用的现有代码钩子

| 复用点 | 位置 |
|---|---|
| 表结构范本（生命周期字段） | `schema.sql` 的 `etf_signals` 表 |
| 信号来源 | `strategy_results.results_json`（15:40 `strategy_scan_loop` 落库） |
| 准入/白名单过滤 | `market_regime.is_strategy_admitted` + `recommendation.get_push_whitelist` |
| 五档指引 + 量比 | `recommendation.build_open_confirmation` + `scheduler._calc_vol_ratios` |
| 撮合口径 | `backtest/engine.match_signals`（T+1 开盘、止损止盈、含成本） |
| 统计口径 | `backtest/strategies._strategy_stat`（win_rate/avg_pnl_pct/profit_factor） |
| 调度模式 | `track_loop`（盘中每 5 分钟）+ `schedule_state` 幂等 |
| 企微推送 | `wechat.push_strategy_signals`（不受业务开关限制） |
| 前端按钮模式 | `Strategies.vue` 信号行的 addWatch/addPlan |
