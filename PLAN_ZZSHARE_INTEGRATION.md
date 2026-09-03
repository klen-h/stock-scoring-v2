# zzshare 数据源接入评估与整合计划（PLAN_ZZSHARE_INTEGRATION）

> 触发：2026-09-03。README 声称 40+ 接口（行情/涨停/龙虎榜/情绪/板块/财务）。
> 实测（zzshare 0.4.11，匿名）：**财务 5 表 + 涨停复盘 + 板块/情绪/龙虎榜可用；资金流/实时接口（stock_moneyflow/rt_k/market_mf）此版本实际缺失**。
> 原则：zzshare 只作**低频补充数据源**（匿名限流/慢、10s 超时自动重试），不替代现有腾讯/东财主链路。
> 创建：2026-09-03

---

## 一、现有项目缺口 × zzshare 能力对照

| # | 现有项目缺口（对应历史 plan） | zzshare 能力 | 接口 | 优先级 |
|---|---|---|---|---|
| 1 | 质量维度只有 ROE/负债率/毛利率 3 指标；无**现金流质量/扣非/商誉**（PLAN_REGIME_BEARISH 阶段2「经营现金流」） | ✅ 完整现金流量表/资产负债表/指标表 | `finance_cash_flow` / `finance_balance` / `finance_indicator` | **P0（十分需要）** |
| 2 | 基本面估值 PE/PB 仅实时值，无**历史日频估值序列**（行业分位/估值因子回测缺数据） | ✅ 日频估值表（PE/PB/PS/PCF/市值/换手）2005 至今 | `finance_valuation` + `finance_pit`（无前视） | **P1** |
| 3 | 涨停只统计家数（market breadth），无**连板高度/涨停原因/梯队**；战法"涨停回马枪"等缺题材归因 | ✅ 涨停复盘 | `uplimit_stocks` / `uplimit_hot` / `review_uplimit_reason` / `uplimit_trend` | **P1** |
| 4 | 板块分析数据只有东财行业映射；缺**题材热度人气榜**交叉验证主线 | ✅ 同花顺热度 Top / 板块排名 | `ths_hot_top` / `plates_rank` / `market_plate_stocks` | P2 |
| 5 | regime/情绪只有宽度自算 | ✅ 情绪指标/涨跌分布 | `market_sentiment` / `updown_distribution` / `sentiment_trend` | P2 |
| 6 | 龙虎榜/异动（持仓撤退提醒上下文） | ✅ 龙虎榜/异动 | `lhb_list` / `movement_alerts` | P2 |
| 7 | 交易日历依赖本地推断 | ✅ 交易日历 | `trade_days` | 已接入方向（低） |
| — | **资金面无真实资金流**（北向空壳） | ❌ **0.4.11 无此接口**（README 超前） | `stock_moneyflow`/`market_mf` | 暂不可用，升级包后复查 |

**P0 判断依据**：质量权重在 defensive 档 35% / neutral 档 11%+；而暴雷（商誉减值/现金流断裂）恰好是"绿盘高分仍在榜"的隐藏雷。加净现比/扣非后质量维度才接近"不踩雷"本意，且**不改变权重的增量（维度内加子指标）需要回测验证** → 先入库 + 因子分析，再决定并入打分。

---

## 二、阶段 0（已做）：接口可用性实测

- `trade_days` / `finance_latest(indicator|valuation)` 匿名可用（有 10s 超时自动重试，慢）；
- `finance_latest.indicator` 返回 18 列：roe/gross_profit_margin/net_profit_margin/扣非/营收环比/净利同比…；
- `finance_latest.valuation` 返回 12 列：pe/pb/ps/pcf_ratio/market_cap/trade_date/turnover_ratio…（pcf 为负=现金流失血）；
- `stock_moneyflow`/`rt_k`/`market_mf` **AttributeError 不存在**（pyi/README 声明超前于实现）→ 不接；
- 匿名限流+慢，token 可通过环境变量 `ZZSHARE_TOKEN`（官网免费）提升频率。

---

## 三、阶段 1（本次已实现代码）：现金流/财报增量入库 + 因子分析脚本

改动文件（低风险、不触碰评分主链）：
1. `backend/app/zzshare_client.py`：DataApi 懒加载封装（token 从 env `ZZSHARE_TOKEN`，缺省匿名）；
2. `backend/app/zzshare_finance.py`：表 `stock_finance_zz`（code 主键）初始化 + 批量同步最新 indicator/balance/cash_flow → 落库；
3. `scripts/zz_finance_sync.py`：CLI 同步（`--limit` 小样本验证 / 默认全池分批），只写不读评分；
4. `scripts/zz_factor_analysis.py`：从库算「净现比 OCF/净利润」「毛利率/净利率/扣非占比」「商誉/净资产」「有息负债率」等质量扩展因子，复用 ranking_history 快照验证（参考 factor_analysis.py 方法论）→ 出 md 报告。

**验收门槛**（决定是否并入质量维度）：
- 现金流类因子在大跌段（9/1~9/2 快照）对未来 2/5 日收益有区分（分桶单调或 IC>0.1）；
- 与既有质量分组合后相关性 < 0.7（不是重复测量）；
- 覆盖率 ≥ 80%（财报齐全股票）。

**明确不做**：本期不改 engine 质量子指标/权重/排行（等因子报告再定，防无回测上线）。

---

## 四、阶段 2（后续，先出报告再决定）
- 质量维度加「净现比」等子指标（改 `_score_quality`，需 9/4/9/9 回测背书）；
- 涨停原因/连板高度 → 战法复盘增强（涨停回马枪题材归因）与 regime 情绪参考；
- 行业分位估值回测（用 valuation 历史序列把 PE/PB 绝对阈值升级为行业内分位——PLAN_REGIME_BEARISH 阶段 1.3 的待办数据）；
- 龙虎榜/异动 → 撤退提醒上下文。

---

## 五、风险与边界

| 风险 | 应对 |
|---|---|
| zzshare 接口不稳定（10s 超时/限流） | 只作低频补充；同步放 23:00 后（BACKTEST 回填窗口外）；失败重试+跳过不阻塞主流程 |
| 匿名 token 频率低 | env `ZZSHARE_TOKEN` 可选提升；默认每只股票低频 |
| 财报口径与现有 finance.py（东财）不一致 | stock_finance_zz 独立存储；入库时保留 pubDate 可追溯 |
| 改打分导致排行漂移 | 阶段 1 不碰 engine；并入前必须有因子报告 + 回测 |
| 第三方库升级后接口名变化 | adapter 单点隔离，升级只动 zzshare_client.py |
