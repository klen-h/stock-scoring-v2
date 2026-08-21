/**
 * K 线数据 IndexedDB 存储层
 * 
 * 功能：
 *   - 存储历史 K 线数据（约 100MB）
 *   - 支持完整数据包导入
 *   - 支持增量更新
 *   - 版本控制（记录数据日期）
 * 
 * 数据结构：
 *   stocks 表：{ code, name, market_cap, klines: [[date, open, high, low, close, volume], ...] }
 *   meta 表：{ key, value } 用于存储版本信息
 */

const DB_NAME = 'kline_data'
const DB_VERSION = 1
const STORE_STOCKS = 'stocks'
const STORE_META = 'meta'

let db = null

/**
 * 初始化 IndexedDB
 * @returns {Promise<IDBDatabase>}
 */
export async function initKlineDB() {
  if (db) return db

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION)

    request.onerror = () => reject(request.error)
    request.onsuccess = () => {
      db = request.result
      resolve(db)
    }

    request.onupgradeneeded = (event) => {
      const database = event.target.result

      // 创建 stocks 对象仓库
      if (!database.objectStoreNames.contains(STORE_STOCKS)) {
        const stockStore = database.createObjectStore(STORE_STOCKS, { keyPath: 'code' })
        stockStore.createIndex('name', 'name', { unique: false })
        stockStore.createIndex('market_cap', 'market_cap', { unique: false })
      }

      // 创建 meta 对象仓库（用于存储版本信息）
      if (!database.objectStoreNames.contains(STORE_META)) {
        database.createObjectStore(STORE_META, { keyPath: 'key' })
      }
    }
  })
}

/**
 * 导入完整数据包
 * @param {Object} data - 数据包 { version, date, stocks: { code: { name, market_cap, klines } } }
 * @param {Function} onProgress - 进度回调 (loaded, total)
 * @returns {Promise<{ imported: number }>}
 */
export async function importKlinePack(data, onProgress) {
  if (!db) await initKlineDB()

  const stocks = data.stocks || {}
  const codes = Object.keys(stocks)
  const total = codes.length
  let imported = 0

  // 使用事务批量写入
  const batchSize = 100
  for (let i = 0; i < total; i += batchSize) {
    const batch = codes.slice(i, i + batchSize)
    
    await new Promise((resolve, reject) => {
      const tx = db.transaction([STORE_STOCKS], 'readwrite')
      const store = tx.objectStore(STORE_STOCKS)

      for (const code of batch) {
        const stock = stocks[code]
        store.put({
          code,
          name: stock.name,
          market_cap: stock.market_cap || 0,
          klines: stock.klines || [],
        })
      }

      tx.oncomplete = () => {
        imported += batch.length
        onProgress?.(imported, total)
        resolve()
      }
      tx.onerror = () => reject(tx.error)
    })
  }

  // 更新版本信息
  await setMeta('last_update', data.date)
  await setMeta('total_stocks', total)

  return { imported }
}

/**
 * 应用增量更新
 * @param {Object} delta - 增量包 { version, date, stocks: { code: { name, market_cap, klines } } }
 * @returns {Promise<{ updated: number, added: number }>}
 */
export async function applyDelta(delta) {
  if (!db) await initKlineDB()

  const stocks = delta.stocks || {}
  const codes = Object.keys(stocks)
  let updated = 0
  let added = 0

  // 先获取现有数据，判断是新增还是更新
  const existingCodes = await getAllStockCodes()
  const existingSet = new Set(existingCodes)

  // 批量写入
  const tx = db.transaction([STORE_STOCKS], 'readwrite')
  const store = tx.objectStore(STORE_STOCKS)

  for (const code of codes) {
    const stock = stocks[code]
    
    if (existingSet.has(code)) {
      // 更新：合并 K 线（去重）
      const existing = await getStock(code)
      if (existing) {
        const mergedKlines = mergeKlines(existing.klines, stock.klines)
        store.put({
          code,
          name: stock.name || existing.name,
          market_cap: stock.market_cap || existing.market_cap,
          klines: mergedKlines,
        })
        updated++
      }
    } else {
      // 新增
      store.put({
        code,
        name: stock.name,
        market_cap: stock.market_cap || 0,
        klines: stock.klines || [],
      })
      added++
    }
  }

  await new Promise((resolve, reject) => {
    tx.oncomplete = resolve
    tx.onerror = () => reject(tx.error)
  })

  // 更新版本信息
  await setMeta('last_update', delta.date)

  return { updated, added }
}

/**
 * 合并 K 线数据（按日期去重）
 */
function mergeKlines(existing, incoming) {
  const map = new Map()
  
  // 先放现有的
  for (const kline of existing) {
    map.set(kline[0], kline)
  }
  
  // 再放新的（覆盖同日期）
  for (const kline of incoming) {
    map.set(kline[0], kline)
  }
  
  // 按日期排序
  return Array.from(map.values()).sort((a, b) => a[0].localeCompare(b[0]))
}

/**
 * 获取单只股票的 K 线数据
 * @param {string} code - 股票代码
 * @param {number} days - 获取最近 N 天（默认全部）
 * @returns {Promise<Array|null>}
 */
export async function getKlines(code, days) {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_STOCKS], 'readonly')
    const store = tx.objectStore(STORE_STOCKS)
    const request = store.get(code)

    request.onsuccess = () => {
      const result = request.result
      if (!result) {
        resolve(null)
        return
      }

      let klines = result.klines || []
      if (days && klines.length > days) {
        klines = klines.slice(-days)
      }
      resolve(klines)
    }
    request.onerror = () => reject(request.error)
  })
}

/**
 * 获取单只股票的完整信息
 */
export async function getStock(code) {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_STOCKS], 'readonly')
    const store = tx.objectStore(STORE_STOCKS)
    const request = store.get(code)

    request.onsuccess = () => resolve(request.result || null)
    request.onerror = () => reject(request.error)
  })
}

/**
 * 获取所有股票代码
 * @returns {Promise<string[]>}
 */
export async function getAllStockCodes() {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_STOCKS], 'readonly')
    const store = tx.objectStore(STORE_STOCKS)
    const request = store.getAllKeys()

    request.onsuccess = () => resolve(request.result || [])
    request.onerror = () => reject(request.error)
  })
}

/**
 * 获取所有股票（含名称、市值）
 * @returns {Promise<Array<{ code, name, market_cap }>>}
 */
export async function getAllStocks() {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_STOCKS], 'readonly')
    const store = tx.objectStore(STORE_STOCKS)
    const request = store.getAll()

    request.onsuccess = () => {
      const stocks = request.result || []
      // 只返回必要字段，减少内存占用
      resolve(stocks.map(s => ({
        code: s.code,
        name: s.name,
        market_cap: s.market_cap,
      })))
    }
    request.onerror = () => reject(request.error)
  })
}

/**
 * 获取元数据
 */
async function getMeta(key) {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_META], 'readonly')
    const store = tx.objectStore(STORE_META)
    const request = store.get(key)

    request.onsuccess = () => {
      resolve(request.result?.value ?? null)
    }
    request.onerror = () => reject(request.error)
  })
}

/**
 * 设置元数据
 */
async function setMeta(key, value) {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_META], 'readwrite')
    const store = tx.objectStore(STORE_META)
    store.put({ key, value })

    tx.oncomplete = resolve
    tx.onerror = () => reject(tx.error)
  })
}

/**
 * 获取最后更新日期
 * @returns {Promise<string|null>} 格式 YYYYMMDD
 */
export async function getLastUpdateDate() {
  return getMeta('last_update')
}

/**
 * 获取存储的股票总数
 * @returns {Promise<number>}
 */
export async function getStockCount() {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_STOCKS], 'readonly')
    const store = tx.objectStore(STORE_STOCKS)
    const request = store.count()

    request.onsuccess = () => resolve(request.result || 0)
    request.onerror = () => reject(request.error)
  })
}

/**
 * 清空所有数据
 */
export async function clearAll() {
  if (!db) await initKlineDB()

  const tx = db.transaction([STORE_STOCKS, STORE_META], 'readwrite')
  tx.objectStore(STORE_STOCKS).clear()
  tx.objectStore(STORE_META).clear()

  return new Promise((resolve, reject) => {
    tx.oncomplete = resolve
    tx.onerror = () => reject(tx.error)
  })
}

/**
 * 检查并更新数据
 * 自动下载最新数据包或增量包
 * @param {string} baseUrl - 数据包基础 URL（GitHub Pages）
 * @param {Function} onProgress - 进度回调
 * @returns {Promise<{ updated: boolean, message: string }>}
 */
export async function checkAndUpdate(baseUrl = 'https://your-username.github.io/your-repo/data', onProgress) {
  const lastUpdate = await getLastUpdateDate()
  const today = new Date()
  const todayStr = today.toISOString().slice(0, 10).replace(/-/g, '')

  // 如果今天已经更新过，跳过
  if (lastUpdate === todayStr) {
    return { updated: false, message: '数据已是最新' }
  }

  // 首次使用，下载完整包
  if (!lastUpdate) {
    onProgress?.({ stage: 'download', message: '首次加载，下载数据包...' })
    const packUrl = `${baseUrl}/kline-pack-latest.json.gz`
    
    try {
      const response = await fetch(packUrl)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      
      onProgress?.({ stage: 'decompress', message: '解压数据...' })
      const blob = await response.blob()
      const text = await decompressGzip(blob)
      const data = JSON.parse(text)
      
      onProgress?.({ stage: 'import', message: '导入数据...' })
      const result = await importKlinePack(data, (loaded, total) => {
        onProgress?.({ stage: 'import', loaded, total })
      })
      
      return { updated: true, message: `导入 ${result.imported} 只股票` }
    } catch (error) {
      return { updated: false, message: `下载失败: ${error.message}` }
    }
  }

  // 尝试增量更新
  const deltaUrl = `${baseUrl}/kline-delta-${todayStr}.json`
  try {
    onProgress?.({ stage: 'download', message: '检查增量更新...' })
    const response = await fetch(deltaUrl)
    
    if (response.ok) {
      const delta = await response.json()
      onProgress?.({ stage: 'apply', message: '应用增量更新...' })
      const result = await applyDelta(delta)
      return { 
        updated: true, 
        message: `更新 ${result.updated} 只，新增 ${result.added} 只` 
      }
    } else {
      // 增量包不存在，下载完整包
      onProgress?.({ stage: 'download', message: '下载完整数据包...' })
      const packUrl = `${baseUrl}/kline-pack-latest.json.gz`
      const packResponse = await fetch(packUrl)
      if (!packResponse.ok) throw new Error(`HTTP ${packResponse.status}`)
      
      const blob = await packResponse.blob()
      const text = await decompressGzip(blob)
      const data = JSON.parse(text)
      
      const result = await importKlinePack(data, (loaded, total) => {
        onProgress?.({ stage: 'import', loaded, total })
      })
      
      return { updated: true, message: `导入 ${result.imported} 只股票` }
    }
  } catch (error) {
    return { updated: false, message: `更新失败: ${error.message}` }
  }
}

/**
 * 解压 gzip 数据
 */
async function decompressGzip(blob) {
  // 使用 DecompressionStream API（现代浏览器支持）
  if (typeof DecompressionStream !== 'undefined') {
    const stream = blob.stream().pipeThrough(new DecompressionStream('gzip'))
    const decompressed = new Response(stream)
    return decompressed.text()
  }
  
  // 降级：使用 pako 库（需要额外引入）
  // 这里假设浏览器原生支持 DecompressionStream
  throw new Error('浏览器不支持 gzip 解压')
}
