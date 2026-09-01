<template>
  <div>
    <!-- 标题 -->
    <div class="flex items-start justify-between mb-4">
      <div>
        <h1 class="text-base font-bold text-gray-200">📋 模拟盘（纸面交易）</h1>
        <p class="text-xs text-muted mt-1">
          盘后信号自动入池 → 次日 9:35 按量价关系确认成交 → 跟踪止损止盈 → 回填真实胜率
        </p>
      </div>
      <button @click="load" class="px-3 py-1 rounded text-xs border border-border text-muted hover:text-gray-200">
        刷新
      </button>
    </div>

    <!-- 账户概览 -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4">
      <div class="bg-card border border-border rounded p-3">
        <div class="text-xs text-muted mb-1">虚拟本金</div>
        <div class="text-base font-mono text-gray-200">{{ money(account.initial_capital) }}</div>
      </div>
      <div class="bg-card border border-border rounded p-3">
        <div class="text-xs text-muted mb-1">已实现盈亏</div>
        <div class="text-base font-mono" :class="pnlText(account.realized_pnl)">
          {{ signed(account.realized_pnl) }}
        </div>
      </div>
      <div class="bg-card border border-border rounded p-3">
        <div class="text-xs text-muted mb-1">占用仓位</div>
        <div class="text-base font-mono text-gray-200">{{ money(account.used_capital) }}</div>
      </div>
      <div class="bg-card border border-border rounded p-3">
        <div class="text-xs text-muted mb-1">可用资金</div>
        <div class="text-base font-mono text-gray-200">{{ money(account.available_capital) }}</div>
      </div>
    </div>

    <!-- 组合风控状态（PLAN_PAPER_RISK.md）-->
    <div v-if="risk" class="bg-card border border-border rounded p-3 mb-4 text-xs flex flex-wrap items-center gap-x-4 gap-y-1">
      <span class="text-muted">🛡 组合风控</span>
      <span>净值回撤 <b :class="(risk.state?.drawdown || 0) > 0 ? 'text-fall' : 'text-muted'">{{ risk.state?.drawdown ?? '-' }}%</b></span>
      <span v-if="risk.state?.frozen" class="text-rise font-bold">🔒 回撤熔断中</span>
      <span v-else class="text-muted">✓ 正常</span>
      <span v-if="risk.state?.cooldown" class="text-amber-400">⏳ 连亏冷却中</span>
      <span v-if="risk.state?.daily_stop" class="text-amber-400">🚫 日亏损限额已达</span>
      <button v-if="risk.state?.frozen" @click="doUnfreeze"
        class="ml-auto px-2 py-0.5 rounded border border-border text-amber-400 hover:text-amber-300">
        解除熔断
      </button>
      <div v-if="risk.recent_events?.length" class="w-full text-muted pt-1 border-t border-border/50">
        最近事件：
        <span v-for="e in risk.recent_events.slice(0, 3)" :key="e.id" class="mr-3">{{ (e.message || '').slice(0, 40) }}</span>
      </div>
    </div>

    <!-- Tab -->
    <div class="flex flex-wrap gap-2 mb-3">
      <button v-for="t in tabs" :key="t.key" @click="tab = t.key"
        class="px-3 py-1 rounded text-xs border transition-colors"
        :class="tab === t.key ? 'bg-accent/15 text-accent border-accent/40' : 'border-border text-muted hover:text-gray-200'">
        {{ t.label }}<span class="ml-1 opacity-70">({{ countOf(t.key) }})</span>
      </button>
    </div>

    <!-- 战法统计 -->
    <div v-if="tab === 'stats'" class="bg-card border border-border rounded overflow-hidden">
      <table v-if="statRows.length" class="w-full text-xs">
        <thead class="text-muted bg-white/[0.02]">
          <tr>
            <th class="px-3 py-2 text-left">战法</th>
            <th class="px-3 py-2 text-right">成交</th>
            <th class="px-3 py-2 text-right">胜率</th>
            <th class="px-3 py-2 text-right">平均盈亏</th>
            <th class="px-3 py-2 text-right">盈亏比</th>
            <th class="px-3 py-2 text-right">累计</th>
            <th class="px-3 py-2 text-right">平均持仓</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in statRows" :key="r.name" class="border-t border-border hover:bg-white/[0.02]">
            <td class="px-3 py-2">
              <span @click="goDetail(r.code)" class="cursor-pointer hover:text-accent">{{ r.name }}</span>
            </td>
            <td class="px-3 py-2 text-right font-mono">{{ r.v.trades }}</td>
            <td class="px-3 py-2 text-right font-mono" :class="winRateText(r.v.win_rate)">
              {{ r.v.win_rate == null ? '-' : r.v.win_rate + '%' }}
            </td>
            <td class="px-3 py-2 text-right font-mono" :class="pnlText(r.v.avg_pnl_pct)">
              {{ r.v.avg_pnl_pct == null ? '-' : r.v.avg_pnl_pct + '%' }}
            </td>
            <td class="px-3 py-2 text-right font-mono">{{ r.v.profit_factor }}</td>
            <td class="px-3 py-2 text-right font-mono" :class="pnlText(r.v.total_pnl_pct)">
              {{ r.v.total_pnl_pct }}%
            </td>
            <td class="px-3 py-2 text-right font-mono">{{ r.v.avg_hold_days ?? '-' }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="p-8 text-center text-muted text-xs">
        暂无已平仓交易，积累数据后自动生成分战法胜率
      </div>
    </div>

    <!-- 持仓列表 -->
    <div v-else class="bg-card border border-border rounded overflow-hidden">
      <!-- 待确认 -->
      <table v-if="list.length && tab === 'pending'" class="w-full text-xs">
        <thead class="text-muted bg-white/[0.02]">
          <tr>
            <th class="px-3 py-2 text-left">代码</th>
            <th class="px-3 py-2 text-left">名称</th>
            <th class="px-3 py-2 text-left">战法</th>
            <th class="px-3 py-2 text-right">参考介入</th>
            <th class="px-3 py-2 text-right">止损</th>
            <th class="px-3 py-2 text-right">目标</th>
            <th class="px-3 py-2 text-center">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in list" :key="r.id" class="border-t border-border hover:bg-white/[0.02]">
            <td class="px-3 py-2 font-mono">
              <a :href="getXueqiuUrl(r.code)" target="_blank" rel="noopener" title="在雪球查看"
                class="text-accent hover:underline">{{ r.code }}</a>
            </td>
            <td class="px-3 py-2">
              <span @click="goDetail(r.code)" class="cursor-pointer hover:text-accent">{{ r.name }}</span>
            </td>
            <td class="px-3 py-2 text-muted">{{ r.strategy_name_zh || r.strategy_name }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ r.entry_price }}</td>
            <td class="px-3 py-2 text-right font-mono text-rise">{{ r.stop_loss }}</td>
            <td class="px-3 py-2 text-right font-mono text-fall">{{ r.target_price }}</td>
            <td class="px-3 py-2 text-center">
              <button @click="doCancel(r)" class="text-xs text-red-400 hover:text-red-300">取消</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 持仓中 -->
      <table v-else-if="list.length && tab === 'holding'" class="w-full text-xs">
        <thead class="text-muted bg-white/[0.02]">
          <tr>
            <th class="px-3 py-2 text-left">代码</th>
            <th class="px-3 py-2 text-left">名称</th>
            <th class="px-3 py-2 text-right">成交价</th>
            <th class="px-3 py-2 text-right">现价</th>
            <th class="px-3 py-2 text-right">浮盈</th>
            <th class="px-3 py-2 text-right">止损</th>
            <th class="px-3 py-2 text-right">目标</th>
            <th class="px-3 py-2 text-right">股数</th>
            <th class="px-3 py-2 text-center">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in list" :key="r.id" class="border-t border-border hover:bg-white/[0.02]">
            <td class="px-3 py-2 font-mono">
              <a :href="getXueqiuUrl(r.code)" target="_blank" rel="noopener" title="在雪球查看"
                class="text-accent hover:underline">{{ r.code }}</a>
            </td>
            <td class="px-3 py-2">
              <span @click="goDetail(r.code)" class="cursor-pointer hover:text-accent">{{ r.name }}</span>
            </td>
            <td class="px-3 py-2 text-right font-mono">{{ r.fill_price }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ priceOf(r.code) ?? '-' }}</td>
            <td class="px-3 py-2 text-right font-mono" :class="pnlText(floatPnl(r))">
              {{ floatPnl(r) == null ? '-' : floatPnl(r).toFixed(2) + '%' }}
            </td>
            <td class="px-3 py-2 text-right font-mono text-rise">{{ r.stop_loss }}</td>
            <td class="px-3 py-2 text-right font-mono text-fall">{{ r.target_price }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ r.shares }}</td>
            <td class="px-3 py-2 text-center">
              <button @click="doClose(r)" class="text-xs text-amber-400 hover:text-amber-300">平仓</button>
            </td>
          </tr>
        </tbody>
      </table>

      <!-- 已平仓 -->
      <table v-else-if="list.length" class="w-full text-xs">
        <thead class="text-muted bg-white/[0.02]">
          <tr>
            <th class="px-3 py-2 text-left">代码</th>
            <th class="px-3 py-2 text-left">名称</th>
            <th class="px-3 py-2 text-left">战法</th>
            <th class="px-3 py-2 text-right">买入</th>
            <th class="px-3 py-2 text-right">卖出</th>
            <th class="px-3 py-2 text-center">原因</th>
            <th class="px-3 py-2 text-right">盈亏</th>
            <th class="px-3 py-2 text-center">平仓日</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in list" :key="r.id" class="border-t border-border hover:bg-white/[0.02]">
            <td class="px-3 py-2 font-mono">
              <a :href="getXueqiuUrl(r.code)" target="_blank" rel="noopener" title="在雪球查看"
                class="text-accent hover:underline">{{ r.code }}</a>
            </td>
            <td class="px-3 py-2">
              <span @click="goDetail(r.code)" class="cursor-pointer hover:text-accent">{{ r.name }}</span>
            </td>
            <td class="px-3 py-2 text-muted">{{ r.strategy_name_zh || r.strategy_name }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ r.fill_price }}</td>
            <td class="px-3 py-2 text-right font-mono">{{ r.exit_price }}</td>
            <td class="px-3 py-2 text-center">{{ reasonText(r.exit_reason) }}</td>
            <td class="px-3 py-2 text-right font-mono" :class="pnlText(r.pnl_pct)">
              {{ r.pnl_pct == null ? '-' : r.pnl_pct + '%' }}
            </td>
            <td class="px-3 py-2 text-center text-muted">{{ r.exit_date }}</td>
          </tr>
        </tbody>
      </table>

      <div v-if="!list.length" class="p-8 text-center text-muted text-xs">
        {{ emptyText }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  getPaperPositions, getPaperAccount, getPaperStats,
  cancelPaperPosition, closePaperPosition, getBatchPrices,
  getPaperRisk, unfreezePaperRisk,
} from '../api'
import { getXueqiuUrl } from '../composables/stockUtils'

const router = useRouter()

// 名称 → 个股详情页（与"我的持仓"一致）
function goDetail(code) {
  router.push(`/stock/${code}`)
}

const tab = ref('holding')
const tabs = [
  { key: 'pending', label: '待确认' },
  { key: 'holding', label: '持仓中' },
  { key: 'closed', label: '已平仓' },
  { key: 'stats', label: '战法统计' },
]
const account = ref({})
const stats = ref({})
const rows = ref([])
const prices = ref({})
const risk = ref(null)

const list = computed(() => rows.value.filter(r => r.status === tab.value))
const statRows = computed(() =>
  Object.entries(stats.value || {}).map(([name, v]) => ({ name, v })))

const emptyText = computed(() => ({
  pending: '暂无待确认仓位（盘后扫描后自动入池）',
  holding: '暂无持仓（待确认仓位在 9:35 开盘确认后转为持仓）',
  closed: '暂无已平仓记录',
}[tab.value] || ''))

function countOf(k) {
  return rows.value.filter(r => r.status === k).length
}

onMounted(() => { load(); loadRisk() })

async function load() {
  try {
    const [acc, st, pos] = await Promise.all([
      getPaperAccount().then(r => r.data).catch(() => ({})),
      getPaperStats().then(r => r.data).catch(() => ({})),
      getPaperPositions().then(r => r.data?.data || []).catch(() => []),
    ])
    account.value = acc || {}
    stats.value = st || {}
    rows.value = pos || []
    await loadPrices(rows.value)
  } catch (e) {
    console.error('[paper] 加载失败', e)
  }
}

async function loadRisk() {
  try {
    risk.value = await getPaperRisk().then(r => r.data)
  } catch (e) {
    console.error('[paper] 风控状态加载失败', e)
  }
}

async function doUnfreeze() {
  if (!confirm('解除回撤熔断并恢复开新仓？')) return
  try {
    await unfreezePaperRisk()
    await loadRisk()
    await load()
  } catch (e) {
    alert(e?.response?.data?.detail || '操作失败')
  }
}

async function loadPrices(all) {
  const codes = [...new Set((all || []).filter(r => r.status === 'holding').map(r => r.code))]
  if (!codes.length) { prices.value = {}; return }
  try {
    // ★ /score/batch-prices 返回的是列表 [{code, name, price, change_pct}]，
    //   不是 {code: price} 字典 → 先转 map 才能按代码取价（否则现价恒为空）
    const arr = await getBatchPrices(codes).then(r => r.data)
    const map = {}
    ;(arr || []).forEach(x => { if (x && x.code != null) map[x.code] = x.price })
    prices.value = map
  } catch {
    prices.value = {}
  }
}

function priceOf(code) {
  const p = prices.value?.[code]
  if (p == null) return null
  if (typeof p === 'number') return p
  return p.price ?? p.close ?? null
}

function floatPnl(r) {
  const p = priceOf(r.code)
  const fill = Number(r.fill_price)
  if (p == null || !fill) return null
  return (p - fill) / fill * 100
}

async function doCancel(r) {
  if (!confirm(`取消待确认仓位 ${r.name}(${r.code})？`)) return
  try {
    await cancelPaperPosition(r.id)
    await load()
  } catch (e) {
    alert(e?.response?.data?.detail || '取消失败')
  }
}

async function doClose(r) {
  if (!confirm(`手动平仓 ${r.name}(${r.code})？按最新收盘价结算`)) return
  try {
    await closePaperPosition(r.id)
    await load()
  } catch (e) {
    alert(e?.response?.data?.detail || '平仓失败')
  }
}

function reasonText(r) {
  return {
    stop_loss: '止损', take_profit: '止盈', expire: '超期',
    manual: '手动', fill_rejected: '未成交', manual_cancel: '已取消',
  }[r] || r || '-'
}

function money(v) {
  const n = Number(v || 0)
  return n.toLocaleString('zh-CN', { maximumFractionDigits: 0 })
}
function signed(v) {
  const n = Number(v || 0)
  return (n >= 0 ? '+' : '') + money(n)
}
function pnlText(v) {
  const n = Number(v || 0)
  return n > 0 ? 'text-rise' : (n < 0 ? 'text-fall' : 'text-muted')
}
function winRateText(v) {
  if (v == null) return 'text-muted'
  return v >= 55 ? 'text-rise' : (v < 45 ? 'text-fall' : 'text-muted')
}
</script>
