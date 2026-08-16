import { createRouter, createWebHashHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/market', name: 'Market', component: () => import('../views/MarketView.vue') },
  { path: '/stock/:code', name: 'StockDetail', component: () => import('../views/StockDetail.vue') },
  { path: '/score', name: 'ScoreRank', component: () => import('../views/ScoreRank.vue') },
  { path: '/monitor', name: 'Monitor', component: () => import('../views/MonitorView.vue') },
  { path: '/portfolio', name: 'Portfolio', component: () => import('../views/Portfolio.vue') },
  { path: '/watchlist', name: 'Watchlist', component: () => import('../views/Watchlist.vue') },
  { path: '/trade-plans', name: 'TradePlans', component: () => import('../views/TradePlans.vue') },
  { path: '/capital', name: 'Capital', component: () => import('../views/CapitalView.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router