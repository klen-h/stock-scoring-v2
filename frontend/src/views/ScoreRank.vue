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
        {{ klineCacheRefreshing ? '刷新中...' : '刷新缓存' }}
      </button>
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
            <span :class="alert.profit_pct >= 0 ? 'text-emerald-400' : 'text-red-400'">
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
    <div v-if="stats.total > 0 && activeTab !== 'verify' && activeTab !== 'backtest' && activeTab !== 'sector' && activeTab !== 'optimize' && activeTab !== 'anomaly'" class="bg-card border border-border rounded-lg p-4">
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

    <!-- 数据表格 -->
    <div v-if="activeTab !== 'verify' && activeTab !== 'backtest' && activeTab !== 'sector' && activeTab !== 'optimize' && activeTab !== 'anomaly'" class="bg-card border border-border rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">排名</th>
            <th class="text-left py-2.5 px-3">代码</th>
            <th class="text-left py-2.5 px-3">名称</th>
            <th class="text-right py-2.5 px-3">涨跌幅</th>
            <th class="text-right py-2.5 px-3">综合评分</th>
            <th class="text-center py-2.5 px-3">信号</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">连续</th>
            <th v-if="activeTab === 'top'" class="text-center py-2.5 px-3">可信度</th>
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
            <td class="py-2 px-3 text-right font-mono text-xs"
              :class="(item.change_pct || 0) > 0 ? 'text-red-400' : (item.change_pct || 0) < 0 ? 'text-emerald-400' : 'text-muted'">
              {{ (item.change_pct || 0) > 0 ? '+' : '' }}{{ (item.change_pct || 0).toFixed(2) }}%
            </td>
            <td class="py-2 px-3 text-right">
              <span class="font-bold" :class="item.total_score >= 65 ? 'text-emerald-400' : item.total_score >= 45 ? 'text-amber-400' : 'text-red-400'">
                {{ item.total_score }}
              </span>
            </td>
            <td class="py-2 px-3 text-center">
              <span class="px-2 py-0.5 rounded-full text-xs"
                :class="item.signal.includes('买入') ? 'bg-emerald-500/20 text-emerald-400' :
                       item.signal.includes('卖出') ? 'bg-red-500/20 text-red-400' :
                       'bg-amber-500/20 text-amber-400'">
                {{ item.signal }}
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
            <td v-if="activeTab === 'top'" class="py-2 px-3 text-center" @click.stop>
              <button v-if="!portfolioCodes?.has(item.code)"
                @click="quickAddPosition(item)"
                class="px-2 py-0.5 rounded text-[11px] bg-accent/15 text-accent hover:bg-accent/25 transition-colors"
                title="以当前价添加到持仓">
                + 持仓
              </button>
              <span v-else class="text-[11px] text-muted">已持有</span>
            </td>
          </tr>
          <tr v-if="!tableData.length">
            <td :colspan="activeTab === 'top' ? 11 : 5" class="py-12 text-center text-muted">
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
          <div class="p-2 bg-emerald-500/10 rounded-lg">
            <div class="text-emerald-400 text-xs">推荐盈利占比</div>
            <div class="text-lg font-bold text-emerald-400 mt-1">{{ verifySummary.winRate }}%</div>
          </div>
          <div class="p-2" :class="verifySummary.avgReturn >= 0 ? 'bg-emerald-500/10' : 'bg-red-500/10'">
            <div class="text-xs" :class="verifySummary.avgReturn >= 0 ? 'text-emerald-400' : 'text-red-400'">平均收益</div>
            <div class="text-lg font-bold mt-1" :class="verifySummary.avgReturn >= 0 ? 'text-emerald-400' : 'text-red-400'">
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
            <span v-if="snap.verified" class="text-xs" :class="snap.winRate >= 50 ? 'text-emerald-400' : 'text-red-400'">
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
                <td class="py-1.5 px-3 text-right" :class="(s.returnPct || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'">
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
            <div class="text-lg font-bold" :class="btResult.summary[p]?.avg_return >= 0 ? 'text-emerald-400' : 'text-red-400'">
              {{ btResult.summary[p]?.avg_return >= 0 ? '+' : '' }}{{ btResult.summary[p]?.avg_return }}%
            </div>
            <div class="text-xs mt-1">
              <span class="text-muted">胜率</span>
              <span class="font-bold" :class="btResult.summary[p]?.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'">
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
            <h3 class="text-sm font-semibold mb-2">各信号等级历史胜率</h3>
            <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
              <div v-for="(stats, sig) in optResult.signal_analysis" :key="sig"
                class="p-2 rounded-lg bg-bg text-center">
                <div class="text-xs text-muted">{{ sig }}</div>
                <div class="text-lg font-bold mt-1" :class="stats.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'">
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
import { ref, reactive, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { getScoreTop, getScoreBottom, getScoreBySignal, getMarketTemperature, getBatchPrices, getBacktest, getSectorIndustry, getIndustryFlow, getWeightAdvice, getAnomalies, getRankingPersistence, checkExitAlerts, getKlineCacheStatus, refreshKlineCache, getSnapshots, captureScoreSnapshot } from '../api'
import { getXueqiuUrl } from '../composables/stockUtils'
import { addPosition, usePortfolio, isTradingTime, getRefreshInterval } from '../composables/usePortfolio'
import { useFrontendScoring, runLocalBacktest } from '../composables/useFrontendScoring'

const router = useRouter()

// bottom / signal 入口暂隐藏（后续优化完再开放）
const tabs = [
  { key: 'top', label: '评分 Top 50' },
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
const cacheStatus = ref('loading')
const stats = reactive({ total: 0, buyCount: 0, watchCount: 0, sellCount: 0 })
const temp = ref({})   // 市场环境温度（独立信号）

// ── 自动刷新（盘中每60秒，非交易时段不自动刷新）──
const autoCountdown = ref(60)
const isTradingNow = ref(isTradingTime())
let refreshTimer = null

// ── 持仓联动 ──
const { positions } = usePortfolio()
const portfolioCodes = computed(() => new Set(positions.value.map(p => p.code)))

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
  saveFrontendModePreference,
} = useFrontendScoring()
const frontendInitialized = ref(false)

// ── K线缓存状态 ──
const klineCacheStatus = ref(null)
const klineCacheRefreshing = ref(false)

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
    await refreshKlineCache()
    // 等待几秒后刷新状态
    setTimeout(async () => {
      await loadKlineCacheStatus()
      klineCacheRefreshing.value = false
    }, 3000)
  } catch (e) {
    console.error('刷新K线缓存失败', e)
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
  
  const mode = activeTab.value === 'top' ? 'top' : 
               activeTab.value === 'bottom' ? 'bottom' : 'signal'
  
  const results = await computeRanking({
    mode,
    limit: 50,
    signal: signalType.value,
  })
  
  if (results && results.length > 0) {
    tableData.value = results
    cacheStatus.value = 'ready'
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

function switchTab(tab) {
  activeTab.value = tab
  if (tab === 'sector') loadSectorData()
  else if (tab === 'optimize') runWeightAnalysis()
  else if (tab === 'anomaly') loadAnomalies()
  else if (tab !== 'verify' && tab !== 'backtest') loadData()
}

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
      loadData()
      checkPriceAlerts()
      autoCountdown.value = 60
    }
  }, 1000)
}

function stopAutoRefresh() {
  if (refreshTimer) { clearInterval(refreshTimer); refreshTimer = null }
}

onMounted(async () => {
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
  if (autoSaveTimer) clearInterval(autoSaveTimer)
  stopAutoRefresh()
})
</script>