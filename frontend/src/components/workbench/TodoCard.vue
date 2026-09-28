<template>
  <div class="border-b border-border/40 py-2 text-xs">
    <div class="flex gap-2 items-center flex-wrap">
      <!-- ★ 2026-09-28：改走共享 `hhmm()` —— 原为 `.slice(0, 5)`，而 `alert_time` 是
           **完整 ISO**（2026-09-28T09:35:12）⇒ 截出来是 `2026-`（用户："2026- 少了什么？"）。 -->
      <span class="font-mono text-muted">{{ hhmm(alert.alert_time) }}</span>
      <span class="font-semibold">{{ alert.label }}</span>
      <span v-if="alert.code" class="text-muted">
        <a :href="xqUrl(alert.code)" target="_blank" class="hover:text-accent" title="雪球">{{ alert.code }}</a>
        <a :href="'#/stock/' + alert.code" target="_blank" class="hover:text-accent">{{ alert.name }}</a>
      </span>
      <!-- 状态徽标：**已决策的永远显示结果**（用户："决策的结果展示在那里即可，无需消失"）；
           未决策 + 回放/只读时显示"未决策"。 -->
      <span v-if="isDone || readonly" class="ml-auto"
            :class="alert.executed === 'yes' ? 'text-emerald-400' : alert.executed === 'no' ? 'text-amber-400' : 'text-muted'">
        {{ alert.executed === 'yes' ? '已执行' : alert.executed === 'no' ? '已放弃' : '未决策' }}
      </span>
    </div>
    <div class="text-muted mt-1 whitespace-pre-wrap">{{ firstLine(alert.message) }}</div>

    <!-- 操作按钮：**仅"未决策且可交互"**时出现
         ★★ 2026-09-28 修复：原条件只有 `!readonly` ⇒ 已执行/已放弃的卡**仍显示按钮**
         （点上就能重复回写教练一致性数据）。现在由 `alert.executed` 自己把关，
         不再依赖外层是否传 `readonly`（漏传就是当初那个 bug 的成因）。 -->
    <div v-if="canAct" class="mt-1.5 flex gap-2">
      <button class="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25"
              @click="mode = 'exec'">执行</button>
      <button class="px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/30 hover:bg-amber-500/25"
              @click="mode = 'abandon'">放弃</button>
    </div>

    <!-- 二次确认：执行（防误触——这是纪律回写数据） -->
    <div v-if="canAct && mode === 'exec'" class="mt-1.5 text-[11px]">
      <div class="text-muted">确认已按纪律执行？（二次确认防误触）</div>
      <div class="flex gap-2 mt-1">
        <button class="px-2 py-0.5 rounded bg-emerald-500 text-black font-semibold disabled:opacity-50"
                :disabled="busy" @click="submit('yes')">{{ busy ? '提交中…' : '确认执行' }}</button>
        <button class="px-2 py-0.5 rounded border border-border text-muted" @click="cancel">取消</button>
      </div>
    </div>

    <!-- 二次确认：放弃（理由必填，回写供周报复盘） -->
    <div v-if="canAct && mode === 'abandon'" class="mt-1.5">
      <textarea v-model="reason" rows="2"
                placeholder="放弃理由（必填，回写供周报复盘）"
                class="w-full bg-background border border-border rounded px-2 py-1 text-[11px]"></textarea>
      <div v-if="err" class="text-red-400 text-[11px]">{{ err }}</div>
      <div class="flex gap-2 mt-1">
        <button class="px-2 py-0.5 rounded bg-amber-500 text-black font-semibold disabled:opacity-50"
                :disabled="busy" @click="submit('no')">{{ busy ? '提交中…' : '提交放弃' }}</button>
        <button class="px-2 py-0.5 rounded border border-border text-muted" @click="cancel">取消</button>
      </div>
    </div>
  </div>
</template>

<script setup>
// ★ 2026-09-25（工作台 Phase 2）：教练卡执行/放弃回写卡，交互自包含。
//   - 二次确认：第一次点选动作、第二次确认提交（防误触污染一致性统计）
//   - 放弃必须填理由（后端 400 校验同款），理由回写供周报复盘
//   - 教练卡是纪律提醒，不是自动交易指令——按钮语义是"我已执行/我放弃"
// ★ 2026-09-28：① 时刻统一走共享 `hhmm()`（原 `.slice(0,5)` 在 ISO 上显示成 `2026-`）；
//   ② **已决策 ⇒ 自动只读**（显示结果徽标、藏掉按钮）—— 修"已执行的卡还能重复回写"，
//      并让"决策结果"留在原地不消失（用户要求）。
import { computed, ref } from 'vue'

// 雪球链接（A股代码前缀 SH/SZ/BJ）——与 Workbench.vue 同口径
function xqUrl(code) {
  const c = String(code || '').replace(/\D/g, '').slice(0, 6)
  const pfx = c.startsWith('6') || c.startsWith('9') ? 'SH' : (c.startsWith('4') || c.startsWith('8') ? 'BJ' : 'SZ')
  return `https://xueqiu.com/S/${pfx}${c}`
}
import { executeCoachAlert } from '../../api'
import { hhmm } from '../../composables/displayMeta'

const props = defineProps({
  alert: { type: Object, required: true },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(['done'])

/**
 * 是否**已决策**。`coach_alerts.executed` 是三态**字符串**（不是布尔）：
 * `'yes'` 已执行 ｜ `'no'` 已放弃 ｜ 其它（null/''）未决策 ⇒ 不能用 `!!` 之外的花样。
 */
const isDone = computed(() => !!(props.alert && props.alert.executed))

/** 可交互 = 非只读/非回放 **且未决策**。 */
const canAct = computed(() => !props.readonly && !isDone.value)

const mode = ref(null)        // null | 'exec' | 'abandon'
const reason = ref('')
const err = ref('')
const busy = ref(false)

const firstLine = (s) => String(s || '').split('\n').find(x => x.trim()) || ''
function cancel() { mode.value = null; err.value = ''; reason.value = '' }

async function submit(executed) {
  if (executed === 'no' && !reason.value.trim()) {
    err.value = '放弃必须填写理由（回写供周报复盘）'
    return
  }
  busy.value = true
  err.value = ''
  try {
    await executeCoachAlert(props.alert.id, executed, reason.value.trim())
    mode.value = null
    emit('done', { id: props.alert.id, executed })
  } catch (e) {
    err.value = e?.response?.data?.detail || e?.message || '回写失败'
  } finally {
    busy.value = false
  }
}
</script>
