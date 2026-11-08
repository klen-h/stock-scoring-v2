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
      <!-- ★★ 2026-11-01（用户："文字提示不需要展示了"）：
           删掉整块口径标注（本地实时/后端批次 · 120s 后刷新 · H2' 防御影子 · 实时切换按钮）——
           右栏只有 320px，这几行把三张榜都压得很挤；口径仍留在 `topMode`/`defMeta` 里
           供 hover/其它页面使用。防御榜改为**默认走精算排名**（见 Workbench `loadDefTop`）。 -->

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

      <!-- ② 防御 Top10（H2' 阴跌防御影子榜）
           ★ 2026-11-01（用户："不需要显示防御二字"）：行内去掉「防御」加成标记。
             涨跌幅现在**必有** —— 防御榜默认改为走**精算排名**
             （Workbench `loadDefTop` → `loadDefLive`，本地引擎精算宽防御组，
              带实时 `change_pct`；本地引擎未就绪时回退后端实时重排）。 -->
      <template v-else-if="cardTab === 'def'">
        <div v-if="defErr" class="text-muted text-xs">—（{{ defErr }}）</div>
        <div v-else-if="!defItems.length" class="text-muted text-xs">—（暂无数据）</div>
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
            <span v-if="r.change_pct != null" class="shrink-0 font-mono"
                  :class="pctClass(r.change_pct)">{{ signNum(r.change_pct) }}%</span>
            <span v-else class="shrink-0 font-mono text-muted">—</span>
            <span class="ml-auto shrink-0 font-mono font-bold" :class="scoreClass(r.total_score)">
              {{ r.total_score }}</span>
          </div>
        </div>
      </template>

      <!-- ★★ 2026-11-08（ETF 榜·方案 B）：ETF 热度 Top10——排在防御 Top10 之后（用户指定顺序） -->
      <template v-else-if="cardTab === 'etf'">
        <EtfBoardCard :top="10" />
      </template>
      <!-- ③ 观察池（买入闸门候选）—— 由**独立第二张卡**并入本卡，排在防御 Top10 之后
           ★ 2026-11-01（用户："将观察池作为一个 tab 并入评分 top10 卡片，放在防御 top10 的后面，
             也是展示 10 个，文字提示不需要展示了"）：
             · 去掉「候选 N · 三绿 M · 分布 · 战法信号 · 市况」整行（右栏 320px 太挤）；
             · 展示数 8 → 10（与另两个 tab 对齐）；
             · 行 hover 仍给 `gwTip()`（ready 定性 + 为什么等 + 还差什么），信息不丢。 -->
      <template v-else>
        <div v-if="gwErr" class="text-muted text-xs">—（加载失败：{{ gwErr }}）</div>
        <div v-else-if="!gwItems.length" class="text-muted text-xs">—（当前无 ready≥2 的候选）</div>
        <div v-else class="space-y-1 text-xs">
          <div v-for="g in gwItems.slice(0, 10)" :key="g.code"
               class="flex items-center gap-1.5 flex-wrap border-b border-border/40 py-1"
               :title="gwTip(g)">
            <a :href="stockHref(g.code)" target="_blank"
               class="font-semibold hover:text-accent truncate max-w-[80px]">{{ g.name || g.code }}</a>
            <a :href="xqUrl(g.code)" target="_blank" rel="noopener" title="雪球"
               class="font-mono text-muted hover:text-accent shrink-0">{{ g.code }}</a>
            <span v-if="gwPct(g) != null" class="shrink-0 font-mono" :class="pctClass(gwPct(g))">
              {{ signNum(gwPct(g)) }}%</span>
            <span v-else class="shrink-0 font-mono text-muted">—</span>
            <span class="px-1 rounded font-mono text-[10px] shrink-0"
                  :class="g.ready === 3 ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-300'">
              {{ g.ready }}/3</span>
            <span v-if="g.phase" class="px-1 rounded shrink-0 cursor-help"
                  :class="(MF_PHASE_STYLE[g.phase] || {}).cls || 'bg-white/5 text-muted'"
                  :title="(MF_PHASE_STYLE[g.phase] || {}).tip || ''">{{ g.phase_cn || g.phase }}</span>
            <span v-if="g.flow_consec_cn" class="px-1 rounded shrink-0 cursor-help"
                  :class="(CONSEC_STYLE[g.flow_consec_tier] || {}).cls || 'bg-red-500/20 text-red-400'"
                  :title="(CONSEC_STYLE[g.flow_consec_tier] || {}).tip || ''">{{ g.flow_consec_cn }}</span>
            <span v-for="(s, si) in (g.strategies || [])" :key="si"
                  class="ml-auto px-1 rounded bg-accent/10 text-accent border border-accent/30 text-[10px] shrink-0">
              {{ strategyShort(s) }}</span>
          </div>
        </div>
      </template>
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
import EtfBoardCard from './EtfBoardCard.vue'
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
  // ★ 2026-10-25（用户："想让它在盘中变动"）：实时预览开关（父级负责取数，本组件只发事件）
  defIsLive: { type: Boolean, default: false },
  defLiveLoading: { type: Boolean, default: false },
})
const emit = defineEmits(['toggle-live'])

// ★ 2026-11-01：观察池已并入 tab ⇒ `watchOpen`（原"默认收起"开关）删除。
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
// ★★ 2026-11-01（用户："将观察池作为一个 tab 并入评分 top10 卡片，放在防御 top10 的后面"）：
//   「观察池」从**独立第二张卡**收为第三 tab ⇒ 右栏少一张卡、三榜一处切换。
const cardTabs = [
  { key: 'score', label: '评分', tip: '生产评分榜 Top10（与榜单页同源）' },
  { key: 'def', label: '防御', tip: '阴跌防御影子榜 Top10（银行组阴跌段加权；影子期不改生产排序）' },
  { key: 'etf', label: 'ETF', tip: 'ETF 实时热度榜 Top10（量比×涨跌×成交额；行情展示非信号）' },
  { key: 'watch', label: '观察池', tip: '买入闸门候选（ready≥2）· 日批快照 + 实时涨跌幅' },
]
const CARD_TAB_KEY = 'wb_score_card_tab'
const cardTab = ref((() => {
  try {
    const v = localStorage.getItem(CARD_TAB_KEY)
    return (v === 'def' || v === 'watch' || v === 'etf') ? v : 'score'
  } catch { return 'score' }
})())
watch(cardTab, (v) => {
  try { localStorage.setItem(CARD_TAB_KEY, v) } catch (e) { console.warn('[top10] tab 记忆失败', e?.message) }
})
const detailTo = computed(() => {
  if (cardTab.value === 'def') return { path: '/score', query: { tab: 'defensive' } }
  if (cardTab.value === 'watch') return { path: '/score', query: { tab: 'watch' } }
  return { path: '/score' }
})

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
