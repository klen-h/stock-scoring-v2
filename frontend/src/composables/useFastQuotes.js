/**
 * 关键标的快速轮询（先知雷达盘中时效·方案 A 子集）
 *
 * 持仓 + 自选 + 指数这类"关键标的"用 12 秒批量轮询（腾讯 qt.gtimg.cn
 * 单请求可带 50 只，免费 HTTP 轮询——免费层无 A 股 WebSocket，见
 * 项目进展文档）；全池仍走 2-3 分钟慢刷。
 *
 * 用法：
 *   const fast = useFastQuotes(
 *     () => [...watchlist.value.map(w => w.code), ...portfolioCodes],
 *     (quotes) => { Object.entries(quotes).forEach(([c, d]) => merge(c, d)) },
 *   )
 *   fast.start()   // onMounted
 *   fast.stop()    // onBeforeUnmount
 *
 * 交易时段外自动停轮（isTradingTime 判定，含集合竞价前后）。
 */

import { fetchRealtimeQuotes } from '../api/tencent'
import { isTradingTime } from './usePortfolio'

export function useFastQuotes(getCodes, onQuotes, intervalMs = 12000) {
  let timer = null

  async function tick() {
    if (!isTradingTime()) return          // 非交易时段不打腾讯接口
    const codes = [...new Set((getCodes() || []).filter(Boolean))]
    if (!codes.length) return
    try {
      const quotes = await fetchRealtimeQuotes(codes)
      if (quotes && Object.keys(quotes).length) onQuotes(quotes)
    } catch (e) { /* 静默：下次 tick 再试 */ }
  }

  function start() {
    stop()
    tick()
    timer = setInterval(tick, intervalMs)
  }

  function stop() {
    if (timer) { clearInterval(timer); timer = null }
  }

  return { start, stop }
}
