import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  base: '/stock-scoring-v2/',
  plugins: [vue()],
  resolve: {
    alias: {
      // 启用 Vue 运行时编译器（支持组件内 inline template 字符串）
      vue: 'vue/dist/vue.esm-bundler.js',
    },
  },
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      }
    }
  }
})
