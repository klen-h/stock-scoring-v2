<template>
  <!-- ★★ 2026-10-24（用户："快讯卡片过于简略……补充大盘走势、板块轮动、个股异动，
       突出异动原因和影响范围"）：工作台「快讯解读」卡。
       ★ 2026-10-26：位置已从"右栏"改为**主区右卡**（与「盘面速览」左右并排，均在观察器下方）。
       数据：`GET /flash/brief`（只读缓存/快照，不烧 LLM）—— 现由 `useFlashBrief` 单例供给，
             与盘面速览的持仓行共享同一条 60s 轮询；
       解读：`POST /flash/brief/refresh`（一次批量 LLM，重要条全进同一 prompt）。
       系统级：盘中异动由调度器 `brief_loop` 自动触发 → 浏览器通知（/flash/notifications）。 -->
  <div class="bg-card border border-border rounded-lg p-3 text-xs">
    <div class="flex items-center gap-2 flex-wrap">
      <button type="button" class="flex items-center gap-2"
              :title="bodyOpen ? '收起' : '展开'"
              @click="bodyOpen = !bodyOpen">
        <span class="text-sm font-semibold">快讯解读</span>
        <span class="text-[10px] text-muted">{{ bodyOpen ? '▾' : '▸' }}</span>
      </button>
      <span v-if="triggers.length"
            class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px] cursor-help"
            :title="'盘中异动：' + triggers.join('、')">异动 {{ triggers.length }}</span>
      <span v-if="asOf" class="text-[10px] text-muted font-mono">数据 {{ asOf }}</span>
      <router-link v-if="bodyOpen" to="/monitor" target="_blank" rel="noopener"
                   class="ml-auto text-[10px] text-accent hover:underline">全部快讯 ↗</router-link>
    </div>

    <template v-if="bodyOpen">
    <!-- 原有诊断一行（D 状态 / 主导叙事 / 仓位上限）—— 保留，避免丢信息。
         ★ 2026-10-25（用户）：去掉 truncate 省略号 ⇒ 换行完整展示。 -->
    <div v-if="flashDiag" class="mt-1 text-[10px] text-muted leading-relaxed"
         :title="diagnosisLine">
      {{ diagnosisLine }}
    </div>

    <div v-if="loadErr" class="mt-2 text-[11px] text-amber-400">{{ loadErr }}</div>
    <div v-else-if="!snapshot" class="mt-2 text-[11px] text-muted">加载中…</div>

    <template v-else>
      <!-- ① 大盘 -->
      <div class="mt-1.5 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[11px]">
        <span v-for="x in indices" :key="x.name">
          <span class="text-muted">{{ x.name }}</span>
          <b class="font-mono ml-0.5" :class="pctCls(x.chg_pct)">{{ fmtPct(x.chg_pct) }}</b>
        </span>
      </div>
      <div class="text-[10px] text-muted mt-0.5">
        <template v-if="market.up != null">
          涨/跌 {{ market.up }}/{{ market.down }}
          <template v-if="market.ratio != null"> · 涨跌比 {{ market.ratio }}</template>
        </template>
        <template v-if="market.limit_up != null"> · 涨停 {{ market.limit_up }}</template>
        <template v-if="market.limit_down"> · 跌停 {{ market.limit_down }}</template>
        <template v-if="market.amount_yi"> · 成交 {{ market.amount_yi }} 亿</template>
        <span v-if="market.up == null" class="text-amber-400/80"> · 涨跌统计数据不足</span>
      </div>

      <!-- ② 板块轮动 -->
      <div v-if="sectors.top.length || sectors.bottom.length"
           class="mt-1.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px]">
        <span class="text-muted">板块</span>
        <span v-for="s in sectors.top.slice(0, 3)" :key="'t' + s.name" class="text-rise">
          {{ s.name }} {{ fmtPct(s.chg_pct) }}
        </span>
        <span v-for="s in sectors.bottom.slice(0, 3)" :key="'b' + s.name" class="text-fall">
          {{ s.name }} {{ fmtPct(s.chg_pct) }}
        </span>
      </div>

      <!-- ③ 个股异动 -->
      <!-- ★ 2026-10-26（用户："异动加上股票代码，方便跳转去雪球"）：
           名称 → 本地详情页；**代码 → 雪球**（全站口径：名称走站内、代码走雪球）。 -->
      <div v-if="movers.length" class="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px]">
        <span class="text-muted">异动</span>
        <span v-for="m in movers" :key="m.code">
          <a :href="stockHref(m.code)" target="_blank" rel="noopener"
             class="hover:text-accent">{{ m.name }}</a>
          <a v-if="m.code" :href="xqUrl(m.code)" target="_blank" rel="noopener"
             class="ml-0.5 font-mono text-muted hover:text-accent" :title="`雪球 ${m.code}`">{{ m.code }}</a>
          <b class="font-mono ml-0.5" :class="pctCls(m.chg_pct)">{{ fmtPct(m.chg_pct) }}</b>
          <span class="text-muted/80">（{{ (m.tags || []).join('/') }}）</span>
        </span>
      </div>

      <!-- ④ 快讯卡片列表（点击展开原因 / 影响范围）
           ★ 2026-10-25（用户）：列表限高 + 溢出滚动，避免撑爆右栏 -->
      <div v-if="cards.length" class="mt-2 space-y-1 max-h-80 overflow-y-auto pr-1">
        <div v-for="(n, i) in cards" :key="n.key"
             class="rounded border border-border/60 bg-white/[0.04]">
          <button type="button" class="w-full text-left px-2 py-1.5"
                  @click="open = (open === i ? -1 : i)">
            <div class="flex items-center gap-1.5">
              <span class="font-mono text-[10px] text-muted">{{ hhmm(n.time) }}</span>
              <span class="px-1 rounded text-[10px]" :class="clusterCls(n.cluster)">{{ n.cluster }}</span>
              <span v-if="n.direction" class="text-[10px]" :class="dirCls(n.direction)">{{ n.direction }}</span>
              <span v-if="n.score >= 4" class="text-[10px] text-amber-300">重要</span>
              <span class="ml-auto text-[10px] text-muted">{{ open === i ? '▾' : '▸' }}</span>
            </div>
            <div class="mt-0.5 text-[11px] text-gray-200 leading-snug">{{ n.gist }}</div>
          </button>
          <div v-if="open === i" class="px-2 pb-2 pt-1.5 border-t border-border/40 space-y-1 text-[11px]">
            <!-- ★ 2026-10-25（用户："原因都是数据不足，是否正常"）：旧文案是 cause 为空时
                 的兜底，极具误导（实际是"该条尚未 AI 解读"，休市日 source=snapshot 属预期）
                 ⇒ 按来源显示诚实文案。 -->
            <div><span class="text-muted">原因　</span>{{ n.cause || causeFallback }}</div>
            <div><span class="text-muted">影响　</span>{{ n.scope || '—' }}</div>
            <div class="text-[10px] text-muted leading-relaxed">{{ n.text }}</div>
          </div>
        </div>
      </div>
      <div v-else class="mt-2 text-[11px] text-muted">
        近期没有命中规则的重要快讯（低热度条目已折叠，完整列表见「全部快讯」）。
      </div>

      <!-- ⑤ AI 结论 + 手动解读 -->
      <div class="mt-2 pt-2 border-t border-border/40">
        <div v-if="llm.conclusion" class="text-[11px] text-gray-200 leading-snug">
          <span class="text-accent">AI</span> {{ llm.conclusion }}
        </div>
        <div v-if="llm.market" class="text-[10px] text-muted mt-0.5 leading-relaxed">{{ llm.market }}</div>
        <!-- ★ 2026-10-24（用户："给推翻条件"）：可观测阈值，不是"走势反转"这种空话 -->
        <div v-if="llm.invalidate" class="text-[10px] text-amber-300/90 mt-1 leading-relaxed">
          <span class="text-amber-400">推翻条件</span> {{ llm.invalidate }}
        </div>
        <div class="flex items-center gap-2 mt-1">
          <button type="button"
                  class="px-2 py-0.5 rounded border border-border text-[10px] text-accent
                         hover:border-accent/50 disabled:opacity-50 disabled:cursor-not-allowed"
                  :disabled="busy"
                  title="一次批量解读（重要快讯打包进同一次 LLM 调用）；30 分钟内重复点读缓存"
                  @click="refresh">
            {{ busy ? '解读中…' : (llm.conclusion ? '重新解读' : 'AI 解读') }}
          </button>
          <span v-if="source" class="text-[10px] text-muted">
            {{ source === 'llm' ? 'AI 生成' : source === 'template' ? '规则兜底（LLM 未就绪）' : '快照' }}
          </span>
          <span v-if="cached" class="text-[10px] text-muted">· 缓存</span>
        </div>
      </div>

      <!-- ★ 2026-10-26（第①步：动态盘面 × 消息面融合）：
           原「⑥ 持仓影响」整块**已移除** —— 它讲的是"某只持仓被哪条快讯影响"，
           与上方「持仓状态」是同一批标的的两半信息 ⇒ 现改为**投影到持仓行**
           （`盘面速览` 的持仓行显示 利好N/承压N 徽标，展开即命中原文）。
           规则：快讯列表是原始流，持仓行是它的投影，同一信息只出现一次。 -->
    </template>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { refreshFlashBrief } from '../../api'
// ★ 2026-10-26（第①步：动态盘面 × 消息面融合）：取数上提为**共享单例**
//   —— 本组件与「盘面速览」的持仓行消费同一份快照（一条 60s 轮询，引用计数），
//   组件不再自己 GET/自己 setInterval。
import { useFlashBrief } from '../../composables/useFlashBrief'
// ★ 2026-10-26（用户："异动加代码跳雪球"）：xqUrl = 个股 → 雪球（SH/SZ/BJ 自动拼前缀）；
//   stockHref = 站内详情（hash 路由）。`phaseCn` 曾服务于卡内「持仓影响」块，
//   该块已投影到「盘面速览」的持仓行 ⇒ 本组件不再需要。
import { xqUrl, stockHref } from '../../composables/displayMeta'

const props = defineProps({
  date: { type: String, default: '' },
  flashDiag: { type: Object, default: null },
})

const fb = useFlashBrief()
const data = fb.data             // 共享单例（勿在本地再建 ref，否则两处不一致）
const busy = ref(false)
const loadErr = fb.err
const open = ref(-1)
const bodyOpen = ref(true)   // ★ 2026-10-25（用户）：整卡支持收起/展开

const snapshot = computed(() => data.value?.snapshot || null)
const market = computed(() => snapshot.value?.market || {})
const indices = computed(() => market.value.indices || [])
const sectors = computed(() => snapshot.value?.sectors || { top: [], bottom: [] })
const movers = computed(() => (snapshot.value?.movers?.rows || []).slice(0, 4))
const llm = computed(() => data.value?.llm || {})
const asOf = computed(() => data.value?.as_of || '')
const source = computed(() => data.value?.source || '')
const cached = computed(() => !!data.value?.cached)
const triggers = computed(() => data.value?.trigger || [])

// ★ 2026-10-25（用户："原因都是数据不足，是否正常"）：cause 空 = 该条**尚无 AI 逐条解读**
//   （休市日 source=snapshot 属预期；或 source=template 规则兜底）——按来源给诚实文案。
const causeFallback = computed(() => {
  if (source.value === 'llm') return '—'
  return '尚未 AI 解读——点击下方「AI 解读」生成'
})

const diagnosisLine = computed(() => {
  const d = props.flashDiag || {}
  const corr = d.correlation_diagnosis || {}
  const parts = []
  if (corr.correlation_state) parts.push(corr.correlation_state)
  if (d.dominant_narrative?.narrative) parts.push(d.dominant_narrative.narrative)
  if (d.daily_strategy?.overall_position) parts.push('仓位 ' + d.daily_strategy.overall_position)
  return parts.join(' · ')
})

// 快讯卡：规则条目（snapshot.news）+ LLM 逐条解读（llm.news[idx]）合并
const cards = computed(() => {
  const rows = snapshot.value?.news?.rows || []
  const map = {}
  for (const x of (llm.value.news || [])) map[Number(x.idx)] = x
  return rows.map((r, i) => {
    const l = map[i + 1] || {}
    return {
      key: r.id || i,
      time: r.time,
      cluster: r.cluster || '其他',
      score: r.score || 0,
      text: r.text,
      gist: l.gist || r.gist,
      cause: l.cause || '',
      scope: l.scope || r.scope,
      direction: l.direction || r.direction,
    }
  })
})

function fmtPct(v) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}
function pctCls(v) {
  const n = Number(v) || 0
  return n > 0 ? 'text-rise' : (n < 0 ? 'text-fall' : 'text-muted')
}
function dirCls(d) {
  if (d === '多' || d === '利好') return 'text-rise'
  if (d === '空' || d === '利空') return 'text-fall'
  return 'text-muted'
}
function clusterCls(c) {
  if (['中东战争', '伊朗局势'].includes(c)) return 'bg-rose-500/15 text-rose-300'
  if (['美联储利率', '中美贸易'].includes(c)) return 'bg-sky-500/15 text-sky-300'
  if (['国内政策'].includes(c)) return 'bg-amber-500/15 text-amber-300'
  if (['原油能源', '黄金贵金属'].includes(c)) return 'bg-orange-500/15 text-orange-300'
  return 'bg-white/5 text-muted'
}
function hhmm(t) {
  const s = String(t || '')
  return s.length >= 16 ? s.slice(11, 16) : s
}
// ★ 2026-10-26：删掉本地 `stockHref`（返回 `/stock/${code}`）—— 路由是 **hash 模式**，
//   裸路径会落到站点根 ⇒ 打开 404。统一用 `displayMeta.stockHref`（`#/stock/${code}`）。

async function refresh() {
  if (busy.value) return
  busy.value = true
  try {
    const { data: d } = await refreshFlashBrief(props.date || undefined)
    if (d && d.ok === false) {
      loadErr.value = d.error || '解读失败'
    } else {
      // ★ 写进**共享状态**（不是本地 ref）：持仓行的消息徽标会同步更新
      fb.apply(d)
      open.value = -1
    }
  } catch (e) {
    loadErr.value = '解读失败：' + (e?.message || e)
  } finally {
    busy.value = false
  }
}

// 换日：只改共享源的日期并重取（本组件不再持有独立数据）
watch(() => props.date, (d) => { open.value = -1; fb.load(d) })

// 轮询的生命周期交给共享单例（引用计数：本组件 + 盘面速览各一份订阅，仍只有一条 60s 定时器）
onMounted(() => { fb.start(props.date || '') })
onBeforeUnmount(() => { fb.stop() })
</script>
