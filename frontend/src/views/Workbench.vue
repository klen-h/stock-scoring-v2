<template>
  <div class="fade-in space-y-4">
    <!-- 回放模式横幅（只读） -->
    <div v-if="isReplay"
         class="bg-amber-500/10 border border-amber-500/40 text-amber-300 rounded-lg px-4 py-2 text-sm flex items-center justify-between">
      <span>⏪ 回放 {{ selectedDate }} · 只读（右栏为该日警报历史）</span>
      <button class="text-xs underline" @click="backToToday">切回今天</button>
    </div>

    <!-- 顶栏：日期 · 上下两列（A股/外盘）· 情绪市况模块 · 数据新鲜度（常驻） -->
    <div class="bg-card border border-border rounded-lg px-4 py-2 flex items-center gap-4 flex-wrap text-sm">
      <label class="flex items-center gap-2">
        <span class="text-muted text-xs">日期</span>
        <select v-model="selectedDate"
                class="bg-background border border-border rounded px-2 py-1 text-xs">
          <option v-for="d in dayList" :key="d.date" :value="d.date">
            {{ d.date === todayStr ? '今天 ' + d.date : d.date }}
          </option>
        </select>
      </label>

      <!-- ★ 2026-09-25 用户要求：竖排标签样式（名称/价格/涨跌 上下三行），居中排开空间足够；
           细分隔线区分 A股与外盘 -->
      <div class="flex-1 min-w-[560px] flex items-center justify-center gap-3 flex-wrap">
        <div v-for="ix in topIndices" :key="ix.name" class="text-center px-1.5">
          <div class="text-[10px] text-muted">{{ ix.name }}</div>
          <div class="text-xs font-mono font-semibold leading-snug">{{ fmtNum(ix.price) }}</div>
          <div class="text-xs font-mono font-bold" :class="pctClass(ix.change_pct)">
            {{ signNum(ix.change_pct) }}%
          </div>
        </div>
        <div class="w-px self-stretch bg-border"></div>
        <div v-for="g in globalsRow" :key="g.key" class="text-center px-1.5">
          <div class="text-[10px] text-muted">{{ g.label }}</div>
          <div class="text-xs font-mono font-semibold leading-snug">{{ g.price ?? '—' }}</div>
          <div class="text-xs font-mono font-bold" :class="pctClass(g.pct)">
            {{ signNum(g.pct) }}%
          </div>
        </div>
      </div>

      <!-- 情绪/市况模块（归并为一组） -->
      <div class="flex items-center gap-3 border-l border-border pl-4">
        <div class="text-center">
          <div class="text-lg font-bold font-mono leading-none">{{ temperature?.temperature ?? '—' }}</div>
          <div class="text-[10px] text-muted mt-0.5">情绪·{{ temperature?.level || '—' }}</div>
        </div>
        <div class="w-px h-8 bg-border"></div>
        <div>
          <div class="text-xs font-semibold" :class="regimeClass">{{ regimeLabel }}</div>
          <div class="text-[10px] text-muted">市况</div>
        </div>
      </div>

      <!-- 数据新鲜度（点开看底部状态区） -->
      <span class="flex items-center gap-1.5 cursor-pointer" @click="statusOpen = true">
        <span class="inline-block w-2 h-2 rounded-full"
              :class="freshnessOk ? 'bg-emerald-500' : 'bg-amber-500'"></span>
        <span class="text-muted text-xs">数据新鲜度</span>
      </span>
    </div>

    <!-- 横向时间轴：5 节点等宽（不按真实时间比例）；各时段专属配色——
         盘前蓝=计划筹备 / 盘中红=交易执行 / 午盘琥珀=休市过渡 / 盘后紫=数据结算 / 复盘绿=复盘沉淀 -->
    <div class="bg-card border border-border rounded-lg p-2 grid grid-cols-2 md:grid-cols-5 gap-1.5">
      <button v-for="p in PHASES" :key="p.key"
              class="relative rounded-lg px-2 py-2.5 text-center transition-all border"
              :class="nodeClass(p.key)"
              @click="selectPhase(p.key)">
        <!-- 徽标：未决策教练卡数（当天） / 简报降级 -->
        <span v-if="badge(p.key)" class="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1
                                        rounded-full text-[10px] leading-[18px] text-center font-bold shadow"
              :class="p.key === 'premarket' && briefDegraded ? 'bg-amber-500 text-black' : 'bg-red-500 text-white'">
          {{ badge(p.key) }}
        </span>
        <div class="text-sm font-bold tracking-wide" :class="PHASE_STYLE[p.key].text">{{ p.label }}</div>
        <div class="text-[11px] font-mono text-muted mt-0.5">{{ p.time }}</div>
        <!-- 温和自动跟随提示：用户停在别处而实时阶段已推进 -->
        <div v-if="isToday && livePhase === p.key && selectedPhase !== p.key"
             class="text-[10px] mt-1 animate-pulse" :class="PHASE_STYLE[p.key].text">● 已进入，点击切换</div>
      </button>
    </div>

    <!-- 主体：主工作区（随阶段切换/懒加载） + 常驻右栏 -->
    <div class="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-4 items-start">
      <!-- ── 主工作区 ── -->
      <div class="space-y-4 min-w-0">
        <!-- 回放模式：当日聚合视图（Top50/简报/日报/教练历史） -->
        <template v-if="isReplay">
          <div v-if="dayLoading" class="bg-card border border-border rounded-lg p-6 text-muted text-sm">加载中…</div>
          <template v-else>
            <div id="wb-top50" class="bg-card border border-border rounded-lg p-4">
              <div class="text-sm font-semibold mb-2">评分快照 Top50（{{ selectedDate }}）</div>
              <div v-if="!replay.top50.length" class="text-muted text-xs">—（当日无快照）</div>
              <div v-else class="grid grid-cols-2 md:grid-cols-3 gap-1 text-xs">
                <div v-for="r in replay.top50" :key="r.code"
                     class="flex items-center justify-between border border-border/60 rounded px-2 py-1">
                  <span class="truncate">
                    <a :href="stockHref(r.code)" target="_blank" class="hover:text-accent">{{ r.name || r.code }}</a>
                    <a :href="xqUrl(r.code)" target="_blank" class="text-muted font-mono ml-1" title="雪球">{{ r.code }}</a>
                  </span>
                  <span class="font-mono font-semibold ml-2">{{ r.total_score }}</span>
                </div>
              </div>
            </div>
            <div v-for="ph in ['premarket', 'postmarket']" :key="ph"
                 id="wb-brief" class="bg-card border border-border rounded-lg p-4">
              <div class="text-sm font-semibold mb-2">{{ ph === 'premarket' ? '盘前简报' : '盘后简报' }}</div>
              <div v-if="!replay.briefs[ph]" class="text-muted text-xs">—（当日未生成）</div>
              <div v-else class="md-body" v-html="renderMd(replay.briefs[ph])"></div>
            </div>
            <div id="wb-report" class="bg-card border border-border rounded-lg p-4">
              <div class="text-sm font-semibold mb-2">A股日报</div>
              <div v-if="!replay.report_md" class="text-muted text-xs">—（当日未生成）</div>
              <div v-else class="md-body" v-html="renderMd(replay.report_md)"></div>
            </div>
            <div id="wb-coach" class="bg-card border border-border rounded-lg p-4">
              <div class="text-sm font-semibold mb-2">教练卡历史（{{ replay.coach.length }} 条）</div>
              <div v-if="!replay.coach.length" class="text-muted text-xs">—</div>
              <div v-for="a in replay.coach" :key="a.id" class="border-b border-border/50 py-2 text-xs">
                <div class="flex gap-2 items-center">
                  <span class="font-mono text-muted">{{ (a.alert_time || '').slice(0, 5) }}</span>
                  <span class="font-semibold">{{ a.label }}</span>
                  <span v-if="a.code">{{ a.code }} {{ a.name }}</span>
                  <span class="ml-auto" :class="a.executed === 'yes' ? 'text-emerald-400'
                        : a.executed === 'no' ? 'text-amber-400' : 'text-muted'">
                    {{ a.executed === 'yes' ? '已执行' : a.executed === 'no' ? '已放弃' : '未决策' }}
                  </span>
                </div>
                <div class="text-muted mt-1 whitespace-pre-wrap">{{ firstLine(a.message) }}</div>
              </div>
            </div>
          </template>
        </template>

        <!-- 实时模式 · ① 盘前（阅读模式） -->
        <template v-else-if="selectedPhase === 'premarket'">
          <!-- ★ 2026-09-25 用户反馈：数据中心(旧首页)的宏观方向/规则标签/市场环境整合进来 -->
          <!-- ★ A3 情绪预判卡 v0（近似口径，B1 官方数据后替换） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">情绪预判
                <span class="text-[10px] text-muted font-normal">（昨日涨停表现/连板高度，近似口径）</span>
                <span v-if="emotion?.trading_day === false"
                      class="ml-1 px-1 rounded bg-amber-500/15 text-amber-400 text-[10px]">休市日·显示最近交易日数据</span></div>
              <span class="text-sm font-bold"
                    :class="emotion?.verdict === '亢奋' ? 'text-red-400' : emotion?.verdict === '冰点' ? 'text-emerald-400' : 'text-amber-300'">
                {{ emotion?.verdict || '—' }}</span>
            </div>
            <div v-if="!emotion" class="text-muted text-xs">—（加载失败）</div>
            <div v-else class="grid grid-cols-4 gap-2 text-center text-xs">
              <div><div class="text-lg font-bold text-red-400 font-mono">{{ emotion.limit_up }}</div><div class="text-muted text-[10px]">涨停</div></div>
              <div><div class="text-lg font-bold text-emerald-400 font-mono">{{ emotion.limit_down }}</div><div class="text-muted text-[10px]">跌停</div></div>
              <div><div class="text-lg font-bold font-mono">{{ emotion.max_streak }}</div><div class="text-muted text-[10px]">连板高度</div></div>
              <div><div class="text-lg font-bold font-mono" :class="pctClass(emotion.prev_limit_today_pct)">
                {{ fmtPct(emotion.prev_limit_today_pct) }}</div><div class="text-muted text-[10px]">昨日涨停今日</div></div>
            </div>
            <div class="text-[10px] text-muted mt-2">判读：情绪决定今天"接力的强更强 / 分歧 / 退潮"，对应降低或提高买入标准。</div>
          </div>

          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">宏观与环境（独立信号，不进个股评分）</div>
              <span v-if="macro && macro.locked" class="text-[10px] text-accent">
                今日锁定 · 生成于 {{ (macro.generated_at || '').slice(11, 16) }}
              </span>
            </div>
            <div v-if="macroErr" class="text-muted text-xs">—（加载失败：{{ macroErr }}）</div>
            <div v-else-if="!macro" class="text-muted text-xs">加载中…</div>
            <template v-else>
              <!-- 分节一：宏观方向 + 市场环境 -->
              <div class="flex items-start gap-8 flex-wrap">
                <div class="flex items-center gap-3">
                  <span class="text-4xl font-bold font-mono leading-none"
                        :class="dirColor(macro.direction?.level, macro.direction?.score)">
                    {{ macro.direction?.score ?? '—' }}
                  </span>
                  <div>
                    <div class="text-sm font-semibold"
                         :class="dirColor(macro.direction?.level, macro.direction?.score)">
                      宏观方向 · {{ macro.direction?.level || '—' }}
                    </div>
                    <div class="text-[11px] text-muted mt-0.5">{{ macro.direction?.advisory || '' }}</div>
                  </div>
                </div>
                <div class="w-px self-stretch bg-border hidden sm:block"></div>
                <div class="flex items-center gap-3">
                  <span class="text-4xl font-bold font-mono leading-none">{{ temperature?.temperature ?? '—' }}</span>
                  <div>
                    <div class="text-sm font-semibold">市场环境 · {{ temperature?.level || '—' }}</div>
                    <div class="text-[11px] text-muted mt-0.5">{{ temperature?.advisory || '' }}</div>
                    <div class="text-[10px] text-muted">0~100，越高越亢奋</div>
                  </div>
                </div>
              </div>
              <div class="flex flex-wrap gap-1 mt-3">
                <span v-for="t in (macro.tags_bull || [])" :key="'b' + t"
                      class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">{{ t }}</span>
                <span v-for="t in (macro.tags_bear || [])" :key="'s' + t"
                      class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20">{{ t }}</span>
              </div>

              <div class="w-px h-12 bg-border"></div>
              <!-- 事件诊断（快讯 LLM，独立信号） -->
              <div class="flex items-center gap-2 min-w-[240px] flex-1">
                <div>
                  <div class="text-sm font-bold"
                       :class="flashDiag?.correlation_diagnosis?.correlation_state === 'D状态'
                               ? 'text-amber-400' : 'text-gray-100'">
                    {{ flashDiag?.correlation_diagnosis?.correlation_state || '无法判断' }}
                  </div>
                  <div class="text-[10px] text-muted">事件诊断 · {{ flashDiag?.correlation_diagnosis?.d_state_type || '不适用' }}</div>
                </div>
                <span class="text-gray-300 flex-1 min-w-[180px] leading-relaxed text-xs">
                  {{ flashDiag?.dominant_narrative?.narrative || flashDiag?.market_mood || '—' }}
                </span>
                <span class="text-muted text-xs whitespace-nowrap">
                  仓位 <b class="text-accent">{{ flashDiag?.daily_strategy?.overall_position || '—' }}</b>
                </span>
                <router-link target="_blank" to="/monitor" class="text-accent hover:underline text-xs whitespace-nowrap">详情</router-link>
              </div>
            </template>
          </div>
          <!-- ★ A2 隔夜与今日（财经日历；公告/解禁类待 C1 数据源） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">隔夜与今日（财经日历）</div>
              <router-link target="_blank" to="/calendar" class="text-xs text-accent hover:underline">完整日历</router-link>
            </div>
            <div v-if="calendarErr" class="text-muted text-xs">—（{{ calendarErr }}）</div>
            <div v-else-if="!calendarToday.length" class="text-muted text-xs">—（今日无事件或数据未返回）</div>
            <div v-for="(e, i) in calendarToday" :key="i" class="text-xs border-b border-border/40 py-1">
              <span class="font-mono text-muted mr-2">{{ (e.time || e.date || '').slice(0, 16) }}</span>
              {{ e.title || e.event || e.content || e.name || '—' }}
            </div>
          </div>

          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">决策简报（盘前）</div>
              <router-link target="_blank" to="/report" class="text-xs text-accent hover:underline">完整简报</router-link>
            </div>
            <div v-if="briefErr" class="text-muted text-xs">—（加载失败：{{ briefErr }}）</div>
            <div v-else-if="!briefMd" class="text-muted text-xs">加载中…</div>
            <div v-else class="md-body" v-html="renderMd(briefMd)"></div>
          </div>
          <div class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
            今日教练卡操作在右栏待办完成（执行/放弃均回写供复盘）；开盘确认 9:35 由系统自动执行。
          </div>
        </template>

        <!-- 实时模式 · ② 盘中 / ③ 午盘（执行模式：大字、少文字）
             ★ 2026-09-25 用户要求：指数大字卡删除——与顶栏重复，指数保留顶栏常驻 -->
        <template v-else-if="selectedPhase === 'intraday' || selectedPhase === 'midday'">
          <!-- ★ A1 盘中看盘序（框架：指数→涨跌家数/涨跌停→成交额→情绪） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div v-if="emotion && emotion.trading_day === false"
                 class="text-[11px] text-amber-400 mb-2">⚠ 今日休市——以下为最近交易日快照数据，非实时</div>
            <div class="grid grid-cols-3 md:grid-cols-6 gap-2 text-center">
              <div><div class="text-[10px] text-muted">上涨</div>
                <div class="text-base font-bold text-red-400 font-mono">{{ overview.stats?.up_count ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted">下跌</div>
                <div class="text-base font-bold text-emerald-400 font-mono">{{ overview.stats?.down_count ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted">涨停</div>
                <div class="text-base font-bold text-red-400 font-mono">{{ overview.stats?.limit_up ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted">跌停</div>
                <div class="text-base font-bold text-emerald-400 font-mono">{{ overview.stats?.limit_down ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted">两市成交额</div>
                <div class="text-base font-bold font-mono text-gray-200">
                  {{ overview.stats?.total_amount ? (overview.stats.total_amount / 1e8).toFixed(0) + '亿' : '—' }}</div></div>
              <div><div class="text-[10px] text-muted">情绪判读</div>
                <div class="text-base font-bold"
                     :class="emotion?.verdict === '亢奋' ? 'text-red-400' : emotion?.verdict === '冰点' ? 'text-emerald-400' : 'text-amber-300'">
                  {{ emotion?.verdict || '—' }}</div></div>
            </div>
            <div v-if="emotion" class="text-[11px] text-muted mt-2 border-t border-border/40 pt-2">
              昨日涨停 {{ emotion.prev_limit_count }} 只 · 今日平均表现
              <b :class="pctClass(emotion.prev_limit_today_pct)">{{ fmtPct(emotion.prev_limit_today_pct) }}</b>（赚钱效应）
              · 连板高度 <b class="text-gray-200 font-mono">{{ emotion.max_streak }}</b>
              <template v-if="emotion.leader">（{{ emotion.leader_name || emotion.leader }}）</template>
              <span class="text-[10px]">· {{ emotion.note }}</span>
            </div>
          </div>

          <!-- ★ B2 涨停复盘（zzshare：连板梯队/涨停清单；匿名限流时占位） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">涨停梯队与清单
              <span class="text-[10px] text-muted font-normal">（zzshare 口径，数据空=限流/非交易日）</span></div>
            <div v-if="(limitReview.steps || []).length === 0 && (limitReview.stocks || []).length === 0"
                 class="text-muted text-xs">—（当日无复盘数据）</div>
            <template v-else>
              <div v-if="(limitReview.steps || []).length" class="flex flex-wrap gap-1.5 mb-2">
                <span v-for="(s, i) in limitReview.steps.slice(0, 10)" :key="i"
                      class="px-1.5 py-0.5 rounded text-[11px] bg-rose-500/10 text-rose-300 border border-rose-500/25">
                  {{ Object.values(s).slice(0, 3).join(' · ') }}
                </span>
              </div>
              <div v-if="(limitReview.stocks || []).length" class="text-xs space-y-0.5">
                <div v-for="(s, i) in limitReview.stocks.slice(0, 8)" :key="i"
                     class="flex gap-2 border-b border-border/30 py-0.5">
                  <span class="text-muted font-mono">{{ Object.values(s)[0] }}</span>
                  <span class="truncate">{{ Object.values(s).slice(1, 4).join(' · ') }}</span>
                </div>
              </div>
            </template>
          </div>

          <!-- ★ A1 主线板块 Top5（当日板块快照） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">板块涨幅 Top5</div>
              <router-link target="_blank" to="/sector" class="text-xs text-accent hover:underline">板块详情</router-link>
            </div>
            <div v-if="!sectorTop.length" class="text-muted text-xs">—（当日无板块快照）</div>
            <div v-else class="space-y-1 text-xs">
              <div v-for="(s, i) in sectorTop" :key="i" class="flex items-center gap-2 border-b border-border/40 py-1">
                <span class="w-4 text-muted font-mono">{{ i + 1 }}</span>
                <span class="font-semibold">{{ s.industry || s.name || s.code || '—' }}</span>
                <span class="ml-auto font-mono" :class="pctClass(s.pct_change ?? s.change_pct)">
                  {{ signNum(s.pct_change ?? s.change_pct) }}%</span>
              </div>
            </div>
          </div>

          <!-- ★ Phase 2：教练卡盘中镜像（右栏为主，此处直达） -->
          <div v-if="todoList.length" class="bg-card border border-amber-500/40 rounded-lg p-4">
            <div class="text-sm font-semibold mb-1">待执行决策（{{ todoList.length }}）</div>
            <TodoCard v-for="a in todoList" :key="a.id" :alert="a" @done="loadCoach" />
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">持仓状态（{{ radarSummary.n || 0 }} 只 ·
                风险 {{ radarSummary.risk || 0 }} / 机会 {{ radarSummary.opportunity || 0 }}）</div>
              <span v-if="radarAsOf" class="text-[10px] text-muted font-mono">数据时点 {{ radarAsOf.slice(11, 16) }}</span>
            </div>
            <div v-if="radarErr" class="text-muted text-xs">—（加载失败：{{ radarErr }}）</div>
            <div v-else-if="!radarItems.length" class="text-muted text-xs">
              —（当前无持仓记录；新增后由写入方主动失效缓存，立即可见）
            </div>
            <div v-else class="space-y-2">
              <!-- ★ 2026-09-25 用户反馈：丰富持仓行——此前只显示名称+盈亏（reasons 字段名
                   写错，实际是 alerts），radar 的价格/今日/主力/评分/闸门/观察池全没用上 -->
              <div v-for="it in radarItems" :key="it.code"
                   class="border border-border/60 rounded-lg px-3 py-2.5">
                <!-- 主行：名称 + 盈亏大字 -->
                <div class="flex items-center gap-2 flex-wrap">
                  <a :href="stockHref(it.code)" target="_blank"
                     class="font-semibold hover:text-accent">{{ it.name || it.code }}</a>
                  <a :href="xqUrl(it.code)" target="_blank"
                     class="text-muted text-xs font-mono hover:text-accent" title="雪球">{{ it.code }}</a>
                  <span v-if="it.industry"
                        class="text-[10px] px-1 rounded bg-background border border-border text-muted">{{ it.industry }}</span>
                  <span class="ml-auto text-2xl font-bold font-mono"
                        :class="pctClass(it.pnl_pct)">{{ fmtPct(it.pnl_pct) }}</span>
                </div>
                <!-- 价格行 -->
                <div class="flex items-center gap-2 flex-wrap mt-1 text-xs">
                  <span class="font-mono">现价 {{ it.price ?? '—' }}</span>
                  <span class="font-mono" :class="pctClass(it.day_pct)">今日 {{ signNum(it.day_pct) }}%</span>
                  <span v-if="it.mv" class="text-muted">市值 {{ fmtNum(it.mv) }} 元</span>
                  <span v-if="it.drop_from_high != null && it.drop_from_high <= -3"
                        class="text-amber-400">距日内高 {{ it.drop_from_high }}%</span>
                </div>
                <!-- 标签行：主力 / 评分排名 / 观察池 / 闸门 / 战法 -->
                <div class="flex items-center gap-1.5 flex-wrap mt-1 text-[11px]">
                  <span v-if="it.phase_cn" class="px-1 rounded"
                        :class="it.signal === 'distribution' ? 'bg-red-500/15 text-red-400'
                                : it.signal === 'accum' ? 'bg-emerald-500/15 text-emerald-400'
                                : 'bg-background border border-border text-muted'">主力·{{ it.phase_cn }}</span>
                  <span v-if="it.score != null" class="text-muted">
                    评分 <b class="text-gray-200 font-mono">{{ it.score }}</b>
                    <template v-if="it.rank_pos">（{{ it.rank_date }} 榜单第 {{ it.rank_pos }}）</template>
                  </span>
                  <span v-if="it.in_watch" class="text-sky-400">观察池</span>
                  <span v-if="it.ready_label" class="text-muted">闸门 {{ it.ready_label }}</span>
                  <span v-for="s in (Array.isArray(it.strategies) ? it.strategies : [])" :key="s"
                        class="px-1 rounded bg-accent/10 text-accent border border-accent/30">{{ s }}</span>
                </div>
                <!-- 风险/机会提示 -->
                <div v-if="(it.alerts || []).length" class="mt-1 space-y-0.5">
                  <div v-for="(a, j) in it.alerts" :key="j" class="text-[11px]"
                       :class="a.level === 'risk' ? 'text-red-400' : 'text-emerald-400'">
                    {{ a.level === 'risk' ? '⚠' : '✦' }} {{ a.text }}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- 实时模式 · ④ 盘后（阅读模式） -->
        <template v-else-if="selectedPhase === 'postmarket'">
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">评分榜 Top10</div>
              <router-link target="_blank" to="/score" class="text-xs text-accent hover:underline">查看完整榜</router-link>
            </div>
            <div v-if="topErr" class="text-muted text-xs">—（加载失败）</div>
            <div v-else-if="!topItems.length" class="text-muted text-xs">—（暂无数据）</div>
            <div v-else class="space-y-1 text-xs">
              <div v-for="(r, i) in topItems" :key="r.code"
                   class="flex items-center gap-2 border-b border-border/40 py-1.5">
                <span class="w-6 text-muted font-mono">{{ i + 1 }}</span>
                <a :href="stockHref(r.code)" target="_blank"
                   class="font-semibold hover:text-accent">{{ r.name || r.code }}</a>
                <a :href="xqUrl(r.code)" target="_blank"
                   class="text-muted font-mono hover:text-accent" title="雪球">{{ r.code }}</a>
                <span v-if="r.change_pct != null" class="font-mono" :class="pctClass(r.change_pct)">
                  {{ signNum(r.change_pct) }}%
                </span>
                <span class="ml-auto font-mono font-bold" :class="scoreClass(r.total_score)">
                  {{ r.total_score }}
                </span>
              </div>
            </div>
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">观察池</div>
              <router-link target="_blank" to="/score" class="text-xs text-accent hover:underline">详情</router-link>
            </div>
            <div v-if="gwErr" class="text-muted text-xs">—（加载失败）</div>
            <div v-else class="text-xs text-muted">
              候选 {{ gwItems.length }} 只<span v-if="gwItems.length">，前三：
              {{ gwItems.slice(0, 3).map(x => x.name || x.code).join('、') }}</span>
            </div>
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">今日系统时间线</div>
            <div v-if="!pushItems.length" class="text-muted text-xs">—</div>
            <div v-for="(p, i) in pushItems" :key="i" class="text-xs border-b border-border/40 py-1.5">
              <span class="font-mono text-muted mr-2">{{ (p.ts || '').slice(11, 16) }}</span>
              <span class="font-semibold">{{ p.title }}</span>
              <span class="text-muted ml-2">{{ firstLine(p.content) }}</span>
            </div>
          </div>
        </template>

        <!-- 实时模式 · ⑤ 复盘（阅读模式） -->
        <template v-else>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">执行一致性</div>
              <router-link target="_blank" to="/coach" class="text-xs text-accent hover:underline">教练页 →</router-link>
            </div>
            <!-- ★ 2026-09-25 用户反馈：不再裸奔 JSON，按指标渲染 -->
            <div v-if="!consistency" class="text-muted text-xs">—（暂无数据）</div>
            <template v-else>
              <div class="flex items-center gap-6 flex-wrap">
                <div>
                  <div class="text-3xl font-bold font-mono"
                       :class="(consistency.exec_rate_pct || 0) >= 60 ? 'text-emerald-400' : 'text-amber-300'">
                    {{ consistency.exec_rate_pct ?? '—' }}%
                  </div>
                  <div class="text-[11px] text-muted">执行率（分母=已决策）</div>
                </div>
                <div class="text-xs space-y-0.5">
                  <div>窗口 {{ consistency.window_days }} 天 · 推送 <b>{{ consistency.pushed_total }}</b> 条</div>
                  <div>
                    已执行 <b class="text-emerald-400">{{ consistency.executed }}</b> ·
                    已放弃 <b class="text-amber-300">{{ consistency.abandoned }}</b> ·
                    未响应 <b class="text-muted">{{ consistency.ignored }}</b>
                    <span v-if="consistency.abandon_rate_pct != null">
                      （放弃率 {{ consistency.abandon_rate_pct }}%）
                    </span>
                  </div>
                </div>
              </div>
              <div class="text-[11px] text-muted mt-1">{{ consistency.note }}</div>
            </template>
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">决策简报（盘后）</div>
            <div v-if="!briefMd" class="text-muted text-xs">—（未生成）</div>
            <div v-else class="md-body" v-html="renderMd(briefMd)"></div>
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">A股日报</div>
              <router-link target="_blank" to="/report" class="text-xs text-accent hover:underline">完整日报 →</router-link>
            </div>
            <div v-if="!reportMd" class="text-muted text-xs">—（未生成）</div>
            <div v-else class="md-body" v-html="renderMd(reportMd)"></div>
          </div>
        </template>
      </div>

      <!-- ── 常驻右栏（全天不变，不被时间轴切走） ── -->
      <aside class="space-y-4 min-w-0">
        <!-- 待办：未决策教练卡 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <div class="flex items-center justify-between mb-2">
            <div class="text-sm font-semibold">
              待办 · 未决策教练卡
              <span v-if="todoCount" class="ml-1 px-1.5 rounded-full bg-red-500 text-white text-[10px]">{{ todoCount }}</span>
            </div>
            <router-link target="_blank" to="/coach" class="text-xs text-accent hover:underline">教练</router-link>
          </div>
          <div v-if="coachErr" class="text-muted text-xs">—（加载失败）</div>
          <div v-else-if="!todayCoach.length" class="text-muted text-xs">
            {{ isReplay ? '当日无教练卡' : '今天暂无教练卡 ✓' }}
          </div>
          <!-- ★ Phase 2：当天卡可交互（执行/放弃回写），回放只读带状态徽标 -->
          <TodoCard v-for="a in todayCoach" :key="a.id" :alert="a" :readonly="isReplay" @done="loadCoach" />
        </div>

        <!-- 持仓摘要 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <div class="flex items-center justify-between mb-2">
            <div class="text-sm font-semibold">持仓摘要</div>
            <router-link target="_blank" to="/paper" class="text-xs text-accent hover:underline">模拟盘</router-link>
          </div>
          <div v-if="radarErr" class="text-muted text-xs">—（加载失败）</div>
          <template v-else>
            <div class="grid grid-cols-3 gap-2 text-center text-xs mb-2">
              <div><div class="text-lg font-bold font-mono">{{ radarSummary.n || 0 }}</div><div class="text-muted">持仓</div></div>
              <div><div class="text-lg font-bold font-mono text-red-400">{{ radarSummary.risk || 0 }}</div><div class="text-muted">风险</div></div>
              <div><div class="text-lg font-bold font-mono text-emerald-400">{{ radarSummary.opportunity || 0 }}</div><div class="text-muted">机会</div></div>
            </div>
            <div v-if="worstHolding" class="text-xs border-t border-border/40 pt-2">
              最差：<span class="font-semibold">{{ worstHolding.name || worstHolding.code }}</span>
              <span class="font-mono" :class="pctClass(worstHolding.pnl_pct)">{{ fmtPct(worstHolding.pnl_pct) }}</span>
            </div>
            <div v-if="radarNote" class="text-[10px] text-muted mt-1">{{ radarNote }}</div>
          </template>
        </div>

        <!-- 今日/当日系统推送摘要 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <div class="text-sm font-semibold mb-2">{{ isReplay ? selectedDate + ' 推送' : '今日雷达摘要' }}</div>
          <div v-if="pushConnErr" class="text-[10px] text-amber-400">连接中断（保留上次数据）</div>
          <div v-if="!pushItems.length" class="text-muted text-xs">—</div>
          <div v-for="(p, i) in pushItems.slice(0, 5)" :key="i" class="text-xs border-b border-border/40 py-1.5">
            <div class="flex items-center gap-2">
              <span class="font-mono text-muted">{{ (p.ts || '').slice(11, 16) }}</span>
              <span class="font-semibold truncate">{{ p.title }}</span>
            </div>
            <div class="text-muted mt-0.5">{{ firstLine(p.content) }}</div>
          </div>
        </div>
      </aside>
    </div>

    <!-- 底部折叠：系统状态（运维，默认折叠） -->
    <details class="bg-card border border-border rounded-lg px-4 py-2 text-xs" :open="statusOpen"
             @toggle="onStatusToggle">
      <summary class="cursor-pointer font-semibold text-muted select-none">系统状态（数据新鲜度 / 内存 / 库体积）</summary>
      <div class="py-2 space-y-2" v-html="statusHtml"></div>
    </details>
  </div>
</template>

<script setup>
// ==============================================================================
// 一天流程工作台（2026-09-25，工作台开发文档 v1.0）
// 设计要点：
//   - 时间轴只切换主工作区；右栏（待办/持仓/推送摘要）全天常驻
//   - 懒加载：切到该阶段才拉数据；任何模块失败只显示该模块占位，不阻塞整页
//   - 回放：选历史日期 → 全页只读快照（/api/workbench/day，来自 ranking_history/
//     trader_briefs/daily_reports/coach_alerts）；轮询暂停
//   - 铁律：零评分/策略/回测引擎改动；只读接口已核（§6.1）+ workbench/day*（§6.2）
// ==============================================================================
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import TodoCard from '../components/workbench/TodoCard.vue'
import {
  getWorkbenchDayIndex, getWorkbenchDay,
  getTraderBrief, getCoachAlerts, getCoachConsistency, getCoachPlanExecutionRate,
  getPortfolioRadar, getGateWatch, getPushLog, getScoreTop,
  getMarketOverview, getMarketTemperature, getMarketRegime,
  getDailyReport, getSystemStatus, getSystemMemory, getDbUsage,
  getMacroDaily, getMacroSnapshot, getFlashDiagnosis, getCalendar,
  getMarketEmotion, getMarketLimitReview,
} from '../api'

const mdRenderer = new MarkdownIt({ html: false, linkify: false, breaks: true })

// ★ 2026-09-25 用户要求：工作台所有跳转一律新开标签页（原页轮询/状态不丢）；
//   个股"代码"→雪球（自动拼 SH/SZ/BJ 前缀），股票"名称"→本地个股详情页。
//   本项目为 hash 路由 ⇒ 站内链接直接用 '#/path' + target="_blank" 即可。
function xqUrl(code) {
  const c = String(code || '').replace(/\D/g, '').slice(0, 6)
  if (!c) return ''
  const pfx = c.startsWith('6') || c.startsWith('9') ? 'SH'
    : (c.startsWith('4') || c.startsWith('8') ? 'BJ' : 'SZ')
  return `https://xueqiu.com/S/${pfx}${c}`
}
const stockHref = (code) => `#/stock/${code}`

// ── 常量 ──
const PHASES = [
  { key: 'premarket', label: '盘前', time: '09:10' },
  { key: 'intraday', label: '盘中', time: '09:30' },
  { key: 'midday', label: '午盘', time: '11:30' },
  { key: 'postmarket', label: '盘后', time: '15:00' },
  { key: 'review', label: '复盘', time: '19:30' },
]
// 各时段专属配色（类名必须写成完整字面量，Tailwind JIT 才会生成）：
//   盘前蓝=计划筹备 / 盘中红=交易执行 / 午盘琥珀=休市过渡 / 盘后紫=数据结算 / 复盘绿=复盘沉淀
const PHASE_STYLE = {
  premarket:  { text: 'text-sky-400',    border: 'border-sky-500/60',    bg: 'bg-sky-500/10',    hover: 'hover:border-sky-500/40 hover:bg-sky-500/5' },
  intraday:   { text: 'text-rose-400',   border: 'border-rose-500/60',   bg: 'bg-rose-500/10',   hover: 'hover:border-rose-500/40 hover:bg-rose-500/5' },
  midday:     { text: 'text-amber-300',  border: 'border-amber-500/60',  bg: 'bg-amber-500/10',  hover: 'hover:border-amber-500/40 hover:bg-amber-500/5' },
  postmarket: { text: 'text-violet-400', border: 'border-violet-500/60', bg: 'bg-violet-500/10', hover: 'hover:border-violet-500/40 hover:bg-violet-500/5' },
  review:     { text: 'text-emerald-400', border: 'border-emerald-500/60', bg: 'bg-emerald-500/10', hover: 'hover:border-emerald-500/40 hover:bg-emerald-500/5' },
}

// ── 基础状态 ──
const route = useRoute()
const router = useRouter()
const todayStr = bjToday()
const selectedDate = ref(String(route.query.date || todayStr))
const isReplay = computed(() => selectedDate.value !== todayStr)
const selectedPhase = ref(String(route.query.phase || '') || computePhase())

const dayList = ref([{ date: todayStr, top50: false, briefs: [], report: false, coach_n: 0 }])
const dayLoading = ref(false)

// 模块数据（各自独立，失败互不影响）
const overview = ref({ indices: [], stats: {} })
const temperature = ref(null)
const regimeLabel = ref('—')
const briefMd = ref('')
const briefErr = ref('')
const briefDegraded = ref(false)
const coachList = ref([])
const coachErr = ref('')
const radarItems = ref([])
const radarSummary = ref({})
const radarNote = ref('')
const radarAsOf = ref('')
const radarErr = ref('')
const topItems = ref([])
const topErr = ref('')
const gwItems = ref([])
const gwErr = ref('')
const pushItems = ref([])
const pushConnErr = ref(false)
const consistency = ref(null)
const reportMd = ref('')
const statusHtml = ref('点击展开后加载…')
const statusOpen = ref(false)
// ★ 2026-09-25 用户反馈：盘前加"宏观与环境"卡（数据同数据中心：早盘锁定快照优先）
const macro = ref(null)
const macroErr = ref('')
// ★ 盘中看盘序/情绪（2026-09-25 交易方法论 A1/A3/B2）
const emotion = ref(null)
// 初始即给完整结构：模板首帧（接口返回前）会读取 steps/stocks，null 会崩
const limitReview = ref({ steps: [], stocks: [] })
const sectorTop = ref([])
const calendarToday = ref([])
const calendarErr = ref('')
// ★ 2026-09-25 盘中外盘四件套（A50/离岸/布伦特/纳指期货）——宏面板已在抓，
//   一次新浪批量请求 60s 缓存，工作台只是读取，零新增请求
const globals = ref({})
// 事件诊断（快讯 LLM 输出，用户要求并入宏观与环境卡）
const flashDiag = ref(null)

// 回放数据
const replay = ref({ top50: [], briefs: {}, report_md: null, coach: [] })

// ── 工具 ──
function bjToday() {
  const d = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000)
  return d.toISOString().slice(0, 10)
}
function computePhase() {
  // 与后端 current_phase 同分界（09:30/15:00），UI 细分 5 段（09:10 前归复盘）
  const d = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000)
  const m = d.getHours() * 60 + d.getMinutes()
  if (m >= 550 && m < 570) return 'premarket'
  if (m >= 570 && m < 690) return 'intraday'
  if (m >= 690 && m < 900) return 'midday'
  if (m >= 900 && m < 1170) return 'postmarket'
  return 'review'
}
const livePhase = ref(computePhase())
const renderMd = (md) => mdRenderer.render(String(md || ''))
const firstLine = (s) => String(s || '').split('\n').find(x => x.trim()) || ''
const fmtNum = (v) => (v == null ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 }))
const fmtPct = (v) => (v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`)
const signNum = (v) => (v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}`)
const pctClass = (v) => (Number(v) > 0 ? 'text-red-400' : Number(v) < 0 ? 'text-emerald-400' : 'text-muted')
const scoreClass = (v) => (Number(v) >= 65 ? 'text-red-400' : Number(v) >= 45 ? 'text-amber-300' : 'text-muted')

const topIndices = computed(() => (overview.value.indices || []).slice(0, 3))
const radarSummaryKeyed = computed(() => radarSummary.value || {})
// 组合待办：未决策教练卡（当天实时 / 回放日全部）
const todayCoach = computed(() => {
  if (isReplay.value) return replay.value.coach
  return coachList.value.filter(a => a.alert_date === todayStr)
})
const todoList = computed(() => todayCoach.value.filter(a => !a.executed))
const todoCount = computed(() => todoList.value.length)
// 外盘四件套行：从宏面板取 price/prev_close，pct 现算
const globalsRow = computed(() => {
  const defs = [
    { key: 'a50', label: 'A50期货' },
    { key: 'hstech', label: '恒生科技' },
    { key: 'usdcnh', label: '离岸USDCNH' },
    { key: 'brent', label: '布伦特' },
    { key: 'nasdaq', label: '纳指期货' },
    { key: 'us10y', label: '美债10Y' },
    { key: 'dxy', label: '美元指数' },
  ]
  return defs.map(({ key, label }) => {
    const v = globals.value[key] || {}
    const price = v.price
    const prev = v.prev_close
    const pct = (price && prev) ? (price / prev - 1) * 100 : null
    return { key, label, price: price ?? null, pct }
  })
})

const worstHolding = computed(() => {
  const arr = (radarItems.value || []).filter(x => x.pnl_pct != null)
  return arr.length ? arr.reduce((a, b) => (Number(a.pnl_pct) < Number(b.pnl_pct) ? a : b)) : null
})
const regimeClass = computed(() => ({
  '进攻': 'text-red-400', '震荡': 'text-amber-300',
  '震荡偏空（阴跌）': 'text-amber-400', '防御': 'text-emerald-400',
}[regimeLabel.value] || 'text-muted'))
const freshnessOk = ref(true)

// 徽标：盘前=简报降级 ⚠️；盘中/午盘/盘后=未决策教练卡数；复盘=未决策数
function badge(key) {
  if (key === 'premarket') return briefDegraded.value ? '⚠' : ''
  if (isReplay.value) return ''
  if (['intraday', 'midday', 'postmarket', 'review'].includes(key)) {
    // 未决策数只挂在"涵盖此刻"的节点上（盘中/午盘同属 intraday 后半，简化：全部挂同一数）
    return todoCount.value || ''
  }
  return ''
}
function nodeClass(key) {
  const st = PHASE_STYLE[key]
  const active = selectedPhase.value === key
  const idx = PHASES.findIndex(p => p.key === key)
  const future = isToday.value && idx > PHASES.findIndex(p => p.key === livePhase.value)
  return [
    // 激活：时段专属底色+边框；未激活：透明边框 + 悬停同色系浅底
    active ? `${st.bg} ${st.border} shadow-sm` : `border-transparent ${st.hover}`,
    future && !active ? 'opacity-40' : '',
  ]
}
const isToday = computed(() => !isReplay.value)

// ── 数据加载（每模块独立 try，失败只置错误位）──
async function safe(fn, setter, errSetter) {
  try {
    const { data } = await fn()
    errSetter && errSetter('')
    setter(data)
    return data
  } catch (e) {
    errSetter && errSetter((e && e.message) || '未知错误')
    return null
  }
}

async function loadOverview() {
  await safe(() => getMarketOverview(), d => { overview.value = d || {} })
}
async function loadTemperature() {
  try {
    const { data } = await getMarketTemperature()
    temperature.value = data || null
  } catch { /* 静默保留上次值 */ }
}
async function loadRegime() {
  try {
    const { data } = await getMarketRegime()
    regimeLabel.value = ({ offensive: '进攻', neutral: '震荡', neutral_bearish: '震荡偏空（阴跌）', defensive: '防御' })[data?.regime] || (data?.regime || '—')
  } catch { regimeLabel.value = '—' }
}
async function loadBrief(phase) {
  briefErr.value = ''
  try {
    const { data } = await getTraderBrief(false, phase)
    briefMd.value = data?.markdown || ''
    briefDegraded.value = !!data?.degraded
  } catch (e) {
    briefErr.value = (e && e.message) || '未知错误'
  }
}
async function loadCoach() {
  await safe(() => getCoachAlerts(100), d => { coachList.value = (d && d.data) || [] }, s => { coachErr.value = s })
}
async function loadRadar() {
  try {
    const { data } = await getPortfolioRadar()
    radarItems.value = (data && data.items) || []
    radarSummary.value = (data && data.summary) || {}
    radarNote.value = (data && data.note) || ''
    radarAsOf.value = (data && data.as_of) || ''
    radarErr.value = ''
  } catch (e) {
    radarErr.value = (e && e.message) || '未知错误'   // 保留上次数据（铁律 11）
  }
}
async function loadTop() {
  try {
    const { data } = await getScoreTop({ limit: 10 })
    topItems.value = (data && data.data) || []
    topErr.value = ''
  } catch (e) { topErr.value = (e && e.message) || '未知错误' }
}
async function loadGateWatch() {
  try {
    const { data } = await getGateWatch(80)
    gwItems.value = (data && data.items) || []
    gwErr.value = ''
  } catch (e) { gwErr.value = (e && e.message) || '未知错误' }
}
async function loadPush(date) {
  try {
    const { data } = await getPushLog(date || undefined, 80)
    pushItems.value = (data && data.items) || []
    pushConnErr.value = false
  } catch {
    pushConnErr.value = true        // 静默保留上次数据
  }
}
async function loadConsistency() {
  try {
    const { data } = await getCoachConsistency(30)
    consistency.value = data || null
  } catch { consistency.value = null }
}
async function loadReport(date) {
  try {
    const { data } = await getDailyReport(date)
    reportMd.value = data?.markdown || data?.md || ''
  } catch { reportMd.value = '' }
}
// 事件诊断（最新 LLM 油金相关性输出，与 Dashboard 同源）
async function loadFlashDiag() {
  try {
    const { data } = await getFlashDiagnosis({ limit: 1 })
    flashDiag.value = data?.latest?.output || null
  } catch { flashDiag.value = null }
}
// ★ A2 隔夜与今日：财经日历今日事件（防御性渲染，字段以接口实际返回为准）
async function loadCalendarToday() {
  try {
    const { data } = await getCalendar({ days: 1 })
    const items = (data && data.items) || []
    calendarToday.value = items.filter(it =>
      String(it.date || it.time || '').includes(todayStr)).slice(0, 6)
    calendarErr.value = ''
  } catch (e) {
    // ★ 2026-09-25：带出真实原因（Render 重部署窗口/冷启动超时是最常见场景），
    //   并自动重试一次——loadPhaseData 只在进盘前时调用，重试成本低
    calendarErr.value = (e && e.message) || '加载失败'
    try {
      await new Promise(r => setTimeout(r, 2500))
      const { data } = await getCalendar({ days: 1 })
      const items = (data && data.items) || []
      calendarToday.value = items.filter(it =>
        String(it.date || it.time || '').includes(todayStr)).slice(0, 6)
      calendarErr.value = ''
    } catch (e2) {
      calendarErr.value = (e2 && e2.message) || '重试仍失败'
    }
  }
}
// 情绪快照 v0（涨停/跌停/赚钱效应/连板高度/判读——近似口径，B1 官方数据后替换）
// 外盘四件套（A50 期货覆盖 SGX T+夜盘时段，CN 休市日常仍有报价——已实测中秋在报价）
async function loadGlobals() {
  try {
    const { data } = await getMacroSnapshot()
    // ★ /macro/snapshot 的品种在 data.panel 里（外层是 direction/derived/tags）
    globals.value = (data && data.panel) || {}
  } catch { globals.value = globals.value || {} }
}
async function loadEmotion() {
  try {
    const { data } = await getMarketEmotion()
    emotion.value = data || null
  } catch { emotion.value = null }
}
// 涨停复盘（zzshare：连板梯队 + 涨停清单；匿名可能空 → 占位）
async function loadLimitReview() {
  try {
    const { data } = await getMarketLimitReview(selectedDate.value !== todayStr ? selectedDate.value : undefined)
    limitReview.value = data || { steps: [], stocks: [] }
  } catch { limitReview.value = { steps: [], stocks: [] } }
}
// 主线板块 Top5（当日板块快照按涨跌幅降序，防御性渲染）
async function loadSectorTop() {
  try {
    const { data } = await getSectorSnapshot(todayStr, { limit: 5 })
    sectorTop.value = ((data && data.data) || []).slice(0, 5)
  } catch { sectorTop.value = [] }
}
// 宏观方向（早盘锁定快照优先，回退实时计算）——与 Dashboard.vue 同源同口径
async function loadMacro() {
  macroErr.value = ''
  try {
    const { data: dailyRes } = await getMacroDaily(todayStr)
    if (dailyRes && dailyRes.snapshot) {
      macro.value = { ...dailyRes.snapshot, locked: true }
      return
    }
    const { data } = await getMacroSnapshot()
    macro.value = { ...data, locked: false }
  } catch (e) {
    try {
      const { data } = await getMacroSnapshot()
      macro.value = { ...data, locked: false }
    } catch {
      macro.value = null
      macroErr.value = (e && e.message) || '未知错误'
    }
  }
}
// 宏观方向配色：多→红（A股红涨）、空→绿，与数据中心一致
function dirColor(level, score) {
  if (score != null && Number(score) !== 0) return Number(score) > 0 ? 'text-red-400' : 'text-emerald-400'
  const s = String(level || '')
  if (s.includes('多') || s.includes('热')) return 'text-red-400'
  if (s.includes('空') || s.includes('冷')) return 'text-emerald-400'
  return 'text-amber-300'
}
async function loadDayIndex() {
  try {
    const { data } = await getWorkbenchDayIndex(30)
    if (data?.days?.length) dayList.value = data.days
  } catch { /* 保留仅今天的兜底 */ }
}
async function loadReplayDay(date) {
  dayLoading.value = true
  try {
    const { data } = await getWorkbenchDay(date)
    replay.value = {
      top50: data?.top50 || [],
      briefs: data?.briefs || {},
      report_md: data?.report_md || null,
      coach: data?.coach || [],
    }
  } catch {
    replay.value = { top50: [], briefs: {}, report_md: null, coach: [] }
  }
  dayLoading.value = false
}
async function loadStatus() {
  // ★ 2026-09-25 用户反馈：/system/status 的 sources 是**列表**（[{name,status,lag_days}]），
  //   memory 字段为 rss_mb/peak_mb/used_pct，db-usage 为 {total_mb,limit_mb,used_pct}——
  //   此前按 dict 渲染出 "0: ok"、内存 —MB、库体积裸数字。
  let html = ''
  try {
    const { data } = await getSystemStatus()
    freshnessOk.value = !!data
    const src = data?.sources
    if (Array.isArray(src)) {
      const rows = src.slice(0, 12).map(v => {
        const st = v.status || '?'
        const cls = st === 'ok' ? 'text-emerald-400' : 'text-amber-400'
        const lag = (v.lag_days != null && v.lag_days !== 0) ? `，滞后 ${v.lag_days} 天` : ''
        return `<div>${v.name}: <b class="${cls}">${st}</b>${lag}</div>`
      }).join('')
      html += `<div class="font-semibold mb-1">${data.summary || ''}</div>` + (rows || '<div>—</div>')
    } else if (src && typeof src === 'object') {
      html += Object.entries(src).slice(0, 10)
        .map(([k, v]) => `<div>${k}: <b>${(v && (v.status || v.fresh || v.ok)) ?? '?'}</b></div>`).join('')
    } else {
      html += '<div>—</div>'
    }
  } catch {
    freshnessOk.value = false
    html += '<div>状态加载失败</div>'
  }
  try {
    const { data } = await getSystemMemory({ types: 0 })
    if (data && data.rss_mb != null) {
      html += `<div class="mt-1">内存: ${data.rss_mb}MB（峰值 ${data.peak_mb ?? '—'}MB）· 占用 ${data.used_pct ?? '—'}%</div>`
    }
  } catch { /* 忽略 */ }
  try {
    const { data } = await getDbUsage()
    if (data && typeof data === 'object' && data.total_mb != null) {
      html += `<div>库体积: ${data.total_mb} / ${data.limit_mb ?? '—'} MB（已用 ${data.used_pct ?? '—'}%）</div>`
    } else if (data != null) {
      html += `<div>库体积: ${data} MB</div>`
    }
  } catch { /* 忽略 */ }
  statusHtml.value = html || '<div>—</div>'
}
function onStatusToggle(e) {
  if (e.target.open && statusHtml.value.startsWith('点击')) loadStatus()
}

// ── 阶段切换与懒加载 ──
async function loadPhaseData(phase) {
  if (isReplay.value) return
  if (phase === 'premarket') { await loadBrief('premarket'); await Promise.all([loadMacro(), loadFlashDiag(), loadEmotion(), loadCalendarToday()]) }
  else if (phase === 'postmarket') { await loadTop(); await loadGateWatch(); }
  else if (phase === 'review') { await loadConsistency(); await loadBrief('postmarket'); await loadReport(todayStr); }
  if (phase === 'intraday' || phase === 'midday') {
    await Promise.all([loadEmotion(), loadLimitReview(), loadSectorTop(), loadGlobals()])
  }
  // intraday/midday 的 overview 与 radar 已由常驻轮询覆盖
}
function selectPhase(key) {
  selectedPhase.value = key
  syncQuery()
  loadPhaseData(key)
}
function syncQuery() {
  router.replace({ query: { ...route.query, phase: selectedPhase.value, date: selectedDate.value } })
}
function backToToday() {
  selectedDate.value = todayStr
  syncQuery()
  startPolling()
}

// ── 轮询（仅实时模式）──
let timers = []
function startPolling() {
  stopPolling()
  if (isReplay.value) return
  timers.push(setInterval(() => { loadCoach(); loadRadar() }, 60000))
  timers.push(setInterval(() => loadPush(todayStr), 120000))
  timers.push(setInterval(() => {
    livePhase.value = computePhase()          // 温和自动跟随：只更新提示，不硬切
    loadOverview(); loadTemperature(); loadEmotion(); loadGlobals()
  }, 120000))
}
function stopPolling() { timers.forEach(clearInterval); timers = [] }

// ── 日期切换（回放入口）──
watch(selectedDate, async (d) => {
  syncQuery()
  if (d && d !== todayStr) {
    stopPolling()
    await Promise.all([loadReplayDay(d), loadPush(d)])   // 回放右栏也切当日推送
  } else {
    await Promise.all([loadCoach(), loadPush(todayStr)])
    startPolling()
    loadPhaseData(selectedPhase.value)
  }
})

onMounted(async () => {
  await loadDayIndex()
  loadStatus()   // 顶栏新鲜度灯的数据源（失败自动置黄灯）
  // ★ 外盘四件套已移顶栏常驻（所有阶段可见）——挂载即加载 + 120s 轮询刷新
  loadGlobals()
  await Promise.all([loadOverview(), loadTemperature(), loadRegime(), loadCoach(), loadRadar(), loadPush(todayStr)])
  loadPhaseData(selectedPhase.value)
  startPolling()
})
onUnmounted(stopPolling)
</script>

<style scoped>
/* 日报同款 markdown 渲染样式（markdown-it 输出） */
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
.md-body :deep(li) { margin: 0.15rem 0; }
.md-body :deep(strong) { color: #f3f4f6; font-weight: 600; }
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
