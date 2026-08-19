import { ref } from 'vue'
import { evaluateAlerts, ALERT_CONFIG } from './usePortfolio'
import { evaluateBuySignals } from './useWatchlist'
import { evaluatePlanStatus } from './useTradePlans'

// 趋势健康度恶化阈值：从上次通知时的分数算起，下降 ≥ 此值才通知
const TREND_HEALTH_DROP_THRESHOLD = 2

// ============================================================
//  浏览器桌面通知模块
// ============================================================
//  职责：
//    1. Notification 权限管理（用户主动点"开启通知"按钮时申请）
//    2. 通知发送（封装 new Notification，按类型设图标/行为）
//    3. 状态 diff 去重（核心防骚扰：只在"进入"事件时推，"停留"期间不重复）
//    4. 急涨急跌判定（单日涨跌幅超阈值 → 推送，不入 evaluateAlerts）
//
//  触发时机：无论页面可见与否都推（用户已确认）
//  去重策略：状态变化才推（用户已确认）
//    - 持有 → 止损：推 1 次
//    - 停留在止损期间：不重复推
//    - 止损 → 持有 → 止损：会推 2 次（每次"进入"都推，符合预期）
// ============================================================

// ── 支持检测 + 权限状态（响应式，供 UI 绑定）──
export const supported = typeof window !== 'undefined' && 'Notification' in window
export const permission = ref(supported ? Notification.permission : 'denied')
// 用户开关：即使浏览器授权了，用户也能在本应用内单独关闭（不撤回系统权限，仅停止发送）
// 持久化到 localStorage，刷新页面后保持用户选择
const NOTIF_ENABLED_KEY = 'stock_scoring_notif_enabled'
export const enabled = ref(loadNotifEnabled())

// ── 通知开关持久化 ──
function loadNotifEnabled() {
  try {
    return localStorage.getItem(NOTIF_ENABLED_KEY) === 'true'
  } catch {
    return false
  }
}
function saveNotifEnabled() {
  try {
    localStorage.setItem(NOTIF_ENABLED_KEY, enabled.value ? 'true' : 'false')
  } catch (e) {
    console.error('通知开关保存失败', e)
  }
}

/**
 * 申请通知权限（由用户点击"开启通知"按钮触发，不要自动调用）。
 * 返回 true 表示获得授权。
 */
export async function requestPermission() {
  if (!supported) return false
  if (Notification.permission === 'granted') {
    permission.value = 'granted'
    enabled.value = true
    saveNotifEnabled()
    return true
  }
  if (Notification.permission === 'denied') {
    permission.value = 'denied'
    return false
  }
  // default 状态：弹系统授权框
  const result = await Notification.requestPermission()
  permission.value = result
  if (result === 'granted') {
    enabled.value = true
    saveNotifEnabled()
    // 发一条欢迎通知，让用户确认权限生效
    notify({
      title: '🔔 通知已开启',
      body: '将持续监控您的持仓，重要事件会在此推送。',
      type: 'info',
      tag: 'system-welcome',
    })
    return true
  }
  return false
}

/** 用户在应用内关闭通知（不撤回系统权限，下次开启无需再授权） */
export function disable() {
  enabled.value = false
  saveNotifEnabled()
}

// ── 状态 diff：记录每只票当前已通知的事件集合 ──
// 结构：Map<code, Set<rule>>
//   - 每次刷新计算"当前事件集合"，与上次对比
//   - 新增事件 → 推通知
//   - 消失事件 → 从记录移除（下次再触发会重新推）
const notifiedState = new Map()

/**
 * 主入口：遍历持仓，计算 alerts + 急涨急跌，diff 后发送通知。
 * 在 Portfolio.vue 的 refresh 末尾调用。
 *
 * 参数：
 *   positions:   持仓数组
 *   realtimeMap: { [code]: { price, change_pct, ... } }
 *   scoreMap:    { [code]: { total_score, signal, ... } }
 */
export function checkAndNotify(positions, realtimeMap, scoreMap) {
  // 未开启 / 未授权 → 跳过（但仍更新 notifiedState，避免开启瞬间补发一堆历史通知）
  const canSend = supported && enabled.value && permission.value === 'granted'

  for (const p of positions) {
    const realtime = realtimeMap[p.code]
    const score = scoreMap[p.code]

    // 计算本次该票的所有触发事件（alerts + 急涨急跌）
    const events = []
    if (realtime) {
      // 复用现有评分规则（止损/止盈/移动止盈/评分恶化/评分偏弱）
      events.push(...evaluateAlerts(p, realtime, score))
      // 急涨急跌（仅用于通知，不入 evaluateAlerts）
      const surge = checkSurge(realtime)
      if (surge) events.push(surge)
    }

    // 趋势健康度恶化检测（独立状态跟踪，需要对比历史值）
    const thEvent = checkTrendHealthDrop(p.code, p.name, score)
    if (thEvent) events.push(thEvent)

    // 当前事件的 rule 集合（用于 diff）
    const currentRules = new Set(events.map(e => e.rule))
    const prevRules = notifiedState.get(p.code) || new Set()

    if (canSend) {
      // 找出"新增"的事件（在 current 但不在 prev）→ 推通知
      for (const e of events) {
        if (!prevRules.has(e.rule)) {
          sendAlertNotification(p, e)
        }
      }
    }

    // 更新该票的状态记录（无论是否发送，都要更新，保证 diff 正确）
    notifiedState.set(p.code, currentRules)
  }

  // 清理已删除持仓的状态记录（防止 Map 无限增长）
  const validCodes = new Set(positions.map(p => p.code))
  for (const code of notifiedState.keys()) {
    if (!validCodes.has(code)) notifiedState.delete(code)
  }
  // 同步清理趋势健康度跟踪状态
  for (const code of trendHealthTracker.keys()) {
    if (!validCodes.has(code)) trendHealthTracker.delete(code)
  }
}

// ── 自选股专用 diff 状态（独立 Map，避免和持仓的 notifiedState 冲突）──
// 持仓和自选可能有同一只票，但 rule 不同（持仓="止损"，自选="到达目标价"），
// 用独立 Map 保证两者的去重互不干扰。
const notifiedWatchlistState = new Map()

/**
 * 自选股买点通知检查（与持仓的 checkAndNotify 完全独立）。
 * 在 Watchlist.vue 的 refresh 末尾调用。
 * 提醒全是 info 级（机会提示），普通通知 8 秒自动关，不 requireInteraction。
 */
export function checkWatchlistNotify(watchlist, realtimeMap, scoreMap) {
  const canSend = supported && enabled.value && permission.value === 'granted'

  for (const w of watchlist) {
    const realtime = realtimeMap[w.code]
    const score = scoreMap[w.code]
    const signals = evaluateBuySignals(w, realtime, score)

    const currentRules = new Set(signals.map(s => s.rule))
    const prevRules = notifiedWatchlistState.get(w.code) || new Set()

    if (canSend) {
      for (const s of signals) {
        if (!prevRules.has(s.rule)) {
          sendWatchlistNotification(w, s)
        }
      }
    }
    notifiedWatchlistState.set(w.code, currentRules)
  }

  // 清理已删除自选的状态记录
  const validCodes = new Set(watchlist.map(w => w.code))
  for (const code of notifiedWatchlistState.keys()) {
    if (!validCodes.has(code)) notifiedWatchlistState.delete(code)
  }
}

/**
 * 发送自选股买点通知（info 级，普通通知）。
 */
function sendWatchlistNotification(item, signal) {
  const tag = `watch-${item.code}-${signal.rule}`
  notify({
    title: `${item.name}(${item.code}) ${signal.rule}`,
    body: `${signal.action}：${signal.message}`,
    type: 'info',
    tag,
    // 买点是机会提示，不是紧急风险，用默认 8 秒自动关（不 requireInteraction）
  })
}

// ── 交易计划专用状态记忆（记录每个计划上次的状态，用于检测变化）──
const planPrevStatus = new Map()  // id → 上次 status

/**
 * 交易计划状态变化通知。
 * 调用前需先对每个 plan 调用 evaluatePlanStatus（在 TradePlans.vue 的 refresh 里做），
 * 这里只负责"状态变化时发通知"。
 *
 * 触发的通知：
 *   waiting → hit：触及买点，可考虑买入（info）
 *   hit → targeted：达目标，验证成功 ✓（info）
 *   hit → stopped：破止损，验证失败 ✗（warning，需关注）
 */
export function checkPlansNotify(plans) {
  const canSend = supported && enabled.value && permission.value === 'granted'
  const validIds = new Set(plans.map(p => p.id))

  for (const p of plans) {
    const prev = planPrevStatus.get(p.id)
    // 首次记录（prev 为空）不发通知，避免新建计划瞬间补发
    if (prev !== undefined && prev !== p.status && canSend) {
      sendPlanStatusNotification(p, prev, p.status)
    }
    planPrevStatus.set(p.id, p.status)
  }

  // 清理已删除计划的状态记忆
  for (const id of planPrevStatus.keys()) {
    if (!validIds.has(id)) planPrevStatus.delete(id)
  }
}

function sendPlanStatusNotification(plan, oldStatus, newStatus) {
  let title, body, type
  if (oldStatus === 'waiting' && newStatus === 'hit') {
    title = `${plan.name}(${plan.code}) 触及买点`
    body = `现价已到买点 ${plan.buy_price}，可考虑买入。止损 ${plan.stop_loss}，目标 ${plan.target}`
    type = 'info'
  } else if (newStatus === 'targeted') {
    title = `${plan.name}(${plan.code}) 达到目标 ✓`
    body = `价格触及目标 ${plan.target}，本次计划验证成功`
    type = 'info'
  } else if (newStatus === 'stopped') {
    title = `${plan.name}(${plan.code}) 触及止损 ✗`
    body = `价格跌破止损 ${plan.stop_loss}，本次计划验证失败，注意 T+1 卖压`
    type = 'warning'
  } else if (newStatus === 'expired') {
    title = `${plan.name}(${plan.code}) 错过买点`
    body = `价格已超过目标，未给买点机会`
    type = 'info'
  } else {
    return
  }
  notify({
    title,
    body,
    type,
    tag: `plan-${plan.id}-${newStatus}`,
  })
}

/**
 * 急涨急跌判定（仅通知用，不进 evaluateAlerts）。
 * 返回 alert 对象或 null。
 */
function checkSurge(realtime) {
  const chg = realtime?.change_pct
  if (chg == null) return null
  if (chg >= ALERT_CONFIG.surgeUpPct) {
    return {
      type: 'surge',
      level: 'info',
      rule: '急涨',
      action: '关注',
      message: `今日涨幅 ${chg.toFixed(2)}%，超过 +${ALERT_CONFIG.surgeUpPct}% 阈值`,
    }
  }
  if (chg <= ALERT_CONFIG.surgeDownPct) {
    return {
      type: 'surge',
      level: 'warning',
      rule: '急跌',
      action: '关注',
      message: `今日跌幅 ${chg.toFixed(2)}%，低于 ${ALERT_CONFIG.surgeDownPct}% 阈值`,
    }
  }
  return null
}

// ── 趋势健康度恶化跟踪（独立状态，用于检测分数下降）──
// 结构：Map<code, { lastNotifiedScore, ceiling }>
//   ceiling: 上次通知后见过的最高分（分数恢复后抬高天花板，下次再跌还能通知）
const trendHealthTracker = new Map()

/**
 * 趋势健康度恶化/好转检测。
 * 逻辑：
 *   - 恶化：从上次通知时的分数算起，下降 ≥ 2 分 → 通知
 *   - 好转：从上次通知时的分数算起，上升 ≥ 2 分 → 通知
 *   - 通知后更新 lastNotifiedScore = 当前分数
 *   - 反向恢复后再次变化，会再次触发（ceiling/floor 机制）
 */
function checkTrendHealthDrop(code, name, score) {
  const currentScore = score?.trend_health?.score
  if (typeof currentScore !== 'number' || currentScore <= 0) return null

  let tracker = trendHealthTracker.get(code)
  if (!tracker) {
    // 首次见到这只票，记录基线，不通知
    trendHealthTracker.set(code, { lastNotifiedScore: currentScore, ceiling: currentScore, floor: currentScore })
    return null
  }

  // 更新天花板（分数恢复时抬高，保证下次下跌还能触发）
  if (currentScore > tracker.ceiling) {
    tracker.ceiling = currentScore
    tracker.lastNotifiedScore = currentScore
  }
  // 更新地板（分数继续下跌时压低，保证下次回升还能触发）
  if (currentScore < tracker.floor) {
    tracker.floor = currentScore
  }

  // ── 恶化检测 ──
  const drop = tracker.lastNotifiedScore - currentScore
  if (drop >= TREND_HEALTH_DROP_THRESHOLD) {
    const verdict = score.trend_health.verdict || '趋势恶化'
    tracker.lastNotifiedScore = currentScore
    tracker.floor = currentScore
    return {
      type: 'trend',
      level: drop >= 3 ? 'danger' : 'warning',
      rule: '趋势恶化',
      action: '关注持仓',
      message: `趋势健康度 ${tracker.ceiling}→${currentScore}（${verdict}），${drop}个维度转差`,
    }
  }

  // ── 好转检测 ──
  const rise = currentScore - tracker.lastNotifiedScore
  if (rise >= TREND_HEALTH_DROP_THRESHOLD) {
    const verdict = score.trend_health.verdict || '趋势健康'
    tracker.lastNotifiedScore = currentScore
    tracker.ceiling = currentScore
    return {
      type: 'trend-good',
      level: 'info',
      rule: '趋势好转',
      action: '关注',
      message: `趋势健康度 ${tracker.floor}→${currentScore}（${verdict}），${rise}个维度改善`,
    }
  }

  return null
}

/**
 * 发送单条持仓警报通知。
 */
function sendAlertNotification(position, alert) {
  const icon = typeIcon(alert.type || alert.level)
  // tag 用于浏览器去重：同 tag 的新通知会替换旧的，避免气泡堆积
  const tag = `pos-${position.code}-${alert.rule}`
  // danger 级别（止损/评分恶化）不自动消失，强制用户看到
  const requireInteraction = alert.level === 'danger'

  const notif = notify({
    title: `${position.name}(${position.code}) ${alert.rule}`,
    body: `${alert.action}：${alert.message}`,
    icon,
    tag,
    requireInteraction,
  })

  // 点击通知 → 聚焦窗口并跳转到该股详情
  if (notif) {
    notif.onclick = () => {
      window.focus()
      notif.close()
      // 用 hash 跳转（避免耦合 router 实例；Portfolio.vue 已用 vue-router）
      if (location.pathname !== '/portfolio') {
        location.hash = ''
      }
      // 直接跳详情页
      window.location.href = `${window.location.origin}/stock/${position.code}`
    }
  }
}

/**
 * 底层通知发送封装。
 * @returns {Notification|null} 通知实例（不支持或未授权时返回 null）
 */
export function notify({ title, body = '', icon = null, tag = '', requireInteraction = false, type = 'info' }) {
  if (!supported || permission.value !== 'granted') return null
  try {
    const notif = new Notification(title, {
      body,
      icon: icon || typeIcon(type),
      tag,
      requireInteraction,
    })
    // 非 requireInteraction 的通知 8 秒后自动关闭（部分浏览器不会自动消失）
    if (!requireInteraction) {
      setTimeout(() => notif.close(), 8000)
    }
    return notif
  } catch (e) {
    console.warn('通知发送失败', e)
    return null
  }
}

/**
 * 不同类型通知的 emoji 图标（转 data URL，避免外部资源依赖）。
 * 用 SVG 画一个圆 + emoji 文字，简单可靠。
 */
function typeIcon(type) {
  const map = {
    danger: { emoji: '⚠️', bg: '#ef4444' },      // 红
    warning: { emoji: '⚡', bg: '#f59e0b' },      // 橙
    info: { emoji: '📢', bg: '#3b82f6' },         // 蓝
    surge: { emoji: '📈', bg: '#8b5cf6' },        // 紫
    trend: { emoji: '📉', bg: '#ec4899' },        // 粉（趋势恶化）
    'trend-good': { emoji: '✅', bg: '#10b981' }, // 绿（趋势好转）
  }
  const { emoji, bg } = map[type] || map.info
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">
    <circle cx="32" cy="32" r="30" fill="${bg}"/>
    <text x="32" y="44" font-size="36" text-anchor="middle">${emoji}</text>
  </svg>`
  return 'data:image/svg+xml,' + encodeURIComponent(svg)
}
