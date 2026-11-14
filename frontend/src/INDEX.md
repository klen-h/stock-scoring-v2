# frontend/src 结构地图（LLM 与新人导航 · 2026-11-14）

> 目的：让 LLM/新人在 30 秒内知道「改什么去哪个文件」。**本文件随结构变化同步更新**。
> 依赖方向（只许单向）：`views → components → composables → api → 后端`。utils/ 是纯函数层，被任何层引用，不反向依赖。

## 目录职责

| 目录 | 放什么 | 不放什么 |
|---|---|---|
| `api/` | **请求定义**（参数进响应出，无状态）。`http.js`=唯一 axios 实例+拦截器+token；`modules/<域>.js`=按后端域分文件（auth/flash/market/score/stock/portfolio/system/misc）；`index.js`=**纯 re-export 兼容层** | 业务状态、业务逻辑 |
| `composables/` | **有状态**的 `use*.js`（复用逻辑+响应式状态）；`displayMeta.js`/`stockUtils.js`/`markdown.js` 是无状态共享 helper（历史位置，勿新增同类） | 请求定义（→api/） |
| `utils/` | 纯函数引擎：`scoringEngine.js`（前端评分，与后端对齐）、`klineDB.js`（IndexedDB）、`trendHealth.js`、`predictionEngine.js` | DOM/网络依赖 |
| `components/` | 复用组件：`common/`（Loading/PriceTag/InsightBody 等跨页通用）、`workbench/`（工作台卡×9）、`detail/`（详情页拆出，规划中）、`rank/`（榜单拆出，规划中） | 单页专用逻辑（在页内或 composables） |
| `views/` | 页面级组件（router 一对一）。**Workbench.vue 7133 行是最大技术债**——拆分计划见下 | — |

## 约定（写新代码前必读）

1. **新 API 请求**：写进 `api/modules/<对应域>.js`（按后端 URL 前缀选域），`import { http } from '../http'`。**禁止**往 `api/index.js` 加（它只许 re-export）。
2. **新的可复用逻辑**：有状态 → `composables/use<名>.js`；纯函数 → `utils/<名>.js`。
3. **模板新增绑定**：必须 grep script 侧声明（未声明变量 build 不报错、运行时才炸）。
4. **观点输出**：`md-body`+`v-html=renderMd` 或 `InsightBody`；绝不 `whitespace-pre-wrap` 裸文本（markdown.js 全站唯一）。
5. **轮询**：交易时段才轮（`isTradingTime()` 门控）；新刷新项进 `startPolling()` 组。
6. **检验徽章**：任何展示"信号/评分"的卡片，徽章口径来自后端 SIGNAL_STATUS 下发，前端不自推导。

## 大文件拆分计划（LLM 修改的安全底线）

| 文件 | 行数 | 状态 |
|---|---|---|
| `views/Workbench.vue` | **6837**（-296，拆分进行中） | ✅ 已拆：lidx→`useLidaxiao.js`、防御组→`useDefensiveBoard.js`（DI 注入 useFrontendScoring 三成员）、观察器AI解读→`useObsAIReview.js`（DI selectedDate）；卡已组件化 `components/workbench/`×12。下一候选：scen/events 簇——**两处前置已探明**：①refs/helpers 散落三处（5512-5531 scen refs+badge/leanDot/scenVal；3933 eventSignal；5578-5600 eventTitle computed），需先收拢到连续块再切；②loadScenario 内部重置已抽出的 obsAI 状态（跨簇依赖）⇒ composable 需 DI 注入 obsAI/obsAIOpen/loadObsAI（同防御组模式）。**拆分规程：按函数边界 grep 起止（`^function X`→下一 `^function`），禁按行号硬切；解构同名 ⇒ 模板零改动；每步 build 验证** |
| `views/ScoreRank.vue` | 2909 | 未拆；tab 结构与 Workbench 同型，可复制其拆法 |
| `views/StockDetail.vue` | 1420 | tab 已独立（score/kline/rank/fund）；诊断区可拆 `components/detail/` |
| `api/index.js` | 720→**29** | ✅ 2026-11-14 已拆：8 域模块 + http.js + re-export shim（234 导出全部保留，对账通过） |

## 常见任务 → 去哪里改

| 任务 | 文件 |
|---|---|
| 加一个后端请求 | `api/modules/<域>.js` |
| 新增工作台卡 | `components/workbench/<名>Card.vue` + Workbench 注册挂载 |
| 改快讯解读逻辑 | 后端 `flash_brief.py`；前端卡 `components/workbench/FlashBriefCard.vue`、三时段卡 `FlashPhaseCard.vue` |
| 改评分算法 | 前端 `utils/scoringEngine.js`（与后端 `app/scoring/engine.py` 对齐，改任一侧必须跑对齐校验） |
| 改颜色/徽章/文案口径 | `composables/displayMeta.js`（唯一共享源） |
