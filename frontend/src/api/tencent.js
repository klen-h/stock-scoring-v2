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
// 腾讯 API 格式：/q=sz000001,sz000002（= 是路径的一部分，不是查询参数）
// 开发环境：通过 Vite 代理（/tencent-api/q=xxx → qt.gtimg.cn/q=xxx）
// 生产环境：通过 Cloudflare Worker 代理（worker.example/q=xxx → qt.gtimg.cn/q=xxx）

// 自动检测环境
const isDev = typeof import.meta !== 'undefined' && import.meta.env?.DEV

// 生产环境 Cloudflare Worker 代理 URL（部署后配置）
// 格式：'https://your-worker.workers.dev'（不带末尾斜杠）
const CF_PROXY_URL = ''

// 根据环境选择基础 URL
let BASE_URL
if (isDev) {
  BASE_URL = '/tencent-api'  // Vite 代理前缀
} else if (CF_PROXY_URL) {
  BASE_URL = CF_PROXY_URL    // Cloudflare Worker
} else {
  BASE_URL = 'https://qt.gtimg.cn'  // 直连（可能被 CORS 拦截）
}

// 每批请求数量（避免触发 WAF）
const BATCH_SIZE = 50

// 请求超时（毫秒）
const TIMEOUT = 10000

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
      // CORS 或网络错误，记录但不重试（代理已在环境层配置）
      if (error.message.includes('Failed to fetch') || error.message.includes('CORS')) {
        console.error('网络请求失败，请检查代理配置')
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

  // 构造 URL：BASE_URL + /q=sz000001,sz000002
  const url = `${BASE_URL}/q=${symbols}`

  const response = await fetchWithTimeout(url, TIMEOUT)
  
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

      // 字段数不足 50 说明数据残缺，跳过（市值/ PB 字段在 44~46）
      if (fields.length < 50) continue

      // 字段索引与后端 tencent.py 完全对齐（data[N] 对应腾讯协议固定含义）
      const code = fields[2]
      const name = fields[1]
      const price = parseFloat(fields[3]) || 0
      const prevClose = parseFloat(fields[4]) || 0
      const open = parseFloat(fields[5]) || 0
      const volume = parseFloat(fields[6]) || 0  // 成交量（手）
      const buyVolume = parseFloat(fields[7]) || 0
      const sellVolume = parseFloat(fields[8]) || 0
      const change_pct = parseFloat(fields[32]) || 0   // 涨跌幅 %
      const amount = parseFloat(fields[37]) || 0       // 成交额（万元）
      const turnover_rate = parseFloat(fields[38]) || 0   // 换手率 %
      const pe = parseFloat(fields[39]) || 0           // 市盈率 PE（腾讯协议 data[39]）
      const pb = parseFloat(fields[46]) || 0           // 市净率 PB（data[46]；data[40] 实测为空）
      const high = parseFloat(fields[41]) || 0         // 最高（后端 data[41]）
      const low = parseFloat(fields[42]) || 0          // 最低（后端 data[42]）
      const amplitude = parseFloat(fields[43]) || 0    // 振幅 %（后端 data[43]）
      // data[44]=流通市值（亿元）、data[45]=总市值（亿元），实测验证；
      // data[57] 实为成交额、data[58] 无效，早期误用已修正。×10000 转万元，与后端单位对齐。
      const market_cap = (parseFloat(fields[45]) || 0) * 10000  // 总市值（万元）
      const float_cap = (parseFloat(fields[44]) || 0) * 10000   // 流通市值（万元）

      if (price > 0 && name) {
        result[code] = {
          code,
          name,
          price,
          yesterday_close: prevClose,
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
          market_cap,            // 万元（与后端单位一致）
          float_cap,             // 万元（与后端单位一致）
          amount: amount * 10000,  // 转换为元
          amplitude,               // 振幅 %（接口直接提供）
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
 * 测试连接是否可用
 * @returns {Promise<boolean>}
 */
export async function testCORS() {
  try {
    const url = `${BASE_URL}/q=sz000001`
    
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
