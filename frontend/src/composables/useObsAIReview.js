// composables/useObsAIReview.js —— 观察器 AI 解读数据层（自 Workbench.vue 抽出，2026-11-14）
// 设计（原注释保留）：默认收起、点开才请求；按日缓存 + 数据指纹；只做性质判断不进决策链。
import { ref, computed } from 'vue'
import { getObserverReview } from '../api'

// ★★ 2026-10-24（用户："观察器什么时候能加上 llm 分析"）：观察器 AI 解读。
//   · **默认收起、点开才请求** —— 它是同步 LLM 调用；按日缓存 + 数据指纹，30 分钟内重复点免费。
//   · 只做"性质判断 + 推翻条件"，不给仓位/买卖建议（观察器不进决策链）。
const obsAIOpen = ref(false)
const obsAI = ref({ loading: false, error: '', markdown: '', conclusion: '', reasons: [],
                    cached: false, as_of: '', date: '' })
const obsAISummary = computed(() => {
  const o = obsAI.value
  if (o.loading) return '分析中…（LLM，约十几秒）'
  if (o.error) return '解读失败（点开查看）'
  if (o.conclusion) return o.conclusion
  return '点开让 AI 解读今天的变化（反弹 / 反转 + 推翻条件）'
})
async function loadObsAI(force = false) {
  if (obsAI.value.loading) return
  obsAI.value = { ...obsAI.value, loading: true, error: '' }
  try {
    const { data } = await getObserverReview(selectedDate.value, !!force)
    if (data && data.ok === false) {
      obsAI.value = { ...obsAI.value, loading: false, error: data.error || '解读失败' }
      return
    }
    obsAI.value = {
      loading: false, error: '',
      markdown: data.markdown || '', conclusion: data.conclusion || '',
      reasons: data.reasons || [], cached: !!data.cached,
      as_of: data.as_of || '', date: data.date || '',
    }
  } catch (e) {
    obsAI.value = { ...obsAI.value, loading: false,
                    error: '请求失败：' + (e?.message || e) }
  }
}
function toggleObsAI() {
  obsAIOpen.value = !obsAIOpen.value
  if (obsAIOpen.value && !obsAI.value.loading
      && !obsAI.value.markdown && !obsAI.value.conclusion) {
    loadObsAI(false)
  }
}


export function useObsAIReview({ selectedDate }) {
  return { obsAIOpen, obsAI, obsAISummary, loadObsAI, toggleObsAI }
}
