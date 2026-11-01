<template>
  <!-- ★★ 2026-11-01（用户："新增一个早盘复盘标签页，对当日早盘的盘面走势、宏观面及
       消息面进行系统分析，输出可研判午盘行情；内容结构清晰、数据与结论可追溯"）。
       结构顺序按"结论先行 → 数据分层 → 溯源收尾"：
         ① 午后研判（结论条，首行加粗，依据可展开）
         ② 盘面走势  ③ 宏观面  ④ 消息面（每块标题右侧标 source / as_of）
         ⑤ 口径与溯源（默认收起，逐项列数据来源与截至时刻）
       数据全部来自 `GET /api/market/amreview`（只读快照，缺失项进 `missing`）。 -->
  <div class="space-y-3 min-w-0">

    <!-- 头部：日期 + 口径 + 刷新 -->
    <div class="bg-card border border-border rounded-lg p-3">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-sm font-semibold">早盘复盘</span>
        <span class="text-[10px] text-muted">{{ date }}</span>
        <span v-if="data && data.is_live === false"
              class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px]"
              :title="'当前查看的是历史日期，不是今日'">历史回放</span>
        <span v-if="stale"
              class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px] cursor-help"
              :title="'该日没有 11:30 定稿快照，展示的是收盘定稿口径（已在各条目标注）'">收盘口径</span>
        <span v-if="data?.as_of" class="text-[10px] text-muted font-mono ml-auto">取数 {{ hhmm(data.as_of) }}</span>
        <button type="button" class="text-[10px] text-accent hover:underline"
                :disabled="loading" @click="$emit('refresh')">刷新</button>
      </div>
      <div class="text-[10px] text-muted mt-1 leading-relaxed">
        口径：早盘 = 09:30-11:30；11:30 由调度器落定稿快照，缺快照的日期会显式标注「该日未留存」。
      </div>
    </div>

    <!-- 骨架 -->
    <div v-if="loading && !data" class="space-y-3">
      <div v-for="i in 4" :key="i"
           class="bg-card border border-border rounded-lg p-4 animate-pulse">
        <div class="h-3 w-24 bg-border rounded mb-3"></div>
        <div class="h-2 w-full bg-border/70 rounded mb-2"></div>
        <div class="h-2 w-3/4 bg-border/70 rounded"></div>
      </div>
    </div>

    <div v-else-if="error"
         class="bg-card border border-border rounded-lg p-4 text-xs text-rise">
      {{ error }}
    </div>

    <template v-else-if="data">
      <!-- ① 结论条 -->
      <div class="bg-card border border-border border-l-2 border-l-amber-500/70 rounded-lg p-3">
        <div class="flex items-center gap-2 flex-wrap mb-1">
          <span class="text-sm font-semibold">午后研判</span>
          <span v-if="conclusion.time" class="text-[10px] text-muted font-mono">
            {{ hhmm(conclusion.time) }} 生成
          </span>
          <span v-if="!conclusion.markdown"
                class="px-1.5 py-0.5 rounded bg-border text-muted text-[10px] cursor-help"
                :title="'系统在 11:32 自动生成午盘策略复盘；若当日窗口内后端未运行，则该日没有这条结论'">
            未生成
          </span>
          <button v-if="conclusion.markdown" type="button"
                  class="ml-auto text-[10px] text-accent hover:underline"
                  @click="bodyOpen = !bodyOpen">
            {{ bodyOpen ? '收起依据' : '展开依据' }}
          </button>
        </div>
        <div v-if="!conclusion.markdown" class="text-xs text-muted">
          —（该日暂无复盘结论，通常因为 11:32 时后端未运行，可到「快讯监控」手动补跑）
        </div>
        <template v-else>
          <div class="text-xs font-semibold text-fg leading-relaxed line-clamp-3">{{ lead }}</div>
          <div v-if="bodyOpen" class="md-body text-xs mt-2 max-h-[46vh] overflow-y-auto"
               v-html="html"></div>
        </template>
      </div>

      <!-- ② 盘面走势 -->
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-sm font-semibold">盘面走势</span>
          <span class="text-[10px] text-muted">上午 09:30-11:30</span>
          <span class="text-[10px] text-muted ml-auto font-mono">{{ srcLabel(data.blocks.market) }}</span>
        </div>
        <div v-if="!data.blocks.market.length" class="text-xs text-muted">
          —（该日未留存早盘盘面数据）
        </div>
        <div v-else class="grid grid-cols-2 md:grid-cols-3 gap-2">
          <div v-for="(it, i) in data.blocks.market" :key="i"
               class="rounded-lg border border-border/70 bg-bg/40 px-2.5 py-2 min-w-0">
            <div class="text-[10px] text-muted truncate" :title="it.caliber">{{ it.label }}</div>
            <div class="text-base font-semibold truncate" :class="valClass(it)">
              {{ it.value }}<span class="text-[10px] text-muted font-normal ml-0.5">{{ it.unit }}</span>
            </div>
            <div class="text-[10px] text-muted truncate"
                 :title="`${srcCn(it.source)} · 截至 ${hhmm(it.as_of) || '—'}`">
              {{ srcCn(it.source) }} · {{ hhmm(it.as_of) }}
            </div>
          </div>
        </div>
      </div>

      <!-- ③ 宏观面 -->
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-sm font-semibold">宏观面</span>
          <span class="text-[10px] text-muted">环境 / 外盘 / 已公布数据</span>
          <span class="text-[10px] text-muted ml-auto font-mono">{{ srcLabel(data.blocks.macro) }}</span>
        </div>
        <div v-if="!data.blocks.macro.length" class="text-xs text-muted">—（该日未留存宏观数据）</div>
        <div v-else class="space-y-1">
          <div v-for="(it, i) in data.blocks.macro" :key="i"
               class="flex items-start gap-2 py-1 border-b border-border/40 last:border-0">
            <span class="text-[10px] text-muted w-28 shrink-0 truncate" :title="it.label">{{ it.label }}</span>
            <span class="text-xs flex-1 min-w-0 break-words" :class="valClass(it)">{{ it.value }}</span>
            <span class="text-[10px] text-muted shrink-0 font-mono cursor-help"
                  :title="`${srcCn(it.source)} · ${it.caliber}`">{{ hhmm(it.as_of) }}</span>
          </div>
        </div>
      </div>

      <!-- ④ 消息面 -->
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center gap-2 mb-2">
          <span class="text-sm font-semibold">消息面</span>
          <span class="text-[10px] text-muted">当日要点与重要快讯</span>
          <span class="text-[10px] text-muted ml-auto font-mono">{{ srcLabel(data.blocks.news) }}</span>
        </div>
        <div v-if="!data.blocks.news.length" class="text-xs text-muted">
          —（该日尚未生成快讯解读；只读页不会触发 LLM）
        </div>
        <div v-else class="space-y-2">
          <div v-for="(it, i) in data.blocks.news" :key="i"
               class="flex items-start gap-2">
            <span class="text-[10px] text-muted font-mono w-10 shrink-0">{{ hhmm(it.as_of) || '—' }}</span>
            <span class="w-0.5 self-stretch rounded bg-teal-500/40 shrink-0"></span>
            <div class="min-w-0">
              <div class="text-xs leading-relaxed break-words">{{ it.value }}</div>
              <div v-if="it.caliber" class="text-[10px] text-muted mt-0.5">{{ it.caliber }}</div>
            </div>
          </div>
        </div>
      </div>

      <!-- ⑤ 口径与溯源 -->
      <div class="bg-card border border-border rounded-lg p-3">
        <button type="button" class="flex items-center gap-2 w-full text-left"
                @click="traceOpen = !traceOpen">
          <span class="text-sm font-semibold">口径与溯源</span>
          <span class="text-[10px] text-muted">{{ data.trace.length }} 项</span>
          <span v-if="data.missing.length"
                class="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 text-[10px]">
            未留存 {{ data.missing.length }}
          </span>
          <span class="ml-auto text-[10px] text-muted">{{ traceOpen ? '▾' : '▸' }}</span>
        </button>
        <div v-if="traceOpen" class="mt-2 space-y-1">
          <div v-for="(m, i) in data.missing" :key="'m' + i"
               class="text-[10px] text-muted bg-border/30 rounded px-2 py-1">
            <span class="text-amber-300">{{ blockCn(m.block) }}</span> / {{ m.item }} —— {{ m.reason }}
          </div>
          <div v-for="(t, i) in data.trace" :key="'t' + i"
               class="grid grid-cols-[52px_1fr_auto] gap-2 text-[10px] text-muted border-b border-border/40 last:border-0 py-0.5">
            <span class="truncate">{{ blockCn(t.block) }}</span>
            <span class="truncate" :title="t.item">{{ t.item }}</span>
            <span class="truncate" :title="t.source">{{ srcCn(t.source) }} · {{ hhmm(t.as_of) || '—' }}</span>
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
const traceOpen = ref(false)

const conclusion = computed(() => props.data?.conclusion || {})

// ★★ 2026-11-01（用户："其中有些是英文字眼，得换成中文"）：
//   后端 `blocks` 的 key（market/macro/news）与各数据源标识（表名/接口名）都是英文，
//   直接摆到界面上是"数据库术语"，对使用者没有意义。这里统一做**展示层翻译**，
//   不改变后端结构（trace/missing 仍按英文 key 传递，便于排障时对得上代码）。
const BLOCK_CN = { market: '盘面', macro: '宏观', news: '消息' }
const SRC_CN = {
  market_emotion_daily: '情绪快照表',
  market_amount: '两市成交额',
  'market_overview 快照': '指数行情',
  market_regime_history: '市场状态表',
  macro_daily_history: '外盘日线',
  flash_calendar: '财经日历',
  flash_brief: '快讯解读',
  'flash_brief.news': '快讯条目',
  腾讯指数: '指数行情',
}
const blockCn = (b) => BLOCK_CN[b] || b || ''
const srcCn = (s) => SRC_CN[s] || s || ''
const html = computed(() => renderMd(conclusion.value.markdown || ''))
/** 结论首行（去掉 Markdown 标记）—— 卡片折叠时先给一句判断 */
const lead = computed(() => {
  const line = String(conclusion.value.markdown || '')
    .split('\n')
    .map((s) => s.replace(/^#+\s*/, '').replace(/\*\*/g, '').trim())
    .find((s) => s.length > 0) || ''
  return line.slice(0, 160)
})
/** 该日没有 11:30 定稿 ⇒ 页面上要显式提示"这是收盘口径" */
const stale = computed(() =>
  (props.data?.blocks?.market || []).some((i) => String(i.caliber || '').includes('收盘定稿口径')))

function hhmm(s) {
  const t = String(s || '')
  const m = t.match(/(\d{2}:\d{2})/)
  return m ? m[1] : (t.length === 10 ? t : '')
}
/** 每块右上角标"主要来源"，避免用户以为所有数字都来自同一处 */
function srcLabel(items) {
  const srcs = [...new Set((items || []).map((i) => i.source).filter(Boolean))]
  if (!srcs.length) return ''
  const cn = srcs.map(srcCn)
  return cn.length === 1 ? cn[0] : `${cn[0]} 等 ${cn.length} 处`
}
/** 有 delta 的按红涨绿跌；否则一律普通前景色（避免文字被判读成涨跌） */
function valClass(it) {
  // 无 delta 的（家数/判读/成交额…）**不加颜色** —— 给它们红绿会被读成涨跌
  if (it?.delta == null) return ''
  const v = Number(it.delta)
  return v > 0 ? 'text-red-400' : (v < 0 ? 'text-emerald-400' : '')
}
</script>
