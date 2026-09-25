<template>
  <div class="p-4 space-y-4">
    <!-- ── 头部：日期切换 + 当日统计 ── -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div class="text-sm font-semibold">龙虎榜
          <span class="text-[10px] text-muted font-normal">
            （数据源 zzshare · 每日 17:45 同步 · <b>读库非实时</b>）</span>
        </div>
        <div class="flex items-center gap-2">
          <select v-model="date" @change="load()"
                  class="bg-background border border-border rounded px-2 py-1 text-xs">
            <option v-for="d in dates" :key="d" :value="d">{{ d }}</option>
          </select>
          <button @click="load()" :disabled="loading"
                  class="px-2 py-1 rounded bg-accent/20 text-accent text-xs disabled:opacity-50">
            {{ loading ? '加载中…' : '刷新' }}
          </button>
        </div>
      </div>

      <div v-if="err" class="text-xs text-red-400">{{ err }}</div>
      <template v-else>
        <div class="grid grid-cols-3 gap-3 text-center text-xs">
          <div>
            <div class="text-lg font-bold font-mono">{{ stats.count ?? '—' }}</div>
            <div class="text-muted text-[10px]">上榜只数</div>
          </div>
          <div>
            <div class="text-lg font-bold font-mono" :class="cls(stats.net_buy_sum_wan)">
              {{ fmtWan(stats.net_buy_sum_wan) }}</div>
            <div class="text-muted text-[10px]">净买合计</div>
          </div>
          <div>
            <div class="text-lg font-bold font-mono cursor-help" :title="seatsHint">{{ seatsCount ?? '—' }}</div>
            <div class="text-muted text-[10px]">有席位明细</div>
          </div>
        </div>
        <!-- ★ 口径说明：席位只对池内个股富化（实测覆盖率约 2%）⇒ 必须说清，
             否则会被误读成"今天机构都没上榜"。 -->
        <div v-if="note" class="text-[11px] text-amber-400 mt-2">{{ note }}</div>
        <div v-else class="text-[10px] text-muted mt-2">
          提示：<b>席位明细只对评分池内个股富化</b>（覆盖率低属正常）；「净买」为正＝当日龙虎榜净买入。
        </div>
      </template>
    </div>

    <!-- ── 两栏：净买榜 / 净卖榜 ── -->
    <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
      <div v-for="side in sides" :key="side.key" class="bg-card border border-border rounded-lg p-4 min-w-0">
        <div class="flex items-center justify-between mb-2">
          <div class="text-sm font-semibold" :class="side.cls">{{ side.label }}</div>
          <span class="text-[10px] text-muted">净买额（万元）· 点击行看该股历史</span>
        </div>
        <table class="w-full text-xs">
          <thead class="text-muted">
            <tr class="border-b border-border">
              <th class="text-left py-1.5 w-[46%]">股票</th>
              <th class="text-right py-1.5">净买</th>
              <th class="text-right py-1.5 cursor-help" title="当日涨跌幅">涨幅</th>
              <th class="text-left py-1.5 pl-2">原因 / 席位</th>
            </tr>
          </thead>
          <tbody>
            <template v-for="r in side.rows" :key="side.key + r.code">
              <tr class="border-b border-border/40 hover:bg-white/5 cursor-pointer"
                  @click="toggle(r.code)">
                <td class="py-1.5">
                  <span class="font-mono text-muted mr-1">{{ r.code }}</span>
                  <span class="font-semibold">{{ r.name }}</span>
                </td>
                <td class="py-1.5 text-right font-mono" :class="cls(r.net_buy_wan)">{{ fmtWan(r.net_buy_wan) }}</td>
                <td class="py-1.5 text-right font-mono" :class="cls(r.quote_change)">
                  {{ r.quote_change == null ? '—' : (r.quote_change > 0 ? '+' : '') + r.quote_change + '%' }}</td>
                <td class="py-1.5 pl-2 text-muted truncate" :title="(r.concepts || '') + ' / ' + (r.up_reason || '')">
                  {{ r.up_reason || '—' }}
                  <span v-if="r.seats_count" class="ml-1 px-1 rounded bg-sky-500/15 text-sky-400 text-[10px]"
                        :title="`有 ${r.seats_count} 个席位明细，点击展开`">席位{{ r.seats_count }}</span>
                </td>
              </tr>
              <!-- 席位明细（展开） -->
              <tr v-if="expanded === r.code">
                <td colspan="4" class="py-2 bg-white/5">
                  <div v-if="!(r.seats || []).length" class="text-[11px] text-muted">
                    该股无席位明细（仅池内个股会富化）
                  </div>
                  <table v-else class="w-full text-[11px]">
                    <thead class="text-muted">
                      <tr><th class="text-left">#</th><th class="text-left">席位</th>
                          <th class="text-right">买入(万)</th><th class="text-right">卖出(万)</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="s in r.seats" :key="s.rank + s.name" class="border-t border-border/30">
                        <td class="py-0.5 font-mono">{{ s.rank }}</td>
                        <td class="py-0.5">
                          <span :class="s.youzi ? 'text-amber-400 font-semibold' : 'text-gray-300'">{{ s.name }}</span>
                          <!-- ★ 数据里自带 youzi 标记（知名游资）⇒ 直接展示，不自己猜 -->
                          <span v-if="s.youzi" class="ml-1 px-1 rounded bg-amber-500/15 text-amber-400 text-[10px]">游资</span>
                        </td>
                        <td class="py-0.5 text-right font-mono text-red-400">{{ fmtWan(s.buy / 10000) }}</td>
                        <td class="py-0.5 text-right font-mono text-emerald-400">{{ fmtWan(s.sell / 10000) }}</td>
                      </tr>
                    </tbody>
                  </table>
                </td>
              </tr>
            </template>
            <tr v-if="!side.rows.length">
              <td colspan="4" class="py-3 text-center text-muted text-xs">—（当日无数据）</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ── 个股上榜历史（点击行后出现）── -->
    <div v-if="stockCode" class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-2">
        <div class="text-sm font-semibold">{{ stockName || stockCode }} · 上榜历史
          <span class="text-[10px] text-muted font-normal">（最近 {{ stockRows.length }} 次）</span></div>
        <button class="text-xs text-muted hover:text-gray-200" @click="clearStock()">关闭</button>
      </div>
      <table class="w-full text-xs">
        <thead class="text-muted">
          <tr class="border-b border-border">
            <th class="text-left py-1.5">日期</th><th class="text-right py-1.5">净买(万)</th>
            <th class="text-right py-1.5">涨幅</th><th class="text-left py-1.5 pl-2">原因</th>
            <th class="text-left py-1.5">概念</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="x in stockRows" :key="x.date" class="border-b border-border/40">
            <td class="py-1.5 font-mono">{{ x.date }}</td>
            <td class="py-1.5 text-right font-mono" :class="cls(x.net_buy_wan)">{{ fmtWan(x.net_buy_wan) }}</td>
            <td class="py-1.5 text-right font-mono" :class="cls(x.quote_change)">
              {{ x.quote_change == null ? '—' : (x.quote_change > 0 ? '+' : '') + x.quote_change + '%' }}</td>
            <td class="py-1.5 pl-2">{{ x.up_reason || '—' }}</td>
            <td class="py-1.5 text-muted truncate max-w-[280px]" :title="x.concepts || ''">{{ x.concepts || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getDragonTiger } from '../api'

const loading = ref(false)
const err = ref('')
const date = ref('')
const dates = ref([])
const topBuy = ref([])
const topSell = ref([])
const stats = ref({})
const seatsCount = ref(null)
const note = ref('')
const expanded = ref('')       // 展开席位的股票代码（一次只展开一只，防页面过长）
const stockCode = ref('')
const stockName = ref('')
const stockRows = ref([])

const seatsHint = computed(() => {
  const n = stats.value?.count || 0
  const s = seatsCount.value || 0
  return n ? `当日上榜 ${n} 只，其中 ${s} 只有席位明细（${Math.round(s / n * 100)}%）`
    : '席位明细仅对评分池内个股富化'
})

const sides = computed(() => ([
  { key: 'buy', label: '净买榜（主力买入）', cls: 'text-red-400', rows: topBuy.value },
  { key: 'sell', label: '净卖榜（主力卖出）', cls: 'text-emerald-400', rows: topSell.value },
]))

// A 股习惯：红涨绿跌（与项目其它表一致）
const cls = (v) => (Number(v) > 0 ? 'text-red-400' : Number(v) < 0 ? 'text-emerald-400' : 'text-muted')

// 金额：输入为**万元**；≥1 亿转"亿"（龙虎榜净买常达数亿，直接显示万读起来费劲）
function fmtWan(v) {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  const sign = n > 0 ? '+' : ''
  if (Math.abs(n) >= 10000) return sign + (n / 10000).toFixed(2) + '亿'
  return sign + n.toFixed(0) + '万'
}

async function load() {
  loading.value = true
  err.value = ''
  try {
    const { data } = await getDragonTiger({ date: date.value || undefined, limit: 30 })
    date.value = data.date || date.value
    dates.value = data.dates || []
    topBuy.value = data.top_buy || []
    topSell.value = data.top_sell || []
    stats.value = data.stats || {}
    seatsCount.value = data.seats_count ?? null
    note.value = data.note || ''
    expanded.value = ''
  } catch (e) {
    err.value = (e && e.message) || '加载失败'
  } finally {
    loading.value = false
  }
}

// 点击榜单行 ⇒ 展开席位 + 拉该股上榜历史（两个信息一起给，避免再次点击）
async function toggle(code) {
  if (!code) return
  if (expanded.value === code) { expanded.value = ''; return }
  expanded.value = code
  stockCode.value = code
  const row = [...topBuy.value, ...topSell.value].find(x => x.code === code)
  stockName.value = row?.name || ''
  try {
    const { data } = await getDragonTiger({ code, limit: 20 })
    stockRows.value = data.data || []
  } catch { stockRows.value = [] }
}
function clearStock() { stockCode.value = ''; stockRows.value = []; expanded.value = '' }

onMounted(load)
</script>
