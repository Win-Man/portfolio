/**
 * 全局 toast 单例
 *
 * 在 App.vue 挂载 <Toast ref="toastRef" />，通过 setToastRef() 注册。
 * 任何地方 import { toast } from '@/composables/useToast' 即可调 toast.success / error / info。
 *
 * 也支持 provide/inject：在 setup() 里 const t = useToast()，调用 t.success('xxx')。
 */
import { ref, inject } from 'vue'

const _toast = ref(null)

export function setToastRef(refInstance) {
  _toast.value = refInstance
}

function push(type, message, duration) {
  if (_toast.value) {
    _toast.value.push(type, message, duration)
  } else {
    // 兜底：还没挂载时用 console
    console[type === 'error' ? 'error' : 'log']`[toast] ${message}`
  }
}

export const toast = {
  success: (msg, duration) => push('success', msg, duration),
  error:   (msg, duration) => push('error', msg, duration ?? 5000),
  info:    (msg, duration) => push('info', msg, duration),
  warning: (msg, duration) => push('warning', msg, duration ?? 4000),
}

const TOKE_N_KEY = Symbol('toast')

export function provideToast(app) {
  app.provide(TOKE_N_KEY, toast)
}

export function useToast() {
  return inject(TOKE_N_KEY, toast)
}