/**
 * 关键标的快速轮询（先知雷达盘中时效·方案 A 子集）
 *
 * 持仓 + 自选 + 指数这类"关键标的"用 12 秒批量轮询（腾讯 qt.gtimg.cn
 * 单请求可带 50 只，免费 HTTP 轮询——免费层无 A 股 WebSocket，见
 * 项目进展文档）；全池仍走 2-3 分钟慢刷。
 *
 * 用法（与旧版**完全兼容**，页面无需改动）：
 *   const fast = useFastQuotes(
 *     () => [...watchlist.value.map(w => w.code), ...portfolioCodes],
 *     (quotes) => { Object.entries(quotes).forEach(([c, d]) => merge(c, d)) },
 *   )
 *   fast.start()   // onMounted
 *   fast.stop()    // onBeforeUnmount
 *
 * ★★ 2026-10-26（G4 + G5 轮询合并）—— 由「每处调用各建一条 12s 循环」改为
 *    **全站共享一条调度器** + **跨标签租约去重**：
 *    ① 同标签内多订阅者（持仓页 / 自选页 / 以后任何页面）⇒ 代码集**并集去重**后
 *       只发一次请求，结果按各自代码集分发（原来 N 个订阅者 = N 条 12s 循环）；
 *    ② 多标签同时开（本项目常态）⇒ 用 localStorage 租约选一个"抓取者"，
 *       其余标签直接复用同一份结果 ⇒ **每 12s 全浏览器只打一次腾讯**；
 *    ③ 租约/缓存的读写全部 try/catch（隐私模式禁用 localStorage 时自动退化为各标签自取，
 *       功能不受影响，只是少省一点请求）。
 *    ⚠️ 纪律不变：非交易时段不发、页面后台（document.hidden）不发、失败保留旧值
 *      （不调 `onQuotes`，页面上的数字原地不动）。
 */

import { fetchRealtimeQuotes } from '../api/tencent'
import { isTradingTime } from './usePortfolio'

const DEFAULT_INTERVAL = 12000
const MIN_INTERVAL = 5000          // 再快对免费接口不友好，也没必要
const LEASE_KEY = 'qs:fast:lease'  // 跨标签租约
const CACHE_KEY = 'qs:fast:cache'  // 跨标签行情缓存

const subs = new Set()
let timer = null
let timerMs = 0
let inflight = false
const myId = `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`

// ── localStorage 安全封装（隐私模式/配额满都不许抛）──────────────────────────
function lsGet(key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch { return null }
}
function lsSet(key, val) {
  try { localStorage.setItem(key, JSON.stringify(val)); return true } catch { return false }
}

function codeKey(codes) {
  return codes.slice().sort().join(',')
}

/** 缓存是否覆盖本次要的代码集（缓存的代码集必须是超集） */
function covers(cache, codes) {
  if (!cache || !cache.quotes) return false
  const have = new Set(String(cache.codes || '').split(',').filter(Boolean))
  for (const c of codes) if (!have.has(c)) return false
  return true
}

function distribute(quotes) {
  if (!quotes || !Object.keys(quotes).length) return
  for (const s of subs) {
    let own
    try { own = new Set((s.getCodes() || []).filter(Boolean)) } catch { own = new Set() }
    if (!own.size) continue
    const payload = {}
    for (const c of own) if (quotes[c]) payload[c] = quotes[c]
    if (!Object.keys(payload).length) continue
    try { s.onQuotes(payload) } catch (e) { console.warn('[fastQuotes] onQuotes 抛错（已隔离）', e?.message || e) }
  }
}

async function tick() {
  if (!subs.size) return
  if (!isTradingTime()) return                              // 非交易时段不打腾讯接口
  if (typeof document !== 'undefined' && document.hidden) return  // 后台标签不发
  if (inflight) return                                      // 防抖动/防重叠

  const codes = []
  const seen = new Set()
  for (const s of subs) {
    let arr = []
    try { arr = s.getCodes() || [] } catch { arr = [] }
    for (const c of arr) {
      if (c && !seen.has(c)) { seen.add(c); codes.push(c) }
    }
  }
  if (!codes.length) return

  // ① 跨标签：租约还新鲜 ⇒ 本标签不当抓取者，直接复用别人的结果
  const now = Date.now()
  const lease = lsGet(LEASE_KEY)
  const leaseMs = Math.max(MIN_INTERVAL, timerMs - 1000)
  const otherOwner = lease && lease.owner && lease.owner !== myId && (now - (lease.ts || 0) < leaseMs)
  if (otherOwner) {
    const cache = lsGet(CACHE_KEY)
    if (covers(cache, codes) && now - (cache.ts || 0) < timerMs + 3000) {
      distribute(cache.quotes)
      return
    }
    // 缓存不覆盖（本标签多了新代码）⇒ 继续自己取，但把租约抢过来，避免双方都取
  }

  inflight = true
  try {
    const quotes = await fetchRealtimeQuotes(codes)
    if (!quotes || !Object.keys(quotes).length) return      // 失败/空 ⇒ 保留旧值
    lsSet(LEASE_KEY, { owner: myId, ts: Date.now() })
    // 缓存只存本次代码集（超集判断用），体积过大时放弃缓存（宁可多取一次也不塞爆配额）
    const payload = { ts: Date.now(), codes: codeKey(codes), quotes }
    if (JSON.stringify(payload).length < 200000) lsSet(CACHE_KEY, payload)
    distribute(quotes)
  } catch (e) {
    /* 静默：下次 tick 再试（页面数字保持上一次的值） */
  } finally {
    inflight = false
  }
}

function ensureTimer() {
  const want = subs.size
    ? Math.max(MIN_INTERVAL, Math.min(...[...subs].map(s => s.intervalMs || DEFAULT_INTERVAL)))
    : 0
  if (want === timerMs && timer) return
  if (timer) { clearInterval(timer); timer = null }
  timerMs = want
  if (!want) return
  timer = setInterval(tick, want)
}

export function useFastQuotes(getCodes, onQuotes, intervalMs = DEFAULT_INTERVAL) {
  const sub = {
    getCodes,
    onQuotes,
    intervalMs: Math.max(MIN_INTERVAL, Number(intervalMs) || DEFAULT_INTERVAL),
  }
  let active = false

  function start() {
    if (active) return
    active = true
    subs.add(sub)
    ensureTimer()
    tick()            // 首次立即取一次（与旧版一致）
  }

  function stop() {
    if (!active) return
    active = false
    subs.delete(sub)
    ensureTimer()     // 无人订阅 ⇒ 清掉定时器（页面切换后零空转）
  }

  return { start, stop }
}
