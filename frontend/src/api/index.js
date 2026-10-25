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
      // ★ 2026-10-24（BK-57）：修复旧实现的两个坑——
      //   ① 本项目是 hash 路由（createWebHashHistory），旧代码 `href='/login'` 跳的是
      //      path 而非路由，落到静态服务器的 /login 上（404 或空 hash 兜底）；
      //   ② 旧判断用 `pathname !== '/login'`，一次跳转后 pathname 永远停在 /login
      //      ⇒ 后续 401 不再跳登录页 = token 过期后接口静默失败（鉴权收紧后此坑
      //        出现频率大增）。⇒ 改判 hash 前缀 + 只改 hash（不整页重载，
      //        并发 401 也只会跳一次，由路由守卫接管）。
      if (!window.location.hash.startsWith('#/login')) {
        window.location.hash = '#/login'
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
// ★ 2026-09-27 事件驱动信号（E2 政策脉冲）—— v0 展示项，不进决策链
export const getMarketEvents = () => http.get('/market/events')

// 市场状态识别
export const getMarketRegime = () => http.get('/strategies/market/regime')
export const getStrategyTypes = () => http.get('/strategies/market/strategy-types')
export const getStrategyRecommendation = (name) => http.get(`/strategies/${name}/recommendation`)

// 支撑阻力 + RSI
export const getSupportResistance = (code, lookback = 60) => http.get(`/strategies/${code}/support-resistance`, { params: { lookback } })
export const getRSISignals = (code, period = 14) => http.get(`/strategies/${code}/rsi`, { params: { period } })

// 板块联动筛选（2026-10-19，判据冻结于 PLAN_SECTOR_LINKAGE.md v1.0）
// mode=off|shadow|on；on 时下发 weak_codes（弱势板块代码）供本地榜对齐过滤
export const getSectorStrength = (date) => http.get('/score/batch/sector-strength', { params: { date } })

// 宏观数据（全球+国内面板 + 规则方向分）
export const getMacroSnapshot = () => http.get('/macro/snapshot')
export const getMacroDaily = (date) => http.get('/macro/daily', { params: { date } })
// 央行储备月度面板（外储/黄金储备/黄金占比）——月频慢变量，非交易信号（见 PLAN_RESERVE_SIGNALS.md）
export const getMacroReserves = (months = 24) => http.get('/macro/reserves', { params: { months } })

// 快讯监控（事件流 / LLM诊断 / 三段复盘 / 信号跟踪）
export const getFlashEvents = (params) => http.get('/flash/events', { params })
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
export const refreshFlashBrief = (date) =>
  http.post('/flash/brief/refresh', {}, { params: { date }, timeout: 120000 })
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
// ★★ 2026-09-29（P2）：**周复盘**（regime 轨迹 + 执行一致性/放弃理由 + 日报与系统提示索引 +
//   下周日历）。口径/边界见后端 `app/weekly_review.py`（纯只读聚合、零外部网络请求）。
//   `end` 可选（YYYY-MM-DD）用于回看历史某周。
export const getWeeklyReview = (days = 7, end = null) =>
  http.get('/report/weekly', { params: { days, ...(end ? { end } : {}) }, timeout: 30000 })
// ★ 2026-09-25 工作台：加可选 phase（premarket/intraday/postmarket），原调用兼容
// ★ 2026-09-30：`refresh=true` 是**同步重新生成（消耗 1 次 LLM）** ⇒ 超时给足 180s，
//   否则慢响应会被前端默认超时砍掉、看起来像"永远失败"。
// ★ 2026-10-23：`refresh`（"重新生成"按钮）同时带 `push=true` ⇒ 盘后简报生成后补推企微
//   （后端按 pushed_at 去重，当天最多推一次；盘前/盘中 phase 不推）。
export const getTraderBrief = (refresh, phase) => http.get('/system/trader-brief',
  { params: { ...(refresh ? { refresh: true, push: true } : {}),
              ...(phase ? { phase } : {}) },
    ...(refresh ? { timeout: 180000 } : {}) })

// 矛盾扫描引擎
export const getContradictions = (params = {}) => http.get('/contradictions', { params })
export const getContradictionsSummary = (date) => http.get('/contradictions/summary', { params: date ? { date } : {} })
export const getContradictionsReport = (date) => http.get('/contradictions/report', { params: date ? { date } : {} })
// ★ 重任务统一走 GitHub Actions（Render 512MB 不再自己算）：
//   本接口只投递任务，由 Actions 完成后写库，前端稍后刷新读结果即可
export const triggerDailyBatch = (tasks = 'all', force = true) =>
  http.post('/tasks/trigger', { task: tasks, force })
export const getTaskConfig = () => http.get('/tasks/config')

export const triggerContradictionsScan = (date) => http.post('/contradictions/scan', {}, { params: date ? { date } : {} })
export const triggerContradictionsReport = (date) => http.post('/contradictions/report', {}, { params: date ? { date } : {} })

// 【已退役 2026-09-12】浏览器数据镜像的前端调用（getFlashBackup / restoreFlashBackup）已移除：
//   它保护的 9 个数据文件早已全部迁库、只剩空壳，却每 5 分钟驱动一次「读诊断正文只为计数」
//   的 Supabase egress 消耗。现在改由后端启动检查 + `/api/system/runtime-files` 盯着仍在
//   文件里的数据（财经日历/LLM 用量/K线缓存/数据包）。后端 /flash/backup、/flash/restore
//   保留为手动导出/回填工具（不再被页面自动调用）。
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
export const getStockScore = (symbol) => http.get(`/score/${symbol}`)
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
// ★ 买入闸门观察池（ready≥2 的「等状态」候选 + 还差什么条件）—— 2026-09-22
//   ⚠️ 路径是 `/score/batch/gate-watch`（**两段**）：后端 `/{symbol}` 单段通配路由
//   注册在前、会吞掉单段静态路径 ⇒ 原先 `/score/gate-watch` 恒返回
//   `{"error":"未找到股票 gate-watch"}`（200、不抛错）⇒ 页面静默空列表。
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
export const postFeedback = (payload) =>
  http.post('/feedback', null, { params: payload, timeout: 10000 })
// ★ 持仓雷达（2026-09-23）：以**用户持仓为主轴**的状态聚合 —— 评分/主力阶段/闸门就绪/
//   战法命中/是否在观察池/所属板块被矛盾点名，一次回答「我手里那几只怎么样」。
//   ⚠️ 路径两段 `/score/batch/*`：后端 `/{symbol}` 单段通配路由会吞掉单段静态路径（9-22 实锤）。
//   ⚠️ 后端冷启动首次约半分钟 ⇒ 可能先返回 `warming: true` 占位（服务端有后台预热 loop，
//      正常秒开）；前端据此显示「正在准备」，**不要当成错误**。
export const getPortfolioRadar = () => http.get('/score/batch/portfolio-radar', { timeout: 45000 })
// ★ 2026-10-18：市场底确认清单（**纯观察器，非信号**；口径见后端 app/policy_bottom_watch.py）
//   只有第①条（国家队公告）可人工打勾 —— 无结构化数据源，打勾权在人。
export const getPolicyBottom = () => http.get('/market/policy-bottom')

export const confirmPolicyBottom = (checked, note = '', date = '') =>
  http.post('/market/policy-bottom/confirm', null, {
    params: { checked: checked ? 1 : 0, note, ...(date ? { date } : {}) },
  })

// ★ 2026-10-19：第①条的**自动提示**（巨潮公告扫描结果，日批写入；**只提示不打勾**）
//   ⚠️ searchkey 是分词匹配，过滤在后端本地做（见 app/policy_announcements.py）
export const getPolicyAnnouncements = (date = '') =>
  http.get('/market/policy-bottom/announcements', { params: date ? { date } : {} })

// ★ 2026-10-25：李大霄视角卡（**纯展示参考，非信号**；口径见后端 app/lidaxiao_views.py）。
//   数据由 xiaopage 在 LLM 输出解读的时刻事件推送落库（POST /market/lidaxiao/push），
//   本接口只读最新一期 + 近 14 期轨迹；工作台 60s 随教练卡轮询，响应 ~3KB。
export const getLidaxiaoView = (limit = 14) =>
  http.get('/market/lidaxiao', { params: { limit } })

// ★ 2026-10-25：流动性三件套（两融/期指基差/中美利差，日批落库的历史序列 + 最新一期；
// 纯观察器不进决策链，口径见后端 app/liquidity_watch.py）。日频数据，进页面加载即可。
export const getMacroLiquidity = (days = 30) =>
  http.get('/macro/liquidity', { params: { days } })

// 手动重扫（不推企微）；网络请求最长约 30~60s（2 个关键词 × 最多 5 页）
export const refreshPolicyAnnouncements = (date = '') =>
  http.post('/market/policy-bottom/announcements/refresh', null,
    { params: date ? { date } : {}, timeout: 60000 })

// ★ 2026-10-19：第①条的**份额异动证据**（上交所官方；连续 2 日 ≥8% ⇒ 自动打勾）
//   ⚠️ 深市 159915 暂未纳入（深交所该报表固定 10 条/页 = 76 页/日，成本过高）
export const getPolicyEtfShares = (date = '') =>
  http.get('/market/policy-bottom/etf-shares', { params: date ? { date } : {} })

export const refreshPolicyEtfShares = (date = '') =>
  http.post('/market/policy-bottom/etf-shares/refresh', null,
    { params: date ? { date } : {}, timeout: 60000 })
// ★ 系统提示时间线（信号总线，2026-09-23）：「系统今天说了什么」的统一视图。
//   记录点在 `flash.wechat.push_markdown_batched`（全部业务推送的单点入口）⇒ 覆盖
//   盘中警示 / 矛盾扫描 / 午间雷达 / 日报周报 / 模拟盘 / 教练 / 数据源健康等全部来源。
export const getPushLog = (date, limit = 80) => http.get('/flash/push-log', { params: { date, limit }, timeout: 20000 })
// ★ 系统自洽性审查（2026-09-23）：把今日全部系统提示交给 LLM，检查**系统自身**的判断
//   是否互相矛盾/前后反复（对照：`contradictions` 扫的是**市场数据**层面的矛盾）。
//   后端按日缓存 ⇒ 同日再调零成本；refresh=true 强制重算（消耗一次 LLM 调用）。
export const getRadarAnalysis = (date, refresh = false) => http.get('/flash/radar-analysis', { params: { date, refresh }, timeout: 120000 })
export const getScoreBottom = (params) => http.get('/score/batch/bottom', { params, timeout: 60000 })
export const getScoreBySignal = (params) => http.get('/score/batch/signal', { params, timeout: 60000 })
export const getBatchPrices = (codes) => http.get('/score/batch-prices', { params: { codes: codes.join(',') } })
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

// ★ 2026-09-27：zzshare 板块序列（**独立体系**，东财 push2 不稳时的稳定替代源）。
//   东财 `sector_daily` 因限流**缺日**（错过即无法回补），而 zzshare 支持历史日期 ⇒ 可回填。
//   ⚠️ 两套 taxonomy 不兼容（东财 BK/496 vs zzshare 881/104）⇒ 独立表，勿与上面混用。
export const getZzSectorDispersion = (params) => http.get('/sector/zz/dispersion', { params })
export const getZzSectorSnapshot = (date, params) => http.get(`/sector/zz/snapshot/${date}`, { params })
export const getZzSectorHistory = (code, days = 60) => http.get(`/sector/zz/history/${code}`, { params: { days } })
export const getZzSectorStats = () => http.get('/sector/zz/stats')
export const takeZzSectorSnapshot = (date) => http.post('/sector/zz/take', {}, { params: { date } })
// 个股→行业映射（评分引擎板块因子的基础数据）
export const getStockIndustry = (code) => http.get(`/sector/stock-industry/${code}`)
export const getIndustryMapStats = () => http.get('/sector/industry-map/stats')
// 行业主线/共振（每日 Top50 × 行业映射 → 主线榜 + 风格切换信号）
export const getMainlineSummary = (days = 12) => http.get('/sector/mainline/summary', { params: { days } })
export const buildMainlineDate = (date) => http.get('/sector/mainline/date', { params: { date } })
export const pushMainlineReport = (days = 12) => http.post('/sector/mainline/push', {}, { params: { days } })
export const getMainlinePerformance = (days = 30) => http.get('/sector/mainline/performance', { params: { days } })
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
export const getSystemStatus = () => http.get('/system/status')
// ★ 2026-09-23：进程内存诊断（Render 500MB 反复 OOM 的运维出口；首页自动调用 ⇒
//   每次加载都推进一次后端快照，diff 自然累积成"谁在涨"的时间序列）
export const getSystemMemory = (params) => http.get('/system/memory', { params })
// ★ 2026-09-24：数据库体积（Supabase 免费档 500MB/项目 —— **超限变只读、日批全挂且不会自愈**，
//   比 OOM 更致命 ⇒ 与进程内存并列的运维出口）。分表体积 + 死行比（判断是否该 VACUUM）。
export const getDbUsage = (params) => http.get('/system/db-usage', { params })
export const getSystemPerformance = () => http.get('/system/performance', { timeout: 120000 })  // 首访冷路径 ~60s
export const getUserWatchlist = () => http.get('/user/watchlist')
export const upsertUserWatch = (item) => http.post('/user/watchlist', item)
export const deleteUserWatch = (code) => http.delete(`/user/watchlist/${code}`)
export const getUserPlans = () => http.get('/user/plans')
export const upsertUserPlan = (item) => http.post('/user/plans', item)
export const updateUserPlan = (id, item) => http.put(`/user/plans/${id}`, item)
export const deleteUserPlan = (id) => http.delete(`/user/plans/${id}`)
export const getUserPortfolio = () => http.get('/user/portfolio')
// 仓位建议（W1.5）：后端数据联动（regime+宏观+情绪+宽度+主力筹码），替代前端写死规则
export const getUserPositionSizing = () => http.get('/user/position-sizing')
// ★ 2026-09-27 统一出场建议（「什么时候卖」的单一事实源）：
//   后端合并 portfolio_radar（组合面）+ exit_alert（技术面）+ 移动止盈，
//   输出单一 action（清仓/减仓½/减仓⅓/关注减仓/持有/可加仓）。
//   取代此前「我的持仓页前端自算」那套 —— 消除同持仓在不同页给出相反建议的问题。
export const getUserExitAdvice = () => http.get('/user/exit-advice')
// ★ 2026-09-27 数据可信度总表（「已验证 / 未验证 / 已证伪」单一事实源）。
//   用于在界面标注各结论的实证状态 —— 避免用户把未验证的评分/战法
//   与已验证的闸门（19.5 年核算）当成同等权威。见 backend/app/verification.py。
export const getVerification = () => http.get('/market/verification')
// ★ 2026-09-25：龙虎榜（读库 lhb_history，每日 17:45 同步）。
//   不传 code ⇒ 某日净买/净卖榜 + 统计；传 code ⇒ 该股上榜历史（含席位明细，如有）。
export const getDragonTiger = (params) => http.get('/capital/dragon-tiger', { params })
export const upsertUserPortfolio = (item) => http.post('/user/portfolio', item)
export const deleteUserPortfolio = (code) => http.delete(`/user/portfolio/${code}`)
export const batchSyncUser = (data) => http.post('/user/sync', data)

// 模拟盘（纸面交易：pending 待确认 → holding 持仓 → closed 已平仓）
export const getPaperPositions = (status) => http.get('/paper/positions', { params: { status } })
export const manualIngestPaper = (body) => http.post('/paper/positions/manual', body)
export const closePaperPosition = (id) => http.post(`/paper/positions/${id}/close`)
export const cancelPaperPosition = (id) => http.delete(`/paper/positions/${id}`)
export const getPaperStats = () => http.get('/paper/stats')
export const getPaperAttribution = () => http.get('/paper/attribution')
export const getPaperAccount = () => http.get('/paper/account')
export const refreshPaperWhitelist = () => http.post('/paper/whitelist/refresh')
export const getPaperRisk = () => http.get('/paper/risk')
export const unfreezePaperRisk = () => http.post('/paper/risk/unfreeze')

// 交易教练（Coach W1：规则引擎硬警报 + 执行回写 + 执行一致性 KPI）
// 教练只输出「规则结果」，数字由代码注入（LLM 不参与决策，见 PLAN §2.4 评审①）
export const getCoachAlerts = (limit = 50) => http.get('/coach/alerts', { params: { limit } })
export const executeCoachAlert = (id, executed, reason = '') =>
  http.post(`/coach/alerts/${id}/execute`, { executed, reason })
// ★ 2026-09-25：加 `day`（YYYY-MM-DD，可选）= **只统计该日** —— 供盘后「今日执行回看」。
//   ⚠️ 不能用 `days=1` 顶替"今天"（那是"近 1 天"，`alert_date >= 昨天`，会把昨天算进来）。
export const getCoachConsistency = (days = 30, day = '') =>
  http.get('/coach/consistency', { params: { days, day } })
export const getCoachPlanExecutionRate = (days = 30) => http.get('/coach/plans/execution-rate', { params: { days } })
export const getCoachPlans = (positionIds) => http.get('/coach/plans', { params: { position_ids: (positionIds || []).join(',') } })
export const abandonCoachPlan = (id, reason) => http.post(`/coach/plans/${id}/abandon`, { reason })
export const getCoachAbandonReasons = (limit = 20) => http.get('/coach/abandon-reasons', { params: { limit } })
// ★★ 2026-09-29（P2 / 缺口 3）：教练建议**事后归因** —— 补「5 日结果只回填不评估」那一半。
//   按 建议类型/主力阶段/执行结果/放弃理由 分组算 T+5/T+20 对基准超额 ⇒ 分别对应
//   「逻辑错 / 时机错 / 执行错」（执行率只覆盖最后一种）。口径与已知限制见后端模块头。
export const getCoachAttribution = (days = 60) => http.get('/coach/attribution', { params: { days } })

// ── 工作台（2026-09-25）：回放按日期取数（后端 system.py 只读端点）──
// ── 工作台·盘中看盘序（2026-09-25 交易方法论 A1/A3/B2）──
export const getMarketEmotion = () => http.get('/market/emotion', { timeout: 20000 })
// ★ 2026-09-25 P3：情绪对账（盘前预判 vs 当日实际）—— 读 market_emotion_daily 的两组字段
export const getEmotionReview = (days = 30) => http.get('/market/emotion-review', { params: { days } })
// ★ 2026-09-25 需求 3：尾盘承接（14:30 基线 → 现在/收盘）—— 决定是否持仓过夜
export const getMarketTailReview = (date) => http.get('/market/tail-review', { params: { date } })
// ★ 2026-09-25 需求 4：行业成交额占比（腾讯内存行情 + 落库映射聚合 ⇒ 零外部请求，
//   不受东财封禁影响；东财板块接口本身也不带成交额字段）
export const getSectorAmountShare = (top = 15) => http.get('/sector/amount-share', { params: { top } })
export const getMarketLimitReview = (date) => http.get('/market/limit-review', { params: { date }, timeout: 30000 })
// ★ 2026-09-29 P1：盘中实时涨停/炸板（zzshare rt_k 全市场精确口径，判据用 high_limit 字段）。
//   与 limit-review 的「收盘定稿」口径**互补而非替代**：本接口盘前/休市时返回上一交易日
//   收盘快照（`is_intraday=false` + `data_date`），盘中返回此刻动态值。
export const getRealtimeUplimit = () => http.get('/market/uplimit-realtime', { timeout: 20000 })
// ★ 2026-09-29 P1：财经日历**前瞻**（未来 N 天核心事件：PCE/CPI/非农/FOMC…）。
//   与 /flash/calendar（全量、含官员讲话）互补；筛选口径与盘前企微推送同源（后端统一）。
export const getCalendarAhead = (days = 3) => http.get('/flash/calendar-ahead', { params: { days } })
// ★ 2026-09-29：两市成交额环比（U 型曲线法）。⚠️ `reliable=false`（14:00 前）时
//   前端只显示累计，**不得**给缩量/放量结论（早盘预估中位误差 16%）。
export const getAmountRealtime = () => http.get('/market/amount-realtime', { timeout: 20000 })
// ★ 2026-09-25 P3：情绪对账（盘前预判 vs 当日实际）—— 读 market_emotion_daily 的两组字段

// ⚠️⚠️ 2026-09-25 修正：三个路径必须带 `/system` 前缀！
//   后端 `routers/system.py` 在 main.py 里挂在 `prefix="/api/system"` 下
//   ⇒ `@router.get("/workbench/decision-card")` 的真实路径是 `/api/system/workbench/decision-card`。
//   此前写 `/workbench/...` ⇒ FastAPI 不匹配 ⇒ **落到后端 SPA 静态兜底** ⇒ 返回 200 + `text/html`
//   （首页 HTML）⇒ axios 拿到的是字符串而非 JSON ⇒ 解构后字段全空、页面"空白"，
//   但 Network 面板显示 200 且有内容（极易误判为"后端旧进程/数据缺失"）。
export const getWorkbenchDayIndex = (days = 30) => http.get('/system/workbench/day-index', { params: { days } })
export const getWorkbenchDay = (date) => http.get('/system/workbench/day', { params: { date }, timeout: 30000 })
export const getWorkbenchDecisionCard = () => http.get('/system/workbench/decision-card', { timeout: 30000 })

// ★ 2026-10-22：盘中「变化检测器」——8 档规则采样（零 LLM，09:45~14:45）
export const getWorkbenchScan = (date) => http.get('/system/workbench-scan', { params: { date }, timeout: 30000 })
export const getWorkbenchScanSummary = (days = 7) => http.get('/system/workbench-scan/summary', { params: { days }, timeout: 30000 })

// ★ 2026-10-23：两个观察器（不进决策链）——拥挤赛道 / 指数关键位
export const getCrowdingWatch = (date) => http.get('/system/crowding-watch', { params: { date }, timeout: 30000 })
export const getKeyLevelWatch = (date) => http.get('/system/key-level-watch', { params: { date }, timeout: 30000 })
export const getAiBasketWatch = (date) => http.get('/system/ai-basket-watch', { params: { date }, timeout: 30000 })
export const getScenarioWatch = (date) => http.get('/system/scenario-watch', { params: { date }, timeout: 30000 })
// ★ 2026-10-24：观察器 AI 解读（LLM 同步调用，按日缓存 + 数据指纹；默认点按钮才请求）
export const getObserverReview = (date, refresh = false) => http.get('/system/observer-review', { params: { date, refresh }, timeout: 120000 })
