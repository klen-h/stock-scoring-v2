// ==============================================================================
// auth.js —— 由 api/index.js 按域拆分（2026-11-14 结构重组；index.js re-export 兼容）
// 本文件只做「请求定义」：参数进、响应出，无状态。业务状态在 composables/。
// ==============================================================================
import { http } from '../http'

export function clearApiCache() {
  const keys = Object.keys(localStorage).filter(k => k.startsWith(CACHE_PREFIX))
  keys.forEach(k => localStorage.removeItem(k))
  console.info(`[api-cache] 已清空 ${keys.length} 条`)
  return keys.length
}
if (typeof window !== 'undefined') window.clearApiCache = clearApiCache

// ── Token 管理 ──
const TOKEN_KEY = 'auth_token'
const USER_KEY = 'auth_user'

export function getToken() {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token, user) {
  localStorage.setItem(TOKEN_KEY, token)
  if (user) localStorage.setItem(USER_KEY, JSON.stringify(user))
}

export function removeToken() {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}

export function isLoggedIn() {
  return !!getToken()
}

// ── 请求拦截器：自动带 Token + 本地缓存命中短路 ──
http.interceptors.request.use(config => {
  const token = getToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  // ★ 缓存命中就直接短路，一次网络都不发（省掉后端的重算，不只是省流量）
  //   ⚠️ axios 的 request 拦截器没有"直接返回响应"的官方口子 ⇒ 用 reject 把命中值
  //      传给下方 response 拦截器的 error 分支还原（`__cacheHit` 标记）。
  if (LOCAL_CACHE
      && (config.method || 'get').toLowerCase() === 'get'
      && !(config.params && config.params.nocache)) {
    const hit = _cacheGet(_cacheKey(config))
    if (hit) {
      console.debug('[api-cache] HIT', config.url)
      return Promise.reject({ __cacheHit: true, data: hit.d, config })
    }
  }
  return config
})

// ── 响应拦截器：写缓存 / 还原缓存命中 / 401 自动跳转登录 ──
http.interceptors.response.use(
  response => {
    // ★ 成功响应写入缓存（只落 GET）。同 key 已存在则不覆盖 ⇒ 保住最早的时间戳，
    //   否则每次"绕过缓存"的请求都会刷新 t，LRU 就永远淘汰不到它。
    if (LOCAL_CACHE && (response.config?.method || 'get').toLowerCase() === 'get') {
      const key = _cacheKey(response.config)
      if (!localStorage.getItem(key)) _cacheSet(key, response.data)
    }
    return response
  },
  error => {
    // ★ 缓存命中（由请求拦截器短路而来）：还原成正常响应
    if (error && error.__cacheHit) {
      return Promise.resolve({
        data: error.data, status: 200, statusText: 'OK (cache)',
        headers: {}, config: error.config, request: {},
      })
    }
    if (error.response?.status === 401) {
      // Token 过期或无效，清除本地状态
      removeToken()
      // ★ 2026-10-24（BK-57）：修复旧实现的两个坑——
      //   ① 本项目是 hash 路由（createWebHashHistory），旧代码 `href='/login'` 跳的是
      //      path 而非路由，落到静态服务器的 /login 上（404 或空 hash 兜底）；
      //   ② 旧判断用 `pathname !== '/login'`，一次跳转后 pathname 永远停在 /login
      //      ⇒ 后续 401 不再跳登录页 = token 过期后接口静默失败（鉴权收紧后此坑
      //        出现频率大增）。⇒ 改判 hash 前缀 + 只改 hash（不整页重载，
      //        并发 401 也只会跳一次，由路由守卫接管）。
      if (!window.location.hash.startsWith('#/login')) {
        window.location.hash = '#/login'
      }
    }
    return Promise.reject(error)
  }
)

// ── 认证 API ──

export const authRegister = (username, password) => http.post('/auth/register', { username, password })

export const authLogin = (username, password) => http.post('/auth/login', { username, password })

export const authCurrent = () => http.get('/auth/current')

// 市场行情
