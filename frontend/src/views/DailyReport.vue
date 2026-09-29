<template>
  <div class="fade-in">
    <!-- 头部 -->
    <div class="bg-card border border-border rounded-lg p-4 mb-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 class="text-lg font-bold text-gray-100">
            {{ mode === 'week' ? '周复盘' : 'A股大盘日报' }}</h1>
          <p class="text-xs text-muted mt-0.5">
            <template v-if="mode === 'week'">
              按周回顾：本周状态轨迹 + 执行一致性（含放弃理由）+ 系统说了什么 + 下周日历。
              口径：窗口为**自然日**闭区间；regime 只含已落库交易日（缺口不插值）。
            </template>
            <template v-else>
              每个交易日 16:20 自动生成：外围环境 + 指数/个股结构 + 北向资金 + 系统状态 + 持仓 + AI 解读。
            </template>
          </p>
        </div>
        <div class="flex items-center gap-2">
          <!-- ★★ 2026-09-29（P2）：日报 / 周复盘 切换 —— 复盘层原只有**当日闭环**，缺周期视角。
               "周日晚上看周度"是投资纪律的通行习惯（用户框架："复盘要区分逻辑错/时机错/执行错"，
               日度做不到，周度才可以）。两模式共用本页 = 不必新建路由与导航项。 -->
          <div class="flex rounded border border-border overflow-hidden">
            <button v-for="t in TABS" :key="'tb' + t.k" @click="switchMode(t.k)"
              class="px-2.5 py-1.5 text-[11px] transition-colors"
              :class="mode === t.k ? 'bg-accent/20 text-accent' : 'text-muted hover:text-gray-200'">
              {{ t.label }}</button>
          </div>
          <button @click="reload" :disabled="loading || weekLoading"
            class="px-3 py-1.5 rounded text-xs border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            {{ (loading || weekLoading) ? '加载中…' : '↻ 刷新' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ★★ 2026-09-29（P2）：**周复盘**（`/api/report/weekly` 一次取全，纯只读聚合）。
         五块：① 状态轨迹 ② 执行一致性 ③ 系统提示 ④ 日报索引 ⑤ 下周日历。
         ⚠️ 口径边界全写在每块下方（窗口=自然日闭区间 / regime 缺口不补 0 /
            「已推送」才进执行率分母 / push_log 不代表企微已送达）。 -->
    <div v-if="mode === 'week'" class="space-y-4">
      <div v-if="weekLoading" class="bg-card border border-border rounded-lg p-8 text-center">
        <div class="loading-spinner mx-auto mb-3"></div>
        <p class="text-xs text-muted">加载周复盘…</p>
      </div>
      <div v-else-if="weekErr" class="bg-card border border-border rounded-lg p-8 text-center text-sm text-fall">
        {{ weekErr }}
      </div>
      <template v-else-if="week">
        <!-- 窗口 -->
        <div class="bg-card border border-border rounded-lg p-3 flex items-center gap-3 flex-wrap">
          <span class="text-xs text-muted">窗口</span>
          <b class="text-sm font-mono text-gray-200">{{ week.window?.start }} ~ {{ week.window?.end }}</b>
          <span class="text-[11px] text-muted">（{{ week.window?.days }} 个自然日 · 闭区间）</span>
          <div class="flex rounded border border-border overflow-hidden ml-auto">
            <button v-for="d in WEEK_OPTS" :key="'wo' + d" @click="loadWeek(d)"
              class="px-2 py-1 text-[11px] transition-colors"
              :class="weekDays === d ? 'bg-accent/20 text-accent' : 'text-muted hover:text-gray-200'">
              近 {{ d }} 天</button>
          </div>
        </div>

        <!-- ★★ 2026-09-29（P0 基准双口径）：**组合绩效** —— 复盘最该先看的数（此前整块缺失）。
             近 5 个交易日净值变化 + 三口径（绝对 / 对沪深300 / 对中证1000 超额）。
             口径：按当前持仓回算（未考虑窗口内加减仓）；中证1000 更贴近中小盘持仓风格；
             「跑赢基准」≠「赚钱」。数据 = `portfolio_drawdown`（唯一实现），失败静默不渲染。 -->
        <div v-if="perf?.available" class="bg-card border border-border rounded-lg p-4">
          <h2 class="text-sm font-bold text-gray-100 mb-2">本周绩效
            <span class="text-[10px] text-muted font-normal">
              （{{ perf.window?.start }} ~ {{ perf.window?.end }}<template
                v-if="perf.window?.degraded"> · 建仓以来仅 {{ perf.window?.days }} 个交易日</template>）</span></h2>
          <p class="text-xs text-gray-300 leading-relaxed">{{ perf.sentence }}</p>
          <div class="grid grid-cols-1 sm:grid-cols-3 gap-2 mt-3">
            <div class="border border-border/60 rounded px-2 py-1.5">
              <div class="text-[10px] text-muted">组合（{{ perf.window?.degraded
                ? '建仓以来' : '近 ' + (perf.window?.days ?? 5) + ' 交易日' }}）</div>
              <div class="text-base font-mono font-semibold"
                   :class="pctCls(perf.portfolio_ret)">{{ fmtPct(perf.portfolio_ret) }}</div>
            </div>
            <div class="border border-border/60 rounded px-2 py-1.5">
              <div class="text-[10px] text-muted">对沪深300 超额</div>
              <div class="text-base font-mono font-semibold"
                   :class="pctCls(perf.excess_hs300)">{{ fmtPct(perf.excess_hs300) }}</div>
            </div>
            <div class="border border-border/60 rounded px-2 py-1.5">
              <div class="text-[10px] text-muted">对中证1000 超额
                <span class="text-[9px] text-muted/70">（风格参照）</span></div>
              <div class="text-base font-mono font-semibold"
                   :class="pctCls(perf.excess_zz1000)">{{ fmtPct(perf.excess_zz1000) }}</div>
            </div>
          </div>
          <div class="text-[10px] text-muted mt-2 leading-relaxed"
               :title="[perf.note, perf.dd_note].filter(Boolean).join('\n')">
            {{ perf.dd_note }}
          </div>
        </div>

        <!-- ★★ 2026-09-30（P1）：**本周板块** —— 复盘本该回答"这一周市场发生了什么"，
             此前只有 regime 与执行一致性，整块"哪些板块在动"是缺的。
             数据 = `app.sector_momentum`（zzshare 104 粗分，**与日报同模块同口径**）；
             口径限制：**不关联个股/评分**（zzshare 104 粗分 与 新浪 49 类 实测仅 4 个同名，
             强行按名匹配会给出误导性的"板块内 0 只上榜"，缺失 ≠ 0）；
             与日报的分工：日报看当日/3 日（今天谁在动），本块看 **5 日**（本周谁在动）。 -->
        <div v-if="sec.sectors?.available" class="bg-card border border-border rounded-lg p-4">
          <h2 class="text-sm font-bold text-gray-100 mb-2">本周板块
            <span class="text-[10px] text-muted font-normal">
              （zzshare {{ sec.sectors.sectors }} 个粗分板块 · 近 5 交易日 · 截至
              {{ sec.sectors.as_of }}）</span></h2>
          <p class="text-xs text-gray-300 leading-relaxed">{{ sec.sectors.sentence }}</p>
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 mt-3">
            <div>
              <div class="text-[10px] text-muted mb-1">5 日最强</div>
              <div v-for="r in (sec.sectors.top || []).slice(0, 5)" :key="'t' + r.industry"
                   class="flex items-center justify-between text-[11px] border-b border-border/30 py-0.5">
                <span class="truncate">{{ r.industry }}</span>
                <span class="font-mono text-red-400">{{ fmtPct(r.ret5) }}</span>
              </div>
            </div>
            <div>
              <div class="text-[10px] text-muted mb-1">5 日最弱</div>
              <div v-for="r in (sec.sectors.bottom || []).slice(0, 5)" :key="'b' + r.industry"
                   class="flex items-center justify-between text-[11px] border-b border-border/30 py-0.5">
                <span class="truncate">{{ r.industry }}</span>
                <span class="font-mono text-emerald-400">{{ fmtPct(r.ret5) }}</span>
              </div>
            </div>
          </div>
          <div v-if="(sec.sectors.moves || []).length"
               class="mt-2 text-[10px] text-amber-400/90 leading-relaxed">
            新进入异动（连续命中只报首日）：
            {{ (sec.sectors.moves || []).map(m => m.industry + '（' + m.reason + '）').join('、') }}
          </div>
          <div class="text-[10px] text-muted mt-2 leading-relaxed" :title="sec.sectors.note">
            {{ sec.sectors.note }}
          </div>
        </div>

        <!-- ① 状态轨迹 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <h2 class="text-sm font-bold text-gray-100 mb-2">① 本周市场状态轨迹</h2>
          <p class="text-xs text-gray-300 leading-relaxed">{{ sec.regime?.sentence }}</p>
          <div class="flex flex-wrap gap-1.5 mt-3">
            <div v-for="p in regimePts" :key="'rg' + p.date"
                 class="px-2 py-1 rounded border text-[11px]" :class="stateCls(p.state)">
              <span class="font-mono">{{ p.date.slice(5) }}</span>
              <b class="ml-1">{{ p.state_cn }}</b>
              <span class="ml-1 text-[10px] opacity-80 font-mono">{{ p.score ?? '—' }}</span>
            </div>
          </div>
          <div class="text-[10px] text-muted mt-2">
            只含**已落库**的交易日（缺口不插值、不补 0）；数字为 regime_score（越负越防御）。
          </div>
        </div>

        <!-- ② 执行一致性 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <h2 class="text-sm font-bold text-gray-100 mb-2">② 本周执行一致性</h2>
          <p class="text-xs text-gray-300 leading-relaxed">{{ sec.execution?.sentence }}</p>
          <div class="grid grid-cols-2 sm:grid-cols-4 gap-2 mt-3">
            <div v-for="k in kpiTiles" :key="'kt' + k.label"
                 class="border border-border/60 rounded px-2 py-1.5">
              <div class="text-[10px] text-muted">{{ k.label }}</div>
              <div class="text-base font-mono font-semibold" :class="k.cls">{{ k.value }}</div>
            </div>
          </div>
          <div v-if="alerts.length" class="mt-3 border-t border-border/40 pt-2">
            <div class="flex text-[10px] text-muted mb-1">
              <span class="flex-1">按规则</span>
              <span class="w-12 text-right">命中</span>
              <span class="w-14 text-right">已推送</span>
              <span class="w-10 text-right">执行</span>
              <span class="w-10 text-right">放弃</span>
            </div>
            <div v-for="a in alerts" :key="'al' + a.rule_id"
                 class="flex items-center text-[11px] py-0.5">
              <span class="flex-1 truncate text-gray-300" :title="a.rule_id + ' · ' + a.label">
                {{ a.label || a.rule_id }}</span>
              <span class="w-12 text-right font-mono text-muted">{{ a.n }}</span>
              <span class="w-14 text-right font-mono"
                    :class="a.n_pushed ? 'text-accent' : 'text-muted/50'">{{ a.n_pushed }}</span>
              <span class="w-10 text-right font-mono text-emerald-400">{{ a.yes }}</span>
              <span class="w-10 text-right font-mono text-amber-400">{{ a.no }}</span>
            </div>
            <div class="text-[10px] text-muted mt-1.5">
              「已推送」才进执行率分母 —— 项目中多数卡默认静默（push=false），
              故「命中数 &gt; 已推送数」是正常的，不是漏推。
            </div>
          </div>
          <div v-if="drops.length" class="mt-3 border-t border-border/40 pt-2">
            <div class="text-[11px] text-muted mb-1">放弃理由（高频 = 最易失守的纪律点）</div>
            <div v-for="(r, i) in drops" :key="'dr' + i"
                 class="border-l-2 border-amber-500/40 pl-2 py-0.5 mb-1">
              <div class="text-[10px] text-muted">
                <span class="font-mono">{{ r.alert_date }}</span> · {{ r.label }}
                <span v-if="r.name"> · {{ r.name }}</span>
              </div>
              <div class="text-[11px] text-gray-300">{{ r.abandon_reason }}</div>
            </div>
          </div>
          <div v-else class="text-[10px] text-muted mt-2">本窗口没有放弃记录。</div>
        </div>

        <!-- ③ 系统提示 + ④ 日报索引 -->
        <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div class="bg-card border border-border rounded-lg p-4">
            <h2 class="text-sm font-bold text-gray-100 mb-2">
              ③ 本周系统提示（{{ sec.system?.total || 0 }} 条）</h2>
            <div class="flex flex-wrap gap-1.5">
              <span v-for="c in (sec.system?.by_category || [])" :key="'pc' + c.category"
                    class="px-1.5 py-0.5 rounded text-[11px] bg-white/5 border border-border text-gray-300">
                {{ catCn(c.category) }} {{ c.n }}</span>
              <span v-if="!(sec.system?.by_category || []).length" class="text-[11px] text-muted">—</span>
            </div>
            <div class="text-[10px] text-muted mt-2">
              {{ sec.system?.note }}。完整时间线见「评分榜 → 今日雷达」。
            </div>
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <h2 class="text-sm font-bold text-gray-100 mb-2">
              ④ 本周日报（{{ (sec.reports || []).length }} 篇）</h2>
            <div class="flex flex-wrap gap-1.5">
              <button v-for="r in (sec.reports || [])" :key="'rp' + r.date" @click="openReport(r.date)"
                class="px-2 py-1 rounded text-[11px] border border-border text-muted hover:text-gray-200 hover:border-accent/30 transition-colors">
                {{ r.date }}<span class="text-[10px] ml-1">{{ r.len }}字</span></button>
              <span v-if="!(sec.reports || []).length" class="text-[11px] text-muted">—</span>
            </div>
            <div class="text-[10px] text-muted mt-2">点日期跳回「日报」模式看全文。</div>
          </div>
        </div>

        <!-- ⑤ 下周日历 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <h2 class="text-sm font-bold text-gray-100 mb-2">
            ⑤ 下周关注（未来 7 天 · 核心 {{ sec.next_week?.core_count || 0 }} 条）</h2>
          <div v-if="!nextItems.length" class="text-xs text-muted">
            未来 7 天无核心事件（或日历源暂不可用）。</div>
          <div v-else class="space-y-0.5">
            <div v-for="(x, i) in nextItems" :key="'nw' + i" class="flex items-baseline gap-2 text-[11px]">
              <span class="font-mono text-muted w-[72px] shrink-0">{{ x.date }}</span>
              <span class="text-gray-300 shrink-0">{{ x.country || '' }}</span>
              <span class="text-gray-200">{{ x.title }}</span>
              <span v-if="x.star" class="text-amber-400 text-[10px] shrink-0">{{ '★'.repeat(x.star) }}</span>
              <span v-if="x.impact" class="text-[10px] text-muted truncate">— {{ x.impact }}</span>
            </div>
          </div>
          <div class="text-[10px] text-muted mt-2">
            口径：核心指标词 / 5 星 / 4 星非讲话类（官员讲话已过滤）；数据源为已落库财经日历。
          </div>
        </div>

        <!-- 口径边界 -->
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="text-[10px] text-muted leading-relaxed">
            <b class="text-gray-400">口径与边界</b>：{{ (week.notes || []).join('；') }}。
            本页为纯只读聚合（零外部网络请求），结论由规则生成、非 LLM 判断。
          </div>
        </div>
      </template>
    </div>

    <div v-else class="flex gap-4 items-start">
      <!-- 左侧日期列表 -->
      <div class="w-48 shrink-0 space-y-2">
        <div v-if="listLoading" class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
          加载中…
        </div>
        <div v-else-if="listError" class="bg-card border border-border rounded-lg p-4 text-xs text-fall">
          {{ listError }}
        </div>
        <div v-else-if="!reportList.length" class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
          暂无日报（交易日 16:20 自动生成）
        </div>
        <button v-for="r in reportList" :key="r.date" @click="select(r.date)"
          class="w-full text-left px-3 py-2 rounded border transition-colors"
          :class="currentDate === r.date
            ? 'bg-accent/15 border-accent/30 text-accent'
            : 'bg-card border-border text-muted hover:text-gray-200 hover:border-accent/30'">
          <div class="text-xs font-medium">{{ r.date }}</div>
          <div class="text-[10px] text-muted mt-0.5">{{ fmtTime(r.created_at) }} · {{ r.len }} 字</div>
        </button>
      </div>

      <!-- 右侧正文 -->
      <div class="flex-1 min-w-0">
        <!-- 决策简报（PLAN_TRADER_WORKFLOW Phase 1） -->
        <div v-if="briefHtml" class="bg-card border border-accent/30 rounded-lg p-4 mb-3">
          <div class="flex items-center justify-between mb-2">
            <h2 class="text-sm font-bold text-accent">🎯 交易员决策简报
              <span class="text-[10px] text-muted font-normal">{{ phaseLabel }} · {{ briefDate }}</span>
            </h2>
            <button @click="loadBrief(true)" :disabled="briefLoading"
              class="px-2 py-1 rounded text-[10px] border border-border text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
              {{ briefLoading ? '生成中…' : '↻ 重新生成' }}
            </button>
          </div>
          <div class="md-body text-xs" v-html="briefHtml"></div>
        </div>
        <div v-if="loading" class="bg-card border border-border rounded-lg p-8 text-center">
          <div class="loading-spinner mx-auto mb-3"></div>
          <p class="text-xs text-muted">加载日报…</p>
        </div>
        <div v-else-if="error" class="bg-card border border-border rounded-lg p-8 text-center text-sm text-fall">
          {{ error }}
        </div>
        <div v-else-if="html" class="bg-card border border-border rounded-lg p-6">
          <div class="md-body" v-html="html"></div>
        </div>
        <div v-else class="bg-card border border-border rounded-lg p-8 text-center text-sm text-muted">
          选择左侧日期查看日报
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import MarkdownIt from 'markdown-it'
import { getDailyReportList, getDailyReport, getWeeklyReview } from '../api'
import { getTraderBrief } from '../api'

// ★★ 2026-09-29（P2）：模式切换（日报 / 周复盘）。周复盘数据来自 `/api/report/weekly`
//   （一次取全：regime 轨迹 + 执行一致性 + 系统提示/日报索引 + 下周日历）。
//   口径与四条边界见后端 `app/weekly_review.py` 文件头。
const TABS = [{ k: 'daily', label: '日报' }, { k: 'week', label: '周复盘' }]
const mode = ref('daily')
const WEEK_OPTS = [7, 14, 30]
const weekDays = ref(7)
const week = ref(null)
const weekLoading = ref(false)
const weekErr = ref('')
const sec = computed(() => week.value?.section || {})
// ★ 2026-09-29（P0 基准双口径）：组合绩效块（近 5 交易日净值 + 对 300/1000 超额）
const perf = computed(() => sec.value.performance || null)
// 绩效数字格式化：+x.xx%（null → —）；配色按 A 股惯例（涨红 / 跌绿）
const fmtPct = (v) => (v == null ? '—' : (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%')
const pctCls = (v) => (v == null ? 'text-muted' : v >= 0 ? 'text-red-400' : 'text-emerald-400')
const regimePts = computed(() => sec.value.regime?.track?.points || [])
const alerts = computed(() => sec.value.execution?.alerts || [])
const drops = computed(() => sec.value.execution?.abandon_reasons || [])
const nextItems = computed(() => sec.value.next_week?.items || [])
// regime 配色沿用工作台 `regimeClass` 口径（进攻红 / 震荡琥珀 / 防御绿）
const stateCls = (s) => ({
  offensive: 'bg-red-500/10 border-red-500/30 text-red-300',
  neutral: 'bg-white/5 border-border text-gray-300',
  neutral_bearish: 'bg-amber-500/10 border-amber-500/30 text-amber-300',
  defensive: 'bg-emerald-500/10 border-emerald-500/30 text-emerald-300',
}[s] || 'bg-white/5 border-border text-muted')
const CAT_CN = { brief: '简报', risk: '风险', alert: '警示', other: '其他' }
const catCn = (c) => CAT_CN[c] || c || '其他'
// 执行率分档配色（阈值仅用于**配色**，不改任何统计口径）：≥85 良好 / ≥60 一般 / <60 偏低
const kpiTiles = computed(() => {
  const k = sec.value.execution?.kpi || {}
  const r = k.exec_rate_pct
  return [
    { label: '已推送', value: k.pushed_total ?? '—', cls: 'text-gray-200' },
    { label: '已决策', value: k.decided ?? '—', cls: 'text-gray-200' },
    {
      label: '执行率', value: r == null ? '—' : r + '%',
      cls: r == null ? 'text-muted'
        : (r >= 85 ? 'text-emerald-400' : r >= 60 ? 'text-amber-300' : 'text-amber-400'),
    },
    { label: '未响应', value: k.ignored ?? '—', cls: 'text-muted' },
  ]
})
async function loadWeek(d = null) {
  if (d) weekDays.value = d
  weekLoading.value = true
  weekErr.value = ''
  try {
    const { data } = await getWeeklyReview(weekDays.value)
    week.value = data || null
  } catch (e) {
    weekErr.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    weekLoading.value = false
  }
}
// 懒加载：切到周复盘才发请求（该接口是只读聚合，但没必要进页面就发）
function switchMode(k) {
  mode.value = k
  if (k === 'week' && !week.value) loadWeek()
}
function reload() { mode.value === 'week' ? loadWeek() : load(currentDate.value) }
function openReport(date) { mode.value = 'daily'; select(date) }

const reportList = ref([])
// 决策简报（Phase 1）
const briefHtml = ref('')
const briefLoading = ref(false)
const briefDate = ref('')
const phaseLabel = ref('')
const PHASE_LABELS = { premarket: '盘前', intraday: '盘中', postmarket: '盘后' }
async function loadBrief(refresh = false) {
  briefLoading.value = true
  try {
    const { data } = await getTraderBrief(refresh)
    if (data?.ok) {
      briefDate.value = data.date || ''
      phaseLabel.value = PHASE_LABELS[data.phase] || data.phase || ''
      briefHtml.value = mdRenderer.render(data.markdown || '')
    }
  } catch (e) { /* 简报失败不影响日报 */ }
  finally { briefLoading.value = false }
}
const listLoading = ref(false)
const listError = ref('')
const currentDate = ref('')
const html = ref('')
const loading = ref(false)
const error = ref('')

const mdRenderer = new MarkdownIt({ html: false, linkify: true, breaks: true })

function fmtTime(iso) {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch { return '' }
}

async function loadList() {
  listLoading.value = true
  listError.value = ''
  try {
    const { data } = await getDailyReportList(30)
    reportList.value = data?.data || []
    if (reportList.value.length && !currentDate.value) {
      currentDate.value = reportList.value[0].date
      await load(currentDate.value)
    }
  } catch (e) {
    listError.value = '加载列表失败：' + (e.response?.data?.detail || e.message)
  } finally {
    listLoading.value = false
  }
}

async function load(date) {
  loading.value = true
  error.value = ''
  html.value = ''
  try {
    const { data } = await getDailyReport(date)
    if (data?.markdown) {
      html.value = mdRenderer.render(data.markdown)
    } else {
      error.value = '该日期暂无日报'
    }
  } catch (e) {
    error.value = '加载失败：' + (e.response?.data?.detail || e.message)
  } finally {
    loading.value = false
  }
}

function select(date) {
  currentDate.value = date
  load(date)
}

onMounted(loadList)
loadBrief()
</script>

<style scoped>
/* 日报 markdown 渲染样式（含表格，markdown-it 输出） */
.md-body {
  color: #d1d5db;
  font-size: 0.8rem;
  line-height: 1.75;
  word-break: break-word;
}
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3) {
  color: #e5e7eb;
  font-weight: 600;
  margin: 0.75rem 0 0.4rem;
}
.md-body :deep(h1) { font-size: 1.05rem; border-bottom: 1px solid #374151; padding-bottom: 0.4rem; }
.md-body :deep(h2) { font-size: 0.95rem; }
.md-body :deep(h3) { font-size: 0.88rem; }
.md-body :deep(p) { margin: 0.35rem 0; }
.md-body :deep(ul),
.md-body :deep(ol) { margin: 0.35rem 0; padding-left: 1.25rem; }
.md-body :deep(ul) { list-style: disc; }
.md-body :deep(ol) { list-style: decimal; }
.md-body :deep(li) { margin: 0.15rem 0; }
.md-body :deep(strong) { color: #f3f4f6; font-weight: 600; }
.md-body :deep(em) { color: #9ca3af; }
.md-body :deep(blockquote) {
  border-left: 3px solid #4b5563;
  padding-left: 0.75rem;
  margin: 0.4rem 0;
  color: #9ca3af;
}
.md-body :deep(code) {
  background: rgba(255, 255, 255, 0.08);
  padding: 0.1rem 0.3rem;
  border-radius: 0.25rem;
  font-size: 0.75rem;
}
.md-body :deep(hr) { border-color: #374151; margin: 0.75rem 0; }
.md-body :deep(a) { color: #60a5fa; }
.md-body :deep(table) {
  border-collapse: collapse;
  width: 100%;
  margin: 0.5rem 0;
  font-size: 0.78rem;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #374151;
  padding: 0.35rem 0.55rem;
  text-align: left;
}
.md-body :deep(th) { background: rgba(255, 255, 255, 0.05); color: #e5e7eb; }
.md-body :deep(tr:nth-child(even)) { background: rgba(255, 255, 255, 0.02); }
</style>
