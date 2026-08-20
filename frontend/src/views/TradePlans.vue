<template>
  <div class="fade-in space-y-4">
    <!-- 顶部：标题 + 操作 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3">
          <h2 class="text-lg font-bold">交易计划</h2>
          <span class="text-xs text-muted">{{ summary.count }} 个</span>
          <span v-if="summary.pending > 0" class="px-2 py-0.5 rounded-full text-xs bg-blue-500/20 text-blue-400">{{ summary.pending }} 待验证</span>
          <span v-if="summary.targeted > 0" class="px-2 py-0.5 rounded-full text-xs bg-emerald-500/20 text-emerald-400">{{ summary.targeted }} 达目标✓</span>
          <span v-if="summary.stopped > 0" class="px-2 py-0.5 rounded-full text-xs bg-red-500/20 text-red-400">{{ summary.stopped }} 破止损✗</span>
        </div>
        <div class="flex items-center gap-2">
          <button v-if="notifSupported"
            @click="toggleNotification"
            :disabled="notifPermission === 'denied'"
            :class="notifEnabled
              ? 'bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30'
              : 'bg-white/5 text-muted hover:text-gray-200'"
            :title="notifPermission === 'denied' ? '通知权限被拒绝' : (notifEnabled ? '通知已开启' : '开启通知：触及买点/达目标/破止损会推送')"
            class="px-3 py-1 rounded text-xs transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            {{ notifEnabled ? '🔔 通知已开' : '🔔 开启通知' }}
          </button>
          <span class="text-xs" :class="tradingNow ? 'text-emerald-400' : 'text-muted'">
            {{ tradingNow ? '● 交易中' : '○ 已休市' }}
          </span>
          <span class="text-xs text-muted">{{ countdown > 0 ? countdown + 's' : '刷新中' }}</span>
          <button @click="refresh" :disabled="loading"
            class="px-3 py-1 rounded text-xs bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-50">
            {{ loading ? '...' : '刷新' }}
          </button>
          <button @click="toggleAddForm" class="px-3 py-1 rounded text-xs bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25">
            {{ showAddForm ? '收起' : '+ 新建计划' }}
          </button>
          <button @click="exportPlansJSON" class="px-3 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200">导出</button>
          <label class="px-3 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200 cursor-pointer">
            导入<input type="file" accept=".json" class="hidden" @change="handleImport"/>
          </label>
        </div>
      </div>
    </div>

    <!-- 添加/编辑表单 -->
    <div v-if="showAddForm" class="bg-card border border-border rounded-lg p-4 fade-in">
      <h3 class="text-sm font-semibold mb-3">新建交易计划</h3>
      <div class="grid grid-cols-1 md:grid-cols-6 gap-3">
        <div class="relative md:col-span-2">
          <label class="text-xs text-muted">股票代码/名称</label>
          <input v-model="form.code" @input="onCodeSearch" @focus="showSearchDropdown = true" @blur="hideDropdown"
            placeholder="搜索代码或名称"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
          <div v-if="showSearchDropdown && searchResults.length"
            class="absolute z-50 top-full mt-1 left-0 right-0 bg-card border border-border rounded shadow-lg max-h-48 overflow-auto">
            <div v-for="r in searchResults" :key="r.code" @mousedown.prevent="pickStock(r)"
              class="px-3 py-1.5 text-xs hover:bg-white/5 cursor-pointer flex justify-between">
              <span>{{ r.name }}</span><span class="text-muted font-mono">{{ r.code }}</span>
            </div>
          </div>
        </div>
        <div>
          <label class="text-xs text-muted">买点价</label>
          <input v-model.number="form.buy_price" type="number" step="0.001" placeholder="买入价位"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
        <div>
          <label class="text-xs text-muted">止损价</label>
          <input v-model.number="form.stop_loss" type="number" step="0.001" placeholder="跌破此价止损"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
        <div>
          <label class="text-xs text-muted">目标价</label>
          <input v-model.number="form.target" type="number" step="0.001" placeholder="预期目标"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
        <div>
          <label class="text-xs text-muted">盈亏比（自动）</label>
          <div class="mt-1 px-2 py-1 text-sm font-mono rounded bg-bg border border-border" :class="previewRRClass">
            {{ previewRRText }}
          </div>
        </div>
        <div class="md:col-span-3">
          <label class="text-xs text-muted">买点理由</label>
          <input v-model="form.reason" placeholder="为什么这是买点（技术/资金/基本面）"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
        <div class="md:col-span-3">
          <label class="text-xs text-muted">预期走势</label>
          <input v-model="form.expected" placeholder="预期接下来会怎么走"
            class="w-full mt-1 bg-bg border border-border rounded px-2 py-1 text-sm focus:outline-none focus:border-accent/50"/>
        </div>
      </div>
      <div class="flex gap-2 mt-3">
        <button @click="submitForm" class="px-4 py-1 rounded text-xs bg-emerald-500/20 text-emerald-400 hover:bg-emerald-500/30">添加</button>
        <button @click="resetForm" class="px-4 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200">取消</button>
        <span v-if="formError" class="text-xs text-red-400 self-center">{{ formError }}</span>
      </div>
    </div>

    <!-- 空状态 -->
    <div v-if="!plans.length" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
      <div class="text-sm">还没有交易计划</div>
      <div class="text-xs mt-2">评分高的票不等于立即买入。分析后判断买点，记录理由，系统会自动跟踪验证你的判断。</div>
    </div>

    <!-- 计划表格 -->
    <div v-else class="bg-card border border-border rounded-lg overflow-hidden overflow-x-auto">
      <table class="w-full text-sm whitespace-nowrap">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">名称/代码</th>
            <th class="text-right py-2.5 px-3">现价</th>
            <th class="text-right py-2.5 px-3">距买点</th>
            <th class="text-right py-2.5 px-3">买点</th>
            <th class="text-right py-2.5 px-3">止损</th>
            <th class="text-right py-2.5 px-3">目标</th>
            <th class="text-center py-2.5 px-3">盈亏比</th>
            <th class="text-center py-2.5 px-3">状态</th>
            <th class="text-right py-2.5 px-3">T+1</th>
            <th class="text-left py-2.5 px-3">理由/预期</th>
            <th class="text-center py-2.5 px-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in tableRows" :key="row.plan.id"
            class="border-b border-border/50 hover:bg-white/3 transition-colors">
            <!-- 名称/代码 -->
            <td class="py-2 px-3">
              <div class="cursor-pointer hover:text-accent inline-block" @click="goDetail(row.plan.code)">{{ row.plan.name }}</div>
              <div class="text-muted font-mono text-xs">
                <a :href="getXueqiuUrl(row.plan.code)" target="_blank" rel="noopener" class="hover:text-accent hover:underline">{{ row.plan.code }}</a>
              </div>
            </td>
            <!-- 现价 -->
            <td class="py-2 px-3 text-right font-mono">{{ row.price || '-' }}</td>
            <!-- 距买点 -->
            <td class="py-2 px-3 text-right font-mono text-xs" :class="row.distanceClass">
              {{ row.distanceText }}
            </td>
            <!-- 买点/止损/目标 -->
            <td class="py-2 px-3 text-right font-mono">{{ row.plan.buy_price }}</td>
            <td class="py-2 px-3 text-right font-mono text-red-400/80">{{ row.plan.stop_loss }}</td>
            <td class="py-2 px-3 text-right font-mono text-emerald-400/80">{{ row.plan.target }}</td>
            <!-- 盈亏比 -->
            <td class="py-2 px-3 text-center font-mono" :class="row.rrClass">{{ row.rrText }}</td>
            <!-- 状态 -->
            <td class="py-2 px-3 text-center">
              <span class="px-2 py-0.5 rounded-full text-xs" :class="statusBadgeClass(row.plan.status)">
                {{ statusLabel(row.plan.status) }}
              </span>
            </td>
            <!-- T+1 -->
            <td class="py-2 px-3 text-right font-mono text-xs">
              <span v-if="row.t1Perf !== null" :class="row.t1Perf >= 0 ? 'text-rise' : 'text-fall'">
                {{ row.t1Perf >= 0 ? '+' : '' }}{{ row.t1Perf.toFixed(2) }}%
              </span>
              <span v-else class="text-muted">-</span>
            </td>
            <!-- 理由/预期 -->
            <td class="py-2 px-3 max-w-xs">
              <div class="text-xs truncate" :title="row.plan.reason">{{ row.plan.reason || '-' }}</div>
              <div class="text-[10px] text-muted truncate" v-if="row.plan.expected" :title="row.plan.expected">预期：{{ row.plan.expected }}</div>
            </td>
            <!-- 操作 -->
            <td class="py-2 px-3 text-center">
              <button @click="confirmRemove(row.plan)" class="text-xs text-red-400 hover:underline">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 规则说明 -->
    <div class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
      <div class="font-semibold text-gray-300 mb-2">交易计划说明</div>
      <div class="space-y-2">
        <div><span class="text-blue-400">等待</span>：价格还在买点上方，等回调到买点</div>
        <div><span class="text-blue-400">已触及</span>：价格到达买点，开始 T+1 跟踪（A股 T+1，次日才能卖）</div>
        <div><span class="text-emerald-400">达目标✓</span>：价格触及目标价，计划验证成功</div>
        <div><span class="text-red-400">破止损✗</span>：价格跌破止损价，计划验证失败</div>
        <div class="pt-2 border-t border-border">
          <span class="text-gray-300">盈亏比</span> = (目标-买点)/(买点-止损)。≥2 绿色（高质量），1~2 黄色（及格），&lt;1 红色（不值得做）
        </div>
        <div>
          <span class="text-gray-300">T+1</span>：触及买点后，次日的涨跌幅（基于次日首次刷新价近似）。
          <span class="text-amber-400">注：实时接口无历史分时，T+1 为近似值，精确回填需 K线接口（二期）</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { useRouter } from 'vue-router'
import { getStockRealtime, searchStock } from '../api'
import {
  useTradePlans, addPlan, removePlan, evaluatePlanStatus,
  calcRiskReward, calcDistanceToBuy, calcT1Performance,
  usePlansSummary, exportPlansJSON, importPlansJSON,
} from '../composables/useTradePlans'
import { isTradingTime, getRefreshInterval } from '../composables/usePortfolio'
import {
  supported as notifSupported, permission as notifPermission,
  enabled as notifEnabled, requestPermission, disable as disableNotification,
  checkPlansNotify,
} from '../composables/useNotifications'
import { getXueqiuUrl } from '../composables/stockUtils'

const router = useRouter()
const { plans } = useTradePlans()

// ── 实时行情缓存 ──
const realtimeMap = ref({})
const loading = ref(false)
const countdown = ref(getRefreshInterval())
const tradingNow = ref(isTradingTime())
let timer = null

const summary = usePlansSummary(realtimeMap)

// ── 通知开关 ──
async function toggleNotification() {
  if (notifEnabled.value) disableNotification()
  else {
    const ok = await requestPermission()
    if (!ok && notifPermission.value === 'denied') {
      alert('浏览器通知权限已被拒绝，请在浏览器设置中允许后重试。')
    }
  }
}

// ── 添加表单 ──
const showAddForm = ref(false)
const form = reactive({ code: '', name: '', buy_price: null, stop_loss: null, target: null, reason: '', expected: '' })
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
  form.code = ''; form.name = ''
  form.buy_price = null; form.stop_loss = null; form.target = null
  form.reason = ''; form.expected = ''
  formError.value = ''; searchResults.value = []
}
function onCodeSearch() {
  clearTimeout(searchTimer)
  const kw = form.code?.trim()
  if (!kw) { searchResults.value = []; return }
  searchTimer = setTimeout(async () => {
    try { const { data } = await searchStock(kw); searchResults.value = data || [] }
    catch { searchResults.value = [] }
  }, 300)
}
function pickStock(r) {
  form.code = r.code; form.name = r.name
  searchResults.value = []; showSearchDropdown.value = false
}
function hideDropdown() { setTimeout(() => { showSearchDropdown.value = false }, 200) }

// 盈亏比实时预览
const previewRR = computed(() => {
  if (!form.buy_price || !form.stop_loss || !form.target) return null
  const reward = form.target - form.buy_price
  const risk = form.buy_price - form.stop_loss
  if (risk <= 0) return null
  return reward / risk
})
const previewRRText = computed(() => {
  const rr = previewRR.value
  if (rr == null) return '-'
  if (rr < 0) return '止损/目标设错'
  return '1 : ' + rr.toFixed(2)
})
const previewRRClass = computed(() => {
  const rr = previewRR.value
  if (rr == null) return 'text-muted'
  if (rr < 0) return 'text-red-400'
  if (rr >= 2) return 'text-emerald-400'
  if (rr >= 1) return 'text-amber-400'
  return 'text-red-400'
})

function submitForm() {
  if (!/^\d{6}$/.test(form.code)) { formError.value = '代码必须是 6 位数字'; return }
  if (!form.buy_price || form.buy_price <= 0) { formError.value = '买点价必须 > 0'; return }
  if (!form.stop_loss || form.stop_loss >= form.buy_price) { formError.value = '止损价必须 < 买点价'; return }
  if (!form.target || form.target <= form.buy_price) { formError.value = '目标价必须 > 买点价'; return }
  addPlan({
    code: form.code, name: form.name || form.code,
    buy_price: form.buy_price, stop_loss: form.stop_loss, target: form.target,
    reason: form.reason, expected: form.expected,
  })
  resetForm()
  refresh()
}

function confirmRemove(plan) {
  if (confirm(`确认删除「${plan.name}」的交易计划？`)) {
    removePlan(plan.id)
    delete realtimeMap.value[plan.code]
  }
}

async function handleImport(e) {
  const file = e.target.files?.[0]
  if (!file) return
  try { await importPlansJSON(file); alert('导入成功'); refresh() }
  catch (err) { alert('导入失败：' + err.message) }
  e.target.value = ''
}

// ── 表格行数据 ──
const tableRows = computed(() => {
  return plans.value.map(plan => {
    const realtime = realtimeMap.value[plan.code]
    const price = realtime?.price || 0
    const distance = calcDistanceToBuy(plan, price)
    const rr = calcRiskReward(plan)
    const t1Perf = calcT1Performance(plan)
    return { plan, price, distance, rr, t1Perf,
      distanceText: distance != null ? (distance >= 0 ? '+' : '') + distance.toFixed(2) + '%' : '-',
      distanceClass: distance != null && distance <= 0 && plan.status === 'waiting' ? 'text-emerald-400 font-bold' : distance != null && distance > 0 ? 'text-muted' : 'text-muted',
      rrText: rr == null ? '-' : '1:' + rr.toFixed(2),
      rrClass: rr == null ? 'text-muted' : rr >= 2 ? 'text-emerald-400' : rr >= 1 ? 'text-amber-400' : 'text-red-400',
    }
  })
})

// ── 刷新：拉行情 + 状态机判定 + 通知检查 ──
async function refresh() {
  if (!plans.value.length) return
  loading.value = true
  countdown.value = 0

  const promises = plans.value.map(p =>
    getStockRealtime(p.code).then(({ data }) => {
      if (data && data.price > 0) {
        realtimeMap.value[p.code] = data
        if (data.name) {
          const item = plans.value.find(x => x.id === p.id)
          if (item && item.name !== data.name) item.name = data.name
        }
        // ★ 状态机判定：更新 plan 的 status 和跟踪字段
        const item = plans.value.find(x => x.id === p.id)
        if (item) evaluatePlanStatus(item, data)
      }
    }).catch(() => {})
  )

  Promise.allSettled(promises).finally(() => {
    // 持久化状态变化 + 通知检查
    localStorage.setItem('trade_plans', JSON.stringify(plans.value))
    checkPlansNotify(plans.value)
    loading.value = false
    tradingNow.value = isTradingTime()
    countdown.value = getRefreshInterval()
  })
}

// ── 倒计时轮询 ──
function startTimer() {
  stopTimer()
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) refresh()
    else {
      const t = isTradingTime()
      if (t !== tradingNow.value) { tradingNow.value = t; countdown.value = getRefreshInterval() }
    }
  }, 1000)
}
function stopTimer() { if (timer) { clearInterval(timer); timer = null } }

// ── 样式 helper ──
function statusBadgeClass(s) {
  return {
    waiting: 'bg-gray-500/20 text-gray-400',
    hit: 'bg-blue-500/20 text-blue-400',
    targeted: 'bg-emerald-500/20 text-emerald-400',
    stopped: 'bg-red-500/20 text-red-400',
    expired: 'bg-amber-500/20 text-amber-400',
  }[s] || 'bg-gray-500/20 text-gray-400'
}
function statusLabel(s) {
  return { waiting: '等待', hit: '已触及', targeted: '达目标✓', stopped: '破止损✗', expired: '错过' }[s] || s
}

function goDetail(code) { router.push(`/stock/${code}`) }

watch(() => plans.value.length, (n, old) => { if (n > (old || 0)) refresh() })

onMounted(() => {
  refresh()
  startTimer()
  // 从数据库同步交易计划
  import('../composables/useTradePlans.js').then(m => m.syncFromServer())
})
onBeforeUnmount(() => { stopTimer() })
</script>
