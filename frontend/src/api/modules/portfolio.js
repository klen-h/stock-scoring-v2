// ==============================================================================
// portfolio.js —— 由 api/index.js 按域拆分（2026-11-14 结构重组；index.js re-export 兼容）
// 本文件只做「请求定义」：参数进、响应出，无状态。业务状态在 composables/。
// ==============================================================================
import { http } from '../http'

export const triggerDailyBatch = (tasks = 'all', force = true) =>
  http.post('/tasks/trigger', { task: tasks, force })

export const getTaskConfig = () => http.get('/tasks/config')

export const getUserWatchlist = () => http.get('/user/watchlist')

export const upsertUserWatch = (item) => http.post('/user/watchlist', item)

export const deleteUserWatch = (code) => http.delete(`/user/watchlist/${code}`)

export const getUserPlans = () => http.get('/user/plans')

export const upsertUserPlan = (item) => http.post('/user/plans', item)

export const updateUserPlan = (id, item) => http.put(`/user/plans/${id}`, item)

export const deleteUserPlan = (id) => http.delete(`/user/plans/${id}`)

export const getUserPortfolio = () => http.get('/user/portfolio')
// 仓位建议（W1.5）：后端数据联动（regime+宏观+情绪+宽度+主力筹码），替代前端写死规则

export const getUserPositionSizing = () => http.get('/user/position-sizing')
// ★ 2026-09-27 统一出场建议（「什么时候卖」的单一事实源）：
//   后端合并 portfolio_radar（组合面）+ exit_alert（技术面）+ 移动止盈，
//   输出单一 action（清仓/减仓½/减仓⅓/关注减仓/持有/可加仓）。
//   取代此前「我的持仓页前端自算」那套 —— 消除同持仓在不同页给出相反建议的问题。

export const getUserExitAdvice = () => http.get('/user/exit-advice')
// ★ 2026-09-27 数据可信度总表（「已验证 / 未验证 / 已证伪」单一事实源）。
//   用于在界面标注各结论的实证状态 —— 避免用户把未验证的评分/战法
//   与已验证的闸门（19.5 年核算）当成同等权威。见 backend/app/verification.py。

export const upsertUserPortfolio = (item) => http.post('/user/portfolio', item)

export const deleteUserPortfolio = (code) => http.delete(`/user/portfolio/${code}`)

export const batchSyncUser = (data) => http.post('/user/sync', data)

// 模拟盘（纸面交易：pending 待确认 → holding 持仓 → closed 已平仓）

export const getPaperPositions = (status) => http.get('/paper/positions', { params: { status } })

export const manualIngestPaper = (body) => http.post('/paper/positions/manual', body)

export const closePaperPosition = (id) => http.post(`/paper/positions/${id}/close`)

export const cancelPaperPosition = (id) => http.delete(`/paper/positions/${id}`)

export const getPaperStats = () => http.get('/paper/stats')

export const getPaperAttribution = () => http.get('/paper/attribution')

export const getPaperAccount = () => http.get('/paper/account')

export const refreshPaperWhitelist = () => http.post('/paper/whitelist/refresh')

export const getPaperRisk = () => http.get('/paper/risk')

export const unfreezePaperRisk = () => http.post('/paper/risk/unfreeze')

// 交易教练（Coach W1：规则引擎硬警报 + 执行回写 + 执行一致性 KPI）
// 教练只输出「规则结果」，数字由代码注入（LLM 不参与决策，见 PLAN §2.4 评审①）
