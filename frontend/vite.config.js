import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  // ★★ 2026-11-03（自有服务器上线踩到）：**base 不能写死**。
  //   原来固定 `/stock-scoring-v2/`（GitHub Pages 部署在子路径下必须这样），
  //   但自有服务器把 `dist` 挂在**站点根目录** ⇒ 构建产物里的资源路径
  //   会变成 `/stock-scoring-v2/assets/xxx.js`，而服务器上文件在 `/assets/xxx.js`
  //   ⇒ 全部 404，浏览器报 "MIME type of text/html"（被 SPA 回退兜成了 index.html）。
  //   ⇒ 改成环境变量可覆盖，**默认值保持不变**（GitHub Pages 照旧可用）：
  //     自有服务器构建：      VITE_BASE=/ npm run build
  //     本地开发（默认）：     npm run build
  base: process.env.VITE_BASE || '/stock-scoring-v2/',
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
