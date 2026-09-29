<template>
  <div class="fade-in">
    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg p-4 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">交易教练</h1>
          <p class="text-xs text-muted mt-0.5">
            规则引擎生成（LLM 不参与决策）· 硬警报 → 执行回写 → 一致性复盘。
            教练的价值是「劝住冲动」，不是选股。
          </p>
        </div>
        <div class="flex gap-2">
          <select v-model.number="days" @change="Promise.all([loadConsistency(), loadPlanRate(), loadAttribution()])"
            class="bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50">
            <option :value="7">近 7 天</option>
            <option :value="30">近 30 天</option>
            <option :value="90">近 90 天</option>
          </select>
          <button @click="load" :disabled="loading"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ loading ? '加载中…' : '刷新' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 执行一致性 KPI（教练核心度量：重点不是"建议对不对"，而是"有没有照做"） -->
    <div class="grid grid-cols-2 md:grid-cols-5 gap-3 mb-2">
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">执行率</div>
        <div class="text-xl font-bold mt-1"
          :class="consistency?.exec_rate_pct == null ? 'text-muted' :
                  consistency.exec_rate_pct >= 80 ? 'text-rise' :
                  consistency.exec_rate_pct >= 50 ? 'text-amber-400' : 'text-fall'">
          {{ consistency?.exec_rate_pct == null ? '—' : consistency.exec_rate_pct + '%' }}
        </div>
        <div class="text-[10px] text-muted mt-0.5">已执行 ÷ 已决策</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">已决策</div>
        <div class="text-xl font-bold text-gray-100 mt-1">{{ consistency?.decided ?? 0 }}</div>
        <div class="text-[10px] text-muted mt-0.5">执行 + 放弃</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">未响应</div>
        <div class="text-xl font-bold text-muted mt-1">{{ consistency?.ignored ?? 0 }}</div>
        <div class="text-[10px] text-muted mt-0.5">不进分母</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">已放弃</div>
        <div class="text-xl font-bold text-amber-400 mt-1">{{ consistency?.abandoned ?? 0 }}</div>
        <div class="text-[10px] text-muted mt-0.5">{{ consistency?.abandon_rate_pct == null ? '—' : consistency.abandon_rate_pct + '%' }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">已推送</div>
        <div class="text-xl font-bold text-accent mt-1">{{ consistency?.pushed_total ?? 0 }}</div>
        <div class="text-[10px] text-muted mt-0.5">近 {{ consistency?.window_days ?? days }} 天</div>
      </div>
    </div>
    <div class="text-[11px] text-muted bg-card border border-border rounded-lg px-3 py-2 mb-4 leading-relaxed">
      KPI 口径：{{ consistency?.note || '执行率分母 = 已决策（不含未响应）' }}
      <span class="text-muted/70">·「没看见」与「看见了但放弃」是两回事，混算会虚高执行率。</span>
    </div>

    <!-- 预承诺执行率 KPI（模拟盘完整接入 B-4：开仓写的退出剧本有多少被实际执行） -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-2">
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">预承诺执行率</div>
        <div class="text-xl font-bold mt-1"
          :class="planRate?.follow_rate_pct == null ? 'text-muted' :
                  planRate.follow_rate_pct >= 80 ? 'text-rise' :
                  planRate.follow_rate_pct >= 50 ? 'text-amber-400' : 'text-fall'">
          {{ planRate?.follow_rate_pct == null ? '—' : planRate.follow_rate_pct + '%' }}
        </div>
        <div class="text-[10px] text-muted mt-0.5">按剧本离场 ÷ 已平仓</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">已结算剧本</div>
        <div class="text-xl font-bold text-gray-100 mt-1">{{ planRate?.plans_settled ?? 0 }}</div>
        <div class="text-[10px] text-muted mt-0.5">已平仓 {{ planRate?.plans_closed ?? 0 }} / 放弃 {{ planRate?.plans_abandoned ?? 0 }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">持仓中剧本</div>
        <div class="text-xl font-bold text-accent mt-1">{{ planRate?.plans_open ?? 0 }}</div>
        <div class="text-[10px] text-muted mt-0.5">未到期</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">按剧本离场</div>
        <div class="text-xl font-bold text-rise mt-1">{{ planRate?.followed_count ?? 0 }}</div>
        <div class="text-[10px] text-muted mt-0.5">stop_loss / expire</div>
      </div>
    </div>
    <div class="text-[11px] text-muted bg-card border border-border rounded-lg px-3 py-2 mb-4 leading-relaxed">
      模拟盘闭环口径：{{ planRate?.note || '按剧本离场 = 止损触发 或 持有到期（强制评估）' }}
      <span class="text-muted/70">· manual / take_profit / 主动放弃 视为未严格按剧本（followed=0）。</span>
    </div>

    <!-- ★★ 2026-09-29（P2 / 缺口 3）：**事后归因** —— 补 `audit.py` 自陈的
         「5 日结果只回填不评估」那一半。回答执行率答不出的问题：
         哪种建议期望最差（逻辑错）/ 放弃是躲过还是错过（执行错）/ 同一逻辑在不同
         主力阶段差多少（时机错）。⚠️ 口径与三条已知限制见下方 note（必须一并展示）；
         ⚠️ 组内 n<5 置灰不参与排行；小样本只作方向参考。 -->
    <div v-if="attr?.available" class="bg-card border border-border rounded-lg p-4 mb-4">
      <div class="flex items-baseline justify-between gap-2 mb-2 flex-wrap">
        <h2 class="text-sm font-bold text-gray-100">事后归因
          <span class="text-[10px] text-muted font-normal">
            近 {{ attr.window?.days }} 天 · 带标的 {{ attr.coverage?.with_code }} 条 ·
            可评 {{ attr.coverage?.with_exc5 }}（{{ attr.coverage?.evaluable_pct }}%）·
            基准 {{ attr.bench?.label }}{{ attr.bench?.fallback ? '（退路）' : '' }}</span></h2>
        <span class="text-[10px] text-muted cursor-help"
              :title="attr.note">口径与限制 ⓘ</span>
      </div>

      <!-- 规则生成的结论（不用 LLM） -->
      <p class="text-xs text-gray-300 leading-relaxed mb-3">{{ attr.sentence }}</p>

      <!-- 四张维度表 -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div v-for="dim in DIMS" :key="dim.key">
          <div class="text-[11px] text-muted mb-1">{{ dim.title }}
            <span class="text-[10px] text-muted/70">{{ dim.hint }}</span></div>
          <table class="w-full text-[11px]">
            <thead>
              <tr class="text-[10px] text-muted border-b border-border/60">
                <th class="text-left font-normal py-0.5">分组</th>
                <th class="text-right font-normal w-10">n</th>
                <th class="text-right font-normal w-16">T+5 超额</th>
                <th class="text-right font-normal w-14">胜率</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="g in (groups[dim.key] || [])" :key="dim.key + g.key"
                  class="border-b border-border/30" :class="{ 'opacity-45': g.insufficient }">
                <td class="py-0.5 truncate max-w-[11rem]" :title="g.key + (g.insufficient ? '（样本不足）' : '')">
                  {{ g.label || g.key }}</td>
                <td class="text-right font-mono text-muted">{{ g.n }}</td>
                <td class="text-right font-mono" :class="excCls(g.exc5)">{{ excText(g.exc5) }}</td>
                <td class="text-right font-mono text-muted">
                  {{ g.win5 == null ? '—' : g.win5 + '%' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 口径与限制（三条必须可见，别只藏在 hover 里） -->
      <div class="text-[10px] text-muted mt-3 leading-relaxed border-t border-border/40 pt-2">
        {{ attr.note }}
      </div>
      <div class="text-[10px] text-muted mt-1">
        口径自检：T+5 重算 vs 库内 `outcome_pct` —— 比对 {{ attr.consistency?.checked }} 条，
        <span class="text-rise">一致 {{ attr.consistency?.match }}</span>
        <span v-if="attr.consistency?.mismatch" class="text-fall"> / 不一致 {{ attr.consistency?.mismatch }}</span>
        · 维度分组和自检
        <span :class="attr.selftest?.ok ? 'text-rise' : 'text-fall'">
          {{ attr.selftest?.ok ? '通过（和 = 总数）' : '异常' }}</span>
      </div>
    </div>

    <div class="flex gap-4 items-start">
      <!-- 左：建议历史 + 执行回写 -->
      <div class="flex-1 min-w-0">
        <div class="bg-card border border-border rounded-lg p-4">
          <div class="flex items-center justify-between mb-3">
            <h2 class="text-sm font-bold text-gray-100">建议历史</h2>
            <span class="text-[10px] text-muted">
              第一周仅「止损触发 / 持有满3日」推送，其余只落库攒样本
            </span>
          </div>

          <div v-if="loading && !alerts.length" class="py-8 text-center">
            <div class="loading-spinner mx-auto mb-3"></div>
            <p class="text-xs text-muted">加载中…</p>
          </div>
          <div v-else-if="error" class="py-4 text-xs text-fall">{{ error }}</div>
          <div v-else-if="!alerts.length" class="py-6 text-xs text-muted text-center">
            暂无教练建议（规则未触发，或教练循环尚未运行）。
          </div>

          <div v-else class="space-y-3">
            <div v-for="a in alerts" :key="a.id"
              class="border border-border rounded-lg p-3 hover:border-accent/30 transition-colors"
              :class="a.severity === 'alert' ? 'bg-red-500/5' : (a.severity === 'warn' ? 'bg-amber-500/5' : '')">
              <!-- 行头 -->
              <div class="flex items-center justify-between gap-2 flex-wrap mb-1.5">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="px-1.5 py-0.5 rounded text-[10px] border" :class="severityCls(a.severity)">
                    {{ severityLabel(a.severity) }}
                  </span>
                  <span class="text-xs font-medium text-gray-100">{{ a.label }}</span>
                  <span v-if="a.pushed" class="px-1.5 py-0.5 rounded text-[10px] bg-accent/15 text-accent border border-accent/20">
                    已推企微
                  </span>
                </div>
                <span class="text-[10px] text-muted font-mono">{{ (a.alert_time || '').replace('T', ' ').slice(0, 16) }}</span>
              </div>

              <!-- 标的 -->
              <div v-if="a.code" class="text-[11px] text-muted mb-1">
                <router-link :to="`/stock/${a.code}`" class="hover:text-accent transition-colors">
                  {{ a.name }} <span class="font-mono">{{ a.code }}</span>
                </router-link>
              </div>

              <!-- 规则原文（代码注入的数字，LLM 不参与） -->
              <div class="text-xs text-gray-300 whitespace-pre-line leading-relaxed">{{ a.message }}</div>

              <!-- T+5 结果回填 -->
              <div v-if="a.outcome_pct != null" class="mt-1.5 text-[10px] text-muted">
                T+5 结果：<span :class="a.outcome_pct > 0 ? 'text-rise' : 'text-fall'">
                  {{ a.outcome_pct > 0 ? '+' : '' }}{{ a.outcome_pct }}%
                </span>
                <span class="ml-1">（{{ a.outcome_date }}）</span>
              </div>

              <!-- 执行回写 -->
              <div class="mt-2.5 pt-2 border-t border-border/60">
                <!-- 已决策态 -->
                <template v-if="a.executed === 'yes'">
                  <span class="text-[11px] text-rise">✓ 已执行</span>
                </template>
                <template v-else-if="a.executed === 'no'">
                  <span class="text-[11px] text-amber-400">✗ 已放弃</span>
                  <span class="text-[11px] text-muted ml-1.5">理由：{{ a.abandon_reason }}</span>
                </template>
                <!-- 未决策态 -->
                <template v-else>
                  <div v-if="!reasonOpen[a.id]" class="flex gap-2">
                    <button @click="doWrite(a, 'yes')" :disabled="writing === a.id"
                      class="px-2.5 py-1 rounded text-[11px] border border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 transition-colors disabled:opacity-50">
                      ✓ 已执行
                    </button>
                    <button @click="openReason(a.id)"
                      class="px-2.5 py-1 rounded text-[11px] border border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-colors">
                      ✗ 放弃
                    </button>
                  </div>
                  <!-- 放弃必须填理由（治"再等等看"） -->
                  <div v-else class="space-y-1.5">
                    <textarea v-model="reasonDraft[a.id]" rows="2"
                      placeholder="放弃原因（必填，回写供周报复盘；例：跌破关键位想等反抽 / 觉得业绩好会涨回来）"
                      class="w-full bg-bg border border-border rounded px-2 py-1 text-[11px] text-gray-200 focus:outline-none focus:border-accent/50 resize-none"></textarea>
                    <div class="flex gap-2 items-center">
                      <button @click="doWrite(a, 'no')"
                        :disabled="!reasonDraft[a.id]?.trim() || writing === a.id"
                        class="px-2.5 py-1 rounded text-[11px] border border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
                        确认放弃
                      </button>
                      <button @click="closeReason(a.id)"
                        class="px-2.5 py-1 rounded text-[11px] border border-border text-muted hover:text-gray-200 transition-colors">
                        取消
                      </button>
                      <span class="text-[10px] text-muted">放弃必须填理由</span>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 右：放弃理由清单（高频理由 = 最易失守的纪律点） -->
      <div class="w-72 shrink-0">
        <div class="bg-card border border-border rounded-lg p-4">
          <h2 class="text-sm font-bold text-gray-100 mb-1">放弃理由</h2>
          <p class="text-[10px] text-muted mb-3">高频理由 = 你最易失守的纪律点（周报复盘用）</p>
          <div v-if="!reasons.length" class="text-xs text-muted">暂无放弃记录。</div>
          <div v-else class="space-y-2.5">
            <div v-for="(r, i) in reasons" :key="i" class="border-l-2 border-amber-500/40 pl-2.5">
              <div class="text-[10px] text-muted flex items-center justify-between">
                <span>{{ r.alert_date }}</span>
                <span>{{ r.label }}</span>
              </div>
              <div class="text-[11px] text-gray-300 mt-0.5">{{ r.name || r.code }}</div>
              <div class="text-[11px] text-amber-400/90 mt-0.5">{{ r.abandon_reason }}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, reactive, onMounted } from 'vue'
import {
  getCoachAlerts,
  executeCoachAlert,
  getCoachConsistency,
  getCoachPlanExecutionRate,
  getCoachAbandonReasons,
  getCoachAttribution,
} from '../api'

const loading = ref(false)
const error = ref('')
const alerts = ref([])
const consistency = ref(null)
const planRate = ref(null)
const reasons = ref([])
const days = ref(30)
const writing = ref(null)          // 正在回写的 alert id
const reasonOpen = reactive({})    // { [id]: true } 展开理由输入
const reasonDraft = reactive({})   // { [id]: '理由文本' }

// ★ 2026-09-29（P2）：事后归因（四个维度 = 逻辑错 / 执行错 / 时机错 / 成因）
const attr = ref(null)
const groups = computed(() => attr.value?.groups || {})
const DIMS = [
  { key: 'by_rule', title: '① 按建议类型', hint: '「逻辑错」：哪条期望最差' },
  { key: 'by_executed', title: '② 按执行结果', hint: '「执行错」：放弃的后果' },
  { key: 'by_phase', title: '③ 按主力阶段', hint: '「时机错」：同逻辑的阶段差异' },
  { key: 'by_abandon', title: '④ 按放弃理由', hint: '仅放弃的建议（成因）' },
]
// 超额配色：正=红（跑赢基准）、负=绿（A 股惯例；与页面其它处一致）
const excText = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + 'pt')
const excCls = (v) => (v == null ? 'text-muted' : v >= 0 ? 'text-rise' : 'text-fall')

async function loadAttribution() {
  try {
    // ⚠️ 归因窗口**不低于 60 天**：它靠累积样本（组内 n<5 置灰），"近 7 天"会近乎全灰。
    //   标题会显示实际窗口，避免与上方 KPI 的"近 N 天"混淆。
    const { data } = await getCoachAttribution(Math.max(days.value, 60))
    attr.value = data
  } catch (e) {
    console.error('loadAttribution error', e)
  }
}

// 严重度映射（与后端 rules.yaml 的 severity: alert/warn/info 同口径）
const severityMap = {
  alert: { label: '硬警报', cls: 'bg-red-500/15 text-red-400 border-red-500/20' },
  warn: { label: '提示', cls: 'bg-amber-500/15 text-amber-400 border-amber-500/20' },
  info: { label: '信息', cls: 'bg-blue-500/15 text-blue-400 border-blue-500/20' },
}
const severityLabel = (s) => severityMap[s]?.label || s || ''
const severityCls = (s) => severityMap[s]?.cls || 'bg-white/5 text-muted border-border'

function openReason(id) {
  reasonOpen[id] = true
  if (reasonDraft[id] == null) reasonDraft[id] = ''
}
function closeReason(id) {
  reasonOpen[id] = false
}

async function doWrite(alert, executed) {
  const reason = executed === 'no' ? (reasonDraft[alert.id] || '').trim() : ''
  if (executed === 'no' && !reason) return
  writing.value = alert.id
  error.value = ''
  try {
    await executeCoachAlert(alert.id, executed, reason)
    // 本地即时更新，避免整表重拉
    alert.executed = executed
    alert.abandon_reason = reason || null
    reasonOpen[alert.id] = false
    await Promise.all([loadConsistency(), loadReasons()])
  } catch (e) {
    error.value = '回写失败：' + (e.response?.data?.detail || e.message)
  } finally {
    writing.value = null
  }
}

async function loadConsistency() {
  try {
    const { data } = await getCoachConsistency(days.value)
    consistency.value = data
  } catch (e) {
    console.error('loadConsistency error', e)
  }
}

async function loadPlanRate() {
  try {
    const { data } = await getCoachPlanExecutionRate(days.value)
    planRate.value = data
  } catch (e) {
    console.error('loadPlanRate error', e)
  }
}

async function loadReasons() {
  try {
    const { data } = await getCoachAbandonReasons(20)
    reasons.value = data?.data || []
  } catch (e) {
    console.error('loadReasons error', e)
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getCoachAlerts(50)
    alerts.value = data?.data || []
    await Promise.all([loadConsistency(), loadPlanRate(), loadReasons(), loadAttribution()])
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
