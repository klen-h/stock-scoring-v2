/**
 * 前端评分计算 Composable
 * 
 * 封装 IndexedDB + Web Worker + 腾讯 API + 评分引擎
 * 提供响应式的评分结果给 ScoreRank.vue 使用
 */

import { ref, computed } from 'vue'
import {
  initKlineDB,
  checkAndUpdate,
  getAllStocks,
  getKlines,
  getLastUpdateDate,
  getStockCount,
} from '../utils/klineDB'
import { fetchRealtimeQuotes } from '../api/tencent'
import { scoreStock, roughScore } from '../utils/scoringEngine'

// ── 响应式状态 ──
const dbReady = ref(false)
const dbStockCount = ref(0)
const lastUpdateDate = ref(null)
const isUpdating = ref(false)
const updateProgress = ref({ stage: '', message: '', loaded: 0, total: 0 })
const isComputing = ref(false)
const scoringResult = ref([])
const lastScoreTime = ref(null)
const error = ref(null)

// Worker 实例（单例）
let worker = null
let workerReady = false
const pendingRequests = new Map()
let requestId = 0

/**
 * 初始化前端评分系统
 * - 打开 IndexedDB
 * - 检查并更新数据
 * - 启动 Web Worker
 */
export async function initFrontendScoring() {
  try {
    error.value = null

    // 1. 初始化 IndexedDB
    await initKlineDB()
    dbReady.value = true

    // 2. 获取数据状态
    dbStockCount.value = await getStockCount()
    lastUpdateDate.value = await getLastUpdateDate()

    // 3. 启动 Worker
    initWorker()

    // 4. 检查数据更新
    if (dbStockCount.value === 0) {
      // 首次使用，需要下载数据
      return { needsDownload: true }
    }

    return { needsDownload: false }
  } catch (e) {
    error.value = e.message
    console.error('初始化前端评分失败:', e)
    return { needsDownload: false, error: e.message }
  }
}

/**
 * 下载并导入 K 线数据包
 * @param {string} baseUrl - 数据包 URL 前缀
 */
export async function downloadKlineData(baseUrl) {
  isUpdating.value = true
  updateProgress.value = { stage: 'download', message: '准备下载...', loaded: 0, total: 0 }

  try {
    const result = await checkAndUpdate(baseUrl, (progress) => {
      updateProgress.value = progress
    })

    if (result.updated) {
      dbStockCount.value = await getStockCount()
      lastUpdateDate.value = await getLastUpdateDate()
    }

    return result
  } catch (e) {
    error.value = e.message
    return { updated: false, message: e.message }
  } finally {
    isUpdating.value = false
  }
}

/**
 * 执行评分计算
 * @param {Object} options
 * @param {string} options.mode - 'top' | 'bottom' | 'signal'
 * @param {number} options.limit - 返回数量
 * @param {string} options.signal - 信号类型（mode='signal' 时使用）
 */
export async function computeRanking(options = {}) {
  const { mode = 'top', limit = 50, signal = '买入' } = options

  if (!dbReady.value || dbStockCount.value === 0) {
    error.value = '数据未就绪，请先下载 K 线数据'
    return []
  }

  isComputing.value = true
  error.value = null

  try {
    // 1. 获取所有股票列表
    const allStocks = await getAllStocks()

    // 2. 批量拉取实时行情
    const codes = allStocks.map(s => s.code)
    const quotes = await fetchRealtimeQuotes(codes)

    // 3. 合并行情数据
    const stocksWithQuotes = allStocks
      .map(s => ({
        ...s,
        ...(quotes[s.code] || {}),
      }))
      .filter(s => s.price > 0)

    // 4. 简化评分快速排序
    const scored = stocksWithQuotes.map(s => ({
      ...s,
      roughScore: roughScore(s),
    }))

    // 5. 根据模式排序并取候选池
    let candidates = []
    if (mode === 'top') {
      scored.sort((a, b) => b.roughScore - a.roughScore)
      candidates = scored.slice(0, Math.min(150, scored.length))
    } else if (mode === 'bottom') {
      scored.sort((a, b) => a.roughScore - b.roughScore)
      candidates = scored.slice(0, Math.min(100, scored.length))
    } else if (mode === 'signal') {
      // 先全量精算，再按信号过滤
      candidates = scored.slice(0, Math.min(200, scored.length))
    }

    // 6. 精算候选池（使用 Worker）
    const preciseResults = await preciseScoreBatch(candidates)

    // 7. 排序并取结果
    preciseResults.sort((a, b) => b.total_score - a.total_score)

    let finalResults = []
    if (mode === 'signal') {
      finalResults = preciseResults.filter(r => r.signal === signal).slice(0, limit)
    } else {
      finalResults = preciseResults.slice(0, limit)
    }

    scoringResult.value = finalResults
    lastScoreTime.value = new Date()

    return finalResults
  } catch (e) {
    error.value = e.message
    console.error('评分计算失败:', e)
    return []
  } finally {
    isComputing.value = false
  }
}

/**
 * 批量精算评分（使用 Worker）
 */
async function preciseScoreBatch(stocks) {
  if (!worker || !workerReady) {
    // Worker 不可用，降级到主线程计算
    console.warn('Worker 不可用，降级到主线程计算')
    return preciseScoreBatchMainThread(stocks)
  }

  const results = []

  // 分批发送给 Worker（每批 20 只）
  const batchSize = 20
  for (let i = 0; i < stocks.length; i += batchSize) {
    const batch = stocks.slice(i, i + batchSize)

    // 获取每只股票的 K 线数据
    const items = []
    for (const stock of batch) {
      const klines = await getKlines(stock.code, 60)
      if (klines && klines.length >= 30) {
        items.push({ code: stock.code, klines })
      }
    }

    if (items.length === 0) continue

    // 发送给 Worker 计算
    const workerResults = await sendToWorker({
      type: 'BATCH_CALC',
      items,
    })

    // 合并结果
    for (const wr of workerResults) {
      const stock = stocks.find(s => s.code === wr.code)
      if (!stock) continue

      const scoreResult = scoreStock({
        code: wr.code,
        name: stock.name,
        technicalData: wr.series,
        stockInfo: stock,
      })

      results.push(scoreResult)
    }
  }

  return results
}

/**
 * 主线程降级计算
 */
async function preciseScoreBatchMainThread(stocks) {
  const results = []

  for (const stock of stocks) {
    try {
      const klines = await getKlines(stock.code, 60)
      if (!klines || klines.length < 30) continue

      // 主线程计算指标（简化版）
      const technicalData = calcTechnicalSimple(klines)
      if (!technicalData) continue

      const scoreResult = scoreStock({
        code: stock.code,
        name: stock.name,
        technicalData,
        stockInfo: stock,
      })

      results.push(scoreResult)
    } catch (e) {
      console.warn(`精算失败 ${stock.code}:`, e)
    }
  }

  return results
}

/**
 * 简化版指标计算（主线程用）
 */
function calcTechnicalSimple(klines) {
  if (!klines || klines.length < 30) return null

  const closes = klines.map(k => k[4])
  const highs = klines.map(k => k[2])
  const lows = klines.map(k => k[3])

  // 计算 MA
  const ma5 = calcMA(closes, 5)
  const ma10 = calcMA(closes, 10)
  const ma20 = calcMA(closes, 20)

  // 组装结果
  return klines.map((k, i) => ({
    date: k[0],
    open: k[1],
    high: k[2],
    low: k[3],
    close: k[4],
    volume: k[5],
    ma5: ma5[i],
    ma10: ma10[i],
    ma20: ma20[i],
    // 其他指标简化处理
    dif: 0,
    dea: 0,
    macd: 0,
    rsi: 50,
    k: 50,
    d: 50,
    j: 50,
    boll_upper: null,
    boll_mid: ma20[i],
    boll_lower: null,
  }))
}

function calcMA(data, window) {
  const result = new Array(data.length).fill(null)
  for (let i = window - 1; i < data.length; i++) {
    let sum = 0
    for (let j = i - window + 1; j <= i; j++) {
      sum += data[j]
    }
    result[i] = sum / window
  }
  return result
}

// ── Worker 管理 ──

function initWorker() {
  if (worker) return

  try {
    worker = new Worker(
      new URL('../workers/indicatorWorker.js', import.meta.url),
      { type: 'module' }
    )

    worker.onmessage = handleWorkerMessage
    worker.onerror = (e) => {
      console.error('Worker 错误:', e)
      workerReady = false
    }
  } catch (e) {
    console.warn('Worker 初始化失败:', e)
    workerReady = false
  }
}

function handleWorkerMessage(e) {
  const { type, code, series, latest, results, message } = e.data

  if (type === 'READY') {
    workerReady = true
    return
  }

  if (type === 'RESULT') {
    const resolver = pendingRequests.get(code)
    if (resolver) {
      resolver({ series, latest })
      pendingRequests.delete(code)
    }
    return
  }

  if (type === 'BATCH_RESULT') {
    // 批量结果，用特殊 key 查找
    const resolver = pendingRequests.get('batch')
    if (resolver) {
      resolver(results)
      pendingRequests.delete('batch')
    }
    return
  }

  if (type === 'ERROR') {
    console.warn(`Worker 错误 [${code}]:`, message)
    const resolver = pendingRequests.get(code)
    if (resolver) {
      resolver(null)
      pendingRequests.delete(code)
    }
  }
}

function sendToWorker(message) {
  return new Promise((resolve, reject) => {
    if (!worker || !workerReady) {
      reject(new Error('Worker 不可用'))
      return
    }

    const id = ++requestId
    const key = message.type === 'BATCH_CALC' ? 'batch' : message.code

    // 设置超时
    const timeout = setTimeout(() => {
      pendingRequests.delete(key)
      reject(new Error('Worker 响应超时'))
    }, 30000)

    pendingRequests.set(key, (result) => {
      clearTimeout(timeout)
      resolve(result)
    })

    worker.postMessage(message)
  })
}

// ── 导出响应式状态 ──

export function useFrontendScoring() {
  return {
    dbReady,
    dbStockCount,
    lastUpdateDate,
    isUpdating,
    updateProgress,
    isComputing,
    scoringResult,
    lastScoreTime,
    error,
    initFrontendScoring,
    downloadKlineData,
    computeRanking,
  }
}
