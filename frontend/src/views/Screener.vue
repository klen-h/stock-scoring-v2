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
        <select v-model="f.phase" class="bg-white/5 border border-border rounded px-2 py-1">
          <option value="">全部</option>
          <option v-for="p in ['吸筹', '洗盘', '拉升', '出货', '下跌', '盘整']" :key="p" :value="p">{{ p }}</option>
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
            <td class="py-1.5 px-2">
              <a :href="`/stock/${r.code}`" target="_blank" rel="noopener"
                 class="hover:text-accent font-medium">{{ r.name }}</a>
              <span class="text-muted font-mono text-[10px] ml-1">{{ r.code }}</span>
            </td>
            <td class="px-2 text-center text-muted">{{ r.industry || '—' }}</td>
            <td class="px-2 text-center font-mono">{{ r.close ?? '—' }}</td>
            <td class="px-2 text-center font-mono" :class="cls(r.chg_pct)">{{ fmt(r.chg_pct) }}</td>
            <td class="px-2 text-center font-mono" :class="cls(r.ret5)">{{ fmt(r.ret5) }}</td>
            <td class="px-2 text-center font-mono" :class="cls(r.ret20)">{{ fmt(r.ret20) }}</td>
            <td class="px-2 text-center font-mono text-muted">{{ r.dist_from_60d_high != null ? r.dist_from_60d_high.toFixed(0) + '%' : '—' }}</td>
            <td class="px-2 text-center font-mono">{{ r.score_total ?? '—' }}</td>
            <td class="px-2 text-center font-mono text-muted">{{ r.rank_pos ?? '—' }}</td>
            <!-- ★ 2026-10-26（用户："主力阶段现在是英文"）：走全站唯一映射 `phaseCn`
                 （优先后端 phase_cn，缺失回退 displayMeta.PHASE_CN） -->
            <td class="px-2 text-center">{{ phaseCn(r.phase) || '—' }}</td>
            <td class="px-2 text-center font-mono" :class="pct(r.flow5_amt_yi)">
              {{ r.flow5_amt_yi != null ? r.flow5_amt_yi.toFixed(1) : '—' }}</td>
            <td class="px-2 text-center text-muted">{{ r.gate_ready != null ? r.gate_ready + '/3' : '—' }}</td>
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
    <!-- 分页 -->
    <div class="flex items-center gap-3 mt-3 text-xs" v-if="total > f.size">
      <button @click="page--" :disabled="page <= 0" class="px-2 py-1 rounded bg-white/5 disabled:opacity-40">上一页</button>
      <span class="text-muted">{{ page + 1 }} / {{ Math.ceil(total / f.size) }}</span>
      <button @click="page++" :disabled="(page + 1) * f.size >= total" class="px-2 py-1 rounded bg-white/5 disabled:opacity-40">下一页</button>
    </div>
    <div class="mt-3 text-[10px] text-muted leading-relaxed">
      徽章口径：评分〔预登记 FAIL 2026-10-11，禁作买入依据〕· 龙虎榜回避〔PASS-回避侧〕· 形态〔预登记中〕· 板块联动〔影子期 PARTIAL〕· 防御组〔H2' 影子中〕· 闸门〔状态展示非信号〕。
      本表 = 事实组合筛选，不含任何加权综合分（加权=新信号须预登记）。
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, onMounted, watch } from 'vue'
// ★ 2026-10-26（审查 P2）：走统一 http 封装（鉴权头/统一错误拦截），不再自建 axios
import { getStockFactsWide } from '../api'
// 主力阶段英文枚举 → 中文（唯一映射源后端 phases.PHASE_CN，前端兜底表在 displayMeta）
import { phaseCn } from '../composables/displayMeta'

const rows = ref([])
const total = ref(0)
const dataDate = ref('')
const loading = ref(false)
const err = ref('')
const page = ref(0)
const f = reactive({ phase: '', defensive: false, lhbRisk: false, pattern: false,
                     sectorStrong: false, order: 'rank_pos', size: 100 })

const DESC = { ret5: 1, ret20: 1, flow5_amt_yi: 1, turnover_pct: 1 }

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
function pct(v) {
  if (v == null) return 'text-muted'
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return 'text-muted'
  return n > 0 ? 'text-rise' : 'text-fall'
}
</script>
