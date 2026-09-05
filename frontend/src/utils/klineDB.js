/**
 * K 线数据 IndexedDB 存储层
 *
 * 功能：
 *   - 存储历史 K 线数据（约 100MB）
 *   - 支持完整数据包导入
 *   - 支持增量更新
 *   - 版本控制（记录数据日期）
 *   - 存储预计算指标包（indicators-pack：_series 500 天口径，评分统一事实源）
 *
 * 数据结构：
 *   stocks 表：{ code, name, market_cap, klines: [[date, open, high, low, close, volume], ...] }
 *   indicators 表（DB v2）：{ code, ind: {ma5..._series: [近60天指标数组], _state} }
 *   meta 表：{ key, value } 用于存储版本信息
 */

const DB_NAME = 'kline_data'
const DB_VERSION = 2
const STORE_STOCKS = 'stocks'
const STORE_INDICATORS = 'indicators'
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

      // v2：预计算指标包（_series，评分/详情页零现算的事实源）
      if (!database.objectStoreNames.contains(STORE_INDICATORS)) {
        database.createObjectStore(STORE_INDICATORS, { keyPath: 'code' })
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

  // 更新版本信息（记录数据包版本，用于检测数据结构升级）
  await setMeta('last_update', data.date)
  await setMeta('pack_version', data.version || 1)
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

  // 更新版本信息（记录数据包版本，用于检测数据结构升级）
  await setMeta('last_update', delta.date)
  if (delta.version) await setMeta('pack_version', delta.version)

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

// ── 预计算指标包（indicators store，PLAN_PACK_MIGRATION Phase 2）──────────────

/**
 * 导入指标数据包
 * @param {Object} data - { version, date, indicators: { code: {ma5..., _series: [...], _state} } }
 * @param {Function} onProgress - 进度回调 (loaded, total)
 * @returns {Promise<{ imported: number }>}
 */
export async function importIndicatorsPack(data, onProgress) {
  if (!db) await initKlineDB()

  const indicators = data.indicators || {}
  const codes = Object.keys(indicators)
  const total = codes.length
  let imported = 0

  const batchSize = 200
  for (let i = 0; i < total; i += batchSize) {
    const batch = codes.slice(i, i + batchSize)

    await new Promise((resolve, reject) => {
      const tx = db.transaction([STORE_INDICATORS], 'readwrite')
      const store = tx.objectStore(STORE_INDICATORS)

      for (const code of batch) {
        store.put({ code, ind: indicators[code] })
      }

      tx.oncomplete = () => {
        imported += batch.length
        onProgress?.(imported, total)
        resolve()
      }
      tx.onerror = () => reject(tx.error)
    })
  }

  // 指标包日期独立记录（与 K 线包对齐时跳过重复下载）
  await setMeta('ind_last_update', data.date)
  await setMeta('ind_count', total)
  return { imported }
}

/**
 * 读取单只股票的预计算指标（含 _series）
 * @returns {Promise<Object|null>}
 */
export async function getIndicator(code) {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_INDICATORS], 'readonly')
    const store = tx.objectStore(STORE_INDICATORS)
    const request = store.get(code)

    request.onsuccess = () => resolve(request.result?.ind || null)
    request.onerror = () => reject(request.error)
  })
}

/**
 * 指标包日期（YYYYMMDD，与 K 线包对齐时无需重复下载）
 */
export async function getIndicatorsDate() {
  return getMeta('ind_last_update')
}

/**
 * 已存指标股票数
 */
export async function getIndicatorsCount() {
  if (!db) await initKlineDB()

  return new Promise((resolve, reject) => {
    const tx = db.transaction([STORE_INDICATORS], 'readonly')
    const store = tx.objectStore(STORE_INDICATORS)
    const request = store.count()

    request.onsuccess = () => resolve(request.result || 0)
    request.onerror = () => reject(request.error)
  })
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

  const tx = db.transaction([STORE_STOCKS, STORE_INDICATORS, STORE_META], 'readwrite')
  tx.objectStore(STORE_STOCKS).clear()
  tx.objectStore(STORE_INDICATORS).clear()
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

  // 数据包结构版本：version 2 = 150 天 K 线（评分需要足够历史让 EMA 系列指标收敛）
  const REQUIRED_PACK_VERSION = 2
  // 缓存破坏参数：GitHub Pages 有 CDN 缓存，同一文件名可能被浏览器/CDN 返回旧版本，
  // 导致「下载到旧版包 → 写入旧版本号 → 下次又被判需重下」的循环，故每次请求强制绕过缓存
  const cacheBust = `t=${Date.now()}`
  const localPackVersion = await getMeta('pack_version') || 1
  const needFullRedownload = lastUpdate && localPackVersion < REQUIRED_PACK_VERSION

  // 如果今天已经更新过且数据包版本匹配，跳过（版本不匹配时强制重新下载）
  if (lastUpdate === todayStr && !needFullRedownload) {
    return { updated: false, message: '数据已是最新' }
  }

  // 首次使用或数据包结构升级，下载完整包（增量包无法补齐历史天数）
  if (!lastUpdate || needFullRedownload) {
    onProgress?.({ stage: 'download', message: needFullRedownload ? '数据结构升级，重新下载完整包...' : '首次加载，下载数据包...' })
    const packUrl = `${baseUrl}/kline-pack-latest.json.gz?${cacheBust}`
    
    try {
      const response = await fetch(packUrl, { cache: 'no-store' })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      
      onProgress?.({ stage: 'decompress', message: '解压数据...' })
      const blob = await response.blob()
      const text = await decompressGzip(blob)
      const data = JSON.parse(text)

      // 防御：服务端/CDN 仍返回旧版包时拒绝导入（否则旧版本号会污染本地元信息）
      if ((data.version || 1) < REQUIRED_PACK_VERSION) {
        return { updated: false, message: `服务器数据包仍为旧版（v${data.version || 1}），请确认数据生成工作流已用新代码重新运行` }
      }
      
      onProgress?.({ stage: 'import', message: '导入数据...' })
      const result = await importKlinePack(data, (loaded, total) => {
        onProgress?.({ stage: 'import', loaded, total })
      })
      
      return { updated: true, message: `导入 ${result.imported} 只股票` }
    } catch (error) {
      return { updated: false, message: `下载失败: ${error.message}` }
    }
  }

  // 尝试增量更新（本地已是新版数据时才允许走增量路径）
  const deltaUrl = `${baseUrl}/kline-delta-${todayStr}.json?${cacheBust}`
  try {
    onProgress?.({ stage: 'download', message: '检查增量更新...' })
    const response = await fetch(deltaUrl, { cache: 'no-store' })
    
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
      const packUrl = `${baseUrl}/kline-pack-latest.json.gz?${cacheBust}`
      const packResponse = await fetch(packUrl, { cache: 'no-store' })
      if (!packResponse.ok) throw new Error(`HTTP ${packResponse.status}`)
      
      const blob = await packResponse.blob()
      const text = await decompressGzip(blob)
      const data = JSON.parse(text)

      // 防御：旧版包拒绝导入（同上）
      if ((data.version || 1) < REQUIRED_PACK_VERSION) {
        return { updated: false, message: `服务器数据包仍为旧版（v${data.version || 1}），请确认数据生成工作流已用新代码重新运行` }
      }
      
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
 * 优先 DecompressionStream（现代浏览器原生）；失败降级 pako 纯 JS 解压。
 * ★ 实测部分内嵌浏览器（IAB）DecompressionStream 构造器存在但流读取必抛
 *   "Failed to fetch" —— 因此 catch 后必须真降级，不能只判 typeof。
 */
async function decompressGzip(blob) {
  if (typeof DecompressionStream !== 'undefined') {
    try {
      const stream = blob.stream().pipeThrough(new DecompressionStream('gzip'))
      const decompressed = new Response(stream)
      return decompressed.text()
    } catch (e) {
      console.warn('[klineDB] DecompressionStream 失败，降级 pako:', e?.message)
    }
  }
  const { ungzip } = await import('pako')
  const buf = new Uint8Array(await blob.arrayBuffer())
  return ungzip(buf, { to: 'string' })
}
