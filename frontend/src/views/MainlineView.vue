<template>
  <div class="fade-in space-y-4">
    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg p-4 space-y-3">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">行业主线</h1>
          <p class="text-xs text-muted mt-0.5">
            每个交易日收盘后分析评分 Top50 的行业扎堆：主线榜 + 风格切换信号。
            <span class="text-gray-400">窗口 {{ summary.dates?.[0] || '-' }} ~ {{ summary.dates?.[1] || '-' }}（{{ summary.days || 0 }} 日）</span>
          </p>
        </div>
        <div class="flex items-center gap-2">
          <button @click="doBuild" :disabled="building"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ building ? '分析中…' : '↻ 分析今日' }}
          </button>
          <button @click="doPush" :disabled="pushing"
            class="px-3 py-1.5 rounded text-xs bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors disabled:opacity-50">
            {{ pushing ? '推送中…' : '📤 推送企微日报' }}
          </button>
        </div>
      </div>
      <div class="flex items-center gap-5 text-xs flex-wrap">
        <div class="flex items-center gap-1.5">
          <span class="text-muted">窗口</span>
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="d in [6, 12, 20]" :key="d"
              class="px-2.5 py-1 rounded transition-colors"
              :class="days === d ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
              @click="days = d">{{ d }}日</button>
          </div>
        </div>
        <span v-if="loading" class="text-accent">⏳ 加载中…</span>
        <span v-if="error" class="text-fall">⚠️ {{ error }}</span>
      </div>
    </div>

    <!-- 风格切换信号 -->
    <div v-if="summary.switches?.length" class="bg-card border border-border rounded-lg p-4">
      <h2 class="text-sm font-bold text-gray-200 mb-2">🔄 风格切换信号</h2>
      <p class="text-xs text-muted mb-3">后 1/4 窗口 vs 前 3/4 窗口的行业占比突变（资金进出）。</p>
      <div class="space-y-1.5">
        <div v-for="w in summary.switches" :key="w.industry"
          class="flex items-center gap-2 text-xs">
          <span class="px-2 py-0.5 rounded font-medium"
            :class="w.action === 'in' ? 'bg-rise/15 text-rise' : 'bg-fall/15 text-fall'">
            {{ w.action === 'in' ? '流入' : '退出' }}
          </span>
          <span class="font-medium text-gray-200 w-20">{{ w.industry }}</span>
          <span class="text-muted">{{ w.from }}只 → <span class="font-bold"
            :class="w.action === 'in' ? 'text-rise' : 'text-fall'">{{ w.to }}只</span></span>
        </div>
      </div>
    </div>

    <!-- 主线榜 -->
    <div v-if="!loading && !error" class="space-y-3">
      <div class="flex items-center justify-between">
        <h2 class="text-sm font-bold text-gray-200">📈 主线榜（Top50 行业扎堆）</h2>
        <span v-if="summary.unknown_latest > 0" class="text-xs text-fall">
          ⚠️ 最新一天 {{ summary.unknown_latest }} 只 Top50 股票无行业映射
        </span>
      </div>
      <div v-if="!summary.mainlines?.length" class="bg-card border border-border rounded-lg p-8 text-center">
        <p class="text-sm text-gray-300">窗口内暂无达标主线</p>
        <p class="text-xs text-muted mt-2">行业出现率需 ≥ 窗口一半且日均 ≥ 1.5 只。</p>
      </div>
      <div v-for="(m, i) in summary.mainlines" :key="m.industry"
        class="bg-card border border-border rounded-lg p-4 hover:border-accent/30 transition-colors">
        <div class="flex items-center justify-between flex-wrap gap-2">
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted w-5">{{ i + 1 }}</span>
            <span class="font-bold text-gray-100">{{ m.industry }}</span>
            <span :class="trendCls(m.trend)">{{ trendArrow(m.trend) }}</span>
            <span class="px-2 py-0.5 rounded text-[10px] font-medium"
              :class="m.trend === 'up' ? 'bg-rise/15 text-rise' : m.trend === 'down' ? 'bg-fall/15 text-fall' : 'bg-bg text-muted'">
              {{ m.trend === 'up' ? '增强' : m.trend === 'down' ? '减弱' : '平稳' }}
            </span>
          </div>
          <div class="flex items-center gap-4 text-xs text-muted">
            <span>出现 <b class="text-gray-200">{{ m.appear }}</b></span>
            <span>近期日均 <b class="text-gray-200">{{ m.recent }}</b></span>
            <span>早期 <b class="text-gray-200">{{ m.early }}</b></span>
            <span>均排名 <b class="text-gray-200">{{ m.avg_rank }}</b></span>
            <span>今日 <b class="text-gray-200">{{ m.latest_count }}</b> 只</span>
          </div>
        </div>
        <div v-if="m.latest_stocks?.length" class="flex items-center gap-1.5 mt-3 flex-wrap">
          <span class="text-[10px] text-muted mr-1">今日成分：</span>
          <span v-for="s in m.latest_stocks" :key="s.code"
            class="px-2 py-0.5 rounded text-[10px] bg-bg border border-border text-gray-300">
            {{ s.name }}<span class="text-muted ml-1">#{{ s.rank }}</span>
          </span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { getMainlineSummary, buildMainlineDate, pushMainlineReport } from '../api'

const days = ref(12)
const summary = ref({})
const loading = ref(false)
const error = ref('')
const building = ref(false)
const pushing = ref(false)

const trendArrow = (t) => ({ up: '▲', down: '▼', flat: '→' }[t] || '→')
const trendCls = (t) => t === 'up' ? 'text-rise' : t === 'down' ? 'text-fall' : 'text-muted'

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getMainlineSummary(days.value)
    if (data?.ok === false) {
      error.value = data.error || '无数据'
      summary.value = {}
    } else {
      summary.value = data
    }
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

async function doBuild() {
  building.value = true
  try {
    const { data } = await buildMainlineDate()
    if (data?.ok) await load()
    else error.value = data?.error || '分析失败'
  } catch (e) {
    error.value = '分析失败：' + (e.response?.data?.detail || e.message)
  } finally {
    building.value = false
  }
}

async function doPush() {
  pushing.value = true
  try {
    const { data } = await pushMainlineReport(days.value)
    if (data?.ok === false) error.value = data.error || '推送失败'
  } catch (e) {
    error.value = '推送失败：' + (e.response?.data?.detail || e.message)
  } finally {
    pushing.value = false
  }
}

watch(days, load)
onMounted(load)
</script>
