<template>
  <div class="fade-in space-y-3">
    <!-- ① 头部 -->
    <div class="bg-card border border-border rounded-lg p-3">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3 flex-wrap">
          <h1 class="text-lg font-bold text-gray-100">题材轮动</h1>
          <span class="text-[11px] text-muted">风向标 · 看人气与阶段，不是买卖信号</span>
          <span v-if="data.date" class="text-[11px] px-2 py-0.5 rounded bg-white/5 text-gray-300 font-mono">
            {{ data.date }}<template v-if="data.prev_date"> ← {{ data.prev_date }}</template>
          </span>
          <span v-if="data.regime" class="text-[11px] px-2 py-0.5 rounded font-semibold"
                :class="regimeBadge(data.regime.level)">
            {{ data.regime.label }} · 均涨 {{ fmt(data.regime.avg_pct, 2) }}% ·
            {{ fmt((data.regime.up_ratio || 0) * 100, 0) }}% 上涨
          </span>
        </div>
        <div class="flex items-center gap-2">
          <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
            <button v-for="t in TYPES" :key="t.v"
                    class="px-2.5 py-1 rounded text-xs transition-colors"
                    :class="plateType === t.v ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
                    @click="plateType = t.v; reload()">{{ t.label }}</button>
          </div>
          <input v-model="day" type="date"
                 class="px-2 py-1 rounded text-xs bg-bg border border-border text-gray-200" />
          <button @click="reload()" :disabled="loading"
                  class="px-3 py-1 rounded text-xs border border-border text-muted hover:text-gray-200 disabled:opacity-50">
            {{ loading ? '…' : '↻' }}
          </button>
        </div>
      </div>
      <div class="flex items-center justify-between mt-2 flex-wrap gap-2">
        <div class="flex items-center gap-4 text-[11px] text-muted flex-wrap">
          <span>在榜 <b class="text-gray-200 font-mono">{{ data.counts?.total || 0 }}</b></span>
          <span>新进 <b class="text-accent font-mono">{{ data.counts?.new_in || 0 }}</b></span>
          <span>掉队 <b class="text-fall font-mono">{{ data.counts?.drop || 0 }}</b></span>
          <span v-if="error" class="text-fall">⚠️ {{ error }}</span>
        </div>
        <!-- ★ 页面太长信息不集中 ⇒ 按看盘动线分三个 tab：
             盘中决策（流图+热力图+阶段+卡片，联动强关联必须同屏）/
             细分题材库（zzshare 另一套口径，独立研究）/
             信号验证（低频，点计算才出数）。头部是三个 tab 共用的市况上下文，不进 tab。 -->
        <div class="flex items-center gap-1 bg-bg border border-border rounded-lg p-0.5">
          <button v-for="t in TABS" :key="t.v"
                  class="px-2.5 py-1 rounded text-xs transition-colors"
                  :class="activeTab === t.v ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'"
                  @click="activeTab = t.v">
            {{ t.label }}<span v-if="t.v === 'lib' && lib.n"
                               class="ml-1 text-[10px] text-muted font-mono">{{ lib.n }}</span>
          </button>
        </div>
      </div>
    </div>

    <!-- ①·五 政策脉冲（★ 2026-11-28）：政策快讯 → 受益板块。
         用户复盘 11-27："政策中午发文，午盘开盘猛拉，我当时不知所措" ——
         实测 13:24 政策 → 13:33 中证1000期货涨停，只有 9 分钟。
         本卡只回答"什么级别、影响哪些板块"，**不回答买什么**（纯观察器）。 -->
    <!-- 显示条件：有政策事件 / 有期货涨跌停 / 广度极端（全面暴涨或暴跌）。
         平时的"分化/普涨"不弹卡 —— 否则每天都有，变成噪音。 -->
    <div v-if="(pulse.events && pulse.events.length)
              || (pulse.futures_limit || []).length
              || (pulse.breadth && (pulse.breadth.level === 'extreme_up'
                                    || pulse.breadth.level === 'extreme_down'))
              || (pulse.open_pulse && ['extreme', 'strong'].includes(pulse.open_pulse.level))"
         class="bg-card border border-amber-500/30 rounded-lg p-3">
      <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div class="flex items-center gap-2">
          <span class="text-sm font-bold text-amber-300">⚡ 政策脉冲</span>
          <span class="text-[11px] text-muted">{{ pulse.date }} · {{ pulse.events.length }} 条</span>
        </div>
        <span class="text-[10px] text-muted">{{ pulse.note }}</span>
      </div>
      <!-- ★ 2026-11-28 广度确认：11-27 的完整信号是「政策 + 期货涨停 + 全面暴涨」，
           只给政策不给广度仍然不完整。两条口径不同，分开标：
           广度=收盘定稿板块口径；期货涨跌停=盘中快讯识别。 -->
      <div v-if="pulse.breadth || (pulse.futures_limit || []).length || pulse.open_pulse"
           class="flex items-center gap-2 text-[11px] mb-2 flex-wrap pb-2 border-b border-border/25">
        <span class="text-muted shrink-0">广度</span>
        <span v-if="pulse.breadth" class="px-1.5 py-0.5 rounded font-semibold"
              :class="breadthCls(pulse.breadth.level)"
              :title="'板块口径：' + pulse.breadth.n_sectors + ' 个，中位 ' + pulse.breadth.median_chg + '%'">
          {{ pulse.breadth.verdict }} {{ fmt((pulse.breadth.up_ratio || 0) * 100, 0) }}% 上涨
          · 暴涨 {{ pulse.breadth.strong_up }} 个
        </span>
        <span v-for="f in (pulse.futures_limit || [])" :key="f.product + f.kind"
              class="px-1.5 py-0.5 rounded font-mono"
              :class="f.kind === 'limit_up' ? 'bg-rise/15 text-rise' : 'bg-fall/15 text-fall'"
              :title="f.text">
          {{ f.name }}{{ f.kind === 'limit_up' ? '涨停' : '跌停' }}
          <span class="text-[9px] opacity-70">{{ (f.time || '').slice(11, 16) }}</span>
        </span>
        <!-- ★★ 午盘开盘异动：11-27 实测比首条政策快讯早 19 分钟 -->
        <span v-if="pulse.open_pulse"
              class="px-1.5 py-0.5 rounded font-semibold"
              :class="['extreme', 'strong'].includes(pulse.open_pulse.level)
                ? 'bg-rise/20 text-rise' : 'bg-white/5 text-muted'"
              :title="pulse.open_pulse.caliber">
          {{ pulse.open_pulse.verdict }} {{ pulse.open_pulse.pm5_max }}%
          <span class="text-[9px] opacity-70">
            {{ ((pulse.open_pulse.indices || [])[0] || {}).name }}
          </span>
        </span>
      </div>
      <div v-if="pulse.events && pulse.events.length" class="space-y-1.5">
        <div v-for="e in pulse.events" :key="e.theme"
             class="flex items-start gap-2 text-[11px] flex-wrap">
          <span class="font-mono text-muted shrink-0">{{ (e.first_time || '').slice(11, 16) }}</span>
          <span class="shrink-0 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-300">
            {{ e.actor || '政策' }}
          </span>
          <span class="shrink-0 font-medium text-gray-200">{{ e.theme }}</span>
          <span class="text-muted truncate max-w-[300px]" :title="e.title">{{ e.title }}</span>
          <span class="flex items-center gap-1 flex-wrap">
            <span v-for="s in e.sectors" :key="s.name"
                  class="px-1.5 py-0.5 rounded bg-white/5 font-mono"
                  :class="pctCls(s.chg_pct)">
              {{ s.name }}<template v-if="s.chg_pct !== null && s.chg_pct !== undefined"> {{ fmt(s.chg_pct, 1) }}%</template>
            </span>
          </span>
        </div>
      </div>
    </div>

    <!-- ② Tab1 盘中决策：热力图→流图→阶段→卡片/个股，一条决策链（联动强关联，必须同屏）。
         ★ 2026-11-28 两图互换：热力图（全局格局）在前、流图（单题材轨迹）在后，
         复盘动线 = 先扫全局再追细节 -->
    <div v-show="activeTab === 'trade'" class="space-y-3">
      <!-- ③ 多日排名热力图（★ 2026-11-28 由对比表升级：格子越红名次越靠前，
          一眼看出谁持续霸榜、谁一日游、谁在爬坡——表格要逐格扫，热力图扫一眼即可） -->
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="flex items-center justify-between mb-1 flex-wrap gap-2">
          <div class="flex items-center gap-1.5">
            <h2 class="text-sm font-bold text-gray-200">多日排名热力图</h2>
            <span class="text-[11px] text-muted cursor-help hover:text-gray-300"
                  title="行 = 题材（名字颜色同流图，按最新名次从上到下）；格子越红名次越靠前，暗格 = 掉榜；列数随「回看」天数；悬停格子看名次变化与当日涨幅，悬停行名流图里只突出它的线。">ⓘ</span>
          </div>
          <div class="flex items-center gap-1 text-[11px] shrink-0">
            <span class="text-muted">回看</span>
            <button v-for="d in [5, 8, 12, 20]" :key="d"
                    class="px-2 py-0.5 rounded border transition-colors"
                    :class="histDays === d ? 'border-accent/40 bg-accent/15 text-accent' : 'border-border text-muted hover:text-gray-200'"
                    @click="histDays = d; loadHistory()">{{ d }}日</button>
          </div>
        </div>
        <div ref="heatRef" class="w-full" :style="{ height: heatH + 'px' }"></div>
        <div v-if="!hist.items?.length" class="text-xs text-muted py-4 text-center">
          {{ histErr || '暂无数据' }}
        </div>
      </div>

      <!-- ② 排名流图（核心可视化） -->
      <div class="bg-card border border-border rounded-lg p-3">
        <!-- ★ 2026-11-28 阅读说明收进 ⓘ 悬停提示、图例与标题同行（用户要求省空间给图表）：
            单行 = 标题 + ⓘ + 图例12项，说明行与独立图例行全部省掉。 -->
        <div class="flex items-center justify-between mb-1 flex-wrap gap-2">
          <div class="flex items-center flex-wrap gap-x-2.5 gap-y-1 min-w-0">
            <h2 class="text-sm font-bold text-gray-200 shrink-0">排名流图</h2>
            <span class="text-[11px] text-muted cursor-help hover:text-gray-300 shrink-0"
                  title="阅读方式：纵轴是名次（1 在最上），线往上走 = 人气在进来，断线 = 当天掉出榜，只画最近进过前 20 的题材。
  颜色图例：色点 = 线条颜色（按题材固定，不随名次变）；悬停图例只突出这条线，点击显示/隐藏该题材。">ⓘ</span>
            <!-- 颜色图例：色点=流图线条颜色（按题材 code 固定，不随名次变） -->
            <button v-for="lg in legendItems" :key="lg.code"
                    class="flex items-center gap-1 text-[10px] transition-opacity"
                    :class="hiddenCodes.has(lg.code) ? 'opacity-30 line-through' : 'hover:opacity-100'"
                    @click="toggleSeries(lg.code)"
                    @mouseenter="hoverSeries(lg.code)"
                    @mouseleave="hoverSeries(null)">
              <span class="w-2 h-2 rounded-sm shrink-0" :style="{ background: topicColor(lg.code) }"></span>
              <span class="text-gray-400">{{ lg.name }}</span>
            </button>
          </div>
        </div>
        <div ref="chartRef" class="w-full" style="height: 287px"></div>
        <div v-if="!hist.items?.length" class="text-xs text-muted py-6 text-center">
          {{ histErr || '暂无排名序列（需要至少 2 个交易日数据）' }}
        </div>
      </div>

      <!-- ④ 主题材卡片 + 个股（★ 2026-11-28 阶段分布模块已融合进卡片区：
          四阶段变成卡片筛选 tab（带数量），卡片名前带阶段图标——同一信息不再占一个独立模块） -->
      <div class="grid grid-cols-1 xl:grid-cols-[1.55fr_1fr] gap-3">
        <!-- 卡片网格 -->
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="flex items-center justify-between mb-4 flex-wrap gap-2">
            <div class="flex items-center gap-1.5 shrink-0">
              <h2 class="text-sm font-bold text-gray-200">全部在榜题材</h2>
              <span class="text-[11px] text-muted cursor-help hover:text-gray-300"
                    title="交互：点任意题材查看相关个股（当日涨幅最高的成分股）；悬停卡片，流图里只突出它的线。
  阶段图标（规则化判定，不是预测）：🚀 刚进榜在加速 / 📈 前列且在加速 / ⚠️ 前排不再加速（可能见顶） / 📉 退潮。
  排序：资金 = 谁在真金白银流入；加速度 = 谁刚启动（5 日明显强于 20 日，缺 20 日档的排最后）。
  第二行横条 = 当前所选排序维度的相对值（面板内最大 = 满格，颜色 = 题材固定色）；切换排序 tab 横条随之切换，负值不画。">ⓘ</span>
            </div>
            <div class="flex items-center gap-1 flex-wrap">
              <!-- 阶段筛选 tab（原独立"阶段分布"模块融合至此，数字=该阶段在榜数） -->
              <button @click="stageFilter = 'all'"
                      class="px-2 py-0.5 rounded text-[10px] border transition-colors"
                      :class="stageFilter === 'all'
                        ? 'border-accent/40 bg-accent/15 text-accent'
                        : 'border-border text-muted hover:text-gray-200'">
                全部 {{ stageCounts.all }}
              </button>
              <button v-for="g in STAGES" :key="g.key"
                      @click="stageFilter = g.key"
                      class="px-2 py-0.5 rounded text-[10px] border transition-colors"
                      :class="stageFilter === g.key
                        ? 'border-accent/40 bg-accent/15 text-accent'
                        : 'border-border text-muted hover:text-gray-200'"
                      :title="g.label + '（规则化判定，不是预测）'">
                {{ g.icon }}{{ g.label }} {{ stageCounts[g.key] }}
              </button>
              <button @click="stageFilter = 'new'"
                      class="px-2 py-0.5 rounded text-[10px] border transition-colors"
                      :class="stageFilter === 'new'
                        ? 'border-amber-400/50 bg-amber-500/15 text-amber-300'
                        : 'border-border text-muted hover:text-gray-200'">
                NEW {{ stageCounts.new }}
              </button>
              <span class="mx-1 h-3 w-px bg-border shrink-0"></span>
              <button v-for="s in SORTS" :key="s.v"
                      class="px-2 py-0.5 rounded text-[10px] border transition-colors"
                      :class="sortBy === s.v
                        ? 'border-accent/40 bg-accent/15 text-accent'
                        : 'border-border text-muted hover:text-gray-200'"
                      @click="sortBy = s.v">{{ s.label }}</button>
            </div>
          </div>
          <div class="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-3 gap-1.5 max-h-[560px] overflow-y-auto pr-1">
            <!-- ★ 职业视角卡片：左色条=题材固定色（与流图线条一致，记忆锚点）。
                第二行可视化（2026-11-28，用户要求去拥挤）：数字墙 → 相对条形——
                条形长度 = 当前排序维度（涨幅/资金/加速度）在面板内的相对值，
                颜色 = 题材固定色；眼睛扫长度代替读数字，细节（资金/加速度具体值）
                收进 hover 提示。NEW/名次跃升保留徽章。 -->
            <button v-for="x in sortedTop" :key="x.code"
                    class="text-left rounded border px-2 py-1.5 transition-colors"
                    :class="cardCls(x)"
                    :style="cardStyle(x)"
                    :title="`${x.name}（${stageOf(x.code) || '未定阶段'}）
  涨幅名次 #${x.rank} · 当日 ${fmt(x.sum_rate, 1)}%${rankChangeOf(x.code) ? ` · 较昨日${rankChangeOf(x.code) > 0 ? '↑' : '↓'}${Math.abs(rankChangeOf(x.code))}` : ''}
  龙头资金 ${x.money_yi != null ? fmt(x.money_yi, 1) + ' 亿' : '—'} · 加速度 ${x.accel != null ? fmt(x.accel, 1) : '—'}${x.is_new ? ' · 新进榜' : ''}`"
                    @click="pick(x)"
                    @mouseenter="hoverSeries(x.code)"
                    @mouseleave="hoverSeries(null)">
              <div class="flex items-center justify-between gap-1">
                <span class="text-[11px] font-medium truncate">
                  <span class="shrink-0">{{ stageIconOf(x.code) }}</span>{{ x.name }}
                </span>
                <span class="text-[10px] font-mono shrink-0" :class="pctCls(x.sum_rate)">
                  {{ fmt(x.sum_rate, 1) }}%
                </span>
              </div>
              <div class="flex items-center gap-1.5 mt-1 text-[9px] font-mono">
                <span class="text-muted shrink-0">#{{ x.rank }}</span>
                <span v-if="rankChangeOf(x.code)" class="shrink-0 font-bold"
                      :class="rankChangeOf(x.code) > 0 ? 'text-rise' : 'text-fall'">
                  {{ rankChangeOf(x.code) > 0 ? '↑' : '↓' }}{{ Math.abs(rankChangeOf(x.code)) }}
                </span>
                <span v-if="x.is_new"
                      class="shrink-0 px-1 rounded font-bold bg-amber-500/20 text-amber-300">NEW</span>
                <span class="flex-1 h-1 rounded-full bg-white/5 overflow-hidden">
                  <span class="block h-full rounded-full transition-all"
                        :style="{ width: sortBarW(x) + '%', background: topicColor(x.code) }"></span>
                </span>
              </div>
            </button>
            <div v-if="!sortedTop.length" class="col-span-full text-[11px] text-muted py-4 text-center">
              无在榜题材
            </div>
          </div>
        </div>

        <!-- 个股面板 -->
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="flex items-center justify-between mb-2">
            <h2 class="text-sm font-bold text-gray-200 truncate">
              {{ sel ? sel.name + ' · 相关个股' : '相关个股' }}
            </h2>
            <span v-if="stk.n_total" class="text-[10px] text-muted">
              {{ stk.n_total }} 只成分<template v-if="stk.date"> · 行情截至 {{ stk.date.slice(5) }}</template>
            </span>
          </div>
          <p v-if="!sel" class="text-[11px] text-muted py-6 text-center">
            左侧点一个题材，这里显示它当日涨幅最高的成分股。
          </p>
          <div v-else-if="stkLoading" class="text-[11px] text-muted py-6 text-center">加载中…</div>
          <p v-else-if="stkErr" class="text-[11px] text-fall py-6 text-center">⚠️ {{ stkErr }}</p>
          <div v-else class="space-y-0.5">
            <div class="flex items-center gap-2 text-[10px] text-muted border-b border-border/30 pb-1">
              <span class="flex-1">名称</span>
              <span class="w-16 text-right">当日</span>
              <span class="w-12 text-right">5日</span>
              <span class="w-12 text-right">评分</span>
            </div>
            <!-- ★ 前 3 = 龙一/龙二/龙三（当日涨幅最高的即"龙头"，题材行情看龙头脸色）；
                当日列带相对涨幅条（面板内最大涨幅=满格），扫一眼知道谁在领涨。 -->
            <div v-for="(s, i) in (stk.items || [])" :key="s.code"
                class="flex items-center gap-2 text-[11px] py-0.5 hover:bg-white/5 rounded px-1">
              <span class="flex-1 truncate">
                <span v-if="i < 3" class="text-[9px] px-1 rounded font-bold mr-1"
                      :class="i === 0 ? 'bg-amber-500/25 text-amber-300'
                        : i === 1 ? 'bg-slate-400/20 text-slate-300'
                        : 'bg-orange-800/30 text-orange-400'">龙{{ '一二三'[i] }}</span>
                <StockName :code="s.code" :name="s.name" />
              </span>
              <span class="w-16 text-right relative">
                <span v-if="(s.chg_pct || 0) > 0"
                      class="absolute inset-y-0 right-0 rounded bg-rise/15"
                      :style="{ width: pctBar(s.chg_pct) + '%' }"></span>
                <span class="relative font-mono" :class="pctCls(s.chg_pct)">{{ fmt(s.chg_pct, 2) }}%</span>
              </span>
              <span class="w-12 text-right font-mono text-muted">{{ fmt(s.ret5, 1) }}</span>
              <span class="w-12 text-right font-mono text-muted">{{ fmt(s.score, 0) }}</span>
            </div>
          </div>
          <p v-if="sel && !stkLoading && !stkErr && stk.note"
            class="text-[10px] text-muted mt-2 leading-relaxed">{{ stk.note }}</p>
        </div>
      </div>
    </div><!-- /Tab1 盘中决策 -->

    <!-- ⑤·五 Tab2 细分题材库（zzshare topic_table，官方阶段 + 驱动逻辑 + 个股相关性） -->
    <div v-show="activeTab === 'lib'" class="bg-card border border-border rounded-lg p-3">
      <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
        <div>
          <h2 class="text-sm font-bold text-gray-200">细分题材库</h2>
          <p class="text-[11px] text-muted mt-0.5">
            {{ lib.n || 0 }} 个细分题材 · 阶段由 zzshare 官方算法给出（topic_stage_v2）。
            <b class="text-gray-300">与上方板块榜是两套口径</b>（名称只重合 9/140，不强行匹配）。
          </p>
        </div>
        <div class="flex items-center gap-1 flex-wrap">
          <!-- ★ 2026-11-28 tab 可视化：选中态随阶段切换递进色（浅橙→深红）；
               「全部」选中态用中性灰白；文字加粗便于扫视 -->
          <button v-for="g in libGroups" :key="g.key"
                  class="px-2 py-0.5 rounded text-[10px] font-semibold border transition-colors"
                  :style="libFilter === g.key && g.key !== 'all'
                    ? { backgroundColor: libStage(g.key).bgC, borderColor: libStage(g.key).borderC, color: libStage(g.key).textC }
                    : {}"
                  :class="libFilter === g.key
                    ? (g.key === 'all' ? 'border-gray-300/50 bg-white/10 text-gray-100' : 'border')
                    : 'border-border text-muted hover:text-gray-200'"
                  @click="libFilter = g.key">
            {{ g.label }} {{ g.count }}
          </button>
        </div>
      </div>
      <!-- 阶段占比条：一眼看清 140 个细分题材的结构（数量降序排列，色=阶段语义色） -->
      <div v-if="lib.n" class="flex h-2 rounded-full overflow-hidden mb-2 bg-white/5" title="阶段结构占比（悬停各分段无提示，看下方 tab 数字）">
        <div v-for="g in libGroups.filter(x => x.key !== 'all')" :key="g.key"
             :class="libDot(g.key)" :style="{ width: (g.count / lib.n * 100) + '%' }"></div>
      </div>
      <!-- ★ 2026-11-28 左列改固定宽度（列表只需 名字+涨幅条），余下全给右侧详情；
           详情面板 flex 纵向布局，相关个股区 flex-1 自动吃满剩余高度 -->
      <div class="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-3">
        <!-- 左：题材列表（★ 2026-11-28 高度加大到一屏；涨幅带相对条形，扫长度代替读数） -->
        <div class="space-y-0.5 max-h-[640px] overflow-y-auto pr-1">
          <!-- ★ 2026-11-28（用户指定）：整个列表项铺蓝阶背景色（浅蓝→深蓝 = 生命周期），
               文字色随底色深浅配对（浅底深字 / 深底浅字），选中态 = 同色系边框。
               阶段/连续性收进 hover。单行化：名字 + 股数 + 涨幅条 -->
          <button v-for="x in libFiltered" :key="x.unique_key"
                  class="w-full text-left px-2 py-1.5 rounded border transition-colors"
                  :style="{
                    backgroundColor: libStage(x.stage_tag).bgC,
                    borderColor: libSel && libSel.unique_key === x.unique_key ? libStage(x.stage_tag).borderC : 'transparent',
                  }"
                  :title="`${x.stage_tag} · ${x.continuity_state}`"
                  @click="pickLib(x)">
            <div class="flex items-center gap-2">
              <span class="text-[11px] truncate flex-1 min-w-0 font-medium"
                    :style="{ color: libStage(x.stage_tag).textC }">
                {{ x.name }}
              </span>
              <span class="font-mono text-[9px] shrink-0 opacity-70"
                    :style="{ color: libStage(x.stage_tag).textC }">{{ x.stock_count }}只</span>
              <span class="w-20 shrink-0 text-right relative">
                <span v-if="(x.today_pct || 0) > 0"
                      class="absolute inset-y-0 right-0 rounded bg-rise/15"
                      :style="{ width: libBar(x) + '%' }"></span>
                <span class="relative font-mono text-[10px]" :class="pctCls(x.today_pct)">
                  {{ fmt(x.today_pct, 1) }}%
                </span>
              </span>
            </div>
          </button>
          <div v-if="!libFiltered.length" class="text-[11px] text-muted px-2 py-4">该类暂无</div>
        </div>

        <!-- 右：详情（驱动逻辑 + 个股相关性）。flex 纵向 + max-h 640（与左列表等高，
             grid 行高不能靠 stretch 约束——行高本身由最高子项决定）：
             相关个股区 flex-1 吃满剩余高度，超出滚动 -->
        <div class="border border-border/50 rounded-lg p-3 flex flex-col max-h-[640px] min-h-0">
          <p v-if="!libSel" class="text-[11px] text-muted py-8 text-center">
            左侧点一个题材，这里显示它的<b class="text-gray-300">驱动逻辑</b>和
            <b class="text-gray-300">相关个股（含相关性原文）</b>。
          </p>
          <div v-else-if="libLoading" class="text-[11px] text-muted py-8 text-center">加载中…</div>
          <p v-else-if="libErr" class="text-[11px] text-fall py-8 text-center">⚠️ {{ libErr }}</p>
          <template v-else>
            <div class="flex items-center justify-between gap-2 mb-1.5">
              <span class="text-xs font-semibold text-gray-200 truncate">{{ libSel.name }}</span>
              <span class="text-[11px] font-mono shrink-0" :class="pctCls(libDetail.today_pct)">
                {{ fmt(libDetail.today_pct, 2) }}%
              </span>
            </div>
            <div class="text-[10px] mb-1 flex items-center gap-1.5">
              <span class="w-1.5 h-1.5 rounded-full" :class="libDot(libSel.stage_tag)"></span>
              <!-- ★ 文字回归页面主题色（textC 是配列表底色的，深底上看不清） -->
              <span class="text-gray-200 font-medium">{{ libSel.stage_tag }} · {{ libSel.continuity_state }}</span>
              <span class="text-muted">
                <template v-if="libSel.confidence"> · 置信度
                  <b :class="confCls(libSel.confidence)" :title="'官方算法置信度，≥80 可信 / 60~79 一般 / <60 存疑'">{{ libSel.confidence }}</b>
                </template>
              </span>
            </div>
            <!-- 阶段变化（源端无历史接口 ⇒ 从上线日起逐日积累） -->
            <div v-if="(libHist.points || []).length > 1"
                 class="text-[10px] mb-2 flex items-center gap-1 flex-wrap">
              <span class="text-muted">阶段变化</span>
              <template v-for="(p, i) in libHist.points" :key="p.date">
                <span class="px-1 rounded bg-white/5 text-gray-400">{{ p.date.slice(5) }}</span>
                <span class="text-gray-300">{{ p.stage_tag }}</span>
                <span v-if="i < libHist.points.length - 1" class="text-muted">→</span>
              </template>
            </div>
            <!-- ★ 2026-11-28 关键内容高亮：利好词红 / 利空词绿 / 百分比数字提亮 -->
            <div class="text-[10px] text-gray-300 leading-relaxed max-h-[200px] overflow-y-auto
                        bg-black/20 rounded p-2 mb-2 whitespace-pre-line"
                 v-html="highlightContent(libDetail.content)"></div>
            <div class="flex items-center justify-between text-[10px] text-muted mb-1">
              <span>相关个股 <b class="text-gray-300">{{ libDetail.n_rows || 0 }}</b> 条</span>
              <span v-if="libDetail.n_with_quote">
                {{ libDetail.n_with_quote }} 只有行情 · 按当日涨跌排
              </span>
            </div>
            <div class="flex items-center gap-2 text-[9px] text-muted border-b border-border/30 pb-0.5 mb-0.5">
              <span class="w-32 text-right">个股</span>
              <span class="w-12 text-right">当日</span>
              <span class="flex-1">相关性</span>
            </div>
            <div class="flex-1 min-h-0 overflow-y-auto">
              <div v-for="(r, i) in (libDetail.rows || [])" :key="i"
                   class="flex items-start gap-2 text-[10px] py-0.5 border-b border-border/15">
                <!-- ★ 2026-11-28：原版代码只藏在 :title 里、链接硬编码 '/stock/'。
                     改用 StockName 后名称与代码都可见且分别跳转；列宽 16→32 容纳"名称（代码）"。 -->
                <div v-if="r.code" class="w-32 shrink-0 flex justify-end font-medium overflow-hidden">
                  <StockName :code="r.code" :name="r.stock" />
                </div>
                <span v-else class="w-32 shrink-0 text-right text-gray-300 font-medium">{{ r.stock }}</span>
                <span class="w-12 text-right font-mono shrink-0" :class="pctCls(r.chg_pct)">
                  {{ fmt(r.chg_pct, 2) }}%
                </span>
                <!-- ★ 2026-11-28 相关性原文同样关键内容高亮（利好红/利空绿/数字提亮） -->
                <span class="flex-1 text-muted leading-snug" :title="r.rel"
                      v-html="highlightContent(r.rel)"></span>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>

    <!-- ⑥ Tab3 信号验证（牛熊分层，低频，需要时再算） -->
    <div v-show="activeTab === 'verify'" class="bg-card border border-border rounded-lg p-3">
      <div class="flex items-center justify-between mb-2">
        <div>
          <h2 class="text-sm font-bold text-gray-200">信号在不同环境下的表现</h2>
          <p class="text-[11px] text-muted mt-0.5">
            新进榜题材持有 5 日的超额收益。<b class="text-gray-300">弱势环境胜率 42%</b> ——追新必亏。
          </p>
        </div>
        <button @click="loadVerify" :disabled="vLoading"
                class="text-[10px] px-2 py-1 rounded border border-border text-muted hover:text-accent disabled:opacity-50">
          {{ vLoading ? '计算中…' : (vRows.length ? '重算' : '计算') }}
        </button>
      </div>
      <table v-if="vRows.length" class="w-full text-[11px]">
        <thead class="text-muted">
          <tr class="border-b border-border/40">
            <th class="text-left py-1 font-normal">环境</th>
            <th class="text-right font-normal">超额</th>
            <th class="text-right font-normal">胜率</th>
            <th class="text-right font-normal">样本</th>
            <th class="text-right font-normal">判定</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in vRows" :key="r.label" class="border-b border-border/20">
            <td class="py-1">{{ r.label }}</td>
            <td class="text-right font-mono" :class="pctCls(r.excess)">{{ fmt(r.excess, 3) }}%</td>
            <td class="text-right font-mono" :class="(r.win || 0) >= 0.5 ? 'text-gray-200' : 'text-fall'">
              {{ fmt((r.win || 0) * 100, 1) }}%
            </td>
            <td class="text-right font-mono text-muted">{{ r.n }}</td>
            <td class="text-right">
              <span class="px-1.5 py-0.5 rounded text-[10px]" :class="verdictCls(r)">{{ verdictText(r) }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="text-[11px] text-muted">点「计算」跑一次事件研究（扫全表，约 1~2 秒）。</p>
    </div>

    <p class="text-[10px] text-muted px-1">{{ data.note }}</p>
  </div>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
// ★ 2026-11-28：个股展示统一走共享组件（名称带代码 + 名称跳详情 + 代码跳雪球）
import StockName from '../components/StockName.vue'
import {
  getTopicRotationDashboard, getTopicRotationVerify,
  getTopicRotationHistory, getTopicRotationConstituents,
  getTopicLib, getTopicDetail, getTopicLibHistory,
  getPolicyPulse,
} from '../api/modules/market'

const TYPES = [{ v: 17, label: '题材' }, { v: 15, label: '概念' }, { v: 14, label: '行业' }]
// 页面按看盘动线分三个 tab：决策链（强关联同屏）/ 题材库（另一套口径）/ 信号验证（低频）
const TABS = [
  { v: 'trade', label: '盘中决策' },
  { v: 'lib', label: '细分题材库' },
  { v: 'verify', label: '信号验证' },
]
// 卡片区三种排序：涨幅（名次）/ 资金（谁在真金白银流入）/ 加速度（谁刚启动）
const SORTS = [
  { v: 'rank', label: '涨幅' },
  { v: 'money', label: '资金' },
  { v: 'accel', label: '加速度' },
]
const STAGES = [
  { key: 'emerging', icon: '🚀', label: '启动', cls: 'text-rise', box: 'border-rise/30 bg-rise/5' },
  { key: 'leading', icon: '📈', label: '主升', cls: 'text-accent', box: 'border-accent/30 bg-accent/5' },
  { key: 'crowded', icon: '⚠️', label: '高位', cls: 'text-amber-400', box: 'border-amber-500/30 bg-amber-500/5' },
  { key: 'fading', icon: '📉', label: '退潮', cls: 'text-muted', box: 'border-border/60 bg-white/[0.02]' },
]

const day = ref('')
const plateType = ref(17)
const activeTab = ref('trade')
const data = ref({})
const loading = ref(false)
const error = ref('')
// ★ 2026-11-28 政策脉冲：政策快讯 → 受益板块（纯观察器，只给线索、不给买卖建议）
const pulse = ref({})

// 切回决策 tab 时容器从 display:none 恢复，echarts 需要重算尺寸
watch(activeTab, (t) => {
  if (t === 'trade') nextTick(() => { chart && chart.resize(); heatChart && heatChart.resize() })
})

const hist = ref({ dates: [], items: [] })
const histErr = ref('')
const histDays = ref(8)
const histTop = ref(20)

// ── ★ 题材固定颜色映射 ────────────────────────────────────────────
// 用户诉求：每个题材一个固定颜色，形成"光通信=红、算力=蓝"这类记忆锚点。
// 实现：djb2 hash(plate_code) → 36 色调色板取模。**纯 hash 不做冲突探测**：
// 探测会让"新进题材挤占色号"导致老题材换色，破坏固定记忆；
// 撞色概率 ≈ n/36，有线条标签 + 图例名字兜底。
// 调色板冷暖交替排列，相邻 index 色差尽量大；全部为暗底高对比色。
const TOPIC_PALETTE = [
  '#f85149', '#3fb950', '#58a6ff', '#e3b341', '#bc8cff', '#ff7b72',
  '#39c5cf', '#ff9642', '#f778ba', '#7ee787', '#79c0ff', '#f2cc60',
  '#d2a8ff', '#ff5c8a', '#56d4dd', '#ffa657', '#ea4aaa', '#8ddb8c',
  '#a5b4fc', '#fbbf24', '#34d399', '#fb7185', '#60a5fa', '#c084fc',
  '#f472b6', '#a3e635', '#22d3ee', '#fb923c', '#818cf8', '#4ade80',
  '#f87171', '#2dd4bf', '#e879f9', '#facc15', '#38bdf8', '#86efac',
]
function topicColor(code) {
  const s = String(code ?? '')
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h * 33) ^ s.charCodeAt(i)) >>> 0
  return TOPIC_PALETTE[h % TOPIC_PALETTE.length]
}

// ── 流图图例 + 联动高亮 ──
// 图例只列最近仍在榜的前 12 个（按最新名次），隐藏的题材点图例恢复。
const hiddenCodes = ref(new Set())
let visCodes = []   // drawChart 时记录可见系列的 code 顺序，供 highlight 定位
const legendItems = computed(() => {
  return (hist.value.items || [])
    .filter(x => x.last !== null && x.last !== undefined)
    .sort((a, b) => a.last - b.last)
    .slice(0, 12)
})
function toggleSeries(code) {
  const s = new Set(hiddenCodes.value)
  if (s.has(code)) s.delete(code); else s.add(code)
  hiddenCodes.value = s
  drawChart()
}
function hoverSeries(code) {
  // highlight 会触发 series 的 emphasis.focus 效果：目标线加粗、其余线进入 blur 变淡
  if (!chart) return
  chart.dispatchAction({ type: 'downplay' })
  if (!code) return
  const idx = visCodes.indexOf(code)
  if (idx >= 0) chart.dispatchAction({ type: 'highlight', seriesIndex: idx })
}

// ── 多日排名热力图（替代原对比表）──
const heatRef = ref(null)
let heatChart = null
const heatH = computed(() => {
  const rows = Math.min(hist.value.items?.length || 0, 17)
  // ★ 2026-11-28 行距 24→19、17 行：与排名流图（245px）一起在一屏（1080p ≈ 950px 视口）内展示完
  return Math.min(Math.max(rows * 19 + 44, 180), 440)
})

const sel = ref(null)
const stk = ref({})
const stkLoading = ref(false)
const stkErr = ref('')

const vRows = ref([])
const vLoading = ref(false)

const chartRef = ref(null)
let chart = null

function fmt(v, n = 2) {
  if (v === null || v === undefined || Number.isNaN(v)) return '—'
  return Number(v).toFixed(n)
}
function pctCls(v) {
  if (v === null || v === undefined) return 'text-muted'
  return v > 0 ? 'text-rise' : (v < 0 ? 'text-fall' : 'text-muted')
}
// 个股"当日"列的相对涨幅条宽度（面板内最大涨幅 = 满格）
function pctBar(v) {
  const arr = stk.value.items || []
  const max = Math.max(...arr.map(x => x.chg_pct || 0), 0.01)
  return Math.max(0, Math.min(100, ((v || 0) / max) * 100))
}
// ── zzshare 官方 stage_tag → 语义色。
// ★ 2026-11-28（用户指定）蓝阶递进（Material Blue 100/300/600/900）。
// ★ 用户反馈去掉透明度 ⇒ bgC 用**实色**（0.3 的浅蓝叠深底会变脏灰、深蓝变黑）；
//   文字色按底色配对：浅底深字、深底浅字；详情面板文字回归页面主题色（不再用 textC）。
//   选中边框统一中亮蓝（#9ec3f0），四种底色上都清晰。
const LIB_STAGE_CLS = {
  '底部启动': { dot: 'bg-sky-100', textC: '#0d2c54', bgC: '#e3f2fd', borderC: '#9ec3f0' },
  '震荡蓄势': { dot: 'bg-sky-300', textC: '#0d2c54', bgC: '#90caf9', borderC: '#9ec3f0' },
  '主升加速': { dot: 'bg-sky-600', textC: '#ffffff', bgC: '#1e88e5', borderC: '#9ec3f0' },
  '高潮':     { dot: 'bg-sky-900', textC: '#e3f2fd', bgC: '#0d47a1', borderC: '#9ec3f0' },
}
function libStage(tag) {
  if (LIB_STAGE_CLS[tag]) return LIB_STAGE_CLS[tag]
  const t = tag || ''
  if (t.includes('启动')) return LIB_STAGE_CLS['底部启动']
  if (t.includes('蓄势')) return LIB_STAGE_CLS['震荡蓄势']
  if (t.includes('主升') || t.includes('加速')) return LIB_STAGE_CLS['主升加速']
  if (t.includes('高潮') || t.includes('高位')) return LIB_STAGE_CLS['高潮']
  return { dot: 'bg-gray-500', textC: '#8b949e', bgC: 'rgba(110,118,129,0.20)', borderC: 'rgba(110,118,129,0.40)' }
}
// 置信度分值高亮：≥80 红（可信）、60~79 琥珀（一般）、<60 灰（存疑）
function confCls(v) {
  const n = Number(v) || 0
  if (n >= 80) return 'text-rise font-bold'
  if (n >= 60) return 'text-amber-300 font-semibold'
  return 'text-muted'
}
// ★ 详情正文关键内容高亮（用户 2026-11-28）：利好词红 / 利空词绿 / 百分比数字提亮。
//   先 HTML 转义再替换关键词 ⇒ 外部数据无 XSS 风险。
function escHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]))
}
const GOOD_WORDS = /(大涨|暴涨|猛涨|涨超|上调|调涨|飙升至|飙升|突破|新高|走强|高压|供不应求|缺货|涨价|涨势|景气|强劲|旺盛|超级周期|攀升|超预期|增长|翻倍|量价齐升|需求激增|全面上涨|加速上行|上修|集体走强|逆势上涨|业绩大爆发|放量增长|长期持续|持续上行|进一步上升|走高压)/g
const BAD_WORDS = /(下跌|大跌|暴跌|跌超|下调|砍单|过剩|走弱|放缓|收紧优惠|低于预期|衰退|萎缩|降温|转弱|回落|收窄|停产|暂停报价|下调至|远低于)/g
function highlightContent(content) {
  let s = escHtml(content || '')
  s = s.replace(GOOD_WORDS, m => `<span class="text-rise font-semibold">${m}</span>`)
  s = s.replace(BAD_WORDS, m => `<span class="text-fall font-semibold">${m}</span>`)
  // 百分比/倍数数字提亮（在词高亮之后跑，不会破坏已生成的 span）
  s = s.replace(/([+\-]?\d+(?:\.\d+)?[%％~～]\d*(?:\.\d+)?[%％]?|\d+倍)/g, m => `<span class="text-gray-50 font-medium">${m}</span>`)
  return s
}
function libDot(tag) { return libStage(tag).dot }
// 列表涨幅相对条形：当前筛选列表内最大涨幅 = 满格（负值不画）
const libMax = computed(() =>
  Math.max(...(libFiltered.value.map(x => x.today_pct || 0)), 0.001))
function libBar(x) {
  return Math.min(100, Math.max(0, ((x.today_pct || 0) / libMax.value) * 100))
}
function regimeBadge(lv) {
  if (lv === 1) return 'bg-rise/15 text-rise'
  if (lv === -1) return 'bg-fall/15 text-fall'
  return 'bg-amber-500/15 text-amber-400'
}
function weekday(d) {
  try {
    const w = new Date(d + 'T00:00:00').getDay()
    return '周' + '日一二三四五六'[w]
  } catch { return '' }
}
function cardCls(x) {
  // 按阶段着色：启动=红边、主升=蓝边、高位=黄边、退潮=灰边
  const st = stageOf(x.code)
  if (st === 'emerging') return 'border-rise/30 hover:border-rise/60 text-gray-300'
  if (st === 'leading') return 'border-accent/30 hover:border-accent/60 text-gray-300'
  if (st === 'crowded') return 'border-amber-500/30 hover:border-amber-500/60 text-gray-300'
  if (st === 'fading') return 'border-border/50 hover:border-border text-muted'
  return 'border-border/40 hover:border-border text-gray-300'
}
// ★ 选中卡片的边框整体用题材固定色（左边条颜色随内联样式覆盖）
function cardStyle(x) {
  const c = topicColor(x.code)
  if (sel.value && sel.value.code === x.code) {
    return { borderLeft: `3px solid ${c}`, borderColor: c, background: 'rgba(255,255,255,.05)' }
  }
  return { borderLeft: `3px solid ${c}` }
}
const stageMap = ref({})
function stageOf(code) {
  return stageMap.value[code] || ''
}

// ── 卡片排序（三种维度）+ 阶段/NEW 筛选（★ 原"阶段分布"独立模块融合至此）──
const sortBy = ref('rank')
const stageFilter = ref('all')   // 'all' | 'new' | STAGES key
const sortedTop = computed(() => {
  let arr = (data.value.top || []).slice()
  if (stageFilter.value === 'new') arr = arr.filter(x => x.is_new)
  else if (stageFilter.value !== 'all') arr = arr.filter(x => stageOf(x.code) === stageFilter.value)
  if (sortBy.value === 'money') {
    arr.sort((a, b) => (b.money_yi ?? -1e18) - (a.money_yi ?? -1e18))
  } else if (sortBy.value === 'accel') {
    // accel 为 null 的排最后（缺 20 日档），不能当 0
    arr.sort((a, b) => {
      const x = a.accel, y = b.accel
      if (x === null || x === undefined) return 1
      if (y === null || y === undefined) return -1
      return y - x
    })
  } else {
    arr.sort((a, b) => (a.rank || 999) - (b.rank || 999))
  }
  return arr
})
// 筛选 tab 上的数量（与 stageMap 同源）
const stageCounts = computed(() => {
  const top = data.value.top || []
  const c = { all: top.length, new: 0, emerging: 0, leading: 0, crowded: 0, fading: 0 }
  for (const x of top) {
    if (x.is_new) c.new++
    const s = stageOf(x.code)
    if (c[s] !== undefined) c[s]++
  }
  return c
})
// 卡片名前的阶段图标（一眼看出阶段；未分阶段的题材无图标）
function stageIconOf(code) {
  const g = STAGES.find(x => x.key === stageOf(code))
  return g ? g.icon + ' ' : ''
}
// 卡片第二行条形：当前排序维度的相对值（面板内最大值 = 满格）
// 负值（跌幅/负加速度）不画长度——条形表达"谁更强"，弱者留空轨道即可
const sortMax = computed(() => {
  const arr = sortedTop.value
  if (sortBy.value === 'money') return Math.max(...arr.map(y => y.money_yi ?? 0), 0.001)
  if (sortBy.value === 'accel') return Math.max(...arr.map(y => Math.max(y.accel ?? 0, 0)), 0.001)
  return Math.max(...arr.map(y => Math.max(y.sum_rate ?? 0, 0)), 0.001)
})
function sortBarW(x) {
  const v = sortBy.value === 'money' ? (x.money_yi ?? 0)
    : sortBy.value === 'accel' ? Math.max(x.accel ?? 0, 0)
    : Math.max(x.sum_rate ?? 0, 0)
  return Math.min(100, Math.max(0, (v / sortMax.value) * 100))
}

// ── 较昨日名次跃升/下滑（后端 surge/drop，正 = 名次上升）──
// load 时从 dashboard 顶层字段构建 map，卡片上显示 ↑N/↓N。
const rankChangeMap = ref({})
function rankChangeOf(code) { return rankChangeMap.value[code] || 0 }

// ── 细分题材库（zzshare topic_table）──
const lib = ref({ items: [], n: 0 })
const libFilter = ref('all')
const libSel = ref(null)
const libDetail = ref({})
const libLoading = ref(false)
const libErr = ref('')
const libHist = ref({ points: [] })

const libGroups = computed(() => {
  const items = lib.value.items || []
  const cnt = {}
  for (const x of items) {
    const k = x.stage_tag || '未标注'
    cnt[k] = (cnt[k] || 0) + 1
  }
  const out = [{ key: 'all', label: '全部', count: items.length }]
  // ★ 2026-11-28（用户要求）：tab 按蓝阶从右到左排 = 屏幕上 深→浅：
  //   高潮 → 主升加速 → 震荡蓄势 → 底部启动（高潮预警最靠前，符合看盘动线）。
  //   实际 stage_tag 可能带 emoji 前缀（如 "⚡ 主升加速"）⇒ 用关键词匹配原文
  const used = new Set()
  for (const kw of ['高潮', '主升加速', '震荡蓄势', '底部启动']) {
    const hit = Object.keys(cnt).find(ck => !used.has(ck) && ck.includes(kw))
    if (hit) { out.push({ key: hit, label: hit, count: cnt[hit] }); used.add(hit) }
  }
  // 官方未来新增的 tag 追加尾部（按数量降序）
  Object.entries(cnt)
    .filter(([k]) => !used.has(k))
    .sort((a, b) => b[1] - a[1])
    .forEach(([k, v]) => out.push({ key: k, label: k, count: v }))
  return out
})
const libFiltered = computed(() => {
  const items = lib.value.items || []
  if (libFilter.value === 'all') return items
  return items.filter(x => (x.stage_tag || '未标注') === libFilter.value)
})

async function loadLib() {
  try {
    const { data: d } = await getTopicLib()
    if (d && d.ok) lib.value = d
  } catch (e) {
    // 题材库是补充信息，失败不打断主页面
    console.warn('[rotation] lib load failed', e?.message)
  }
}

async function pickLib(x) {
  libSel.value = x
  libDetail.value = {}
  libHist.value = { points: [] }
  libErr.value = ''
  libLoading.value = true
  try {
    const { data: d } = await getTopicDetail({ unique_key: x.unique_key })
    if (d && d.ok) libDetail.value = d
    else libErr.value = (d && d.error) || '无数据'
  } catch (e) {
    libErr.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    libLoading.value = false
  }
  // 阶段历史单独拉（可能为空，失败不打断）
  try {
    const { data: h } = await getTopicLibHistory({ unique_key: x.unique_key, days: 20 })
    if (h && h.ok) libHist.value = h
  } catch { /* 历史尚在积累，忽略 */ }
}

// ★ 2026-11-28：政策脉冲 —— 盘中回答"这是什么级别的利好、影响哪些板块"，
//   不回答"买什么"。失败静默（辅助块，不拖垮主页面）。
async function loadPulse() {
  try {
    const params = day.value ? { day: day.value } : {}
    const { data: d } = await getPolicyPulse(params)
    pulse.value = (d && d.ok) ? d : {}
  } catch {
    pulse.value = {}
  }
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const params = { plate_type: plateType.value }
    if (day.value) params.day = day.value
    const { data: d } = await getTopicRotationDashboard(params)
    if (!d || d.ok === false) {
      error.value = (d && d.error) || '无数据'
      data.value = {}
    } else {
      data.value = d
      if (!day.value && d.date) day.value = d.date
      const m = {}
      for (const k of ['emerging', 'leading', 'crowded', 'fading']) {
        for (const x of (d.stages?.[k] || [])) m[x.code] = k
      }
      stageMap.value = m
      // 较昨日名次跃升/下滑(≥3 位才有意义,后端已过滤),卡片上显示 ↑N/↓N
      const rc = {}
      for (const x of (d.surge || [])) rc[x.code] = x.rank_change
      for (const x of (d.drop || [])) rc[x.code] = x.rank_change
      rankChangeMap.value = rc
      // ★ 2026-11-28 修复"加速度排序点了没反应"：dashboard 的 top 行从来不含
      //   accel 字段（后端 accel 只在独立 accel/drop_accel 列表），top 100 的
      //   x.accel 全是 undefined ⇒ 排序比较器恒返回 1，排序**静默失效**，
      //   卡片上"速N"也一直不显示。此处把 accel merge 进 top 行。
      const am = {}
      for (const x of (d.accel || [])) am[x.code] = x.accel
      for (const x of (d.drop_accel || [])) am[x.code] = x.accel
      for (const x of (d.top || [])) x.accel = am[x.code] ?? null
    }
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

async function loadHistory() {
  histErr.value = ''
  try {
    const params = { days: histDays.value, top: histTop.value, plate_type: plateType.value }
    const { data: d } = await getTopicRotationHistory(params)
    if (!d || d.ok === false) {
      histErr.value = (d && d.error) || '无数据'
      hist.value = { dates: [], items: [] }
      return
    }
    hist.value = d
    await nextTick()
    drawChart()
    drawHeat()
  } catch (e) {
    histErr.value = e?.response?.data?.detail || e.message || '加载失败'
  }
}

function drawChart() {
  if (!chartRef.value) return
  const d = hist.value
  if (!d.dates?.length || !d.items?.length) {
    if (chart) { chart.dispose(); chart = null }
    visCodes = []
    return
  }
  if (!chart) chart = echarts.init(chartRef.value, 'dark')
  const dates = d.dates.map(x => x.slice(5))
  // ★ 颜色改为**按题材固定**（topicColor(code)）：线往上走的同时，
  //   "光通信永远红色"这类记忆锚点跨日期稳定。原版按名次分配红橙黄，
  //   今天排第 1 的红、明天排第 5 就换色，用户没法建立记忆。
  // 当前在榜的线实、掉队的线虚淡；最新名次前 3 加粗放大（颜色仍保持自身）。
  const vis = d.items.filter(x => !hiddenCodes.value.has(x.code))
  visCodes = vis.map(x => x.code)
  const series = vis.map((it, i) => {
    const c = topicColor(it.code)
    const onBoard = it.last !== null && it.last !== undefined
    const top3 = onBoard && it.last <= 3
    return {
      name: it.name,
      type: 'line',
      data: it.ranks,
      connectNulls: false,
      smooth: 0.25,
      symbol: 'circle',
      symbolSize: top3 ? 6 : 4,
      lineStyle: { width: top3 ? 2.4 : 1.2, opacity: onBoard ? 0.95 : 0.35, color: c },
      itemStyle: { color: c },
      emphasis: { focus: 'series', lineStyle: { width: 3 } },
      // 图例/卡片 hover 联动时其余线压到几乎看不见
      blur: { lineStyle: { opacity: 0.05 }, itemStyle: { opacity: 0.05 } },
      endLabel: {
        // ★ 2026-11-28 原为 `show: i < 8`（怕右侧标签重叠的保守值，防错了对象）：
        //   在榜题材末端 y 坐标 = 最新名次（1~20 唯一），天然错开不会重叠，
        //   全显示也放得下（340px/20 档 ≈ 17px 行距 > 10px 字高）；
        //   真正会重叠的是掉队题材——线中途断掉，标签停在图中部随机高度。
        //   ⇒ 改为按 onBoard 判断：在榜全显示，掉队不显示。
        show: onBoard,
        formatter: it.name,
        color: c,
        fontSize: 10,
      },
    }
  })
  chart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#8b949e' },
    grid: { left: 34, right: 78, top: 14, bottom: 24 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(22,27,34,.96)',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 11 },
      formatter(ps) {
        const head = ps.length ? ps[0].axisValue : ''
        const rows = ps
          .filter(p => p.value !== null && p.value !== undefined)
          .sort((a, b) => a.value - b.value)
          .slice(0, 12)
          .map(p => `${p.marker} ${p.seriesName} <b>#${p.value}</b>`)
        return head + '<br/>' + rows.join('<br/>')
      },
    },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#30363d' } },
      // ★ x 轴标签距离加大（用户 2026-11-28）：按绘图区宽度动态稀疏化，
      //   保证相邻标签 ≥ ~80px；margin 8→12 拉开标签与轴的距离
      axisLabel: { fontSize: 10, margin: 12,
                   interval: Math.max(0, Math.ceil(dates.length / Math.max(1, Math.floor((chartRef.value.clientWidth - 112) / 80))) - 1) },
    },
    yAxis: {
      type: 'value',
      inverse: true,          // ★ 名次 1 在最上面
      min: 1,
      max: histTop.value,
      splitLine: { lineStyle: { color: '#21262d' } },
      axisLabel: { fontSize: 10, formatter: v => '#' + v },
    },
    series,
  }, true)
  chart.resize()
}

// ── 多日排名热力图：x=交易日，y=题材（最新名次升序，最热在最上），
//    值=名次，颜色 rank1 最亮红 → rank20 最暗，掉榜=近黑格。
//    表格要逐格扫名字，热力图扫一眼就能看出谁持续霸榜、谁一日游、谁在爬坡。
function drawHeat() {
  if (!heatRef.value) return
  const d = hist.value
  const dates = d.dates || []
  const items = (d.items || []).slice()
    .sort((a, b) => (a.last ?? 999) - (b.last ?? 999))
    .slice(0, 17)
  if (!dates.length || !items.length) {
    if (heatChart) { heatChart.dispose(); heatChart = null }
    return
  }
  if (!heatChart) heatChart = echarts.init(heatRef.value, 'dark')
  // ★ 掉榜格必须与正常格分开成第二个 series：连续型 visualMap 对范围外的值
  //   会把颜色 clamp 到渐变端（亮红），outOfRange 不可靠——实测 2026-11-28，
  //   掉榜格全被染成 rank1 的亮红。visualMap 只绑正常格，掉榜格用固定近黑。
  const hot = []
  const cold = []
  const colorByCode = {}
  items.forEach((it, yi) => {
    colorByCode[it.name] = it.code
    for (let xi = 0; xi < dates.length; xi++) {
      const r = it.ranks[xi]
      if (r === null || r === undefined) cold.push([xi, yi, 0])
      else hot.push([xi, yi, r])
    }
  })
  heatChart.setOption({
    backgroundColor: 'transparent',
    textStyle: { color: '#8b949e' },
    grid: { left: 78, right: 16, top: 8, bottom: 26 },
    tooltip: {
      trigger: 'item',
      backgroundColor: 'rgba(22,27,34,.96)',
      borderColor: '#30363d',
      textStyle: { color: '#c9d1d9', fontSize: 11 },
      formatter(p) {
        const [xi, yi, r] = p.value
        const it = items[yi]
        const rate = (it.rates || [])[xi]
        const prev = xi > 0 ? it.ranks[xi - 1] : null
        const delta = (prev && r) ? prev - r : null
        const wk = weekday(dates[xi])
        return `<b>${it.name}</b> · ${dates[xi]} ${wk}<br/>` +
          `名次 <b>${r || '掉榜'}</b>` +
          (delta > 0 ? ` <span style="color:#3fb950">↑${delta}</span>`
            : delta < 0 ? ` <span style="color:#f85149">↓${-delta}</span>` : '') +
          (rate !== null && rate !== undefined ? ` · 当日 ${Number(rate).toFixed(1)}%` : '')
      },
    },
    xAxis: {
      type: 'category',
      data: dates.map(x => x.slice(5)),
      axisLine: { lineStyle: { color: '#30363d' } },
      // ★ x 轴标签距离加大（同流图）：相邻标签 ≥ ~80px 动态稀疏化，margin 12
      axisLabel: { fontSize: 10, margin: 12, rotate: dates.length > 10 ? 35 : 0,
                   interval: Math.max(0, Math.ceil(dates.length / Math.max(1, Math.floor((heatRef.value.clientWidth - 94) / 80))) - 1) },
    },
    yAxis: {
      type: 'category',
      data: items.map(x => x.name),
      inverse: true,          // 排序后 index 0 = 最新名次最靠前 ⇒ 放最上面
      axisLine: { lineStyle: { color: '#30363d' } },
      // ★ 行名字用题材固定色（与流图线条一致），跨图表强化记忆锚点
      axisLabel: {
        fontSize: 10,
        width: 72,
        overflow: 'truncate',
        color: v => topicColor(colorByCode[v] ?? v),
      },
    },
    visualMap: {
      show: false,
      type: 'continuous',
      seriesIndex: 0,      // 只映射正常名次格
      min: 1,
      max: histTop.value,
      // 名次好 = 亮红（A 股语境红=热），名次差 = 暗红渐隐
      inRange: { color: ['#ff5d4d', '#e5484d', '#b63038', '#7d2a35', '#4d2a33', '#332b31'] },
    },
    series: [
      {
        type: 'heatmap',
        data: hot,
        itemStyle: { borderRadius: 2, borderColor: 'transparent', borderWidth: 1 },
        label: {
          show: true, fontSize: 9, color: '#e6edf3',
          formatter: p => (p.value[2] ? String(p.value[2]) : ''),
        },
        emphasis: { itemStyle: { borderColor: '#8b949e' } },
      },
      {
        // 掉榜格：近黑无数字，整行"突然变暗"本身就是信号
        type: 'heatmap',
        data: cold,
        itemStyle: { color: '#14181e', borderRadius: 2 },
        label: { show: false },
      },
    ],
  }, true)
  heatChart.resize()
  // 联动：悬停热力图的格子/行名 → 流图里只突出该题材的线（同一套记忆锚点贯穿所有图）
  heatChart.off('mouseover').on('mouseover', p => {
    if (p.componentType === 'series') hoverSeries(colorByCode[p.name])
  })
  heatChart.off('globalout').on('globalout', () => hoverSeries(null))
}

async function pick(x) {
  sel.value = x
  stk.value = {}
  stkErr.value = ''
  stkLoading.value = true
  try {
    const params = { code: x.code, plate_type: plateType.value, top: 15 }
    if (day.value) params.day = day.value
    const { data: d } = await getTopicRotationConstituents(params)
    if (!d || d.ok === false) stkErr.value = (d && d.error) || '无数据'
    else stk.value = d
  } catch (e) {
    stkErr.value = e?.response?.data?.detail || e.message || '加载失败'
  } finally {
    stkLoading.value = false
  }
}

async function loadVerify() {
  if (vLoading.value) return
  vLoading.value = true
  try {
    const out = []
    for (const [lv, label] of [[1, '强势'], [0, '中性'], [-1, '弱势']]) {
      try {
        const { data: r } = await getTopicRotationVerify({
          plate_type: plateType.value, horizon: 5, min_days: 10, regime: lv,
        })
        out.push(r && r.ok
          ? { label, excess: r.avg_excess_pct, win: r.win_rate, n: r.n_events,
              valid: r.valid, tradable: r.tradable }
          : { label, excess: null, win: null, n: 0, valid: false, tradable: false })
      } catch {
        out.push({ label, excess: null, win: null, n: 0, valid: false, tradable: false })
      }
    }
    vRows.value = out
  } finally {
    vLoading.value = false
  }
}

function verdictText(r) {
  if (r.tradable) return '可用'
  if (r.valid) return '显著'
  return '无'
}
// ★ 2026-11-28：广度分档配色（红涨绿跌，与全站一致）
function breadthCls(level) {
  return {
    extreme_up: 'bg-rise/20 text-rise',
    up: 'bg-rise/10 text-rise',
    mixed: 'bg-white/5 text-muted',
    down: 'bg-fall/10 text-fall',
    extreme_down: 'bg-fall/20 text-fall',
  }[level] || 'bg-white/5 text-muted'
}

function verdictCls(r) {
  if (r.tradable) return 'bg-rise/15 text-rise'
  if (r.valid) return 'bg-amber-500/15 text-amber-400'
  return 'bg-white/5 text-muted'
}

async function reload() {
  await Promise.all([load(), loadHistory(), loadPulse()])
  if (sel.value) {
    const cur = (data.value.top || []).find(x => x.code === sel.value.code)
    if (cur) pick(cur)
  }
}

function onResize() {
  chart && chart.resize()
  heatChart && heatChart.resize()
}

onMounted(async () => {
  await Promise.all([reload(), loadLib()])
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  if (chart) { chart.dispose(); chart = null }
  if (heatChart) { heatChart.dispose(); heatChart = null }
})
</script>
