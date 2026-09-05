<template>
  <div class="fade-in space-y-4" v-if="loaded">
    <!-- ① 头部卡片：行情 + 关键结论徽章（信号/健康度/消息面 一眼看全） -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center gap-3 flex-wrap">
        <span class="text-2xl font-bold">{{ stockInfo.name || code }}</span>
        <span class="text-muted font-mono text-sm">{{ code }}</span>
        <span v-if="scoreData.total_score" class="px-2.5 py-0.5 rounded-full text-xs font-bold"
          :class="scoreColorClass">
          {{ scoreData.signal }} {{ scoreData.total_score }}
        </span>
        <span v-if="scoreData.trend_health?.verdict" class="px-2 py-0.5 rounded-full text-xs font-bold"
          :class="healthVerdictClass(scoreData.trend_health.verdict)">
          {{ scoreData.trend_health.verdict }} {{ scoreData.trend_health.score }}/5
        </span>
        <span v-if="newsData" class="px-2 py-0.5 rounded-full text-xs font-bold" :class="newsLevelClass">
          消息 {{ newsData.level_text }} {{ newsData.score > 0 ? '+' : '' }}{{ newsData.score }}
        </span>
      </div>
      <!-- 上下布局：上一行大字号现价/涨跌，下一行全宽行情字段（左右并排时字段被挤得很窄） -->
      <div class="mt-3 space-y-3">
        <div class="flex items-end gap-4 flex-wrap">
          <span class="text-4xl md:text-5xl font-bold leading-none tabular-nums"
            :class="stockInfo.change_pct >= 0 ? 'text-rise' : 'text-fall'">{{ stockInfo.price }}</span>
          <div class="flex items-end gap-3">
            <span class="text-xl md:text-2xl font-semibold leading-none tabular-nums"
              :class="stockInfo.change_pct >= 0 ? 'text-rise' : 'text-fall'">
              {{ stockInfo.change_amt >= 0 ? '+' : '' }}{{ stockInfo.change_amt }}
            </span>
            <span class="text-lg md:text-xl font-bold leading-none tabular-nums px-2.5 py-1.5 rounded-md"
              :class="stockInfo.change_pct >= 0 ? 'text-rise bg-rise/10' : 'text-fall bg-fall/10'">
              {{ stockInfo.change_pct >= 0 ? '+' : '' }}{{ stockInfo.change_pct }}%
            </span>
          </div>
        </div>
        <!-- 行情字段：标签与数值上下分离，数值用等宽数字对齐，价格类按昨收着色 -->
        <div class="grid grid-cols-4 md:grid-cols-8 gap-2">
          <div v-for="q in quoteFields" :key="q.label"
            class="px-3 py-2 rounded-md bg-bg/60 border border-border/40">
            <div class="text-xs text-muted leading-none mb-2">{{ q.label }}</div>
            <div class="font-mono tabular-nums text-base font-semibold leading-none" :class="q.color">{{ q.value }}</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ② 关键价位（支撑/阻力）：买卖决策的核心参考，提到页签之上常驻，
         不再藏在 K 线页签里——看评分时同样需要对照价位 -->
    <div v-if="supportResistance?.levels?.length" class="bg-card border border-border rounded-lg p-3">
      <div class="flex items-center gap-x-4 gap-y-3 flex-wrap">
        <span class="text-sm font-bold text-muted shrink-0">关键价位</span>

        <!-- 阻力位（由近及远） -->
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-xs text-muted shrink-0">阻力</span>
          <span v-for="lv in resistanceLevels" :key="'r' + lv.price"
            class="px-2 py-1 rounded text-sm font-semibold font-mono tabular-nums bg-red-500/10 text-red-400 border border-red-500/25"
            :title="`触及 ${lv.touches} 次 · ${strengthText(lv.strength)}`">
            {{ lv.price }}
          </span>
          <span v-if="!resistanceLevels.length" class="text-sm text-muted">-</span>
        </div>

        <!-- 当前价格在区间中的位置 -->
        <div class="flex items-center gap-2 shrink-0" title="当前价在区间中的相对位置">
          <div class="w-24 h-2 rounded-full overflow-hidden" style="background:#21262d">
            <div class="h-full rounded-full transition-all"
              :style="{ width: supportResistance.position_pct + '%', background: srBarBg }"></div>
          </div>
          <span class="text-sm font-bold font-mono tabular-nums" :style="{ color: srBarBg }">{{ supportResistance.position_pct }}%</span>
        </div>

        <!-- 支撑位（由近及远） -->
        <div class="flex items-center gap-2 flex-wrap">
          <span class="text-xs text-muted shrink-0">支撑</span>
          <span v-for="lv in supportLevels" :key="'s' + lv.price"
            class="px-2 py-1 rounded text-sm font-semibold font-mono tabular-nums bg-emerald-500/10 text-emerald-400 border border-emerald-500/25"
            :title="`触及 ${lv.touches} 次 · ${strengthText(lv.strength)}`">
            {{ lv.price }}
          </span>
          <span v-if="!supportLevels.length" class="text-sm text-muted">-</span>
        </div>

        <!-- 建议：按措辞判定风险/机会/观察，用着重色突出（风险优先匹配） -->
        <span v-if="supportResistance.suggestion"
          class="text-xs font-semibold leading-snug px-2.5 py-1.5 rounded-md border basis-full lg:basis-auto lg:ml-auto"
          :class="suggestionToneClass">
          {{ supportResistance.suggestion }}
        </span>
      </div>
    </div>

    <!-- ③ 页签：详情按维度收纳，默认只展开评分，页面不再无限拉长 -->
    <div class="flex gap-2 flex-wrap">
      <button v-for="t in tabs" :key="t.key" @click="activeTab = t.key"
        :class="activeTab === t.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
        class="px-3 py-1 rounded text-xs transition-colors">
        {{ t.label }}
      </button>
    </div>

    <!-- ③ 评分页签：左 = 综合评分 + 趋势健康度，右 = 消息面情绪 -->
    <div v-if="activeTab === 'score'" class="grid grid-cols-1 lg:grid-cols-3 gap-4">
      <!-- 左列（2/3）：综合评分 + 维度明细 + 因素标签 + 趋势健康度（合并一张卡） -->
      <div v-if="scoreData.total_score || scoreData.trend_health?.verdict"
        class="lg:col-span-2 bg-card border border-border rounded-lg p-5">
      <template v-if="scoreData.total_score">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-sm font-semibold">多因子综合评分</h3>
          <span class="text-xs text-muted">技术面40% + 资金面25% + 基本面35%</span>
        </div>

        <div class="flex items-center gap-6">
          <!-- 左侧：总分圆环 -->
          <div class="flex-shrink-0 relative w-28 h-28">
            <svg class="w-28 h-28 -rotate-90" viewBox="0 0 120 120">
              <circle cx="60" cy="60" r="52" fill="none" stroke="#21262d" stroke-width="8"/>
              <circle cx="60" cy="60" r="52" fill="none" :stroke="scoreRingColor" stroke-width="8"
                stroke-linecap="round"
                :stroke-dasharray="2 * Math.PI * 52"
                :stroke-dashoffset="2 * Math.PI * 52 * (1 - scoreData.total_score / 100)"/>
            </svg>
            <div class="absolute inset-0 flex flex-col items-center justify-center">
              <span class="text-2xl font-bold" :class="scoreTextColor">{{ scoreData.total_score }}</span>
              <span class="text-xs text-muted">综合分</span>
            </div>
          </div>

          <!-- 右侧：维度条 -->
          <div class="flex-1 space-y-3">
            <div v-for="dim in scoreData.dimensions" :key="dim.name">
              <div class="flex justify-between text-xs mb-1">
                <span class="text-gray-300">{{ dim.name }}</span>
                <span :class="dimScoreColor(dim.score)">{{ dim.score }}<span class="text-muted"> / 100</span></span>
              </div>
              <div class="h-2 bg-bg rounded-full overflow-hidden">
                <div class="h-full rounded-full transition-all duration-500" :class="dimBarColor(dim.score)"
                  :style="{ width: dim.score + '%' }"></div>
              </div>
              <!-- 子项展开 -->
              <div v-if="dim.details && showDetails" class="mt-1 grid grid-cols-2 gap-x-4 gap-y-0.5 text-[11px] text-muted pl-2">
                <div v-for="(val, key) in dim.details" :key="key" class="flex justify-between">
                  <span>{{ key }}</span>
                  <span :class="val.分值 >= 70 ? 'text-emerald-400' : val.分值 <= 35 ? 'text-red-400' : ''">
                    {{ val.分值 }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 因素标签 -->
        <div class="mt-4 flex flex-wrap gap-2">
          <span v-for="f in scoreData.factors_up" :key="'u'+f"
            class="px-2 py-0.5 rounded text-xs bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">
            + {{ f }}
          </span>
          <span v-for="f in scoreData.factors_down" :key="'d'+f"
            class="px-2 py-0.5 rounded text-xs bg-red-500/15 text-red-400 border border-red-500/20">
            - {{ f }}
          </span>
        </div>

        <!-- 摘要 -->
        <p v-if="scoreData.summary" class="mt-3 text-xs text-muted leading-relaxed">{{ scoreData.summary }}</p>
      </template>

      <!-- 趋势健康度诊断（与评分同属"结论"，合并进本卡） -->
      <div v-if="scoreData.trend_health?.verdict" class="mt-4 pt-4 border-t border-border">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-semibold">趋势健康度诊断</h3>
          <span class="px-2.5 py-0.5 rounded-full text-xs font-bold"
            :class="healthVerdictClass(scoreData.trend_health.verdict)">
            {{ scoreData.trend_health.verdict }} {{ scoreData.trend_health.score }}/5
          </span>
        </div>
        <div class="grid grid-cols-1 md:grid-cols-5 gap-2">
          <div v-for="d in scoreData.trend_health.details" :key="d.dim"
            class="px-3 py-2 rounded-lg text-xs"
            :class="d.healthy ? 'bg-emerald-500/10 border border-emerald-500/20' : 'bg-red-500/10 border border-red-500/20'">
            <div class="flex items-center gap-1.5 mb-1">
              <span class="w-2 h-2 rounded-full" :class="d.healthy ? 'bg-emerald-400' : 'bg-red-400'"></span>
              <span class="font-semibold" :class="d.healthy ? 'text-emerald-400' : 'text-red-400'">{{ d.dim }}</span>
            </div>
            <div class="text-muted leading-tight">{{ d.desc }}</div>
          </div>
        </div>
        <div class="mt-3 text-[11px] text-muted">
          ≥4/5 趋势健康，回调大概率为洗盘，耐心持有；≤2/5 趋势恶化，真跌风险高，考虑减仓
        </div>
      </div>

      <!-- 主力行为（筹码结构 × 资金流组合信号，全池截面回测验证） -->
      <div v-if="scoreData.mainforce" class="mt-4 pt-4 border-t border-border">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-semibold">主力行为</h3>
          <div class="flex items-center gap-2">
            <span v-if="scoreData.mainforce.phase_cn"
              class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-white/5 text-muted">
              {{ scoreData.mainforce.phase_cn }}段
            </span>
            <span v-if="scoreData.mainforce.signal_cn"
              class="px-2.5 py-0.5 rounded-full text-xs font-bold"
              :class="scoreData.mainforce.signal === 'distribution' ? 'bg-red-500/20 text-red-400' : 'bg-emerald-500/20 text-emerald-400'">
              {{ scoreData.mainforce.signal_cn }}
            </span>
          </div>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
          <div class="px-3 py-2 rounded-lg bg-white/5">
            <div class="text-muted mb-1">获利盘</div>
            <div class="font-mono font-bold"
              :class="(scoreData.mainforce.chip?.winner_ratio ?? 0) > 0.7 ? 'text-red-400' : (scoreData.mainforce.chip?.winner_ratio ?? 0) < 0.3 ? 'text-emerald-400' : ''">
              {{ ((scoreData.mainforce.chip?.winner_ratio ?? 0) * 100).toFixed(0) }}%
            </div>
          </div>
          <div class="px-3 py-2 rounded-lg bg-white/5">
            <div class="text-muted mb-1">筹码位置</div>
            <div class="font-mono font-bold"
              :class="(scoreData.mainforce.chip?.price_pos ?? 0) > 0.75 ? 'text-red-400' : (scoreData.mainforce.chip?.price_pos ?? 0) < 0.35 ? 'text-emerald-400' : ''">
              {{ ((scoreData.mainforce.chip?.price_pos ?? 0) * 100).toFixed(0) }}%
            </div>
          </div>
          <div class="px-3 py-2 rounded-lg bg-white/5">
            <div class="text-muted mb-1">筹码集中度</div>
            <div class="font-mono font-bold">{{ ((scoreData.mainforce.chip?.concentration ?? 0) * 100).toFixed(0) }}%</div>
          </div>
          <div class="px-3 py-2 rounded-lg bg-white/5">
            <div class="text-muted mb-1">5日主力净流入</div>
            <div class="font-mono font-bold"
              :class="(scoreData.mainforce.flow5_amt ?? 0) > 0 ? 'text-red-400' : 'text-emerald-400'">
              {{ scoreData.mainforce.flow5_amt != null ? (scoreData.mainforce.flow5_amt > 0 ? '+' : '') + scoreData.mainforce.flow5_amt + '%' : '-' }}
            </div>
          </div>
        </div>
        <p v-if="scoreData.mainforce.reason" class="mt-2 text-[11px] text-muted leading-relaxed">
          {{ scoreData.mainforce.reason }}
        </p>
        <div class="mt-2 text-[11px] text-muted">
          高位高获利+主力流出 = 出货嫌疑（回测 10 日 -7.5pt）；低位筹码密集+主力净流入 = 吸筹区（10 日 +1.1pt）。均值成本 {{ scoreData.mainforce.chip?.avg_cost ?? '-' }}
        </div>
      </div>
      </div>

      <!-- 右列（1/3）：消息面情绪 -->
      <div v-if="newsData" class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between mb-3">
          <h3 class="text-sm font-semibold">消息面情绪</h3>
          <div class="flex items-center gap-2">
            <span class="font-mono text-sm font-bold" :class="newsScoreClass">{{ newsData.score > 0 ? '+' : '' }}{{ newsData.score }}</span>
            <span class="px-2.5 py-0.5 rounded-full text-xs font-bold" :class="newsLevelClass">{{ newsData.level_text }}</span>
          </div>
        </div>
        <div v-if="newsData.items?.length" class="space-y-1.5">
          <div v-for="n in newsData.items.slice(0, 6)" :key="n.time + n.title"
            class="flex items-start gap-2 px-3 py-2 rounded-lg text-xs bg-white/5">
            <span class="font-mono shrink-0" :class="n.score < 0 ? 'text-red-400' : 'text-emerald-400'">{{ n.score > 0 ? '+' : '' }}{{ n.score }}</span>
            <span class="leading-snug">{{ n.title }}</span>
            <span class="ml-auto shrink-0 text-muted text-[10px]">{{ (n.time || '').slice(5, 16) }}</span>
          </div>
        </div>
        <div v-else class="text-xs text-muted">近 3 天无该股票的情绪倾向新闻（共匹配 {{ newsData.news_count }} 条快讯）</div>
        <div v-if="newsHistory.length >= 2" class="mt-2">
          <div class="text-[10px] text-muted mb-1">消息分走势（每日盘后快照）</div>
          <div ref="newsSparkRef" style="height: 48px"></div>
        </div>
        <div v-else class="mt-2 text-[10px] text-muted">历史走势：数据积累中（首个快照于工作日 15:20 后生成）</div>
        <div class="mt-3 text-[11px] text-muted">东财 7×24 快讯 + 关键词规则打分（72h 衰减）；独立维度不进总分，仅供参考</div>
      </div>
      <div v-else class="bg-card border border-border rounded-lg p-6 text-xs text-muted text-center">消息面加载中…</div>
    </div>

    <!-- ④ K 线页签：K线图 + 支撑阻力 + RSI -->
    <template v-else-if="activeTab === 'kline'">
      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex gap-2 mb-3 flex-wrap">
          <button v-for="p in ['day','week','month']" :key="p" @click="changePeriod(p)"
            :class="period === p ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
            class="px-3 py-1 rounded text-xs transition-colors">{{ {day:'日K',week:'周K',month:'月K'}[p] }}</button>
          <div class="ml-auto flex gap-2">
            <button v-for="ind in indicators" :key="ind.key" @click="toggleIndicator(ind.key)"
              :class="activeIndicators.includes(ind.key) ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted'"
              class="px-2 py-1 rounded text-xs transition-colors">{{ ind.label }}</button>
          </div>
        </div>
        <div ref="klineChartRef" class="h-[500px]"></div>
      </div>

      <!-- RSI（支撑阻力已上移到头部常驻区，这里独占整行，内部横向三栏） -->
      <div v-if="rsiSignals" class="bg-card border border-border rounded-lg p-4">
        <h3 class="text-sm font-semibold text-muted mb-3">RSI 指标</h3>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
          <!-- ① 当前值 + 刻度条 -->
          <div>
            <div class="flex items-center justify-between mb-2">
              <span class="text-xs text-muted">当前 RSI</span>
              <span class="text-2xl font-bold font-mono tabular-nums" :class="rsiColor">{{ rsiSignals.current_rsi }}</span>
            </div>
            <div class="relative h-3 rounded-full overflow-hidden"
              style="background: linear-gradient(to right, #22c55e 0%, #22c55e 20%, #f59e0b 30%, #8b949e 50%, #f59e0b 70%, #ef4444 80%, #ef4444 100%)">
              <div class="absolute top-0 h-full w-1 bg-white shadow" :style="{ left: rsiSignals.current_rsi + '%' }"></div>
            </div>
            <div class="flex justify-between text-[10px] text-muted mt-1">
              <span>超卖 30</span>
              <span>中性 50</span>
              <span>超买 70</span>
            </div>
          </div>
          <!-- ② 区间 / 信号 -->
          <div class="space-y-2 text-xs md:px-4 md:border-x md:border-border">
            <div class="flex justify-between">
              <span class="text-muted">区间</span>
              <span :class="rsiColor">{{ rsiZoneText }}</span>
            </div>
            <div v-if="rsiSignals.signal" class="flex justify-between gap-2">
              <span class="text-muted shrink-0">信号</span>
              <span class="text-right" :class="rsiSignals.signal.type === 'buy' ? 'text-emerald-400' : rsiSignals.signal.type === 'sell' ? 'text-red-400' : 'text-amber-400'">
                {{ rsiSignals.signal.description }}
              </span>
            </div>
          </div>
          <!-- ③ 解读 -->
          <div class="text-xs text-muted leading-relaxed">
            {{ rsiSignals.interpretation }}
          </div>
        </div>
      </div>
    </template>

    <!-- ⑤ 评分验证页签：评分 vs 价格 -->
    <div v-else-if="activeTab === 'rank'">
      <div v-if="rankHistoryData && rankHistoryData.points && rankHistoryData.points.length >= 2"
           class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between flex-wrap gap-2 mb-2">
          <h3 class="text-sm font-semibold">📈 评分 vs 价格（近 30 日收盘快照）</h3>
          <div class="flex gap-2 flex-wrap text-[10px]">
            <span v-for="b in rankHistoryData.bucket_stats || []" :key="b.bucket"
                  class="px-1.5 py-0.5 rounded border"
                  :class="(b.bucket === '>=70' || b.bucket === '60-70')
                    ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
                    : 'bg-white/5 text-muted border-border'">
              评分{{ b.bucket }}→未来{{ rankHistoryData.fwd_days }}日均 {{ b.avg_fwd5 > 0 ? '+' : '' }}{{ b.avg_fwd5 }}%（{{ b.count }}次）
            </span>
          </div>
        </div>
        <div ref="rankHistoryRef" class="h-[260px]"></div>
        <div class="mt-1 text-[10px] text-muted">
          评分是技术结构分（趋势+动量+超买超卖），与当日涨跌天然相关；看分桶更有意义——
          高分桶的未来均值显著高于低分桶，才说明评分有预测力。样本较少时仅供参考。
        </div>
      </div>
      <div v-else class="bg-card border border-border rounded-lg p-6 text-xs text-muted text-center">
        该股评分快照还在积累中（每日盘后自动落库，上榜 ≥ 2 天后可看评分 vs 价格走势）
      </div>
    </div>

    <!-- ⑦ 基本面页签 -->
    <div v-else-if="activeTab === 'fund'" class="bg-card border border-border rounded-lg p-4">
      <h3 class="text-sm font-semibold text-muted mb-3">基本面数据</h3>
      <div v-if="fundamental.valuation && fundamental.valuation['市盈率(动态)'] !== undefined" class="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">市盈率(动态)</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['市盈率(动态)'] }}</div>
        </div>
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">市净率</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['市净率'] }}</div>
        </div>
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">总市值(亿)</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['总市值(亿)'] }}</div>
        </div>
        <div class="text-center p-3 bg-bg rounded-lg">
          <div class="text-muted text-xs">流通市值(亿)</div>
          <div class="text-lg font-bold mt-1">{{ fundamental.valuation['流通市值(亿)'] }}</div>
        </div>
      </div>
      <div v-else class="text-xs text-muted text-center py-4">暂无基本面数据</div>
    </div>
  </div>
  <div v-else class="flex items-center justify-center py-32"><div class="loading-spinner"></div></div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import { useRoute } from 'vue-router'
import * as echarts from 'echarts'
import { getStockKline, getStockRealtime, getStockFundamental, getStockTechnical, getStockScore, getSupportResistance, getRSISignals, getStockNews, getStockNewsHistory, getRankHistory, getStockFinance, getScoreWeights } from '../api'
import { loadLocalKline, computeLocalScore } from '../composables/useFrontendScoring'

const route = useRoute()
const code = route.params.code

const stockInfo = ref({})
const klineData = ref([])
const technicalData = ref([])
const fundamental = ref({ valuation: {}, financial: {} })
const scoreData = ref({})
const loaded = ref(false)
const period = ref('day')
const activeIndicators = ref(['ma', 'macd', 'vol'])
const showDetails = ref(true)

// ── 页签收纳：图表容器随页签条件渲染（隐藏时 ref 为 null，render 函数自动跳过）──
const tabs = [
  { key: 'score', label: '评分' },
  { key: 'kline', label: 'K 线' },
  { key: 'rank', label: '评分验证' },
  { key: 'fund', label: '基本面' },
]
const activeTab = ref('score')

// 支撑阻力 + RSI
const supportResistance = ref(null)
const rsiSignals = ref(null)

// 消息面（独立维度，异步加载不阻塞主数据）
const newsData = ref(null)
// 消息分历史快照（每日盘后落库，画走势图用）
const newsHistory = ref([])
const newsSparkRef = ref(null)
let newsSparkChart = null

// 评分 vs 价格（每日收盘快照，评分有效性个股级验证）
const rankHistoryData = ref(null)
const rankHistoryRef = ref(null)
let rankHistoryChart = null

const klineChartRef = ref(null)
let charts = []

const indicators = [
  { key: 'ma', label: 'MA' },
  { key: 'macd', label: 'MACD' },
  { key: 'vol', label: '成交量' },
  { key: 'boll', label: 'BOLL' },
  { key: 'rsi', label: 'RSI' },
]

// 评分颜色
const scoreRingColor = computed(() => {
  const s = scoreData.value.total_score || 0
  if (s >= 65) return '#22c55e'
  if (s >= 45) return '#f59e0b'
  return '#ef4444'
})
const scoreTextColor = computed(() => {
  const s = scoreData.value.total_score || 0
  if (s >= 65) return 'text-emerald-400'
  if (s >= 45) return 'text-amber-400'
  return 'text-red-400'
})
const scoreColorClass = computed(() => {
  const s = scoreData.value.total_score || 0
  if (s >= 65) return 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
  if (s >= 45) return 'bg-amber-500/20 text-amber-400 border border-amber-500/30'
  return 'bg-red-500/20 text-red-400 border border-red-500/30'
})

function dimScoreColor(s) {
  if (s >= 70) return 'text-emerald-400'
  if (s >= 45) return 'text-amber-400'
  return 'text-red-400'
}
function dimBarColor(s) {
  if (s >= 70) return 'bg-emerald-500'
  if (s >= 45) return 'bg-amber-500'
  return 'bg-red-500'
}

function healthVerdictClass(verdict) {
  return { '趋势健康': 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
    '趋势偏弱': 'bg-amber-500/20 text-amber-400 border border-amber-500/30',
    '趋势恶化': 'bg-red-500/20 text-red-400 border border-red-500/30',
  }[verdict] || 'bg-white/5 text-muted'
}

// ── 消息面徽章配色（5 档：强烈负面 → 强烈正面）──
const newsScoreClass = computed(() => {
  const s = newsData.value?.score || 0
  if (s <= -1.5) return 'text-red-400'
  if (s >= 1.5) return 'text-emerald-400'
  return 'text-muted'
})
const newsLevelClass = computed(() => ({
  [-2]: 'bg-red-500/20 text-red-400 border border-red-500/30',
  [-1]: 'bg-red-500/10 text-red-300 border border-red-500/20',
  0: 'bg-white/5 text-muted border border-border',
  1: 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20',
  2: 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
}[newsData.value?.level ?? 0]))

// ── 消息分走势小图（历史 ≥ 2 天才有意义）──
function renderNewsSpark() {
  if (!newsSparkRef.value || newsHistory.value.length < 2) return
  if (newsSparkChart) newsSparkChart.dispose()
  const c = echarts.init(newsSparkRef.value, 'dark')
  c.setOption({
    backgroundColor: 'transparent',
    grid: { left: 28, right: 8, top: 6, bottom: 16 },
    xAxis: { type: 'category', data: newsHistory.value.map(h => (h.date || '').slice(5)),
      axisLabel: { fontSize: 9, color: '#8b949e' }, axisLine: { lineStyle: { color: '#30363d' } } },
    yAxis: { type: 'value', min: -10, max: 10, splitNumber: 2,
      axisLabel: { fontSize: 9, color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
    tooltip: { trigger: 'axis', formatter: p => `${p[0].axisValue}<br/>消息分 ${p[0].value}` },
    series: [{
      type: 'line', data: newsHistory.value.map(h => h.score), smooth: true,
      symbol: 'circle', symbolSize: 4,
      lineStyle: { color: '#58a6ff', width: 1.5 }, itemStyle: { color: '#58a6ff' },
      markLine: { silent: true, symbol: 'none', label: { show: false },
        data: [{ yAxis: 0 }], lineStyle: { color: '#30363d', type: 'dashed' } },
    }],
  })
  newsSparkChart = c
  charts.push(c)
}
watch(newsHistory, () => nextTick(renderNewsSpark))

// ── 评分 vs 价格 双轴折线（每日收盘快照）──
function renderRankHistory() {
  const d = rankHistoryData.value
  if (!rankHistoryRef.value || !d?.points || d.points.length < 2) return
  if (rankHistoryChart) rankHistoryChart.dispose()
  const pts = d.points
  const c = echarts.init(rankHistoryRef.value, 'dark')
  c.setOption({
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      formatter: ps => {
        let s = ps[0].axisValue
        for (const p of ps) s += `<br/>${p.seriesName} ${p.value ?? '-'}`
        const pt = pts[ps[0].dataIndex]
        if (pt && pt.fwd5 != null) s += `<br/>未来${d.fwd_days}日 ${pt.fwd5 > 0 ? '+' : ''}${pt.fwd5}%`
        return s
      },
    },
    legend: { data: ['评分', '收盘价'], top: 0, textStyle: { fontSize: 10, color: '#8b949e' } },
    grid: { left: 40, right: 52, top: 28, bottom: 20 },
    xAxis: { type: 'category', data: pts.map(p => (p.date || '').slice(5)),
      axisLabel: { fontSize: 9, color: '#8b949e' }, axisLine: { lineStyle: { color: '#30363d' } } },
    yAxis: [
      { type: 'value', min: 0, max: 100, name: '评分',
        nameTextStyle: { fontSize: 9, color: '#8b949e' },
        axisLabel: { fontSize: 9, color: '#8b949e' }, splitLine: { lineStyle: { color: '#21262d' } } },
      { type: 'value', scale: true, name: '价格',
        nameTextStyle: { fontSize: 9, color: '#8b949e' },
        axisLabel: { fontSize: 9, color: '#8b949e' }, splitLine: { show: false } },
    ],
    series: [
      { name: '评分', type: 'line', yAxisIndex: 0, smooth: true, symbol: 'circle', symbolSize: 4,
        data: pts.map(p => p.score),
        lineStyle: { color: '#a371f7', width: 1.5 }, itemStyle: { color: '#a371f7' } },
      { name: '收盘价', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none',
        data: pts.map(p => p.price),
        lineStyle: { color: '#58a6ff', width: 1.5, opacity: 0.85 }, itemStyle: { color: '#58a6ff' } },
    ],
  })
  rankHistoryChart = c
  charts.push(c)
}

// ── 头部行情字段：标签/数值分离渲染，价格类按昨收着色（涨红跌绿）──
const quoteFields = computed(() => {
  const s = stockInfo.value
  const base = parseFloat(s.prev_close)
  const colorOf = (v) => {
    const p = parseFloat(v)
    if (!p || !base) return 'text-gray-200'
    return p > base ? 'text-rise' : p < base ? 'text-fall' : 'text-gray-200'
  }
  return [
    { label: '今开', value: s.open ?? '-', color: colorOf(s.open) },
    { label: '最高', value: s.high ?? '-', color: colorOf(s.high) },
    { label: '最低', value: s.low ?? '-', color: colorOf(s.low) },
    { label: '昨收', value: s.prev_close ?? '-', color: 'text-gray-200' },
    { label: '成交量', value: formatVol(s.volume), color: 'text-gray-200' },
    { label: '成交额', value: formatAmt(s.amount), color: 'text-gray-200' },
    { label: '换手率', value: s.turnover_rate != null ? s.turnover_rate + '%' : '-', color: 'text-gray-200' },
    { label: '市盈率', value: s.pe ?? '-', color: 'text-gray-200' },
  ]
})

// ── 支撑阻力 + RSI 显示辅助 ──
const srBarBg = computed(() => {
  if (!supportResistance.value) return '#8b949e'
  const p = supportResistance.value.position_pct
  if (p >= 75) return '#f59e0b'  // amber
  if (p <= 25) return '#22c55e'  // emerald
  return '#8b949e'               // gray/muted
})

// 阻力位按价格升序（离现价最近的在前），支撑位按价格降序
const resistanceLevels = computed(() =>
  (supportResistance.value?.levels || [])
    .filter(l => l.type === 'resistance')
    .sort((a, b) => a.price - b.price))
const supportLevels = computed(() =>
  (supportResistance.value?.levels || [])
    .filter(l => l.type === 'support')
    .sort((a, b) => b.price - a.price))

function strengthText(s) {
  return s === 'strong' ? '强' : s === 'medium' ? '中' : '弱'
}

// ── 建议文案的着重色：后端 _generate_suggestion() 共 7 种措辞，按情绪分四档着色。
// 风险类必须优先匹配——"接近强阻力位，谨慎追高，可考虑减仓" 同时含"阻力"和
// "减仓"，若先判观察色就把最需要警示的情况弱化了
const suggestionToneClass = computed(() => {
  const t = supportResistance.value?.suggestion || ''
  // 风险：接近强阻力位，谨慎追高，可考虑减仓
  if (/减仓|谨慎追高|强阻力/.test(t)) return 'bg-red-500/10 text-red-400 border-red-500/30'
  // 机会：接近强支撑位，可考虑轻仓试探 / 超跌反弹机会，可分批建仓
  if (/强支撑|轻仓试探|分批建仓|超跌反弹/.test(t)) return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30'
  // 观察：接近阻力位注意突破 / 位置偏高可持有观望 / 位置偏低等待企稳信号
  if (/阻力|注意观察|位置偏高|等待企稳/.test(t)) return 'bg-amber-500/10 text-amber-400 border-amber-500/30'
  // 中性：处于区间中部，方向不明确，观望为主
  return 'bg-white/5 text-muted border-border'
})

const rsiColor = computed(() => {
  if (!rsiSignals.value) return 'text-muted'
  const z = rsiSignals.value.zone
  return { 'strong_overbought': 'text-red-400', 'overbought': 'text-amber-400',
    'strong_oversold': 'text-emerald-400', 'oversold': 'text-emerald-400',
    'neutral': 'text-muted' }[z] || 'text-muted'
})

const rsiZoneText = computed(() => {
  if (!rsiSignals.value) return ''
  return { 'strong_overbought': '强超买', 'overbought': '超买',
    'strong_oversold': '强超卖', 'oversold': '超卖', 'neutral': '中性' }[rsiSignals.value.zone] || ''
})

function formatVol(v) {
  const n = parseFloat(v)
  if (!n) return '-'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  return (n / 1e4).toFixed(1) + '万'
}
function formatAmt(v) {
  const n = parseFloat(v)
  if (!n) return '-'
  if (n >= 1e12) return (n / 1e12).toFixed(2) + '万亿'
  if (n >= 1e8) return (n / 1e8).toFixed(2) + '亿'
  return (n / 1e4).toFixed(1) + '万'
}

function toggleIndicator(key) {
  const idx = activeIndicators.value.indexOf(key)
  if (idx >= 0) activeIndicators.value.splice(idx, 1)
  else activeIndicators.value.push(key)
  renderKline()
}

function changePeriod(p) {
  period.value = p
  loadKline()
}

async function loadKline() {
  // ★ 本地优先（PLAN_PACK_MIGRATION Phase 2）：K 线包在本地时零后端请求，
  //   消除 /api/stock/kline 的并发爆发（Render OOM 触发点）。
  //   仅日 K 走本地（周K/月K 需重采样，回退后端）；失败自动回退后端接口。
  if (period.value === 'day') {
    try {
      const local = await loadLocalKline(code, stockInfo.value)
      if (local && local.klines?.length) {
        klineData.value = local.klines
        technicalData.value = local.technical
        await nextTick()
        renderKline()
        return
      }
    } catch (e) { console.warn('[detail] 本地K线不可用，回退后端:', e) }
  }
  try {
    const { data: kd } = await getStockKline(code, { period: period.value })
    klineData.value = kd
    const { data: td } = await getStockTechnical(code, period.value)
    technicalData.value = td
    await nextTick()
    renderKline()
  } catch (e) { console.error(e) }
}

// ── 构建支撑阻力标记线 ──
function buildSRMarkLine() {
  if (!supportResistance.value || !supportResistance.value.levels?.length) return { data: [] }
  const lines = supportResistance.value.levels.map(level => {
    const color = level.type === 'resistance' ? '#ef4444' : level.type === 'support' ? '#22c55e' : '#f59e0b'
    return {
      yAxis: level.price,
      label: {
        formatter: `${level.type === 'resistance' ? '阻' : level.type === 'support' ? '支' : ''} ${level.price}`,
        position: 'insideEndTop',
        fontSize: 10,
        color: color,
      },
      lineStyle: { color: color, type: 'dashed', width: 1, opacity: 0.6 },
    }
  })
  return {
    symbol: 'none',
    data: lines,
    animation: false,
  }
}

function renderKline() {
  if (!klineChartRef.value || !klineData.value.length) return

  const dates = klineData.value.map(d => d.date)
  const ohlc = klineData.value.map(d => [d.open, d.close, d.low, d.high])
  const volumes = klineData.value.map(d => d.volume)
  const colors = klineData.value.map(d => d.close >= d.open ? '#ef4444' : '#22c55e')

  // containLabel：由 ECharts 自动为 Y 轴刻度文本预留空间。用百分比边距时，
  // 标签宽度不参与计算，窄容器下标签会挤占绘图区、宽容器下又留出大片空白
  const gridCfg = [{ left: 8, right: 16, top: 26, height: '52%', containLabel: true }]
  const xAxisCfg = [{ type: 'category', data: dates, gridIndex: 0, axisLabel: { fontSize: 10 }, boundaryGap: true }]
  const yAxisCfg = [{ type: 'value', gridIndex: 0, scale: true, splitLine: { lineStyle: { color: '#21262d' } } }]
  const seriesCfg = [
    { name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0, data: ohlc, itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' },
      markLine: buildSRMarkLine(),
    },
  ]

  let gridIdx = 1

  if (activeIndicators.value.includes('ma') && technicalData.value.length) {
    const maColors = ['#f59e0b', '#8b5cf6', '#06b6d4', '#ec4899']
    ;['ma5','ma10','ma20','ma60'].forEach((key, i) => {
      seriesCfg.push({ name: key.toUpperCase(), type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d[key]), lineStyle: { color: maColors[i], width: 1 }, symbol: 'none' })
    })
  }

  if (activeIndicators.value.includes('boll') && technicalData.value.length) {
    seriesCfg.push(
      { name: 'BOLL上', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d.boll_upper), lineStyle: { color: '#6366f1', width: 1, type: 'dashed' }, symbol: 'none' },
      { name: 'BOLL中', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d.boll_mid), lineStyle: { color: '#6366f1', width: 1 }, symbol: 'none' },
      { name: 'BOLL下', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: technicalData.value.map(d => d.boll_lower), lineStyle: { color: '#6366f1', width: 1, type: 'dashed' }, symbol: 'none' },
    )
  }

  if (activeIndicators.value.includes('vol')) {
    gridCfg.push({ left: 8, right: 16, top: '63%', height: '12%', containLabel: true })
    xAxisCfg.push({ type: 'category', data: dates, gridIndex: gridIdx, axisLabel: { show: false }, boundaryGap: true })
    yAxisCfg.push({ type: 'value', gridIndex: gridIdx, scale: true, splitLine: { show: false }, axisLabel: { show: false } })
    seriesCfg.push({ name: '成交量', type: 'bar', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: volumes.map((v, i) => ({ value: v, itemStyle: { color: colors[i], opacity: 0.7 } })), barMaxWidth: 4 })
    gridIdx++
  }

  if (activeIndicators.value.includes('macd') && technicalData.value.length) {
    gridCfg.push({ left: 8, right: 16, top: '78%', height: '15%', containLabel: true })
    xAxisCfg.push({ type: 'category', data: dates, gridIndex: gridIdx, axisLabel: { fontSize: 10 }, boundaryGap: true })
    yAxisCfg.push({ type: 'value', gridIndex: gridIdx, scale: true, splitLine: { show: false }, axisLabel: { show: false } })
    seriesCfg.push(
      { name: 'DIF', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: technicalData.value.map(d => d.dif), lineStyle: { color: '#f59e0b', width: 1 }, symbol: 'none' },
      { name: 'DEA', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: technicalData.value.map(d => d.dea), lineStyle: { color: '#8b5cf6', width: 1 }, symbol: 'none' },
      { name: 'MACD', type: 'bar', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: technicalData.value.map(d => ({ value: d.macd, itemStyle: { color: d.macd >= 0 ? '#ef4444' : '#22c55e' } })), barMaxWidth: 3 },
    )
    gridIdx++
  }

  if (activeIndicators.value.includes('rsi') && rsiSignals.value?.rsi_history?.length) {
    const rsiData = rsiSignals.value.rsi_history
    // 对齐日期：用 K线日期匹配 RSI 日期
    const rsiMap = new Map(rsiData.map(d => [d.date, d.rsi]))
    const alignedRsi = dates.map(dt => rsiMap.get(dt) ?? null)

    gridCfg.push({ left: 8, right: 16, top: '78%', height: '15%', containLabel: true })
    xAxisCfg.push({ type: 'category', data: dates, gridIndex: gridIdx, axisLabel: { fontSize: 10 }, boundaryGap: true })
    yAxisCfg.push({ type: 'value', gridIndex: gridIdx, min: 0, max: 100, splitLine: { show: false }, axisLabel: { show: false } })
    seriesCfg.push(
      { name: 'RSI', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: alignedRsi, lineStyle: { color: '#f59e0b', width: 1.5 }, symbol: 'none' },
      { name: '超买', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: dates.map(() => 70), lineStyle: { color: '#ef4444', width: 1, type: 'dashed' }, symbol: 'none' },
      { name: '超卖', type: 'line', xAxisIndex: gridIdx, yAxisIndex: gridIdx, data: dates.map(() => 30), lineStyle: { color: '#22c55e', width: 1, type: 'dashed' }, symbol: 'none' },
    )
    gridIdx++
  }

  const zoomAxes = xAxisCfg.map((_, i) => i)

  // 默认展示最近 N 根（按数据量换算成百分比）。写死 start:70 时，数据量大就只露
  // 最后 30%、数据少又会把 K 线全挤在右侧，是"左侧大片空白"的直接来源
  const DEFAULT_SHOW = 120
  const zoomStart = dates.length > DEFAULT_SHOW
    ? +(((dates.length - DEFAULT_SHOW) / dates.length) * 100).toFixed(2)
    : 0

  if (charts[0]) charts[0].dispose()
  const chart = echarts.init(klineChartRef.value, 'dark')
  chart.setOption({
    backgroundColor: 'transparent', textStyle: { color: '#8b949e' },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    legend: { data: seriesCfg.map(s => s.name), textStyle: { color: '#8b949e', fontSize: 10 }, top: 0, itemWidth: 12, itemHeight: 8 },
    grid: gridCfg, xAxis: xAxisCfg, yAxis: yAxisCfg,
    dataZoom: [
      { type: 'inside', xAxisIndex: zoomAxes, start: zoomStart, end: 100 },
      { type: 'slider', xAxisIndex: zoomAxes, bottom: 0, height: 18, borderColor: '#30363d', backgroundColor: '#161b22', fillerColor: 'rgba(88,166,255,0.1)', textStyle: { color: '#8b949e', fontSize: 10 } },
    ],
    series: seriesCfg,
  })
  charts[0] = chart

  // 容器刚由 v-if 挂载时可能还没完成最终布局，此时 init 会按错误宽度计算绘图区。
  // 下一帧补一次 resize，让图表按真实宽度重绘
  requestAnimationFrame(() => { if (charts[0] === chart) chart.resize() })
}

// 页签切换后重画对应图表：容器此前隐藏（ref 为 null）时 render 函数是空操作，
// 切到可见后必须手动补一次渲染（nextTick 等 DOM 布局完成）
watch(activeTab, (t) => {
  nextTick(() => {
    if (t === 'kline') renderKline()
    else if (t === 'rank') renderRankHistory()
    else if (t === 'score') renderNewsSpark()   // 消息面在评分页签右列，切回时容器重挂载需重画
  })
})

/**
 * 本地评分 + 本地K线一次取齐（PLAN_PACK_MIGRATION Phase 2）。
 * K 线包在本地时：评分用指标包 _series（与后端同一事实源）、K 线用本地包——
 * 零 /api/score/{code} 与 /api/stock/kline 调用（Render 0.1CPU 的 OOM 触发点）。
 * 任一数据缺失返回 null，调用方回退后端接口。
 */
async function tryLoadLocalAll() {
  const out = { score: null, klines: null, technical: null }
  try {
    // 财报（轻接口，本地库查询）：扁平形状与 scoreStock finance 参数一致
    let finance = null
    try {
      const { data: fin } = await getStockFinance(code)
      if (fin && (fin.revenue_yoy != null || fin.roe != null)) finance = fin
    } catch { finance = null }
    // 当前生效权重（与排行榜同源；失败用引擎默认）
    let weights = null
    try {
      const w = await getScoreWeights()
      weights = (w && w.data && w.data.weights) || null
    } catch { weights = null }

    out.score = await computeLocalScore(code, stockInfo.value, finance, weights)
    const local = await loadLocalKline(code, stockInfo.value)
    if (local) {
      out.klines = local.klines
      out.technical = local.technical
    }
  } catch (e) { console.warn('[detail] 本地评分/K线失败:', e) }
  return out.score || out.klines ? out : { score: null, klines: null, technical: null }
}

onMounted(async () => {
  // 消息面独立加载（首次需拉东财快讯，不阻塞主数据渲染）
  getStockNews(code).then(({ data }) => { newsData.value = data }).catch(() => {})
  getStockNewsHistory(code, 30).then(({ data }) => { newsHistory.value = data.history || [] }).catch(() => {})
  try {
    // ★ 本地优先：实时行情（轻）先行，评分/K线尝试全本地（零后端重接口）；
    //   支撑阻力/RSI 为轻量查询照常并行。本地不可用再回退后端精算。
    const [info, fund, sr, rsi] = await Promise.allSettled([
      getStockRealtime(code),
      getStockFundamental(code),
      getSupportResistance(code),
      getRSISignals(code),
    ])
    if (info.status === 'fulfilled') stockInfo.value = info.value.data || {}
    if (fund.status === 'fulfilled') fundamental.value = fund.value.data || {}
    if (sr.status === 'fulfilled' && sr.value.data) supportResistance.value = sr.value.data.data
    if (rsi.status === 'fulfilled' && rsi.value.data) rsiSignals.value = rsi.value.data.data

    const local = await tryLoadLocalAll()
    if (local.score) {
      scoreData.value = local.score
    } else {
      // 本地不可用（未下 K 线包/该股不在包内）→ 后端精算兜底
      const score = await getStockScore(code).catch(() => null)
      if (score && score.data) scoreData.value = score.data
    }
    if (local.klines) {
      klineData.value = local.klines
      technicalData.value = local.technical
    }
  } catch (e) { console.error(e) }

  // 先放开首屏（默认页签是"评分"，不依赖 K 线容器）；K 线数据并行加载，
  // 切到 K 线页签时由 watch(activeTab) 补渲染——数据已就绪则立即出图，
  // 用户抢先切页签（数据未回）时 loadKline 完成后也会兜底重画
  loaded.value = true
  if (!klineData.value.length) await loadKline()

  // 评分 vs 价格历史（独立加载，失败静默——数据积累需要时间）
  getRankHistory(code, 30)
    .then(({ data }) => {
      rankHistoryData.value = data
      nextTick(renderRankHistory)
    })
    .catch(() => {})

  window.addEventListener('resize', () => charts.forEach(c => c && c.resize()))
})

onBeforeUnmount(() => { charts.forEach(c => c && c.dispose()); charts = [] })

watch(() => route.params.code, () => { window.location.reload() })
</script>
