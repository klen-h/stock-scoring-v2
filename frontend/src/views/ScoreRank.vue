<template>
  <div class="fade-in space-y-4">
    <!-- 市场环境提示（独立信号，仅供参考，不改个股评分） -->
    <div v-if="temp.temperature != null" class="bg-card border border-border rounded-lg p-3 flex items-center justify-between flex-wrap gap-x-4 gap-y-1">
      <div class="flex items-center gap-2">
        <span class="text-xs text-muted">市场环境</span>
        <span class="text-base font-bold" :class="levelColor(temp.level)">{{ temp.level }} {{ temp.temperature }}</span>
      </div>
      <span class="text-xs text-gray-300 flex-1 min-w-[200px]">{{ temp.advisory }}</span>
      <span class="text-xs text-muted">建议买入线 <span class="text-accent font-bold">{{ temp.buy_threshold }}</span></span>
    </div>

    <!-- K线数据库缓存状态 -->
    <div v-if="klineCacheStatus" class="bg-card border border-border rounded-lg p-2 px-3 flex items-center justify-between flex-wrap gap-2 text-xs">
      <div class="flex items-center gap-3">
        <span class="text-muted">K线缓存</span>
        <span v-if="klineCacheStatus.total_cached > 0" class="text-emerald-400">● {{ klineCacheStatus.total_cached }}只</span>
        <span v-else class="text-amber-400">○ 未缓存</span>
        <span v-if="klineCacheStatus.newest_update" class="text-muted">更新: {{ klineCacheStatus.newest_update.substring(0, 16) }}</span>
        <span v-if="klineCacheStatus.expired_count > 0" class="text-amber-400">{{ klineCacheStatus.expired_count }}只过期</span>
      </div>
      <button @click="handleRefreshKlineCache" :disabled="klineCacheRefreshing"
        class="px-2 py-1 rounded text-xs transition-colors"
        :class="klineCacheRefreshing ? 'bg-white/5 text-muted cursor-not-allowed' : 'bg-accent/10 text-accent hover:bg-accent/20'">
        {{ klineCacheRefreshing ? '提交中...' : '刷新缓存' }}
      </button>
    </div>
    <div v-if="klineCacheMsg" class="bg-card border border-border rounded-lg p-2 px-3 text-xs text-rise">
      {{ klineCacheMsg }}
    </div>

    <!-- 前端评分系统状态 -->
    <div v-if="frontendInitialized" class="bg-card border border-border rounded-lg p-2 px-3 flex items-center justify-between flex-wrap gap-2 text-xs">
      <div class="flex items-center gap-3">
        <span class="text-muted">本地计算</span>
        <span v-if="frontendDbReady && frontendStockCount > 0" class="text-emerald-400">● {{ frontendStockCount }}只</span>
        <span v-else class="text-amber-400">○ 未就绪</span>
        <span v-if="useFrontendMode" class="text-accent">（当前使用本地计算）</span>
        <span v-if="frontendComputing" class="text-blue-400">计算中...</span>
      </div>
      <div class="flex items-center gap-2">
        <button v-if="!frontendDbReady || frontendStockCount === 0" 
          @click="handleDownloadKlineData"
          :disabled="frontendUpdating"
          class="px-2 py-1 rounded text-xs bg-accent/10 text-accent hover:bg-accent/20 transition-colors">
          {{ frontendUpdating ? frontendProgress.message || '下载中...' : '下载数据' }}
        </button>
        <template v-else>
          <button 
            @click="handleDownloadKlineData"
            :disabled="frontendUpdating"
            class="px-2 py-1 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors"
            title="手动更新 K 线数据包">
            {{ frontendUpdating ? '更新中...' : '更新数据' }}
          </button>
          <button 
            @click="toggleFrontendMode"
            class="px-2 py-1 rounded text-xs transition-colors"
            :class="useFrontendMode ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'">
            {{ useFrontendMode ? '切换到后端' : '切换到本地' }}
          </button>
        </template>
      </div>
    </div>
    <!-- 下载进度条 -->
    <div v-if="frontendUpdating && frontendProgress.loaded > 0" class="bg-card border border-border rounded-lg p-2 px-3">
      <div class="flex items-center gap-2 text-xs">
        <span class="text-muted">{{ frontendProgress.message }}</span>
        <div class="flex-1 bg-white/5 rounded-full h-1.5">
          <div class="bg-accent h-full rounded-full transition-all"
            :style="{ width: frontendProgress.total > 0 ? (frontendProgress.loaded / frontendProgress.total * 100) + '%' : '30%' }"></div>
        </div>
        <span v-if="frontendProgress.total > 0" class="text-muted">{{ frontendProgress.loaded }}/{{ frontendProgress.total }}</span>
      </div>
    </div>

    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3">
          <h2 class="text-lg font-bold">评分排行榜</h2>
          <span class="text-xs" :class="isTradingNow ? 'text-emerald-400' : 'text-muted'"
            :title="isTradingNow ? '交易时段，每60秒自动刷新' : '非交易时段'">
            {{ isTradingNow ? '● 交易中' : '○ 已休市' }}
          </span>
          <span v-if="autoCountdown > 0 && autoCountdown < 60" class="text-xs text-muted">{{ autoCountdown }}s</span>
        </div>
        <div class="flex gap-2 flex-wrap">
          <button v-for="tab in tabs" :key="tab.key" @click="switchTab(tab.key)"
            :class="activeTab === tab.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
            class="px-3 py-1 rounded text-xs transition-colors">
            {{ tab.label }}
          </button>
          <select v-if="activeTab === 'signal'" v-model="signalType"
            class="bg-bg border border-border rounded px-2 py-1 text-xs text-gray-300"
            @change="loadData">
            <option v-for="s in signalOptions" :key="s" :value="s">{{ s }}</option>
          </select>
        </div>
      </div>
    </div>

    <!-- 盘中警示条（12s 轮询市场总览：指数急跌/涨跌比/跌停，秒级滞后） -->
    <div v-if="activeTab === 'top' && marketAlerts.length" class="mb-3 space-y-1.5">
      <div v-for="(a, i) in marketAlerts" :key="i"
        class="px-3 py-2 rounded text-xs border"
        :class="a.sev === '🔴' ? 'bg-red-500/10 border-red-500/30 text-red-400' : 'bg-amber-500/10 border-amber-500/30 text-amber-400'">
        {{ a.text }}
      </div>
    </div>

    <!-- 评分变动提醒（与上次快照对比） -->
    <div v-if="(scoreAlerts.upgrades.length || scoreAlerts.downgrades.length) && activeTab === 'top'"
      class="bg-card border border-border rounded-lg p-3 space-y-2">
      <div class="text-xs font-semibold text-muted">信号变动（对比最近快照）</div>
      <div v-if="scoreAlerts.upgrades.length" class="flex items-center gap-2 flex-wrap">
        <span class="text-xs text-emerald-400 flex-shrink-0">↑ 升级 {{ scoreAlerts.upgrades.length }} 只</span>
        <span v-for="s in scoreAlerts.upgrades.slice(0, 8)" :key="'u'+s.code"
          class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20 cursor-pointer hover:bg-emerald-500/25"
          @click="goDetail(s.code)">
          {{ s.name }} {{ s.prevSignal }}→{{ s.signal }}
        </span>
        <span v-if="scoreAlerts.upgrades.length > 8" class="text-xs text-muted">+{{ scoreAlerts.upgrades.length - 8 }} 只</span>
      </div>
      <div v-if="scoreAlerts.downgrades.length" class="flex items-center gap-2 flex-wrap">
        <span class="text-xs text-red-400 flex-shrink-0">↓ 降级 {{ scoreAlerts.downgrades.length }} 只</span>
        <span v-for="s in scoreAlerts.downgrades.slice(0, 8)" :key="'d'+s.code"
          class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20 cursor-pointer hover:bg-red-500/25"
          @click="goDetail(s.code)">
          {{ s.name }} {{ s.prevSignal }}→{{ s.signal }}
        </span>
        <span v-if="scoreAlerts.downgrades.length > 8" class="text-xs text-muted">+{{ scoreAlerts.downgrades.length - 8 }} 只</span>
      </div>
    </div>

    <!-- 持仓撤退提醒 -->
    <div v-if="exitAlerts.length && activeTab === 'top'"
      class="bg-card border border-red-500/30 rounded-lg p-3 space-y-2">
      <div class="flex items-center justify-between">
        <span class="text-sm font-semibold text-red-400">持仓撤退提醒</span>
        <span class="text-xs text-muted">{{ exitAlerts.length }} 个提醒</span>
      </div>
      <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
        <div v-for="alert in exitAlerts" :key="alert.code"
          :class="['p-2 rounded text-xs', alert.level === 'urgent' ? 'bg-red-500/10 border border-red-500/30' : 'bg-amber-500/10 border border-amber-500/30']">
          <div class="flex items-center justify-between">
            <span class="font-medium">{{ alert.name }} ({{ alert.code }})</span>
            <span :class="alert.level === 'urgent' ? 'text-red-400 font-bold' : 'text-amber-400'">
              {{ alert.level === 'urgent' ? '紧急撤退' : '警告' }}
            </span>
          </div>
          <div class="text-muted mt-1">{{ alert.action }}</div>
          <div class="flex gap-2 mt-1">
            <span class="text-muted">现价 {{ alert.current_price }}</span>
            <span :class="alert.profit_pct >= 0 ? 'text-rise' : 'text-fall'">
              盈亏 {{ alert.profit_pct >= 0 ? '+' : '' }}{{ alert.profit_pct }}%
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 连续/可信度加载失败提示（默认只在 top 榜显示） -->
    <div v-if="activeTab === 'top' && persistenceError" class="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-400">
      ⚠️ 连续/可信度加载失败：{{ persistenceError }}
    </div>

    <!-- 评分分布概览 -->
    <div v-if="stats.total > 0 && activeTab !== 'verify' && activeTab !== 'backtest' && activeTab !== 'sector' && activeTab !== 'optimize' && activeTab !== 'anomaly' && activeTab !== 'shadow' && activeTab !== 'watch'" class="bg-card border border-border rounded-lg p-4">
      <div class="grid grid-cols-3 md:grid-cols-5 gap-3 text-center text-sm">
        <div class="p-2 bg-bg rounded-lg">
          <div class="text-muted text-xs">评分股票数</div>
          <div class="text-lg font-bold mt-1">{{ stats.total }}</div>
        </div>
        <div class="p-2 bg-emerald-500/10 rounded-lg">
          <div class="text-emerald-400 text-xs">强烈买入/买入</div>
          <div class="text-lg font-bold text-emerald-400 mt-1">{{ stats.buyCount }}</div>
        </div>
        <div class="p-2 bg-amber-500/10 rounded-lg">
          <div class="text-amber-400 text-xs">观望</div>
          <div class="text-lg font-bold text-amber-400 mt-1">{{ stats.watchCount }}</div>
        </div>
        <div class="p-2 bg-red-500/10 rounded-lg">
          <div class="text-red-400 text-xs">卖出/强烈卖出</div>
          <div class="text-lg font-bold text-red-400 mt-1">{{ stats.sellCount }}</div>
        </div>
        <div class="p-2 bg-bg rounded-lg">
          <div class="text-muted text-xs">缓存状态</div>
          <div class="text-sm mt-1" :class="cacheStatus === 'ready' ? 'text-emerald-400' : 'text-amber-400'">
            {{ cacheStatus === 'ready' ? '就绪' : '加载中...' }}
          </div>
        </div>
      </div>
    </div>

    <!-- 影子榜对比（生产 vs 梯度衰减 vs ×0 对照，三套 top50） -->
    <div v-if="activeTab === 'shadow'" class="bg-card border border-border rounded-lg overflow-hidden">
      <div class="px-3 py-2 text-xs text-muted border-b border-border bg-white/[0.02] flex items-center justify-between">
        <span>影子榜对比 · 生产 / 梯度衰减（主）/ ×0 对照（各 top50）</span>
        <span v-if="shadowData.date" class="font-mono">数据日 {{ shadowData.date }}</span>
      </div>
      <div v-if="shadowLoading" class="p-6 text-center text-xs text-muted">加载中…</div>
      <div v-else-if="shadowError" class="p-6 text-center text-xs text-amber-400">{{ shadowError }}</div>
      <div v-else class="grid grid-cols-3">
        <div v-for="(col, ci) in shadowCols" :key="col.key" :class="ci ? 'border-l border-border' : ''">
          <div class="px-2 py-2 text-xs font-semibold border-b border-border">
            {{ col.label }}
            <span class="text-muted font-normal text-[10px]">{{ col.hint }}</span>
          </div>
          <table class="w-full text-sm">
            <thead>
              <tr class="border-b border-border text-muted text-xs">
                <th class="py-2 px-2 text-left">#</th>
                <th class="py-2 px-2 text-left">代码</th>
                <th class="py-2 px-2 text-left">名称</th>
                <th class="py-2 px-2 text-left">板块</th>
                <th class="py-2 px-2 text-right">涨跌</th>
                <th class="py-2 px-2 text-right">分</th>
                <th class="py-2 px-2 text-center">主力</th>
                <th class="py-2 px-2 text-right">龄</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="r in shadowData[col.key]" :key="col.key + r.code"
                class="border-b border-border/50 hover:bg-white/3 cursor-pointer"
                :class="col.key !== 'base' && !shadowData.base.some(b => b.code === r.code) ? 'bg-emerald-500/5' : ''"
                @click="goDetail(r.code)">
                <td class="py-1.5 px-2 text-muted font-mono text-xs">{{ r.rank_pos }}</td>
                <td class="py-1.5 px-2 font-mono text-xs text-accent">
                  <a :href="getXueqiuUrl(r.code)" target="_blank" rel="noopener" @click.stop
                    class="hover:underline" :title="r.name">{{ r.code }}</a>
                </td>
                <td class="py-1.5 px-2 text-xs truncate max-w-[80px]">{{ r.name }}</td>
                <td class="py-1.5 px-2 text-xs truncate max-w-[72px]">
                  <span :title="chainTip(r)" class="text-muted">{{ industryOf(r) }}</span>
                </td>
                <td class="py-1.5 px-2 text-right font-mono text-xs"
                  :class="(r.change_pct || 0) > 0 ? 'text-red-400' : (r.change_pct || 0) < 0 ? 'text-emerald-400' : 'text-muted'">
                  {{ (r.change_pct || 0) > 0 ? '+' : '' }}{{ (r.change_pct || 0).toFixed(2) }}%
                </td>
                <td class="py-1.5 px-2 text-right font-mono text-xs">{{ r.total_score }}</td>
                <td class="py-1.5 px-2 text-center">
                  <span v-if="r.mainforce_signal" class="px-1 py-0.5 rounded text-[10px] font-bold"
                    :class="r.mainforce_signal === 'distribution' ? 'bg-red-500/20 text-red-400' : 'bg-emerald-500/20 text-emerald-400'">
                    {{ r.mainforce_signal === 'distribution' ? '出货' : '吸筹' }}
                  </span>
                  <span v-else class="text-[10px] text-muted">-</span>
                </td>
                <td class="py-1.5 px-2 text-right text-muted text-xs">{{ r.decay_age != null ? r.decay_age : '-' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <div class="px-3 py-2 text-xs text-muted border-t border-border">
        与生产榜重叠：梯度 {{ shadowData.overlap?.grad ?? '-' }}/50、×0 {{ shadowData.overlap?.zero ?? '-' }}/50（绿色行=该榜相对生产新进）。
        「梯度」= 公告龄降权（0-5 天剔除、6-20 天权重×0.5，主版本）；「×0」= 公告后 ≤25 天成长/质量直接归零（初版对照）。均仅旁路展示、不参与实际评分。
      </div>
    </div>

    <!-- 组合分排序口径提示（nb 阴跌市，后端切换排序键） -->
    <div v-if="rankMode === 'composite' && activeTab === 'top'"
      class="bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 text-xs text-amber-400 mb-3">
      ⚠️ 当前为阴跌防御市（neutral_bearish），榜单按「组合分 = 基本面 + 资金面 − 技术面」排序（非总分）。
      回测背书：总分在该市况排序失效（高分票补跌），组合分 IC +0.259 vs 总分 +0.020。总分仅作参考。
    </div>

    <!-- ★ 我的持仓（2026-09-23）：以**持仓为主轴**的状态聚合 —— 回答「我手里那几只怎么样」，
         而不是全市场候选。数据 /score/batch/portfolio-radar（评分/主力阶段/闸门就绪/
         战法/观察池/矛盾 六合一）。定位：状态展示与提示聚合，**不是买卖信号**（同观察池纪律）。 -->
    <div v-if="activeTab === 'positions'" class="space-y-3">
      <div class="bg-card border border-border rounded-lg overflow-hidden">
        <div class="px-3 py-2 text-xs text-muted border-b border-border bg-white/[0.02] flex items-center justify-between flex-wrap gap-2">
          <span>我的持仓 · 状态聚合（评分 / 主力阶段 / 闸门就绪 / 战法 / 观察池 / 板块风险）</span>
          <span class="font-mono">
            市况 {{ portfolioRadar.regime || '-' }}
            ｜ 持仓 {{ portfolioRadar.summary?.n ?? 0 }}
            ｜ 风险 {{ portfolioRadar.summary?.risk ?? 0 }}
            ｜ 机会 {{ portfolioRadar.summary?.opportunity ?? 0 }}
            ｜ 在观察池 {{ portfolioRadar.summary?.in_watch ?? 0 }}
          </span>
        </div>

        <div v-if="portfolioRadarLoading" class="p-6 text-center text-xs text-muted">加载中…</div>
        <div v-else-if="portfolioRadarWarming" class="p-6 text-center text-xs text-amber-400">{{ portfolioRadarError }}</div>
        <div v-else-if="portfolioRadarError" class="p-6 text-center text-xs text-amber-400">{{ portfolioRadarError }}</div>
        <div v-else-if="!portfolioRadar.items?.length" class="p-6 text-center text-xs text-muted">
          还没有持仓记录 —— 在个股详情页「加入持仓」后，这里会显示每只的状态与风险提示。
        </div>
        <div v-else class="overflow-x-auto">
          <table class="w-full text-xs">
            <thead class="text-muted bg-white/[0.02]">
              <tr>
                <th class="py-2 px-3 text-left font-normal">代码 / 名称</th>
                <th class="py-2 px-3 text-right font-normal">现价</th>
                <th class="py-2 px-3 text-right font-normal">涨跌</th>
                <th class="py-2 px-3 text-right font-normal">盈亏</th>
                <th class="py-2 px-3 text-left font-normal">主力阶段</th>
                <th class="py-2 px-3 text-left font-normal">闸门就绪</th>
                <th class="py-2 px-3 text-right font-normal">评分 / 排名</th>
                <th class="py-2 px-3 text-left font-normal">板块</th>
                <th class="py-2 px-3 text-left font-normal">提示</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in portfolioRadar.items" :key="p.code"
                class="border-t border-border hover:bg-white/[0.02]">
                <td class="py-2 px-3">
                  <a :href="getXueqiuUrl(p.code)" target="_blank" rel="noopener"
                    class="text-accent hover:underline font-mono">{{ p.code }}</a>
                  <div class="text-muted">{{ p.name }}</div>
                </td>
                <td class="py-2 px-3 text-right font-mono">{{ p.price ?? '—' }}</td>
                <td class="py-2 px-3 text-right font-mono" :class="pnlCls(p.day_pct)">{{ pnlText(p.day_pct) }}</td>
                <td class="py-2 px-3 text-right font-mono" :class="pnlCls(p.pnl_pct)">{{ pnlText(p.pnl_pct) }}</td>
                <td class="py-2 px-3">
                  <!-- 主力阶段：复用全站 PHASE_STYLE（绿=机会 / 红=风险），显示后端 phase_cn -->
                  <span v-if="p.phase" class="px-1.5 py-0.5 rounded font-bold cursor-help"
                    :class="(PHASE_STYLE[p.phase] || {}).cls || 'bg-white/5 text-muted'"
                    :title="(PHASE_STYLE[p.phase] || {}).tip || ''">{{ p.phase_cn || p.phase }}</span>
                  <span v-else class="text-muted">—</span>
                </td>
                <td class="py-2 px-3 cursor-help" :class="readyCls(p.ready)" :title="p.ready_hint || ''">
                  {{ p.ready ?? '—' }}/3 {{ p.ready_label || '' }}
                </td>
                <td class="py-2 px-3 text-right font-mono">
                  {{ p.score ?? '—' }}<span v-if="p.rank_pos" class="text-muted"> #{{ p.rank_pos }}</span>
                </td>
                <td class="py-2 px-3 text-muted">{{ p.industry || '—' }}</td>
                <td class="py-2 px-3">
                  <div v-if="p.alerts?.length" class="space-y-0.5">
                    <div v-for="(a, i) in p.alerts" :key="i" :class="alertCls(a.level)">• {{ a.text }}</div>
                  </div>
                  <span v-else class="text-muted">—</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="px-3 py-2 text-[11px] text-muted border-t border-border leading-relaxed">
          口径：评分/排名取**日批榜单**（不实时精算）；闸门就绪由后端 <span class="font-mono">trade_gate.summarize</span> 唯一产出；
          「提示」只聚合既有模块结论（主力阶段 / 闸门 / 战法 / 观察池 / 矛盾 / 涨跌），**不构成买卖建议**。
        </div>
      </div>

      <!-- 今日未兑现疑虑（市场级）—— 持仓所属板块被点名时，上方「提示」列会标红 -->
      <div v-if="portfolioRadar.market?.contradictions?.length"
        class="bg-card border border-border rounded-lg p-3 space-y-1.5">
        <div class="text-xs font-semibold text-muted">今日未兑现疑虑（市场级）</div>
        <div v-for="(c, i) in portfolioRadar.market.contradictions" :key="i" class="text-xs">
          <span :class="c.severity === 'severe' ? 'text-red-400' : c.severity === 'obvious' ? 'text-amber-400' : 'text-muted'">[{{ c.severity }}]</span>
          <span class="font-semibold text-gray-300"> {{ c.title }}</span>
          <div class="text-muted leading-relaxed">{{ c.summary }}</div>
        </div>
      </div>
    </div>

    <!-- ★ 观察池（2026-09-22）：买入闸门 ready≥2 的「等状态」候选
         设计：低频事件（三绿）的可视化做「候池」而非「出票」—— 多数交易日三绿为 0
         （市况不容许），日常价值在"还差一步"的池子（主力有根据+不追高，等市况/时机）。
         数据：trade_gate.summarize 唯一产出，不参与排序、不改总分。 -->
    <div v-if="activeTab === 'watch'" class="bg-card border border-border rounded-lg overflow-hidden">
      <div class="px-3 py-2 text-xs text-muted border-b border-border bg-white/[0.02] flex items-center justify-between flex-wrap gap-2">
        <span>买入闸门观察池 · 就绪度 ≥2（主力有根据 + 不追高，等市况/时机）</span>
        <span class="font-mono">市况 {{ gateWatch.regime || '-' }} ｜ 候选 {{ gateWatch.total }} 只 ｜ 三绿 {{ gateWatch.ready3 }} 只 ｜ 战法命中 {{ gateWatch.strategy_hits || 0 }} 只 ｜ 数据日 {{ gateWatch.data_date || (gateWatch.source === 'live' ? '实时' : '-') }}</span>
      </div>
      <!-- ★ 双信号交叉（2026-09-22）：战法信号（图形）∩ 闸门 ready（主力/筹码/市况）
           —— 两个独立体系同时看中，比单信号更值得看。战法本身不推送（白名单空）。 -->
      <div v-if="!gateWatchLoading" class="px-3 py-1.5 border-b border-border bg-white/[0.01] flex items-center gap-3 text-xs">
        <label class="flex items-center gap-1 cursor-pointer text-muted hover:text-gray-200">
          <input type="checkbox" v-model="watchOnlyStrategy" class="accent-blue-500">
          只看双信号（战法命中 {{ watchStrategyCount }} 只）
        </label>
        <span class="text-muted">战法数据日 {{ gateWatch.strategy_date || '-' }}</span>
      </div>
      <div v-if="gateWatchLoading" class="p-6 text-center text-xs text-muted">加载中…</div>
      <div v-else-if="!gateWatch.items.length" class="p-6 text-center text-xs text-muted">当前无 ready≥2 的候选（或 mainforce_state 无数据）</div>
      <table v-else class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">代码</th>
            <th class="text-left py-2.5 px-3">名称</th>
            <th class="text-left py-2.5 px-3">板块</th>
            <!-- ★ 2026-09-22：实时涨跌幅（60s 随全局刷新；A 股习惯红涨绿跌） -->
            <th class="text-right py-2.5 px-3">涨跌幅</th>
            <th class="text-center py-2.5 px-3">就绪</th>
            <th class="text-left py-2.5 px-3">还差什么</th>
            <th class="text-left py-2.5 px-3">战法</th>
            <th class="text-left py-2.5 px-3">主力阶段</th>
            <th class="text-right py-2.5 px-3">5日主力占额</th>
            <th class="text-right py-2.5 px-3">筹码位置</th>
            <th class="text-right py-2.5 px-3">获利盘</th>
            <th class="text-center py-2.5 px-3">建议仓位</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="g in watchItems" :key="g.code"
            class="border-b border-border/50 hover:bg-white/3 cursor-pointer transition-colors"
            @click="goDetail(g.code)">
            <!-- ★ 2026-09-22：点代码去雪球（与榜单 Top50 同款；`@click.stop` 阻止
                 冒泡，否则会同时触发行的 goDetail）—— 整行点击 = 本页详情，
                 点代码 = 雪球新标签，两个入口并存。 -->
            <td class="py-2 px-3 font-mono text-xs text-accent">
              <a :href="getXueqiuUrl(g.code)" target="_blank" rel="noopener" @click.stop
                class="hover:underline" title="在雪球查看（新标签）">{{ g.code }}</a>
            </td>
            <td class="py-2 px-3 text-xs">{{ g.name }}</td>
            <!-- 板块：细分主行业 + 悬停给归属链（数据 /score/batch/industry-map） -->
            <td class="py-2 px-3 text-xs truncate max-w-[90px]">
              <span :title="chainTip(g)" class="text-muted">{{ industryOf(g) }}</span>
            </td>
            <!-- 实时涨跌幅（数据来自 /score/batch-prices，与榜单同口径：红涨绿跌） -->
            <td class="py-2 px-3 text-right font-mono text-xs" :class="wpColor(g.code)">
              {{ wpText(g.code) }}
            </td>
            <td class="py-2 px-3 text-center">
              <span class="px-1.5 py-0.5 rounded text-xs font-bold"
                :class="g.ready === 3 ? 'bg-emerald-500/20 text-emerald-400' : 'bg-amber-500/20 text-amber-400'">
                {{ g.ready }}/3
              </span>
            </td>
            <td class="py-2 px-3 text-xs text-amber-400">{{ g.missing && g.missing.length ? g.missing.join('、') : '—' }}</td>
            <td class="py-2 px-3 text-xs">
              <span v-if="g.strategies && g.strategies.length"
                class="px-1.5 py-0.5 rounded bg-blue-500/15 text-blue-300 font-mono text-[11px]">
                {{ g.strategies.map(s => strategyShort(s.name) + (s.conf ? '(' + s.conf + ')' : '')).join(' / ') }}
              </span>
              <span v-else class="text-muted">-</span>
            </td>
            <!-- ★ 2026-09-22：阶段配色标签 —— 显示用后端 `phase_cn`（唯一映射源
                 `phases.PHASE_CN`），配色键用英文枚举 `phase`；悬停有含义说明。
                 语义与全站主力标签一致（绿=机会 / 红=风险，非 A 股红涨绿跌）：
                 吸筹=机会、出货=风险、**拉升=追高警惕**（trade_gate 的拦截条件）、
                 洗盘=中性持有、下跌=回避、盘整=无方向。 -->
            <td class="py-2 px-3 text-xs">
              <span v-if="g.phase" class="px-1.5 py-0.5 rounded font-bold cursor-help"
                :class="(PHASE_STYLE[g.phase] || {}).cls || 'bg-white/5 text-muted'"
                :title="(PHASE_STYLE[g.phase] || {}).tip || ''">
                {{ g.phase_cn || g.phase }}
              </span>
              <span v-else class="text-muted">-</span>
            </td>
            <td class="py-2 px-3 text-right font-mono text-xs">
              <span :class="g.flow5_level === 'extreme' ? 'text-red-400 font-bold'
                            : g.flow5_level === 'over' ? 'text-amber-400'
                            : (g.flow5_amt ?? 0) > 0 ? 'text-red-400' : 'text-emerald-400'">
                {{ g.flow5_amt != null ? (g.flow5_amt > 0 ? '+' : '') + g.flow5_amt + '%' : '-' }}
              </span>
              <div v-if="g.flow5_level === 'extreme'" class="text-[10px] text-red-400 leading-tight">极端流入·陷阱区</div>
              <div v-else-if="g.flow5_level === 'over'" class="text-[10px] text-amber-400 leading-tight">过峰值区</div>
            </td>
            <td class="py-2 px-3 text-right font-mono text-xs">{{ g.price_pos != null ? (g.price_pos * 100).toFixed(0) + '%' : '-' }}</td>
            <td class="py-2 px-3 text-right font-mono text-xs">{{ g.winner_ratio != null ? (g.winner_ratio * 100).toFixed(0) + '%' : '-' }}</td>
            <td class="py-2 px-3 text-center text-xs">
              {{ g.position_label || '-' }}<span v-if="g.position_pct != null" class="text-muted ml-1">({{ g.position_pct }}%)</span>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="px-3 py-2 text-[11px] text-muted border-t border-border">
        「还差什么」= 未满足的买入条件（「状态允许」需 regime 转出 defensive）。就绪度由后端 trade_gate.summarize 唯一产出，不参与排序、不改总分。
        「5日主力占额」>5% 标「过峰值区」、>20% 标「极端流入·陷阱区」（倒U曲线打 0 分区间，散户陷阱假设）—— 与闸门 A 条件（吸筹区）是两个口径，强度过高不代表更好。
        「战法」列 = **双信号交叉**（闸门 ready ∩ 最新战法扫描命中，两个独立体系同时看中）；战法信号本身不推送（白名单为空：实测负期望）。
      </div>
    </div>

    <!-- 数据表格 -->
    <div v-if="activeTab !== 'verify' && activeTab !== 'backtest' && activeTab !== 'sector' && activeTab !== 'optimize' && activeTab !== 'anomaly' && activeTab !== 'shadow' && activeTab !== 'watch'" class="bg-card border border-border rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">排名</th>
            <th class="text-left py-2.5 px-3">代码</th>
            <th class="text-left py-2.5 px-3">名称</th>
            <th v-if="activeTab === 'top'" class="text-left py-2.5 px-3">板块</th>
            <th class="text-right py-2.5 px-3">涨跌幅</th>
            <th class="text-right py-2.5 px-3">综合评分</th>
            <th class="text-center py-2.5 px-3">信号</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">连续</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">可信度</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">消息</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">主力</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">买入时机</th>
            <th v-if="activeTab === 'top'" class="text-left py-2.5 px-3">买入原因</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(item, idx) in tableData" :key="item.code"
            class="border-b border-border/50 hover:bg-white/3 cursor-pointer transition-colors"
            @click="goDetail(item.code)">
            <td class="py-2 px-3 text-muted font-mono text-xs">{{ activeTab === 'bottom' ? stats.total - idx : idx + 1 }}</td>
            <td class="py-2 px-3 font-mono text-xs text-accent">
              <a :href="getXueqiuUrl(item.code)" target="_blank" rel="noopener"
                 @click.stop
                 class="hover:underline"
                 title="在雪球查看">{{ item.code }}</a>
            </td>
            <td class="py-2 px-3">{{ item.name }}</td>
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-xs truncate max-w-[90px]">
              <span :title="chainTip(item)" class="text-muted">{{ industryOf(item) }}</span>
            </td>
            <td class="py-2 px-3 text-right font-mono text-xs"
              :class="(item.change_pct || 0) > 0 ? 'text-red-400' : (item.change_pct || 0) < 0 ? 'text-emerald-400' : 'text-muted'">
              {{ (item.change_pct || 0) > 0 ? '+' : '' }}{{ (item.change_pct || 0).toFixed(2) }}%
            </td>
            <td class="py-2 px-3 text-right">
              <span class="font-bold" :class="item.total_score >= 65 ? 'text-emerald-400' : item.total_score >= 45 ? 'text-amber-400' : 'text-red-400'">
                {{ item.total_score }}
              </span>
              <div v-if="rankMode === 'composite' && item.composite_score != null" class="text-[10px] text-muted mt-0.5">
                组合分 {{ item.composite_score }}
              </div>
            </td>
            <td class="py-2 px-3 text-center">
              <span class="px-2 py-0.5 rounded-full text-xs"
                :class="item.signal.includes('买入') ? 'bg-emerald-500/20 text-emerald-400' :
                       item.signal.includes('卖出') ? 'bg-red-500/20 text-red-400' :
                       'bg-amber-500/20 text-amber-400'">
                {{ item.signal }}
              </span>
              <!-- 自我冲突：评分说买但主力在出货（回测 10 日 -7.5pt） -->
              <span v-if="item.signal.includes('买入') && item.mainforce?.signal === 'distribution'"
                class="ml-1 px-1.5 py-0.5 rounded-full text-[10px] bg-red-500/20 text-red-400 border border-red-500/30"
                title="自我冲突：评分看多但主力资金在出货——回测 10 日 -7.5pt，信号降权">
                ⚠ 冲突
              </span>
            </td>
            <!-- 连续上榜天数 -->
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center">
              <span v-if="persistenceMap[item.code]" 
                :class="persistenceMap[item.code].consecutive_days >= 5 ? 'text-emerald-400 font-bold' : persistenceMap[item.code].consecutive_days >= 3 ? 'text-amber-400 font-medium' : 'text-muted'">
                {{ persistenceMap[item.code].consecutive_days }}天
              </span>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <!-- 可信度等级 -->
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center">
              <span v-if="persistenceMap[item.code]" 
                :class="rankTrustClass(persistenceMap[item.code].trust_grade)"
                class="px-1.5 py-0.5 rounded text-xs font-bold"
                :title="persistenceMap[item.code].advice">
                {{ persistenceMap[item.code].trust_grade }}
              </span>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <!-- 消息面情绪分（参考，不参与综合分；前端本地模式无此数据） -->
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center">
              <span v-if="item.news_score != null"
                class="px-2 py-0.5 rounded-full text-xs font-bold"
                :class="item.news_score > 0 ? 'bg-emerald-500/20 text-emerald-400' :
                       item.news_score < 0 ? 'bg-red-500/20 text-red-400' :
                       'bg-white/5 text-muted'">
                {{ item.news_score > 0 ? '+' : '' }}{{ item.news_score }}
              </span>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <!-- 主力行为标注（筹码结构×资金流组合信号，日批计算；后端模式才有数据）
                 distribution=出货嫌疑（高位高获利+主力流出，回测10日-7.5pt）
                 accum=吸筹区（低位筹码密集+主力净流入，回测10日+1.1pt） -->
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center">
              <span v-if="item.mainforce?.signal"
                class="px-1.5 py-0.5 rounded text-xs font-bold cursor-help"
                :class="item.mainforce.signal === 'distribution' ? 'bg-red-500/20 text-red-400' :
                       'bg-emerald-500/20 text-emerald-400'"
                :title="`${item.mainforce.phase_cn || '盘整'}段 · 现价筹码位置 ${(item.mainforce.price_pos * 100).toFixed(0)}% · 获利盘 ${(item.mainforce.winner_ratio * 100).toFixed(0)}% · 5日主力占额累计 ${item.mainforce.flow5_amt ?? '-'}`">
                {{ item.mainforce.signal === 'distribution' ? '出货' : '吸筹' }}
              </span>
              <!-- ★ 2026-09-17 买入条件就绪（**状态展示，非买入信号**：不参与排序、不改总分）
                   就绪度由后端 trade_gate.summarize 唯一产出，前端只渲染 -->
              <span v-if="item.gate"
                class="ml-1 px-1 py-0.5 rounded text-[10px] font-mono cursor-help"
                :class="item.gate.ready === 3 ? 'bg-emerald-500/20 text-emerald-400' :
                       item.gate.ready === 2 ? 'bg-amber-500/20 text-amber-400' : 'bg-white/5 text-muted'"
                :title="`买入条件就绪 ${item.gate.ready}/3 · ${item.gate.label} ｜ ${item.gate.items.map(i => (i.ok ? '✓' : '✗') + i.label).join(' / ')} ｜ ${item.gate.hint}（建议仓位口径 ${item.gate.position_pct}%）`">
                {{ item.gate.ready }}/3
              </span>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <!-- 买入时机列：仅 Top 50 显示具体价位 + 时机标签 -->
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center">
              <div v-if="item.buy_point?.buy_timing" class="space-y-0.5">
                <!-- 时机标签：绿=适合介入 黄=等回调 红=追高风险 -->
                <span class="px-2 py-0.5 rounded-full text-xs font-medium"
                  :class="item.buy_point.buy_timing === '适合介入' ? 'bg-emerald-500/20 text-emerald-400' :
                         item.buy_point.buy_timing === '等回调' ? 'bg-amber-500/20 text-amber-400' :
                         'bg-red-500/20 text-red-400'">
                  {{ item.buy_point.buy_timing }}
                </span>
                <!-- 具体价位：当前价 → 建议区间 -->
                <div class="text-[11px] text-muted leading-tight">
                  <span>现价 {{ item.buy_point.current_price }}</span>
                  <span v-if="item.buy_point.buy_range" class="ml-1">
                    → <span class="text-gray-300">{{ item.buy_point.buy_range[0] }}-{{ item.buy_point.buy_range[1] }}</span>
                  </span>
                </div>
                <!-- Tooltip: 详细支撑位 -->
              </div>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <!-- 买入原因列：仅 Top 50 tab 显示，展示加分因素绿色小标签 -->
            <td v-if="activeTab === 'top'" class="py-2 px-3" @click.stop>
              <div v-if="item.factors_up && item.factors_up.length" class="flex flex-wrap gap-1">
                <span v-for="f in item.factors_up" :key="f"
                  class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20"
                  :title="'加分因素：' + f">{{ f }}</span>
              </div>
              <span v-else class="text-xs text-muted">-</span>
            </td>
            <!-- 操作列：仅 Top 50，一键添加到持仓 -->
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center space-x-1" @click.stop>
              <button v-if="!portfolioCodes?.has(item.code)"
                @click="quickAddPosition(item)"
                class="px-2 py-0.5 rounded text-[11px] bg-accent/15 text-accent hover:bg-accent/25 transition-colors"
                title="以当前价添加到持仓">
                + 持仓
              </button>
              <span v-else class="text-[11px] text-muted">已持有</span>
              <button v-if="!watchCodes.has(item.code)"
                @click="quickAddWatch(item)"
                class="px-2 py-0.5 rounded text-[11px] bg-white/5 text-muted hover:text-gray-200 hover:bg-white/10 transition-colors"
                title="加入自选股">
                + 自选
              </button>
              <span v-else class="text-[11px] text-muted">已自选</span>
            </td>
          </tr>
          <tr v-if="!tableData.length">
            <td :colspan="activeTab === 'top' ? 13 : 5" class="py-12 text-center text-muted">
              {{ cacheStatus === 'loading' ? '行情数据加载中，请稍后...' : '暂无数据' }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 胜率回查面板 -->
    <div v-if="activeTab === 'verify'" class="space-y-4">
      <!-- 操作栏 -->
      <div class="bg-card border border-border rounded-lg p-4 flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3 flex-wrap">
          <h2 class="text-lg font-bold">推荐胜率回查</h2>
          <span v-if="lastMsg" class="text-xs text-amber-400">{{ lastMsg }}</span>
        </div>
        <div class="flex gap-2">
          <button @click="captureSnapshot" :disabled="!tableData.length"
            class="px-3 py-1.5 rounded text-xs bg-accent/20 text-accent hover:bg-accent/30 transition-colors disabled:opacity-40">
            保存当前排行快照
          </button>
          <button @click="verifyAll" :disabled="!snapshotList.length || verifying"
            class="px-3 py-1.5 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors disabled:opacity-40">
            {{ verifying ? '查询中...' : '查询当前收益' }}
          </button>
        </div>
      </div>
      <!-- 汇总统计 -->
      <div v-if="verifySummary.total > 0" class="bg-card border border-border rounded-lg p-4">
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-center text-sm">
          <div class="p-2 bg-bg rounded-lg">
            <div class="text-muted text-xs">快照次数</div>
            <div class="text-lg font-bold mt-1">{{ verifySummary.total }}</div>
          </div>
          <!-- A股口径：盈利/正收益=红(rise)，亏损/负收益=绿(fall) -->
          <div class="p-2 bg-rise/10 rounded-lg">
            <div class="text-rise text-xs">推荐盈利占比</div>
            <div class="text-lg font-bold text-rise mt-1">{{ verifySummary.winRate }}%</div>
          </div>
          <div class="p-2" :class="verifySummary.avgReturn >= 0 ? 'bg-rise/10' : 'bg-fall/10'">
            <div class="text-xs" :class="verifySummary.avgReturn >= 0 ? 'text-rise' : 'text-fall'">平均收益</div>
            <div class="text-lg font-bold mt-1" :class="verifySummary.avgReturn >= 0 ? 'text-rise' : 'text-fall'">
              {{ verifySummary.avgReturn >= 0 ? '+' : '' }}{{ verifySummary.avgReturn }}%
            </div>
          </div>
          <div class="p-2 bg-bg rounded-lg">
            <div class="text-muted text-xs">最近快照</div>
            <div class="text-sm mt-1 text-gray-300">{{ verifySummary.lastDate || '-' }}</div>
          </div>
        </div>
      </div>
      <!-- 快照列表 -->
      <div v-for="snap in snapshotList" :key="snap.date" class="bg-card border border-border rounded-lg overflow-hidden">
        <div class="p-3 flex items-center justify-between cursor-pointer hover:bg-white/3" @click="toggleSnap(snap.date)">
          <div class="flex items-center gap-3">
            <span class="text-sm font-bold">{{ snap.date }}</span>
            <span class="text-xs text-muted">{{ snap.stocks.length }} 只</span>
            <span v-if="snap.verified" class="text-xs" :class="snap.winRate >= 50 ? 'text-rise' : 'text-fall'">
              胜率 {{ snap.winRate }}% · 均收益 {{ snap.avgReturn >= 0 ? '+' : '' }}{{ snap.avgReturn }}%
              <span class="text-muted ml-1">({{ fmtVerifyTime(snap.verifiedAt) }})</span>
            </span>
          </div>
          <span class="text-muted text-xs">{{ expandedSnapshots.has(snap.date) ? '收起' : '展开' }}</span>
        </div>
        <div v-if="expandedSnapshots.has(snap.date)">
          <table class="w-full text-sm">
            <thead>
              <tr class="border-t border-border text-muted text-xs">
                <th class="text-left py-2 px-3">代码</th>
                <th class="text-left py-2 px-3">名称</th>
                <th class="text-right py-2 px-3">评分</th>
                <th class="text-center py-2 px-3">信号</th>
                <th class="text-right py-2 px-3">快照价</th>
                <th class="text-right py-2 px-3">现价</th>
                <th class="text-right py-2 px-3">收益</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="s in snap.stocks" :key="s.code" class="border-b border-border/50 text-xs">
                <td class="py-1.5 px-3 font-mono text-accent">{{ s.code }}</td>
                <td class="py-1.5 px-3">{{ s.name }}</td>
                <td class="py-1.5 px-3 text-right">{{ s.score }}</td>
                <td class="py-1.5 px-3 text-center">
                  <span class="px-1.5 py-0.5 rounded-full text-[10px]"
                    :class="s.signal.includes('买入') ? 'bg-emerald-500/20 text-emerald-400' :
                           s.signal.includes('卖出') ? 'bg-red-500/20 text-red-400' :
                           'bg-amber-500/20 text-amber-400'">{{ s.signal }}</span>
                </td>
                <td class="py-1.5 px-3 text-right text-muted">{{ s.price || '-' }}</td>
                <td class="py-1.5 px-3 text-right">{{ s.currentPrice || '-' }}</td>
                <td class="py-1.5 px-3 text-right" :class="(s.returnPct || 0) >= 0 ? 'text-rise' : 'text-fall'">
                  {{ s.returnPct != null ? (s.returnPct >= 0 ? '+' : '') + s.returnPct + '%' : '-' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      <!-- 空状态 -->
      <div v-if="!snapshotList.length" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
        暂无快照记录，收盘后点击「保存当前排行快照」开始记录
      </div>
    </div>

    <!-- 历史回测面板 -->
    <div v-if="activeTab === 'backtest'" class="space-y-4">
      <!-- 配置区 -->
      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-lg font-bold mb-3">技术面历史回测</h2>
        <p class="text-xs text-muted mb-4">用过去 N 天的技术面评分模拟选股，计算持有 M 天后的实际收益。回测池：市值前 100 只。</p>
        <div class="flex items-end gap-4 flex-wrap">
          <div>
            <label class="text-xs text-muted block mb-1">选股数</label>
            <select v-model.number="btConfig.topN" class="bg-bg border border-border rounded px-2 py-1 text-sm">
              <option :value="5">Top 5</option>
              <option :value="10">Top 10</option>
              <option :value="20">Top 20</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-muted block mb-1">回测天数</label>
            <select v-model.number="btConfig.days" class="bg-bg border border-border rounded px-2 py-1 text-sm">
              <option :value="30">30 天</option>
              <option :value="60">60 天</option>
              <option :value="90">90 天</option>
            </select>
          </div>
          <button @click="runBacktest" :disabled="btLoading"
            class="px-4 py-1.5 rounded text-sm bg-accent/20 text-accent hover:bg-accent/30 transition-colors disabled:opacity-40">
            {{ btLoading ? '回测中（约 30-60 秒）...' : '开始回测' }}
          </button>
        </div>
      </div>
      <!-- 结果区 -->
      <div v-if="btResult" class="bg-card border border-border rounded-lg p-4">
        <div class="text-xs text-muted mb-3">
          回测 {{ btResult.backtest_days }} 天 · {{ btResult.stock_pool_size }} 只股票池 · 每日选 Top {{ btResult.top_n }}
          <span v-if="btResult.source === 'local'"
                class="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">本地计算</span>
          <span v-else
                class="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-white/5 text-muted border border-border">后端计算</span>
        </div>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div v-for="p in btResult.periods" :key="p" class="p-3 bg-bg rounded-lg text-center">
            <div class="text-muted text-xs mb-1">持有 {{ p }} 天</div>
            <div class="text-lg font-bold" :class="btResult.summary[p]?.avg_return >= 0 ? 'text-rise' : 'text-fall'">
              {{ btResult.summary[p]?.avg_return >= 0 ? '+' : '' }}{{ btResult.summary[p]?.avg_return }}%
            </div>
            <div class="text-xs mt-1">
              <span class="text-muted">胜率</span>
              <span class="font-bold" :class="btResult.summary[p]?.win_rate >= 50 ? 'text-rise' : 'text-fall'">
                {{ btResult.summary[p]?.win_rate }}%
              </span>
              <span class="text-muted ml-1">({{ btResult.summary[p]?.total }} 笔)</span>
            </div>
          </div>
        </div>
      </div>
      <!-- 空状态 -->
      <div v-if="!btResult && !btLoading" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
        配置参数后点击「开始回测」，验证技术面评分的历史预测力
      </div>
    </div>

    <!-- 权重优化面板 -->
    <div v-if="activeTab === 'optimize'" class="space-y-4">
      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-lg font-bold mb-2">评分权重优化分析</h2>
        <p class="text-xs text-muted mb-4">
          基于历史快照的实际收益表现，分析技术面/资金面/基本面三个维度的预测力，建议更优的权重分配。
          数据越多分析越准确——建议积累 7 天以上已验证快照后再查看。
        </p>
        <div v-if="optLoading" class="text-center text-muted py-8">分析中...</div>
        <div v-else-if="optResult?.error" class="text-center text-muted py-8">{{ optResult.error }}</div>
        <div v-else-if="optResult" class="space-y-4">
          <!-- 信号等级胜率 -->
          <div>
            <h3 class="text-sm font-semibold mb-2">
              各信号等级历史胜率
              <span class="text-xs font-normal text-muted">
                {{ optResult.horizon_days ? `（固定持有 ${optResult.horizon_days} 个交易日）` : '（快照日→现价混合窗口）' }}
              </span>
            </h3>
            <p class="text-[11px] text-muted mb-2">
              口径说明：买入信号存在短期（1 日）反转效应——快照时点技术形态最强 = 已涨过一段，
              次日回调概率高；持有拉长到 5 日后胜率显著回升（分桶实测 1日 39.7% → 5日 53.6%）。
              本面板为固定 5 日口径，与"买入后拿一天就走"的实际体感不同。
            </p>
            <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
              <div v-for="(stats, sig) in optResult.signal_analysis" :key="sig"
                class="p-2 rounded-lg bg-bg text-center">
                <div class="text-xs text-muted">{{ sig }}</div>
                <div class="text-lg font-bold mt-1" :class="stats.win_rate >= 50 ? 'text-rise' : 'text-fall'">
                  {{ stats.win_rate }}%
                </div>
                <div class="text-[10px] text-muted">{{ stats.count }}次 · 均{{ stats.avg_return >= 0 ? '+' : '' }}{{ stats.avg_return }}%</div>
              </div>
            </div>
          </div>
          <!-- 维度预测力 -->
          <div>
            <h3 class="text-sm font-semibold mb-2">维度预测力（与实际收益的相关性）</h3>
            <div class="grid grid-cols-3 gap-3">
              <div v-for="(corr, dim) in optResult.dim_correlation" :key="dim"
                class="p-3 rounded-lg bg-bg text-center">
                <div class="text-xs text-muted">{{ dim }}</div>
                <div v-if="corr !== null" class="text-lg font-bold mt-1"
                  :class="corr > 0.1 ? 'text-emerald-400' : corr < -0.1 ? 'text-red-400' : 'text-amber-400'">
                  {{ corr >= 0 ? '+' : '' }}{{ corr }}
                </div>
                <div v-else class="text-sm text-muted mt-1">数据不足</div>
              </div>
            </div>
            <p class="text-[10px] text-muted mt-1">相关系数 >0 表示该维度分高时实际收益好，越接近 +1 预测力越强</p>
          </div>
          <!-- 权重建议 -->
          <div>
            <h3 class="text-sm font-semibold mb-2">权重调整建议</h3>
            <div class="grid grid-cols-3 gap-3 mb-3">
              <div v-for="(cur, dim) in optResult.current_weights" :key="dim" class="p-3 rounded-lg bg-bg">
                <div class="text-xs text-muted mb-1">{{ dim }}</div>
                <div class="flex items-end gap-2">
                  <span class="text-lg font-bold">{{ (cur * 100).toFixed(0) }}%</span>
                  <span v-if="optResult.suggested_weights[dim] !== cur" class="text-sm text-accent mb-0.5">
                    → {{ (optResult.suggested_weights[dim] * 100).toFixed(0) }}%
                  </span>
                </div>
              </div>
            </div>
            <div class="space-y-1">
              <div v-for="(msg, i) in optResult.advice" :key="i" class="text-xs text-muted">· {{ msg }}</div>
            </div>
          </div>
          <div class="text-[10px] text-muted border-t border-border pt-2">
            样本量：{{ optResult.sample_size }} 条记录（{{ optResult.snapshot_count }} 天快照）。
            样本 <100 条时建议仅供参考，系统用保守混合策略（70%当前+30%最优）避免过拟合。
          </div>
        </div>
        <div v-else class="text-center text-muted py-8">
          切换到此处后自动分析，基于历史已验证快照评估评分权重
        </div>
      </div>
    </div>

    <!-- 异动监控面板 -->
    <div v-if="activeTab === 'anomaly'" class="space-y-4">
      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between flex-wrap gap-3 mb-3">
          <div>
            <h2 class="text-lg font-bold">全市场异动监控</h2>
            <p class="text-xs text-muted mt-1">检测急涨≥5%/急跌≤-5%/涨停/跌停/高换手>10%/大振幅>8%</p>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-xs text-muted">共 {{ anomalyTotal }} 条异动</span>
            <button @click="loadAnomalies" :disabled="anomalyLoading"
              class="px-3 py-1 rounded text-xs bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50">
              {{ anomalyLoading ? '加载中...' : '刷新' }}
            </button>
          </div>
        </div>
        <!-- 过滤按钮 -->
        <div class="flex gap-2 mb-3">
          <button @click="anomalyFilter = 'all'"
            :class="anomalyFilter === 'all' ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
            class="px-3 py-1 rounded text-xs transition-colors">全部</button>
          <button @click="anomalyFilter = 'watched'"
            :class="anomalyFilter === 'watched' ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
            class="px-3 py-1 rounded text-xs transition-colors">仅持仓</button>
          <button @click="anomalyFilter = 'high'"
            :class="anomalyFilter === 'high' ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
            class="px-3 py-1 rounded text-xs transition-colors">高严重度</button>
        </div>
        <!-- 异动列表 -->
        <div v-if="anomalyLoading" class="text-center text-muted py-8">扫描全市场缓存中...</div>
        <div v-else-if="filteredAnomalies.length" class="grid grid-cols-1 md:grid-cols-2 gap-2">
          <div v-for="a in filteredAnomalies" :key="a.code"
            class="flex items-center justify-between px-3 py-2 rounded bg-bg hover:bg-white/3 transition-colors cursor-pointer"
            @click="goDetail(a.code)">
            <div class="flex items-center gap-2 min-w-0">
              <span v-if="a.is_watched" class="px-1 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-400 flex-shrink-0">持仓</span>
              <span class="text-sm truncate">{{ a.name }}</span>
              <span class="text-xs text-muted font-mono">{{ a.code }}</span>
            </div>
            <div class="flex items-center gap-2 flex-shrink-0">
              <!-- 异动标签 -->
              <span v-for="tag in a.tags" :key="tag"
                class="px-1.5 py-0.5 rounded text-[10px]"
                :class="tag.includes('涨') || tag === '涨停' ? 'bg-red-500/20 text-red-400' :
                       tag.includes('跌') || tag === '跌停' ? 'bg-emerald-500/20 text-emerald-400' :
                       'bg-amber-500/20 text-amber-400'">
                {{ tag }}
              </span>
              <!-- 涨跌幅 -->
              <span class="text-xs font-mono font-bold w-16 text-right"
                :class="(a.change_pct || 0) > 0 ? 'text-red-400' : (a.change_pct || 0) < 0 ? 'text-emerald-400' : 'text-muted'">
                {{ (a.change_pct || 0) > 0 ? '+' : '' }}{{ (a.change_pct || 0).toFixed(2) }}%
              </span>
              <!-- 现价 -->
              <span class="text-xs font-mono w-16 text-right">{{ a.price }}</span>
            </div>
          </div>
        </div>
        <div v-else class="text-center text-muted py-8">
          暂无符合条件的异动记录
        </div>
      </div>
    </div>

    <!-- 板块分析面板 -->
    <div v-if="activeTab === 'sector'" class="space-y-4">
      <!-- 行业板块涨跌 -->
      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-lg font-bold mb-3">行业板块涨跌</h2>
        <div v-if="sectorLoading" class="text-center text-muted py-8">加载中...</div>
        <div v-else-if="sectorData.length" class="grid grid-cols-1 md:grid-cols-2 gap-2">
          <div v-for="(s, idx) in sectorData.slice(0, 20)" :key="idx"
            class="flex items-center justify-between px-3 py-2 rounded bg-bg hover:bg-white/3 transition-colors">
            <div class="flex items-center gap-2">
              <span class="text-xs text-muted w-5">{{ idx + 1 }}</span>
              <span class="text-sm">{{ s.name }}</span>
            </div>
            <div class="flex items-center gap-3 text-xs">
              <span class="text-muted">涨{{ s.up_count || 0 }}家</span>
              <span class="font-mono font-bold"
                :class="(s.change_pct || 0) > 0 ? 'text-red-400' : (s.change_pct || 0) < 0 ? 'text-emerald-400' : 'text-muted'">
                {{ (s.change_pct || 0) > 0 ? '+' : '' }}{{ (s.change_pct || 0).toFixed(2) }}%
              </span>
            </div>
          </div>
        </div>
      </div>
      <!-- 行业资金流 -->
      <div class="bg-card border border-border rounded-lg p-4">
        <h2 class="text-lg font-bold mb-3">行业资金流向（主力净流入）</h2>
        <div v-if="sectorFlowData.length" class="grid grid-cols-1 md:grid-cols-2 gap-2">
          <div v-for="(s, idx) in sectorFlowData.slice(0, 20)" :key="'f'+idx"
            class="flex items-center justify-between px-3 py-2 rounded bg-bg hover:bg-white/3 transition-colors">
            <div class="flex items-center gap-2">
              <span class="text-xs text-muted w-5">{{ idx + 1 }}</span>
              <span class="text-sm">{{ s.name }}</span>
            </div>
            <div class="text-xs font-mono font-bold"
              :class="(s.main_net_inflow || 0) > 0 ? 'text-red-400' : 'text-emerald-400'">
              {{ ((s.main_net_inflow || 0) / 100000000).toFixed(2) }}亿
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { upsertUserWatch, getUserWatchlist } from '../api'
import { getScoreTop, getScoreBottom, getScoreBySignal, getMarketTemperature, getBatchPrices, getBacktest, getSectorIndustry, getIndustryFlow, getWeightAdvice, getAnomalies, getRankingPersistence, checkExitAlerts, getKlineCacheStatus, triggerDailyBatch, getSnapshots, captureScoreSnapshot, getShadowRank, getGateWatch, getBatchIndustry, getPortfolioRadar } from '../api'
import { getXueqiuUrl } from '../composables/stockUtils'
import { addPosition, usePortfolio, isTradingTime, getRefreshInterval } from '../composables/usePortfolio'
import { useFrontendScoring, runLocalBacktest } from '../composables/useFrontendScoring'

const router = useRouter()

// bottom / signal 入口暂隐藏（后续优化完再开放）
const tabs = [
  { key: 'top', label: '评分 Top 50' },
  { key: 'positions', label: '我的持仓' },
  { key: 'watch', label: '观察池' },
  { key: 'shadow', label: '衰减对比' },
  { key: 'sector', label: '板块分析' },
  { key: 'verify', label: '胜率回查' },
  { key: 'backtest', label: '历史回测' },
  { key: 'optimize', label: '权重优化' },
  { key: 'anomaly', label: '异动监控' },
]
const signalOptions = ['强烈买入', '买入', '观望', '卖出', '强烈卖出']

const activeTab = ref('top')
const signalType = ref('买入')
const tableData = ref([])
// ★ 观察池（2026-09-22）：买入闸门 ready≥2 的「等状态」候选
const gateWatch = ref({ regime: '', total: 0, ready3: 0, items: [] })
const gateWatchLoading = ref(false)
// ★ 持仓雷达（2026-09-23）：以**用户持仓**为主轴的状态聚合
//   （评分 / 主力阶段 / 闸门就绪 / 战法命中 / 是否在观察池 / 所属板块被矛盾点名）
const portfolioRadar = ref({ items: [], summary: {}, market: {}, regime: '' })
const portfolioRadarLoading = ref(false)
const portfolioRadarError = ref('')
const portfolioRadarWarming = ref(false)
// 双信号交叉筛选（战法命中 = 闸门 ready ∩ 战法信号，两个独立体系）
const watchOnlyStrategy = ref(false)
const STRATEGY_SHORT = {
  ma_convergence_breakout: '收敛突破', single_yang_unbroken: '单阳不破',
  dragon_turnaround: '龙回头', ma_pullback: '回踩',
  limit_up_boomerang: '涨停回马枪', wizard_pointer: '神奇指针', old_duck_head: '老鸭头',
}
function strategyShort(n) { return STRATEGY_SHORT[n] || n }

// ★ 主力阶段配色（2026-09-22，用户要求"不同状态用不同颜色标签"）
//   键用**英文枚举**（后端 `phase` 即此，稳定）；显示用后端 `phase_cn`
//   （唯一映射源 `mainforce/phases.PHASE_CN`，前端不再自建中文映射）。
//   语义与全站主力标签一致（**绿=机会 / 红=风险**，非 A 股红涨绿跌）：
//     吸筹=机会｜出货=风险｜**拉升=追高警惕**（trade_gate/战法过滤的拦截条件，
//     故用琥珀而非红）｜洗盘=中性持有｜下跌=回避｜盘整=无方向。
//   ⚠️ 类名必须是**静态字面量**（Tailwind 只在源码里扫字符串，拼接会漏扫）。
const PHASE_STYLE = {
  accumulation: {
    cls: 'bg-emerald-500/20 text-emerald-400',
    tip: '吸筹段：主力低位建仓、时间换空间（闸门 A「主力有根据」的来源）',
  },
  shakeout: {
    cls: 'bg-cyan-500/20 text-cyan-300',
    tip: '洗盘段：缩量回调不破位＝主力没走，持有/观察',
  },
  markup: {
    cls: 'bg-amber-500/20 text-amber-400',
    tip: '拉升段：放量上攻，**追高风险**（闸门与战法过滤都会拦 markup）',
  },
  distribution: {
    cls: 'bg-red-500/20 text-red-400',
    tip: '出货段：高位放量滞涨，筹码换手给散户（回测 10 日 -7.5pt）',
  },
  decline: {
    cls: 'bg-zinc-500/20 text-zinc-400',
    tip: '下跌段：无主力接管，回避',
  },
  sideways: {
    cls: 'bg-white/5 text-muted',
    tip: '盘整段：无明显方向',
  },
}
// ── 持仓雷达的局部格式化（2026-09-23）────────────────────────────────────────
// ★ 复用语义，不新建第二套：
//   · 主力阶段 → 全站 `PHASE_STYLE`（绿=机会 / 红=风险）
//   · 涨跌与盈亏 → **A 股习惯红涨绿跌**（与观察池 `wpColor`、榜单 Top50 同口径）
function pnlText(v) {
  if (v == null) return '—'
  const n = Number(v)
  return Number.isFinite(n) ? (n > 0 ? '+' : '') + n.toFixed(2) + '%' : '—'
}
function pnlCls(v) {
  if (v == null) return 'text-muted'
  const n = Number(v)
  if (!Number.isFinite(n) || n === 0) return 'text-muted'
  return n > 0 ? 'text-red-400' : 'text-emerald-400'
}
/** 闸门就绪配色：3/3 绿（三条件齐）｜2/3 琥珀（等状态）｜≤1 灰（还差条件）—— 与观察池列同语义 */
function readyCls(ready) {
  if (ready == null) return 'text-muted'
  if (ready >= 3) return 'text-emerald-400 font-bold'
  if (ready === 2) return 'text-amber-400'
  return 'text-muted'
}
/** 持仓提示级别配色：risk 红 / opportunity 绿 / info 灰 */
function alertCls(level) {
  return level === 'risk' ? 'text-red-400'
    : level === 'opportunity' ? 'text-emerald-400' : 'text-muted'
}
const watchStrategyCount = computed(() =>
  (gateWatch.value.items || []).filter(g => g.strategies && g.strategies.length).length)
const watchItems = computed(() => watchOnlyStrategy.value
  ? (gateWatch.value.items || []).filter(g => g.strategies && g.strategies.length)
  : (gateWatch.value.items || []))
const cacheStatus = ref('loading')
const rankMode = ref('total_score')   // total_score | composite（nb 市组合分排序口径）
const stats = reactive({ total: 0, buyCount: 0, watchCount: 0, sellCount: 0 })
const temp = ref({})   // 市场环境温度（独立信号）
// ── 影子榜对比（生产 / 梯度衰减 / ×0 对照）──
const shadowData = ref({ date: null, base: [], zero: [], grad: [], overlap: {}, note: '' })
const shadowLoading = ref(false)
const shadowError = ref('')
const shadowCols = [
  { key: 'base', label: '生产榜', hint: '当前评分' },
  { key: 'grad', label: '梯度衰减', hint: '主·0-5天剔除/6-20天×0.5' },
  { key: 'zero', label: '×0 对照', hint: '公告后≤25天归零' },
]

// ── 自动刷新（盘中每60秒，非交易时段不自动刷新）──
const autoCountdown = ref(60)
const isTradingNow = ref(isTradingTime())
let refreshTimer = null

// ── 持仓联动 ──
const { positions } = usePortfolio()
const portfolioCodes = computed(() => new Set(positions.value.map(p => p.code)))

// ── 自选联动 ──
const watchCodes = ref(new Set())
async function loadWatchCodes() {
  try {
    const { data } = await getUserWatchlist()
    watchCodes.value = new Set((data || []).map(x => x.code))
  } catch (e) { /* 未登录/无自选不阻塞排行 */ }
}
// ── 页内盘中警示条（12s 轮询市场总览：指数急跌/涨跌比/跌停，秒级滞后）──
const marketAlerts = ref([])
let alertTimer = null
const _ALERT_RULES = [
  { key: 'indexdrop', test: (o) => {
      const idx = (o.indices || []).find(i => i.name === '上证指数')
      if (!idx || idx.change_pct == null) return null
      if (idx.change_pct <= -2.5) return { sev: '🔴', text: `上证急跌 ${idx.change_pct.toFixed(2)}%——不接飞刀、不加仓` }
      if (idx.change_pct <= -1.5) return { sev: '🟡', text: `上证下跌 ${idx.change_pct.toFixed(2)}%——单边走弱` }
      return null
    } },
  { key: 'breadth', test: (o) => {
      const st = o.stats || {}
      if (!st.up_count || !st.down_count) return null
      const r = st.up_count / Math.max(1, st.down_count)
      if (r < 0.25) return { sev: '🔴', text: `涨跌比 ${st.up_count}/${st.down_count}（${r.toFixed(2)}）——跌停潮式结构恶化` }
      return null
    } },
  { key: 'limitdown', test: (o) => {
      const ld = (o.stats || {}).limit_down
      if (ld >= 30) return { sev: ld >= 60 ? '🔴' : '🟡', text: `跌停 ${ld} 只——恐慌蔓延，不抄底、不补仓` }
      return null
    } },
]
async function checkMarketAlerts() {
  // 休盘时数据冻结、警示不可能新触发 → 清空并停查（与项目"非交易时段不轮询"规范一致）
  if (!isTradingTime()) {
    marketAlerts.value = []
    return
  }
  try {
    const { data: o } = await getMarketOverview()
    const hits = _ALERT_RULES.map(r => r.test(o)).filter(Boolean)
    marketAlerts.value = hits
  } catch { marketAlerts.value = [] }
}
function startMarketAlerts() {
  stopMarketAlerts()
  checkMarketAlerts()
  alertTimer = setInterval(checkMarketAlerts, 12000)
}
function stopMarketAlerts() {
  if (alertTimer) { clearInterval(alertTimer); alertTimer = null }
}

async function quickAddWatch(item) {
  try {
    await upsertUserWatch({ code: item.code, name: item.name,
                            note: `评分${item.total_score} ${item.signal}` })
    watchCodes.value = new Set([...watchCodes.value, item.code])
  } catch (e) { alert('加自选失败：' + (e.response?.data?.detail || e.message)) }
}

// ── 排行榜可信度（连续上榜天数） ──
const persistenceMap = ref({})  // { code: { consecutive_days, trust_score, trust_grade, advice } }
const persistenceError = ref('')

// ── 持仓撤退提醒 ──
const exitAlerts = ref([])

// ── 前端评分系统 ──
const {
  dbReady: frontendDbReady,
  dbStockCount: frontendStockCount,
  isComputing: frontendComputing,
  isUpdating: frontendUpdating,
  updateProgress: frontendProgress,
  error: frontendError,
  poolCount: frontendPoolCount,
  useFrontendMode,
  initFrontendScoring,
  downloadKlineData,
  computeRanking,
  shadowBoards,
  saveFrontendModePreference,
} = useFrontendScoring()
const frontendInitialized = ref(false)

// ── K线缓存状态 ──
const klineCacheStatus = ref(null)
const klineCacheRefreshing = ref(false)
// 重任务已投递 GitHub Actions 的反馈文案
const klineCacheMsg = ref('')

function quickAddPosition(item) {
  // 以当前价 + 默认 100 股添加到持仓
  const price = item.buy_point?.current_price || 0
  if (!price) return
  addPosition({
    code: item.code,
    name: item.name,
    cost: price,
    shares: 100,
    note: `评分${item.total_score} ${item.signal}`,
  })
}

// ── 加载排行榜可信度 ──
async function loadPersistence() {
  if (!tableData.value.length || activeTab.value !== 'top') return
  persistenceError.value = ''
  try {
    const codes = tableData.value.map(i => i.code)
    const { data } = await getRankingPersistence(codes)
    const list = data.data || []
    if (data.error) {
      persistenceError.value = data.error
      persistenceMap.value = {}
      return
    }
    const map = {}
    for (const item of list) {
      map[item.code] = item
    }
    persistenceMap.value = map
  } catch (e) {
    // 显示到界面：超时/网络失败时浏览器统一报 CORS，真实原因需要看 detail
    persistenceError.value = (e?.response?.status ? `HTTP ${e.response.status} ` : '') +
      (e?.response?.data?.detail || e?.message || '未知错误（可能是接口超时）')
    persistenceMap.value = {}
    console.error('加载排行可信度失败', e)
  }
}

// ── 加载持仓撤退提醒 ──
async function loadExitAlerts() {
  if (!positions.value.length) {
    exitAlerts.value = []
    return
  }
  try {
    const posList = positions.value.map(p => ({
      code: p.code,
      name: p.name,
      entry_price: p.cost,
      stop_loss: p.stop_loss || 0,
      target_price: p.target_price || 0,
    }))
    const { data } = await checkExitAlerts(posList)
    exitAlerts.value = data.data || []
  } catch (e) {
    console.error('加载撤退提醒失败', e)
  }
}

// ── 可信度等级样式 ──
function rankTrustClass(grade) {
  return {
    'A+': 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30',
    'A': 'bg-emerald-500/15 text-emerald-400',
    'B': 'bg-amber-500/15 text-amber-400',
    'C': 'bg-white/5 text-muted',
    'D': 'bg-red-500/15 text-red-400',
  }[grade] || 'text-muted'
}

// ── K线数据库缓存 ──
async function loadKlineCacheStatus() {
  try {
    const res = await getKlineCacheStatus()
    klineCacheStatus.value = res.data
  } catch (e) {
    console.error('加载K线缓存状态失败', e)
  }
}

async function handleRefreshKlineCache() {
  if (klineCacheRefreshing.value) return
  klineCacheRefreshing.value = true
  try {
    // ★ K线/评分数据刷新是重活，转发 GitHub Actions（本地跑会打爆 512MB 实例）
    //   backfill=回测价格回填，score_snapshot=评分 Top50 快照
    const r = await triggerDailyBatch('backfill,score_snapshot')
    klineCacheMsg.value = r.data?.message
      || '已提交「数据回填 + 评分快照」到 GitHub Actions，约 5-10 分钟后刷新查看'
    setTimeout(() => { klineCacheRefreshing.value = false }, 1000)
  } catch (e) {
    console.error('提交刷新任务失败', e)
    klineCacheMsg.value = '提交失败：' + (e.response?.data?.detail || e.message)
    klineCacheRefreshing.value = false
  }
}

// 切换前端/后端模式，并持久化
function toggleFrontendMode() {
  useFrontendMode.value = !useFrontendMode.value
  saveFrontendModePreference(useFrontendMode.value)
  // 切换后立即重新加载
  loadData()
}

// 下载前端 K 线数据
async function handleDownloadKlineData() {
  if (frontendUpdating.value) return
  const result = await downloadKlineData()
  if (result.updated) {
    console.log('数据下载完成:', result.message)
    // 不再自动切换模式：下载/更新数据只是让“本地计算”变得可用，
    // 是否使用由用户手动切换，避免前后端榜单不一致被误认为 bug
    if (useFrontendMode.value) {
      await loadFrontendRanking()
    } else {
      await loadData()
    }
  } else {
    console.warn('数据下载失败:', result.message)
  }
}

// ── 快照 / 胜率回查 ──
const SNAP_KEY = 'score_snapshots'
const snapshots = ref({})          // { 'YYYY-MM-DD': { ts, stocks: [...] } }
let snapshotsSource = 'local'      // 'server' | 'local'：快照来源（后端统一后默认 server）
const snapshotList = computed(() =>
  Object.entries(snapshots.value)
    .sort(([a], [b]) => b.localeCompare(a))
    .map(([date, d]) => ({ date, ...d }))
)
const expandedSnapshots = ref(new Set())
const verifying = ref(false)
const lastAutoSaveDate = ref('')
const lastMsg = ref('')            // 快照操作结果提示（如非交易日拒绝保存）
let autoSaveTimer = null

// ── 评分变动提醒 ──
const scoreAlerts = ref({ upgrades: [], downgrades: [] })

// ── 历史回测 ──
const btConfig = reactive({ topN: 10, days: 30 })
const btResult = ref(null)
const btLoading = ref(false)

// ── 权重优化 ──
const optResult = ref(null)
const optLoading = ref(false)

async function runWeightAnalysis() {
  optLoading.value = true
  optResult.value = null
  try {
    // 统一后：不传快照，后端自动从每日快照库（含维度分+价格）读取已验证记录，
    // 收益由后端按现价计算，不再依赖前端手动验证
    const { data } = await getWeightAdvice()
    if (data.error) {
      optResult.value = { error: data.error }
    } else {
      optResult.value = data
    }
  } catch (e) {
    optResult.value = { error: '分析请求失败' }
  } finally {
    optLoading.value = false
  }
}

// ── 板块分析 ──
const sectorData = ref([])
const sectorFlowData = ref([])
const sectorLoading = ref(false)

// ── 异动监控 ──
const anomalyData = ref([])
const anomalyTotal = ref(0)
const anomalyLoading = ref(false)
const anomalyFilter = ref('all') // all / watched / high

async function loadAnomalies() {
  anomalyLoading.value = true
  try {
    // 把持仓代码传过去，优先显示持仓异动
    const watchedCodes = portfolioCodes.value ? [...portfolioCodes.value].join(',') : ''
    const { data } = await getAnomalies(watchedCodes)
    anomalyData.value = data.data || []
    anomalyTotal.value = data.total || 0
  } catch (e) {
    console.error(e)
  } finally {
    anomalyLoading.value = false
  }
}

const filteredAnomalies = computed(() => {
  if (anomalyFilter.value === 'watched') return anomalyData.value.filter(a => a.is_watched)
  if (anomalyFilter.value === 'high') return anomalyData.value.filter(a => a.severity >= 2)
  return anomalyData.value
})

async function loadSectorData() {
  sectorLoading.value = true
  try {
    const [indRes, flowRes] = await Promise.allSettled([
      getSectorIndustry({ limit: 50 }),
      getIndustryFlow({ limit: 30 }),
    ])
    sectorData.value = indRes.status === 'fulfilled' ? (indRes.value.data?.data || []) : []
    sectorFlowData.value = flowRes.status === 'fulfilled' ? (flowRes.value.data?.data || []) : []
  } catch (e) {
    console.error(e)
  } finally {
    sectorLoading.value = false
  }
}

const verifySummary = computed(() => {
  const verified = snapshotList.value.filter(s => s.verified)
  if (!verified.length) return { total: 0, winRate: 0, avgReturn: 0, lastDate: '' }
  let totalStocks = 0, wins = 0, totalReturn = 0
  for (const snap of verified) {
    for (const s of snap.stocks) {
      if (s.returnPct != null) {
        totalStocks++
        if (s.returnPct > 0) wins++
        totalReturn += s.returnPct
      }
    }
  }
  return {
    total: verified.length,
    winRate: totalStocks ? Math.round(wins / totalStocks * 100) : 0,
    avgReturn: totalStocks ? (totalReturn / totalStocks).toFixed(2) : 0,
    lastDate: verified[0]?.date || '',
  }
})

// ── 快照管理 ──
function toggleSnap(date) {
  const s = new Set(expandedSnapshots.value)
  s.has(date) ? s.delete(date) : s.add(date)
  expandedSnapshots.value = s
}

async function loadSnapshots() {
  // 统一后：优先后端快照库（每日自动落库，含维度分+价格+收益，跨设备不丢数据）；
  // 后端不可用（离线/开发）才回退本地 localStorage 缓存
  try {
    const { data } = await getSnapshots(30)
    const list = data.data || []
    if (list.length) {
      const obj = {}
      for (const snap of list) obj[snap.date] = snap
      snapshots.value = obj
      snapshotsSource = 'server'
      return
    }
  } catch { /* 后端不可用，走本地 */ }
  try {
    const raw = localStorage.getItem(SNAP_KEY)
    if (raw) snapshots.value = JSON.parse(raw)
  } catch { snapshots.value = {} }
  snapshotsSource = 'local'
}

function saveSnapshots() {
  localStorage.setItem(SNAP_KEY, JSON.stringify(snapshots.value))
}

async function captureSnapshot() {
  // 统一后：后端立即记录当日 Top 50 快照（含维度分+价格，同日幂等覆盖），
  // 与调度器每日自动任务同源；后端不可用（离线/开发）才回退本地 localStorage。
  try {
    const { data } = await captureScoreSnapshot()
    if (data && data.recorded > 0) {
      lastAutoSaveDate.value = data.date || new Date().toISOString().slice(0, 10)
      await loadSnapshots()   // 重新从后端拉取最新快照列表
      return
    }
    if (data && data.error) {
      // 后端明确拒绝（如非交易日休市）：提示且不落本地兜底，避免周末写入脏快照
      console.warn('[snapshot]', data.error)
      lastMsg.value = data.error
      return
    }
  } catch { /* 后端不可用，走本地兜底 */ }

  // ── 本地兜底（离线模式）：始终拉取最新 Top 50 ──
  let freshData
  try {
    const res = await getScoreTop({ limit: 50 })
    freshData = res.data.data || []
  } catch { return }
  if (!freshData.length) return

  const codes = freshData.map(i => i.code)
  let priceMap = {}
  try {
    const { data } = await getBatchPrices(codes)
    priceMap = Object.fromEntries(data.map(s => [s.code, s.price]))
  } catch { /* 价格获取失败，后续可回查 */ }

  // 价格覆盖率检查：低于 80% 则不保存（数据不完整）
  const withPrice = freshData.filter(i => priceMap[i.code] > 0).length
  if (withPrice < freshData.length * 0.8) return

  const today = new Date().toISOString().slice(0, 10)
  snapshots.value[today] = {
    ts: Date.now(),
    stocks: freshData.map(i => ({
      code: i.code, name: i.name, score: i.total_score,
      signal: i.signal, price: priceMap[i.code] || 0,
      // 维度分（用于权重优化分析）
      dimensions: i.dimensions || {},
    })),
  }
  saveSnapshots()
  lastAutoSaveDate.value = today
}

function detectScoreChanges() {
  // 与最近一次快照对比，检测信号升降级
  const dates = Object.keys(snapshots.value).sort().reverse()
  if (!dates.length || !tableData.value.length) return
  const prev = snapshots.value[dates[0]]
  const prevMap = Object.fromEntries(prev.stocks.map(s => [s.code, s]))
  const upgrades = [], downgrades = []
  const signalRank = { '强烈买入': 2, '买入': 1, '观望': 0, '卖出': -1, '强烈卖出': -2 }
  for (const cur of tableData.value) {
    const p = prevMap[cur.code]
    if (!p) continue
    const oldR = signalRank[p.signal] ?? 0
    const newR = signalRank[cur.signal] ?? 0
    if (newR > oldR) upgrades.push({ ...cur, prevSignal: p.signal, prevScore: p.score })
    else if (newR < oldR) downgrades.push({ ...cur, prevSignal: p.signal, prevScore: p.score })
  }
  scoreAlerts.value = { upgrades, downgrades }
}

async function verifyAll() {
  if (!snapshotList.value.length) return
  verifying.value = true
  try {
    // 统一后：后端快照已按现价自动计算收益（保存满 1 天），重新拉取即可
    await loadSnapshots()
    // 本地兜底模式（后端不可用，快照来自 localStorage）才需要本地计算收益
    if (snapshotsSource === 'local') {
      const allCodes = new Set()
      for (const snap of snapshotList.value) {
        for (const s of snap.stocks) allCodes.add(s.code)
      }
      const { data } = await getBatchPrices([...allCodes])
      const priceMap = Object.fromEntries(data.map(s => [s.code, s]))
      for (const [date, snap] of Object.entries(snapshots.value)) {
        let wins = 0, totalRet = 0, cnt = 0
        for (const s of snap.stocks) {
          const cur = priceMap[s.code]
          if (cur && s.price > 0) {
            s.currentPrice = cur.price
            s.returnPct = +((cur.price - s.price) / s.price * 100).toFixed(2)
            cnt++
            totalRet += s.returnPct
            if (s.returnPct > 0) wins++
          }
        }
        snap.verified = cnt > 0
        snap.verifiedAt = Date.now()
        snap.winRate = cnt ? Math.round(wins / cnt * 100) : 0
        snap.avgReturn = cnt ? +(totalRet / cnt).toFixed(2) : 0
      }
      saveSnapshots()   // 持久化验证结果，刷新页面后仍可看到
    }
  } catch (e) { console.error(e) }
  verifying.value = false
}

function autoSaveCheck() {
  const now = new Date()
  if (now.getDay() === 0 || now.getDay() === 6) return
  const h = now.getHours(), m = now.getMinutes()
  if (h < 9 || h > 15) return
  const today = now.toISOString().slice(0, 10)
  if (lastAutoSaveDate.value === today) return
  // 15:10 后自动保存（给数据源留 10 分钟稳定时间）
  // captureSnapshot 会自己拉取最新 Top 50，不依赖 tableData
  if (h === 15 && m >= 10 || h > 15) {
    captureSnapshot()
  }
}

async function runBacktest() {
  btLoading.value = true
  btResult.value = null
  try {
    let data = null
    // 本地 K 线库可用时优先本地计算：零后端请求、不触发 WAF、不受服务重启影响
    if (frontendStockCount.value >= 100) {
      try {
        const local = await runLocalBacktest({
          topN: btConfig.topN,
          days: btConfig.days,
        })
        if (local && !local.error) {
          data = local
        } else if (local && local.error) {
          console.warn('[本地回测不可用]', local.error)
        }
      } catch (e) {
        console.warn('[本地回测失败，回退后端]', e)
      }
    }
    // 本地不可用/失败 → 回退后端接口
    if (!data) {
      const { data: remote } = await getBacktest({
        top_n: btConfig.topN,
        days: btConfig.days,
      })
      data = remote
    }
    if (data && data.error) {
      console.error(data.error)
    } else {
      btResult.value = data
    }
  } catch (e) { console.error(e) }
  btLoading.value = false
}

async function loadData() {
  // 如果使用前端模式，调用前端计算
  if (useFrontendMode.value && frontendDbReady.value) {
    await loadFrontendRanking()
    return
  }

  try {
    let res
    if (activeTab.value === 'top') {
      res = await getScoreTop({ limit: 50 })
    } else if (activeTab.value === 'bottom') {
      res = await getScoreBottom({ limit: 50 })
    } else {
      res = await getScoreBySignal({ signal: signalType.value, limit: 50 })
    }
    const d = res.data
    tableData.value = d.data || []
    cacheStatus.value = d.cache_status || 'unknown'
    rankMode.value = d.rank_mode || 'total_score'
    stats.total = d.total || 0
    // 简单统计
    stats.buyCount = tableData.value.filter(i => i.signal.includes('买入')).length
    stats.watchCount = tableData.value.filter(i => i.signal === '观望').length
    stats.sellCount = tableData.value.filter(i => i.signal.includes('卖出')).length
    // Top 50 加载完成后检测与上次快照的信号变动
    if (activeTab.value === 'top') {
      detectScoreChanges()
      loadPersistence()  // 加载连续上榜天数
      loadExitAlerts()   // 加载持仓撤退提醒
      // 前端模式下不加载后端K线缓存状态（无关）
      if (!useFrontendMode.value) {
        loadKlineCacheStatus()
      }
    }
  } catch (e) {
    console.error('后端评分失败:', e.message)
    // 不再静默切换本地模式：本地计算仅由用户手动开启，
    // 避免前端 K 线包不完整/算法口径差异导致前后端榜单不一致
    cacheStatus.value = 'error'
  }
}

// 前端计算排行榜
async function loadFrontendRanking() {
  cacheStatus.value = 'computing'
  rankMode.value = 'total_score'   // 本地计算无组合分排序口径
  
  const mode = activeTab.value === 'bottom' ? 'bottom' :
               (activeTab.value === 'top' || activeTab.value === 'shadow') ? 'top' : 'signal'
  
  const results = await computeRanking({
    mode,
    limit: 50,
    signal: signalType.value,
  })
  
  if (results && results.length > 0) {
    tableData.value = results
    cacheStatus.value = 'ready'
    // 影子榜（本地）：与主榜同一次精算同源、盘中实时、零额外网络/Supabase 成本
    if (shadowBoards.value && shadowBoards.value.base && shadowBoards.value.base.length) {
      shadowData.value = { ...shadowBoards.value }
    }
    // 与后端模式对齐：统计全量股票池数量，而非返回列表长度（列表固定截取 Top 50）
    stats.total = frontendPoolCount.value || results.length
    stats.buyCount = results.filter(i => i.signal.includes('买入')).length
    stats.watchCount = results.filter(i => i.signal === '观望').length
    stats.sellCount = results.filter(i => i.signal.includes('卖出')).length
    
    if (activeTab.value === 'top') {
      detectScoreChanges()
      // 与后端模式对齐：本地计算同样加载连续上榜/可信度 + 持仓撤退提醒
      loadPersistence()
      loadExitAlerts()
    }
  } else {
    cacheStatus.value = 'error'
  }
}

// 影子榜数据源判定：本地模式 → 与主榜同源的本地结果（算主榜时顺带产出）；
// 否则 → 后端接口 /score/batch/shadow-rank（读 ranking_live，盘中为昨日快照）
function shadowLocal() {
  return useFrontendMode.value && frontendDbReady.value
}

async function loadShadowRank() {
  shadowLoading.value = true
  shadowError.value = ''
  try {
    const { data } = await getShadowRank(50)
    shadowData.value = data
    if (!data.date) shadowError.value = data.note || '暂无旁路数据'
  } catch (e) {
    shadowError.value = '加载失败：' + (e?.response?.data?.detail || e?.message || e)
  } finally {
    shadowLoading.value = false
  }
}

// ★ 观察池加载（2026-09-22）：低频事件做「候池」——多数交易日三绿为 0，
//   日常价值在"还差一步"的池子；数据由后端 trade_gate.summarize 唯一产出。
async function loadGateWatch() {
  gateWatchLoading.value = true
  try {
    // ⚠️ 2026-09-22 修复：拦截器是 `response => response`（返回**完整 axios response**），
    //   此处原先写 `const d = await getGateWatch(80)` 后取 `d.items` ⇒ 恒 undefined
    //   （真实数据在 `d.data.items`）⇒ 静默走空分支 ⇒ **观察池永远显示"无候选"**。
    //   教训：`pnpm build` 通过 ≠ 功能可用（build 不查运行时契约），必须真跑页面验收。
    //   全项目约定：`const { data } = await api.xxx()` —— 本处已对齐。
    const { data } = await getGateWatch(80)
    gateWatch.value = (data && data.items) ? data : { regime: '', total: 0, ready3: 0, items: [] }
  } catch (e) {
    gateWatch.value = { regime: '', total: 0, ready3: 0, items: [] }
  } finally {
    gateWatchLoading.value = false
  }
}

// ★ 持仓雷达加载（2026-09-23）—— 以**持仓**为主轴，回答「我手里那几只怎么样」。
//   ⚠️ 与观察池同款约定：拦截器返回**完整 axios response** ⇒ 必须 `const { data } = await`。
//   ⚠️ 后端冷启动首次约半分钟 ⇒ 可能返回 `warming: true` 占位（服务端有预热 loop，
//      正常秒开）；此处显示「正在准备」，**不要当成错误**。
async function loadPortfolioRadar() {
  portfolioRadarLoading.value = true
  portfolioRadarError.value = ''
  try {
    const { data } = await getPortfolioRadar()
    if (data && data.warming) {
      portfolioRadarWarming.value = true
      portfolioRadarError.value = data.note || '正在准备持仓数据…'
    } else {
      portfolioRadarWarming.value = false
      portfolioRadar.value = data || { items: [], summary: {} }
    }
  } catch (e) {
    portfolioRadarError.value = '加载失败：' + (e?.response?.data?.detail || e?.message || e)
  } finally {
    portfolioRadarLoading.value = false
  }
}

// ── 观察池：实时涨跌幅（2026-09-22 用户需求）────────────────────────────────
// ★ 为什么单独拉价、不让 gate-watch 顺带返回：
//   ① 闸门就绪度 / 主力阶段是**日频**数据（mainforce_state 日更）⇒ 盘中重算没有意义；
//   ② gate-watch 是**全池重算**（2196 只、>30s，易撞前端超时）⇒ 绝不能拿它做 60s 轮询；
//   ③ batch-prices 一次轻请求就够（复用项目既有接口，返回 {code,name,price,change_pct}）。
//   ⇒ 60s 自动刷新时观察池**只刷价格**（见 startAutoRefresh），闸门数据不重算。
const watchPriceMap = ref({})

/** 观察池涨跌幅文案（无数据/未拉到时给「—」；数值缺失**不崩** —— 行情偶发返回 null）。 */
function wpText(code) {
  const p = watchPriceMap.value[code]
  if (!p || p.change_pct == null) return '—'
  const v = Number(p.change_pct)
  return (Number.isFinite(v) ? (v > 0 ? '+' : '') + v.toFixed(2) : '—') + '%'
}

/** 观察池涨跌幅配色：A 股习惯红涨绿跌（与榜单 Top50 同口径）。 */
function wpColor(code) {
  const p = watchPriceMap.value[code]
  const v = p && p.change_pct != null ? Number(p.change_pct) : null
  if (v == null || !Number.isFinite(v) || v === 0) return 'text-muted'
  return v > 0 ? 'text-red-400' : 'text-emerald-400'
}

async function loadWatchPrices() {
  const codes = (gateWatch.value.items || []).map(g => g.code).filter(Boolean)
  if (!codes.length) return
  try {
    const { data } = await getBatchPrices(codes)
    const m = {}
    for (const s of data || []) {
      if (s && s.code) m[s.code] = { price: s.price, change_pct: s.change_pct }
    }
    if (Object.keys(m).length) watchPriceMap.value = m
  } catch (e) {
    // 拉价失败：保留上一次的值（不清空 ⇒ 页面不闪 '-'）；交易时段外多为休市/网络抖动
    console.warn('[watch] 实时价刷新失败', e?.message || e)
  }
}

function switchTab(tab) {
  activeTab.value = tab
  // ★ 持仓雷达（2026-09-23）：每次切进来都重拉（后端有缓存 + 预热 ⇒ 秒开）
  if (tab === 'positions') loadPortfolioRadar()
  // 观察池：先取闸门清单（拿到 code 列表）→ 再拉一次实时涨跌幅
  else if (tab === 'watch') loadGateWatch().then(loadWatchPrices)
  else if (tab === 'sector') loadSectorData()
  else if (tab === 'shadow') {
    // 本地模式：走 loadData（算主榜时顺带产出同源影子榜）；否则走后端接口
    if (shadowLocal()) loadData()
    else loadShadowRank()
  }
  else if (tab === 'optimize') runWeightAnalysis()
  else if (tab === 'anomaly') loadAnomalies()
  else if (tab !== 'verify' && tab !== 'backtest') loadData()
}

// ── 板块归属（2026-09-23，用户要求：三 tab 都显示，**细分 + 悬停给归属链**）──────
// ★ 数据源：后端 `/score/batch/industry-map`（`stock_industry`：东财**细分**主行业 +
//   层级链）。为什么按需拉、而不塞进三个榜单接口的返回：
//     · 三处返回结构各异，且观察池主路径读的是**日批快照** ⇒ 散点插入要改快照结构；
//     · 行业是**慢变数据** ⇒ 前端页面级缓存一份即可，缺哪个补哪个（一次 ≈1KB）。
//   ⇒ 三 tab 共用同一映射；**本地评分模式**（pack 里无行业字段）同样能被补齐。
const industryMap = ref({})

function _codesMissingIndustry() {
  const need = new Set()
  const scan = (arr) => {
    for (const x of arr || []) {
      if (x && x.code && !x.industry && !industryMap.value[x.code]) need.add(x.code)
    }
  }
  scan(tableData.value)
  scan(gateWatch.value.items)
  scan(shadowData.value.base)
  scan(shadowData.value.zero)
  scan(shadowData.value.grad)
  return [...need]
}

async function ensureIndustry() {
  const codes = _codesMissingIndustry()
  if (!codes.length) return
  try {
    const { data } = await getBatchIndustry(codes)
    if (data && typeof data === 'object') {
      industryMap.value = { ...industryMap.value, ...data }
    }
  } catch (e) {
    // 板块是非关键信息：失败静默（该列显示 —），不影响排序/评分/闸门
    console.warn('[industry] 板块信息拉取失败', e?.message || e)
  }
}

/** 展示用：优先条目自带字段，其次映射缓存（都没有 ⇒ —）。 */
function industryOf(o) {
  if (!o) return '—'
  return o.industry || (industryMap.value[o.code] || {}).industry || '—'
}

/** 悬停提示：归属层级链（细分 ⊂ 上级 ⊂ …）。无链时回退行业名。 */
function chainTip(o) {
  if (!o) return ''
  const raw = o.industry_chain || (industryMap.value[o.code] || {}).chain || []
  const arr = (Array.isArray(raw) ? raw : [raw]).filter(Boolean)
  if (arr.length) return `板块归属：${arr.join(' ⊂ ')}`
  const name = industryOf(o)
  return name === '—' ? '' : `板块：${name}`
}

// 三 tab 数据一变化就补齐（切 tab / 刷新 / 本地评分完成都会触发）；缺失才请求 ⇒ 幂等
watch([tableData, () => gateWatch.value.items, shadowData], () => { ensureIndustry() })

function goDetail(code) {
  const { href } = router.resolve(`/stock/${code}`)
  window.open(href, '_blank')
}

// 市场温度等级配色：冷→蓝，中性→琥珀，热→红
function levelColor(level) {
  return { '过热': 'text-red-400', '偏热': 'text-orange-400', '中性': 'text-amber-400',
           '偏冷': 'text-cyan-400', '过冷': 'text-blue-400' }[level] || 'text-muted'
}

// 验证时间格式化：显示“刚刚”或具体日期
function fmtVerifyTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const today = new Date()
  if (d.toDateString() === today.toDateString()) {
    return `今天 ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  return `${d.getMonth() + 1}/${d.getDate()}`
}

async function loadTemp() {
  try {
    const { data } = await getMarketTemperature()
    temp.value = data
  } catch (e) { console.error(e) }
}

// ── 到价提醒：检查 Top 50 中是否有股票回调到买入区间 ──
const alertedCodes = ref(new Set())  // 已通知过的代码（避免重复通知）

async function checkPriceAlerts() {
  if (!tableData.value.length || activeTab.value !== 'top') return
  // 只检查有买入区间且未通知过的
  const needCheck = tableData.value.filter(
    i => i.buy_point?.buy_range && !alertedCodes.value.has(i.code) && !portfolioCodes.value.has(i.code)
  )
  if (!needCheck.length) return

  const codes = needCheck.map(i => i.code)
  let priceMap = {}
  try {
    const { data } = await getBatchPrices(codes)
    priceMap = Object.fromEntries(data.map(s => [s.code, s.price]))
  } catch { return }

  for (const item of needCheck) {
    const price = priceMap[item.code]
    if (!price || price <= 0) continue
    const [low, high] = item.buy_point.buy_range
    // 当前价跌入买入区间 → 通知
    if (price <= high && price >= low * 0.98) {
      alertedCodes.value.add(item.code)
      // 浏览器桌面通知
      if (Notification.permission === 'granted') {
        new Notification(`${item.name}(${item.code}) 进入买入区间`, {
          body: `现价 ${price} | 建议区间 ${low}-${high} | 评分 ${item.total_score}`,
        })
      }
    }
  }
}

// ── 自动刷新定时器 ──
function startAutoRefresh() {
  stopAutoRefresh()
  autoCountdown.value = 60
  refreshTimer = setInterval(() => {
    isTradingNow.value = isTradingTime()
    if (!isTradingNow.value) {
      autoCountdown.value = 0
      return
    }
    autoCountdown.value--
    if (autoCountdown.value <= 0) {
      // ★ 观察池（2026-09-22）：只刷**实时涨跌幅** —— 闸门/主力阶段是日频，
      //   且 gate-watch 全池重算太慢（>30s）不能做轮询 ⇒ 只发一次轻量的 batch-prices。
      //   （修正：此前观察池落到下面的 `else loadData()`，刷的是 Top50 榜单，观察池纹丝不动。）
      if (activeTab.value === 'watch') loadWatchPrices()
      // 影子榜 tab 也盘中刷新：本地模式走 loadData（同源），否则走后端接口
      else if (activeTab.value === 'shadow' && !shadowLocal()) loadShadowRank()
      else loadData()
      checkPriceAlerts()
      autoCountdown.value = 60
    }
  }, 1000)
}

function stopAutoRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null }
}

onMounted(() => {
  loadWatchCodes()
  startMarketAlerts()
  // 初始化前端评分系统（后台进行，不阻塞主流程）
  initFrontendScoring().then(result => {
    frontendInitialized.value = true
    if (result.needsDownload) {
      console.log('前端评分系统需要下载数据')
    } else {
      console.log('前端评分系统就绪，股票数:', frontendStockCount.value)
      // 如果用户之前选择了前端模式且数据已就绪，自动使用本地计算
      if (useFrontendMode.value && frontendDbReady.value && frontendStockCount.value > 0) {
        console.log('恢复前端计算模式')
        loadData()
      }
    }
  }).catch(e => {
    console.warn('前端评分系统初始化失败:', e)
  })

  // 如果用户之前选了前端模式，等 init 完成后再加载（上面的 .then 会处理）
  // 否则立即走后端加载
  if (!useFrontendMode.value) {
    loadData()
  }
  loadTemp()
  loadSnapshots()
  autoSaveTimer = setInterval(autoSaveCheck, 60000)
  startAutoRefresh()
})

onBeforeUnmount(() => {
  stopMarketAlerts()
  if (autoSaveTimer) clearInterval(autoSaveTimer)
  stopAutoRefresh()
})
</script>