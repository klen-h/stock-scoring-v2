// ==============================================================================
// api/index.js —— 兼容层（2026-11-14 结构重组）：按域拆分到 modules/*.js + http.js，
//   本文件 re-export 全部旧名 ⇒ 全仓既有 `from '../api'` 零改动。
//   ★ 约定：新请求写进对应域模块，不要再往这里堆（本文件只许有 re-export）。
// ==============================================================================
export * from './http'
export * from './modules/auth'
export * from './modules/flash'
export * from './modules/market'
export * from './modules/misc'
export * from './modules/portfolio'
export * from './modules/score'
export * from './modules/stock'
export * from './modules/system'
