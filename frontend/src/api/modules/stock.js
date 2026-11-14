// ==============================================================================
// stock.js —— 由 api/index.js 按域拆分（2026-11-14 结构重组；index.js re-export 兼容）
// 本文件只做「请求定义」：参数进、响应出，无状态。业务状态在 composables/。
// ==============================================================================
import { http } from '../http'

export const getStockKline = (symbol, params) => http.get(`/stock/kline/${symbol}`, { params })

export const getStockRealtime = (symbol) => http.get(`/stock/realtime/${symbol}`)

export const getStockFundamental = (symbol) => http.get(`/stock/fundamental/${symbol}`)

export const getStockNews = (symbol) => http.get(`/stock/news/${symbol}`)

export const getStockNewsHistory = (symbol, days = 30) => http.get(`/stock/news/${symbol}/history`, { params: { days } })

export const getStockTechnical = (symbol, period = 'day') => http.get(`/stock/technical/${symbol}`, { params: { period } })

export const getStockFinance = (symbol, params) => http.get(`/stock/finance/${symbol}`, { params })

export const getStockFinanceHistory = (symbol, limit = 12) => http.get(`/stock/finance/${symbol}/history`, { params: { limit } })
// ★ 2026-10-08：单股异动「闸门」（**轻量、不调 LLM**）—— 页面加载即可调用，
//   用于主动标出"这只票此刻有异动"（用户诉求原话是"项目察觉不到"）。

export const getStockAnomalySignals = (symbol) => http.get(`/stock/anomaly-signals/${symbol}`)
// ★ 2026-10-08：单股异动 LLM 分析（**按需触发**）—— 每次最多 1 次 LLM 调用，
//   后端 30 分钟内复用缓存（命中则零调用）。同步 LLM ⇒ timeout 给足（同 getRadarAnalysis）。

export const getStockAnomalyAnalysis = (symbol, refresh = false) => http.get(`/stock/anomaly-analysis/${symbol}`, { params: { refresh }, timeout: 120000 })
// 批量财报（前端本地评分引擎算 top50 时用：1 次取回候选池财报，传给 scoreStock 算成长/质量维度）
// ★ 响应拦截器返回的是 AxiosResponse，这里统一解包成 .data，避免调用方拿到整个 response 对象

export const getFinanceBatch = (codes) => http.post('/stock/finance/batch', codes).then(r => r.data)

export const searchStock = (keyword) => http.get('/stock/search', { params: { keyword } })

// 评分引擎

export const postFeedback = (payload) =>
  http.post('/feedback', null, { params: payload, timeout: 10000 })
// ★ 持仓雷达（2026-09-23）：以**用户持仓为主轴**的状态聚合 —— 评分/主力阶段/闸门就绪/
//   战法命中/是否在观察池/所属板块被矛盾点名，一次回答「我手里那几只怎么样」。
//   ⚠️ 路径两段 `/score/batch/*`：后端 `/{symbol}` 单段通配路由会吞掉单段静态路径（9-22 实锤）。
//   ⚠️ 后端冷启动首次约半分钟 ⇒ 可能先返回 `warming: true` 占位（服务端有后台预热 loop，
//      正常秒开）；前端据此显示「正在准备」，**不要当成错误**。

export const getAnomalies = (watchCodes) => http.get('/stock/anomalies', { params: { watch_codes: watchCodes } })

// 资金流向

export const getPositionLines = (code) => http.get(`/stock/${code}/position-lines`, { timeout: 15000 })
