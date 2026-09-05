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
  getIndicator,
  getIndicatorsDate,
  getIndicatorsCount,
  importIndicatorsPack,
} from '../utils/klineDB'
import { fetchRealtimeQuotes } from '../api/tencent'
import { getFinanceBatch, getScoreWeights } from '../api'
import { scoreStock, roughScore } from '../utils/scoringEngine'
import { isTradingDay } from './usePortfolio'

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
// 流通市值 > 50 亿、股价 > 3 元（成交额门槛已移除：盘中早盘时段当日成交额未累积到位，会误杀大量股票）
const MIN_FLOAT_CAP = 50 * 10000  // 万元（50 亿）
const MIN_PRICE = 3               // 元

/**
 * 质量过滤：流通市值/股价门槛（需已合并实时行情）
 */
function passQualityFilter(stock) {
  if ((stock.float_cap || 0) < MIN_FLOAT_CAP) return false
  if ((stock.price || 0) < MIN_PRICE) return false
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

// 数据包基础 URL（GitHub Pages 托管）。
// localStorage 'kline_data_base_url' 可覆盖——本地端到端验收/私有部署用，
// 不设置即走生产地址。
const KLINE_DATA_BASE_URL = (typeof localStorage !== 'undefined'
  && localStorage.getItem('kline_data_base_url'))
  || 'https://klen-h.github.io/stock-scoring-v2/data'

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

    // 5. 自动检测过期：如果数据不是今天的，后台静默更新
    const todayStr = new Date().toISOString().slice(0, 10).replace(/-/g, '')
    if (lastUpdateDate.value !== todayStr) {
      console.log(`[前端评分] 数据过期（${lastUpdateDate.value}），后台静默更新...`)
      silentUpdate()
    } else {
      // K 线包已是今日，但指标包可能缺失（旧版包/首次升级 DB v2）→ 补拉
      const indDate = await getIndicatorsDate()
      if (indDate !== lastUpdateDate.value) {
        ensureIndicatorsPack().then((r) => {
          if (r.updated) console.log(`[前端评分] 指标包补拉完成: ${r.message}`)
        })
      }
    }

    return { needsDownload: false }
  } catch (e) {
    error.value = e.message
    console.error('初始化前端评分失败:', e)
    return { needsDownload: false, error: e.message }
  }
}

/**
 * 下载并导入 K 线数据包（默认使用 KLINE_DATA_BASE_URL）
 */
export async function downloadKlineData(baseUrl = KLINE_DATA_BASE_URL) {
  isUpdating.value = true
  updateProgress.value = { stage: 'download', message: '准备下载...', loaded: 0, total: 0 }

  try {
    const result = await checkAndUpdate(baseUrl, (progress) => {
      updateProgress.value = progress
    })

    if (result.updated) {
      dbStockCount.value = await getStockCount()
      lastUpdateDate.value = await getLastUpdateDate()
      // K 线包更新后同步拉指标包（评分统一事实源，失败不影响 K 线可用性）
      const ind = await ensureIndicatorsPack(baseUrl)
      if (ind.updated) console.log(`[前端评分] 指标包更新: ${ind.message}`)
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
 * 确保指标包与 K 线包同版本：缺/旧则下载 indicators-pack.json.gz 并导入。
 * 失败只告警不抛错——评分会自动回退 150 根现算（现状兜底）。
 * @returns {Promise<{ updated: boolean, message: string }>}
 */
export async function ensureIndicatorsPack(baseUrl = KLINE_DATA_BASE_URL) {
  try {
    const klineDate = await getLastUpdateDate()
    const indDate = await getIndicatorsDate()
    if (!klineDate || klineDate === indDate) {
      return { updated: false, message: '指标包已是最新' }
    }

    updateProgress.value = { stage: 'download', message: '下载指标包...', loaded: 0, total: 0 }
    const packUrl = `${baseUrl}/indicators-pack.json.gz?t=${Date.now()}`
    const response = await fetch(packUrl, { cache: 'no-store' })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)

    updateProgress.value = { stage: 'decompress', message: '解压指标包...', loaded: 0, total: 0 }
    const blob = await response.blob()
    const text = await decompressGzipText(blob)
    const data = JSON.parse(text)

    updateProgress.value = { stage: 'import', message: '导入指标包...', loaded: 0, total: 0 }
    const result = await importIndicatorsPack(data, (loaded, total) => {
      updateProgress.value = { stage: 'import', message: '导入指标包...', loaded, total }
    })
    return { updated: true, message: `导入 ${result.imported} 只指标` }
  } catch (e) {
    console.warn('[前端评分] 指标包下载失败（评分回退 150 根现算）:', e.message)
    return { updated: false, message: e.message }
  }
}

/**
 * 后台静默更新数据包（不阻塞 UI，不显示进度）
 * 在 initFrontendScoring 检测到数据过期时自动调用。
 */
async function silentUpdate() {
  try {
    const result = await checkAndUpdate(KLINE_DATA_BASE_URL, () => {})
    if (result.updated) {
      dbStockCount.value = await getStockCount()
      lastUpdateDate.value = await getLastUpdateDate()
      console.log(`[前端评分] 静默更新完成: ${result.message}`)
      // K 线包换新后指标包同步更新（后台静默，失败自动回退现算）
      const ind = await ensureIndicatorsPack(KLINE_DATA_BASE_URL)
      if (ind.updated) console.log(`[前端评分] 指标包静默更新: ${ind.message}`)
    } else {
      console.log(`[前端评分] 静默更新: ${result.message}`)
      // K 线未过期但指标缺失（DB v2 升级首次）→ 补拉
      const indDate = await getIndicatorsDate()
      if (lastUpdateDate.value && indDate !== lastUpdateDate.value) {
        const ind = await ensureIndicatorsPack(KLINE_DATA_BASE_URL)
        if (ind.updated) console.log(`[前端评分] 指标包补拉: ${ind.message}`)
      }
    }
  } catch (e) {
    console.warn('[前端评分] 静默更新失败（不影响当前使用）:', e.message)
  }
}

/**
 * 解压 gzip 为文本（DecompressionStream 现代浏览器可用）
 */
async function decompressGzipText(blob) {
  // ★ 与 klineDB.decompressGzip 同款降级：部分内嵌浏览器（IAB）的
  //   DecompressionStream 构造器存在但流读取必抛 —— catch 后必须真降级 pako
  if (typeof DecompressionStream !== 'undefined') {
    try {
      const stream = blob.stream().pipeThrough(new DecompressionStream('gzip'))
      return await new Response(stream).text()
    } catch (e) {
      console.warn('[前端评分] DecompressionStream 失败，降级 pako:', e?.message)
    }
  }
  const { ungzip } = await import('pako')
  const buf = new Uint8Array(await blob.arrayBuffer())
  return ungzip(buf, { to: 'string' })
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
    // ★ 0. 同步当前生效权重（后端会随 regime 动态切换，如震荡偏空
    //   27/23/23/17/11）——拉取失败回退静态默认（scoringEngine 兜底）
    let currentWeights = null
    try {
      const wres = await getScoreWeights()
      currentWeights = (wres && wres.data && wres.data.weights) || null
    } catch {
      currentWeights = null
    }

    // 1. 获取所有股票列表
    const allStocks = await getAllStocks()

    // 2. 批量拉取实时行情
    const codes = allStocks.map(s => s.code)
    const quotes = await fetchRealtimeQuotes(codes)

    // 3. 合并行情数据并逐层过滤（拆开统计便于排查“评分股票数偏少”问题）
    const merged = allStocks.map(s => ({
      ...s,
      ...(quotes[s.code] || {}),
    }))
    // 行情拉取失败/停牌 → price 缺失，直接出局（质量过滤依赖实时市值/成交额，无法兜底）
    const withQuote = merged.filter(s => s.price > 0)
    const valid = withQuote.filter(isValidStock)
    const stocksWithQuotes = valid.filter(passQualityFilter)
    poolCount.value = stocksWithQuotes.length
    console.info(`[前端评分] 过滤漏斗: 本地库 ${allStocks.length} → 行情成功 ${withQuote.length} → 有效(非ST/PE>0) ${valid.length} → 质量门槛通过 ${stocksWithQuotes.length}`)

    // 3.5 批量加载财报（成长/质量维度的数据源）
    //   后端 1 次 SQL + 30 分钟进程缓存，上限 1000/批，所以分批调
    //   合并进 stock.finance —— roughScore/scoreStock 会兜底从 stockInfo.finance 读取，
    //   Worker 也能透明传递（stockInfo 整个丢给 scoreStock）
    let finMap = {}
    try {
      const codes = stocksWithQuotes.map(s => s.code)
      for (let i = 0; i < codes.length; i += 1000) {
        const chunk = codes.slice(i, i + 1000)
        const part = await getFinanceBatch(chunk)
        // ★ 响应拦截器返回的是 AxiosResponse 对象，真实数据在 .data 里；
        //   不取 .data 会让 finMap 变成 {data:{...}, status, headers}，导致 finMap[code] 永远 undefined、财报静默丢失
        const partData = (part && typeof part === 'object' && 'data' in part) ? part.data : part
        finMap = { ...finMap, ...partData }
      }
    } catch (e) {
      console.warn('[前端评分] 财报加载失败（成长/质量维度将不参与加权）:', e)
    }
    const stocksWithFin = stocksWithQuotes.map(s => ({
      ...s,
      finance: finMap[s.code] || null,
    }))

    // 4. 简化评分快速排序
    const scored = stocksWithFin.map(s => ({
      ...s,
      roughScore: roughScore(s, null, currentWeights),
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

    // 6. 精算候选池（使用 Worker，同步当前生效权重）
    const preciseResults = await preciseScoreBatch(candidates, currentWeights)

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
 * 拼接今日实时快照作为最后一根 K 线
 * 历史 K 线格式：[date, open, high, low, close, volume]
 * 腾讯快照字段：open, high, low, price, volume
 * 如果历史数据已包含今天（数据包当天更新过），则跳过拼接。
 *
 * ★ 两类场景禁止拼接（否则把最后收盘价重复计一天，MA/MACD/RSI/KDJ 全部失真）：
 *   1. 非交易日（周末休市，复用 usePortfolio 的 isTradingDay 判断）——
 *      周六打开页面会把周五收盘再拼一条"周六"K 线（000833 实测总分虚增 ~2 分）
 *   2. 交易日 09:25 前——集合竞价未出价，实时快照仍是昨日数据，拼上等于昨日重复
 *   另外日期必须用本地时间（原 toISOString 是 UTC，北京时间 0-8 点会错一天）
 */
export function appendTodayBar(klines, stock, now = new Date()) {
  // 无实时数据时不拼接（停牌/拉取失败）
  if (!stock.price || stock.price <= 0) return klines
  if (!isTradingDay(now)) return klines
  if (now.getHours() * 60 + now.getMinutes() < 9 * 60 + 25) return klines

  const pad = n => String(n).padStart(2, '0')
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  const lastBar = klines[klines.length - 1]

  // 已经包含今天的数据，跳过（避免重复）
  if (lastBar && lastBar[0] === today) return klines

  // 拼接今日 K 线：[date, open, high, low, close, volume]
  const todayBar = [
    today,
    stock.open || stock.price,   // 今开（无则用现价兑底）
    stock.high || stock.price,
    stock.low || stock.price,
    stock.price,                 // 现价作为 close
    stock.volume || 0,           // 成交量（股）
  ]

  return [...klines, todayBar]
}

/**
 * _series 是否新鲜（可直接喂评分，无需拼今日实时 bar）。
 * 判定与 appendTodayBar 的拼接条件互为反证：
 *   - 非交易日：包内最后一根就是最近交易日收盘 → 新鲜
 *   - 交易日 09:25 前：集合竞价未出价，实时快照还是昨日 → 新鲜
 *   - 交易日 ≥09:25 且 _series 末根 ≠ 今天（盘中/包未含今日）→ 不新鲜，回退现算
 * @param {Array} series - 预计算指标数组（_series，元素含 date 字段）
 */
function seriesIsFresh(series) {
  if (!series || !series.length) return false
  const now = new Date()
  if (!isTradingDay(now)) return true
  if (now.getHours() * 60 + now.getMinutes() < 9 * 60 + 25) return true
  const pad = n => String(n).padStart(2, '0')
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
  return series[series.length - 1].date === today
}

/**
 * 批量精算评分
 *
 * ★ 优先读指标包（IndexedDB indicators store，_series 500 天口径、与后端
 *   indicator_cache 同一引擎预计算）——零现算且与后端评分同源对齐；
 *   指标缺失或含今日实时要求（盘中 _series 未含今日 bar）时，回退
 *   150 根 K 线 + Worker 现算（现状兜底，仍零后端请求）。
 */
async function preciseScoreBatch(stocks, weights) {
  if (!worker || !workerReady) {
    // Worker 不可用，降级到主线程计算
    console.warn('Worker 不可用，降级到主线程计算')
    return preciseScoreBatchMainThread(stocks, weights)
  }

  const results = []
  const fallback = []   // 指标包不可用/不新鲜 → 走现算

  // 1) 先尝试指标包直读（批量预热，逐只读 IndexedDB 很快）
  for (const stock of stocks) {
    let used = false
    try {
      const ind = await getIndicator(stock.code)
      const series = ind && ind._series
      if (series && seriesIsFresh(series)) {
        results.push(scoreStock({
          code: stock.code,
          name: stock.name,
          technicalData: series,
          stockInfo: stock,
          weights,
        }))
        used = true
      }
    } catch { used = false }
    if (!used) fallback.push(stock)
  }

  // 2) 回退：150 根 K 线 + Worker 现算（现状兜底路径）
  const batchSize = 20
  for (let i = 0; i < fallback.length; i += batchSize) {
    const batch = fallback.slice(i, i + batchSize)

    // 获取每只股票的 K 线数据（150 天，与后端 500 天历史相比足以让 EMA 系列指标收敛）
    // 并拼接今日实时快照作为最后一根 K 线（盘中准实时指标）
    const items = []
    for (const stock of batch) {
      const klines = await getKlines(stock.code, 150)
      if (klines && klines.length >= 30) {
        const klinesWithToday = appendTodayBar(klines, stock)
        items.push({ code: stock.code, klines: klinesWithToday })
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
        weights,
      })

      results.push(scoreResult)
    }
  }

  return results
}

/**
 * 主线程降级计算
 */
async function preciseScoreBatchMainThread(stocks, weights) {
  const results = []

  for (const stock of stocks) {
    try {
      const klines = await getKlines(stock.code, 150)
      if (!klines || klines.length < 30) continue

      // 主线程计算指标（简化版）—— 同样拼接今日实时数据
      const klinesWithToday = appendTodayBar(klines, stock)
      const technicalData = calcTechnicalSimple(klinesWithToday)
      if (!technicalData) continue

      const scoreResult = scoreStock({
        code: stock.code,
        name: stock.name,
        technicalData,
        stockInfo: stock,
        weights,
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

// ── 本地历史回测（用 IndexedDB 里的 K 线，零后端请求）──

/**
 * 本地历史回测：用过去 N 天的技术面评分模拟选股，计算持有 M 天后的收益。
 *
 * 与后端 /score/backtest 同口径，但数据和算力都在浏览器本地：
 *   - 零后端请求 → 不触发腾讯 WAF、不受服务重启影响（后端方案的两个卡点）
 *   - 本地 CPU + Worker 并行 → 秒级完成（后端方案在 0.1 CPU 上要几分钟）
 *
 * 关键优化（比后端快约 30 倍）：MA/MACD/RSI/KDJ/BOLL 都是递推指标，
 * 第 i 天的值只依赖 i 之前的数据（无未来函数），所以每只股票只需算一次完整序列，
 * 之后每天用 series.slice(0, idx+1) 切片评分即可——与按天重算切片数学等价。
 *
 * @param {Object} opts - { topN, days, periods, poolSize }
 * @returns {Promise<Object>} { summary, backtest_days, stock_pool_size, source: 'local' }
 */
export async function runLocalBacktest({
  topN = 10,
  days = 30,
  periods = [1, 3, 5, 10],
  poolSize = 100,
} = {}) {
  // 1) 建池：本地库里按市值取前 poolSize 只（数据包本身按市值降序生成）
  const all = await getAllStocks()
  const withCap = (all || []).filter(s => (s.market_cap || 0) > 0)
  withCap.sort((a, b) => (b.market_cap || 0) - (a.market_cap || 0))
  const pool = withCap.slice(0, poolSize)
  if (pool.length < 20) {
    return { error: '本地K线数据不足（至少需要 20 只），请先下载数据包' }
  }

  // 2) 读 K 线（150 根）
  const items = []
  for (const s of pool) {
    const klines = await getKlines(s.code, 150)
    if (klines && klines.length >= 30) {
      items.push({ code: s.code, name: s.name, klines })
    }
  }
  if (items.length < 10) {
    return { error: '有效K线数据不足（本地数据包可能不完整）' }
  }

  // 3) 算指标序列：每只只算一次完整序列（Worker 优先，失败降级主线程）
  const seriesMap = {}
  const calcFallback = (batch) => {
    for (const it of batch) {
      const tech = calcTechnicalSimple(it.klines)
      if (tech) seriesMap[it.code] = tech
    }
  }

  if (worker && workerReady) {
    const batchSize = 20
    for (let i = 0; i < items.length; i += batchSize) {
      const batch = items.slice(i, i + batchSize)
        .map(it => ({ code: it.code, klines: it.klines }))
      try {
        const results = await sendToWorker({ type: 'BATCH_CALC', items: batch })
        for (const r of results || []) {
          if (r && r.series) seriesMap[r.code] = r.series
        }
      } catch (e) {
        calcFallback(batch)
      }
    }
  } else {
    calcFallback(items)
  }

  const codes = Object.keys(seriesMap)
  if (codes.length < 10) return { error: '指标计算失败，请稍后重试' }

  // 4) 回测窗口（与后端同口径：30 根指标预热 + 最长持有期前瞻）
  //    注意：不能直接对全部代码取最小长度——数据包里可能有 K 线很短的股票
  //    （新股/数据不全），一只 45 根就会把 minLen 拉低导致整体误报"数据不足"。
  //    先剔除长度不足的股票，再用剩余的回测。
  const minNeed = 30 + Math.max(...periods) + 35   // 预热 + 前瞻
  const usable = codes.filter(c => seriesMap[c].length >= minNeed)
  if (usable.length < 10) {
    return { error: `历史数据不足以完成回测（K线超过 ${minNeed} 根的股票只有 ${usable.length} 只）` }
  }
  const maxP = Math.max(...periods)
  const minLen = Math.min(...usable.map(c => seriesMap[c].length))
  const btDays = Math.min(days, minLen - maxP - 35)
  if (btDays < 10) {
    return { error: '历史数据不足以完成回测（本地数据包仅 150 根K线）' }
  }
  const startIdx = minLen - btDays - maxP

  // 5) 逐日回测：切片评分（零重算）+ 各持有期收益
  const stats = {}
  periods.forEach(p => { stats[p] = { wins: 0, count: 0, totalReturn: 0 } })

  for (let offset = 0; offset < btDays; offset++) {
    const idx = startIdx + offset
    const dayScores = []

    for (const c of codes) {
      const series = seriesMap[c]
      if (idx >= series.length - 1) continue
      const slice = series.slice(0, idx + 1)
      if (slice.length < 30) continue
      const r = scoreStock({ code: c, name: '', technicalData: slice, stockInfo: {} })
      dayScores.push({ code: c, score: (r && r.dimensions && r.dimensions.technical
        ? r.dimensions.technical.score : 0) })
    }
    if (dayScores.length < topN) continue

    dayScores.sort((a, b) => b.score - a.score)
    for (const t of dayScores.slice(0, topN)) {
      const series = seriesMap[t.code]
      const buy = series[idx].close
      if (!buy || buy <= 0) continue
      for (const p of periods) {
        const fwd = idx + p
        if (fwd >= series.length) continue
        const sell = series[fwd].close
        if (!sell) continue
        const ret = (sell - buy) / buy * 100
        stats[p].count++
        stats[p].totalReturn += ret
        if (ret > 0) stats[p].wins++
      }
    }
  }

  // 6) 汇总
  const summary = {}
  periods.forEach(p => {
    const s = stats[p]
    summary[p] = s.count > 0
      ? {
          win_rate: Math.round(s.wins / s.count * 100),
          avg_return: Math.round(s.totalReturn / s.count * 100) / 100,
          total: s.count,
        }
      : { win_rate: 0, avg_return: 0, total: 0 }
  })

  return {
    summary,
    backtest_days: btDays,
    stock_pool_size: codes.length,
    top_n: topN,
    periods,
    source: 'local',
  }
}


// ── 个股详情页本地数据（PLAN_PACK_MIGRATION Phase 2：消除 /api/stock/kline 并发爆发）──

/**
 * 详情页本地 K 线 + 指标叠加序列（零后端请求）。
 * K 线来自 IndexedDB 包，指标叠加（MA/BOLL/MACD）本地现算（与 K 线逐根对齐），
 * 交易日盘中自动拼接今日实时快照。
 * @returns {Promise<{klines, technical}|null>} null = 本地数据不可用，调用方回退后端接口
 */
export async function loadLocalKline(code, stockInfo = {}, bars = 150) {
  const klines = await getKlines(code, bars)
  if (!klines || klines.length < 30) return null
  const klinesWithToday = appendTodayBar(klines, stockInfo)
  const technical = calcTechnicalSimple(klinesWithToday)
  if (!technical) return null
  // ★ 页面 klineData 期望「对象数组」（d.date / d.open / d.volume...，与后端
  //   /api/stock/kline 一致），而 IndexedDB 包内存的是紧凑数组 [d,o,h,l,c,v] ——
  //   calcTechnicalSimple 的输出行本身就是 OHLCV 对象 + 指标，且与 K 线逐根
  //   对齐，直接复用为 klines（klineData 与 technicalData 天然同轴对齐）。
  return { klines: technical, technical }
}

/**
 * 详情页本地评分（与排行榜同一 scoreStock 引擎、同一指标包事实源）。
 * 指标包 _series 新鲜（非盘中）直读；盘中回退 150 根现算 + 今日快照。
 * @param {Object} finance - 扁平财报 {revenue_yoy, profit_yoy, roe, debt_ratio, gross_margin}
 * @returns {Promise<Object|null>} scoreStock 结果（dimensions 已适配详情页数组模板）；null = 本地不可用
 */
export async function computeLocalScore(code, stockInfo = {}, finance = null, weights = null) {
  let technical = null
  try {
    const ind = await getIndicator(code)
    const series = ind && ind._series
    if (series && seriesIsFresh(series)) technical = series
  } catch { /* 指标包缺失 → 现算兜底 */ }
  if (!technical) {
    const klines = await getKlines(code, 150)
    if (!klines || klines.length < 30) return null
    technical = calcTechnicalSimple(appendTodayBar(klines, stockInfo))
  }
  if (!technical) return null

  const result = scoreStock({
    code,
    name: stockInfo.name || code,
    technicalData: technical,
    stockInfo,
    finance,
    weights,
  })

  // 详情页模板期望 dimensions 为数组（后端 ScoreResult 形状）：
  // scoreStock 返回对象 → 按展示顺序转数组，details 的 {分值/满分} 形状两端一致
  const dimMap = result.dimensions || {}
  const order = ['technical', 'capital', 'fundamental', 'growth', 'quality']
  const nameMap = { technical: '技术面', capital: '资金面', fundamental: '基本面', growth: '成长', quality: '质量' }
  result.dimensions = order
    .filter(k => dimMap[k])
    .map(k => ({
      name: nameMap[k],
      score: dimMap[k].score,
      details: dimMap[k].details,
      weighted_score: dimMap[k].weighted_score,
    }))
  return result
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
