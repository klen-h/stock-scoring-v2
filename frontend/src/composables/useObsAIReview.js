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
// ⚠️ 注意：`loadObsAI` / `toggleObsAI` **必须定义在工厂函数体内**（见下方）。
//   它们依赖 `selectedDate`，而那是 `useObsAIReview({ selectedDate })` 的参数 ——
//   只在函数作用域里存在。放在模块层 ⇒ 运行时 `ReferenceError: selectedDate is not defined`
//   ⇒ 被 `catch` 吞成"请求失败"，用户只看到"AI 解读失败"，看不出是代码错。
//
//   ★ 2026-11-16 实际踩到（用户截图："重新解读 → 请求失败：selectedDate is not defined"）。
//     这与 INDEX.md 记录的"结构错家族"是**同一类**：
//     拆 composable 时把**依赖 DI 参数的函数**留在了模块层。
//     ⇒ 纪律：**凡是读 DI 参数的函数，一律写在 export function 体内**；
//       只有"纯自持状态"（如 obsAIOpen / obsAI 这些单例 ref）才可以留模块层。


export function useObsAIReview({ selectedDate }) {
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

  return { obsAIOpen, obsAI, obsAISummary, loadObsAI, toggleObsAI }
}
