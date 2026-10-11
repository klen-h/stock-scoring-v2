<template>
  <!-- ★★ 2026-10-11（L2/L3）「观点卡」：把**一句话结论**置顶、依据按角色渲染（可折叠）。
       它只做**展示**、不做判断。两种来源，优先级从高到低：
         ① **结构化**（L3）：后端 `app/insight.py` 校验后的 `conclusion` + `reasons[]`，
            直接按角色渲染（结论条 + 依据列表）—— 不依赖任何文本标记；
         ② **文本通道**（L2/L1）：从 `text` 的首行抽 `**结论**：…`（`splitInsight`）；
            抽不到就整段按普通 md 渲染（**旧数据/未升级的生成器零影响**）。
       ⚠️ 根元素自带 `md-body`：调用方传的 `text-xs` / `max-h-*` 等工具类仍照旧生效
       （`:where(.md-body)` 零特异性，与改造前 `class="md-body text-xs"` 行为一致）。 -->
  <div class="md-body">
    <div v-if="conclusionHtml" class="insight-conclusion" v-html="conclusionHtml"></div>
    <template v-if="collapsible && conclusionHtml">
      <button type="button"
              class="mb-1 text-[11px] text-muted hover:text-accent transition-colors"
              @click="open = !open">
        <span class="text-[10px]">{{ open ? '▼' : '▶' }}</span>
        {{ open ? '收起' : '展开' }}{{ label }}<span v-if="reasonCount">（{{ reasonCount }} 条）</span>
      </button>
      <div v-show="open" v-html="bodyHtml"></div>
    </template>
    <div v-else-if="bodyHtml" v-html="bodyHtml"></div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { renderMd, reasonsMarkdown, splitInsight } from '../composables/markdown'

const props = defineProps({
  // 文本通道（L2/L1）：整段 markdown。结构化字段缺失时才会被解析。
  text: { type: String, default: '' },
  // 结构化通道（L3）：后端下发的结论与依据（**优先**）。
  conclusion: { type: String, default: '' },
  reasons: { type: Array, default: () => [] },
  // 「依据」默认收起 —— 只对**卡片内嵌的观点**开（雷达 / 一页纸诊断）：先给结论，细节由用户点开。
  // 整页长文（日报/简报/周报/复盘）**不要**开：那类场景用户就是要通读，折叠反而添堵。
  collapsible: { type: Boolean, default: false },
  label: { type: String, default: '依据' },
})

// 有结构化依据 ⇒ 文本通道整体让位（`text` 是后端按同一份数据组装的，重复渲染没有意义）
const structured = computed(() => Array.isArray(props.reasons) && props.reasons.length > 0)
const parts = computed(() => (structured.value
  ? { conclusion: '', body: '' }
  : splitInsight(props.text)))

const conclusionHtml = computed(() => renderMd(props.conclusion || parts.value.conclusion))
const bodyHtml = computed(() => renderMd(structured.value
  ? reasonsMarkdown(props.reasons)
  : parts.value.body))
const reasonCount = computed(() => (structured.value ? props.reasons.length : 0))
const open = ref(false)
</script>
