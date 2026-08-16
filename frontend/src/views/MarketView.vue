<template>
  <div class="fade-in space-y-4">
    <div class="flex items-center justify-between">
      <h2 class="text-lg font-semibold">全A股实时行情</h2>
      <div class="flex gap-2">
        <select v-model="sortBy" @change="fetchData" class="bg-card border border-border rounded px-3 py-1.5 text-sm text-gray-200 focus:outline-none">
          <option v-for="col in sortOptions" :key="col.value" :value="col.value">{{ col.label }}</option>
        </select>
        <button @click="order = order === 'desc' ? 'asc' : 'desc'; fetchData()"
          class="bg-card border border-border rounded px-3 py-1.5 text-sm text-gray-200 hover:bg-white/5">
          {{ order === 'desc' ? '降序' : '升序' }}
        </button>
      </div>
    </div>
    <div v-if="tableData.length" class="bg-card border border-border rounded-lg overflow-hidden">
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-muted text-xs">
              <th class="px-3 py-2.5 text-left">代码</th>
              <th class="px-3 py-2.5 text-left">名称</th>
              <th class="px-3 py-2.5 text-right">最新价</th>
              <th class="px-3 py-2.5 text-right">涨跌幅</th>
              <th class="px-3 py-2.5 text-right">涨跌额</th>
              <th class="px-3 py-2.5 text-right">成交量</th>
              <th class="px-3 py-2.5 text-right">成交额</th>
              <th class="px-3 py-2.5 text-right">振幅</th>
              <th class="px-3 py-2.5 text-right">换手率</th>
              <th class="px-3 py-2.5 text-right">市盈率</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in tableData" :key="row.code"
              class="border-b border-border/50 hover:bg-white/3 cursor-pointer transition-colors"
              @click="$router.push(`/stock/${row.code}`)">
              <td class="px-3 py-2 text-muted font-mono">
                <a :href="getXueqiuUrl(row.code)" target="_blank" rel="noopener"
                   @click.stop
                   class="hover:text-accent hover:underline"
                   title="在雪球查看">{{ row.code }}</a>
              </td>
              <td class="px-3 py-2 font-medium">{{ row.name }}</td>
              <td class="px-3 py-2 text-right font-mono">{{ row.price }}</td>
              <td class="px-3 py-2 text-right font-mono" :class="row.change_pct >= 0 ? 'text-rise' : 'text-fall'">
                {{ row.change_pct >= 0 ? '+' : '' }}{{ row.change_pct }}%
              </td>
              <td class="px-3 py-2 text-right font-mono" :class="row.change_amt >= 0 ? 'text-rise' : 'text-fall'">
                {{ row.change_amt }}
              </td>
              <td class="px-3 py-2 text-right text-muted font-mono">{{ formatVol(row.volume) }}</td>
              <td class="px-3 py-2 text-right text-muted font-mono">{{ formatAmt(row.amount) }}</td>
              <td class="px-3 py-2 text-right text-muted">{{ row.amplitude }}%</td>
              <td class="px-3 py-2 text-right text-muted">{{ row.turnover_rate }}%</td>
              <td class="px-3 py-2 text-right text-muted">{{ row.pe }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="flex items-center justify-between px-3 py-2 border-t border-border">
        <span class="text-xs text-muted">共 {{ total }} 条</span>
        <div class="flex gap-1">
          <button @click="page = Math.max(1, page - 1); fetchData()" :disabled="page <= 1"
            class="px-2.5 py-1 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">上一页</button>
          <span class="px-3 py-1 text-xs text-muted">{{ page }} / {{ totalPages }}</span>
          <button @click="page = Math.min(totalPages, page + 1); fetchData()" :disabled="page >= totalPages"
            class="px-2.5 py-1 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">下一页</button>
        </div>
      </div>
    </div>
    <div v-else class="bg-card border border-border rounded-lg p-12 text-center text-muted">
      数据加载中，首次需扫描全量股票代码，请稍候...<br>
      <span class="text-xs mt-2 block">如长时间未加载，请检查后端日志</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getMarketRealtime } from '../api'
import { getXueqiuUrl } from '../composables/stockUtils'

const tableData = ref([])
const total = ref(0)
const page = ref(1)
const size = 50
const sortBy = ref('change_pct')
const order = ref('desc')

const sortOptions = [
  { value: 'change_pct', label: '涨跌幅' },
  { value: 'amount', label: '成交额' },
  { value: 'turnover_rate', label: '换手率' },
  { value: 'amplitude', label: '振幅' },
  { value: 'price', label: '最新价' },
  { value: 'volume', label: '成交量' },
]

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size)))

function formatVol(v) {
  const n = parseFloat(v)
  if (!n) return '-'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return n.toFixed(0)
}

function formatAmt(v) {
  const n = parseFloat(v)
  if (!n) return '-'
  if (n >= 1e12) return (n / 1e12).toFixed(2) + '万亿'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (n >= 1e4) return (n / 1e4).toFixed(1) + '万'
  return n.toFixed(0)
}

async function fetchData() {
  try {
    const { data } = await getMarketRealtime({ page: page.value, size, sort_by: sortBy.value, order: order.value })
    tableData.value = data.data || []
    total.value = data.total || 0
  } catch (e) { console.error(e) }
}

onMounted(fetchData)
</script>
