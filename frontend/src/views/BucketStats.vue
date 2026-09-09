<template>
  <div class="fade-in space-y-4">
    <!-- 头部：标题 + 参数 -->
    <div class="bg-card border border-border rounded-lg p-4 space-y-3">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">评分分桶胜率</h1>
          <p class="text-xs text-muted mt-0.5">
            验证“评分越高，未来收益越好吗”——按每日 Top50 快照评分分桶，统计持有 N 个交易日后的胜率与平均收益。
            <span class="text-gray-300">窗口 {{ stats.window?.[0] || '—' }} ~ {{ stats.window?.[1] || '—' }}</span>
          </p>
        </div>
        <div class="flex items-center gap-2">
          <button v-for="d in dayOptions" :key="d"
            class="px-3 py-1.5 rounded text-xs border transition-colors"
            :class="days === d ? 'bg-accent/15 text-accent border-accent/40' : 'border-border text-muted hover:text-gray-200'"
            @click="switchDays(d)">{{ d }}天</button>
        </div>
      </div>
      <div class="flex items-center gap-4 text-xs text-muted">
        <!-- 口径切换 -->
        <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
          <button class="px-3 py-1 rounded text-xs transition-colors"
            :class="mode === 'all' ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
            @click="mode = 'all'">全部记录</button>
          <button class="px-3 py-1 rounded text-xs transition-colors"
            :class="mode === 'buy' ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
            @click="mode = 'buy'">仅买入信号</button>
        </div>
        <span>快照 {{ stats.total_records }} 条</span>
        <span>价格覆盖 {{ fmtPct(stats.price_coverage) }}</span>
        <span v-if="stats.cached" class="text-accent">（缓存 {{ cacheAge }}s）</span>
        <span v-if="loading" class="text-accent">⏳ 计算中…</span>
        <span v-if="error" class="text-fall">⚠️ {{ error }}</span>
      </div>
    </div>

    <!-- 结论 -->
    <div v-if="stats.conclusion" class="bg-card border border-border rounded-lg p-3 text-xs leading-relaxed">
      <span class="text-muted">💡 结论（持有 {{ stats.horizons?.[stats.horizons.length - 1] }} 日）：</span>
      <span class="text-gray-300">{{ stats.conclusion }}</span>
    </div>

    <!-- 分桶胜率表 -->
    <div class="bg-card border border-border rounded-lg overflow-hidden">
      <div class="grid grid-cols-[1.2fr_repeat(3,1fr)] border-b border-border">
        <div class="px-3 py-2 text-xs text-muted">评分桶</div>
        <div v-for="h in stats.horizons || []" :key="h" class="px-3 py-2 text-xs text-muted text-center">
          持有 {{ h }} 日
        </div>
      </div>

      <!-- 各评分桶 -->
      <div v-for="row in rows" :key="row.bucket"
        class="grid grid-cols-[1.2fr_repeat(3,1fr)] border-b border-border/50 last:border-b-0"
        :class="row.bucket === '全样本' ? 'bg-white/[0.02]' : ''">
        <div class="px-3 py-2 flex items-center gap-2">
          <span class="text-sm font-medium" :class="row.bucket === '全样本' ? 'text-accent' : 'text-gray-200'">{{ row.bucket }}</span>
          <span class="text-[10px] text-muted">{{ row.countLabel }}</span>
        </div>
        <div v-for="h in stats.horizons || []" :key="h" class="px-3 py-2">
          <div v-if="cell(row, h).n >= 20" class="flex flex-col gap-1">
            <div class="flex items-center gap-1.5">
              <span class="text-sm font-bold font-mono" :class="winColor(cell(row, h).win_rate)">{{ cell(row, h).win_rate }}%</span>
              <div class="flex-1 h-1 bg-bg rounded overflow-hidden">
                <div class="h-full rounded" :class="winBar(cell(row, h).win_rate)"
                  :style="{ width: Math.min(cell(row, h).win_rate, 100) + '%' }"></div>
              </div>
            </div>
            <div class="text-[11px] text-muted font-mono">
              均 <span :class="retColor(cell(row, h).avg_ret)">{{ fmtRet(cell(row, h).avg_ret) }}</span>
              <span v-if="cell(row, h).median_ret != null"> / 中 {{ fmtRet(cell(row, h).median_ret) }}</span>
              <span class="text-muted/70"> · n={{ cell(row, h).n }}</span>
            </div>
          </div>
          <!-- ★ n<20 灰显：小样本胜率方差极大（如 <60 桶 n=6 显示 100%），不能当结论看 -->
          <div v-else-if="cell(row, h).n > 0" class="text-xs text-muted/60 py-1">
            n={{ cell(row, h).n }} · 样本不足
          </div>
          <div v-else class="text-xs text-muted/50 py-1">—</div>
        </div>
      </div>
    </div>

    <!-- 主力行为分桶（吸筹/出货预测力验证，2026-09-09 起积累数据） -->
    <div v-if="stats.mainforce" class="bg-card border border-border rounded-lg overflow-hidden">
      <div class="px-3 py-2 text-xs text-muted border-b border-border bg-white/[0.02]">
        主力行为标签胜率（验证吸筹区/出货嫌疑的预测力 · 09-09 起积累）
      </div>
      <div class="grid grid-cols-[1.2fr_repeat(3,1fr)] border-b border-border">
        <div class="px-3 py-2 text-xs text-muted">标签</div>
        <div v-for="h in stats.horizons || []" :key="'mf'+h" class="px-3 py-2 text-xs text-muted text-center">
          持有 {{ h }} 日
        </div>
      </div>
      <div v-for="(label, key) in mfLabels" :key="key"
        class="grid grid-cols-[1.2fr_repeat(3,1fr)] border-b border-border/50 last:border-b-0">
        <div class="px-3 py-2 flex items-center gap-2">
          <span class="text-sm font-medium" :class="key === 'distribution' ? 'text-fall' : key === 'accum' ? 'text-rise' : 'text-gray-300'">{{ label }}</span>
          <span class="text-[10px] text-muted" v-if="stats.mainforce[key]">{{ mfCount(key) }} 条</span>
        </div>
        <div v-for="h in stats.horizons || []" :key="key+h" class="px-3 py-2">
          <div v-if="mfCell(key, h).n >= 20" class="flex flex-col gap-1">
            <span class="text-sm font-bold font-mono" :class="winColor(mfCell(key, h).win_rate)">{{ mfCell(key, h).win_rate }}%</span>
            <span class="text-[11px] text-muted font-mono">
              均 <span :class="retColor(mfCell(key, h).avg_ret)">{{ fmtRet(mfCell(key, h).avg_ret) }}</span>
              <span class="text-muted/70"> · n={{ mfCell(key, h).n }}</span>
            </span>
          </div>
          <div v-else-if="mfCell(key, h).n > 0" class="text-xs text-muted/60 py-1">n={{ mfCell(key, h).n }} · 样本不足</div>
          <div v-else class="text-xs text-muted/50 py-1">—</div>
        </div>
      </div>
    </div>

    <!-- 说明 -->
    <div class="bg-card border border-border rounded-lg p-3 text-[11px] text-muted leading-relaxed space-y-1">
      <div>📌 口径说明</div>
      <div>· 数据源：每日盘后自动落库的评分 Top50 快照（<code class="text-gray-300">ranking_history</code>）+ 日线价格（回填表 + K线缓存）。</div>
      <div>· “胜率”= 该分桶内收益为正的样本占比；“平均收益”= 持有 N 个交易日后的收益均值（前复权）。</div>
      <div>· “仅买入信号”只统计信号为“强烈买入/买入”的快照，是实际会执行的信号，更有参考意义。</div>
      <div>· 样本太少时胜率波动很大，请结合 n 一起看；数据每天自动积累，跑几周后才有统计意义。</div>
      <div>· n &lt; 20 的格子已灰显为「样本不足」——小样本下 66%/100% 这类数字纯属噪声，不要据此决策。</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getBucketStats } from '../api'

const days = ref(120)
const mode = ref('all')
const stats = ref({})
const loading = ref(false)
const error = ref('')
const loadedAt = ref(0)

const dayOptions = [60, 120, 365]

async function load() {
  loading.value = true
  error.value = ''
  try {
    const { data } = await getBucketStats(days.value)
    stats.value = data || {}
    loadedAt.value = Date.now()
  } catch (e) {
    error.value = e?.response?.data?.detail || e?.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function switchDays(d) {
  days.value = d
  load()
}

const cacheAge = computed(() => loadedAt.value ? Math.max(0, Math.round((Date.now() - loadedAt.value) / 1000)) : 0)

// 表行：各评分桶 + 全样本 baseline
const rows = computed(() => {
  const b = (stats.value.buckets || []).map(x => ({
    bucket: x.bucket,
    countLabel: `${countAll(x)} 条`,
    group: x,
  }))
  b.push({ bucket: '全样本', countLabel: `${countAll(stats.value.baseline || {})} 条`, group: stats.value.baseline || {}, baseline: true })
  return b
})

function countAll(group) {
  const g = mode.value === 'buy' ? (group.buy || {}) : (group.all || {})
  return Object.values(g).reduce((s, v) => s + (v?.n || 0), 0)
}

function cell(row, h) {
  const g = row.baseline
    ? (row.group[mode.value] || {})
    : (row.group[mode.value] || {})
  return g[String(h)] || {}
}

// ===== 主力行为分桶（吸筹/出货预测力验证）=====
const mfLabels = { distribution: '出货嫌疑', accum: '吸筹区', none: '无标签' }
function mfCell(key, h) {
  return stats.value.mainforce?.[key]?.[String(h)] || {}
}
function mfCount(key) {
  const hs = stats.value.horizons || []
  const last = hs[hs.length - 1]
  return mfCell(key, last).n || 0
}

function fmtPct(v) {
  if (v == null) return '—'
  return (v * 100).toFixed(1) + '%'
}

function fmtRet(v) {
  if (v == null) return '—'
  return (v > 0 ? '+' : '') + v + '%'
}

// 中国习惯：红涨绿跌
function winColor(v) {
  if (v == null) return 'text-muted'
  if (v >= 55) return 'text-rise'
  if (v <= 45) return 'text-fall'
  return 'text-gray-300'
}
function retColor(v) {
  if (v == null) return 'text-muted'
  if (v > 0) return 'text-rise'
  if (v < 0) return 'text-fall'
  return 'text-gray-300'
}
function winBar(v) {
  if (v == null) return 'bg-border'
  if (v >= 55) return 'bg-[var(--rise)]'
  if (v <= 45) return 'bg-[var(--fall)]'
  return 'bg-gray-500'
}

onMounted(load)
</script>
