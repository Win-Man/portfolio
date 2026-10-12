<template>
  <div class="tooltip">
    <span class="tooltip-icon">?</span>
    <div class="tooltip-content">{{ text }}</div>
  </div>
</template>

<script setup>
defineProps({
  text: {
    type: String,
    required: true
  }
})
</script>
