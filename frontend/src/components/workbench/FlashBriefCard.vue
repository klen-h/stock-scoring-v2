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
      <!-- ★ 2026-10-28（方案 2）：60s 轮询是静默替换 ⇒ 显式告诉用户"这次多了几条"。
           只统计**展示出来的那几条**里窗口内新增的，不虚报。 -->
      <span v-if="!isHistory && newsNew"
            class="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 text-[10px] cursor-help"
            :title="`近 ${newsWindow} 分钟内新增的重要快讯（条目左侧蓝点）`">新 {{ newsNew }}</span>
      <span v-if="asOf" class="text-[10px] text-muted font-mono">数据 {{ asOf }}</span>
      <router-link v-if="bodyOpen" to="/monitor" target="_blank" rel="noopener"
                   class="ml-auto text-[10px] text-accent hover:underline">全部快讯 ↗</router-link>
    </div>

    <template v-if="bodyOpen">
    <!-- ★★ 2026-10-26（方案 B）：一天 1~8 次解读 ⇒ 时间 tab 回看。
         与「李大霄视角」同款交互：水平滚动、选中自动居中、按 run_id 记忆；
         tab 只承载"历史判断"，当前结论永远是第一个 tab（最新）。 -->
    <div v-if="runs.length > 1" class="mt-1.5 flex gap-1 overflow-x-auto pb-1 -mx-1 px-1 wb-hscroll">
      <button v-for="(r, i) in runs" :key="r.run_id"
              :ref="(el) => setTab(el, i)"
              type="button"
              class="shrink-0 px-1.5 py-0.5 rounded border text-[10px] transition-colors"
              :class="(selRunId ? r.run_id === selRunId : i === 0)
                ? 'bg-accent/20 text-accent border-accent/40'
                : 'bg-white/5 text-muted border-border hover:text-gray-200'"
              :title="`${trigCn(r.trigger)} · ${r.source === 'template' ? '规则兜底（LLM 未就绪）' : r.source === 'backfill' ? '历史回补' : 'AI 解读'}${r.conclusion ? '：' + r.conclusion : ''}`"
              @click="pickRun(r, i)">
        <span class="font-mono">{{ r.time || '—' }}</span>
        <span class="ml-0.5">{{ trigCn(r.trigger) }}</span>
        <span v-if="r.source === 'template'" class="ml-0.5 opacity-60">规则</span>
      </button>
    </div>
    <div v-if="isHistory && hist" class="mt-1 text-[10px] text-amber-300/90">
      历史回看 · {{ String(hist.ts || '').slice(11, 16) }} 的判断（只读）；最新结论请点第一个 tab
    </div>
    <div v-else-if="histLoading" class="mt-1 text-[10px] text-muted">读取历史解读…</div>
    <div v-if="histErr" class="mt-1 text-[10px] text-amber-400">{{ histErr }}</div>

    <!-- 原有诊断一行（D 状态 / 主导叙事 / 仓位上限）—— 保留，避免丢信息。
         ★ 2026-10-25（用户）：去掉 truncate 省略号 ⇒ 换行完整展示。 -->
    <div v-if="flashDiag" class="mt-1 text-[10px] text-muted leading-relaxed"
         :title="diagnosisLine">
      {{ diagnosisLine }}
    </div>

    <div v-if="loadErr" class="mt-2 text-[11px] text-amber-400">{{ loadErr }}</div>
    <div v-else-if="!snapshot" class="mt-2 text-[11px] text-muted">加载中…</div>

    <template v-else>
      <!-- ★ 2026-10-26（用户："盘面速览的涨跌/主线和快讯解读的相同部分有什么区别"）：
           答案 = **没有区别**，都是同一份行情换了个位置画。故这里删掉两处重复：
             · 原「① 大盘」（指数 + 涨/跌 + 涨跌比 + 涨停/跌停 + 成交额）
               ⇒ 指数在**顶栏**、家数/涨停/成交额在**盘面速览的涨跌条**，全都有了；
             · 原「② 板块轮动」的**领涨 3 个** ⇒ 已由**主线板块 Top5**（含领涨股）覆盖；
           只保留唯一增量：**领跌 3 个**（盘面速览只讲强的一侧，弱的这侧只有这里有）。 -->
      <!-- ★★ 2026-10-28（用户："我看到领跌是昨日的快照"）：不是过期，是**把"最弱"当成了"下跌"**。
           后端旧实现取"涨幅最小 4 个"，全市场普涨那天就出现"+1.45% 领跌"（跌色渲染正数）
           ⇒ 用户只能怀疑数据是昨天的。现后端只把 `change_pct < 0` 的算领跌；
           全线上涨时给 `bottom_all_up` + `weakest`，这里如实说"无下跌板块（最弱 …）"。 -->
      <div v-if="!isHistory && sectors.bottom.length" class="mt-1.5 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px]">
        <span class="text-muted">领跌</span>
        <span v-for="s in sectors.bottom.slice(0, 3)" :key="'b' + s.name" class="text-fall">
          {{ s.name }} {{ fmtPct(s.chg_pct) }}
        </span>
      </div>
      <div v-else-if="!isHistory && sectors.bottom_all_up" class="mt-1.5 flex flex-wrap gap-x-2 text-[10px]">
        <span class="text-muted">领跌</span>
        <span class="text-muted" :title="'全部行业板块上涨，没有下跌板块；这里给的是最弱的一个（仍在涨）'">
          无（全行业上涨<template v-if="sectors.weakest">，最弱 {{ sectors.weakest.name }} {{ fmtPct(sectors.weakest.chg_pct) }}</template>）</span>
      </div>

      <!-- ③ 个股异动 -->
      <!-- ★ 2026-10-26（用户："异动加上股票代码，方便跳转去雪球"）：
           名称 → 本地详情页；**代码 → 雪球**（全站口径：名称走站内、代码走雪球）。 -->
      <div v-if="!isHistory && movers.length" class="mt-1 flex flex-wrap gap-x-2 gap-y-0.5 text-[10px]">
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
                  @click="openKey = (openKey === n.key ? '' : n.key)">
            <div class="flex items-center gap-1.5">
              <!-- ★ 2026-10-28（方案 2）：窗口内新增标记（蓝点），解决"静默替换看不出变化" -->
              <span v-if="n.isNew" class="w-1.5 h-1.5 rounded-full bg-sky-400 shrink-0"
                    :title="`近 ${newsWindow} 分钟新增`"></span>
              <span class="font-mono text-[10px] text-muted">{{ hhmm(n.time) }}</span>
              <span class="px-1 rounded text-[10px]" :class="clusterCls(n.cluster)">{{ n.cluster }}</span>
              <span v-if="n.direction" class="text-[10px]" :class="dirCls(n.direction)">{{ n.direction }}</span>
              <span v-if="n.score >= 4" class="text-[10px] text-amber-300">重要</span>
              <span class="ml-auto text-[10px] text-muted">{{ openKey === n.key ? '▾' : '▸' }}</span>
            </div>
            <div class="mt-0.5 text-[11px] text-gray-200 leading-snug">{{ n.gist }}</div>
          </button>
          <div v-if="openKey === n.key" class="px-2 pb-2 pt-1.5 border-t border-border/40 space-y-1 text-[11px]">
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
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
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
const openKey = ref('')   // 按条目 key 记忆（60s 刷新后下标会漂移，用 index 会"串行"展开错条）
const bodyOpen = ref(true)   // ★ 2026-10-25（用户）：整卡支持收起/展开

const snapshot = computed(() => data.value?.snapshot || null)
// ★ 2026-10-26：`market` / `indices` 两个 computed 已随「① 大盘」块删除而移除
//   （指数看顶栏、家数看盘面速览；保留只会是死代码）。
const sectors = computed(() => snapshot.value?.sectors || { top: [], bottom: [] })
const movers = computed(() => (snapshot.value?.movers?.rows || []).slice(0, 4))
// ★★ 2026-10-26（方案 B：一天可能触发 1~8 次解读 → 做时间 tab 回看）：
//   · `runs` 来自 `/flash/brief` 的精简 meta（run_id / HH:MM / 触发原因 / 来源 / 结论摘要）；
//   · 选中历史 tab 才 `loadRun()` 拉全文（不覆盖共享"最新一份"，轮询不会冲掉它）；
//   · 选中态按 run_id 记忆：60s 轮询来了新解读时，不会把你从历史踢回最新（除非该条被裁掉）。
const runs = computed(() => (data.value && data.value.runs) || [])
const latestId = computed(() => (runs.value[0] && runs.value[0].run_id) || '')
const selRunId = ref('')
const hist = ref(null)
const histLoading = ref(false)
const histErr = ref('')      // 历史读取错误（不复用共享 err，避免污染"最新一份"的错误态）
const isHistory = computed(() => !!selRunId.value && selRunId.value !== latestId.value)
const TRIGGER_CN = { auto: '异动', manual: '手动', read: '读取', backfill: '历史' }
function trigCn(t) { return TRIGGER_CN[t] || '解读' }
const tabEls = []
function setTab(el, i) { if (el) tabEls[i] = el }

const llm = computed(() => (isHistory.value ? (hist.value?.llm || {}) : (data.value?.llm || {})))
const asOf = computed(() => (isHistory.value
  ? String(hist.value?.ts || '').slice(11, 16)
  : (data.value?.as_of || '')))
const source = computed(() => (isHistory.value ? (hist.value?.source || '') : (data.value?.source || '')))
const cached = computed(() => !!data.value?.cached)
const triggers = computed(() => data.value?.trigger || [])
// ★ 2026-10-28（方案 2）：窗口内新增的**展示条数** + 窗口分钟数（由后端下发，不写死）。
//   历史 tab 没有实时快照 ⇒ 一律 0（不伪造"当时新增了几条"）。
const newsNew = computed(() => (isHistory.value ? 0 : (snapshot.value?.news?.new_count || 0)))
const newsWindow = computed(() => snapshot.value?.news?.new_window_min || 30)

async function pickRun(r, i) {
  if (!r) return
  if (r.run_id === latestId.value) {
    selRunId.value = ''
    hist.value = null
  } else {
    selRunId.value = r.run_id
    histLoading.value = true
    histErr.value = ''
    try {
      const res = await fb.loadRun(r.run_id)
      hist.value = (res && res.ok) ? res : null
      if (!hist.value) histErr.value = (res && res.error) || '历史解读读取失败'
    } finally {
      histLoading.value = false
    }
  }
  // 选中后把自己滚到中间（tab 条可能很长）
  nextTick(() => {
    const el = tabEls[i]
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
    }
  })
}

watch(runs, (arr) => {
  if (!arr.length) { selRunId.value = ''; hist.value = null; return }
  // 选中的那条被裁掉（超过 RUN_KEEP）⇒ 老实回到最新，避免停留在空白历史
  if (selRunId.value && !arr.some(x => x.run_id === selRunId.value)) {
    selRunId.value = ''
    hist.value = null
  }
})

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
// ★ 2026-10-26：历史 tab 只有 `llm.news`（没有当时快照的原文行）⇒ 直接用逐条解读渲染，
//   不伪造"当时快讯列表"（宁可少显示，也不张冠李戴）。
const cards = computed(() => {
  if (isHistory.value) {
    return (hist.value?.llm?.news || []).map((x, i) => ({
      key: 'h' + i,
      time: '',
      cluster: '',
      score: 0,
      text: '',
      gist: x.gist || '',
      cause: x.cause || '',
      scope: x.scope || '',
      direction: x.direction || '',
    }))
  }
  const rows = snapshot.value?.news?.rows || []
  // ★★ 2026-10-28（方案 3）：解读按 **id** 回填，序号只作旧数据兜底。
  //   原因：60s 轮询会重算 Top5（新快讯插队、排序变化），若还按 `idx` 对齐，
  //   A 条的解读会被贴到 B 条上（"张冠李戴"）。后端 prompt 已要求逐字回传 id。
  const byId = {}
  const byIdx = {}
  for (const x of (llm.value.news || [])) {
    if (x.id !== undefined && x.id !== null && x.id !== '') byId[String(x.id)] = x
    byIdx[Number(x.idx)] = x
  }
  return rows.map((r, i) => {
    const l = (r.id != null && byId[String(r.id)]) || byIdx[i + 1] || {}
    return {
      key: r.id != null ? r.id : i,
      time: r.time,
      cluster: r.cluster || '其他',
      score: r.score || 0,
      text: r.text,
      gist: l.gist || r.gist,
      cause: l.cause || '',
      scope: l.scope || r.scope,
      direction: l.direction || r.direction,
      // ★ 2026-10-28（方案 2）：窗口内新增（后端 `is_new`）⇒ 条目左侧蓝点
      isNew: !!r.is_new,
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
      openKey.value = ''
    }
  } catch (e) {
    loadErr.value = '解读失败：' + (e?.message || e)
  } finally {
    busy.value = false
  }
}

// 换日：只改共享源的日期并重取（本组件不再持有独立数据）
watch(() => props.date, (d) => { openKey.value = ''; fb.load(d) })

// 轮询的生命周期交给共享单例（引用计数：本组件 + 盘面速览各一份订阅，仍只有一条 60s 定时器）
onMounted(() => { fb.start(props.date || '') })
onBeforeUnmount(() => { fb.stop() })
</script>

<style scoped>
/* 解读 tab 横向滚动：细滚动条，避免右卡被撑高（与「李大霄视角」同款，2026-10-26） */
.wb-hscroll::-webkit-scrollbar { height: 4px; }
.wb-hscroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,.18); border-radius: 2px; }
.wb-hscroll { scrollbar-width: thin; }
</style>
