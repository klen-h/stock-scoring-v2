<template>
  <div class="fade-in">
    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg p-4 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">A股大盘日报</h1>
          <p class="text-xs text-muted mt-0.5">
            每个交易日 16:20 自动生成：外围环境 + 指数/个股结构 + 北向资金 + 系统状态 + 持仓 + AI 解读。
          </p>
        </div>
        <button @click="load(currentDate)" :disabled="loading"
          class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
          {{ loading ? '加载中…' : '↻ 刷新' }}
        </button>
      </div>
    </div>

    <div class="flex gap-4 items-start">
      <!-- 左侧日期列表 -->
      <div class="w-48 shrink-0 space-y-2">
        <div v-if="listLoading" class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
          加载中…
        </div>
        <div v-else-if="listError" class="bg-card border border-border rounded-lg p-4 text-xs text-fall">
          {{ listError }}
        </div>
        <div v-else-if="!reportList.length" class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
          暂无日报（交易日 16:20 自动生成）
        </div>
        <button v-for="r in reportList" :key="r.date" @click="select(r.date)"
          class="w-full text-left px-3 py-2 rounded border transition-colors"
          :class="currentDate === r.date
            ? 'bg-accent/15 border-accent/30 text-accent'
            : 'bg-card border-border text-muted hover:text-gray-200 hover:border-accent/30'">
          <div class="text-xs font-medium">{{ r.date }}</div>
          <div class="text-[10px] text-muted mt-0.5">{{ fmtTime(r.created_at) }} · {{ r.len }} 字</div>
        </button>
      </div>

      <!-- 右侧正文 -->
      <div class="flex-1 min-w-0">
        <div v-if="loading" class="bg-card border border-border rounded-lg p-8 text-center">
          <div class="loading-spinner mx-auto mb-3"></div>
          <p class="text-xs text-muted">加载日报…</p>
        </div>
        <div v-else-if="error" class="bg-card border border-border rounded-lg p-8 text-center text-sm text-fall">
          {{ error }}
        </div>
        <div v-else-if="html" class="bg-card border border-border rounded-lg p-6">
          <div class="md-body" v-html="html"></div>
        </div>
        <div v-else class="bg-card border border-border rounded-lg p-8 text-center text-sm text-muted">
          选择左侧日期查看日报
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import MarkdownIt from 'markdown-it'
import { getDailyReportList, getDailyReport } from '../api'

const reportList = ref([])
const listLoading = ref(false)
const listError = ref('')
const currentDate = ref('')
const html = ref('')
const loading = ref(false)
const error = ref('')

const mdRenderer = new MarkdownIt({ html: false, linkify: true, breaks: true })

function fmtTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '' }
}

async function loadList() {
  listLoading.value = true
  listError.value = ''
  try {
    const { data } = await getDailyReportList(30)
    reportList.value = data?.data || []
    if (reportList.value.length && !currentDate.value) {
      currentDate.value = reportList.value[0].date
      await load(currentDate.value)
    }
  } catch (e) {
    listError.value = '加载列表失败：' + (e.response?.data?.detail || e.message)
  } finally {
    listLoading.value = false
  }
}

async function load(date) {
  loading.value = true
  error.value = ''
  html.value = ''
  try {
    const { data } = await getDailyReport(date)
    if (data?.markdown) {
      html.value = mdRenderer.render(data.markdown)
    } else {
      error.value = '该日期暂无日报'
    }
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

function select(date) {
  currentDate.value = date
  load(date)
}

onMounted(loadList)
</script>

<style scoped>
/* 日报 markdown 渲染样式（含表格，markdown-it 输出） */
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
.md-body :deep(code) {
  background: rgba(255, 255, 255, 0.08);
  padding: 0.1rem 0.3rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
}
.md-body :deep(hr) { border-color: #374151; margin: 0.75rem 0; }
.md-body :deep(a) { color: #60a5fa; }
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
</style>
