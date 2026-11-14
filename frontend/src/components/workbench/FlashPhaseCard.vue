<template>
  <!-- ★ 2026-11-14（用户需求）：快讯三时段复盘卡——盘前/午盘/盘后各时段最新判断 +
       确定性 verdict（宏观判断 vs 可观测实况）+ LLM 矛盾解释（prev_check）。
       数据：GET /flash/brief/review（只读不烧 LLM）；时段切点与教练侧复盘对账一致。 -->
  <div class="bg-card border border-border rounded-lg p-3 text-xs">
    <div class="flex items-center gap-2 flex-wrap">
      <span class="text-sm font-semibold">快讯三时段</span>
      <span class="text-[10px] text-muted">判断 vs 实况 · 观察参考</span>
      <span v-if="date" class="ml-auto text-[10px] text-muted font-mono">{{ date }}</span>
    </div>

    <div v-if="phases.length" class="mt-2 space-y-2">
      <div v-for="p in phases" :key="p.phase"
           class="rounded border border-border/60 px-2 py-1.5 bg-white/[0.04]">
        <div class="flex items-center gap-1.5 flex-wrap">
          <span class="font-semibold">{{ p.phase }}</span>
          <span v-if="p.ts" class="font-mono text-[10px] text-muted">{{ p.ts }}</span>
          <span v-if="p.stance" class="px-1.5 rounded text-[10px] font-medium"
                :class="p.stance === '进攻' ? 'bg-red-500/20 text-red-300'
                        : p.stance === '防御' ? 'bg-sky-500/20 text-sky-300'
                        : 'bg-white/5 text-muted'">{{ p.stance }}</span>
          <span v-if="p.verdict" class="px-1.5 rounded text-[10px] font-medium"
                :class="p.verdict === '命中' ? 'bg-emerald-500/20 text-emerald-300'
                        : p.verdict === '打脸' ? 'bg-red-500/20 text-red-300'
                        : 'bg-white/5 text-muted'">{{ p.verdict }}</span>
          <span v-if="!p.conclusion" class="ml-auto text-[10px] text-muted/70">暂无解读</span>
        </div>
        <div v-if="p.conclusion" class="mt-0.5 text-[11px] text-gray-200 leading-snug">
          {{ p.conclusion }}
        </div>
        <div v-if="p.bullish?.length || p.bearish?.length" class="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px]">
          <span v-for="(b, i) in p.bullish" :key="'b' + i" class="text-rise">利好·{{ b }}</span>
          <span v-for="(b, i) in p.bearish" :key="'x' + i" class="text-fall">利空·{{ b }}</span>
        </div>
        <div v-if="p.prev_check" class="mt-1 text-[10px] text-amber-300/90 leading-relaxed">
          <span class="text-amber-400">验证</span> {{ p.prev_check }}
        </div>
      </div>
    </div>
    <div v-else class="mt-2 text-[11px] text-muted">今日尚无解读（盘中自动触发或点「AI 解读」生成）</div>

    <div class="mt-2 text-[10px] text-muted leading-relaxed">
      盘前/午盘判断由后续时段实况自动判定（上证 ±0.3% 分界）；矛盾由下一次解读的 prev_check 解释。
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from 'vue'
import axios from 'axios'
import { isTradingTime } from '../../composables/usePortfolio'

const props = defineProps({
  date: { type: String, default: '' },
})
const date = ref('')
const phases = ref([])
let timer = null

async function load() {
  try {
    const { data } = await axios.get(
      (import.meta.env.VITE_API_BASE_URL || '/api') + '/flash/brief/review',
      { params: props.date ? { date: props.date } : {}, timeout: 20000 })
    date.value = data.date || ''
    phases.value = data.phases || []
  } catch { /* 静默：卡不阻塞页面 */ }
}

onMounted(() => {
  load()
  // ★ 与 FlashBriefCard 同纪律：休市不轮询（数据只在解读生成后变化）
  timer = setInterval(() => { if (isTradingTime()) load() }, 120000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>
