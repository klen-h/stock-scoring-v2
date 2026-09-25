// ==============================================================================
// 展示元数据的**唯一共享源**（2026-09-25）
//
// 【为什么抽出来】用户要求"工作台观察池的配色与命名，跟榜单页观察池一致" —— 而
//   `PHASE_STYLE`（主力阶段配色）、`STRATEGY_SHORT`（战法中文名）、`readyCls`（闸门就绪配色）
//   原先都**定义在 `ScoreRank.vue` 内部** ⇒ 工作台想用只能复制一份，两份必然漂移。
//   ⇒ 抽到这里，两个页面 import 同一份（改一处、两处生效）。
//
// ⚠️ Tailwind 只扫源码里的**静态字面量**（`tailwind.config.js` 的 content 已含 `src/**/*.js`）
//   ⇒ 下面的 `cls` 必须写**完整类名**，不可拼接（`bg-${x}-500` 会被漏扫 ⇒ 样式静默消失）。
// ==============================================================================

// ── 主力阶段配色 ──
// 键 = 后端英文枚举（唯一映射源 `mainforce/phases.PHASE_CN` 的 key）；显示用后端 `phase_cn`。
// 语义：**绿=机会 / 红=风险**（不是 A 股的红涨绿跌）——
//   吸筹=机会｜出货=风险｜**拉升=追高警惕**（`trade_gate` 与战法过滤都会拦 markup，故用琥珀）
//   ｜洗盘=中性持有｜下跌=回避｜盘整=无方向。
export const PHASE_STYLE = {
  accumulation: {
    cls: 'bg-emerald-500/20 text-emerald-400',
    tip: '吸筹段：主力低位建仓、时间换空间（闸门 A「主力有根据」的来源）',
  },
  shakeout: {
    cls: 'bg-cyan-500/20 text-cyan-300',
    tip: '洗盘段：缩量回调不破位＝主力没走，持有/观察',
  },
  markup: {
    cls: 'bg-amber-500/20 text-amber-400',
    tip: '拉升段：放量上攻，**追高风险**（闸门与战法过滤都会拦 markup）',
  },
  distribution: {
    cls: 'bg-red-500/20 text-red-400',
    tip: '出货段：高位放量滞涨，筹码换手给散户（回测 10 日 -7.5pt）',
  },
  decline: {
    cls: 'bg-zinc-500/20 text-zinc-400',
    tip: '下跌段：无主力接管，回避',
  },
  sideways: {
    cls: 'bg-white/5 text-muted',
    tip: '盘整段：无明显方向',
  },
}

// ── 战法中文名（榜单/工作台卡片展示用，取更短的"展示名"）──
// 与后端 `strategies/recommendation.STRATEGY_ZH`（推送用全称）**键一一对应**；
// 这里短一些，便于表格/卡片一行放得下（如"均线粘合突破"→"收敛突破"）。
// ★ 2026-09-25 顺手统一一处不一致：原榜单页把 `wizard_pointer` 写成"神奇指针"，
//   而后端与行业惯用为"**仙人指路**" ⇒ 以**后端为准**改名（同一战法不该有两个名字）。
export const STRATEGY_SHORT = {
  ma_convergence_breakout: '收敛突破',
  single_yang_unbroken: '单阳不破',
  dragon_turnaround: '龙回头',
  ma_pullback: '回踩',
  limit_up_boomerang: '涨停回马枪',
  wizard_pointer: '仙人指路',
  old_duck_head: '老鸭头',
  advance2retreat1: '进二退一',
  morning_star: '早晨之星',
  double_cannon: '双响炮',
}

/** 战法 → 中文名。入参兼容字符串名与 `{name|strategy_name}` 对象（两种后端形态都出现过）。 */
export function strategyShort(n) {
  if (!n) return ''
  const k = typeof n === 'string' ? n : (n.name || n.strategy_name || '')
  // 未登记的战法**回退英文名**（不隐藏 —— 否则新增战法上线后没人发现它没中文名）
  return STRATEGY_SHORT[k] || k
}

// ── 闸门就绪配色 ──
// 3/3 绿（三条件齐）｜2/3 琥珀（等状态）｜≤1 灰（还差条件）—— 与观察池列同语义。
export function readyCls(ready) {
  if (ready == null) return 'text-muted'
  if (ready >= 3) return 'text-emerald-400 font-bold'
  if (ready === 2) return 'text-amber-400'
  return 'text-muted'
}
