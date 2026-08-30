<template>
  <div class="fade-in space-y-4">
    <!-- 顶部标题栏 -->
    <div class="bg-card border border-border rounded-lg p-4">
      <div class="flex items-center justify-between flex-wrap gap-3">
        <div class="flex items-center gap-3">
          <h2 class="text-lg font-bold">战法选股</h2>
          <span class="text-xs text-muted">{{ strategies.length }} 个战法</span>
          <span v-if="scanResults.length" class="px-2 py-0.5 rounded-full text-xs bg-emerald-500/20 text-emerald-400">
            {{ scanResults.length }} 只信号
          </span>
        </div>
        <!-- 市场状态指示器 -->
        <div v-if="marketRegime" class="flex items-center gap-2 text-xs">
          <span class="text-muted">市场状态:</span>
          <span :class="regimeColor" class="font-medium">{{ regimeText }}</span>
          <span class="text-muted">置信度 {{ marketRegime.confidence }}%</span>
          <span class="text-muted">|</span>
          <span class="text-muted">ADX {{ marketRegime.adx }}</span>
          <button @click="loadMarketRegime" class="px-2 py-0.5 rounded bg-white/5 text-muted hover:text-gray-200 transition-colors">
            刷新
          </button>
        </div>
      </div>
      <!-- 战法适用性提示 -->
      <div v-if="strategyRecommendation && strategyRecommendation.suitability" class="mt-3 flex items-center gap-2 text-xs">
        <span class="text-muted">当前战法:</span>
        <span v-if="strategyRecommendation.suitability === 'high'" class="text-emerald-400">
          ✓ 适合当前市场
        </span>
        <span v-else-if="strategyRecommendation.suitability === 'medium'" class="text-amber-400">
          △ 可谨慎使用
        </span>
        <span v-else-if="strategyRecommendation.suitability === 'low'" class="text-red-400">
          ✗ 不太适用
        </span>
        <span class="text-muted">{{ strategyRecommendation.advice }}</span>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-4 gap-4">
      <!-- 左侧：战法列表 + 操作按钮 -->
      <div class="lg:col-span-1 space-y-3">
        <div class="bg-card border border-border rounded-lg p-3">
          <h3 class="text-sm font-medium mb-3 text-muted">选择战法</h3>
          <div class="space-y-2 max-h-[40vh] overflow-y-auto pr-1">
            <div v-for="s in strategies" :key="s.name_en"
              @click="selectStrategy(s)"
              :class="[
                'p-2.5 rounded-lg cursor-pointer transition-all relative',
                currentStrategy?.name_en === s.name_en
                  ? 'bg-accent/15 border border-accent/30'
                  : 'bg-white/5 hover:bg-white/10 border border-transparent'
              ]">
              <!-- 推荐标记 -->
              <span v-if="isRecommended(s.name_en)" class="absolute top-1 right-1 px-1 py-0.5 rounded text-[10px] bg-emerald-500/20 text-emerald-400">
                推荐
              </span>
              <div class="font-medium text-sm">{{ s.name }}</div>
              <div class="text-xs text-muted mt-0.5 line-clamp-1">{{ s.description }}</div>
            </div>
          </div>
        </div>

        <!-- 筛选条件 + 扫描按钮（固定在左侧） -->
        <div v-if="currentStrategy" class="bg-card border border-border rounded-lg p-3 space-y-3">
          <div>
            <label class="text-xs text-muted">置信度</label>
            <select v-model="filterConfidence" class="mt-1 w-full px-2 py-1.5 rounded bg-white/5 border border-border text-sm">
              <option value="">全部</option>
              <option value="high">高置信（≥6分）</option>
              <option value="medium">中置信（4-5分）</option>
              <option value="low">低置信（<4分）</option>
            </select>
          </div>
          <div>
            <label class="text-xs text-muted">最小市值（亿）</label>
            <input v-model.number="filterMinCap" type="number" min="0" step="10"
              class="mt-1 w-full px-2 py-1.5 rounded bg-white/5 border border-border text-sm">
          </div>
          <div class="flex items-center gap-2 pt-1">
            <span v-if="scanning" class="text-xs text-amber-400 flex-1">
              <span class="animate-pulse">●</span> 扫描中...
            </span>
            <span v-else-if="lastScanDate" class="text-xs text-muted flex-1">
              扫描于 {{ lastScanDate }}
            </span>
            <span v-else class="flex-1"></span>
          </div>
          <button @click="triggerScan" :disabled="scanning || !currentStrategy"
            class="w-full px-3 py-2 rounded text-sm bg-accent/15 text-accent hover:bg-accent/25 transition-colors disabled:opacity-50 font-medium">
            {{ scanning ? '扫描中...' : '执行扫描' }}
          </button>
          <button @click="exportCSV" :disabled="!scanResults.length"
            class="w-full px-3 py-1.5 rounded text-xs bg-white/5 text-muted hover:text-gray-200 transition-colors disabled:opacity-50">
            导出CSV
          </button>
          
          <!-- 回测按钮 -->
          <div class="border-t border-border pt-3 mt-3">
            <button @click="triggerBacktest" :disabled="backtesting || !currentStrategy"
              class="w-full px-3 py-2 rounded text-sm bg-purple-500/15 text-purple-400 hover:bg-purple-500/25 transition-colors disabled:opacity-50 font-medium">
              {{ backtesting ? '回测中...' : '历史回测' }}
            </button>
            <div v-if="backtestResult" class="mt-3 p-2 rounded bg-purple-500/10 border border-purple-500/20 text-xs space-y-1">
              <div class="flex justify-between">
                <span class="text-muted">胜率</span>
                <span :class="backtestResult.win_rate >= 50 ? 'text-rise' : 'text-fall'">
                  {{ backtestResult.win_rate }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">信号数</span>
                <span>{{ backtestResult.signals }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">盈亏比</span>
                <span>{{ backtestResult.profit_factor }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">平均收益</span>
                <span :class="backtestResult.avg_profit_pct >= 0 ? 'text-rise' : 'text-fall'">
                  {{ backtestResult.avg_profit_pct }}%
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：扫描结果 -->
      <div class="lg:col-span-3">
        <div v-if="!currentStrategy" class="bg-card border border-border rounded-lg p-8 text-center text-muted">
          <div class="text-4xl mb-3">📊</div>
          <p>请从左侧选择一个战法</p>
        </div>

        <div v-else-if="!scanResults.length && !scanning" class="bg-card border border-border rounded-lg p-8 text-center text-muted">
          <div class="text-4xl mb-3">🔍</div>
          <p v-if="scanMessage">{{ scanMessage }}</p>
          <template v-else>
            <p>暂无扫描结果</p>
            <p class="text-sm mt-2">点击"执行扫描"开始扫描股票池</p>
          </template>
        </div>

        <div v-else class="bg-card border border-border rounded-lg overflow-hidden">
          <!-- 结果统计 -->
          <div class="p-3 border-b border-border flex items-center justify-between">
            <div class="flex items-center gap-4 text-sm">
              <span class="text-muted">共 {{ filteredResults.length }} 只</span>
              <span class="text-emerald-400">高置信 {{ highCount }}</span>
              <span class="text-amber-400">中置信 {{ mediumCount }}</span>
              <span v-if="persistentSignals.length" class="text-blue-400">连续上榜 {{ persistentSignals.length }} 只</span>
            </div>
            <div class="flex items-center gap-2">
              <template v-if="activeTab === 'list' && filteredResults.length">
                <button @click="batchSyncWatchlist" class="px-2 py-1 rounded text-xs bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 transition-colors">
                  全部+自选
                </button>
                <button @click="batchSyncTradePlan" class="px-2 py-1 rounded text-xs bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 transition-colors">
                  全部+计划
                </button>
              </template>
              <button v-for="tab in ['list', 'watch']" :key="tab"
                @click="activeTab = tab"
                :class="[
                  'px-3 py-1 rounded text-xs transition-colors',
                  activeTab === tab ? 'bg-accent/15 text-accent' : 'text-muted hover:text-gray-200'
                ]">
                {{ tab === 'list' ? '信号列表' : `观察池(${watchPool.length})` }}
              </button>
            </div>
          </div>

          <!-- 信号列表 -->
          <div v-if="activeTab === 'list'" class="overflow-x-auto">
            <table class="w-full text-sm">
              <thead class="bg-white/5 text-xs text-muted">
                <tr>
                  <th class="px-3 py-2 text-left">股票</th>
                  <th class="px-3 py-2 text-center">置信度</th>
                  <th class="px-3 py-2 text-center">共振</th>
                  <th class="px-3 py-2 text-center">连续</th>
                  <th class="px-3 py-2 text-center">可信度</th>
                  <th class="px-3 py-2 text-right">介入价</th>
                  <th class="px-3 py-2 text-right">止损价</th>
                  <th class="px-3 py-2 text-right">目标价</th>
                  <th class="px-3 py-2 text-center">位置%</th>
                  <th class="px-3 py-2 text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="r in filteredResults" :key="r.code"
                  class="border-t border-border hover:bg-white/5 transition-colors">
                  <td class="px-3 py-2">
                    <div class="font-medium">{{ r.name }}</div>
                    <a :href="getXueqiuUrl(r.code)" target="_blank" rel="noopener"
                      @click.stop class="text-xs text-muted hover:text-accent hover:underline transition-colors"
                      title="在雪球查看">{{ r.code }}</a>
                  </td>
                  <td class="px-3 py-2 text-center">
                    <span :class="confidenceClass(r.confidence_level)">
                      {{ r.confidence }}分
                    </span>
                    <div class="text-xs text-muted">{{ confidenceText(r.confidence_level) }}</div>
                  </td>
                  <td class="px-3 py-2 text-center">
                    <span v-if="r.signal_grade" :class="gradeClass(r.signal_grade)" class="font-bold">
                      {{ r.signal_grade }}
                    </span>
                    <span v-else class="text-muted">-</span>
                    <div v-if="r.signal_score" class="text-xs text-muted">{{ r.signal_score }}分</div>
                  </td>
                  <td class="px-3 py-2 text-center">
                    <span v-if="r.consecutive_days" :class="consecutiveClass(r.consecutive_days)" class="font-medium">
                      {{ r.consecutive_days }}天
                    </span>
                    <span v-else class="text-muted">-</span>
                  </td>
                  <td class="px-3 py-2 text-center">
                    <span v-if="r.trust_grade" :class="trustGradeClass(r.trust_grade)" class="font-bold">
                      {{ r.trust_grade }}
                    </span>
                    <span v-else class="text-muted">-</span>
                    <div v-if="r.trust_score" class="text-xs text-muted">{{ r.trust_score }}分</div>
                  </td>
                  <td class="px-3 py-2 text-right font-mono">{{ r.entry_price }}</td>
                  <td class="px-3 py-2 text-right font-mono text-red-400">{{ r.stop_loss }}</td>
                  <td class="px-3 py-2 text-right font-mono text-emerald-400">{{ r.target_price }}</td>
                  <td class="px-3 py-2 text-center">
                    <span :class="r.position_pct <= 15 ? 'text-emerald-400' : r.position_pct <= 25 ? 'text-amber-400' : 'text-muted'">
                      {{ r.position_pct }}%
                    </span>
                  </td>
                  <td class="px-3 py-2 text-center">
                    <button @click="viewDetail(r)" class="text-xs text-accent hover:underline mr-1">详情</button>
                    <button @click="addToWatch(r)" :disabled="isInWatch(r.code)"
                      class="text-xs text-muted hover:text-gray-200 disabled:opacity-50 mr-1">
                      {{ isInWatch(r.code) ? '已观察' : '+观察' }}
                    </button>
                    <button @click="syncToWatchlist(r)"
                      class="text-xs text-blue-400 hover:text-blue-300 mr-1">
                      +自选
                    </button>
                    <button @click="syncToTradePlan(r)"
                      class="text-xs text-amber-400 hover:text-amber-300">
                      +计划
                    </button>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- 观察池 -->
          <div v-else>
            <!-- 撤退提醒区域 -->
            <div v-if="exitAlerts.length" class="p-3 border-b border-border">
              <div class="flex items-center justify-between mb-2">
                <span class="text-sm font-medium text-red-400">撤退提醒</span>
                <span class="text-xs text-muted">{{ exitAlerts.length }} 个提醒</span>
              </div>
              <div class="space-y-1.5">
                <div v-for="alert in exitAlerts" :key="alert.code"
                  :class="['p-2 rounded text-xs', alert.level === 'urgent' ? 'bg-red-500/10 border border-red-500/30' : alert.level === 'warning' ? 'bg-amber-500/10 border border-amber-500/30' : 'bg-white/5']">
                  <div class="flex items-center justify-between">
                    <span class="font-medium">{{ alert.name }} ({{ alert.code }})</span>
                    <span :class="alert.level === 'urgent' ? 'text-red-400 font-bold' : 'text-amber-400'">
                      {{ alert.level === 'urgent' ? '紧急' : alert.level === 'warning' ? '警告' : '观察' }}
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
            <table v-if="watchPool.length" class="w-full text-sm">
              <thead class="bg-white/5 text-xs text-muted">
                <tr>
                  <th class="px-3 py-2 text-left">股票</th>
                  <th class="px-3 py-2 text-right">介入价</th>
                  <th class="px-3 py-2 text-right">止损价</th>
                  <th class="px-3 py-2 text-right">目标价</th>
                  <th class="px-3 py-2 text-center">加入日期</th>
                  <th class="px-3 py-2 text-center">操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="w in watchPool" :key="w.code"
                  class="border-t border-border hover:bg-white/5 transition-colors">
                  <td class="px-3 py-2">
                    <div class="font-medium">{{ w.name }}</div>
                    <a :href="getXueqiuUrl(w.code)" target="_blank" rel="noopener"
                      @click.stop class="text-xs text-muted hover:text-accent hover:underline transition-colors"
                      title="在雪球查看">{{ w.code }}</a>
                  </td>
                  <td class="px-3 py-2 text-right font-mono">{{ w.entry_price }}</td>
                  <td class="px-3 py-2 text-right font-mono text-red-400">{{ w.stop_loss }}</td>
                  <td class="px-3 py-2 text-right font-mono text-emerald-400">{{ w.target_price }}</td>
                  <td class="px-3 py-2 text-center text-xs text-muted">{{ w.added_date || '-' }}</td>
                  <td class="px-3 py-2 text-center">
                    <button @click="removeFromWatch(w.code)" class="text-xs text-red-400 hover:underline mr-1">移除</button>
                    <button @click="syncToWatchlist(w)" class="text-xs text-blue-400 hover:text-blue-300 mr-1">+自选</button>
                    <button @click="syncToTradePlan(w)" class="text-xs text-amber-400 hover:text-amber-300">+计划</button>
                  </td>
                </tr>
              </tbody>
            </table>
            <div v-else class="p-8 text-center text-muted">
              <p>观察池为空</p>
              <p class="text-xs mt-2">从信号列表点击“+观察”添加</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- 详情弹窗 -->
    <div v-if="showDetail" class="fixed inset-0 bg-black/50 flex items-center justify-center z-50" @click.self="showDetail = false">
      <div class="bg-card border border-border rounded-lg w-full max-w-3xl max-h-[90vh] overflow-y-auto">
        <div class="p-4 border-b border-border flex items-center justify-between">
          <h3 class="font-medium">{{ detailStock?.name }} ({{ detailStock?.code }}) 详情</h3>
          <button @click="showDetail = false" class="text-muted hover:text-gray-200">×</button>
        </div>
        <div class="p-4 space-y-4">
          <!-- K线图区域 -->
          <div class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">最近5日K线</div>
            <div class="grid grid-cols-5 gap-2 text-xs">
              <div v-for="k in detailKlines" :key="k.date"
                :class="['p-2 rounded', k.is_positive ? 'bg-red-500/10' : 'bg-emerald-500/10']">
                <div class="text-muted">{{ k.date?.slice(5) }}</div>
                <div :class="k.change_pct >= 0 ? 'text-red-400' : 'text-emerald-400'">
                  {{ k.change_pct >= 0 ? '+' : '' }}{{ k.change_pct }}%
                </div>
                <div class="text-muted mt-1">
                  <div>开 {{ k.open?.toFixed(2) }}</div>
                  <div>收 {{ k.close?.toFixed(2) }}</div>
                </div>
              </div>
            </div>
          </div>

          <!-- 关键价位 -->
          <div class="grid grid-cols-3 gap-4">
            <div class="bg-white/5 rounded-lg p-3">
              <div class="text-xs text-muted">介入价</div>
              <div class="text-lg font-mono text-accent">{{ detailStock?.entry_price }}</div>
            </div>
            <div class="bg-white/5 rounded-lg p-3">
              <div class="text-xs text-muted">止损价</div>
              <div class="text-lg font-mono text-red-400">{{ detailStock?.stop_loss }}</div>
            </div>
            <div class="bg-white/5 rounded-lg p-3">
              <div class="text-xs text-muted">目标价</div>
              <div class="text-lg font-mono text-emerald-400">{{ detailStock?.target_price }}</div>
            </div>
          </div>

          <!-- 形态详情：进二退一 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'advance2retreat1'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（进二退一）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between">
                <span>Day1 涨幅</span>
                <span :class="detailStock.details.day1.change_pct >= 7 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.day1.change_pct }}% (量比 {{ detailStock.details.day1.volume_ratio }})
                </span>
              </div>
              <div class="flex justify-between">
                <span>Day2 涨幅</span>
                <span class="text-muted">
                  {{ detailStock.details.day2.change_pct }}% (上影比 {{ detailStock.details.day2.upper_shadow_ratio?.toFixed(2) }})
                </span>
              </div>
              <div class="flex justify-between">
                <span>Day3 缩量</span>
                <span :class="detailStock.details.day3.volume_ratio <= 0.5 ? 'text-emerald-400' : 'text-muted'">
                  量比 {{ detailStock.details.day3.volume_ratio }} (回调 {{ detailStock.details.day3.pullback_pct }}%)
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：仙人指路 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'wizard_pointer'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（仙人指路）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between">
                <span>前一日（放量大阳）</span>
                <span :class="detailStock.details.prev?.change_pct >= 5 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.prev?.change_pct }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>仙人指路日 涨幅</span>
                <span class="text-muted">
                  {{ detailStock.details.wizard?.change_pct }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>振幅</span>
                <span :class="detailStock.details.wizard?.amplitude >= 7 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.wizard?.amplitude?.toFixed(1) }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>上影线占比</span>
                <span class="text-muted">
                  {{ (detailStock.details.wizard?.upper_shadow_ratio * 100)?.toFixed(1) }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>量比</span>
                <span :class="detailStock.details.wizard?.volume_ratio >= 1 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.wizard?.volume_ratio?.toFixed(2) }}
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between">
                <span>次日确认</span>
                <span :class="detailStock.details.confirm?.is_high_open ? 'text-emerald-400' : 'text-amber-400'">
                  {{ detailStock.details.confirm?.is_high_open ? '高开高走 ✓' : '低开高走' }}
                </span>
              </div>
              <div class="flex justify-between">
                <span>次日涨幅</span>
                <span :class="detailStock.details.confirm?.change_pct >= 1 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.confirm?.change_pct }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>强势股</span>
                <span class="text-emerald-400">
                  近20日涨幅 {{ detailStock.details.recent_gain }}%
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：龙回头 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'dragon_turnaround'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（龙回头）</div>
            <div class="space-y-2 text-xs">
              <!-- 第一波上涨 -->
              <div class="font-medium text-muted">📈 第一波上涨</div>
              <div class="flex justify-between">
                <span class="text-muted">区间</span>
                <span>{{ detailStock.details.wave1?.start_date?.slice(5) }} ~ {{ detailStock.details.wave1?.end_date?.slice(5) }}（{{ detailStock.details.wave1?.days }}天）</span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">涨幅</span>
                <span :class="detailStock.details.wave1?.gain >= 20 ? 'text-emerald-400' : 'text-amber-400'">
                  {{ detailStock.details.wave1?.gain }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">涨停次数</span>
                <span class="text-emerald-400">{{ detailStock.details.wave1?.limit_up_count }}次</span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">量比</span>
                <span>{{ detailStock.details.wave1?.volume_ratio }}倍</span>
              </div>
              <div class="border-t border-border my-1"></div>
              <!-- 回调段 -->
              <div class="font-medium text-muted">📉 回调段</div>
              <div class="flex justify-between">
                <span class="text-muted">回调天数</span>
                <span>{{ detailStock.details.pullback?.days }}天</span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">回调深度</span>
                <span :class="detailStock.details.pullback?.depth_pct <= 10 ? 'text-emerald-400' : 'text-amber-400'">
                  {{ detailStock.details.pullback?.depth_pct }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">缩量程度</span>
                <span :class="detailStock.details.pullback?.volume_ratio <= 0.5 ? 'text-emerald-400' : 'text-muted'">
                  {{ (detailStock.details.pullback?.volume_ratio * 100)?.toFixed(0) }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">黄金窗口</span>
                <span :class="detailStock.details.pullback?.is_golden_window ? 'text-emerald-400' : 'text-amber-400'">
                  {{ detailStock.details.pullback?.is_golden_window ? '是 ✓' : '否' }}
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <!-- 反转确认 -->
              <div class="font-medium text-muted">🚀 反转确认</div>
              <div class="flex justify-between">
                <span class="text-muted">日期</span>
                <span>{{ detailStock.details.reversal?.date?.slice(5) }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">涨幅</span>
                <span :class="detailStock.details.reversal?.change_pct >= 5 ? 'text-emerald-400' : 'text-amber-400'">
                  {{ detailStock.details.reversal?.change_pct }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">量比</span>
                <span :class="detailStock.details.reversal?.volume_ratio >= 1.5 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.reversal?.volume_ratio }}倍
                </span>
              </div>
              <div class="flex justify-between">
                <span class="text-muted">吞没形态</span>
                <span :class="detailStock.details.reversal?.is_engulfing ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.reversal?.is_engulfing ? '是 ✓' : '否' }}
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：涨停回马枪 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'limit_up_boomerang'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（涨停回马枪）</div>
            <div class="space-y-2 text-xs">
              <div v-for="(val, key) in detailStock.details" :key="key" class="flex justify-between">
                <span class="text-muted">{{ key }}</span>
                <span>{{ typeof val === 'number' ? val.toFixed(2) : val }}</span>
              </div>
            </div>
          </div>

          <!-- 形态详情：涨停双响炮 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'double_cannon'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（涨停双响炮）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between font-medium text-muted mb-1">
                <span>🔫 第一炮</span>
                <span>{{ detailStock.details.first_cannon?.date?.slice(5) }}</span>
              </div>
              <div class="flex justify-between">
                <span>涨停涨幅</span>
                <span :class="detailStock.details.first_cannon?.first_change >= 9.5 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.first_cannon?.first_change }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>量比</span>
                <span class="text-muted">{{ detailStock.details.first_cannon?.volume_ratio }}倍</span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between font-medium text-muted mb-1">
                <span>📉 中间段</span>
                <span>{{ detailStock.details.pullback?.days }}根K线</span>
              </div>
              <div class="flex justify-between">
                <span>缩量程度</span>
                <span :class="detailStock.details.pullback?.volume_ratio <= 0.3 ? 'text-emerald-400' : 'text-muted'">
                  首板的{{ (detailStock.details.pullback?.volume_ratio * 100)?.toFixed(0) }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>回调深度</span>
                <span class="text-muted">{{ detailStock.details.pullback?.depth_pct }}%</span>
              </div>
              <div class="flex justify-between">
                <span>支撑强度</span>
                <span :class="detailStock.details.pullback?.support_level === 'strong' ? 'text-emerald-400' : 'text-amber-400'">
                  {{ { strong: '不破收盘价', medium: '不破开盘价', weak: '跌破开盘价' }[detailStock.details.pullback?.support_level] || '-' }}
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between font-medium text-muted mb-1">
                <span>🔫 第二炮</span>
                <span>{{ detailStock.details.second_cannon?.date?.slice(5) }}</span>
              </div>
              <div class="flex justify-between">
                <span>涨停涨幅</span>
                <span class="text-emerald-400">{{ detailStock.details.second_cannon?.second_change }}%</span>
              </div>
              <div class="flex justify-between">
                <span>量比（vs中间段）</span>
                <span :class="detailStock.details.second_cannon?.volume_ratio >= 2 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.second_cannon?.volume_ratio }}倍
                </span>
              </div>
              <div class="flex justify-between">
                <span>突破中间段高点</span>
                <span :class="detailStock.details.second_cannon?.is_breakout ? 'text-emerald-400' : 'text-amber-400'">
                  {{ detailStock.details.second_cannon?.is_breakout ? '是 ✓' : '否' }}
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：单阳不破 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'single_yang_unbroken'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（单阳不破）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between">
                <span>大阳线涨幅</span>
                <span :class="detailStock.details.single_yang?.change_pct >= 7 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.single_yang?.change_pct }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>强度类型</span>
                <span class="text-muted">{{ detailStock.details.strength_type }}</span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between">
                <span>整理天数</span>
                <span :class="detailStock.details.consolidation?.is_golden_window ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.consolidation?.days }}天
                </span>
              </div>
              <div class="flex justify-between">
                <span>缩量程度</span>
                <span class="text-muted">{{ (detailStock.details.consolidation?.volume_ratio * 100)?.toFixed(0) }}%</span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between">
                <span>突破涨幅</span>
                <span class="text-emerald-400">{{ detailStock.details.breakout?.change_pct }}%</span>
              </div>
              <div class="flex justify-between">
                <span>突破量比</span>
                <span :class="detailStock.details.breakout?.volume_ratio >= 2 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.breakout?.volume_ratio }}倍
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：均线回踩 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'ma_pullback'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（均线回踩）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between">
                <span>回踩模式</span>
                <span class="text-accent">{{ detailStock.details.mode }}</span>
              </div>
              <div class="flex justify-between">
                <span>均线周期</span>
                <span class="text-muted">{{ detailStock.details.ma_period }}日</span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between">
                <span>均线值</span>
                <span class="text-muted">{{ detailStock.details.pullback?.ma_value }}</span>
              </div>
              <div class="flex justify-between">
                <span>回踩天数</span>
                <span class="text-muted">{{ detailStock.details.pullback?.days }}天</span>
              </div>
              <div class="flex justify-between">
                <span>缩量程度</span>
                <span :class="detailStock.details.pullback?.volume_ratio <= 0.5 ? 'text-emerald-400' : 'text-muted'">
                  {{ (detailStock.details.pullback?.volume_ratio * 100)?.toFixed(0) }}%
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between">
                <span>反弹涨幅</span>
                <span class="text-emerald-400">{{ detailStock.details.bounce?.change_pct }}%</span>
              </div>
              <div class="flex justify-between">
                <span>反弹量比</span>
                <span :class="detailStock.details.bounce?.volume_ratio >= 1.5 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.bounce?.volume_ratio }}倍
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：老鸭头 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'old_duck_head'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（老鸭头）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between font-medium text-muted">
                <span>🦆 鸭颈</span>
                <span>涨{{ detailStock.details.neck?.rise_pct }}%</span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between font-medium text-muted">
                <span>🦆 鸭头（洗盘）</span>
                <span>{{ detailStock.details.head?.days }}天</span>
              </div>
              <div class="flex justify-between">
                <span>回调幅度</span>
                <span :class="detailStock.details.head?.is_best_window ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.head?.pullback_pct }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>缩量程度</span>
                <span class="text-muted">{{ (detailStock.details.head?.volume_ratio * 100)?.toFixed(0) }}%</span>
              </div>
              <div class="flex justify-between">
                <span>60日线</span>
                <span class="text-muted">{{ detailStock.details.head?.ma60_value }}</span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between font-medium text-muted">
                <span>🦆 鸭嘴（启动）</span>
                <span>{{ detailStock.details.mouth?.date?.slice(5) }}</span>
              </div>
              <div class="flex justify-between">
                <span>涨幅</span>
                <span class="text-emerald-400">{{ detailStock.details.mouth?.change_pct }}%</span>
              </div>
              <div class="flex justify-between">
                <span>放量倍数</span>
                <span :class="detailStock.details.mouth?.volume_ratio >= 2 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.mouth?.volume_ratio }}倍
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：均线粘合突破 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'ma_convergence_breakout'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（均线粘合突破）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between">
                <span>横盘天数</span>
                <span :class="detailStock.details.consolidation?.days >= 30 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.consolidation?.days }}天
                </span>
              </div>
              <div class="flex justify-between">
                <span>横盘振幅</span>
                <span class="text-muted">{{ detailStock.details.consolidation?.amplitude }}%</span>
              </div>
              <div class="flex justify-between">
                <span>量能衰减</span>
                <span :class="detailStock.details.consolidation?.volume_decay <= 0.3 ? 'text-emerald-400' : 'text-muted'">
                  {{ (detailStock.details.consolidation?.volume_decay * 100)?.toFixed(0) }}%
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between">
                <span>突破涨幅</span>
                <span class="text-emerald-400">{{ detailStock.details.breakout?.change_pct }}%</span>
              </div>
              <div class="flex justify-between">
                <span>突破量比</span>
                <span :class="detailStock.details.breakout?.volume_ratio >= 2 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.breakout?.volume_ratio }}倍
                </span>
              </div>
            </div>
          </div>

          <!-- 形态详情：早晨之星 -->
          <div v-if="detailStock?.details && currentStrategy?.name_en === 'morning_star'" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析（早晨之星）</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between font-medium text-muted">
                <span>⭐ 第一根（长阴）</span>
                <span>{{ detailStock.details.day1?.date?.slice(5) }}</span>
              </div>
              <div class="flex justify-between">
                <span>跌幅</span>
                <span :class="detailStock.details.day1?.change_pct <= -5 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.day1?.change_pct }}%
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between font-medium text-muted">
                <span>⭐ 第二根（星线）</span>
                <span>{{ detailStock.details.day2?.date?.slice(5) }}</span>
              </div>
              <div class="flex justify-between">
                <span>振幅</span>
                <span :class="detailStock.details.day2?.amplitude <= 1 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.day2?.amplitude }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>跳空</span>
                <span :class="detailStock.details.day2?.is_gap_down ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.day2?.is_gap_down ? '是' : '否' }}
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div class="flex justify-between font-medium text-muted">
                <span>⭐ 第三根（确认）</span>
                <span>{{ detailStock.details.day3?.date?.slice(5) }}</span>
              </div>
              <div class="flex justify-between">
                <span>涨幅</span>
                <span class="text-emerald-400">{{ detailStock.details.day3?.change_pct }}%</span>
              </div>
              <div class="flex justify-between">
                <span>覆盖阴线</span>
                <span :class="detailStock.details.day3?.coverage >= 0.8 ? 'text-emerald-400' : 'text-muted'">
                  {{ (detailStock.details.day3?.coverage * 100)?.toFixed(0) }}%
                </span>
              </div>
              <div class="flex justify-between">
                <span>放量倍数</span>
                <span :class="detailStock.details.day3?.volume_ratio >= 1.5 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.details.day3?.volume_ratio }}倍
                </span>
              </div>
            </div>
          </div>

          <!-- 通用形态详情（未适配的战法） -->
          <div v-if="detailStock?.details && !isKnownStrategy" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">形态分析</div>
            <div class="space-y-2 text-xs">
              <div v-for="(val, key) in detailStock.details" :key="key" class="flex justify-between">
                <span class="text-muted">{{ key }}</span>
                <span>{{ typeof val === 'number' ? val.toFixed(2) : (typeof val === 'object' ? JSON.stringify(val) : val) }}</span>
              </div>
            </div>
          </div>

          <!-- 信号共振验证 -->
          <div v-if="detailStock?.confirmation" class="bg-white/5 rounded-lg p-3">
            <div class="flex items-center justify-between mb-2">
              <span class="text-sm text-muted">信号共振验证</span>
              <span :class="gradeClass(detailStock.signal_grade)" class="text-lg font-bold">
                {{ detailStock.signal_grade }}级
                <span class="text-xs font-normal text-muted">({{ detailStock.signal_score }}分)</span>
              </span>
            </div>
            <div class="space-y-1.5 text-xs">
              <div class="flex justify-between items-center">
                <span class="text-muted">RSI 共振</span>
                <span :class="detailStock.confirmation.rsi?.score >= 20 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.confirmation.rsi?.details }}
                </span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-muted">支撑共振</span>
                <span :class="detailStock.confirmation.support?.score >= 20 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.confirmation.support?.details }}
                </span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-muted">量能共振</span>
                <span :class="detailStock.confirmation.volume?.score >= 20 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.confirmation.volume?.details }}
                </span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-muted">趋势共振</span>
                <span :class="detailStock.confirmation.trend?.score >= 20 ? 'text-emerald-400' : 'text-muted'">
                  {{ detailStock.confirmation.trend?.details }}
                </span>
              </div>
            </div>
            <div class="mt-2 pt-2 border-t border-border text-xs text-muted">
              {{ detailStock.confirmation.verdict }}
            </div>
          </div>

          <!-- 信号可信度（连续上榜） -->
          <div v-if="detailStock?.trust_grade" class="bg-white/5 rounded-lg p-3">
            <div class="flex items-center justify-between mb-2">
              <span class="text-sm text-muted">信号可信度</span>
              <span :class="trustGradeClass(detailStock.trust_grade)" class="text-lg font-bold">
                {{ detailStock.trust_grade }}级
                <span class="text-xs font-normal text-muted">({{ detailStock.trust_score }}分)</span>
              </span>
            </div>
            <div class="space-y-1.5 text-xs">
              <div class="flex justify-between items-center">
                <span class="text-muted">连续上榜</span>
                <span :class="consecutiveClass(detailStock.consecutive_days)" class="font-medium">
                  {{ detailStock.consecutive_days || 1 }} 天
                  {{ detailStock.consecutive_days >= 5 ? '— 极强共识' : detailStock.consecutive_days >= 3 ? '— 持续强势' : detailStock.consecutive_days >= 2 ? '— 初步确认' : '— 观察期' }}
                </span>
              </div>
              <div class="flex justify-between items-center">
                <span class="text-muted">建议</span>
                <span :class="detailStock.trust_grade === 'A+' || detailStock.trust_grade === 'A' ? 'text-emerald-400' : detailStock.trust_grade === 'B' ? 'text-amber-400' : 'text-muted'">
                  {{ detailStock.trust_grade === 'A+' ? '放心买，极强共识' : detailStock.trust_grade === 'A' ? '可重仓，持续强势' : detailStock.trust_grade === 'B' ? '可轻仓，初步确认' : detailStock.trust_grade === 'C' ? '建议观望，观察期' : '不建议操作' }}
                </span>
              </div>
            </div>
          </div>

          <!-- 支撑阻力位 -->
          <div v-if="supportResistance" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">支撑阻力分析</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between items-center">
                <span>当前位置</span>
                <span :class="supportResistance.position_pct >= 75 ? 'text-amber-400' : supportResistance.position_pct <= 25 ? 'text-emerald-400' : 'text-muted'">
                  {{ supportResistance.position_pct }}% ({{ supportResistance.suggestion }})
                </span>
              </div>
              <div class="border-t border-border my-1"></div>
              <div v-for="level in supportResistance.levels" :key="level.price" class="flex justify-between items-center">
                <span class="text-muted">{{ level.type === 'resistance' ? '阻力' : level.type === 'support' ? '支撑' : '中性' }}</span>
                <span class="font-mono">{{ level.price }}</span>
                <span :class="level.strength === 'strong' ? 'text-emerald-400' : level.strength === 'medium' ? 'text-amber-400' : 'text-muted'">
                  {{ level.strength === 'strong' ? '强' : level.strength === 'medium' ? '中' : '弱' }} ({{ level.touches }}次)
                </span>
              </div>
            </div>
          </div>

          <!-- RSI 指标 -->
          <div v-if="rsiSignals" class="bg-white/5 rounded-lg p-3">
            <div class="text-sm text-muted mb-2">RSI 指标</div>
            <div class="space-y-2 text-xs">
              <div class="flex justify-between items-center">
                <span>当前 RSI</span>
                <span :class="rsiZoneClass">
                  {{ rsiSignals.current_rsi }} ({{ rsiZoneText }})
                </span>
              </div>
              <div v-if="rsiSignals.signal" class="flex justify-between items-center">
                <span>信号</span>
                <span :class="rsiSignals.signal.type === 'buy' ? 'text-emerald-400' : rsiSignals.signal.type === 'sell' ? 'text-red-400' : 'text-amber-400'">
                  {{ rsiSignals.signal.description }}
                </span>
              </div>
              <div class="flex justify-between items-center">
                <span>解读</span>
                <span class="text-muted text-right max-w-[60%]">{{ rsiSignals.interpretation }}</span>
              </div>
            </div>
          </div>

          <!-- 操作按钮 -->
          <div class="flex flex-wrap justify-end gap-2">
            <button @click="addToWatch(detailStock)" :disabled="isInWatch(detailStock.code)"
              class="px-3 py-2 rounded text-xs bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-50">
              {{ isInWatch(detailStock.code) ? '已在观察池' : '+观察池' }}
            </button>
            <button @click="syncToWatchlist(detailStock)"
              class="px-3 py-2 rounded text-xs bg-blue-500/15 text-blue-400 hover:bg-blue-500/25">
              +自选股
            </button>
            <button @click="syncToTradePlan(detailStock)"
              class="px-3 py-2 rounded text-xs bg-amber-500/15 text-amber-400 hover:bg-amber-500/25">
              +交易计划
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- Toast 提示 -->
  <transition name="fade">
    <div v-if="toastVisible" class="fixed bottom-6 left-1/2 -translate-x-1/2 px-4 py-2 rounded-lg bg-gray-800 text-sm text-white shadow-lg z-[60] border border-border">
      {{ toastMsg }}
    </div>
  </transition>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import {
  getStrategiesList,
  scanStrategy,
  getStrategyResult,
  getStrategyWatch,
  updateStrategyWatch,
  getScanStatus,
  runBacktest,
  getBacktestResult,
  getMarketRegime,
  getStrategyRecommendation,
  getStrategyDetail,
  getSupportResistance,
  getRSISignals,
  getPersistence,
  getPersistentSignals,
  checkExitAlerts,
} from '../api'
import { addWatch } from '../composables/useWatchlist'
import { addPlan } from '../composables/useTradePlans'
import { getXueqiuUrl } from '../composables/stockUtils'

// ── 状态 ──
const strategies = ref([])
const currentStrategy = ref(null)
const scanResults = ref([])
const watchPool = ref([])
const scanning = ref(false)
const lastScanDate = ref('')
const activeTab = ref('list')
const scanMessage = ref('')

// 回测状态
const backtesting = ref(false)
const backtestResult = ref(null)

// 市场状态
const marketRegime = ref(null)
const strategyRecommendation = ref(null)

// 信号持久度
const persistenceData = ref(null)
const persistentSignals = ref([])

// 撤退提醒
const exitAlerts = ref([])
const exitLoading = ref(false)

// 支撑阻力 + RSI
const supportResistance = ref(null)
const rsiSignals = ref(null)

// 筛选
const filterConfidence = ref('')
const filterMinCap = ref(20)

// 详情弹窗
const showDetail = ref(false)
const detailStock = ref(null)
const detailKlines = ref([])

// ── 加载战法列表 ──
async function loadStrategies() {
  try {
    const { data } = await getStrategiesList()
    strategies.value = data.data || []
    if (strategies.value.length && !currentStrategy.value) {
      // 从 sessionStorage 恢复上次选中的战法
      const savedName = sessionStorage.getItem('strategy_selected')
      const saved = strategies.value.find(s => s.name_en === savedName)
      selectStrategy(saved || strategies.value[0])
    }
  } catch (e) {
    console.error('加载战法列表失败', e)
  }
}

// ── 选择战法 ──
function selectStrategy(s) {
  currentStrategy.value = s
  sessionStorage.setItem('strategy_selected', s.name_en)
  loadResult()
  loadWatchPool()
  loadBacktestResult()
  loadRecommendation(s.name_en)
  loadPersistence(s.name_en)
}

// ── 加载扫描结果 ──
async function loadResult() {
  if (!currentStrategy.value) return
  try {
    const { data } = await getStrategyResult(currentStrategy.value.name_en)
    scanResults.value = data.data || []
    lastScanDate.value = data.scan_date || ''
  } catch (e) {
    console.error('加载扫描结果失败', e)
  }
}

// ── 加载观察池 ──
async function loadWatchPool() {
  if (!currentStrategy.value) return
  try {
    const { data } = await getStrategyWatch(currentStrategy.value.name_en)
    watchPool.value = data.data || []
    // 加载观察池后自动检查撤退信号
    if (watchPool.value.length) loadExitAlerts()
  } catch (e) {
    console.error('加载观察池失败', e)
  }
}

// ── 执行扫描 ──
async function triggerScan() {
  if (!currentStrategy.value || scanning.value) return
  scanning.value = true
  scanMessage.value = ''
  try {
    const res = await scanStrategy(currentStrategy.value.name_en, {
      min_market_cap: filterMinCap.value * 1e8,
      force: true,
    })
    // ★ 战法准入：未准入时不轮询，直接显示原因
    if (res.data?.admitted === false) {
      scanResults.value = []
      scanMessage.value = res.data?.message || '当前市场状态该战法未准入'
      scanning.value = false
      return
    }
    // 轮询扫描状态
    await pollScanStatus()
  } catch (e) {
    console.error('扫描失败', e)
    scanning.value = false
  }
}

async function pollScanStatus() {
  const maxAttempts = 60
  let attempts = 0
  const poll = async () => {
    attempts++
    try {
      const { data } = await getScanStatus(currentStrategy.value.name_en)
      if (!data.scanning) {
        scanning.value = false
        await loadResult()
        return
      }
      if (attempts < maxAttempts) {
        setTimeout(poll, 2000)
      } else {
        scanning.value = false
      }
    } catch {
      scanning.value = false
    }
  }
  poll()
}

// ── 回测 ──
async function triggerBacktest() {
  if (!currentStrategy.value || backtesting.value) return
  backtesting.value = true
  backtestResult.value = null
  try {
    await runBacktest(currentStrategy.value.name_en, { days: 60, force: true })
    // 轮询回测状态
    await pollBacktestStatus()
  } catch (e) {
    console.error('回测失败', e)
    backtesting.value = false
    showToast('回测失败: ' + (e.response?.data?.detail || e.message))
  }
}

async function pollBacktestStatus() {
  const maxAttempts = 90
  let attempts = 0
  const poll = async () => {
    attempts++
    try {
      const { data } = await getBacktestResult(currentStrategy.value.name_en)
      if (data.data && data.data.signals !== undefined) {
        backtesting.value = false
        backtestResult.value = data.data
        return
      }
      if (attempts < maxAttempts) {
        setTimeout(poll, 2000)
      } else {
        backtesting.value = false
        showToast('回测超时，请稍后重试')
      }
    } catch {
      if (attempts < maxAttempts) {
        setTimeout(poll, 2000)
      } else {
        backtesting.value = false
      }
    }
  }
  poll()
}

async function loadBacktestResult() {
  if (!currentStrategy.value) return
  try {
    const { data } = await getBacktestResult(currentStrategy.value.name_en)
    if (data.data && data.data.signals !== undefined) {
      backtestResult.value = data.data
    } else {
      backtestResult.value = null
    }
  } catch {
    backtestResult.value = null
  }
}

// ── 筛选结果 ──
const filteredResults = computed(() => {
  let results = scanResults.value
  if (filterConfidence.value) {
    results = results.filter(r => r.confidence_level === filterConfidence.value)
  }
  if (filterMinCap.value > 0) {
    results = results.filter(r => (r.market_cap || 0) >= filterMinCap.value * 1e8)
  }
  return results
})

const highCount = computed(() => scanResults.value.filter(r => r.confidence_level === 'high').length)
const mediumCount = computed(() => scanResults.value.filter(r => r.confidence_level === 'medium').length)

const knownStrategies = ['advance2retreat1', 'wizard_pointer', 'dragon_turnaround', 'limit_up_boomerang', 'double_cannon', 'single_yang_unbroken', 'ma_pullback', 'old_duck_head', 'ma_convergence_breakout', 'morning_star']
const isKnownStrategy = computed(() => knownStrategies.includes(currentStrategy.value?.name_en))

// ── 观察池操作 ──
function isInWatch(code) {
  return watchPool.value.some(w => w.code === code)
}

function addToWatch(stock) {
  if (!stock || isInWatch(stock.code)) return
  watchPool.value.push({
    ...stock,
    added_date: new Date().toISOString().slice(0, 10),
  })
  saveWatchPool()
  // 自动联动到自选股和交易计划
  syncToWatchlist(stock, true)
  syncToTradePlan(stock, true)
}

function removeFromWatch(code) {
  watchPool.value = watchPool.value.filter(w => w.code !== code)
  saveWatchPool()
}

async function saveWatchPool() {
  if (!currentStrategy.value) return
  try {
    await updateStrategyWatch(currentStrategy.value.name_en, watchPool.value)
  } catch (e) {
    console.error('保存观察池失败', e)
  }
}

// ── 详情 ──
async function viewDetail(stock) {
  detailStock.value = stock
  detailKlines.value = stock.klines || []
  showDetail.value = true
  
  // 清空旧数据
  supportResistance.value = null
  rsiSignals.value = null
  
  // 加载支撑阻力和 RSI
  if (stock.code) {
    loadSupportResistance(stock.code)
    loadRSISignals(stock.code)
    // ★ 实时刷新 K 线：详情不展示扫描落库时的旧快照（扫描时若遇腾讯 WAF 冷却会滞后）
    try {
      const { data } = await getStrategyDetail(currentStrategy.value.name_en, stock.code)
      if (data?.klines?.length) detailKlines.value = data.klines
    } catch (e) { /* 拉取失败保留扫描快照 */ }
  }
}

// ── 加载支撑阻力 ──
async function loadSupportResistance(code) {
  try {
    const { data } = await getSupportResistance(code)
    supportResistance.value = data.data || null
  } catch (e) {
    console.error('加载支撑阻力失败', e)
  }
}

// ── 加载 RSI ──
async function loadRSISignals(code) {
  try {
    const { data } = await getRSISignals(code)
    rsiSignals.value = data.data || null
  } catch (e) {
    console.error('加载 RSI 失败', e)
  }
}

// ── 加载信号持久度 ──
async function loadPersistence(strategyName) {
  try {
    const [summary, top] = await Promise.allSettled([
      getPersistence(strategyName),
      getPersistentSignals(strategyName, 2),
    ])
    if (summary.status === 'fulfilled') persistenceData.value = summary.value.data?.data || null
    if (top.status === 'fulfilled') persistentSignals.value = top.value.data?.data || []
  } catch (e) {
    console.error('加载持久度失败', e)
  }
}

// ── 加载撤退提醒 ──
async function loadExitAlerts() {
  if (!watchPool.value.length) {
    exitAlerts.value = []
    return
  }
  exitLoading.value = true
  try {
    const positions = watchPool.value.map(w => ({
      code: w.code,
      name: w.name,
      entry_price: w.entry_price,
      stop_loss: w.stop_loss,
      target_price: w.target_price,
    }))
    const { data } = await checkExitAlerts(positions)
    exitAlerts.value = data.data || []
  } catch (e) {
    console.error('加载撤退提醒失败', e)
  } finally {
    exitLoading.value = false
  }
}

// ── RSI 显示辅助 ──
const rsiZoneText = computed(() => {
  if (!rsiSignals.value) return ''
  const zone = rsiSignals.value.zone
  return {
    'strong_overbought': '强超买',
    'overbought': '超买',
    'strong_oversold': '强超卖',
    'oversold': '超卖',
    'neutral': '中性',
  }[zone] || zone
})

const rsiZoneClass = computed(() => {
  if (!rsiSignals.value) return 'text-muted'
  const zone = rsiSignals.value.zone
  return {
    'strong_overbought': 'text-red-400',
    'overbought': 'text-amber-400',
    'strong_oversold': 'text-emerald-400',
    'oversold': 'text-emerald-400',
    'neutral': 'text-muted',
  }[zone] || 'text-muted'
})

// ── 导出 CSV ──
function exportCSV() {
  if (!filteredResults.value.length) return
  const headers = ['代码', '名称', '置信度', '共振级别', '共振分', '连续天数', '可信度级别', '可信度分', '介入价', '止损价', '目标价', '位置%', '扫描日期']
  const rows = filteredResults.value.map(r => [
    r.code, r.name, r.confidence, r.signal_grade || '', r.signal_score || '',
    r.consecutive_days || 1, r.trust_grade || '', r.trust_score || '',
    r.entry_price, r.stop_loss, r.target_price, r.position_pct, r.signal_date,
  ])
  const csv = [headers, ...rows].map(row => row.join(',')).join('\n')
  const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `${currentStrategy.value?.name || '战法'}_信号_${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

// ── 样式辅助 ──
function confidenceClass(level) {
  return {
    'high': 'text-emerald-400 font-medium',
    'medium': 'text-amber-400',
    'low': 'text-muted',
  }[level] || 'text-muted'
}

function confidenceText(level) {
  return { 'high': '高置信', 'medium': '中置信', 'low': '低置信' }[level] || ''
}

function gradeClass(grade) {
  return {
    'A': 'text-emerald-400',
    'B': 'text-amber-400',
    'C': 'text-muted',
    'D': 'text-red-400',
  }[grade] || 'text-muted'
}

function consecutiveClass(days) {
  if (days >= 5) return 'text-emerald-400'
  if (days >= 3) return 'text-amber-400'
  return 'text-muted'
}

function trustGradeClass(grade) {
  return {
    'A+': 'text-emerald-400 font-bold',
    'A': 'text-emerald-400',
    'B': 'text-amber-400',
    'C': 'text-muted',
    'D': 'text-red-400',
  }[grade] || 'text-muted'
}

// ── 联动自选股 ──
function syncToWatchlist(stock, silent = false) {
  if (!stock) return
  addWatch({
    code: stock.code,
    name: stock.name,
    target_price: stock.entry_price,
    note: `战法信号: ${currentStrategy.value?.name || ''} | 止损:${stock.stop_loss} 目标:${stock.target_price}`,
  })
  if (!silent) showToast(`已加入自选股: ${stock.name}`)
}

// ── 联动交易计划 ──
function syncToTradePlan(stock, silent = false) {
  if (!stock) return
  addPlan({
    code: stock.code,
    name: stock.name,
    buy_price: stock.entry_price,
    stop_loss: stock.stop_loss,
    target: stock.target_price,
    reason: `战法信号: ${currentStrategy.value?.name || ''}，置信度${stock.confidence}分`,
    expected: `目标涨幅 ${((stock.target_price - stock.entry_price) / stock.entry_price * 100).toFixed(1)}%`,
  })
  if (!silent) showToast(`已创建交易计划: ${stock.name}`)
}

// ── 批量联动 ──
function batchSyncWatchlist() {
  if (!filteredResults.value.length) return
  filteredResults.value.forEach(r => syncToWatchlist(r, true))
  showToast(`已批量加入自选股: ${filteredResults.value.length} 只`)
}

function batchSyncTradePlan() {
  if (!filteredResults.value.length) return
  filteredResults.value.forEach(r => syncToTradePlan(r, true))
  showToast(`已批量创建交易计划: ${filteredResults.value.length} 只`)
}

// ── 轻提示 ──
const toastMsg = ref('')
const toastVisible = ref(false)
let toastTimer = null
function showToast(msg) {
  toastMsg.value = msg
  toastVisible.value = true
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toastVisible.value = false }, 2000)
}

// ── 初始化 ──
onMounted(() => {
  loadStrategies()
  loadMarketRegime()
})

// ── 加载市场状态 ──
async function loadMarketRegime() {
  try {
    const { data } = await getMarketRegime()
    marketRegime.value = data.data || null
  } catch (e) {
    console.error('加载市场状态失败', e)
  }
}

// ── 加载战法适用性建议 ──
async function loadRecommendation(strategyName) {
  if (!strategyName) return
  try {
    const { data } = await getStrategyRecommendation(strategyName)
    strategyRecommendation.value = data.data || null
  } catch (e) {
    console.error('加载战法建议失败', e)
  }
}

// ── 市场状态显示 ──
const regimeText = computed(() => {
  if (!marketRegime.value) return ''
  const r = marketRegime.value.regime
  if (r === 'offensive') return '进攻市'
  if (r === 'neutral') return '震荡市'
  if (r === 'defensive') return '防御市'
  return '未知'
})

const regimeColor = computed(() => {
  if (!marketRegime.value) return 'text-muted'
  const r = marketRegime.value.regime
  if (r === 'offensive') return 'text-emerald-400'
  if (r === 'neutral') return 'text-amber-400'
  if (r === 'defensive') return 'text-red-400'
  return 'text-muted'
})

// ── 判断战法是否推荐 ──
function isRecommended(strategyName) {
  if (!marketRegime.value || !marketRegime.value.recommended_strategies) return false
  return marketRegime.value.recommended_strategies.includes(strategyName)
}
</script>
