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
        <!-- ★ 2026-09-25（用户："A 股开盘前若没总结外盘正常开市的时间，会漏点什么"）：
             外盘是 24h 连续的 ⇒ **同一个涨跌幅在不同时段含义完全不同**（A 股收盘后新涨的
             0.5% 需要消化；收盘前就有的 0.5% 昨天已反映）。故在此常显"**此刻哪些市场在开市**"
             —— 一眼看清手里这些数字是"活的"还是"上一收盘的静态值"。
             时段判定**复用后端 `market_clock`**（唯一实现），前端不复制逻辑。 -->
        <div v-if="macroClock" class="text-center px-1.5 border-l border-border/60"
             :title="`北京时间 ${macroClock.beijing_time}｜A股 9:30-15:00 · 港股 9:30-16:00 · 日经 8:00-14:00 · 美股 21:30-04:00`">
          <div class="text-[10px] text-muted">外盘开市</div>
          <div class="text-[11px] font-semibold leading-snug"
               :class="openMarkets.length ? 'text-emerald-400' : 'text-muted'">
            {{ openMarkets.length ? openMarkets.map(m => m.label).join('/') : '全部收盘' }}
          </div>
          <div class="text-[10px] text-muted font-mono">{{ macroClock.beijing_time }}</div>
        </div>
        <!-- ★ 2026-09-25 P1（用户："到了 A 股开盘，如果没总结外盘正常开市时间，会漏点什么"）：
             显示**自上次 A 股收盘以来**外盘的累计变化 —— 这才是"A 股开盘要消化的新增信息"。
             只按 |变化| 取前 2 项（顶栏防过载），全部项与口径说明放 title。
             ⚠️ 用 `change_pct`（相对昨结）做不到这件事：它含"A 股收盘前就已反映"的部分。 -->
        <div v-if="overnightItems.length" class="text-center px-1.5 border-l border-border/60"
             :title="overnightTitle">
          <div class="text-[10px] text-muted">隔夜{{ overnightBaseLabel }}</div>
          <div v-for="o in overnightItems.slice(0, 2)" :key="o.key"
               class="text-[11px] font-mono leading-snug" :class="pctClass(o.pct)">
            {{ o.label }} {{ signNum(o.pct) }}%
          </div>
        </div>
      </div>

      <!-- 情绪/市况模块（归并为一组） -->
      <div class="flex items-center gap-3 border-l border-border pl-4">
        <div class="text-center">
          <div class="text-lg font-bold font-mono leading-none"
               :class="levelColor(temperature?.level || '')">{{ temperature?.temperature ?? '—' }}</div>
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
        <span class="text-muted text-xs">数据新鲜度<template v-if="statusTime"> · 截至 {{ statusTime }}</template></span>
      </span>
    </div>

    <!-- 横向时间轴：**6 节点等宽、始终一行**（不按真实时间比例）；各时段专属配色——
         盘前蓝=计划筹备 / 竞价靛=过渡定稿 / 盘中红=交易执行 / 午盘琥珀=休市过渡 /
         盘后紫=数据结算 / 复盘绿=复盘沉淀
         ★ 2026-09-25：加「竞价」后节点变 6 个，而这里仍是 `md:grid-cols-5` ⇒ 第 6 个被挤到
           第二行（用户反馈"宽度缩小，一行展示就行，空间够的"）。⇒ 改 6 列并收窄内外间距：
           一行放得下 6 个，且 9:25「已进入」提示仍能显示（该提示会临时撑高，故纵向留 py-2）。
         ⚠️ 改节点数时**必须同步这里**（grid 列数 + 注释里的段数）。 -->
    <div class="bg-card border border-border rounded-lg p-1.5 grid grid-cols-3 md:grid-cols-6 gap-1">
      <button v-for="p in PHASES" :key="p.key"
              class="relative rounded-lg px-1.5 py-2 text-center transition-all border min-w-0"
              :class="nodeClass(p.key)"
              @click="selectPhase(p.key)">
        <!-- 徽标：未决策教练卡数（当天） / 简报降级 -->
        <span v-if="badge(p.key)" class="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1
                                        rounded-full text-[10px] leading-[18px] text-center font-bold shadow"
              :class="p.key === 'premarket' && briefDegraded ? 'bg-amber-500 text-black' : 'bg-red-500 text-white'">
          {{ badge(p.key) }}
        </span>
        <div class="text-sm font-bold tracking-wide truncate" :class="PHASE_STYLE[p.key].text">{{ p.label }}</div>
        <div class="text-[11px] font-mono text-muted mt-0.5">{{ p.time }}</div>
        <!-- 温和自动跟随提示：用户停在别处而实时阶段已推进
             ★ 2026-09-25 用户反馈："选择当前流转状态的时候高度会缩小" —— 根因就是这里：
               原为 `v-if="... && selectedPhase !== p.key"` ⇒ **一旦选中该节点，这行直接消失**
               ⇒ 该按钮比其它按钮矮一截，整条时间轴跟着抖动。
               ⇒ 改为"**始终渲染、选中时用 invisible 隐身**"（`visibility:hidden` 仍占位）
                  ⇒ 高度恒定，提示照旧只在未选中时可见。 -->
        <div v-if="isToday && livePhase === p.key"
             class="text-[10px] mt-1 truncate"
             :class="[PHASE_STYLE[p.key].text, selectedPhase === p.key ? 'invisible' : 'animate-pulse']">
          ● 已进入，点击切换</div>
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

        <!-- ★ 2026-09-25 用户："9:15–9:25 是盘前到盘中的关键过渡（竞价额、竞价涨幅榜、
             昨日强势股溢价），时间轴上 9:30 直接从盘前跳盘中，少了一环。"
             ⇒ 新增「竞价」视图：只用**已有**数据（`market_emotion.auction` + `prev_limit_today_pct`），
             零新增接口。当前覆盖"昨日强势股今日溢价/高开榜"；
             ⚠️ **竞价额/全市场竞价涨幅榜暂缺**（需后端扩展 `auction`，见 memory 待办）。 -->
        <template v-else-if="selectedPhase === 'auction'">
          <div class="bg-card border border-indigo-500/40 rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">竞价看板
                <span class="text-[10px] text-muted font-normal">（9:20 后不可撤单 · 9:25 定稿）</span></div>
              <span class="text-[10px] text-muted font-mono">{{ emotion?.as_of || '' }}</span>
            </div>
            <div v-if="!emotion" class="text-muted text-xs">—（加载失败）</div>
            <template v-else>
              <div class="grid grid-cols-3 gap-3 text-center text-xs mb-3">
                <div>
                  <div class="text-lg font-bold font-mono">{{ emotion.auction?.count ?? '—' }}</div>
                  <div class="text-muted text-[10px]">昨涨停今日高开数</div>
                </div>
                <div>
                  <div class="text-lg font-bold font-mono" :class="pctClass(emotion.auction?.avg_gap)">
                    {{ signNum(emotion.auction?.avg_gap) }}%</div>
                  <div class="text-muted text-[10px]">平均高开幅度</div>
                </div>
                <div>
                  <div class="text-lg font-bold font-mono" :class="pctClass(emotion.prev_limit_today_pct)">
                    {{ fmtPct(emotion.prev_limit_today_pct) }}</div>
                  <div class="text-muted text-[10px]">昨涨停今均（溢价）</div>
                </div>
              </div>
              <!-- ★ 2026-09-25 扩展：全市场竞价视角（用户要的"竞价额、竞价涨幅榜"）——
                   上面那块只覆盖"昨日涨停股"，这里补**全市场**高开/低开榜与家数统计。
                   ⚠️ 成交额是**截至 as_of 的累计值**（竞价时段即竞价额），文案已如实标注。 -->
              <div v-if="emotion.auction?.market_count" class="border-t border-border/40 mt-2 pt-2">
                <div class="text-[11px] text-muted mb-1.5">
                  全市场：高开 <b class="text-red-400 font-mono">{{ emotion.auction.up_open }}</b> 家 ·
                  低开 <b class="text-emerald-400 font-mono">{{ emotion.auction.down_open }}</b> 家 ·
                  平均高开 <b class="font-mono" :class="pctClass(emotion.auction.avg_gap_all)">{{ signNum(emotion.auction.avg_gap_all) }}%</b>
                  <template v-if="emotion.auction.total_amount_wan != null">
                    · 累计成交 <b class="text-accent font-mono">{{ fmtAmountWan(emotion.auction.total_amount_wan) }}</b>
                  </template>
                  <span class="text-[10px] cursor-help" :title="emotion.auction.amount_note">ⓘ</span>
                </div>
                <div class="mb-1 text-[10px] text-muted">高开榜 Top20（全市场）</div>
                <div class="flex flex-wrap gap-1">
                  <a v-for="g in (emotion.auction.market_top || [])" :key="'mt' + g.code"
                     :href="stockHref(g.code)" target="_blank"
                     class="px-1.5 py-0.5 rounded border border-border/60 font-mono text-[11px] hover:border-accent"
                     :class="g.gap_pct >= 0 ? 'text-red-400' : 'text-emerald-400'"
                     :title="`成交 ${fmtAmountWan(g.amount_wan)}`">
                    {{ g.name }} {{ signNum(g.gap_pct) }}%
                  </a>
                </div>
                <div class="mb-1 mt-2 text-[10px] text-muted">低开榜 Top20（全市场）</div>
                <div class="flex flex-wrap gap-1">
                  <a v-for="g in (emotion.auction.market_bottom || [])" :key="'mb' + g.code"
                     :href="stockHref(g.code)" target="_blank"
                     class="px-1.5 py-0.5 rounded border border-border/60 font-mono text-[11px] hover:border-accent text-emerald-400"
                     :title="`成交 ${fmtAmountWan(g.amount_wan)}`">
                    {{ g.name }} {{ signNum(g.gap_pct) }}%
                  </a>
                </div>
              </div>
              <div v-if="(emotion.auction?.top || []).length" class="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border/40">
                <span class="text-[10px] text-muted w-full mb-0.5">昨日涨停股高开 Top5（接力视角）</span>
                <a v-for="g in emotion.auction.top" :key="g.code" :href="stockHref(g.code)" target="_blank"
                   class="px-1.5 py-0.5 rounded border border-border/60 font-mono text-xs hover:border-accent"
                   :class="g.gap_pct >= 0 ? 'text-red-400' : 'text-emerald-400'">
                  {{ g.name }} {{ signNum(g.gap_pct) }}%
                </a>
              </div>
              <div v-else class="text-[11px] text-muted">
                —（暂无高开数据。竞价 9:25 定稿后本页有效；休市日无数据属正常）
              </div>
              <div class="text-[10px] text-muted mt-2 border-t border-border/40 pt-2">
                判读：昨涨停股**高开且不炸** ⇒ 接力情绪好；**低开或高开回落** ⇒ 分歧转弱。
                <a class="text-accent hover:underline cursor-pointer" @click="selectPhase('premarket')">
                  回看盘前决策卡</a>
              </div>
            </template>
          </div>
        </template>

        <!-- 实时模式 · ① 盘前（阅读模式） -->
        <template v-else-if="selectedPhase === 'premarket'">
          <!-- ★ 2026-09-25 用户反馈：数据中心(旧首页)的宏观方向/规则标签/市场环境整合进来 -->
          <!-- ★ A3 情绪预判卡 v0（近似口径，B1 官方数据后替换） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">情绪预判
                <span class="text-[10px] text-muted font-normal">（昨日涨停表现/连板高度，近似口径）</span>
                <!-- ★ 2026-09-25：原徽标写"显示最近交易日数据"，但休市时行情缓存为空 ⇒
                     实际输出的是 0（不是最近交易日快照）⇒ **徽标承诺与实际不符**。
                     改为按 `has_live` 如实说明：无实时行情 ⇒ 今日字段均不可用（显示"—"）。
                     ⚠️ 用 `=== false` 严格判断：旧后端没有该字段（undefined）时不误报。 -->
                <span v-if="emotion && emotion.has_live === false"
                      class="ml-1 px-1 rounded bg-amber-500/15 text-amber-400 text-[10px] cursor-help"
                      :title="`当前无实时行情（休市或行情源未刷新）⇒ 涨停/跌停/连板高度不可用（显示—）；可用于参考的历史价格截至 ${emotion.data_date || '—'}`">
                  无实时行情{{ emotion.data_date ? `（价格截至 ${emotion.data_date.slice(5)}）` : '' }}
                </span></div>
              <span class="text-sm font-bold"
                    :class="emotion?.verdict === '亢奋' ? 'text-red-400' : emotion?.verdict === '冰点' ? 'text-emerald-400' : 'text-amber-300'">
                {{ emotion?.verdict || '—' }}</span>
            </div>
            <div v-if="!emotion" class="text-muted text-xs">—（加载失败）</div>
            <div v-else class="grid grid-cols-4 gap-2 text-center text-xs">
              <!-- ★ 2026-09-25：三个"今日"字段在无行情时后端返回 None ⇒ 显示「—」
                   （此前后端返回 0，看起来像"今天一只都没涨停"，其实是"今天没开盘"） -->
              <div><div class="text-lg font-bold text-red-400 font-mono">{{ emotion.limit_up ?? '—' }}</div><div class="text-muted text-[10px]">涨停</div></div>
              <div><div class="text-lg font-bold text-emerald-400 font-mono">{{ emotion.limit_down ?? '—' }}</div><div class="text-muted text-[10px]">跌停</div></div>
              <div><div class="text-lg font-bold font-mono">{{ emotion.max_streak ?? '—' }}</div><div class="text-muted text-[10px]">连板高度</div></div>
              <!-- ★ 2026-09-25 用户问「昨日涨停今日 -1.16% 是什么意思」⇒ 原文案缺"平均"二字、
                   也不给股数，容易被读成"今天的涨跌幅是 -1.16%"。
                   口径：**昨日涨幅≥9.5% 的那批股票，今天的平均涨跌幅**（=赚钱效应）；
                   为正 ⇒ 接力意愿强（昨涨停今天还有人买）；为负 ⇒ 追涨者平均亏钱、情绪转弱。
                   列宽有限 ⇒ 只微调文案，完整解释放 title（悬停可见）。 -->
              <div :title="`昨日涨停的 ${emotion.prev_limit_count ?? '?'} 只股票，今天的**平均**涨跌幅 = ${fmtPct(emotion.prev_limit_today_pct)}（赚钱效应）：为正 ⇒ 接力意愿强；为负 ⇒ 追涨者平均亏钱、情绪转弱`">
                <div class="text-lg font-bold font-mono" :class="pctClass(emotion.prev_limit_today_pct)">
                  {{ fmtPct(emotion.prev_limit_today_pct) }}</div>
                <div class="text-muted text-[10px] cursor-help">昨涨停今均<template v-if="emotion.prev_limit_count">（{{ emotion.prev_limit_count }}只）</template></div>
              </div>
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
              <div v-if="macroSentiment" class="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-1 mt-2 pt-2 border-t border-border/40">
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
                  情绪温度计 {{ macroSentiment.score }}分/{{ macroSentiment.zone }}区（{{ macroSentiment.date }}）·
                  过热与过冷**同时出现**＝市场分歧明显；两项都在 80/20 之外时说明方向一致
                </div>
              </div>
            </template>
          </div>
          <!-- ★ P1 今日决策卡（规则引擎确定性输出；空状态给生成按钮，不静默） -->
          <div class="bg-card border border-accent/40 rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">今日决策卡
                <span class="text-[10px] text-muted font-normal">（规则引擎 · 无 LLM · 确定性输出）</span></div>
              <!-- ★ 2026-09-25：按钮**常显**（原先只在 dcErr 时出现）—— 决策卡只按当日缓存一次，
                   用户需要能随时主动重算（如盘前数据更新后）。 -->
              <button @click="loadDecisionCard()" :disabled="dcLoading"
                      class="px-2 py-0.5 rounded bg-accent/20 text-accent text-xs disabled:opacity-50">
                {{ dcLoading ? '生成中…' : (dcErr ? '生成今日决策卡' : '重新生成') }}</button>
            </div>
            <div v-if="dcLoading" class="text-muted text-xs">生成中…</div>
            <div v-else-if="dcErr" class="text-xs">
              <div class="text-red-400 mb-1">生成失败：{{ dcErr }}</div>
              <button class="px-2 py-0.5 rounded border border-border text-muted" @click="loadDecisionCard()">重试</button>
            </div>
            <template v-else-if="dc">
              <!-- 做不做（★ 2026-09-25：dc.stance 可能缺失 ⇒ 必须局部保护。
                   此前 dbdb3cf 给 dc.do/how_much/if_wrong/environment 都加了 `?.`，唯独漏了
                   stance ⇒ 模板渲染即抛 TypeError: Cannot read properties of undefined
                   (reading 'level')；它后面还紧跟 `.includes()`，连兜底机会都没有。
                   ⚠️ 为什么不把外层 `v-else-if="dc"` 收紧成 `dc && dc.stance`：那样 stance
                   缺失时**整卡都不渲染**，会把同块内仍有数据的"做什么/做多少/错了怎么办"
                   一并丢掉 —— 局部保护才是对的。 -->
              <template v-if="dc.stance">
                <div class="flex items-center gap-3 mb-2">
                  <span class="text-2xl font-bold"
                        :class="(dc.stance.level || '').includes('空仓') ? 'text-muted' : 'text-red-400'">{{ dc.stance.level || '—' }}</span>
                  <span class="text-xs text-muted">{{ dc.stance.why }}</span>
                </div>
              </template>
              <div v-else class="text-xs text-muted mb-2">暂无立场结论（决策卡缺 stance 字段）</div>
              <!-- 做什么 / 做多少 / 错了怎么办 -->
              <div class="text-xs space-y-1.5">
                <div>
                  <span class="text-muted">做什么：</span>
                  <b>{{ (dc.do?.whitelist || []).join('、') || '无白名单战法（推送静默）' }}</b>
                  <template v-if="(dc.do?.candidates || []).length">
                    · 候选 {{ dc.do.candidates.map(c => `${c.name} ${c.score}分`).join('、') }}
                  </template>
                </div>
                <!-- ★ 2026-09-25（用户需求 A）：**「为什么静默」的自解释**。
                     原先只显示"无白名单战法（推送静默）"⇒ 分不清是【市场不对】/【战法坏了】/
                     【系统故障】—— 三者处置完全不同（前两者什么都不用做，后者要修）。
                     数据是 `recommendation` 早已算好并落库的 `whitelist_state` ⇒ 零新增计算。 -->
                <div v-if="dc.strategy_quality?.available"
                     class="mt-1.5 pt-1.5 border-t border-border/50">
                  <div class="flex items-start gap-1.5 flex-wrap">
                    <span class="text-muted shrink-0">战法质量：</span>
                    <span class="text-[11px] leading-snug"
                          :class="dc.strategy_quality.whitelist?.length ? 'text-emerald-400' : 'text-amber-300'">
                      {{ dc.strategy_quality.why }}
                    </span>
                  </div>
                  <div v-if="dc.strategy_quality.regime" class="text-[10px] text-muted mt-0.5">
                    市场状态：<span class="text-gray-300">{{ dc.strategy_quality.regime.cn }}</span>
                    （{{ dc.strategy_quality.regime.date }} · 评分
                    <span class="font-mono">{{ dc.strategy_quality.regime.score }}</span>
                    · 均线 {{ dc.strategy_quality.regime.ma_trend }}）
                  </div>
                  <table v-if="dc.strategy_quality.rows?.length" class="w-full text-[11px] mt-1.5">
                    <thead class="text-muted">
                      <tr class="border-b border-border/50">
                        <th class="text-left py-0.5 font-normal">战法</th>
                        <th class="text-right py-0.5 font-normal">样本</th>
                        <th class="text-right py-0.5 font-normal">胜率</th>
                        <th class="text-right py-0.5 font-normal">均收益</th>
                        <th class="text-right py-0.5 font-normal">盈亏比</th>
                        <th class="text-left py-0.5 pl-2 font-normal">状态</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr v-for="s in dc.strategy_quality.rows" :key="s.key"
                          class="border-b border-border/30">
                        <td class="py-0.5">{{ s.cn }}</td>
                        <td class="py-0.5 text-right font-mono text-muted">{{ s.n }}</td>
                        <td class="py-0.5 text-right font-mono"
                            :class="(s.win_rate || 0) >= 50 ? 'text-rise' : 'text-fall'">
                          {{ s.win_rate }}%</td>
                        <td class="py-0.5 text-right font-mono"
                            :class="(s.avg_ret || 0) >= 0 ? 'text-rise' : 'text-fall'">
                          {{ (s.avg_ret || 0) >= 0 ? '+' : '' }}{{ s.avg_ret }}%</td>
                        <td class="py-0.5 text-right font-mono">{{ s.profit_factor }}</td>
                        <td class="py-0.5 pl-2">
                          <span v-if="s.pass" class="text-emerald-400">达标</span>
                          <span v-else-if="s.insufficient" class="text-muted">样本不足</span>
                          <span v-else class="text-amber-300" :title="s.alert || '未达判据'">
                            未达标<template v-if="s.alert"> ⚠</template></span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <div class="text-[10px] text-muted mt-1">
                    判据 {{ dc.strategy_quality.criterion }}（期望值 / 胜率双轨，任一轨跌破即暂停推送）
                    · 统计于 {{ (dc.strategy_quality.computed_at || '').slice(5, 16).replace('T', ' ') }}
                    · 口径：T+1 开盘成交、涨停一字剔除、含主力闸门过滤
                  </div>
                </div>
                <div v-if="(dc.do?.avoid || []).length">
                  <span class="text-muted">回避：</span><span class="text-red-400">{{ dc.do.avoid.join('；') }}</span>
                </div>
                <div><span class="text-muted">做多少：</span>{{ dc.how_much?.total_cap
                  }}<template v-if="dc.how_much?.single_cap && dc.how_much.single_cap !== '—'"> · {{ dc.how_much.single_cap }}</template></div>
                <div><span class="text-muted">错了怎么办：</span>{{ dc.if_wrong?.stop_rule
                  }}<template v-if="dc.if_wrong?.retreating"> · <span class="text-amber-400">{{ dc.if_wrong.retreating }}</span></template></div>
                <div><span class="text-muted">环境：</span>情绪 {{ dc.environment?.emotion_verdict || '—' }}
                  · {{ dc.environment?.emotion_detail }}</div>
              </div>
              <!-- ★ 2026-09-25 用户需求："负面清单和主线推荐同等重要" ⇒ 决策卡补「负面清单」。
                   数据来自后端 dc.negatives（持仓主力出货 / 候选评分与主力信号冲突 / 矛盾敞口）。
                   ⚠️ 本版**不含**解禁/减持/停牌/问询/新股（无数据源，待 C1 接入后由后端追加，
                   前端无需改动）。空数组 ⇒ 整块不渲染（不给"假清空"的安心感）。 -->
              <div v-if="(dc.negatives || []).length" class="border-t border-border/40 mt-2 pt-2 text-xs">
                <div class="text-muted mb-1">负面清单（今天要避开的）</div>
                <div v-for="(n, i) in dc.negatives" :key="i" class="py-0.5">
                  <span class="px-1 rounded text-[10px]"
                        :class="n.level === 'high' ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'">{{ n.scope }}</span>
                  <a v-if="n.code" :href="stockHref(n.code)" target="_blank"
                     class="ml-1 font-semibold text-red-400 hover:underline">{{ n.name || n.code }}</a>
                  <span class="ml-1 text-gray-300">{{ n.reason }}</span>
                </div>
              </div>
              <!-- ★ 2026-09-25 用户："持仓联动（目前最弱）——盘前应对持仓自动扫描…这个结论
                   应该由系统说出来" ⇒ 从「持仓扫描」升级为「持仓预案」：每只给出**今天怎么处理**
                   （plan_level 决定配色）+ 建议仓位上限。数据来自后端 dc.positions_scan
                   （portfolio_radar 的 alerts × position_sizing 的个股档位，零新增数据源）。
                   ⚠️ 是**纪律提醒**，不是交易指令（后端文案已沿用 coach 口径）。 -->
              <div v-if="(dc.positions_scan || []).length" class="border-t border-border/40 mt-2 pt-2 text-xs">
                <div class="text-muted mb-1">持仓预案（今天怎么处理）</div>
                <div v-for="p in dc.positions_scan" :key="p.code" class="py-0.5">
                  <div class="flex items-start gap-1.5 flex-wrap">
                    <a :href="stockHref(p.code)" target="_blank"
                       class="font-semibold hover:underline">{{ p.name }}</a>
                    <span class="font-mono" :class="pctClass(p.pnl_pct)">{{ fmtPct(p.pnl_pct) }}</span>
                    <span class="text-muted">主力 {{ p.phase_cn || '—' }}</span>
                    <span v-if="p.suggested_pct != null"
                          class="px-1 rounded text-[10px] bg-sky-500/15 text-sky-400 cursor-help"
                          :title="`建议仓位上限 ${p.suggested_pct}%（个股档位 ${p.position_label || '—'}）${(p.sizing_reasons || []).length ? '：' + p.sizing_reasons.join('；') : ''}`">
                      ≤{{ p.suggested_pct }}%
                    </span>
                  </div>
                  <div class="mt-0.5" :class="planClass(p.plan_level)">{{ p.plan || '—' }}</div>
                  <div v-for="(a, j) in (p.alerts || [])" :key="j" class="text-[11px] text-muted">· {{ a.text }}</div>
                </div>
              </div>
            </template>
          </div>

          <!-- ★ 2026-09-25（P2）用户："决策简报里 ma_convergence_breakout 说『需结合行业分布判断』，
               但页面没给分布 —— **58 只信号的行业交叉表应该直接画出来**。"
               且"融捷（锂）和焦作万方（电解铝）笼统归入『有色/化工链条』，**分类口径要标注**"。
               ⇒ 数据来自后端 `dc.signals_industry`（零新增数据源：strategy_results + stock_industry）。
               行业名 = 归一化一级；**悬停显示原始细分口径**（如 有色 → 锂/电解铝）。
               ⚠️ 原始名里新浪 node（new_xxx）已被后端过滤，不会出现乱码。 -->
          <div v-if="dc && (dc.signals_industry?.rows || []).length"
               class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">信号 × 行业
                <span class="text-[10px] text-muted font-normal">
                  （{{ dc.signals_industry.date }} 扫描 · 共 {{ dc.signals_industry.total_codes }} 只有信号）</span>
              </div>
              <span class="text-[10px] text-muted cursor-help"
                    title="行业为归一化后的一级分类；悬停任意一行可看该行业的原始细分口径与战法构成">行业＝一级 · 悬停看口径</span>
            </div>
            <div class="space-y-1 text-xs">
              <div v-for="r in dc.signals_industry.rows" :key="r.industry"
                   class="flex items-center gap-2 cursor-help"
                   :title="`${r.industry}：${r.count} 只信号｜战法构成 ${Object.entries(r.strategies || {}).map(([k, v]) => k + ' ' + v).join(' / ')}${(r.raw || []).length ? '｜原始细分：' + r.raw.join('、') : ''}`">
                <span class="w-[70px] truncate text-gray-200">{{ r.industry }}</span>
                <div class="flex-1 h-2.5 rounded bg-white/5 overflow-hidden">
                  <div class="h-full rounded bg-accent/60" :style="{ width: barW(r.count) }"></div>
                </div>
                <span class="w-8 text-right font-mono text-gray-300">{{ r.count }}</span>
                <span class="w-[140px] truncate text-[10px] text-muted">
                  {{ (r.raw || []).length ? r.raw.join(' / ') : (r.industry === '未映射' ? '无行业映射' : '') }}
                </span>
              </div>
            </div>
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
              <div class="text-sm font-semibold">决策简报（盘前）<span
                v-if="premarketOver" class="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[10px]">盘前已结束 · 以下为盘前回顾（09:10 生成），勿据此做盘中决策</span></div>
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
            <!-- ★★ 2026-09-25 用户需求 1（框架 C5「盘中 9:30-10:00 定方向：黄白线/权重护盘」）：
                 看盘序"指数→家数→成交额"之后**必须回答的方向问题** ——
                 今天涨的是**权重**（指数好看、个股不跟）还是**个股**（题材在扩散）？
                 两者的操作完全相反：权重护盘时"看指数做个股"最容易亏（指数红、账户绿）；
                 小盘活跃时个股信号才可信。数据同源 `/market/overview.style`（零新增请求）。
                 ⚠️ 数据时刻：盘中为实时（≤2 分钟滞后）；**休市/盘后是收盘快照**，已在行内标明。 -->
            <div v-if="ovStyle?.available" class="border-t border-border/40 mt-2 pt-2 text-[11px]">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-muted">大小盘风格</span>
                <span class="font-semibold cursor-help" :class="styleCls" :title="styleTitle">
                  {{ ovStyle.label }}</span>
                <span class="text-muted font-mono">
                  大 <b :class="pctClass(ovBig?.avg)">{{ signNum(ovBig?.avg) }}%</b>
                  <template v-if="ovMid">/ 中 <b :class="pctClass(ovMid?.avg)">{{ signNum(ovMid?.avg) }}%</b></template>
                  / 小 <b :class="pctClass(ovSmall?.avg)">{{ signNum(ovSmall?.avg) }}%</b>
                </span>
                <!-- 背离用中性色：它是"加权−等权"的差值，正负不直接等于"涨/跌"，
                     用红绿会被误读成行情涨跌（正=权重强于个股，对做个股其实偏负面） -->
                <span class="text-muted">· 背离（加权−等权）
                  <b class="font-mono text-gray-300">{{ signNum(ovStyle.spread) }}%</b></span>
                <span v-if="ovStyle.from_snapshot" class="text-amber-400/80 text-[10px]"
                      title="行情缓存来自数据库收盘快照（盘后/周末不重新抓取）">
                  （{{ ovStyle.as_of }} 快照，非实时）</span>
              </div>
              <div class="text-muted mt-0.5">{{ ovStyle.note }}</div>
            </div>
            <div v-if="emotion" class="text-[11px] text-muted mt-2 border-t border-border/40 pt-2">
              昨日涨停 {{ emotion.prev_limit_count ?? '—' }} 只 · 今日平均表现
              <b :class="pctClass(emotion.prev_limit_today_pct)">{{ fmtPct(emotion.prev_limit_today_pct) }}</b>（赚钱效应）
              <!-- ★ 2026-09-25：无行情时后端返回 None ⇒ 显示「—」（原为 0，会被读成"没有连板"） -->
              · 连板高度 <b class="text-gray-200 font-mono">{{ emotion.max_streak ?? '—' }}</b>
              <template v-if="emotion.leader">（{{ emotion.leader_name || emotion.leader }}）</template>
              <span class="text-[10px]">· {{ emotion.note }}</span>
            </div>
            <!-- ★ P1 竞价看板：昨日涨停股今日高开（9:25 竞价定稿后有效；休市日显示最近交易日） -->
            <div v-if="emotion && emotion.auction && emotion.auction.count"
                 class="border-t border-border/40 mt-2 pt-2 text-xs">
              <div class="mb-1">竞价看板：昨日涨停 {{ emotion.auction.count }} 只 ·
                平均高开 <b :class="pctClass(emotion.auction.avg_gap)">{{ signNum(emotion.auction.avg_gap) }}%</b>
                <span class="text-muted">（高开幅度 Top5）</span></div>
              <div class="flex flex-wrap gap-1.5">
                <span v-for="g in (emotion.auction.top || [])" :key="g.code"
                      class="px-1.5 py-0.5 rounded border border-border/60 font-mono"
                      :class="g.gap_pct >= 0 ? 'text-red-400' : 'text-emerald-400'">
                  {{ g.name }} {{ signNum(g.gap_pct) }}%
                </span>
              </div>
            </div>
          </div>

          <!-- ★ B2 涨停复盘（zzshare：连板梯队/涨停清单；匿名限流时占位）
               ★ 2026-09-25：优先读**已落库快照**（`zz_daily_snapshots`）—— 原实现每次直连
                 zzshare ⇒ 匿名受限/盘中易失败，而日批明明已落库一份完整 payload。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">涨停梯队与清单
              <span class="text-[10px] text-muted font-normal">
                （zzshare 口径 · {{ limitReview.source === 'snapshot' ? '读已落库快照' : '实时直连' }}）</span></div>
            <!-- ★★ 连板梯队结构（`uplimit_hot.ban_info`，此前**只落库、从未展示**）
                 —— 价值在**梯队完整性**：某级别为 0 而更高有 ⇒ 断层 ⇒ 高标孤立无承接，
                 短线退潮的常见前兆（实测 09-23 即 5/6 板空档却有 7 板）。 -->
            <div v-if="limitReview.ladder?.dist?.length" class="mb-2">
              <div class="flex items-center gap-1.5 flex-wrap text-[11px]">
                <span class="text-muted">连板梯队</span>
                <span v-for="d in limitReview.ladder.dist" :key="d.level"
                      class="px-1.5 py-0.5 rounded border font-mono"
                      :class="d.count === 0
                        ? 'border-amber-500/50 bg-amber-500/10 text-amber-400'
                        : 'border-border/60 text-gray-300'">
                  {{ d.level }}板 {{ d.count }}</span>
                <span class="text-muted">最高 <b class="text-gray-300">{{ limitReview.ladder.max_count }}</b> 板
                  · 当日涨停 {{ limitReview.ladder.total }} 只</span>
              </div>
              <div v-if="limitReview.ladder.broken" class="text-[11px] text-amber-400 mt-1">
                ⚠ {{ limitReview.ladder.note }}</div>
              <!-- ★ review 意见①：高度塌陷（全首板无连板）是**另一种冰点**，
                   与"断层"并列显示，情绪刻画才完整。 -->
              <div v-else-if="limitReview.ladder.collapsed"
                   class="text-[11px] text-sky-400 mt-1">❄ {{ limitReview.ladder.note }}</div>
            </div>
            <!-- ★★ review 意见③④：炸板率(A) + 大面率(B) —— **刻意拆成两个指标**：
                 A=接力意愿（资金愿不愿把板封死）｜B=亏钱烈度（炸了砸多深）。
                 一只票摸板收 +7% ⇒ A 判"炸"（接力确实失败）、B 判"不炸"（对打板客仍是面）
                 ⇒ 两者都对，因为是两件事。组合读法：**A 高 B 低 = 分歧大但亏钱温和；
                 双高 = 真退潮**。口径细节全在 stats.meta 里（悬停可见）。 -->
            <div v-if="limitReview.stats" class="mb-2 border-t border-border/40 pt-2">
              <div class="flex items-center gap-3 flex-wrap text-[11px]"
                   :title="limitReview.stats.meta">
                <span class="text-muted">炸板率(A) <b class="font-mono text-amber-300">{{ limitReview.stats.break_rate ?? '—' }}%</b>
                  <span class="text-[10px]">接力意愿</span></span>
                <span class="text-muted">大面率(B) <b class="font-mono text-red-400">{{ limitReview.stats.big_loss_rate ?? '—' }}%</b>
                  <span class="text-[10px]">亏钱烈度</span></span>
                <span class="text-muted">封板率 <b class="font-mono text-gray-300">{{ limitReview.stats.seal_rate ?? '—' }}%</b></span>
                <span class="text-[10px] text-muted">
                  曾涨停 {{ limitReview.stats.total?.touched }}（一字 {{ limitReview.stats.total?.oneword }}）
                  · 分母已剔一字与新股</span>
              </div>
              <!-- 分桶：20cm 的炸板与 10cm 不是一个情绪含义，混在一起会稀释信号 -->
              <div v-if="Object.keys(limitReview.stats.buckets || {}).length"
                   class="flex items-center gap-2 flex-wrap text-[10px] mt-1">
                <span class="text-muted">分桶</span>
                <span v-for="(v, k) in limitReview.stats.buckets" :key="k"
                      class="px-1.5 py-0.5 rounded border border-border/60 text-muted">
                  {{ k }} 炸板 <b class="font-mono text-amber-300">{{ v.break_rate ?? '—' }}%</b>
                  / 大面 <b class="font-mono text-red-400">{{ v.big_loss_rate ?? '—' }}%</b>
                  <span class="text-[9px]">(n={{ v.denom }})</span></span>
              </div>
            </div>
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

          <!-- ★ A1 主线板块 Top5 + **板块内强势股**（看盘序第 4-5 层）
               ★ 2026-09-25：改用实时行业板块列表（原为昨日 15:10 快照）并补上「领涨股」——
                 此前缺的正是"板块内强势股"这一层（东财板块接口自带 leader 字段，零成本）。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">主线板块 Top5
                <span class="text-[10px] text-muted font-normal">（实时 · 含板块内领涨股）</span></div>
              <router-link target="_blank" to="/sector" class="text-xs text-accent hover:underline">板块详情</router-link>
            </div>
            <div v-if="!sectorTop.length" class="text-muted text-xs">—（板块数据未返回）</div>
            <div v-else class="space-y-1 text-xs">
              <div v-for="(s, i) in sectorTop" :key="i" class="border-b border-border/40 py-1">
                <div class="flex items-center gap-2">
                  <span class="w-4 text-muted font-mono">{{ i + 1 }}</span>
                  <span class="font-semibold">{{ s.name || s.industry || s.code || '—' }}</span>
                  <span v-if="s.up_count !== undefined" class="text-[10px] text-muted">
                    涨{{ s.up_count }}/跌{{ s.down_count }}</span>
                  <span class="ml-auto font-mono" :class="pctClass(s.change_pct ?? s.pct_change)">
                    {{ signNum(s.change_pct ?? s.pct_change) }}%</span>
                </div>
                <!-- 板块内强势股（快照回退路径没有 leader 字段 ⇒ 该行不渲染） -->
                <div v-if="s.leader" class="flex items-center gap-1.5 pl-6 mt-0.5 text-[11px]">
                  <span class="text-muted">领涨</span>
                  <a v-if="s.leader_code" :href="stockHref(s.leader_code)" target="_blank"
                     class="hover:text-accent">{{ s.leader }}</a>
                  <span v-else>{{ s.leader }}</span>
                  <span class="font-mono" :class="pctClass(s.leader_change_pct)">
                    {{ signNum(s.leader_change_pct) }}%</span>
                </div>
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
          <!-- ★★ 2026-09-25：**复盘数据就绪度** —— 用户指出「19:30 就能复盘了吗？actions 还没跑完」。
               实测证据（昨日 09-24 的落库时刻，北京时间）：
                 日报 22:16 ｜ 矛盾扫描 21:54 ｜ 龙虎榜 22:14 ｜ 评分快照 22:05 ｜ 主力行为 21:45
               ⇒ **19:30 打开复盘时，日报/矛盾扫描等尚未生成**，页面只会显示"—（暂无数据）"，
                 用户无从判断"是没生成还是坏了"。
               ⇒ 按用户要求**不移动时间轴节点**，改为**如实显示每块的就绪状态**；
                 未就绪时说明原因与预计时间，避免把"日批还没跑完"误读成"功能坏了"。 -->
          <div class="bg-card border border-border rounded-lg px-4 py-2 text-[11px] flex items-center gap-3 flex-wrap">
            <span class="text-muted">复盘数据就绪</span>
            <span :class="reportMd ? 'text-emerald-400' : 'text-amber-400'">
              日报 {{ reportMd ? '✓' : '未生成' }}</span>
            <span :class="(emotionReview?.items || []).length ? 'text-emerald-400' : 'text-amber-400'">
              情绪对账 {{ (emotionReview?.items || []).length ? '✓' : '未生成' }}</span>
            <span :class="consistency ? 'text-emerald-400' : 'text-amber-400'">
              执行一致性 {{ consistency ? '✓' : '未生成' }}</span>
            <span v-if="!reportMd" class="text-amber-400">
              —— 日批（GitHub Actions）尚未跑完：昨日实际完成于 <b>22:16</b>，完整数据约 <b>22:30</b> 后就绪；
              届时点上方时间轴「复盘」或刷新本页即可。</span>
          </div>
          <!-- ★ 2026-09-25 P3（用户："复盘建议做『盘前预判 vs 实际走势』对账 —— 预测『分歧/常态』，
               实际是否退潮？对错了要回溯修正，形成闭环，**否则情绪模型永远校准不了**"）：
               同日两组字段对比 —— 预判（主字段，盘前/盘中**首次**算出）vs 实际（`close_*`，收盘后）。
               ⚠️ 两条诚实：① 「预判」的时刻是 `as_of`（可能不是盘前）⇒ 每条都显示时刻，不假装；
               ② 本表自 2026-09-25 起落库且只在交易日写 ⇒ 需要几天才看得出命中率。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
              <div class="text-sm font-semibold">情绪对账（预判 vs 实际）
                <span class="text-[10px] text-muted font-normal">（近 30 天 · 只统计两侧数据都全的日子）</span>
              </div>
              <div v-if="emotionReview?.summary?.comparable" class="text-xs">
                命中 <b class="font-mono text-accent">{{ emotionReview.summary.hit }}/{{ emotionReview.summary.comparable }}</b>
                <span class="text-muted">（{{ emotionReview.summary.hit_rate }}%）</span>
              </div>
            </div>
            <div v-if="!emotionReview" class="text-muted text-xs">—（加载失败）</div>
            <template v-else>
              <div v-if="emotionReview.note" class="text-[11px] text-amber-400 mb-2">{{ emotionReview.note }}</div>
              <table v-if="(emotionReview.items || []).length" class="w-full text-xs">
                <thead class="text-muted">
                  <tr class="border-b border-border">
                    <th class="text-left py-1.5">日期</th>
                    <th class="text-left py-1.5">预判（时刻）</th>
                    <th class="text-left py-1.5">实际（时刻）</th>
                    <th class="text-left py-1.5">结论</th>
                    <th class="text-right py-1.5" title="实际涨停家数 − 预判时涨停家数">Δ涨停</th>
                    <th class="text-right py-1.5" title="实际连板高度 − 预判时">Δ连板</th>
                    <th class="text-right py-1.5" title="实际赚钱效应 − 预判时（%）">Δ溢价</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="it in emotionReview.items" :key="it.date" class="border-b border-border/40">
                    <td class="py-1.5 font-mono">{{ (it.date || '').slice(5) }}</td>
                    <td class="py-1.5">
                      <span class="text-gray-300">{{ it.pre_verdict || '—' }}</span>
                      <span class="text-muted text-[10px] ml-1">{{ (it.pre_as_of || '').slice(11, 16) }}</span>
                    </td>
                    <td class="py-1.5">
                      <span class="text-gray-200">{{ it.act_verdict || '—' }}</span>
                      <span class="text-muted text-[10px] ml-1">{{ (it.act_as_of || '').slice(11, 16) }}</span>
                    </td>
                    <td class="py-1.5" :class="relCls(it.relation)">{{ it.relation || '—' }}</td>
                    <td class="py-1.5 text-right font-mono" :class="pctClass(it.d_limit_up)">{{ it.d_limit_up ?? '—' }}</td>
                    <td class="py-1.5 text-right font-mono" :class="pctClass(it.d_max_streak)">{{ it.d_max_streak ?? '—' }}</td>
                    <td class="py-1.5 text-right font-mono" :class="pctClass(it.d_money)">{{ it.d_money ?? '—' }}</td>
                  </tr>
                </tbody>
              </table>
              <div class="text-[10px] text-muted mt-2">
                读法：「一致」= 收盘仍同档（判读可信）；「偏保守」= 实际比预判更热（没看到升温）；
                「偏乐观」= 实际更冷（没看到退潮）。⚠️ `verdict` 是**状态档位不是概率预测**，
                故按"起终档位差"判定，而非二值对/错。
              </div>
            </template>
          </div>

          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">执行一致性</div>
              <router-link target="_blank" to="/coach" class="text-xs text-accent hover:underline">教练页</router-link>
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
              <router-link target="_blank" to="/report" class="text-xs text-accent hover:underline">完整日报 </router-link>
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
  getWorkbenchDecisionCard,
  getMarketEmotion, getMarketLimitReview,
  getUserPositionSizing,
  getEmotionReview,
  // ★ 2026-09-25 修复：**这两个原本就没 import** —— `loadSectorTop` 里一直在用
  //   `getSectorSnapshot` 却从未导入 ⇒ `vite build` 不做未定义变量检查（ESM 下被当成全局
  //   变量，不报错），而运行时抛 `ReferenceError` 被 `try/catch` 静默吞掉
  //   ⇒ **板块卡一直显示"（当日无板块快照）"**，从构建与日志里都看不出来。
  //   A1 收尾时改用实时 `/sector/industry`（含领涨股）才发现 ⇒ 两个都补上。
  getSectorIndustry, getSectorSnapshot,
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
  // ★ 2026-09-25 用户："9:15–9:25 是盘前到盘中的关键过渡（竞价额、竞价涨幅榜、
  //   昨日强势股溢价），时间轴上 9:30 直接从盘前跳盘中，少了一环。"
  //   ⇒ 补「竞价」节点：9:15 开始（9:20 后不可撤单、9:25 定稿）。
  { key: 'auction', label: '竞价', time: '09:15' },
  { key: 'intraday', label: '盘中', time: '09:30' },
  { key: 'midday', label: '午盘', time: '11:30' },
  { key: 'postmarket', label: '盘后', time: '15:00' },
  { key: 'review', label: '复盘', time: '19:30' },
]
// 各时段专属配色（类名必须写成完整字面量，Tailwind JIT 才会生成）：
//   盘前蓝=计划筹备 / 盘中红=交易执行 / 午盘琥珀=休市过渡 / 盘后紫=数据结算 / 复盘绿=复盘沉淀
const PHASE_STYLE = {
  premarket:  { text: 'text-sky-400',    border: 'border-sky-500/60',    bg: 'bg-sky-500/10',    hover: 'hover:border-sky-500/40 hover:bg-sky-500/5' },
  // 竞价：indigo —— 语义上是"盘前(蓝) → 盘中(红)"的过渡色（类名必须静态字面量，Tailwind 才扫得到）
  auction:    { text: 'text-indigo-400', border: 'border-indigo-500/60', bg: 'bg-indigo-500/10', hover: 'hover:border-indigo-500/40 hover:bg-indigo-500/5' },
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
// ★ P1 决策卡（规则引擎确定性输出；空状态由后端 error 兜底）
const dc = ref(null)
const dcLoading = ref(false)
const dcErr = ref('')
// ★ 2026-09-25 盘中外盘四件套（A50/离岸/布伦特/纳指期货）——宏面板已在抓，
//   一次新浪批量请求 60s 缓存，工作台只是读取，零新增请求
const globals = ref({})
// ★ 2026-09-25：多市场时段（后端 `/macro/snapshot.clock`，源自 `flash/rules.get_market_clock()`
//   的**唯一实现**）⇒ 用于顶栏"外盘开市"指示，前端不自算时段以免口径漂移。
const macroClock = ref(null)
// ★ 2026-09-25 P1：自上次 A 股收盘以来的外盘累计变化（后端 `/macro/snapshot.overnight`
//   ⇒ 基准取自 macro_history 的"收盘后快照"）。纯展示、不参与评分。
const macroOvernight = ref(null)
// ★ 2026-09-25：情绪温度计（金十，`/macro/snapshot.sentiment`）—— 含巴菲特指标/日成交额等
//   12 子项，用于"过热 vs 过冷"两栏对照（回答"为什么巴菲特指标过热而市场温度偏冷"）。
//   ⚠️ 后端是 peek（只读缓存不拉网）⇒ 冷缓存为 null ⇒ 整块不渲染。
const macroSentiment = ref(null)
// ★ 2026-09-25：仓位建议的**定量上限**（`/api/user/position-sizing` 的 total_limit_pct）。
//   宏观卡原只显示 LLM 的定性词（如"轻仓观望"），补上引擎算出的具体上限数字。
const sizing = ref(null)
// ★ 2026-09-25 用户需求 2：组合**周回撤熔断**（同接口的 `portfolio_drawdown`）——
//   组合近 5 个交易日持仓市值回撤 ≥5%（对齐 G2）⇒ 后端已把总上限降半仓，这里显示状态。
const pdd = computed(() => sizing.value?.portfolio_drawdown || null)
const pddTitle = computed(() => {
  const p = pdd.value
  if (!p) return ''
  if (!p.available) return p.note || '暂无数据'
  const cur = (p.curve || []).map(c => `${String(c.date).slice(5)} ${Number(c.nav).toLocaleString('zh-CN')}`).join(' → ')
  return `组合持仓市值路径：${cur}\n峰值 ${Number(p.nav_peak).toLocaleString('zh-CN')}（${p.peak_date}）`
    + ` → 最新 ${Number(p.nav_latest).toLocaleString('zh-CN')}\n${p.advice || ''}\n${p.note || ''}`
})
// ★ 2026-09-25 P3：情绪对账（盘前预判 vs 当日实际）—— 用户："对错了要回溯修正，形成闭环，
//   否则情绪模型永远校准不了"。数据来自 `/api/market/emotion-review`（读同日两组字段）。
const emotionReview = ref(null)
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
  // 与后端 current_phase 同分界（09:30/15:00），UI 在盘前侧再细分成 6 段
  const d = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000)
  const m = d.getHours() * 60 + d.getMinutes()
  if (m >= 550 && m < 555) return 'premarket'    // 9:10-9:15 盘前（简报/决策卡已生成）
  // ★ 2026-09-25：9:15-9:30 独立为「竞价」段。此前整段并进盘前 ⇒ 时间轴上"竞价"这一环
  //   不可见（用户："9:30 直接从盘前跳盘中，少了一环"）。9:20 后不可撤单、9:25 定稿。
  if (m >= 555 && m < 570) return 'auction'
  if (m >= 570 && m < 690) return 'intraday'
  if (m >= 690 && m < 900) return 'midday'
  if (m >= 900 && m < 1170) return 'postmarket'
  return 'review'
}
const livePhase = ref(computePhase())
// ★ 盘前是否已结束（9:30 后）：用于盘前视图的"回顾"标注
const premarketOver = computed(() => {
  const d = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000)
  const m = d.getHours() * 60 + d.getMinutes()
  return m >= 570
})
const renderMd = (md) => mdRenderer.render(String(md || ''))
const firstLine = (s) => String(s || '').split('\n').find(x => x.trim()) || ''
const fmtNum = (v) => (v == null ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 }))
const fmtPct = (v) => (v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`)
const signNum = (v) => (v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}`)
const pctClass = (v) => (Number(v) > 0 ? 'text-red-400' : Number(v) < 0 ? 'text-emerald-400' : 'text-muted')
// ★ 2026-09-25：持仓预案的动作等级配色（后端 `_position_plan` 输出的 plan_level）：
//   act=优先处理(红加粗) / exit=档位0该清(红) / protect=保护利润(琥珀) / hold=持有(绿) / 其余(灰)
const planClass = (lv) => (lv === 'act' ? 'text-red-400 font-semibold'
  : lv === 'exit' ? 'text-red-400'
    : lv === 'protect' ? 'text-amber-400'
      : lv === 'hold' ? 'text-emerald-400' : 'text-gray-300')
// ★ 2026-09-25：成交额（输入**万元**）→ 友好显示（≥1 亿转"亿"）。用于竞价段的"累计成交"。
const fmtAmountWan = (v) => {
  if (v == null || Number.isNaN(Number(v))) return '—'
  const n = Number(v)
  return Math.abs(n) >= 10000 ? (n / 10000).toFixed(2) + '亿' : n.toFixed(0) + '万'
}
// ★ 2026-09-25：信号×行业 的条形宽度（相对**本表最大值**，避免绝对刻度在大盘股/小行业间失真）
const barW = (n) => {
  const rows = dc.value?.signals_industry?.rows || []
  const max = Math.max(1, ...rows.map(r => r.count || 0))
  return Math.round(((n || 0) / max) * 100) + '%'
}
// ★ 2026-09-25 P3：情绪对账的结论配色 —— 一致=绿 / 偏保守=琥珀 / **偏乐观=红**
//   （"偏乐观"最危险：它意味着没看到退潮，而退潮是要降仓的）
const relCls = (r) => (r === '一致' ? 'text-emerald-400'
  : r === '偏保守' ? 'text-amber-300'
    : r === '偏乐观' ? 'text-red-400' : 'text-muted')
const scoreClass = (v) => (Number(v) >= 65 ? 'text-red-400' : Number(v) >= 45 ? 'text-amber-300' : 'text-muted')

const topIndices = computed(() => (overview.value.indices || []).slice(0, 3))
// ★ 2026-09-25 用户需求 1：大小盘风格 / 黄白线背离（框架 C5「9:30-10:00 定方向」）。
//   `overview.style` 由后端在同一份内存行情缓存上算出 ⇒ **零新增请求**。
const ovStyle = computed(() => overview.value?.style || null)
const ovGrp = (k) => (ovStyle.value?.groups || []).find(g => g.key === k) || null
const ovBig = computed(() => ovGrp('big'))
const ovMid = computed(() => ovGrp('mid'))
const ovSmall = computed(() => ovGrp('small'))
// 配色语义：**权重护盘=琥珀**（指数被权重撑起、个股不跟 ⇒ 是"陷阱"型红盘，要警惕）；
//   小盘活跃=红（A股红=涨，题材扩散对做个股是好事）；均衡=灰。
const styleCls = computed(() => (ovStyle.value?.verdict === 'small_active' ? 'text-red-400'
  : ovStyle.value?.verdict === 'weight_support' ? 'text-amber-300' : 'text-gray-300'))
// 悬停详情：口径 + 各组明细 + 数据时刻（⚠️ 休市日是**收盘快照**，必须标明，别当成实时）
const styleTitle = computed(() => {
  const s = ovStyle.value
  if (!s?.available) return ''
  const gs = (s.groups || []).map(g =>
    `${g.label} ${g.n}只 等权${signNum(g.avg)}%（上涨占比 ${g.up_ratio}%）`).join('\n')
  return `白线（流通市值加权） ${signNum(s.weighted)}%\n`
    + `黄线（等权） ${signNum(s.equal)}%\n`
    + `背离（加权−等权） ${signNum(s.spread)}%（正=权重强于个股）\n`
    + `真实上证指数 ${signNum(s.index_pct)}%（对照）\n\n${gs}\n\n`
    + `数据 ${s.as_of || '—'}${s.from_snapshot ? '（收盘快照，非实时）' : ''}\n${s.note || ''}`
})
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
// ★ 2026-09-25：当前**正在交易**的市场（顶栏"外盘开市"指示）——
//   一律取后端 `market_clock` 的布尔字段，前端**不自己算时段**（唯一实现原则）。
//   `clock` 拿不到（旧后端/接口失败）⇒ 返回空 ⇒ 该指示块整体不渲染（安全降级）。
const openMarkets = computed(() => {
  const c = macroClock.value
  if (!c) return []
  const out = []
  // ⚠️ A 股必须**同时**满足"在交易时刻"与"今天是交易日"：`is_a_stock_trading` 只看时刻，
  //    休市日的 13:02 它仍然为 True（实测 2026-09-25 中秋休市日）⇒ 必须叠加 `is_trading_day`。
  //    用 `!== false` 兼容未升级的后端（字段缺失时不误判为休市）。
  if (c.is_a_stock_trading && c.is_trading_day !== false) out.push({ k: 'cn', label: 'A股' })
  // 港股/日经/美股：项目只有 A 股节假日日历，无对应假期表 ⇒ 按**交易时段**判定（近似）。
  if (c.is_hk_trading || c.is_hstech_extended) out.push({ k: 'hk', label: '港股' })
  if (c.is_nikkei_trading) out.push({ k: 'jp', label: '日经' })
  if (c.is_us_trading) out.push({ k: 'us', label: '美股' })
  return out
})
// ★ 2026-09-25 P1：隔夜变化的**显示层**（顶栏一格）。后端已按 |变化| 降序 ⇒ 直接取前 2 项；
//   全部项放 title（避免顶栏信息过载）。⚠️ 空数组 ⇒ 整格不渲染（后端算不出基准时）。
const overnightItems = computed(() => (macroOvernight.value?.items || []))
const overnightBaseLabel = computed(() => {
  const t = macroOvernight.value?.base_time
  // "2026-09-24T15:03:26+08:00" → "（自 09-24 15:03）"
  return t ? `（自 ${String(t).slice(5, 16).replace('T', ' ')}）` : ''
})
const overnightTitle = computed(() => {
  const it = overnightItems.value
  const t = macroOvernight.value?.base_time
  const base = t ? String(t).slice(5, 16).replace('T', ' ') : '—'
  const head = `自上次 A 股收盘（${base}）以来外盘累计变化：`
  const body = it.length
    ? it.map(x => `${x.label} ${signNum(x.pct)}%`).join('\n')
    : (macroOvernight.value?.note || '暂无数据')
  // ⚠️ 明说是"解释/风控"用途 —— 纳指隔夜对 A 股次日的预测力在可交易口径下已实测塌陷
  return `${head}\n${body}\n（仅用于解释开盘与风控；其预测力已被实证否定，不作为交易信号）`
})
// ★ 2026-09-25：情绪温度计"过热 / 过冷"子项（后端已按得分降序，这里按阈值切分）。
//   阈值沿用 `margin_sentiment.sentiment_line()` 的口径（≥80 过热 / ≤20 过冷），保持单一来源。
const hotSubs = computed(() => ((macroSentiment.value?.subs) || []).filter(s => s.score >= 80).slice(0, 4))
const coldSubs = computed(() => ((macroSentiment.value?.subs) || []).filter(s => s.score <= 20).slice(0, 4))

const worstHolding = computed(() => {
  const arr = (radarItems.value || []).filter(x => x.pnl_pct != null)
  return arr.length ? arr.reduce((a, b) => (Number(a.pnl_pct) < Number(b.pnl_pct) ? a : b)) : null
})
const regimeClass = computed(() => ({
  '进攻': 'text-red-400', '震荡': 'text-amber-300',
  '震荡偏空（阴跌）': 'text-amber-400', '防御': 'text-emerald-400',
}[regimeLabel.value] || 'text-muted'))
const freshnessOk = ref(true)
const statusTime = ref('')

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
    // ★ 2026-09-25 修正：该接口响应体是 `{data: {regime: ...}}` **双层包装**
    //   ⇒ axios 解构出的 data 是"整个响应体"，regime 实际在 `data.data.regime`。
    //   原先直接读 `data?.regime` 恒为 undefined ⇒ 界面长期显示"—"。
    //   这里兼容两层（后端若改成单层也不受影响）。
    const r = (data && data.data) || data || {}
    regimeLabel.value = ({ offensive: '进攻', neutral: '震荡', neutral_bearish: '震荡偏空（阴跌）', defensive: '防御' })[r.regime] || (r.regime || '—')
  } catch { regimeLabel.value = '—' }
}
// ══════════════════════════════════════════════════════════════════════════
//  当日数据缓存（★ 2026-09-25 用户建议）
// ══════════════════════════════════════════════════════════════════════════
//  【背景】下面前 6 个 loader 拉的都是"**生成后当天不再变**"的数据（盘前/盘后生成一次、
//    早盘锁定、30 日统计），但它们挂在 `loadPhaseData()` 里 ⇒ **每次切换阶段都会重拉**
//    ⇒ 在 6 个 tab 之间来回点几次，就白拉几次。（120s 轮询里只有"会变"的那几项，
//      这一点架构上本来是对的 —— 重复发生在**切 tab**，不在轮询。）
//  【只缓存"确定当日不变"的】✅ 简报 / 财经日历 / **早盘锁定**的宏观 / 执行一致性 / 日报 / 情绪对账
//    ❌ 一律不缓存：情绪快照、外盘、隔夜变化、板块、涨停梯队、持仓雷达、决策卡
//      —— 它们**会变**，缓存会让人看到过期结论（比多一次请求危险得多）。
//  【三条纪律】
//    ① 键带**日期**：换日或切回放日期自动失效，绝不串数据；
//    ② **只在拿到有效数据时才记**：失败/空一律不记，下次仍会重试；
//    ③ 简报若 `degraded`（LLM 失败降级版）⇒ **不记**：后端的盘前循环窗口内还会重试
//       生成 LLM 版，缓存降级版等于把"临时降级"永久化。
const _dayCache = {}          // { key: 'YYYY-MM-DD' }
const _dayCached = (key, day) => _dayCache[key] === day
const _dayMark = (key, day) => { _dayCache[key] = day }

async function loadBrief(phase) {
  const day = selectedDate.value
  const ck = `brief:${phase}`
  if (_dayCached(ck, day)) return
  briefErr.value = ''
  try {
    const { data } = await getTraderBrief(false, phase)
    briefMd.value = data?.markdown || ''
    briefDegraded.value = !!data?.degraded
    // ★ 纪律③：降级版不缓存（后端窗口内还会重试出 LLM 版）
    if (briefMd.value && !briefDegraded.value) _dayMark(ck, day)
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
  const day = selectedDate.value
  if (_dayCached('consistency', day)) return
  try {
    const { data } = await getCoachConsistency(30)
    consistency.value = data || null
    if (consistency.value) _dayMark('consistency', day)      // 纪律②：空则不缓存
  } catch { consistency.value = null }
}
// ★ 2026-09-25 P3：情绪对账（失败静默 ⇒ 卡片显示"暂无数据"，不影响复盘其它块）
async function loadEmotionReview() {
  const day = selectedDate.value
  if (_dayCached('emotionReview', day)) return
  try {
    const { data } = await getEmotionReview(30)
    emotionReview.value = data || null
    // ★ 对账数据盘后才更新 ⇒ 有内容才缓存（今天还没数据时不该锁死）
    if (emotionReview.value && (emotionReview.value.items || []).length) {
      _dayMark('emotionReview', day)
    }
  } catch { emotionReview.value = null }
}
async function loadReport(date) {
  const day = date || selectedDate.value
  if (_dayCached('report', day)) return
  try {
    const { data } = await getDailyReport(date)
    reportMd.value = data?.markdown || data?.md || ''
    // ★ 日报 19:30 才生成 ⇒ **空就绝不缓存**，否则当天再也拉不到（这个坑很隐蔽）
    if (reportMd.value) _dayMark('report', day)
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
async function loadDecisionCard() {
  dcLoading.value = true
  dcErr.value = ''
  try {
    const { data } = await getWorkbenchDecisionCard()
    if (data && data.error) { dcErr.value = data.error; dc.value = null }
    else dc.value = data
  } catch (e) {
    dcErr.value = (e && e.message) || '生成失败'
  } finally { dcLoading.value = false }
}
async function loadCalendarToday() {
  // ★ P0-1 去重（同一事件多源重复，如"中秋节"×4）+ 按星级排序（重要事件排前）
  const dedupeSort = (rows) => {
    const seen = new Set()
    return (rows || [])
      .filter(it => {
        const k = `${it.title || ''}|${it.country || ''}|${it.date || ''}`
        if (seen.has(k)) return false
        seen.add(k)
        return true
      })
      .sort((a, b) => (b.star || 0) - (a.star || 0))
  }
  const cday = selectedDate.value
  if (_dayCached('calendar', cday)) return
  try {
    const { data } = await getCalendar({ days: 1 })
    calendarToday.value = dedupeSort((data && data.items) || [])
        .filter(it => String(it.date || it.time || '').includes(todayStr)).slice(0, 6)
    calendarErr.value = ''
    // ★ 当日财经事件是静态的（盘前就定）⇒ 有数据才缓存；空则不缓存（当天还会补录事件）
    if (calendarToday.value.length) _dayMark('calendar', cday)
  } catch (e) {
    // ★ 2026-09-25：带出真实原因（Render 重部署窗口/冷启动超时是最常见场景），
    //   并自动重试一次——loadPhaseData 只在进盘前时调用，重试成本低
    calendarErr.value = (e && e.message) || '加载失败'
    try {
      await new Promise(r => setTimeout(r, 2500))
      const { data } = await getCalendar({ days: 1 })
      calendarToday.value = dedupeSort((data && data.items) || [])
        .filter(it => String(it.date || it.time || '').includes(todayStr)).slice(0, 6)
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
    macroClock.value = (data && data.clock) || null   // ★ 时段的唯一来源（见 macroClock 定义）
    macroOvernight.value = (data && data.overnight) || null   // ★ P1：隔夜累计变化
    macroSentiment.value = (data && data.sentiment) || null   // ★ 情绪温度计（供两栏对照）
  } catch { globals.value = globals.value || {} }
}
// ★ 2026-09-25：仓位建议定量上限（失败静默 ⇒ 宏观卡只显示定性词，不显示空括号）
async function loadSizing() {
  try {
    const { data } = await getUserPositionSizing()
    sizing.value = data || null
  } catch (e) { console.warn('仓位建议加载失败', e) }
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
// 主线板块 Top5（★ A1 看盘序第 4-5 层：板块 → **板块内强势股**）
//   ★ 2026-09-25 改动：**主用实时行业板块列表**（`/sector/industry`），快照仅作回退。
//   为什么必须改（两个真问题）：
//     ① 原先用 `/sector/snapshot/{date}` —— 那是**每日 15:10 落的快照**
//        ⇒ 盘中看到的永远是**上一个交易日**的板块排名（今天盘中的板块变化完全看不到）；
//     ② 快照表**不含领涨股**，而"看盘序"的第 5 步恰恰是「板块内强势股」⇒ 缺一层。
//   `/sector/industry` 是实时东财接口（`eastmoney._get_cached` 有 TTL 缓存，
//   不会每帧真请求），自带 `leader` / `leader_change_pct` / `leader_code`
//   ⇒ 一次调用同时解决"实时"与"领涨股"，**零新增接口**。
//   ⚠️ 失败/空数据回退快照：休市或接口异常时仍有内容（只是没有领涨股）。
async function loadSectorTop() {
  try {
    const { data } = await getSectorIndustry({ limit: 5 })
    const rows = (data && data.data) || []
    if (rows.length) {
      sectorTop.value = rows.slice(0, 5)
      return
    }
  } catch { /* 落到快照回退 */ }
  try {
    const { data } = await getSectorSnapshot(todayStr, { limit: 5 })
    sectorTop.value = ((data && data.data) || []).slice(0, 5)
  } catch { sectorTop.value = [] }
}
// 宏观方向（早盘锁定快照优先，回退实时计算）——与 Dashboard.vue 同源同口径
async function loadMacro() {
  // ★ 只缓存"早盘锁定"那一份（dailyRes.snapshot）：它是当日 08:55-13:00 锁定的，
  //   当天不再变 ⇒ 切 tab 重拉纯属浪费。
  //   ⚠️ 回退路径（`getMacroSnapshot()` 实时计算）**不缓存** —— 那个会随外盘变。
  if (_dayCached('macroLocked', todayStr)) return
  macroErr.value = ''
  try {
    const { data: dailyRes } = await getMacroDaily(todayStr)
    if (dailyRes && dailyRes.snapshot) {
      macro.value = { ...dailyRes.snapshot, locked: true }
      _dayMark('macroLocked', todayStr)
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
// ★ 2026-09-25 用户要求：宏观方向/温度配色与数据中心 tab 逐字一致（等级字符串映射）
function dirColor(level) {
  return { '强多': 'text-red-400', '偏多': 'text-orange-400', '中性': 'text-amber-400',
           '偏空': 'text-cyan-400', '强空': 'text-blue-400' }[level] || 'text-muted'
}

function levelColor(level) {
  return { '过热': 'text-red-400', '偏热': 'text-orange-400', '中性': 'text-amber-400',
           '偏冷': 'text-cyan-400', '过冷': 'text-blue-400' }[level] || 'text-muted'
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
    if (data?.generated_at) statusTime.value = String(data.generated_at).slice(11, 16)
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
  if (phase === 'premarket') { await loadBrief('premarket'); await loadDecisionCard(); await Promise.all([loadMacro(), loadFlashDiag(), loadEmotion(), loadCalendarToday(), loadSizing()]) }
  // ★ 2026-09-25：竞价段只拉"竞价相关"的（情绪快照里的 auction + 外盘）—— 零新增接口
  else if (phase === 'auction') { await Promise.all([loadEmotion(), loadGlobals()]) }
  else if (phase === 'postmarket') { await loadTop(); await loadGateWatch(); }
  // ★ 2026-09-25 用户需求 2：复盘也拉仓位（含**组合周回撤**）—— 复盘正是检视
  //   "本周组合回撤了多少、是否该降仓"的时点。⚠️ 刻意**不加进盘中**：该接口会为每只
  //   持仓取一次历史 K 线（有缓存），盘中 120s 轮询没必要反复算慢变量。
  else if (phase === 'review') { await loadConsistency(); await loadEmotionReview(); await loadSizing(); await loadBrief('postmarket'); await loadReport(todayStr); }
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
    // ★ 2026-09-25 用户再次明确：**不要自动流转**。
    //   此处原先有「P0-7 阶段自动流转」—— 阶段推进就 `selectedPhase = livePhase`
    //   把主工作区**切走**（注释说是为修"主体停在盘前、顶栏已是盘中"的混搭）。
    //   ⚠️ 但用户裁定：自动切换会打断正在读的内容（盘前简报读到一半被切走），
    //   宁可自己点。⇒ **只更新 livePhase（供时间轴上的温和提示用）**，
    //   主工作区**永不自动切换**；提示见模板里的「● 已进入，点击切换」。
    //   （此前 dbdb3cf 声称已"回退温和提示"，但这段 P0-7 实际还在 ⇒ 本次真正移除。）
    livePhase.value = computePhase()
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
