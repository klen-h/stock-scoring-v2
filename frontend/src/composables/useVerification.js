// ============================================================
//  数据可信度（全局单例）
// ============================================================
//  职责：拉取后端「验证结论总表」，供各页面标注：
//    verified 已验证 / partial 部分成立 / unverified 未验证 / falsified 已证伪
//
//  ★ 为什么需要（2026-09-27）：界面上「该不该买」（闸门，19.5 年核算过）与
//    「买什么」（评分未验证、战法已证伪）**看起来一样权威**，用户无从分辨。
//    项目本身以"敢说自己的信号无效"为原则，那这条原则就该出现在界面上，
//    而不是只写在报告里。
//
//  用法：
//    const { load, statusOf } = useVerification()
//    onMounted(load)                      // 拉一次（进程内缓存，重复调用不重复请求）
//    statusOf('score')                    // → { key, status, status_cn, note, evidence, ... }
//    STATUS_STYLE[it.status].cls          // 样式类
// ============================================================
import { ref } from 'vue'
import { getVerification } from '../api'

const cache = ref(null)     // 总表（响应式：拉取后所有引用的页面自动更新）
let inflight = null         // 并发去重

export function useVerification() {
  /** 拉取总表（幂等；force=true 强制刷新）。失败静默返回 null。 */
  async function load(force = false) {
    if (cache.value && !force) return cache.value
    if (inflight) return inflight
    inflight = getVerification()
      .then(({ data }) => {
        if (data && Array.isArray(data.items)) cache.value = data
        return cache.value
      })
      .catch(() => null)
      .finally(() => { inflight = null })
    return inflight
  }

  /** 查某条结论的验证状态（未加载返回 null ⇒ 调用方不渲染标签）。 */
  function statusOf(key) {
    const items = cache.value?.items || []
    return items.find(x => x.key === key) || null
  }

  return { cache, load, statusOf }
}

// ── 状态 → 展示样式（纯数据，供各页面直接使用）──
export const STATUS_STYLE = {
  verified:   { text: '已验证',   cls: 'text-emerald-400 border-emerald-500/40 bg-emerald-500/10' },
  partial:    { text: '部分成立', cls: 'text-sky-400 border-sky-500/40 bg-sky-500/10' },
  unverified: { text: '未验证',   cls: 'text-amber-400 border-amber-500/40 bg-amber-500/10' },
  falsified:  { text: '已证伪',   cls: 'text-red-400 border-red-500/40 bg-red-500/10' },
}

/** hover 提示文案：状态 + 摘要 + 证据脚本。 */
export function verifyTip(it) {
  if (!it) return ''
  const parts = [`【${it.status_cn || it.status}】${it.label || ''}`]
  if (it.summary) parts.push(it.summary)
  if (it.note) parts.push(it.note.replace(/\*\*/g, ''))
  if (it.evidence) parts.push(`证据：${it.evidence}`)
  return parts.join('\n')
}
