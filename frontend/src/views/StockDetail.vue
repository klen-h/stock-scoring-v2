<template>
  <div class="fade-in space-y-4" v-if="loaded">
    <!-- 个股头部信息 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center gap-4">
        <div class="flex-1">
          <div class="flex items-center gap-3">
            <span class="text-2xl font-bold">{{ stockInfo.name || code }}</span>
            <span class="text-muted font-mono text-sm">{{ code }}</span>
            <!-- 评分标签 -->
            <span v-if="scoreData.total_score" class="px-2.5 py-0.5 rounded-full text-xs font-bold"
              :class="scoreColorClass">
              {{ scoreData.signal }} {{ scoreData.total_score }}
            </span>
          </div>
          <div class="flex items-center gap-4 mt-2">
            <span class="text-3xl font-bold" :class="stockInfo.change_pct >= 0 ? 'text-rise' : 'text-fall'">{{ stockInfo.price }}</span>
            <div>
              <div class="text-lg font-semibold" :class="stockInfo.change_pct >= 0 ? 'text-rise' : 'text-fall'">
                {{ stockInfo.change_amt >= 0 ? '+' : '' }}{{ stockInfo.change_amt }}
              </div>
              <div class="text-sm" :class="stockInfo.change_pct >= 0 ? 'text-rise' : 'text-fall'">
                {{ stockInfo.change_pct >= 0 ? '+' : '' }}{{ stockInfo.change_pct }}%
              </div>
            </div>
          </div>
        </div>
        <div class="grid grid-cols-4 gap-x-6 gap-y-2 text-sm">
          <div><span class="text-muted">今开</span><div class="font-mono">{{ stockInfo.open }}</div></div>
          <div><span class="text-muted">最高</span><div class="font-mono text-rise">{{ stockInfo.high }}</div></div>
          <div><span class="text-muted">最低</span><div class="font-mono text-fall">{{ stockInfo.low }}</div></div>
          <div><span class="text-muted">昨收</span><div class="font-mono">{{ stockInfo.prev_close }}</div></div>
          <div><span class="text-muted">成交量</span><div class="font-mono">{{ formatVol(stockInfo.volume) }}</div></div>
          <div><span class="text-muted">成交额</span><div class="font-mono">{{ formatAmt(stockInfo.amount) }}</div></div>
          <div><span class="text-muted">换手率</span><div class="font-mono">{{ stockInfo.turnover_rate }}%</div></div>
          <div><span class="text-muted">市盈率</span><div class="font-mono">{{ stockInfo.pe }}</div></div>
        </div>
      </div>
    </div>

    <!-- 综合评分面板 -->
    <div v-if="scoreData.total_score" class="bg-card border border-border rounded-lg p-5">
      <div class="flex items-center justify-between mb-4">
        <h3 class="text-sm font-semibold">多因子综合评分</h3>
        <span class="text-xs text-muted">技术面40% + 资金面25% + 基本面35%</span>
      </div>

      <div class="flex items-center gap-6">
        <!-- 左侧：总分圆环 -->
        <div class="flex-shrink-0 relative w-28 h-28">
          <svg class="w-28 h-28 -rotate-90" viewBox="0 0 120 120">
            <circle cx="60" cy="60" r="52" fill="none" stroke="#21262d" stroke-width="8"/>
            <circle cx="60" cy="60" r="52" fill="none" :stroke="scoreRingColor" stroke-width="8"
              stroke-linecap="round"
              :stroke-dasharray="2 * Math.PI * 52"
              :stroke-dashoffset="2 * Math.PI * 52 * (1 - scoreData.total_score / 100)"/>
          </svg>
          <div class="absolute inset-0 flex flex-col items-center justify-center">
            <span class="text-2xl font-bold" :class="scoreTextColor">{{ scoreData.total_score }}</span>
            <span class="text-xs text-muted">综合分</span>
          </div>
        </div>

        <!-- 右侧：维度条 -->
        <div class="flex-1 space-y-3">
          <div v-for="dim in scoreData.dimensions" :key="dim.name">
            <div class="flex justify-between text-xs mb-1">
              <span class="text-gray-300">{{ dim.name }}</span>
              <span :class="dimScoreColor(dim.score)">{{ dim.score }}<span class="text-muted"> / 100</span></span>
            </div>
            <div class="h-2 bg-bg rounded-full overflow-hidden">
              <div class="h-full rounded-full transition-all duration-500" :class="dimBarColor(dim.score)"
                :style="{ width: dim.score + '%' }"></div>
            </div>
            <!-- 子项展开 -->
            <div v-if="dim.details && showDetails" class="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-muted pl-2">
              <div v-for="(val, key) in dim.details" :key="key" class="flex justify-between">
                <span>{{ key }}</span>
                <span :class="val.分值 >= 70 ? 'text-emerald-400' : val.分值 <= 35 ? 'text-red-400' : ''">
                  {{ val.分值 }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 因素标签 -->
      <div class="mt-4 flex flex-wrap gap-2">
        <span v-for="f in scoreData.factors_up" :key="'u'+f"
          class="px-2 py-0.5 rounded text-xs bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">
          + {{ f }}
        </span>
        <span v-for="f in scoreData.factors_down" :key="'d'+f"
          class="px-2 py-0.5 rounded text-xs bg-red-500/15 text-red-400 border border-red-500/20">
          - {{ f }}
        </span>
      </div>

      <!-- 摘要 -->
      <p v-if="scoreData.summary" class="mt-3 text-xs text-muted leading-relaxed">{{ scoreData.summary }}</p>
    </div>

    <!-- 趋势健康度诊断 -->
    <div v-if="scoreData.trend_health?.verdict" class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-3">
        <h3 class="text-sm font-semibold">趋势健康度诊断</h3>
        <span class="px-2.5 py-0.5 rounded-full text-xs font-bold"
          :class="healthVerdictClass(scoreData.trend_health.verdict)">
          {{ scoreData.trend_health.verdict }} {{ scoreData.trend_health.score }}/5
        </span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-5 gap-2">
        <div v-for="d in scoreData.trend_health.details" :key="d.dim"
          class="px-3 py-2 rounded-lg text-xs"
          :class="d.healthy ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-red-500/10 border border-red-500/20'">
          <div class="flex items-center gap-1.5 mb-1">
            <span class="w-2 h-2 rounded-full" :class="d.healthy ? 'bg-emerald-400' : 'bg-red-400'"></span>
            <span class="font-semibold" :class="d.healthy ? 'text-emerald-400' : 'text-red-400'">{{ d.dim }}</span>
          </div>
          <div class="text-muted leading-tight">{{ d.desc }}</div>
        </div>
      </div>
      <div class="mt-3 text-[11px] text-muted">
        ≥4/5 趋势健康，回调大概率为洗盘，耐心持有；≤2/5 趋势恶化，真跌风险高，考虑减仓
      </div>
    </div>

    <!-- K线图 + 技术指标 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex gap-2 mb-3 flex-wrap">
        <button v-for="p in ['day','week','month']" :key="p" @click="changePeriod(p)"
          :class="period === p ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
          class="px-3 py-1 rounded text-xs transition-colors">{{ {day:'日K',week:'周K',month:'月K'}[p] }}</button>
        <div class="ml-auto flex gap-2">
          <button v-for="ind in indicators" :key="ind.key" @click="toggleIndicator(ind.key)"
            :class="activeIndicators.includes(ind.key) ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted'"
            class="px-2 py-1 rounded text-xs transition-colors">{{ ind.label }}</button>
        </div>
      </div>
      <div ref="klineChartRef" class="h-[500px]"></div>
    </div>

    <!-- 支撑阻力 + RSI -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
      <!-- 支撑阻力 -->
      <div v-if="supportResistance" class="bg-card border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold text-muted mb-3">支撑阻力位</h3>
        <div class="flex items-center justify-between mb-3">
          <span class="text-xs text-muted">当前位置</span>
          <div class="flex items-center gap-2">
            <div class="w-24 h-2 rounded-full overflow-hidden" style="background:#21262d">
              <div class="h-full rounded-full transition-all"
                :style="{ width: supportResistance.position_pct + '%', background: srBarBg }"></div>
            </div>
            <span class="text-sm font-mono" :style="{ color: srBarBg }">{{ supportResistance.position_pct }}%</span>
          </div>
        </div>
        <div class="space-y-1.5">
          <div v-for="level in supportResistance.levels" :key="level.price"
            class="flex items-center justify-between text-xs">
            <span class="w-10 text-muted">{{ level.type === 'resistance' ? '阻力' : level.type === 'support' ? '支撑' : '中性' }}</span>
            <span class="font-mono">{{ level.price }}</span>
            <div class="flex items-center gap-1">
              <span :class="level.strength === 'strong' ? 'text-emerald-400' : level.strength === 'medium' ? 'text-amber-400' : 'text-muted'">
                {{ level.strength === 'strong' ? '●●' : level.strength === 'medium' ? '●○' : '○○' }}
              </span>
              <span class="text-muted w-8 text-right">{{ level.touches }}次</span>
            </div>
          </div>
        </div>
        <div class="mt-3 pt-2 border-t border-border text-xs text-muted leading-relaxed">
          {{ supportResistance.suggestion }}
        </div>
      </div>

      <!-- RSI -->
      <div v-if="rsiSignals" class="bg-card border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold text-muted mb-3">RSI 指标</h3>
        <div class="flex items-center justify-between mb-3">
          <span class="text-xs text-muted">当前 RSI</span>
          <span class="text-2xl font-bold font-mono" :class="rsiColor">{{ rsiSignals.current_rsi }}</span>
        </div>
        <!-- RSI 刻度条 -->
        <div class="relative h-3 rounded-full overflow-hidden mb-3"
          style="background: linear-gradient(to right, #22c55e 0%, #22c55e 20%, #f59e0b 30%, #8b949e 50%, #f59e0b 70%, #ef4444 80%, #ef4444 100%)">
          <div class="absolute top-0 h-full w-1 bg-white shadow" :style="{ left: rsiSignals.current_rsi + '%' }"></div>
        </div>
        <div class="flex justify-between text-[10px] text-muted mb-3">
          <span>超卖 30</span>
          <span>中性 50</span>
          <span>超买 70</span>
        </div>
        <div class="space-y-2 text-xs">
          <div class="flex justify-between">
            <span class="text-muted">区间</span>
            <span :class="rsiColor">{{ rsiZoneText }}</span>
          </div>
          <div v-if="rsiSignals.signal" class="flex justify-between">
            <span class="text-muted">信号</span>
            <span :class="rsiSignals.signal.type === 'buy' ? 'text-emerald-400' : rsiSignals.signal.type === 'sell' ? 'text-red-400' : 'text-amber-400'">
              {{ rsiSignals.signal.description }}
            </span>
          </div>
        </div>
        <div class="mt-3 pt-2 border-t border-border text-xs text-muted leading-relaxed">
          {{ rsiSignals.interpretation }}
        </div>
      </div>
    </div>

    <!-- 基本面 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <h3 class="text-sm font-semibold text-muted mb-3">基本面数据</h3>
      <div v-if="fundamental.valuation && fundamental.valuation['市盈率(动态)'] !== undefined" class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">市盈率(动态)</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['市盈率(动态)'] }}</div>
        </div>
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">市净率</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['市净率'] }}</div>
        </div>
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">总市值(亿)</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['总市值(亿)'] }}</div>
        </div>
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">流通市值(亿)</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['流通市值(亿)'] }}</div>
        </div>
      </div>
    </div>
  </div>
  <div v-else class="flex items-center justify-center py-32"><div class="loading-spinner"></div></div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import { getStockKline, getStockRealtime, getStockFundamental, getStockTechnical, getStockScore, getSupportResistance, getRSISignals } from '../api'

const route = useRoute()
const code = route.params.code

const stockInfo = ref({})
const klineData = ref([])
const technicalData = ref([])
const fundamental = ref({ valuation: {}, financial: {} })
const scoreData = ref({})
const loaded = ref(false)
const period = ref('day')
const activeIndicators = ref(['ma', 'macd', 'vol'])
const showDetails = ref(true)

// 支撑阻力 + RSI
const supportResistance = ref(null)
const rsiSignals = ref(null)

const klineChartRef = ref(null)
let charts = []

const indicators = [
  { key: 'ma', label: 'MA' },
  { key: 'macd', label: 'MACD' },
  { key: 'vol', label: '成交量' },
  { key: 'boll', label: 'BOLL' },
  { key: 'rsi', label: 'RSI' },
]

// 评分颜色
const scoreRingColor = computed(() => {
  const s = scoreData.value.total_score || 0
  if (s >= 65) return '#22c55e'
  if (s >= 45) return '#f59e0b'
  return '#ef4444'
})
const scoreTextColor = computed(() => {
  const s = scoreData.value.total_score || 0
  if (s >= 65) return 'text-emerald-400'
  if (s >= 45) return 'text-amber-400'
  return 'text-red-400'
})
const scoreColorClass = computed(() => {
  const s = scoreData.value.total_score || 0
  if (s >= 65) return 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
  if (s >= 45) return 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
  return 'bg-red-500/20 text-red-400 border border-red-500/30'
})

function dimScoreColor(s) {
  if (s >= 70) return 'text-emerald-400'
  if (s >= 45) return 'text-amber-400'
  return 'text-red-400'
}
function dimBarColor(s) {
  if (s >= 70) return 'bg-emerald-500'
  if (s >= 45) return 'bg-amber-500'
  return 'bg-red-500'
}

function healthVerdictClass(verdict) {
  return { '趋势健康': 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
    '趋势偏弱': 'bg-amber-500/20 text-amber-400 border border-amber-500/30',
    '趋势恶化': 'bg-red-500/20 text-red-400 border border-red-500/30',
  }[verdict] || 'bg-white/5 text-muted'
}

// ── 支撑阻力 + RSI 显示辅助 ──
const srBarBg = computed(() => {
  if (!supportResistance.value) return '#8b949e'
  const p = supportResistance.value.position_pct
  if (p >= 75) return '#f59e0b'  // amber
  if (p <= 25) return '#22c55e'  // emerald
  return '#8b949e'               // gray/muted
})

const srPositionColor = computed(() => {
  if (!supportResistance.value) return 'text-muted'
  const p = supportResistance.value.position_pct
  if (p >= 75) return 'text-amber-400'
  if (p <= 25) return 'text-emerald-400'
  return 'text-muted'
})

const srBarColor = computed(() => {
  if (!supportResistance.value) return 'bg-muted'
  const p = supportResistance.value.position_pct
  if (p >= 75) return 'bg-amber-400'
  if (p <= 25) return 'bg-emerald-400'
  return 'bg-gray-400'
})

const rsiColor = computed(() => {
  if (!rsiSignals.value) return 'text-muted'
  const z = rsiSignals.value.zone
  return { 'strong_overbought': 'text-red-400', 'overbought': 'text-amber-400',
    'strong_oversold': 'text-emerald-400', 'oversold': 'text-emerald-400',
    'neutral': 'text-muted' }[z] || 'text-muted'
})

const rsiZoneText = computed(() => {
  if (!rsiSignals.value) return ''
  return { 'strong_overbought': '强超买', 'overbought': '超买',
    'strong_oversold': '强超卖', 'oversold': '超卖', 'neutral': '中性' }[rsiSignals.value.zone] || ''
})

function formatVol(v) {
  const n = parseFloat(v)
  if (!n) return '-'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  return (n / 1e4).toFixed(1) + '万'
}
function formatAmt(v) {
  const n = parseFloat(v)
  if (!n) return '-'
  if (n >= 1e12) return (n / 1e12).toFixed(2) + '万亿'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  return (n / 1e4).toFixed(1) + '万'
}

function toggleIndicator(key) {
  const idx = activeIndicators.value.indexOf(key)
  if (idx >= 0) activeIndicators.value.splice(idx, 1)
  else activeIndicators.value.push(key)
  renderKline()
}

function changePeriod(p) {
  period.value = p
  loadKline()
}

async function loadKline() {
  try {
    const { data: kd } = await getStockKline(code, { period: period.value })
    klineData.value = kd
    const { data: td } = await getStockTechnical(code, period.value)
    technicalData.value = td
    await nextTick()
    renderKline()
  } catch (e) { console.error(e) }
}

// ── 构建支撑阻力标记线 ──
function buildSRMarkLine() {
  if (!supportResistance.value || !supportResistance.value.levels?.length) return { data: [] }
  const lines = supportResistance.value.levels.map(level => {
    const color = level.type === 'resistance' ? '#ef4444' : level.type === 'support' ? '#22c55e' : '#f59e0b'
    return {
      yAxis: level.price,
      label: {
        formatter: `${level.type === 'resistance' ? '阻' : level.type === 'support' ? '支' : ''} ${level.price}`,
        position: 'insideEndTop',
        fontSize: 10,
        color: color,
      },
      lineStyle: { color: color, type: 'dashed', width: 1, opacity: 0.6 },
    }
  })
  return {
    symbol: 'none',
    data: lines,
    animation: false,
  }
}

function renderKline() {
  if (!klineChartRef.value || !klineData.value.length) return

  const dates = klineData.value.map(d => d.date)
  const ohlc = klineData.value.map(d => [d.open, d.close, d.low, d.high])
  const volumes = klineData.value.map(d => d.volume)
  const colors = klineData.value.map(d => d.close >= d.open ? '#ef4444' : '#22c55e')

  const gridCfg = [{ left: '8%', right: '2%', top: '3%', height: '55%' }]
  const xAxisCfg = [{ type: 'category', data: dates, gridIndex: 0, axisLabel: { fontSize: 10 }, boundaryGap: true }]
  const yAxisCfg = [{ type: 'value', gridIndex: 0, scale: true, splitLine: { lineStyle: { color: '#21262d' } } }]
  const seriesCfg = [
    { name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: ohlc, itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' },
      markLine: buildSRMarkLine(),
    },
  ]

  let gridIdx = 1

  if (activeIndicators.value.includes('ma') && technicalData.value.length) {
    const maColors = ['#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899']
    ;['ma5','ma10','ma20','ma60'].forEach((key, i) => {
      seriesCfg.push({ name: key.toUpperCase(), type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d[key]), lineStyle: { color: maColors[i], width: 1 }, symbol: 'none' })
    })
  }

  if (activeIndicators.value.includes('boll') && technicalData.value.length) {
    seriesCfg.push(
      { name: 'BOLL上', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d.boll_upper), lineStyle: { color: '#6366f1', width: 1, type: 'dashed' }, symbol: 'none' },
      { name: 'BOLL中', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d.boll_mid), lineStyle: { color: '#6366f1', width: 1 }, symbol: 'none' },
      { name: 'BOLL下', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d.boll_lower), lineStyle: { color: '#6366f1', width: 1, type: 'dashed' }, symbol: 'none' },
    )
  }

  if (activeIndicators.value.includes('vol')) {
    gridCfg.push({ left: '8%', right: '2%', top: '65%', height: '12%' })
    xAxisCfg.push({ type: 'category', data: dates, gridIndex: gridIdx, axisLabel: { show: false }, boundaryGap: true })
    yAxisCfg.push({ type: 'value', gridIndex: gridIdx, scale: true, splitLine: { show: false }, axisLabel: { show: false } })
    seriesCfg.push({ name: '成交量', type: 'bar', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: volumes.map((v, i) => ({ value: v, itemStyle: { color: colors[i], opacity: 0.7 } })), barMaxWidth: 4 })
    gridIdx++
  }

  if (activeIndicators.value.includes('macd') && technicalData.value.length) {
    gridCfg.push({ left: '8%', right: '2%', top: '80%', height: '16%' })
    xAxisCfg.push({ type: 'category', data: dates, gridIndex: gridIdx, axisLabel: { fontSize: 10 }, boundaryGap: true })
    yAxisCfg.push({ type: 'value', gridIndex: gridIdx, scale: true, splitLine: { show: false }, axisLabel: { show: false } })
    seriesCfg.push(
      { name: 'DIF', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: technicalData.value.map(d => d.dif), lineStyle: { color: '#f59e0b', width: 1 }, symbol: 'none' },
      { name: 'DEA', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: technicalData.value.map(d => d.dea), lineStyle: { color: '#8b5cf6', width: 1 }, symbol: 'none' },
      { name: 'MACD', type: 'bar', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: technicalData.value.map(d => ({ value: d.macd, itemStyle: { color: d.macd >= 0 ? '#ef4444' : '#22c55e' } })), barMaxWidth: 3 },
    )
    gridIdx++
  }

  if (activeIndicators.value.includes('rsi') && rsiSignals.value?.rsi_history?.length) {
    const rsiData = rsiSignals.value.rsi_history
    const rsiDates = rsiData.map(d => d.date)
    const rsiValues = rsiData.map(d => d.rsi)
    // 对齐日期：用 K线日期匹配 RSI 日期
    const rsiMap = new Map(rsiData.map(d => [d.date, d.rsi]))
    const alignedRsi = dates.map(dt => rsiMap.get(dt) ?? null)

    gridCfg.push({ left: '8%', right: '2%', top: '80%', height: '16%' })
    xAxisCfg.push({ type: 'category', data: dates, gridIndex: gridIdx, axisLabel: { fontSize: 10 }, boundaryGap: true })
    yAxisCfg.push({ type: 'value', gridIndex: gridIdx, min: 0, max: 100, splitLine: { show: false }, axisLabel: { show: false } })
    seriesCfg.push(
      { name: 'RSI', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: alignedRsi, lineStyle: { color: '#f59e0b', width: 1.5 }, symbol: 'none' },
      { name: '超买', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: dates.map(() => 70), lineStyle: { color: '#ef4444', width: 1, type: 'dashed' }, symbol: 'none' },
      { name: '超卖', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: dates.map(() => 30), lineStyle: { color: '#22c55e', width: 1, type: 'dashed' }, symbol: 'none' },
    )
    gridIdx++
  }

  const zoomAxes = xAxisCfg.map((_, i) => i)

  if (charts[0]) charts[0].dispose()
  const chart = echarts.init(klineChartRef.value, 'dark')
  chart.setOption({
    backgroundColor: 'transparent', textStyle: { color: '#8b949e' },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: seriesCfg.map(s => s.name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 0, itemWidth: 12, itemHeight: 8 },
    grid: gridCfg, xAxis: xAxisCfg, yAxis: yAxisCfg,
    dataZoom: [
      { type: 'inside', xAxisIndex: zoomAxes, start: 70, end: 100 },
      { type: 'slider', xAxisIndex: zoomAxes, bottom: 0, height: 18, borderColor: '#30363d', backgroundColor: '#161b22', fillerColor: 'rgba(88,166,255,0.1)', textStyle: { color: '#8b949e', fontSize: 10 } },
    ],
    series: seriesCfg,
  })
  charts[0] = chart
}

onMounted(async () => {
  try {
    const [info, fund, score, sr, rsi] = await Promise.allSettled([
      getStockRealtime(code),
      getStockFundamental(code),
      getStockScore(code),
      getSupportResistance(code),
      getRSISignals(code),
    ])
    if (info.status === 'fulfilled') stockInfo.value = info.value.data || {}
    if (fund.status === 'fulfilled') fundamental.value = fund.value.data || {}
    if (score.status === 'fulfilled' && score.value.data) scoreData.value = score.value.data
    if (sr.status === 'fulfilled' && sr.value.data) supportResistance.value = sr.value.data.data
    if (rsi.status === 'fulfilled' && rsi.value.data) rsiSignals.value = rsi.value.data.data
  } catch (e) { console.error(e) }

  // 关键修复：先把 loaded 置 true，让模板（含 klineChartRef 容器）渲染出来，
  // 再 await nextTick 确保 DOM 完成布局，最后才 loadKline → renderKline。
  // 之前的顺序是 loadKline 在前、loaded=true 在后，导致 echarts 容器不存在，图根本没画。
  loaded.value = true
  await nextTick()
  await loadKline()

  window.addEventListener('resize', () => charts.forEach(c => c && c.resize()))
})

onBeforeUnmount(() => { charts.forEach(c => c && c.dispose()); charts = [] })

watch(() => route.params.code, () => { window.location.reload() })
</script>