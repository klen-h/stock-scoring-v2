<template>
  <div class="fade-in">
    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg p-4 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">矛盾扫描引擎</h1>
          <p class="text-xs text-muted mt-0.5">
            基于《Agent知识库》三层矛盾模型，当前落地「行为背离」扫描：指数与个股结构、板块叙事 vs 资金、量价背离、北向资金、主力资金流。
          </p>
        </div>
        <div class="flex gap-2">
          <button @click="triggerScan" :disabled="scanning"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ scanning ? '扫描中…' : '↻ 扫描' }}
          </button>
          <button @click="load(currentDate)" :disabled="loading"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ loading ? '加载中…' : '刷新' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 摘要卡片 -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4" v-if="summary">
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">扫描日期</div>
        <div class="text-base font-bold text-gray-100 mt-1">{{ summary.date }}</div>
        <div class="text-[10px] text-muted mt-0.5">收盘后自动生成</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">矛盾总数</div>
        <div class="text-base font-bold text-accent mt-1">{{ summary.total }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3 md:col-span-2">
        <div class="text-xs text-muted mb-1.5">严重度分布</div>
        <div class="flex gap-3 text-xs">
          <span class="px-2 py-0.5 rounded bg-red-500/15 text-red-400 border border-red-500/20">
            严重 {{ summary.breakdown?.severe || 0 }}
          </span>
          <span class="px-2 py-0.5 rounded bg-amber-500/15 text-amber-400 border border-amber-500/20">
            明显 {{ summary.breakdown?.obvious || 0 }}
          </span>
          <span class="px-2 py-0.5 rounded bg-blue-500/15 text-blue-400 border border-blue-500/20">
            轻微 {{ summary.breakdown?.minor || 0 }}
          </span>
        </div>
      </div>
    </div>

    <div class="flex gap-4 items-start">
      <!-- 左侧日期/严重级筛选 -->
      <div class="w-56 shrink-0 space-y-3">
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="text-xs font-medium text-gray-100 mb-2">日期</div>
          <input v-model="currentDate" type="date"
            class="w-full bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50" />
        </div>

        <div class="bg-card border border-border rounded-lg p-3">
          <div class="text-xs font-medium text-gray-100 mb-2">严重度过滤</div>
          <div class="flex flex-col gap-1.5">
            <label v-for="s in severities" :key="s.value" class="flex items-center gap-2 text-xs text-muted cursor-pointer hover:text-gray-200">
              <input type="checkbox" v-model="selectedSeverities" :value="s.value"
                class="accent-accent rounded" />
              {{ s.label }}
            </label>
          </div>
        </div>

        <div v-if="summary?.severe_cards?.length" class="bg-card border border-red-500/20 rounded-lg p-3">
          <div class="text-xs font-medium text-red-400 mb-2">严重矛盾</div>
          <div v-for="(c, i) in summary.severe_cards" :key="i" class="text-[11px] text-muted mb-2 last:mb-0">
            <div class="text-gray-200 font-medium">{{ c.title }}</div>
            <div class="mt-0.5 line-clamp-2">{{ c.summary }}</div>
          </div>
        </div>
      </div>

      <!-- 右侧列表 / 报告 -->
      <div class="flex-1 min-w-0 space-y-4">
        <div v-if="loading" class="bg-card border border-border rounded-lg p-8 text-center">
          <div class="loading-spinner mx-auto mb-3"></div>
          <p class="text-xs text-muted">加载中…</p>
        </div>

        <div v-else-if="error" class="bg-card border border-border rounded-lg p-6 text-sm text-fall">
          {{ error }}
        </div>

        <template v-else>
          <!-- 报告区 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-3">
              <h2 class="text-sm font-bold text-gray-100">AI 解读报告</h2>
              <button @click="generateReport" :disabled="reportLoading"
                class="px-2 py-1 rounded text-[10px] border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
                {{ reportLoading ? '生成中…' : '生成报告' }}
              </button>
            </div>
            <div v-if="reportHtml" class="md-body" v-html="reportHtml"></div>
            <div v-else class="text-xs text-muted">暂无报告，点击右上角生成。</div>
          </div>

          <!-- 矛盾列表 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <h2 class="text-sm font-bold text-gray-100 mb-3">矛盾清单</h2>
            <div v-if="filteredItems.length" class="space-y-3">
              <div v-for="(item, idx) in filteredItems" :key="idx"
                class="border border-border rounded-lg p-3 hover:border-accent/30 transition-colors"
                :class="item.severity === 'severe' ? 'bg-red-500/5' : (item.severity === 'obvious' ? 'bg-amber-500/5' : '')">
                <div class="flex items-center justify-between gap-2 flex-wrap mb-1.5">
                  <div class="flex items-center gap-2">
                    <span class="px-1.5 py-0.5 rounded text-[10px] border"
                      :class="severityClass(item.severity)">
                      {{ severityLabel(item.severity) }}
                    </span>
                    <span class="text-xs font-medium text-gray-100">{{ item.title }}</span>
                  </div>
                  <span class="text-[10px] text-muted">{{ typeLabel(item.type) }}</span>
                </div>
                <div class="text-xs text-muted mb-2">{{ item.summary }}</div>
                <div v-if="item.signal" class="text-xs text-accent/90 bg-accent/5 border border-accent/10 rounded px-2 py-1.5">
                  {{ item.signal }}
                </div>
              </div>
            </div>
            <div v-else class="text-xs text-muted">该日暂无矛盾记录。</div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import MarkdownIt from 'markdown-it'
import {
  getContradictions,
  getContradictionsSummary,
  getContradictionsReport,
  triggerContradictionsScan,
  triggerContradictionsReport,
} from '../api'

const mdRenderer = new MarkdownIt({ html: false, linkify: true, breaks: true })

const currentDate = ref('')
const loading = ref(false)
const error = ref('')
const items = ref([])
const summary = ref(null)
const reportHtml = ref('')
const scanning = ref(false)
const reportLoading = ref(false)

// 中文映射（与后端 app/contradictions/labels.py 同口径；新增扫描器两边同步补）
const severityLabels = { severe: '严重', obvious: '明显', minor: '轻微' }
const typeLabels = {
  index_vs_breadth: '指数与个股结构背离',
  sector_narrative_vs_flow: '板块叙事与资金流向背离',
  price_vs_volume: '指数量价背离',
  northbound_vs_index: '北向资金与指数背离',
  index_vs_mainflow: '指数与主力资金流背离',
  calendar_surprise: '宏观数据预期差',
  today_calendar_focus: '今日重点关注数据',
}

function severityLabel(s) {
  return severityLabels[s] || s || ''
}

function typeLabel(t) {
  return typeLabels[t] || t || ''
}

const severities = [
  { label: '严重', value: 'severe' },
  { label: '明显', value: 'obvious' },
  { label: '轻微', value: 'minor' },
]
const selectedSeverities = ref(['severe', 'obvious', 'minor'])

const filteredItems = computed(() => {
  return items.value.filter(i => selectedSeverities.value.includes(i.severity))
})

function severityClass(s) {
  if (s === 'severe') return 'bg-red-500/15 text-red-400 border-red-500/20'
  if (s === 'obvious') return 'bg-amber-500/15 text-amber-400 border-amber-500/20'
  return 'bg-blue-500/15 text-blue-400 border-blue-500/20'
}

async function loadSummary() {
  try {
    const { data } = await getContradictionsSummary(currentDate.value || undefined)
    summary.value = data
    if (!currentDate.value && data?.date) {
      currentDate.value = data.date
    }
  } catch (e) {
    console.error('loadSummary error', e)
  }
}

async function load(date) {
  if (date) currentDate.value = date
  loading.value = true
  error.value = ''
  try {
    await loadSummary()
    const { data } = await getContradictions({ date: currentDate.value })
    items.value = data?.data || []
    await loadReport()
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

async function loadReport() {
  try {
    const { data } = await getContradictionsReport(currentDate.value || undefined)
    if (data?.markdown) {
      reportHtml.value = mdRenderer.render(data.markdown)
    } else {
      reportHtml.value = ''
    }
  } catch (e) {
    reportHtml.value = ''
  }
}

async function triggerScan() {
  scanning.value = true
  try {
    await triggerContradictionsScan(currentDate.value || undefined)
    await load(currentDate.value)
  } catch (e) {
    error.value = '扫描失败：' + (e.response?.data?.detail || e.message)
  } finally {
    scanning.value = false
  }
}

async function generateReport() {
  reportLoading.value = true
  try {
    await triggerContradictionsReport(currentDate.value || undefined)
    await loadReport()
  } catch (e) {
    error.value = '生成报告失败：' + (e.response?.data?.detail || e.message)
  } finally {
    reportLoading.value = false
  }
}

watch(currentDate, () => {
  if (currentDate.value) load(currentDate.value)
})

onMounted(() => {
  load()
})
</script>

<style scoped>
.md-body {
  color: #d1d5db;
  font-size: 0.8rem;
  line-height: 1.75;
  word-break: break-word;
}
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3) {
  color: #e5e7eb;
  font-weight: 600;
  margin: 0.75rem 0 0.4rem;
}
.md-body :deep(h1) { font-size: 1.05rem; border-bottom: 1px solid #374151; padding-bottom: 0.4rem; }
.md-body :deep(h2) { font-size: 0.95rem; }
.md-body :deep(h3) { font-size: 0.88rem; }
.md-body :deep(p) { margin: 0.35rem 0; }
.md-body :deep(ul),
.md-body :deep(ol) { margin: 0.35rem 0; padding-left: 1.25rem; }
.md-body :deep(ul) { list-style: disc; }
.md-body :deep(ol) { list-style: decimal; }
.md-body :deep(li) { margin: 0.15rem 0; }
.md-body :deep(strong) { color: #f3f4f6; font-weight: 600; }
.md-body :deep(em) { color: #9ca3af; }
.md-body :deep(blockquote) {
  border-left: 3px solid #4b5563;
  padding-left: 0.75rem;
  margin: 0.4rem 0;
  color: #9ca3af;
}
.md-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
  font-size: 0.78rem;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #374151;
  padding: 0.35rem 0.55rem;
  text-align: left;
}
.md-body :deep(th) { background: rgba(255, 255, 255, 0.05); color: #e5e7eb; }
.md-body :deep(tr:nth-child(even)) { background: rgba(255, 255, 255, 0.02); }
.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
