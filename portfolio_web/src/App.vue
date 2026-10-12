<template>
  <div class="app">
    <Portfolio />
    <Toast ref="toastRef" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import Portfolio from './views/Portfolio.vue'
import Toast from './components/Toast.vue'
import { setToastRef } from './composables/useToast.js'

const toastRef = ref(null)

onMounted(() => {
  // 注册全局 toast 单例，供任意模块的 toast.success/error 调用
  setToastRef(toastRef.value)
})
</script>