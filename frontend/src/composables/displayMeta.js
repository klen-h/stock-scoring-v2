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

// ── 主力阶段中文名（**兜底表**，2026-09-28）──
// 唯一映射源仍是后端 `mainforce/phases.PHASE_CN`，显示**优先用后端下发的 `phase_cn`**。
// 但并非所有接口都发它：实测 `/score/batch/top` 的 `mainforce` 只给 `phase`（英文枚举）
//   + `signal`，**没有 `phase_cn`** ⇒ 前端只能裸显英文。
// 与 `STRATEGY_SHORT` 同款处理：本地维护一份**展示用**短名，缺 `phase_cn` 时兜底；
//   未登记则回退英文枚举（**不隐藏** —— 否则后端新增阶段上线后没人发现它没中文名）。
export const PHASE_CN = {
  accumulation: '吸筹', shakeout: '洗盘', markup: '拉升',
  distribution: '出货', decline: '下跌', sideways: '盘整',
}

/** 主力阶段 → 中文名：优先后端下发 `phase_cn`，缺失回退兜底表 / 英文枚举。 */
export function phaseCn(phase, cn) {
  if (cn) return cn
  if (!phase) return ''
  return PHASE_CN[phase] || phase
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

/**
 * 闸门就绪的**标签（chip）配色** —— `readyCls` 的底+字版本（同一三档语义）。
 * ★ 2026-09-28：工作台「评分榜 Top10」补「买入条件」列时加；榜单页观察池表用的是同一套
 *   （3 绿 / 2 琥珀 / ≤1 灰）。
 * ⚠️ 必须写**完整类名**（Tailwind 只扫静态字面量，拼接会被漏扫 ⇒ 样式静默消失）。
 */
export function readyChipCls(ready) {
  if (ready == null) return 'bg-white/5 text-muted'
  if (ready >= 3) return 'bg-emerald-500/20 text-emerald-400'
  if (ready === 2) return 'bg-amber-500/20 text-amber-400'
  return 'bg-white/5 text-muted'
}

// ── 通用展示 helper（2026-09-28 从 `Workbench.vue` 收拢）──
// 【为什么收进来】工作台要把「评分榜 Top10 / 观察池」两卡抽成子组件
//   （`components/workbench/StockListsCards.vue`）并在**盘后 + 盘中**两处复用；
//   若这几支 helper 在父/子各留一份，必然漂移 —— 与本文件当初诞生的理由**同款**
//   （"复制一份，两份必然漂移"）⇒ 一律收到这里，两处 import 同一份。

/** 个股 → 本地详情页（hash 路由）。 */
export const stockHref = (code) => `#/stock/${code}`

/** 个股 → 雪球（自动拼 SH/SZ/BJ 前缀）。与全站口径一致：名称→本地页、代码→雪球。 */
export function xqUrl(code) {
  const c = String(code || '').replace(/\D/g, '').slice(0, 6)
  if (!c) return ''
  const pfx = c.startsWith('6') || c.startsWith('9') ? 'SH'
    : (c.startsWith('4') || c.startsWith('8') ? 'BJ' : 'SZ')
  return `https://xueqiu.com/S/${pfx}${c}`
}

/**
 * **指数** → 雪球（必须显式给市场前缀，如 `('sh', '000001')`）。
 *
 * ★ 2026-09-28（用户："顶部的指数点击能像雪球一样跳转到详情页吗？"）：
 *   **不能复用 `xqUrl`** —— 那个按**首位数字**猜市场（6/9→SH、4/8→BJ、其余 SZ），
 *   那是**个股**口径；而指数与个股**会撞码**：`000001` 既是上证指数（**SH**000001）
 *   又是平安银行（**SZ**000001）⇒ 用 `xqUrl` 会把上证指数指到平安银行。
 *   ⇒ 指数一律用**后端下发的 `prefix`**（唯一源 `routers/market.MAIN_INDICES`）。
 *   ⚠️ `prefix` 缺失时返回 `''`（调用方退回纯文本）—— **宁可不给链接，也不指错标的**。
 */
export function xqIndexUrl(prefix, code) {
  const c = String(code || '').replace(/\D/g, '').slice(0, 6)
  const m = String(prefix || '').trim().toLowerCase()
  if (!c || (m !== 'sh' && m !== 'sz' && m !== 'bj')) return ''
  return `https://xueqiu.com/S/${m.toUpperCase()}${c}`
}

/**
 * 时间戳 → `HH:MM`（只看**时刻**的位置用，如教练卡、回放卡历史、盘后今日清单）。
 *
 * ★★ 2026-09-28 修复（用户："2026- 是不是少了什么？"）：此前各处**直接 `.slice(0, 5)`**，
 *   隐含假设"这已经是个 `HH:MM` 字符串"；而 `coach_alerts.alert_time` 存的是**完整 ISO**
 *   （`coach/audit._now()` 写的是 `2026-09-28T09:35:12`）⇒ 截前 5 位得到 **`2026-`**，
 *   于是卡片显示成「**2026-** 防御市外部预警（不期待反弹）」（标签本身没缺字）。
 *   实测同一字段各处取法还不一样（`.slice(0,5)` ❌ / `.slice(11,16)` ✅ /
 *   `.replace('T',' ').slice(0,16)` ✅）—— 就是"同一字段多套口径"的典型。
 *   ⇒ 统一走本函数：**ISO 与 HH:MM 两种形态都吃**，认不出来返回 `''`（不显示半截日期）。
 */
export function hhmm(ts) {
  const s = String(ts || '')
  if (!s) return ''
  let m = s.match(/[T ](\d{2}:\d{2})/)      // ISO：2026-09-28T09:35:12 / 2026-09-28 09:35:12
  if (m) return m[1]
  m = s.match(/^(\d{2}:\d{2})/)             // 已经是 HH:MM / HH:MM:SS
  return m ? m[1] : ''
}

/** 涨跌幅 → 颜色类（A 股惯例：红涨绿跌）。 */
export const pctClass = (v) =>
  (Number(v) > 0 ? 'text-red-400' : Number(v) < 0 ? 'text-emerald-400' : 'text-muted')

/** 带符号数值（2 位小数；null → '—'）。 */
export const signNum = (v) =>
  (v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}`)

// ── 宏观 / 情绪 等级配色（2026-09-28 从 `Workbench.vue` 收拢）──
// 【为什么收进来】顶栏「情绪」用它、宏观卡/顶栏宏观摘要也用它，而宏观卡要抽成子组件
//   （`components/workbench/MacroEnvCard.vue`）⇒ 再在页内留一份必然漂移（本模块诞生的同一理由）。
// 语义：**多→红（A股红涨）、空→绿蓝**，与数据中心 tab 逐字一致（等级字符串映射）。
export function dirColor(level) {
  return { '强多': 'text-red-400', '偏多': 'text-orange-400', '中性': 'text-amber-400',
           '偏空': 'text-cyan-400', '强空': 'text-blue-400' }[level] || 'text-muted'
}

/** 情绪温度（0~100）等级配色：过热→红、过冷→蓝（与 `dirColor` 同一套色义）。 */
export function levelColor(level) {
  return { '过热': 'text-red-400', '偏热': 'text-orange-400', '中性': 'text-amber-400',
           '偏冷': 'text-cyan-400', '过冷': 'text-blue-400' }[level] || 'text-muted'
}

/** 评分 → 颜色类（≥65 红 / ≥45 琥珀 / 其余灰）。 */
export const scoreClass = (v) =>
  (Number(v) >= 65 ? 'text-red-400' : Number(v) >= 45 ? 'text-amber-300' : 'text-muted')
