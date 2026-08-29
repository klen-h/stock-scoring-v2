import { createRouter, createWebHashHistory } from 'vue-router'
import { isLoggedIn } from '../api'

const routes = [
  { path: '/login', name: 'Login', component: () => import('../views/Login.vue'), meta: { public: true } },
  { path: '/', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/market', name: 'Market', component: () => import('../views/MarketView.vue') },
  { path: '/stock/:code', name: 'StockDetail', component: () => import('../views/StockDetail.vue') },
  { path: '/score', name: 'ScoreRank', component: () => import('../views/ScoreRank.vue') },
  { path: '/strategies', name: 'Strategies', component: () => import('../views/Strategies.vue') },
  { path: '/monitor', name: 'Monitor', component: () => import('../views/MonitorView.vue') },
  { path: '/backtest', name: 'Backtest', component: () => import('../views/BacktestView.vue') },
  { path: '/portfolio', name: 'Portfolio', component: () => import('../views/Portfolio.vue') },
  { path: '/watchlist', name: 'Watchlist', component: () => import('../views/Watchlist.vue') },
  { path: '/trade-plans', name: 'TradePlans', component: () => import('../views/TradePlans.vue') },
  { path: '/capital', name: 'Capital', component: () => import('../views/CapitalView.vue') },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

// 路由守卫：未登录跳转到登录页
router.beforeEach((to, from, next) => {
  // 公开页面（登录页）直接放行
  if (to.meta.public) {
    next()
    return
  }
  
  // 检查是否已登录
  if (!isLoggedIn()) {
    next({ name: 'Login' })
    return
  }
  
  next()
})

export default router
