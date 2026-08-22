// vite.config.js
import { defineConfig } from "file:///D:/stock-scoring/stock-scoring-v2/frontend/node_modules/.pnpm/vite@5.4.21/node_modules/vite/dist/node/index.js";
import vue from "file:///D:/stock-scoring/stock-scoring-v2/frontend/node_modules/.pnpm/@vitejs+plugin-vue@5.2.4_vite@5.4.21_vue@3.5.40/node_modules/@vitejs/plugin-vue/dist/index.mjs";
var vite_config_default = defineConfig({
  base: "/stock-scoring-v2/",
  plugins: [vue()],
  resolve: {
    alias: {
      // 启用 Vue 运行时编译器（支持组件内 inline template 字符串）
      vue: "vue/dist/vue.esm-bundler.js"
    }
  },
  server: {
    port: 3e3,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true
      },
      // 腾讯行情 CORS 代理（开发环境）
      // /tencent-api/q=sz000001 → https://qt.gtimg.cn/q=sz000001
      "/tencent-api": {
        target: "https://qt.gtimg.cn",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/tencent-api/, "")
      }
    }
  }
});
export {
  vite_config_default as default
};
//# sourceMappingURL=data:application/json;base64,ewogICJ2ZXJzaW9uIjogMywKICAic291cmNlcyI6IFsidml0ZS5jb25maWcuanMiXSwKICAic291cmNlc0NvbnRlbnQiOiBbImNvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9kaXJuYW1lID0gXCJEOlxcXFxzdG9jay1zY29yaW5nXFxcXHN0b2NrLXNjb3JpbmctdjJcXFxcZnJvbnRlbmRcIjtjb25zdCBfX3ZpdGVfaW5qZWN0ZWRfb3JpZ2luYWxfZmlsZW5hbWUgPSBcIkQ6XFxcXHN0b2NrLXNjb3JpbmdcXFxcc3RvY2stc2NvcmluZy12MlxcXFxmcm9udGVuZFxcXFx2aXRlLmNvbmZpZy5qc1wiO2NvbnN0IF9fdml0ZV9pbmplY3RlZF9vcmlnaW5hbF9pbXBvcnRfbWV0YV91cmwgPSBcImZpbGU6Ly8vRDovc3RvY2stc2NvcmluZy9zdG9jay1zY29yaW5nLXYyL2Zyb250ZW5kL3ZpdGUuY29uZmlnLmpzXCI7aW1wb3J0IHsgZGVmaW5lQ29uZmlnIH0gZnJvbSAndml0ZSdcclxuaW1wb3J0IHZ1ZSBmcm9tICdAdml0ZWpzL3BsdWdpbi12dWUnXHJcblxyXG5leHBvcnQgZGVmYXVsdCBkZWZpbmVDb25maWcoe1xyXG4gIGJhc2U6ICcvc3RvY2stc2NvcmluZy12Mi8nLFxyXG4gIHBsdWdpbnM6IFt2dWUoKV0sXHJcbiAgcmVzb2x2ZToge1xyXG4gICAgYWxpYXM6IHtcclxuICAgICAgLy8gXHU1NDJGXHU3NTI4IFZ1ZSBcdThGRDBcdTg4NENcdTY1RjZcdTdGMTZcdThCRDFcdTU2NjhcdUZGMDhcdTY1MkZcdTYzMDFcdTdFQzRcdTRFRjZcdTUxODUgaW5saW5lIHRlbXBsYXRlIFx1NUI1N1x1N0IyNlx1NEUzMlx1RkYwOVxyXG4gICAgICB2dWU6ICd2dWUvZGlzdC92dWUuZXNtLWJ1bmRsZXIuanMnLFxyXG4gICAgfSxcclxuICB9LFxyXG4gIHNlcnZlcjoge1xyXG4gICAgcG9ydDogMzAwMCxcclxuICAgIHByb3h5OiB7XHJcbiAgICAgICcvYXBpJzoge1xyXG4gICAgICAgIHRhcmdldDogJ2h0dHA6Ly9sb2NhbGhvc3Q6ODAwMCcsXHJcbiAgICAgICAgY2hhbmdlT3JpZ2luOiB0cnVlLFxyXG4gICAgICB9LFxyXG4gICAgICAvLyBcdTgxN0VcdThCQUZcdTg4NENcdTYwQzUgQ09SUyBcdTRFRTNcdTc0MDZcdUZGMDhcdTVGMDBcdTUzRDFcdTczQUZcdTU4ODNcdUZGMDlcclxuICAgICAgLy8gL3RlbmNlbnQtYXBpL3E9c3owMDAwMDEgXHUyMTkyIGh0dHBzOi8vcXQuZ3RpbWcuY24vcT1zejAwMDAwMVxyXG4gICAgICAnL3RlbmNlbnQtYXBpJzoge1xyXG4gICAgICAgIHRhcmdldDogJ2h0dHBzOi8vcXQuZ3RpbWcuY24nLFxyXG4gICAgICAgIGNoYW5nZU9yaWdpbjogdHJ1ZSxcclxuICAgICAgICByZXdyaXRlOiAocGF0aCkgPT4gcGF0aC5yZXBsYWNlKC9eXFwvdGVuY2VudC1hcGkvLCAnJyksXHJcbiAgICAgIH1cclxuICAgIH1cclxuICB9XHJcbn0pXHJcbiJdLAogICJtYXBwaW5ncyI6ICI7QUFBd1QsU0FBUyxvQkFBb0I7QUFDclYsT0FBTyxTQUFTO0FBRWhCLElBQU8sc0JBQVEsYUFBYTtBQUFBLEVBQzFCLE1BQU07QUFBQSxFQUNOLFNBQVMsQ0FBQyxJQUFJLENBQUM7QUFBQSxFQUNmLFNBQVM7QUFBQSxJQUNQLE9BQU87QUFBQTtBQUFBLE1BRUwsS0FBSztBQUFBLElBQ1A7QUFBQSxFQUNGO0FBQUEsRUFDQSxRQUFRO0FBQUEsSUFDTixNQUFNO0FBQUEsSUFDTixPQUFPO0FBQUEsTUFDTCxRQUFRO0FBQUEsUUFDTixRQUFRO0FBQUEsUUFDUixjQUFjO0FBQUEsTUFDaEI7QUFBQTtBQUFBO0FBQUEsTUFHQSxnQkFBZ0I7QUFBQSxRQUNkLFFBQVE7QUFBQSxRQUNSLGNBQWM7QUFBQSxRQUNkLFNBQVMsQ0FBQyxTQUFTLEtBQUssUUFBUSxrQkFBa0IsRUFBRTtBQUFBLE1BQ3REO0FBQUEsSUFDRjtBQUFBLEVBQ0Y7QUFDRixDQUFDOyIsCiAgIm5hbWVzIjogW10KfQo=
