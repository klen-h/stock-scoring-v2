<template>
  <div class="fade-in space-y-4">
    <!-- ① 头部 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3 flex-wrap">
          <h1 class="text-lg font-bold text-gray-100">题材轮动</h1>
          <span class="text-[11px] text-muted">风向标 · 看人气与阶段，不是买卖信号</span>
          <span v-if="data.date" class="text-[11px] px-2 py-0.5 rounded bg-white/5 text-gray-300 font-mono">
            {{ data.date }}<template v-if="data.prev_date"> ← {{ data.prev_date }}</template>
          </span>
          <span v-if="data.regime" class="text-[11px] px-2 py-0.5 rounded font-semibold"
                :class="regimeBadge(data.regime.level)">
            {{ data.regime.label }} · 均涨 {{ fmt(data.regime.avg_pct, 2) }}% ·
            {{ fmt((data.regime.up_ratio || 0) * 100, 0) }}% 上涨
          </span>
        </div>
        <div class="flex items-center gap-2">
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="t in TYPES" :key="t.v"
                    class="px-2.5 py-1 rounded text-xs transition-colors"
                    :class="plateType === t.v ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
                    @click="plateType = t.v; reload()">{{ t.label }}</button>
          </div>
          <input v-model="day" type="date"
                 class="px-2 py-1 rounded text-xs bg-bg border border-border text-gray-200" />
          <button @click="reload()" :disabled="loading"
                  class="px-3 py-1 rounded text-xs border border-border text-muted hover:text-gray-200 disabled:opacity-50">
            {{ loading ? '…' : '↻' }}
          </button>
        </div>
      </div>
      <div class="flex items-center gap-4 mt-2 text-[11px] text-muted flex-wrap">
        <span>在榜 <b class="text-gray-200 font-mono">{{ data.counts?.total || 0 }}</b></span>
        <span>新进 <b class="text-accent font-mono">{{ data.counts?.new_in || 0 }}</b></span>
        <span>掉队 <b class="text-fall font-mono">{{ data.counts?.drop || 0 }}</b></span>
        <span v-if="error" class="text-fall">⚠️ {{ error }}</span>
      </div>
    </div>

    <!-- ② 排名流图（核心可视化） -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div>
          <h2 class="text-sm font-bold text-gray-200">排名流图</h2>
          <p class="text-[11px] text-muted mt-0.5">
            纵轴是名次（1 在最上）。<b class="text-gray-300">线往上走 = 人气在进来</b>；
            断掉 = 当天掉出榜。只画最近进过前 {{ histTop }} 的题材。
          </p>
        </div>
        <div class="flex items-center gap-1 text-[11px]">
          <span class="text-muted">回看</span>
          <button v-for="d in [5, 8, 12, 20]" :key="d"
                  class="px-2 py-0.5 rounded border transition-colors"
                  :class="histDays === d ? 'border-accent/40 bg-accent/15 text-accent' : 'border-border text-muted hover:text-gray-200'"
                  @click="histDays = d; loadHistory()">{{ d }}日</button>
        </div>
      </div>
      <div ref="chartRef" class="w-full" style="height: 340px"></div>
      <div v-if="!hist.items?.length" class="text-xs text-muted py-6 text-center">
        {{ histErr || '暂无排名序列（需要至少 2 个交易日数据）' }}
      </div>
    </div>

    <!-- ③ 多日对比表 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <h2 class="text-sm font-bold text-gray-200 mb-1">多日排名对比</h2>
      <p class="text-[11px] text-muted mb-2">
        每列一个交易日，按当天名次排。<b class="text-rise">↑红</b>=名次上升，
        <b class="text-fall">↓绿</b>=下降，<span class="text-muted">—</span>=持平/新进。
      </p>
      <div class="overflow-x-auto">
        <table class="text-[11px] border-collapse">
          <thead>
            <tr>
              <th class="text-left px-2 py-1 text-muted font-normal sticky left-0 bg-card z-10">日期</th>
              <th v-for="c in multiDay" :key="c.date"
                  class="px-2 py-1 text-left text-muted font-normal whitespace-nowrap">
                {{ c.date.slice(5) }} <span class="text-[10px]">{{ weekday(c.date) }}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in 10" :key="r" class="border-t border-border/20">
              <td class="px-2 py-1 text-muted sticky left-0 bg-card z-10">{{ r }}</td>
              <td v-for="c in multiDay" :key="c.date" class="px-2 py-1 whitespace-nowrap">
                <template v-if="c.rows[r - 1]">
                  <span class="font-medium" :class="cellCls(c.rows[r - 1])">
                    {{ c.rows[r - 1].name }}
                  </span>
                  <span class="text-[10px] text-muted ml-1 font-mono">
                    {{ fmt(c.rows[r - 1].sum_rate, 1) }}%
                  </span>
                  <span v-if="c.rows[r - 1].delta" class="text-[10px] ml-1 font-mono"
                        :class="c.rows[r - 1].delta > 0 ? 'text-rise' : 'text-fall'">
                    {{ c.rows[r - 1].delta > 0 ? '↑' + c.rows[r - 1].delta : '↓' + Math.abs(c.rows[r - 1].delta) }}
                  </span>
                </template>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ④ 四阶段 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-2">
        <h2 class="text-sm font-bold text-gray-200">阶段分布</h2>
        <span class="text-[10px] text-muted">规则化判定，不是预测</span>
      </div>
      <div class="grid grid-cols-2 xl:grid-cols-4 gap-2">
        <div v-for="g in STAGES" :key="g.key"
             class="border rounded-lg p-2.5" :class="g.box">
          <div class="flex items-center justify-between mb-1.5">
            <span class="text-[11px] font-semibold" :class="g.cls">{{ g.label }}</span>
            <span class="text-[10px] text-muted font-mono">{{ (data.stages?.[g.key] || []).length }}</span>
          </div>
          <div class="space-y-0.5 max-h-[160px] overflow-y-auto pr-0.5">
            <button v-for="x in (data.stages?.[g.key] || [])" :key="x.code"
                    class="w-full flex items-center justify-between gap-1.5 text-[11px] text-left
                           hover:bg-white/5 rounded px-1 py-0.5"
                    @click="pick(x)">
              <span class="truncate text-gray-300">{{ x.name }}</span>
              <span class="font-mono shrink-0" :class="pctCls(x.sum_rate)">{{ fmt(x.sum_rate, 1) }}%</span>
            </button>
            <div v-if="!(data.stages?.[g.key] || []).length" class="text-[10px] text-muted px-1">无</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ⑤ 主题材卡片 + 个股 -->
    <div class="grid grid-cols-1 xl:grid-cols-[1.55fr_1fr] gap-3">
      <!-- 卡片网格 -->
      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-bold text-gray-200">全部在榜题材</h2>
          <span class="text-[10px] text-muted">点任意题材查看相关个股</span>
        </div>
        <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-1.5">
          <button v-for="x in (data.top || [])" :key="x.code"
                  class="text-left rounded border px-2 py-1.5 transition-colors"
                  :class="cardCls(x)"
                  @click="pick(x)">
            <div class="flex items-center justify-between gap-1">
              <span class="text-[11px] font-medium truncate">{{ x.name }}</span>
              <span class="text-[10px] font-mono shrink-0" :class="pctCls(x.sum_rate)">
                {{ fmt(x.sum_rate, 2) }}%
              </span>
            </div>
            <div class="flex items-center justify-between mt-0.5 text-[9px] text-muted font-mono">
              <span>#{{ x.rank }}</span>
              <span :class="(x.accel || 0) > 0 ? 'text-rise' : 'text-fall'">
                {{ x.is_new ? 'NEW' : (x.accel === null ? '—' : fmt(x.accel, 1)) }}
              </span>
            </div>
          </button>
        </div>
      </div>

      <!-- 个股面板 -->
      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-bold text-gray-200 truncate">
            {{ sel ? sel.name + ' · 相关个股' : '相关个股' }}
          </h2>
          <span v-if="stk.n_total" class="text-[10px] text-muted">{{ stk.n_total }} 只成分</span>
        </div>
        <p v-if="!sel" class="text-[11px] text-muted py-6 text-center">
          左侧点一个题材，这里显示它当日涨幅最高的成分股。
        </p>
        <div v-else-if="stkLoading" class="text-[11px] text-muted py-6 text-center">加载中…</div>
        <p v-else-if="stkErr" class="text-[11px] text-fall py-6 text-center">⚠️ {{ stkErr }}</p>
        <div v-else class="space-y-0.5">
          <div class="flex items-center gap-2 text-[10px] text-muted border-b border-border/30 pb-1">
            <span class="flex-1">名称</span>
            <span class="w-14 text-right">当日</span>
            <span class="w-12 text-right">5日</span>
            <span class="w-12 text-right">评分</span>
          </div>
          <a v-for="s in (stk.items || [])" :key="s.code"
             :href="'/stock/' + s.code" target="_blank" rel="noopener"
             class="flex items-center gap-2 text-[11px] py-0.5 hover:bg-white/5 rounded px-1">
            <span class="flex-1 truncate text-gray-300" :title="s.code">
              {{ s.name }} <span class="text-[9px] text-muted font-mono">{{ s.code }}</span>
            </span>
            <span class="w-14 text-right font-mono" :class="pctCls(s.chg_pct)">{{ fmt(s.chg_pct, 2) }}%</span>
            <span class="w-12 text-right font-mono text-muted">{{ fmt(s.ret5, 1) }}</span>
            <span class="w-12 text-right font-mono text-muted">{{ fmt(s.score, 0) }}</span>
          </a>
        </div>
        <p v-if="sel && !stkLoading && !stkErr && stk.note"
           class="text-[10px] text-muted mt-2 leading-relaxed">{{ stk.note }}</p>
      </div>
    </div>

    <!-- ⑥ 牛熊分层（折叠，需要时再算） -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-2">
        <div>
          <h2 class="text-sm font-bold text-gray-200">信号在不同环境下的表现</h2>
          <p class="text-[11px] text-muted mt-0.5">
            新进榜题材持有 5 日的超额收益。<b class="text-gray-300">弱势环境胜率 42%</b> ——追新必亏。
          </p>
        </div>
        <button @click="loadVerify" :disabled="vLoading"
                class="text-[10px] px-2 py-1 rounded border border-border text-muted hover:text-accent disabled:opacity-50">
          {{ vLoading ? '计算中…' : (vRows.length ? '重算' : '计算') }}
        </button>
      </div>
      <table v-if="vRows.length" class="w-full text-[11px]">
        <thead class="text-muted">
          <tr class="border-b border-border/40">
            <th class="text-left py-1 font-normal">环境</th>
            <th class="text-right font-normal">超额</th>
            <th class="text-right font-normal">胜率</th>
            <th class="text-right font-normal">样本</th>
            <th class="text-right font-normal">判定</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in vRows" :key="r.label" class="border-b border-border/20">
            <td class="py-1">{{ r.label }}</td>
            <td class="text-right font-mono" :class="pctCls(r.excess)">{{ fmt(r.excess, 3) }}%</td>
            <td class="text-right font-mono" :class="(r.win || 0) >= 0.5 ? 'text-gray-200' : 'text-fall'">
              {{ fmt((r.win || 0) * 100, 1) }}%
            </td>
            <td class="text-right font-mono text-muted">{{ r.n }}</td>
            <td class="text-right">
              <span class="px-1.5 py-0.5 rounded text-[10px]" :class="verdictCls(r)">{{ verdictText(r) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="text-[11px] text-muted">点「计算」跑一次事件研究（扫全表，约 1~2 秒）。</p>
    </div>

    <p class="text-[10px] text-muted px-1">{{ data.note }}</p>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import * as echarts from 'echarts'
import {
  getTopicRotationDashboard, getTopicRotationVerify,
  getTopicRotationHistory, getTopicRotationConstituents,
} from '../api/modules/market'

const TYPES = [{ v: 17, label: '题材' }, { v: 15, label: '概念' }, { v: 14, label: '行业' }]
const STAGES = [
  { key: 'emerging', label: '🚀 启动', cls: 'text-rise', box: 'border-rise/30 bg-rise/5' },
  { key: 'leading', label: '📈 主升', cls: 'text-accent', box: 'border-accent/30 bg-accent/5' },
  { key: 'crowded', label: '⚠️ 高位', cls: 'text-amber-400', box: 'border-amber-500/30 bg-amber-500/5' },
  { key: 'fading', label: '📉 退潮', cls: 'text-muted', box: 'border-border/60 bg-white/[0.02]' },
]

const day = ref('')
const plateType = ref(17)
const data = ref({})
const loading = ref(false)
const error = ref('')

const hist = ref({ dates: [], items: [] })
const histErr = ref('')
const histDays = ref(8)
const histTop = ref(20)

// 多日对比表：从 hist 的 ranks/rates 逐日还原（最后 5 个交易日）
const multiDay = computed(() => {
  const d = hist.value || {}
  const dates = (d.dates || []).slice(-5)
  const offset = (d.dates || []).length - dates.length
  return dates.map((date, k) => {
    const i = offset + k
    const rows = (d.items || [])
      .filter(x => (x.ranks || [])[i] !== null && (x.ranks || [])[i] !== undefined)
      .map(x => {
        const cur = x.ranks[i]
        const prev = i > 0 ? x.ranks[i - 1] : null
        return {
          code: x.code,
          name: x.name,
          rank: cur,
          sum_rate: (x.rates || [])[i],
          delta: (prev !== null && prev !== undefined) ? (prev - cur) : null,
        }
      })
      .sort((a, b) => a.rank - b.rank)
      .slice(0, 10)
    return { date, rows }
  })
})

const sel = ref(null)
const stk = ref({})
const stkLoading = ref(false)
const stkErr = ref('')

const vRows = ref([])
const vLoading = ref(false)

const chartRef = ref(null)
let chart = null

function fmt(v, n = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(n)
}
function pctCls(v) {
  if (v === null || v === undefined) return 'text-muted'
  return v > 0 ? 'text-rise' : (v < 0 ? 'text-fall' : 'text-muted')
}
function regimeBadge(lv) {
  if (lv === 1) return 'bg-rise/15 text-rise'
  if (lv === -1) return 'bg-fall/15 text-fall'
  return 'bg-amber-500/15 text-amber-400'
}
function weekday(d) {
  try {
    const w = new Date(d + 'T00:00:00').getDay()
    return '周' + '日一二三四五六'[w]
  } catch { return '' }
}
function cellCls(r) {
  if (!r) return 'text-muted'
  if ((r.delta || 0) > 0) return 'text-rise'
  if ((r.delta || 0) < 0) return 'text-fall'
  return 'text-gray-300'
}
function cardCls(x) {
  // 按阶段着色：启动=红边、主升=蓝边、高位=黄边、退潮=灰边
  if (sel.value && sel.value.code === x.code) return 'border-accent bg-accent/10'
  const st = stageOf(x.code)
  if (st === 'emerging') return 'border-rise/30 hover:border-rise/60 text-gray-300'
  if (st === 'leading') return 'border-accent/30 hover:border-accent/60 text-gray-300'
  if (st === 'crowded') return 'border-amber-500/30 hover:border-amber-500/60 text-gray-300'
  if (st === 'fading') return 'border-border/50 hover:border-border text-muted'
  return 'border-border/40 hover:border-border text-gray-300'
}
const stageMap = ref({})
function stageOf(code) {
  return stageMap.value[code] || ''
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = { plate_type: plateType.value }
    if (day.value) params.day = day.value
    const { data: d } = await getTopicRotationDashboard(params)
    if (!d || d.ok === false) {
      error.value = (d && d.error) || '无数据'
      data.value = {}
    } else {
      data.value = d
      if (!day.value && d.date) day.value = d.date
      const m = {}
      for (const k of ['emerging', 'leading', 'crowded', 'fading']) {
        for (const x of (d.stages?.[k] || [])) m[x.code] = k
      }
      stageMap.value = m
    }
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  histErr.value = ''
  try {
    const params = { days: histDays.value, top: histTop.value, plate_type: plateType.value }
    const { data: d } = await getTopicRotationHistory(params)
    if (!d || d.ok === false) {
      histErr.value = (d && d.error) || '无数据'
      hist.value = { dates: [], items: [] }
      return
    }
    hist.value = d
    await nextTick()
    drawChart()
  } catch (e) {
    histErr.value = e?.response?.data?.detail || e.message || '加载失败'
  }
}

function drawChart() {
  if (!chartRef.value) return
  const d = hist.value
  if (!d.dates?.length || !d.items?.length) {
    if (chart) { chart.dispose(); chart = null }
    return
  }
  if (!chart) chart = echarts.init(chartRef.value, 'dark')
  const dates = d.dates.map(x => x.slice(5))
  // 颜色：最新在前 3 = 红/橙/黄；前 5 = 蓝；其余灰
  const COLORS = ['#f85149', '#ff7b72', '#ffa657', '#58a6ff', '#58a6ff']
  const series = d.items.map((it, i) => {
    const hot = i < COLORS.length && it.last
    return {
      name: it.name,
      type: 'line',
      data: it.ranks,
      connectNulls: false,
      smooth: 0.25,
      symbol: 'circle',
      symbolSize: hot ? 6 : 4,
      lineStyle: { width: hot ? 2.2 : 1, opacity: hot ? 1 : 0.45,
                   color: hot ? COLORS[i] : '#6e7681' },
      itemStyle: { color: hot ? COLORS[i] : '#6e7681' },
      emphasis: { focus: 'series', lineStyle: { width: 3 } },
      endLabel: {
        show: i < 8,
        formatter: it.name,
        color: hot ? COLORS[i] : '#8b949e',
        fontSize: 10,
      },
    }
  })
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#8b949e' },
    grid: { left: 34, right: 78, top: 14, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(22,27,34,.96)',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 11 },
      formatter(ps) {
        const head = ps.length ? ps[0].axisValue : ''
        const rows = ps
          .filter(p => p.value !== null && p.value !== undefined)
          .sort((a, b) => a.value - b.value)
          .slice(0, 12)
          .map(p => `${p.marker} ${p.seriesName} <b>#${p.value}</b>`)
        return head + '<br/>' + rows.join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#30363d' } },
      axisLabel: { fontSize: 10 },
    },
    yAxis: {
      type: 'value',
      inverse: true,          // ★ 名次 1 在最上面
      min: 1,
      max: histTop.value,
      splitLine: { lineStyle: { color: '#21262d' } },
      axisLabel: { fontSize: 10, formatter: v => '#' + v },
    },
    series,
  }, true)
  chart.resize()
}

async function pick(x) {
  sel.value = x
  stk.value = {}
  stkErr.value = ''
  stkLoading.value = true
  try {
    const params = { code: x.code, plate_type: plateType.value, top: 12 }
    if (day.value) params.day = day.value
    const { data: d } = await getTopicRotationConstituents(params)
    if (!d || d.ok === false) stkErr.value = (d && d.error) || '无数据'
    else stk.value = d
  } catch (e) {
    stkErr.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    stkLoading.value = false
  }
}

async function loadVerify() {
  if (vLoading.value) return
  vLoading.value = true
  try {
    const out = []
    for (const [lv, label] of [[1, '强势'], [0, '中性'], [-1, '弱势']]) {
      try {
        const { data: r } = await getTopicRotationVerify({
          plate_type: plateType.value, horizon: 5, min_days: 10, regime: lv,
        })
        out.push(r && r.ok
          ? { label, excess: r.avg_excess_pct, win: r.win_rate, n: r.n_events,
              valid: r.valid, tradable: r.tradable }
          : { label, excess: null, win: null, n: 0, valid: false, tradable: false })
      } catch {
        out.push({ label, excess: null, win: null, n: 0, valid: false, tradable: false })
      }
    }
    vRows.value = out
  } finally {
    vLoading.value = false
  }
}

function verdictText(r) {
  if (r.tradable) return '可用'
  if (r.valid) return '显著'
  return '无'
}
function verdictCls(r) {
  if (r.tradable) return 'bg-rise/15 text-rise'
  if (r.valid) return 'bg-amber-500/15 text-amber-400'
  return 'bg-white/5 text-muted'
}

async function reload() {
  await Promise.all([load(), loadHistory()])
  if (sel.value) {
    const cur = (data.value.top || []).find(x => x.code === sel.value.code)
    if (cur) pick(cur)
  }
}

function onResize() { chart && chart.resize() }

onMounted(async () => {
  await reload()
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) { chart.dispose(); chart = null }
})
</script>
