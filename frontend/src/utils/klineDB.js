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
let _recoverAttempted = false   // 自愈只试一次，防循环

function _openDB() {
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

function _deleteDB() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase(DB_NAME)
    req.onsuccess = () => resolve()
    req.onerror = () => reject(req.error)
    req.onblocked = () => reject(new Error('删除被其他标签页阻塞：请关闭本站的其他标签页后刷新重试'))
  })
}

/**
 * 初始化 IndexedDB
 * ★ 2026-09-13：Chrome 存储损坏（UnknownError: Internal error）自愈——
 *   打开失败时删除本地库重建（代价：数据包需重新下载），重建仍失败才上抛。
 * @returns {Promise<IDBDatabase>}
 */
export async function initKlineDB() {
  if (db) return db

  try {
    return await _openDB()
  } catch (e) {
    if (_recoverAttempted) {
      throw new Error(`本地存储不可用（${e?.message || e}）：请清除本站数据后重试`)
    }
    _recoverAttempted = true
    console.warn('[klineDB] IndexedDB 打开失败，疑似本地存储损坏，尝试删除重建:', e?.message || e)
    db = null
    await _deleteDB()
    db = await _openDB()
    console.warn('[klineDB] 本地库已重建（数据包需重新下载）')
    return db
  }
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

// 数据包结构版本：version 2 = 150 天 K 线（评分需要足够历史让 EMA 系列指标收敛）
const REQUIRED_PACK_VERSION = 2

/**
 * 下载并导入**完整包**（首次 / 结构升级 / 本地跨天太多 / 增量不可用 都走这里）。
 *
 * ★ 2026-10-29：从 `checkAndUpdate` 里抽出来 —— 全量下载现在有 **4 个入口**
 *   （首次、结构升级、增量 404、增量 base 不匹配），复制四遍必然漂移
 *   （旧版的两处判重逻辑就已经不完全一样了）。
 */
async function _downloadFullPack(baseUrl, cacheBust, onProgress, lastUpdate, reason) {
  onProgress?.({ stage: 'download', message: reason || '下载完整数据包...' })
  const packUrl = `${baseUrl}/kline-pack-latest.json.gz?${cacheBust}`
  const resp = await fetch(packUrl, { cache: 'no-store' })
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

  onProgress?.({ stage: 'decompress', message: '解压数据...' })
  const text = await decompressGzip(await resp.blob())
  const data = JSON.parse(text)

  // 防御：服务端/CDN 仍返回旧版包时拒绝导入（否则旧版本号会污染本地元信息）
  if ((data.version || 1) < REQUIRED_PACK_VERSION) {
    return { updated: false, message: `服务器数据包仍为旧版（v${data.version || 1}），请确认数据生成工作流已用新代码重新运行` }
  }
  // ★ 线上完整包与本地同日期 → 无新数据，跳过导入。
  //   否则每天收盘前（lastUpdate≠今天）打开页面都会：增量 404 → 重下完整包
  //   → 把 1500+ 只重新全量 put 进 IndexedDB，纯浪费（2026-09-08 实测）。
  if (data.date && data.date === lastUpdate) {
    return { updated: false, message: `线上包仍是 ${data.date}，暂无更新` }
  }
  onProgress?.({ stage: 'import', message: '导入数据...' })
  const result = await importKlinePack(data, (loaded, total) => {
    onProgress?.({ stage: 'import', loaded, total })
  })
  return { updated: true, message: `导入 ${result.imported} 只股票` }
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

  // 缓存破坏参数：GitHub Pages 有 CDN 缓存，同一文件名可能被浏览器/CDN 返回旧版本，
  // 导致「下载到旧版包 → 写入旧版本号 → 下次又被判需重下」的循环，故每次请求强制绕过缓存
  const cacheBust = `t=${Date.now()}`
  const localPackVersion = await getMeta('pack_version') || 1
  const needFullRedownload = lastUpdate && localPackVersion < REQUIRED_PACK_VERSION

  // 如果今天已经更新过且数据包版本匹配，跳过（版本不匹配时强制重新下载）
  if (lastUpdate === todayStr && !needFullRedownload) {
    return { updated: false, message: '数据已是最新' }
  }

  // ★★ 2026-10-26（用户报"一天流量 2.18GB"）：**同一窗口只真正检查一次**。
  //   【病因】数据包按**交易日**生成，而这里拿 `todayStr`（UTC 自然日）比 ——
  //     周末/盘前/日批跑完前，`todayStr` 恒 ≠ 包日期 ⇒ 每次进入「工作台 / 榜单」
  //     都会：增量包 404 → 重下完整包 **3.8MB**（下面的
  //     `data.date === lastUpdate` 只省了导入，**没省下载**）。
  //     实测：一天几百次进页面（登录被踢→重进）+ 每次 3.8~5.5MB ≈ 2.18GB。
  //   【修法】`pack_check_at` 记"上次真正检查的时刻"，6 小时内直接返回；
  //     时间戳在**下载前**落盘（下载失败也不反复重试，避免失败风暴）。
  const CHECK_WINDOW_MS = 6 * 3600 * 1000
  const lastCheckTs = Number(await getMeta('pack_check_at') || 0)
  if (lastCheckTs && Date.now() - lastCheckTs < CHECK_WINDOW_MS) {
    const ageMin = Math.max(1, Math.round((Date.now() - lastCheckTs) / 60000))
    return { updated: false, message: `数据包 ${ageMin} 分钟前检查过（6 小时内不重复下载）` }
  }
  await setMeta('pack_check_at', String(Date.now()))

  // 首次使用或数据包结构升级，下载完整包（增量包无法补齐历史天数）
  if (!lastUpdate || needFullRedownload) {
    try {
      return await _downloadFullPack(
        baseUrl, cacheBust, onProgress, lastUpdate,
        needFullRedownload ? '数据结构升级，重新下载完整包...' : '首次加载，下载数据包...')
    } catch (error) {
      return { updated: false, message: `下载失败: ${error.message}` }
    }
  }

  // 尝试增量更新（本地已是新版数据时才允许走增量路径）
  // ★★ 2026-10-29：增量包改 **gzip**，且生成侧已从「伪增量」改成「只传新增的 K 线行」。
  //   改前：`compute_delta` 只要某只今天多一根，就把整段 150 天重发 ⇒ 裸 JSON 10.11MB
  //        （CDN 传输 ≈3MB，仍大于全量的 2.68MB）。
  //   改后：只含 `date > 上一份最后一根` 的行 ⇒ 约 60KB。
  //   ⚠️ 旧版 `.json` 已停产 ⇒ 老客户端这里 404，会下全量 gz，不会坏。
  const deltaUrl = `${baseUrl}/kline-delta-${todayStr}.json.gz?${cacheBust}`
  try {
    onProgress?.({ stage: 'download', message: '检查增量更新...' })
    const response = await fetch(deltaUrl, { cache: 'no-store' })

    if (response.ok) {
      const delta = JSON.parse(await decompressGzip(await response.blob()))

      // ★★ 2026-10-29：**base 校验**（这条是整个"真增量"能否成立的关键）。
      //   delta 只含"从 base 那天往后新增的行"。若本地停在 base 之前（离线几天），
      //   直接应用会**静默缺掉中间几天** —— K 线缺 → 指标错 → 评分错，且不报错。
      //   ⇒ 本地日期 ≠ base 一律**回退全量**（2.68MB，慢一点但正确）。
      //   `base` 为空（老版生成器）也按不安全处理。
      const base = String(delta.base || '')
      if (!base || base !== String(lastUpdate || '')) {
        return await _downloadFullPack(
          baseUrl, cacheBust, onProgress, lastUpdate,
          `本地是 ${lastUpdate || '无'}、增量基于 ${base || '未知'}（中间可能缺天），改下全量...`)
      }

      onProgress?.({ stage: 'apply', message: '应用增量更新...' })
      const result = await applyDelta(delta)
      return {
        updated: true,
        message: `更新 ${result.updated} 只，新增 ${result.added} 只`
      }
    }

    // 增量包不存在（周末/日批未跑）⇒ 下全量
    return await _downloadFullPack(baseUrl, cacheBust, onProgress, lastUpdate)
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
