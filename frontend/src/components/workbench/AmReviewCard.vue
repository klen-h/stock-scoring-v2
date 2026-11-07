<template>
  <!-- ★★ 2026-11-01（用户："样式有点简陋了，根据现项目的风格重新设计一下，重要的数据用亮色样式重点突出"）。
       视觉语言对齐本项目既有卡片（`MacroEnvCard` / `StockListsCards`）：
         · 外壳 `bg-card border border-border rounded-lg`，标题 `text-sm font-semibold`
         · **关键数字用 `font-mono` 大字号**（项目惯例：宏观分 4xl、榜单分 mono）
         · 徽标一律 `bg-{color}-500/15 text-{color}-300 text-[10px]`
         · 行与行用 `border-b border-border/40` 细分割，不做卡片套卡片
       信息层级：① 概览（大数字）② 行动（色条+优先级）③ 异动（大字涨跌）④ 结构 ⑤ 研判 ⑥ 附录 -->
  <div class="space-y-3 min-w-0">

    <!-- ── 概览条：一眼看到"今天要不要动" ───────────────────────────── -->
    <div class="bg-card border border-border rounded-lg overflow-hidden">
      <div class="flex items-stretch">
        <!-- 左：待办数（大字） -->
        <div class="px-4 py-3 flex items-center gap-3 shrink-0"
             :class="p0Count ? 'bg-rose-500/10' : (actions.length ? 'bg-amber-500/10' : 'bg-emerald-500/10')">
          <div class="text-center">
            <div class="text-3xl font-bold font-mono leading-none"
                 :class="p0Count ? 'text-rose-300' : (actions.length ? 'text-amber-300' : 'text-emerald-300')">
              {{ holdings.available ? actions.length : '—' }}
            </div>
            <div class="text-[10px] text-muted mt-1 whitespace-nowrap">午后待办</div>
          </div>
          <div v-if="p0Count" class="w-px h-10 bg-border/70"></div>
          <div v-if="p0Count" class="text-center">
            <div class="text-xl font-bold font-mono leading-none text-rose-300">{{ p0Count }}</div>
            <div class="text-[10px] text-rose-300/80 mt-1 whitespace-nowrap">硬风险</div>
          </div>
        </div>
        <!-- 右：一句话结论 -->
        <div class="flex-1 min-w-0 px-3 py-3">
          <div class="flex items-center gap-2 flex-wrap mb-1">
            <span class="text-sm font-semibold">早盘复盘</span>
            <span class="text-[10px] text-muted font-mono">{{ date }}</span>
            <span v-if="stale" class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px] cursor-help"
                  title="该日没有 11:30 定稿快照，数据为收盘定稿口径（附录各条已标注）">收盘口径</span>
            <span v-if="data?.holdings?.quote_from_snapshot"
                  class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px] cursor-help"
                  :title="`行情来自收盘快照（${data.holdings.quote_data_date || '上一交易日'}），非此刻实时`">行情为快照</span>
            <span class="text-[10px] text-muted ml-auto font-mono">
              行情 {{ hhmm(data?.holdings?.quote_as_of) || hhmm(data?.as_of) || '—' }}
            </span>
            <button type="button" class="text-[10px] text-accent hover:underline"
                    :disabled="loading" @click="$emit('refresh')">刷新</button>
          </div>
          <div class="text-xs leading-relaxed line-clamp-2" :class="headlineCls">{{ headline }}</div>
        </div>
      </div>
    </div>

    <!-- ── 骨架 ── -->
    <div v-if="loading && !data" class="space-y-3">
      <div v-for="i in 3" :key="i" class="bg-card border border-border rounded-lg p-4 animate-pulse">
        <div class="h-3 w-24 bg-border rounded mb-3"></div>
        <div class="h-2 w-full bg-border/70 rounded mb-2"></div>
        <div class="h-2 w-3/4 bg-border/70 rounded"></div>
      </div>
    </div>

    <div v-else-if="error" class="bg-card border border-border rounded-lg p-4 text-xs text-rise">{{ error }}</div>

    <template v-else-if="data">
      <!-- ① 午后怎么做 -->
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="w-1 h-3.5 rounded-full bg-accent"></span>
          <span class="text-sm font-semibold">午后怎么做</span>
          <span class="text-[10px] text-muted">按优先级排序</span>
        </div>

        <div v-if="!holdings.available" class="text-xs text-muted leading-relaxed">
          —（{{ holdingsReason }}）
        </div>
        <div v-else-if="!actions.length"
             class="flex items-center gap-2 rounded-lg bg-emerald-500/10 border border-emerald-500/25 px-3 py-2">
          <span class="text-lg leading-none">✓</span>
          <span class="text-xs text-emerald-300">今日持仓无异动：未触发止损 / 冲高回落 / 主力出货 —— 按原计划持有，不必操作。</span>
        </div>
        <div v-else class="space-y-1.5">
          <div v-for="(a, i) in actions" :key="i"
               class="flex items-stretch gap-0 rounded-lg overflow-hidden border border-border/60">
            <!-- 左侧色条：优先级一眼可辨 -->
            <div class="w-1 shrink-0" :class="prioBar(a.priority)"></div>
            <div class="flex items-start gap-2 px-2.5 py-2 bg-bg/40 flex-1 min-w-0">
              <span class="shrink-0 mt-0.5 px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wide"
                    :class="prioCls(a.priority)">{{ a.priority }}</span>
              <div class="min-w-0 flex-1">
                <div class="flex items-baseline gap-1.5 flex-wrap">
                  <span v-if="a.name" class="text-xs font-semibold text-gray-100">{{ a.name }}</span>
                  <span class="text-[11px] font-semibold" :class="prioTxt(a.priority)">{{ actionWord(a) }}</span>
                </div>
                <div class="text-[10px] text-muted mt-0.5 leading-snug">{{ actionReason(a) }}</div>
              </div>
            </div>
          </div>
          <div class="flex items-center gap-3 text-[10px] text-muted pt-0.5 flex-wrap">
            <span><b class="text-rose-300">P0</b> 开盘就处理</span>
            <span><b class="text-amber-300">P1</b> 今日盯盘</span>
            <span><b class="text-sky-300">P2</b> 机会非必须</span>
          </div>
        </div>
      </div>

      <!-- ② 持仓异动 -->
      <div v-if="holdings.available && holdings.items.length"
           class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="w-1 h-3.5 rounded-full bg-rose-400"></span>
          <span class="text-sm font-semibold">持仓异动</span>
          <span class="text-[10px] text-muted">仅列有异动的 {{ holdings.items.length }} 只</span>
        </div>
        <div class="space-y-2">
          <div v-for="h in holdings.items" :key="h.code"
               class="rounded-lg border border-border/60 overflow-hidden">
            <!-- 首行：名称 + 三个关键数字（大字亮色） -->
            <div class="flex items-center gap-3 px-3 py-2 bg-bg/40 flex-wrap">
              <div class="min-w-0">
                <div class="flex items-center gap-1.5">
                  <span class="text-sm font-semibold">{{ h.name }}</span>
                  <span class="text-[10px] text-muted font-mono">{{ h.code }}</span>
                </div>
                <div class="text-[10px] text-muted mt-0.5 truncate">
                  {{ h.industry || '—' }}
                  <template v-if="h.weight != null"> · 占仓 <b class="text-gray-300 font-mono">{{ h.weight }}%</b></template>
                </div>
              </div>
              <div class="ml-auto flex items-end gap-4">
                <div class="text-right">
                  <div class="text-lg font-bold font-mono leading-none" :class="numCls(h.day_pct)">
                    {{ fmtPct(h.day_pct) }}
                  </div>
                  <div class="text-[10px] text-muted mt-1">今日</div>
                </div>
                <div class="w-px h-8 bg-border/60"></div>
                <div class="text-right">
                  <div class="text-lg font-bold font-mono leading-none" :class="numCls(h.pnl_pct)">
                    {{ fmtPct(h.pnl_pct) }}
                  </div>
                  <div class="text-[10px] text-muted mt-1">浮盈</div>
                </div>
              </div>
            </div>
            <!-- 信号（= 触发原因） -->
            <div class="px-3 py-1.5 flex flex-wrap gap-1 border-t border-border/50">
              <span v-for="(a, i) in h.alerts" :key="i"
                    class="px-1.5 py-0.5 rounded text-[10px] font-medium" :class="lvlCls(a.level)">{{ a.text }}</span>
            </div>
          </div>
        </div>
        <div class="text-[10px] text-muted mt-2 leading-snug">
          信号来自持仓雷达的确定性规则（浮亏阈值 / 冲高回落 / 主力出货 / 板块被点名 / 闸门就绪），非 LLM 生成。
        </div>
      </div>

      <!-- ③ 仓位结构 -->
      <div v-if="holdings.available && structureText"
           class="bg-card border border-border rounded-lg px-3 py-2 flex items-center gap-2 flex-wrap">
        <span class="w-1 h-3.5 rounded-full bg-sky-400 shrink-0"></span>
        <span class="text-sm font-semibold shrink-0">仓位结构</span>
        <span class="text-xs text-muted">{{ structureText }}</span>
      </div>

      <!-- ④ 午后研判（LLM） -->
      <div class="bg-card border border-border border-l-2 border-l-amber-500/70 rounded-lg p-3">
        <div class="flex items-center gap-2 flex-wrap mb-1.5">
          <span class="text-sm font-semibold">午后研判</span>
          <span v-if="conclusion.time" class="text-[10px] text-muted font-mono">{{ hhmm(conclusion.time) }} 生成</span>
          <span v-if="!conclusion.markdown" class="px-1.5 py-0.5 rounded bg-border text-muted text-[10px]">未生成</span>
          <button v-if="conclusion.markdown" type="button"
                  class="ml-auto text-[10px] text-accent hover:underline"
                  @click="bodyOpen = !bodyOpen">{{ bodyOpen ? '收起' : '展开全文' }}</button>
        </div>
        <div v-if="!conclusion.markdown" class="text-xs text-muted">
          —（该日暂无复盘结论；系统在 11:32 自动生成，可在「快讯监控」手动补跑）
        </div>
        <template v-else>
          <div class="text-xs font-medium leading-relaxed line-clamp-3 text-gray-200">{{ lead }}</div>
          <div v-if="bodyOpen" class="md-body text-xs mt-2 max-h-[46vh] overflow-y-auto" v-html="html"></div>
        </template>
      </div>

      <!-- ⑤ 数据附录（默认折叠） -->
      <div class="bg-card border border-border rounded-lg p-3">
        <button type="button" class="flex items-center gap-2 w-full text-left" @click="dataOpen = !dataOpen">
          <span class="text-sm font-semibold">数据附录</span>
          <span class="text-[10px] text-muted">盘面 · 宏观 · 消息 · 溯源</span>
          <span v-if="data.missing.length"
                class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px]">
            未留存 {{ data.missing.length }}
          </span>
          <span class="ml-auto text-[10px] text-muted">{{ dataOpen ? '▾' : '▸' }}</span>
        </button>

        <div v-if="dataOpen" class="mt-2 space-y-2">
          <!-- ★★ 2026-11-01（用户："加一个 llm 解读"）：附录顶部一段 AI 解读。
               走**独立端点**（GET 只读缓存 / POST 才生成），不拖累主端点首屏。 -->
          <div class="rounded-lg border border-border/60 bg-bg/40 px-2.5 py-2">
            <div class="flex items-center gap-2 mb-1">
              <span class="w-1 h-3 rounded-full bg-accent"></span>
              <span class="text-xs font-semibold">AI 解读</span>
              <span v-if="digest?.generated_at" class="text-[11px] text-muted font-mono">
                {{ digest.generated_at }} 生成
              </span>
              <button type="button"
                      class="ml-auto text-[11px] text-accent hover:underline"
                      :disabled="digestLoading"
                      @click="$emit('digest')">
                {{ digestLoading ? '生成中…' : (digest?.markdown ? '重新生成' : '生成解读') }}
              </button>
            </div>
            <div v-if="digestError" class="text-xs text-rise">{{ digestError }}</div>
            <div v-else-if="digestLoading" class="text-xs text-muted">正在让模型读这批数据（约 10~30 秒）…</div>
            <div v-else-if="digest?.markdown" class="md-body text-xs leading-relaxed"
                 v-html="renderMd(digest.markdown)"></div>
            <div v-else class="text-xs text-muted">
              还没生成。点「生成解读」让模型把下面这些数字串成因果（消耗 1 次 LLM；只解读，不给操作建议）。
            </div>
          </div>

          <div v-for="b in BLOCKS" :key="b.key">
            <div class="text-xs font-semibold mb-0.5 flex items-center gap-2">
              {{ b.label }}
              <span class="text-[11px] text-muted font-normal">{{ srcLabel(data.blocks[b.key]) }}</span>
            </div>
            <div v-if="!data.blocks[b.key].length" class="text-[11px] text-muted">—（该日未留存）</div>
            <div v-else class="space-y-0">
              <div v-for="(it, i) in data.blocks[b.key]" :key="i"
                   class="flex items-start gap-2 text-xs border-b border-border/25 last:border-0 py-[3px]">
                <span class="text-muted w-24 shrink-0 truncate" :title="it.caliber">{{ it.label }}</span>
                <span class="flex-1 min-w-0 break-words font-mono" :class="numCls(it.delta)">{{ it.value }}</span>
                <span class="text-muted shrink-0 font-mono cursor-help text-[11px]"
                      :title="`${srcCn(it.source)} · ${it.caliber}`">{{ hhmm(it.as_of) || '—' }}</span>
              </div>
            </div>
          </div>

          <div v-if="data.missing.length">
            <div class="text-xs font-semibold mb-0.5">未留存项</div>
            <div v-for="(m, i) in data.missing" :key="'m' + i"
                 class="text-[11px] text-muted bg-border/30 rounded px-2 py-[3px] mb-0.5">
              <span class="text-amber-300">{{ blockCn(m.block) }}</span> / {{ m.item }} —— {{ m.reason }}
            </div>
          </div>

          <div v-if="data.trace.length">
            <div class="text-xs font-semibold mb-0.5">溯源（{{ data.trace.length }} 项）</div>
            <div v-for="(t, i) in data.trace" :key="'t' + i"
                 class="grid grid-cols-[52px_1fr_auto] gap-2 text-[11px] text-muted border-b border-border/25 last:border-0 py-[2px]">
              <span class="truncate">{{ blockCn(t.block) }}</span>
              <span class="truncate" :title="t.item">{{ t.item }}</span>
              <span class="truncate">{{ srcCn(t.source) }} · {{ hhmm(t.as_of) || '—' }}</span>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { renderMd } from '../../composables/markdown'

const props = defineProps({
  data: { type: Object, default: null },
  loading: { type: Boolean, default: false },
  error: { type: String, default: '' },
  date: { type: String, default: '' },
  // ★ 2026-11-01：数据附录的「AI 解读」由父级取数（GET 只读 / POST 生成），
  //   组件只负责排版 ⇒ mock 预览时也能直接塞假数据看效果。
  digest: { type: Object, default: null },
  digestLoading: { type: Boolean, default: false },
  digestError: { type: String, default: '' },
})
defineEmits(['refresh', 'digest'])

const bodyOpen = ref(false)
const dataOpen = ref(false)

const BLOCKS = [
  { key: 'market', label: '盘面走势' },
  { key: 'macro', label: '宏观面' },
  { key: 'news', label: '消息面' },
]
// 展示层翻译（后端 key/表名是英文，界面上不露）
const BLOCK_CN = { market: '盘面', macro: '宏观', news: '消息', holdings: '持仓' }
const SRC_CN = {
  market_emotion_daily: '情绪快照表', market_amount: '两市成交额',
  'market_overview 快照': '指数行情', market_regime_history: '市场状态表',
  macro_daily_history: '外盘日线', flash_calendar: '财经日历',
  flash_brief: '快讯解读', 'flash_brief.news': '快讯条目', 腾讯指数: '指数行情',
}
const blockCn = (b) => BLOCK_CN[b] || b || ''
const srcCn = (s) => SRC_CN[s] || s || ''

const conclusion = computed(() => props.data?.conclusion || {})
const html = computed(() => renderMd(conclusion.value.markdown || ''))
const holdings = computed(() => props.data?.holdings || { available: false, actions: [], items: [], structure: {} })
const actions = computed(() => holdings.value.actions || [])
const p0Count = computed(() => actions.value.filter((a) => a.priority === 'P0').length)

/** 概览条那句话：无异动给"不必操作"，有异动给最紧急那条 */
const headline = computed(() => {
  if (!holdings.value.available) return holdingsReason.value
  if (!actions.value.length) return '持仓无异动，按原计划持有即可'
  const a = actions.value[0]
  return `${a.name ? a.name + ' ' : ''}${a.text}`
})
const headlineCls = computed(() => {
  if (!holdings.value.available) return 'text-muted'
  if (!actions.value.length) return 'text-emerald-300'
  return p0Count.value ? 'text-rose-200' : 'text-amber-200'
})

const holdingsReason = computed(() => {
  const m = (props.data?.missing || []).find((x) => x.block === 'holdings')
  return m?.reason || '暂无持仓数据'
})

/** 仓位结构一行话：总量 / 集中度 / 风格 */
const structureText = computed(() => {
  const s = holdings.value.structure || {}
  const parts = []
  // ★★ 2026-11-07（用户："仓位结构 持仓市值 0 万，这个还是没有解决"）：
  //   根因**不是 `mv` 缺失**（那个 2026-11-05 已修：市值从盈亏分支里拆出来），
  //   而是**显示精度**——持仓约 3050 元时 `Math.round(3050 / 10000) = 0`，
  //   卡片写"持仓市值 0 万"，看起来像"数据没算出来"，实际是真有这个数、
  //   只是被"万"这个单位四舍五入抹平了。
  //   ⇒ < 1 万按「元」显示，≥ 1 万按「万」且保留 1 位小数（不再把 1.4 万显示成 1 万）。
  const mv = Number(s.total_mv) || 0
  if (mv > 0) {
    parts.push(mv >= 10000
      ? `持仓市值 ${(mv / 10000).toFixed(1)} 万`
      : `持仓市值 ${Math.round(mv)} 元`)
  }
  if (s.concentration_note) parts.push(s.concentration_note)
  if (s.style) parts.push(s.style)
  return parts.join(' ｜ ')
})

const stale = computed(() =>
  (props.data?.blocks?.market || []).some((i) => String(i.caliber || '').includes('收盘定稿口径')))

/** 结论首行（折叠时先给一句判断） */
const lead = computed(() => {
  const line = String(conclusion.value.markdown || '').split('\n')
    .map((s) => s.replace(/^#+\s*/, '').replace(/\*\*/g, '').trim())
    .find((s) => s.length > 0) || ''
  return line.slice(0, 200)
})

// 后端给的是「动作 —— 原因」，拆开排版：动作加粗亮色、原因小字
function actionWord(a) {
  return String(a.text || '').split(' —— ')[0]
}
function actionReason(a) {
  const p = String(a.text || '').split(' —— ')
  return p.length > 1 ? p.slice(1).join(' —— ') : ''
}

function hhmm(s) {
  const t = String(s || '')
  const m = t.match(/(\d{2}:\d{2})/)
  return m ? m[1] : (t.length === 10 ? t : '')
}
function srcLabel(items) {
  const srcs = [...new Set((items || []).map((i) => i.source).filter(Boolean))]
  if (!srcs.length) return ''
  const cn = srcs.map(srcCn)
  return cn.length === 1 ? cn[0] : `${cn[0]} 等 ${cn.length} 处`
}
const fmtPct = (v) => (v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`)
function numCls(v) {
  if (v == null) return 'text-gray-300'
  const n = Number(v)
  return n > 0 ? 'text-red-400' : (n < 0 ? 'text-emerald-400' : 'text-gray-300')
}
function prioBar(p) {
  if (p === 'P0') return 'bg-rose-500'
  if (p === 'P1') return 'bg-amber-500'
  return 'bg-sky-500'
}
function prioCls(p) {
  if (p === 'P0') return 'bg-rose-500/20 text-rose-300'
  if (p === 'P1') return 'bg-amber-500/20 text-amber-300'
  return 'bg-sky-500/20 text-sky-300'
}
function prioTxt(p) {
  if (p === 'P0') return 'text-rose-300'
  if (p === 'P1') return 'text-amber-300'
  return 'text-sky-300'
}
function lvlCls(l) {
  if (l === 'risk') return 'bg-rose-500/15 text-rose-300'
  if (l === 'opportunity') return 'bg-emerald-500/15 text-emerald-300'
  return 'bg-border/60 text-muted'
}
</script>
