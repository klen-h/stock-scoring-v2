<template>
  <div class="min-h-screen bg-bg text-gray-200">
    <!-- 顶部导航 -->
    <nav class="bg-card border-b border-border sticky top-0 z-50">
      <div class="max-w-[1600px] mx-auto px-4 h-12 flex items-center justify-between">
        <div class="flex items-center gap-6">
          <router-link to="/" class="font-bold text-accent text-sm tracking-wide">A股评分系统</router-link>
          <div class="hidden md:flex gap-1">
            <router-link v-for="item in navItems" :key="item.path" :to="item.path"
              class="px-3 py-1 rounded text-xs transition-colors"
              :class="isNavItemActive(item.path) ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'">
              {{ item.label }}
            </router-link>
          </div>
        </div>
        <div class="flex items-center gap-2">
          <!-- 页面通知开关：浏览器系统级通知（新诊断/复盘/信号） -->
          <button @click="toggleNotif" :title="notifTitle"
            class="px-2 py-1 rounded text-sm border border-border bg-bg hover:bg-white/5 transition-colors"
            :class="notifOn ? 'text-accent' : 'text-muted'">
            {{ notifOn ? '🔔' : '🔕' }}
          </button>
          <div class="relative">
            <input v-model="keyword" @keyup.enter="doSearch" @focus="showSearch = true" @blur="hideSearch"
              placeholder="输入代码或名称"
              class="bg-bg border border-border rounded px-3 py-1 text-xs w-40 focus:w-56 transition-all focus:outline-none focus:border-accent/50"/>
            <div v-if="showSearch && searchResults.length" class="absolute top-full mt-1 left-0 right-0 bg-card border border-border rounded shadow-lg z-50">
              <div v-for="r in searchResults" :key="r.code" @mousedown.prevent="goStock(r.code)"
                class="px-3 py-2 text-xs hover:bg-white/5 cursor-pointer flex justify-between">
                <span>{{ r.name }}</span>
                <span class="text-muted font-mono">{{ r.code }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </nav>

    <main class="max-w-[1600px] mx-auto px-4 py-4">
      <router-view/>
    </main>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { searchStock, getFlashNotifications } from './api'

const router = useRouter()
const route = useRoute()

const navItems = [
  { path: '/', label: '首页' },
  { path: '/market', label: '市场行情' },
  { path: '/score', label: '评分排行' },
  { path: '/monitor', label: '快讯监控' },
  { path: '/portfolio', label: '我的持仓' },
  { path: '/watchlist', label: '自选股' },
  { path: '/trade-plans', label: '交易计划' },
  { path: '/capital', label: '资金流向' },
]

// 导航高亮判断：
//   - 首页 '/' 必须严格匹配（否则在 /market 等任何页面首页都会高亮）
//   - 其他 tab 用 startsWith，支持子路由（如 /stock/000001 不命中任何 tab，正确）
function isNavItemActive(path) {
  if (path === '/') return route.path === '/'
  return route.path === path || route.path.startsWith(path + '/')
}

// ──────────────────────────────────────────────────────────────
// 页面通知：浏览器系统级通知（新诊断/复盘/信号入场出场）
// 页面开着就有效（切到别的标签页也能弹，浏览器会节流到 ~1次/分钟，够用）。
// ──────────────────────────────────────────────────────────────
const notifOn = ref(localStorage.getItem('page_notif') === '1')
let notifSince = null        // 首次拉取只建基线，不重播历史事件
let notifTimer = null

const notifTitle = computed(() => {
  if (notifOn.value) return '页面通知已开启（新诊断/复盘/信号会弹系统通知）'
  if (!('Notification' in window)) return '当前浏览器不支持通知'
  return '开启页面通知：新宏观诊断 / 三段复盘 / 信号入场出场'
})

async function toggleNotif() {
  if (!('Notification' in window)) { alert('当前浏览器不支持通知'); return }
  if (notifOn.value) {
    notifOn.value = false
    localStorage.setItem('page_notif', '0')
    return
  }
  // 请求浏览器授权（必须由用户点击触发）
  const perm = Notification.permission === 'granted'
    ? 'granted' : await Notification.requestPermission()
  if (perm === 'granted') {
    notifOn.value = true
    localStorage.setItem('page_notif', '1')
    notifSince = null   // 重置基线：从现在起新事件才通知
    new Notification('✅ 页面通知已开启', { body: '新的宏观诊断、复盘、信号会及时提醒你' })
  }
}

async function pollNotifications() {
  if (!notifOn.value || Notification.permission !== 'granted') return
  try {
    const { data } = await getFlashNotifications(notifSince ? { since: notifSince } : {})
    if (notifSince === null) {
      notifSince = data.now      // 首次只建基线，不重播历史
      return
    }
    for (const ev of data.events) {
      const n = new Notification(ev.title, { body: ev.body, tag: `${ev.type}-${ev.time}` })
      n.onclick = () => { window.focus(); router.push('/monitor') }
    }
    notifSince = data.now
  } catch (e) { /* 后端未起或网络抖动，静默重试 */ }
}

onMounted(() => {
  if (notifOn.value && !('Notification' in window)) notifOn.value = false
  if (notifOn.value) notifTimer = setInterval(pollNotifications, 60 * 1000)
})
onBeforeUnmount(() => { if (notifTimer) clearInterval(notifTimer) })

const keyword = ref('')
const showSearch = ref(false)
const searchResults = ref([])

let searchTimer = null
watch(keyword, (v) => {
  clearTimeout(searchTimer)
  if (!v.trim()) { searchResults.value = []; return }
  searchTimer = setTimeout(async () => {
    try {
      const { data } = await searchStock(v.trim())
      searchResults.value = data || []
    } catch { searchResults.value = [] }
  }, 300)
})

function doSearch() {
  if (keyword.value.trim()) {
    const kw = keyword.value.trim()
    // 如果搜索结果只有一条或精确匹配代码，直接跳转
    if (searchResults.value.length === 1) {
      goStock(searchResults.value[0].code)
    } else if (/^\d{6}$/.test(kw)) {
      goStock(kw)
    }
  }
}

function goStock(code) {
  showSearch.value = false
  router.push(`/stock/${code}`)
}

function hideSearch() {
  setTimeout(() => { showSearch.value = false }, 200)
}
</script>

<style>
@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --bg: #0d1117;
  --card: #161b22;
  --border: #21262d;
  --accent: #58a6ff;
  --muted: #8b949e;
  --rise: #ef4444;
  --fall: #22c55e;
}

body {
  background: var(--bg);
  margin: 0;
}

.bg-bg { background: var(--bg); }
.bg-card { background: var(--card); }
.border-border { border-color: var(--border); }
.text-accent { color: var(--accent); }
.text-muted { color: var(--muted); }
.text-rise { color: var(--rise); }
.text-fall { color: var(--fall); }
.hover\:bg-white\/3:hover { background: rgba(255,255,255,0.03); }
.hover\:bg-white\/5:hover { background: rgba(255,255,255,0.05); }
.focus\:border-accent\/50:focus { border-color: rgba(88,166,255,0.5); }

.fade-in { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }

.loading-spinner {
  width: 32px; height: 32px;
  border: 3px solid var(--border);
  border-top-color: var(--accent);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}
@keyframes spin { to { transform: rotate(360deg); } }
</style>