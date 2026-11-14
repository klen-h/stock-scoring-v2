// ==============================================================================
// market.js —— 由 api/index.js 按域拆分（2026-11-14 结构重组；index.js re-export 兼容）
// 本文件只做「请求定义」：参数进、响应出，无状态。业务状态在 composables/。
// ==============================================================================
import { http } from '../http'

export const getMarketOverview = () => http.get('/market/overview')
// ★ 2026-11-01（「早盘复盘」标签页）：**单一聚合端点**。
//   盘面 / 宏观 / 消息 / 午后研判一次取回（后端并发查询 + 5 分钟按日缓存）。
//   刻意不拆成 5 个请求：Supabase 在 ap-southeast-1，每次往返约 570ms，
//   前端串行拼接会到 3 秒以上，标签切换明显卡顿。

export const getAmReview = (date) =>
  http.get('/market/amreview', { params: date ? { date } : {}, timeout: 20000 })
// ★ 2026-11-01：数据附录的「AI 解读」。
//   GET 只读缓存（绝不触发 LLM）；POST 才真正生成（同步 LLM，十几秒 ⇒ 超时给足 120s）。

export const getAmReviewDigest = (date) =>
  http.get('/market/amreview/digest', { params: date ? { date } : {}, timeout: 15000 })

export const runAmReviewDigest = (date) =>
  http.post('/market/amreview/digest', {}, { params: date ? { date } : {}, timeout: 120000 })

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

export const getMacroSnapshot = () => http.get('/macro/snapshot')

export const getMacroDaily = (date) => http.get('/macro/daily', { params: { date } })
// 央行储备月度面板（外储/黄金储备/黄金占比）——月频慢变量，非交易信号（见 PLAN_RESERVE_SIGNALS.md）

export const getMacroReserves = (months = 24) => http.get('/macro/reserves', { params: { months } })

// 快讯监控（事件流 / LLM诊断 / 三段复盘 / 信号跟踪）

export const getMarketRealtime = (params) => http.get('/market/realtime', { params })

export const getIndexKline = (symbol, period = 'day') => http.get(`/market/index-kline/${symbol}`, { params: { period } })

export const getRefreshStatus = () => http.get('/market/refresh-status')

export const triggerRefresh = () => http.get('/market/trigger-refresh')

// 个股数据

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

// ★ 2026-10-28（用户："盘后也会有更新，加个手动更新按钮"）：推送失败时的手动兜底。
//   后端从 xiaopage 的 videos.json（GitHub raw，~6.4MB）补拉 → 走同一个 ingest 落库；
//   带 ETag 短路（上游没变 → 304 零 body），故重复点击不产生流量。**手动、不进定时**。

export const refreshLidaxiao = () => http.post('/market/lidaxiao/refresh')

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

export const getVerification = () => http.get('/market/verification')
// ★ 2026-09-25：龙虎榜（读库 lhb_history，每日 17:45 同步）。
//   不传 code ⇒ 某日净买/净卖榜 + 统计；传 code ⇒ 该股上榜历史（含席位明细，如有）。

export const getDragonTiger = (params) => http.get('/capital/dragon-tiger', { params })

export const getMarketEmotion = () => http.get('/market/emotion', { timeout: 20000 })
// ★ 2026-11-08：竞价面板三块增强（指数 vs 昨日关键位/预期区间 + 板块联动 + LLM 解读）。
//   GET 面板零 LLM；POST 才真烧（按日缓存+数据指纹，同 obsAI/flash_brief 模式）。
// ⚠️ 本接口**走本地开发缓存**（`VITE_LOCAL_CACHE=1`，TTL=0 永不过期 —— 用户
//   有意为之，用来省后端重算与流量）。副作用：若首次加载时后端还没当日 K 线
//   （`open_price=null`），这份空值会被缓存住 ⇒ 之后一直显示 `—`，
//   **刷新页面/重启前后端都无效**（请求根本不发）。
//   ⇒ 遇到时在浏览器控制台执行 `clearApiCache()`，再刷新页面。
// ★ 2026-11-08（盯盘计划 G1）：持仓参考线（模拟仓 entry/stop/target 落库值 + 真实持仓 cost）
// ★ 2026-11-08（G7）：涨停原因归类 map（事后描述）

export const getUlimitReasons = (date) => http.get('/market/uplimit-reasons', { params: { date }, timeout: 15000 })
// ★ 2026-11-08（G10-A）：行业主力净流入榜（mainflow×industry 聚合，T-1 口径）
// ★ 2026-11-08：后端已删 `days` 参数（原"多日累加"是 for+break 死循环、
//   实际只返回最新 1 天，且无产品需求）⇒ 前端同步去掉，避免"传了也没用"的误导。
// ★ 2026-11-08（ETF 榜·方案 B）：ETF 实时热度榜（白名单 200 只，60s 独立循环）

export const getEtfBoard = (sort = 'hot', top = 20) => http.get('/market/etf-board', { params: { sort, top }, timeout: 20000 })

export const getIndustryMainflow = () => http.get('/capital/industry-mainflow', { timeout: 20000 })

export const getAuctionPanel = () => http.get('/market/auction-panel', { timeout: 30000 })

export const refreshAuctionLlm = (force = false) =>
  http.post('/market/auction-llm', {}, { params: { force }, timeout: 120000 })
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

export const getAmountRealtime = () => http.get('/market/amount-realtime', { timeout: 20000 })
// ★ 2026-10-26（G2 轮询合并）：盘中「涨停/炸板 + 两市成交额环比」一次取回。
//   两者同门控（仅交易时段）、同 60s 节奏、都只读后端进程缓存 ⇒ 合并后**外部请求不变**，
//   浏览器每次 tick 少一次往返。返回 `{ok, uplimit:{available,…}|null, amount:{available,…}|null}`，
//   **按块降级**：单块异常只有该块 available=false，另一块照常（前端各留旧值）。

export const getIntradayLite = () => http.get('/market/intraday-lite', { timeout: 20000 })
// ★ 2026-10-26（G1 轮询合并）：盘面脉冲 —— **一次取回 5 块同源数据**
//   （overview / temperature / emotion / amount_share / 观察池报价），
//   替代原 120s 组里并发的 5 个 HTTP。返回 `{ok, blocks:{…}, errors:{块:原因}}`，
//   **按块降级**：某块 null 时前端保留旧值，其余块照常渲染。

export const getMarketPulse = (codes = []) =>
  http.get('/market/pulse', { params: { codes: codes.join(',') }, timeout: 25000 })
// ★ 2026-09-25 P3：情绪对账（盘前预判 vs 当日实际）—— 读 market_emotion_daily 的两组字段

// ⚠️⚠️ 2026-09-25 修正：三个路径必须带 `/system` 前缀！
//   后端 `routers/system.py` 在 main.py 里挂在 `prefix="/api/system"` 下
//   ⇒ `@router.get("/workbench/decision-card")` 的真实路径是 `/api/system/workbench/decision-card`。
//   此前写 `/workbench/...` ⇒ FastAPI 不匹配 ⇒ **落到后端 SPA 静态兜底** ⇒ 返回 200 + `text/html`
//   （首页 HTML）⇒ axios 拿到的是字符串而非 JSON ⇒ 解构后字段全空、页面"空白"，
//   但 Network 面板显示 200 且有内容（极易误判为"后端旧进程/数据缺失"）。

export const getStyleSpread = (days = 5, lookback = 20) =>
  http.get('/market/style-spread', { params: { days, lookback }, timeout: 20000 })

// ★ 2026-10-23：两个观察器（不进决策链）——拥挤赛道 / 指数关键位
