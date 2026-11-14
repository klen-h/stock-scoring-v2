// composables/useLidaxiao.js —— 李大霄视角卡数据层（自 Workbench.vue 抽出，2026-11-14）
// 设计（原注释保留）：事件驱动数据（xiaopage 产出新解读时才 POST 落库）⇒ 不轮询，
// 进 tab 取一次（30min 复用）+ 手动「↻ 更新」。静默失败保留上次值。
import { ref, computed, watch, nextTick } from 'vue'
import { getLidaxiaoView } from '../api'
// ★ 2026-10-25：李大霄视角卡（xiaopage LLM 解读推送；**观察器，静默失败保留上次值**）。
//   数据由 xiaopage 在 LLM 输出解读的时刻 POST /market/lidaxiao/push 落库（事件驱动，
//   非"通知后回拉"——该时刻 GitHub raw 尚未 commit，回拉必拿旧数据），本卡只读。
const lidx = ref(null)
// ★★ 2026-11-01（用户："lidaxiao_views 改为不轮询"）：
//   `lidaxiao_views` 是**事件驱动**数据（xiaopage 产出新解读时才由它 POST 落库），
//   按秒/按分轮询毫无收益 —— 探针实测这条 `SELECT * ... LIMIT 100` 全周被调
//   **2,703 次 / ≈124MB**（在 egress 榜上排前六），而内容一天最多变一两次。
//   ⇒ 改为：**进 tab 时取一次**（30 分钟内复用）+ 手动「↻ 更新」按钮兜底。
const LIDX_TTL_MS = 30 * 60 * 1000
const lidxTs = ref(0)

async function loadLidaxiao() {
  try {
    const { data } = await getLidaxiaoView()
    if (data && data.latest) lidx.value = data
    lidxTs.value = Date.now()          // 成功才记时间（失败下次仍会重试）
  } catch { /* 静默保留上次值 */ }
}

/** 进「李大霄」tab 时才取；30 分钟内命中内存值，不重复请求。 */
function ensureLidx() {
  if (lidx.value && Date.now() - lidxTs.value < LIDX_TTL_MS) return
  loadLidaxiao()
}
// ★ 2026-10-28（用户："盘后也会有更新，加个手动更新按钮"）：手动补拉。
//   后端拉 xiaopage 的 videos.json（~6.4MB）→ 同一个 ingest 落库（幂等）。
//   ⚠️ 这是**慢请求**：Render 海外 1~3s，本地国内网络实测 59s ⇒
//     必须 busy 态 + 反馈文案，否则用户会重复点击（虽然有 ETag 短路）。
const lidxBusy = ref(false)
const lidxMsg = ref(null)
async function refreshLidx() {
  if (lidxBusy.value) return
  lidxBusy.value = true
  lidxMsg.value = null
  try {
    const { data } = await refreshLidaxiao()
    if (data && data.view && data.view.latest) lidx.value = data.view
    if (data && data.ok === false) {
      lidxMsg.value = { ok: false, text: '更新失败：' + (data.error || '未知原因') }
    } else if (data && data.unchanged) {
      lidxMsg.value = { ok: true, text: '上游无新解读（已是最新）' }
    } else {
      const d = String((data && data.latest && data.latest.date) || '').slice(5, 16)
      lidxMsg.value = {
        ok: true,
        text: `已更新：新增 ${(data && data.stored) ?? 0} 条` + (d ? ` · 最新 ${d}` : ''),
      }
    }
  } catch (e) {
    lidxMsg.value = { ok: false, text: '更新失败（网络或超时，可稍后重试）' }
  } finally {
    lidxBusy.value = false
    setTimeout(() => { lidxMsg.value = null }, 5000)
  }
}
// ★ 2026-10-27（市场底清单并入观察器）：摘要行 —— 收起时也能看出"3/6 · 距 4 条还差 1 条"。
//   `n_pass` 由后端下发（默认 4），不写死，避免阈值调了文案没跟。
const pbwLine = computed(() => {
  if (!pbw.value) return '加载中…'
  const n = Number(pbw.value.n_checked) || 0
  const total = Number(pbw.value.n_total) || 6
  const pass = Number(pbw.value.n_pass) || 4
  const gap = pass - n
  return `${n}/${total}` + (gap > 0 ? ` · 距 ${pass} 条还差 ${gap} 条` : ' · 已达成')
})

// ★★ 2026-10-26（用户："李大霄卡片加展开收起；一天可能有几个视频，卡内 tab 切换，
//   水平滚动，选中后 tab 自行居中"）：
//   · `items` 由后端下发（最近 12 期精简投影）；缺 items 时回退单条 latest（旧接口兼容）
//   · 选中态按 `entry_id` 记忆 —— 60s 轮询有新视频插到队首时，不会把用户踢回第一条
const lidxOpen = ref(true)
const lidxSel = ref(0)
const lidxSelId = ref('')
const lidxTabEls = []
function setLidxTab(el, i) { if (el) lidxTabEls[i] = el }
const lidxItems = computed(() => {
  const arr = (lidx.value && lidx.value.items) || []
  if (arr.length) return arr
  return (lidx.value && lidx.value.latest) ? [lidx.value.latest] : []
})
const lidxCur = computed(() => lidxItems.value[lidxSel.value] || lidxItems.value[0] || {})
// ★ 2026-11-08：xiaopage v2 的 market 枚举 → 中文（与 xiaopage 企微推送同一套标签）
const LIDX_MARKET_LABEL = {
  a_share: 'A股', us: '美股', hk: '港股',
  other: '其他', mixed: '多市场', unstated: '未指明'
}
function lidxMarketLabel(m) { return LIDX_MARKET_LABEL[m] || m || '—' }
function lidxTabLabel(t) {
  const s = String((t && t.date) || '')
  return s.slice(5, 16) || '—'    // MM-DD HH:MM（同日多条视频靠时刻区分）
}
function selectLidx(i) {
  lidxSel.value = i
  lidxSelId.value = (lidxItems.value[i] || {}).entry_id || ''
  // 选中后把自己滚到可视区中央（横向 tab 条可能很长）
  nextTick(() => {
    const el = lidxTabEls[i]
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
    }
  })
}
watch(lidxItems, (arr) => {
  if (!arr.length) return
  const i = lidxSelId.value ? arr.findIndex(x => x.entry_id === lidxSelId.value) : -1
  lidxSel.value = i >= 0 ? i : 0
})
// 情绪六级配色（口径=后端 sentiment_level，移植自 xiaopage sentiment.ts；多→红系，空→蓝青系）
function lidxLevelColor(level) {
  return { '极度狂热': 'text-red-400', '乐观': 'text-orange-400', '谨慎偏多': 'text-amber-400',
           '谨慎偏空': 'text-cyan-400', '悲观': 'text-blue-400', '极度恐慌': 'text-blue-300' }[level] || 'text-muted'
}
function lidxBarColor(score) {
  const s = Number(score) || 0
  if (s >= 76) return '#ef4444'
  if (s >= 56) return '#f97316'
  if (s >= 46) return '#f59e0b'
  if (s >= 36) return '#06b6d4'
}


export function useLidaxiao() {
  return {
    lidx, lidxTs, lidxBusy, lidxMsg, lidxOpen, lidxSel, lidxSelId, lidxItems, lidxCur,
    lidxTabEls, setLidxTab, loadLidaxiao, ensureLidx, refreshLidx,
    lidxMarketLabel, lidxTabLabel, selectLidx, lidxLevelColor, lidxBarColor,
  }
}
