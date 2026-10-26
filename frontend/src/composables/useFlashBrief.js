/**
 * 快讯解读共享数据源（★ 2026-10-26，动态盘面 × 消息面融合的第①步）
 *
 * 为什么要有它：
 *   原来「快讯解读」卡片**自己** `GET /flash/brief`，别人拿不到这份数据 ⇒ 只能把
 *   「持仓影响」画在快讯卡里（与上方「持仓状态」隔着一张卡）。融合的做法是把取数上提成
 *   **单例**，让「盘面速览」的持仓行直接消费同一份快照：
 *     · 组件 <FlashBriefCard> 仍负责渲染快讯列表/AI 结论；
 *     · 父级（盘面速览）用 `byCode` 把「消息×持仓」投影到持仓行；
 *     · 两者共用**一条 60s 轮询**（引用计数：最后一个消费者退出即停），不会双倍请求。
 *
 * 纪律（与全站一致）：
 *   ① 仅交易时段轮询（休市 brief 不会变；挂载时首次加载保留，休市也能看到已有解读）；
 *   ② 失败**保留上一次数据**（不把页面清空），错误只记在 `err` 供 UI 提示；
 *   ③ 不触发 LLM：这里只 GET（读库/缓存）；`POST /flash/brief/refresh` 只由用户点击触发。
 */

import { ref, computed, reactive } from 'vue'
import { getFlashBrief, getFlashBriefRun } from '../api'
import { isTradingTime } from './usePortfolio'

const POLL_MS = 60000

const data = ref(null)
const err = ref('')
const loading = ref(false)
const state = reactive({ as_of: '', at: 0 })

let timer = null
let refs = 0
let curDate = ''
let inflight = false

async function load(date) {
  if (typeof date === 'string') curDate = date
  if (inflight) return
  inflight = true
  loading.value = true
  try {
    const { data: d } = await getFlashBrief(curDate || undefined)
    if (d) {
      data.value = d
      state.as_of = d.as_of || ''
      state.at = Date.now()
      err.value = ''
    }
  } catch (e) {
    // 有旧数据就继续用（60s 后自动重试），没有才报错
    if (!data.value) err.value = '加载失败：' + (e?.message || e)
  } finally {
    inflight = false
    loading.value = false
  }
}

/** 手动解读成功后把结果直接写进共享状态（省一次 GET） */
function apply(payload) {
  if (!payload) return
  const trg = (data.value && data.value.trigger) || []
  data.value = { ...payload, trigger: payload.trigger || trg }
  state.as_of = payload.as_of || state.as_of
  state.at = Date.now()
  err.value = ''
}

function start(date) {
  refs += 1
  if (typeof date === 'string') curDate = date
  if (refs > 1) return
  load()
  timer = setInterval(() => { if (isTradingTime()) load() }, POLL_MS)
}

function stop() {
  refs = Math.max(0, refs - 1)
  if (refs > 0) return
  if (timer) { clearInterval(timer); timer = null }
}

/** 持仓行投影：{code: {lean, n, hits[]}} —— 后端 `holdings_impact` 已是规则联动结果 */
const byCode = computed(() => {
  const out = {}
  const rows = (data.value && data.value.holdings_impact) || []
  for (const h of rows) {
    if (!h || !h.code) continue
    out[h.code] = {
      lean: h.lean || '',
      n: (h.hits || []).length,
      hits: h.hits || [],
      industry: h.industry || '',
      phase_cn: h.phase_cn || '',
    }
  }
  return out
})

/**
 * 取某一次历史解读的全文（tab 回看用）。
 * ⚠️ 不进共享 `data`：历史是**按需**拉的，不能覆盖"最新一份"，否则轮询会把它冲掉。
 */
async function loadRun(runId) {
  if (!runId) return null
  try {
    const { data: d } = await getFlashBriefRun(runId)
    return d || null
  } catch (e) {
    return { ok: false, error: (e && e.message) || '读取失败' }
  }
}

/**
 * 板块投影（★ 2026-10-26 第②步）：`sector_hits` → `{行业: {n, ben, bear, hits[]}}`。
 * 后端用 industry_map 的行业名做确定性匹配（零外部请求），前端只负责"挂到板块行上"。
 */
const byIndustry = computed(() => {
  const out = {}
  for (const h of (data.value && data.value.sector_hits) || []) {
    const k = h.industry
    if (!k) continue
    if (!out[k]) out[k] = { n: 0, ben: 0, bear: 0, hits: [] }
    out[k].n += 1
    out[k].hits.push(h)
    if (h.direction === '利好') out[k].ben += 1
    else if (h.direction === '利空') out[k].bear += 1
  }
  return out
})

export function useFlashBrief() {
  return { data, err, loading, state, byCode, byIndustry, load, loadRun, apply, start, stop }
}
