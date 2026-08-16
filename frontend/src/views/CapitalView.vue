<template>
  <div class="fade-in space-y-4">
    <!-- 北向资金概览 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-bold">北向资金</h2>
        <span class="text-xs text-muted">{{ nb.time ? '截至 ' + nb.time : '—' }}</span>
      </div>
      <div class="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div class="p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">沪股通净流入</div>
          <div class="text-xl font-bold mt-1 font-mono" :class="flowClass(nb.sh_net)">{{ formatYuan(nb.sh_net) }}</div>
        </div>
        <div class="p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">深股通净流入</div>
          <div class="text-xl font-bold mt-1 font-mono" :class="flowClass(nb.sz_net)">{{ formatYuan(nb.sz_net) }}</div>
        </div>
        <div class="p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">北向合计净流入</div>
          <div class="text-xl font-bold mt-1 font-mono" :class="flowClass(nb.total_net)">{{ formatYuan(nb.total_net) }}</div>
        </div>
      </div>
      <div v-if="nb.total_net === 0 && nb.time" class="text-xs text-muted mt-3">
        * 合计净流入为 0，可能是非交易时段或休市。
      </div>
    </div>

    <!-- Tab + 操作栏 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex gap-2 flex-wrap">
          <button v-for="tab in tabs" :key="tab.key" @click="switchTab(tab.key)"
            :class="activeTab === tab.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
            class="px-3 py-1 rounded text-xs transition-colors">
            {{ tab.label }}
          </button>
        </div>
        <div class="flex gap-2">
          <select v-if="activeTab === 'stock'" v-model="stockOrder" @change="loadData"
            class="bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300">
            <option value="desc">主力净流入最多</option>
            <option value="asc">主力净流出最多</option>
          </select>
          <button @click="loadData"
            class="bg-white/5 border border-border rounded px-3 py-1 text-xs text-gray-300 hover:bg-white/10">
            刷新
          </button>
        </div>
      </div>
    </div>

    <!-- 数据表格 -->
    <div class="bg-card border border-border rounded-lg overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-muted text-xs">
              <th class="text-left py-2.5 px-3">排名</th>
              <th class="text-left py-2.5 px-3">代码</th>
              <th class="text-left py-2.5 px-3">名称</th>
              <th v-if="activeTab === 'stock'" class="text-right py-2.5 px-3">最新价</th>
              <th class="text-right py-2.5 px-3">涨跌幅</th>
              <th class="text-right py-2.5 px-3">主力净流入</th>
              <th class="text-right py-2.5 px-3">净流入占比</th>
              <th class="text-right py-2.5 px-3">超大单</th>
              <th class="text-right py-2.5 px-3">大单</th>
              <th class="text-right py-2.5 px-3">中单</th>
              <th class="text-right py-2.5 px-3">小单</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(item, idx) in tableData" :key="item.code"
              class="border-b border-border/50 hover:bg-white/3 transition-colors"
              :class="activeTab === 'stock' ? 'cursor-pointer' : ''"
              @click="activeTab === 'stock' ? goDetail(item.code) : null">
              <td class="py-2 px-3 text-muted font-mono text-xs">{{ idx + 1 }}</td>
              <td class="py-2 px-3 font-mono text-xs" :class="activeTab === 'stock' ? 'text-accent' : 'text-muted'">
                <a v-if="activeTab === 'stock'" :href="getXueqiuUrl(item.code)" target="_blank" rel="noopener"
                   @click.stop class="hover:underline" title="在雪球查看">{{ item.code }}</a>
                <span v-else>{{ item.code }}</span>
              </td>
              <td class="py-2 px-3 font-medium">{{ item.name }}</td>
              <td v-if="activeTab === 'stock'" class="py-2 px-3 text-right font-mono text-muted">{{ item.price }}</td>
              <td class="py-2 px-3 text-right font-mono" :class="pctClass(item.change_pct)">
                {{ fmtPct(item.change_pct) }}
              </td>
              <td class="py-2 px-3 text-right font-mono font-bold" :class="flowClass(item.net_inflow)">
                {{ formatYuan(item.net_inflow) }}
              </td>
              <td class="py-2 px-3 text-right font-mono" :class="flowClass(item.net_inflow_pct)">
                {{ fmtPct(item.net_inflow_pct) }}
              </td>
              <td class="py-2 px-3 text-right font-mono text-xs" :class="flowClass(item.super_large_net)">{{ formatYuan(item.super_large_net) }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs" :class="flowClass(item.large_net)">{{ formatYuan(item.large_net) }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs" :class="flowClass(item.medium_net)">{{ formatYuan(item.medium_net) }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs" :class="flowClass(item.small_net)">{{ formatYuan(item.small_net) }}</td>
            </tr>
            <tr v-if="!tableData.length">
              <td colspan="11" class="py-12 text-center text-muted">
                {{ loading ? '数据加载中...' : error ? '加载失败，请稍后重试' : '暂无数据' }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="tableData.length" class="px-3 py-2 border-t border-border text-xs text-muted">
        共 {{ total }} 条 · 数据来源：东方财富
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getIndustryFlow, getConceptFlow, getMainFlow, getNorthboundFlow } from '../api'
import { getXueqiuUrl } from '../composables/stockUtils'

const router = useRouter()

const tabs = [
  { key: 'industry', label: '行业板块资金流' },
  { key: 'concept', label: '概念板块资金流' },
  { key: 'stock', label: '个股主力资金' },
]

const activeTab = ref('industry')
const stockOrder = ref('desc')
const tableData = ref([])
const total = ref(0)
const loading = ref(false)
const error = ref(false)
const nb = ref({})   // 北向资金对象

async function loadNorthbound() {
  try {
    const { data } = await getNorthboundFlow()
    nb.value = data || {}
  } catch (e) {
    console.error('北向资金加载失败', e)
  }
}

async function loadData() {
  loading.value = true
  error.value = false
  try {
    let res
    if (activeTab.value === 'industry') {
      res = await getIndustryFlow({ limit: 200 })
    } else if (activeTab.value === 'concept') {
      res = await getConceptFlow({ limit: 200 })
    } else {
      res = await getMainFlow({ order: stockOrder.value, limit: 100 })
    }
    const d = res.data
    tableData.value = d.data || []
    total.value = d.total || 0
  } catch (e) {
    console.error(e)
    error.value = true
    tableData.value = []
  } finally {
    loading.value = false
  }
}

function switchTab(key) {
  activeTab.value = key
  loadData()
}

function goDetail(code) {
  router.push(`/stock/${code}`)
}

// ── 格式化工具 ──
// 元 → 亿/万（A 股习惯单位）
function formatYuan(v) {
  const n = parseFloat(v)
  if (n === 0 || isNaN(n)) return '0'
  const abs = Math.abs(n)
  const sign = n < 0 ? '-' : ''
  if (abs >= 1e8) return sign + (abs / 1e8).toFixed(2) + '亿'
  if (abs >= 1e4) return sign + (abs / 1e4).toFixed(2) + '万'
  return sign + abs.toFixed(0)
}

function fmtPct(v) {
  const n = parseFloat(v)
  if (isNaN(n) || n === 0) return '0.00%'
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}

// 涨跌幅配色：A 股涨红跌绿
function pctClass(v) {
  const n = parseFloat(v)
  if (isNaN(n) || n === 0) return 'text-muted'
  return n > 0 ? 'text-rise' : 'text-fall'
}

// 资金净流入配色：正(流入)红、负(流出)绿
function flowClass(v) {
  const n = parseFloat(v)
  if (isNaN(n) || n === 0) return 'text-muted'
  return n > 0 ? 'text-rise' : 'text-fall'
}

onMounted(() => {
  loadNorthbound()
  loadData()
})
</script>
