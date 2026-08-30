<template>
  <div class="fade-in space-y-4">
    <!-- 头部：标题 + 类型切换 + 日期 + 手动记录 -->
    <div class="bg-card border border-border rounded-lg p-4 space-y-3">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">板块分化</h1>
          <p class="text-xs text-muted mt-0.5">
            每个交易日收盘后记录全部板块快照，用涨跌幅离散度衡量「结构性行情」强度。
            <span v-if="stats.days" class="text-gray-400">已积累 {{ stats.days }} 天 / {{ stats.total_rows }} 行</span>
            <span v-else class="text-gray-400">数据积累中</span>
          </p>
        </div>
        <button @click="doTake" :disabled="taking"
          class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
          {{ taking ? '记录中…' : '↻ 手动记录' }}
        </button>
      </div>

      <div class="flex items-center gap-5 text-xs flex-wrap">
        <div class="flex items-center gap-1.5">
          <span class="text-muted">类型</span>
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="k in kindOptions" :key="k.value"
              class="px-2.5 py-1 rounded transition-colors"
              :class="kind === k.value ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
              @click="kind = k.value">{{ k.label }}</button>
          </div>
        </div>
        <div class="flex items-center gap-1.5">
          <span class="text-muted">日期</span>
          <select v-model="date"
            class="bg-bg border border-border rounded px-2 py-1 text-xs text-gray-200 focus:outline-none focus:border-accent/50">
            <option v-for="d in stats.latest_dates" :key="d" :value="d">{{ d }}</option>
          </select>
        </div>
        <span v-if="loading" class="text-accent">⏳ 加载中…</span>
        <span v-if="error" class="text-fall">⚠️ {{ error }}</span>
      </div>
    </div>

    <!-- 空状态：数据还没攒够（东财不可用时会跳过记录） -->
    <div v-if="!loading && !stats.days" class="bg-card border border-border rounded-lg p-8 text-center">
      <p class="text-sm text-gray-300">板块历史序列尚未开始积累</p>
      <p class="text-xs text-muted mt-2 leading-relaxed">
        调度器会在每个交易日 15:10 自动记录全部板块快照。<br/>
        需要东方财富接口可用 —— 若其不可用（降级新浪）会主动跳过，
        避免两套板块代码体系混进同一张表导致序列断裂。
      </p>
      <p class="text-xs text-muted mt-2">
        序列需要积累 20-60 个交易日才能支撑板块动量类因子，越早开始越好。
      </p>
    </div>

    <template v-if="stats.days && !loading">
      <!-- 分化度指标卡 -->
      <div v-if="disp.sector_count" class="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="text-xs text-muted">分化强度（标准差）</div>
          <div class="text-xl font-bold mt-1" :class="dispClass">{{ disp.std_dev }}</div>
          <div class="text-[10px] mt-0.5" :class="dispClass">{{ dispLevel }}</div>
        </div>
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="text-xs text-muted">最强最弱差距</div>
          <div class="text-xl font-bold mt-1 text-gray-100">{{ disp.max_spread }}%</div>
          <div class="text-[10px] mt-0.5 text-muted">头尾板块涨跌幅差</div>
        </div>
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="text-xs text-muted">上涨板块占比</div>
          <div class="text-xl font-bold mt-1" :class="disp.up_ratio >= 0.5 ? 'text-rise' : 'text-fall'">
            {{ (disp.up_ratio * 100).toFixed(0) }}%
          </div>
          <div class="text-[10px] mt-0.5 text-muted">{{ disp.up_sectors }} 涨 / {{ disp.down_sectors }} 跌</div>
        </div>
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="text-xs text-muted">板块平均涨跌</div>
          <div class="text-xl font-bold mt-1" :class="disp.mean_change >= 0 ? 'text-rise' : 'text-fall'">
            {{ disp.mean_change > 0 ? '+' : '' }}{{ disp.mean_change }}%
          </div>
          <div class="text-[10px] mt-0.5 text-muted">共 {{ disp.sector_count }} 个板块</div>
        </div>
      </div>

      <!-- 分化度解读 -->
      <div v-if="disp.sector_count" class="bg-card border border-border rounded-lg p-3 text-xs leading-relaxed">
        <span class="text-muted">💡 解读：</span>
        <span class="text-gray-300">{{ dispHint }}</span>
      </div>

      <!-- 最强 / 最弱板块 -->
      <div v-if="disp.sector_count" class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div class="bg-card border border-border rounded-lg overflow-hidden">
          <div class="px-3 py-2 border-b border-border text-xs text-muted">🔥 最强板块</div>
          <div v-for="s in disp.top" :key="s.name"
            class="px-3 py-1.5 border-b border-border/50 last:border-b-0 flex justify-between cursor-pointer hover:bg-white/[0.02]"
            @click="pickByName(s.name)">
            <span class="text-xs text-gray-200">{{ s.name }}</span>
            <span class="text-xs font-mono text-rise">+{{ s.change_pct }}%</span>
          </div>
        </div>
        <div class="bg-card border border-border rounded-lg overflow-hidden">
          <div class="px-3 py-2 border-b border-border text-xs text-muted">❄️ 最弱板块</div>
          <div v-for="s in disp.bottom" :key="s.name"
            class="px-3 py-1.5 border-b border-border/50 last:border-b-0 flex justify-between cursor-pointer hover:bg-white/[0.02]"
            @click="pickByName(s.name)">
            <span class="text-xs text-gray-200">{{ s.name }}</span>
            <span class="text-xs font-mono text-fall">{{ s.change_pct }}%</span>
          </div>
        </div>
      </div>

      <!-- 板块列表（点击看历史） -->
      <div class="bg-card border border-border rounded-lg overflow-hidden">
        <div class="px-3 py-2 border-b border-border flex items-center justify-between">
          <span class="text-xs text-muted">当日板块快照（按涨跌幅排序，点击查看历史序列）</span>
          <span class="text-xs text-muted">{{ rows.length }} 个</span>
        </div>
        <div class="grid grid-cols-[1.4fr_0.8fr_1fr_1fr_1.2fr] border-b border-border/50 text-[11px] text-muted">
          <div class="px-3 py-1.5">板块</div>
          <div class="px-3 py-1.5 text-right">涨跌幅</div>
          <div class="px-3 py-1.5 text-right">涨跌家数</div>
          <div class="px-3 py-1.5 text-right">主力净流入</div>
          <div class="px-3 py-1.5">领涨股</div>
        </div>
        <div v-for="r in rows" :key="r.code"
          class="grid grid-cols-[1.4fr_0.8fr_1fr_1fr_1.2fr] border-b border-border/50 last:border-b-0 cursor-pointer hover:bg-white/[0.02] text-xs"
          :class="selected?.code === r.code ? 'bg-accent/10' : ''"
          @click="pick(r)">
          <div class="px-3 py-1.5 text-gray-200 truncate">{{ r.name }}</div>
          <div class="px-3 py-1.5 text-right font-mono"
            :class="r.change_pct >= 0 ? 'text-rise' : 'text-fall'">
            {{ r.change_pct >= 0 ? '+' : '' }}{{ r.change_pct }}%
          </div>
          <div class="px-3 py-1.5 text-right font-mono text-muted">
            <span class="text-rise">{{ r.up_count }}</span>/<span class="text-fall">{{ r.down_count }}</span>
          </div>
          <div class="px-3 py-1.5 text-right font-mono"
            :class="r.net_inflow >= 0 ? 'text-rise' : 'text-fall'">
            {{ fmtYi(r.net_inflow) }}
          </div>
          <div class="px-3 py-1.5 text-muted truncate">{{ r.leader || '—' }}</div>
        </div>
      </div>

      <!-- 单板块历史序列图 -->
      <div v-if="selected" class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-semibold text-gray-100">
            {{ selected.name }} · 历史序列
            <span class="text-xs text-muted font-normal ml-1">（{{ history.length }} 天）</span>
          </h3>
          <span class="text-xs text-muted">涨跌幅折线 + 主力净流入柱状</span>
        </div>
        <div ref="chartRef" class="h-64"></div>
        <p v-if="history.length < 20" class="text-[11px] text-muted mt-2">
          ⚠️ 仅 {{ history.length }} 天数据，板块动量类因子通常需 20 个交易日以上才可靠。
        </p>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import {
  getSectorDispersion, getSectorSnapshot, getSectorHistory,
  getSectorSnapshotStats, takeSectorSnapshot,
} from '../api'

const stats = ref({ latest_dates: [], days: 0, total_rows: 0 })
const disp = ref({})
const rows = ref([])
const history = ref([])
const selected = ref(null)
const kind = ref('industry')
const date = ref('')
const loading = ref(false)
const taking = ref(false)
const error = ref('')
const chartRef = ref(null)
let chart = null

const kindOptions = [
  { value: 'industry', label: '行业' },
  { value: 'concept', label: '概念' },
]

// 分化强度分级（A股板块日涨跌幅标准差的经验区间）
const dispLevel = computed(() => {
  const v = disp.value.std_dev
  if (v == null) return '—'
  if (v < 1.0) return '普涨普跌'
  if (v < 2.0) return '正常分化'
  return '严重分化'
})
const dispClass = computed(() => {
  const v = disp.value.std_dev
  if (v == null) return 'text-muted'
  if (v < 1.0) return 'text-accent'
  if (v < 2.0) return 'text-gray-100'
  return 'text-rise'
})
const dispHint = computed(() => {
  const v = disp.value.std_dev ?? 0
  const up = disp.value.up_ratio ?? 0
  if (v < 1.0) {
    return up >= 0.6
      ? '板块涨跌高度一致且普涨 —— 系统性行情，仓位比选板块重要。'
      : '板块涨跌高度一致 —— 系统性行情，此时个股alpha有限，宜控制仓位而非精选板块。'
  }
  if (v < 2.0) {
    return '板块之间存在正常差异 —— 结构性机会与普涨因素并存，可在强势板块中择优。'
  }
  return '板块涨跌差异极大 —— 典型结构性行情，选对板块远比仓位重要，选错方向损失显著。'
})

function fmtYi(v) {
  if (!v) return '—'
  const y = v / 1e8
  return (y >= 0 ? '+' : '') + y.toFixed(2) + '亿'
}

async function loadMeta() {
  try {
    const { data } = await getSectorSnapshotStats()
    stats.value = data || { latest_dates: [], days: 0 }
    if (stats.value.latest_dates?.length && !date.value) {
      date.value = stats.value.latest_dates[0]
    }
  } catch (e) {
    error.value = '概况加载失败：' + (e.response?.data?.detail || e.message)
  }
}

async function loadData() {
  if (!date.value) return
  loading.value = true
  error.value = ''
  try {
    const [d, snap] = await Promise.all([
      getSectorDispersion({ date: date.value, kind: kind.value }),
      getSectorSnapshot(date.value, { kind: kind.value }),
    ])
    disp.value = d.data || {}
    rows.value = snap.data?.data || []
    selected.value = null
    history.value = []
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
    disp.value = {}
    rows.value = []
  } finally {
    loading.value = false
  }
}

async function pick(sector) {
  selected.value = sector
  try {
    const { data } = await getSectorHistory(sector.code, 60)
    history.value = data?.data || []
  } catch {
    history.value = []
  }
  await nextTick()
  drawChart()
}

function pickByName(name) {
  const hit = rows.value.find(r => r.name === name)
  if (hit) pick(hit)
}

async function doTake() {
  taking.value = true
  try {
    await takeSectorSnapshot()
    await loadMeta()
    if (!date.value && stats.value.latest_dates?.length) {
      date.value = stats.value.latest_dates[0]
    }
    await loadData()
  } catch (e) {
    error.value = '记录失败：' + (e.response?.data?.detail || e.message)
  } finally {
    taking.value = false
  }
}

function drawChart() {
  if (!chartRef.value) return
  if (!chart) chart = echarts.init(chartRef.value, 'dark')
  const dates = history.value.map(h => (h.date || '').slice(5))
  const chg = history.value.map(h => h.change_pct || 0)
  const flow = history.value.map(h => ({
    value: (h.net_inflow || 0) / 1e8,
    itemStyle: { color: (h.net_inflow || 0) >= 0 ? '#ef4444' : '#22c55e' },
  }))
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#8b949e' },
    tooltip: { trigger: 'axis' },
    legend: { data: ['涨跌幅', '主力净流入(亿)'], textStyle: { color: '#8b949e' }, top: 0 },
    grid: { left: '8%', right: '6%', top: '18%', bottom: '10%' },
    xAxis: { type: 'category', data: dates, axisLabel: { fontSize: 10 } },
    yAxis: [
      { type: 'value', name: '%', nameTextStyle: { fontSize: 10 }, scale: true,
        splitLine: { lineStyle: { color: '#21262d' } } },
      { type: 'value', name: '亿', nameTextStyle: { fontSize: 10 }, scale: true,
        splitLine: { show: false } },
    ],
    series: [
      { name: '涨跌幅', type: 'line', data: chg, smooth: true, symbol: 'circle',
        symbolSize: 4, lineStyle: { color: '#58a6ff', width: 2 },
        itemStyle: { color: '#58a6ff' } },
      { name: '主力净流入(亿)', type: 'bar', yAxisIndex: 1, data: flow, barMaxWidth: 12 },
    ],
  }, true)
}

function onResize() { chart?.resize() }

watch([kind, date], () => { if (stats.value.days) loadData() })

onMounted(async () => {
  await loadMeta()
  if (stats.value.days) await loadData()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>
