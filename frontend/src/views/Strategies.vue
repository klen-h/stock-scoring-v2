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
                'p-2.5 rounded-lg cursor-pointer transition-all',
                currentStrategy?.name_en === s.name_en
                  ? 'bg-accent/15 border border-accent/30'
                  : 'bg-white/5 hover:bg-white/10 border border-transparent'
              ]">
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
          <p>暂无扫描结果</p>
          <p class="text-sm mt-2">点击"执行扫描"开始扫描股票池</p>
        </div>

        <div v-else class="bg-card border border-border rounded-lg overflow-hidden">
          <!-- 结果统计 -->
          <div class="p-3 border-b border-border flex items-center justify-between">
            <div class="flex items-center gap-4 text-sm">
              <span class="text-muted">共 {{ filteredResults.length }} 只</span>
              <span class="text-emerald-400">高置信 {{ highCount }}</span>
              <span class="text-amber-400">中置信 {{ mediumCount }}</span>
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
          <div v-else class="overflow-x-auto">
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
              <p class="text-xs mt-2">从信号列表点击"+观察"添加</p>
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
  } catch (e) {
    console.error('加载观察池失败', e)
  }
}

// ── 执行扫描 ──
async function triggerScan() {
  if (!currentStrategy.value || scanning.value) return
  scanning.value = true
  try {
    await scanStrategy(currentStrategy.value.name_en, {
      min_market_cap: filterMinCap.value * 1e8,
      force: true,
    })
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
}

// ── 导出 CSV ──
function exportCSV() {
  if (!filteredResults.value.length) return
  const headers = ['代码', '名称', '置信度', '级别', '介入价', '止损价', '目标价', '位置%', '扫描日期']
  const rows = filteredResults.value.map(r => [
    r.code, r.name, r.confidence, r.confidence_level,
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
})
</script>
