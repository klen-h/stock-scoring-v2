// composables/useWorkbenchBrief.js —— 三时段简报加载层（自 Workbench.vue 抽出，2026-11-14）
// briefMd 是多阶段共用的单个 ref ⇒ briefPhase 记「当前内容属于哪个时段」（2026-09-30 事故防线）。
// DI：selectedDate（日期回放联动）。缓存走共享基建 utils/dayCache.js。
import { ref } from 'vue'
import { getTraderBrief } from '../api'
import { _dayCache, _dayCached, _dayMark } from '../utils/dayCache'

const briefMd = ref('')
const briefErr = ref('')
const briefDegraded = ref(false)
// ★★ 2026-09-30（用户："盘前午盘盘后的 llm 分析失败，可以加一个手动重试"）：
//   `briefMd` 是**多阶段共用**的单个 ref ⇒ 必须记「当前内容属于哪个 phase」，
//   否则会把盘前简报渲染进「决策简报（盘后）」卡（盘后段原先**根本不加载**简报，
//   只有切到「复盘」段才拉 ⇒ 该卡要么空着、要么显示盘前内容，属既有缺陷）。
const briefPhase = ref('')
const briefLoading = ref(false)


async function loadBrief(phase, force = false) {
  const day = selectedDate.value
  const ck = `brief:${phase}`
  if (!force && _dayCached(ck, day)) return
  // ★★ 2026-09-30（用户："llm 分析失败，可以加一个手动重试"）：`force=true` 时
  //   ① 先清掉"当日已取到"标记 —— 否则重试后切回本段仍被缓存挡住（用户以为没生效）；
  //   ② 传 `refresh=true` 让后端**重新生成**（消耗 1 次 LLM，api 层已给 180s 超时）。
  if (force) delete _dayCache[ck]
  briefErr.value = ''
  briefLoading.value = true
  briefPhase.value = phase
  try {
    const { data } = await getTraderBrief(!!force, phase)
    briefMd.value = data?.markdown || ''
    briefDegraded.value = !!data?.degraded
    // ★ 纪律③：降级版不缓存（后端窗口内还会重试出 LLM 版）
    if (briefMd.value && !briefDegraded.value) _dayMark(ck, day)
  } catch (e) {
    briefMd.value = ''
    briefErr.value = (e?.response?.data?.detail || e?.message) || '未知错误'
  } finally {
    briefLoading.value = false
  }
}


export function useWorkbenchBrief({ selectedDate }) {
}
