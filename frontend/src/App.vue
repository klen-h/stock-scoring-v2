<template>
  <div class="min-h-screen bg-bg text-gray-200">
    <!-- 登录页不显示导航 -->
    <template v-if="!isLoginPage">
      <!-- 顶部导航 -->
      <nav class="bg-card border-b border-border sticky top-0 z-50">
        <div class="max-w-[1600px] mx-auto px-4 h-12 flex items-center justify-between">
          <div class="flex items-center gap-6">
            <router-link to="/" class="font-bold text-accent text-sm tracking-wide">A股评分系统</router-link>
            <div class="hidden md:flex gap-1 items-center" ref="navRef">
              <template v-for="group in navGroups" :key="group.label">
                <!-- 单页组（首页） -->
                <router-link v-if="group.path" :to="group.path"
                  class="px-3 py-1 rounded text-xs transition-colors"
                  :class="isNavItemActive(group.path) ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'">
                  {{ group.label }}
                </router-link>
                <!-- 下拉聚合组 -->
                <div v-else class="relative nav-group"
                  @mouseenter="openMenu = group.label"
                  @mouseleave="openMenu = null">
                  <button @click="openMenu = group.label"
                    class="px-3 py-1 rounded text-xs flex items-center gap-1 transition-colors"
                    :class="isGroupActive(group) ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'">
                    {{ group.label }}<span class="text-[9px] leading-none">▼</span>
                  </button>
                  <div v-if="openMenu === group.label"
                    class="absolute top-full left-0 pt-1 z-50 min-w-[104px]">
                    <div class="bg-card border border-border rounded shadow-lg py-1">
                      <router-link v-for="item in group.items" :key="item.path" :to="item.path"
                        @click="openMenu = null"
                        class="block px-3 py-2 text-xs transition-colors"
                        :class="isNavItemActive(item.path) ? 'text-accent bg-accent/10' : 'text-muted hover:text-gray-200 hover:bg-white/5'">
                        {{ item.label }}
                      </router-link>
                    </div>
                  </div>
                </div>
              </template>
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
import { searchStock, getFlashNotifications, getUser, removeToken } from './api'

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

// 点击外部关闭用户菜单 / 导航下拉
function handleClickOutside(e) {
  if (userMenuRef.value && !userMenuRef.value.contains(e.target)) {
    showUserMenu.value = false
  }
  if (navRef.value && !navRef.value.contains(e.target)) {
    openMenu.value = null
  }
}

onMounted(() => {
  document.addEventListener('click', handleClickOutside)
})

// ── 导航分组（17 个页面按工作流聚成 4 组，顶栏 5 项）──
//   市场=盘面+消息（"现在市场怎么样"）；选股=找股；交易=操作；
//   复盘=盘后总结与验证。要调整分组只改这个数组。
const navGroups = [
  { path: '/', label: '首页' },
  { label: '市场', items: [
    { path: '/market', label: '市场行情' },
    { path: '/sector', label: '板块分化' },
    { path: '/mainline', label: '行业主线' },
    { path: '/capital', label: '资金流向' },
    { path: '/monitor', label: '快讯监控' },
    { path: '/calendar', label: '财经日历' },
  ]},
  { label: '选股', items: [
    { path: '/score', label: '评分排行' },
    { path: '/strategies', label: '战法选股' },
    { path: '/score/stats', label: '评分验证' },
  ]},
  { label: '交易', items: [
    { path: '/watchlist', label: '自选股' },
    { path: '/trade-plans', label: '交易计划' },
    { path: '/portfolio', label: '我的持仓' },
    { path: '/paper', label: '模拟盘' },
  ]},
  { label: '复盘', items: [
    { path: '/report', label: '每日日报' },
    { path: '/contradictions', label: '矛盾扫描' },
    { path: '/backtest', label: '回测中心' },
    { path: '/performance', label: '系统绩效' },
  ]},
]
const openMenu = ref(null)
const navRef = ref(null)

function isGroupActive(group) {
  return (group.items || []).some(i => isNavItemActive(i.path))
}

// 导航高亮判断：
//   - 首页 '/' 与评分排行 '/score' 必须严格匹配（避免子路由 /score/stats 同时高亮两个 tab）
//   - 其他 tab 用 startsWith，支持子路由（如 /stock/000001 不命中任何 tab，正确）
function isNavItemActive(path) {
  if (path === '/' || path === '/score') return route.path === path
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
onBeforeUnmount(() => {
  if (notifTimer) clearInterval(notifTimer)
  document.removeEventListener('click', handleClickOutside)
})

// ──────────────────────────────────────────────────────────────
// 【已退役 2026-09-12】浏览器数据镜像（每 5 分钟把 backend/data 备份到 localStorage，
// 部署清零后自动回传）：
//   · 它保护的 9 个文件（flash/analyses/reviews/tracking/macro_history/etf_close/
//     flash_state/schedule_state/strategies）早已全部迁进数据库，文件只剩空壳
//     → 镜像实际不保护任何东西；
//   · 代价却是每 5 分钟 × 每个打开的标签页调用 /api/flash/backup，而该接口为了
//     取条目数会把 50 条诊断正文读出来（实测 40MB/天 Supabase egress）。
//   现改为后端「文件型数据完整性检查」：启动即核对仍在文件里的数据
//   （财经日历 / LLM 用量 / K线缓存 / 数据包），缺失即企微告警，
//   接口 `/api/system/runtime-files` 可查（见 backend/app/data_files.py）。
// ──────────────────────────────────────────────────────────────

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