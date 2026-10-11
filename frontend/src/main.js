import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'
// ★ 2026-10-11（L1）：观点输出（LLM 日报/简报/复盘/矛盾报告/一页纸诊断）的**统一排版与重点层级**。
//   此前各页面在自己的 `<style scoped>` 里各抄一份、已经漂移 ⇒ 收拢为全站唯一实现。
//   引入顺序：须在 `style.css`（Tailwind）之后。
import './styles/md.css'

createApp(App).use(router).mount('#app')
