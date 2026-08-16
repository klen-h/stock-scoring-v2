<template>
  <div class="flex items-center justify-center py-20">
    <div class="loading-spinner"></div>
    <span class="ml-3 text-muted text-sm">{{ text || '加载中...' }}</span>
  </div>
</template>
<script setup>
defineProps({ text: String })
</script>