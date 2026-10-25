<template>
  <div class="max-w-[1400px] mx-auto p-4">
    <!-- 头部：定位 + 市况披露 -->
    <div class="flex items-center gap-3 flex-wrap mb-3">
      <h2 class="text-base font-semibold">选股工作台</h2>
      <span class="text-xs text-muted">事实筛选，不是买入清单；排序维度须看检验徽章</span>
      <span v-if="dataDate" class="ml-auto text-xs text-muted font-mono">数据截至 {{ dataDate }}（日批定稿）</span>
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
      <label class="flex items-center gap-1.5"><input type="checkbox" v-model="f.pattern">
        <span>形态命中 <span class="text-amber-300/80">〔预登记中〕</span></span></label>
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
      <span class="text-muted ml-auto">共 {{ total }} 只（NULL = 不在榜/无数据，非 0）</span>
    </div>

    <!-- 表 -->
    <div class="bg-card border border-border rounded-lg overflow-x-auto">
      <table class="w-full text-xs" v-if="rows.length">
        <thead><tr class="text-muted border-b border-border">
          <th class="py-2 px-2 text-left">代码/名称</th>
          <th class="px-2">行业</th>
          <th class="px-2">收盘</th>
          <th class="px-2">日涨跌</th>
          <th class="px-2">5日涨幅</th>
          <th class="px-2">20日涨幅</th>
          <th class="px-2" title="相对最近 60 个交易日最高收盘的偏离（负数=低于高点）">距60日高点</th>
          <th class="px-2" title="评分=展示工具（预登记 FAIL）：高分≠会涨">综合分 ⚠</th>
          <th class="px-2">名次</th>
          <th class="px-2">主力阶段</th>
          <th class="px-2" title="5 日主力净流入合计（亿元）">5日主力净流入(亿)</th>
          <th class="px-2" title="状态展示，非买入信号">闸门</th>
          <th class="px-2">龙虎榜</th>
          <th class="px-2">板块</th>
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
            <td class="px-2 text-center font-mono">{{ r.close ?? '—' }}</td>
            <td class="px-2 text-center font-mono" :class="cls(r.chg_pct)">{{ fmt(r.chg_pct) }}</td>
            <td class="px-2 text-center font-mono" :class="cls(r.ret5)">{{ fmt(r.ret5) }}</td>
            <td class="px-2 text-center font-mono" :class="cls(r.ret20)">{{ fmt(r.ret20) }}</td>
            <!-- 距高点：贴近高点（≥ −3%）标高亮，越远离越灰 —— 位置感一眼看出 -->
            <td class="px-2 text-center font-mono" :class="highCls(r.dist_from_60d_high)">
              {{ r.dist_from_60d_high != null ? r.dist_from_60d_high.toFixed(0) + '%' : '—' }}</td>
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
            <td class="px-2 text-center"><span v-if="r.defensive_group" class="text-sky-300">◆</span>
              <span v-else class="text-muted">·</span></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="p-8 text-center text-xs" :class="err ? 'text-amber-400' : 'text-muted'">
        {{ loading ? '加载中…' : (err || '无匹配（宽表首批数据由今晚日批生成；此前显示上一交易日）') }}
      </div>
    </div>
    <!-- 分页（★ 2026-10-26：每页 25 只；2000+ 只 ⇒ 必须能直接跳首/末页，不然要点几十次） -->
    <div class="flex items-center gap-2 mt-3 text-xs flex-wrap" v-if="total > 0">
      <span class="text-muted">共 <b class="text-gray-200 font-mono">{{ total }}</b> 只 ·
        每页 {{ f.size }} 只</span>
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
      徽章口径：评分〔预登记 FAIL 2026-10-11，禁作买入依据〕· 龙虎榜回避〔PASS-回避侧〕· 形态〔预登记中〕· 板块联动〔影子期 PARTIAL〕· 防御组〔H2' 影子中〕· 闸门〔状态展示非信号〕。
      本表 = 事实组合筛选，不含任何加权综合分（加权=新信号须预登记）。
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
// ★ 2026-10-26（审查 P2）：走统一 http 封装（鉴权头/统一错误拦截），不再自建 axios
import { getStockFactsWide } from '../api'
// 主力阶段中文名 + 配色（唯一映射源后端 phases.PHASE_CN / 前端兜底表在 displayMeta）
// ★ 2026-10-26（用户："颜色区分，比如吸筹之前是绿色标签"）：**复用**全站同一份
//   `PHASE_STYLE`（绿=机会/红=风险）与 `scoreClass`、`readyChipCls`，不另建一套配色。
import { phaseCn, PHASE_CN, PHASE_STYLE, scoreClass, readyChipCls, stockHref, xqUrl } from '../composables/displayMeta'

const rows = ref([])
const total = ref(0)
const dataDate = ref('')
const loading = ref(false)
const err = ref('')
const page = ref(0)
// ★ 2026-10-26（用户："一页数据太长了，一页20只就好"）：100 → 20（15 列宽表，100 行要滚很久）
const f = reactive({ phase: '', defensive: false, lhbRisk: false, pattern: false,
                     sectorStrong: false, order: 'rank_pos', size: 20 })

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

watch(page, load)
onMounted(load)

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
