<template>
  <!--
    个股展示唯一组件（2026-11-28，见 .codebuddy/rules/stock-display/RULE.mdc）
    同时实现三条硬规则：名称带代码 / 名称跳本地详情 / 代码跳雪球。
    页面里用它就不可能漏代码、也不可能拼错市场前缀。
  -->
  <span class="inline-flex items-baseline gap-0.5 whitespace-nowrap">
    <a :href="stockHref(code)" target="_blank" rel="noopener"
       class="text-gray-200 hover:text-accent hover:underline">{{ displayName }}</a>
    <span class="text-muted">（</span>
    <a :href="xqUrl(code)" target="_blank" rel="noopener"
       class="font-mono text-muted hover:text-gray-200 hover:underline"
       :title="'在雪球查看 ' + code">{{ code }}</a>
    <span class="text-muted">）</span>
  </span>
</template>

<script setup>
import { computed } from 'vue'
import { stockHref, xqUrl } from '../composables/displayMeta'

const props = defineProps({
  code: { type: [String, Number], required: true },
  name: { type: String, default: '' },
})

// 名称缺失时用代码兜底 —— 至少不会出现"空名称 + 代码"的半截展示
const displayName = computed(() => props.name || String(props.code))
</script>
