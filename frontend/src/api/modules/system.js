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
// ★★ 2026-11-05：事件 → 观察名单落点（主题 → 行业 → 标的当前状态）。
//   纯读 stock_facts_wide（日批产物），零外部请求 ⇒ 8s 足够。
//   ⚠️ 不传 date 时后端取事实表最新日，所以回放历史日也安全（会带 fallback 标记）。

export const getEventTargets = (params) => http.get('/system/event-targets', { params, timeout: 20000 })

export const getEventThemes = () => http.get('/system/event-targets/themes', { timeout: 20000 })
// ★★ 2026-11-05：震荡区间识别（每只票的箱体上下沿/位置/持续天数）。
//   纯读 range_watch 表（日批产物）⇒ 零外部请求。返回含 `mine`（我的持仓切片）。

export const getRangeWatch = (params) => http.get('/system/range-watch', { params, timeout: 20000 })
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
