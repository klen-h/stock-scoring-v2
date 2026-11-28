// ==============================================================================
// system.js —— 由 api/index.js 按域拆分（2026-11-14 结构重组；index.js re-export 兼容）
// 本文件只做「请求定义」：参数进、响应出，无状态。业务状态在 composables/。
// ==============================================================================
import { http } from '../http'

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

export const triggerContradictionsScan = (date) => http.post('/contradictions/scan', {}, { params: date ? { date } : {} })

export const triggerContradictionsReport = (date) => http.post('/contradictions/report', {}, { params: date ? { date } : {} })

// 【已退役 2026-09-12】浏览器数据镜像的前端调用（getFlashBackup / restoreFlashBackup）已移除：
//   它保护的 9 个数据文件早已全部迁库、只剩空壳，却每 5 分钟驱动一次「读诊断正文只为计数」
//   的 Supabase egress 消耗。现在改由后端启动检查 + `/api/system/runtime-files` 盯着仍在
//   文件里的数据（财经日历/LLM 用量/K线缓存/数据包）。后端 /flash/backup、/flash/restore
//   保留为手动导出/回填工具（不再被页面自动调用）。

export const getStockFactsWide = (params) => http.get('/system/stock-facts/wide', { params, timeout: 30000 })
// ★ 买入闸门观察池（ready≥2 的「等状态」候选 + 还差什么条件）—— 2026-09-22
//   ⚠️ 路径是 `/score/batch/gate-watch`（**两段**）：后端 `/{symbol}` 单段通配路由
//   注册在前、会吞掉单段静态路径 ⇒ 原先 `/score/gate-watch` 恒返回
//   `{"error":"未找到股票 gate-watch"}`（200、不抛错）⇒ 页面静默空列表。

export const getSystemStatus = () => http.get('/system/status')
// ★ 2026-09-23：进程内存诊断（Render 500MB 反复 OOM 的运维出口；首页自动调用 ⇒
//   每次加载都推进一次后端快照，diff 自然累积成"谁在涨"的时间序列）

export const getSystemMemory = (params) => http.get('/system/memory', { params })
// ★ 2026-09-24：数据库体积（Supabase 免费档 500MB/项目 —— **超限变只读、日批全挂且不会自愈**，
//   比 OOM 更致命 ⇒ 与进程内存并列的运维出口）。分表体积 + 死行比（判断是否该 VACUUM）。

export const getDbUsage = (params) => http.get('/system/db-usage', { params })

export const getSystemPerformance = () => http.get('/system/performance', { timeout: 120000 })  // 首访冷路径 ~60s

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
// ★★ 2026-11-14（用户："对盘前盘中盘后进行复盘回测，验证准确性及优化；我主要在企微看，
//   需要把重要内容简要常驻到工作台"）：按**日**的三时段复盘对账。
//   与 rule-stats（按规则）/ attribution（按类型）分工 —— 这里回答"这一天说了什么、对了几条"。

// ★★ 2026-11-15（用户："新增股票量化做T功能…盘中获取实时行情"）：
//   做T 读接口。`plans` 的 `watch_only=1` 只返回**自选+持仓**里可做的
//   （全市场今天可能 300+ 只 feasible，全塞给用户等于噪音）。
// ★★ 2026-11-22（用户："做T 看板 · 预案（加载失败），跟超时有关"）：
//   后端 `daily_summary` 实测 15.9s（内部还调一次 list_plans），`list_plans` 8~16s，
//   `enrich_facts` +3s、`attach_buy_check` +2.3s。服务器走老库（新加坡）国际链路更慢。
//   全局默认 30s 会被打穿 ⇒ 前端先超时、后端还在跑（重试=白跑两遍）。
//   给 90s（与 getScoreTop 同档），保证"后端真失败时前端才失败"。
export const getTTradeSummary = () => http.get('/t-trade/summary', { timeout: 90000 })
// ★ 2026-11-22：历史成交 + 累计收益（`/summary` 只查当日，隔天/周末打开看不到历史的 T）。
//   纯单表查询，很快 ⇒ 30s 足够。
export const getTTradeHistory = (params = {}) =>
  http.get('/t-trade/history', { params, timeout: 30000 })
export const getTTradePlans = (params = {}) => http.get('/t-trade/plans', { params, timeout: 90000 })
export const getTTradeExplain = (code) => http.get('/t-trade/explain', { params: { code }, timeout: 45000 })
// 做T 池（**独立于自选**：自选是"想盯的票"，池是"能做T 的票"）
export const getTTradePool = () => http.get('/t-trade/pool')
export const addTTradePool = (codes, note = '') =>
  http.post('/t-trade/pool', { codes, note })
export const delTTradePool = (code) => http.delete(`/t-trade/pool/${code}`)
export const triggerTTradeScan = () => http.post('/t-trade/scan')
// 做T 推送自检（验证企微 coach 分类通道是否通）
export const testTTradePush = () => http.post('/t-trade/test-push')
// 用预案支撑位建「底仓建仓」交易计划（**这是建仓不是做T**，走 user_trade_plans）
export const buildTTradePlan = (code) =>
  http.post('/t-trade/build-plan', null, { params: { code } })
// ★ 2026-11-16（用户："价格到位，系统自动处理"）：**模拟底仓**
//   建仓点命中后系统自动在 `t_sim_positions` 建一笔（不真实下单、不写真实持仓），
//   于是这只票立刻"有底仓"⇒ 盘中的做T 循环能对它工作。
export const getTTradeSim = () => http.get('/t-trade/sim')
export const delTTradeSim = (code) => http.delete(`/t-trade/sim/${code}`)
// ★★ 2026-11-19（用户："池子系统通过挑选自动加入池"）：立即自动选池。
//   平时由盘后扫描自动触发；这个用于手动重跑。
//   ⚠️ 只替换 `source='auto'` 的行，手动加的票不会被清。
export const autoTTradePool = (params = {}) => http.post('/t-trade/pool/auto', null, { params })

export const getCoachReviewSummary = (date = '', days = 20) =>
  http.get('/coach/review-summary', { params: { date, days } })
// ★ 2026-11-13：按规则的准确率（补 /attribution 覆盖不到的市场级建议）

export const getCoachRuleStats = (days = 90) => http.get('/coach/rule-stats', { params: { days } })

// ── 工作台（2026-09-25）：回放按日期取数（后端 system.py 只读端点）──
// ── 工作台·盘中看盘序（2026-09-25 交易方法论 A1/A3/B2）──

export const getWorkbenchDayIndex = (days = 30) => http.get('/system/workbench/day-index', { params: { days } })

export const getWorkbenchDay = (date) => http.get('/system/workbench/day', { params: { date }, timeout: 30000 })

export const getWorkbenchDecisionCard = () => http.get('/system/workbench/decision-card', { timeout: 30000 })

// ★ 2026-10-22：盘中「变化检测器」——8 档规则采样（零 LLM，09:45~14:45）

export const getWorkbenchScan = (date) => http.get('/system/workbench-scan', { params: { date }, timeout: 30000 })

export const getWorkbenchScanSummary = (days = 7) => http.get('/system/workbench-scan/summary', { params: { days }, timeout: 30000 })

// ★★ 2026-11-13：风格分化观察器（钱在权重还是在成长）。
//   零外部请求（只读日批回填的指数日线）⇒ 给 20s 足够。
//   ⚠️ 不传 date：这是"当前分化度"，历史由返回里的 `series` 自带（近 20 日滚动）。

export const getCrowdingWatch = (date) => http.get('/system/crowding-watch', { params: { date }, timeout: 30000 })

export const getKeyLevelWatch = (date) => http.get('/system/key-level-watch', { params: { date }, timeout: 30000 })

export const getAiBasketWatch = (date) => http.get('/system/ai-basket-watch', { params: { date }, timeout: 30000 })

export const getScenarioWatch = (date) => http.get('/system/scenario-watch', { params: { date }, timeout: 30000 })
// ★★ 2026-11-05：跨市场异动（外围指数极端异动 + 中国 vs 日韩区域背离）。
//   纯读宏观面板的共享 60s 缓存 ⇒ 通常 <100ms；冷缓存时首次抓取可能 10s+ ⇒ 给 20s。
//   ⚠️ 不传 date：这是**实时**状态（"今天外面在跌、我们在涨"），历史回放没有意义
//      —— 客户端本来就只展示最新快照。

export const getCrossMarketWatch = () => http.get('/system/cross-market-watch', { timeout: 20000 })
// ★★ 2026-11-16（用户："可以"）：跨市场危险信号（tell）检测器。
//   7 项清单：黄金/人民币/美元/A50/涨跌比/铜油/美债。
//   全灭 = 恐慌未扩散（箱体内轮动）；点亮越多越像流动性问题。
//   纯读宏观面板共享缓存 + 内存宽度 ⇒ 零新增外部请求，20s 足够。
export const getTellWatch = () => http.get('/system/tell-watch', { timeout: 20000 })
// ★★ 2026-11-22（用户："可以"，承接 11-20 复盘）：广度-指数背离观察器。
//   等权涨跌（你的体感）vs 流通市值加权（指数想表达的），差 = gap。
//   11-20 实测：等权 -4.18%、加权 -1.44%（沪指 -1.23%）、gap 2.74pp、92% 在跌
//   ⇒ 指数只跌 1.2% 时个股已失血 4%，"沪指跌 3% 才减仓"式的风控会失效。
//   日频（日批快照）⇒ 读库为主，20s 足够；老库慢时可能几秒。
export const getBreadthDivergence = (date) =>
  http.get('/system/breadth-divergence', { params: date ? { date } : {}, timeout: 30000 })
// ★★ 2026-11-22（用户："开始做 P0"）：推演登记 + 每日复盘。
//   周末登记情景/概率/验证项 → 日批自动对照实际数据 + LLM 修正 → 推企微。
//   纯复盘，不进决策链（与观察器同纪律）。
export const getThesisMetrics = () => http.get('/thesis/metrics')
// ★ 2026-11-22：粘贴自然语言推演 → LLM 解析成草案（**只解析不落库**，回填表单供审阅）。
//   会调 1 次 LLM，给 60s；解析完由用户改数字后自己点登记。
export const parseThesisText = (text) =>
  http.post('/thesis/parse', { text }, { timeout: 60000 })
export const getThesisScenarios = (params = {}) =>
  http.get('/thesis/scenarios', { params })
export const createThesisScenario = (payload) => http.post('/thesis/scenarios', payload)
export const getThesisDaily = (id, limit = 30) =>
  http.get(`/thesis/scenarios/${id}/daily`, { params: { limit } })
// ⚠️ review 会调 1 次 LLM（同步十几秒）⇒ 给 120s，不能沿用默认 30s
export const rerunThesisReview = (id, date) =>
  http.post(`/thesis/scenarios/${id}/review`,
    null, { params: date ? { date } : {}, timeout: 120000 })
export const archiveThesisScenario = (id) =>
  http.post(`/thesis/scenarios/${id}/archive`)

// ★★ 2026-11-28：动作协议卡（规则层，回答"怎么办"，与推演"会怎样"并列）。
//   起因：用户 11-27 复盘 —— 12:32 企微就收到了「平准基金」，到开盘还有 28 分钟，
//   却"不知所措"。不是信息失败，是**没有卡可翻**。
export const getActionProtocols = (activeOnly = false) =>
  http.get('/thesis/protocols', { params: { active_only: activeOnly } })
// 纯匹配：贴一段快讯/通稿，看会翻哪张卡（不落库、不推送、不调 LLM）
export const matchActionProtocol = (text) =>
  http.get('/thesis/protocols/match', { params: { text }, timeout: 20000 })
export const createActionProtocol = (payload) =>
  http.post('/thesis/protocols', payload)
export const archiveActionProtocol = (id) =>
  http.post(`/thesis/protocols/${id}/archive`)
// ★ 2026-11-28：今日重要快讯留账（matched/unmatched）——
//   unmatched = 有大事但没卡可翻，就是该补的缺口。
export const getProtocolNews = (params = {}) =>
  http.get('/thesis/protocols/news', { params })
// ★ 2026-11-28：从缺口快讯起草协议卡（**只起草不落库**，人工审阅后登记）。
//   ⚠️ 会调 1 次 LLM ⇒ 给 90s
export const draftProtocolFromNews = (payload) =>
  http.post('/thesis/protocols/draft', payload, { timeout: 90000 })
// ★★ 2026-11-16：铜裁决器 —— 油跌时区分"供给驱动(D1)"与"需求驱动(D2)"。
//   把 prompt 里只写给 LLM 的 D1/D2 判据显式化成确定性输出。
export const getCopperWatch = () => http.get('/system/copper-watch', { timeout: 20000 })
// ★★ 2026-11-05：事件 → 观察名单落点（主题 → 行业 → 标的当前状态）。
//   纯读 stock_facts_wide（日批产物），零外部请求 ⇒ 8s 足够。
//   ⚠️ 不传 date 时后端取事实表最新日，所以回放历史日也安全（会带 fallback 标记）。

export const getEventTargets = (params) => http.get('/system/event-targets', { params, timeout: 20000 })

export const getEventThemes = () => http.get('/system/event-targets/themes', { timeout: 20000 })
// ★★ 2026-11-05：震荡区间识别（每只票的箱体上下沿/位置/持续天数）。
//   纯读 range_watch 表（日批产物）⇒ 零外部请求。返回含 `mine`（我的持仓切片）。

export const getRangeWatch = (params) => http.get('/system/range-watch', { params, timeout: 20000 })
// ★★ 2026-11-25（用户："每天都要盯好几个股票，顾不过来，也不知道支撑位和阻力位，
//   上涨是正常的还是套人的"）：**持仓/指定个股的分级观察**。
//   · 支撑/阻力 = 箱体下沿/上沿（`range_watch` 日批口径）
//   · `pos_in_range` 用**实时价**重算 ⇒ 会随盘中变化，这是期望行为
//   · `rise` = 上涨质量证据（量能/主力/位置/上影线）—— **观察项，不是买卖建议**
//   · `codes` 为空 ⇒ 后端自动取当前持仓；传数组则只观察这些
//   ⚠️ 与 `/user/exit-advice` 的分工：那边是**行动**（清仓/减仓/持有），
//      这边是**证据**（涨得实不实、离支撑阻力多远）。两者不应互相覆盖。
export const getTierWatch = (codes, live = true) =>
  http.get('/system/tier-watch', {
    params: {
      ...((codes && codes.length) ? { codes: codes.join(',') } : {}),
      live,
    },
    timeout: 30000,
  })
// ★★ 2026-11-05：人物立场时间线（金十「人物讲话」，T+1 更新，非实时）。
//   ⚠️ 这是**历史**纵览；当日实时看快讯（flash_brief）。

export const getPeopleTimeline = (params) => http.get('/system/people-watch', { params, timeout: 20000 })

export const getPeopleList = () => http.get('/system/people-watch/people', { timeout: 20000 })
// ★ 2026-10-30：金十每日汇总（A股盘前/回顾/存储/ETF）—— 原文直出，不经 LLM 改写

export const getJin10Tidy = (date) => http.get('/system/jin10-tidy', { params: { date }, timeout: 30000 })
// ★ 2026-11-01：刷新（抓最新 + LLM 跨条目归纳）。
//   summarize=false 只抓数据不烧 LLM；force=true 忽略归纳缓存。
//   ★ 2026-11-04：`compute()` 改为「栏目 × 日期」抓最近 7 天 = 28 个请求
//     （不再按 id 扫描）。超时仍留 180s。

export const refreshJin10Tidy = (date, summarize = true, force = false) =>
  http.post('/system/jin10-tidy/refresh', null,
    { params: { date, summarize, force }, timeout: 180000 })
// ★ 2026-10-30：组合风险档（滞胀交易状态位）—— 只标注，不覆盖宏观分

export const getStagflation = (date) => http.get('/system/stagflation', { params: { date }, timeout: 30000 })
// ★ 2026-10-24：观察器 AI 解读（LLM 同步调用，按日缓存 + 数据指纹；默认点按钮才请求）

export const getObserverReview = (date, refresh = false) => http.get('/system/observer-review', { params: { date, refresh }, timeout: 120000 })
