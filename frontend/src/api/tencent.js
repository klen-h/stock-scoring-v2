/**
 * 腾讯行情 API 客户端
 * 
 * 功能：
 *   - 批量拉取 A 股实时行情
 *   - 解析腾讯 API 返回格式
 *   - 支持分批请求（避免触发限制）
 *   - CORS 问题自动降级到代理
 * 
 * 数据源：qt.gtimg.cn（腾讯财经公开接口）
 * 返回格式：v_sz000001="51~平安银行~000001~10.5~10.3~..."
 */

// 腾讯行情 API 基础 URL
const TENCENT_API = 'https://qt.gtimg.cn/q='

// 每批请求数量（避免触发 WAF）
const BATCH_SIZE = 50

// 请求超时（毫秒）
const TIMEOUT = 10000

// 是否使用代理（CORS 问题时自动启用）
let useProxy = false
const PROXY_URL = 'https://your-worker.workers.dev/proxy?url='

/**
 * 批量拉取实时行情
 * @param {string[]} codes - 股票代码列表（纯数字，如 ['000001', '600519']）
 * @returns {Promise<Object>} { code: { name, price, change_pct, market_cap, pe, pb, ... } }
 */
export async function fetchRealtimeQuotes(codes) {
  if (!codes || codes.length === 0) return {}

  const result = {}
  const batches = chunk(codes, BATCH_SIZE)

  for (let i = 0; i < batches.length; i++) {
    const batch = batches[i]
    try {
      const batchResult = await fetchBatch(batch)
      Object.assign(result, batchResult)
    } catch (error) {
      console.warn(`批次 ${i + 1} 失败:`, error.message)
      // 如果是 CORS 错误，切换到代理模式
      if (error.message.includes('CORS') || error.message.includes('Failed to fetch')) {
        useProxy = true
        console.log('检测到 CORS 问题，启用代理模式')
        // 重试当前批次
        try {
          const retryResult = await fetchBatch(batch)
          Object.assign(result, retryResult)
        } catch (retryError) {
          console.error(`批次 ${i + 1} 重试失败:`, retryError.message)
        }
      }
    }

    // 避免请求过快
    if (i < batches.length - 1) {
      await sleep(100)
    }
  }

  return result
}

/**
 * 拉取单批行情
 */
async function fetchBatch(codes) {
  const symbols = codes.map(code => {
    const prefix = code.startsWith('6') ? 'sh' : 'sz'
    return `${prefix}${code}`
  }).join(',')

  const url = TENCENT_API + symbols
  const finalUrl = useProxy ? PROXY_URL + encodeURIComponent(url) : url

  const response = await fetchWithTimeout(finalUrl, TIMEOUT)
  
  // 腾讯 API 返回 GBK 编码
  const buffer = await response.arrayBuffer()
  const text = decodeGBK(buffer)

  return parseTencentResponse(text)
}

/**
 * 解析腾讯 API 响应
 * 格式：v_sz000001="51~平安银行~000001~10.5~10.3~..."
 */
function parseTencentResponse(text) {
  const result = {}
  const lines = text.split(';')

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed || !trimmed.includes('=')) continue

    try {
      const [varName, dataStr] = trimmed.split('=', 2)
      const data = dataStr.replace(/"/g, '').trim()
      const fields = data.split('~')

      if (fields.length < 50) continue

      const code = fields[2]
      const name = fields[1]
      const price = parseFloat(fields[3]) || 0
      const yesterdayClose = parseFloat(fields[4]) || 0
      const open = parseFloat(fields[5]) || 0
      const volume = parseFloat(fields[6]) || 0  // 成交量（手）
      const buyVolume = parseFloat(fields[7]) || 0
      const sellVolume = parseFloat(fields[8]) || 0
      const high = parseFloat(fields[33]) || 0
      const low = parseFloat(fields[34]) || 0
      const change_pct = parseFloat(fields[32]) || 0
      const turnover_rate = parseFloat(fields[38]) || 0
      const pe = parseFloat(fields[39]) || 0
      const pb = parseFloat(fields[46]) || 0
      const market_cap = parseFloat(fields[45]) || 0  // 总市值（亿）
      const float_cap = parseFloat(fields[44]) || 0  // 流通市值（亿）
      const amount = parseFloat(fields[37]) || 0  // 成交额（万元）

      if (price > 0 && name) {
        result[code] = {
          code,
          name,
          price,
          yesterday_close: yesterdayClose,
          open,
          high,
          low,
          volume: volume * 100,  // 转换为股
          buy_volume: buyVolume * 100,
          sell_volume: sellVolume * 100,
          change_pct,
          turnover_rate,
          pe,
          pb,
          market_cap: market_cap * 10000,  // 转换为万元
          float_cap: float_cap * 10000,
          amount: amount * 10000,  // 转换为元
        }
      }
    } catch (error) {
      // 忽略解析错误的行
      continue
    }
  }

  return result
}

/**
 * 解码 GBK 编码
 */
function decodeGBK(buffer) {
  // 使用 TextDecoder API（现代浏览器支持）
  try {
    const decoder = new TextDecoder('gbk')
    return decoder.decode(buffer)
  } catch (error) {
    // 降级：手动解码（简化版，可能不完美）
    console.warn('GBK 解码失败，使用 UTF-8 降级')
    const decoder = new TextDecoder('utf-8')
    return decoder.decode(buffer)
  }
}

/**
 * 带超时的 fetch
 */
async function fetchWithTimeout(url, timeout) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), timeout)

  try {
    const response = await fetch(url, { signal: controller.signal })
    clearTimeout(timeoutId)
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }
    
    return response
  } catch (error) {
    clearTimeout(timeoutId)
    if (error.name === 'AbortError') {
      throw new Error('请求超时')
    }
    throw error
  }
}

/**
 * 数组分块
 */
function chunk(array, size) {
  const result = []
  for (let i = 0; i < array.length; i += size) {
    result.push(array.slice(i, i + size))
  }
  return result
}

/**
 * 睡眠函数
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

/**
 * 测试 CORS 是否可用
 * @returns {Promise<boolean>}
 */
export async function testCORS() {
  try {
    const testCode = '000001'
    const prefix = 'sz'
    const url = TENCENT_API + prefix + testCode
    
    const response = await fetchWithTimeout(url, 5000)
    const buffer = await response.arrayBuffer()
    const text = decodeGBK(buffer)
    
    return text.includes('v_sz000001')
  } catch (error) {
    return false
  }
}

/**
 * 获取单只股票实时行情
 * @param {string} code - 股票代码
 * @returns {Promise<Object|null>}
 */
export async function fetchSingleQuote(code) {
  const result = await fetchRealtimeQuotes([code])
  return result[code] || null
}

/**
 * 批量获取多只股票实时行情（简化版，只返回必要字段）
 * @param {string[]} codes - 股票代码列表
 * @returns {Promise<Object>} { code: { price, change_pct } }
 */
export async function fetchBatchPrices(codes) {
  const full = await fetchRealtimeQuotes(codes)
  
  const simplified = {}
  for (const [code, data] of Object.entries(full)) {
    simplified[code] = {
      price: data.price,
      change_pct: data.change_pct,
    }
  }
  
  return simplified
}
