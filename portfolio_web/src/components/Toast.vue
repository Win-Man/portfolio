<template>
  <div class="toast-container">
    <transition-group name="toast" tag="div">
      <div
        v-for="t in toasts"
        :key="t.id"
        :class="['toast', 'toast-' + t.type]"
      >
        <span class="toast-icon">{{ icon(t.type) }}</span>
        <span class="toast-message">{{ t.message }}</span>
        <button class="toast-close" @click="remove(t.id)">×</button>
      </div>
    </transition-group>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const toasts = ref([])
let nextId = 1

function icon(type) {
  return { success: '✓', error: '✕', info: 'ℹ', warning: '⚠' }[type] || 'ℹ'
}

function remove(id) {
  toasts.value = toasts.value.filter(t => t.id !== id)
}

function push(type, message, duration = 3000) {
  const id = nextId++
  toasts.value.push({ id, type, message })
  if (duration > 0) {
    setTimeout(() => remove(id), duration)
  }
}

defineExpose({ push, remove })
</script>

<style scoped>
.toast-container {
  position: fixed;
  top: 24px;
  right: 24px;
  z-index: 9999;
  display: flex;
  flex-direction: column;
  gap: 8px;
  pointer-events: none;
}

.toast {
  pointer-events: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 240px;
  max-width: 400px;
  padding: 10px 14px;
  border-radius: 8px;
  background: #fff;
  color: #1e293b;
  box-shadow: 0 4px 12px rgba(0,0,0,0.15);
  font-size: 14px;
  border-left: 4px solid #64748b;
}

.toast-success { border-left-color: #22c55e; }
.toast-error   { border-left-color: #ef4444; }
.toast-warning { border-left-color: #f59e0b; }
.toast-info    { border-left-color: #3b82f6; }

.toast-icon { font-size: 16px; font-weight: 700; }
.toast-message { flex: 1; }
.toast-close {
  background: none;
  border: none;
  font-size: 18px;
  cursor: pointer;
  color: #64748b;
  padding: 0 4px;
}
.toast-close:hover { color: #1e293b; }

.toast-enter-from {
  opacity: 0;
  transform: translateX(20px);
}
.toast-leave-to {
  opacity: 0;
  transform: translateX(20px);
}
.toast-enter-active, .toast-leave-active {
  transition: all 0.25s ease;
}
</style>