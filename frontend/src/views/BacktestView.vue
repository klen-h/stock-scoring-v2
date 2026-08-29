<template>
  <div class="max-w-[1600px] mx-auto px-4 py-4 space-y-4">
    <!-- 页头 -->
    <div class="flex items-center justify-between flex-wrap gap-2">
      <div>
        <h1 class="text-lg font-bold text-gray-100">🧪 回测中心</h1>
        <p class="text-xs text-muted mt-0.5">
          三类策略实时回测 + 每周五 16:00 自动生成的周度报告归档 · 信号源于 strategy_results（每日盘后扫描），行情源于 backtest_prices（每日 15:40 回填）
        </p>
      </div>
    </div>

    <!-- 子 Tab -->
    <div class="bg-card border border-border rounded-lg p-3">
      <div class="flex gap-2 flex-wrap">
        <button v-for="tab in tabs" :key="tab.key" @click="switchTab(tab.key)"
          :class="activeTab === tab.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
          class="px-3 py-1 rounded text-xs transition-colors">
          {{ tab.label }}
        </button>
      </div>
    </div>

    <!-- ══════════ Tab 1: 策略回测 ══════════ -->
    <div v-if="activeTab === 'strategy'" class="space-y-4">
      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between gap-3">
          <div class="text-sm font-semibold">📈 策略回测（历史数据验证）</div>
          <select v-model="backtestName" @change="loadBacktest"
                  class="bg-white/5 border border-border rounded px-2 py-1 text-xs text-gray-200">
            <option value="signals">LLM 信号绩效追踪</option>
            <option value="warfare">战法选股回测</option>
            <option value="macro">宏观方向分回测</option>
          </select>
        </div>
        <div v-if="backtestLoading" class="py-8 text-center text-sm text-muted">⏳ 回测计算中…（战法回测需加载历史行情，可能稍慢）</div>
        <div v-else-if="backtestError" class="py-4 text-center text-sm text-fall">{{ backtestError }}</div>
        <template v-else-if="backtest.metrics || backtest.total">
          <div v-if="backtest.sample_note" class="mt-2 text-xs text-amber-400">⚠️ {{ backtest.sample_note }}</div>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mt-3">
            <StatBox label="总收益率" :value="pct(backtest.metrics?.total_return)" :color="pnlColor(backtest.metrics?.total_return)" />
            <StatBox label="年化收益" :value="pct(backtest.metrics?.annual_return)" :color="pnlColor(backtest.metrics?.annual_return)" />
            <StatBox label="最大回撤" :value="pct(backtest.metrics?.max_drawdown)" color="text-fall" />
            <StatBox label="夏普比率" :value="backtest.metrics?.sharpe ?? '—'" :color="backtest.metrics?.sharpe >= 1 ? 'text-rise' : ''" />
            <StatBox label="胜率" :value="backtest.metrics?.win_rate != null ? backtest.metrics.win_rate + '%' : '—'"
              :color="backtest.metrics?.win_rate >= 50 ? 'text-rise' : backtest.metrics?.win_rate != null ? 'text-fall' : ''" />
            <StatBox label="盈亏比" :value="backtest.metrics?.profit_factor ?? '—'" />
            <StatBox label="基准(沪深300)" :value="pct(backtest.metrics?.benchmark_return)" :color="pnlColor(backtest.metrics?.benchmark_return)" />
            <StatBox label="超额收益" :value="pct(backtest.metrics?.excess_return)" :color="pnlColor(backtest.metrics?.excess_return)" />
          </div>
          <div v-if="backtest.metrics" class="mt-2 text-xs text-muted">
            交易 {{ backtest.metrics.trade_count }} 笔 · 平均单笔 {{ backtest.metrics.avg_pnl_pct }}% · 平均持仓 {{ backtest.metrics.avg_hold_days }} 天 · 样本 {{ backtest.metrics.period_days }} 天
          </div>
          <!-- LLM 信号：按来源分组 -->
          <div v-if="Object.keys(backtest.by_source || {}).length" class="mt-3">
            <div class="text-xs text-muted mb-1">按来源分组：</div>
            <table class="w-full text-sm">
              <thead><tr class="border-b border-border text-muted text-xs">
                <th class="text-left py-1 px-2">来源</th><th class="text-right py-1 px-2">平仓</th>
                <th class="text-right py-1 px-2">胜率</th><th class="text-right py-1 px-2">均收益%</th><th class="text-right py-1 px-2">盈亏比</th>
              </tr></thead>
              <tbody>
                <tr v-for="(g, k) in backtest.by_source" :key="k" class="border-b border-border/50">
                  <td class="py-1 px-2">{{ g.name }}</td>
                  <td class="py-1 px-2 text-right font-mono">{{ g.closed }}</td>
                  <td class="py-1 px-2 text-right font-mono">{{ g.win_rate }}%</td>
                  <td class="py-1 px-2 text-right font-mono">{{ g.avg_profit_pct }}</td>
                  <td class="py-1 px-2 text-right font-mono">{{ g.profit_factor }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- 资产曲线概要 -->
          <div v-if="backtest.curve_summary" class="mt-3 text-xs text-muted bg-white/5 border border-border rounded p-3">
            资产曲线：{{ backtest.curve_summary.start }} ~ {{ backtest.curve_summary.end }}（{{ backtest.curve_summary.days }} 天）·
            净值 {{ backtest.curve_summary.start_nav }} → {{ backtest.curve_summary.end_nav }} ·
            峰值 {{ backtest.curve_summary.peak_date }} {{ backtest.curve_summary.peak_nav }} ·
            最大回撤段 {{ backtest.curve_summary.mdd_from }} → {{ backtest.curve_summary.mdd_to }}（{{ pct(backtest.curve_summary.mdd_ratio) }}）
          </div>
          <!-- 战法：70/30 切分防过拟合 -->
          <div v-if="backtest.in_sample?.metrics || backtest.out_sample?.metrics" class="grid md:grid-cols-2 gap-3 mt-3">
            <div class="bg-white/5 border border-border rounded p-3">
              <div class="text-xs text-muted mb-1">前 70% 样本（样本内）</div>
              <div class="text-sm">总收益 {{ pct(backtest.in_sample?.metrics?.total_return) }} · 胜率 {{ backtest.in_sample?.metrics?.win_rate }}% · 夏普 {{ backtest.in_sample?.metrics?.sharpe }}</div>
            </div>
            <div class="bg-white/5 border border-border rounded p-3">
              <div class="text-xs text-muted mb-1">后 30% 样本（样本外）</div>
              <div class="text-sm">总收益 {{ pct(backtest.out_sample?.metrics?.total_return) }} · 胜率 {{ backtest.out_sample?.metrics?.win_rate }}% · 夏普 {{ backtest.out_sample?.metrics?.sharpe }}</div>
            </div>
          </div>
          <!-- 按战法分组胜率（验证各战法有效性，点击行=筛选该战法） -->
          <div v-if="Object.keys(backtest.by_strategy || {}).length" class="mt-3">
            <div class="text-xs text-muted mb-1">按战法分组统计（点击行筛选下方逐笔明细）：</div>
            <table class="w-full text-sm">
              <thead><tr class="border-b border-border text-muted text-xs">
                <th class="text-left py-1 px-2">战法</th><th class="text-right py-1 px-2">成交</th>
                <th class="text-right py-1 px-2">胜率</th><th class="text-right py-1 px-2">平均单笔%</th>
                <th class="text-right py-1 px-2">盈亏比</th><th class="text-right py-1 px-2">累计单笔%</th>
                <th class="text-right py-1 px-2">平均持仓</th>
              </tr></thead>
              <tbody>
                <tr v-for="(s, k) in backtest.by_strategy" :key="k"
                    class="border-b border-border/50 cursor-pointer hover:bg-white/5 transition-colors"
                    :class="strategyFilter === k ? 'bg-accent/10' : ''"
                    @click="strategyFilter = strategyFilter === k ? '' : k">
                  <td class="py-1 px-2" :class="strategyFilter === k ? 'text-accent font-medium' : ''">{{ k }}</td>
                  <td class="py-1 px-2 text-right font-mono">{{ s.trades }}</td>
                  <td class="py-1 px-2 text-right font-mono" :class="s.win_rate >= 50 ? 'text-rise' : s.win_rate != null ? 'text-fall' : ''">{{ s.win_rate != null ? s.win_rate + '%' : '—' }}</td>
                  <td class="py-1 px-2 text-right font-mono" :class="pnlColor(s.avg_pnl_pct)">{{ s.avg_pnl_pct }}</td>
                  <td class="py-1 px-2 text-right font-mono">{{ s.profit_factor }}</td>
                  <td class="py-1 px-2 text-right font-mono" :class="pnlColor(s.total_pnl_pct)">{{ s.total_pnl_pct }}</td>
                  <td class="py-1 px-2 text-right font-mono text-muted">{{ s.avg_hold_days }} 天</td>
                </tr>
              </tbody>
            </table>
          </div>
          <!-- 战法：逐笔 Top10 -->
          <div v-if="backtest.top_trades?.length" class="mt-3">
            <div class="flex items-center justify-between gap-3 mb-1">
              <div class="text-xs text-muted">逐笔交易 Top10（按单笔收益绝对值）：</div>
              <select v-if="Object.keys(backtest.by_strategy || {}).length" v-model="strategyFilter"
                      class="bg-white/5 border border-border rounded px-2 py-0.5 text-xs text-gray-200">
                <option value="">全部战法</option>
                <option v-for="(s, k) in backtest.by_strategy" :key="k" :value="k">{{ k }}</option>
              </select>
            </div>
            <table class="w-full text-sm">
              <thead><tr class="border-b border-border text-muted text-xs">
                <th class="text-left py-1 px-2">股票</th><th class="text-left py-1 px-2">战法</th>
                <th class="text-center py-1 px-2">方向</th>
                <th class="text-left py-1 px-2">入场</th><th class="text-left py-1 px-2">出场</th>
                <th class="text-right py-1 px-2">单笔%</th><th class="text-left py-1 px-2">原因</th>
              </tr></thead>
              <tbody>
                <tr v-for="(t, i) in filteredTopTrades" :key="i" class="border-b border-border/50">
                  <td class="py-1 px-2">
                    <div class="cursor-pointer hover:text-accent transition-colors inline-block"
                         @click="goDetail(t.code)" :title="'查看详情：' + t.name">{{ t.name }}</div>
                    <div class="text-muted font-mono text-xs">
                      <a :href="getXueqiuUrl(t.code)" target="_blank" rel="noopener"
                         class="hover:text-accent hover:underline" title="在雪球查看">{{ t.code }}</a>
                    </div>
                  </td>
                  <td class="py-1 px-2 text-xs text-muted">{{ t.strategy || '—' }}</td>
                  <td class="py-1 px-2 text-center text-xs" :class="t.direction > 0 ? 'text-rise' : 'text-fall'">{{ t.direction > 0 ? '多' : '空' }}</td>
                  <td class="py-1 px-2 font-mono text-xs">{{ t.entry_date }}</td>
                  <td class="py-1 px-2 font-mono text-xs">{{ t.exit_date }}</td>
                  <td class="py-1 px-2 text-right font-mono" :class="pnlColor(t.pnl_pct)">{{ t.pnl_pct }}</td>
                  <td class="py-1 px-2 text-xs text-muted">{{ t.exit_reason }}</td>
                </tr>
                <tr v-if="!filteredTopTrades.length">
                  <td colspan="7" class="py-3 text-center text-xs text-muted">该战法在 Top10 中无成交</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>
        <div v-else class="py-6 text-center text-sm text-muted">{{ backtest.sample_note || '暂无回测数据' }}</div>
      </div>
    </div>

    <!-- ══════════ Tab 2: 周度回测报告（markdown 归档） ══════════ -->
    <div v-if="activeTab === 'report'" class="space-y-4">
      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between gap-3">
          <div class="text-sm font-semibold">📄 周度回测报告</div>
          <select v-model="reportName" @change="loadReportContent"
                  class="bg-white/5 border border-border rounded px-2 py-1 text-xs text-gray-200 max-w-[65%]">
            <option v-for="r in reportOptions" :key="r.name" :value="r.name">{{ r.label }}</option>
          </select>
        </div>
        <div v-if="reportLoading" class="py-6 text-center text-sm text-muted">加载中…</div>
        <div v-else-if="reportError" class="py-4 text-center text-sm text-fall">{{ reportError }}</div>
        <div v-else-if="reportHtml" class="md-body max-h-[70vh] overflow-y-auto mt-3" v-html="reportHtml"></div>
        <div v-else class="py-6 text-center text-sm text-muted">暂无报告（每周五 16:00 自动生成）</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import { getBacktestStrategy, getBacktestReports, getBacktestReportContent } from '../api'
import { getXueqiuUrl } from '../composables/stockUtils'

const router = useRouter()

function goDetail(code) {
  router.push(`/stock/${code}`)
}

const tabs = [
  { key: 'strategy', label: '策略回测' },
  { key: 'report', label: '周度报告' },
]
const activeTab = ref('strategy')

function switchTab(key) {
  activeTab.value = key
  if (key === 'report' && !reportHtml.value && !reportError.value) loadReportContent()
}

// ── 策略回测（后端 10 分钟缓存）──
const backtestName = ref('warfare')
const backtest = ref({})
const backtestLoading = ref(false)
const backtestError = ref('')

// 战法筛选（分组统计表点行联动 / 下拉选择）
const strategyFilter = ref('')
const filteredTopTrades = computed(() => {
  const list = backtest.value.top_trades || []
  if (!strategyFilter.value) return list
  return list.filter(t => (t.strategy || '未知') === strategyFilter.value)
})

function pct(v) {
  if (v == null) return '—'
  return (v > 0 ? '+' : '') + v + '%'
}
function pnlColor(v) {
  if (v == null) return ''
  return v > 0 ? 'text-rise' : v < 0 ? 'text-fall' : ''
}

async function loadBacktest() {
  strategyFilter.value = ''   // 切换策略时重置战法筛选
  backtestLoading.value = true
  backtestError.value = ''
  try {
    const { data } = await getBacktestStrategy(backtestName.value)
    if (data.error) { backtestError.value = data.error; backtest.value = {} }
    else backtest.value = data
  } catch (e) {
    backtestError.value = '回测接口不可用：' + (e?.message || e)
  } finally {
    backtestLoading.value = false
  }
}

// ── 周度回测报告（markdown 归档，scheduler 每周五 16:00 生成）──
const reportName = ref('latest.md')
const reportOptions = ref([{ name: 'latest.md', label: '最新（latest.md）' }])
const reportHtml = ref('')
const reportLoading = ref(false)
const reportError = ref('')

const mdRenderer = new MarkdownIt({ html: false, linkify: true, breaks: true })

async function loadReportList() {
  try {
    const { data } = await getBacktestReports()
    const list = (data.reports || []).map(r => ({
      name: r.name,
      label: `${r.mtime} · ${r.name.replace('backtest_report', '').replace('.md', '')}`,
    }))
    if (list.length) {
      reportOptions.value = [{ name: 'latest.md', label: '最新（latest.md）' }, ...list]
    }
  } catch { /* 列表失败不阻塞，保留 latest.md 默认项 */ }
}

async function loadReportContent() {
  reportLoading.value = true
  reportError.value = ''
  try {
    const { data } = await getBacktestReportContent(reportName.value)
    if (data.error) {
      reportError.value = data.error
      reportHtml.value = ''
    } else {
      reportHtml.value = mdRenderer.render(data.content || '')
    }
  } catch (e) {
    reportError.value = '报告加载失败：' + (e?.message || e)
  } finally {
    reportLoading.value = false
  }
}

onMounted(() => {
  loadBacktest()      // 默认 warfare（数据最有观察价值）
  loadReportList()
})
</script>

<script>
// 简单统计盒（内联组件，与 MonitorView 同风格）
export default {
  components: {
    StatBox: {
      props: ['label', 'value', 'color'],
      template: `
        <div class="bg-card border border-border rounded-lg p-3 text-center">
          <div class="text-xs text-muted">{{ label }}</div>
          <div class="text-lg font-bold mt-1 font-mono" :class="color || 'text-gray-200'">{{ value }}</div>
        </div>
      `,
    },
  },
}
</script>

<style scoped>
/* 周报 markdown 渲染样式（markdown-it 输出，与 MonitorView 复盘区一致） */
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
.md-body :deep(h1) { font-size: 1rem; }
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
.md-body :deep(table) { border-collapse: collapse; margin: 0.5rem 0; width: 100%; }
.md-body :deep(th),
.md-body :deep(td) { border: 1px solid #374151; padding: 0.25rem 0.5rem; font-size: 0.75rem; }
</style>
