import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: true,
    proxy: {
      // 后端所有 /api/* 路径走 FastAPI
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true
      },
      // 健康检查也走后端（绕过 vite SPA fallback）
      '/health': {
        target: 'http://localhost:8000',
        changeOrigin: true
      }
    }
  }
})