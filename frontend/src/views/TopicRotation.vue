<template>
  <div class="fade-in space-y-4">
    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg p-4 space-y-3">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">题材轮动</h1>
          <p class="text-xs text-muted mt-0.5">
            <b class="text-gray-300">风向标</b>：看哪个方向有人气、处在启动/主升/高位/退潮的哪一步，
            用来避免在没人气且在下行的方向硬扛。
          </p>
        </div>
        <div class="flex items-center gap-2">
          <input v-model="day" type="date"
                 class="px-2 py-1.5 rounded text-xs bg-bg border border-border text-gray-200" />
          <button @click="load" :disabled="loading"
                  class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ loading ? '加载中…' : '↻ 刷新' }}
          </button>
        </div>
      </div>
      <div class="flex items-center gap-4 text-xs flex-wrap">
        <div class="flex items-center gap-1.5">
          <span class="text-muted">视角</span>
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="t in TYPES" :key="t.v"
                    class="px-2.5 py-1 rounded transition-colors"
                    :class="plateType === t.v ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
                    @click="plateType = t.v; load()">{{ t.label }}</button>
          </div>
        </div>
        <span v-if="data.date" class="text-muted">
          数据日 <b class="text-gray-300">{{ data.date }}</b>
          <template v-if="data.prev_date"> · 对比 {{ data.prev_date }}</template>
        </span>
        <span v-if="error" class="text-fall">⚠️ {{ error }}</span>
      </div>
    </div>

    <!-- ① 市场环境 + 牛熊分层（核心） -->
    <div class="grid grid-cols-1 xl:grid-cols-[1fr_1.3fr] gap-3">
      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-sm font-bold text-gray-200 mb-2">🌡️ 当前市场环境</h2>
        <div class="flex items-baseline gap-3 mb-2">
          <span class="text-2xl font-bold"
                :class="regimeCls(data.regime?.level)">{{ data.regime?.label || '—' }}</span>
          <span class="text-xs text-muted">
            板块均涨 <b class="font-mono" :class="pctCls(data.regime?.avg_pct)">{{ fmt(data.regime?.avg_pct, 2) }}%</b>
            · 上涨占比 <b class="font-mono">{{ fmt((data.regime?.up_ratio || 0) * 100, 0) }}%</b>
            <template v-if="data.regime?.n"> · {{ data.regime.n }} 个板块</template>
          </span>
        </div>
        <p class="text-[11px] text-muted leading-relaxed">
          口径：104 个行业板块的横截面广度。**指数被权重股拉红、但多数板块在跌**的日子，
          做题材通常是亏钱的，所以这里看广度不看指数点位。
        </p>
        <div v-if="data.counts" class="mt-3 pt-3 border-t border-border/40 grid grid-cols-3 gap-2 text-center">
          <div><div class="text-lg font-bold text-gray-200">{{ data.counts.total }}</div>
            <div class="text-[10px] text-muted">在榜题材</div></div>
          <div><div class="text-lg font-bold text-accent">{{ data.counts.new_in }}</div>
            <div class="text-[10px] text-muted">新进榜</div></div>
          <div><div class="text-lg font-bold text-fall">{{ data.counts.drop }}</div>
            <div class="text-[10px] text-muted">掉队</div></div>
        </div>
      </div>

      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between mb-2">
          <h2 class="text-sm font-bold text-gray-200">📊 信号在不同环境下的表现</h2>
          <button @click="loadVerify" :disabled="vLoading"
                  class="text-[10px] px-2 py-0.5 rounded border border-border text-muted hover:text-accent disabled:opacity-50">
            {{ vLoading ? '计算中…' : (vRows.length ? '重算' : '计算') }}
          </button>
        </div>
        <p class="text-[11px] text-muted mb-2">
          「新进榜」题材持有 5 日的超额收益（扣掉同日全体题材均值）。<b class="text-gray-300">这才是判断能否用的依据</b>。
        </p>
        <table v-if="vRows.length" class="w-full text-[11px]">
          <thead class="text-muted">
            <tr class="border-b border-border/40">
              <th class="text-left py-1 font-normal">环境</th>
              <th class="text-right font-normal">超额</th>
              <th class="text-right font-normal">胜率</th>
              <th class="text-right font-normal">样本</th>
              <th class="text-right font-normal">判定</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in vRows" :key="r.label" class="border-b border-border/20">
              <td class="py-1">{{ r.label }}</td>
              <td class="text-right font-mono" :class="pctCls(r.excess)">{{ fmt(r.excess, 3) }}%</td>
              <td class="text-right font-mono"
                  :class="(r.win || 0) >= 0.5 ? 'text-gray-200' : 'text-fall'">
                {{ fmt((r.win || 0) * 100, 1) }}%
              </td>
              <td class="text-right font-mono text-muted">{{ r.n }}</td>
              <td class="text-right">
                <span class="px-1.5 py-0.5 rounded text-[10px]"
                      :class="verdictCls(r)">{{ verdictText(r) }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="text-xs text-muted py-3">
          点「计算」跑一次事件研究（约 1~2 秒，扫全表，不进轮询）。
        </div>
        <p v-if="vRows.length" class="text-[10px] text-muted mt-2 leading-relaxed">
          ⚠️ 判定分两级：<b class="text-gray-300">显著</b>=统计上非噪声；
          <b class="text-gray-300">可用</b>=还要胜率&gt;52% 且超额&gt;0.30%（覆盖双边成本）。
          实测：D+5 全样本超额 +0.157%、胜率 48.6% ⇒ 显著但**不可用**。
        </p>
      </div>
    </div>

    <!-- ② 四阶段 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <h2 class="text-sm font-bold text-gray-200 mb-1">🧭 阶段分布</h2>
      <p class="text-[11px] text-muted mb-3">
        规则化判定（不含预测）：<b class="text-gray-300">启动</b>=新进榜且加速且名次≤30；
        <b class="text-gray-300">主升</b>=前 10 且仍在加速；
        <b class="text-gray-300">高位</b>=前 10 但加速度转负；
        <b class="text-gray-300">退潮</b>=名次掉≥10 位或加速转负且名次靠后。
      </p>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-3">
        <div v-for="g in STAGES" :key="g.key"
             class="border border-border/60 rounded-lg p-3">
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-semibold" :class="g.cls">{{ g.label }}</span>
            <span class="text-[10px] text-muted font-mono">{{ (data.stages?.[g.key] || []).length }} 个</span>
          </div>
          <div class="space-y-1 max-h-[220px] overflow-y-auto pr-1">
            <div v-for="x in (data.stages?.[g.key] || [])" :key="x.code"
                 class="flex items-center justify-between gap-2 text-[11px]">
              <span class="truncate text-gray-300" :title="x.name">{{ x.name }}</span>
              <span class="font-mono shrink-0" :class="pctCls(x.sum_rate)">{{ fmt(x.sum_rate, 2) }}%</span>
            </div>
            <div v-if="!(data.stages?.[g.key] || []).length" class="text-[11px] text-muted">无</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ③ 加速榜 + ④ 变化榜 -->
    <div class="grid grid-cols-1 xl:grid-cols-2 gap-3">
      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-sm font-bold text-gray-200 mb-1">🚀 加速度榜</h2>
        <p class="text-[11px] text-muted mb-2">
          加速度 = 5 日累计 − 20 日累计/4。<b class="text-gray-300">正值大</b>说明近 5 日明显强于过去 20 日
          （刚启动）；负值说明前期涨过、现在在歇。
        </p>
        <div class="space-y-1">
          <div v-for="x in (data.accel || [])" :key="x.code"
               class="flex items-center gap-2 text-[11px] py-0.5">
            <span class="w-6 text-right font-mono text-muted shrink-0">{{ x.rank }}</span>
            <span class="flex-1 truncate text-gray-300">{{ x.name }}</span>
            <span class="font-mono text-muted shrink-0" :title="'20日累计'">
              {{ fmt(x.sum_rate_20d, 1) }}
            </span>
            <span class="font-mono shrink-0" :class="pctCls(x.sum_rate)">{{ fmt(x.sum_rate, 2) }}%</span>
            <span class="font-mono shrink-0 w-16 text-right"
                  :class="(x.accel || 0) > 0 ? 'text-rise' : 'text-fall'">
              {{ fmt(x.accel, 1) }}
            </span>
          </div>
        </div>
      </div>

      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-sm font-bold text-gray-200 mb-1">🔄 名次变化（对比前一日）</h2>
        <p class="text-[11px] text-muted mb-2">跃升 ≥3 位 / 掉队 ≥3 位。掉队榜是「资金正在撤」的直接证据。</p>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <div class="text-[11px] text-rise mb-1">跃升</div>
            <div class="space-y-0.5">
              <div v-for="x in (data.surge || [])" :key="x.code"
                   class="flex items-center gap-1.5 text-[11px]">
                <span class="font-mono text-rise w-9 text-right">+{{ x.rank_change }}</span>
                <span class="flex-1 truncate text-gray-300">{{ x.name }}</span>
                <span class="font-mono text-muted">{{ x.rank }}</span>
              </div>
              <div v-if="!(data.surge || []).length" class="text-[11px] text-muted">无</div>
            </div>
          </div>
          <div>
            <div class="text-[11px] text-fall mb-1">掉队</div>
            <div class="space-y-0.5">
              <div v-for="x in (data.drop || [])" :key="x.code"
                   class="flex items-center gap-1.5 text-[11px]">
                <span class="font-mono text-fall w-9 text-right">{{ x.rank_change }}</span>
                <span class="flex-1 truncate text-gray-300">{{ x.name }}</span>
                <span class="font-mono text-muted">{{ x.rank }}</span>
              </div>
              <div v-if="!(data.drop || []).length" class="text-[11px] text-muted">无</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ⑤ 新进榜 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <h2 class="text-sm font-bold text-gray-200 mb-1">🆕 新进榜（源端 is_new）</h2>
      <p class="text-[11px] text-muted mb-2">
        「相对 3 天前新进榜」的题材。⚠️ 已验证：这类题材 D+5 超额 +0.157%、胜率 48.6%，
        <b class="text-gray-300">均值被少数大涨拉高，多数在回落</b> ⇒ 只作方向参考，不是入场信号。
      </p>
      <div class="flex flex-wrap gap-1.5">
        <span v-for="x in (data.new_in || [])" :key="x.code"
              class="px-2 py-0.5 rounded text-[11px] bg-accent/10 text-accent border border-accent/20">
          {{ x.name }} <span class="font-mono text-[10px]">{{ fmt(x.sum_rate, 1) }}%</span>
        </span>
        <span v-if="!(data.new_in || []).length" class="text-[11px] text-muted">无</span>
      </div>
    </div>

    <p class="text-[10px] text-muted px-1">{{ data.note }}</p>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { getTopicRotationDashboard, getTopicRotationVerify } from '../api/modules/market'

const TYPES = [
  { v: 17, label: '题材' },
  { v: 15, label: '概念' },
  { v: 14, label: '行业' },
]
const STAGES = [
  { key: 'emerging', label: '🚀 启动', cls: 'text-rise' },
  { key: 'leading', label: '📈 主升', cls: 'text-accent' },
  { key: 'crowded', label: '⚠️ 高位', cls: 'text-amber-400' },
  { key: 'fading', label: '📉 退潮', cls: 'text-fall' },
]

const day = ref('')
const plateType = ref(17)
const data = ref({})
const loading = ref(false)
const error = ref('')
const vRows = ref([])
const vLoading = ref(false)

function fmt(v, n = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(n)
}
function pctCls(v) {
  if (v === null || v === undefined) return 'text-muted'
  return v > 0 ? 'text-rise' : (v < 0 ? 'text-fall' : 'text-muted')
}
function regimeCls(lv) {
  if (lv === 1) return 'text-rise'
  if (lv === -1) return 'text-fall'
  return 'text-amber-400'
}
function verdictText(r) {
  if (r.tradable) return '可用'
  if (r.valid) return '显著'
  return '无'
}
function verdictCls(r) {
  if (r.tradable) return 'bg-rise/15 text-rise'
  if (r.valid) return 'bg-amber-500/15 text-amber-400'
  return 'bg-white/5 text-muted'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = { plate_type: plateType.value }
    if (day.value) params.day = day.value
    const { data: d } = await getTopicRotationDashboard(params)
    if (!d || d.ok === false) {
      error.value = (d && d.error) || '无数据'
      data.value = {}
    } else {
      data.value = d
      if (!day.value && d.date) day.value = d.date
    }
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadVerify() {
  if (vLoading.value) return
  vLoading.value = true
  try {
    const out = []
    for (const [lv, label] of [[1, '强势'], [0, '中性'], [-1, '弱势']]) {
      try {
        const { data: r } = await getTopicRotationVerify({
          plate_type: plateType.value, horizon: 5, min_days: 10, regime: lv,
        })
        if (r && r.ok) {
          out.push({ label, excess: r.avg_excess_pct, win: r.win_rate,
                     n: r.n_events, valid: r.valid, tradable: r.tradable })
        } else {
          out.push({ label, excess: null, win: null, n: 0, valid: false, tradable: false })
        }
      } catch (e) {
        out.push({ label, excess: null, win: null, n: 0, valid: false, tradable: false })
      }
    }
    vRows.value = out
  } finally {
    vLoading.value = false
  }
}

onMounted(load)
</script>
