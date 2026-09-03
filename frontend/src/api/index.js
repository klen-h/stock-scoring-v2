import axios from 'axios'

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
})

// ── Token 管理 ──
const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function removeToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function getUser() {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? JSON.parse(raw) : null
  } catch {
    return null
  }
}

export function isLoggedIn() {
  return !!getToken()
}

// ── 请求拦截器：自动带 Token ──
http.interceptors.request.use(config => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// ── 响应拦截器：401 自动跳转登录 ──
http.interceptors.response.use(
  response => response,
  error => {
    if (error.response?.status === 401) {
      // Token 过期或无效，清除本地状态
      removeToken()
      // 如果不在登录页，跳转到登录
      if (window.location.hash !== '#/login' && window.location.pathname !== '/login') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  }
)

// ── 认证 API ──
export const authRegister = (username, password) => http.post('/auth/register', { username, password })
export const authLogin = (username, password) => http.post('/auth/login', { username, password })
export const authCurrent = () => http.get('/auth/current')

// 市场行情
export const getMarketOverview = () => http.get('/market/overview')
export const getMarketTemperature = () => http.get('/market/temperature')

// 市场状态识别
export const getMarketRegime = () => http.get('/strategies/market/regime')
export const getStrategyTypes = () => http.get('/strategies/market/strategy-types')
export const getStrategyRecommendation = (name) => http.get(`/strategies/${name}/recommendation`)

// 支撑阻力 + RSI
export const getSupportResistance = (code, lookback = 60) => http.get(`/strategies/${code}/support-resistance`, { params: { lookback } })
export const getRSISignals = (code, period = 14) => http.get(`/strategies/${code}/rsi`, { params: { period } })

// 宏观数据（全球+国内面板 + 规则方向分）
export const getMacroSnapshot = () => http.get('/macro/snapshot')
export const getMacroDaily = (date) => http.get('/macro/daily', { params: { date } })

// 快讯监控（事件流 / LLM诊断 / 三段复盘 / 信号跟踪）
export const getFlashEvents = (params) => http.get('/flash/events', { params })
export const getFlashDiagnosis = (params) => http.get('/flash/diagnosis', { params })
export const getFlashReview = (phase) => http.get(`/flash/review/${phase}`)
export const getFlashReviewHistory = (phase, limit = 20) => http.get(`/flash/review/${phase}/history`, { params: { limit } })
export const getFlashSignals = () => http.get('/flash/signals')
export const triggerFlashIngest = () => http.post('/flash/ingest')
export const getFlashStatus = () => http.get('/flash/status')
export const getFlashNotifications = (params) => http.get('/flash/notifications', { params })
export const getFlashAudit = () => http.get('/flash/audit')

// 财经日历（金十：经济指标 / 事件讲话 / 交易所休市，后端每日 07:00 自动刷新缓存）
export const getCalendar = (params) => http.get('/flash/calendar', { params })
export const refreshCalendar = (daysAhead = 14) => http.post('/flash/calendar/refresh', {}, { params: { days_ahead: daysAhead } })

// 历史回测（引擎计算，后端 10 分钟缓存；冷启动需从库拉几百只日线，放宽超时）
export const getBacktestStrategy = (name) => http.get('/backtest/strategy', { params: { name }, timeout: 120000 })

// 周度回测报告归档（scheduler 每周五 16:00 生成的 markdown 文件）
export const getBacktestReports = () => http.get('/backtest/reports')
export const getBacktestReportContent = (name) => http.get('/backtest/reports/content', { params: { name } })

// A股大盘日报（scheduler 每日 16:20 生成，落库 daily_reports）
export const getDailyReportList = (limit = 30) => http.get('/report/list', { params: { limit } })
export const getDailyReport = (date) => http.get('/report/daily', { params: date ? { date } : {} })

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
export const getStockNews = (symbol) => http.get(`/stock/news/${symbol}`)
export const getStockNewsHistory = (symbol, days = 30) => http.get(`/stock/news/${symbol}/history`, { params: { days } })
export const getStockTechnical = (symbol, period = 'day') => http.get(`/stock/technical/${symbol}`, { params: { period } })
export const getStockFinance = (symbol, params) => http.get(`/stock/finance/${symbol}`, { params })
export const getStockFinanceHistory = (symbol, limit = 12) => http.get(`/stock/finance/${symbol}/history`, { params: { limit } })
// 批量财报（前端本地评分引擎算 top50 时用：1 次取回候选池财报，传给 scoreStock 算成长/质量维度）
// ★ 响应拦截器返回的是 AxiosResponse，这里统一解包成 .data，避免调用方拿到整个 response 对象
export const getFinanceBatch = (codes) => http.post('/stock/finance/batch', codes).then(r => r.data)
export const searchStock = (keyword) => http.get('/stock/search', { params: { keyword } })

// 评分引擎
export const getStockScore = (symbol) => http.get(`/score/${symbol}`)
export const getScoreTop = (params) => http.get('/score/batch/top', { params, timeout: 90000 })
export const getScoreBottom = (params) => http.get('/score/batch/bottom', { params, timeout: 60000 })
export const getScoreBySignal = (params) => http.get('/score/batch/signal', { params, timeout: 60000 })
export const getBatchPrices = (codes) => http.get('/score/batch-prices', { params: { codes: codes.join(',') } })
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
export const getAnomalies = (watchCodes) => http.get('/stock/anomalies', { params: { watch_codes: watchCodes } })

// 资金流向
export const getNorthboundFlow = () => http.get('/capital/northbound')
// order: 'desc'=主力净流入最多（涌入榜）/ 'asc'=净流出最多（出逃榜）
export const getMainFlow = (params) => http.get('/capital/main-flow', { params })

// 板块数据
export const getIndustryFlow = (params) => http.get('/sector/industry-flow', { params })
export const getConceptFlow = (params) => http.get('/sector/concept-flow', { params })
export const getSectorIndustry = (params) => http.get('/sector/industry', { params })
export const getSectorConcept = (params) => http.get('/sector/concept', { params })

// 板块每日快照（历史序列 → 分化度 / 板块动量；交易日 15:10 自动记录）
export const getSectorDispersion = (params) => http.get('/sector/dispersion', { params })
export const getSectorSnapshot = (date, params) => http.get(`/sector/snapshot/${date}`, { params })
export const getSectorHistory = (code, days = 30) => http.get(`/sector/history/${code}`, { params: { days } })
export const getSectorSnapshotStats = () => http.get('/sector/snapshot-stats')
export const takeSectorSnapshot = (date) => http.post('/sector/snapshot/take', {}, { params: { date } })
// 个股→行业映射（评分引擎板块因子的基础数据）
export const getStockIndustry = (code) => http.get(`/sector/stock-industry/${code}`)
export const getIndustryMapStats = () => http.get('/sector/industry-map/stats')
// 行业主线/共振（每日 Top50 × 行业映射 → 主线榜 + 风格切换信号）
export const getMainlineSummary = (days = 12) => http.get('/sector/mainline/summary', { params: { days } })
export const buildMainlineDate = (date) => http.get('/sector/mainline/date', { params: { date } })
export const pushMainlineReport = (days = 12) => http.post('/sector/mainline/push', {}, { params: { days } })
export const getNorthboundHoldings = () => Promise.resolve({ data: [] }) // 北向持股明细暂未实现

// 战法选股
export const getStrategiesList = () => http.get('/strategies/list')
export const scanStrategy = (name, params) => http.get(`/strategies/${name}/scan`, { params, timeout: 120000 })
export const getStrategyResult = (name) => http.get(`/strategies/${name}/result`)
export const getStrategyWatch = (name) => http.get(`/strategies/${name}/watch`)
export const updateStrategyWatch = (name, stocks) => http.post(`/strategies/${name}/watch`, stocks)
export const getStrategyDetail = (name, code) => http.get(`/strategies/${name}/detail/${code}`)
export const getScanStatus = (name) => http.get(`/strategies/${name}/status`)

// 战法回测
export const runBacktest = (name, params) => http.get(`/strategies/${name}/backtest`, { params, timeout: 120000 })
export const getBacktestResult = (name) => http.get(`/strategies/${name}/backtest/result`)
export const getBacktestSummary = () => http.get('/strategies/backtest/summary')

// 信号持久度 + 撤退提醒
export const getPersistence = (name) => http.get(`/strategies/${name}/persistence`)
export const getPersistentSignals = (name, minDays = 3) => http.get(`/strategies/${name}/persistent-signals`, { params: { min_days: minDays } })
export const checkExitAlerts = (positions) => http.post('/strategies/exit-alerts', positions)
export const getExitSummary = (positions) => http.post('/strategies/exit-summary', positions)

// 用户数据（自选股/交易计划/持仓 → 数据库同步）
export const getUserWatchlist = () => http.get('/user/watchlist')
export const upsertUserWatch = (item) => http.post('/user/watchlist', item)
export const deleteUserWatch = (code) => http.delete(`/user/watchlist/${code}`)
export const getUserPlans = () => http.get('/user/plans')
export const upsertUserPlan = (item) => http.post('/user/plans', item)
export const updateUserPlan = (id, item) => http.put(`/user/plans/${id}`, item)
export const deleteUserPlan = (id) => http.delete(`/user/plans/${id}`)
export const getUserPortfolio = () => http.get('/user/portfolio')
export const upsertUserPortfolio = (item) => http.post('/user/portfolio', item)
export const deleteUserPortfolio = (code) => http.delete(`/user/portfolio/${code}`)
export const batchSyncUser = (data) => http.post('/user/sync', data)

// 模拟盘（纸面交易：pending 待确认 → holding 持仓 → closed 已平仓）
export const getPaperPositions = (status) => http.get('/paper/positions', { params: { status } })
export const manualIngestPaper = (body) => http.post('/paper/positions/manual', body)
export const closePaperPosition = (id) => http.post(`/paper/positions/${id}/close`)
export const cancelPaperPosition = (id) => http.delete(`/paper/positions/${id}`)
export const getPaperStats = () => http.get('/paper/stats')
export const getPaperAccount = () => http.get('/paper/account')
export const refreshPaperWhitelist = () => http.post('/paper/whitelist/refresh')
export const getPaperRisk = () => http.get('/paper/risk')
export const unfreezePaperRisk = () => http.post('/paper/risk/unfreeze')
