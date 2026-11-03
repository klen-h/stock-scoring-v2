import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

// ★★ 2026-11-03（自有服务器上线踩到两次，记下来）：
//   ① `base` 原来写死 `/stock-scoring-v2/`（GitHub Pages 部署在子路径下必须如此），
//      而自有服务器把 dist 挂在**站点根** ⇒ 资源变成 `/stock-scoring-v2/assets/x.js`
//      而服务器上文件在 `/assets/x.js` ⇒ 全部 404。
//   ② 改成 `process.env.VITE_BASE` 后**仍然不生效** —— 因为 `vite.config.js`
//      在 **环境变量文件(.env.*) 加载之前**求值，此时 `process.env` 里什么都没有。
//      ⇒ 必须用 `loadEnv(mode, ...)` **自己读**（这是本项目踩过的具体坑，不是理论）。
//
//   用法：
//     GitHub Pages（默认）：  npm run build           → base=/stock-scoring-v2/
//     自有服务器：            npm run build:server    → 读 `.env.server`，base=/、API=/api
export default defineConfig(({ mode }) => {
  // 第三个参数传 '' = 读取**全部**变量（不只是 VITE_ 前缀）；
  // base 需要在 node 侧使用，不受"只有 VITE_ 才会暴露给客户端"的限制。
  const env = loadEnv(mode, process.cwd(), '')

  return {
    base: env.VITE_BASE || '/stock-scoring-v2/',
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
  }
})
