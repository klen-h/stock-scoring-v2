<template>
  <!-- ★★ 2026-11-01（用户："减少数据罗列…重点转向异动解读：明确标注持仓异动的关键信号与
       触发原因，分析对当前持仓的具体影响（盈亏/风险敞口/仓位结构），并针对午盘给出可执行
       的应对策略与优先级；站在用户视角：先讲结论与行动，再辅以数据支撑"）。
       页面顺序按此重排 —— **行动在最上面，数据降级为附录（默认折叠）**：
         ① 午后行动清单（P0/P1/P2）
         ② 持仓异动（只列"有异动"的，无异动不罗列）
         ③ 仓位结构 / 风险敞口（一行）
         ④ 午后研判（LLM 原文，默认折叠）
         ⑤ 数据附录（盘面/宏观/消息 + 溯源，默认折叠） -->
  <div class="space-y-3 min-w-0">

    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg px-3 py-2">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-sm font-semibold">早盘复盘</span>
        <span class="text-[10px] text-muted">{{ date }}</span>
        <span v-if="stale" class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px] cursor-help"
              title="该日没有 11:30 定稿快照，数据为收盘定稿口径（各条已标注）">收盘口径</span>
        <span v-if="data?.holdings?.quote_from_snapshot"
              class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px] cursor-help"
              :title="`行情来自收盘快照（${data.holdings.quote_data_date || '上一交易日'}），不是此刻实时`">
              行情为快照
        </span>
        <span class="text-[10px] text-muted ml-auto font-mono">
          行情 {{ hhmm(data?.holdings?.quote_as_of) || hhmm(data?.as_of) || '—' }}
        </span>
        <button type="button" class="text-[10px] text-accent hover:underline"
                :disabled="loading" @click="$emit('refresh')">刷新</button>
      </div>
    </div>

    <!-- 骨架 -->
    <div v-if="loading && !data" class="space-y-3">
      <div v-for="i in 3" :key="i" class="bg-card border border-border rounded-lg p-4 animate-pulse">
        <div class="h-3 w-24 bg-border rounded mb-3"></div>
        <div class="h-2 w-full bg-border/70 rounded mb-2"></div>
        <div class="h-2 w-3/4 bg-border/70 rounded"></div>
      </div>
    </div>

    <div v-else-if="error" class="bg-card border border-border rounded-lg p-4 text-xs text-rise">{{ error }}</div>

    <template v-else-if="data">
      <!-- ① 午后行动清单 -->
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-sm font-semibold">午后怎么做</span>
          <span v-if="actions.length" class="text-[10px] text-muted">{{ actions.length }} 条</span>
          <span class="text-[10px] text-muted ml-auto">按优先级排序 · 数据见下方附录</span>
        </div>

        <div v-if="!holdings.available" class="text-xs text-muted leading-relaxed">
          —（{{ holdingsReason }}）
        </div>
        <div v-else-if="!actions.length"
             class="text-xs text-emerald-400/90 leading-relaxed">
          今日持仓无异动：没有触发止损、冲高回落、主力出货等信号 —— 按原计划持有，不必操作。
        </div>
        <div v-else class="space-y-1.5">
          <div v-for="(a, i) in actions" :key="i"
               class="flex items-start gap-2 rounded-lg border border-border/60 bg-bg/40 px-2.5 py-1.5">
            <span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold" :class="prioCls(a.priority)">
              {{ a.priority }}
            </span>
            <div class="min-w-0 flex-1">
              <div class="text-xs leading-relaxed">
                <span v-if="a.name" class="font-semibold mr-1 text-accent">{{ a.name }}</span>{{ a.text }}
              </div>
            </div>
          </div>
          <div class="text-[10px] text-muted pt-0.5">
            P0 = 开盘就处理（硬风险）· P1 = 今日盯盘处理 · P2 = 机会，非必须
          </div>
        </div>
      </div>

      <!-- ② 持仓异动 -->
      <div v-if="holdings.available && holdings.items.length"
           class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-sm font-semibold">持仓异动</span>
          <span class="text-[10px] text-muted">只列有异动的 {{ holdings.items.length }} 只</span>
        </div>
        <div class="space-y-2">
          <div v-for="h in holdings.items" :key="h.code"
               class="rounded-lg border border-border/60 bg-bg/40 px-2.5 py-2">
            <div class="flex items-baseline gap-2 flex-wrap">
              <span class="text-xs font-semibold">{{ h.name }}</span>
              <span class="text-[10px] text-muted font-mono">{{ h.code }}</span>
              <span class="text-xs font-mono" :class="numCls(h.day_pct)">今日 {{ fmtPct(h.day_pct) }}</span>
              <span class="text-xs font-mono" :class="numCls(h.pnl_pct)">浮盈 {{ fmtPct(h.pnl_pct) }}</span>
              <span v-if="h.weight != null" class="text-[10px] text-muted">占仓 {{ h.weight }}%</span>
              <span v-if="h.industry" class="text-[10px] text-muted">{{ h.industry }}</span>
            </div>
            <!-- 信号 chips -->
            <div class="flex flex-wrap gap-1 mt-1.5">
              <span v-for="(a, i) in h.alerts" :key="i"
                    class="px-1.5 py-0.5 rounded text-[10px]" :class="lvlCls(a.level)">{{ a.text }}</span>
            </div>
          </div>
        </div>
        <div class="text-[10px] text-muted mt-1.5">
          信号来自持仓雷达的确定性规则（浮亏阈值 / 冲高回落 / 主力出货 / 板块被点名 / 闸门就绪），非 LLM 生成。
        </div>
      </div>

      <!-- ③ 仓位结构 -->
      <div v-if="holdings.available && structureText"
           class="bg-card border border-border rounded-lg px-3 py-2">
        <div class="flex items-center gap-2 flex-wrap text-xs">
          <span class="text-sm font-semibold shrink-0">仓位结构</span>
          <span class="text-muted">{{ structureText }}</span>
        </div>
      </div>

      <!-- ④ 午后研判（LLM） -->
      <div class="bg-card border border-border border-l-2 border-l-amber-500/70 rounded-lg p-3">
        <div class="flex items-center gap-2 flex-wrap mb-1">
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
          <div class="text-xs font-semibold leading-relaxed line-clamp-3">{{ lead }}</div>
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

        <div v-if="dataOpen" class="mt-2 space-y-3">
          <div v-for="b in BLOCKS" :key="b.key">
            <div class="text-[11px] font-semibold mb-1 flex items-center gap-2">
              {{ b.label }}
              <span class="text-[10px] text-muted font-normal">{{ srcLabel(data.blocks[b.key]) }}</span>
            </div>
            <div v-if="!data.blocks[b.key].length" class="text-[10px] text-muted">—（该日未留存）</div>
            <div v-else class="space-y-0.5">
              <div v-for="(it, i) in data.blocks[b.key]" :key="i"
                   class="flex items-start gap-2 text-[10px] border-b border-border/30 last:border-0 py-0.5">
                <span class="text-muted w-24 shrink-0 truncate" :title="it.caliber">{{ it.label }}</span>
                <span class="flex-1 min-w-0 break-words" :class="numCls(it.delta)">{{ it.value }}</span>
                <span class="text-muted shrink-0 font-mono cursor-help"
                      :title="`${srcCn(it.source)} · ${it.caliber}`">{{ hhmm(it.as_of) || '—' }}</span>
              </div>
            </div>
          </div>

          <div v-if="data.missing.length" class="pt-1">
            <div class="text-[11px] font-semibold mb-1">未留存项</div>
            <div v-for="(m, i) in data.missing" :key="'m' + i"
                 class="text-[10px] text-muted bg-border/30 rounded px-2 py-1 mb-1">
              <span class="text-amber-300">{{ blockCn(m.block) }}</span> / {{ m.item }} —— {{ m.reason }}
            </div>
          </div>

          <div v-if="data.trace.length">
            <div class="text-[11px] font-semibold mb-1">溯源（{{ data.trace.length }} 项）</div>
            <div v-for="(t, i) in data.trace" :key="'t' + i"
                 class="grid grid-cols-[52px_1fr_auto] gap-2 text-[10px] text-muted border-b border-border/30 last:border-0 py-0.5">
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
})
defineEmits(['refresh'])

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

/** 持仓雷达不可用时，把 `missing` 里的原因原文搬过来（不给空洞的"—"） */
const holdingsReason = computed(() => {
  const m = (props.data?.missing || []).find((x) => x.block === 'holdings')
  return m?.reason || '暂无持仓数据'
})

/** 仓位结构一行话：总量 / 集中度 / 风格 */
const structureText = computed(() => {
  const s = holdings.value.structure || {}
  const parts = []
  if (s.total_mv) parts.push(`持仓市值 ${Math.round(s.total_mv / 10000)} 万`)
  if (s.concentration_note) parts.push(s.concentration_note)
  if (s.style) parts.push(s.style)
  if (!parts.length) return ''
  return parts.join(' ｜ ')
})

const stale = computed(() =>
  (props.data?.blocks?.market || []).some((i) => String(i.caliber || '').includes('收盘定稿口径')))

/** 结论首行（折叠时先给一句判断） */
const lead = computed(() => {
  const line = String(conclusion.value.markdown || '').split('\n')
    .map((s) => s.replace(/^#+\s*/, '').replace(/\*\*/g, '').trim())
    .find((s) => s.length > 0) || ''
  return line.slice(0, 160)
})

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
  if (v == null) return ''
  const n = Number(v)
  return n > 0 ? 'text-red-400' : (n < 0 ? 'text-emerald-400' : '')
}
function prioCls(p) {
  if (p === 'P0') return 'bg-red-500/20 text-red-300'
  if (p === 'P1') return 'bg-amber-500/20 text-amber-300'
  return 'bg-sky-500/20 text-sky-300'
}
function lvlCls(l) {
  if (l === 'risk') return 'bg-red-500/15 text-red-300'
  if (l === 'opportunity') return 'bg-emerald-500/15 text-emerald-300'
  return 'bg-border/60 text-muted'
}
</script>
