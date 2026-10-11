<template>
  <!-- ★★ 2026-10-11（L2）「观点卡」：把**一句话结论**置顶、依据可选折叠。
       它只做**展示**、不做判断：结论来自生成侧**显式写出**的 `**结论**：…` 首行
       （解析见 `composables/markdown.js::splitInsight`）—— **没有标记就完全按普通
       md-body 渲染**，所以对旧数据/未改的生成器**零影响**（向后兼容是硬约束）。
       ⚠️ 根元素自带 `md-body` ⇒ 调用方传来的 `text-xs` / `max-h-*` 等工具类仍照旧生效
       （`:where(.md-body)` 零特异性，与改造前 `class="md-body text-xs"` 行为一致）。 -->
  <div class="md-body">
    <div v-if="conclusion" class="insight-conclusion" v-html="conclusionHtml"></div>
    <template v-if="conclusion && collapsible">
      <button type="button"
              class="mb-1 text-[11px] text-muted hover:text-accent transition-colors"
              @click="open = !open">
        <span class="text-[10px]">{{ open ? '▼' : '▶' }}</span>
        {{ open ? '收起' : '展开' }}{{ label }}
      </button>
      <div v-show="open" v-html="bodyHtml"></div>
    </template>
    <div v-else-if="bodyHtml" v-html="bodyHtml"></div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { renderMd, splitInsight } from '../composables/markdown'

const props = defineProps({
  text: { type: String, default: '' },
  // 「依据」默认收起 —— 只对**卡片内嵌的观点**开（雷达 / 一页纸诊断）：先给结论，细节由用户点开。
  // 整页长文（日报/简报/周报/复盘）**不要**开：那类场景用户就是要通读，折叠反而添堵。
  collapsible: { type: Boolean, default: false },
  label: { type: String, default: '依据' },
})

const parts = computed(() => splitInsight(props.text))
const conclusionHtml = computed(() => renderMd(parts.value.conclusion))
const bodyHtml = computed(() => renderMd(parts.value.body))
const open = ref(false)
</script>
