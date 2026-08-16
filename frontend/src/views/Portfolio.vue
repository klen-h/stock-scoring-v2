<template>
  <div class="fade-in space-y-4">
    <!-- 顶部：标题 + 操作 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3">
          <h2 class="text-lg font-bold">我的持仓</h2>
          <span class="text-xs text-muted">{{ summary.count }} 只</span>
          <span v-if="summary.triggeredCount > 0"
            class="px-2 py-0.5 rounded-full text-xs bg-red-500/20 text-red-400">
            {{ summary.triggeredCount }} 只触发提醒
          </span>
        </div>
        <div class="flex items-center gap-2">
          <!-- 通知开关：不支持 Notification API 时隐藏；权限被拒时禁用 -->
          <button v-if="notifSupported"
            @click="toggleNotification"
            :disabled="notifPermission === 'denied'"
            :class="notifEnabled
              ? 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30'
              : 'bg-white/5 text-muted hover:text-gray-200'"
            :title="notifPermission === 'denied'
              ? '浏览器通知权限已被拒绝，请在浏览器设置中允许本站点通知'
              : (notifEnabled ? '通知已开启，点击关闭' : '开启桌面通知：持仓止损/止盈/急涨急跌等事件会推送到桌面')"
            class="px-3 py-1 rounded text-xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            {{ notifEnabled ? '🔔 通知已开' : '🔔 开启通知' }}
          </button>
          <span class="text-xs" :class="tradingNow ? 'text-emerald-400' : 'text-muted'"
            :title="tradingNow ? '当前为交易时段，每30秒刷新' : '非交易时段（周末/夜间/午休），每5分钟刷新一次'">
            {{ tradingNow ? '● 交易中' : '○ 已休市' }}
          </span>
          <span class="text-xs text-muted">{{ countdown > 0 ? countdown + 's 后刷新' : '刷新中...' }}</span>
          <button @click="refresh" :disabled="loading"
            class="px-3 py-1 rounded text-xs bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50">
            {{ loading ? '加载中...' : '刷新' }}
          </button>
          <button @click="toggleAddForm" class="px-3 py-1 rounded text-xs bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25 transition-colors">
            {{ showAddForm ? '收起' : '+ 添加持仓' }}
          </button>
          <button @click="exportJSON" class="px-3 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors">导出</button>
          <label class="px-3 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors cursor-pointer">
            导入
            <input type="file" accept=".json" class="hidden" @change="handleImport"/>
          </label>
        </div>
      </div>
    </div>

    <!-- 汇总卡片 -->
    <div v-if="positions.length" class="grid grid-cols-2 md:grid-cols-4 gap-3">
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">总市值</div>
        <div class="text-lg font-bold mt-1">{{ formatMoney(summary.totalMarketValue) }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">总成本</div>
        <div class="text-lg font-bold mt-1">{{ formatMoney(summary.totalCost) }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">浮动盈亏</div>
        <div class="text-lg font-bold mt-1" :class="summary.totalProfit >= 0 ? 'text-rise' : 'text-fall'">
          {{ summary.totalProfit >= 0 ? '+' : '' }}{{ formatMoney(summary.totalProfit) }}
        </div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs text-muted">总收益率</div>
        <div class="text-lg font-bold mt-1" :class="summary.totalProfitPct >= 0 ? 'text-rise' : 'text-fall'">
          {{ summary.totalProfitPct >= 0 ? '+' : '' }}{{ summary.totalProfitPct.toFixed(2) }}%
        </div>
      </div>
    </div>

    <!-- 添加/编辑持仓表单 -->
    <div v-if="showAddForm" class="bg-card border border-border rounded-lg p-4 fade-in">
      <h3 class="text-sm font-semibold mb-3">{{ editingCode ? '编辑持仓' : '添加持仓' }}</h3>
      <div class="grid grid-cols-1 md:grid-cols-5 gap-3">
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
          <label class="text-xs text-muted">成本价</label>
          <input v-model.number="form.cost" type="number" step="0.001" placeholder="0.00"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
        <div>
          <label class="text-xs text-muted">股数</label>
          <input v-model.number="form.shares" type="number" step="100" placeholder="0"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
        <div>
          <label class="text-xs text-muted">备注（可选）</label>
          <input v-model="form.note" placeholder="如：长线/短线"
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
    <div v-if="!positions.length" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
      <div class="text-sm">还没有持仓，点击右上角「添加持仓」开始监控</div>
    </div>

    <!-- 持仓表格 -->
    <div v-else class="bg-card border border-border rounded-lg overflow-hidden overflow-x-auto">
      <table class="w-full text-sm whitespace-nowrap">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">名称/代码</th>
            <th class="text-right py-2.5 px-3">现价</th>
            <th class="text-right py-2.5 px-3">今日</th>
            <th class="text-right py-2.5 px-3">成本价</th>
            <th class="text-right py-2.5 px-3">股数</th>
            <th class="text-right py-2.5 px-3">市值</th>
            <th class="text-right py-2.5 px-3">浮动盈亏</th>
            <th class="text-center py-2.5 px-3">评分</th>
            <th class="text-left py-2.5 px-3">建议操作</th>
            <th class="text-center py-2.5 px-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in tableRows" :key="row.position.code"
            class="border-b border-border/50 hover:bg-white/3 transition-colors"
            :class="rowBorderClass(row)">
            <!-- 名称/代码 -->
            <td class="py-2 px-3">
              <div class="cursor-pointer hover:text-accent transition-colors inline-block" @click="goDetail(row.position.code)">{{ row.position.name }}</div>
              <div class="text-muted font-mono text-xs">
                <a :href="getXueqiuUrl(row.position.code)" target="_blank" rel="noopener"
                   class="hover:text-accent hover:underline"
                   title="在雪球查看">{{ row.position.code }}</a>
              </div>
              <div v-if="row.position.note" class="text-[10px] text-muted mt-0.5">{{ row.position.note }}</div>
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
            <!-- 成本价 -->
            <td class="py-2 px-3 text-right font-mono">{{ row.position.cost }}</td>
            <!-- 股数 -->
            <td class="py-2 px-3 text-right font-mono">{{ row.position.shares }}</td>
            <!-- 市值 -->
            <td class="py-2 px-3 text-right font-mono">{{ formatMoney(row.profit.marketValue) }}</td>
            <!-- 浮动盈亏 -->
            <td class="py-2 px-3 text-right">
              <div class="font-mono" :class="row.profit.profit >= 0 ? 'text-rise' : 'text-fall'">
                {{ row.profit.profit >= 0 ? '+' : '' }}{{ formatMoney(row.profit.profit) }}
              </div>
              <div class="text-xs font-mono" :class="row.profit.profitPct >= 0 ? 'text-rise' : 'text-fall'">
                {{ row.profit.profitPct >= 0 ? '+' : '' }}{{ row.profit.profitPct.toFixed(2) }}%
              </div>
            </td>
            <!-- 评分 -->
            <td class="py-2 px-3 text-center">
              <span v-if="row.score" class="px-2 py-0.5 rounded-full text-xs"
                :class="scoreBadgeClass(row.score)">
                {{ row.score.signal }} {{ row.score.total_score }}
              </span>
              <span v-else class="text-muted text-xs">-</span>
            </td>
            <!-- 建议操作 -->
            <td class="py-2 px-3">
              <div v-if="row.alerts.length">
                <div v-for="(a, i) in row.alerts" :key="i" class="text-xs" :class="alertTextClass(a.level)">
                  · {{ a.action }}
                </div>
              </div>
              <span v-else class="text-xs text-muted">持有</span>
            </td>
            <!-- 操作 -->
            <td class="py-2 px-3 text-center whitespace-nowrap">
              <button @click="editPosition(row.position)"
                class="text-xs text-accent hover:underline mr-2">编辑</button>
              <button @click="confirmRemove(row.position)"
                class="text-xs text-red-400 hover:underline">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 规则说明 -->
    <div class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
      <div class="font-semibold text-gray-300 mb-2">监控规则（阈值可在源码 ALERT_CONFIG 调整）</div>
      <ul class="space-y-1 list-disc list-inside">
        <li><span class="text-red-400">硬止损</span>：浮亏 ≤ {{ ALERT_CONFIG.stopLossPct }}% → 建议止损卖出</li>
        <li><span class="text-amber-400">硬止盈</span>：浮盈 ≥ +{{ ALERT_CONFIG.takeProfitPct }}% → 建议止盈清仓</li>
        <li><span class="text-amber-400">移动止盈</span>：从持仓期高点回撤 ≥ {{ ALERT_CONFIG.trailingDrawdownPct }}%（且仍有盈利）→ 建议减仓锁利</li>
        <li><span class="text-amber-400">评分恶化</span>：综合评分 ≤ {{ ALERT_CONFIG.scoreSellThreshold }} → 关注卖出</li>
        <li><span class="text-muted">评分偏弱</span>：评分 {{ ALERT_CONFIG.scoreSellThreshold }}~{{ ALERT_CONFIG.scoreWeakThreshold }} → 考虑减仓</li>
        <li><span class="text-purple-400">急涨/急跌</span>：单日涨幅 ≥ +{{ ALERT_CONFIG.surgeUpPct }}% 或 ≤ {{ ALERT_CONFIG.surgeDownPct }}% → 推送桌面通知（仅通知，不进建议操作列）</li>
      </ul>
      <div class="mt-3 pt-3 border-t border-border">
        <div class="font-semibold text-gray-300 mb-1">刷新频率</div>
        <div>交易时段（工作日 9:30-11:30 / 13:00-15:00）每 30 秒刷新；其余时间每 5 分钟刷新一次，避免无意义请求。</div>
      </div>
      <div class="mt-2 pt-3 border-t border-border">
        <div class="font-semibold text-gray-300 mb-1">桌面通知</div>
        <div>点击右上角「🔔 开启通知」授权后，上述事件（含急涨急跌）触发时会推送桌面通知。<span class="text-amber-400">同一事件仅在"触发"时推一次，不会刷屏。</span>止损等紧急通知需手动关闭。</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getStockRealtime, getStockScore, searchStock } from '../api'
import {
  addPosition, removePosition, updatePosition,
  calcProfit, evaluateAlerts, updateHighWaterMark, useSummary,
  exportJSON, importJSON, usePortfolio, ALERT_CONFIG,
  isTradingTime, getRefreshInterval,
} from '../composables/usePortfolio'
import {
  supported as notifSupported, permission as notifPermission,
  enabled as notifEnabled, requestPermission, disable as disableNotification,
  checkAndNotify,
} from '../composables/useNotifications'
import { getXueqiuUrl } from '../composables/stockUtils'

const router = useRouter()

// ── 通知开关 ──
async function toggleNotification() {
  if (notifEnabled.value) {
    // 已开启 → 关闭（仅停止发送，不撤回系统权限）
    disableNotification()
  } else {
    // 未开启 → 申请权限并开启
    const ok = await requestPermission()
    if (!ok && notifPermission.value === 'denied') {
      alert('浏览器通知权限已被拒绝。\n请在浏览器地址栏左侧的"站点信息"中，把"通知"改为"允许"，然后刷新页面重试。')
    }
  }
}

// positions 是全局单例 ref（由 usePortfolio 组合式函数提供）
const { positions } = usePortfolio()

// ── 实时行情 & 评分缓存（响应式，刷新时更新）──
const realtimeMap = ref({})   // { [code]: { price, change_pct, ... } }
const scoreMap = ref({})      // { [code]: { total_score, signal, ... } }
const loading = ref(false)
const countdown = ref(getRefreshInterval())
const tradingNow = ref(isTradingTime())  // 当前是否交易时段（用于 UI 提示）
let timer = null

// ── 汇总统计（computed，依赖 realtimeMap）──
const summary = useSummary(realtimeMap)

// ── 表格行数据（computed：合并持仓 + 行情 + 盈亏 + 提醒）──
const tableRows = computed(() => {
  return positions.value.map(p => {
    const realtime = realtimeMap.value[p.code]
    const score = scoreMap.value[p.code]
    const price = realtime?.price || 0
    const profit = calcProfit(p.cost, p.shares, price)
    const alerts = evaluateAlerts(p, realtime, score)
    return { position: p, realtime, score, profit, alerts }
  })
})

// ── 添加/编辑表单 ──
const showAddForm = ref(false)
const editingCode = ref('')
const form = reactive({ code: '', name: '', cost: null, shares: null, note: '' })
const formError = ref('')
const searchResults = ref([])
const showSearchDropdown = ref(false)
let searchTimer = null

function toggleAddForm() {
  if (showAddForm.value) {
    resetForm()
  } else {
    showAddForm.value = true
  }
}

function resetForm() {
  showAddForm.value = false
  editingCode.value = ''
  form.code = ''
  form.name = ''
  form.cost = null
  form.shares = null
  form.note = ''
  formError.value = ''
  searchResults.value = []
}

function onCodeSearch() {
  // 编辑模式不触发搜索
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

function editPosition(p) {
  editingCode.value = p.code
  form.code = p.code
  form.name = p.name
  form.cost = p.cost
  form.shares = p.shares
  form.note = p.note || ''
  formError.value = ''
  showAddForm.value = true
}

function submitForm() {
  // 校验
  if (!/^\d{6}$/.test(form.code)) {
    formError.value = '股票代码必须是 6 位数字'; return
  }
  if (!form.cost || form.cost <= 0) {
    formError.value = '成本价必须大于 0'; return
  }
  if (!form.shares || form.shares <= 0 || !Number.isInteger(form.shares)) {
    formError.value = '股数必须为正整数'; return
  }
  addPosition({
    code: form.code,
    name: form.name || form.code,
    cost: form.cost,
    shares: form.shares,
    note: form.note,
  })
  resetForm()
  // 新增后立即刷新该只行情
  refresh()
}

function confirmRemove(p) {
  if (confirm(`确认删除持仓「${p.name}(${p.code})」？`)) {
    removePosition(p.code)
    // 清理缓存
    delete realtimeMap.value[p.code]
    delete scoreMap.value[p.code]
  }
}

async function handleImport(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try {
    await importJSON(file)
    alert('导入成功')
    refresh()
  } catch (err) {
    alert('导入失败：' + err.message)
  }
  // 清空 input，允许重复选同一文件
  e.target.value = ''
}

// ── 刷新行情 & 评分 ──
async function refresh() {
  if (!positions.value.length) return
  loading.value = true
  countdown.value = 0

  // 并发拉取所有持仓的实时行情（allSettled 容错：单只失败不影响整体）
  const realtimePromises = positions.value.map(p =>
    getStockRealtime(p.code).then(({ data }) => {
      if (data && data.price > 0) {
        realtimeMap.value[p.code] = data
        // 同步名称（防止用户添加时名称不准）
        if (data.name) {
          const pos = positions.value.find(x => x.code === p.code)
          if (pos && pos.name !== data.name) updatePosition(p.code, { name: data.name })
        }
        // 维护持仓期最高价
        updateHighWaterMark(p.code, data.price)
      }
    }).catch(() => {})  // 静默失败
  )

  // 评分接口较慢，并行拉取；全部完成后再触发通知检查（保证评分类规则不延迟一轮）
  const scorePromises = positions.value.map(p =>
    getStockScore(p.code).then(({ data }) => {
      if (data && data.total_score) scoreMap.value[p.code] = data
    }).catch(() => {})
  )

  // 行情就绪 → 解锁 UI；行情+评分都就绪 → 通知检查
  Promise.allSettled(realtimePromises).finally(() => {
    loading.value = false
    tradingNow.value = isTradingTime()
    countdown.value = getRefreshInterval()
  })
  Promise.allSettled([...realtimePromises, ...scorePromises]).finally(() => {
    // 行情 + 评分都到位后，检查并发送桌面通知（内部做 diff 去重）
    checkAndNotify(positions.value, realtimeMap.value, scoreMap.value)
  })
}

// ── 倒计时轮询（动态间隔：交易时段 30s，非交易时段 5min）──
function startTimer() {
  stopTimer()
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      refresh()
    } else {
      // 每秒同步一次交易状态，便于 UI 实时反映"开盘/休市"切换
      const nowTrading = isTradingTime()
      if (nowTrading !== tradingNow.value) {
        tradingNow.value = nowTrading
        // 状态切换时，把倒计时重置为新时段的间隔（避免长时间用错频率）
        countdown.value = getRefreshInterval()
      }
    }
  }, 1000)
}
function stopTimer() {
  if (timer) { clearInterval(timer); timer = null }
}

// ── 持仓变化时自动刷新（新增/删除后）──
watch(() => positions.value.length, (n, old) => {
  if (n > (old || 0)) refresh()  // 新增时拉行情；删除时无需拉
})

// ── 格式化 ──
function formatMoney(v) {
  if (!v && v !== 0) return '-'
  const abs = Math.abs(v)
  if (abs >= 1e8) return (v / 1e8).toFixed(2) + '亿'
  if (abs >= 1e4) return (v / 1e4).toFixed(2) + '万'
  return v.toFixed(2)
}

// ── 样式 helper ──
function rowBorderClass(row) {
  // 根据最高优先级提醒给整行加左边框色
  const top = row.alerts[0]
  if (!top) return ''
  if (top.level === 'danger') return 'border-l-2 border-l-red-500'
  if (top.level === 'warning') return 'border-l-2 border-l-amber-500'
  return ''
}
function scoreBadgeClass(score) {
  if (score.signal?.includes('买入')) return 'bg-emerald-500/20 text-emerald-400'
  if (score.signal?.includes('卖出')) return 'bg-red-500/20 text-red-400'
  return 'bg-amber-500/20 text-amber-400'
}
function alertTextClass(level) {
  if (level === 'danger') return 'text-red-400'
  if (level === 'warning') return 'text-amber-400'
  return 'text-muted'
}

function goDetail(code) {
  router.push(`/stock/${code}`)
}

onMounted(() => {
  refresh()
  startTimer()
})
onBeforeUnmount(() => {
  stopTimer()
})
</script>
