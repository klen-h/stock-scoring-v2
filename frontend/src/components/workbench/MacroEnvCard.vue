<template>
  <div class="bg-card border border-border rounded-lg p-4">
    <div class="flex items-center justify-between mb-2">
      <div class="text-sm font-semibold">宏观与环境（独立信号，不进个股评分）</div>
      <span v-if="macro && macro.locked" class="text-[10px] text-accent">
        今日锁定 · 生成于 {{ hhmm(macro.generated_at) }}
      </span>
    </div>
    <div v-if="macroErr" class="text-muted text-xs">—（加载失败：{{ macroErr }}）</div>
    <div v-else-if="!macro" class="text-muted text-xs">加载中…</div>
    <template v-else>
      <!-- 单行三段式：宏观方向 | 市场环境 | 事件诊断（★ 2026-09-25 用户要求同一行，
           严格单行不换行：长文本一律 truncate，悬停 title 看全文）
           ★ 2026-09-29：上述是**默认（内联卡）**口径；`full=true`（顶栏浮层）改成
             "可换行 + 不截断"= **展示全部**（见 `full` prop 注释）。 -->
      <div :class="full
              ? 'flex flex-wrap items-center gap-x-3 gap-y-1.5 min-w-0'
              : 'flex items-center gap-3 min-w-0'">
        <div class="flex items-center gap-2.5 flex-shrink-0">
          <span class="text-4xl font-bold font-mono leading-none"
                :class="dirColor(macro.direction?.level)">
            {{ macro.direction?.score ?? '—' }}
          </span>
          <div class="min-w-0">
            <div class="text-xs font-semibold whitespace-nowrap"
                 :class="dirColor(macro.direction?.level)">
              宏观方向 · {{ macro.direction?.level || '—' }}
            </div>
            <!-- ⚠️ `full` 下仍给一个**宽度上限**（360px）而不是完全放开：这句的父级是
                 `flex-shrink-0`（不收缩），若让它按 max-content 撑开，整行会再度超出面板
                 ⇒ 又不是"展示全部"了。给上限 ⇒ 它在 2~3 行内**完整换行显示**（不截断）。 -->
            <div class="text-[11px] text-muted"
                 :class="full ? 'max-w-[360px]' : 'truncate max-w-[240px]'"
                 :title="macro.direction?.advisory">{{ macro.direction?.advisory || '' }}</div>
          </div>
        </div>
        <div class="w-px h-12 bg-border flex-shrink-0"></div>
        <div class="flex items-center gap-2.5 flex-shrink-0">
          <span class="text-4xl font-bold font-mono leading-none"
                :class="levelColor(temperature?.level || '')">{{ temperature?.temperature ?? '—' }}</span>
          <div>
            <div class="text-xs font-semibold whitespace-nowrap"
                 :class="levelColor(temperature?.level || '')">市场环境 · {{ temperature?.level || '—' }}</div>
            <div class="text-[11px] text-muted whitespace-nowrap">0~100，越高越亢奋</div>
          </div>
        </div>
        <div class="w-px h-12 bg-border flex-shrink-0"></div>
        <!-- ⚠️ `full` 时给 `min-w-[280px]`：让这一段**够窄就整段换行**（而不是把叙事压成一条），
             否则 flex 会优先压缩它、truncate 兜底 ⇒ "展示全部"就落空了。 -->
        <div :class="full ? 'flex items-center gap-3 flex-1 min-w-[280px]'
                          : 'flex items-center gap-3 flex-1 min-w-0'">
          <div class="flex-shrink-0">
            <div class="text-sm font-bold whitespace-nowrap"
                 :class="flashDiag?.correlation_diagnosis?.correlation_state === 'D状态'
                         ? 'text-amber-400' : 'text-gray-100'">
              {{ flashDiag?.correlation_diagnosis?.correlation_state || '无法判断' }}
            </div>
            <div class="text-[10px] text-muted max-w-[200px] truncate"
                 :title="`事件诊断 · ${fullDiagType(flashDiag?.correlation_diagnosis?.d_state_type)}`">
              事件诊断 · {{ shortDiagType(flashDiag?.correlation_diagnosis?.d_state_type) }}</div>
          </div>
          <!-- ★ 2026-09-25 用户要求：文字部分同一 div 上下布局（叙事上 / 仓位·详情下） -->
          <div class="flex-1 min-w-0 text-xs">
            <div class="text-gray-300"
                 :class="full ? '' : 'truncate'"
                 :title="flashDiag?.dominant_narrative?.narrative || flashDiag?.market_mood || ''">
              {{ flashDiag?.dominant_narrative?.narrative || flashDiag?.market_mood || '—' }}
            </div>
            <div class="text-muted" :class="full ? '' : 'whitespace-nowrap'">
              仓位 <b class="text-accent">{{ flashDiag?.daily_strategy?.overall_position || '—' }}</b>
              <!-- ★ 2026-09-25 用户："仓位建议『轻仓观望』后面补上具体上限数字" ——
                   LLM 那句是**定性**的，这里补上仓位引擎算出的**定量上限**
                   （`/api/user/position-sizing` 的 total_limit_pct；个股页已在用，只补上）。
                   ⚠️ 用 `!= null` 而不是 `||`：**0 是合法上限**（空仓），不能被吞掉。 -->
              <template v-if="sizing && sizing.total_limit_pct != null">
                · 上限 <b class="text-accent font-mono">{{ sizing.total_limit_pct }}%</b>
              </template>
              <!-- ★ 2026-09-25 用户需求 2（框架 A6「周回撤熔断」）：
                   组合近 5 个交易日**持仓市值回撤** ≥5%（对齐 G2 阈值）⇒ 后端已自动把
                   总上限降半仓（见 total_limit_pct），此处只显示状态与数值，让人知道"为什么降了"。
                   悬停看完整口径/曲线/近似警告（⚠️ 按当前持仓回算，有交易会失真）。 -->
              <template v-if="pdd && pdd.available">
                · 周回撤 <b class="font-mono cursor-help"
                            :class="pdd.triggered ? 'text-red-400' : 'text-gray-300'"
                            :title="pddTitle">{{ pdd.drawdown_pct }}%</b>
                <span v-if="pdd.triggered" class="text-red-400">（已降仓）</span>
              </template>
              <template v-else-if="pdd && pdd.note">
                · <span class="text-muted cursor-help" :title="pdd.note">周回撤 —</span>
              </template>
              · <router-link target="_blank" to="/monitor" class="text-accent hover:underline">详情</router-link>
            </div>
          </div>
        </div>
      </div>
      <!-- ★ 2026-09-25 用户："『负相关（弱）』这个标题用户看不懂，改成『压制因素』"
           + "巴菲特指标 94（过热）与温度 28.1（偏冷）并存，正是市场分歧明显的体现，
           建议做成多空两栏对照，而不是埋在长句里"。
           ⚠️ 说明：`correlation_state`（正相关/负相关/D状态）指的是**油金相关性**，
           与"对 A 股的压制因素"不是一回事 ⇒ **不改它的语义**（改了会误导）。
           改成给多空标签**加标题 + 左右两栏**：左＝支撑因素（利多）／右＝压制因素（利空）
           ⇒ 一次同时满足"看得懂"与"两栏对照"。 -->
      <div class="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1 mt-2.5">
        <div class="flex items-start gap-1.5 min-w-0">
          <span class="text-[11px] text-emerald-400 flex-shrink-0 mt-0.5 w-[52px]">支撑因素</span>
          <div class="flex flex-wrap gap-1 min-w-0">
            <span v-for="t in (macro.tags_bull || [])" :key="'b' + t"
                  class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">{{ t }}</span>
            <span v-if="!(macro.tags_bull || []).length" class="text-[11px] text-muted">—（无）</span>
          </div>
        </div>
        <div class="flex items-start gap-1.5 min-w-0">
          <span class="text-[11px] text-red-400 flex-shrink-0 mt-0.5 w-[52px]">压制因素</span>
          <div class="flex flex-wrap gap-1 min-w-0">
            <span v-for="t in (macro.tags_bear || [])" :key="'s' + t"
                  class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20">{{ t }}</span>
            <span v-if="!(macro.tags_bear || []).length" class="text-[11px] text-muted">—（无）</span>
          </div>
        </div>
      </div>
      <!-- ★★★ 2026-10-28（用户："缺少解释性输出" + "原油/美债都涨了为什么还强多"）：
           把**全部规则（含未触发）**摊开，并给出每组对总分的**实际贡献（分）**。
           为什么必须有：只看"支撑/压制因素"两个列表时，用户看到的永远是
           **系统算了什么**，看不到**系统没算什么、以及为什么** ——
           这正是"我明明看到利空，结论却是强多"这类困惑的唯一出口。
           典型（当天真实）：布伦特 +2.36% 落在 (2,3] 的**无信号区间** ⇒ 不计分；
           美债 10Y 单日 +3.6bp **未达** 6bp 阈值 ⇒ 不计分。不写出来就是黑箱。
           ⚠️ 默认折叠：这是"要追问时才看"的信息，不该占常规版面。 -->
      <details v-if="(macro.rules_detail || []).length" class="mt-1.5">
        <summary class="text-[10px] text-muted cursor-pointer hover:text-accent select-none">
          依据明细 · {{ (macro.rules_detail || []).length }} 条规则
          <span class="text-muted/70">（触发 {{ triggeredCount }} 条，其余为未达阈值/无信号）</span>
        </summary>
        <!-- ① 分组贡献：先回答"分数是谁推上去的"（归一值看不出大小，所以算成分） -->
        <div class="flex flex-wrap items-center gap-x-2.5 gap-y-0.5 mt-1 text-[10px] font-mono">
          <span v-for="(v, g) in (macro.direction?.group_contrib || {})" :key="g"
                :class="v > 0 ? 'text-emerald-400' : (v < 0 ? 'text-red-400' : 'text-muted')"
                :title="`${groupLabel(g)}组：权重 ${groupWeight(g)}，组得分 ${macro.direction?.group_scores?.[g]}，对总分贡献 ${v} 分`">
            {{ groupLabel(g) }} {{ fmtSigned(v) }}
          </span>
          <span class="text-muted">= 总分 {{ macro.direction?.score }}</span>
        </div>
        <!-- ② 逐条：触发的排前面，未触发的灰显并附**为什么没触发** -->
        <div class="mt-1 space-y-0.5">
          <div v-for="r in sortedRules" :key="r.id" class="text-[10px] leading-snug">
            <span class="font-mono"
                  :class="r.hit ? (r.direction > 0 ? 'text-emerald-400' : 'text-red-400') : 'text-muted/50'">
              {{ r.hit ? (r.direction > 0 ? '＋' : '－') : '·' }}
            </span>
            <span :class="r.hit ? 'text-gray-200' : 'text-muted/80'">{{ r.id }}</span>
            <span class="text-muted"> {{ r.metric }} = {{ r.value ?? '—' }}</span>
            <span v-if="r.hit" class="ml-1"
                  :class="r.direction > 0 ? 'text-emerald-400' : 'text-red-400'">{{ r.tag }}</span>
            <!-- ★ 2026-10-28（用户："原油涨了却没算进去"）：未触发但**有说法**的规则
                 （区间型规则的盲区，如油价 +2.36% 落在 (2,3]）也要显示 tag ——
                 否则"系统看到了但不计分"在界面上凭空消失，用户只能怀疑系统坏了。 -->
            <span v-else-if="r.tag" class="ml-1 text-muted/80">{{ r.tag }}</span>
            <span v-if="!r.hit && r.note" class="text-muted/60"> · {{ r.note }}</span>
          </div>
        </div>
      </details>
      <!-- ★ 2026-09-25：情绪温度计子项的**过热 vs 过冷**两栏对照 —— 直接回答
           "为什么巴菲特指标过热(94)而市场温度偏冷(28)" = 市场分歧明显的可视化。
           数据来自 `/macro/snapshot.sentiment`（金十 12 子项，后端已按得分降序）。
           ⚠️ peek 缓存 ⇒ 冷缓存时为 null ⇒ 整块不渲染（不假装有数据）。 -->
      <div v-if="sentiment" class="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1 mt-2 pt-2 border-t border-border/40">
        <div class="flex items-start gap-1.5 min-w-0">
          <span class="text-[11px] text-red-400 flex-shrink-0 mt-0.5 w-[52px] cursor-help"
                title="情绪温度计里得分高的子项（越热越需要警惕追高）">过热项</span>
          <div class="flex flex-wrap gap-1 min-w-0">
            <span v-for="s in hotSubs" :key="'h' + s.key"
                  class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20"
                  :title="`${s.name}：值 ${s.value ?? '—'}，热度分 ${s.score}`">{{ s.name }} {{ s.value ?? '' }}({{ s.score }})</span>
            <span v-if="!hotSubs.length" class="text-[11px] text-muted">—（无）</span>
          </div>
        </div>
        <div class="flex items-start gap-1.5 min-w-0">
          <span class="text-[11px] text-emerald-400 flex-shrink-0 mt-0.5 w-[52px] cursor-help"
                title="得分低的子项（越冷越可能是低位机会）">过冷项</span>
          <div class="flex flex-wrap gap-1 min-w-0">
            <span v-for="s in coldSubs" :key="'c' + s.key"
                  class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
                  :title="`${s.name}：值 ${s.value ?? '—'}，热度分 ${s.score}`">{{ s.name }} {{ s.value ?? '' }}({{ s.score }})</span>
            <span v-if="!coldSubs.length" class="text-[11px] text-muted">—（无）</span>
          </div>
        </div>
        <div class="md:col-span-2 text-[10px] text-muted">
          情绪温度计 {{ sentiment.score }}分/{{ sentiment.zone }}区（{{ sentiment.date }}）·
          过热与过冷**同时出现**＝市场分歧明显；两项都在 80/20 之外时说明方向一致
        </div>
      </div>
      <!-- ★★ 2026-09-29（P2 用户："继续做 IM 基差"）：**股指期货基差**（IF/IH/IC/IM
           × 当月合约）—— 北向实时额停披后现存少数的「内资情绪 / 对冲需求」温度计：
           贴水加深 = 对冲盘或看空力量占优。当月合约 = 标准口径（次月与三条边界放 hover）。
           ⚠️ **中性色**：贴水深浅不直接等于涨/跌（用红绿会被误读成行情涨跌）；
           ⚠️ **不设阈值**：须先积累历史基线，当前只做展示 + 四品种横向对照；
           ⚠️ 纯展示层、不进决策链；失败静默（null ⇒ 整块不渲染）。 -->
      <div v-if="basisRows.length" class="mt-2 pt-2 border-t border-border/40">
        <div class="flex items-baseline gap-2 text-[11px] text-muted mb-1">
          <span>股指期货基差</span>
          <span class="text-[10px] text-muted/70">{{ basisNote }}</span>
          <span class="text-[10px] text-muted/70"
                title="IM 贴水含雪球/DMA 对冲盘的结构性需求，不能干净解读为「市场情绪」">ⓘ</span>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-0.5">
          <div v-for="r in basisRows" :key="'fb' + r.product"
               class="text-[11px] min-w-0 cursor-help" :title="r.title">
            <span class="font-mono text-gray-300">{{ r.product }}</span>
            <span class="text-muted text-[10px]"> {{ r.spot_name }}</span>
            <span class="ml-1 font-mono text-gray-200">
              {{ r.label }} {{ Math.abs(r.rate).toFixed(3) }}%</span>
            <span class="ml-1 text-[10px] text-muted font-mono">
              年化 {{ r.ann == null ? '—' : (r.ann >= 0 ? '+' : '') + r.ann.toFixed(1) + '%' }}</span>
            <span class="ml-1 text-[10px] text-muted/70 font-mono">剩{{ r.days }}天</span>
          </div>
        </div>
      </div>
      <!-- ★ 2026-10-25（eypai 溯源调研）：流动性三件套（两融余额 / 中美利差 / 当月贴水率）
           —— 日批落库（app/liquidity_watch.py），正是上方基差块"须先积累历史基线"的那段历史。
           ⚠️ 配色纪律：**迷你条一律中性蓝**（水位不是涨跌）；只有两融"当日/5日净变化"
           用红绿（那是真正的方向量）。两融为 T+1 数据（margin_date 自证）。
           ⚠️ 纯观察器不进决策链；数据为日批快照，非实时（实时链路另有 /macro/panel）。 -->
      <div v-if="liq" class="mt-2 pt-2 border-t border-border/40 text-[11px]">
        <div class="flex items-baseline gap-2 text-muted mb-1">
          <span>流动性三件套</span>
          <span class="text-[10px] text-muted/70"
                title="两融为 T+1 数据（沪+深，亿元；兜底源=沪深北口径）；利差=中债10Y−美债10Y（bp）；贴水率=当月合约基差率（正=贴水）。纯观察器，不进决策链">ⓘ</span>
          <span v-if="liqDegraded" class="text-[10px] text-amber-400/80"
                :title="'日批当日缺数据源：' + liqDegraded + '（失败不落值，不填 0）'">缺:{{ liqDegraded }}</span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1">
          <div class="flex items-center gap-1.5 min-w-0 cursor-help" :title="liqMarginTitle">
            <span class="text-muted flex-shrink-0">两融</span>
            <span class="font-mono text-gray-200">{{ liqMarginWanYi }}</span>
            <span class="font-mono text-[10px]" :class="dirNumClass(liq.latest?.margin?.add_yi)">
              {{ fmtSigned(liq.latest?.margin?.add_yi) }}亿</span>
            <span class="text-[10px] text-muted font-mono">
              5日 {{ fmtSigned(liq.latest?.margin?.chg5_yi) }}亿</span>
            <span class="text-[10px] text-muted/70">({{ (liq.latest?.margin?.date || '').slice(5) }})</span>
          </div>
          <div class="flex items-center gap-1.5 min-w-0 cursor-help" :title="liqSpreadTitle">
            <span class="text-muted flex-shrink-0">中美利差</span>
            <span class="font-mono text-gray-200">{{ fmtSigned(liq.latest?.bonds?.spread_bp) }}bp</span>
            <span class="text-[10px] text-muted font-mono">
              中债 {{ liq.latest?.bonds?.cn10y ?? '—' }}% / 美债 {{ liq.latest?.bonds?.us10y ?? '—' }}%</span>
          </div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 mt-1">
          <div v-for="c in liqCharts" :key="'lc' + c.key"
               class="flex items-center gap-1.5 min-w-0" :title="c.title">
            <span class="text-[10px] text-muted/80 flex-shrink-0">{{ c.label }}</span>
            <div class="flex items-end gap-[2px] h-6 flex-1 min-w-0">
              <div v-for="(h, i) in c.bars" :key="c.key + i"
                   class="flex-1 min-w-[2px] rounded-t"
                   :class="i === c.bars.length - 1 ? 'bg-[#58a6ff]' : 'bg-[#58a6ff]/50'"
                   :style="{ height: h + '%' }"></div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
// ==============================================================================
// 【MacroEnvCard】「宏观与环境」卡 —— **全站唯一实现**
//
// ★★ 2026-09-28（用户："把宏观方向 · 偏空的分数也移到顶部右上角，放在情绪的左边…
//   但是快讯的也很重要，以及支撑因素、压制因素、过热项、过冷项也需要展示，你有什么好的方案？"
//   ⇒ 选定方案 A）：本卡原**内联在 `Workbench.vue` 的「盘前」模板块里** ⇒
//   ① 只有盘前阶段才渲染（盘中/盘后**看不到**，尽管数据一直有）；② 顶栏要加"宏观摘要 +
//   点开看全文"就必须**再写一份**同样的展示 —— 而"同一份数据两处渲染必然漂移"是本项目
//   反复踩过的坑（同日刚在教练卡上踩过一次）。
//   ⇒ 抽成子组件：**盘前内联 + 顶栏浮层共用同一个组件**，改一处两处生效。
//   ⚠️ 浮层宽度只有 ~420px（窄于主区）⇒ 已用 `md:grid-cols-2` 自适应降级为单列，不破版。
//
// 【数据来源与"变不变"】（决定了它值得常驻）
//   · `macro`（宏观方向/分数/支撑压制因素）：后端 `macro_daily_loop` 在**工作日 08:55-13:00
//     锁定一次**（`macro_daily` 表）⇒ 当日**固定不漂移**；当天没锁定时前端回退
//     `/macro/snapshot`（实时算）。`macro.locked` 为真时标题右侧标"今日锁定 · 生成于 HH:MM"。
//   · `temperature`（市场环境 0~100）：与顶栏「情绪」同一来源。
//   · `flashDiag`（事件诊断 = 快讯 LLM 最新一条）：**高速变量**，随时间更新 ⇒
//     另在右栏常驻一行（方案 C），扫一眼即得。
//   · `sentiment`（情绪温度计 12 子项 → 过热/过冷）：来自 `/macro/snapshot.sentiment`，
//     在 `loadGlobals()` 的 120s 轮询里刷新。
//   · `sizing`（仓位上限 + 组合周回撤熔断）：`/api/user/position-sizing`。
// ==============================================================================
import { computed } from 'vue'
import { dirColor, levelColor, hhmm } from '../../composables/displayMeta'

const props = defineProps({
  macro: { type: Object, default: null },
  macroErr: { type: String, default: '' },
  temperature: { type: Object, default: null },
  flashDiag: { type: Object, default: null },
  sizing: { type: Object, default: null },
  sentiment: { type: Object, default: null },
  // ★★ 2026-09-29（P2 用户："继续做 IM 基差"）：股指期货基差（`/macro/snapshot.futures_basis`）
  //   —— 北向实时额停披后现存少数的「内资情绪 / 对冲需求」温度计。
  //   ⚠️ 独立 prop 而非从 `macro` 取：`macro` 可能是 `macro_daily` 的**当日锁定快照**
  //     （不含该字段）⇒ 挂上去会时有时无（与 `sentiment` 同款处理）。
  futuresBasis: { type: Object, default: null },
  // ★ 2026-10-25：流动性三件套（两融/中美利差/当月贴水率，日批落库 `/macro/liquidity`）。
  //   日频快照数据 ⇒ Workbench 进页面加载一次即可（不挂轮询）；`latest` 为空整块不渲染。
  liquidity: { type: Object, default: null },
  // ★ 2026-09-29（用户："你改为展示全部吧，宽度自适应"）：`full = true` ⇒ **展示全部**：
  //   首行允许**换行**、所有长文本**不截断**（顶栏浮层用 —— 浮层里没有"必须单行"的理由，
  //   而截断会让用户看不到 叙事/仓位建议 的全文）。
  //   ⚠️ 默认 false 保持不变 = 内联卡（盘前那处）的「严格单行 + truncate + hover 看全文」
  //      —— 那是 2026-09-25 用户明确要求的，**不能顺手改掉**（两处诉求不同）。
  full: { type: Boolean, default: false },
})

// ★ 2026-10-23（用户："文字过长导致卡片溢出以及弹窗被撑开"）：
//   `d_state_type` 会带一长串括注（"D1供给驱动（地缘风险未实质升级，油价高位震荡…VIX 19.387…）"）
//   ⇒ 内联卡与浮层都只展示**标签**（截断前 14 字），全文放 `title`。
function shortDiagType(s) {
  const t = String(s || '').split(/[（(]/)[0].trim()
  if (!t) return '不适用'
  return t.length > 14 ? `${t.slice(0, 14)}…` : t
}
function fullDiagType(s) {
  return String(s || '') || '不适用'
}

// ★ 2026-09-28：原来定义在 `Workbench.vue`，只被本卡用 ⇒ 随卡一起内聚到这里。
// 组合周回撤熔断（`sizing.portfolio_drawdown`）：悬停给完整路径与口径说明。
const pdd = computed(() => props.sizing?.portfolio_drawdown || null)
const pddTitle = computed(() => {
  const p = pdd.value
  if (!p) return ''
  if (!p.available) return p.note || '暂无数据'
  const cur = (p.curve || []).map(c => `${String(c.date).slice(5)} ${Number(c.nav).toLocaleString('zh-CN')}`).join(' → ')
  return `组合持仓市值路径：${cur}\n峰值 ${Number(p.nav_peak).toLocaleString('zh-CN')}（${p.peak_date}）`
    + ` → 最新 ${Number(p.nav_latest).toLocaleString('zh-CN')}\n${p.advice || ''}\n${p.note || ''}`
})

// ★ 2026-09-25：情绪温度计"过热 / 过冷"子项（后端已按得分降序，这里按阈值切分）。
//   阈值沿用 `margin_sentiment.sentiment_line()` 的口径（≥80 过热 / ≤20 过冷），保持单一来源。
const hotSubs = computed(() => ((props.sentiment?.subs) || []).filter(s => s.score >= 80).slice(0, 4))
const coldSubs = computed(() => ((props.sentiment?.subs) || []).filter(s => s.score <= 20).slice(0, 4))

// ★★ 2026-09-29（P2）：股指期货基差 —— 每品种取**当月合约**（标准口径），次月放 hover 对照。
//   【配色纪律】**一律中性色，不用红绿** —— 贴水深浅**不直接等于**"涨/跌"（对现货是
//   对冲成本、对情绪是需求强度），用红绿会被误读成行情涨跌。同项目前一例：
//   `Workbench.vue` 的「背离（加权−等权）」就是因为这个原因改用中性色。
//   【阈值纪律】暂**不设**绝对阈值（`参数表校准来源不空`）—— 贴水深度须与**自身历史**
//   比才有意义，而本项首次上线无历史基线 ⇒ 先积累，只做展示 + 四品种横向对照。
const basisRows = computed(() => {
  const items = props.futuresBasis?.items || []
  return items.map(r => {
    const ts = r.tenors || []
    const cur = ts.find(x => x.label === '当月') || ts[0]
    if (!cur) return null
    const nxt = ts.find(x => x.label === '次月')
    const fmtT = (t, tag) => `${tag} ${t.code}：期货 ${t.fut}，基差 ${t.basis}（${t.basis_rate}%），`
      + `年化 ${t.annualized == null ? '—（临近到期不年化）' : t.annualized + '%'}，剩 ${t.days_left} 天`
      + `，成交 ${Math.round(t.volume || 0).toLocaleString('zh-CN')} 手`
    const title = [
      `${r.product}（${r.spot_name}）：现货收盘 ${r.spot}`,
      fmtT(cur, '当月'),
      nxt ? fmtT(nxt, '次月') : '',
      '口径：基差 = 现货收盘 − 期货收盘（正 = 期货贴水）；年化 = 基差率 × 365 / 剩余自然日',
      '⚠️ 当月合约临近到期时年化对天数极敏感（9/28 剩 17 天 ⇒ 放大 21 倍），别当趋势读',
      '⚠️ IM 贴水含雪球/DMA 对冲盘的结构性需求，不能干净解读为「市场情绪」',
      '⚠️ 纯展示层，不进决策链；且**暂不设阈值**（需先积累历史基线才有意义）',
      props.futuresBasis?.note || '',
    ].filter(Boolean).join('\n')
    return {
      product: r.product, spot_name: r.spot_name,
      rate: cur.basis_rate, ann: cur.annualized, days: cur.days_left,
      label: cur.basis_rate >= 0 ? '贴水' : '升水', title,
    }
  }).filter(Boolean)
})
const basisNote = computed(() => {
  const a = props.futuresBasis?.as_of
  return `当月合约（正=贴水）${a ? ' · ' + a.slice(5, 16) : ''}`
})

// ★ 2026-10-25：流动性三件套 —— 两融/中美利差/当月贴水率的近 30 日迷你趋势。
//   【配色纪律】迷你条一律中性蓝（水位不是涨跌）；只有两融"净变化"用红绿（方向量）。
//   【口径】两融 T+1（沪+深，亿元）；利差 bp；贴水率 = 当月基差率（正=贴水，年化敏感不用于趋势）。
const liq = computed(() => (props.liquidity && props.liquidity.latest) ? props.liquidity : null)
const liqDegraded = computed(() => props.liquidity?.degraded || '')
const liqMarginWanYi = computed(() => {
  const t = props.liquidity?.latest?.margin?.total_yi
  return t == null ? '—' : (t / 10000).toFixed(2) + '万亿'
})
function fmtSigned(v) {
  const n = Number(v)
  if (v == null || Number.isNaN(n)) return '—'
  const r = Math.abs(n) >= 100 ? Math.round(n) : Math.round(n * 10) / 10
  return (r > 0 ? '+' : '') + r
}
// ── ★★ 2026-10-28：规则明细（可解释性）───────────────────────────────────
//   后端 `direction.group_contrib` = 每组对总分的**实际贡献（分）**，
//   与 `group_scores`（-1~1 归一值）并存 —— 后者单独看根本不知道"扣了几分"。
const _GROUP_CN = { china: '中国', global: '全球', commodity: '商品', internal: '内部' }
const _GROUP_W = { china: 0.32, global: 0.35, commodity: 0.15, internal: 0.18 }
function groupLabel(g) { return _GROUP_CN[g] || g }
function groupWeight(g) { return _GROUP_W[g] ?? '—' }
const triggeredCount = computed(
  () => (props.macro?.rules_detail || []).filter(r => r.hit).length)
// 排序：**触发的在前**（按贡献绝对值降序），未触发的在后仍按强度降序
//   —— 用户第一眼要看"是谁把分数顶上去的"，未触发的价值在于"它没算"。
const sortedRules = computed(() => {
  const rows = [...(props.macro?.rules_detail || [])]
  return rows.sort((a, b) => {
    if (a.hit !== b.hit) return a.hit ? -1 : 1
    return Math.abs(b.contribution || 0) - Math.abs(a.contribution || 0)
      || (b.strength || 0) - (a.strength || 0)
  })
})
function dirNumClass(v) {
  const n = Number(v)
  if (v == null || Number.isNaN(n) || n === 0) return 'text-muted'
  return n > 0 ? 'text-rise' : 'text-fall'
}
// 序列 → 8~100% 归一化高度（null 留 0 = 缺数据不假装）；全平序列给中位高度
function barHeights(series) {
  const vals = (series || []).filter(v => v != null)
  if (!vals.length) return []
  const mn = Math.min(...vals), mx = Math.max(...vals)
  const span = (mx - mn) || 1
  return (series || []).map(v => v == null ? 0 : 8 + Math.round(((v - mn) / span) * 92))
}
function _rangeTitle(series, unit) {
  const vals = (series || []).filter(v => v != null)
  if (vals.length < 2) return ''
  return `区间 ${Math.round(Math.min(...vals) * 100) / 100}~${Math.round(Math.max(...vals) * 100) / 100}${unit} · 最新 ${Math.round(vals[vals.length - 1] * 100) / 100}${unit}`
}
const liqMarginTitle = computed(() => {
  const m = props.liquidity?.latest?.margin || {}
  return [
    `两融余额（${m.src === 'eastmoney' ? '沪深北' : '沪+深'}，亿元；T+1 数据 ${m.date || '—'}）`,
    `沪 ${m.sse_yi ?? '—'} / 深 ${m.szse_yi ?? '—'}（东财兜底源无分拆）`,
    `当日净变化 ${fmtSigned(m.add_yi)}亿 · 5日 ${fmtSigned(m.chg5_yi)}亿`,
    _rangeTitle((props.liquidity?.history || []).map(h => h.margin_total_yi), '亿'),
    '两融净增=杠杆资金流入；口径台阶（沪+深 vs 沪深北）以 margin_src 为准',
  ].join('\n')
})
const liqSpreadTitle = computed(() => {
  const b = props.liquidity?.latest?.bonds || {}
  return [
    `中美利差 = 中债10Y − 美债10Y（bp，负=中债低于美债）`,
    `中债 ${b.cn10y ?? '—'}% / 美债 ${b.us10y ?? '—'}% · 利差 ${fmtSigned(b.spread_bp)}bp`,
    _rangeTitle((props.liquidity?.history || []).map(h => h.spread_bp), 'bp'),
  ].join('\n')
})
const liqCharts = computed(() => {
  const hist = props.liquidity?.history || []
  const pick = fn => hist.map(fn)
  const mk = (key, label, series, title) => {
    const bars = barHeights(series)
    return bars.length ? { key, label, bars, title: title + '\n' + _rangeTitle(series, '') } : null
  }
  return [
    mk('margin', '两融余额', pick(h => h.margin_total_yi), '近30日两融余额（亿元）'),
    mk('spread', '中美利差', pick(h => h.spread_bp), '近30日中美利差（bp）'),
    mk('if', 'IF贴水率', pick(h => h.basis_rate?.IF), '近30日 IF 当月贴水率（%），正=贴水'),
    mk('im', 'IM贴水率', pick(h => h.basis_rate?.IM), '近30日 IM 当月贴水率（%），正=贴水（含雪球/DMA 结构性需求）'),
  ].filter(Boolean)
})
</script>
