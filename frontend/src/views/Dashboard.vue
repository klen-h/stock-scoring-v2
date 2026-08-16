<template>
  <div class="fade-in space-y-4">
    <!-- 今日宏观方向（规则引擎，独立信号） -->
    <div v-if="macro.direction" class="bg-card border border-border rounded-lg p-4 flex items-center gap-4 flex-wrap">
      <div class="flex items-center gap-3">
        <div class="text-3xl font-bold font-mono" :class="dirColor(macro.direction.level)">{{ macro.direction.score }}</div>
        <div>
          <div class="text-sm font-semibold" :class="dirColor(macro.direction.level)">今日宏观方向 · {{ macro.direction.level }}</div>
          <div class="text-xs text-muted">-100~+100，规则引擎 {{ macro.rules_version }}</div>
        </div>
      </div>
      <div class="flex-1 min-w-[200px]">
        <div class="text-sm text-gray-300 mb-1.5">{{ macro.direction.advisory }}</div>
        <div class="flex flex-wrap gap-1" v-if="macro.tags_bull.length || macro.tags_bear.length">
          <span v-for="t in macro.tags_bull" :key="'b'+t"
            class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">{{ t }}</span>
          <span v-for="t in macro.tags_bear" :key="'s'+t"
            class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20">{{ t }}</span>
        </div>
      </div>
      <div class="text-xs text-muted text-right leading-relaxed">
        <div v-for="(label, key) in groupLabels" :key="key">
          {{ label }} <span :class="macro.direction.group_scores[key] > 0 ? 'text-rise' : macro.direction.group_scores[key] < 0 ? 'text-fall' : ''">{{ fmtScore(macro.direction.group_scores[key]) }}</span>
        </div>
      </div>
    </div>

    <!-- 快讯诊断头条（事件面：LLM 油金相关性诊断） -->
    <div v-if="flashDiag" class="bg-card border border-border rounded-lg p-3 flex items-center gap-3 flex-wrap text-xs">
      <span class="text-muted flex-shrink-0">事件诊断</span>
      <span class="font-bold text-sm" :class="flashDiag.correlation_diagnosis?.correlation_state === 'D状态' ? 'text-amber-400' : 'text-gray-200'">
        {{ flashDiag.correlation_diagnosis?.correlation_state || '—' }}
        <span class="text-muted font-normal text-[11px]">{{ flashDiag.correlation_diagnosis?.d_state_type || '' }}</span>
      </span>
      <span class="text-gray-300 flex-1 min-w-[180px] truncate" :title="flashDiag.dominant_narrative?.narrative">
        {{ flashDiag.dominant_narrative?.narrative || flashDiag.market_mood || '' }}
      </span>
      <span class="text-muted">仓位 <span class="text-accent font-bold">{{ flashDiag.daily_strategy?.overall_position || '—' }}</span></span>
      <router-link to="/monitor" class="text-accent hover:underline flex-shrink-0">详情 →</router-link>
    </div>

    <!-- 市场环境温度（独立信号，不进个股评分） -->
    <div v-if="temp.temperature != null" class="bg-card border border-border rounded-lg p-4 flex items-center gap-4 flex-wrap">
      <div class="flex items-center gap-3">
        <div class="text-3xl font-bold font-mono" :class="levelColor(temp.level)">{{ temp.temperature }}</div>
        <div>
          <div class="text-sm font-semibold" :class="levelColor(temp.level)">市场环境 · {{ temp.level }}</div>
          <div class="text-xs text-muted">0~100，越高越亢奋</div>
        </div>
      </div>
      <div class="flex-1 min-w-[220px] text-sm text-gray-300">{{ temp.advisory }}</div>
      <div class="text-xs text-muted text-right leading-relaxed">
        <div>涨跌比 <span class="text-gray-200">{{ temp.breadth.ratio }}</span>（{{ temp.breadth.up }}涨 / {{ temp.breadth.down }}跌）</div>
        <div>建议买入线 <span class="text-accent font-bold">{{ temp.buy_threshold }}</span> 分</div>
      </div>
    </div>

    <!-- 大盘指数卡片 -->
    <div class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      <div v-for="idx in overview.indices" :key="idx.code"
        class="bg-card border border-border rounded-lg p-3 hover:border-accent/40 transition-colors cursor-default">
        <div class="text-xs text-muted mb-1">{{ idx.name }}</div>
        <div class="text-lg font-bold" :class="idx.change_pct >= 0 ? 'text-rise' : 'text-fall'">
          {{ idx.price }}
        </div>
        <div class="text-sm font-medium" :class="idx.change_pct >= 0 ? 'text-rise' : 'text-fall'">
          {{ idx.change_pct >= 0 ? '+' : '' }}{{ idx.change_pct }}%
        </div>
      </div>
      <div v-if="!overview.indices.length" class="col-span-full text-center text-muted py-6">指数加载中...</div>
    </div>

    <!-- 市场统计 -->
    <div v-if="overview.stats.total" class="grid grid-cols-3 md:grid-cols-7 gap-3">
      <StatCard label="上涨" :value="overview.stats.up_count" color="text-rise" />
      <StatCard label="下跌" :value="overview.stats.down_count" color="text-fall" />
      <StatCard label="平盘" :value="overview.stats.flat_count" color="text-muted" />
      <StatCard label="涨停" :value="overview.stats.limit_up" color="text-orange-400" />
      <StatCard label="跌停" :value="overview.stats.limit_down" color="text-blue-400" />
      <StatCard label="平均涨幅" :value="overview.stats.avg_change_pct + '%'" :color="overview.stats.avg_change_pct >= 0 ? 'text-rise' : 'text-fall'" />
      <StatCard label="总成交额" :value="formatAmount(overview.stats.total_amount)" color="text-accent" />
    </div>
    <div v-else class="bg-card border border-border rounded-lg p-6 text-center text-muted">
      股票数据加载中，首次需扫描约14000个代码，请稍候...
    </div>

    <!-- 图表区 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <h3 class="text-sm font-semibold text-muted mb-3">沪深300走势</h3>
      <div ref="indexChartRef" class="h-72"></div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import { getMarketOverview, getIndexKline, getMarketTemperature, getMacroSnapshot, getFlashDiagnosis } from '../api'

const overview = ref({ indices: [], stats: {} })
const temp = ref({})
const macro = ref({})
const flashDiag = ref(null)
const indexChartRef = ref(null)
let charts = []

// 宏观方向分组标签
const groupLabels = { china: '中国信号', global: '全球风险', commodity: '商品需求', internal: '内部状态' }

function formatAmount(v) {
  if (!v || v < 1e8) return (v / 1e4).toFixed(0) + '万'
  return (v / 1e8).toFixed(1) + '亿'
}

// 市场温度等级配色：冷→蓝，中性→琥珀，热→红
function levelColor(level) {
  return { '过热': 'text-red-400', '偏热': 'text-orange-400', '中性': 'text-amber-400',
           '偏冷': 'text-cyan-400', '过冷': 'text-blue-400' }[level] || 'text-muted'
}

// 宏观方向等级配色：多→红（A股红涨），空→蓝/青
function dirColor(level) {
  return { '强多': 'text-red-400', '偏多': 'text-orange-400', '中性': 'text-amber-400',
           '偏空': 'text-cyan-400', '强空': 'text-blue-400' }[level] || 'text-muted'
}

function fmtScore(v) {
  const n = parseFloat(v) || 0
  return (n > 0 ? '+' : '') + n.toFixed(2)
}

onMounted(async () => {
  try {
    const { data } = await getMarketOverview()
    overview.value = data
  } catch (e) { console.error(e) }

  // 市场环境温度（独立信号，失败不影响主页面）
  try {
    const { data } = await getMarketTemperature()
    temp.value = data
  } catch (e) { console.error(e) }

  // 今日宏观方向（规则引擎，失败不影响主页面）
  try {
    const { data } = await getMacroSnapshot()
    macro.value = data
  } catch (e) { console.error(e) }

  // 最新 LLM 诊断头条（事件面，失败不影响主页面）
  try {
    const { data } = await getFlashDiagnosis({ limit: 1 })
    flashDiag.value = data.latest?.output || null
  } catch (e) { console.error(e) }

  await nextTick()

  // 沪深300 K线
  try {
    const { data: klineData } = await getIndexKline('000300')
    if (klineData.length && indexChartRef.value) {
      const chart = echarts.init(indexChartRef.value, 'dark')
      charts.push(chart)
      chart.setOption({
        backgroundColor: 'transparent',
        textStyle: { color: '#8b949e' },
        tooltip: { trigger: 'axis' },
        grid: [{ left: '8%', right: '3%', top: '10%', height: '55%' }, { left: '8%', right: '3%', top: '72%', height: '20%' }],
        xAxis: [
          { type: 'category', data: klineData.map(d => d.date), gridIndex: 0, axisLabel: { fontSize: 10 } },
          { type: 'category', data: klineData.map(d => d.date), gridIndex: 1, axisLabel: { show: false } },
        ],
        yAxis: [
          { type: 'value', gridIndex: 0, scale: true, splitLine: { lineStyle: { color: '#21262d' } } },
          { type: 'value', gridIndex: 1, scale: true, splitLine: { show: false }, axisLabel: { formatter: (v) => (v / 1e8).toFixed(1) + '亿' } },
        ],
        dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 }],
        series: [
          { name: '沪深300', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: klineData.map(d => d.close), lineStyle: { color: '#58a6ff', width: 1.5 }, symbol: 'none', areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(88,166,255,0.2)' }, { offset: 1, color: 'rgba(88,166,255,0)' }]) } },
          { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: klineData.map(d => d.volume), itemStyle: { color: '#30363d' }, barMaxWidth: 3, tooltip: { valueFormatter: (v) => (v / 1e8).toFixed(2) + '亿' } },
        ],
      })
    }
  } catch (e) { console.error(e) }

  window.addEventListener('resize', () => charts.forEach(c => c.resize()))
})

onBeforeUnmount(() => { charts.forEach(c => c.dispose()); charts = [] })
</script>

<script>
export default {
  components: {
    StatCard: {
      props: ['label', 'value', 'color'],
      template: `
        <div class="bg-card border border-border rounded-lg p-3 text-center">
          <div class="text-xs text-muted">{{ label }}</div>
          <div class="text-lg font-bold mt-1" :class="color">{{ value }}</div>
        </div>
      `,
    },
  },
}
</script>
