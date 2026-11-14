// ==============================================================================
// flash.js —— 由 api/index.js 按域拆分（2026-11-14 结构重组；index.js re-export 兼容）
// 本文件只做「请求定义」：参数进、响应出，无状态。业务状态在 composables/。
// ==============================================================================
import { http } from '../http'

export const getFlashEvents = (params) => http.get('/flash/events', { params })
// ★ 2026-11-07：快讯检索（分类 facet + 关键词搜索；语料=flash_news + jin10_tidy_news）。
//   只读零 LLM；category 词表复用 policy_tone_watch 冻结判据（单一事实源）。
//   ⚠️ 人工按需调用，禁止挂轮询（后端一次最坏 ~1.5MB DB egress）。

export const searchFlash = (params) => http.get('/flash/search', { params, timeout: 30000 })
// cluster 口径重算（幂等；EVENT_CLUSTERS 改词后拉齐历史用，返回迁移统计）

export const reclassifyFlash = (params) => http.post('/flash/reclassify', {}, { params, timeout: 120000 })

export const getFlashDiagnosis = (params) => http.get('/flash/diagnosis', { params })

export const getFlashReview = (phase) => http.get(`/flash/review/${phase}`)

export const getFlashReviewHistory = (phase, limit = 20) => http.get(`/flash/review/${phase}/history`, { params: { limit } })
// ★★ 2026-09-30（用户："盘前午盘盘后的 llm 分析失败，可以加一个手动重试"）：
//   三段复盘（盘前 premarket / 午盘 lunchbreak / 盘后 postmarket）的**手动重跑**。
//   后端 `POST /flash/review/{phase}/run` **一直就有**（注释自陈"测试/补跑用"，且成功后
//   `mark_schedule_done` ⇒ 调度器不会再跑一遍 ⇒ **不会 LLM 双烧**）；此前**缺的只是前端入口**
//   ⇒ 复盘一旦失败/错过窗口，用户只能干等下一个交易日。
//   ⚠️ 这是一次**同步 LLM 调用**（十几秒~1 分钟）⇒ 超时给足 120s，调用方必须 loading + 防连点。

export const runFlashReview = (phase) =>
  http.post(`/flash/review/${phase}/run`, {}, { timeout: 120000 })

export const getFlashSignals = () => http.get('/flash/signals')

export const triggerFlashIngest = () => http.post('/flash/ingest')

export const getFlashStatus = () => http.get('/flash/status')
// ★ 2026-10-24：快讯解读卡（大盘/板块/个股 + 重要快讯）
//   GET 只读库/快照（不烧 LLM）；POST 才真解读（一次批量 LLM，30 分钟内重复点读缓存）

export const getFlashBrief = (date) => http.get('/flash/brief', { params: { date }, timeout: 30000 })
// ★ 2026-10-26（方案 B：一天多次解读 → tab 回看）：取**某一次历史解读的全文**。
//   列表只随 /flash/brief 返回 meta（≈1KB），点开 tab 才付全文成本（4~6KB）。

export const getFlashBriefRun = (runId) =>
  http.get('/flash/brief', { params: { run_id: runId }, timeout: 20000 })

export const refreshFlashBrief = (date) =>
  http.post('/flash/brief/refresh', {}, { params: { date }, timeout: 120000 })

export const getFlashNotifications = (params) => http.get('/flash/notifications', { params })

export const getFlashAudit = () => http.get('/flash/audit')

// 财经日历（金十：经济指标 / 事件讲话 / 交易所休市，后端每日 07:00 自动刷新缓存）

export const getCalendar = (params) => http.get('/flash/calendar', { params })

export const refreshCalendar = (daysAhead = 14) => http.post('/flash/calendar/refresh', {}, { params: { days_ahead: daysAhead } })

// 历史回测（引擎计算，后端 10 分钟缓存；冷启动需从库拉几百只日线，放宽超时）

export const getPushLog = (date, limit = 80) => http.get('/flash/push-log', { params: { date, limit }, timeout: 20000 })
// ★ 系统自洽性审查（2026-09-23）：把今日全部系统提示交给 LLM，检查**系统自身**的判断
//   是否互相矛盾/前后反复（对照：`contradictions` 扫的是**市场数据**层面的矛盾）。
//   后端按日缓存 ⇒ 同日再调零成本；refresh=true 强制重算（消耗一次 LLM 调用）。

export const getRadarAnalysis = (date, refresh = false) => http.get('/flash/radar-analysis', { params: { date, refresh }, timeout: 120000 })

export const getCalendarAhead = (days = 3) => http.get('/flash/calendar-ahead', { params: { days } })
// ★ 2026-09-29：两市成交额环比（U 型曲线法）。⚠️ `reliable=false`（14:00 前）时
//   前端只显示累计，**不得**给缩量/放量结论（早盘预估中位误差 16%）。
