import { ref, computed } from 'vue'
import {
  getUserPlans,
  upsertUserPlan,
  deleteUserPlan
} from '../api/index.js'

// ============================================================
//  交易计划（买点跟踪 + T+1 验证）
// ============================================================
//  与自选股的区别：
//    自选 = "盯着等机会"（轻量观察）
//    交易计划 = "已分析，有明确买点/止损/目标，要验证对错"（重量决策记录）
//
//  核心是状态机：waiting → hit → targeted/stopped
//  每次刷新自动判定状态转移 + 更新跟踪数据
//
//  数据结构：
//    {
//      id, code, name,
//      buy_price, stop_loss, target,    // 计划三要素
//      reason, expected,                // 理由 + 预期
//      created_at,
//      status, hit_at,                  // 状态机
//      t1_close,                        // T+1 次日收盘近似
//      max_high_after_hit, min_low_after_hit
//    }
// ============================================================

const STORAGE_KEY = 'trade_plans'
// 是否启用 expired 状态（涨过头判定）。默认关闭防误判。
const ENABLE_EXPIRED = false

// 全局单例
const plans = ref(loadFromStorage())

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
    localStorage.setItem(STORAGE_KEY, JSON.stringify(plans.value))
  } catch (e) {
    console.error('交易计划保存失败', e)
  }
}

// ── CRUD ──
export function addPlan({ code, name, buy_price, stop_loss, target, reason = '', expected = '' }) {
  const plan = {
    id: 'tp_' + Date.now() + '_' + Math.random().toString(36).slice(2, 6),
    code,
    name: name || code,
    buy_price: Number(buy_price),
    stop_loss: Number(stop_loss),
    target: Number(target),
    reason,
    expected,
    created_at: Date.now(),
    status: 'waiting',
    hit_at: null,
    t1_close: null,
    max_high_after_hit: null,
    min_low_after_hit: null,
  }
  plans.value.push(plan)
  saveToStorage()
  // 同步到数据库
  upsertUserPlan(plan).catch(() => {})
}

export function removePlan(id) {
  const idx = plans.value.findIndex(p => p.id === id)
  if (idx >= 0) {
    plans.value.splice(idx, 1)
    saveToStorage()
    // 从数据库删除
    deleteUserPlan(id).catch(() => {})
  }
}

export function updatePlan(id, fields) {
  const p = plans.value.find(p => p.id === id)
  if (p) {
    Object.assign(p, fields)
    saveToStorage()
    // 同步到数据库
    upsertUserPlan(p).catch(() => {})
  }
}

// ── 从数据库加载 ──
export async function syncFromServer() {
  try {
    const { data } = await getUserPlans()
    const items = data.data || data || []
    if (items.length > 0) {
      const map = new Map(plans.value.map(p => [p.id, p]))
      for (const item of items) {
        map.set(item.id, { ...map.get(item.id), ...item })
      }
      plans.value = Array.from(map.values())
      saveToStorage()
    }
  } catch (e) {
    // API 失败时用 localStorage
  }
}

// ── 盈亏比计算（计划质量指标）──
export function calcRiskReward(plan) {
  const reward = Number(plan.target) - Number(plan.buy_price)
  const risk = Number(plan.buy_price) - Number(plan.stop_loss)
  if (risk <= 0) return null   // 止损设错（≥买点），盈亏比无意义
  return reward / risk
}

// ── 状态机判定（核心逻辑，每次刷新调用）──
// 返回 { statusChanged: bool, newStatus, oldStatus, plan }
// 修改 plan 的状态和跟踪字段，调用者负责 saveToStorage
export function evaluatePlanStatus(plan, realtime) {
  if (!realtime || !realtime.price || plan.status === 'targeted' || plan.status === 'stopped') {
    return { statusChanged: false, plan }
  }
  const price = Number(realtime.price)
  const oldStatus = plan.status
  const now = Date.now()

  // waiting：等待买点
  if (plan.status === 'waiting') {
    // 触及买点（现价 ≤ 买点价）
    if (price <= plan.buy_price) {
      plan.status = 'hit'
      plan.hit_at = now
      plan.max_high_after_hit = price
      plan.min_low_after_hit = price
    }
    // 可选：涨过头（超过目标 5%），错过机会
    else if (ENABLE_EXPIRED && price > plan.target * 1.05) {
      plan.status = 'expired'
    }
  }

  // hit：跟踪中，判定目标/止损
  if (plan.status === 'hit') {
    // 更新期间高低点
    plan.max_high_after_hit = Math.max(plan.max_high_after_hit || 0, price)
    plan.min_low_after_hit = plan.min_low_after_hit != null
      ? Math.min(plan.min_low_after_hit, price)
      : price

    // 达到目标
    if (price >= plan.target) {
      plan.status = 'targeted'
    }
    // 触及止损
    else if (price <= plan.stop_loss) {
      plan.status = 'stopped'
    }

    // T+1 逻辑：hit 后若跨自然日，记录当日首次刷新价为 T+1 收盘近似
    if (plan.hit_at && plan.t1_close == null) {
      const hitDate = new Date(plan.hit_at).toDateString()
      const nowDate = new Date(now).toDateString()
      if (hitDate !== nowDate) {
        plan.t1_close = price
      }
    }
  }

  return {
    statusChanged: plan.status !== oldStatus,
    newStatus: plan.status,
    oldStatus,
    plan,
  }
}

// ── 距买点计算（表格展示）──
export function calcDistanceToBuy(plan, price) {
  if (!price || price <= 0) return null
  return ((price - plan.buy_price) / plan.buy_price) * 100
}

// ── T+1 表现（触及后次日的涨跌幅，近似）──
export function calcT1Performance(plan) {
  if (plan.t1_close == null || plan.hit_at == null) return null
  // T+1 涨跌幅 = (次日收盘 - 买点价) / 买点价
  return ((plan.t1_close - plan.buy_price) / plan.buy_price) * 100
}

// ── 导入导出 ──
export function exportPlansJSON() {
  const data = {
    version: 1,
    type: 'trade_plans',
    exported_at: new Date().toISOString(),
    plans: plans.value,
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `trade_plans_${new Date().toISOString().slice(0, 10)}.json`
  a.click()
  URL.revokeObjectURL(url)
}

export function importPlansJSON(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      try {
        const data = JSON.parse(e.target.result)
        const arr = Array.isArray(data) ? data : (data.plans || data.items)
        if (!Array.isArray(arr)) throw new Error('文件格式不正确')
        const map = new Map(plans.value.map(p => [p.id, p]))
        for (const item of arr) {
          if (item.id) map.set(item.id, { ...map.get(item.id), ...item })
        }
        plans.value = Array.from(map.values())
        saveToStorage()
        resolve(plans.value.length)
      } catch (err) {
        reject(err)
      }
    }
    reader.onerror = () => reject(new Error('文件读取失败'))
    reader.readAsText(file)
  })
}

// ── 汇总统计 ──
export function usePlansSummary(realtimeMap) {
  return computed(() => {
    let pending = 0    // waiting + hit（待验证）
    let targeted = 0   // 达目标 ✓
    let stopped = 0    // 破止损 ✗
    let expired = 0    // 错过
    for (const p of plans.value) {
      if (p.status === 'waiting' || p.status === 'hit') pending++
      else if (p.status === 'targeted') targeted++
      else if (p.status === 'stopped') stopped++
      else if (p.status === 'expired') expired++
    }
    return {
      count: plans.value.length,
      pending,
      targeted,
      stopped,
      expired,
    }
  })
}

export function useTradePlans() {
  return { plans }
}
