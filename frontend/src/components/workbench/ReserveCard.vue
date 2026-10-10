<template>
  <div class="bg-card border border-border rounded-lg p-4">
    <div class="flex items-start justify-between gap-2 mb-2">
      <div class="min-w-0">
        <div class="text-sm font-semibold">央行储备（月频慢变量）</div>
        <div class="text-[10px] text-muted">结构背景 · 非交易信号 · 不进决策链</div>
      </div>
      <span v-if="latest" class="text-[10px] text-muted flex-shrink-0">{{ latest.month }} · 外管局/央行口径</span>
    </div>

    <div v-if="err" class="text-muted text-xs">—（加载失败：{{ err }}）</div>
    <div v-else-if="!items.length" class="text-muted text-xs">加载中…</div>
    <template v-else>
      <div class="flex flex-wrap items-center gap-x-5 gap-y-2 min-w-0">
        <div class="flex items-center gap-2.5 flex-shrink-0">
          <span class="text-4xl font-bold font-mono leading-none text-amber-400">{{ latest.gold_reserve_tonnes ?? '—' }}</span>
          <div class="min-w-0">
            <div class="text-xs font-semibold whitespace-nowrap">黄金储备 · 吨</div>
            <div class="text-[11px] text-muted whitespace-nowrap">{{ latest.gold_reserve_wan_oz }} 万盎司</div>
          </div>
        </div>
        <div class="w-px h-12 bg-border flex-shrink-0"></div>
        <div class="flex items-center gap-2.5 flex-shrink-0">
          <span class="text-4xl font-bold font-mono leading-none text-gray-100">{{ fxWanYi }}</span>
          <div class="min-w-0">
            <div class="text-xs font-semibold whitespace-nowrap">外汇储备 · 万亿美元</div>
            <div class="text-[11px] text-muted whitespace-nowrap">{{ latest.fx_reserve_100m_usd }} 亿美元</div>
          </div>
        </div>
        <div class="w-px h-12 bg-border flex-shrink-0"></div>
        <div class="flex items-center gap-2.5 flex-shrink-0">
          <span class="text-4xl font-bold font-mono leading-none text-amber-400">{{ latest.gold_share_pct ?? '—' }}<span class="text-base">%</span></span>
          <div class="min-w-0">
            <div class="text-xs font-semibold whitespace-nowrap">黄金占储备</div>
            <div class="text-[11px] text-muted whitespace-nowrap" :title="shareNote">月末近似</div>
          </div>
        </div>
        <div class="min-w-[150px] flex-1">
          <div class="flex items-end gap-[2px] h-10">
            <div v-for="(it, i) in trend" :key="i" class="flex-1 bg-amber-500/60 rounded-sm"
                 :style="{ height: barH(it) }"
                 :title="it.month + ' · ' + (it.gold_reserve_tonnes ?? '—') + ' 吨'"></div>
          </div>
          <div class="text-[10px] text-muted">近 {{ trend.length }} 月黄金储备（吨）</div>
        </div>
      </div>
      <div class="mt-2 text-[10px] text-muted leading-relaxed">{{ disclaimer }}</div>
    </template>
  </div>
</template>

<script setup>
// ==============================================================================
// 【ReserveCard】央行储备月度面板（P0-2，见 `PLAN_RESERVE_SIGNALS.md` §6.2）
//   · 定位：把“减美债、增黄金”落成 **可追溯的月频事实**；**非交易信号、不进决策链**
//   · 数据：GET /api/macro/reserves（源 = akshare macro_china_foreign_exchange_gold）
//   · **自取数据**（月频数据无需进 120s 轮询组）：挂载时一次请求，失败降级为“—”
//   · 单位照抄官方口径：黄金=万盎司（吨为换算展示）、外储=亿美元
// ==============================================================================
import { computed, onMounted, ref } from 'vue'
import { getMacroReserves } from '../../api'

const items = ref([])
const latest = ref(null)
const disclaimer = ref('结构背景（月频慢变量），非交易信号，不进决策链')
const shareNote = ref('')
const err = ref('')

const fxWanYi = computed(() => {
  const v = latest.value && latest.value.fx_reserve_100m_usd
  return v ? (v / 10000).toFixed(2) : '—'
})
const trend = computed(() => (items.value || []).slice(-12))
const _max = computed(() => Math.max(...trend.value.map(x => x.gold_reserve_tonnes || 0), 1))
const _min = computed(() => Math.min(...trend.value.map(x => x.gold_reserve_tonnes || 0)))

function barH(it) {
  const v = it.gold_reserve_tonnes || 0
  const lo = _min.value
  const hi = _max.value
  const r = hi > lo ? (v - lo) / (hi - lo) : 0.5
  return (20 + r * 80) + '%'      // 20%~100%：避免最矮那根看不见
}

onMounted(async () => {
  try {
    const { data } = await getMacroReserves(24)
    items.value = (data && data.items) || []
    latest.value = (data && data.latest) || items.value[items.value.length - 1] || null
    if (data && data.disclaimer) disclaimer.value = data.disclaimer
    if (data && data.share_note) shareNote.value = data.share_note
  } catch (e) {
    err.value = (e && e.message) || String(e)
  }
})
</script>
