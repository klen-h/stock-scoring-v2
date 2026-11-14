// composables/useScenarioEvents.js —— 磨底记分卡 + 事件信号数据层（自 Workbench.vue 抽出，2026-11-14）
// DI：obsAI/obsAIOpen/loadObsAI——loadScenario 换日时重置已抽出的观察器 AI 解读（跨簇依赖显式化，同防御组 DI 模式）。
import { ref, computed } from 'vue'
import { getScenarioWatch, getMarketEvents } from '../api'
export function useScenarioEvents({ selectedDate, obsAI, obsAIOpen, loadObsAI }) {
// ★ 2026-09-27 事件驱动信号（E2 政策脉冲）—— v0 展示项，不进决策链。
//   稀有事件（21 年 298 天触发），触发时顶栏显一项；未触发则不占位。
const eventSignal = ref(null)

// ★ 2026-10-23：磨底 2.0 推演证伪记分卡
const scen = ref(null)
// ★ 2026-11-01：加载失败与"还在加载"分开——失败时摘要行显示"—（暂无数据）"，
//   不再永远"加载中…"（后端 114s 现算事故的 UI 侧防线）。
const scenErr = ref(false)
function scenBadge(s) {
  if (s === '熊市加深') return 'bg-rose-500/20 text-rose-300'
  if (s === '提前修复') return 'bg-emerald-500/20 text-emerald-300'
  return 'bg-amber-500/20 text-amber-300'
}
function leanDot(l) {
  if (l === '多') return 'bg-emerald-400'
  if (l === '空') return 'bg-rose-400'
  if (l === '不足') return 'bg-slate-500'
  return 'bg-slate-400/60'
}
function scenVal(s) {
  const v = s.value
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return `${Number.isInteger(n) ? n : n.toFixed(2)}${s.unit || ''}`
}

async function loadScenario() {
  scenErr.value = false
  try {
    const { data } = await getScenarioWatch(selectedDate.value)
    scen.value = data || null
    scenErr.value = !data          // 200 但空 ⇒ 也算"暂无"，不当成加载中
  } catch (e) {
    scen.value = null
    scenErr.value = true
  }
  // 换日 / 回放切日：清掉上一天的 AI 解读（按日缓存，留着会误导）
  obsAI.value = { loading: false, error: '', markdown: '', conclusion: '', reasons: [],
                  cached: false, as_of: '', date: '' }
  if (obsAIOpen.value) loadObsAI(false)
}

// ★ 2026-09-27 事件驱动信号（v0 展示项）。定位三行（项目约定）：
//   **是什么**  极端普涨（涨家数占比≥90%）+ 涨停骤增（≥50 家）的"政策脉冲"实时提示
//   **不是什么** 不是买卖信号；不进决策链；不改市况/仓位/战法准入
//   **下一步**  实盘样本积累 1~2 季后复核「能否作为 defensive 先行解除」
async function loadEvents() {
  try {
    const { data } = await getMarketEvents()
    eventSignal.value = (data && data.event) || null
  } catch { /* 静默保留上次值：事件极稀有，失败不该打扰用户 */ }
}
/** 事件悬浮说明（顶栏 title）：带历史预期，防止被当买卖信号用。 */
const eventTitle = computed(() => {
  const e = eventSignal.value
  if (!e || !e.available) return ''
  const base = `涨家数占比 ${((Number(e.up_ratio) || 0) * 100).toFixed(1)}%`
    + ` · 涨停 ${e.limit_up} · 跌停 ${e.limit_down}`
  const s = e.e2_stats
  if (e.e2_policy_surge && s) {
    const d = (s.by_regime && s.by_regime.defensive) || {}
    // ★ 可执行标的（真实指数终验 + 成本口径，见 _report_事件驱动信号源接入 §8.4）
    const ex = (e.exec_stats && e.exec_stats.targets) || []
    const exLine = ex.length
      ? `历史同态可执行标的（T+1 开盘买、持 20 日、已扣 0.3% 成本）：\n`
        + ex.map(t => `  ${t.name}(${t.etf})  +${t.net_edge}pp  P=${t.p}  n=${t.n}`).join('\n')
        + `\n（沪深300 扣成本后为负；-7% 止损为负贡献，未采用）\n`
      : ''
    return `${base}\nE2 政策脉冲（历史 ${s.n} 天 / ${s.n_clusters} 簇）：`
      + `后 20 日全市场等权 +${s.h20_mean}%，基准 +${s.h20_base}%，胜率 ${s.h20_win}%\n`
      + `防御期内增量 +${d.diff}pp（n=${d.n}）\n`
      + exLine
      + `⚠️ 展示项：历史统计参考，非投资建议，不改市况/仓位`
  }
  if (e.e1_capitulation) {
    return `${base}\n冰点信号（E1）：历史上无额外 edge（已归档，仅作背景提示）`
  }
  return base
})


  return {
    scen, scenErr, scenBadge, leanDot, scenVal, loadScenario,
    eventSignal, loadEvents, eventTitle,
  }
}
