// ── 消息面徽章（持仓页/观察池复用）──
// 逐只调 /stock/news/{code}（后端 60s 缓存），返回响应式 {code: {score, level, level_text}}
import { reactive } from 'vue'
import { getStockNews } from '../api'

export function useNewsBadges() {
  const newsScores = reactive({})

  async function loadNews(codes) {
    for (const c of [...new Set(codes || [])]) {
      if (!c || newsScores[c]) continue
      getStockNews(c).then(({ data }) => {
        newsScores[c] = data
      }).catch(() => {})
    }
  }

  function newsBadgeClass(level) {
    return {
      [-2]: 'bg-red-500/20 text-red-400 border border-red-500/30',
      [-1]: 'bg-red-500/10 text-red-300 border border-red-500/20',
      0: 'bg-white/5 text-muted border border-border',
      1: 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20',
      2: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
    }[level ?? 0]
  }

  return { newsScores, loadNews, newsBadgeClass }
}
