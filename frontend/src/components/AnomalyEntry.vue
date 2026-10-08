<template>
  <!-- 异动分析入口（**共享组件**）★ 2026-10-08 P3
       【为什么必须做成组件】观察池在**两个地方**渲染 —— 工作台右栏
       `workbench/StockListsCards.vue` 与榜单页观察池表格 `ScoreRank.vue`。
       各自写一份入口必然漂移（本项目铁律："同一数据两处渲染=必然漂移"）。
       【成本纪律】本入口**只做「跳转 + 展开」**，**不自动调 LLM** ——
       详情页的「异动分析」块展开后，由用户点「开始分析」才真消耗 1 次调用。
       （若要改成"一点即析"，把 href 的 `anomaly=1` 改为 `analyze=1` 并在详情页处理即可，
         但那会变成"导航即烧钱"，与"按需触发 + 成本可见"的纪律相悖。） -->
  <a :href="href" target="_blank" rel="noopener" @click.stop
     class="px-1 rounded text-[10px] bg-accent/15 text-accent hover:bg-accent/25 shrink-0"
     :title="tip">异动</a>
</template>

<script setup>
import { computed } from 'vue'
import { stockHref } from '../composables/displayMeta'

const props = defineProps({
  code: { type: String, required: true },
})

// `?anomaly=1` ⇒ 详情页把「异动分析」块**默认展开**（仍然不自动请求）
const href = computed(() => `${stockHref(props.code)}?anomaly=1`)
const tip = '打开该股详情并展开「异动分析」（LLM 判读：拉高出货 / 诱多上套 / 真实突破）。' +
  '本入口不会自动消耗 LLM，需在页面内点「开始分析」。'
</script>
