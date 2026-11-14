// composables/useDefensiveBoard.js —— 防御 Top10/影子榜数据层（自 Workbench.vue 抽出，2026-11-14）
// 设计（原注释保留）：日批快照为底座，实时预览=同一冻结公式重排；影子挂了不拖累生产榜。
// DI：useFrontendMode/feDbReady/scoreCodeList 来自 useFrontendScoring（本地精算依赖注入）。
import { ref } from 'vue'
import { getShadowDefensive, getDefensiveGroup } from '../api'

// ★ 2026-10-25：防御 Top10（H2' 影子榜，日批快照）—— 与 loadTop 解耦：
//   · 失败只置 `defErr`，**不动 topItems**（两张榜各自独立，铁律 11 保留上次值）；
//   · 后端 `ok=false`（尚无 defensive 行）时 `defItems=[]` + note 透传，卡内显示「等日批产出」。
async function loadDefTop() {
  try {
    const { data } = await getShadowDefensive(10)
    if (data && data.ok === false) {
      defErr.value = data.note || data.reason || '暂无防御影子榜数据'
      return
    }
    defTopItems.value = (data && data.items) || []
    defMeta.value = {
      date: (data && data.date) || '',
      gate: !!(data && data.gate),
      eq_state: (data && data.eq_state) || '',
      eq_date: (data && data.eq_date) || '',
      delta: (data && data.delta != null) ? data.delta : null,
    }
    defIsLive.value = false
    defSnap.value = { items: defTopItems.value, meta: defMeta.value }
    defErr.value = ''
    // ★★ 2026-11-01（用户："使用选股工作台的防御组的精算板名次的逻辑"）：
    //   日批榜只是**底座**，这里自动叠一层精算 ——
    //     ① 读宽防御组名单（银行∪高股息∪低波，≈15KB/日）
    //     ② `loadDefLive()` 在浏览器里逐只精算 → 按总分重排 + 带实时涨跌幅
    //   原来要靠用户点「实时」按钮；既然它是默认该看的榜，就不该需要手动点。
    //   精算失败：`loadDefLive` 内部只写 `defErr`，底座（日批榜）照常显示，不白屏。
    if (!defGroup.value.items.length) await loadDefGroup()
    await loadDefLive()
  } catch (e) {
    defErr.value = '读取失败：' + ((e && e.message) || e)   // 保留上次数据
  }
}

// 实时预览：当前精算榜 + 同一冻结公式（银行组 ×(1+δ)）重排。不改官方榜、不进判定。
// 宽防御组名单（银行 ∪ 高股息代理 ∪ 低波）：日批落库，读一次 ≈15KB，失败静默
async function loadDefGroup() {
  try {
    const { data } = await getDefensiveGroup()
    defGroup.value = {
      ok: !!(data && data.ok),
      date: (data && data.date) || '',
      items: (data && data.items) || [],
      counts: (data && data.counts) || {},
      gate: !!(data && data.gate),
      eq_state: (data && data.eq_state) || '',
    }
  } catch (e) {
    console.warn('[workbench] 宽防御组读取失败:', e?.message || e)
  }
}

async function loadDefLive() {
  if (defLiveLoading.value) return
  // ① **本地引擎优先**（用户拍板）：名单内的 137 只逐只在浏览器精算
  //    ⇒ 不调 /batch/top、不读 kline_cache，后端只剩 15KB 名单 +
  //      （首次的）财报批量接口。
  if (useFrontendMode.value && feDbReady.value && (defGroup.value.items || []).length) {
    defLiveLoading.value = true
    defErr.value = ''
    try {
      const codes = defGroup.value.items.map(x => x.code)
      const rows = await scoreCodeList(codes)
      if (!rows || !rows.length) {
        defErr.value = '本地精算无结果（K 线包未覆盖组内代码？可先下载 K 线包）'
        return
      }
      const delta = defMeta.value.delta != null ? Number(defMeta.value.delta) : 0.80
      const gate = !!defGroup.value.gate
      const items = rows
        .map(r => ({
          code: r.code, name: r.name || '',
          raw_score: r.total_score,
          total_score: gate
            ? Math.round(Number(r.total_score || 0) * (1 + delta) * 10) / 10
            : r.total_score,
          change_pct: r.change_pct,
          boosted: gate,
        }))
        .sort((a, b) => (b.total_score || 0) - (a.total_score || 0))
        .slice(0, 10)
        .map((x, i) => ({ ...x, rank_pos: i + 1 }))
      defTopItems.value = items
      defMeta.value = {
        ...defMeta.value,
        date: defGroup.value.date || defMeta.value.date,
        gate,
        eq_state: defGroup.value.eq_state || defMeta.value.eq_state,
        live: true, local: true,
        as_of: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
        group_n: codes.length,
      }
      defIsLive.value = true
    } catch (e) {
      defErr.value = '本地精算失败：' + ((e && e.message) || e)
    } finally {
      defLiveLoading.value = false
    }
    return
  }
  // ② 回退：后端实时重排（本地引擎未就绪时；首次约 1 分钟、10~15MB）
  defLiveLoading.value = true
  defErr.value = ''
  try {
    const { data } = await getShadowDefensive(10, true)
    if (!data || data.ok === false) {
      defErr.value = (data && (data.note || data.reason)) || '实时重排失败'
      return
    }
    defTopItems.value = (data && data.items) || []
    defMeta.value = {
      date: (data && data.date) || '',
      gate: !!(data && data.gate),
      eq_state: (data && data.eq_state) || '',
      eq_date: (data && data.eq_date) || '',
      delta: (data && data.delta != null) ? data.delta : null,
      live: true,
      as_of: (data && data.as_of) || '',
    }
    defIsLive.value = true
  } catch (e) {
    defErr.value = '实时重排失败：' + ((e && e.message) || e)
  } finally {
    defLiveLoading.value = false
  }
}

function toggleDefLive() {
  if (defIsLive.value) {
    // 还原官方日批（内存里就有，零请求）
    defTopItems.value = defSnap.value.items || []
    defMeta.value = defSnap.value.meta || {}
    defIsLive.value = false
    defErr.value = ''
    return
  }
  loadDefLive()
}



export function useDefensiveBoard({ useFrontendMode, feDbReady, scoreCodeList }) {
//   ⚠️ 它是**日频**数据，只在切阶段/进页面加载，不挂 120s 轮询（盘中重拉没有意义）。
const defTopItems = ref([])
const defMeta = ref({})
const defErr = ref('')
// ★★ 2026-10-25（用户："想让它在盘中变动"）：工作台这张卡也支持「实时」预览 ——
//   官方日批（`variant='defensive'` 快照）↔ 实时（当前精算榜代入同一冻结公式重排）。
//   `defSnap` 存官方那份，点「日批」直接还原（零请求）；实时首次约 1 分钟（后端 3 分钟缓存）。
const defSnap = ref({ items: [], meta: {} })
const defIsLive = ref(false)
const defLiveLoading = ref(false)
// ★★ 2026-10-25（用户选"前端本地引擎算组内 100 只"）：宽防御组**名单**（日批小表）。
//   本地实时 = 名单（≈15KB/日）+ 浏览器内精算 ⇒ 后端零精算 egress、也不读 kline_cache。
const defGroup = ref({ ok: false, date: '', items: [], counts: {}, gate: false, eq_state: '' })

  return {
    defTopItems, defMeta, defErr, defSnap, defIsLive, defLiveLoading, defGroup,
    loadDefTop, loadDefGroup, loadDefLive, toggleDefLive,
  }
}
