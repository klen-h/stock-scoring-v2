# 模拟盘组合风控实施计划

> 创建时间：2026-09-02
> 状态：规划中，未实施
> 前置：`PLAN_PAPER_TRADING.md` Phase 1-6 已上线（成交确认 / T+1 跟踪 / 统计回填）

## 设计目标

模拟盘当前只有**逐仓止损止盈 + 资金约束**，缺组合级风控。不补上的后果：

1. **行业集中**：同一战法同一天选出的股票高度同板块（如全是银行），实盘不会满仓 5 只同行业 → 模拟盘胜率**系统性高估实盘**
2. **无熔断**：战法进入失效期时无限开新仓，失去对"连亏期"的真实检验
3. **等额仓位失真**：固定 5 万/仓不看个股波动，高波动股的风险敞口被低估

**核心原则：风控只做在"开仓 gate + 账户状态"层，不改已成交笔的撮合与 pnl 口径**
——胜率按笔统计与回测仍可比；风控改变的是"开不开这一笔"，不是"这笔怎么算"。

---

## 规则总表

### A. 开仓 Gate（`fill_pending_positions` 成交分支，与现有资金检查并列）

| # | 规则 | 阈值（可配常量） | 动作 | exit_reason |
|---|---|---|---|---|
| G1 | 同行业持仓上限 | 同 `main_industry` ≤ **2** 只 | 放弃 | `industry_limit` |
| G2 | 组合回撤熔断 | 账户净值自峰值回撤 ≥ **5%** | 冻结开仓（净值修复或手动解锁） | `risk_freeze` |
| G3 | 连亏冷却 | 连续 **3** 笔止损平仓 | 冷却 **1 个交易日** | `cooldown` |
| G4 | 日亏损限额 | 当日平仓亏损合计 ≥ **2%** 本金 | 当日不再开新仓 | `daily_stop` |

已有且不重复实现：资金检查（`no_capital`）、单战法上限 8 笔（`strategy_limit`）、
入池时止损异常过滤、T+1 不可卖。

### B. 波动率仓位（替代固定 5 万等分）

```
每股风险 = entry_price - stop_loss          （信号自带止损，天然可用）
单笔风险额 = 本金 × 0.8% = 8,000 元          （对齐 tracker.RISK_CONFIG 的 1.5%，因 20 仓更保守）
股数 = 单笔风险额 / 每股风险
市值钳位 = [20,000, 80,000]                  （防极端：低价股巨量 / 高价股 1 手都买不起）
止损缺失兜底：每股风险 = 2 × ATR14（get_kline 日线计算，参考 backtest/market_regime._atr）
```

与回测口径差异：回测等权 `position_ratio`，模拟盘波动率仓位 → **胜率按笔统计不受影响**；
组合收益曲线口径不同，对比验证时注明（胜率对比为主，收益对比为辅）。

### C. 半仓（防诱多）逻辑保留

`half` 档 = 股数减半（风险额同减半），与现有一致。

---

## 数据层变更

**`paper_account` 幂等加列**（ALTER TABLE IF NOT EXISTS 风格，参照 ranking_history 的迁移）：

| 字段 | 说明 |
|---|---|
| peak_equity REAL | 账户净值峰值（回撤基准），初始 = initial_capital |
| cooldown_until TEXT | 连亏冷却截止日（YYYY-MM-DD，空=无） |
| risk_frozen INTEGER | 回撤熔断标志 0/1 |

**净值口径**：`equity = initial_capital + realized_pnl + Σ(持仓浮盈)`；
`track_positions` 与每次平仓时刷新 `peak_equity = max(peak, equity)`。

**新表 `paper_risk_events`（审计 + 企微推送底稿）**：

| 字段 | 说明 |
|---|---|
| id, time | 自增 / 事件时间 |
| event_type | industry_limit / risk_freeze / cooldown_start / daily_stop / unfreeze |
| code | 相关个股（可空） |
| message | 人话描述（推企微用同一文案） |

---

## Phase 划分与工作量

| Phase | 内容 | 依赖 |
|---|---|---|
| 1 | 数据层加列 + risk_events 表 + 净值/峰值维护（track 与平仓两处挂钩） | 无 |
| 2 | G1-G4 开仓 gate + 冷却/熔断状态机（cooldown 到期由 `paper_track_loop` 检查解除） | P1 |
| 3 | 波动率仓位（替代固定单仓；需 `get_kline` 拉 ATR） | P1 |
| 4 | `/api/paper/risk`（风控状态总览）+ 前端模拟盘页"风控状态"卡片 + 企微事件推送 | P2 |

## 可复用代码钩子

| 钩子 | 位置 |
|---|---|
| 个股→行业映射 | `sector_industry.get_stock_industry(code)` → `main_industry` |
| ATR 参考实现 | `backtest/market_regime._atr()`（14 日，纯 numpy） |
| 行情批量 | `tencent.get_stocks_batch` |
| 风控参数范本 | `signals/tracker.RISK_CONFIG`（实盘信号跟踪已在用同一套思路） |
| gate 模式 | `paper_trading` 现有资金/战法上限检查（照抄结构） |
| 事件推送 | `flash/wechat.push_markdown_batched(force=True)` |
| 冷却到期检查 | `paper_track_loop`（每 5 分钟已在跑） |

---

## 验收标准

1. 场景构造验证全过：同行业第 3 只被拒 / 连亏 3 笔次日 cooldown / 回撤触发冻结 / 当日亏损达限停开
2. 风控事件 100% 落 `paper_risk_events` + 同步企微
3. `paper_stats` 样本可标注"风控启用日期"，**对比验证（模拟盘 vs 回测）时区分风控前后样本**
4. 已成交笔的 pnl 计算零改动（不改 `_close_position` 的结算公式）

---

## 阈值取值依据与开放问题

| 阈值 | 取值 | 依据 |
|---|---|---|
| 单笔风险 0.8% | 8,000 元 | tracker 用 1.5%，模拟盘 20 仓满仓更保守取半 |
| 回撤熔断 5% | 5 万 | tracker `max_total_risk=6%` 同源略紧 |
| 同行业 2 只 | — | 组合分散常规；等 risk_events 攒数据后回调 |
| 连亏 3 笔冷却 1 日 | — | 战法失效期的最小可观测窗口 |

**开放问题**：
1. 行业映射每月重建一次，新股/未覆盖股行业缺失 → 建议**缺失视为独立行业不限制**，但记 risk_events 留痕（宁可漏杀不可错杀开仓机会，模拟盘目的是攒样本）
2. 阈值初期为专家经验值，`paper_risk_events` 攒一个月后用实际触发频率回调
3. 波动率仓位上线后，与回测等权口径的组合收益不可直接比 → 对比验证以胜率为主

## 建议落地顺序

**本周**：Phase 1 + 2（gate 是核心价值，半天工作量）
**下周**：Phase 3 波动率仓位 + Phase 4 可见化
**持续**：risk_events 攒 2-4 周 → 与平仓样本一起做"模拟盘 vs 回测"首次正式对比
