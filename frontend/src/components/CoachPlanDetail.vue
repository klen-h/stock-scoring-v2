<template>
  <div v-if="plan" class="text-xs">
    <div class="flex items-center gap-2 mb-2">
      <span class="font-bold text-gray-200">教练剧本</span>
      <span class="px-1.5 py-0.5 rounded text-[10px] border" :class="statusCls">{{ statusLabel }}</span>
    </div>
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-[11px] text-muted mb-2">
      <div>止损 <span class="text-rise font-mono">{{ plan.stop_loss ?? '-' }}</span></div>
      <div>复核日 <span class="font-mono text-gray-200">{{ plan.review_date ?? '-' }}</span></div>
      <div>移动止盈触发 <span class="font-mono text-gray-200">{{ plan.trail_trigger_pct != null ? plan.trail_trigger_pct + '%' : '-' }}</span></div>
      <div>开仓日 <span class="font-mono text-gray-200">{{ plan.plan_date ?? '-' }}</span></div>
    </div>
    <div v-if="conditions.length" class="space-y-1 mb-2">
      <div v-for="(c, i) in conditions" :key="i" class="text-[11px] text-gray-300">· {{ c }}</div>
    </div>
    <div v-if="plan.status === 'open'" class="mt-2">
      <div v-if="!open">
        <button @click="open = true"
          class="px-2 py-1 rounded text-[11px] border border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20">
          ✗ 放弃剧本
        </button>
        <span class="text-[10px] text-muted ml-2">放弃须填理由，回写供复盘</span>
      </div>
      <div v-else class="space-y-1.5">
        <textarea v-model="draft" rows="2"
          placeholder="放弃剧本的原因（必填；例：想等反抽再止损 / 觉得基本面会扛住）"
          class="w-full bg-bg border border-border rounded px-2 py-1 text-[11px] text-gray-200 focus:outline-none focus:border-accent/50 resize-none"></textarea>
        <div class="flex gap-2 items-center">
          <button @click="submit" :disabled="!draft.trim() || sending"
            class="px-2 py-1 rounded text-[11px] border border-amber-500/30 bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 disabled:opacity-40 disabled:cursor-not-allowed">
            确认放弃
          </button>
          <button @click="open = false"
            class="px-2 py-1 rounded text-[11px] border border-border text-muted hover:text-gray-200">
            取消
          </button>
        </div>
      </div>
    </div>
    <div v-else-if="plan.status === 'abandoned'" class="text-[11px] text-amber-400 mt-2">
      已放弃：{{ plan.abandon_reason }}
    </div>
    <div v-else-if="plan.status === 'closed'" class="text-[11px] text-muted mt-2">
      已关闭（{{ reasonText(plan.actual_exit_reason) }}）· 按剧本离场：{{ plan.followed ? '是' : '否' }}
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { abandonCoachPlan } from '../api'

const props = defineProps({ plan: Object })
const emit = defineEmits(['updated'])

const open = ref(false)
const draft = ref('')
const sending = ref(false)

const conditions = computed(() => {
  if (!props.plan?.exit_conditions) return []
  try {
    return Object.entries(JSON.parse(props.plan.exit_conditions)).map(([k, v]) => `${k}：${v}`)
  } catch {
    return [props.plan.exit_conditions]
  }
})

const statusLabel = computed(() =>
  ({ open: '进行中', closed: '已关闭', abandoned: '已放弃' }[props.plan?.status] || props.plan?.status || '未知'))

const statusCls = computed(() =>
  ({
    open: 'border-accent/40 text-accent bg-accent/10',
    closed: 'border-border text-muted bg-white/[0.03]',
    abandoned: 'border-amber-500/40 text-amber-400 bg-amber-500/10',
  }[props.plan?.status] || 'border-border text-muted bg-white/[0.03]'))

function reasonText(r) {
  return {
    stop_loss: '止损', take_profit: '止盈', expire: '超期',
    timeout_no_trigger: '超时未触发',
    manual: '手动', fill_rejected: '未成交', manual_cancel: '已取消',
    abandon: '主动放弃',
  }[r] || r || '-'
}

async function submit() {
  const reason = draft.value.trim()
  if (!reason) return
  sending.value = true
  try {
    await abandonCoachPlan(props.plan.id, reason)
    draft.value = ''
    open.value = false
    emit('updated')
  } catch (e) {
    alert(e?.response?.data?.detail || '放弃失败')
  } finally {
    sending.value = false
  }
}
</script>
