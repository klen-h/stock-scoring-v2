<template>
  <!-- ★ 2026-11-14（用户需求）：快讯三时段复盘卡——盘前/盘中/盘后各时段最新判断 +
       确定性 verdict（宏观判断 vs 可观测实况）+ LLM 矛盾解释（prev_check）。
       数据：GET /flash/brief/review（只读不烧 LLM）；时段切点与教练侧复盘对账一致。
       ★★ 2026-11-16 样式整理：右栏是定高窄栏 ⇒ 列表区可滚动；利好/利空改 chip 分组；
          未判定时显式显示"待验证"（原先空白，看着像漏数据）；
          底部说明去掉内部字段名 `prev_check`。 -->
  <div class="bg-card border border-border rounded-lg p-3 text-xs h-full flex flex-col min-h-0">
    <div class="flex items-center gap-2 shrink-0">
      <span class="text-sm font-semibold">快讯三时段</span>
      <span class="text-[10px] text-muted">判断 vs 实况 · 观察参考</span>
      <span v-if="date" class="ml-auto text-[10px] text-muted font-mono">{{ date }}</span>
    </div>

    <div v-if="phases.length" class="mt-2 flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-0.5">
      <div v-for="p in phases" :key="p.phase"
           class="rounded border px-2 py-1.5"
           :class="phaseTone(p.phase).box">
        <!-- 时段 / 时间 / 基调 / 判定 -->
        <div class="flex items-center gap-1.5 flex-wrap">
          <span class="w-1 h-3 rounded-full shrink-0" :class="phaseTone(p.phase).dot"></span>
          <span class="text-[11px] font-semibold" :class="phaseTone(p.phase).label">{{ p.phase }}</span>
          <span v-if="p.ts" class="font-mono text-[10px] text-muted">{{ p.ts }}</span>
          <span v-if="p.stance" class="px-1.5 rounded text-[10px] font-medium"
                :class="stanceTone(p.stance)">{{ p.stance }}</span>
          <span v-if="verdictOf(p)" class="px-1.5 rounded text-[10px] font-medium"
                :class="verdictTone(verdictOf(p))">{{ verdictOf(p) }}</span>
          <span v-if="!p.conclusion" class="ml-auto text-[10px] text-muted/70">待生成</span>
        </div>

        <div v-if="p.conclusion" class="mt-1 text-[11px] text-gray-200 leading-snug break-words">
          {{ p.conclusion }}
        </div>

        <!-- 利好/利空：chip 分组（原来 8 条平铺成一行，分不清正反） -->
        <div v-if="p.bullish?.length" class="mt-1.5 flex flex-wrap items-center gap-1">
          <span class="text-[9px] text-rise/80 shrink-0 mr-0.5">利好</span>
          <span v-for="(b, i) in p.bullish" :key="'b' + i"
                class="px-1 py-px rounded bg-rose-500/10 text-rise text-[10px] leading-tight"
                :title="b">{{ b }}</span>
        </div>
        <div v-if="p.bearish?.length" class="mt-1 flex flex-wrap items-center gap-1">
          <span class="text-[9px] text-fall/80 shrink-0 mr-0.5">利空</span>
          <span v-for="(b, i) in p.bearish" :key="'x' + i"
                class="px-1 py-px rounded bg-emerald-500/10 text-fall text-[10px] leading-tight"
                :title="b">{{ b }}</span>
        </div>

        <div v-if="p.prev_check"
             class="mt-1.5 pt-1 border-t border-white/5 text-[10px] text-amber-300/90 leading-relaxed">
          <span class="text-amber-400 font-medium">验证</span> {{ p.prev_check }}
        </div>
      </div>
    </div>

    <div v-else class="mt-2 flex-1 flex items-center justify-center text-[11px] text-muted">
      今日尚无解读（盘中自动触发，或点「AI 解读」生成）
    </div>

    <div class="mt-2 pt-1.5 border-t border-border/50 text-[10px] text-muted leading-relaxed shrink-0"
         title="上证涨跌幅 ±0.3% 为分界：与基调一致记命中，相反记打脸，之间记待验证">
      盘前 / 盘中判断由后续时段实况自动判定；矛盾由下一次解读说明。
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

// Tailwind 只扫描完整类名字符串 ⇒ 这里整串返回，不能拼模板
function phaseTone(phase) {
  if (phase === '盘前') return {
    box: 'border-sky-400/25 bg-sky-400/[0.04]',
    dot: 'bg-sky-400', label: 'text-sky-300',
  }
  if (phase === '盘中') return {
    box: 'border-amber-400/25 bg-amber-400/[0.04]',
    dot: 'bg-amber-400', label: 'text-amber-300',
  }
  return {
    box: 'border-violet-400/25 bg-violet-400/[0.04]',
    dot: 'bg-violet-400', label: 'text-violet-300',
  }
}

function stanceTone(s) {
  if (s === '进攻') return 'bg-rose-500/20 text-rose-300'
  if (s === '防御') return 'bg-sky-500/20 text-sky-300'
  return 'bg-white/5 text-muted'
}

/** 已解读但尚未被后续时段判定 ⇒ 显示"待验证"，不要留空白让人以为漏数据。 */
function verdictOf(p) {
  if (p.verdict) return p.verdict
  return p.conclusion ? '待验证' : ''
}

// ★ 2026-11-22（用户："命中颜色应该是红色"）：与 Workbench 复盘卡同一套语义色
//   （本项目 rise=红=好 / fall=绿=差，A 股配色）。原先这里是"命中绿、打脸红"，
//   与同页盈亏、涨跌幅语义相反。
function verdictTone(v) {
  if (v === '命中') return 'bg-rise/20 text-rise'
  if (v === '打脸') return 'bg-fall/20 text-fall'
  return 'bg-white/5 text-muted'
}

async function load() {
  try {
    const { data } = await axios.get(
      (import.meta.env.VITE_API_BASE_URL || '/api') + '/flash/brief/review',
      { params: props.date ? { date: props.date } : {}, timeout: 20000 })
    date.value = data.date || ''
    phases.value = data.phases || []
  } catch { /* 静默：卡不阻塞页面 */ }
}

// 切换回放日期时重新取数（原实现只在 mounted 拉一次，切日期后卡片一直显示旧日内容）
watch(() => props.date, load)

onMounted(() => {
  load()
  // ★ 与 FlashBriefCard 同纪律：休市不轮询（数据只在解读生成后变化）
  timer = setInterval(() => { if (isTradingTime()) load() }, 120000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>
