<template>
  <div class="fade-in space-y-4">
    <!-- ① 头部 + 添加 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <div class="flex items-center gap-2 flex-wrap">
            <h1 class="text-lg font-bold text-gray-100">个股盯盘</h1>
            <!-- ★ 2026-11-28：默认跟随持仓（清单为空时自动取持仓前 5 只） -->
            <span v-if="status.mode === 'portfolio'"
              class="px-1.5 py-0.5 rounded text-[10px] bg-sky-500/15 text-sky-300 border border-sky-500/30">
              默认跟随持仓
            </span>
            <span v-else-if="status.mode === 'manual'"
              class="px-1.5 py-0.5 rounded text-[10px] bg-white/5 text-muted border border-border">
              自定义清单 {{ status.n_manual }}
            </span>
          </div>
          <p class="text-xs text-muted mt-0.5">
            最多 <b class="text-gray-300">{{ status.max_codes || 5 }}</b> 只 ·
            阻力=昨收 · 支撑=min(昨收, 近5日最低) · 盘中 60 秒一刷 ·
            突破/跌破/大额压单推企微
          </p>
          <p v-if="status.mode === 'portfolio'" class="text-[10px] text-muted mt-0.5">
            清单为空时自动盯你的持仓（最多 5 只）；添加任意一只后转为自定义清单。
          </p>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <input v-model="newCode" maxlength="6" placeholder="6 位代码"
            @keyup.enter="add"
            class="w-24 px-2 py-1.5 rounded text-xs bg-bg border border-border text-gray-200 placeholder:text-muted focus:border-accent/40 focus:outline-none font-mono" />
          <input v-model="newName" placeholder="名称（可选）"
            @keyup.enter="add"
            class="w-28 px-2 py-1.5 rounded text-xs bg-bg border border-border text-gray-200 placeholder:text-muted focus:border-accent/40 focus:outline-none" />
          <button @click="add" :disabled="adding"
            class="px-3 py-1.5 rounded text-xs bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors disabled:opacity-50">
            {{ adding ? '…' : '＋ 添加' }}
          </button>
          <button @click="scan" :disabled="scanning"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ scanning ? '扫描中…' : '↻ 扫描' }}
          </button>
        </div>
      </div>
      <p v-if="err" class="text-[11px] text-fall mt-2">⚠️ {{ err }}</p>
      <p v-if="scanMsg" class="text-[11px] text-muted mt-2">{{ scanMsg }}</p>
    </div>

    <!-- ② 空态 -->
    <div v-if="!loading && !(status.targets || []).length"
      class="bg-card border border-border rounded-lg p-8 text-center text-xs text-muted">
      没有持仓，也没有自定义清单<br />
      <span class="text-[10px] mt-1 inline-block">
        输入 6 位代码后回车添加（最多 5 只）；有持仓时会自动跟随持仓
      </span>
    </div>

    <!-- ③ 盯盘卡：★ 2026-11-28 两只股票**左右并排**（最多 5 只自动换行）。
         原来每张卡占满整行，2 只要滚两屏；现在一行两张，盘口卖/买仍并排、高度不变。 -->
    <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
    <div v-for="t in (status.targets || [])" :key="t.code"
      class="bg-card border rounded-lg p-3 min-w-0"
      :class="hitKinds(t.code).length ? 'border-amber-500/50' : 'border-border'">
      <!-- 头部：名称（代码）+ 现价 + 移除 -->
      <div class="flex items-center justify-between gap-2 flex-wrap mb-2">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-sm font-bold"><StockName :code="t.code" :name="t.name" /></span>
          <template v-if="snapOf(t.code)">
            <span class="text-sm font-mono text-gray-100">{{ fmtP(snapOf(t.code).price) }}</span>
            <span class="text-xs font-mono" :class="pctCls(chgOf(t.code))">{{ fmtPct(chgOf(t.code)) }}</span>
          </template>
          <span v-else class="text-[11px] text-muted">（点「扫描」取实时）</span>
          <span v-for="k in hitKinds(t.code)" :key="k"
            class="px-1.5 py-0.5 rounded text-[10px]"
            :class="k === 'breakout' ? 'bg-rise/15 text-rise'
                  : k === 'breakdown' ? 'bg-fall/15 text-fall'
                  : 'bg-amber-500/15 text-amber-300'">
            {{ KIND_CN[k] || k }}
          </span>
        </div>
        <!-- 跟随持仓的标的没有"移除"（移除的是持仓本身，不归这里管） -->
        <button v-if="t.source === 'manual'" @click="remove(t.code)"
          class="text-[10px] px-2 py-0.5 rounded border border-border text-muted hover:text-fall hover:border-fall/40 transition-colors">
          移除
        </button>
        <span v-else class="text-[10px] text-muted">来自持仓</span>
      </div>

      <!-- 关键位 -->
      <div class="flex items-center gap-2 text-[11px] mb-3 flex-wrap">
        <span class="text-muted">阻力
          <b class="font-mono text-rise">{{ fmtP(t.resistance) }}</b>
          <span class="text-[10px]">（{{ t.r_src || '—' }}）</span>
        </span>
        <span class="text-muted">支撑
          <b class="font-mono text-fall">{{ fmtP(t.support) }}</b>
          <span class="text-[10px]">（{{ t.s_src || '—' }}）</span>
        </span>
        <span v-if="snapOf(t.code)" class="text-muted">
          委差 <b class="font-mono" :class="(snapOf(t.code).bid_diff || 0) >= 0 ? 'text-rise' : 'text-fall'">
            {{ fmtVol(snapOf(t.code).bid_diff) }}
          </b>
        </span>
      </div>

      <!-- 五档盘口 -->
      <div v-if="snapOf(t.code) && (snapOf(t.code).asks || []).length" class="grid grid-cols-2 gap-2">
        <!-- 卖盘：卖5 在上，卖1 在下 -->
        <div class="space-y-0.5">
          <div class="text-[10px] text-muted mb-1">卖盘（压单阈值 {{ fmtVol(snapOf(t.code).wall_thr) }} 手）</div>
          <div v-for="(a, i) in asksDesc(t.code)" :key="'a' + i" class="flex items-center gap-2 text-[10px]">
            <span class="w-6 text-muted shrink-0">卖{{ 5 - i }}</span>
            <span class="w-14 text-right font-mono text-fall shrink-0">{{ fmtP(a[0]) }}</span>
            <div class="flex-1 h-3 bg-bg/60 rounded overflow-hidden">
              <div class="h-full bg-fall/40 rounded-r"
                :style="{ width: barW(a[1], maxVol(t.code)) }"></div>
            </div>
            <span class="w-14 text-right font-mono shrink-0"
              :class="a[1] >= (snapOf(t.code).wall_thr || 1e18) ? 'text-fall font-bold' : 'text-muted'">
              {{ fmtVol(a[1]) }}
            </span>
          </div>
        </div>
        <!-- 买盘：买1 在上 -->
        <div class="space-y-0.5">
          <div class="text-[10px] text-muted mb-1">买盘</div>
          <div v-for="(b, i) in (snapOf(t.code).bids || [])" :key="'b' + i" class="flex items-center gap-2 text-[10px]">
            <span class="w-6 text-muted shrink-0">买{{ i + 1 }}</span>
            <span class="w-14 text-right font-mono text-rise shrink-0">{{ fmtP(b[0]) }}</span>
            <div class="flex-1 h-3 bg-bg/60 rounded overflow-hidden">
              <div class="h-full bg-rise/40 rounded-r"
                :style="{ width: barW(b[1], maxVol(t.code)) }"></div>
            </div>
            <span class="w-14 text-right font-mono shrink-0"
              :class="b[1] >= (snapOf(t.code).wall_thr || 1e18) ? 'text-rise font-bold' : 'text-muted'">
              {{ fmtVol(b[1]) }}
            </span>
          </div>
        </div>
      </div>

      <!-- 今日已触发信号 -->
      <div v-if="sigOf(t.code).length" class="mt-3 pt-2 border-t border-border/30 space-y-1">
        <div v-for="(s, i) in sigOf(t.code)" :key="i" class="text-[11px] text-gray-300">
          <span class="font-mono text-muted mr-2">{{ (s.pushed_at || '').slice(11, 16) }}</span>
          <span class="px-1 py-0.5 rounded text-[10px] mr-1"
            :class="s.kind === 'breakout' ? 'bg-rise/15 text-rise'
                  : s.kind === 'breakdown' ? 'bg-fall/15 text-fall'
                  : 'bg-amber-500/15 text-amber-300'">{{ KIND_CN[s.kind] || s.kind }}</span>
          {{ s.detail }}
        </div>
      </div>
    </div>
    </div>

    <!-- ④ 规则说明（随接口下发，前端不硬编码口径） -->
    <div class="bg-card border border-border rounded-lg p-3">
      <div class="text-xs font-bold text-gray-200 mb-2">口径与边界</div>
      <ul class="text-[11px] text-muted space-y-1 leading-relaxed">
        <li>· <b class="text-gray-300">阻力 = 昨收价</b>：昨日收盘是最强心理位，昨日堆在该价的卖单会延续到今日</li>
        <li>· <b class="text-gray-300">支撑 = min(昨收, 近 5 日最低)</b>：跌破昨收后，下方看近 5 日实测低点</li>
        <li>· 突破/跌破阈值 {{ ((status.rules?.break_pct || 0.003) * 100).toFixed(1) }}%；
            压单阈值 max({{ fmtVol(status.rules?.wall_abs || 200000) }} 手, 每分钟均量×{{ status.rules?.wall_ratio || 3 }})</li>
        <li>· <b class="text-gray-300">压单需连续 2 次快照</b>才推送 —— 挂单随时可撤，单次快照多是幌骗</li>
        <li>· 每只票每类信号每日最多 1 条；盘中自动扫描间隔 {{ status.rules?.interval_sec || 60 }} 秒</li>
        <li>· ⚠️ <b class="text-gray-300">观察器，不是买卖建议</b>；阈值未回测校准，仅供盯盘参考</li>
      </ul>
    </div>
  </div>
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import StockName from '../components/StockName.vue'
// displayMeta 导出的是 pctClass（TopicRotation 里的 pctCls 是它自己的本地函数）
import { pctClass as pctCls } from '../composables/displayMeta'
import {
  getWatchRadar, addWatchTarget, removeWatchTarget, scanWatchRadar,
} from '../api/modules/market'

const KIND_CN = {
  breakout: '▲ 突破', breakdown: '▼ 跌破',
  wall_ask: '🧱 压单', wall_bid: '🛡 托单',
}

const status = ref({})
const snaps = ref({})            // {code: snapshot}（含五档）
const loading = ref(false)
const scanning = ref(false)
const adding = ref(false)
const err = ref('')
const scanMsg = ref('')
const newCode = ref('')
const newName = ref('')
let timer = null

function fmtP(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}
function fmtVol(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  const a = Math.abs(n)
  if (a >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  if (a >= 1e4) return (n / 1e4).toFixed(0) + '万'
  return n.toFixed(0)
}
function fmtPct(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return (n >= 0 ? '+' : '') + n.toFixed(2) + '%'
}

function snapOf(code) { return snaps.value[code] || null }

function chgOf(code) {
  const s = snapOf(code)
  if (!s || !s.prev_close) return null
  return (s.price / s.prev_close - 1) * 100
}

function asksDesc(code) {
  const s = snapOf(code)
  const a = (s && s.asks) || []
  return a.slice().reverse()          // 卖5 在上、卖1 在下
}

function maxVol(code) {
  const s = snapOf(code)
  if (!s) return 1
  const all = [...(s.asks || []), ...(s.bids || [])].map(x => Number(x[1]) || 0)
  return Math.max(1, ...all)
}

function barW(v, max) {
  const n = Number(v) || 0
  return Math.max(2, Math.min(100, (n / (max || 1)) * 100)) + '%'
}

// 本次扫描得到的信号（push=false 不落库，只在本页展示）
const liveSignals = ref([])

function sigOf(code) {
  const saved = (status.value.signals || []).filter(s => s.code === code)
  // 已落库的优先；本次新扫出的补在后面（按 kind 去重，避免同一信号重复两行）
  const extra = liveSignals.value.filter(
    s => s.code === code && !saved.some(x => x.kind === s.kind))
  return [...saved, ...extra]
}
function hitKinds(code) {
  return [...new Set(sigOf(code).map(s => s.kind))]
}

async function reload() {
  loading.value = true
  err.value = ''
  try {
    const { data: d } = await getWatchRadar()
    status.value = (d && d.ok) ? d : {}
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function scan(silent = false) {
  if (!(status.value.targets || []).length) {
    if (!silent) scanMsg.value = '清单为空，先添加标的'
    return
  }
  scanning.value = true
  try {
    const { data: d } = await scanWatchRadar(false)   // false = 不推企微（页面查看）
    if (d && d.ok) {
      const m = {}
      for (const s of (d.snapshots || [])) m[s.code] = s
      snaps.value = m
      // 本次信号（后端 push=false 时不落库，这里本地留一份用于展示）
      liveSignals.value = (d.signals || []).map(s => ({
        ...s, pushed_at: s.first_time || new Date().toISOString().slice(0, 19).replace('T', ' '),
      }))
      if (!silent) {
        scanMsg.value = d.n_signal
          ? `本次命中 ${d.n_signal} 个信号（已落库去重，盘中自动推企微）`
          : `扫描完成：${d.n} 只，暂无新信号`
      }
      await reload()
    } else {
      err.value = (d && d.error) || '扫描失败'
    }
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message || '扫描失败'
  } finally {
    scanning.value = false
  }
}

async function add() {
  const code = newCode.value.trim()
  if (!/^\d{6}$/.test(code)) { err.value = '请输入 6 位数字代码'; return }
  adding.value = true
  err.value = ''
  try {
    await addWatchTarget(code, newName.value.trim())
    newCode.value = ''
    newName.value = ''
    await reload()
    await scan(true)
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message || '添加失败'
  } finally {
    adding.value = false
  }
}

async function remove(code) {
  if (!confirm(`移除 ${code}？`)) return
  try {
    await removeWatchTarget(code)
    delete snaps.value[code]
    await reload()
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message || '移除失败'
  }
}

function inTrading() {
  const n = new Date()
  const w = n.getDay()
  if (w === 0 || w === 6) return false
  const t = n.getHours() * 100 + n.getMinutes()
  return (t >= 915 && t <= 1130) || (t >= 1300 && t <= 1505)
}

onMounted(async () => {
  await reload()
  if ((status.value.targets || []).length) await scan(true)
  // 页面开着时盘中每 60 秒自动扫一次（与后端 watch_radar_loop 同频）
  timer = setInterval(() => { if (inTrading()) scan(true) }, 60000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>
