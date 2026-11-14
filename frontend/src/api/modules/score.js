// ==============================================================================
// score.js —— 由 api/index.js 按域拆分（2026-11-14 结构重组；index.js re-export 兼容）
// 本文件只做「请求定义」：参数进、响应出，无状态。业务状态在 composables/。
// ==============================================================================
import { http } from '../http'

export const getSectorStrength = (date) => http.get('/score/batch/sector-strength', { params: { date } })

// 宏观数据（全球+国内面板 + 规则方向分）

export const getBacktestStrategy = (name) => http.get('/backtest/strategy', { params: { name }, timeout: 120000 })

// 周度回测报告归档（scheduler 每周五 16:00 生成的 markdown 文件）

export const getBacktestReports = () => http.get('/backtest/reports')

export const getBacktestReportContent = (name) => http.get('/backtest/reports/content', { params: { name } })

// A股大盘日报（scheduler 每日 16:20 生成，落库 daily_reports）

export const getStockScore = (symbol) => http.get(`/score/${symbol}`)
// ★ 2026-11-01：评分**附加上下文**（精算榜定位 / 龙虎榜 / 防御属性）。
//   本地评分（useFrontendScoring.computeLocalScore）已覆盖 total_score/dimensions/
//   factors/summary/mainforce/trend_health/gate，只缺这几个纯点查字段。
//   后端带 30 分钟进程缓存 ⇒ 实测 **~14ms**（而完整 `/score/{symbol}` 约 1.7s）。

export const getScoreContext = (symbol, name = '') =>
  http.get(`/score/${symbol}/context`, { params: name ? { name } : {} })

export const getScoreTop = (params) => http.get('/score/batch/top', { params, timeout: 90000 })
// 旁路衰减灰度对比（生产 vs 公告后衰减 两套 top N，日批快照，非实时）

export const getShadowRank = (limit = 50) => http.get('/score/batch/shadow-rank', { params: { limit }, timeout: 15000 })
// ★ 2026-10-25（用户："阴跌防御影子榜独立标签页 + 工作台防御 Top10"）：
//   H2' 旁路影子榜（`shadow_rank_daily.variant='defensive'`，日批落库，**不改生产排序**）。
//   只读快照、零重算 ⇒ 轻请求；无数据时后端返回 `ok=false` + note（前端如实说明）。
//   `live=true` ⇒ 盘中实时预览（用当前精算榜代入同一冻结公式重排；官方榜仍为日批快照）。
//   ⚠️ live 首次会触发全市场精算（~1 分钟，后端 3 分钟缓存）⇒ 超时给足。

export const getShadowDefensive = (limit = 50, live = false) => http.get('/score/batch/shadow-defensive', { params: { limit, live }, timeout: live ? 120000 : 20000 })
// ★ 2026-10-25（用户选"前端本地引擎算组内 100 只"）：宽防御组**名单**（日批小表 ≈15KB）。
//   前端拿名单后在浏览器逐只精算 ⇒ 后端零精算 egress；官方影子榜仍走 shadow-defensive。

export const getDefensiveGroup = (day) => http.get('/score/batch/defensive-group', { params: { day }, timeout: 20000 })
// ★ 2026-10-26（审查 P2）：选股工作台·事实宽表。走统一 http（带鉴权/统一错误拦截），
//   不要在各页面自建 axios 实例（否则加鉴权后立刻 401，且错误处理各写一套）。

export const getGateWatch = (limit = 80) => http.get('/score/batch/gate-watch', { params: { limit }, timeout: 60000 })
// ★ 2026-10-11（P1）个股「一页纸诊断」：既有结构化事实的聚合表述（**不是投资建议**）。
//   ⚠️ 路径是**两段** `/score/diag/{symbol}`：后端 `/{symbol}` 单段通配路由会吞掉单段静态路径
//   （2026-09-22 实锤，同上）。ai=false 只要事实（零 LLM 成本）；ai=true 追加 AI 解读。

export const getStockDiag = (symbol, ai = false) =>
  http.get(`/score/diag/${symbol}`, { params: { ai }, timeout: 45000 })
// ★ 2026-10-11（P2）个股「事实问答」：对**当前标的**提问，只答既有事实范围内的问题。
//   ⚠️ 用 **POST**：问题文本不进 URL/访问日志（隐私）；且 POST 不会被后端 `/{symbol}` 单段通配吞掉。

export const askStock = (code, q, ai = true) =>
  http.post('/score/ask', { code, q, ai }, { timeout: 60000 })
// ★ 2026-10-11（C2，PLAN_NEXT 附 B）输出消费标记：这条输出对我有没有影响。
//   只记 source/ref/date（**不存内容**）；同一 (source, ref) 幂等覆盖（可改选）。

export const getPortfolioRadar = () => http.get('/score/batch/portfolio-radar', { timeout: 45000 })
// ★ 2026-10-18：市场底确认清单（**纯观察器，非信号**；口径见后端 app/policy_bottom_watch.py）
//   只有第①条（国家队公告）可人工打勾 —— 无结构化数据源，打勾权在人。

export const getScoreBottom = (params) => http.get('/score/batch/bottom', { params, timeout: 60000 })

export const getScoreBySignal = (params) => http.get('/score/batch/signal', { params, timeout: 60000 })

export const getBatchPrices = (codes) => http.get('/score/batch-prices', { params: { codes: codes.join(',') } })
// ★ 2026-10-26（用户："盘中实时刷新涨跌幅会加大流量吗"→"可以"）：
//   **只读后端行情内存缓存**的批量报价（`cached=1`）——调度器每 ~120s 已刷全市场，
//   这里 **上游请求 = 0**，只花本机 CPU；20 只响应 ≈1.6KB（gzip 0.57KB）。
//   返回 `{ok, cached, rows:[{code,name,price,change_pct}], as_of, data_ts,
//          data_date, from_snapshot, age_sec, n}` —— 结构是**对象**（老接口是数组），
//   因为必须把「行情时刻 / 是否收盘快照」一起带上，否则休市日会把上一交易日
//   收盘价当"今日现价"渲染（本项目踩过快照事故）。

export const getBatchPricesCached = (codes) =>
  http.get('/score/batch-prices', { params: { codes: codes.join(','), cached: 1 }, timeout: 10000 })
// ★ 板块归属（细分主行业 + 层级链）—— 榜单三 tab 共用一份，前端页面级缓存，按需拉取

export const getBatchIndustry = (codes) => http.get('/score/batch/industry-map', { params: { codes: codes.join(',') }, timeout: 15000 })
// ★ 缩量启动形态扫描 / 观察（2026-10-15）：候选清单 + 观察追踪，描述性观察，非信号

export const getPatternScan = () => http.get('/pattern/scan', { timeout: 30000 })

export const getPatternWatch = () => http.get('/pattern/watch', { timeout: 15000 })

export const addPatternWatch = (code, note = '') => http.post('/pattern/watch', { code, note })

export const updatePatternWatch = (code, payload) => http.patch(`/pattern/watch/${code}`, payload)

export const deletePatternWatch = (code) => http.delete(`/pattern/watch/${code}`)

export const getPatternConfig = () => http.get('/pattern/config', { timeout: 15000 })

export const putPatternConfig = (cfg) => http.put('/pattern/config', cfg)

export const getPatternHighlightCodes = () => http.get('/pattern/highlight-codes', { timeout: 15000 })

export const getBacktest = (params) => http.get('/score/backtest', { params, timeout: 120000 })
// 单股历史评分 vs 价格（评分有效性个股级验证，详情页折线图）

export const getRankHistory = (code, days = 30) => http.get(`/score/rank-history/${code}`, { params: { days } })
// 评分分桶 × 持有期胜率统计（全局验证"评分越高，未来收益越好吗"）

export const getBucketStats = (days = 120) => http.get('/score/bucket-stats', { params: { days }, timeout: 60000 })

// 权重优化分析：不传 snapshots 时后端自动从每日快照库读取已验证记录（统一后推荐用法）

export const getWeightAdvice = (snapshots = null) =>
  http.post('/score/weight-advice', snapshots ? { snapshots } : {}, { timeout: 30000 })
// 当前引擎生效权重 + 市场状态（前端本地评分需同步，避免两套口径漂移）

export const getScoreWeights = () => http.get('/score/weights', { timeout: 10000 })
// 评分排行快照：后端每日自动落库（含维度分+价格），前端直接读取

export const getSnapshots = (days = 30) => http.get('/score/snapshots', { params: { days } })
// 手动立即记录当日快照（与调度器每日任务同源，同日幂等覆盖）

export const captureScoreSnapshot = () => http.post('/score/snapshots/capture', {}, { timeout: 120000 })

export const getRankingPersistence = (codes) => http.post('/score/ranking-persistence', codes)

export const recordRanking = (stocks) => http.post('/score/ranking-record', stocks)

export const getKlineCacheStatus = () => http.get('/score/kline-cache/status')

export const refreshKlineCache = () => http.post('/score/kline-cache/refresh', {}, { timeout: 10000 })

export const getIndicatorCacheStatus = () => http.get('/score/indicator-cache/status')

export const refreshIndicatorCache = () => http.post('/score/indicator-cache/refresh', {}, { timeout: 10000 })

export const incrementalIndicatorUpdate = (code, price, high, low) => http.post('/score/indicator-cache/incremental', { code, price, high, low })

// 异动监控
