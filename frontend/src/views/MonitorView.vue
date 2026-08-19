<template>
  <div class="fade-in space-y-4">
    <!-- 系统状态条（数据源健康 / 配置 / LLM 用量） -->
    <div class="bg-card border border-border rounded-lg p-3 flex items-center justify-between flex-wrap gap-x-4 gap-y-1 text-xs">
      <div class="flex items-center gap-3 flex-wrap">
        <span class="text-muted">调度器</span>
        <span :class="status.running ? 'text-emerald-400' : 'text-red-400'">{{ status.running ? '运行中' : '未运行' }}</span>
        <span class="text-muted">|</span>
        <span v-if="!status.sources">数据源加载中...</span>
        <span v-for="(h, key) in status.sources" :key="key" :title="`成功率${h.ok_rate ?? '-'}%${h.last_error ? '，最近错误: ' + h.last_error : ''}`">
          {{ h.name }}
          <span v-if="h.status === '健康'" class="text-emerald-400">✓</span>
          <span v-else-if="h.status === '异常'" class="text-red-400 font-bold">✗</span>
          <span v-else class="text-muted">·</span>
        </span>
        <span class="text-muted">|</span>
        <span>微信 <span :class="status.config?.wechat_configured ? 'text-emerald-400' : 'text-muted'">{{ status.config?.wechat_configured ? '✓' : '未配置' }}</span></span>
        <span class="text-muted">|</span>
        <span class="text-muted" :title="'本浏览器每5分钟镜像一次服务端数据；部署清零后自动恢复'">浏览器镜像 {{ localStorage.getItem('flash_mirror_time') || '未建立' }}</span>
      </div>
      <div class="flex items-center gap-3">
        <span v-if="status.llm_usage?.today" class="text-muted" :class="{'text-amber-400': status.llm_usage.remaining_today <= 5}">
          今日 LLM {{ status.llm_usage.today.calls }}/{{ status.llm_usage.daily_limit }} 次 ·
          {{ fmtTokens(status.llm_usage.today.prompt_tokens + status.llm_usage.today.completion_tokens) }}token
          <span v-if="status.llm_usage.blocked_reason" class="text-red-400">（{{ status.llm_usage.blocked_reason }}）</span>
        </span>
        <span class="text-muted">上次轮询 {{ fmtTime(status.last_flash_poll?.time) }}</span>
        <button @click="ingest" :disabled="ingesting"
          class="bg-white/5 border border-border rounded px-3 py-1 text-gray-300 hover:bg-white/10 disabled:opacity-40">
          {{ ingesting ? '拉取中...' : '立即拉取' }}
        </button>
      </div>
    </div>

    <!-- Tab -->
    <div class="bg-card border border-border rounded-lg p-3">
      <div class="flex gap-2 flex-wrap">
        <button v-for="tab in tabs" :key="tab.key" @click="switchTab(tab.key)"
          :class="activeTab === tab.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted hover:text-gray-200'"
          class="px-3 py-1 rounded text-xs transition-colors">
          {{ tab.label }}
        </button>
      </div>
    </div>

    <!-- ══════════ Tab 1: 今日诊断 ══════════ -->
    <div v-if="activeTab === 'diagnosis'" class="space-y-4">
      <div v-if="!diag" class="bg-card border border-border rounded-lg p-12 text-center text-muted">
        暂无诊断记录——需配置 FLASH_COOKIE + LLM_API_KEY，等调度器抓到新事件后自动生成。
      </div>
      <template v-else>
        <!-- 诊断头部 -->
        <div class="bg-card border border-border rounded-lg p-4 grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <div class="text-xs text-muted mb-1">相关性状态</div>
            <div class="text-lg font-bold" :class="corrColor(diag.correlation_diagnosis?.correlation_state)">
              {{ diag.correlation_diagnosis?.correlation_state || '—' }}
            </div>
            <div class="text-xs text-muted">{{ diag.correlation_diagnosis?.d_state_type || '' }}</div>
          </div>
          <div>
            <div class="text-xs text-muted mb-1">当前阶段</div>
            <div class="text-lg font-bold font-mono">{{ diag.correlation_diagnosis?.current_phase || '—' }}</div>
          </div>
          <div>
            <div class="text-xs text-muted mb-1">市场情绪</div>
            <div class="text-lg font-bold">{{ diag.market_mood || '—' }}</div>
            <div class="text-xs text-muted">不确定性 {{ diag.uncertainty_level || '—' }}</div>
          </div>
          <div>
            <div class="text-xs text-muted mb-1">数据质量</div>
            <div class="text-lg font-bold" :class="diag.diagnostic_status?.data_quality === '充足' ? 'text-emerald-400' : 'text-amber-400'">
              {{ diag.diagnostic_status?.data_quality || '—' }}
            </div>
            <div class="text-xs text-muted">置信 {{ diag.diagnostic_status?.overall_confidence || '—' }}</div>
          </div>
        </div>

        <!-- 主导叙事 -->
        <div v-if="diag.dominant_narrative?.narrative" class="bg-card border border-border rounded-lg p-4">
          <div class="text-xs text-muted mb-1">主导叙事</div>
          <div class="text-sm text-gray-200">{{ diag.dominant_narrative.narrative }}</div>
          <div class="text-xs text-amber-400 mt-1" v-if="diag.dominant_narrative.fragility">
            脆弱点：{{ diag.dominant_narrative.fragility }}
          </div>
        </div>

        <!-- 情景推演 -->
        <div v-if="diag.scenarios?.length" class="bg-card border border-border rounded-lg p-4">
          <h3 class="text-sm font-semibold mb-3">🎭 情景推演</h3>
          <div class="grid md:grid-cols-2 gap-3">
            <div v-for="s in diag.scenarios" :key="s.scenario_name" class="p-3 bg-bg rounded-lg">
              <div class="text-sm font-bold text-gray-200">{{ s.scenario_name }} <span class="text-xs text-muted font-normal">（{{ s.probability_qualitative }}）</span></div>
              <div class="text-xs text-muted mt-1">油路：{{ s.oil_path }}</div>
              <div class="text-xs text-accent mt-1">触发观察：{{ s.trigger_to_watch }}</div>
              <div v-if="s.affected_etfs?.length" class="text-xs mt-1 text-muted">关联：{{ s.affected_etfs.join('、') }}</div>
            </div>
          </div>
        </div>

        <!-- 重点事件 -->
        <div v-if="diag.top_events?.length" class="bg-card border border-border rounded-lg p-4">
          <h3 class="text-sm font-semibold mb-3">🔍 重点事件（Top 5）</h3>
          <div class="space-y-3">
            <div v-for="(e, i) in diag.top_events.slice(0, 5)" :key="i" class="p-3 bg-bg rounded-lg">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="font-bold text-sm" :class="e.action === '加仓' || e.action === '埋伏' ? 'text-rise' : e.action === '减仓' ? 'text-fall' : 'text-amber-400'">
                  {{ e.action }} {{ e.target }}
                </span>
                <span v-if="e.time_sensitive" class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20">紧急</span>
                <span class="text-xs text-muted">价值分 {{ e.value_score }} · {{ e.urgency }}</span>
              </div>
              <div class="text-xs text-gray-300 mt-1">{{ e.why }}</div>
              <div class="text-xs text-muted mt-1">链条：{{ e.transmission_chain }}</div>
            </div>
          </div>
        </div>

        <!-- 每日策略 -->
        <div v-if="diag.daily_strategy" class="bg-card border border-border rounded-lg p-4">
          <h3 class="text-sm font-semibold mb-2">📅 交易策略 <span class="text-xs text-muted">（{{ diag.daily_strategy.max_position_confidence }}置信度）</span></h3>
          <div class="text-sm mb-2">总仓位：<span class="font-bold text-accent">{{ diag.daily_strategy.overall_position }}</span></div>
          <div class="text-xs text-gray-300 mb-2">{{ diag.daily_strategy.core_logic }}</div>
          <div class="flex flex-wrap gap-2 mt-2">
            <span v-for="k in diag.daily_strategy.key_risks" :key="k"
              class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/10 text-red-400 border border-red-500/20">{{ k }}</span>
          </div>
        </div>
      </template>

      <!-- 最新复盘 -->
      <div class="bg-card border border-border rounded-lg p-4">
        <div class="flex items-center justify-between mb-2">
          <h3 class="text-sm font-semibold">📋 最新复盘</h3>
          <div class="flex gap-1">
            <button v-for="p in phases" :key="p.key" @click="loadReview(p.key)"
              :class="reviewPhase === p.key ? 'bg-accent/20 text-accent' : 'bg-white/5 text-muted'"
              class="px-2 py-0.5 rounded text-[11px]">{{ p.label }}</button>
          </div>
        </div>
        <div v-if="review.markdown" class="space-y-2">
          <div class="text-xs text-muted">{{ fmtTime(review.time) }}</div>
          <div class="text-sm text-gray-300 whitespace-pre-wrap leading-relaxed max-h-96 overflow-y-auto">{{ review.markdown }}</div>
          <div v-if="review.signals?.length" class="text-xs text-muted">
            本轮信号：{{ review.signals.map(s => `${s.etfName}(${s.direction === 'long' ? '多' : '空'})`).join('、') }}
          </div>
        </div>
        <div v-else class="text-sm text-muted py-6 text-center">该阶段暂无复盘记录</div>
      </div>
    </div>

    <!-- ══════════ Tab 2: 事件流 ══════════ -->
    <div v-if="activeTab === 'events'" class="bg-card border border-border rounded-lg overflow-hidden">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2.5 px-3">时间</th>
            <th class="text-left py-2.5 px-3">热度</th>
            <th class="text-left py-2.5 px-3">内容</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in events" :key="item.id" class="border-b border-border/50 hover:bg-white/3">
            <td class="py-2 px-3 text-muted font-mono text-xs whitespace-nowrap">{{ item.time?.slice(11) || item.time }}</td>
            <td class="py-2 px-3">
              <span :class="item.hot === '爆' ? 'text-red-400' : item.hot === '沸' ? 'text-orange-400' : 'text-amber-400'" class="text-xs font-bold">{{ item.hot }}</span>
            </td>
            <td class="py-2 px-3 text-gray-300 leading-snug">{{ item.content }}</td>
          </tr>
          <tr v-if="!events.length">
            <td colspan="3" class="py-12 text-center text-muted">暂无快讯——需配置 FLASH_COOKIE，或点击上方「立即拉取」</td>
          </tr>
        </tbody>
      </table>
      <div v-if="eventsTotal > eventSize" class="flex items-center justify-center gap-2 px-3 py-2 border-t border-border text-xs">
        <button @click="eventPage > 1 && (eventPage--, loadEvents())" :disabled="eventPage <= 1"
          class="px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">上一页</button>
        <span class="text-muted">{{ eventPage }} / {{ Math.ceil(eventsTotal / eventSize) }}（共{{ eventsTotal }}条）</span>
        <button @click="eventPage++; loadEvents()" :disabled="eventPage >= Math.ceil(eventsTotal / eventSize)"
          class="px-2.5 py-1 rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">下一页</button>
      </div>
    </div>

    <!-- ══════════ Tab 3: 信号跟踪 ══════════ -->
    <div v-if="activeTab === 'signals'" class="space-y-4">
      <!-- 绩效 -->
      <div class="grid grid-cols-2 md:grid-cols-6 gap-3">
        <StatBox label="总交易" :value="signals.performance?.total ?? 0" />
        <StatBox label="胜率" :value="(signals.performance?.winRate ?? 0) + '%'" :color="parseFloat(signals.performance?.winRate) >= 50 ? 'text-rise' : 'text-fall'" />
        <StatBox label="盈/亏" :value="`${signals.performance?.wins ?? 0}/${signals.performance?.losses ?? 0}`" />
        <StatBox label="盈亏比" :value="signals.metrics?.profitFactor ?? '-'" />
        <StatBox label="最大回撤" :value="signals.metrics ? '-' + signals.metrics.maxDrawdown + '%' : '-'" />
        <StatBox label="活跃信号" :value="signals.activeSignals?.length ?? 0" color="text-accent" />
      </div>

      <!-- 活跃信号表 -->
      <div class="bg-card border border-border rounded-lg overflow-hidden">
        <div class="px-3 py-2 border-b border-border text-sm font-semibold">🎯 活跃信号（waiting/active）</div>
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-muted text-xs">
              <th class="text-left py-2 px-3">ETF</th>
              <th class="text-center py-2 px-3">方向</th>
              <th class="text-center py-2 px-3">状态</th>
              <th class="text-right py-2 px-3">入场</th>
              <th class="text-right py-2 px-3">止损</th>
              <th class="text-right py-2 px-3">止盈</th>
              <th class="text-right py-2 px-3">技术分</th>
              <th class="text-right py-2 px-3">现价</th>
              <th class="text-left py-2 px-3">理由</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in signals.activeSignals" :key="s.id" class="border-b border-border/50">
              <td class="py-2 px-3 font-medium">{{ s.etfName }}</td>
              <td class="py-2 px-3 text-center" :class="s.direction === 'long' ? 'text-rise' : 'text-fall'">{{ s.direction === 'long' ? '多' : '空' }}</td>
              <td class="py-2 px-3 text-center">
                <span :class="s.status === 'active' ? 'text-emerald-400' : 'text-amber-400'" class="text-xs">{{ s.status === 'active' ? '已入场' : '等待' }}</span>
              </td>
              <td class="py-2 px-3 text-right font-mono">{{ s.entryCondition?.targetPrice }}</td>
              <td class="py-2 px-3 text-right font-mono text-fall">{{ s.stopLoss }}</td>
              <td class="py-2 px-3 text-right font-mono text-rise">{{ s.takeProfit }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs">{{ s.techScore }}<span class="text-muted">/{{ s.techGrade }}</span></td>
              <td class="py-2 px-3 text-right font-mono">{{ s.lastCheckedPrice ?? '-' }}</td>
              <td class="py-2 px-3 text-xs text-muted max-w-[240px] truncate" :title="s.reasoning">{{ s.reasoning }}</td>
            </tr>
            <tr v-if="!signals.activeSignals?.length">
              <td colspan="9" class="py-10 text-center text-muted">暂无活跃信号——复盘产生的信号通过风控门槛后会出现在这里</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 平仓历史 -->
      <div class="bg-card border border-border rounded-lg overflow-hidden" v-if="closedHistory.length">
        <div class="px-3 py-2 border-b border-border text-sm font-semibold">📜 平仓历史</div>
        <table class="w-full text-sm">
          <tbody>
            <tr v-for="(h, i) in closedHistory" :key="i" class="border-b border-border/50">
              <td class="py-2 px-3 font-medium">{{ h.etfName }}</td>
              <td class="py-2 px-3 text-xs" :class="h.direction === 'long' ? 'text-rise' : 'text-fall'">{{ h.direction === 'long' ? '多' : '空' }}</td>
              <td class="py-2 px-3 text-xs text-muted">{{ h.exits?.[h.exits.length - 1]?.reason }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs" :class="h.isWin ? 'text-rise' : 'text-fall'">
                {{ h.profit > 0 ? '+' : '' }}{{ h.profit }}%
              </td>
              <td class="py-2 px-3 text-right font-mono text-xs text-muted">{{ fmtTime(h.exitTime) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 过期信号（自动失效的等待信号） -->
      <div class="bg-card border border-border rounded-lg overflow-hidden" v-if="expiredHistory.length">
        <div class="px-3 py-2 border-b border-border text-sm font-semibold text-muted">⏰ 已过期信号（论点失效/超时未触发）</div>
        <table class="w-full text-sm">
          <tbody>
            <tr v-for="(h, i) in expiredHistory" :key="'e'+i" class="border-b border-border/50">
              <td class="py-2 px-3 font-medium text-muted">{{ h.etfName }}</td>
              <td class="py-2 px-3 text-xs text-muted">{{ h.direction === 'long' ? '多' : '空' }}</td>
              <td class="py-2 px-3 text-xs text-muted">{{ h.expireReason || '已过期' }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs text-muted">入场 {{ h.entryCondition?.targetPrice }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ══════════ Tab 4: 对账（LLM 预测 vs 实际） ══════════ -->
    <div v-if="activeTab === 'audit'" class="space-y-4">
      <!-- 漏斗概览 -->
      <div class="grid grid-cols-2 md:grid-cols-7 gap-3">
        <StatBox label="提议信号" :value="audit.summary?.proposed ?? 0" />
        <StatBox label="通过门槛" :value="(audit.summary?.proposed ?? 0) - (audit.summary?.rejected ?? 0)" color="text-accent" />
        <StatBox label="等待/持仓" :value="`${audit.summary?.waiting ?? 0}/${audit.summary?.holding ?? 0}`" />
        <StatBox label="已平仓" :value="audit.summary?.closed ?? 0" />
        <StatBox label="胜率" :value="audit.summary?.win_rate != null ? audit.summary.win_rate + '%' : '—'"
          :color="audit.summary?.win_rate >= 50 ? 'text-rise' : audit.summary?.win_rate != null ? 'text-fall' : ''" />
        <StatBox label="平均收益" :value="audit.summary?.avg_profit != null ? (audit.summary.avg_profit > 0 ? '+' : '') + audit.summary.avg_profit + '%' : '—'"
          :color="audit.summary?.avg_profit > 0 ? 'text-rise' : audit.summary?.avg_profit < 0 ? 'text-fall' : ''" />
        <StatBox label="盈亏比" :value="audit.summary?.profit_factor ?? '—'" />
      </div>
      <div v-if="audit.note" class="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded px-3 py-2">
        ⚠️ {{ audit.note }}
      </div>
      <div v-if="audit.summary?.stale_waiting?.length" class="text-xs text-muted">
        僵尸等待（超5天未触发）：{{ audit.summary.stale_waiting.join('、') }}
      </div>
      <div v-if="audit.summary?.expired_count" class="text-xs text-muted">
        近30天过期信号：{{ audit.summary.expired_count }} 条
        <span v-if="audit.summary.expired_details?.length" class="text-muted ml-1">
          （{{ audit.summary.expired_details.map(d => d.etf).join('、') }}）
        </span>
      </div>

      <!-- 按阶段 + 按方向 -->
      <div class="grid md:grid-cols-2 gap-4">
        <div class="bg-card border border-border rounded-lg overflow-hidden">
          <div class="px-3 py-2 border-b border-border text-sm font-semibold">📊 按复盘阶段</div>
          <table class="w-full text-sm">
            <thead><tr class="border-b border-border text-muted text-xs">
              <th class="text-left py-2 px-3">阶段</th><th class="text-right py-2 px-3">提议</th>
              <th class="text-right py-2 px-3">通过</th><th class="text-right py-2 px-3">平仓</th>
              <th class="text-right py-2 px-3">胜率</th><th class="text-right py-2 px-3">均收益</th>
            </tr></thead>
            <tbody>
              <tr v-for="(p, key) in audit.by_phase" :key="key" class="border-b border-border/50">
                <td class="py-2 px-3">{{ p.name }}</td>
                <td class="py-2 px-3 text-right font-mono">{{ p.proposed }}</td>
                <td class="py-2 px-3 text-right font-mono text-accent">{{ p.passed }}</td>
                <td class="py-2 px-3 text-right font-mono">{{ p.closed }}</td>
                <td class="py-2 px-3 text-right font-mono" :class="p.win_rate >= 50 ? 'text-rise' : p.win_rate != null ? 'text-fall' : 'text-muted'">{{ p.win_rate != null ? p.win_rate + '%' : '—' }}</td>
                <td class="py-2 px-3 text-right font-mono" :class="p.avg_profit > 0 ? 'text-rise' : p.avg_profit < 0 ? 'text-fall' : 'text-muted'">{{ p.avg_profit != null ? (p.avg_profit > 0 ? '+' : '') + p.avg_profit + '%' : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="bg-card border border-border rounded-lg overflow-hidden">
          <div class="px-3 py-2 border-b border-border text-sm font-semibold">📈 按方向（多 vs 空）</div>
          <table class="w-full text-sm">
            <thead><tr class="border-b border-border text-muted text-xs">
              <th class="text-left py-2 px-3">方向</th><th class="text-right py-2 px-3">平仓</th>
              <th class="text-right py-2 px-3">胜率</th><th class="text-right py-2 px-3">均收益</th>
            </tr></thead>
            <tbody>
              <tr class="border-b border-border/50">
                <td class="py-2 px-3 text-rise">做多</td>
                <td class="py-2 px-3 text-right font-mono">{{ audit.by_direction?.long?.closed ?? 0 }}</td>
                <td class="py-2 px-3 text-right font-mono">{{ audit.by_direction?.long?.win_rate != null ? audit.by_direction.long.win_rate + '%' : '—' }}</td>
                <td class="py-2 px-3 text-right font-mono">{{ audit.by_direction?.long?.avg_profit != null ? audit.by_direction.long.avg_profit + '%' : '—' }}</td>
              </tr>
              <tr class="border-b border-border/50">
                <td class="py-2 px-3 text-fall">做空</td>
                <td class="py-2 px-3 text-right font-mono">{{ audit.by_direction?.short?.closed ?? 0 }}</td>
                <td class="py-2 px-3 text-right font-mono">{{ audit.by_direction?.short?.win_rate != null ? audit.by_direction.short.win_rate + '%' : '—' }}</td>
                <td class="py-2 px-3 text-right font-mono">{{ audit.by_direction?.short?.avg_profit != null ? audit.by_direction.short.avg_profit + '%' : '—' }}</td>
              </tr>
            </tbody>
          </table>
          <div v-if="Object.keys(audit.rejection_gates || {}).length" class="px-3 py-2 border-t border-border text-xs text-muted">
            拒绝原因：<span v-for="(n, k) in audit.rejection_gates" :key="k" class="mr-2">{{ k }}×{{ n }}</span>
          </div>
        </div>
      </div>

      <!-- 按 ETF -->
      <div class="bg-card border border-border rounded-lg overflow-hidden">
        <div class="px-3 py-2 border-b border-border text-sm font-semibold">🏷️ 按 ETF（哪个推得最差一目了然）</div>
        <table class="w-full text-sm">
          <thead><tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2 px-3">ETF</th><th class="text-right py-2 px-3">提议</th>
            <th class="text-right py-2 px-3">通过</th><th class="text-right py-2 px-3">平仓</th>
            <th class="text-right py-2 px-3">胜率</th><th class="text-right py-2 px-3">均收益</th>
          </tr></thead>
          <tbody>
            <tr v-for="e in audit.by_etf" :key="e.etf" class="border-b border-border/50">
              <td class="py-2 px-3 font-medium">{{ e.etf }}</td>
              <td class="py-2 px-3 text-right font-mono">{{ e.proposed }}</td>
              <td class="py-2 px-3 text-right font-mono text-accent">{{ e.passed }}</td>
              <td class="py-2 px-3 text-right font-mono">{{ e.closed }}</td>
              <td class="py-2 px-3 text-right font-mono" :class="e.win_rate >= 50 ? 'text-rise' : e.win_rate != null ? 'text-fall' : 'text-muted'">{{ e.win_rate != null ? e.win_rate + '%' : '—' }}</td>
              <td class="py-2 px-3 text-right font-mono" :class="e.avg_profit > 0 ? 'text-rise' : e.avg_profit < 0 ? 'text-fall' : 'text-muted'">{{ e.avg_profit != null ? (e.avg_profit > 0 ? '+' : '') + e.avg_profit + '%' : '—' }}</td>
            </tr>
            <tr v-if="!audit.by_etf?.length"><td colspan="6" class="py-10 text-center text-muted">还没有提议记录</td></tr>
          </tbody>
        </table>
      </div>

      <!-- 平仓明细 -->
      <div class="bg-card border border-border rounded-lg overflow-hidden" v-if="audit.closed_trades?.length">
        <div class="px-3 py-2 border-b border-border text-sm font-semibold">📋 平仓明细（最近30笔）</div>
        <table class="w-full text-sm">
          <thead><tr class="border-b border-border text-muted text-xs">
            <th class="text-left py-2 px-3">ETF</th><th class="text-center py-2 px-3">方向</th>
            <th class="text-left py-2 px-3">来源</th><th class="text-right py-2 px-3">入场</th>
            <th class="text-right py-2 px-3">出场</th><th class="text-left py-2 px-3">原因</th>
            <th class="text-right py-2 px-3">盈亏</th><th class="text-right py-2 px-3">时间</th>
          </tr></thead>
          <tbody>
            <tr v-for="(t, i) in audit.closed_trades" :key="i" class="border-b border-border/50">
              <td class="py-2 px-3 font-medium">{{ t.etfName }}</td>
              <td class="py-2 px-3 text-center text-xs" :class="t.direction === 'long' ? 'text-rise' : 'text-fall'">{{ t.direction === 'long' ? '多' : '空' }}</td>
              <td class="py-2 px-3 text-xs text-muted">{{ { premarket: '盘前', lunchbreak: '午盘', postmarket: '盘后' }[t.source] || t.source }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs">{{ t.entryPrice }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs">{{ t.exitPrice }}</td>
              <td class="py-2 px-3 text-xs text-muted">{{ t.reason }}</td>
              <td class="py-2 px-3 text-right font-mono text-xs" :class="t.isWin ? 'text-rise' : 'text-fall'">{{ t.profit > 0 ? '+' : '' }}{{ t.profit }}%</td>
              <td class="py-2 px-3 text-right font-mono text-xs text-muted">{{ fmtTime(t.exitTime) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { getFlashStatus, getFlashDiagnosis, getFlashEvents, getFlashReview, getFlashSignals, getFlashAudit, triggerFlashIngest } from '../api'
// localStorage 在模板里直接读（状态条显示镜像时间；切到本页时刷新一次）
const localStorage = window.localStorage

const tabs = [
  { key: 'diagnosis', label: '今日诊断' },
  { key: 'events', label: '事件流' },
  { key: 'signals', label: '信号跟踪' },
  { key: 'audit', label: '对账' },
]
const phases = [
  { key: 'premarket', label: '盘前' },
  { key: 'lunchbreak', label: '午盘' },
  { key: 'postmarket', label: '盘后' },
]

const activeTab = ref('diagnosis')
const status = ref({})
const diag = ref(null)
const events = ref([])
const eventsTotal = ref(0)
const eventPage = ref(1)
const eventSize = 50
const signals = ref({})
const audit = ref({})
const review = ref({})
const reviewPhase = ref('premarket')
const ingesting = ref(false)

// 平仓历史（排除过期信号）+ 过期信号列表
const closedHistory = computed(() => (signals.value.history || []).filter(h => h.status === 'closed'))
const expiredHistory = computed(() => (signals.value.history || []).filter(h => h.status === 'expired'))

function corrColor(state) {
  if (state === '正相关') return 'text-orange-400'
  if (state === '负相关') return 'text-cyan-400'
  if (state === 'D状态') return 'text-amber-400'
  return 'text-muted'
}

function fmtTime(t) {
  if (!t) return '—'
  return String(t).slice(5, 16).replace('T', ' ')
}

function fmtTokens(n) {
  if (!n) return '0'
  if (n >= 10000) return (n / 10000).toFixed(1) + '万'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return String(n)
}

async function loadStatus() {
  try { const { data } = await getFlashStatus(); status.value = data } catch (e) { console.error(e) }
}

async function loadDiagnosis() {
  try {
    const { data } = await getFlashDiagnosis({ limit: 1 })
    diag.value = data.latest?.output || null
  } catch (e) { console.error(e) }
}

async function loadEvents() {
  try {
    const { data } = await getFlashEvents({ page: eventPage.value, size: eventSize })
    events.value = data.data || []
    eventsTotal.value = data.total || 0
  } catch (e) { console.error(e) }
}

async function loadSignals() {
  try { const { data } = await getFlashSignals(); signals.value = data } catch (e) { console.error(e) }
}

async function loadAudit() {
  try { const { data } = await getFlashAudit(); audit.value = data } catch (e) { console.error(e) }
}

async function loadReview(phase) {
  reviewPhase.value = phase
  try { const { data } = await getFlashReview(phase); review.value = data } catch (e) { console.error(e) }
}

async function ingest() {
  ingesting.value = true
  try {
    await triggerFlashIngest()
    await Promise.all([loadEvents(), loadDiagnosis()])
  } catch (e) { console.error(e) } finally { ingesting.value = false }
}

function switchTab(key) {
  activeTab.value = key
  if (key === 'events' && !events.value.length) loadEvents()
  if (key === 'signals' && !signals.value.performance) loadSignals()
  if (key === 'audit' && !audit.value.summary) loadAudit()
}

onMounted(() => {
  loadStatus()
  loadDiagnosis()
  loadReview('premarket')
})
</script>

<script>
// 简单统计盒（内联组件，避免单文件滥用）
export default {
  components: {
    StatBox: {
      props: ['label', 'value', 'color'],
      template: `
        <div class="bg-card border border-border rounded-lg p-3 text-center">
          <div class="text-xs text-muted">{{ label }}</div>
          <div class="text-lg font-bold mt-1 font-mono" :class="color || 'text-gray-200'">{{ value }}</div>
        </div>
      `,
    },
  },
}
</script>
