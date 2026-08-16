import axios from 'axios'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
})

// 市场行情
export const getMarketOverview = () => http.get('/market/overview')
export const getMarketTemperature = () => http.get('/market/temperature')

// 宏观数据（全球+国内面板 + 规则方向分）
export const getMacroSnapshot = () => http.get('/macro/snapshot')

// 快讯监控（事件流 / LLM诊断 / 三段复盘 / 信号跟踪）
export const getFlashEvents = (params) => http.get('/flash/events', { params })
export const getFlashDiagnosis = (params) => http.get('/flash/diagnosis', { params })
export const getFlashReview = (phase) => http.get(`/flash/review/${phase}`)
export const getFlashSignals = () => http.get('/flash/signals')
export const triggerFlashIngest = () => http.post('/flash/ingest')
export const getFlashStatus = () => http.get('/flash/status')
export const getFlashNotifications = (params) => http.get('/flash/notifications', { params })
export const getFlashAudit = () => http.get('/flash/audit')

// 浏览器数据镜像（两人小团队的持久化兜底：定期备份到 localStorage，服务端清零后自动恢复）
export const getFlashBackup = () => http.get('/flash/backup')
export const restoreFlashBackup = (files, headers) => http.post('/flash/restore', { files }, { headers })
export const getMarketRealtime = (params) => http.get('/market/realtime', { params })
export const getIndexKline = (symbol, period = 'day') => http.get(`/market/index-kline/${symbol}`, { params: { period } })
export const getRefreshStatus = () => http.get('/market/refresh-status')
export const triggerRefresh = () => http.get('/market/trigger-refresh')

// 个股数据
export const getStockKline = (symbol, params) => http.get(`/stock/kline/${symbol}`, { params })
export const getStockRealtime = (symbol) => http.get(`/stock/realtime/${symbol}`)
export const getStockFundamental = (symbol) => http.get(`/stock/fundamental/${symbol}`)
export const getStockTechnical = (symbol, period = 'day') => http.get(`/stock/technical/${symbol}`, { params: { period } })
export const searchStock = (keyword) => http.get('/stock/search', { params: { keyword } })

// 评分引擎
export const getStockScore = (symbol) => http.get(`/score/${symbol}`)
export const getScoreTop = (params) => http.get('/score/batch/top', { params })
export const getScoreBottom = (params) => http.get('/score/batch/bottom', { params })
export const getScoreBySignal = (params) => http.get('/score/batch/signal', { params })
export const getBatchPrices = (codes) => http.get('/score/batch-prices', { params: { codes: codes.join(',') } })
export const getBacktest = (params) => http.get('/score/backtest', { params, timeout: 120000 })

// 资金流向
export const getNorthboundFlow = () => http.get('/capital/northbound')
// order: 'desc'=主力净流入最多（涌入榜）/ 'asc'=净流出最多（出逃榜）
export const getMainFlow = (params) => http.get('/capital/main-flow', { params })

// 板块数据
export const getIndustryFlow = (params) => http.get('/sector/industry-flow', { params })
export const getConceptFlow = (params) => http.get('/sector/concept-flow', { params })
export const getSectorIndustry = (params) => http.get('/sector/industry', { params })
export const getSectorConcept = (params) => http.get('/sector/concept', { params })
export const getNorthboundHoldings = () => Promise.resolve({ data: [] }) // 北向持股明细暂未实现
