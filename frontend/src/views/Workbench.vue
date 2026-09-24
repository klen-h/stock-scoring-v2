<template>
  <div class="fade-in space-y-4">
    <!-- 回放模式横幅（只读） -->
    <div v-if="isReplay"
         class="bg-amber-500/10 border border-amber-500/40 text-amber-300 rounded-lg px-4 py-2 text-sm flex items-center justify-between">
      <span>⏪ 回放 {{ selectedDate }} · 只读（右栏为该日警报历史）</span>
      <button class="text-xs underline" @click="backToToday">切回今天</button>
    </div>

    <!-- 顶栏：日期 · 市况/温度/指数 · 数据新鲜度（常驻） -->
    <div class="bg-card border border-border rounded-lg px-4 py-2.5 flex items-center gap-4 flex-wrap text-sm">
      <label class="flex items-center gap-2">
        <span class="text-muted text-xs">日期</span>
        <select v-model="selectedDate"
                class="bg-background border border-border rounded px-2 py-1 text-xs">
          <option v-for="d in dayList" :key="d.date" :value="d.date">
            {{ d.date === todayStr ? '今天 ' + d.date : d.date }}
          </option>
        </select>
      </label>

      <!-- 指数（overview 前三） -->
      <span v-for="ix in topIndices" :key="ix.name" class="flex items-center gap-1">
        <span class="text-muted text-xs">{{ ix.name }}</span>
        <span class="font-mono text-xs">{{ fmtNum(ix.price) }}</span>
        <span class="font-mono text-xs font-semibold" :class="pctClass(ix.change_pct)">
          {{ signNum(ix.change_pct) }}%
        </span>
      </span>

      <!-- 情绪温度 -->
      <span class="flex items-center gap-1">
        <span class="text-muted text-xs">情绪</span>
        <span v-if="temperature" class="font-mono text-xs"
              :class="pctClass(-(100 - Number(temperature.temperature || 50)))">
          {{ temperature.temperature ?? '—' }} {{ temperature.level || '' }}
        </span>
        <span v-else class="text-muted text-xs">—</span>
      </span>

      <!-- 市场状态 -->
      <span class="flex items-center gap-1">
        <span class="text-muted text-xs">市况</span>
        <span class="text-xs font-semibold" :class="regimeClass">{{ regimeLabel }}</span>
      </span>

      <!-- 数据新鲜度（点开看底部状态区） -->
      <span class="ml-auto flex items-center gap-1.5 cursor-pointer" @click="statusOpen = true">
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
                  <span class="truncate">{{ r.rank_pos }}. {{ r.name || r.code }}</span>
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
              <div class="flex items-center gap-6 flex-wrap mb-2">
                <div class="flex items-center gap-2">
                  <span class="text-3xl font-bold font-mono"
                        :class="dirColor(macro.direction?.level, macro.direction?.score)">
                    {{ macro.direction?.score ?? '—' }}
                  </span>
                  <div>
                    <div class="text-xs font-semibold"
                         :class="dirColor(macro.direction?.level, macro.direction?.score)">
                      宏观方向 · {{ macro.direction?.level || '—' }}
                    </div>
                    <div class="text-[11px] text-muted">{{ macro.direction?.advisory || '' }}</div>
                  </div>
                </div>
                <div class="flex items-center gap-2">
                  <span class="text-3xl font-bold font-mono">{{ temperature?.temperature ?? '—' }}</span>
                  <div>
                    <div class="text-xs font-semibold">市场环境 · {{ temperature?.level || '—' }}</div>
                    <div class="text-[11px] text-muted">{{ temperature?.advisory || '' }}</div>
                    <div class="text-[10px] text-muted">0~100，越高越亢奋</div>
                  </div>
                </div>
              </div>
              <div class="flex flex-wrap gap-1">
                <span v-for="t in (macro.tags_bull || [])" :key="'b' + t"
                      class="px-1.5 py-0.5 rounded text-[11px] bg-emerald-500/15 text-emerald-400 border border-emerald-500/20">{{ t }}</span>
                <span v-for="t in (macro.tags_bear || [])" :key="'s' + t"
                      class="px-1.5 py-0.5 rounded text-[11px] bg-red-500/15 text-red-400 border border-red-500/20">{{ t }}</span>
              </div>
            </template>
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">决策简报（盘前）</div>
              <router-link to="/report" class="text-xs text-accent hover:underline">完整简报 →</router-link>
            </div>
            <div v-if="briefErr" class="text-muted text-xs">—（加载失败：{{ briefErr }}）</div>
            <div v-else-if="!briefMd" class="text-muted text-xs">加载中…</div>
            <div v-else class="md-body" v-html="renderMd(briefMd)"></div>
          </div>
          <div class="bg-card border border-border rounded-lg p-4 text-xs text-muted">
            今日教练卡操作在右栏待办完成（执行/放弃均回写供复盘）；开盘确认 9:35 由系统自动执行。
          </div>
        </template>

        <!-- 实时模式 · ② 盘中 / ③ 午盘（执行模式：大字、少文字） -->
        <template v-else-if="selectedPhase === 'intraday' || selectedPhase === 'midday'">
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">市场警示</div>
            <div class="grid grid-cols-3 gap-3">
              <div v-for="ix in topIndices" :key="ix.name">
                <div class="text-muted text-xs">{{ ix.name }}</div>
                <div class="text-2xl font-bold font-mono" :class="pctClass(ix.change_pct)">
                  {{ signNum(ix.change_pct) }}%
                </div>
              </div>
            </div>
            <div v-if="overviewErr" class="text-muted text-xs mt-2">—（加载失败）</div>
          </div>
          <!-- ★ Phase 2：教练卡盘中镜像（右栏为主，此处直达） -->
          <div v-if="todoList.length" class="bg-card border border-amber-500/40 rounded-lg p-4">
            <div class="text-sm font-semibold mb-1">待执行决策（{{ todoList.length }}）</div>
            <TodoCard v-for="a in todoList" :key="a.id" :alert="a" @done="loadCoach" />
          </div>
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">持仓状态（{{ radarSummary.n || 0 }} 只 ·
              风险 {{ radarSummary.risk || 0 }} / 机会 {{ radarSummary.opportunity || 0 }}）</div>
            <div v-if="radarErr" class="text-muted text-xs">—（加载失败：{{ radarErr }}）</div>
            <div v-else-if="!radarItems.length" class="text-muted text-xs">—（当前无持仓记录）</div>
            <div v-else class="space-y-2">
              <div v-for="it in radarItems" :key="it.code"
                   class="flex items-center gap-3 border border-border/60 rounded px-3 py-2">
                <span class="font-semibold">{{ it.name || it.code }}</span>
                <span class="text-muted text-xs">{{ it.code }}</span>
                <span class="ml-auto text-xl font-bold font-mono"
                      :class="pctClass(it.pnl_pct)">{{ fmtPct(it.pnl_pct) }}</span>
                <span class="text-xs text-muted w-40 truncate text-right">
                  {{ firstLine((it.reasons && (it.reasons.risk || [])[0]) || (it.reasons && (it.reasons.opportunity || [])[0]) || '') }}
                </span>
              </div>
            </div>
          </div>
        </template>

        <!-- 实时模式 · ④ 盘后（阅读模式） -->
        <template v-else-if="selectedPhase === 'postmarket'">
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">评分榜 Top10</div>
              <router-link to="/score" class="text-xs text-accent hover:underline">查看完整榜 →</router-link>
            </div>
            <div v-if="topErr" class="text-muted text-xs">—（加载失败）</div>
            <div v-else-if="!topItems.length" class="text-muted text-xs">—（暂无数据）</div>
            <div v-else class="space-y-1 text-xs">
              <div v-for="(r, i) in topItems" :key="r.code"
                   class="flex items-center gap-2 border-b border-border/40 py-1.5">
                <span class="w-6 text-muted font-mono">{{ i + 1 }}</span>
                <router-link :to="'/stock/' + r.code" class="font-semibold hover:text-accent">
                  {{ r.name || r.code }}
                </router-link>
                <span class="text-muted font-mono">{{ r.code }}</span>
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
              <router-link to="/score" class="text-xs text-accent hover:underline">详情 →</router-link>
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
              <router-link to="/coach" class="text-xs text-accent hover:underline">教练页 →</router-link>
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
              <router-link to="/report" class="text-xs text-accent hover:underline">完整日报 →</router-link>
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
            <router-link to="/coach" class="text-xs text-accent hover:underline">教练 →</router-link>
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
            <router-link to="/paper" class="text-xs text-accent hover:underline">模拟盘 →</router-link>
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
  getMacroDaily, getMacroSnapshot,
} from '../api'

const mdRenderer = new MarkdownIt({ html: false, linkify: false, breaks: true })

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
const overviewErr = ref('')
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
  await safe(() => getMarketOverview(), d => { overview.value = d || {} }, s => { overviewErr.value = s })
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
// 宏观方向（早盘锁定快照优先，回退实时计算）——与 Dashboard.vue 同源同口径
async function loadMacro() {
  macroErr.value = ''
  try {
    const { data: dailyRes } = await getMacroDaily(todayStr())
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
  if (phase === 'premarket') { await loadBrief('premarket'); await loadMacro() }
  else if (phase === 'postmarket') { await loadTop(); await loadGateWatch(); }
  else if (phase === 'review') { await loadConsistency(); await loadBrief('postmarket'); await loadReport(todayStr); }
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
    loadOverview(); loadTemperature()
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
