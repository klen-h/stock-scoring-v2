<template>
  <div class="fade-in space-y-4">
    <!-- ① 头部 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">动作协议</h1>
          <p class="text-xs text-muted mt-0.5">
            规则层：触发条件命中 → 首动作 / 裁决 / 仓位路径 / 失效条件。
            盘中命中会推企微。<b class="text-gray-300">不自动下单、不改评分、不进闸门</b>。
          </p>
          <p class="text-[11px] text-muted mt-1">
            与「推演复盘」分工：那张回答<b class="text-gray-300">会怎样</b>，这张回答<b class="text-gray-300">怎么办</b>。
          </p>
        </div>
        <button @click="reload" :disabled="loading"
          class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
          {{ loading ? '加载中…' : '刷新' }}
        </button>
      </div>
    </div>

    <!-- ② 匹配测试：贴一段快讯，看会翻哪张卡 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center gap-2 mb-2 flex-wrap">
        <h2 class="text-sm font-bold text-gray-200">匹配测试</h2>
        <span class="text-[11px] text-muted">
          贴一段快讯/通稿，看会翻哪张卡 —— 不落库、不推送、不调 LLM
        </span>
      </div>
      <textarea v-model="testText" rows="3"
        placeholder="例：粘贴国常会通稿全文，或任意一条快讯…"
        class="w-full px-3 py-2 rounded text-xs bg-bg border border-border text-gray-200 placeholder:text-muted focus:border-accent/40 focus:outline-none resize-y"></textarea>
      <div class="flex items-center gap-2 mt-2 flex-wrap">
        <button @click="doMatch" :disabled="matching || !testText.trim()"
          class="px-3 py-1 rounded text-xs bg-accent/15 text-accent border border-accent/30 hover:bg-accent/25 transition-colors disabled:opacity-50">
          {{ matching ? '匹配中…' : '匹配' }}
        </button>
        <button @click="testText = ''; matchHits = []; matchMsg = ''"
          class="px-3 py-1 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors">
          清空
        </button>
        <span v-if="matchMsg" class="text-[11px]"
          :class="matchHits.length ? 'text-amber-300' : 'text-muted'">{{ matchMsg }}</span>
      </div>
      <div v-if="matchHits.length" class="mt-2 space-y-1">
        <div v-for="h in matchHits" :key="h.id"
          class="text-[11px] px-2 py-1.5 rounded bg-amber-500/10 border border-amber-500/30 text-amber-200">
          ⚡ 命中「{{ h.name }}」
          <span class="text-muted ml-1">
            · {{ (h._hit_keywords || []).length }} 个关键词：{{ (h._hit_keywords || []).join('、') }}
          </span>
        </div>
      </div>
    </div>

    <!-- ③ 协议卡列表 -->
    <div v-if="err" class="text-xs text-fall px-1">⚠️ {{ err }}</div>

    <div v-if="!loading && !list.length"
      class="bg-card border border-border rounded-lg p-8 text-center text-xs text-muted">
      还没有协议卡<br />
      <span class="text-[10px] mt-1 inline-block">
        跑 <code class="px-1 py-0.5 rounded bg-bg border border-border">python scripts/seed_action_protocol.py</code>
        或调 <code class="px-1 py-0.5 rounded bg-bg border border-border">POST /api/thesis/protocols</code> 登记
      </span>
    </div>

    <div v-for="p in list" :key="p.id"
      class="bg-card border rounded-lg p-4 transition-colors"
      :class="hitIds.has(p.id) ? 'border-amber-500/60 shadow-lg shadow-amber-500/5' : 'border-border'">
      <div class="flex items-start justify-between gap-3 mb-2 flex-wrap">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-sm font-bold text-gray-100">{{ p.name }}</span>
          <span class="px-1.5 py-0.5 rounded text-[10px] border"
            :class="p.status === 'active'
              ? 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30'
              : 'bg-white/5 text-muted border-border'">
            {{ p.status === 'active' ? '启用' : '归档' }}
          </span>
          <span v-if="hitIds.has(p.id)"
            class="px-1.5 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300">当前命中</span>
        </div>
        <button v-if="p.status === 'active'" @click="archive(p)" :disabled="archiving === p.id"
          class="text-[10px] px-2 py-0.5 rounded border border-border text-muted hover:text-fall hover:border-fall/40 transition-colors disabled:opacity-50">
          {{ archiving === p.id ? '…' : '归档' }}
        </button>
      </div>

      <!-- 触发器：日历事件 或 快讯关键词（二者之一） -->
      <div v-if="p.calendar && p.calendar.date"
        class="flex items-center gap-2 text-[11px] mb-2 flex-wrap px-2 py-1.5 rounded bg-sky-500/10 border border-sky-500/25">
        <span class="text-sky-300 shrink-0">📅 日历</span>
        <span class="font-mono text-sky-200">{{ p.calendar.date }}</span>
        <span class="text-gray-200">{{ p.calendar.event }}</span>
        <span v-if="p.calendar.time" class="text-muted">{{ p.calendar.time }}</span>
        <span v-if="p.calendar.note" class="text-[10px] text-muted w-full">{{ p.calendar.note }}</span>
      </div>
      <div v-if="(p.trigger.keywords || []).length"
        class="flex items-start gap-2 text-[11px] mb-3 flex-wrap">
        <span class="text-muted shrink-0">触发</span>
        <span class="flex items-center gap-1 flex-wrap">
          <span v-for="k in (p.trigger.keywords || [])" :key="k"
            class="px-1.5 py-0.5 rounded bg-white/5 border border-border/50 text-gray-300">{{ k }}</span>
        </span>
        <span class="text-muted ml-auto shrink-0">
          min_hits {{ p.trigger.min_hits || 1 }} · {{ p.trigger.window || '—' }}
        </span>
      </div>
      <p v-if="p.trigger && p.trigger.note" class="text-[10px] text-muted mb-2 -mt-1">{{ p.trigger.note }}</p>

      <!-- ★ 分支预案：如果…就…（日历卡的核心，事件结果未知但动作已定） -->
      <div v-if="(p.branches || []).length" class="mb-3 space-y-1">
        <div v-for="(b, i) in p.branches" :key="i"
          class="flex items-start gap-2 text-[11px] px-2 py-1.5 rounded bg-bg/70 border border-border/40">
          <span class="shrink-0 px-1 py-0.5 rounded text-[10px] bg-sky-500/15 text-sky-300">如果</span>
          <span class="text-gray-300 flex-1 leading-relaxed">{{ b.if }}</span>
          <span class="shrink-0 text-muted">→</span>
          <span class="shrink-0 px-1 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-300">则</span>
          <span class="text-gray-200 flex-1 leading-relaxed">{{ b.then }}</span>
        </div>
      </div>

      <!-- 四段动作 -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
        <div v-for="f in FIELDS" :key="f.key"
          class="bg-bg/60 border border-border/40 rounded p-2.5">
          <div class="text-[10px] text-muted mb-1">{{ f.icon }} {{ f.label }}</div>
          <div class="text-[11px] leading-relaxed whitespace-pre-line" :class="f.cls">
            {{ p[f.key] || '—' }}
          </div>
        </div>
      </div>
    </div>

    <p class="text-[10px] text-muted px-1">
      协议卡只回答「触发后第一步做什么」，不构成买卖建议。命中推送走企微，每张卡每日至多一次。
    </p>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import {
  getActionProtocols, matchActionProtocol, archiveActionProtocol,
} from '../api/modules/system'

// 四段动作的展示顺序与配色：裁决最重要（它是打破规则冲突的那一条），用琥珀强调
const FIELDS = [
  { key: 'first_move', label: '首动作（T+0）', icon: '①', cls: 'text-gray-200' },
  { key: 'verdict', label: '裁决条款', icon: '②', cls: 'text-amber-200' },
  { key: 'position_path', label: '仓位路径', icon: '③', cls: 'text-cyan-200' },
  { key: 'invalidate', label: '失效条件', icon: '④', cls: 'text-fall' },
]

const list = ref([])
const loading = ref(false)
const err = ref('')
const archiving = ref('')

const testText = ref('')
const matching = ref(false)
const matchMsg = ref('')
const matchHits = ref([])
const hitIds = computed(() => new Set(matchHits.value.map(h => h.id)))

async function loadList() {
  loading.value = true
  err.value = ''
  try {
    const { data: d } = await getActionProtocols(false)
    list.value = (d && d.data) || []
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message || '加载失败'
    list.value = []
  } finally {
    loading.value = false
  }
}

function reload() {
  loadList()
}

async function doMatch() {
  const text = testText.value.trim()
  if (!text) return
  matching.value = true
  matchMsg.value = ''
  matchHits.value = []
  try {
    const { data: d } = await matchActionProtocol(text)
    matchHits.value = (d && d.data) || []
    matchMsg.value = matchHits.value.length
      ? `命中 ${matchHits.value.length} 张卡 —— 盘中会推企微`
      : '未命中任何协议（关键词不足或不在启用状态）'
  } catch (e) {
    matchMsg.value = e?.response?.data?.detail || e.message || '匹配失败'
  } finally {
    matching.value = false
  }
}

async function archive(p) {
  if (!confirm(`归档「${p.name}」？归档后不再参与盘中匹配。`)) return
  archiving.value = p.id
  try {
    await archiveActionProtocol(p.id)
    p.status = 'archived'
  } catch (e) {
    err.value = e?.response?.data?.detail || e.message || '归档失败'
  } finally {
    archiving.value = ''
  }
}

onMounted(loadList)
</script>
