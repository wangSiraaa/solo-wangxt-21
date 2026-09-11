<script setup>
defineProps({ reason: { type: Object, required: true } })
</script>

<template>
  <div class="reason-card" :class="{ bye: reason.role === 'bye' }">
    <div class="head">
      {{ reason.role === 'bye' ? '⏸ ' : '⚔ ' }}{{ reason.headline }}
    </div>
    <ul>
      <li v-for="(f, i) in reason.factors" :key="i">{{ f }}</li>
    </ul>
  </div>
</template>
