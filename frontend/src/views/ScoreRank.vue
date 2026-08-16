<template>
  <div class="fade-in space-y-4">
    <!-- 市场环境提示（独立信号，仅供参考，不改个股评分） -->
    <div v-if="temp.temperature != null" class="bg-card border border-border rounded-lg p-3 flex items-center justify-between flex-wrap gap-x-4 gap-y-1">
      <div class="flex items-center gap-2">
        <span class="text-xs text-muted">市场环境</span>
        <span class="text-base font-bold" :class="levelColor(temp.level)">{{ temp.level }} {{ temp.temperature }}</span>
      </div>
      <span class="text-xs text-gray-300 flex-1 min-w-[200px]">{{ temp.advisory }}</span>
      <span class="text-xs text-muted">建议买入线 <span class="text-accent font-bold">{{ temp.buy_threshold }}</span></span>
    </div>

    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <h2 class="text-lg font-bold">评分排行榜</h2>
        <div class="flex gap-2 flex-wrap">
          <button v-for="tab in tabs" :key="tab.key" @click="switchTab(tab.key)"
            :class="activeTab === tab.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
            class="px-3 py-1 rounded text-xs transition-colors">
            {{ tab.label }}
          </button>
          <select v-if="activeTab === 'signal'" v-model="signalType"
            class="bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300"
            @change="loadData">
            <option v-for="s in signalOptions" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
      </div>
    </div>

    <!-- 评分变动提醒（与上次快照对比） -->
    <div v-if="(scoreAlerts.upgrades.length || scoreAlerts.downgrades.length) && activeTab === 'top'"
      class="bg-card border border-border rounded-lg p-3 space-y-2">
      <div class="text-xs font-semibold text-muted">信号变动（对比最近快照）</div>
      <div v-if="scoreAlerts.upgrades.length" class="flex items-center gap-2 flex-wrap">
        <span class="text-xs text-emerald-400 flex-shrink-0">↑ 升级 {{ scoreAlerts.upgrades.length }} 只</span>
        <span v-for="s in scoreAlerts.upgrades.slice(0, 8)" :key="'u'+s.code"
          class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 cursor-pointer hover:bg-emerald-500/25"
          @click="goDetail(s.code)">
          {{ s.name }} {{ s.prevSignal }}→{{ s.signal }}
        </span>
        <span v-if="scoreAlerts.upgrades.length > 8" class="text-xs text-muted">+{{ scoreAlerts.upgrades.length - 8 }} 只</span>
      </div>
      <div v-if="scoreAlerts.downgrades.length" class="flex items-center gap-2 flex-wrap">
        <span class="text-xs text-red-400 flex-shrink-0">↓ 降级 {{ scoreAlerts.downgrades.length }} 只</span>
        <span v-for="s in scoreAlerts.downgrades.slice(0, 8)" :key="'d'+s.code"
          class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20 cursor-pointer hover:bg-red-500/25"
          @click="goDetail(s.code)">
          {{ s.name }} {{ s.prevSignal }}→{{ s.signal }}
        </span>
        <span v-if="scoreAlerts.downgrades.length > 8" class="text-xs text-muted">+{{ scoreAlerts.downgrades.length - 8 }} 只</span>
      </div>
    </div>

    <!-- 评分分布概览 -->
    <div v-if="stats.total > 0 && activeTab !== 'verify' && activeTab !== 'backtest'" class="bg-card border border-border rounded-lg p-4">
      <div class="grid grid-cols-3 md:grid-cols-5 gap-3 text-center text-sm">
        <div class="p-2 bg-bg rounded-lg">
          <div class="text-muted text-xs">评分股票数</div>
          <div class="text-lg font-bold mt-1">{{ stats.total }}</div>
        </div>
        <div class="p-2 bg-emerald-500/10 rounded-lg">
          <div class="text-emerald-400 text-xs">强烈买入/买入</div>
          <div class="text-lg font-bold text-emerald-400 mt-1">{{ stats.buyCount }}</div>
        </div>
        <div class="p-2 bg-amber-500/10 rounded-lg">
          <div class="text-amber-400 text-xs">观望</div>
          <div class="text-lg font-bold text-amber-400 mt-1">{{ stats.watchCount }}</div>
        </div>
        <div class="p-2 bg-red-500/10 rounded-lg">
          <div class="text-red-400 text-xs">卖出/强烈卖出</div>
          <div class="text-lg font-bold text-red-400 mt-1">{{ stats.sellCount }}</div>
        </div>
        <div class="p-2 bg-bg rounded-lg">
          <div class="text-muted text-xs">缓存状态</div>
          <div class="text-sm mt-1" :class="cacheStatus === 'ready' ? 'text-emerald-400' : 'text-amber-400'">
            {{ cacheStatus === 'ready' ? '就绪' : '加载中...' }}
          </div>
        </div>
      </div>
    </div>

    <!-- 数据表格 -->
    <div v-if="activeTab !== 'verify' && activeTab !== 'backtest'" class="bg-card border border-border rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">排名</th>
            <th class="text-left py-2.5 px-3">代码</th>
            <th class="text-left py-2.5 px-3">名称</th>
            <th class="text-right py-2.5 px-3">综合评分</th>
            <th class="text-center py-2.5 px-3">信号</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">买入时机</th>
            <th v-if="activeTab === 'top'" class="text-left py-2.5 px-3">买入原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in tableData" :key="item.code"
            class="border-b border-border/50 hover:bg-white/3 cursor-pointer transition-colors"
            @click="goDetail(item.code)">
            <td class="py-2 px-3 text-muted font-mono text-xs">{{ activeTab === 'bottom' ? stats.total - idx : idx + 1 }}</td>
            <td class="py-2 px-3 font-mono text-xs text-accent">
              <a :href="getXueqiuUrl(item.code)" target="_blank" rel="noopener"
                 @click.stop
                 class="hover:underline"
                 title="在雪球查看">{{ item.code }}</a>
            </td>
            <td class="py-2 px-3">{{ item.name }}</td>
            <td class="py-2 px-3 text-right">
              <span class="font-bold" :class="item.total_score >= 65 ? 'text-emerald-400' : item.total_score >= 45 ? 'text-amber-400' : 'text-red-400'">
                {{ item.total_score }}
              </span>
            </td>
            <td class="py-2 px-3 text-center">
              <span class="px-2 py-0.5 rounded-full text-xs"
                :class="item.signal.includes('买入') ? 'bg-emerald-500/20 text-emerald-400' :
                       item.signal.includes('卖出') ? 'bg-red-500/20 text-red-400' :
                       'bg-amber-500/20 text-amber-400'">
                {{ item.signal }}
              </span>
            </td>
            <!-- 买入时机列：仅 Top 50 显示，绿=适合介入 黄=等回调 红=追高风险 -->
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center">
              <span v-if="item.buy_point?.buy_timing" class="px-2 py-0.5 rounded-full text-xs font-medium"
                :class="item.buy_point.buy_timing === '适合介入' ? 'bg-emerald-500/20 text-emerald-400' :
                       item.buy_point.buy_timing === '等回调' ? 'bg-amber-500/20 text-amber-400' :
                       'bg-red-500/20 text-red-400'"
                :title="`MA20偏离${item.buy_point.ma20_deviation || 0}% · 布林位置${item.buy_point.boll_position ?? '-'} · 支撑位${item.buy_point.support || '-'}`">
                {{ item.buy_point.buy_timing }}
              </span>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <!-- 买入原因列：仅 Top 50 tab 显示，展示加分因素绿色小标签 -->
            <td v-if="activeTab === 'top'" class="py-2 px-3" @click.stop>
              <div v-if="item.factors_up && item.factors_up.length" class="flex flex-wrap gap-1">
                <span v-for="f in item.factors_up" :key="f"
                  class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
                  :title="'加分因素：' + f">{{ f }}</span>
              </div>
              <span v-else class="text-xs text-muted">-</span>
            </td>
          </tr>
          <tr v-if="!tableData.length">
            <td :colspan="activeTab === 'top' ? 8 : 5" class="py-12 text-center text-muted">
              {{ cacheStatus === 'loading' ? '行情数据加载中，请稍后...' : '暂无数据' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 胜率回查面板 -->
    <div v-if="activeTab === 'verify'" class="space-y-4">
      <!-- 操作栏 -->
      <div class="bg-card border border-border rounded-lg p-4 flex items-center justify-between flex-wrap gap-3">
        <h2 class="text-lg font-bold">推荐胜率回查</h2>
        <div class="flex gap-2">
          <button @click="captureSnapshot" :disabled="!tableData.length"
            class="px-3 py-1.5 rounded text-xs bg-accent/20 text-accent hover:bg-accent/30 transition-colors disabled:opacity-40">
            保存当前排行快照
          </button>
          <button @click="verifyAll" :disabled="!snapshotList.length || verifying"
            class="px-3 py-1.5 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors disabled:opacity-40">
            {{ verifying ? '查询中...' : '查询当前收益' }}
          </button>
        </div>
      </div>
      <!-- 汇总统计 -->
      <div v-if="verifySummary.total > 0" class="bg-card border border-border rounded-lg p-4">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-center text-sm">
          <div class="p-2 bg-bg rounded-lg">
            <div class="text-muted text-xs">快照次数</div>
            <div class="text-lg font-bold mt-1">{{ verifySummary.total }}</div>
          </div>
          <div class="p-2 bg-emerald-500/10 rounded-lg">
            <div class="text-emerald-400 text-xs">推荐盈利占比</div>
            <div class="text-lg font-bold text-emerald-400 mt-1">{{ verifySummary.winRate }}%</div>
          </div>
          <div class="p-2" :class="verifySummary.avgReturn >= 0 ? 'bg-emerald-500/10' : 'bg-red-500/10'">
            <div class="text-xs" :class="verifySummary.avgReturn >= 0 ? 'text-emerald-400' : 'text-red-400'">平均收益</div>
            <div class="text-lg font-bold mt-1" :class="verifySummary.avgReturn >= 0 ? 'text-emerald-400' : 'text-red-400'">
              {{ verifySummary.avgReturn >= 0 ? '+' : '' }}{{ verifySummary.avgReturn }}%
            </div>
          </div>
          <div class="p-2 bg-bg rounded-lg">
            <div class="text-muted text-xs">最近快照</div>
            <div class="text-sm mt-1 text-gray-300">{{ verifySummary.lastDate || '-' }}</div>
          </div>
        </div>
      </div>
      <!-- 快照列表 -->
      <div v-for="snap in snapshotList" :key="snap.date" class="bg-card border border-border rounded-lg overflow-hidden">
        <div class="p-3 flex items-center justify-between cursor-pointer hover:bg-white/3" @click="toggleSnap(snap.date)">
          <div class="flex items-center gap-3">
            <span class="text-sm font-bold">{{ snap.date }}</span>
            <span class="text-xs text-muted">{{ snap.stocks.length }} 只</span>
            <span v-if="snap.verified" class="text-xs" :class="snap.winRate >= 50 ? 'text-emerald-400' : 'text-red-400'">
              胜率 {{ snap.winRate }}% · 均收益 {{ snap.avgReturn >= 0 ? '+' : '' }}{{ snap.avgReturn }}%
              <span class="text-muted ml-1">({{ fmtVerifyTime(snap.verifiedAt) }})</span>
            </span>
          </div>
          <span class="text-muted text-xs">{{ expandedSnapshots.has(snap.date) ? '收起' : '展开' }}</span>
        </div>
        <div v-if="expandedSnapshots.has(snap.date)">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-t border-border text-muted text-xs">
                <th class="text-left py-2 px-3">代码</th>
                <th class="text-left py-2 px-3">名称</th>
                <th class="text-right py-2 px-3">评分</th>
                <th class="text-center py-2 px-3">信号</th>
                <th class="text-right py-2 px-3">快照价</th>
                <th class="text-right py-2 px-3">现价</th>
                <th class="text-right py-2 px-3">收益</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in snap.stocks" :key="s.code" class="border-b border-border/50 text-xs">
                <td class="py-1.5 px-3 font-mono text-accent">{{ s.code }}</td>
                <td class="py-1.5 px-3">{{ s.name }}</td>
                <td class="py-1.5 px-3 text-right">{{ s.score }}</td>
                <td class="py-1.5 px-3 text-center">
                  <span class="px-1.5 py-0.5 rounded-full text-[10px]"
                    :class="s.signal.includes('买入') ? 'bg-emerald-500/20 text-emerald-400' :
                           s.signal.includes('卖出') ? 'bg-red-500/20 text-red-400' :
                           'bg-amber-500/20 text-amber-400'">{{ s.signal }}</span>
                </td>
                <td class="py-1.5 px-3 text-right text-muted">{{ s.price || '-' }}</td>
                <td class="py-1.5 px-3 text-right">{{ s.currentPrice || '-' }}</td>
                <td class="py-1.5 px-3 text-right" :class="(s.returnPct || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">
                  {{ s.returnPct != null ? (s.returnPct >= 0 ? '+' : '') + s.returnPct + '%' : '-' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <!-- 空状态 -->
      <div v-if="!snapshotList.length" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
        暂无快照记录，收盘后点击「保存当前排行快照」开始记录
      </div>
    </div>

    <!-- 历史回测面板 -->
    <div v-if="activeTab === 'backtest'" class="space-y-4">
      <!-- 配置区 -->
      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-lg font-bold mb-3">技术面历史回测</h2>
        <p class="text-xs text-muted mb-4">用过去 N 天的技术面评分模拟选股，计算持有 M 天后的实际收益。回测池：市值前 100 只。</p>
        <div class="flex items-end gap-4 flex-wrap">
          <div>
            <label class="text-xs text-muted block mb-1">选股数</label>
            <select v-model.number="btConfig.topN" class="bg-bg border border-border rounded px-2 py-1 text-sm">
              <option :value="5">Top 5</option>
              <option :value="10">Top 10</option>
              <option :value="20">Top 20</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-muted block mb-1">回测天数</label>
            <select v-model.number="btConfig.days" class="bg-bg border border-border rounded px-2 py-1 text-sm">
              <option :value="30">30 天</option>
              <option :value="60">60 天</option>
              <option :value="90">90 天</option>
            </select>
          </div>
          <button @click="runBacktest" :disabled="btLoading"
            class="px-4 py-1.5 rounded text-sm bg-accent/20 text-accent hover:bg-accent/30 transition-colors disabled:opacity-40">
            {{ btLoading ? '回测中（约 30-60 秒）...' : '开始回测' }}
          </button>
        </div>
      </div>
      <!-- 结果区 -->
      <div v-if="btResult" class="bg-card border border-border rounded-lg p-4">
        <div class="text-xs text-muted mb-3">
          回测 {{ btResult.backtest_days }} 天 · {{ btResult.stock_pool_size }} 只股票池 · 每日选 Top {{ btResult.top_n }}
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div v-for="p in btResult.periods" :key="p" class="p-3 bg-bg rounded-lg text-center">
            <div class="text-muted text-xs mb-1">持有 {{ p }} 天</div>
            <div class="text-lg font-bold" :class="btResult.summary[p]?.avg_return >= 0 ? 'text-emerald-400' : 'text-red-400'">
              {{ btResult.summary[p]?.avg_return >= 0 ? '+' : '' }}{{ btResult.summary[p]?.avg_return }}%
            </div>
            <div class="text-xs mt-1">
              <span class="text-muted">胜率</span>
              <span class="font-bold" :class="btResult.summary[p]?.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'">
                {{ btResult.summary[p]?.win_rate }}%
              </span>
              <span class="text-muted ml-1">({{ btResult.summary[p]?.total }} 笔)</span>
            </div>
          </div>
        </div>
      </div>
      <!-- 空状态 -->
      <div v-if="!btResult && !btLoading" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
        配置参数后点击「开始回测」，验证技术面评分的历史预测力
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { getScoreTop, getScoreBottom, getScoreBySignal, getMarketTemperature, getBatchPrices, getBacktest } from '../api'
import { getXueqiuUrl } from '../composables/stockUtils'

const router = useRouter()

const tabs = [
  { key: 'top', label: '评分 Top 50' },
  { key: 'bottom', label: '评分 Bottom 50' },
  { key: 'signal', label: '按信号筛选' },
  { key: 'verify', label: '胜率回查' },
  { key: 'backtest', label: '历史回测' },
]
const signalOptions = ['强烈买入', '买入', '观望', '卖出', '强烈卖出']

const activeTab = ref('top')
const signalType = ref('买入')
const tableData = ref([])
const cacheStatus = ref('loading')
const stats = reactive({ total: 0, buyCount: 0, watchCount: 0, sellCount: 0 })
const temp = ref({})   // 市场环境温度（独立信号）

// ── 快照 / 胜率回查 ──
const SNAP_KEY = 'score_snapshots'
const snapshots = ref({})          // { 'YYYY-MM-DD': { ts, stocks: [...] } }
const snapshotList = computed(() =>
  Object.entries(snapshots.value)
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([date, d]) => ({ date, ...d }))
)
const expandedSnapshots = ref(new Set())
const verifying = ref(false)
const lastAutoSaveDate = ref('')
let autoSaveTimer = null

// ── 评分变动提醒 ──
const scoreAlerts = ref({ upgrades: [], downgrades: [] })

// ── 历史回测 ──
const btConfig = reactive({ topN: 10, days: 60 })
const btResult = ref(null)
const btLoading = ref(false)

const verifySummary = computed(() => {
  const verified = snapshotList.value.filter(s => s.verified)
  if (!verified.length) return { total: 0, winRate: 0, avgReturn: 0, lastDate: '' }
  let totalStocks = 0, wins = 0, totalReturn = 0
  for (const snap of verified) {
    for (const s of snap.stocks) {
      if (s.returnPct != null) {
        totalStocks++
        if (s.returnPct > 0) wins++
        totalReturn += s.returnPct
      }
    }
  }
  return {
    total: verified.length,
    winRate: totalStocks ? Math.round(wins / totalStocks * 100) : 0,
    avgReturn: totalStocks ? (totalReturn / totalStocks).toFixed(2) : 0,
    lastDate: verified[0]?.date || '',
  }
})

// ── 快照管理 ──
function toggleSnap(date) {
  const s = new Set(expandedSnapshots.value)
  s.has(date) ? s.delete(date) : s.add(date)
  expandedSnapshots.value = s
}

function loadSnapshots() {
  try {
    const raw = localStorage.getItem(SNAP_KEY)
    if (raw) snapshots.value = JSON.parse(raw)
  } catch { snapshots.value = {} }
}

function saveSnapshots() {
  localStorage.setItem(SNAP_KEY, JSON.stringify(snapshots.value))
}

async function captureSnapshot() {
  // 始终拉取最新 Top 50，不依赖当前 tab 的 tableData（可能是其他视图）
  let freshData
  try {
    const res = await getScoreTop({ limit: 50 })
    freshData = res.data.data || []
  } catch { return }
  if (!freshData.length) return

  const codes = freshData.map(i => i.code)
  let priceMap = {}
  try {
    const { data } = await getBatchPrices(codes)
    priceMap = Object.fromEntries(data.map(s => [s.code, s.price]))
  } catch { /* 价格获取失败，后续可回查 */ }

  // 价格覆盖率检查：低于 80% 则不保存（数据不完整）
  const withPrice = freshData.filter(i => priceMap[i.code] > 0).length
  if (withPrice < freshData.length * 0.8) return

  const today = new Date().toISOString().slice(0, 10)
  snapshots.value[today] = {
    ts: Date.now(),
    stocks: freshData.map(i => ({
      code: i.code, name: i.name, score: i.total_score,
      signal: i.signal, price: priceMap[i.code] || 0,
    })),
  }
  saveSnapshots()
  lastAutoSaveDate.value = today
}

function detectScoreChanges() {
  // 与最近一次快照对比，检测信号升降级
  const dates = Object.keys(snapshots.value).sort().reverse()
  if (!dates.length || !tableData.value.length) return
  const prev = snapshots.value[dates[0]]
  const prevMap = Object.fromEntries(prev.stocks.map(s => [s.code, s]))
  const upgrades = [], downgrades = []
  const signalRank = { '强烈买入': 2, '买入': 1, '观望': 0, '卖出': -1, '强烈卖出': -2 }
  for (const cur of tableData.value) {
    const p = prevMap[cur.code]
    if (!p) continue
    const oldR = signalRank[p.signal] ?? 0
    const newR = signalRank[cur.signal] ?? 0
    if (newR > oldR) upgrades.push({ ...cur, prevSignal: p.signal, prevScore: p.score })
    else if (newR < oldR) downgrades.push({ ...cur, prevSignal: p.signal, prevScore: p.score })
  }
  scoreAlerts.value = { upgrades, downgrades }
}

async function verifyAll() {
  if (!snapshotList.value.length) return
  verifying.value = true
  try {
    const allCodes = new Set()
    for (const snap of snapshotList.value) {
      for (const s of snap.stocks) allCodes.add(s.code)
    }
    const { data } = await getBatchPrices([...allCodes])
    const priceMap = Object.fromEntries(data.map(s => [s.code, s]))
    for (const [date, snap] of Object.entries(snapshots.value)) {
      let wins = 0, totalRet = 0, cnt = 0
      for (const s of snap.stocks) {
        const cur = priceMap[s.code]
        if (cur && s.price > 0) {
          s.currentPrice = cur.price
          s.returnPct = +((cur.price - s.price) / s.price * 100).toFixed(2)
          cnt++
          totalRet += s.returnPct
          if (s.returnPct > 0) wins++
        }
      }
      snap.verified = cnt > 0
      snap.verifiedAt = Date.now()
      snap.winRate = cnt ? Math.round(wins / cnt * 100) : 0
      snap.avgReturn = cnt ? +(totalRet / cnt).toFixed(2) : 0
    }
    saveSnapshots()   // 持久化验证结果，刷新页面后仍可看到
  } catch (e) { console.error(e) }
  verifying.value = false
}

function autoSaveCheck() {
  const now = new Date()
  if (now.getDay() === 0 || now.getDay() === 6) return
  const h = now.getHours(), m = now.getMinutes()
  if (h < 9 || h > 15) return
  const today = now.toISOString().slice(0, 10)
  if (lastAutoSaveDate.value === today) return
  if (!tableData.value.length) return
  // 15:10 后自动保存（给数据源留 10 分钟稳定时间）
  if (h === 15 && m >= 10 || h > 15) {
    captureSnapshot()
  }
}

async function runBacktest() {
  btLoading.value = true
  btResult.value = null
  try {
    const { data } = await getBacktest({
      top_n: btConfig.topN,
      days: btConfig.days,
    })
    if (data.error) {
      console.error(data.error)
    } else {
      btResult.value = data
    }
  } catch (e) { console.error(e) }
  btLoading.value = false
}

async function loadData() {
  try {
    let res
    if (activeTab.value === 'top') {
      res = await getScoreTop({ limit: 50 })
    } else if (activeTab.value === 'bottom') {
      res = await getScoreBottom({ limit: 50 })
    } else {
      res = await getScoreBySignal({ signal: signalType.value, limit: 50 })
    }
    const d = res.data
    tableData.value = d.data || []
    cacheStatus.value = d.cache_status || 'unknown'
    stats.total = d.total || 0
    // 简单统计
    stats.buyCount = tableData.value.filter(i => i.signal.includes('买入')).length
    stats.watchCount = tableData.value.filter(i => i.signal === '观望').length
    stats.sellCount = tableData.value.filter(i => i.signal.includes('卖出')).length
    // Top 50 加载完成后检测与上次快照的信号变动
    if (activeTab.value === 'top') detectScoreChanges()
  } catch (e) {
    console.error(e)
    cacheStatus.value = 'error'
  }
}

function switchTab(tab) {
  activeTab.value = tab
  if (tab !== 'verify' && tab !== 'backtest') loadData()
}

function goDetail(code) {
  router.push(`/stock/${code}`)
}

// 市场温度等级配色：冷→蓝，中性→琥珀，热→红
function levelColor(level) {
  return { '过热': 'text-red-400', '偏热': 'text-orange-400', '中性': 'text-amber-400',
           '偏冷': 'text-cyan-400', '过冷': 'text-blue-400' }[level] || 'text-muted'
}

// 验证时间格式化：显示“刚刚”或具体日期
function fmtVerifyTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const today = new Date()
  if (d.toDateString() === today.toDateString()) {
    return `今天 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function loadTemp() {
  try {
    const { data } = await getMarketTemperature()
    temp.value = data
  } catch (e) { console.error(e) }
}

onMounted(() => {
  loadData()
  loadTemp()
  loadSnapshots()
  autoSaveTimer = setInterval(autoSaveCheck, 60000)
})

onBeforeUnmount(() => {
  if (autoSaveTimer) clearInterval(autoSaveTimer)
})
</script>