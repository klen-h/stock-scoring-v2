# 项目上下文摘要

> 最后更新：2026-08-25

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

### 2. 前端/后端评分微小差异
- **原因**：历史长度（150 vs 500 天）、股票池覆盖范围不同
- **影响**：可接受，不影响使用

### 3. Render 算力瓶颈（规划中）
- **问题**：盘中指标刷新、全量评分对 0.1 CPU 压力大
- **方案**：将每日指标刷新迁移到 GitHub Actions（尚未实施）
- **收益**：Render 盘中只做轻量查表+评分

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
