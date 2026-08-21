<template>
  <div class="fade-in space-y-4">
    <!-- 顶部：标题 + 操作 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3">
          <h2 class="text-lg font-bold">自选股</h2>
          <span class="text-xs text-muted">{{ summary.count }} 只</span>
          <span v-if="summary.reachedTarget > 0"
            class="px-2 py-0.5 rounded-full text-xs bg-emerald-500/20 text-emerald-400">
            {{ summary.reachedTarget }} 只到目标价
          </span>
          <span v-if="summary.scoreStrong > 0"
            class="px-2 py-0.5 rounded-full text-xs bg-purple-500/20 text-purple-400">
            {{ summary.scoreStrong }} 只评分转强
          </span>
        </div>
        <div class="flex items-center gap-2">
          <button v-if="notifSupported"
            @click="toggleNotification"
            :disabled="notifPermission === 'denied'"
            :class="notifEnabled
              ? 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30'
              : 'bg-white/5 text-muted hover:text-gray-200'"
            :title="notifPermission === 'denied'
              ? '浏览器通知权限已被拒绝，请在浏览器设置中允许本站点通知'
              : (notifEnabled ? '通知已开启，点击关闭' : '开启桌面通知：到达目标价、评分转强等买点会推送')"
            class="px-3 py-1 rounded text-xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            {{ notifEnabled ? '🔔 通知已开' : '🔔 开启通知' }}
          </button>
          <span class="text-xs" :class="tradingNow ? 'text-emerald-400' : 'text-muted'"
            :title="tradingNow ? '当前为交易时段，每30秒自动刷新' : '非交易时段，数据不变化，已停止自动刷新'">
            {{ tradingNow ? '● 交易中（自动刷新）' : '○ 已休市（已暂停）' }}
          </span>
          <span v-if="tradingNow && countdown > 0" class="text-xs text-muted">{{ countdown }}s 后刷新</span>
          <span v-else-if="loading" class="text-xs text-muted">刷新中...</span>
          <button @click="refresh" :disabled="loading"
            class="px-3 py-1 rounded text-xs bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50">
            {{ loading ? '加载中...' : '刷新' }}
          </button>
          <button @click="toggleAddForm" class="px-3 py-1 rounded text-xs bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25 transition-colors">
            {{ showAddForm ? '收起' : '+ 添加自选' }}
          </button>
          <button @click="exportWatchlistJSON" class="px-3 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors">导出</button>
          <label class="px-3 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors cursor-pointer">
            导入
            <input type="file" accept=".json" class="hidden" @change="handleImport"/>
          </label>
        </div>
      </div>
    </div>

    <!-- 汇总卡片 -->
    <div v-if="watchlist.length" class="grid grid-cols-3 gap-3">
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">自选总数</div>
        <div class="text-lg font-bold mt-1">{{ summary.count }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">到达目标价</div>
        <div class="text-lg font-bold mt-1 text-emerald-400">{{ summary.reachedTarget }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">评分转强（≥65）</div>
        <div class="text-lg font-bold mt-1 text-purple-400">{{ summary.scoreStrong }}</div>
      </div>
    </div>

    <!-- 添加/编辑表单 -->
    <div v-if="showAddForm" class="bg-card border border-border rounded-lg p-4 fade-in">
      <h3 class="text-sm font-semibold mb-3">{{ editingCode ? '编辑自选' : '添加自选' }}</h3>
      <div class="grid grid-cols-1 md:grid-cols-4 gap-3">
        <!-- 代码搜索 -->
        <div class="relative md:col-span-2">
          <label class="text-xs text-muted">股票代码 / 名称</label>
          <input v-model="form.code" @input="onCodeSearch" @focus="showSearchDropdown = true" @blur="hideDropdown"
            placeholder="输入代码或名称搜索"
            :disabled="!!editingCode"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm disabled:opacity-50 focus:outline-none focus:border-accent/50"/>
          <div v-if="showSearchDropdown && searchResults.length"
            class="absolute z-50 top-full mt-1 left-0 right-0 bg-card border border-border rounded shadow-lg max-h-48 overflow-auto">
            <div v-for="r in searchResults" :key="r.code" @mousedown.prevent="pickStock(r)"
              class="px-3 py-1.5 text-xs hover:bg-white/5 cursor-pointer flex justify-between">
              <span>{{ r.name }}</span>
              <span class="text-muted font-mono">{{ r.code }}</span>
            </div>
          </div>
        </div>
        <div>
          <label class="text-xs text-muted">目标买入价（可选）</label>
          <input v-model.number="form.target_price" type="number" step="0.001" placeholder="留空则只看评分"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
        <div>
          <label class="text-xs text-muted">备注（可选）</label>
          <input v-model="form.note" placeholder="如：等回调"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
      </div>
      <div class="flex gap-2 mt-3">
        <button @click="submitForm"
          class="px-4 py-1 rounded text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30 transition-colors">
          {{ editingCode ? '保存' : '添加' }}
        </button>
        <button @click="resetForm"
          class="px-4 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors">取消</button>
        <span v-if="formError" class="text-xs text-red-400 self-center">{{ formError }}</span>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="!watchlist.length" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
      <div class="text-sm">还没有自选股，点击右上角「添加自选」开始观察</div>
      <div class="text-xs mt-2">把感兴趣的股票加进来，系统会在价格到位或评分转强时提醒你</div>
    </div>

    <!-- 自选表格 -->
    <div v-else class="bg-card border border-border rounded-lg overflow-hidden overflow-x-auto">
      <table class="w-full text-sm whitespace-nowrap">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">名称/代码</th>
            <th class="text-right py-2.5 px-3">现价</th>
            <th class="text-right py-2.5 px-3">今日</th>
            <th class="text-right py-2.5 px-3">目标价</th>
            <th class="text-right py-2.5 px-3">距目标价</th>
            <th class="text-center py-2.5 px-3">评分</th>
            <th class="text-left py-2.5 px-3">买入时机</th>
            <th class="text-center py-2.5 px-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in tableRows" :key="row.item.code"
            class="border-b border-border/50 hover:bg-white/3 transition-colors"
            :class="row.reached ? 'border-l-2 border-l-emerald-500' : ''">
            <!-- 名称/代码 -->
            <td class="py-2 px-3">
              <div class="cursor-pointer hover:text-accent transition-colors inline-block" @click="goDetail(row.item.code)">{{ row.item.name }}</div>
              <div class="text-muted font-mono text-xs">
                <a :href="getXueqiuUrl(row.item.code)" target="_blank" rel="noopener"
                   class="hover:text-accent hover:underline" title="在雪球查看">{{ row.item.code }}</a>
              </div>
              <div v-if="row.item.note" class="text-[10px] text-muted mt-0.5">{{ row.item.note }}</div>
            </td>
            <!-- 现价 -->
            <td class="py-2 px-3 text-right font-mono">
              <span v-if="row.realtime">{{ row.realtime.price }}</span>
              <span v-else class="text-muted">-</span>
            </td>
            <!-- 今日涨跌 -->
            <td class="py-2 px-3 text-right font-mono text-xs">
              <span v-if="row.realtime" :class="row.realtime.change_pct >= 0 ? 'text-rise' : 'text-fall'">
                {{ row.realtime.change_pct >= 0 ? '+' : '' }}{{ row.realtime.change_pct }}%
              </span>
              <span v-else class="text-muted">-</span>
            </td>
            <!-- 目标价 -->
            <td class="py-2 px-3 text-right font-mono">
              <span v-if="row.item.target_price">{{ row.item.target_price }}</span>
              <span v-else class="text-muted text-xs">未设置</span>
            </td>
            <!-- 距目标价 -->
            <td class="py-2 px-3 text-right font-mono">
              <span v-if="row.deviation !== null"
                :class="row.reached ? 'text-emerald-400 font-bold' : 'text-muted'">
                {{ row.deviation >= 0 ? '+' : '' }}{{ row.deviation.toFixed(2) }}%
              </span>
              <span v-else class="text-muted text-xs">-</span>
            </td>
            <!-- 评分 -->
            <td class="py-2 px-3 text-center">
              <span v-if="row.score" class="px-2 py-0.5 rounded-full text-xs"
                :class="scoreBadgeClass(row.score)">
                {{ row.score.signal }} {{ row.score.total_score }}
              </span>
              <span v-else class="text-muted text-xs">-</span>
            </td>
            <!-- 买入时机 -->
            <td class="py-2 px-3">
              <div v-if="row.signals.length">
                <div v-for="(s, i) in row.signals" :key="i" class="text-xs text-emerald-400">
                  · {{ s.action }}
                </div>
              </div>
              <span v-else class="text-xs text-muted">观望</span>
            </td>
            <!-- 操作 -->
            <td class="py-2 px-3 text-center whitespace-nowrap">
              <button @click="editItem(row.item)"
                class="text-xs text-accent hover:underline mr-2">编辑</button>
              <button @click="confirmRemove(row.item)"
                class="text-xs text-red-400 hover:underline">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 规则说明 -->
    <div class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
      <div class="font-semibold text-gray-300 mb-2">买点提醒规则（自选股关注"何时买"，区别于持仓的"何时卖"）</div>
      <ul class="space-y-1 list-disc list-inside">
        <li><span class="text-emerald-400">到达目标价</span>：现价 ≤ 你设的目标买入价 → 可考虑买入（行左侧绿色边框高亮）</li>
        <li><span class="text-emerald-400">急跌机会</span>：单日跌幅 ≤ {{ WATCH_CONFIG.surgeDownPct }}% → 可能现短线机会</li>
        <li><span class="text-purple-400">评分转强</span>：综合评分 ≥ {{ WATCH_CONFIG.scoreBuyThreshold }}（买入信号）→ 关注买点</li>
        <li><span class="text-muted">评分走弱</span>：评分 ≤ {{ WATCH_CONFIG.scoreSellThreshold }} → 可考虑移出自选</li>
      </ul>
      <div class="mt-3 pt-3 border-t border-border">
        <div class="text-gray-300">提示：目标价可选——设了才会触发价格提醒，不设则只看评分变化。通知权限与持仓页共享，无需重复开启。</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getStockRealtime, getStockScore, searchStock } from '../api'
import {
  useWatchlist, addWatch, removeWatch, updateWatch,
  evaluateBuySignals, calcTargetDeviation, useWatchlistSummary,
  exportWatchlistJSON, importWatchlistJSON,
} from '../composables/useWatchlist'
import { isTradingTime, getRefreshInterval } from '../composables/usePortfolio'
import {
  supported as notifSupported, permission as notifPermission,
  enabled as notifEnabled, requestPermission, disable as disableNotification,
  checkWatchlistNotify,
} from '../composables/useNotifications'
import { getXueqiuUrl } from '../composables/stockUtils'

const router = useRouter()

const { watchlist, WATCH_CONFIG } = useWatchlist()

// ── 实时行情 & 评分缓存 ──
const realtimeMap = ref({})
const scoreMap = ref({})
const loading = ref(false)
const countdown = ref(getRefreshInterval())
const tradingNow = ref(isTradingTime())
let timer = null

// ── 汇总统计 ──
const summary = useWatchlistSummary(realtimeMap, scoreMap)

// ── 表格行数据（合并自选 + 行情 + 距目标价 + 买点）──
const tableRows = computed(() => {
  return watchlist.value.map(w => {
    const realtime = realtimeMap.value[w.code]
    const score = scoreMap.value[w.code]
    const price = realtime?.price || 0
    const { deviation, reached } = calcTargetDeviation(w, price)
    const signals = evaluateBuySignals(w, realtime, score)
    return { item: w, realtime, score, deviation, reached, signals }
  })
})

// ── 通知开关（复用权限单例，与持仓共享）──
async function toggleNotification() {
  if (notifEnabled.value) {
    disableNotification()
  } else {
    const ok = await requestPermission()
    if (!ok && notifPermission.value === 'denied') {
      alert('浏览器通知权限已被拒绝。\n请在浏览器地址栏左侧的"站点信息"中，把"通知"改为"允许"，然后刷新页面重试。')
    }
  }
}

// ── 添加/编辑表单 ──
const showAddForm = ref(false)
const editingCode = ref('')
const form = reactive({ code: '', name: '', target_price: null, note: '' })
const formError = ref('')
const searchResults = ref([])
const showSearchDropdown = ref(false)
let searchTimer = null

function toggleAddForm() {
  showAddForm.value = !showAddForm.value
  if (!showAddForm.value) resetForm()
}

function resetForm() {
  showAddForm.value = false
  editingCode.value = ''
  form.code = ''
  form.name = ''
  form.target_price = null
  form.note = ''
  formError.value = ''
  searchResults.value = []
}

function onCodeSearch() {
  if (editingCode.value) return
  clearTimeout(searchTimer)
  const kw = form.code?.trim()
  if (!kw) { searchResults.value = []; return }
  searchTimer = setTimeout(async () => {
    try {
      const { data } = await searchStock(kw)
      searchResults.value = data || []
    } catch { searchResults.value = [] }
  }, 300)
}

function pickStock(r) {
  form.code = r.code
  form.name = r.name
  searchResults.value = []
  showSearchDropdown.value = false
}

function hideDropdown() {
  setTimeout(() => { showSearchDropdown.value = false }, 200)
}

function editItem(w) {
  editingCode.value = w.code
  form.code = w.code
  form.name = w.name
  form.target_price = w.target_price
  form.note = w.note || ''
  formError.value = ''
  showAddForm.value = true
}

function submitForm() {
  if (!/^\d{6}$/.test(form.code)) {
    formError.value = '股票代码必须是 6 位数字'; return
  }
  // 目标价可选，但填了必须 > 0
  if (form.target_price !== null && form.target_price !== '' && Number(form.target_price) <= 0) {
    formError.value = '目标价必须大于 0（或留空）'; return
  }
  addWatch({
    code: form.code,
    name: form.name || form.code,
    target_price: form.target_price === '' ? null : form.target_price,
    note: form.note,
  })
  resetForm()
  refresh()
}

function confirmRemove(w) {
  if (confirm(`确认删除自选「${w.name}(${w.code})」？`)) {
    removeWatch(w.code)
    delete realtimeMap.value[w.code]
    delete scoreMap.value[w.code]
  }
}

async function handleImport(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    await importWatchlistJSON(file)
    alert('导入成功')
    refresh()
  } catch (err) {
    alert('导入失败：' + err.message)
  }
  e.target.value = ''
}

// ── 刷新行情 & 评分 ──
async function refresh() {
  if (!watchlist.value.length) return
  loading.value = true
  countdown.value = 0

  const realtimePromises = watchlist.value.map(w =>
    getStockRealtime(w.code).then(({ data }) => {
      if (data && data.price > 0) {
        realtimeMap.value[w.code] = data
        if (data.name) {
          const item = watchlist.value.find(x => x.code === w.code)
          if (item && item.name !== data.name) updateWatch(w.code, { name: data.name })
        }
      }
    }).catch(() => {})
  )

  const scorePromises = watchlist.value.map(w =>
    getStockScore(w.code).then(({ data }) => {
      if (data && data.total_score) scoreMap.value[w.code] = data
    }).catch(() => {})
  )

  Promise.allSettled(realtimePromises).finally(() => {
    loading.value = false
    tradingNow.value = isTradingTime()
    countdown.value = getRefreshInterval()
  })
  Promise.allSettled([...realtimePromises, ...scorePromises]).finally(() => {
    checkWatchlistNotify(watchlist.value, realtimeMap.value, scoreMap.value)
  })
}

// ── 倒计时轮询 ──
function startTimer() {
  stopTimer()
  timer = setInterval(() => {
    // 非交易时段不轮询（节省资源，数据不会变）
    if (!isTradingTime()) {
      tradingNow.value = false
      return
    }
    tradingNow.value = true
    countdown.value--
    if (countdown.value <= 0) {
      refresh()
    }
  }, 1000)
}
function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}

// ── 样式 helper ──
function scoreBadgeClass(score) {
  if (score.signal?.includes('买入')) return 'bg-emerald-500/20 text-emerald-400'
  if (score.signal?.includes('卖出')) return 'bg-red-500/20 text-red-400'
  return 'bg-amber-500/20 text-amber-400'
}

function goDetail(code) {
  router.push(`/stock/${code}`)
}

// 持仓数量变化时（新增）自动刷新
watch(() => watchlist.value.length, (n, old) => {
  if (n > (old || 0)) refresh()
})

onMounted(() => {
  refresh()
  startTimer()
  // 从数据库同步自选股
  import('../composables/useWatchlist.js').then(m => m.syncFromServer())
})
onBeforeUnmount(() => {
  stopTimer()
})
</script>
