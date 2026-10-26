<template>
  <div class="max-w-[1400px] mx-auto p-4">
    <!-- 头部：定位 + 市况披露 -->
    <div class="flex items-center gap-3 flex-wrap mb-3">
      <h2 class="text-base font-semibold">选股工作台</h2>
      <span class="text-xs text-muted">事实筛选，不是买入清单；排序维度须看检验徽章</span>
      <span class="ml-auto flex items-center gap-2 text-xs flex-wrap">
        <!-- ★ 2026-10-27（用户："批量查询当前页的实时涨跌幅"）：手动刷新按钮。
             两段式：先读后端行情内存缓存（零外呼），偏旧（>90s）才自动直连腾讯补一次（60s 节流）。 -->
        <button @click="refreshLiveClick" :disabled="refreshing"
                class="px-2 py-0.5 rounded border border-border text-[10px] text-muted hover:bg-white/5 hover:text-gray-200 disabled:opacity-40"
                title="立即刷新当前页的实时现价/涨跌幅：先读后端行情内存缓存（零外部请求，最多 2 分钟延迟）；缓存偏旧（>90s）时自动直连腾讯补一次（60s 节流，连点不会打爆上游）。休市时点击无效（维持日批口径）。">
          {{ refreshing ? '刷新中…' : '刷新涨跌幅' }}</button>
        <!-- ★ 2026-10-26：盘中实时口径**主动标注**（涨跌幅/现价来自后端行情缓存） -->
        <span v-if="liveOn"
              class="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-400 text-[10px] cursor-help"
              :title="`盘中实时：现价与涨跌幅来自后端行情内存缓存${liveMeta.age_sec != null ? '（' + liveMeta.age_sec + 's 前采集）' : ''}，约 2 分钟随调度器刷新；5 日/20 日涨幅与距高点已按现价重算。零外部请求（没有向腾讯多发一次）`">
          盘中实时 {{ liveAsOf || '—' }}
        </span>
        <span v-else class="text-[10px]" :class="liveEmpty && tradingNow ? 'text-amber-400/80' : 'text-muted'">
          {{ liveNote }}
        </span>
        <span v-if="dataDate" class="text-muted font-mono">数据截至 {{ dataDate }}（日批定稿）</span>
      </span>
    </div>

    <!-- 筛选器 -->
    <div class="bg-card border border-border rounded-lg p-3 mb-3 flex flex-wrap gap-x-5 gap-y-2 items-end text-xs">
      <label class="flex flex-col gap-1">
        <span class="text-muted">主力阶段</span>
        <!-- ★ 2026-10-26：**值必须是英文枚举**（`stock_facts.phase` 存的就是枚举）——
             原来直接传中文「吸筹」⇒ 后端等值匹配恒空、筛选静默 0 行；文案仍显示中文。 -->
        <select v-model="f.phase" class="bg-white/5 border border-border rounded px-2 py-1">
          <option value="">全部</option>
          <option v-for="p in PHASE_OPTIONS" :key="p.key" :value="p.key">{{ p.cn }}</option>
        </select>
      </label>
      <label class="flex items-center gap-1.5"><input type="checkbox" v-model="f.defensive">
        <span>防御组 <span class="text-sky-300/80">〔H2' 影子中〕</span></span></label>
      <label class="flex items-center gap-1.5"><input type="checkbox" v-model="f.lhbRisk">
        <span>龙虎榜回避 <span class="text-emerald-300/80">〔PASS-回避〕</span></span></label>
      <label class="flex items-center gap-1.5"
             title="缩量启动形态（预登记中：正边际但不赚钱）。⚠️ 只在上游 backtest_prices 宇宙（~1032 只）扫描，判据苛刻 ⇒ 单日常 0~1 只；勾上后空表属正常，不是坏了">
        <input type="checkbox" v-model="f.pattern">
        <span>形态命中 <span class="text-amber-300/80">〔预登记中〕</span></span></label>
      <!-- ★ 2026-10-26（用户："还有加个战法命中"）：任意战法命中（日批 strategy_results） -->
      <label class="flex items-center gap-1.5"
             title="当日被任一战法扫中（ma_pullback / single_yang_unbroken / ma_convergence_breakout 等 10 个）。数据来自日批扫描结果，实测单日约 50~60 只；未命中 = 空，不是没扫">
        <input type="checkbox" v-model="f.strategy">
        <span>战法命中 <span class="text-emerald-300/80">〔日批扫描〕</span></span></label>
      <label class="flex items-center gap-1.5"><input type="checkbox" v-model="f.sectorStrong">
        <span>强势板块 <span class="text-amber-300/80">〔影子 PARTIAL〕</span></span></label>
      <label class="flex flex-col gap-1">
        <span class="text-muted">排序</span>
        <select v-model="f.order" class="bg-white/5 border border-border rounded px-2 py-1">
          <option value="rank_pos">精算榜名次↑</option>
          <option value="ret5">5日涨幅↓</option>
          <option value="ret20">20日涨幅↓</option>
          <option value="flow5_amt_yi">5日主力净流入↓</option>
          <option value="dist_from_60d_high">距60日高点↑</option>
          <option value="turnover_pct">换手率↓</option>
          <option value="float_cap_yi">流通市值↑</option>
        </select>
      </label>
      <button @click="apply" :disabled="loading"
        class="px-3 py-1 rounded bg-accent/20 text-accent hover:bg-accent/30 disabled:opacity-40">
        {{ loading ? '查询中…' : '应用' }}</button>
      <!-- ★ 2026-10-26（用户："共 N 只（NULL = 不在榜/无数据，非 0）放到分页那里"）：
           计数与 NULL 口径移到下方分页条，筛选区不再重复占位。 -->
    </div>

    <!-- 表 -->
    <div class="bg-card border border-border rounded-lg overflow-x-auto">
      <table class="w-full text-xs" v-if="rows.length">
        <thead><tr class="text-muted border-b border-border">
          <th class="py-2 px-2 text-left">代码/名称</th>
          <th class="px-2">行业</th>
          <th class="px-2" title="交易时段＝实时现价（后端行情缓存，约 2 分钟一刷）；休市＝当日收盘（日批定稿）">现价</th>
          <th class="px-2" title="交易时段随现价走；休市为日批定稿值">日涨跌</th>
          <th class="px-2" title="基准＝日批收盘；交易时段已按现价重算（×当日涨跌）">5日涨幅</th>
          <th class="px-2" title="基准＝日批收盘；交易时段已按现价重算（×当日涨跌）">20日涨幅</th>
          <th class="px-2" title="相对最近 60 个交易日最高收盘的偏离（负数=低于高点）；交易时段已按现价重算">距60日高点</th>
          <th class="px-2" title="评分=展示工具（预登记 FAIL）：高分≠会涨">综合分 ⚠</th>
          <th class="px-2">名次</th>
          <th class="px-2">主力阶段</th>
          <th class="px-2" title="5 日主力净流入合计（亿元）">5日主力净流入(亿)</th>
          <th class="px-2" title="状态展示，非买入信号">闸门</th>
          <th class="px-2">龙虎榜</th>
          <th class="px-2">板块</th>
          <th class="px-2" title="当日命中的战法（日批 strategy_results）；形态命中另以「形态」chip 标注">战法</th>
          <th class="px-2">防御</th>
        </tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.code" class="border-b border-border/40 hover:bg-white/[0.03]">
            <!-- ★ 2026-10-26（用户："点击股票代码需要跳到雪球"；"点击股票名称跳转错误"）：
                 名称 → 站内详情页（⚠️ 必须用 `stockHref`：路由是 **hash 模式**，
                 裸写 `/stock/${code}` 会落到站点根路径 ⇒ 打开是 404/首页，这就是"跳转错误"）；
                 **代码 → 雪球**（全站口径：名称走站内、代码走雪球）。 -->
            <td class="py-1.5 px-2 whitespace-nowrap">
              <a :href="stockHref(r.code)" target="_blank" rel="noopener"
                 class="hover:text-accent font-medium">{{ r.name }}</a>
              <a :href="xqUrl(r.code)" target="_blank" rel="noopener"
                 class="text-muted font-mono text-[10px] ml-1 hover:text-accent hover:underline"
                 :title="`雪球 ${r.code}`">{{ r.code }}</a>
            </td>
            <td class="px-2 text-center text-muted">{{ r.industry || '—' }}</td>
            <!-- ★ 2026-10-26：以下四列交易时段用**实时值**（`q()/lv()` 派生），
                 休市或取不到 ⇒ 回退日批值（缺数据不填 0，也不假装实时）。 -->
            <td class="px-2 text-center font-mono">
              <span :class="q(r) ? 'text-gray-200' : ''">{{ q(r) ? q(r).price : (r.close ?? '—') }}</span>
            </td>
            <td class="px-2 text-center font-mono" :class="cls(q(r) ? q(r).change_pct : r.chg_pct)">
              {{ fmt(q(r) ? q(r).change_pct : r.chg_pct) }}</td>
            <td class="px-2 text-center font-mono" :class="cls(lv(r, r.ret5))">{{ fmt(lv(r, r.ret5)) }}</td>
            <td class="px-2 text-center font-mono" :class="cls(lv(r, r.ret20))">{{ fmt(lv(r, r.ret20)) }}</td>
            <!-- 距高点：贴近高点（≥ −3%）标高亮，越远离越灰 —— 位置感一眼看出 -->
            <td class="px-2 text-center font-mono" :class="highCls(lv(r, r.dist_from_60d_high))">
              {{ pct0(lv(r, r.dist_from_60d_high)) }}</td>
            <!-- 综合分：全站 scoreClass（≥65 红 / ≥45 琥珀 / 其余灰） -->
            <td class="px-2 text-center font-mono font-bold" :class="scoreClass(r.score_total)">
              {{ r.score_total ?? '—' }}</td>
            <td class="px-2 text-center font-mono" :class="rankCls(r.rank_pos)">{{ r.rank_pos ?? '—' }}</td>
            <!-- ★ 2026-10-26（用户："主力阶段现在是英文"→中文；"颜色区分，如吸筹原是绿色标签"）：
                 文案走 `phaseCn`（后端 phase_cn 优先），配色复用全站 `PHASE_STYLE`
                 —— 语义是 **绿=机会 / 红=风险**（吸筹绿、出货红、拉升琥珀=追高、洗盘青、
                 下跌灰、盘整无底色），与榜单页/观察池同一份映射，不另建一套。 -->
            <td class="px-2 text-center">
              <span v-if="r.phase"
                    class="px-1.5 py-0.5 rounded text-[10px] font-semibold cursor-help"
                    :class="(PHASE_STYLE[r.phase] || {}).cls || 'bg-white/5 text-muted'"
                    :title="(PHASE_STYLE[r.phase] || {}).tip || ''">{{ phaseCn(r.phase) }}</span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="px-2 text-center font-mono" :class="pct(r.flow5_amt_yi)">
              {{ r.flow5_amt_yi != null ? r.flow5_amt_yi.toFixed(1) : '—' }}</td>
            <!-- 闸门：与观察池同款 `readyChipCls`（3/3 绿、2/3 琥珀、其余灰；状态展示非信号） -->
            <td class="px-2 text-center">
              <span v-if="r.gate_ready != null"
                    class="px-1 rounded font-mono text-[10px] font-bold cursor-help"
                    :class="readyChipCls(r.gate_ready)"
                    title="买入条件就绪度（状态展示，非买入信号）">{{ r.gate_ready }}/3</span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="px-2 text-center">
              <span v-if="r.lhb_big_net_sell" class="text-amber-400" title="最近上榜为大额净卖（PASS-回避）">回避</span>
              <span v-else class="text-muted">—</span></td>
            <td class="px-2 text-center">
              <span v-if="r.sector_top30 === 1" class="text-rise">强</span>
              <span v-else-if="r.sector_top30 === 0" class="text-muted">·</span>
              <span v-else class="text-muted">—</span></td>
            <!-- ★ 2026-10-26（用户："还有加个战法命中"）：命中的战法 chips（中文短名）＋形态 chip。
                 无命中但当日扫过 ⇒ 「·」；当日根本没产出扫描 ⇒ 「—」（无数据 ≠ 未命中）。 -->
            <td class="px-2 text-center whitespace-nowrap">
              <template v-if="strategyList(r).length || r.pattern_hit === 1">
                <span v-for="s in strategyList(r)" :key="s"
                      class="px-1 rounded text-[10px] bg-emerald-500/15 text-emerald-300 mr-0.5 cursor-help"
                      :title="`战法命中：${strategyShort(s)}（${s}）｜ 来源：日批 strategy_results`">{{ strategyShort(s) }}</span>
                <span v-if="r.pattern_hit === 1"
                      class="px-1 rounded text-[10px] bg-amber-500/15 text-amber-300 cursor-help"
                      title="缩量启动形态命中（预登记中：正边际但不赚钱，仅候选观察）">形态</span>
              </template>
              <span v-else-if="r.pattern_hit === 0" class="text-[10px] text-muted">·</span>
              <span v-else class="text-muted">—</span>
            </td>
            <td class="px-2 text-center"><span v-if="r.defensive_group" class="text-sky-300">◆</span>
              <span v-else class="text-muted">·</span></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="p-8 text-center text-xs" :class="err ? 'text-amber-400' : 'text-muted'">
        {{ loading ? '加载中…' : (err || '无匹配（宽表首批数据由今晚日批生成；此前显示上一交易日）') }}
      </div>
    </div>
    <!-- 分页（★ 2026-10-26：每页 20 只；2000+ 只 ⇒ 必须能直接跳首/末页，不然要点几十次） -->
    <div class="flex items-center gap-2 mt-3 text-xs flex-wrap" v-if="total > 0">
      <span class="text-muted">共 <b class="text-gray-200 font-mono">{{ total }}</b> 只
        （NULL = 不在榜/无数据，非 0） · 每页 {{ f.size }} 只</span>
      <template v-if="total > f.size">
        <button @click="goPage(0)" :disabled="page <= 0"
          class="px-2 py-1 rounded bg-white/5 disabled:opacity-40">« 首页</button>
        <button @click="page--" :disabled="page <= 0"
          class="px-2 py-1 rounded bg-white/5 disabled:opacity-40">上一页</button>
        <span class="text-muted font-mono">{{ page + 1 }} / {{ totalPages }}</span>
        <button @click="page++" :disabled="page + 1 >= totalPages"
          class="px-2 py-1 rounded bg-white/5 disabled:opacity-40">下一页</button>
        <button @click="goPage(totalPages - 1)" :disabled="page + 1 >= totalPages"
          class="px-2 py-1 rounded bg-white/5 disabled:opacity-40">末页 »</button>
      </template>
    </div>
    <!-- ★ 2026-10-26：配色图例（两套色系并存，必须说明，否则"绿=机会/绿=跌"会打架） -->
    <div class="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-[10px] text-muted">
      <span class="text-gray-300">配色</span>
      <span>阶段
        <span class="ml-0.5 px-1 rounded bg-emerald-500/20 text-emerald-400">吸筹</span>
        <span class="px-1 rounded bg-red-500/20 text-red-400">出货</span>
        <span class="px-1 rounded bg-amber-500/20 text-amber-400">拉升</span>
        <span class="px-1 rounded bg-cyan-500/20 text-cyan-300">洗盘</span>
        <span class="px-1 rounded bg-zinc-500/20 text-zinc-400">下跌</span>
        <span class="px-1 rounded bg-white/5">盘整</span>
        <span class="text-muted/70">（绿=机会，红=风险，非涨跌）</span>
      </span>
      <span>涨跌/资金流 <span class="text-rise font-mono">+</span>/<span class="text-fall font-mono">−</span>
        <span class="text-muted/70">（红=涨/净流入）</span></span>
      <span>闸门 <span class="px-1 rounded bg-emerald-500/20 text-emerald-400 font-mono">3/3</span></span>
      <span>距高点 <span class="text-amber-300 font-mono">≥−3%</span>
        <span class="text-muted/70">（贴近 60 日高）</span></span>
    </div>
    <div class="mt-3 text-[10px] text-muted leading-relaxed">
      徽章口径：评分〔预登记 FAIL 2026-10-11，禁作买入依据〕· 龙虎榜回避〔PASS-回避侧〕· 形态〔预登记中〕· 板块联动〔影子期 PARTIAL〕· 防御组〔H2' 影子中〕· 闸门〔状态展示非信号〕· 战法命中〔日批扫描，单日约 50~60 只〕。
      本表 = 事实组合筛选，不含任何加权综合分（加权=新信号须预登记）。
      <br>
      ⚠️ 「形态命中」稀疏属正常：上游只在 backtest_prices 宇宙（约 1032 只）扫描、判据苛刻，
      单日常 0~1 只；「战法」列 `—` 表示当日未产出扫描数据（NULL），`·` 表示扫过但未命中。
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, onBeforeUnmount, watch } from 'vue'
// ★ 2026-10-26（审查 P2）：走统一 http 封装（鉴权头/统一错误拦截），不再自建 axios
// ★ 2026-10-26（用户批准"盘中实时涨跌幅"）：`getBatchPricesCached` = 只读后端内存缓存，
//   上游请求 0；`isTradingTime` 保证只在交易时段轮询（休市一分钟都不发）。
import { getStockFactsWide, getBatchPricesCached, getBatchPrices } from '../api'
import { isTradingTime } from '../composables/usePortfolio'
// 主力阶段中文名 + 配色（唯一映射源后端 phases.PHASE_CN / 前端兜底表在 displayMeta）
// ★ 2026-10-26（用户："颜色区分，比如吸筹之前是绿色标签"）：**复用**全站同一份
//   `PHASE_STYLE`（绿=机会/红=风险）与 `scoreClass`、`readyChipCls`，不另建一套配色。
import { phaseCn, PHASE_CN, PHASE_STYLE, scoreClass, readyChipCls, stockHref,
         strategyShort, xqUrl } from '../composables/displayMeta'

const rows = ref([])
const total = ref(0)
const dataDate = ref('')
const loading = ref(false)
const err = ref('')
const page = ref(0)
// ★ 2026-10-26（用户："一页数据太长了，一页20只就好"）：100 → 20（15 列宽表，100 行要滚很久）
const f = reactive({ phase: '', defensive: false, lhbRisk: false, pattern: false,
                     strategy: false, sectorStrong: false, order: 'rank_pos', size: 20 })

// 战法命中列：`strategy_hit` 是英文 key 逗号分隔（如 "ma_pullback,single_yang_unbroken"）
function strategyList(r) {
  const s = (r && r.strategy_hit) || ''
  return s ? String(s).split(',').filter(Boolean) : []
}

const DESC = { ret5: 1, ret20: 1, flow5_amt_yi: 1, turnover_pct: 1 }

// 主力阶段下拉：**value=英文枚举 / 文案=中文**（与库里 `stock_facts.phase` 对齐）
const PHASE_ORDER = ['accumulation', 'shakeout', 'markup', 'distribution', 'decline', 'sideways']
const PHASE_OPTIONS = PHASE_ORDER.map(k => ({ key: k, cn: PHASE_CN[k] }))

async function load() {
  loading.value = true
  err.value = ''
  try {
    const { data } = await getStockFactsWide({
      order_by: f.order, desc: !!DESC[f.order],
      limit: f.size, offset: page.value * f.size,
      ...(f.phase ? { phase: f.phase } : {}),
      ...(f.defensive ? { defensive: 1 } : {}),
      ...(f.lhbRisk ? { lhb_risk: 1 } : {}),
      ...(f.pattern ? { pattern: 1 } : {}),
      ...(f.strategy ? { strategy: 'any' } : {}),
      ...(f.sectorStrong ? { sector_strong: 1 } : {}),
    })
    rows.value = (data && data.rows) || []
    total.value = (data && data.total) || 0
    dataDate.value = (data && data.date) || ''
  } catch (e) {
    // ★ 审查 P2：失败要**如实说**，不能清空后伪装成"无匹配"
    rows.value = []
    total.value = 0
    err.value = '加载失败：' + (e && e.response && e.response.status
      ? `HTTP ${e.response.status}` : ((e && e.message) || e))
  } finally { loading.value = false }
}

// 改筛选后必须回到第 1 页（否则可能停在第 3 页看到空白）
function apply() {
  page.value = 0
  load()
}

// ── 盘中实时（★ 2026-10-26，用户批准）─────────────────────────────────────────
//   数据源 = **后端行情内存缓存**（`/score/batch-prices?cached=1`，调度器每 ~120s
//   刷全市场）⇒ **上游请求 = 0**，只花本机 CPU；20 只 ≈1.6KB/次（gzip 0.57KB）。
//   纪律（与全站一致）：
//     ① 只在交易时段轮询（`isTradingTime` 已排周末/午休），休市一分钟都不发；
//     ② 页面在后台（`document.hidden`）不请求 —— 省电省流量；
//     ③ 失败/无数据 ⇒ **保留上一次的值**（不闪「—」、不填 0），只降级为日批口径；
//     ④ 后端若报 `from_snapshot=true`（休市日缓存是上一交易日收盘快照）⇒ **拒用**，
//        否则会把昨天收盘当"今日现价"（本项目踩过快照谎报事故）。
const live = ref({})        // {code: {price, change_pct}}
const liveMeta = ref({})    // {as_of, data_ts, data_date, from_snapshot, age_sec, n}
const liveEmpty = ref(false)   // 缓存没覆盖到这批票（冷启动/冷门票）⇒ 如实说明，不装"正在取"
const tradingNow = ref(isTradingTime())
let liveTimer = null

const liveOn = computed(() => Object.keys(live.value).length > 0)
const liveAsOf = computed(() => liveMeta.value.as_of || '')
// 未拿到实时值时的**诚实**说明（不写"正在加载"糊过去）
const liveNote = computed(() => {
  if (!tradingNow.value) return '休市 · 日批口径'
  if (liveMeta.value.rejected === 'snapshot') return '行情为收盘快照 · 日批口径'
  if (liveEmpty.value) return '行情缓存未就绪 · 日批口径'
  return '取实时行情中…'
})

function q(r) { return live.value[(r && r.code) || ''] || null }

// 基准换算：日批的 ret5/ret20/距高点都以**日批收盘**为基准，
// 换成现价只需整体 ×(1+当日涨跌) —— 不需要重新取任何历史数据。
function lv(r, v) {
  if (v == null) return v
  const m = q(r)
  if (!m || m.change_pct == null) return v
  const g = Number(m.change_pct) / 100
  if (!Number.isFinite(g)) return v
  return ((1 + Number(v) / 100) * (1 + g) - 1) * 100
}
function pct0(v) {
  return v == null ? '—' : Number(v).toFixed(0) + '%'
}

// `mode`：'auto' = 60s 轮询（页面在后台就不发）；'click' = 用户点按钮（正盯着页面，跳过 hidden 检查）
async function loadLive(mode = 'auto') {
  tradingNow.value = isTradingTime()
  const codes = (rows.value || []).map(r => r.code).filter(Boolean)
  if (!tradingNow.value || !codes.length) return
  if (mode === 'auto' && typeof document !== 'undefined' && document.hidden) return
  try {
    const { data } = await getBatchPricesCached(codes)
    if (!data || data.from_snapshot) {
      liveMeta.value = { ...(data || {}), rejected: 'snapshot' }
      live.value = {}
      liveEmpty.value = true
      return
    }
    const m = {}
    for (const s of (data.rows || [])) {
      if (s && s.code && Number(s.price) > 0) m[s.code] = s
    }
    if (Object.keys(m).length) {
      live.value = m
      liveMeta.value = data
      liveEmpty.value = false
    } else {
      liveEmpty.value = true     // 缓存没覆盖这批票（冷启动期/极冷门票）⇒ 保留日批值
    }
  } catch (e) {
    console.warn('[screener] 实时行情刷新失败（保留上次）', (e && e.message) || e)
  }
}

// ── 手动刷新按钮（★ 2026-10-27，用户："批量查询当前页的实时涨跌幅"）──────────────
//   两段式：① 先读后端行情内存缓存（cached=1，**零外部请求**，最多 2 分钟延迟）；
//           ② 若拿到的偏旧（age_sec>90）或压根没拿到（缓存冷）⇒ **自动直连腾讯补一次**
//             （cached=0；20 只 = 1 批，属"用户点击的按需请求"，全站口径允许）。
//   【节流】直连 60s 内至多一次 —— 连点按钮不会变成对腾讯的轮询（纪律：按需允许、轮询禁止）。
//   【休市】isTradingTime=false ⇒ 直接 return（liveNote 已显示"休市 · 日批口径"），
//           休市直连拿到的也是昨收，没有任何增量信息。
const refreshing = ref(false)
let lastDirectAt = 0            // 上次直连腾讯的 ms 时间戳（60s 节流）

async function fetchDirect(codes) {
  try {
    const { data } = await getBatchPrices(codes)
    // 兼容两种返回：cached=0 是老数组结构；万一后端改动成 {rows} 也不至于白屏
    const arr = Array.isArray(data) ? data : ((data && data.rows) || [])
    const m = {}
    for (const s of arr) {
      if (s && s.code && Number(s.price) > 0) m[s.code] = s
    }
    if (Object.keys(m).length) {
      live.value = m
      liveMeta.value = {
        as_of: new Date().toTimeString().slice(0, 5),   // 直连 ⇒ 数据时刻就是现在
        n: Object.keys(m).length,
        direct: true,                                    // 徽章 title 会注明"刚刚直连"
      }
      liveEmpty.value = false
    }
  } catch (e) {
    console.warn('[screener] 直连取价失败（保留上次）', (e && e.message) || e)
  }
}

async function refreshLiveClick() {
  if (refreshing.value) return
  refreshing.value = true
  try {
    if (!isTradingTime()) { tradingNow.value = false; return }
    await loadLive('click')                              // 第一步：读缓存（0 外呼）
    const age = Number((liveMeta.value && liveMeta.value.age_sec) || 0)
    const stale = !liveOn.value || age > 90              // 缓存冷启动 / 超过 90s 未采集
    if (stale && Date.now() - lastDirectAt >= 60000) {
      lastDirectAt = Date.now()
      const codes = (rows.value || []).map(r => r.code).filter(Boolean)
      if (codes.length) await fetchDirect(codes)
    }
  } finally {
    refreshing.value = false
  }
}

// 行/页变化：先清掉旧页的实时值（否则会把上一页价格贴到新票上），再取新页
watch(rows, () => { live.value = {}; liveEmpty.value = false; loadLive() })
watch(page, () => { live.value = {}; liveEmpty.value = false; load() })
onMounted(() => {
  load()
  liveTimer = setInterval(() => { if (isTradingTime()) loadLive() }, 60000)
})
onBeforeUnmount(() => { if (liveTimer) clearInterval(liveTimer) })

function fmt(v) {
  if (v == null) return '—'
  return (v > 0 ? '+' : '') + Number(v).toFixed(1) + '%'
}
function cls(v) {
  const n = Number(v) || 0
  return n > 0 ? 'text-rise' : (n < 0 ? 'text-fall' : 'text-muted')
}
// ★★ 2026-10-26（审查 P0-①）：模板里一直在用 `pct()`，但本文件从未定义
//   ⇒ 运行时 `pct is not a function`，整表渲染中断（构建不报错，同 pbwOpen 教训）。
//   语义与 `cls` 一致：A 股习惯 **红=净流入/上涨，绿=净流出/下跌**，缺失=灰。
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / f.size)))
// 直接跳页（首/末页按钮用）；`watch(page, load)` 负责真正请求
function goPage(p) {
  page.value = Math.min(Math.max(0, p), totalPages.value - 1)
}
function pct(v) {
  if (v == null) return 'text-muted'
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return 'text-muted'
  return n > 0 ? 'text-rise' : 'text-fall'
}
// 距 60 日高点：≥ −3% = 贴近高点（高位区，琥珀提示）；≤ −20% 明显回落（灰淡）；
// 其余中性 —— 这是**位置事实**，不与「红涨绿跌」混用同一套语义。
function highCls(v) {
  if (v == null) return 'text-muted'
  const n = Number(v)
  if (!Number.isFinite(n)) return 'text-muted'
  if (n >= -3) return 'text-amber-300'
  if (n <= -20) return 'text-muted/60'
  return 'text-gray-300'
}
// 名次：精算榜前 50 高亮（在榜前列），其余保持灰
function rankCls(v) {
  if (v == null) return 'text-muted'
  return Number(v) <= 50 ? 'text-gray-200 font-bold' : 'text-muted'
}
</script>
