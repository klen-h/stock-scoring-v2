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
    build: {
      // ★★ 2026-11-04（实测 `ENOTDIR: not a directory, scandir '.../dist/.user.ini'`）：
      //   宝塔"防跨站攻击"会在**网站根目录**（本项目 = `frontend/dist`）放一个 `.user.ini`，
      //   而 vite 构建前会 `emptyDir(outDir)` ⇒ 删到它必然失败（该文件常带 immutable，连 root 都删不掉）。
      //   ⇒ 用 `VITE_OUT_DIR` 指定另一个产物目录（`deploy.sh` 传 `dist-build`），
      //     构建完再把产物同步回 `dist` 并**保留 `.user.ini`**。
      //   ⚠️ 必须读 `process.env`：`loadEnv()` 只读 `.env.*` 文件，**看不到 shell 变量**
      //      （与上面 `base` 那个坑同源，别再只写 `env.VITE_OUT_DIR`）。
      //   ⚠️ 也**不能**用 CLI `--outDir`：实测 `npm run build:server -- --outDir=xxx`
      //      在 Windows 下不生效（产物照样写进 dist），直调 `vite.js` 才行 —— 配置最稳。
      outDir: process.env.VITE_OUT_DIR || env.VITE_OUT_DIR || 'dist',
    },
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
