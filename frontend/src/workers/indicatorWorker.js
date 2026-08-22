/**
 * 技术指标计算 Web Worker
 * 
 * 功能：
 *   - 在后台线程计算技术指标（MA/MACD/RSI/KDJ/BOLL）
 *   - 不阻塞 UI 线程
 *   - 支持全量计算和增量更新
 * 
 * 消息协议：
 *   主线程 → Worker:
 *     { type: 'CALC', code: '000001', klines: [[date, open, high, low, close, volume], ...] }
 *     { type: 'INCREMENTAL', code: '000001', newPrice: 12.5, state: {...} }
 *     { type: 'BATCH_CALC', items: [{ code, klines }, ...] }
 *   
 *   Worker → 主线程:
 *     { type: 'RESULT', code: '000001', indicators: {...}, series: [...] }
 *     { type: 'BATCH_RESULT', results: [{ code, indicators, series }, ...] }
 *     { type: 'ERROR', code: '000001', message: '...' }
 */

// ── 工具函数 ──

/**
 * 计算简单移动平均（SMA/MA）
 */
function calcMA(data, window) {
  const result = new Array(data.length).fill(null)
  if (data.length < window) return result
  
  let sum = 0
  for (let i = 0; i < window; i++) {
    sum += data[i]
  }
  result[window - 1] = sum / window
  
  for (let i = window; i < data.length; i++) {
    sum += data[i] - data[i - window]
    result[i] = sum / window
  }
  
  return result
}

/**
 * 计算指数移动平均（EMA）
 */
function calcEMA(data, span) {
  const result = new Array(data.length)
  const alpha = 2.0 / (span + 1)
  
  result[0] = data[0]
  for (let i = 1; i < data.length; i++) {
    result[i] = data[i] * alpha + result[i - 1] * (1 - alpha)
  }
  
  return result
}

/**
 * 计算 MACD（DIF, DEA, MACD柱）
 */
function calcMACD(closes) {
  const ema12 = calcEMA(closes, 12)
  const ema26 = calcEMA(closes, 26)
  
  const dif = new Array(closes.length)
  for (let i = 0; i < closes.length; i++) {
    dif[i] = ema12[i] - ema26[i]
  }
  
  const dea = calcEMA(dif, 9)
  
  const macd = new Array(closes.length)
  for (let i = 0; i < closes.length; i++) {
    macd[i] = (dif[i] - dea[i]) * 2
  }
  
  return { dif, dea, macd }
}

/**
 * 计算 RSI（与后端 _calc_technical_fast 一致：近 14 日简单平均口径，非 Wilder 平滑）
 */
function calcRSI(closes, period = 14) {
  const result = new Array(closes.length).fill(null)
  if (closes.length < period + 1) return result

  for (let i = period; i < closes.length; i++) {
    let avgGain = 0
    let avgLoss = 0
    // 窗口 = delta[i-13 .. i]，即 closes[i-14 .. i] 的 14 个变化量（与后端一致）
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

/**
 * 计算 KDJ
 */
function calcKDJ(highs, lows, closes, n = 9) {
  const length = closes.length
  const kValues = new Array(length).fill(50)
  const dValues = new Array(length).fill(50)
  const jValues = new Array(length).fill(50)
  
  if (length < n) return { k: kValues, d: dValues, j: jValues }
  
  let prevK = 50
  let prevD = 50
  
  for (let i = n - 1; i < length; i++) {
    // 计算 n 日内最高价和最低价
    let highN = -Infinity
    let lowN = Infinity
    for (let j = i - n + 1; j <= i; j++) {
      if (highs[j] > highN) highN = highs[j]
      if (lows[j] < lowN) lowN = lows[j]
    }
    
    // 计算 RSV
    const rsv = highN === lowN ? 50 : ((closes[i] - lowN) / (highN - lowN)) * 100
    
    // 计算 K, D
    const k = (2 / 3) * prevK + (1 / 3) * rsv
    const d = (2 / 3) * prevD + (1 / 3) * k
    const j = 3 * k - 2 * d
    
    kValues[i] = k
    dValues[i] = d
    jValues[i] = j
    
    prevK = k
    prevD = d
  }
  
  return { k: kValues, d: dValues, j: jValues }
}

/**
 * 计算布林带（BOLL）
 */
function calcBOLL(closes, window = 20, numStd = 2) {
  const mid = calcMA(closes, window)
  const upper = new Array(closes.length).fill(null)
  const lower = new Array(closes.length).fill(null)
  
  for (let i = window - 1; i < closes.length; i++) {
    let sum = 0
    for (let j = i - window + 1; j <= i; j++) {
      sum += (closes[j] - mid[i]) ** 2
    }
    const std = Math.sqrt(sum / window)
    
    upper[i] = mid[i] + numStd * std
    lower[i] = mid[i] - numStd * std
  }
  
  return { upper, mid, lower }
}

/**
 * 计算所有技术指标
 * @param {Array} klines - K线数据 [[date, open, high, low, close, volume], ...]
 * @returns {Object} { series: [...], latest: {...} }
 */
function calcTechnical(klines) {
  if (!klines || klines.length < 30) {
    return null
  }
  
  const length = klines.length
  
  // 提取价格数组
  const closes = new Array(length)
  const highs = new Array(length)
  const lows = new Array(length)
  const opens = new Array(length)
  const volumes = new Array(length)
  const dates = new Array(length)
  
  for (let i = 0; i < length; i++) {
    const k = klines[i]
    dates[i] = k[0]
    opens[i] = k[1]
    highs[i] = k[2]
    lows[i] = k[3]
    closes[i] = k[4]
    volumes[i] = k[5]
  }
  
  // 计算各指标
  const ma5 = calcMA(closes, 5)
  const ma10 = calcMA(closes, 10)
  const ma20 = calcMA(closes, 20)
  const ma60 = calcMA(closes, 60)
  
  const { dif, dea, macd } = calcMACD(closes)
  const rsi = calcRSI(closes, 14)
  const { k, d, j } = calcKDJ(highs, lows, closes, 9)
  const boll = calcBOLL(closes, 20, 2)
  
  // 组装结果序列
  const series = new Array(length)
  for (let i = 0; i < length; i++) {
    series[i] = {
      date: dates[i],
      close: round2(closes[i]),
      open: round2(opens[i]),
      high: round2(highs[i]),
      low: round2(lows[i]),
      volume: volumes[i],
      ma5: round2(ma5[i]),
      ma10: round2(ma10[i]),
      ma20: round2(ma20[i]),
      ma60: round2(ma60[i]),
      dif: round2(dif[i]),
      dea: round2(dea[i]),
      macd: round2(macd[i]),
      rsi: round2(rsi[i]),
      k: round2(k[i]),
      d: round2(d[i]),
      j: round2(j[i]),
      boll_upper: round2(boll.upper[i]),
      boll_mid: round2(boll.mid[i]),
      boll_lower: round2(boll.lower[i]),
    }
  }
  
  // 最新一行
  const latest = series[length - 1]
  
  return { series, latest }
}

/**
 * 四舍五入到 2 位小数
 */
function round2(val) {
  if (val === null || val === undefined || isNaN(val)) return null
  return Math.round(val * 100) / 100
}

// ── Worker 消息处理 ──

self.onmessage = function(e) {
  const { type, code, klines, items, newPrice, state } = e.data
  
  try {
    if (type === 'CALC') {
      // 单只股票全量计算
      const result = calcTechnical(klines)
      if (result) {
        self.postMessage({
          type: 'RESULT',
          code,
          series: result.series,
          latest: result.latest,
        })
      } else {
        self.postMessage({
          type: 'ERROR',
          code,
          message: 'K线数据不足',
        })
      }
    }
    
    else if (type === 'BATCH_CALC') {
      // 批量计算
      const results = []
      for (const item of items) {
        const result = calcTechnical(item.klines)
        if (result) {
          results.push({
            code: item.code,
            series: result.series,
            latest: result.latest,
          })
        }
      }
      self.postMessage({
        type: 'BATCH_RESULT',
        results,
      })
    }
    
    else if (type === 'INCREMENTAL') {
      // 增量更新（简化版：重新计算最后几个值）
      // 实际生产中应该维护状态，这里为了简化先全量重算
      // TODO: 实现真正的增量更新
      self.postMessage({
        type: 'ERROR',
        code,
        message: '增量更新暂未实现，请使用全量计算',
      })
    }
    
  } catch (error) {
    self.postMessage({
      type: 'ERROR',
      code: code || 'unknown',
      message: error.message,
    })
  }
}

// 通知主线程 Worker 已就绪
self.postMessage({ type: 'READY' })
