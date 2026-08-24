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

/**
 * 过滤无效股票（ST、亏损、科创板、创业板）
 */
function isValidStock(stock) {
  const name = (stock.name || '').replace(/\s/g, '').toUpperCase()
  const code = stock.code || ''
  
  // 过滤 ST/*ST
  if (name.startsWith('ST') || name.startsWith('*ST') || name.startsWith('SST')) {
    return false
  }
  // 过滤科创板 (688) 和创业板 (300/301)
  if (code.startsWith('688') || code.startsWith('300') || code.startsWith('301')) {
    return false
  }
  // 过滤亏损股（PE <= 0）
  if (stock.pe !== undefined && stock.pe <= 0) {
    return false
  }
  return true
}

// ── 股票池质量门槛（与后端一致）──
// 流通市值 > 50 亿、股价 > 3 元、成交额 > 1 亿（当日成交额近似）
const MIN_FLOAT_CAP = 50 * 10000  // 万元（50 亿）
const MIN_PRICE = 3               // 元
const MIN_AMOUNT = 1e8            // 元（1 亿）

/**
 * 质量过滤：流通市值/股价/成交额门槛（需已合并实时行情）
 */
function passQualityFilter(stock) {
  if ((stock.float_cap || 0) < MIN_FLOAT_CAP) return false
  if ((stock.price || 0) < MIN_PRICE) return false
  if ((stock.amount || 0) < MIN_AMOUNT) return false
  return true
}

// ── 响应式状态 ──
const dbReady = ref(false)
const dbStockCount = ref(0)
const lastUpdateDate = ref(null)
const isUpdating = ref(false)
const updateProgress = ref({ stage: '', message: '', loaded: 0, total: 0 })
const isComputing = ref(false)
const scoringResult = ref([])
// 本轮实际参与评分的股票池数量（过滤后全量，非返回列表长度）
const poolCount = ref(0)
const lastScoreTime = ref(null)
const error = ref(null)

// ── 持久化用户的前端模式选择 ──
const FRONTEND_MODE_KEY = 'score_use_frontend_mode'
const useFrontendMode = ref(false)

function loadFrontendModePreference() {
  try {
    const saved = localStorage.getItem(FRONTEND_MODE_KEY)
    useFrontendMode.value = saved === 'true'
  } catch { useFrontendMode.value = false }
}

function saveFrontendModePreference(value) {
  try { localStorage.setItem(FRONTEND_MODE_KEY, String(value)) } catch {}
}

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

    // 3. 合并行情数据并过滤（含流通市值/股价/成交额质量门槛）
    const stocksWithQuotes = allStocks
      .map(s => ({
        ...s,
        ...(quotes[s.code] || {}),
      }))
      .filter(s => s.price > 0 && isValidStock(s) && passQualityFilter(s))
    poolCount.value = stocksWithQuotes.length

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

    // 获取每只股票的 K 线数据（150 天，与后端 500 天历史相比足以让 EMA 系列指标收敛）
    const items = []
    for (const stock of batch) {
      const klines = await getKlines(stock.code, 150)
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
      const klines = await getKlines(stock.code, 150)
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
 * 完整计算所有指标（MA/MACD/RSI/KDJ/BOLL），与 Worker 保持一致
 */
function calcTechnicalSimple(klines) {
  if (!klines || klines.length < 30) return null

  const length = klines.length
  const closes = klines.map(k => k[4])
  const highs = klines.map(k => k[2])
  const lows = klines.map(k => k[3])
  const opens = klines.map(k => k[1])
  const volumes = klines.map(k => k[5])

  // MA
  const ma5 = calcMA(closes, 5)
  const ma10 = calcMA(closes, 10)
  const ma20 = calcMA(closes, 20)
  const ma60 = calcMA(closes, 60)

  // MACD
  const ema12 = calcEMA(closes, 12)
  const ema26 = calcEMA(closes, 26)
  const dif = closes.map((_, i) => ema12[i] - ema26[i])
  const dea = calcEMA(dif, 9)
  const macd = dif.map((d, i) => (d - dea[i]) * 2)

  // RSI
  const rsi = calcRSI(closes, 14)

  // KDJ
  const kdj = calcKDJ(highs, lows, closes, 9)

  // BOLL
  const boll = calcBOLL(closes, 20, 2)

  // 组装结果
  return klines.map((k, i) => ({
    date: k[0],
    open: opens[i],
    high: highs[i],
    low: lows[i],
    close: closes[i],
    volume: volumes[i],
    ma5: ma5[i],
    ma10: ma10[i],
    ma20: ma20[i],
    ma60: ma60[i],
    dif: round4(dif[i]),
    dea: round4(dea[i]),
    macd: round4(macd[i]),
    rsi: round2(rsi[i]),
    k: round2(kdj.k[i]),
    d: round2(kdj.d[i]),
    j: round2(kdj.j[i]),
    boll_upper: boll.upper[i],
    boll_mid: boll.mid[i],
    boll_lower: boll.lower[i],
  }))
}

function round2(val) {
  if (val === null || val === undefined || isNaN(val)) return null
  return Math.round(val * 100) / 100
}

// DIF/DEA/MACD 专用 4 位小数（与后端 round(v,4) 一致，避免微小值被压成 0）
function round4(val) {
  if (val === null || val === undefined || isNaN(val)) return null
  return Math.round(val * 10000) / 10000
}

function calcMA(data, window) {
  const result = new Array(data.length).fill(null)
  if (data.length < window) return result
  let sum = 0
  for (let i = 0; i < window; i++) sum += data[i]
  result[window - 1] = sum / window
  for (let i = window; i < data.length; i++) {
    sum += data[i] - data[i - window]
    result[i] = sum / window
  }
  return result
}

function calcEMA(data, span) {
  const result = new Array(data.length)
  const alpha = 2.0 / (span + 1)
  result[0] = data[0]
  for (let i = 1; i < data.length; i++) {
    result[i] = data[i] * alpha + result[i - 1] * (1 - alpha)
  }
  return result
}

function calcRSI(closes, period) {
  // 与后端 _calc_technical_fast 一致：近 14 日简单平均口径（非 Wilder 平滑）
  const result = new Array(closes.length).fill(null)
  if (closes.length < period + 1) return result

  for (let i = period; i < closes.length; i++) {
    let avgGain = 0
    let avgLoss = 0
    // 窗口 = delta[i-period+1 .. i]，即 closes[i-period .. i] 的 period 个变化量（与后端一致）
    for (let p = i - period + 1; p <= i; p++) {
      const change = closes[p] - closes[p - 1]
      if (change > 0) avgGain += change
      else if (change < 0) avgLoss -= change
    }
    avgGain /= period
    avgLoss /= period
    result[i] = avgLoss > 0 ? 100 - 100 / (1 + avgGain / avgLoss) : 100
  }

  return result
}

function calcKDJ(highs, lows, closes, n) {
  const length = closes.length
  const kValues = new Array(length).fill(50)
  const dValues = new Array(length).fill(50)
  const jValues = new Array(length).fill(50)
  if (length < n) return { k: kValues, d: dValues, j: jValues }
  let prevK = 50, prevD = 50
  for (let i = n - 1; i < length; i++) {
    let highN = -Infinity, lowN = Infinity
    for (let j = i - n + 1; j <= i; j++) {
      if (highs[j] > highN) highN = highs[j]
      if (lows[j] < lowN) lowN = lows[j]
    }
    const rsv = highN === lowN ? 50 : ((closes[i] - lowN) / (highN - lowN)) * 100
    const k = (2 / 3) * prevK + (1 / 3) * rsv
    const d = (2 / 3) * prevD + (1 / 3) * k
    const j = 3 * k - 2 * d
    kValues[i] = k; dValues[i] = d; jValues[i] = j
    prevK = k; prevD = d
  }
  return { k: kValues, d: dValues, j: jValues }
}

function calcBOLL(closes, window, numStd) {
  const mid = calcMA(closes, window)
  const upper = new Array(closes.length).fill(null)
  const lower = new Array(closes.length).fill(null)
  for (let i = window - 1; i < closes.length; i++) {
    let sum = 0
    for (let j = i - window + 1; j <= i; j++) sum += (closes[j] - mid[i]) ** 2
    const std = Math.sqrt(sum / window)
    upper[i] = mid[i] + numStd * std
    lower[i] = mid[i] - numStd * std
  }
  return { upper, mid, lower }
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
  // 初始化时加载用户偏好
  loadFrontendModePreference()

  return {
    dbReady,
    dbStockCount,
    lastUpdateDate,
    isUpdating,
    updateProgress,
    isComputing,
    scoringResult,
    poolCount,
    lastScoreTime,
    error,
    useFrontendMode,
    initFrontendScoring,
    downloadKlineData,
    computeRanking,
    saveFrontendModePreference,
  }
}
