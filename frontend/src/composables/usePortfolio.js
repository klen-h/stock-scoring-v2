import { ref, computed } from 'vue'

// ============================================================
//  持仓管理组合式函数（全局单例）
// ============================================================
//  职责：
//    1. 持仓数据 CRUD + localStorage 持久化
//    2. 盈亏计算（纯函数）
//    3. 盘中提醒判定（止损/止盈/移动止盈/评分信号）
//    4. 导入导出 JSON
//
//  数据结构（每个持仓）：
//    {
//      code: '000001',           // 股票代码（主键）
//      name: '平安银行',          // 名称（添加时从搜索结果取，刷新时用实时数据覆盖）
//      cost: 12.5,               // 成本价（元）
//      shares: 1000,             // 股数
//      note: '长线',             // 备注（可选）
//      high_water_mark: 13.2,    // 持仓期最高价（用于移动止盈，仅向上更新）
//      created_at: 1700000000000 // 添加时间戳
//    }
// ============================================================

// ── 可配置阈值（后续可改造成设置面板）──
export const ALERT_CONFIG = {
  stopLossPct: -8,        // 硬止损：浮亏 ≤ -8% → 建议止损卖出
  takeProfitPct: 30,      // 硬止盈：浮盈 ≥ +30% → 建议止盈清仓
  trailingDrawdownPct: 8, // 移动止盈：从最高点回撤 ≥ 8%（且浮盈为正）→ 建议减仓锁利
  scoreSellThreshold: 35, // 评分 ≤ 此值 → 评分恶化，关注卖出
  scoreWeakThreshold: 45, // 评分 ≤ 此值（且 > 35）→ 评分偏弱，考虑减仓
  surgeUpPct: 5,          // 急涨：单日涨幅 ≥ +5% → 推送通知（仅通知，不入 UI 建议操作列）
  surgeDownPct: -5,       // 急跌：单日跌幅 ≤ -5% → 推送通知
}

// ── 刷新策略：区分交易时段 / 非交易时段 ──
export const REFRESH_CONFIG = {
  tradingInterval: 30,      // 交易时段刷新间隔（秒）
  nonTradingInterval: 300,  // 非交易时段刷新间隔（秒，5分钟）
}
// A股交易时段（24小时制，本地时间）
//   上午 9:30-11:30，下午 13:00-15:00，仅周一至周五
const TRADING_SESSIONS = [
  { start: [9, 30], end: [11, 30] },
  { start: [13, 0], end: [15, 0] },
]

/**
 * 判断当前是否为 A股交易时段（含午休排除）。
 * 注意：这里用浏览器本地时间，假设用户在中国时区（UTC+8）。
 *       若部署给海外用户，需改用服务器时间或显式时区换算。
 */
export function isTradingTime(date = new Date()) {
  const day = date.getDay()
  if (day === 0 || day === 6) return false  // 周末休市
  const minutes = date.getHours() * 60 + date.getMinutes()  // 当天总分钟数
  return TRADING_SESSIONS.some(({ start, end }) => {
    const s = start[0] * 60 + start[1]
    const e = end[0] * 60 + end[1]
    return minutes >= s && minutes <= e
  })
}

/** 获取当前应使用的刷新间隔（秒） */
export function getRefreshInterval() {
  return isTradingTime() ? REFRESH_CONFIG.tradingInterval : REFRESH_CONFIG.nonTradingInterval
}

const STORAGE_KEY = 'portfolio_stocks'

// 全局单例：所有引用本模块的组件共享同一份 positions（模块级变量天然单例）
const positions = ref(loadFromStorage())

// ── localStorage 读写 ──
function loadFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? JSON.parse(raw) : []
  } catch {
    return []
  }
}

function saveToStorage() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(positions.value))
  } catch (e) {
    console.error('持仓保存失败', e)
  }
}

// ── 持仓 CRUD ──
export function addPosition({ code, name, cost, shares, note = '' }) {
  // 去重：同一代码已存在则更新（而非新增）
  const existing = positions.value.find(p => p.code === code)
  if (existing) {
    Object.assign(existing, { cost, shares, note, name: name || existing.name })
  } else {
    positions.value.push({
      code,
      name: name || code,
      cost: Number(cost),
      shares: Number(shares),
      note,
      // 初始最高点设为成本价（保守起见，后续刷新会被真实高价覆盖）
      high_water_mark: Number(cost),
      created_at: Date.now(),
    })
  }
  saveToStorage()
}

export function removePosition(code) {
  const idx = positions.value.findIndex(p => p.code === code)
  if (idx >= 0) {
    positions.value.splice(idx, 1)
    saveToStorage()
  }
}

export function updatePosition(code, fields) {
  const p = positions.value.find(p => p.code === code)
  if (p) {
    Object.assign(p, fields)
    saveToStorage()
  }
}

// ── 盈亏计算（纯函数，无副作用，便于测试）──
export function calcProfit(cost, shares, price) {
  const c = Number(cost) || 0
  const s = Number(shares) || 0
  const pr = Number(price) || 0
  const costValue = c * s        // 投入成本
  const marketValue = pr * s     // 当前市值
  const profit = marketValue - costValue               // 浮动盈亏额（正=赚 负=亏）
  const profitPct = costValue > 0 ? (profit / costValue) * 100 : 0  // 浮动盈亏率 %
  return {
    costValue,
    marketValue,
    profit,
    profitPct,
  }
}

// ── 盘中提醒判定 ──
// 返回提醒数组（可能多条，按 level 排序：danger > warning > info）
// 每条：{ level: 'danger'|'warning'|'info', rule: string, action: string, message: string }
export function evaluateAlerts(position, realtime, score) {
  const alerts = []
  if (!position) return alerts

  const price = realtime?.price
  // 无实时价时无法判断价格类规则
  if (price && price > 0) {
    const { profitPct } = calcProfit(position.cost, position.shares, price)

    // 规则1：硬止损
    if (profitPct <= ALERT_CONFIG.stopLossPct) {
      alerts.push({
        level: 'danger',
        rule: '硬止损',
        action: '建议止损卖出',
        message: `浮亏 ${profitPct.toFixed(2)}%，触及 ${ALERT_CONFIG.stopLossPct}% 止损线`,
      })
    }

    // 规则2：硬止盈
    if (profitPct >= ALERT_CONFIG.takeProfitPct) {
      alerts.push({
        level: 'warning',
        rule: '硬止盈',
        action: '建议止盈清仓',
        message: `浮盈 ${profitPct.toFixed(2)}%，达到 ${ALERT_CONFIG.takeProfitPct}% 止盈目标`,
      })
    }

    // 规则3：移动止盈（从持仓期最高点回撤，且当前仍有盈利）
    const hwm = position.high_water_mark || position.cost
    if (hwm > 0 && profitPct > 0) {
      const drawdownPct = ((hwm - price) / hwm) * 100
      if (drawdownPct >= ALERT_CONFIG.trailingDrawdownPct) {
        alerts.push({
          level: 'warning',
          rule: '移动止盈',
          action: '建议减仓锁利',
          message: `从阶段高点 ${hwm.toFixed(2)} 回撤 ${drawdownPct.toFixed(2)}%，保住利润`,
        })
      }
    }
  }

  // 规则4：评分信号（评分接口可能为空，做容错）
  const totalScore = score?.total_score
  if (typeof totalScore === 'number') {
    if (totalScore <= ALERT_CONFIG.scoreSellThreshold) {
      alerts.push({
        level: 'danger',
        rule: '评分恶化',
        action: '关注卖出',
        message: `综合评分 ${totalScore}（≤${ALERT_CONFIG.scoreSellThreshold}），多因子转弱`,
      })
    } else if (totalScore <= ALERT_CONFIG.scoreWeakThreshold) {
      alerts.push({
        level: 'info',
        rule: '评分偏弱',
        action: '考虑减仓',
        message: `综合评分 ${totalScore}，趋势/资金面偏弱`,
      })
    }
  }

  // 优先级排序：danger > warning > info
  const order = { danger: 0, warning: 1, info: 2 }
  alerts.sort((a, b) => order[a.level] - order[b.level])
  return alerts
}

// ── 高水位维护：刷新行情后更新持仓期最高价（仅向上更新）──
export function updateHighWaterMark(code, price) {
  const p = positions.value.find(p => p.code === code)
  if (p && price && price > (p.high_water_mark || 0)) {
    p.high_water_mark = price
    saveToStorage()
  }
}

// ── 导入导出 JSON ──
export function exportJSON() {
  const data = {
    version: 1,
    exported_at: new Date().toISOString(),
    positions: positions.value,
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `portfolio_${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

export function importJSON(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target.result)
        const arr = Array.isArray(data) ? data : data.positions
        if (!Array.isArray(arr)) throw new Error('文件格式不正确')
        // 合并导入：按 code 去重，新数据覆盖旧数据
        const map = new Map(positions.value.map(p => [p.code, p]))
        for (const item of arr) {
          if (item.code && item.cost && item.shares) {
            map.set(item.code, { ...map.get(item.code), ...item })
          }
        }
        positions.value = Array.from(map.values())
        saveToStorage()
        resolve(positions.value.length)
      } catch (err) {
        reject(err)
      }
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsText(file)
  })
}

// ── 汇总统计（computed）──
// 入参：每只持仓的实时行情映射 { [code]: { price, ... } }
export function useSummary(realtimeMap) {
  return computed(() => {
    let totalCost = 0
    let totalMarketValue = 0
    let triggeredCount = 0  // 触发任意提醒的数量
    for (const p of positions.value) {
      totalCost += p.cost * p.shares
      const rt = realtimeMap.value[p.code]
      if (rt && rt.price > 0) {
        totalMarketValue += rt.price * p.shares
        // 是否触发提醒（用 evaluateAlerts 判定，有 danger/warning 即算）
        const alerts = evaluateAlerts(p, rt, null)
        if (alerts.some(a => a.level === 'danger' || a.level === 'warning')) {
          triggeredCount++
        }
      } else {
        // 无实时价时按成本计市值，避免汇总抖动
        totalMarketValue += p.cost * p.shares
      }
    }
    const totalProfit = totalMarketValue - totalCost
    const totalProfitPct = totalCost > 0 ? (totalProfit / totalCost) * 100 : 0
    return {
      totalCost,
      totalMarketValue,
      totalProfit,
      totalProfitPct,
      triggeredCount,
      count: positions.value.length,
    }
  })
}

// 导出全局 positions 供组件使用
export function usePortfolio() {
  return { positions }
}
