<template>
  <span :class="change >= 0 ? 'text-rise' : 'text-fall'">
    {{ change >= 0 ? '+' : '' }}{{ change }}%
  </span>
</template>
<script setup>
defineProps({ change: { type: Number, default: 0 } })
</script>