<template>
  <!-- ★ 2026-09-28：改为**竖排**（原为并排两列）—— 用户要求挂进**常驻右栏**（320px 窄栏）。
       两张卡：① 评分榜 Top10（含「主力行为」列）② 观察池（**默认收起**，点击展开）。 -->
  <div class="space-y-4">
    <!-- ① 评分榜 Top10 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <!-- ★★ 2026-10-25（用户："评分榜区域新增「防御Top10」标签页，与现有 Top10 自由切换"；
           "查看完整版改文案为详情"；"移除预登记 FAIL 那段文字"）：
           ① 标题行改为**两个标签页**（评分Top10 / 防御Top10），与榜单页 tab 同款观感
              （选中 = `bg-accent/20 text-accent`），点击即切、共享下方的展开/收起；
           ② 右侧链接文案 `查看完整榜` → `详情`（右栏仅 320px，省空间）；
           ③ FAIL 披露整段移除（用户明确要求不在界面展示；口径仍在后端/方案文档留痕）。 -->
      <div class="flex items-center justify-between mb-2 gap-2">
        <div class="flex items-center gap-1.5 min-w-0">
          <button type="button" class="text-[10px] text-muted hover:text-accent shrink-0"
                  @click="toggleTop" :title="topOpen ? '点击收起' : '点击展开'">
            {{ topOpen ? '▼' : '▶' }}
          </button>
          <button v-for="t in cardTabs" :key="t.key" type="button"
                  class="px-2 py-0.5 rounded text-xs transition-colors shrink-0"
                  :class="cardTab === t.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
                  :title="t.tip"
                  @click="cardTab = t.key">{{ t.label }}</button>
        </div>
        <router-link target="_blank" :to="detailTo" class="text-xs text-accent hover:underline shrink-0">详情</router-link>
      </div>
      <!-- 口径如实标注：本地实时（前端引擎，盘中可实时）vs 后端批次；防御榜为日批快照 -->
      <div class="mb-1.5 text-[10px] text-muted leading-relaxed">
        <template v-if="cardTab === 'score'">
          <span v-if="topMode" :class="topMode === 'local' ? 'text-emerald-400' : ''"
                :title="topMode === 'local'
                  ? '本地计算：前端 K 线包 + 实时行情、本地精算 ⇒ 盘中为实时（与榜单页同源）。K 线包 2026-10-11 起扩容纳入科创板；包未覆盖的票自动缺席'
                  : '后端批次：/score/batch/top（盘中通常是上一交易日快照）。全市场口径（含创业板/科创板）'">
            {{ topMode === 'local' ? '本地实时' : '后端批次' }}<template
              v-if="topCountdown > 0"> · {{ topCountdown }}s 后刷新</template></span>
        </template>
        <template v-else>
          <span :class="defMeta.gate ? 'text-sky-300' : ''"
                :title="defMeta.gate
                  ? '阴跌段门控 ON：银行组总分 ×' + (1 + (defMeta.delta ?? 0)).toFixed(2) + ' 后重排'
                  : '门控 OFF：当前非阴跌段，本榜 = 生产口径'">
            H2' 防御影子{{ defMeta.date ? ' · ' + defMeta.date : '' }}</span>
          <span v-if="defMeta.eq_state" class="ml-1 font-mono">（{{ defMeta.eq_state }}）</span>
        </template>
      </div>
      <!-- ① 评分 Top10（原卡，未改动行内口径） -->
      <template v-if="cardTab === 'score'">
      <div v-if="topErr" class="text-muted text-xs">—（加载失败）</div>
      <div v-else-if="!topItems.length" class="text-muted text-xs">—（暂无数据）</div>
      <div v-else-if="!topOpen" class="text-[11px] text-muted truncate">
        Top1 {{ topItems[0].name || topItems[0].code }}
        <b class="font-mono text-gray-200">{{ topItems[0].total_score }}</b>
        · 共 {{ topItems.length }} 只
      </div>
      <div v-else class="space-y-1 text-xs">
        <div v-for="(r, i) in topItems" :key="r.code"
             class="flex items-center gap-1.5 border-b border-border/40 py-1.5">
          <span class="w-5 shrink-0 text-muted font-mono">{{ i + 1 }}</span>
          <a :href="stockHref(r.code)" target="_blank"
             class="font-semibold hover:text-accent truncate">{{ r.name || r.code }}</a>
          <a :href="xqUrl(r.code)" target="_blank"
             class="shrink-0 text-muted font-mono hover:text-accent" title="雪球">{{ r.code }}</a>
          <!-- ★ 2026-10-11：板块标注（科创/创业；主板不标注保持稀缺性）——
               K 线包扩容后两板块进入各榜单，需一眼认出归属 -->
          <span v-if="boardChip(r.code)"
                class="shrink-0 px-1 rounded text-[10px]" :class="boardChip(r.code).cls">{{ boardChip(r.code).label }}</span>
          <span v-if="r.change_pct != null" class="shrink-0 font-mono" :class="pctClass(r.change_pct)">
            {{ signNum(r.change_pct) }}%</span>
          <!-- ★ 2026-09-28 改版（用户："主力行为怎么只有一个吸筹标签，而且位置有点靠右了，
               左边还有空间的"）：
               ① **口径 signal → phase（主力阶段）**。原用 `signal`（吸筹/出货），而它是
                  **稀有信号** —— 实测全市场 2083 只里仅 157 只有值（accum 139 /
                  distribution 18，其余 1926 只 null）⇒ 10 行里常只有 1 个标签。
                  改用 `phase`（**全覆盖六档**：盘整 931 / 下跌 721 / 吸筹 296 / 拉升 119 /
                  洗盘 8 / 出货 8）⇒ 每行都有信息，且与全站（观察池/持仓/详情页）同口径。
               ② **位置左移**：原 `ml-auto` 把标签顶到最右、左侧留空 ⇒ 改为紧跟涨跌幅；
                  `ml-auto` 移到评分上，评分仍保持右对齐。 -->
          <span v-if="mfPhase(r)" class="shrink-0 px-1 py-0.5 rounded text-[10px] font-bold cursor-help"
                :class="(MF_PHASE_STYLE[mfPhase(r)] || {}).cls || 'bg-white/5 text-muted'"
                :title="(MF_PHASE_STYLE[mfPhase(r)] || {}).tip || ''">{{ phaseCn(mfPhase(r), mfPhaseCn(r)) }}</span>
          <span v-else class="shrink-0 text-[10px] text-muted">-</span>
          <!-- ★ 2026-09-28（用户："主力行为右边不是还有一个买入条件的吗？补上"）：
               与榜单页观察池表**同口径**的「买入条件」标签 `ready N/3`
               （3 绿 / 2 琥珀 / ≤1 灰），hover 给 `label + hint`
               （如"等状态：主力有根据、不追高，但市况不允许"）。
               数据：本地模式 `r.gate`（`scoringEngine.buildGate`，阈值由后端 /score/weights 下发）；
                     后端模式 `/score/batch/top` 亦下发 `item.gate`（`trade_gate.summarize`
                     —— 即观察池用的**同一个** summarize）⇒ 两端口径一致，非自行推导。 -->
          <span v-if="r.gate" class="shrink-0 px-1 rounded font-mono text-[10px] font-bold cursor-help"
                :class="readyChipCls(r.gate.ready)"
                :title="`买入条件 ${r.gate.ready}/3 · ${r.gate.label || ''}${r.gate.hint ? '：' + r.gate.hint : ''}`">
            {{ r.gate.ready }}/3</span>
          <span v-else class="shrink-0 text-[10px] text-muted">-</span>
          <span class="ml-auto shrink-0 font-mono font-bold" :class="scoreClass(r.total_score)">
            {{ r.total_score }}</span>
        </div>
      </div>
      </template>

      <!-- ② 防御 Top10（H2' 阴跌防御影子榜；日批快照，不改生产排序）
           ★ 只显示"排名 / 名称 / 防御加成标记 / 分数" —— 该榜没有 mainforce/gate 字段
             （影子榜只存分数与名次），硬塞会变成一排 "-"，反而不如不给。 -->
      <template v-else>
        <div v-if="defErr" class="text-muted text-xs">—（{{ defErr }}）</div>
        <div v-else-if="!defItems.length" class="text-muted text-xs">—（暂无数据，等日批产出）</div>
        <div v-else-if="!topOpen" class="text-[11px] text-muted truncate">
          Top1 {{ defItems[0].name || defItems[0].code }}
          <b class="font-mono text-gray-200">{{ defItems[0].total_score }}</b>
          · 共 {{ defItems.length }} 只
        </div>
        <div v-else class="space-y-1 text-xs">
          <div v-for="(r, i) in defItems" :key="r.code"
               class="flex items-center gap-1.5 border-b border-border/40 py-1.5">
            <span class="w-5 shrink-0 text-muted font-mono">{{ i + 1 }}</span>
            <a :href="stockHref(r.code)" target="_blank"
               class="font-semibold hover:text-accent truncate">{{ r.name || r.code }}</a>
            <a :href="xqUrl(r.code)" target="_blank"
               class="shrink-0 text-muted font-mono hover:text-accent" title="雪球">{{ r.code }}</a>
            <span v-if="r.boosted"
                  class="shrink-0 px-1 py-0.5 rounded text-[10px] bg-sky-500/20 text-sky-300 cursor-help"
                  :title="`阴跌段防御加成：银行组 ×${(1 + (defMeta.delta ?? 0)).toFixed(2)}`">防御</span>
            <span class="ml-auto shrink-0 font-mono font-bold" :class="scoreClass(r.total_score)">
              {{ r.total_score }}</span>
          </div>
        </div>
      </template>
    </div>

    <!-- ② 观察池（买入闸门候选）—— **默认收起**，点击标题展开 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
        <!-- ★ 2026-09-28（用户："观察池的标题就观察池三个字就行了，放不下了"）：
             标题瘦身为**三个字**（右栏仅 320px，原"观察池（买入闸门候选）"+ 括号 meta 会折行）；
             「候选 N · 三绿 M · 数据时点」**移到展开区首行**（信息不丢，且收起时不占位）。 -->
        <button type="button" class="text-sm font-semibold flex items-center gap-1 hover:text-accent"
                @click="watchOpen = !watchOpen"
                :title="watchOpen ? '点击收起' : '点击展开'">
          <span class="text-[10px] text-muted">{{ watchOpen ? '▼' : '▶' }}</span>
          观察池
        </button>
        <!-- ★ 2026-09-28（用户："观察池详情跳转后要选中观察池的 tab"）：
             `?tab=watch` 直达 —— 榜单页 `onMounted` 读该参数直接定 tab（见 ScoreRank.vue）。 -->
        <router-link target="_blank" :to="{ path: '/score', query: { tab: 'watch' } }"
                     class="text-xs text-accent hover:underline">详情</router-link>
      </div>
      <template v-if="watchOpen">
        <div v-if="gwErr" class="text-muted text-xs">—（加载失败：{{ gwErr }}）</div>
        <template v-else-if="gwItems.length">
          <div class="text-[11px] text-muted mb-1.5">
            <!-- ★ 原在**标题**里的 meta（候选/三绿/数据时点）挪到首行，并合并原有的「分布」。 -->
            候选 <b class="text-gray-200 font-mono">{{ gwMeta.total || gwItems.length }}</b> 只 ·
            三绿 <b class="text-emerald-400 font-mono">{{ gwMeta.ready3 || 0 }}</b> 只<template
              v-if="gwMeta.data_date"> · 数据 {{ gwMeta.data_date }}</template>
            <span v-if="gwMeta.counts" class="ml-1">
              （分布 1绿 {{ gwMeta.counts[1] || 0 }} / 2绿 {{ gwMeta.counts[2] || 0 }} / 3绿 {{ gwMeta.counts[3] || 0 }}）</span>
            <span v-if="gwMeta.strategy_hits" class="ml-1">· 战法信号 {{ gwMeta.strategy_hits }}</span>
            <span v-if="gwMeta.regime" class="ml-1">· 市况 {{ gwMeta.regime }}</span>
          </div>
          <div class="space-y-1 text-xs">
            <div v-for="g in gwItems.slice(0, 8)" :key="g.code"
                 class="flex items-center gap-1.5 flex-wrap border-b border-border/40 py-1"
                 :title="gwTip(g)">
              <a :href="stockHref(g.code)" target="_blank"
                 class="font-semibold hover:text-accent truncate max-w-[80px]">{{ g.name || g.code }}</a>
              <a :href="xqUrl(g.code)" target="_blank" rel="noopener" title="雪球"
                 class="font-mono text-muted hover:text-accent shrink-0">{{ g.code }}</a>
              <!-- ★★ 2026-09-30（用户："工作台的观察池需要展示个股的涨跌幅，**等状态文字可去掉**"）：
                   ① **加涨跌幅**（红涨绿跌，与评分榜行/榜单页观察池同口径）；
                      数据 = 父级 `gwPrices`（`getBatchPrices` 批量拉，盘中随轮询刷新）；
                      未拉到 ⇒ 「—」（**缺失 ≠ 0**，不假装 0.00%）。
                   ② **去掉常显的状态文字**（原 `g.label`，如"等状态"）—— 右栏仅 320px，
                      它挤掉了更有用的一列；**信息不丢**：label + hint + 还差什么
                      全部并入**行 hover 提示** `gwTip()`（比原来那一行更全）。 -->
              <span v-if="gwPct(g) != null" class="shrink-0 font-mono" :class="pctClass(gwPct(g))">
                {{ signNum(gwPct(g)) }}%</span>
              <span v-else class="shrink-0 font-mono text-muted">—</span>
              <span class="px-1 rounded font-mono text-[10px] shrink-0"
                    :class="g.ready === 3 ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-300'">
                {{ g.ready }}/3</span>
              <span v-if="g.phase" class="px-1 rounded shrink-0 cursor-help"
                    :class="(MF_PHASE_STYLE[g.phase] || {}).cls || 'bg-white/5 text-muted'"
                    :title="(MF_PHASE_STYLE[g.phase] || {}).tip || ''">{{ g.phase_cn || g.phase }}</span>
              <!-- ★ 2026-10-10（P1-2）：连续净流入分档（与榜单页观察池**同一份**共享配色/文案，
                   避免两处漂移）。文案用后端 `flow_consec_cn`。 -->
              <span v-if="g.flow_consec_cn" class="px-1 rounded shrink-0 cursor-help"
                    :class="(CONSEC_STYLE[g.flow_consec_tier] || {}).cls || 'bg-red-500/20 text-red-400'"
                    :title="(CONSEC_STYLE[g.flow_consec_tier] || {}).tip || ''">{{ g.flow_consec_cn }}</span>
              <!-- ★ 2026-10-11（用户："观察池卡片的异动标签隐藏，位置放不下了"）：
                   移除异动分析入口（原 2026-10-08 P3 加的共享组件）—— 右栏仅 320px，
                   行内元素已超载；信息不丢：榜单页观察池表仍保留该入口 -->
              <span v-for="(s, si) in (g.strategies || [])" :key="si"
                    class="ml-auto px-1 rounded bg-accent/10 text-accent border border-accent/30 text-[10px] shrink-0">
                {{ strategyShort(s) }}</span>
            </div>
          </div>
          <div v-if="gwItems.length > 8" class="text-[10px] text-muted mt-1">
            仅列前 8 只（共 {{ gwItems.length }} 只，全部见榜单页）
          </div>
        </template>
        <div v-else class="text-muted text-xs">
          —（当前无 ready≥2 的候选。三绿是**低频**条件，平时池空属正常）
        </div>
      </template>
      <div v-else class="text-[10px] text-muted">已收起 —— 点击标题展开</div>
    </div>
  </div>
</template>

<script setup>
// ==============================================================================
// 【StockListsCards】常驻右栏的两张卡（竖排）
//   ① 评分榜 Top10（含「主力行为」列） ② 观察池（买入闸门候选，**默认收起**可展开）
//
// ★ 2026-09-28 变更史：
//   ① 抽出为子组件（盘后 + 盘中/午盘复用同一份，避免"复制一份必然漂移"）；
//   ② 用户改为"挂常驻右栏（持仓摘要下方）" ⇒ 由**并排两列**改为**竖排**（右栏仅 320px）；
//      并按要求给评分榜加「主力行为」列、观察池改**默认收起**。
//
// ⚠️ 数据由**父组件**加载并以 props 传入（父组件「就绪状态条」用同一批数据）。
// ⚠️ 口径：观察池来自**晚间日批快照** ⇒ 盘中/15:00 看到的是**上一交易日**的池子
//    （`gwMeta.data_date` 显示实际日期）；评分榜的口径由 `topMode` 标注（本地实时/后端批次）。
// ==============================================================================
import { computed, ref, watch } from 'vue'
import {
  PHASE_STYLE as MF_PHASE_STYLE, CONSEC_STYLE, strategyShort, phaseCn, readyChipCls,
  stockHref, xqUrl, pctClass, signNum, scoreClass, boardChip,
} from '../../composables/displayMeta'

const props = defineProps({
  topItems: { type: Array, default: () => [] },
  topErr: { type: String, default: '' },
  topMode: { type: String, default: '' },
  // ★ 2026-09-28：距下次刷新的秒数（0 = 不显示，非交易时段由父级置 0）
  topCountdown: { type: Number, default: 0 },
  gwItems: { type: Array, default: () => [] },
  gwMeta: { type: Object, default: () => ({}) },
  gwErr: { type: String, default: '' },
  // ★ 2026-09-30：观察池**实时涨跌幅** `{code: {price, change_pct}}`（父级用
  //   `getBatchPrices` 拉，60s/120s 刷新）；未到达的 code ⇒ 显示「—」。
  gwPrices: { type: Object, default: () => ({}) },
  // ★ 2026-10-25（用户："评分榜区域新增防御 Top10 标签页"）：
  //   H2' 阴跌防御影子榜（`/score/batch/shadow-defensive`，日批快照）。
  //   父级与 topItems **同批加载**（一次轻请求），失败只影响该 tab（各自 err）。
  defItems: { type: Array, default: () => [] },
  defMeta: { type: Object, default: () => ({}) },
  defErr: { type: String, default: '' },
})

// 观察池默认**收起**（用户要求）
const watchOpen = ref(false)
// ★ 2026-10-23（用户："评分榜 Top10 也做展开收起"）：默认展开，状态记 localStorage
const TOP_KEY = 'wb_top_open'
const topOpen = ref((() => {
  try {
    const v = localStorage.getItem(TOP_KEY)
    return v == null ? true : v === '1'
  } catch { return true }
})())
function toggleTop() {
  topOpen.value = !topOpen.value
  try { localStorage.setItem(TOP_KEY, topOpen.value ? '1' : '0') } catch { /* ignore */ }
}

// ── 卡内两个标签页（2026-10-25）────────────────────────────────────────────────
//   与榜单页 tab 同款切换体验；**选中态也记忆**（用户下次进来还是那张榜）。
//   `detailTo` 随之切换：防御榜带 `?tab=defensive` 直达榜单页对应标签。
const cardTabs = [
  { key: 'score', label: '评分Top10', tip: '生产评分榜 Top10（与榜单页同源）' },
  { key: 'def', label: '防御Top10', tip: '阴跌防御影子榜 Top10（银行组阴跌段加权；影子期不改生产排序）' },
]
const CARD_TAB_KEY = 'wb_score_card_tab'
const cardTab = ref((() => {
  try { return localStorage.getItem(CARD_TAB_KEY) === 'def' ? 'def' : 'score' } catch { return 'score' }
})())
watch(cardTab, (v) => {
  try { localStorage.setItem(CARD_TAB_KEY, v) } catch (e) { console.warn('[top10] tab 记忆失败', e?.message) }
})
const detailTo = computed(() => (cardTab.value === 'def'
  ? { path: '/score', query: { tab: 'defensive' } }
  : { path: '/score' }))

/**
 * 主力阶段（六档全覆盖）：本地模式来自 `mainforce.phase`（K 线包），
 * 后端 `/score/batch/top` 亦下发 `mainforce.phase` ⇒ 两者同一路径。
 * （`ranking_history` 影子榜那套扁平字段 `mainforce_phase` 一并兜底。）
 */
function mfPhase(r) {
  return (r.mainforce && r.mainforce.phase) || r.mainforce_phase || null
}

/** 主力阶段中文名：优先后端 `phase_cn`，缺失用共享兜底表（后端 batch/top 不发 phase_cn）。 */
function mfPhaseCn(r) {
  return (r.mainforce && r.mainforce.phase_cn) || r.mainforce_phase_cn || ''
}

/**
 * 观察池该票的**实时涨跌幅**（%）；未拉到/无值 ⇒ `null`（模板显示「—」，**缺失 ≠ 0**）。
 * ⚠️ 行情偶发返回 null（停牌/休市/未订阅）⇒ 必须判 `Number.isFinite`，
 *    不能直接 `.toFixed(2)`（会抛异常、整卡白屏）。
 */
function gwPct(g) {
  const p = props.gwPrices ? props.gwPrices[g.code] : null
  const v = p && p.change_pct != null ? Number(p.change_pct) : null
  return v == null || !Number.isFinite(v) ? null : v
}

/**
 * 观察池行 hover 提示 —— **去掉常显状态文字后的信息出口**（2026-09-30）。
 * 合并三项：`label`（就绪度定性，如"等状态"）+ `hint`（为什么等）+ 「还差什么」（missing）。
 * ⚠️ `missing` 后端是**数组**（`[i["label"] for i in items if not ok]`）⇒ 需 join；
 *    老结构快照里也可能是字符串 ⇒ 两种都兼容（别只写 `.join` 否则字符串会崩）。
 */
function gwTip(g) {
  const missing = Array.isArray(g.missing) ? g.missing.join('、') : (g.missing || '')
  return [g.label, g.hint, missing ? `还差：${missing}` : ''].filter(Boolean).join('；')
}
</script>
