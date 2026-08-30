# 项目上下文摘要

> 最后更新：2026-08-31

## 项目概况

- **仓库**：`stock-scoring-v2`
- **架构**：Python FastAPI 后端（部署于 Render，0.1 CPU）+ Vue 3 前端（Vite + Tailwind）
- **数据库**：PostgreSQL（Render 托管）
- **数据源**：腾讯 `qt.gtimg.cn` 实时行情 + 东财快讯/搜索接口
- **K 线数据包**：GitHub Actions 每日 16:00 生成，发布到 GitHub Pages，前端 IndexedDB 消费

---

## 本轮会话完成的工作

### 1. 复盘未触发修复
**文件**：`backend/app/flash/scheduler.py`

- **Bug**：`review_loop` 无论 `run_review` 成功/失败都 `mark_schedule_done`，导致 LLM 超时后当天不再重试
- **修复**：只有 `result` 无 `error` 时才标记完成；失败时推企微告警，窗口内允许重试

### 2. 前端 K 线拼接今日实时数据
**文件**：`frontend/src/composables/useFrontendScoring.js`

- 新增 `appendTodayBar(klines, stock)` 函数：把腾讯快照的今日 OHLCV 拼为最后一根 K 线
- `preciseScoreBatch` 和 `preciseScoreBatchMainThread` 在发给 Worker 前调用此函数
- **效果**：前端本地模式盘中也能产出接近后端的评分

### 3. 数据包全量更新 + 自动同步
**文件**：
- `scripts/generate-kline-pack.py`：`--top` 默认改为 0（不限制），包含所有通过质量门槛的股票
- `.github/workflows/kline-data.yml`：移除 `top` 输入参数
- `frontend/src/composables/useFrontendScoring.js`：
  - `initFrontendScoring()` 检测 `lastUpdateDate !== today` 时后台静默调用 `silentUpdate()`
  - 新增 `silentUpdate()` 函数（不阻塞 UI）
  - `downloadKlineData` 默认 URL 常量化为 `KLINE_DATA_BASE_URL`

### 4. 前端"更新数据"按钮
**文件**：`frontend/src/views/ScoreRank.vue`

- 数据就绪后显示"更新数据"按钮（原来首次下载后无法再手动更新）
- URL 统一由 composable 管理，`handleDownloadKlineData` 不再硬编码

### 5. 快照自动保存修复
**文件**：`frontend/src/views/ScoreRank.vue`

- **Bug**：`autoSaveCheck` 有 `if (!tableData.value.length) return`，但 `captureSnapshot` 自己拉数据不依赖 `tableData`
- **修复**：删掉该检查，15:10 后自动保存不再依赖页面是否已加载排行

### 6. P3：regime 与战法准入（对应"高波动常态"）
**文件**：`backend/app/backtest/strategies.py`、`backend/app/backtest/run.py`、`backend/app/strategies/market_regime.py`、`backend/app/strategies/router.py`、`frontend/src/views/Strategies.vue`

- **① 分层回测**：新增 `backtest_warfare_by_regime()`（`--strategy regime_warfare`），对 `strategy_results` 信号打市场状态标签（进攻/震荡/防御 × 高/正常/低波动），按"战法 × 状态/波动"分组统计胜率、盈亏比 → 战法准入的数据依据
- **② 统一 regime 口径**：战法侧 `detect_market_regime()` 从"腾讯实时 ADX+布林带 → trending/oscillating/transition"改为复用 `backtest/market_regime`（本地 3 年沪深300：MA60+ADX+ATR 分位 → 进攻/震荡/防御 + 波动率），与回测、评分动态权重完全同源
- **③ 准入落地**：新增 `is_strategy_admitted()`（`ADMISSION_MATRIX` 专家规则初始版：进攻→趋势类、震荡→震荡类、防御→全禁，高波动震荡只放行低吸/反转类）；`_do_scan` 与 `/scan` 接口加 gate，非准入战法禁止扫描并返回明确原因；前端 `Strategies.vue` 显示未准入提示

### 7. 战法买入信号企微推送
**文件**：`backend/app/strategies/recommendation.py`（新增）、`backend/app/flash/wechat.py`、`backend/app/flash/scheduler.py`

- **白名单推送**：只推「样本≥30 且 胜率≥55%」的战法（`PUSH_STRATEGY_WHITELIST`，当前=单阳不破 60.7%/56样本），避免低胜率战法噪音
- **消息含买入逻辑**：形态链（放量大阳→缩量整理→放量突破）+ 目标价推导依据 + 止损破位依据 + 历史胜率背书 + 当前市场匹配，讲清"为什么买 / 目标怎么来 / 跌破哪里走"
- **推送时机**：盘后扫描（15:40 后）落库后自动推送；置信度 high/medium 过滤 + 每战法限量 8 条，避免刷屏
- **开关**：不受 `WECHAT_BUSINESS_ALERTS` 限制（核心交易通知），配了 `WECHAT_WEBHOOK` 即推

### 8. 次日开盘买点确认
**文件**：`backend/app/strategies/recommendation.py`、`backend/app/flash/scheduler.py`

- 交易日 **09:35-11:20** 窗口对白名单战法最近一次扫描信号做「买点确认」（幂等，当天一次，错过窗口可补跑）
- 实时开盘价 vs 参考介入价 vs 止损位 → **五档指引**：高开>3% 放弃/减仓 → 正常买点 → 低吸买点 → 回踩企稳再买 → 破位放弃
- **量价验证（防诱多）**：量比 = 实时量 / (昨日量 × 已交易分钟/240)，≥1.5 放量 / <0.6 缩量；高开缩量→⚠️谨慎追高（防诱多）、低开缩量→先观望防阴跌、放量承接→低吸更优
- 解决"介入价=收盘价无法成交"的矛盾：次日开盘按实际价执行，参考价仅作锚点，止损以形态位为准

### 9. 详情分 == 排行分（评分口径统一）
**文件**：`frontend/src/composables/useFrontendScoring.js`、`frontend/src/views/StockDetail.vue`

- 详情页综合分改用前端引擎 `scoreSingleStock`（与排行榜同源：klineDB 150 天 + 今日实时快照 + `scoreStock`），解决"详情 68.7 vs 排行 72.5"的分差
- 前端 `scoreStock` 的 `dimensions` 对象转数组适配详情页模板；`trend_health`/`buy_point` 等详情专属信息仍由后端提供

### 10. 战法详情 K 线实时拉取
**文件**：`frontend/src/views/Strategies.vue`

- 详情弹窗"最近5日K线"从"扫描落库快照"改为实时调 `getStrategyDetail` 刷新（扫描时若遇腾讯 WAF 冷却会存到旧 K 线快照，导致详情显示 08-26/08-27 而非 08-28）

### 11. 消息面情绪修复（去重/错股/回购情境化）
**文件**：`backend/app/eastmoney_news.py`、`backend/app/news_sentiment.py`

- 搜索源严格关联：只保留标题含代码/公司名的文章（杜绝"保荐机构被点名"的弱相关新闻误挂到本股）
- 相似事件去重 `_dedup_similar`：72h 窗口内标题相似度≥0.55 的重复报道只保留最新（"XX拟回购"08-04/08-05 不再重复 +2）
- 明细按时间倒序（原按情绪强度倒序）
- **回购情境化**：标题含"员工持股/股权激励"→ 激励型回购 +1（股东回报有限）；注销/减资 → 保持 +2（真正利好股东）

### 12. 排行榜消息分标注
**文件**：`backend/app/routers/scoring.py`、`frontend/src/views/ScoreRank.vue`

- `/score/batch/top` 用快讯源批量算消息分（1 次请求，90s 缓存），结果加 `news_score` 字段（不参与综合分）
- 前端 Top 榜单加"消息"列（位于可信度之后），绿/红/灰徽章；前端本地模式无此数据时显示"-"

### 13. 财报批量接口缓存优化
**文件**：`backend/app/finance.py`

- `get_finance_batch` 缓存命中改为**按 code 粒度**（命中直接返回、只补查未命中的），不再"整批有一个未命中就全量重查"
- TTL 30 分钟 → 24 小时（财报季度更新，靠 `refresh` 后 `clear_finance_cache` 立即失效）
- 实测：全命中 0.000s、部分命中只补查新增（原整批重查每次 4-5s）

### 14. 战法扫描未准入完全跳过
**文件**：`backend/app/flash/scheduler.py`

- `scan_all_strategies` 对未准入战法完全跳过（不调 `_do_scan`、不写空结果、单独计 `skipped`），与手动 `/scan` 的准入 gate 一致

---

## 关键文件清单

| 文件 | 作用 |
|---|---|
| `backend/app/flash/scheduler.py` | 调度器（复盘/快讯/行情/快照循环） |
| `backend/app/flash/service.py` | 复盘执行逻辑（`run_review`） |
| `backend/app/flash/llm.py` | LLM 客户端（DeepSeek-R1，600s 超时） |
| `frontend/src/composables/useFrontendScoring.js` | 前端评分引擎（IndexedDB + Worker + 腾讯 API） |
| `frontend/src/utils/scoringEngine.js` | 前端评分算法（与后端对齐） |
| `frontend/src/utils/klineDB.js` | IndexedDB 存储层 + `checkAndUpdate` 增量更新 |
| `frontend/src/views/ScoreRank.vue` | 评分排行页（含快照/前端模式切换） |
| `scripts/generate-kline-pack.py` | GitHub Actions 数据包生成脚本 |
| `.github/workflows/kline-data.yml` | K 线数据包每日生成 workflow |

---

## 已知问题 / 待办

### 1. LLM 复盘超时
- **现象**：DeepSeek-R1 推理模型响应极慢（600s timeout），Render 0.1 CPU 上容易超时
- **当前缓解**：修复后允许窗口内重试
- **根因**：LLM 性能瓶颈，可能需要换更快模型或优化 prompt

### 2. 前端/后端评分差异（已大幅缓解）
- **现状**：详情页/排行榜已统一到前端引擎（同源 K 线 + 同套算法），差异消除
- **剩余**：自选页（Watchlist）/组合页（Portfolio）仍走后端 `score_single`，待统一到同一前端引擎

### 3. Render 算力瓶颈（规划中）
- **问题**：盘中指标刷新、全量评分对 0.1 CPU 压力大
- **方案**：将每日指标刷新迁移到 GitHub Actions（尚未实施）
- **收益**：Render 盘中只做轻量查表+评分

### 4. 战法 × regime 分层回测样本不足（P3 后续调优）
- `strategy_results` 仅有调度器接入以来的几天信号（全落在震荡+正常波动），暂无高波动/进攻/防御样本
- **影响**：`ADMISSION_MATRIX` 目前是专家规则初始版，需分层数据支撑后调优
- **方案**：盘后全量扫描持续积累（每日 ~113 信号），待覆盖高波动期后重跑 `python -m app.backtest.run --strategy regime_warfare` 校准准入清单

### 5. 消息分回测验证（阶段 3，规划中）
- **现状**：消息分独立展示（详情页 + 排行榜标注），不参与综合总分
- **待验证**：用 `news_history` 每日快照验证「消息分 vs 未来收益」相关性
- **后续**：验证有效后再设计纳入综合分的权重（第 6 维度或加减项）

---

## 代码规范备忘

- 用户自行 `git push`，不要代推
- 前端轮询策略：交易时段自动刷新，非交易时段停止
- 消息分暂不纳入综合总分
- 腾讯 API 字段索引前后端必须严格对齐
- 前端用户偏好设置需持久化到 localStorage
- 非交易时段停止实时接口轮询

---

## 架构决策记录

### 前端本地模式 vs 后端模式
- **前端本地模式**：IndexedDB 数据包 + Web Worker 计算指标 + 腾讯实时快照拼接今日 K 线
- **后端模式**：Render 服务器计算（0.1 CPU，盘中可能慢）
- **设计目标**：前端成为评分计算的唯一真理来源，Render 只处理非计算任务

### K 线数据包策略
- GitHub Actions 每日 16:00 生成（全量股票，无 top 限制）
- 前端自动检测过期并后台静默更新
- 支持手动"更新数据"按钮

### 消息面评分
- 东财快讯 + 个股搜索双源合并
- 消息分暂不纳入综合总分
- 每日 15:20 快照持久化到 `news_history` 表
