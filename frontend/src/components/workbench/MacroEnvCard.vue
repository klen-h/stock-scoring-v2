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
           严格单行不换行：长文本一律 truncate，悬停 title 看全文） -->
      <div class="flex items-center gap-5 min-w-0">
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
            <div class="text-[11px] text-muted truncate max-w-[240px]"
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
        <div class="flex items-center gap-3 flex-1 min-w-0">
          <div class="flex-shrink-0">
            <div class="text-sm font-bold whitespace-nowrap"
                 :class="flashDiag?.correlation_diagnosis?.correlation_state === 'D状态'
                         ? 'text-amber-400' : 'text-gray-100'">
              {{ flashDiag?.correlation_diagnosis?.correlation_state || '无法判断' }}
            </div>
            <div class="text-[10px] text-muted whitespace-nowrap">事件诊断 · {{ flashDiag?.correlation_diagnosis?.d_state_type || '不适用' }}</div>
          </div>
          <!-- ★ 2026-09-25 用户要求：文字部分同一 div 上下布局（叙事上 / 仓位·详情下） -->
          <div class="flex-1 min-w-0 text-xs">
            <div class="text-gray-300 truncate"
                 :title="flashDiag?.dominant_narrative?.narrative || flashDiag?.market_mood || ''">
              {{ flashDiag?.dominant_narrative?.narrative || flashDiag?.market_mood || '—' }}
            </div>
            <div class="text-muted whitespace-nowrap">
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
})

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
</script>
