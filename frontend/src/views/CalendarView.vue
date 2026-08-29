<template>
  <div class="fade-in space-y-4">
    <!-- 头部：标题 + 刷新 + 筛选 -->
    <div class="bg-card border border-border rounded-lg p-4 space-y-3">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">财经日历</h1>
          <p class="text-xs text-muted mt-0.5">
            金十数据 · 经济指标（含前值/预期/实际）、事件讲话、交易所休市。
            数据每日 07:00 自动刷新，★ 为金十标注的重要性。
            <span v-if="updatedAt" class="text-gray-400">更新于 {{ updatedAt }}</span>
            <span v-if="range.start" class="text-muted/70">（缓存 {{ range.start }} ~ {{ range.end }}，共 {{ total }} 条）</span>
          </p>
        </div>
        <button @click="doRefresh" :disabled="refreshing"
          class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
          {{ refreshing ? '刷新中…' : '↻ 立即刷新' }}
        </button>
      </div>

      <!-- 筛选：区间 / 星级 / 类型 -->
      <div class="flex items-center gap-5 text-xs flex-wrap">
        <div class="flex items-center gap-1.5">
          <span class="text-muted">区间</span>
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="d in dayOptions" :key="d"
              class="px-2.5 py-1 rounded transition-colors"
              :class="days === d ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
              @click="days = d">{{ d }}天</button>
          </div>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="text-muted">星级</span>
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="s in starOptions" :key="s.value"
              class="px-2.5 py-1 rounded transition-colors"
              :class="minStar === s.value ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
              @click="minStar = s.value">{{ s.label }}</button>
          </div>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="text-muted">类型</span>
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="k in kindOptions" :key="k.value"
              class="px-2.5 py-1 rounded transition-colors"
              :class="kind === k.value ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
              @click="kind = k.value">{{ k.label }}</button>
          </div>
        </div>
        <span v-if="loading" class="text-accent">⏳ 加载中…</span>
        <span v-if="error" class="text-fall">⚠️ {{ error }}</span>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="!loading && !grouped.length" class="bg-card border border-border rounded-lg p-8 text-center">
      <p class="text-sm text-muted">该筛选条件下没有日程</p>
      <p class="text-xs text-muted/70 mt-1">试试放宽星级（当前 ★≥{{ minStar || '不限' }}）或延长区间</p>
    </div>

    <!-- 按日期分组的日程 -->
    <div v-for="g in grouped" :key="g.date"
      class="bg-card border border-border rounded-lg overflow-hidden">
      <!-- 日期头 -->
      <div class="px-3 py-2 border-b border-border flex items-center gap-2"
        :class="g.isToday ? 'bg-accent/10' : 'bg-white/[0.02]'">
        <span class="text-sm font-bold" :class="g.isToday ? 'text-accent' : 'text-gray-200'">
          {{ g.date.slice(5) }}
        </span>
        <span class="text-xs text-muted">{{ g.week }}</span>
        <span v-if="g.rel" class="text-[10px] px-1.5 py-0.5 rounded bg-accent/15 text-accent">{{ g.rel }}</span>
        <span v-if="g.isWeekend" class="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-muted">周末</span>
        <span class="ml-auto text-xs text-muted">{{ g.items.length }} 项</span>
      </div>

      <!-- 日程条目 -->
      <div v-for="it in g.items" :key="it.kind + '-' + it.id"
        class="px-3 py-2 border-b border-border/50 last:border-b-0 hover:bg-white/[0.02]">
        <div class="flex items-start gap-3">
          <!-- 时间 -->
          <span class="text-xs font-mono text-muted w-11 flex-shrink-0 pt-0.5">{{ timeOf(it) }}</span>
          <!-- 星级 -->
          <span class="text-[11px] w-14 flex-shrink-0 pt-0.5" :class="starClass(it.star)">
            {{ starText(it.star) }}
          </span>
          <!-- 主体 -->
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="text-xs px-1.5 py-0.5 rounded bg-bg text-muted flex-shrink-0">
                {{ it.country || '—' }}
              </span>
              <span class="text-sm text-gray-200">{{ it.title }}</span>
              <span v-if="it.period" class="text-[11px] text-muted">({{ it.period }})</span>
              <!-- 经济指标：前值/预期/实际 -->
              <span v-if="it.kind === 'data'" class="text-[11px] font-mono flex items-center gap-2 flex-wrap">
                <span class="text-muted">前值 <span class="text-gray-300">{{ val(it.prev) }}</span></span>
                <span class="text-muted">预期 <span class="text-accent">{{ val(it.consensus) }}</span></span>
                <span v-if="it.actual != null && it.actual !== ''">
                  实际 <span class="text-gray-100 font-bold">{{ it.actual }}</span>
                </span>
                <span v-else class="text-muted/60">待公布</span>
                <span v-if="it.unit" class="text-muted/60">{{ it.unit }}</span>
              </span>
            </div>
            <!-- 事件内容 / 休市说明 -->
            <p v-if="it.kind === 'event' && it.content"
              class="text-[11px] text-muted mt-0.5 leading-relaxed">{{ it.content }}</p>
            <p v-else-if="it.kind === 'holiday'"
              class="text-[11px] mt-0.5" :class="isAHoliday(it) ? 'text-rise' : 'text-muted'">
              🏖 {{ it.exchange }} · {{ it.rest_note || '休市' }}
              <span v-if="isAHoliday(it)" class="text-[10px] px-1 rounded bg-rise/15 text-rise ml-1">A股</span>
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { getCalendar, refreshCalendar } from '../api'

const items = ref([])
const total = ref(0)
const range = ref({})
const updatedAt = ref('')
const loading = ref(false)
const refreshing = ref(false)
const error = ref('')

// 筛选条件
const days = ref(7)
const minStar = ref(0)
const kind = ref('')
const dayOptions = [3, 7, 14]
const starOptions = [
  { value: 0, label: '全部' },
  { value: 4, label: '★≥4' },
  { value: 5, label: '★★★★★' },
]
const kindOptions = [
  { value: '', label: '全部' },
  { value: 'data', label: '经济指标' },
  { value: 'event', label: '事件讲话' },
  { value: 'holiday', label: '休市' },
]

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getCalendar({ days: days.value, min_star: minStar.value, kind: kind.value })
    items.value = data.items || []
    total.value = data.total || 0
    range.value = data.range || {}
    updatedAt.value = data.updated_at
      ? new Date(data.updated_at).toLocaleString('zh-CN', {
          month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
      : ''
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
    items.value = []
  } finally {
    loading.value = false
  }
}

async function doRefresh() {
  refreshing.value = true
  try {
    await refreshCalendar(14)
    await load()
  } catch (e) {
    error.value = '刷新失败：' + (e.response?.data?.detail || e.message)
  } finally {
    refreshing.value = false
  }
}

// ── 展示辅助 ──
function timeOf(it) {
  const t = it.time || ''
  return t.length >= 16 ? t.slice(11, 16) : (t.slice(5) || '—')
}

function val(v) {
  return (v === null || v === undefined || v === '') ? '—' : v
}

function starText(star) {
  if (!star) return '—'
  return '★'.repeat(Math.min(star, 5))
}

function starClass(star) {
  if (star >= 5) return 'text-rise'        // 红：最高重要性
  if (star === 4) return 'text-accent'     // 蓝：次高
  return 'text-muted'
}

// 是否为 A 股（沪深/北交所）休市
function isAHoliday(it) {
  const ex = it.exchange || ''
  return ex.includes('沪深') || ex.includes('北交所')
}

// 按日期分组（升序），并附加周几/今天/明天等元信息
const grouped = computed(() => {
  const map = {}
  for (const it of items.value) {
    const d = it.date || ''
    if (!map[d]) map[d] = []
    map[d].push(it)
  }
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  return Object.keys(map).sort().map((d) => {
    const dt = new Date(d + 'T00:00:00')
    const diff = Math.round((dt - today) / 86400000)
    return {
      date: d,
      week: ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][dt.getDay()],
      rel: diff === 0 ? '今天' : diff === 1 ? '明天' : diff === 2 ? '后天' : '',
      isToday: diff === 0,
      isWeekend: dt.getDay() === 0 || dt.getDay() === 6,
      items: map[d],
    }
  })
})

watch([days, minStar, kind], load)
onMounted(load)
</script>
