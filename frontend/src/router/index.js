import { createRouter, createWebHashHistory } from 'vue-router'
import { isLoggedIn } from '../api'

const routes = [
  { path: '/login', name: 'Login', component: () => import('../views/Login.vue'), meta: { public: true } },
  // ★ 2026-09-25 工作台上线：'/' 默认落在工作台；旧 Dashboard 保留为"数据中心"（导航入口已改名）
  { path: '/', name: 'Workbench', component: () => import('../views/Workbench.vue') },
  { path: '/dashboard', name: 'Dashboard', component: () => import('../views/Dashboard.vue') },
  { path: '/market', name: 'Market', component: () => import('../views/MarketView.vue') },
  { path: '/sector', name: 'Sector', component: () => import('../views/SectorView.vue') },
  { path: '/mainline', name: 'Mainline', component: () => import('../views/MainlineView.vue') },
  // ★ 2026-11-28：题材轮动（风向标：哪个方向有人气、处在启动/主升/高位/退潮哪一步）
  { path: '/rotation', name: 'TopicRotation', component: () => import('../views/TopicRotation.vue') },
  { path: '/stock/:code', name: 'StockDetail', component: () => import('../views/StockDetail.vue') },
  { path: '/score', name: 'ScoreRank', component: () => import('../views/ScoreRank.vue') },
  { path: '/screener', name: 'Screener', component: () => import('../views/Screener.vue') },
  { path: '/score/stats', name: 'BucketStats', component: () => import('../views/BucketStats.vue') },
  { path: '/strategies', name: 'Strategies', component: () => import('../views/Strategies.vue') },
  { path: '/monitor', name: 'Monitor', component: () => import('../views/MonitorView.vue') },
  { path: '/calendar', name: 'Calendar', component: () => import('../views/CalendarView.vue') },
  { path: '/backtest', name: 'Backtest', component: () => import('../views/BacktestView.vue') },
  { path: '/paper', name: 'PaperTrading', component: () => import('../views/PaperTrading.vue') },
  // ★ 2026-11-15：做T 交易台（全市场清单 + 池子管理 + 成交明细 + 单只诊断）
  { path: '/t-trade', name: 'TTrade', component: () => import('../views/TTrade.vue') },
  { path: '/coach', name: 'Coach', component: () => import('../views/CoachView.vue') },
  { path: '/portfolio', name: 'Portfolio', component: () => import('../views/Portfolio.vue') },
  { path: '/watchlist', name: 'Watchlist', component: () => import('../views/Watchlist.vue') },
  { path: '/trade-plans', name: 'TradePlans', component: () => import('../views/TradePlans.vue') },
  { path: '/capital', name: 'Capital', component: () => import('../views/CapitalView.vue') },
  // ★ 2026-09-25：龙虎榜（数据层早已完备，此前无入口）
  { path: '/lhb', name: 'Lhb', component: () => import('../views/LhbView.vue') },
  { path: '/report', name: 'DailyReport', component: () => import('../views/DailyReport.vue') },
  { path: '/contradictions', name: 'Contradictions', component: () => import('../views/ContradictionsView.vue') },
  // ★ 2026-11-22：推演登记 + 逐日对照（P1 前端，后端 API 已于 11-22 上线）
  { path: '/thesis', name: 'Thesis', component: () => import('../views/ThesisView.vue') },
  // ★ 2026-11-28：动作协议卡（规则层，回答"怎么办"）。与 /thesis 并列：
  //   那张是"会怎样"（情景+概率+事后验证），这张是"触发后第一步做什么"。
  { path: '/protocols', name: 'Protocols', component: () => import('../views/ProtocolView.vue') },
  { path: '/performance', name: 'Performance', component: () => import('../views/Performance.vue') },
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
