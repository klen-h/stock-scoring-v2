import axios from 'axios'
// ★ 2026-11-14 结构重组：唯一 axios 实例 + 拦截器 + token 管理（单一事实源）。
//   领域模块（modules/*.js）从这里拿 http；页面只 import api/index.js（兼容层）。

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL || '/api',
  timeout: 30000,
})

// ==============================================================================
//  本地开发响应缓存（VITE_LOCAL_CACHE=1 开启；生产构建不带）
// ==============================================================================
// ★★ 2026-11-07（用户："可以将请求的数据缓存在本地吗？第二次请求就直接使用"）：
//   背景是**本地改前端**时反复刷新同一页，每次刷新后端都要重算一遍
//   （评分精算、雷达、复盘聚合等，重的几秒）。
//   ⚠️ 导库（`scripts/export_local_sqlite.py`）解决的是**查库耗 egress**；
//      本层解决的是**重复计算**——两者不重叠，各管一段。
//
// ★ 为什么放在 axios 层而不是后端中间件：
//   ① 不动生产代码（后端中间件要在线上一起跑，读 body 还要重拼 StreamingResponse）；
//   ② 本地开发只有一个浏览器，localStorage 足够；
//   ③ 生产构建（`.env.server` / `.env.production`）不含这个变量 ⇒ 零风险。
//
// ★ 边界（必须知道）：
//   · 只缓存 **GET**。POST 一律透传（落库/刷新按钮本来就该走网络）。
//   · 单条 >150KB 不缓存（榜单/宽表类，localStorage 5MB 装不下几条）。
//   · 总量超 3MB 按时间 LRU 清理。
//   · 数据会**停在缓存那一刻**，看新数据要清缓存或带 `params.nocache=1`。
//   · 控制台可手动清：`clearApiCache()`（已挂到 window）。
const LOCAL_CACHE = import.meta.env.VITE_LOCAL_CACHE === '1'
// 0 = 永不过期（默认，符合"第二次直接用"）；要自动失效就设秒数
const CACHE_TTL_MS = Number(import.meta.env.VITE_LOCAL_CACHE_TTL || 0) * 1000
const CACHE_PREFIX = 'api_cache:'
const CACHE_ITEM_MAX = 150 * 1024        // 单条上限（字符数，约 150KB）
const CACHE_TOTAL_MAX = 3 * 1024 * 1024  // 总量上限

function _cacheKey(config) {
  const p = config.params ? JSON.stringify(config.params) : ''
  return CACHE_PREFIX + (config.url || '') + (p ? '|' + p : '')
}

function _cacheGet(key) {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const o = JSON.parse(raw)
    if (CACHE_TTL_MS > 0 && Date.now() - (o.t || 0) > CACHE_TTL_MS) {
      localStorage.removeItem(key)
      return null
    }
    return o
  } catch {
    return null
  }
}

function _cacheTrim(aggressive) {
  const keys = Object.keys(localStorage).filter(k => k.startsWith(CACHE_PREFIX))
  const items = keys.map(k => {
    const raw = localStorage.getItem(k) || ''
    let t = 0
    try { t = JSON.parse(raw).t || 0 } catch { /* 坏数据当最旧处理 */ }
    return { k, t, n: raw.length }
  })
  let total = items.reduce((a, b) => a + b.n, 0)
  const limit = aggressive ? CACHE_TOTAL_MAX / 2 : CACHE_TOTAL_MAX
  if (total <= limit) return
  items.sort((a, b) => a.t - b.t)          // 最旧的先删
  for (const it of items) {
    if (total <= limit) break
    localStorage.removeItem(it.k)
    total -= it.n
  }
}

function _cacheSet(key, data) {
  let s
  try {
    s = JSON.stringify({ t: Date.now(), d: data })
  } catch {
    return                                  // 含循环引用等，直接放弃
  }
  if (s.length > CACHE_ITEM_MAX) return     // 大响应不缓存
  try {
    localStorage.setItem(key, s)
  } catch {
    _cacheTrim(true)                        // 配额满：砍半再试一次
    try { localStorage.setItem(key, s) } catch { /* 仍失败就放弃 */ }
  }
  _cacheTrim(false)
}

/** 清空本地 API 缓存（控制台 `clearApiCache()` 或页面上手动调用）。 */
export { http, LOCAL_CACHE }
