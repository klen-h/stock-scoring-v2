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
      },
      // 腾讯行情 CORS 代理（开发环境）
      // /tencent-api/q=sz000001 → https://qt.gtimg.cn/q=sz000001
      '/tencent-api': {
        target: 'https://qt.gtimg.cn',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/tencent-api/, ''),
      }
    }
  }
})
