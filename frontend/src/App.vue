<template>
  <div class="min-h-screen bg-bg text-gray-200">
    <!-- 登录页不显示导航 -->
    <template v-if="!isLoginPage">
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
            <!-- 用户信息 + 登出 -->
            <div class="relative" ref="userMenuRef">
              <button @click="showUserMenu = !showUserMenu"
                class="px-2 py-1 rounded text-xs border border-border bg-bg hover:bg-white/5 transition-colors text-muted flex items-center gap-1">
                <span>{{ currentUser?.username || '用户' }}</span>
                <span class="text-[10px]">▼</span>
              </button>
              <div v-if="showUserMenu" class="absolute top-full mt-1 right-0 bg-card border border-border rounded shadow-lg z-50 min-w-[120px]">
                <button @click="handleLogout" class="w-full px-3 py-2 text-xs text-left hover:bg-white/5 text-red-400">
                  退出登录
                </button>
              </div>
            </div>
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
    </template>

    <main :class="isLoginPage ? '' : 'max-w-[1600px] mx-auto px-4 py-4'">
      <router-view/>
    </main>
  </div>
</template>

<script setup>
import { ref, watch, onMounted, onBeforeUnmount, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { searchStock, getFlashNotifications, getFlashBackup, restoreFlashBackup, getUser, removeToken } from './api'

const router = useRouter()
const route = useRoute()

// ── 用户状态 ──
const currentUser = computed(() => getUser())
const showUserMenu = ref(false)
const userMenuRef = ref(null)
const isLoginPage = computed(() => route.name === 'Login')

function handleLogout() {
  removeToken()
  showUserMenu.value = false
  router.push('/login')
}

// 点击外部关闭用户菜单
function handleClickOutside(e) {
  if (userMenuRef.value && !userMenuRef.value.contains(e.target)) {
    showUserMenu.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

const navItems = [
  { path: '/', label: '首页' },
  { path: '/market', label: '市场行情' },
  { path: '/score', label: '评分排行' },
  { path: '/strategies', label: '战法选股' },
  { path: '/monitor', label: '快讯监控' },
  { path: '/backtest', label: '回测中心' },
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
  // 数据镜像：页面开着就是一台"备份机"，每 5 分钟同步一次
  setTimeout(syncDataMirror, 5 * 1000)
  mirrorTimer = setInterval(syncDataMirror, 5 * 60 * 1000)
})
onBeforeUnmount(() => {
  if (notifTimer) clearInterval(notifTimer)
  if (mirrorTimer) clearInterval(mirrorTimer)
  document.removeEventListener('click', handleClickOutside)
})

// ──────────────────────────────────────────────────────────────
// 浏览器数据镜像：把 backend/data 备份到 localStorage，
// 服务端（Render 免费版）部署清零后自动恢复。
// 判定：镜像条目数 > 服务端条目数 且镜像 7 天内 → 恢复。
// ──────────────────────────────────────────────────────────────
const MIRROR_KEY = 'flash_data_mirror'
let mirrorTimer = null

async function syncDataMirror() {
  try {
    const { data: server } = await getFlashBackup()
    const stored = JSON.parse(localStorage.getItem(MIRROR_KEY) || 'null')
    const saveMirror = (bundle) => {
      try {
        localStorage.setItem(MIRROR_KEY, JSON.stringify(bundle))
        localStorage.setItem('flash_mirror_time',
          new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }))
      } catch (e) { console.warn('镜像写入失败（可能超出 localStorage 容量）', e) }
    }

    if (!stored || server.total_entries >= stored.total_entries) {
      saveMirror(server)      // 服务端正常/更新 → 刷新本地镜像
      return
    }
    // 服务端条目比镜像少 → 疑似部署清零 → 用镜像恢复
    const ageHours = (Date.now() - new Date(stored.time || 0).getTime()) / 36e5
    if (ageHours > 24 * 7) { saveMirror(server); return }   // 镜像太旧，不复活陈旧数据

    const headers = {}
    const sec = localStorage.getItem('backup_secret')
    if (sec) headers['X-Backup-Secret'] = sec
    let res
    try {
      res = await restoreFlashBackup(stored.files, headers)
    } catch (err) {
      if (err.response?.status === 401) {
        const s = prompt('数据恢复需要密钥（服务端已配置 BACKUP_SECRET）')
        if (!s) throw err
        localStorage.setItem('backup_secret', s)
        res = await restoreFlashBackup(stored.files, { 'X-Backup-Secret': s })
      } else throw err
    }
    if ('Notification' in window && Notification.permission === 'granted' && res.data?.restored?.length) {
      new Notification('♻️ 数据已从浏览器镜像恢复',
        { body: `恢复 ${res.data.restored.length} 个数据文件（信号/诊断/历史）` })
    }
    console.log('[镜像] 已恢复:', res.data?.restored)
    const { data: fresh } = await getFlashBackup()   // 恢复后重取作为新镜像基线
    saveMirror(fresh)
  } catch (e) {
    console.error('数据镜像同步失败', e?.response?.status || e.message)
  }
}

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