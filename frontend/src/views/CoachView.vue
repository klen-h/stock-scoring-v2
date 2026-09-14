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
          <select v-model.number="days" @change="loadConsistency"
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
import { ref, reactive, onMounted } from 'vue'
import {
  getCoachAlerts,
  executeCoachAlert,
  getCoachConsistency,
  getCoachAbandonReasons,
} from '../api'

const loading = ref(false)
const error = ref('')
const alerts = ref([])
const consistency = ref(null)
const reasons = ref([])
const days = ref(30)
const writing = ref(null)          // 正在回写的 alert id
const reasonOpen = reactive({})    // { [id]: true } 展开理由输入
const reasonDraft = reactive({})   // { [id]: '理由文本' }

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
    await Promise.all([loadConsistency(), loadReasons()])
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
