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

    <!-- 跟主线能赚钱吗？（收益验证，2026-09-09 起积累） -->
    <div v-if="perf.ok" class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-2 mb-3">
        <h2 class="text-sm font-bold text-gray-200">🎯 跟主线能赚钱吗？</h2>
        <span class="text-[10px] text-muted">
          窗口 {{ perf.window?.[0] }} ~ {{ perf.window?.[1] }}（{{ perf.days }} 日）· 主线候选 {{ perf.mainline_pairs }} (次)
        </span>
      </div>
      <p class="text-[11px] text-muted mb-3">
        主线候选 = 当日 Top50 中所属行业扎堆（≥2 只）的股票。对照：同日全部 Top50（同池剔除入选偏差）与沪深300。
      </p>
      <div class="overflow-x-auto">
        <table class="w-full text-xs">
          <thead>
            <tr class="text-muted border-b border-border">
              <th class="text-left py-1.5 px-2 font-normal">组合</th>
              <th v-for="h in perf.horizons" :key="h" class="text-right py-1.5 px-2 font-normal">持有 {{ h }} 日</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in perfRows" :key="row.key" class="border-b border-border/50 last:border-b-0">
              <td class="py-2 px-2">
                <span class="font-medium" :class="row.cls">{{ row.label }}</span>
              </td>
              <td v-for="h in perf.horizons" :key="h" class="text-right py-2 px-2 font-mono">
                <div :class="(perfData(row.key)[h]?.avg_ret ?? 0) >= 0 ? 'text-rise' : 'text-fall'">
                  {{ perfData(row.key)[h] ? fmtRet(perfData(row.key)[h].avg_ret) : '—' }}
                </div>
                <div class="text-[10px] text-muted">
                  {{ perfData(row.key)[h] ? `胜率${perfData(row.key)[h].win_rate}% · n=${perfData(row.key)[h].n}` : '' }}
                </div>
              </td>
            </tr>
            <tr class="bg-white/[0.02]">
              <td class="py-2 px-2 font-medium text-accent">超额 vs Top50</td>
              <td v-for="h in perf.horizons" :key="'e'+h" class="text-right py-2 px-2 font-mono font-bold"
                :class="(perf.excess_vs_top50?.[String(h)] ?? 0) >= 0 ? 'text-rise' : 'text-fall'">
                {{ fmtRet(perf.excess_vs_top50?.[String(h)]) }}
              </td>
            </tr>
            <tr>
              <td class="py-2 px-2 font-medium text-accent">超额 vs 沪深300</td>
              <td v-for="h in perf.horizons" :key="'h'+h" class="text-right py-2 px-2 font-mono font-bold"
                :class="(perf.excess_vs_hs300?.[String(h)] ?? 0) >= 0 ? 'text-rise' : 'text-fall'">
                {{ fmtRet(perf.excess_vs_hs300?.[String(h)]) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-[11px] text-muted mt-2">
        判读：超额持续为正 → 「跟主线」有效，主线榜可作选股入口；超额≈0 或为负 → 主线只是事后描述，不构成选股优势。
        数据 09-09 起积累，样本 &lt; 20 的窗口仅供参考。
      </p>
    </div>
  </div>
</template>

<script setup>
import { ref, watch, onMounted } from 'vue'
import { getMainlineSummary, buildMainlineDate, pushMainlineReport, getMainlinePerformance } from '../api'

// ===== 跟主线能赚钱吗？（收益验证）=====
const perf = ref({})
const fmtRet = (v) => v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%'
const perfRows = [
  { key: 'ml', label: '主线候选', cls: 'text-gray-100' },
  { key: 't50', label: 'Top50 全体', cls: 'text-muted' },
  { key: 'hs', label: '沪深300', cls: 'text-muted' },
]
const perfData = (key) => perf.value[key === 'ml' ? 'mainline' : key === 't50' ? 'all_top50' : 'hs300'] || {}
async function loadPerf() {
  try {
    const { data } = await getMainlinePerformance(30)
    perf.value = data?.ok ? data : {}
  } catch { perf.value = {} }
}

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
onMounted(() => { load(); loadPerf() })
</script>
