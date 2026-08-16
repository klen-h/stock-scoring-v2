import { ref, computed } from 'vue'
import { ALERT_CONFIG } from './usePortfolio'

// ============================================================
//  自选股 / 观察列表（组合式函数，全局单例）
// ============================================================
//  与持仓的核心区别：
//    持仓 = 已买入，关心"何时卖"（止损止盈，danger 级）
//    自选 = 未买入，关心"何时买"（价格到位、评分转强，info 级机会）
//
//  数据结构（每个自选项）：
//    {
//      code: '000001',
//      name: '平安银行',
//      target_price: 12.5,    // 目标买入价（可选；不填则只看评分提醒）
//      note: '等回调到12.5',
//      created_at: 1700000000000
//    }
// ============================================================

// 自选股专用阈值（复用 ALERT_CONFIG 的部分，新增买点阈值）
const WATCH_CONFIG = {
  surgeDownPct: ALERT_CONFIG.surgeDownPct,   // 急跌机会：单日跌幅 ≤ -5% → 可能现机会
  scoreBuyThreshold: 65,                     // 评分转强：≥ 65（买入信号）→ 关注
  scoreSellThreshold: ALERT_CONFIG.scoreSellThreshold,  // 评分走弱：≤ 35 → 可移出自选
}

const STORAGE_KEY = 'watchlist_stocks'

// 全局单例（模块级变量天然单例，所有组件共享）
const watchlist = ref(loadFromStorage())

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
    localStorage.setItem(STORAGE_KEY, JSON.stringify(watchlist.value))
  } catch (e) {
    console.error('自选股保存失败', e)
  }
}

// ── CRUD ──
export function addWatch({ code, name, target_price, note = '' }) {
  const existing = watchlist.value.find(w => w.code === code)
  if (existing) {
    // 已存在则更新（允许改目标价）
    Object.assign(existing, {
      target_price: target_price ?? existing.target_price,
      note: note || existing.note,
      name: name || existing.name,
    })
  } else {
    watchlist.value.push({
      code,
      name: name || code,
      target_price: target_price ? Number(target_price) : null,
      note,
      created_at: Date.now(),
    })
  }
  saveToStorage()
}

export function removeWatch(code) {
  const idx = watchlist.value.findIndex(w => w.code === code)
  if (idx >= 0) {
    watchlist.value.splice(idx, 1)
    saveToStorage()
  }
}

export function updateWatch(code, fields) {
  const w = watchlist.value.find(w => w.code === code)
  if (w) {
    Object.assign(w, fields)
    saveToStorage()
  }
}

// ── 买点提醒判定（自选股核心逻辑，与持仓的卖点逻辑完全独立）──
// 返回提醒数组。全部 info 级（机会提示，非风险警报）。
export function evaluateBuySignals(item, realtime, score) {
  const alerts = []
  if (!item) return alerts

  const price = realtime?.price

  // 规则1：到达目标价（设了目标价时）
  if (price && price > 0 && item.target_price && Number(item.target_price) > 0) {
    if (price <= Number(item.target_price)) {
      const dev = ((price - item.target_price) / item.target_price * 100).toFixed(2)
      alerts.push({
        level: 'info',
        rule: '到达目标价',
        action: '可考虑买入',
        message: `现价 ${price} 已${dev < 0 ? '跌破' : '到达'}目标价 ${item.target_price}`,
      })
    }
  }

  // 规则2：急跌机会（单日大跌可能现买点）
  const chg = realtime?.change_pct
  if (chg != null && chg <= WATCH_CONFIG.surgeDownPct) {
    alerts.push({
      level: 'info',
      rule: '急跌机会',
      action: '可能出现机会',
      message: `今日跌幅 ${chg.toFixed(2)}%，短期超跌`,
    })
  }

  // 规则3：评分转强（买入信号）
  const totalScore = score?.total_score
  if (typeof totalScore === 'number') {
    if (totalScore >= WATCH_CONFIG.scoreBuyThreshold) {
      alerts.push({
        level: 'info',
        rule: '评分转强',
        action: '关注买点',
        message: `综合评分 ${totalScore}（≥${WATCH_CONFIG.scoreBuyThreshold}），信号：${score.signal || '买入'}`,
      })
    } else if (totalScore <= WATCH_CONFIG.scoreSellThreshold) {
      // 规则4：评分走弱（提示清理）
      alerts.push({
        level: 'info',
        rule: '评分走弱',
        action: '可考虑移出',
        message: `综合评分 ${totalScore}（≤${WATCH_CONFIG.scoreSellThreshold}），趋势转弱`,
      })
    }
  }

  return alerts
}

// ── 距目标价计算（用于表格展示）──
// 返回 { deviation: 百分比, reached: 是否已到达 }
export function calcTargetDeviation(item, price) {
  if (!item.target_price || !price || price <= 0) {
    return { deviation: null, reached: false }
  }
  const deviation = ((price - item.target_price) / item.target_price) * 100
  return { deviation, reached: price <= item.target_price }
}

// ── 导入导出 JSON ──
export function exportWatchlistJSON() {
  const data = {
    version: 1,
    type: 'watchlist',
    exported_at: new Date().toISOString(),
    items: watchlist.value,
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `watchlist_${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

export function importWatchlistJSON(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target.result)
        const arr = Array.isArray(data) ? data : (data.items || data.positions)
        if (!Array.isArray(arr)) throw new Error('文件格式不正确')
        const map = new Map(watchlist.value.map(w => [w.code, w]))
        for (const item of arr) {
          if (item.code) {
            map.set(item.code, { ...map.get(item.code), ...item })
          }
        }
        watchlist.value = Array.from(map.values())
        saveToStorage()
        resolve(watchlist.value.length)
      } catch (err) {
        reject(err)
      }
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsText(file)
  })
}

// ── 汇总统计（computed）──
export function useWatchlistSummary(realtimeMap, scoreMap) {
  return computed(() => {
    let reachedTarget = 0   // 到达目标价数
    let scoreStrong = 0     // 评分转强数
    for (const w of watchlist.value) {
      const rt = realtimeMap.value[w.code]
      const dev = calcTargetDeviation(w, rt?.price)
      if (dev.reached) reachedTarget++
      const sc = scoreMap.value[w.code]
      if (sc?.total_score >= WATCH_CONFIG.scoreBuyThreshold) scoreStrong++
    }
    return {
      count: watchlist.value.length,
      reachedTarget,
      scoreStrong,
    }
  })
}

// 导出全局 watchlist + 配置
export function useWatchlist() {
  return { watchlist, WATCH_CONFIG }
}
