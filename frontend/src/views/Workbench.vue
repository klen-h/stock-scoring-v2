<template>
  <div class="fade-in space-y-4">
    <!-- 回放模式横幅（只读） -->
    <div v-if="isReplay"
         class="bg-amber-500/10 border border-amber-500/40 text-amber-300 rounded-lg px-4 py-2 text-sm flex items-center justify-between">
      <span>⏪ 回放 {{ selectedDate }} · 只读（右栏为该日警报历史）</span>
      <button class="text-xs underline" @click="backToToday">切回今天</button>
    </div>

    <!-- 顶栏：日期 · 上下两列（A股/外盘）· 情绪/市况模块（内含下方数据新鲜度，2026-09-25 挪入） -->
    <div class="bg-card border border-border rounded-lg px-4 py-2 flex items-center gap-4 flex-wrap text-sm">
      <!-- ★ 2026-09-25 用户："底部的系统状态移到顶部的日期的下面，把简要信息展示一行，
           点击再悬浮展示内容" ⇒ 日期正下方常驻一行摘要；点击就地弹出**浮层**
           （absolute 定位，不再用底部 `<details>` 撑开页面），点浮层外关闭。 -->
      <div class="relative flex flex-col gap-1">
        <label class="flex items-center gap-2">
          <span class="text-muted text-xs">日期</span>
          <select v-model="selectedDate"
                  class="bg-card border border-border rounded px-2 py-1 text-xs">
            <option v-for="d in dayList" :key="d.date" :value="d.date">
              {{ d.date === todayStr ? '今天 ' + d.date : d.date }}
            </option>
          </select>
        </label>
        <!-- ★ 2026-09-28（用户："缩写系统状态 · 13/13 新鲜 · 库 46.8% 截至 15:18 ▾ 的宽度"）：
             顶栏要腾地方给「宏观」摘要 ⇒ 本行去标签字与空格：
               `● 系统状态 · 13/13 新鲜 · 库 46.8% · 内存 350MB · 截至 15:18 ▾`
             → `● 13/13 · 库46.8% · 15:18 ▾`（省约一半宽度）。
             ⚠️ 敢缩的前提：**完整解释在点击后的浮层里**（`statusHtml`，信息一点没丢）；
               内存**只在 ≥75% 时**才亮出来（平时不是关注点，且它最易波动）；
               全部字段的含义见 `title`。 -->
        <button class="flex items-center gap-1.5 text-[10px] text-muted hover:text-gray-300 w-fit"
                :title="statusTitle"
                @click.stop="statusOpen = !statusOpen">
          <span class="inline-block w-2 h-2 rounded-full"
                :class="freshnessOk ? 'bg-emerald-500' : 'bg-amber-500'"></span>
          <span v-if="statusBrief.summary">{{ statusBrief.summary }}</span>
          <span v-else>系统状态</span>
          <span v-if="statusBrief.dbPct != null">· 库{{ statusBrief.dbPct }}%</span>
          <span v-if="memWarn" class="text-amber-400">· 内存{{ statusBrief.memMb }}MB</span>
          <span v-if="statusTime">· {{ statusTime }}</span>
          <span>▾</span>
        </button>
        <!-- ★ 2026-09-29：与「宏观」浮层同款处理 —— 遮罩**压暗**（原来完全透明 ⇒ 没有层次线索），
             但压得轻一些（这是个小组件，不是大对话框）。 -->
        <div v-if="statusOpen" class="fixed inset-0 z-40 bg-black/45"
             @click="statusOpen = false"></div>
        <!-- ★ 2026-09-29（同「宏观」浮层的溢出问题）：高度上限从 `70vh` 改为
             `calc(100vh - 6rem)` —— `70vh` 没扣掉顶栏（`App.vue` 的 48px sticky 导航）与
             浮层起点，短屏下底部会顶出屏幕；此值保证「起点 + 高度」恒在视口内。
             `overscroll-contain` 防滚动穿透。横向本就在左侧、`left-0` 无溢出风险，故仍锚在按钮下。 -->
        <div v-if="statusOpen"
             class="absolute left-0 top-full z-50 mt-1 w-[min(92vw,420px)]
                    max-h-[calc(100vh_-_6rem)] overflow-y-auto overscroll-contain
                    bg-card border border-white/15 rounded-lg p-3 text-xs space-y-1
                    shadow-[0_16px_48px_-12px_rgba(0,0,0,0.9)]"
             v-html="statusHtml"></div>
      </div>

      <!-- ★ 2026-09-25 用户要求：竖排标签样式（名称/价格/涨跌 上下三行），居中排开空间足够；
           细分隔线区分 A股与外盘 -->
      <div class="flex-1 min-w-[560px] flex items-center justify-center gap-3 flex-wrap">
        <!-- ★ 2026-09-28（用户："顶部的指数点击能像雪球一样跳转到详情页吗？"）：
             指数整块可点 → **雪球指数页**（新标签，与全站"外链一律新标签"惯例一致）。
             用 `<component :is>` 而非直接写 `<a>`：拿不到 `prefix` 时（老数据/降级）
             退回 `<div>` 纯文本 —— 宁可不给链接，也不能指到撞码的个股（`000001`）。 -->
        <component v-for="ix in topIndices" :key="ix.name"
                   :is="ix.href ? 'a' : 'div'"
                   :href="ix.href || undefined"
                   :target="ix.href ? '_blank' : undefined"
                   rel="noopener"
                   class="text-center px-1.5"
                   :class="ix.href ? 'rounded hover:bg-white/5 transition-colors cursor-pointer' : ''"
                   :title="ix.href ? `${ix.name} 详情（雪球，新标签）` : ''">
          <div class="text-[10px] text-muted">{{ ix.name }}</div>
          <div class="text-xs font-mono font-semibold leading-snug">{{ fmtNum(ix.price) }}</div>
          <div class="text-xs font-mono font-bold" :class="pctClass(ix.change_pct)">
            {{ signNum(ix.change_pct) }}%
          </div>
        </component>
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
        <!-- <div v-if="macroClock" class="text-center px-1.5 border-l border-border/60"
             :title="`北京时间 ${macroClock.beijing_time}｜A股 9:30-15:00 · 港股 9:30-16:00 · 日经 8:00-14:00 · 美股 21:30-04:00`">
          <div class="text-[10px] text-muted">外盘开市</div>
          <div class="text-[11px] font-semibold leading-snug"
               :class="openMarkets.length ? 'text-emerald-400' : 'text-muted'">
            {{ openMarkets.length ? openMarkets.map(m => m.label).join('/') : '全部收盘' }}
          </div>
          <div class="text-[10px] text-muted font-mono">{{ macroClock.beijing_time }}</div>
        </div> -->
        <!-- ★ 2026-09-25 P1（用户："到了 A 股开盘，如果没总结外盘正常开市时间，会漏点什么"）：
             显示**自上次 A 股收盘以来**外盘的累计变化 —— 这才是"A 股开盘要消化的新增信息"。
             只按 |变化| 取前 2 项（顶栏防过载），全部项与口径说明放 title。
             ⚠️ 用 `change_pct`（相对昨结）做不到这件事：它含"A 股收盘前就已反映"的部分。 -->
        <button v-if="overnightItems.length" type="button"
                class="text-center px-1.5 border-l border-border/60 hover:opacity-80"
                title="点击查看全部隔夜品种与口径"
                @click.stop="overnightOpen = !overnightOpen">
          <div class="text-[10px] text-muted">隔夜{{ overnightBaseLabel }} <span class="text-[9px]">▼</span></div>
          <div v-for="o in overnightItems.slice(0, 2)" :key="o.key"
               class="text-[11px] font-mono leading-snug" :class="pctClass(o.pct)">
            {{ o.label }} {{ signNum(o.pct) }}%
          </div>
        </button>
      </div>

      <!-- ★ 2026-10-23：隔夜明细浮层（点击顶栏「隔夜」展开，列出全部品种） -->
      <Teleport to="body">
        <div v-if="overnightOpen" class="fixed inset-0 z-40" @click="overnightOpen = false"></div>
        <div v-if="overnightOpen"
             class="fixed right-4 top-[3.25rem] z-50 w-[min(92vw,380px)]
                    bg-card border border-white/15 rounded-lg p-3 text-xs
                    shadow-[0_24px_64px_-16px_rgba(0,0,0,0.9)]"
             @click.stop>
          <div class="flex items-center justify-between mb-1.5">
            <div class="text-sm font-semibold">隔夜{{ overnightBaseLabel }}</div>
            <button type="button" class="text-muted hover:text-gray-200 px-1"
                    @click="overnightOpen = false">✕</button>
          </div>
          <div class="text-[10px] text-muted mb-1.5">
            自上次 A 股收盘以来的累计变化（正=该品种上涨）；只影响"开盘要消化多少"，不是信号。
          </div>
          <div class="space-y-0.5">
            <div v-for="o in overnightItems" :key="o.key"
                 class="flex items-center justify-between border-b border-border/40 py-1">
              <span>{{ o.label }}</span>
              <span class="font-mono" :class="pctClass(o.pct)">{{ signNum(o.pct) }}%</span>
            </div>
          </div>
          <div class="text-[10px] text-muted mt-2 whitespace-pre-line">{{ overnightTitle }}</div>
        </div>
      </Teleport>

      <!-- ★★ 2026-09-28（用户："把宏观方向 · 偏空的分数也移到顶部右上角，放在情绪的左边…
           但是快讯的也很重要，以及支撑因素、压制因素、过热项、过冷项也需要展示" ⇒ 方案 A）：
           顶栏常驻「宏观」摘要，**放在情绪/市况组左边**，与「系统状态」**同构** ——
           一行摘要（方向 + 分数）＋ 点击弹**浮层**看全文（浮层里就是那张完整卡
           `MacroEnvCard`，含快讯诊断 / 支撑因素 / 压制因素 / 过热项 / 过冷项）。
           ⇒ 顶栏只多占 ~70px，四类信息全都在，不必把长卡塞进顶栏。
           ⚠️ 这一点很重要：本卡原先**只在盘前渲染**，而它的一半内容（快讯诊断、情绪温度计）
              其实是**全天会变**的 ⇒ 抽成组件后顶栏浮层让它在**任何阶段**都能看。 -->
      <div class="relative border-l border-border pl-4 flex flex-col gap-1">
        <button type="button" class="flex flex-col items-center justify-center hover:opacity-80"
                :title="macroTip"
                @click.stop="macroOpen = !macroOpen">
          <div class="flex items-baseline gap-1">
            <span class="text-lg font-bold font-mono leading-none"
                  :class="dirColor(macro?.direction?.level)">{{ macro?.direction?.score ?? '—' }}</span>
            <span class="text-[10px] text-muted">▼</span>
          </div>
          <div class="text-[10px] mt-0.5 whitespace-nowrap"
               :class="dirColor(macro?.direction?.level)">宏观·{{ macro?.direction?.level || '—' }}</div>
        </button>
        <!-- ★★ 2026-09-29（用户："浮层的背景色跟项目的背景色相同，边框的颜色也不明显，
             有没有好的办法做个视觉上的区分？"）：
             原遮罩是**完全透明**的（只为"点外面关闭"而存在），而浮层和页面卡片同用 `--card`、
             描边用的是 `--border`（#21262d，比底色只亮一点点）⇒ **没有任何"浮在上层"的线索**。
             三条线索一起给（缺任一条都会显得"糊在一起"）：
               ① **压暗 + 轻微模糊**页面（遮罩）—— 最有效的一条：有它才有"模态/在上层"的读感
                  （站内先例：`Strategies.vue` 详情弹窗用 `bg-black/50`）；
               ② **亮描边** `border-white/15` 取代几乎看不见的 `--border`；
               ③ **深投影**（自定义，`shadow-2xl` 只有 25% 黑，在深色底上等于没有）。 -->
        <div v-if="macroOpen"
             class="fixed inset-0 z-40 bg-black/60 backdrop-blur-[2px]"
             @click="macroOpen = false"></div>
        <!-- ★★ 2026-09-29（用户："顶部的宏观与环境点击显示的弹窗长度都溢出屏幕了，看不完整"）：
             【原实现的两个硬伤】`absolute left-0 top-full w-[min(92vw,720px)] max-h-[70vh]`
             ① **水平溢出**：触发按钮在顶栏**右侧**，`left-0`（左边缘对齐按钮）⇒ 720px 宽的面板
                向右**伸出视口**，右半边看不见（"看不完整"的真因）；
             ② **垂直没预留顶栏**：`70vh` 是按**视口**算的，但面板起点在顶栏下方
                （另有 `App.vue` 的 `sticky top-0 z-50` 导航占 48px）⇒ 实际底部 = 起点 + 70vh
                可能越过屏幕下沿；且浮层挂在可换行的顶栏行内，高度会被行布局牵制。
             【改法】**Teleport 到 body + fixed 定位**（与全站既有弹窗同款，见 `Strategies.vue`
             的详情弹窗）：`left-1/2 -translate-x-1/2` 水平居中 ⇒ **两侧都不可能出屏**；
             `top-[3.25rem]`（正好在 48px 导航 + 余量之下）+ `max-h-[calc(100vh_-_4rem)]`
             ⇒ 底部恒在屏幕内 12px 处、**结构上不可能超出屏幕**，超出走**内部滚动**
             （`overscroll-contain` 防滚动穿透到页面）。
             ⚠️ Teleport 还顺带摆脱了任何祖先 `overflow/sticky` 的裁剪与层叠（`z-50` 与导航同级，
                靠 body 末尾的 DOM 顺序压住）。宽度仍给足 720px —— 卡内有 `md:grid-cols-2`
                （断点按**视口**算），太窄会把两栏挤在一起。
             ★★ 2026-09-29 追加（用户："点击显示之后，滚动到最右边，溢出部分的背景色怎么是透明的"）：
                **滚动容器自身必须带底色**（`bg-card`）。原因：卡内那一行是
                `flex` + 多个 `flex-shrink-0` 段（刻意"严格单行不换行"，见 `MacroEnvCard` 注释）
                ⇒ 面板变窄时内容会比容器宽；而卡片是 `w-full` 块级元素，它的底色**只覆盖自身矩形**
                ⇒ 横向滚到卡片右边缘之外的那片区域就露出页面（透明）。
                ⇒ 由**滚动容器**兜底上色（`overflow-x-auto` 显式写出，表明"会横滚、且已兜色"）。
                ⚠️ 不能用 `overflow-x-hidden` 压制：那会把"事件诊断/叙事"整段裁掉（宁可能滚，不可丢信息）。 -->
        <Teleport to="body">
          <div v-if="macroOpen"
               class="fixed left-1/2 -translate-x-1/2 top-[3.25rem] z-50
                      w-[min(96vw,1120px)] max-h-[calc(100vh_-_4rem)]
                      overflow-y-auto overflow-x-auto overscroll-contain
                      bg-card border border-white/15 rounded-lg
                      shadow-[0_24px_64px_-16px_rgba(0,0,0,0.9)]"
               @click.stop>
            <!-- ★ 2026-09-29（用户："你改为展示全部吧，宽度自适应"）：
                 `full` ⇒ 卡片首行**可换行、长文本不截断**（浮层里没有"必须单行"的理由，
                 反而截断会藏住 叙事/仓位建议 的全文）；
                 宽度 `min(96vw,1120px)` **自适应屏宽**（原固定 720px 是为了"单行塞得下"，
                 现在既然可换行，就用足屏幕 —— 两列 grid（支撑/压制、过热/过冷）也更舒展）。 -->
            <MacroEnvCard full :macro="macro" :macro-err="macroErr" :temperature="temperature"
                          :stagflation="stagflation"
                          :flash-diag="flashDiag" :sizing="sizing" :sentiment="macroSentiment"
                          :futures-basis="macroFuturesBasis" :liquidity="macroLiquidity" />
          </div>
        </Teleport>
      </div>

      <!-- 情绪/市况模块（归并为一组）；★ 2026-09-25 用户反馈：顶栏一行放不下，
           「数据新鲜度」从独立项挪到本组**下方**（组内竖排）⇒ 顶栏少一个 item，
           挤换行的概率大幅下降；新鲜度改 10px 与市况副标题同级。 -->
      <div class="border-l border-border pl-4 flex flex-col gap-1">
        <div class="flex items-center gap-3">
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
        <!-- ★ 2026-09-27 事件驱动信号（v0 展示项）：仅 E1/E2 触发时占位 ——
             稀有事件（21 年 298 天），平时不出现，避免顶栏常驻噪音。
             副标题「事件·仅提示」是**定位说明**（项目约定），防止被当买卖信号。 -->
        <template v-if="eventSignal && eventSignal.available && (eventSignal.e2_policy_surge || eventSignal.e1_capitulation)">
          <div class="w-px h-8 bg-border"></div>
          <div class="text-center" :title="eventTitle">
            <div class="text-xs font-semibold"
                 :class="eventSignal.e2_policy_surge ? 'text-red-400' : 'text-cyan-400'">
              {{ eventSignal.e2_policy_surge ? '政策脉冲' : '冰点' }}
            </div>
            <div class="text-[10px] text-muted">事件·仅提示</div>
          </div>
        </template>
      </div>
      <!-- ★ 2026-09-25：原「数据新鲜度 · 截至 HH:MM」入口已**并入日期下方的系统状态行**
           （同一点、同一浮层）⇒ 此处删除，避免同一份状态在顶栏出现两次。 -->
    </div>
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
              :class="[nodeClass(p.key),
                       selectedPhase === p.key
                         ? 'ring-2 ring-accent/70 shadow-[0_0_14px_rgba(88,166,255,0.45)]'
                         : (isToday && livePhase === p.key
                            ? 'ring-1 ring-accent/40 animate-pulse' : '')]"
              @click="selectPhase(p.key)">
        <!-- 徽标：未决策教练卡数（当天） / 简报降级 -->
        <span v-if="badge(p.key)" class="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1
                                        rounded-full text-[10px] leading-[18px] text-center font-bold shadow"
              :class="p.key === 'premarket' && briefDegraded ? 'bg-amber-500 text-black' : 'bg-red-500 text-white'">
          {{ badge(p.key) }}
        </span>
        <div class="text-sm font-bold tracking-wide truncate" :class="PHASE_STYLE[p.key].text">{{ p.label }}</div>
      </button>
    </div>

    <!-- 主体：主工作区（随阶段切换/懒加载） + 常驻右栏 -->
    <div class="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-3 items-start">
      <!-- ── 主工作区 ── -->
      <div class="space-y-3 min-w-0">
        <!-- ★ 2026-10-24（用户："观察器独占一行"）：3:2 并排改为单列，观察器整行铺满；教练卡移回右栏。 -->
        <div class="grid grid-cols-1 gap-3 items-stretch">
        <!-- ★ 2026-10-23：观察器合并（盘中变化 / 拥挤与关键位 / 推演证伪 / AI 篮子）
             原 4 张卡 → 1 张卡 + 4 行摘要（默认收起，localStorage 记忆）。
             摘要行常显关键数字，避免"收起=藏信息"；红点=需关注/硬触发。 -->
        <div class="bg-card border border-border rounded-lg p-3 h-full flex flex-col">
          <div class="flex items-center justify-between mb-1 shrink-0">
            <!-- ★ 2026-10-26（用户："这些文字放在一个 ? 里"）：原来卡底常显 2 行判据/配色说明，
                 占高且每次都在抢注意力 ⇒ 收进标题旁的 `?`（悬停看全文，内容一字不减）。 -->
            <div class="text-sm font-semibold flex items-center gap-1">
              <span>观察器</span>
              <span class="text-[10px] text-muted font-normal">（纯观察 · 不进决策链）</span>
              <span class="text-[10px] text-muted cursor-help border border-border rounded-full px-1 leading-[14px]"
                    :title="obsHelp">?</span>
            </div>
            <button type="button" class="text-[10px] text-muted hover:text-accent"
                    :title="obsAllOpen ? '全部收起' : '全部展开'"
                    @click="toggleAllObs">{{ obsAllOpen ? '全部收起 ▴' : '全部展开 ▾' }}</button>
          </div>

          <div class="flex-1 min-h-0 overflow-y-auto pr-1">
          <!-- ① 盘中变化 -->
          <div class="border-t border-border/40 py-1.5">
            <button type="button" class="w-full flex items-center gap-2 text-left"
                    :title="wsPick?.reasons?.map(x => x.text).join('；') || ''"
                    @click="wsOpen = !wsOpen; saveObs()">
              <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="wsDot"></span>
              <span class="text-xs font-semibold shrink-0">盘中变化</span>
              <span class="text-[10px] text-muted truncate flex-1">{{ wsLine }}</span>
              <span class="text-[10px] text-muted shrink-0">{{ wsOpen ? '▾' : '▸' }}</span>
            </button>

          <div v-if="wsOpen" class="mt-1.5 rounded-md border border-border/60 bg-white/[0.06] p-2">
          <div v-if="wsLoading" class="text-xs text-muted">加载中…</div>
          <div v-else-if="wsErr" class="text-xs text-muted mt-2">{{ wsErr }}</div>
          <template v-else-if="wsDay">
            <div v-if="!wsRows.length" class="text-xs text-muted mt-2">
              该日尚无采样——检测器 2026-10-21 上线，交易时段每 30 分钟一档。
            </div>
            <template v-else>
              <div class="flex flex-wrap gap-1.5 mt-2">
                <button v-for="r in wsRows" :key="r.slot"
                        @click.stop="wsSel = r.slot; wsOpen = true"
                        class="px-1.5 py-0.5 rounded border text-[11px] font-mono transition"
                        :class="[wsColor(r.score), wsSel === r.slot ? 'ring-1 ring-accent' : 'opacity-80 hover:opacity-100']"
                        :title="(r.reasons || []).map(x => x.text + (x.pts ? ' +' + x.pts : '')).join('\n')">
                  {{ r.slot }}<span v-if="r.worth" class="ml-0.5" :class="wsVerdict(r).cls">●</span><span class="ml-1">{{ r.score }}</span>
                </button>
              </div>

              <div v-if="wsOpen && wsPick" class="mt-2 border-t border-border/40 pt-2 text-xs space-y-1">
                <div class="flex items-center justify-between">
                  <span class="text-muted">{{ wsPick.slot }} · 交易 {{ wsPick.trading_min }} 分钟<span v-if="wsPick.as_of"> · {{ wsPick.as_of }}</span></span>
                  <span :class="wsVerdict(wsPick).cls">
                    {{ wsVerdict(wsPick).cn }}<template v-if="wsPick.bias"> · 方向 {{ wsBiasText(wsPick) }}</template>
                    · {{ wsPick.score }} 分</span>
                </div>
                <div v-if="!(wsPick.reasons || []).length" class="text-muted">—（无触发项）</div>
                <div v-for="(x, i) in (wsPick.reasons || [])" :key="i" class="flex gap-2">
                  <span class="text-muted font-mono w-20 shrink-0" :title="x.tag">{{ wsTagCn(x.tag) }}</span>
                  <!-- 逐条方向：利多/利空（红/绿）或中性；后端 dir 字段（老数据为空 ⇒ 中性） -->
                  <span class="shrink-0 text-[10px] w-7" :class="wsDirCls(x.dir)">{{ wsDirCn(x.dir) }}</span>
                  <span class="flex-1">{{ x.text }}</span>
                  <span v-if="x.pts" class="text-accent font-mono shrink-0">+{{ x.pts }}</span>
                </div>
                <div v-if="wsPick.leader_cur" class="text-muted">领涨板块：{{ wsPick.leader_cur }}</div>
              </div>

              <div class="text-[10px] text-muted mt-2">
                判据：指数 30 分钟移动 / 宽度翻转 / 涨跌停变化 / 领涨切换 / 区间成交额 / 快讯；
                ≥40 分为「有变化」，再按方向分 <b class="text-rose-300">正影响</b>/<b class="text-emerald-300">反影响</b>
                （|方向分|≥20），方向不明记「中性变化」。放量/换主线/快讯条数只算变化、不判方向。
                展示项，不进决策链。
              </div>
            </template>
          </template>
          </div>
          </div>

          <!-- ② 拥挤与关键位（AI 篮子在④） -->
          <div class="border-t border-border/40 py-1.5">
            <button type="button" class="w-full flex items-center gap-2 text-left"
                    @click="crowdOpen = !crowdOpen; saveObs()">
              <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="crowdDot"></span>
              <span class="text-xs font-semibold shrink-0">拥挤与关键位</span>
              <span class="text-[10px] text-muted truncate flex-1">{{ crowdLine }}</span>
              <span class="text-[10px] text-muted shrink-0">{{ crowdOpen ? '▾' : '▸' }}</span>
            </button>
            <div v-if="crowdOpen" class="mt-1.5 rounded-md border border-border/60 bg-white/[0.06] p-2">
          <div v-if="riskLoading" class="text-xs text-muted">加载中…</div>
          <div v-else-if="riskErr" class="text-xs text-muted">{{ riskErr }}</div>
          <div v-else class="grid grid-cols-1 lg:grid-cols-2 gap-3 text-xs">
            <div>
              <!-- ★ 2026-10-29（用户：\"拥挤与关键位盘中不显示\"）：盘中今天的收盘数据还没算出来，
                   后端已回退到最近一次收盘 ⇒ 这里必须**明确标出实际日期**，
                   否则用户会以为这是今日拥挤度（和"领跌把涨幅最小当下跌"同类的误导）。 -->
              <div class="text-[11px] text-muted mb-1">拥挤赛道（30日 / 高位占比 / 距120日高）
                <span v-if="crowd?.fallback" class="text-amber-400/80"
                      :title="`今日 ${crowd.requested_date} 的收盘数据尚未生成，这里显示的是 ${crowd.date} 收盘结果`">
                  · {{ crowd.date }} 收盘</span></div>
              <div v-if="!crowd || !(crowd.rows || []).length" class="text-muted">—</div>
              <div v-else class="space-y-0.5">
                <div v-for="r in crowdTop" :key="r.industry" class="flex items-center gap-2">
                  <span class="w-16 shrink-0 truncate" :title="(r.leaders || []).join('、')">{{ r.industry }}</span>
                  <span class="w-14 text-right font-mono"
                        :class="(r.ret30 || 0) >= 0 ? 'text-rise' : 'text-fall'">{{ fmtPct(r.ret30) }}</span>
                  <span class="w-12 text-right font-mono text-muted"
                        :title="'pos120≥0.9 的成员占比'">{{ Math.round((r.pos_high || 0) * 100) }}%</span>
                  <span class="w-14 text-right font-mono"
                        :class="(r.dd120 || 0) <= -25 ? 'text-rose-400' : 'text-muted'">{{ fmtPct(r.dd120) }}</span>
                  <span class="px-1 rounded text-[10px]" :class="crowdBadge(r.level)">{{ r.level }}</span>
                </div>
              </div>
            </div>
            <div>
              <div class="text-[11px] text-muted mb-1">指数关键位（前低 / 整数位 / 年内低点）
                <span v-if="keyLv?.fallback" class="text-amber-400/80"
                      :title="`今日 ${keyLv.requested_date} 的收盘数据尚未生成，这里显示的是 ${keyLv.date} 收盘结果`">
                  · {{ keyLv.date }} 收盘</span></div>
              <div v-if="!keyLv || !(keyLv.rows || []).length" class="text-muted">—</div>
              <div v-else class="space-y-0.5">
                <div v-for="r in keyLv.rows" :key="r.code" class="flex items-center gap-2">
                  <span class="w-14 shrink-0 truncate">{{ r.name }}</span>
                  <span class="w-16 text-right font-mono">{{ r.close }}</span>
                  <span class="w-14 text-right font-mono"
                        :class="(r.chg_pct || 0) >= 0 ? 'text-rise' : 'text-fall'">{{ fmtPct(r.chg_pct) }}</span>
                  <span class="w-20 text-right text-muted" :title="r.detail">{{ r.support_type }} {{ r.support }}</span>
                  <span class="w-12 text-right font-mono"
                        :class="(r.dist_pct ?? 99) <= 1.5 ? 'text-amber-300' : 'text-muted'">{{ fmtPct(r.dist_pct) }}</span>
                  <span class="px-1 rounded text-[10px]" :class="keyBadge(r.status)">{{ r.status }}</span>
                </div>
              </div>
            </div>
          </div>
            </div>
          </div>

          <!-- ★★ 2026-10-30（用户："现在缺国内的消息"）：金十每日汇总。
               ① **原文直出**，不经 LLM 改写 —— 这些汇总是被人提炼过的结论，
                  再让 LLM 总结一遍属于信息损耗（"四部门提高自贸港便利化"会被
                  压成"利好海南"，丢掉"两类库容/LNG期货交割"这些可用细节）；
               ② 每天约 50~60 条，**整块折叠 + 每条点击展开正文**，不占版面；
               ③ 数据缺失时给出可行动的原因（JIN10_X_TOKEN 过期是唯一需要人工处理的故障）。 -->
          <div class="border-t border-border/40 py-1.5">
            <button type="button" class="w-full flex items-center gap-2 text-left"
                    @click="jin10Open = !jin10Open; saveObs()">
              <span class="w-1.5 h-1.5 rounded-full shrink-0"
                    :class="(jin10?.sections || []).length ? 'bg-sky-400' : 'bg-slate-500'"></span>
              <span class="text-xs font-semibold shrink-0">金十消息面</span>
              <span class="text-[10px] text-muted truncate flex-1">{{ jin10Line }}</span>
              <span class="text-[10px] text-muted shrink-0">{{ jin10Open ? '▾' : '▸' }}</span>
            </button>
            <div v-if="jin10Open" class="mt-1.5 rounded-md border border-border/60 bg-white/[0.06] p-2">
              <div v-if="!(jin10?.sections || []).length" class="text-xs text-muted">
                暂无数据（汇总每天盘后生成；若长期为空，请检查 Render 的 JIN10_X_TOKEN 是否过期）
              </div>
              <template v-else>
                <div class="text-[10px] text-muted mb-1.5">
                  {{ jin10.date }}
                  <span v-if="jin10.fallback" class="text-amber-400/80"
                        :title="`请求 ${jin10.requested_date} 无数据，此处显示最近一期`">
                    · 非当日（该日无数据）</span>
                  · 原文未解读
                </div>
                <div class="space-y-2 max-h-96 overflow-y-auto pr-1">
                  <div v-for="sec in jin10.sections" :key="sec.category_id" class="space-y-0.5">
                    <div class="text-[11px] font-medium text-gray-300 flex items-center gap-1.5">
                      <span>{{ sec.name }}</span>
                      <span class="text-muted font-normal">{{ sec.items.length }} 条</span>
                    </div>
                    <div v-for="(it, i) in sec.items" :key="sec.category_id + '-' + i"
                         class="pl-1.5 border-l border-border/40">
                      <button type="button" class="w-full text-left flex items-start gap-1.5"
                              @click="openJin10 = (openJin10 === sec.category_id + '-' + i ? '' : sec.category_id + '-' + i)">
                        <span class="text-[11px] text-gray-300 leading-snug flex-1">{{ it.title }}</span>
                        <span v-for="t in it.tags" :key="t"
                              class="shrink-0 px-1 rounded text-[9px]" :class="jin10TagCls(t)">{{ t }}</span>
                      </button>
                      <div v-if="openJin10 === sec.category_id + '-' + i"
                           class="text-[10px] text-muted leading-relaxed mt-0.5">
                        {{ it.content || '（该条只有标题，无正文）' }}
                      </div>
                    </div>
                  </div>
                </div>
              </template>
            </div>
          </div>

          <!-- ③ 推演证伪 -->
          <div class="border-t border-border/40 py-1.5">
            <button type="button" class="w-full flex items-center gap-2 text-left"
                    @click="scenOpen = !scenOpen; saveObs()">
              <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="scenDot"></span>
              <span class="text-xs font-semibold shrink-0">推演证伪</span>
              <span v-if="scen" class="shrink-0 px-1.5 py-0.5 rounded text-[10px] font-medium"
                    :class="scenBadgeClass">{{ scen.scenario }}</span>
              <span v-if="scen && scen.scenario_candidate"
                    class="shrink-0 px-1.5 py-0.5 rounded text-[10px] bg-rose-500/20 text-rose-300"
                    :title="scen.scenario_candidate">候选</span>
              <span class="text-[10px] text-muted truncate flex-1">{{ scenLine }}</span>
              <span class="text-[10px] text-muted shrink-0">{{ scenOpen ? '▾' : '▸' }}</span>
            </button>
            <!-- ★ 2026-10-24（用户："展开内容没有边框和背景色、不明显、边距太挤"）：
                 与「盘中变化 / 拥挤与关键位」统一成同一视觉块（圆角+边框+浅底+内边距），
                 信号格拉开 gap，日历/锚点/说明各自用分隔线分组。 -->
            <div v-if="scenOpen"
                 class="mt-1.5 rounded-md border border-border/60 bg-white/[0.06] p-2.5">
              <div v-if="!scen" class="text-xs text-muted">加载中…</div>
              <div v-else class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-2 text-xs">
                <div v-for="s in scen.signals" :key="s.key"
                     class="flex items-center gap-1.5 rounded px-1 py-0.5 hover:bg-white/[0.03]">
                  <span class="w-2 h-2 rounded-full shrink-0" :class="leanDot(s.lean)"></span>
                  <span class="text-muted shrink-0">{{ s.label }}</span>
                  <span class="font-mono text-gray-200" :title="s.note">{{ scenVal(s) }}</span>
                </div>
              </div>
              <div v-if="scen"
                   class="mt-2.5 pt-2 border-t border-border/50 text-[11px] text-muted flex flex-wrap gap-x-3 gap-y-1">
                <span v-for="e in scen.events" :key="e.date"
                      :class="e.soon ? 'text-amber-300' : ''"
                      :title="e.note">{{ e.name }}
                  <span class="font-mono">{{ e.t_minus == null ? '' : (e.t_minus < 0 ? '已过' : 'T-' + e.t_minus + 'd') }}</span>
                </span>
              </div>
              <div v-if="scen" class="mt-2 pt-2 border-t border-border/50 space-y-1">
                <div v-if="scen.anchor" class="text-[10px]"
                     :class="scen.anchor.state === '跌破' ? 'text-rose-300' : 'text-emerald-300'">
                  锚点 {{ scen.anchor.level }}：{{ scen.anchor.state }}
                  <span v-if="scen.anchor.close">（收盘 {{ scen.anchor.close }}）</span>；
                  跌破 → 下看 {{ scen.anchor.downside }}；{{ scen.anchor.fix }}
                </div>
                <div v-if="scen.geo_alert" class="text-[10px] text-rose-300">
                  ★ 地缘硬触发：金十已出现伊朗实际动能事件（空袭/布雷/封锁等）；情景仍按油价/10Y/点位判定。
                </div>
                <div v-if="scen.geo_candidate" class="text-[10px] text-rose-300">
                  ★ 地缘候选：近 48h 动能 ≥3 条 ⇒ 熊市情景候选（仅提示，不自动改仓）。
                </div>
                <!-- ★ 2026-10-31（用户："降档等只出文字提示，不碰仓位"）：
                     高影响事件（选举/FOMC）T-N 天内的文字提示。
                     刻意**不做成自动降档** —— 仓位由人决定，系统只按时提醒。 -->
                <div v-if="scen.event_alert?.active" class="text-[10px] text-amber-300">
                  {{ scen.event_alert.text }}
                </div>
                <div class="text-[10px] text-muted leading-relaxed">{{ scen.note }}</div>
              </div>
            </div>
          </div>

          <!-- ④ AI 硬件篮子 -->
          <div v-if="aiBasket && aiBasket.basket" class="border-t border-border/40 py-1.5">
            <button type="button" class="w-full flex items-center gap-2 text-left"
                    @click="aiOpen = !aiOpen; saveObs()">
              <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="aiDot"></span>
              <span class="text-xs font-semibold shrink-0">AI 硬件篮子</span>
              <span class="text-[10px] text-muted truncate flex-1">{{ aiLine }}</span>
              <span class="text-[10px] text-muted shrink-0">{{ aiOpen ? '▾' : '▸' }}</span>
            </button>
            <div v-if="aiOpen" class="mt-1.5 text-xs rounded-md border border-border/60 bg-white/[0.06] p-2">
              <div class="flex items-center gap-3 flex-wrap">
                <span class="font-mono" :class="(aiBasket.basket.dd120 || 0) <= -25 ? 'text-rose-300' : 'text-muted'">
                  回撤 {{ fmtPct(aiBasket.basket.dd120) }}</span>
                <span class="font-mono" :class="(aiBasket.basket.ret5 || 0) >= 0 ? 'text-rise' : 'text-fall'">
                  5日 {{ fmtPct(aiBasket.basket.ret5) }}</span>
                <span class="font-mono" :class="(aiBasket.basket.ret30 || 0) >= 0 ? 'text-rise' : 'text-fall'">
                  30日 {{ fmtPct(aiBasket.basket.ret30) }}</span>
                <span class="text-muted">高位占比
                  {{ Math.round((aiBasket.basket.pos120 || 0) * 100) }}%</span>
              </div>
              <div class="flex flex-wrap gap-x-3 gap-y-0.5 mt-1 text-[11px]">
                <span v-for="r in (aiBasket.rows || [])" :key="r.code" class="text-muted">
                  {{ r.name }}
                  <span class="font-mono" :class="(r.dd120 || 0) <= -40 ? 'text-rose-300' : ''">{{ fmtPct(r.dd120) }}</span>
                </span>
              </div>
            </div>
          </div>

          <!-- ★★ 2026-10-24（用户："观察器什么时候能加上 llm 分析？"）：⑤ AI 解读。
               默认收起、点开才请求（同步 LLM）；按日缓存 + 数据指纹，读数变了会自动重算。
               定位：**只做性质判断（反弹/反转）+ 推翻条件**，不给仓位/买卖建议。 -->
          <div class="border-t border-border/40 py-1.5">
            <button type="button" class="w-full flex items-center gap-2 text-left"
                    @click="toggleObsAI"
                    :title="obsAIOpen ? '点击收起' : '点开让 AI 解读今天的观察器读数（首次会调用 LLM）'">
              <span class="w-1.5 h-1.5 rounded-full shrink-0"
                    :class="obsAI.loading ? 'bg-sky-400 animate-pulse'
                            : (obsAI.error ? 'bg-amber-400'
                               : (obsAI.conclusion ? 'bg-accent' : 'bg-slate-500'))"></span>
              <span class="text-xs font-semibold shrink-0">AI 解读</span>
              <span class="text-[10px] text-muted truncate flex-1">{{ obsAISummary }}</span>
              <span class="text-[10px] text-muted shrink-0">{{ obsAIOpen ? '▾' : '▸' }}</span>
            </button>
            <div v-if="obsAIOpen"
                 class="mt-1.5 rounded-md border border-border/60 bg-white/[0.06] p-2.5">
              <div class="flex items-center gap-2 flex-wrap text-[10px] text-muted mb-1">
                <span v-if="obsAI.as_of">数据截至 {{ obsAI.as_of }}</span>
                <span v-if="obsAI.cached">· 当日缓存</span>
                <span v-if="obsAI.loading">· 分析中…</span>
                <button class="ml-auto px-2 py-0.5 rounded border border-border text-[10px]
                               text-accent hover:border-accent/50 disabled:opacity-50"
                        :disabled="obsAI.loading"
                        title="强制重算（消耗 1 次 LLM 调用）"
                        @click="loadObsAI(true)">
                  {{ obsAI.loading ? '分析中…' : '重新解读' }}
                </button>
              </div>
              <div v-if="obsAI.loading" class="text-[11px] text-muted">
                分析中…（LLM 同步调用，约十几秒）
              </div>
              <div v-else-if="obsAI.error" class="text-[11px] text-amber-400">{{ obsAI.error }}</div>
              <InsightBody v-else-if="obsAI.markdown || obsAI.conclusion"
                           :text="obsAI.markdown"
                           :conclusion="obsAI.conclusion" :reasons="obsAI.reasons"
                           feedback-source="observer"
                           :feedback-ref="'observer:' + (selectedDate || todayStr)"
                           collapsible class="text-xs" />
              <div v-else class="text-[11px] text-muted leading-relaxed">
                点击「重新解读」让 LLM 读一遍今天的观察器读数：定性（反弹 / 反转 / 无效）、
                还缺哪几条打勾、以及**看到什么就说明这个判断错了**。
              </div>
            </div>
          </div>

          <!-- ★★ 2026-10-27（用户："市场底清单也是按日更新，放进观察器"）：⑤ 市场底确认清单。
               与其余观察器块**同一时效档**（日频：收盘定稿 / 周线 / 每日宏观；
               只有①国家队可能盘中变 —— 人工打勾、公告、ETF 份额），
               ⇒ 轮询固定 **300s**（不进 60s 快组），异动时由 `onIntradayAlert` 立即重拉。 -->
          <div class="border-t border-border/40 py-1.5">
            <button type="button" class="w-full flex items-center gap-2 text-left"
                    @click="togglePbw()"
                    :title="pbwOpen ? '点击收起' : '点击展开（清单 6 条，达成 4 条视为确认）'">
              <span class="w-1.5 h-1.5 rounded-full shrink-0"
                    :class="!pbw ? 'bg-slate-500'
                            : (pbw.n_checked >= (pbw.n_pass || 4) ? 'bg-rose-400' : 'bg-amber-400')"></span>
              <span class="text-xs font-semibold shrink-0">市场底确认清单</span>
              <span class="text-[10px] text-muted truncate flex-1">{{ pbwLine }}</span>
              <span class="text-[10px] text-muted shrink-0">{{ pbwOpen ? '▾' : '▸' }}</span>
            </button>
            <div v-if="pbwOpen && pbw" class="mt-1.5 rounded-md border border-border/60 bg-white/[0.06] p-2">
              <div class="flex items-center justify-between mb-1 text-[10px]">
                <span :class="pbw.n_checked >= (pbw.n_pass || 4) ? 'text-red-400' : 'text-amber-400'">
                  {{ pbw.position_hint }}</span>
                <span class="text-muted font-mono">{{ pbw.n_checked }}/{{ pbw.n_total }}</span>
              </div>
              <div class="space-y-1 text-[11px]">
                <div v-for="it in pbwItems" :key="it.key" class="space-y-0.5">
                  <div class="flex items-start gap-2">
                    <span class="shrink-0" :class="it.checked ? 'text-emerald-400' : 'text-muted'">
                      {{ it.checked ? '✅' : '⬜' }}<template
                        v-if="!it.checked && it.insufficient"><span class="text-[10px]">数据不足</span></template>
                    </span>
                    <span class="text-muted w-20 shrink-0">{{ it.label }}</span>
                    <span class="flex-1 break-all">{{ it.detail }}</span>
                    <span v-if="it.key === 'nation_team_ann' && it.hintCount"
                          class="shrink-0 px-1 rounded bg-amber-500/15 text-amber-400 text-[10px]"
                          :title="it.hintText">{{ it.hintCount }} 条公告</span>
                    <button v-if="it.key === 'nation_team_ann'"
                            class="px-1.5 py-0.5 rounded border border-border/60 text-[10px] hover:border-accent shrink-0"
                            :title="it.checked ? '取消人工确认' : '人工确认（有汇金/国新等公告时点这里）'"
                            @click.stop="toggleNationTeam">
                      {{ it.checked ? '取消' : '打勾' }}
                    </button>
                  </div>
                  <!-- ① 三层证据：公告（只提示不打勾）/ ETF 份额（连续 2 日 ≥8% 自动打勾） -->
                  <div v-if="it.key === 'nation_team_ann'"
                       class="ml-6 pl-1.5 border-l border-border/40 space-y-0.5">
                    <a v-for="h in it.hints.slice(0, 6)" :key="h.id"
                       :href="h.url || undefined" target="_blank" rel="noopener"
                       class="block text-[10px] text-amber-300/90 hover:underline break-all"
                       :title="h.reason + (h.url ? '' : '（该条无原文链接）')">
                      🔔 {{ h.date }} {{ h.code }} {{ h.name }}：{{ h.title }}
                    </a>
                    <div class="flex items-center gap-2 text-[10px]"
                         :class="it.hintStatus === 'unavailable' ? 'text-red-400'
                                 : (it.hintCount ? 'text-amber-400/80' : 'text-muted')">
                      <span class="flex-1 break-all">{{ it.hintText || '自动检查：尚未运行' }}</span>
                      <button class="shrink-0 hover:text-accent disabled:opacity-50"
                              :disabled="annRefreshing"
                              title="手动重扫巨潮公告（只更新提示，不推企微、不打勾）"
                              @click.stop="refreshAnnouncements">
                        {{ annRefreshing ? '扫描中…' : '重扫' }}
                      </button>
                    </div>
                  </div>
                  <div v-if="it.key === 'nation_team_ann'"
                       class="ml-6 pl-1.5 border-l border-border/40 space-y-0.5">
                    <div class="flex items-center gap-2 text-[10px]"
                         :class="it.etfStatus === 'confirmed' ? 'text-red-400'
                                 : (it.etfStatus === 'watch' ? 'text-amber-400/80' : 'text-muted')">
                      <span class="flex-1 break-all">{{ it.etfText || '份额异动：尚未运行' }}</span>
                      <button class="shrink-0 hover:text-accent disabled:opacity-50"
                              :disabled="etfRefreshing"
                              title="手动重算宽基 ETF 份额（上交所官方；连续 2 日 ≥8% 会自动打勾）"
                              @click.stop="refreshEtf">
                        {{ etfRefreshing ? '计算中…' : '重算' }}
                      </button>
                    </div>
                    <div v-for="r in it.etfHits.slice(0, 5)" :key="r.code"
                         class="text-[10px] text-muted break-all">
                      {{ r.code }} {{ r.name }}：今日
                      <span :class="r.chg_pct >= 8 ? 'text-amber-400' : ''">{{ Number(r.chg_pct).toFixed(2) }}%</span>
                      <template v-if="r.prev_chg_pct !== null && r.prev_chg_pct !== undefined">
                        ｜前一日 {{ Number(r.prev_chg_pct).toFixed(2) }}%
                      </template>
                      <span v-if="r.streak >= 2" class="text-red-400">｜连续 {{ r.streak }} 日</span>
                    </div>
                    <div v-if="it.flowText" class="text-[10px] text-muted">{{ it.flowText }}</div>
                    <div v-if="it.manualDecided" class="text-[10px] text-muted">
                      （已有人工判断，自动结果不覆盖）
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>

        </div>


        </div>

        </div>

        <!-- ★★ 2026-10-26（用户：盘面速览与快讯解读从观察器容器里独立出来，左卡/右卡）：
             原来二者被误嵌在观察器卡内部（插入点落在那张卡的脚注之后、卡闭合之前）⇒ 视觉上是"卡中卡"。
             现在它们是观察器**同级**的两列：左＝盘面速览（涨跌/主线/持仓），右＝快讯解读（事件驱动）。
             ⚠️ 断点 xl：窄屏自动上下堆叠（先盘面、后快讯）。 -->
        <div class="grid grid-cols-1 xl:grid-cols-[3fr_2fr] gap-3 items-start">
          <!-- ★★ 2026-10-26（用户：把快讯解读 / 市场涨跌 / 主线Top5 / 持仓状态整合为**一张大卡**，
               置于观察器下方；消除市场涨跌在多处重复）：
               · 涨跌家数/涨停跌停/成交额/情绪 —— 全站**只此一处**（原盘中看盘序、盘后结算卡各有一份）；
               · 主线 Top5（含领涨股+代码）与行业成交占比收进左下（占比默认折叠，避免抢注意力）；
               · 持仓状态收进右下（组合风控一行摘要 + 逐只精简行，滚动区内滚，不撑高整卡）；
               · 2026-10-26 调整：快讯解读改为**右侧独立卡片**（本卡只留涨跌/主线/持仓）。
               ⚠️ 数据时刻一律用**数据自身时刻**（ovStyle.data_date / radarQuote），不用请求时刻顶替。 -->
          <div id="wb-pulse" class="bg-card border border-border rounded-lg p-3">
            <div class="flex items-center justify-between mb-1.5 flex-wrap gap-2">
              <div class="text-sm font-semibold">盘面速览
                <span class="text-[10px] text-muted font-normal">涨跌 · 主线 · 持仓</span>
              </div>
              <span class="text-[10px] font-mono cursor-help"
                    :class="ovStyle?.from_snapshot ? 'text-amber-400' : 'text-muted'"
                    :title="ovStyle?.from_snapshot
                      ? `行情缓存来自收盘快照 ⇒ 数据是 ${ovStyle?.data_date || '上一交易日'} 收盘，不是今天的`
                      : `行情时刻 ${ovStyle?.as_of || '—'}（后端内存缓存，盘中每 2~3 分钟刷新）`">
                {{ ovStyle?.from_snapshot ? '收盘快照' : '盘中实时' }}<template
                  v-if="ovStyle?.data_date"> · {{ ovStyle.data_date }}</template><template
                  v-else-if="ovStyle?.as_of"> · {{ ovStyle.as_of }}</template>
              </span>
            </div>

            <!-- A 市场涨跌（全站唯一一处） -->
            <div class="grid grid-cols-3 md:grid-cols-6 gap-2 text-center py-2 border-t border-border/40">
              <div><div class="text-[10px] text-muted">上涨</div>
                <div class="text-base font-bold text-red-400 font-mono">{{ overview.stats?.up_count ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted">下跌</div>
                <div class="text-base font-bold text-emerald-400 font-mono">{{ overview.stats?.down_count ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted cursor-help" :title="limitTip()">涨停</div>
                <div class="text-base font-bold text-red-400 font-mono">{{ overview.stats?.limit_up ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted cursor-help" :title="limitTip()">跌停</div>
                <div class="text-base font-bold text-emerald-400 font-mono">{{ overview.stats?.limit_down ?? '—' }}</div></div>
              <div><div class="text-[10px] text-muted">两市成交额</div>
                <div class="text-base font-bold font-mono text-gray-200">
                  {{ amountRt?.available ? fmtYi(amountRt.cum_yi)
                     : (overview.stats?.total_amount ? (overview.stats.total_amount / 1e8).toFixed(0) + '亿' : '—') }}</div>
                <div v-if="amountRt?.available && amountRt.show_ratio" class="text-[10px] leading-tight">
                  <b :class="amountRt.verdict === '放量' ? 'text-red-400'
                           : amountRt.verdict === '缩量' ? 'text-emerald-400' : 'text-gray-400'">{{ amountRt.verdict }}</b>
                  {{ signNum(amountRt.ratio_pct) }}%
                </div>
              </div>
              <div><div class="text-[10px] text-muted">情绪判读</div>
                <div class="text-base font-bold"
                     :class="emotion?.verdict === '亢奋' ? 'text-red-400' : emotion?.verdict === '冰点' ? 'text-emerald-400' : 'text-amber-300'">
                  {{ emotion?.verdict || '—' }}</div></div>
            </div>

            <!-- ★★ 2026-10-26（用户："大小盘风格 + 涨停赚钱效应 简化描述后放到涨跌下面"）：
                 从「盘中结构」卡移来，两行压成一行一行的小字：
                   · 长解释（"大小盘基本同步，无明显风格偏离"、板幅判定 5%/10%/20%/30%）**收进 hover**，
                     行内只留数字 —— 之前这两段常显文字把涨跌条下面塞满了；
                   · 快照态只写"快照 10-23"（原为"（10-23 20:38 快照，非实时）"）。 -->
            <div class="mt-1.5 space-y-0.5 text-[11px] text-muted">
              <div v-if="ovStyle?.available" class="flex items-center gap-x-2 gap-y-0.5 flex-wrap">
                <span>风格</span>
                <b class="cursor-help" :class="styleCls" :title="ovStyle.note || styleTitle">{{ ovStyle.label }}</b>
                <span class="font-mono">
                  大 <b :class="pctClass(ovBig?.avg)">{{ signNum(ovBig?.avg) }}%</b><template
                    v-if="ovMid"> / 中 <b :class="pctClass(ovMid?.avg)">{{ signNum(ovMid?.avg) }}%</b></template>
                  / 小 <b :class="pctClass(ovSmall?.avg)">{{ signNum(ovSmall?.avg) }}%</b>
                </span>
                <span class="cursor-help" title="背离 = 市值加权涨幅 − 等权涨幅；正=权重强于个股（做个股偏逆风），负=个股强于权重">
                  背离 <b class="font-mono text-gray-300">{{ signNum(ovStyle.spread) }}%</b></span>
                <span v-if="ovStyle.from_snapshot" class="text-[10px] text-amber-400/80 cursor-help"
                      title="行情缓存来自数据库收盘快照（盘后/周末不重新抓取）⇒ 不是此刻的实时风格">
                  快照 {{ String(ovStyle.as_of || '').slice(0, 10) }}</span>
              </div>
              <div v-if="emotion" class="flex items-center gap-x-2 gap-y-0.5 flex-wrap">
                <span>{{ prevLimitLabel() }}涨停 <b class="font-mono text-gray-200">{{ emotion.prev_limit_count ?? '—' }}</b> 只</span>
                <span>赚钱效应 <b :class="pctClass(emotion.prev_limit_today_pct)">{{ fmtPct(emotion.prev_limit_today_pct) }}</b></span>
                <span>连板 <b class="font-mono text-gray-200">{{ emotion.max_streak ?? '—' }}</b><template
                  v-if="emotion.leader">（{{ emotion.leader_name || emotion.leader }}）</template></span>
                <!-- 口径说明移到 ⓘ（原来常显一大段"按板幅判定（ST 5% / 主板 10% …），不再是 >=9.5% 一刀切"） -->
                <span class="text-[10px] cursor-help opacity-70"
                      :title="emotion.note || '涨跌停/连板按板幅判定：ST 5% / 主板 10% / 创业板·科创 20% / 北交所 30%（rt_k 精确优先）'">口径 ⓘ</span>
              </div>
            </div>

            <!-- B 左：主线 Top5 ｜ 右：持仓状态
                 ★ 2026-10-26（用户："两块之间加分割线 + 离上面涨跌远点"）：
                   ① `mt-4 pt-3`：与涨跌条的间距从 8px 拉到 28px（视觉分组更清楚）；
                   ② `lg:divide-x`：宽屏在**两列之间**画一条竖分割线（窄屏堆叠时不画）；
                   ③ 右列 `lg:pl-5`：分割线两侧各留呼吸位，文字不贴线。 -->
            <div class="grid grid-cols-1 lg:grid-cols-2 gap-y-3 lg:gap-x-0 lg:divide-x lg:divide-border/40 border-t border-border/40 mt-4 pt-3">
              <div class="min-w-0 lg:pr-4">
                <div class="flex items-center justify-between mb-1">
                  <div class="text-xs font-semibold">主线板块 Top5
                    <span class="text-[10px] text-muted font-normal">
                      {{ sectorTopSrc === 'east' ? '（实时 · 含领涨股）'
                         : sectorTopSrc === 'local' ? '（实时 · 本地聚合，龙头由涨停原因反推）'
                         : sectorTopSrc === 'zz' ? '（收盘定稿 · zzshare）'
                         : sectorTopSrc === 'snap' ? '（快照回退）' : '' }}</span></div>
                  <router-link target="_blank" to="/sector" class="text-[10px] text-accent hover:underline">板块详情</router-link>
                </div>
                <div v-if="!sectorTop.length" class="text-muted text-xs">—（板块数据未返回）</div>
                <div v-else class="space-y-0.5 text-xs">
                  <div v-for="(s, i) in sectorTop.slice(0, 5)" :key="i"
                       class="border-b border-border/30 py-0.5">
                    <div class="flex items-center gap-2">
                      <span class="w-3.5 text-muted font-mono">{{ i + 1 }}</span>
                      <span class="font-semibold truncate">{{ s.name || s.industry || s.code || '—' }}</span>
                      <span v-if="s.up_count != null" class="text-[10px] text-muted shrink-0">
                        涨{{ s.up_count }}/跌{{ s.down_count }}</span>
                      <!-- ★ 2026-10-26（第②步）：该板块命中的快讯 —— 与持仓行的「消息N」同一套投影逻辑 -->
                      <button v-if="sectorMsg(s.name)" type="button"
                              class="px-1 rounded border text-[10px] shrink-0 cursor-help"
                              :class="sectorMsgCls(sectorMsg(s.name))"
                              :title="`命中 ${sectorMsg(s.name).n} 条快讯 · 观察参考，非交易建议`"
                              @click.stop="toggleSectorMsg(s.name)">消息{{ sectorMsg(s.name).n }}</button>
                      <span class="ml-auto font-mono shrink-0" :class="pctClass(s.change_pct ?? s.pct_change)">
                        {{ signNum(s.change_pct ?? s.pct_change) }}%</span>
                    </div>
                    <div v-if="sectorMsg(s.name) && sectorMsgOpen[s.name]" class="pl-5 mt-0.5 space-y-0.5">
                      <div v-for="(hit, j) in sectorMsg(s.name).hits" :key="j"
                           class="text-[10px] text-muted leading-relaxed">
                        <span class="font-mono">[{{ hit.idx }}]</span>
                        <span class="ml-0.5" :class="hitCls(hit.direction)">{{ hit.direction }}</span>
                        <span class="ml-0.5">（{{ hit.match }}）</span>
                        <span class="ml-0.5">{{ hit.cause || hit.gist }}</span>
                      </div>
                    </div>
                  </div>
                  <!-- 领涨股：Top5 里每个有 leader 的板块各一行（紧凑 10px 行，不额外占卡片高度） -->
                  <div v-for="s in sectorTop.slice(0, 5).filter(x => x.leader)" :key="'ld' + (s.code || s.name)"
                       class="flex items-center gap-1.5 pl-5 text-[10px] text-muted">
                    <span>{{ s.name || s.industry }} 领涨</span>
                    <a v-if="s.leader_code" :href="stockHref(s.leader_code)" target="_blank"
                       class="hover:text-accent">{{ s.leader }}</a>
                    <span v-else>{{ s.leader }}</span>
                    <a v-if="s.leader_code" :href="xqUrl(s.leader_code)" target="_blank" rel="noopener"
                       class="font-mono hover:text-accent">{{ s.leader_code }}</a>
                    <!-- ★ 2026-10-28（用户："首板单独标出来"）：连板高度标签 ——
                         首板=蓝（当天新启动，主线**萌芽**）；≥2 连板=红（人气高，但接力风险也高）。
                         此前两者外观相同，看不出主线处在"启动"还是"发酵末期"。 -->
                    <span v-if="s.leader_boards_text"
                          class="px-1 rounded text-[9px] shrink-0 cursor-help"
                          :class="(s.leader_boards || 1) > 1
                            ? 'bg-rose-500/15 text-rose-300'
                            : 'bg-sky-500/15 text-sky-300'"
                          :title="(s.leader_boards || 1) > 1
                            ? `${s.leader} 已 ${s.leader_boards} 连板（人气高，接力风险也高）`
                            : `${s.leader} 今日首板（板块新启动）`">{{ s.leader_boards_text }}</span>
                    <span class="font-mono" :class="pctClass(s.leader_change_pct)">{{ signNum(s.leader_change_pct) }}%</span>
                  </div>
                </div>
                <!-- ★★★ 2026-10-28（用户：\"跟涨停梯队卡有冲突吗\"）——**已删除原「涨停结构」行**。
                     原因：它与「涨停梯队与清单」卡里的**连板梯队**是同一件事的两份数据，且数字不一致：

                       · 已有（zzshare 日批 `uplimit_hot.ban_info`）：首板 65 / 2板 6 / 3板 4 / 5板 1 = 76
                       · 我加（fuyao `page_boards`，实时）      ：首板 61 / 2板 5 / 3板 2 / 5板+ 1 = 69

                     同屏两个\"首板 65\"和\"首板 61\"必然被读成数据打架（用户当场就问出来了）。
                     已有的那份还带**断层判读**（\"4板为 0 但存在 5 板 ⇒ 高标孤立\"），功能更强
                     ⇒ **保留已有、删掉重复**。连板语义只在两处出现，且各司其职：
                       ① 个股级：本卡「XX 领涨 襄阳轴承 5连板」（`leader_boards_text`，标记主线阶段）
                       ② 市场级：「涨停梯队与清单」卡的连板梯队（zzshare 唯一口径）
                     ⚠️ 教训：加展示前要 grep **展示文案**，不能只 grep **字段名** ——
                        `limit_boards` 在 `overview.stats` 里躺着，并不代表页面上没有等价物。 -->
                <!-- 行业成交额占比：默认折叠（真金白银集中在哪，和涨幅榜互补） -->
                <details v-if="amountShare?.available" class="mt-1">
                  <summary class="text-[10px] text-muted cursor-pointer hover:text-accent">
                    行业成交额占比（Top5 占 {{ amountShare.top5_share_pct }}% · 数据 {{ amountShare.as_of }}{{ amountShare.from_snapshot ? ' 快照' : '' }}）
                  </summary>
                  <div class="space-y-0.5 text-[11px] mt-1">
                    <div v-for="(r, i) in amountShare.rows" :key="r.industry"
                         class="flex items-center gap-2 py-0.5">
                      <span class="w-3.5 text-muted font-mono">{{ i + 1 }}</span>
                      <span class="font-semibold w-20 truncate" :title="r.industry">{{ r.industry }}</span>
                      <span class="flex-1 h-1.5 rounded bg-border/40 overflow-hidden min-w-[30px]">
                        <span class="block h-full bg-accent/70" :style="{ width: barWShare(r.share_pct) }"></span></span>
                      <span class="font-mono text-gray-300 w-12 text-right">{{ r.share_pct }}%</span>
                      <span class="font-mono w-12 text-right" :class="pctClass(r.avg_change_pct)">{{ signNum(r.avg_change_pct) }}%</span>
                    </div>
                  </div>
                </details>
              </div>

              <div class="min-w-0 lg:pl-5">
                <div class="flex items-center justify-between mb-2">
                  <div class="text-xs font-semibold">持仓状态（{{ radarSummary.n || 0 }} 只 ·
                    风险 {{ radarSummary.risk || 0 }} / 机会 {{ radarSummary.opportunity || 0 }}）</div>
                  <span v-if="radarQuote" class="text-[10px] font-mono shrink-0"
                        :class="radarQuote.stale ? 'text-amber-400' : 'text-muted'"
                        :title="radarQuoteTitle">行情 {{ radarQuote.text }}</span>
                  <span v-else-if="radarAsOf" class="text-[10px] text-muted font-mono shrink-0">
                    计算于 {{ radarAsOf.slice(11, 16) }}</span>
                </div>
                <div v-if="radarErr" class="text-muted text-xs">—（加载失败：{{ radarErr }}）</div>
                <div v-else-if="!radarItems.length" class="text-muted text-xs">—（当前无持仓记录）</div>
                <template v-else>
                  <div v-if="radarRisk?.available" class="text-[10px] text-muted mb-1.5 truncate"
                       :title="[(radarRisk.style ? '风格 ' + radarRisk.style.band : ''),
                                (radarRisk.concentration || []).length ? '板块集中 ' + (radarRisk.concentration || []).map(c => c.industry).join('/') : '',
                                (radarRisk.overseas || []).length ? '海外敏感 ' + (radarRisk.overseas || []).map(o => o.chain).join('/') : ''
                               ].filter(Boolean).join(' · ')">
                    组合风控：{{ radarRisk.style?.band || '—' }}<template
                      v-if="(radarRisk.concentration || []).length"> · 集中 {{ (radarRisk.concentration || []).length }} 项</template><template
                      v-if="(radarRisk.overseas || []).length"> · 海外 {{ (radarRisk.overseas || []).length }} 项</template>
                  </div>
                  <!-- ★ 2026-10-26（用户："持仓状态的内容也有点挤"）：
                       行距 1→1.5、卡片内边距 px-2 py-1 → px-2.5 py-1.5、两行之间 mt-0.5 → mt-1，
                       可滚动高度 190 → 240（同一屏能舒服看完 3~4 只，不再挤成一坨）。 -->
                  <div class="space-y-1.5 max-h-[240px] overflow-y-auto pr-1">
                    <div v-for="it in radarItems" :key="it.code"
                         class="border border-border/50 rounded px-2.5 py-1.5">
                      <div class="flex items-center gap-1.5 flex-wrap text-xs">
                        <a :href="stockHref(it.code)" target="_blank"
                           class="font-semibold hover:text-accent truncate">{{ it.name || it.code }}</a>
                        <a :href="xqUrl(it.code)" target="_blank"
                           class="text-muted text-[10px] font-mono hover:text-accent" title="雪球">{{ it.code }}</a>
                        <span v-if="it.industry" class="text-[10px] text-muted truncate">{{ it.industry }}</span>
                        <span class="ml-auto font-bold font-mono" :class="pctClass(it.pnl_pct)">{{ fmtPct(it.pnl_pct) }}</span>
                      </div>
                      <div class="flex items-center gap-1.5 flex-wrap text-[10px] text-muted mt-1">
                        <span class="font-mono">现价 {{ it.price ?? '—' }}</span>
                        <span class="font-mono" :class="pctClass(it.day_pct)">今日 {{ signNum(it.day_pct) }}%</span>
                        <span v-if="it.phase" class="px-1 rounded"
                              :class="(MF_PHASE_STYLE[it.phase] || {}).cls || 'bg-white/5'">{{ it.phase_cn || it.phase }}</span>
                        <span v-if="it.score != null">评分 <b class="text-gray-200 font-mono">{{ it.score }}</b></span>
                        <span v-if="it.ready_label">闸门 {{ it.ready_label }}</span>
                        <span v-if="it.in_watch" class="text-sky-400">观察池</span>
                        <!-- ★ 2026-10-26（第①步：消息面投影）：这只持仓被哪条快讯影响 ——
                             原来画在「快讯解读」卡里的「持仓影响」块，现在**长在持仓行上**。
                             唯一性：快讯列表 = 原始流；这里只放"方向 + 条数"，点开看原文。 -->
                        <button v-if="fbByCode[it.code]" type="button"
                                class="px-1 rounded border cursor-help"
                                :class="msgCls(fbByCode[it.code].lean)"
                                :title="`快讯命中 ${fbByCode[it.code].n} 条 · ${fbByCode[it.code].lean}（观察参考，非交易建议）`"
                                @click.stop="toggleHoldingMsg(it.code)">
                          消息{{ fbByCode[it.code].n }}
                        </button>
                      </div>
                      <div v-for="(a, j) in (it.alerts || []).slice(0, 2)" :key="j" class="text-[10px] mt-1"
                           :class="a.level === 'risk' ? 'text-red-400' : 'text-emerald-400'">
                        {{ a.level === 'risk' ? '⚠' : '✦' }} {{ a.text }}
                      </div>
                      <!-- 展开：命中快讯原文（方向 + 簇/匹配词 + 原因；无原因则给一句概括） -->
                      <div v-if="fbByCode[it.code] && holdingMsgOpen[it.code]"
                           class="mt-1 pl-2 border-l border-accent/40 space-y-0.5">
                        <div v-for="(hit, j) in fbByCode[it.code].hits" :key="j"
                             class="text-[10px] text-muted leading-relaxed">
                          <span class="font-mono">[{{ hit.idx }}]</span>
                          <span class="ml-0.5" :class="hitCls(hit.direction)">{{ hit.direction }}</span>
                          <span class="ml-0.5">（{{ hit.match }}）</span>
                          <span class="ml-0.5">{{ hit.cause || hit.gist }}</span>
                        </div>
                        <div class="text-[10px] text-muted/70">规则联动 · 观察参考，非交易建议</div>
                      </div>
                    </div>
                  </div>
                </template>
              </div>
            </div>
          </div>

          <!-- 右卡：快讯解读（自带卡片外壳） -->
          <div class="min-w-0">
            <FlashBriefCard :date="selectedDate" :flash-diag="flashDiag" />
          </div>
        </div>

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
                  <!-- ★ 2026-09-28：原 `.slice(0,5)` 在 ISO 时间上会显示成 `2026-` ⇒ 统一走 hhmm() -->
                  <span class="font-mono text-muted">{{ hhmm(a.alert_time) }}</span>
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
             ⇒ 新增「竞价」视图，**零新增接口**（全部来自 `market_emotion` 快照）。
             现状（2026-09-25 晚更新 —— ⚠️ 原注释写的"竞价额/全市场榜单暂缺"**已过时**）：
               · 昨日涨停股视角：高开家数 / 平均高开 / 溢价（昨涨停今均）+ 高开 Top5
               · 全市场视角：高开·低开家数 / 平均高开 / 累计成交额 + 高开榜·低开榜 Top20
             ⚠️ **已知缺口**（评估结论，等用户定做哪个）：
               ① 竞价数据**未落库**（`market_emotion_daily` 只存 up/down/limit_up/…/verdict，
                  不含 auction 的高开家数/平均/成交额）⇒ **无法与近几日对比，也无法回测
                  "竞价强度 → 当日走势"**；而同库已有成熟的"先落库攒样本"模式（close_*、
                  尾盘承接基线）可照抄。
               ② 刷新节奏：`startPolling` 的 120s 轮询**已含 `loadEmotion()`**（数据会自动更新），
                  但竞价窗口只有 10 分钟 ⇒ 最多刷 5 次，**9:24→9:25 定稿瞬间可能滞后 ≤2 分钟**；
                  若要盯最后几分钟，可给竞价时段单独加快到 30~60s（尚未做）。
                  ⚠️ 我最初误判为"不在轮询里"—— 起因是搜索输出被截断（只看到 2098-2108 行），
                     没读到 2109 行的 `loadEmotion()`。**"没搜到" ≠ "不存在"**。
               ③ 高开榜只给名字与幅度，**未标注"是否池内 / 有信号"**（数据现成）。
               ④ 无竞价量比（需"昨日同期竞价额"，本项目无分时数据 ⇒ 短期做不到，诚实标注）。 -->
        <template v-else-if="selectedPhase === 'auction'">
          <div class="bg-card border border-indigo-500/40 rounded-lg p-3">
            <div class="flex items-center justify-between mb-1.5">
              <div class="text-sm font-semibold">竞价看板
                <span class="text-[10px] text-muted font-normal">（9:20 后不可撤单 · 9:25 定稿）</span></div>
              <span class="text-[10px] text-muted font-mono">{{ emotion?.as_of || '' }}</span>
            </div>
            <div v-if="!emotion" class="text-muted text-xs">—（加载失败）</div>
            <template v-else>
              <!-- ★★ 2026-09-29（#2 修复：盘前 open 文案偏差，**显式提示**）：
                   盘前（9:15 前）**今晨的 open 还没产生** ⇒ 行情缓存里的 `open` 是
                   **最近已完成交易日**的 ⇒ 本块（高开家数 / 平均缺口 / 判读）描述的是
                   那一天，不是"此刻"。此前只在 hover 里注一句，而"竞价看板"这个视图名
                   本身就在暗示"讲今天" ⇒ 改为一律**挂在顶上**（宁可啰嗦，不要一句错的"今日"）。
                   ⚠️ 复用既有 `isTodayPrice()`（与 `priceDayLabel()` 同规则，2026-09-26 引入）。 -->
              <div v-if="!isTodayPrice()"
                   class="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] text-amber-300 leading-relaxed">
                ⚠️ <b>非当日口径</b>：以下竞价数据属于
                <b class="font-mono">{{ mmdd(emotion.price_date) }}</b>
                （今晨开盘价尚未产生）⇒ 是**那一天已完成**的接力/开局，<b>别按"今天"读</b>。
              </div>
              <!-- ★★ 2026-09-25（用户："改完之后页面长度变长了，有些可以改成左右布局"）：
                   竞价看板内部**左右分栏**，按"信息性质"切（不按先后）：
                     · 左列 = 判读 + 三个数字 + 刻度尺 + 全市场家数/分布 + 接力 Top5（**整体冷热**）
                     · 右列 = 两个榜单**并排**（高开｜低开，**具体标的**）
                   收益：两榜从"上下 20 行"变"左右各 10 行"（省一半高度），且"整体冷热"与
                     "个股榜"不再上下叠 ⇒ 卡片高度约 660px → 330px。
                   ⚠️ 断点用 **xl**：主区已被常驻右栏占掉 320px，lg(1024) 分栏后每列不足 350px
                     会把条形榜挤坏；窄屏自动回退单列（信息一条不丢）。 -->
              <div class="grid grid-cols-1 xl:grid-cols-2 gap-x-4 gap-y-2 items-start">
              <div class="min-w-0">
              <!-- ★★ 2026-09-25 用户："这些结论可不可以生成在页面里，这样用户就不需要思考太多"
                   ⇒ 判读**置顶**（数字在下作支撑）：接力意愿 + 全市场开局 + 「该注意什么」。
                   ⚠️ 后端已声明三条边界（悬停可见完整口径）：这是**开盘前强度描述、不是涨跌预测**；
                      阈值为**经验初值、未回测** ⇒ 纯展示、不进信号与仓位；数据缺则不显示。 -->
              <div v-if="emotion.auction?.verdict"
                   class="mb-3 rounded border border-border/50 px-2 py-1.5 space-y-0.5">
                <div class="text-xs font-semibold cursor-help"
                     :class="{ good: 'text-rise', mixed: 'text-amber-300',
                               bad: 'text-fall', neutral: 'text-gray-300' }[emotion.auction.verdict.level]"
                     :title="emotion.auction.verdict.note">
                  竞价判读 · {{ emotion.auction.verdict.text }}
                </div>
                <div class="text-[11px] text-muted">
                  <template v-if="emotion.auction.verdict.relay_cn">{{ emotion.auction.verdict.relay_cn }}</template>
                  <template v-if="emotion.auction.verdict.relay_cn && emotion.auction.verdict.market_cn"> · </template>
                  <template v-if="emotion.auction.verdict.market_cn">{{ emotion.auction.verdict.market_cn }}</template>
                </div>
                <div class="text-[11px] text-accent/90">
                  该注意：{{ emotion.auction.verdict.action }}</div>
              </div>
              <!-- ★ 2026-09-25（口径标注）：三个数**时刻不同**却并排，最容易误读 ——
                   ② 是**开盘缺口**（竞价那一刻）、③ 是**现值**（含盘中），悬停给完整口径。
                   典型误读场景：高开 +2% 而现值 -1% ⇒ 不是"矛盾"，是**高开后被砸**（更弱）。 -->
              <div class="grid grid-cols-3 gap-3 text-center text-xs mb-2">
                <div>
                  <div class="text-lg font-bold font-mono">{{ emotion.auction?.count ?? '—' }}</div>
                  <div class="text-muted text-[10px] cursor-help"
                       title="昨日涨停的股票中，今天**开盘高开**（开盘价 > 昨收）的家数；开盘不涨不跌或低开的不计入。">昨涨停今高开数</div>
                </div>
                <div>
                  <div class="text-lg font-bold font-mono" :class="pctClass(emotion.auction?.avg_gap)">
                    {{ signNum(emotion.auction?.avg_gap) }}%</div>
                  <div class="text-muted text-[10px] cursor-help"
                       :title="`${prevLimitLabel()}涨停股${priceDayLabel()}**开盘缺口**的平均值 =（开盘价 / 昨收 − 1）。只反映竞价那一刻的接力意愿，不含盘中变化。`">平均高开（{{ priceDayLabel() }}缺口）</div>
                </div>
                <div>
                  <div class="text-lg font-bold font-mono" :class="pctClass(emotion.prev_limit_today_pct)">
                    {{ fmtPct(emotion.prev_limit_today_pct) }}</div>
                  <div class="text-muted text-[10px] cursor-help"
                       :title="`${prevLimitLabel()} 涨幅≥9.5% 的股票，在${priceDayLabel()}的**平均**涨跌幅 = 真实盈亏（含盘中）。⚠️ 与左边「平均高开」不是同一时刻：高开 +2% 而现值 -1% ⇒ 高开后一路被砸，比单纯低开更弱。`">{{ prevLimitLabel() }}涨停均（现价）</div>
                </div>
              </div>
              <!-- ★ 2026-09-25（A 档 3）：刻度尺 —— 把「接力溢价」与「全市场平均」放到**同一根尺**
                   上，背离直接看得见（此前两个数分处两块，要靠心算才知差多少）。
                   ⚠️ 固定 ±3% 刻度（而非自适应）才能**跨日横比**；超出时游标贴边，
                      右侧数值仍显示真实值（不撒谎）。0 刻度处加亮，尺带 `bg-card` 的小标注
                      防止两个游标靠近时文字糊在一起。 -->
              <div v-if="auctionScale.relayPos != null || auctionScale.allPos != null"
                   class="relative h-11 mb-3 select-none">
                <div class="absolute inset-x-0 top-5 h-px bg-border"></div>
                <div class="absolute top-4 left-1/2 w-px h-3 bg-border"></div>
                <span class="absolute left-0 top-6 text-[9px] text-muted bg-card pr-1">-3%</span>
                <span class="absolute right-0 top-6 text-[9px] text-muted bg-card pl-1">+3%</span>
                <template v-if="auctionScale.relayPos != null">
                  <div class="absolute top-3 -translate-x-1/2 w-px h-2 bg-amber-400"
                       :style="{ left: auctionScale.relayPos + '%' }"></div>
                  <div class="absolute top-0 -translate-x-1/2 text-[9px] font-mono text-amber-300 whitespace-nowrap bg-card px-0.5"
                       :style="{ left: auctionScale.relayPos + '%' }"
                       title="接力：昨日涨停股今日平均开盘缺口（强势股有没有人接）">
                    接力 {{ signNum(auctionScale.relay) }}%</div>
                </template>
                <template v-if="auctionScale.allPos != null">
                  <div class="absolute top-5 -translate-x-1/2 w-px h-2 bg-gray-400"
                       :style="{ left: auctionScale.allPos + '%' }"></div>
                  <div class="absolute top-8 -translate-x-1/2 text-[9px] font-mono text-gray-400 whitespace-nowrap bg-card px-0.5"
                       :style="{ left: auctionScale.allPos + '%' }"
                       title="全市场：所有个股平均开盘缺口（大盘开局冷暖）">
                    全市场 {{ signNum(auctionScale.all) }}%</div>
                </template>
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
                  <!-- ★ 2026-09-25：下面分布条的总数含「平开」（= 0%）⇒ 与这里的"高开+低开"**对不上**
                       是正常的（平开两边都不计）。不说明的话又是一处"数字对不上"。 -->
                  <span class="text-[10px] cursor-help"
                        title="高开/低开不含「平开」（开盘价恰好等于昨收）⇒ 高开+低开 略小于全市场家数，下面的分布条总数才是全量。">ⓘ</span>
                </div>
                <!-- ★★ 2026-09-29（P1）：A50 隔夜 vs 竞价整体高开 = **期现背离（内外资态度差）**。
                     北向实时额 2024-08 停披后，这是现存**唯一**可观测的"外资定价 vs 内资集合竞价
                     结果"之差：A50 走夜盘（外资定价）、竞价高开均值是 9:25 内资的结论。
                     ⚠️ 纯展示层（A50 对次日的预测力在可交易口径已实测塌陷），
                        阈值 ±0.5pt 为经验初值、未回测；口径与边界全放 `title`。 -->
                <div v-if="a50Divergence" class="text-[11px] mb-2" :title="a50Divergence.title">
                  <span class="text-muted">期现背离</span>
                  A50 <b class="font-mono" :class="pctClass(a50Divergence.a50)">{{ signNum(a50Divergence.a50) }}%</b>
                  <span class="text-muted">vs 竞价</span>
                  <b class="font-mono" :class="pctClass(a50Divergence.gap)">{{ signNum(a50Divergence.gap) }}%</b>
                  <span class="text-[10px] text-muted font-mono">（差 {{ signNum(a50Divergence.spread) }}pt）</span>
                  <b v-if="a50Divergence.label" class="ml-1 text-amber-300">{{ a50Divergence.label }}</b>
                </div>
                <!-- ★★ 2026-09-25（A 档 1）：全市场涨幅**分布条** —— 用户反馈"不够直观"的根因
                     是：两个总数（643:1915）读不出"跌得多深"。同样 1915 家低开，
                     "全在 -0.5% 内"（阴跌）与"一半跌超 3%"（恐慌）是完全不同的盘面。
                     · 条长对**最大档**归一化（图上看得出形状）；右侧另标真实家数与占比（读数准确）。
                     · 六档合计 = 全市场家数（后端保证），不会出现"占比加不满 100%"。 -->
                <div v-if="auctionHist.length" class="mb-2.5 space-y-[3px]">
                  <div v-for="b in auctionHist" :key="'ah' + b.label"
                       class="flex items-center gap-2 text-[10px]">
                    <span class="w-[52px] shrink-0 text-right text-muted font-mono">{{ b.label }}</span>
                    <span class="flex-1 h-2.5 rounded-sm bg-border/25 overflow-hidden">
                      <span class="block h-full rounded-sm"
                            :class="b.dir === 'up' ? 'bg-red-500/60' : 'bg-emerald-500/60'"
                            :style="{ width: b.w + '%' }"></span>
                    </span>
                    <span class="w-[68px] shrink-0 font-mono text-muted">
                      {{ b.count }}<span class="text-[9px] ml-1">{{ b.pctNum }}%</span></span>
                  </div>
                </div>
                </div>
              <!-- 接力 Top5（从卡底搬到左列：与上面的"接力溢价"同源，放一起读更顺） -->
              <!-- ★ 2026-09-27（用户："接力 Top5 的股票代码也展示出来，点击跳雪球"）：
                   全站口径一致 —— 名称→本地详情页（stockHref）、代码→雪球（xqUrl）。
                   原外层 <a> 整块跳本地页 ⇒ 改为 <span> 容器 + 两个链接
                   （HTML 不允许 <a> 嵌套 <a>，故根元素必须换成 span）。 -->
              <div v-if="(emotion.auction?.top || []).length" class="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border/40">
                <span class="text-[10px] text-muted w-full mb-0.5">昨日涨停股高开 Top5（接力视角）</span>
                <span v-for="g in emotion.auction.top" :key="g.code"
                   class="px-1.5 py-0.5 rounded border border-border/60 font-mono text-xs hover:border-accent inline-flex items-center gap-1"
                   :class="g.gap_pct >= 0 ? 'text-red-400' : 'text-emerald-400'">
                  <a :href="stockHref(g.code)" target="_blank" class="hover:underline">{{ g.name }}</a>
                  <a :href="xqUrl(g.code)" target="_blank" rel="noopener"
                     class="text-muted hover:text-accent" title="雪球">{{ g.code }}</a>
                  <span>{{ signNum(g.gap_pct) }}%</span>
                </span>
              </div>
              <div v-else class="text-[11px] text-muted mt-2">
                —（暂无高开数据。竞价 9:25 定稿后本页有效；休市日无数据属正常）
              </div>
              </div>
              <!-- ★ 2026-09-25 用户："左右的两部分搞个分割线" ⇒ 左列（整体冷热）与右列（个股榜）
                   之间加**主竖线**（比两榜之间那条 `border-border/40` 更实，形成主次层次）；
                   窄屏回退单列时线自动消失（`xl:` 前缀）。
                   右列内：两个榜单**并排**（各 20 行条形）。 -->
              <div v-if="emotion.auction?.market_count"
                   class="min-w-0 grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 items-start
                          xl:border-l xl:border-border xl:pl-4">
              <div class="min-w-0">
                <div class="mb-1 text-[10px] text-muted">高开榜 Top20（全市场）
                  <span class="cursor-help" title="带「★」= 有战法信号；带「持」= 我的持仓。看榜单的目的正是「里面有没有我能接的」。条长 = 相对本榜最大幅度 ⇒ 可看出**断层**（一字板那一簇 vs 后面的普通高开）。">★信号 · 持持仓 · 条长看断层</span>
                </div>
                <div class="space-y-1">
                  <!-- ★ 2026-09-26（用户："竞价看板的高开榜和低开榜双击股票名跳到雪球"）：
                       一个元素承载两个动作 ⇒ 单击（整行）= 本地详情页，**双击（仅名字）= 雪球**。
                       ⚠️ 浏览器在 `dblclick` 之前**必定先发两次 click** ⇒ 单击动作必须**延迟**
                         （见脚本区 `auctionRowClick` 的注释），否则双击会先弹出两个本地页。
                       ⚠️ 双击**只绑在名字上**（`@dblclick.stop`）：双击行的其它位置不会有反应
                         （"取消单击"逻辑正好让它变成无动作）。 -->
                  <a v-for="g in auctionBars(emotion.auction.market_top)" :key="'mt' + g.code"
                     :href="stockHref(g.code)" target="_blank"
                     class="flex items-center gap-1.5 text-[11px] font-mono group"
                     @click.prevent="auctionRowClick(g.code)"
                     :title="`成交 ${fmtAmountWan(g.amount_wan)}${g.sig ? '｜有战法信号' : ''}${g.held ? '｜我的持仓' : ''}`">
                    <span class="w-3 shrink-0 text-accent">{{ g.held ? '持' : (g.sig ? '★' : '') }}</span>
                    <span class="w-[76px] shrink-0 truncate group-hover:underline cursor-pointer"
                          :class="(g.sig || g.held) ? 'text-accent' : 'text-gray-200'"
                          title="双击跳雪球（单击看本地详情）"
                          @dblclick.stop.prevent="auctionNameDblClick(g.code)">{{ g.name }}</span>
                    <span class="flex-1 h-3 rounded-sm bg-border/25 overflow-hidden">
                      <span class="block h-full rounded-sm bg-red-500/60"
                            :style="{ width: g.w + '%' }"></span>
                    </span>
                    <span class="w-[52px] shrink-0 text-right text-red-400">{{ signNum(g.gap_pct) }}%</span>
                  </a>
                </div>
              </div>
              <!-- ★ 2026-09-25 用户："左右的两部分搞个分割线" ⇒ 两榜之间的**次级**竖线
                   （主分界在"左列｜右列"上，用更实的 border）；窄屏并排失效时线也自动消失。 -->
              <div class="min-w-0 md:border-l md:border-border/40 md:pl-4">
                <div class="mb-1 text-[10px] text-muted">低开榜 Top20（全市场）
                  <span class="cursor-help" title="条长 = 相对本榜最大跌幅 ⇒ 看**恐慌有没有加速**（跌幅断层越大，越可能是集中砸盘而非普跌）。">条长看断层</span>
                </div>
                <div class="space-y-1">
                  <!-- ★ 2026-09-26（同高开榜）：单击（整行）= 本地详情页，**双击（仅名字）= 雪球**；
                       单击延迟/DoubleClick 冲突处理见脚本区 `auctionRowClick` 注释。 -->
                  <a v-for="g in auctionBars(emotion.auction.market_bottom)" :key="'mb' + g.code"
                     :href="stockHref(g.code)" target="_blank"
                     class="flex items-center gap-1.5 text-[11px] font-mono group"
                     @click.prevent="auctionRowClick(g.code)"
                     :title="`成交 ${fmtAmountWan(g.amount_wan)}${g.sig ? '｜有战法信号' : ''}${g.held ? '｜我的持仓' : ''}`">
                    <span class="w-3 shrink-0 text-accent">{{ g.held ? '持' : (g.sig ? '★' : '') }}</span>
                    <span class="w-[76px] shrink-0 truncate group-hover:underline cursor-pointer"
                          :class="(g.sig || g.held) ? 'text-accent' : 'text-gray-200'"
                          title="双击跳雪球（单击看本地详情）"
                          @dblclick.stop.prevent="auctionNameDblClick(g.code)">{{ g.name }}</span>
                    <span class="flex-1 h-3 rounded-sm bg-border/25 overflow-hidden">
                      <span class="block h-full rounded-sm bg-emerald-500/60"
                            :style="{ width: g.w + '%' }"></span>
                    </span>
                    <span class="w-[52px] shrink-0 text-right text-emerald-400">{{ signNum(g.gap_pct) }}%</span>
                  </a>
                </div>
              </div>
              </div>
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
          <!-- ★ A3 情绪预判卡 v0（近似口径，B1 官方数据后替换）；
               ★ 2026-09-25 用户："1200px 还是太长" ⇒ 情绪预判与持仓预案**左右两卡**
               （lg 以下回退单列）。持仓预案原嵌在今日决策卡内 ⇒ 抽成独立卡
               （数据仍是 dc.positions_scan，零新接口）。 -->
          <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div class="bg-card border border-border rounded-lg p-4 flex flex-col justify-evenly">
            <div class="flex items-center justify-between">
              <div class="text-sm font-semibold">情绪预判
                <!-- ★ 2026-09-26：副标题由"（昨日涨停表现/连板高度，近似口径）"改为**带口径日期**
                     —— 原写法在休市/盘前会把用户带偏（"昨日/今日"相对的是**数据口径日**，
                     不是自然日"今天"）。日期来自后端 `price_date` / `prev_limit_date`。 -->
                <span class="text-[10px] text-muted font-normal cursor-help"
                      :title="`数据口径日 = ${emotion?.price_date || '—'}${isTodayPrice() ? '（即今天）' : '（休市/盘前，不是自然日「今天」）'}。涨停/跌停/连板高度均为该日；口径按**板幅**（ST 5% / 主板 10% / 创业板科创 20% / 北交所 30%，rt_k 精确优先，来源 ${emotion?.limit_src || '—'}）；「昨涨停均」= ${prevLimitLabel()} 涨停股在${priceDayLabel()}的平均涨跌幅`">{{ isTodayPrice() ? '今日' : '口径 ' + mmdd(emotion?.price_date) }} · 涨跌停按板幅（ST5/主板10/双创20）</span>
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
            <!-- ★ 2026-09-25 用户反馈：原 grid-cols-4 把 4 格**均分整卡宽度**，"47 涨停"与
                 "14 跌停"之间大片空白 ⇒ "一行宽、信息不集中"；判读又孤行贴底。
                 改（用户选定方案）：① 4 格从均分改**紧凑左聚**（固定 gap，不再摊满）；
                 ② 判读移到**右侧同一行**（细竖线分隔、小字、垂直居中）⇒ 整卡矮一行，
                 视线"左=是什么 / 右=怎么看"。 -->
            <!-- ★ 2026-09-25 用户：数字行**占满剩余空间** —— 四组 flex-1 均分整卡宽度
                 （半宽 ~600px 下摊开正合适，不再左聚留白）。 -->
            <div v-else class="flex items-center text-xs min-w-0 py-3">
              <div class="text-center flex-1"><div class="text-lg font-bold text-red-400 font-mono">{{ emotion.limit_up ?? '—' }}</div><div class="text-muted text-[10px]">涨停</div></div>
              <div class="text-center flex-1"><div class="text-lg font-bold text-emerald-400 font-mono">{{ emotion.limit_down ?? '—' }}</div><div class="text-muted text-[10px]">跌停</div></div>
              <div class="text-center flex-1"><div class="text-lg font-bold font-mono">{{ emotion.max_streak ?? '—' }}</div><div class="text-muted text-[10px]">连板高度</div></div>
              <!-- ★ 2026-09-25 用户问「昨日涨停今日 -1.16% 是什么意思」⇒ 原文案缺"平均"二字、
                   也不给股数，容易被读成"今天的涨跌幅是 -1.16%"。
                   口径：**昨日涨幅≥9.5% 的那批股票，今天的平均涨跌幅**（=赚钱效应）；
                   为正 ⇒ 接力意愿强（昨涨停今天还有人买）；为负 ⇒ 追涨者平均亏钱、情绪转弱。
                   列宽有限 ⇒ 只微调文案，完整解释放 title（悬停可见）。 -->
              <div class="text-center flex-1" :title="`${prevLimitLabel()} 涨幅 ≥9.5% 的 ${emotion.prev_limit_count ?? '?'} 只股票，在${priceDayLabel()}的**平均**涨跌幅 = ${fmtPct(emotion.prev_limit_today_pct)}（赚钱效应）：为正 ⇒ 接力意愿强；为负 ⇒ 追涨者平均亏钱、情绪转弱`">
                <div class="text-lg font-bold font-mono" :class="pctClass(emotion.prev_limit_today_pct)">
                  {{ fmtPct(emotion.prev_limit_today_pct) }}</div>
                <div class="text-muted text-[10px] cursor-help">{{ prevLimitLabel() }}涨停均<template v-if="emotion.prev_limit_count">（{{ emotion.prev_limit_count }}只）</template></div>
              </div>
            </div>
            <!-- ★ 2026-09-25 用户：数字组与判读改回**上下布局**（左卡半宽后右侧放不下判读）；
                 卡是 flex-col justify-evenly ⇒ 标题/数字/判读三行在右卡撑起的等高内均匀分布。 -->
            <div class="text-[10px] text-muted leading-snug">
              判读：情绪决定今天"接力的强更强 / 分歧 / 退潮"，对应降低或提高买入标准。
            </div>
          </div>

          <!-- ★ 2026-09-25 用户要求：持仓预案从今日决策卡抽出成**独立卡**，与情绪预判
               左右并排（每张卡 ~600px，替代原来的 1200px 长条）。数据仍是
               `dc.positions_scan`（今日决策卡接口顺带返回，零新接口）⇒ dc 未生成时
               本卡显示引导占位（不假装有数据）。是**纪律提醒**，不是交易指令。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">持仓预案
              <span class="text-[10px] text-muted font-normal">（今天怎么处理 · 纪律提醒）</span></div>
            <div v-if="dcLoading" class="text-muted text-xs">生成中…</div>
            <div v-else-if="!dc" class="text-muted text-xs">
              待生成今日决策卡后展示——每只持仓给出今天的处理建议与仓位上限
              <div class="mt-1"><button @click="loadDecisionCard()" :disabled="dcLoading"
                    class="px-2 py-0.5 rounded border border-border text-muted">生成今日决策卡</button></div>
            </div>
            <div v-else-if="!(dc.positions_scan || []).length" class="text-muted text-xs">—（当前无持仓，或后端未返回预案）</div>
            <div v-else class="space-y-1.5 text-xs">
              <div v-for="p in dc.positions_scan" :key="p.code" class="py-0.5">
                <div class="flex items-start gap-1.5 flex-wrap">
                  <a :href="stockHref(p.code)" target="_blank"
                     class="font-semibold hover:underline">{{ p.name }}</a>
                  <span class="font-mono" :class="pctClass(p.pnl_pct)">{{ fmtPct(p.pnl_pct) }}</span>
                  <!-- ★ 2026-09-25：主力阶段改用**共享** `PHASE_STYLE` 上色（用户："颜色逻辑一致…没生效"）
                       —— 原来这里是**纯灰文本**（`主力 盘整`），因为后端只下传了 `phase_cn` 中文、
                       没给英文枚举 ⇒ 前端无法查配色表。现已让后端补 `phase`。 -->
                  <span v-if="p.phase" class="px-1 rounded cursor-help text-[10px]"
                        :class="(MF_PHASE_STYLE[p.phase] || {}).cls || 'bg-white/5 text-muted'"
                        :title="(MF_PHASE_STYLE[p.phase] || {}).tip || ''">{{ p.phase_cn || p.phase }}</span>
                  <span v-else class="text-muted">主力 {{ p.phase_cn || '—' }}</span>
                  <span v-if="p.suggested_pct != null"
                        class="px-1 rounded text-[10px] bg-sky-500/15 text-sky-400 cursor-help"
                        :title="`建议仓位上限 ${p.suggested_pct}%（个股档位 ${p.position_label || '—'}）${(p.sizing_reasons || []).length ? '：' + p.sizing_reasons.join('；') : ''}`">
                    ≤{{ p.suggested_pct }}%
                  </span>
                  <!-- ★ 2026-09-25 用户："工商银行和中国海油在昨日的一片绿的行情下是红的"
                       ⇒ **逆势强度**：当日涨幅与「相对全市场等权」的差值。
                       ⚠️ 只显示客观数值、**不下结论**（阈值需先回测）；市况基准值放 title。 -->
                  <span v-if="p.day_pct != null" class="text-[10px] cursor-help"
                        :title="`当日涨幅 ${fmtPct(p.day_pct)}；全市场等权 ${fmtPct(p.market_avg_pct)}；相对强度 = 个股 − 全市场（正 = 强于大盘，逆势红盘即此列）`">
                    当日 <b :class="pctClass(p.day_pct)">{{ fmtPct(p.day_pct) }}</b>
                    <template v-if="p.rel_pct != null">
                      · vs全市场 <b :class="pctClass(p.rel_pct)">{{ signNum(p.rel_pct) }}pp</b>
                    </template>
                  </span>
                </div>
                <div class="mt-0.5" :class="planClass(p.plan_level)">{{ p.plan || '—' }}</div>
                <div v-for="(a, j) in (p.alerts || [])" :key="j" class="text-[11px] text-muted">· {{ a.text }}</div>
              </div>
            </div>
          </div>
          </div>

          <!-- ★★ 2026-09-28（用户选定方案 A：顶栏放摘要 + 点开看全文）：
               本卡**整块抽成子组件 `MacroEnvCard.vue`** —— 盘前这里内联、顶栏「宏观」浮层
               复用**同一个组件** ⇒ 一处实现两处用（防"同一份数据两处渲染必然漂移"，
               同日刚在教练卡上踩过一次）。
               ⚠️ 原来它只会出现在盘前（数据一直在，只是别处不渲染）⇒ 抽出来后
                 顶栏浮层让它在**任何阶段**都能看。 -->
          <MacroEnvCard :macro="macro" :macro-err="macroErr" :temperature="temperature"
                        :stagflation="stagflation"
                        :flash-diag="flashDiag" :sizing="sizing" :sentiment="macroSentiment"
                        :futures-basis="macroFuturesBasis" :liquidity="macroLiquidity" />
          <!-- ★ 2026-10-10（P0-2）：央行储备月度面板（月频慢变量，非信号）。紧跟宏观卡，
               与「宏观与环境」构成"环境 → 慢变量背景"的阅读顺序；自取数据、不参与轮询。 -->
          <ReserveCard />
          <!-- ★★ 2026-09-25 用户："信号×行业是战法扫描出来的吗？如果是，今日决策卡的战法内容
               也可以摘出来，剩余的部分跟隔夜与今日（财经日历）形成左右卡片。"
               ⇒ 确认：`signal_industry_cross` 读的就是 `strategy_results`（战法扫描）⇒ 同源。
               ⇒ 两列起点**上移到决策卡**（原先只从"信号×行业"起 ⇒ 决策卡仍全宽）：
                  左列＝决策卡（剩余：立场/做多少/错了/负面清单）+ 战法卡（战法内容+信号分布）
                  右列＝隔夜与今日（财经日历）+ 决策简报（盘前）
               · 断点 **2xl**：主区已被常驻右栏占 320px，xl 下每列仅 ~460px 太挤。
               · 两列**各自堆叠**（masonry）而非 grid 行对齐 —— 否则短列会留大片空洞。 -->
          <div class="grid grid-cols-1 2xl:grid-cols-2 gap-3 items-start">
          <div class="space-y-4 min-w-0">
          <!-- ★ P1 今日决策卡（规则引擎确定性输出；空状态给生成按钮，不静默） -->
          <div class="bg-card border border-accent/40 rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">今日决策卡
                <span class="text-[10px] text-muted font-normal">（规则引擎 · 无 LLM · 确定性输出）</span>
                <!-- ★ 数据可信度：该不该买 = 闸门（19.5 年四态分布 + 尾部风险画像已复核） -->
                <span v-if="verStatus('regime_gate')"
                      :class="['ml-1 px-1 py-px rounded border text-[9px] font-normal align-middle',
                               STATUS_STYLE[verStatus('regime_gate').status].cls]"
                      :title="verifyTip(verStatus('regime_gate'))">
                  {{ STATUS_STYLE[verStatus('regime_gate').status].text }}
                </span>
              </div>
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
              <!-- 做多少 / 错了怎么办 -->
              <!-- ★★ 2026-09-25 用户："今日决策卡的战法内容也可以摘出来" ——
                   「做什么（白名单/候选）」「回避」「战法质量（含绩效表）」已**整体摘到
                   独立「战法卡」**（与同源的「信号×行业」合并，见下方战法卡）。
                   本卡只保留纯决策内容：立场 / 做多少 / 错了怎么办 / 负面清单。 -->
              <div class="text-xs space-y-1.5">
                <div><span class="text-muted">做多少：</span>{{ dc.how_much?.total_cap
                  }}<template v-if="dc.how_much?.single_cap && dc.how_much.single_cap !== '—'"> · {{ dc.how_much.single_cap }}</template></div>
                <div><span class="text-muted">错了怎么办：</span>{{ dc.if_wrong?.stop_rule
                  }}<template v-if="dc.if_wrong?.retreating"> · <span class="text-amber-400">{{ dc.if_wrong.retreating }}</span></template></div>
                <!-- ★ 2026-09-25 用户："环境：情绪 分歧/常态 · 涨停 47/跌停 14 …"与
                     **情绪预判卡**完全同源（同为 verdict + 涨停/跌停/连板高度/昨涨停今均）
                     ⇒ 决策卡删掉这一行，避免同一批数字在盘前出现两次。 -->
              </div>
              <!-- ★ 2026-09-25 用户需求："负面清单和主线推荐同等重要" ⇒ 决策卡补「负面清单」。
                   数据来自后端 dc.negatives（持仓主力出货 / 候选信号冲突 / 矛盾敞口
                   / ★ C1 大额解禁）。
                   ⚠️ 更新（2026-09-25）：**解禁已接入**（C1 风险事件闸门，阈值 10% 有回测依据）；
                   仍**不含**减持/质押/停牌/问询/新股（暂无结构化数据源）。
                   空数组 ⇒ 整块不渲染（不给"假清空"的安心感）。 -->
              <div v-if="(dc.negatives || []).length" class="border-t border-border/40 mt-2 pt-2 text-xs">
                <div class="text-muted mb-1">负面清单（今天要避开的）</div>
                <div v-for="(n, i) in negativeList" :key="i" class="py-0.5">
                  <span class="px-1 rounded text-[10px]"
                        :class="n.level === 'high' ? 'bg-red-500/15 text-red-400' : 'bg-amber-500/15 text-amber-400'">{{ n.scope }}</span>
                  <a v-if="n.code" :href="stockHref(n.code)" target="_blank"
                     class="ml-1 font-semibold text-red-400 hover:underline">{{ n.name || n.code }}</a>
                  <span class="ml-1 text-gray-300">{{ n.reason }}</span>
                </div>
                <!-- ★ 2026-09-25：矛盾敞口不在此重复（简报「该做」段已逐条列出）⇒ 只报数量 + 指路 -->
                <div v-if="conflictCount" class="text-[10px] text-muted py-0.5">
                  矛盾敞口 {{ conflictCount }} 条 —— 已在
                  <router-link target="_blank" to="/report" class="text-accent hover:underline">盘前简报</router-link>
                  的「该做」段列明，此处不重复
                </div>
                <!-- ★ C1：把"扫过的范围"说出来（否则用户不知道解禁也查过了） -->
                <div v-if="dc.risk_events?.scanned" class="text-[10px] text-muted mt-1">
                  其中已扫解禁风险：{{ dc.risk_events.scanned }} 只持仓/候选（未来 20 天）</div>
              </div>
              <!-- ★ C1 且**无命中**时也留一行：避免"没有条目"被误解成"没做这件事" -->
              <div v-else-if="dc.risk_events?.scanned"
                   class="border-t border-border/40 mt-2 pt-2 text-xs text-muted">
                负面清单：无（已扫 {{ dc.risk_events.scanned }} 只持仓/候选的未来 20 天解禁风险，无命中）
              </div>
              <!-- ★★ 2026-09-27（P2-a 落地）春节「日历例外」—— 防御档下唯一有实证的可用窗口。
                   为什么单独成块：它**不在**闸门三条件内 —— 后端刻意**不改** `条件C` /
                   `REGIME_ALLOWED` / `REGIME_POSITION`（那些是"买入资格 / 仓位档位"的唯一
                   事实源，被评分榜 50 只与观察池复用），而是叠加的独立提示通道。
                   `active=false`（非窗口期）⇒ 整块不渲染（同负面清单的"不给假清空"约定）。 -->
              <div v-if="dc.calendar_exception?.active"
                   class="border-t border-border/40 mt-2 pt-2 text-xs">
                <div class="flex items-center gap-1.5 flex-wrap mb-1">
                  <span class="text-muted">春节日历例外</span>
                  <span class="px-1 rounded text-[10px]"
                        :class="dc.calendar_exception.action === 'buy'
                          ? 'bg-emerald-500/15 text-emerald-400'
                          : (dc.calendar_exception.action === 'watch'
                            ? 'bg-amber-500/15 text-amber-400' : 'bg-slate-500/15 text-slate-400')">
                    {{ sfActionCn(dc.calendar_exception.action) }}
                  </span>
                  <span v-if="dc.calendar_exception.precision === 'estimate'"
                        class="text-[10px] text-slate-500">日期为预估</span>
                  <span v-if="dc.calendar_exception.overrides" class="text-[10px] text-amber-400">
                    （临时放宽「{{ sfRegimeCn(dc.calendar_exception.overrides) }}」档限制）
                  </span>
                </div>
                <div class="text-[11px] font-semibold mb-0.5">{{ dc.calendar_exception.title }}</div>
                <div class="text-[11px] text-muted leading-relaxed">{{ dc.calendar_exception.note }}</div>
                <div v-if="(dc.calendar_exception.targets || []).length" class="mt-1 space-y-0.5">
                  <div v-for="(t, i) in dc.calendar_exception.targets" :key="i"
                       class="flex items-baseline gap-1 flex-wrap">
                    <a :href="stockHref(t.code)" target="_blank"
                       class="font-semibold text-emerald-400 hover:underline">{{ t.name }}({{ t.code }})</a>
                    <span class="text-[10px] text-muted">{{ t.note }}</span>
                  </div>
                  <div v-if="dc.calendar_exception.position_hint" class="text-[10px] text-muted">
                    建议仓位 {{ dc.calendar_exception.position_hint.pct }}%（{{
                      dc.calendar_exception.position_hint.label }}）—— {{
                      dc.calendar_exception.position_hint.note }}
                  </div>
                </div>
                <div v-if="dc.calendar_exception.caveat"
                     class="text-[10px] text-amber-400/80 mt-1 leading-relaxed">
                  {{ dc.calendar_exception.caveat }}
                </div>
              </div>
            </template>
          </div>

          <!-- ★★ 战法卡（2026-09-25 用户方案）：收拢所有**战法相关**内容 ——
               从决策卡摘出的「做什么（白名单/候选）」「回避」「战法质量」＋ 同源的「信号 × 行业」。
               （已确认 `signal_industry_cross` 读的就是 `strategy_results` 战法扫描结果 ⇒ 同源。）
               顺序＝结论（能不能做）→ 分布（信号落在哪些行业）→ 依据（绩效与为什么静默）。
               ⚠️ v-if 从 `dc.signals_industry.rows.length` 放宽为 `dc`：即使某天没有信号，
               白名单/战法质量仍需显示（原条件会把整卡藏掉）。
               📌 信号×行业的由来（P2，2026-09-25）：用户"决策简报说『需结合行业分布判断』，
               但页面没给分布 —— 58 只信号的行业交叉表应该直接画出来"，且"融捷（锂）和焦作万方
               （电解铝）笼统归入『有色/化工链条』⇒ 分类口径要标注"。数据 = `dc.signals_industry`
               （零新增数据源：strategy_results + stock_industry）；行业名＝归一化一级，
               悬停看原始细分；原始名里新浪 node（new_xxx）已在后端过滤。 -->
          <div v-if="dc" class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">战法
                <span class="text-[10px] text-muted font-normal">（能不能做 · 为什么 · 信号分布）</span>
                <!-- ★ 数据可信度：6 战法已于 2026-09-27 经 bootstrap 全市场检验证伪
                     ⇒ 动态白名单当前为空，本卡「做什么」一栏经常为空属**正常**结果。 -->
                <span v-if="verStatus('strategies')"
                      :class="['ml-1 px-1 py-px rounded border text-[9px] font-normal align-middle',
                               STATUS_STYLE[verStatus('strategies').status].cls]"
                      :title="verifyTip(verStatus('strategies'))">
                  {{ STATUS_STYLE[verStatus('strategies').status].text }}
                </span>
              </div>
            </div>
            <!-- ① 做什么（白名单 + 候选）+ 回避 —— 从决策卡摘出 -->
            <div class="text-xs space-y-1">
              <div>
                <span class="text-muted">做什么：</span>
                <b>{{ (dc.do?.whitelist || []).join('、') || '无白名单战法（推送静默）' }}</b>
                <template v-if="(dc.do?.candidates || []).length">
                  · 候选 {{ dc.do.candidates.map(c => `${c.name} ${c.score}分`).join('、') }}
                  <!-- ★ 数据可信度：候选来自综合评分，P0 独立性检验因样本不足未完成 -->
                  <span v-if="verStatus('score')"
                        :class="['ml-1 px-1 py-px rounded border text-[9px] align-middle',
                                 STATUS_STYLE[verStatus('score').status].cls]"
                        :title="verifyTip(verStatus('score'))">
                    {{ STATUS_STYLE[verStatus('score').status].text }}
                  </span>
                </template>
              </div>
              <!-- ★ 2026-09-27 合并买入清单（战法命中个股 × 闸门就绪度）—— 此前
                   用户要自己在「白名单战法名」与「评分 Top3」之间脑内合并。
                   组合只由「战法命中 + 闸门」构成，**刻意不混入未验证的评分**。 -->
              <div v-if="(dc.do?.merged || []).length" class="mt-1">
                <span class="text-muted">合并清单：</span>
                <div v-for="m in dc.do.merged" :key="m.code"
                     class="ml-2 flex items-center gap-1.5 flex-wrap py-px">
                  <span class="text-gray-300">{{ m.name }} ({{ m.code }})</span>
                  <span class="text-muted">战法 {{ (m.strategies || []).join('、') }}</span>
                  <span :class="(m.gate_ready || 0) >= 3 ? 'text-emerald-400'
                                : (m.gate_ready || 0) === 2 ? 'text-amber-400' : 'text-muted'">
                    闸门 {{ m.gate_ready || 0 }}/3{{ m.gate_label ? ' ' + m.gate_label : '' }}
                  </span>
                </div>
              </div>
              <div v-else class="text-[10px] text-muted">
                合并清单为空 —— 当前无白名单战法命中（战法已证伪 ⇒ 动态白名单为空属正常）。
              </div>
              <div v-if="(dc.do?.avoid || []).length">
                <span class="text-muted">回避：</span><span class="text-red-400">{{ dc.do.avoid.join('；') }}</span>
              </div>
            </div>
            <!-- ② 信号 × 行业（战法扫描的信号分布） -->
            <div v-if="(dc.signals_industry?.rows || []).length" class="mt-3 pt-3 border-t border-border/40">
            <div class="flex items-center justify-between mb-1.5">
              <div class="text-xs font-semibold">信号 × 行业
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

            <!-- ③ 战法质量（为什么静默 / 绩效 / 判据）—— 从决策卡摘出。
                 ★ 这是「无白名单战法（推送静默）」的**自解释**：分不清是市场不对 / 战法坏了 /
                 系统故障时，三者处置完全不同（前两者什么都不用做，后者要修）。 -->
            <div v-if="dc.strategy_quality?.available" class="mt-3 pt-3 border-t border-border/40">
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
          </div>

          </div>
          <!-- 右列：财经日历 + 盘前简报 -->
          <div class="space-y-4 min-w-0">

          <!-- ★ A2 隔夜与今日（财经日历；公告/解禁类待 C1 数据源） -->
          <div class="bg-card border border-border rounded-lg p-3">
            <div class="flex items-center justify-between mb-1.5">
              <div class="text-sm font-semibold">隔夜与今日（财经日历）</div>
              <router-link target="_blank" to="/calendar" class="text-xs text-accent hover:underline">完整日历</router-link>
            </div>
            <!-- ★★ 2026-09-29（P1）：未来 3 天**关键事件**（PCE/CPI/非农/FOMC…）。
                 与下方"今日全部事件"互补：本块回答"**哪天是数据敏感期**"——已滤官员讲话、
                 只留美国/中国、同指标多口径（年率/月率）已合并 ⇒ 高信噪比。
                 筛选**全在后端** `flash/calendar_ahead`（与盘前企微推送同一套口径，
                 前端不复制，避免页面与推送两套筛选漂移）。
                 `by_date` 是对象 ⇒ v-for 第二参数取日期键（后端已按日期升序插入）。 -->
            <div v-if="calendarAhead?.items?.length" class="mb-2 pb-2 border-b border-border/40">
              <div class="text-[11px] text-muted mb-0.5">未来 3 天关键事件
                <span class="text-[10px]">（核心指标 · 已滤官员讲话）</span></div>
              <div v-for="(rows, d) in calendarAhead.by_date" :key="d"
                   class="text-xs py-0.5 flex gap-1.5 items-start">
                <span class="font-mono text-muted shrink-0">{{ mmdd(d) }}</span>
                <span class="min-w-0">
                  <template v-for="(x, i) in rows" :key="x.id">
                    <span class="cursor-help"
                          :class="x.channel === '5star' ? 'text-amber-300 font-semibold' : 'text-gray-300'"
                          :title="`${x.country || ''}${x.title}${x.star ? '（' + '★'.repeat(x.star) + '）' : ''}${x.impact ? ' — ' + x.impact : ''}`"
                    >{{ x.country }}{{ x.title }}<span v-if="x.star"
                      class="text-[9px] align-super">{{ '★'.repeat(x.star) }}</span></span><span
                      v-if="i < rows.length - 1" class="text-muted"> · </span>
                  </template>
                </span>
              </div>
            </div>
            <div v-if="calendarErr" class="text-muted text-xs">—（{{ calendarErr }}）</div>
            <div v-else-if="!calendarToday.length" class="text-muted text-xs">—（今日无事件或数据未返回）</div>
            <div v-else class="text-xs">
              <div v-for="(e, i) in calShown" :key="i" class="border-b border-border/40 py-1">
                <span class="font-mono text-muted mr-2">{{ (e.time || e.date || '').slice(0, 16) }}</span>
                {{ e.title || e.event || e.content || e.name || '—' }}
              </div>
              <button v-if="calendarToday.length > 3" type="button"
                      class="mt-1 text-[10px] text-accent hover:underline"
                      @click="calOpen = !calOpen">
                {{ calOpen ? '收起' : `展开全部（${calendarToday.length} 条）` }}
              </button>
            </div>
          </div>
          <div class="bg-card border border-border rounded-lg p-3">
            <div class="flex items-center justify-between mb-1.5">
              <div class="text-sm font-semibold">决策简报（盘前）<span
                v-if="premarketOver" class="ml-2 px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-400 text-[10px]">盘前已结束 · 以下为盘前回顾（09:10 生成），勿据此做盘中决策</span></div>
              <div class="flex items-center gap-2">
                <span v-if="briefDegraded" class="text-[10px] text-amber-400 cursor-help"
                      title="当前是**规则降级版**（LLM 调用失败时的兜底输出）—— 数字可靠，但少了 LLM 的综合判断。可点右侧「重新生成」重试。">降级版</span>
                <!-- ★ 2026-09-30（用户："llm 分析失败，可以加一个手动重试"）：
                     后端 refresh=true 一直支持重新生成，此前**无前端入口** ⇒ 失败只能干等。
                     ⚠️ 明示"消耗 1 次 LLM"并禁用连点（briefLoading）。 -->
                <button @click="loadBrief('premarket', true)" :disabled="briefLoading"
                        class="px-2 py-0.5 rounded text-[11px] border border-border text-accent hover:border-accent/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        title="重新生成盘前简报。⚠️ 消耗 1 次 LLM 调用（同步，约需十几秒）。">
                  {{ briefLoading ? '生成中…' : (briefPhase === 'premarket' && briefMd ? '重新生成' : '生成') }}
                </button>
                <router-link target="_blank" to="/report" class="text-xs text-accent hover:underline">完整简报</router-link>
              </div>
            </div>
            <div v-if="briefLoading" class="text-muted text-xs">生成中…（LLM 同步调用，约需十几秒）</div>
            <div v-else-if="briefErr" class="text-xs text-fall">—（加载失败：{{ briefErr }}）
              <button @click="loadBrief('premarket', true)"
                      class="ml-2 px-2 py-0.5 rounded border border-border text-accent hover:border-accent/50 transition-colors">重试</button>
            </div>
            <div v-else-if="briefPhase !== 'premarket' || !briefMd" class="text-muted text-xs">—（尚未生成）</div>
            <!-- ★ 2026-10-11：简报 prompt 已约定首行 `**结论**：…` ⇒ 走「观点卡」抽出结论条置顶；
                 「依据」= 该做/该防等原文，**不折叠**（简报本身是给人通读的，折叠只添堵）。 -->
            <div v-else class="max-h-[320px] overflow-y-auto pr-1">
              <InsightBody :text="briefMd" class="text-xs" />
            </div>
          </div>
          </div>
          </div>
        </template>

        <!-- 实时模式 · ② 盘中 / ③ 午盘（执行模式：大字、少文字）
             ★ 2026-09-25 用户要求：指数大字卡删除——与顶栏重复，指数保留顶栏常驻 -->
        <template v-else-if="selectedPhase === 'intraday' || selectedPhase === 'midday'">
          <!-- ★★ 2026-09-25（用户："同理，你调整一下盘中的布局" + "这个部分我想用一个长卡片展示"）：
               **① 看盘序 = 全宽长卡，不参与分栏** —— 它内部是 6 格横向指标 + 数行横向信息
               （尾盘承接 / 竞价 Top5；大小盘风格与昨日涨停已移到「盘面速览」涨跌条下方），天然"横着读"
               ⇒ 挤进半列必然折行。②③④⑤ 才分栏（安排见下方注释）。 -->
        <!-- 看盘序 3/5 · 涨停梯队 2/5 -->
        <div class="grid grid-cols-1 xl:grid-cols-[3fr_2fr] gap-3 items-start">
          <!-- ★ 2026-10-26（用户：消除市场涨跌重复）：涨跌家数/涨停跌停/成交额/情绪已上移至「盘面速览」大卡；本卡只保留**风格 / 尾盘 / 昨日涨停 / 竞价**这些盘面速览没有的内容。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-1">盘中结构<span class="text-[10px] text-muted font-normal">（尾盘承接 / 竞价）</span></div>
            <div v-if="!tailReview?.available && !(emotion && emotion.auction && emotion.auction.count)"
                 class="text-[11px] text-muted">—（尾盘承接需 14:30 基线；竞价看板 9:25 后有效）</div>
            <!-- ★ 2026-10-26（用户）：大小盘风格与"昨日涨停/赚钱效应"已移到「盘面速览」的涨跌条下方，
                 本卡只留尾盘承接与竞价看板（避免同一读数两处出现）。 -->
            <!-- ★★ 2026-09-25 需求 3（框架 C2「14:30 承接：回封/抢筹/跳水」）：
                 尾盘半小时的承接强度**决定是否持仓过夜** —— 走强=资金愿持股过夜；
                 走弱=有资金尾盘撤退（该减仓过夜）。对比 14:30 基线 vs 现在。
                 ⚠️ 基线需 14:30 时后端在线（由 intraday_alert_loop 落库）；无基线时整块不渲染。 -->
            <div v-if="tailReview?.available" class="text-[11px]">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-muted">尾盘承接</span>
                <span class="font-semibold cursor-help" :class="tailCls" :title="tailTitle">
                  {{ tailReview.label }}</span>
                <span class="text-muted font-mono">
                  Δ涨停 <b :class="pctClass(tailReview.deltas.limit_up)">{{ tailDelta(tailReview.deltas.limit_up) }}</b>
                  · Δ上涨 <b :class="pctClass(tailReview.deltas.up)">{{ tailDelta(tailReview.deltas.up) }}</b>
                  · 尾盘量能 <b class="text-gray-300">{{ tailReview.deltas.amount_yi }}亿</b>
                </span>
                <span v-for="i in (tailReview.indices || [])" :key="i.code"
                      class="text-muted font-mono" title="该指数 14:30 → 现价的变化">
                  {{ i.name }} <b :class="pctClass(i.d_pct)">{{ signNum(i.d_pct) }}%</b>
                </span>
              </div>
              <div class="text-muted mt-0.5">{{ tailReview.advice }}</div>
            </div>
            <!-- ★ 2026-10-26：昨日涨停 / 赚钱效应 / 连板高度已移到「盘面速览」涨跌条下方
                 （口径说明收进 ⓘ 悬停，不再在这一行常显长句）。 -->
            <!-- ★ P1 竞价看板：昨日涨停股今日高开（9:25 竞价定稿后有效；休市日显示最近交易日） -->
            <div v-if="emotion && emotion.auction && emotion.auction.count"
                 class="border-t border-border/40 mt-2 pt-2 text-xs">
              <!-- ★ 2026-09-28（用户："这个怎么一会一个，一会又三个"）：补**数据到位度**
                   —— 竞价窗口内行情缓存逐分钟刷新 ⇒ 能算出高开的股票数从少到多，
                   `count/limit_up_total` 一眼看出"是数据还没到齐，不是真的只有这几只"。 -->
              <!-- ★ 2026-09-29（用户追问："盘中的竞价看板「一会一个、一会三个」也是正常的吗？"
                   ⇒ 不正常，已修：名单来源由「行情缓存的**当日**涨幅」（开盘后=今日涨停）
                   改为「按历史收盘自算的**昨日**涨停」）—— 顺带把**口径与边界**放 hover：
                   ① 名单按**已回填日线**自算（库内仅覆盖 ~839 只）⇒ 可能少于真实涨停家数；
                   ② 开盘价 9:15 前尚未产生，此时用的是上一交易日值。 -->
              <div class="mb-1"
                   :title="`名单口径：按已回填日线自算的「昨日涨停」——当日仅 ${emotion.auction.covered ?? '—'} 只股票有日线数据，故通常少于真实涨停家数（已结算昨日涨停家数 ~47 只）\n开盘价：9:15 前尚未产生，此时取上一交易日值`">竞价看板：昨日涨停
                <b class="font-mono">{{ emotion.auction.count }}<template
                  v-if="emotion.auction.limit_up_total">/{{ emotion.auction.limit_up_total }}</template></b> 只<template
                  v-if="emotion.auction.limit_up_total && emotion.auction.count < emotion.auction.limit_up_total">
                  <span class="text-[10px] text-amber-400/80 ml-0.5"
                        :title="`昨日涨停共 ${emotion.auction.limit_up_total} 只，其中 ${emotion.auction.count} 只的开盘价已到（竞价窗口内逐步刷新）`">·开盘价已到</span></template> ·
                平均高开 <b :class="pctClass(emotion.auction.avg_gap)">{{ signNum(emotion.auction.avg_gap) }}%</b>
                <span class="text-muted">（高开幅度 Top5）</span></div>
              <!-- ★ 2026-09-27（用户："盘中的高开幅度 Top5 的也加上代码"）：
                   与上面「接力 Top5」及全站口径一致 —— 名称→本地详情页、代码→雪球。
                   （原为纯文本 <span> 无任何链接；改为 name/code 两个 <a>，涨幅仍纯文本。） -->
              <div class="flex flex-wrap gap-1.5 mt-2.5">
                <span v-for="g in (emotion.auction.top || [])" :key="g.code"
                      class="px-1.5 py-0.5 rounded border border-border/60 font-mono inline-flex items-center gap-1"
                      :class="g.gap_pct >= 0 ? 'text-red-400' : 'text-emerald-400'">
                  <a :href="stockHref(g.code)" target="_blank" class="hover:underline">{{ g.name }}</a>
                  <a :href="xqUrl(g.code)" target="_blank" rel="noopener"
                     class="text-muted hover:text-accent" title="雪球">{{ g.code }}</a>
                  <span>{{ signNum(g.gap_pct) }}%</span>
                </span>
              </div>
            </div>
          </div>

          <!-- ★ B2 涨停复盘（zzshare：连板梯队/涨停清单；匿名限流时占位）
               ★ 2026-09-25：优先读**已落库快照**（`zz_daily_snapshots`）—— 原实现每次直连
                 zzshare ⇒ 匿名受限/盘中易失败，而日批明明已落库一份完整 payload。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">涨停梯队与清单
                <!-- ★ 2026-09-25：补**数据时点**。原来只写"读已落库快照"却**不说是哪天的**，
                     而最常见状态恰恰是"今天日批还没跑 ⇒ 退回昨日快照"
                     ⇒ 会被当成"今天的涨停数据"读（`stale`=双源都失败后的降级兜底）。 -->
                <span class="text-[10px] text-muted font-normal">
                  （zzshare 口径 · {{ limitReview.source === 'snapshot' ? '读已落库快照' : '实时直连' }}
                  <template v-if="limitReview.as_of">· 数据 <b class="font-mono">{{ limitReview.as_of }}</b></template>
                  <span v-if="limitReview.stale" class="text-amber-400">· 上一交易日（今日快照未生成）</span>）</span>
              </div>
              <!-- ★ 2026-09-27（用户："涨停梯队与清单的右上角加个详情按钮"）：
                   外站（quant.zizizaizai.com 复盘页，新开标签）；与「主线板块 Top5」标题行
                   同款布局（`flex items-center justify-between` + 右侧 `text-xs text-accent`）。 -->
              <a href="https://quant.zizizaizai.com/review/uplimit" target="_blank" rel="noopener"
                 class="text-xs text-accent hover:underline shrink-0 ml-2">详情</a>
            </div>
            <!-- ★★ 2026-09-29（P1）：盘中实时涨停/炸板（rt_k 全市场精确口径）。
                 与下面「连板梯队 / 炸板率」是**两个时点、两个覆盖**，刻意并列、各自标注：
                   · 本块 = 此刻快照（全市场 5569 只；判据用数据源的 `high_limit` 字段
                     ⇒ 20cm/ST/北交所板幅不会像 `change_pct>=9.9` 那样误判）
                   · 下面 = 日批快照 + `backtest_prices`（**收盘定稿** & ~839 只覆盖）
                 ⚠️ 盘前/休市时后端 `is_intraday=false` ⇒ 本块显示的是**上一交易日收盘定稿**
                 （`data_date`），与下面同源，不是"过期数据"。 -->
            <div v-if="realtimeUplimit?.available" class="mb-2 pb-2 border-b border-border/40"
                 :title="realtimeUplimit.note">
              <div class="flex items-center gap-3 flex-wrap text-[11px]">
                <span class="text-muted">实时
                  <b class="font-mono text-gray-300">{{ realtimeUplimit.is_intraday
                    ? (realtimeUplimit.updated_at || '').slice(11) : realtimeUplimit.data_date }}</b>
                  <span class="text-[10px]">{{ realtimeUplimit.is_intraday ? '' : '（收盘定稿）' }}</span></span>
                <!-- ★ 2026-10-23：涨停/跌停已在上方「看盘序」显示，此处去重，只留炸板/一字/晋级率 -->
                <span class="text-muted">炸板 <b class="font-mono text-amber-300">{{ realtimeUplimit.broken }}</b><span
                  class="text-[10px]">（{{ realtimeUplimit.broken_rate ?? '—' }}%）</span></span>
                <span class="text-muted">一字 <b class="font-mono text-gray-300">{{ realtimeUplimit.yizi }}</b></span>
                <span class="text-[10px] text-muted">全市场 {{ realtimeUplimit.trading }} 只 · 精确口径</span>
              </div>
              <!-- ★★ 2026-09-29（P3）：**晋级率** —— 昨日的涨停股今天还活着吗。
                   与上一行「炸板」是**两个分母**：上面 = 全市场曾触板；这里 = **昨日涨停股**。
                   后者才是"接力资金撤没撤"的直接读数（筛掉全市场噪声），也是「退潮禁接力」
                   判据的量化依据。数据 = 日批快照的昨日名单 × 上面同一份 rt_k 分类
                   （同一次抓取 ⇒ 零新增请求）；`promotion` 缺失时整行不渲染。 -->
              <div v-if="promotion?.available" class="flex items-center gap-3 flex-wrap text-[11px] mt-1"
                   :title="promotionTip">
                <span class="text-muted">晋级率
                  <b class="font-mono text-red-400">{{ promotion.promote_rate ?? '—' }}%</b>
                  <span class="text-[10px]">{{ promotion.promoted }}/{{ promotion.den }}</span></span>
                <span class="text-muted">昨涨停炸板
                  <b class="font-mono text-amber-300">{{ promotion.promote_break_rate ?? '—' }}%</b></span>
                <span class="text-[10px] text-muted">昨涨停 {{ promotion.universe_count }} 只
                  <template v-if="promotion.missing">（停牌/无数据 {{ promotion.missing }} 单列）</template></span>
                <span class="text-[10px]" :class="promotionColor">{{ promotion.verdict }}</span>
              </div>
            </div>
            <!-- ★★ 连板梯队结构（`uplimit_hot.ban_info`，此前**只落库、从未展示**）
                 —— 价值在**梯队完整性**：某级别为 0 而更高有 ⇒ 断层 ⇒ 高标孤立无承接，
                 短线退潮的常见前兆（实测 09-23 即 5/6 板空档却有 7 板）。 -->
            <div v-if="limitReview.ladder?.dist?.length" class="mb-2">
              <div class="flex items-center gap-1.5 flex-wrap text-[11px]">
                <span class="text-muted cursor-help"
                      title="连板梯队（zzshare 日批 ban_info，收盘定稿）：首板=新题材在扩散；≥2 板=资金在高位接力（风险更高）。全系统连板档位**只有这一份**口径。">连板梯队</span>
                <!-- ★ 2026-10-28（用户："首板单独标出来"）：这里与个股级标签同一套色义 ——
                     首板（level 1）**蓝** = 新启动；≥2 板**红** = 高位接力。
                     空档仍用 amber（现有「断层」警示，别混进红/蓝里）。 -->
                <span v-for="d in limitReview.ladder.dist" :key="d.level"
                      class="px-1.5 py-0.5 rounded border font-mono"
                      :class="d.count === 0
                        ? 'border-amber-500/50 bg-amber-500/10 text-amber-400'
                        : d.level === 1
                          ? 'border-sky-500/40 bg-sky-500/10 text-sky-300'
                          : 'border-rose-500/40 bg-rose-500/10 text-rose-300'"
                      :title="d.level === 1
                        ? `首板 ${d.count} 只（今日新启动，题材在扩散）`
                        : `${d.level} 连板 ${d.count} 只（高位接力，风险更高）`">
                  {{ d.level === 1 ? '首板' : d.level + '板' }} {{ d.count }}</span>
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
              <!-- ★★ 2026-10-28（用户："日批炸板率样本标注"）：覆盖率不足 50% 时**明确标成参考值**。
                   为什么必须有这一行：同一张卡上方是「实时全市场炸板 50%」，这里是「日批样本 42.1%」，
                   两个数并列却不标分母 ⇒ 用户只能问"哪个对"。差 8 个点几乎全部来自样本覆盖。 -->
              <div v-if="statsSampleLow" class="flex items-center gap-1.5 flex-wrap text-[10px] mb-1"
                   :title="`分母来自 backtest_prices，当日仅覆盖 ${limitReview.stats.sample_n} 只股票；` +
                           `样本内封住 ${limitReview.stats.total?.sealed} 只 vs 官方涨停 ` +
                           `${limitReview.stats.official_limit_up} 只 ⇒ 覆盖 ${limitReview.stats.coverage_pct}%。` +
                           `与上方实时口径（全市场 rt_k）**不可直接对比**。`">
                <span class="px-1 rounded bg-amber-500/15 text-amber-300 shrink-0">⚠ 参考值</span>
                <span class="text-muted">样本覆盖 {{ limitReview.stats.coverage_pct }}%
                  （{{ limitReview.stats.total?.sealed }}/{{ limitReview.stats.official_limit_up }} 只涨停 ·
                  样本 {{ limitReview.stats.sample_n }} 只）</span>
                <span class="text-muted/70">· 与上方实时口径分母不同</span>
              </div>
              <div class="flex items-center gap-3 flex-wrap text-[11px]"
                   :title="limitReview.stats.meta">
                <span class="text-muted">炸板率(A)
                  <b class="font-mono" :class="statsSampleLow ? 'text-amber-300/60' : 'text-amber-300'">{{ limitReview.stats.break_rate ?? '—' }}%</b>
                  <span class="text-[10px]">接力意愿</span></span>
                <span class="text-muted">大面率(B)
                  <b class="font-mono" :class="statsSampleLow ? 'text-red-400/60' : 'text-red-400'">{{ limitReview.stats.big_loss_rate ?? '—' }}%</b>
                  <span class="text-[10px]">亏钱烈度</span></span>
                <span class="text-muted">封板率
                  <b class="font-mono" :class="statsSampleLow ? 'text-gray-500' : 'text-gray-300'">{{ limitReview.stats.seal_rate ?? '—' }}%</b></span>
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
              <!-- ★ 2026-09-25：题材归因 `steps` 同样**按名取**（原 `Object.values` 位置取法脆弱，
                   真实字段是 plate_code / plate_name / plate_score / stocks）。
                   `plate_score` 量纲不对外 ⇒ 不直接展示，改显示**该题材的涨停家数**
                   （与上面的"连板梯队"构成"题材梯队"视角），强度分放悬停。 -->
              <div v-if="(limitReview.steps || []).length" class="flex flex-wrap gap-1.5 mb-2">
                <span v-for="(s, i) in limitReview.steps.slice(0, 10)" :key="i"
                      class="px-1.5 py-0.5 rounded text-[11px] bg-rose-500/10 text-rose-300 border border-rose-500/25"
                      :title="`题材强度分 ${s.plate_score ?? '—'}（口径未对外，仅相对比较）；题材代码 ${s.plate_code || '—'}`">
                  {{ s.plate_name || s.plate_code || '—' }}
                  <b v-if="(s.stocks || []).length" class="font-mono">{{ (s.stocks || []).length }}只</b>
                </span>
              </div>
              <!-- ★★ 2026-09-25 修复（用户问："这个数据是正常的吗？"）—— **数据正常、渲染错位**：
                   原实现按 `Object.values(s)[0]` / `.slice(1,4)` **按位置**取值，而 zzshare 的
                   `uplimit_stocks` 每行有 **27 个字段**（字典序第一个是 `amount` = 成交额）
                   ⇒ 8 行全渲染成 "3.83 · ·"（成交额 + 三个空字符串字段）。
                   ⇒ 改为**按字段名**取。⭐ 泛化：**永远不要靠 dict 的字段顺序取值** ——
                     数据源加一个字段（这里加的是 `auction_*`）就会静默错位，且**看起来像数据坏了**。
                   真实字段：stock_code / stock_name / up_limit_desc（如"2连板"）/
                   up_limit_time（涨停时间）/ amount（成交额,亿）/ market_c（流通市值,亿）。 -->
              <div v-if="(limitReview.stocks || []).length" class="text-xs space-y-0.5">
                <!-- ★ 2026-09-25：如实标注**这不是全量** —— 该表当日仅 3 只（全市场涨停 52 只），
                     不写清会被读成"今天只有这几只涨停"。权威家数看上方"连板梯队"。
                     （后端已按 stock_code 去重：上游同一只票会返回多行，只有 id 不同。） -->
                <div class="text-[10px] text-muted mb-0.5">
                  涨停明细（{{ limitReview.stocks.length }} 只 · zzshare 明细表，<b class="text-amber-400/90">非当日全量</b>；全量家数见上方梯队）
                </div>
                <div v-for="(s, i) in limitReview.stocks.slice(0, 8)" :key="s.stock_code || i"
                     class="flex items-center gap-2 border-b border-border/30 py-0.5">
                  <a v-if="s.stock_code" :href="stockHref(s.stock_code)" target="_blank"
                     class="font-semibold hover:text-accent truncate max-w-[92px]">{{ s.stock_name || s.stock_code }}</a>
                  <span v-else class="truncate">{{ s.stock_name || '—' }}</span>
                  <!-- ★ 2026-09-26（用户："盘中的涨停梯队与清单的股票代码跳到雪球"）：
                       全站口径一致 —— **名称→本地详情页**（上面那条 `<a>` 已是），**代码→雪球**。 -->
                  <a v-if="s.stock_code" :href="xqUrl(s.stock_code)" target="_blank" rel="noopener"
                     class="text-muted font-mono hover:text-accent">{{ s.stock_code }}</a>
                  <span v-if="s.up_limit_desc" class="px-1 rounded bg-red-500/15 text-red-400">{{ s.up_limit_desc }}</span>
                  <span v-if="s.up_limit_time" class="text-muted font-mono">{{ s.up_limit_time }}</span>
                  <span v-if="s.amount != null" class="ml-auto text-muted font-mono">成交 {{ s.amount }}亿</span>
                </div>
              </div>
            </template>
          </div>

        </div>
          <!-- ★ 2026-10-27：旧的市场底清单块已删除（2026-10-23 搬到右栏时留下 v-if=false 死代码；现并入观察器 ⑤）。 -->

          <!-- ★★ 2026-09-25 分栏安排（① 看盘序已全宽，在分栏之外）：
                 · 左列 = **盘面结构**（② 涨停梯队与清单 ③ 主线板块 Top5）
                 · 右列 = **行动**（⑤ 持仓状态；原 ④ 待执行决策 2026-09-28 已删 —— 与右栏教练卡重复）
               与竞价看板同一切法逻辑（左"读"右"做"），但**不加竖线**（多张独立卡各有边框）。
               ⚠️ 断点 **xl**(1280)；两列各自 `space-y-4` 堆叠、不做行对齐 ——
                 ⑤ 持仓状态的高度随持仓只数变化，行对齐必在短列留大片空洞。 -->
          <!-- ★★ 2026-09-28（用户："主线板块 Top5 放在左边，涨停梯队与清单和持仓状态放在右边"）：
               两列内容**互换** —— 用 **CSS `order`**（`xl:order-*` 仅宽屏生效）实现，
               而非搬动 ~200 行模板：**同一份 DOM、只改视觉列序** ⇒ 零搬运风险、易于回退。
               生效后：**左列 = ③ 主线板块 Top5**；**右列 = ② 涨停梯队与清单 + ⑤ 持仓状态**。
               （④ 待执行决策 2026-09-28 已删 —— 教练卡统一只在常驻右栏展示一份。）
               ⚠️ 窄屏（单列）不受影响 ⇒ 仍按 DOM 顺序 ②⑤③ 排。 -->
          <!-- ★ 2026-10-26：主线/持仓上移后本区只剩「涨停梯队与清单」⇒ 单列铺满。 -->
          <div class="min-w-0">
          <div class="min-w-0 space-y-4 xl:order-2">
          <!-- ★ 2026-09-25 用户："持仓状态放在涨停梯队与清单下面，主线板块 Top5 移到右边"
               ⇒ 左列 = ② 涨停梯队与清单 + ⑤ 持仓状态；右列 = ③ 主线板块（+ 当时的 ④ 待执行决策）。
               ★ 本次**必须搬内容**（顺序变更无法用"选切点"实现）：先在 ② 之后**插入副本**，
                 再删除 ④ 之后的原块（**先插后删** ⇒ 中途失败也不会丢卡片）。
               ★ 2026-09-28：④ 待执行决策已删 ⇒ 这里的列内容说明以本条后续注释为准。 -->
          <!-- ★ 2026-10-26：持仓状态已上移至「盘面速览」大卡（观察器下方）⇒ 此处不再渲染，避免同屏两份。 -->
          <!-- ★ 2026-09-28（用户："只留一份在右栏即可"）：原「待执行决策（N）」卡**已删除** ——
               它与常驻右栏那张教练卡是**同一份数据**，留两份必然同屏重复（且两处口径曾不一致
               ⇒ 用户看到"右栏不跟着变"）。教练卡现在的**唯一**展示位 = 右栏（`<aside>`，
               全天常驻、默认展开可折叠）：未决策可交互，已决策显示结果徽标。 -->
          </div>
          <!-- ★ 2026-09-28：本列（③ 主线板块 Top5）经 `xl:order-1` 排到**视觉左列**；
               原 ④ 待执行决策卡已删（教练卡只在右栏留一份）⇒ 本列只剩主线板块一张卡。 -->
          <div class="min-w-0 space-y-4 xl:order-1">

          <!-- ★ A1 主线板块 Top5 + **板块内强势股**（看盘序第 4-5 层）
               ★ 2026-09-25：改用实时行业板块列表（原为昨日 15:10 快照）并补上「领涨股」——
                 此前缺的正是"板块内强势股"这一层（东财板块接口自带 leader 字段，零成本）。 -->
          <!-- ★ 2026-10-26：主线 Top5 与行业成交占比已上移至「盘面速览」大卡 ⇒ 此处不再渲染。 -->

          <!-- ★ 2026-09-28：原 ④ 待执行决策卡**已删除**（用户："只留一份在右栏即可"）——
               教练卡唯一展示位 = 常驻右栏 `<aside>`：未决策可交互、已决策显示结果徽标、
               标题行可折叠（默认展开）。此处不再有卡片。 -->
          </div>
          </div>

          <!-- ★ 2026-09-28（用户："评分榜 Top10 加多一列主力行为，挂在持仓摘要的下方"）：
               「评分榜 Top10 + 观察池」两卡已移到 **常驻右栏**（`<aside>` 内、持仓摘要下方）
               ⇒ 此处不再渲染（**避免与右栏同屏重复**；右栏全阶段常驻，覆盖面更广）。 -->
        </template>

        <!-- 实时模式 · ④ 盘后（阅读模式） -->
        <template v-else-if="selectedPhase === 'postmarket'">
          <!-- ★★ 2026-09-25 加固（用户问："盘后这些数据真的能在 15:00 百分百能拿到吗？"）——
               答案**不是百分百**，所以要让它**自证**：本行逐项显示每个模块的就绪状态
               **与数据时点**，让"哪些是今天的、哪些是上一交易日的、哪个失败了"一眼可辨，
               而不是让人对着可能是旧数据的数字做决策。
               ★ 四类数据的真实机制（详见各卡注释）：
                 ① 数据库类（今日执行 / 系统时间线）—— **一定能拿到** ✓
                 ② 内存行情类（今日结算 / 盘面定稿）—— 交易时段由调度器每 2~3 分钟刷新 ⇒
                    15:00 拿到的是**收盘定稿** ✓；⚠️ 但"进程在 15:00 前重启"的 ≤3 分钟窗口内，
                    缓存是**数据库收盘快照**（上一交易日）⇒ 看是否出现"收盘快照"警示。
                 ③ **日批快照类（观察池）** —— 快照由**晚间日批**写入 ⇒ 15:00 显示的是
                    **上一交易日**的池子（已在下方标注 `data_date`）⚠️ 最容易误读的一项。
                 ④ 外部接口类（板块 Top5 / 外盘）—— 东财等**可能限流/失败** ⇒ 不保证（失败则不显示该块）。
               ⚠️ 因此本页所有"今日/收盘"表述都以**数据自带时刻**为准，不用"请求时刻"顶替
                 （后端 `_cache['data_ts']` 与 `from_snapshot` 就是为此存在的）。 -->
          <div class="bg-card border border-border rounded-lg px-4 py-2 text-[11px] flex items-center gap-x-3 gap-y-1 flex-wrap">
            <span class="text-muted">盘后数据就绪</span>
            <span :class="radarErr ? 'text-red-400' : radarItems.length ? 'text-emerald-400' : 'text-amber-400'">
              持仓结算 {{ radarErr ? '失败' : (radarItems.length ? '✓' : '加载中') }}</span>
            <span :class="emotion ? 'text-emerald-400' : 'text-amber-400'">
              盘面定稿 {{ emotion ? '✓' : '未就绪' }}<template
                v-if="ovStyle?.from_snapshot && ovStyle?.data_date">（数据 {{ ovStyle.data_date }} · <b>快照</b>）</template><template
                v-else-if="ovStyle?.as_of">（{{ ovStyle.as_of }}<template
                v-if="ovStyle.from_snapshot"> · <b>快照</b></template>）</template></span>
            <span :class="topErr ? 'text-red-400' : topItems.length ? 'text-emerald-400' : 'text-amber-400'">
              评分榜 {{ topErr ? '失败' : (topItems.length ? '✓' : '加载中') }}</span>
            <span :class="gwErr ? 'text-red-400' : gwItems.length ? 'text-emerald-400' : 'text-muted'">
              观察池 {{ gwErr ? '失败' : (gwItems.length ? '✓' : '空（正常）') }}<template
                v-if="gwMeta.data_date">（{{ gwMeta.data_date }}<template
                v-if="gwMeta.source === 'snapshot'"> 日批快照</template>）</template></span>
            <span :class="todayExecErr ? 'text-red-400' : todayExec ? 'text-emerald-400' : 'text-amber-400'">
              今日执行 {{ todayExecErr ? '失败' : (todayExec ? '✓' : '加载中') }}<template
                v-if="todayExec && !todayExec.pushed_total">（今日无推送）</template></span>
            <span :class="sectorTop.length ? 'text-emerald-400' : 'text-muted'">
              板块 Top5 {{ sectorTop.length ? '✓' : '未返回（外部接口可能受限）' }}</span>
            <!-- ★ 2026-09-26（用户："这个文字改为鼠标经过显示此说明"）：
                 原为**常显**的一整行小字（占宽度、每次都在抢注意力）⇒ 收成一个 `ⓘ` 悬停说明。 -->
            <span class="text-muted text-[10px] cursor-help shrink-0"
                  title="观察池 / 板块快照类由【晚间日批】写入 ⇒ 15:00 看到的是上一交易日；其余各项走内存行情与数据库，收盘后即定稿。">ⓘ</span>
          </div>
          <!-- ★★ 2026-09-25（用户："把合理的都做了"）—— **P0 盘后「今日结算」**：
               原盘后只有"系统榜 + 推送日志"，看不到"我今天的结果"（`summary` 里没有盈亏字段）
               ⇒ 收盘时页面在讲"系统"、不在讲"你"。本卡补上：当日收益 / 涨跌家数 / 累计浮盈 /
               逐只当日表现。口径见 `portfolioDay` 注释（按市值加权，缺股数的不计入加权）。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
              <div class="text-sm font-semibold">今日结算（组合当日表现）
                <span class="text-[10px] text-muted font-normal">
                  （按持仓市值加权<template v-if="portfolioDay && portfolioDay.usable !== portfolioDay.n">
                  · 参与加权 {{ portfolioDay.usable }}/{{ portfolioDay.n }} 只</template><template
                  v-if="radarAsOf"> · 数据 {{ radarAsOf.slice(11, 16) }}</template>）</span></div>
              <router-link target="_blank" to="/paper" class="text-xs text-accent hover:underline">模拟盘</router-link>
            </div>
            <template v-if="portfolioDay">
              <div class="grid grid-cols-2 md:grid-cols-4 gap-3 text-center text-xs">
                <div>
                  <div class="text-2xl font-bold font-mono" :class="pctClass(portfolioDay.dayPct)">
                    {{ portfolioDay.dayPct == null ? '—' : signNum(portfolioDay.dayPct) + '%' }}</div>
                  <div class="text-muted">当日收益（加权）</div>
                  <div v-if="portfolioDay.dayAmt != null" class="font-mono text-[11px]"
                       :class="pctClass(portfolioDay.dayAmt)">
                    {{ portfolioDay.dayAmt > 0 ? '+' : '' }}{{ fmtNum(portfolioDay.dayAmt) }} 元</div>
                </div>
                <div>
                  <div class="text-2xl font-bold font-mono">
                    <span class="text-red-400">{{ portfolioDay.up }}</span>
                    <span class="text-muted text-base"> / </span>
                    <span class="text-emerald-400">{{ portfolioDay.down }}</span></div>
                  <div class="text-muted">持仓涨 / 跌<template v-if="portfolioDay.flat">（平 {{ portfolioDay.flat }}）</template></div>
                </div>
                <div>
                  <div class="text-2xl font-bold font-mono" :class="pctClass(portfolioDay.pnlPct)">
                    {{ portfolioDay.pnlPct == null ? '—' : signNum(portfolioDay.pnlPct) + '%' }}</div>
                  <div class="text-muted">累计浮盈（加权）</div>
                </div>
                <div>
                  <div class="text-2xl font-bold font-mono text-gray-200">
                    {{ fmtNum(portfolioDay.mvNow) }}<span class="text-muted text-[10px]"> 元</span></div>
                  <div class="text-muted">持仓市值</div>
                </div>
              </div>
              <!-- 逐只当日表现：★ 条长用**固定刻度**（±10% 满格，最小 3% 保底）而不是榜内归一化
                   —— 固定刻度才能**跨日比较**（与竞价刻度尺同一条理由）。 -->
              <div v-if="portfolioDay.ranked.length" class="mt-3 pt-3 border-t border-border/40 space-y-1">
                <div class="text-[10px] text-muted">持仓当日表现（按当日涨跌排序 · 条长刻度 ±10%）</div>
                <div v-for="h in portfolioDay.ranked" :key="h.code"
                     class="flex items-center gap-2 text-[11px] font-mono">
                  <a :href="stockHref(h.code)" target="_blank"
                     class="w-[76px] shrink-0 truncate hover:text-accent">{{ h.name || h.code }}</a>
                  <span class="flex-1 h-2.5 rounded-sm bg-border/25 overflow-hidden">
                    <span class="block h-full rounded-sm"
                          :class="Number(h.day_pct) >= 0 ? 'bg-red-500/60' : 'bg-emerald-500/60'"
                          :style="{ width: Math.max(3, Math.min(100, Math.abs(Number(h.day_pct) || 0) * 10)) + '%' }"></span>
                  </span>
                  <span class="w-[56px] shrink-0 text-right" :class="pctClass(h.day_pct)">{{ signNum(h.day_pct) }}%</span>
                </div>
              </div>
              <div class="text-[10px] text-muted mt-2">
                当日盈亏 = 今日市值 − 昨收市值（同股数）· 累计浮盈 = 今日市值 / 持仓成本 − 1；
                ⚠️ 停牌或无行情的持仓**不计入加权**（仍计入涨跌家数）。
              </div>
            </template>
            <div v-else class="text-muted text-xs">—（无持仓，或持仓数据未就绪）</div>
          </div>
          <!-- ★★ 2026-09-25（盘后补强 · 缺口1）「**今日盘面定稿**」——
               15:00 时页面上**没有"今天是什么行情"**：日报要 22:16 才生成，而 zzshare 快照此刻
               仍是**昨天的**（`limit-review` 实测 stale=True）⇒ 两者都不能用。
               ⇒ 本卡改用**内存行情**（收盘后不再变化 = 收盘定稿）给出：涨跌家数 / 涨跌停 /
               成交额 / 情绪判读 + 大小盘风格 + 昨日涨停赚钱效应 + 板块主线 Top5。
               ⚠️ 与盘中「看盘序」是**同一份数据**，差别只在时点（15:00 看 = 定稿）；
                  这里**不抽公共组件**：两处措辞与取舍不同（盘中强调实时、盘后强调定稿，
                  且盘后刻意不含炸板率/大面率 —— 那两项来自 zzshare 快照，15:00 还是昨天的，
                  放进来会拿昨天的数据冒充今天）。
               ⚠️ 成交额与家数依赖常驻的 `overview`（全天轮询）✓；情绪/板块由 `loadPhaseData`
                  在切到盘后时补拉（见该函数注释）。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
              <div class="text-sm font-semibold">今日盘面定稿
                <span class="text-[10px] text-muted font-normal">（收盘口径<template
                  v-if="ovStyle?.from_snapshot && ovStyle?.data_date"> · 数据 {{ ovStyle.data_date }} 收盘</template><template
                  v-else-if="ovStyle?.as_of"> · 行情 {{ ovStyle.as_of }}</template>）</span></div>
              <!-- ★ 2026-09-25 加固（用户问"15:00 真能拿到吗"）：**数据时刻必须取"数据自身时刻"**
                   `ovStyle.as_of`（后端来自 `_cache['data_ts']`），而不是 `emotion.as_of`
                   （那只是**计算时刻**）—— 两者在"进程重启后从收盘快照恢复"时会差一整天，
                   用错就是**谎报新鲜度**（后端注释里专门警告过这一点）。
                   ★★ 2026-10-14（用户报"今天的数据怎么用了昨天的"）：`as_of`（写入/抓取时刻）
                   仍不足以表达"数据是哪天的"—— 快照可能在**凌晨/夜间**被写（冷启动 bootstrap，
                   内容 = 上一交易日收盘）⇒ 显示成"10-14 04:32"会被读成"今天的行情"。
                   ⇒ 快照路径改用后端下发的 **`ovStyle.data_date`（数据所属交易日）**，
                     如"数据 10-13 收盘"，与 ⚠ 徽章互为印证。 -->
              <span v-if="ovStyle?.from_snapshot" class="text-[10px] text-amber-400"
                    :title="`行情缓存来自数据库收盘快照（进程刚重启或非交易时段不重新抓取）⇒ 数据是 ${ovStyle?.data_date || '上一交易日'} 收盘，不是今天的`">
                ⚠ 来自收盘快照（非实时{{ ovStyle?.data_date ? ' · 数据 ' + ovStyle.data_date : '' }}）</span>
              <span v-else-if="emotion && emotion.trading_day === false"
                    class="text-[10px] text-amber-400">⚠ 非交易日（为最近交易日快照）</span>
            </div>
            <!-- ★ 2026-10-26（同上）：涨跌家数已上移至「盘面速览」，此处只留风格。 -->
            <!-- 大小盘风格（决定"今天赚指数还是赚个股"，与盘后结算卡互为解释） -->
            <div v-if="ovStyle?.available" class="text-[11px] mt-1">
              <div class="flex items-center gap-2 flex-wrap">
                <span class="text-muted">大小盘风格</span>
                <span class="font-semibold cursor-help" :class="styleCls" :title="styleTitle">{{ ovStyle.label }}</span>
                <span class="text-muted font-mono">
                  大 <b :class="pctClass(ovBig?.avg)">{{ signNum(ovBig?.avg) }}%</b>
                  <template v-if="ovMid">/ 中 <b :class="pctClass(ovMid?.avg)">{{ signNum(ovMid?.avg) }}%</b></template>
                  / 小 <b :class="pctClass(ovSmall?.avg)">{{ signNum(ovSmall?.avg) }}%</b></span>
                <span class="text-muted">· 背离（加权−等权）
                  <b class="font-mono text-gray-300">{{ signNum(ovStyle.spread) }}%</b></span>
              </div>
              <div class="text-muted mt-0.5">{{ ovStyle.note }}</div>
            </div>
            <!-- 情绪定稿：赚钱效应 + 连板高度（明日预判的输入） -->
            <div v-if="emotion" class="border-t border-border/40 mt-2 pt-2 text-[11px] text-muted">
              {{ prevLimitLabel() }}涨停 {{ emotion.prev_limit_count ?? '—' }} 只 · {{ priceDayLabel() }}平均表现
              <b :class="pctClass(emotion.prev_limit_today_pct)">{{ fmtPct(emotion.prev_limit_today_pct) }}</b>（赚钱效应）
              · 连板高度 <b class="text-gray-200 font-mono">{{ emotion.max_streak ?? '—' }}</b>
              <template v-if="emotion.leader">（{{ emotion.leader_name || emotion.leader }}）</template>
              <span class="text-[10px]">· {{ emotion.note }}</span>
            </div>
            <!-- 板块主线（收盘时刻的东财实时接口） -->
            <div v-if="sectorTop.length" class="border-t border-border/40 mt-2 pt-2">
              <div class="text-[11px] text-muted mb-1">板块主线 Top5
                <span class="text-[10px]">{{ sectorTopSrc === 'east' ? '（实时 · 含板块内领涨股）'
                  : sectorTopSrc === 'local' ? '（实时 · 本地聚合，龙头由涨停原因反推）'
                  : sectorTopSrc === 'zz' ? '（收盘定稿 · zzshare 104 板块）'
                  : sectorTopSrc === 'snap' ? '（快照回退 · 东财）' : '' }}</span>
                <router-link target="_blank" to="/sector" class="text-accent hover:underline ml-1">板块详情</router-link></div>
              <div class="flex flex-wrap gap-x-4 gap-y-1 text-[11px]">
                <span v-for="(s, i) in sectorTop.slice(0, 5)" :key="i" class="font-mono">
                  <span class="text-muted">{{ i + 1 }}</span>
                  <span class="font-semibold text-gray-200">{{ s.name || s.industry || s.code }}</span>
                  <b :class="pctClass(s.change_pct ?? s.pct_change)">{{ signNum(s.change_pct ?? s.pct_change) }}%</b>
                  <!-- ★ 2026-09-26：与盘中「主线板块 Top5」**保持同一口径** —— 领涨股补上
                       **代码**并跳雪球（后端 `/sector/industry` 本就返回 `leader_code`，零后端改动）。
                       理由：两处是同一份 `sectorTop` 的两种展示，只改一处会造成"同名模块口径不一致"。 -->
                  <span v-if="s.leader" class="text-muted">（{{ s.leader }}
                    <a v-if="s.leader_code" :href="xqUrl(s.leader_code)" target="_blank" rel="noopener"
                       class="text-muted hover:text-accent">{{ s.leader_code }}</a>
                    <b :class="pctClass(s.leader_change_pct)">{{ signNum(s.leader_change_pct) }}%</b>
                    <!-- ★ 2026-10-28：此处与盘中卡是**同一份 sectorTop 的两种展示**，
                         连板标签也必须同口径（只改一处会造成"同名模块口径不一致"，与 09-26 同理）。 -->
                    <b v-if="s.leader_boards_text" class="text-[10px] px-1 rounded"
                       :class="(s.leader_boards || 1) > 1 ? 'bg-rose-500/15 text-rose-300' : 'bg-sky-500/15 text-sky-300'">{{ s.leader_boards_text }}</b>）</span>
                </span>
              </div>
            </div>
          </div>
          <!-- ★★ 2026-09-25（盘后补强 · 缺口2）「**今日执行**」——
               15:00 该回答"今天系统推了几条、我做了几条"。此前只有右栏"未决策待办"（存量）
               与复盘里的**窗口统计**（近 N 天），**唯独缺"今天"这一天**。
               后端 `/coach/consistency` 本次新增 `day` 参数（按日精确）。
               ⚠️ 口径（后端定义）：分母 = **已决策**，"未响应"单列不进分母 ——
                  "没看见"与"看见了但放弃"是两回事，混算会虚高执行率。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
              <div class="text-sm font-semibold">今日执行（教练卡）
                <span class="text-[10px] text-muted font-normal">（{{ todayStr }} · 只统计今日**已推送**）</span></div>
              <router-link target="_blank" to="/coach" class="text-xs text-accent hover:underline">教练页</router-link>
            </div>
            <div v-if="todayExecErr" class="text-muted text-xs">—（加载失败：{{ todayExecErr }}）</div>
            <template v-else-if="todayExec">
              <div v-if="!todayExec.pushed_total" class="text-muted text-xs">
                —（今天没有推送的教练卡）。没有推送通常意味着**今天没有触发纪律条件**，属正常。
              </div>
              <template v-else>
                <div class="flex items-center gap-6 flex-wrap text-xs">
                  <div>
                    <div class="text-2xl font-bold font-mono"
                         :class="(todayExec.exec_rate_pct || 0) >= 60 ? 'text-emerald-400' : 'text-amber-300'">
                      {{ todayExec.exec_rate_pct ?? '—' }}%</div>
                    <div class="text-[11px] text-muted">执行率（分母=已决策）</div>
                  </div>
                  <div class="space-y-0.5">
                    <div>今日推送 <b class="font-mono text-gray-200">{{ todayExec.pushed_total }}</b> 条</div>
                    <div>已执行 <b class="text-emerald-400 font-mono">{{ todayExec.executed }}</b> ·
                      已放弃 <b class="text-amber-300 font-mono">{{ todayExec.abandoned }}</b> ·
                      未响应 <b class="text-muted font-mono">{{ todayExec.ignored }}</b></div>
                  </div>
                </div>
                <!-- 今日清单（与上方数字同口径：只列已推送的） -->
                <div v-if="todayPushedCards.length" class="mt-2 pt-2 border-t border-border/40 space-y-1 text-xs">
                  <div v-for="a in todayPushedCards" :key="a.id" class="flex items-center gap-2 flex-wrap">
                    <!-- ★ 2026-09-28：这处原本用 `.slice(11,16)`（依赖 ISO 格式才碰巧对）⇒
                         统一走 hhmm()，与教练卡/回放卡历史同口径。 -->
                    <span class="text-muted font-mono">{{ hhmm(a.alert_time) }}</span>
                    <span class="font-semibold text-gray-200">{{ a.label || a.rule_id }}</span>
                    <a :href="stockHref(a.code)" target="_blank"
                       class="hover:text-accent">{{ a.name || a.code }}</a>
                    <span class="ml-auto"
                          :class="a.executed === 'yes' ? 'text-emerald-400'
                                  : a.executed === 'no' ? 'text-amber-300' : 'text-muted'">
                      {{ a.executed === 'yes' ? '已执行' : a.executed === 'no' ? '已放弃' : '未响应' }}</span>
                  </div>
                </div>
                <div class="text-[10px] text-muted mt-1">{{ todayExec.note }}</div>
              </template>
            </template>
            <div v-else class="text-muted text-xs">—（加载中…）</div>
          </div>
          <!-- ★ 2026-09-25 用户："评分榜 Top10 和观察池（买入闸门候选）左右卡片布局"
               ⇒ 两卡**并排**（都是"清单"性质、各自不长；并排后盘后首屏更紧凑）。
               ⚠️ 断点 **xl**(1280)：主区被常驻右栏占掉 320px；窄屏回退单列（信息不丢）。
               ⚠️ **不加竖分割线** —— 两边是独立卡片、各有边框（与盘中分栏同一条理由）。 -->
          <!-- ★ 2026-09-26（用户："评分榜 Top10 和观察池（买入闸门候选）等高"）：
               `items-start` → **`items-stretch`** + 列与卡片都加 `h-full`
               ⇒ 两卡等高（与复盘"执行一致性 | 明日准备"同一做法）。
               ⚠️ 窄屏回退单列时 `h-full` 无害（行高仍由内容决定）。 -->
          <!-- ★ 2026-09-28：本处两卡已移到**常驻右栏**（持仓摘要下方）—— 见 `<aside>` 内注释。 -->
          <!-- ★ 2026-09-26（用户："删除盘后的"）—— 删掉盘后主区这张卡：
               它与右栏「今日系统时间线」**完全同源**（同一个 `pushItems` ← `push_log`），
               而右栏是**全时段常驻**的 ⇒ 盘后照样能看到 ⇒ 留两份只会同屏重复。
               （右栏那份已"条数放开 + 320px 限高滚动"，见其注释。） -->
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
              —— 日批（GitHub Actions）尚未跑完：正常路径 = 上游包 <b>20:43</b> 完成 → 日批
              <b>~21:57</b> 全链完成（日报实测 <b>22:16</b>）；⚠️ 兜底日会拖到 <b>~00:57</b>。
              <b>日批跑完会企微通知你</b>（Actions 完成即回调后端推一条，**事件驱动、无轮询**），
              收到通知再回来即可 —— 本页不会自己轮询等待。</span>
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

          <!-- ★ 2026-09-25 需求 3：尾盘承接（复盘回看今天尾盘的**最终**承接结果 ——
               此时"现在"即收盘值，Δ 就是"14:30 → 收盘"的尾段变化，正是决定隔夜仓的那半小时）。 -->
          <div v-if="tailReview?.available" class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
              <div class="text-sm font-semibold">尾盘承接
                <span class="text-[10px] text-muted font-normal">（14:30 → 收盘 · 决定隔夜仓）</span></div>
              <div class="text-xs font-semibold cursor-help" :class="tailCls" :title="tailTitle">
                {{ tailReview.label }}</div>
            </div>
            <div class="text-xs flex items-center gap-2 flex-wrap">
              <span class="text-muted font-mono">
                Δ涨停 <b :class="pctClass(tailReview.deltas.limit_up)">{{ tailDelta(tailReview.deltas.limit_up) }}</b>
                · Δ上涨 <b :class="pctClass(tailReview.deltas.up)">{{ tailDelta(tailReview.deltas.up) }}</b>
                · 尾盘量能 <b class="text-gray-300">{{ tailReview.deltas.amount_yi }}亿</b>
              </span>
              <span v-for="i in (tailReview.indices || [])" :key="i.code"
                    class="text-muted font-mono">
                {{ i.name }} <b :class="pctClass(i.d_pct)">{{ signNum(i.d_pct) }}%</b>
              </span>
            </div>
            <div class="text-[11px] text-gray-300 mt-1">{{ tailReview.advice }}</div>
            <div class="text-[10px] text-muted mt-1">{{ tailReview.note }}</div>
          </div>

          <!-- ★ 2026-09-26（用户："我想把执行一致性卡片和明日准备（待触发计划）卡片左右布局"）：
               两卡都短（执行一致性 = 一个大数字 + 两行；明日准备 = N 条计划）
               ⇒ 并排后复盘首屏更紧凑，且**语义相配**：
                 一个答"今天做得对不对"（执行），一个答"明天要做什么"（交接棒）。
               ⚠️ 断点 **xl**(1280)：主区被常驻右栏占掉 320px；窄屏回退单列（信息一条不丢）。
               ⚠️ **不加竖分割线** —— 两边是独立卡片、各自有边框（与盘后/盘中分栏同一理由）。
               ⚠️ 顺带把「明日准备」**上移**到两篇长文（决策简报 / A股日报）之前：
                 原顺序把"行动"夹在两篇长文之间 ⇒ 改为"先结论与行动、后长文"。
               ★ 2026-09-26 追加（用户："讲执行一致性和明日准备的高度保持一致吧"）：
                 外层 `items-start` → **`items-stretch`**，且列与卡片都加 `h-full`
                 ⇒ 两卡**等高**（否则短的那张只占自身高度，旁边留一块空白，看起来像缺内容）。
                 ⚠️ 窄屏回退单列时 `h-full` 无害（行高仍由内容决定）。 -->
          <div class="grid grid-cols-1 xl:grid-cols-2 gap-x-4 gap-y-4 items-stretch">
          <div class="min-w-0 h-full">
          <div class="bg-card border border-border rounded-lg p-4 h-full flex flex-col">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">执行一致性</div>
              <router-link target="_blank" to="/coach" class="text-xs text-accent hover:underline">教练页</router-link>
            </div>
            <!-- ★ 2026-09-25 用户反馈：不再裸奔 JSON，按指标渲染 -->
            <div v-if="!consistency" class="text-muted text-xs">—（暂无数据）</div>
            <template v-else>
              <!-- ★ 2026-09-26（用户："执行一致性卡片下部分有空白处，有没有好的布局方式？"）：
                   该卡内容天然少（一个大数字 + 两行），而**等高**后被"明日准备"拉到同样高度
                   ⇒ 底部留白。解法：内容区 `flex-1 + items-center`**垂直居中**（把留白分摊到上下，
                   成为呼吸感），`note` 用 `mt-auto` + 上边框**贴底**当脚注
                   ⇒ 视觉上"上半是数字、下半是说明"，而不是一堆空白堆在底部（那看着像缺内容）。 -->
              <div class="flex-1 flex items-center">
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
              </div>
              <div class="text-[11px] text-muted mt-auto pt-2 border-t border-border/40">
                {{ consistency.note }}</div>
            </template>
          </div>
          </div>
          <div class="min-w-0">
          <!-- ★★ 2026-09-25（P1「明日准备」）—— 复盘 → 次日的**交接棒**。
               复盘原本 6 块全在回答"今天发生了什么"，**没有一块回答"明天要做什么"**
               ⇒ 复盘的结论没有被转成明日待办，一天的首尾（复盘 → 次日盘前）没接上。
               数据：`/user/plans` 的 `status=waiting` 计划（含 A4 的结构化触发条件）。
               ⚠️ 本卡只摆事实，不催办、不改状态。 -->
          <div class="bg-card border border-border rounded-lg p-4 h-full">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">明日准备（待触发计划）
                <span class="text-[10px] text-muted font-normal">（未触发 {{ plans.length }} 条）</span></div>
              <router-link target="_blank" to="/trade-plans" class="text-xs text-accent hover:underline">计划管理</router-link>
            </div>
            <div v-if="plansErr" class="text-muted text-xs">—（加载失败：{{ plansErr }}）</div>
            <div v-else-if="!plans.length" class="text-muted text-xs">
              —（暂无待触发计划）。复盘出的想法应当**在今晚写成计划** —— 盘前写预案、盘中不临场；
              有触发条件的计划会由系统盘中每 60s 比对行情并推送。
            </div>
            <div v-else class="space-y-1.5 text-xs">
              <div v-for="p in plans" :key="p.id" class="border-b border-border/40 pb-1.5">
                <div class="flex items-center gap-2 flex-wrap">
                  <span class="px-1 rounded text-[10px] shrink-0"
                        :class="p.plan_type === 'add' ? 'bg-red-500/15 text-red-400'
                                : p.plan_type === 'target' ? 'bg-amber-500/15 text-amber-300'
                                : p.plan_type === 'stop' ? 'bg-emerald-500/15 text-emerald-400'
                                : 'bg-sky-500/15 text-sky-400'">{{ PLAN_TYPE_CN[p.plan_type] || '试仓' }}</span>
                  <a :href="stockHref(p.code)" target="_blank"
                     class="font-semibold hover:text-accent">{{ p.name || p.code }}</a>
                  <span class="font-mono text-muted">{{ p.code }}</span>
                  <span v-if="p.buy_price" class="font-mono text-muted">买 {{ p.buy_price }}</span>
                  <span v-if="p.stop_loss" class="font-mono text-emerald-400/90">止损 {{ p.stop_loss }}</span>
                  <span v-if="p.target" class="font-mono text-red-400/90">目标 {{ p.target }}</span>
                </div>
                <!-- A4 结构化触发条件：到点只需执行、不需临场判断 -->
                <div v-if="p.trigger_high_open != null || p.trigger_volume_break != null"
                     class="text-[11px] text-accent/90 mt-0.5">
                  触发：<template v-if="p.trigger_high_open != null">高开 ≥ {{ p.trigger_high_open }}%</template>
                  <template v-if="p.trigger_high_open != null && p.trigger_volume_break != null"> ｜ </template>
                  <template v-if="p.trigger_volume_break != null">放量过 {{ p.trigger_volume_break }}</template>
                </div>
                <div v-if="p.reason" class="text-[11px] text-muted mt-0.5">{{ p.reason }}</div>
              </div>
            </div>
          </div>
          </div>
          </div>
          <!-- 长文卡（保持在最后：先看结论与行动，再看长文） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">决策简报（盘后）</div>
              <div class="flex items-center gap-2">
                <span v-if="briefDegraded && briefPhase === 'postmarket'"
                      class="text-[10px] text-amber-400 cursor-help"
                      title="当前是**规则降级版**（LLM 调用失败时的兜底输出）—— 数字可靠，但少了 LLM 的综合判断。可点右侧重试。">降级版</span>
                <!-- ★ 2026-09-30：盘后简报此前**只能等日批（19:xx）/切到复盘段**才生成；
                     现补「生成/重新生成」入口（后端 refresh=true 早已支持）。 -->
                <button @click="loadBrief('postmarket', true)" :disabled="briefLoading"
                        class="px-2 py-0.5 rounded text-[11px] border border-border text-accent hover:border-accent/50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        title="生成盘后决策简报。⚠️ 消耗 1 次 LLM 调用（同步，约需十几秒）。">
                  {{ briefLoading ? '生成中…' : (briefPhase === 'postmarket' && briefMd ? '重新生成' : '生成') }}
                </button>
              </div>
            </div>
            <div v-if="briefLoading" class="text-muted text-xs">生成中…（LLM 同步调用，约需十几秒）</div>
            <div v-else-if="briefErr" class="text-xs text-fall">—（生成失败：{{ briefErr }}）
              <button @click="loadBrief('postmarket', true)"
                      class="ml-2 px-2 py-0.5 rounded border border-border text-accent hover:border-accent/50 transition-colors">重试</button>
            </div>
            <div v-else-if="briefPhase !== 'postmarket' || !briefMd" class="text-muted text-xs">
              —（未生成。正常由日批 19:xx 自动生成，也可点右上「生成」马上出一份）
            </div>
            <!-- ★ 2026-10-11：与盘前同一份「观点卡」（首行 `**结论**：…` → 结论条置顶） -->
            <InsightBody v-else :text="briefMd" class="text-xs" />
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
      <aside class="space-y-3 min-w-0">

        <!-- ★★ 2026-10-24（用户："快讯卡片过于简略，要看大盘/板块/个股异动 + 原因 + 影响范围"）：
             原「快讯诊断」一行摘要 → 独立组件 `FlashBriefCard.vue`：
               · 大盘（指数/涨跌家数/涨跌比/涨停/成交额）
               · 板块轮动（领涨领跌 + 主力资金）
               · 个股异动（系统规则命中，带标签）
               · 重要快讯卡片列表（时间/簇/方向/一句话），点开看 原因 / 影响范围
               · AI 结论（一次批量 LLM；`GET` 只读缓存，`POST` 才解读）
             ⚠️ 系统级：盘中异动由调度器 `brief_loop` 自动触发并推浏览器通知。
             ⚠️ 原诊断信息（D状态/叙事/仓位）保留在卡内首行，不做两处渲染。 -->
        <!-- ★ 2026-10-26：快讯解读已并入「盘面速览」大卡（主区观察器下方）⇒ 右栏不再重复。 -->

        <!-- ★ 2026-10-25：李大霄视角卡（xiaopage LLM 解读推送接入，**纯展示参考不进决策链**；
             事件驱动：xiaopage 在 LLM 输出解读的时刻 POST 回调落库，本卡 60s 随教练卡轮询。
             卡面按用户线框定稿：情绪/防御话术/final_conclusion/板块/近14期情绪条。 -->
        <div v-if="lidx && lidx.latest" class="bg-card border border-border rounded-lg p-3">
          <div class="flex items-center justify-between mb-1.5 flex-wrap gap-1">
            <!-- ★★ 2026-10-26（用户："李大霄视角卡片加上展开收起功能"）：标题即开关 -->
            <!-- ★ 2026-10-28（用户："简化一下文字使得按钮能加进去"）：
                 「外部观点参考」+「数据截至 10-24 21:02 · 落后4天」≈ 300px，
                 塞不下按钮 ⇒ 缩为「10-24 21:02 · 4天前」，原义移到 hover title。 -->
            <button type="button" class="text-sm font-semibold flex items-center gap-1 hover:text-accent text-left"
                    @click="lidxOpen = !lidxOpen" :title="lidxOpen ? '点击收起' : '点击展开'">
              <span class="text-[10px] text-muted">{{ lidxOpen ? '▼' : '▶' }}</span>
              李大霄视角
            </button>
            <div class="flex items-center gap-1.5 shrink-0">
              <span class="text-[10px]" :class="lidx.stale ? 'text-amber-400' : 'text-muted'"
                    :title="`外部观点参考（xiaopage LLM 二手解读，不进决策链）· 数据截至 ${lidx.latest.date || '—'}`">
                {{ (lidx.latest.date || '').slice(5, 16) }} · {{ lidx.lag_days == null ? '—' : lidx.lag_days }}天前<template v-if="lidx.stale"> ⚠</template>
              </span>
              <!-- ★ 2026-10-28：手动补拉（推送失败兜底）。盘后也会更新，故不能只靠事件回调。 -->
              <button type="button"
                      class="text-[10px] px-1.5 py-0.5 rounded border border-border hover:text-accent hover:border-accent/40 disabled:opacity-50 disabled:cursor-wait"
                      :disabled="lidxBusy"
                      :title="'从 xiaopage 补拉最新解读（盘后也会更新；上游没变时零流量）'"
                      @click="refreshLidx">
                {{ lidxBusy ? '…' : '↻ 更新' }}
              </button>
            </div>
          </div>
          <div v-if="lidxMsg" class="text-[10px] mb-1"
               :class="lidxMsg.ok ? 'text-emerald-400' : 'text-amber-400'">{{ lidxMsg.text }}</div>

          <template v-if="lidxOpen">
            <!-- ★★ 2026-10-26（用户："一天可能有几个视频，卡片内部 tab 切换，
                 支持水平滚动，选中后 tab 自行居中"）：最近 12 条视频各一个 tab。
                 实测 10-23 就有 3 条（10:52 / 14:18 / 20:33）⇒ 必须能选，不能只显示最新。 -->
            <div v-if="lidxItems.length > 1"
                 ref="lidxTabsEl"
                 class="flex gap-1 overflow-x-auto pb-1 -mx-1 px-1 wb-hscroll">
              <button v-for="(t, i) in lidxItems" :key="t.entry_id || i"
                      :ref="(el) => setLidxTab(el, i)"
                      type="button"
                      class="shrink-0 px-2 py-0.5 rounded text-[10px] border transition-colors"
                      :class="i === lidxSel
                        ? 'bg-accent/20 text-accent border-accent/40'
                        : 'bg-white/5 text-muted border-border hover:text-gray-200'"
                      :title="t.title || ''"
                      @click="selectLidx(i)">
                {{ lidxTabLabel(t) }}
              </button>
            </div>
            <div class="flex items-center gap-2 text-xs mb-1 mt-1.5 flex-wrap">
              <span class="font-semibold text-gray-200">{{ lidxCur.title }}</span>
              <a v-if="lidxCur.video_url" :href="lidxCur.video_url" target="_blank"
                 class="text-accent hover:underline flex-shrink-0">{{ lidxCur.bvid }} ↗</a>
              <span class="text-muted flex-shrink-0">{{ lidxCur.date }}</span>
            </div>
            <div class="text-xs mb-1.5">
              情绪 <span class="font-bold font-mono" :class="lidxLevelColor(lidxCur.sentiment_level)">{{ lidxCur.sentiment_score ?? '—' }}</span>
              <span :class="lidxLevelColor(lidxCur.sentiment_level)">· {{ lidxCur.sentiment_level || '—' }}</span>
              <span class="text-muted"> · 防御话术 {{ lidxCur.disclaimer_level ?? '—' }}/5</span>
            </div>
            <div v-if="lidxCur.final_conclusion" class="text-xs text-gray-300 mb-1.5 leading-relaxed">
              ▸ {{ lidxCur.final_conclusion }}
            </div>
            <div v-if="lidxCur.sectors && lidxCur.sectors.length" class="flex flex-wrap gap-1 mb-2">
              <span v-for="s in lidxCur.sectors" :key="s"
                    class="px-1.5 py-0.5 rounded text-[11px] bg-blue-500/10 text-blue-300 border border-blue-500/20">{{ s }}</span>
            </div>
            <div v-if="lidx.trend && lidx.trend.length" class="flex items-end gap-[3px] h-8">
              <div v-for="(t, i) in lidx.trend" :key="i"
                   class="flex-1 min-w-[4px] rounded-t"
                   :style="{ height: Math.max(6, t.score || 0) + '%', background: lidxBarColor(t.score) }"
                   :title="`${t.date} · 情绪${t.score} · ${t.title}`"></div>
            </div>
            <div v-if="lidx.trend && lidx.trend.length" class="text-[10px] text-muted mt-1">
              近{{ lidx.trend.length }}期情绪（0~100，越高越乐观）
            </div>
          </template>
        </div>

        <!-- ★ 2026-10-27：市场底清单已并入观察器 ⑤（日频数据，300s 刷新），右栏不再保留副本。 -->

        <!-- 教练卡（今日）—— **全站唯一一份**
             ★ 2026-09-28 起唯一展示位就在右栏；10-24 紧贴清单下方；
               10-27 清单并入观察器后，右栏首张卡（上方是快讯解读 / 李大霄）。
             ⚠️ 回放（isReplay）时 readonly=true ⇒ 只读 + 状态徽标（历史不可改）。 -->
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="flex items-center justify-between mb-1.5 flex-wrap gap-2">
            <button type="button" class="text-sm font-semibold flex items-center gap-1 hover:text-accent"
                    @click="todoOpen = !todoOpen" :title="todoOpen ? '点击收起' : '点击展开'">
              <span class="text-[10px] text-muted">{{ todoOpen ? '▼' : '▶' }}</span>教练卡
              <span v-if="todoCount" class="ml-1 px-1.5 rounded-full bg-red-500 text-white text-[10px]"
                    :title="`${todoCount} 条未决策`">{{ todoCount }}</span>
            </button>
            <router-link target="_blank" to="/coach" class="text-xs text-accent hover:underline">教练</router-link>
          </div>
          <div v-if="todoOpen" class="flex-1 min-h-0 overflow-y-auto pr-1 space-y-2">
            <div v-if="coachErr" class="text-muted text-xs">—（加载失败）</div>
            <div v-else-if="!todayCoach.length" class="text-muted text-xs">
              {{ isReplay ? '当日无教练卡' : '今天暂无教练卡 ✓' }}
            </div>
            <TodoCard v-for="a in todayCoach" :key="a.id" :alert="a" :readonly="isReplay" @done="loadCoach" />
          </div>
          <div v-else class="text-[10px] text-muted">
            已收起（{{ todayCoach.length }} 条<template v-if="todoCount">，其中未决策 {{ todoCount }}</template>）—— 点击标题展开
          </div>
        </div>

        <!-- 持仓摘要
             ★ 2026-09-28（用户："持仓摘要右栏卡片先隐藏吧"）：**暂时隐藏**。
             ⚠️ 卡体**原样保留**、只加 `v-if="false"` —— 要恢复**删掉这个 v-if 即可**
               （用户说的是"**先**隐藏"，预期会回来；且它的数据与主区「持仓状态」同源，留着便于对照）。 -->
        <div v-if="false" class="bg-card border border-border rounded-lg p-4">
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

        <!-- ★ 2026-09-28（用户："评分榜 Top10 加多一列主力行为，挂在持仓摘要的下方"；
             观察池"也挂右侧，在评分榜下方，但是点击展开，默认收起"）：
             「评分榜 Top10（含**主力行为**列）+ 观察池」挂到**常驻右栏**（全阶段可见、不被时间轴切走）。
             ⚠️ 数据仍由父级加载（顶部「就绪状态条」复用同一批 `topItems` / `gwItems`）。 -->
        <StockListsCards :top-items="topItems" :top-err="topErr" :top-mode="topMode"
                         :top-countdown="refreshLeft"
                         :def-items="defTopItems" :def-meta="defMeta" :def-err="defErr"
                         :def-is-live="defIsLive" :def-live-loading="defLiveLoading"
                         @toggle-live="toggleDefLive"
                         :gw-items="gwItems" :gw-meta="gwMeta" :gw-err="gwErr"
                         :gw-prices="gwPrices" />

        <!-- ★ 2026-10-24（用户："今日系统时间线移到最下面"）：由右栏顶部移到最下。
             ⚠️ 它内含「系统自洽性审查（LLM）」—— 同源同卡，随本卡一起下移。
             ⚠️ 全站只此一份（主区盘后那张同源卡 2026-09-26 已删）。 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <button type="button" class="text-sm font-semibold flex items-center gap-1 hover:text-accent"
                  @click="pushOpen = !pushOpen"
                  :title="pushOpen ? '点击收起' : '点击展开'">
            <span class="text-[10px] text-muted">{{ pushOpen ? '▼' : '▶' }}</span>
            {{ isReplay ? selectedDate + ' 系统时间线' : '今日系统时间线' }}
          </button>
          <template v-if="pushOpen">
            <div v-if="pushConnErr" class="text-[10px] text-amber-400 mt-2">连接中断（保留上次数据）</div>
            <div v-if="!pushItems.length" class="text-muted text-xs mt-2">—</div>
            <div v-else class="max-h-80 overflow-y-auto pr-1 mt-2">
              <div v-for="(p, i) in pushItems" :key="i" class="text-xs border-b border-border/40 py-1.5">
                <div class="flex items-center gap-2">
                  <span class="font-mono text-muted">{{ (p.ts || '').slice(11, 16) }}</span>
                  <span class="font-semibold truncate">{{ p.title }}</span>
                </div>
                <div class="text-muted mt-0.5">{{ firstLine(p.content) }}</div>
              </div>
            </div>
          </template>
          <div v-else class="text-[10px] text-muted mt-1">
            已收起（{{ pushItems.length }} 条）—— 点击标题展开
          </div>

          <!-- ★★ 2026-09-30（用户："LLM 自洽性审查接进右栏时间线卡"）：
               与上方时间线**同源**（都基于今日系统提示集合）⇒ 放进**同一张卡**，不新开
               "今日雷达"卡（9/28 刚因"同源两张卡"删过一份，注释原文：留两份只会同屏重复）。
               ⚠️ **默认折叠 ⇒ 不请求**；展开时 `toggleRadar` 先走 `refresh=false` ——
                  当日已分析过 ⇒ 直接读后端缓存（免费）；**当日未分析过 ⇒ 仍会真调一次 LLM**。
               ⚠️ 位置：**必须排在上面 `v-if/v-else` 对之后** —— 插到中间会破坏
                  `<template v-if="pushOpen">` 与"已收起"`v-else` 的邻接关系。 -->
          <div class="mt-2 pt-2 border-t border-border/40">
            <button type="button" class="text-xs font-semibold flex items-center gap-1 hover:text-accent"
                    @click="toggleRadar"
                    :title="radarOpen ? '点击收起' : '点击展开。⚠️ 当日已分析过 ⇒ 直接读缓存（免费）；当日尚未分析 ⇒ 会调用一次 LLM（约十几秒）'">
              <span class="text-[10px] text-muted">{{ radarOpen ? '▼' : '▶' }}</span>
              系统自洽性审查（LLM）
            </button>
            <template v-if="radarOpen">
              <div class="flex items-center gap-2 mt-1.5 flex-wrap">
                <span v-if="radarAnalysis.cached" class="text-[10px] text-muted">当日已分析 · 读缓存</span>
                <span v-if="radarAnalysis.items_used" class="text-[10px] text-muted">
                  基于 {{ radarAnalysis.items_used }} 条系统提示</span>
                <button class="px-2 py-0.5 rounded text-[11px] border border-border transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                        :class="radarAnalysis.loading ? 'text-muted' : 'text-accent hover:border-accent/50'"
                        :disabled="radarAnalysis.loading"
                        title="强制重算。⚠️ 消耗 1 次 LLM 调用（同步，约需十几秒~1 分钟）。同日正常只需读缓存。"
                        @click="loadRadarAnalysis(true)">
                  {{ radarAnalysis.loading ? '分析中…' : (radarAnalysis.markdown ? '重新分析' : '分析矛盾点') }}
                </button>
              </div>
              <div v-if="radarAnalysis.loading" class="mt-1.5 text-xs text-muted">分析中…（LLM 同步调用，约需十几秒）</div>
              <!-- ★ 2026-10-11（L2/L3）：改走「观点卡」—— 后端已把 conclusion/reasons **分开下发**
                   （L3 结构化），前端按角色渲染：结论条置顶、「依据」默认收起。 -->
              <InsightBody v-else-if="radarAnalysis.markdown || radarAnalysis.conclusion"
                           :text="radarAnalysis.markdown"
                           :conclusion="radarAnalysis.conclusion" :reasons="radarAnalysis.reasons"
                           feedback-source="radar"
                           :feedback-ref="'radar:' + (selectedDate || todayStr)"
                           collapsible class="mt-1.5 text-xs max-h-72 overflow-y-auto pr-1" />
              <div v-else-if="radarAnalysis.error" class="mt-1.5 text-xs text-amber-400">{{ radarAnalysis.error }}</div>
              <div v-else class="mt-1 text-[11px] text-muted leading-relaxed">
                检查**系统今天自己发出的多条提示**是否互相打架（如"盘中警示说回避" vs "午间雷达说机会"、
                早盘判断被午后自己推翻）。<span class="text-muted/70">与「矛盾扫描」分工不同：后者扫的是**市场数据**层面的矛盾。</span>
              </div>
            </template>
          </div>
        </div>

      </aside>
    </div>

    <!-- 底部折叠的系统状态已**上移到顶栏日期下方**（2026-09-25 用户要求，改为浮层）
         ⇒ 此处不再保留 details，避免同一份内容占两处。 -->
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
import { ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
// ★ 2026-10-11（L1）：markdown 渲染器收拢为**全站唯一实例**（`composables/markdown`）——
//   原先 5 个页面各自 `new MarkdownIt` 且选项已漂移（本文件是唯一的 `linkify: false`）。
import { renderMd } from '../composables/markdown'
// ★ 2026-10-11（L2）：卡片内嵌的 LLM 观点用「观点卡」（结论置顶 + 依据可折叠）
import InsightBody from '../components/InsightBody.vue'
import TodoCard from '../components/workbench/TodoCard.vue'
// ★ 2026-09-25（用户："观察池…跟现项目观察池的颜色逻辑一致…战法名称用中文"）：
//   与榜单页**共用同一份**展示元数据（主力阶段配色 / 战法中文名 / 闸门就绪配色）。
//   ⚠️ 本文件已有一个同名 `PHASE_STYLE` —— 那是**时段**配色（盘前/竞价/盘中…），
//      语义完全不同 ⇒ 主力阶段配色必须**重命名导入**（`MF_PHASE_STYLE`），避免撞名。
import {
  PHASE_STYLE as MF_PHASE_STYLE, strategyShort,
  stockHref, xqUrl, xqIndexUrl, hhmm, pctClass, signNum, scoreClass,
  dirColor, levelColor,
} from '../composables/displayMeta'
// ★ 2026-09-28：「评分榜 Top10 / 观察池」两卡抽成子组件（盘后 + 盘中/午盘复用同一份）
import StockListsCards from '../components/workbench/StockListsCards.vue'
// ★ 2026-09-28（方案 A，用户："把宏观方向 · 偏空的分数也移到顶部右上角，放在情绪的左边"）：
//   「宏观与环境」卡抽成子组件 —— 盘前内联 + 顶栏「宏观」浮层**共用同一份**
//   （防"同一份数据两处渲染必然漂移"；同理 `dirColor`/`levelColor` 也上移到 displayMeta）。
import MacroEnvCard from '../components/workbench/MacroEnvCard.vue'
// ★ 2026-10-24：右栏「快讯解读」卡（大盘/板块/个股 + 重要快讯 + AI 解读）
import FlashBriefCard from '../components/workbench/FlashBriefCard.vue'
// ★ 2026-10-26（第①步：动态盘面 × 消息面融合）：快讯解读的**共享数据源**（单例，60s）
//   —— 快讯卡与本页「持仓状态」的持仓行消费同一份快照 ⇒ 一条轮询、两处投影、零重复请求。
import { useFlashBrief } from '../composables/useFlashBrief'
// ★ 2026-10-10（P0-2，见 PLAN_RESERVE_SIGNALS.md §6.2）：央行储备月度面板卡
//   （月频慢变量、自取数据、非交易信号）。只挂**一处**，避免同一数据两处渲染漂移。
import ReserveCard from '../components/workbench/ReserveCard.vue'
// ★ 2026-09-28：评分榜改「本地优先」—— 复用榜单页（`ScoreRank.vue`）的**同一套前端评分引擎**。
//   用户："评分榜之前不就是实时的吗?我都是使用本地计算的" ⇒ 工作台原先恒走后端
//   `/score/batch/top`（盘中通常是昨日快照），与榜单页的**本地实时**结果口径不一致
//   （榜单页注释原话："避免前后端榜单不一致被误认为 bug"）。
import { useFrontendScoring, computeRanking, scoreCodeList } from '../composables/useFrontendScoring'
const {
  dbReady: feDbReady,
  useFrontendMode,
  scoringResult: feScoringResult,
  lastScoreTime: feLastScoreTime,
  initFrontendScoring,
} = useFrontendScoring()
// ★ 2026-09-27 数据可信度标注：「该不该买」（闸门，19.5 年核算）与「买什么」
//   （评分未验证 / 战法已证伪）此前在界面上**一样权威**，用户无从分辨。
//   本处把验证状态直接标在对应区块上（hover 显示摘要与证据脚本）。
import { useVerification, STATUS_STYLE, verifyTip } from '../composables/useVerification'
import {
  getWorkbenchDayIndex, getWorkbenchDay,
  getTraderBrief, getCoachAlerts, getCoachConsistency, getCoachPlanExecutionRate,
  getPortfolioRadar, getGateWatch, getPushLog, getScoreTop,
  // ★ 2026-10-25：H2' 阴跌防御影子榜（工作台「防御 Top10」标签页的数据源，日批快照）
  getShadowDefensive,
  // ★ 2026-10-25（用户选"前端本地引擎算组内 100 只"）：宽防御组名单（小表 ≈15KB/日）
  getDefensiveGroup,
 getPolicyBottom, confirmPolicyBottom,
  // ★ 2026-10-19：清单第①条的**自动提示**（巨潮公告；只提示不打勾）
  getPolicyAnnouncements, refreshPolicyAnnouncements,
  // ★ 2026-10-19：第①条的**份额异动证据**（上交所；连续 2 日 ≥8% ⇒ 自动打勾）
  refreshPolicyEtfShares,
  // ★ 2026-09-30（用户："观察池需要展示个股的涨跌幅"）：**复用项目既有批量报价接口**
  //   （榜单页观察池 2026-09-22 起就在用 `getBatchPrices` 做同一件事）。
  // ★ 2026-10-26（P1 修复 + G2 合并）：
  //   · getBatchPricesCached —— 观察池报价改读后端内存缓存（原来走 getBatchPrices 每 120s 直连腾讯）
  //   · getIntradayLite      —— 涨停/炸板 + 成交额环比合成一次请求（原来两条 60s 轮询）
  getBatchPrices, getBatchPricesCached, getIntradayLite, getMarketPulse,
  // ★ 2026-09-30：**系统自洽性审查（LLM）** —— 与 `getPushLog` **同源**（都基于今日提示集合），
  //   接口早就在（`ScoreRank.vue` 一直在用），本轮接进工作台右栏**同一张**时间线卡内。
  getRadarAnalysis,
  getMarketOverview, getMarketTemperature, getMarketRegime, getMarketEvents,
  getDailyReport, getSystemStatus, getSystemMemory, getDbUsage,
  getMacroDaily, getMacroSnapshot, getFlashDiagnosis, getCalendar,
  getWorkbenchDecisionCard,
  // ★ 2026-10-22：盘中变化检测器（8 档规则采样，零 LLM）
  getWorkbenchScan, getWorkbenchScanSummary,
  // ★ 2026-10-23：观察器——拥挤赛道 / 指数关键位 / AI 硬件篮子 / 推演证伪
  getCrowdingWatch, getKeyLevelWatch, getAiBasketWatch, getScenarioWatch, getJin10Tidy,
  getStagflation,
  // ★ 2026-10-24：观察器 AI 解读（LLM）
  getObserverReview,
  getMarketEmotion, getMarketLimitReview, getRealtimeUplimit, getCalendarAhead,
  getAmountRealtime,
  getUserPositionSizing,
  getEmotionReview,
  getMarketTailReview,
  // ★ 2026-09-25 修复：**这两个原本就没 import** —— `loadSectorTop` 里一直在用
  //   `getSectorSnapshot` 却从未导入 ⇒ `vite build` 不做未定义变量检查（ESM 下被当成全局
  //   变量，不报错），而运行时抛 `ReferenceError` 被 `try/catch` 静默吞掉
  //   ⇒ **板块卡一直显示"（当日无板块快照）"**，从构建与日志里都看不出来。
  //   A1 收尾时改用实时 `/sector/industry`（含领涨股）才发现 ⇒ 两个都补上。
  getSectorIndustry, getSectorSnapshot,
  getSectorAmountShare,
  // ★ 2026-09-30（P2 续）：盘后/复盘的板块 Top5 改走 **zzshare 当日快照**（完整 104 板块，
  //   与「板块分化」页同源）—— 东财盘后常被风控、其快照行数残缺且与个股真值矛盾。
  getZzSectorSnapshot,
  // ★ 2026-09-25（P1「明日准备」）：待触发交易计划 —— 接口**早已存在**
  //   （`/user/plans`，含 status / plan_type / trigger_high_open / trigger_volume_break），
  //   此前工作台从未用过 ⇒ 复盘的结论没有被转成"明日待办"，一天的首尾没接上。
  getUserPlans,
  // ★ 2026-10-25：李大霄视角卡（观察器；xiaopage 事件推送，只读）
  getLidaxiaoView,
  // ★ 2026-10-28：手动补拉（推送失败兜底，进卡面「↻ 更新」按钮）
  refreshLidaxiao,
  // ★ 2026-10-25：流动性三件套（两融/基差/中美利差 日批落库序列；观察器，只读）
  getMacroLiquidity,
} from '../api'

// （渲染器 `mdRenderer` 已于 2026-10-11 收拢到 `composables/markdown`）

// ★ 2026-09-25 用户要求：工作台所有跳转一律新开标签页（原页轮询/状态不丢）；
//   个股"代码"→雪球（自动拼 SH/SZ/BJ 前缀），股票"名称"→本地个股详情页。
//   本项目为 hash 路由 ⇒ 站内链接直接用 '#/path' + target="_blank" 即可。
// ★ 2026-09-28：`xqUrl` / `stockHref` 已上移到 `composables/displayMeta`（唯一共享源），
//   供本页与 `StockListsCards.vue` 共用 —— 见文件顶部 import，此处不再本地定义。

// ══ ★★ 2026-09-26（用户："9:15 前涨跌幅不都是昨天的吗？"）══
// 【为什么需要】"昨日/今日"是**相对表述** —— 休市日 / 盘前时，页面的"今日"其实是
//   **数据口径日**（用户实测：文案说"今日平均表现 -1.16%"，实际是
//   "前一日(09-23)涨停 → 最近交易日(09-24)的表现"）⇒ 按字面理解会错一天。
// ⇒ 改为渲染**绝对日期**：`price_date`（价格所属日 = 后端 `_price_date()`）与
//   `prev_limit_date`（涨停名单所属日 = 它的前一交易日）。任何时段都无歧义。
const mmdd = (d) => (d ? String(d).slice(5) : '—')

// ★★ 2026-09-26（用户："根据日期对比，采用昨日/今日比较语义化的方式"）：
//   把上一步的**绝对日期**升级为**条件化语义** —— 相对词只在语义成立时才用，
//   否则退回绝对日期 MM-DD（宁可啰嗦，也不要一句错的"今日"）。
//     规则：① 「今日」= `price_date` 恰是自然日今天；② 「昨日」= 名单日与价格日
//           是**紧邻自然日**（跨周末/长假**不成立** —— 周一的上一交易日是上周五，说"昨日"就错）
//     实现：② 用**自然日差 1 天**判断即可（前端无交易日历），且天然排除跨周末/长假 ✓
//     ⚠️ 关键前提：①不成立时（休市日/数据滞后）**一律**显示绝对日期 ——
//        否则"昨/今"会相对**数据口径日**而非真实今天，又回到本次修掉的那个坑。
const isTodayPrice = () => String(emotion.value?.price_date || '') === todayStr
const priceDayLabel = () => (isTodayPrice() ? '今日' : mmdd(emotion.value?.price_date))
const prevLimitLabel = () => {
  const a = emotion.value?.prev_limit_date
  if (!isTodayPrice()) return mmdd(a)          // 数据不是今天 ⇒ 相对词一律不用
  const b = emotion.value?.price_date
  const ta = Date.parse(String(a || '').slice(0, 10))
  const tb = Date.parse(String(b || '').slice(0, 10))
  const adjacent = Number.isFinite(ta) && Number.isFinite(tb)
    && Math.round((tb - ta) / 86400000) === 1
  return adjacent ? '昨日' : mmdd(a)
}

// ══ ★★ 2026-09-26（用户："竞价看板的高开榜和低开榜双击股票名跳到雪球"）══
// 【为什么需要这套东西】同一个元素要承载两个动作：
//     单击（整行）→ 本地详情页 ｜ **双击（股票名）→ 雪球**。
//   ⚠️⚠️ 而浏览器在触发 `dblclick` 之前**必定先发两次 `click`** ⇒ 不处理的话，
//     双击会在跳雪球之前先弹出**两个**本地详情页（体验就是"双击开一堆页"）。
//   ⇒ 做法：单击**延迟** `DBL_MS` 再执行；第二次 click（无论后面有没有 dblclick）到达就**取消**它。
//     · 双击名字：click1 建定时器 → click2 取消 → dblclick 跳雪球 ⇒ **只跳雪球** ✓
//     · 双击行其它位置：click1 → click2 取消 ⇒ **什么都不发生** ✓（正好符合"只名字可双击"）
//     · 单击行/名字：定时器到期 ⇒ 跳本地详情页 ✓
//   ⚠️ 代价（诚实说明）：单击跳转要等 ~260ms —— 这是"双击"这个交互本身的成本，不是实现缺陷。
const DBL_MS = 260
let _auctionClickTimer = null
function auctionRowClick(code) {
  if (_auctionClickTimer) {          // 第二次 click ⇒ 属于双击序列 ⇒ 撤销单击动作
    clearTimeout(_auctionClickTimer)
    _auctionClickTimer = null
    return
  }
  _auctionClickTimer = setTimeout(() => {
    _auctionClickTimer = null
    // ⚠️ `stockHref` 是 hash 路径（`#/stock/xxx`）⇒ `window.open` 前必须**补成绝对 URL**，
    //   否则新标签会开到"当前路径 + hash"这种古怪地址。
    window.open(new URL(stockHref(code), location.href).href, '_blank')
  }, DBL_MS)
}
function auctionNameDblClick(code) {
  if (_auctionClickTimer) {          // 兜底：确保单击动作不会在双击之后补跳一次
    clearTimeout(_auctionClickTimer)
    _auctionClickTimer = null
  }
  window.open(xqUrl(code), '_blank')
}
// ★ 2026-09-25（P1「明日准备」）：交易计划类型中文
//   （口径同 `backend/schema.sql` 的 `user_trade_plans.plan_type` 注释：
//    trial 试仓 / add 加仓 / target 兑现 / stop 认错 —— 决定文案与仓位提示）
const PLAN_TYPE_CN = { trial: '试仓', add: '加仓', target: '兑现', stop: '认错' }

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
// ★★ 2026-09-30（#3 轻量切换）：涨跌停两格的**口径披露**。
//   后端已改为「**rt_k 精确优先**（`close == high_limit`，全市场含北交所/ST），
//   失败回退内存池按板幅近似」—— 两个口径含义差很大（近似**不含北交所**、`EXCLUDE_ST`
//   把 **ST 股排除**、且判据有 0.3pt 容差；同日实测跌停曾差近一倍 123 vs 65）
//   ⇒ 必须把**当前用的哪个口径**告诉用户，否则用户会把两个数当成同一个。
function limitTip() {
  const s = overview.value?.stats || {}
  const src = s.limit_src === 'rt_k'
    ? 'rt_k 精确口径：close == 涨停价（数据源给出），全市场覆盖（含北交所/ST）'
    : '内存池近似：按板幅判定（⚠️ 不含北交所，ST 股被排除，0.3pt 容差）'
  const extra = []
  if (s.limit_data_date) extra.push(`数据日 ${s.limit_data_date}`)
  if (s.limit_age_sec != null) extra.push(`快照龄 ${s.limit_age_sec}s`)
  if (s.limit_src && s.limit_src !== 'rt_k') extra.push('⚠️ 已回退到近似（rt_k 缓存不可用）')
  return `口径：${src}${extra.length ? ' · ' + extra.join(' · ') : ''}`
}
// ★ 2026-10-28：`limitBoards`（fuyao `page_boards`）**已移除** —— 与「涨停梯队与清单」卡的
//   连板梯队（zzshare `ban_info`）重复且数字不同（首板 61 vs 65、3板 2 vs 4）。
//   市场级连板语义**只保留 zzshare 一份**；本卡只在个股级标 `leader_boards_text`。
const temperature = ref(null)
const regimeLabel = ref('—')
// ★ 2026-09-27 事件驱动信号（E2 政策脉冲）—— **v0 展示项，不进决策链**。
//   稀有事件（21 年 298 天触发），触发时顶栏显一项；未触发则不占位。
const eventSignal = ref(null)
const briefMd = ref('')
const briefErr = ref('')
const briefDegraded = ref(false)
// ★★ 2026-09-30（用户："盘前午盘盘后的 llm 分析失败，可以加一个手动重试"）：
//   `briefMd` 是**多阶段共用**的单个 ref ⇒ 必须记「当前内容属于哪个 phase」，
//   否则会把盘前简报渲染进「决策简报（盘后）」卡（盘后段原先**根本不加载**简报，
//   只有切到「复盘」段才拉 ⇒ 该卡要么空着、要么显示盘前内容，属既有缺陷）。
const briefPhase = ref('')
const briefLoading = ref(false)
const coachList = ref([])
const coachErr = ref('')
const radarItems = ref([])
const radarSummary = ref({})
const radarNote = ref('')
const radarAsOf = ref('')

// ★★ 2026-10-26（第①步：动态盘面 × 消息面融合）——
//   「消息面对持仓的影响」不再单列一块，而是**投影到持仓行**：
//     · 数据 = 快讯解读共享源（`useFlashBrief`，与快讯卡同一条 60s 轮询，零重复请求）
//     · 后端 `holdings_impact` 已是"消息 × 持仓行业/名称 × 行情 × 主力状态"的规则联动结果
//       （持仓不出网、本机确定性计算）
//     · 行内只放「方向 + 条数」徽标，点开才展开命中原文（避免 60s 刷新时一片文字跳）
const fb = useFlashBrief()
const fbByCode = fb.byCode
// ★ 2026-10-26（第②步）：消息面也投影到**主线板块行** —— 板块为什么涨/跌，一行能看到原因。
//   匹配用后端给的行业名（industry_map × 快讯文本），前端只在名称不完全一致时做包含匹配。
const fbByIndustry = fb.byIndustry
const sectorMsgOpen = ref({})
function toggleSectorMsg(name) {
  if (!name) return
  sectorMsgOpen.value = { ...sectorMsgOpen.value, [name]: !sectorMsgOpen.value[name] }
}
function sectorMsg(name) {
  if (!name) return null
  const m = fbByIndustry.value || {}
  if (m[name]) return m[name]
  for (const k of Object.keys(m)) {
    if (k && (k.includes(name) || name.includes(k))) return m[k]
  }
  return null
}
// 方向以"多数票"着色：利好多于利空=红（与全站红涨一致），否则绿
function sectorMsgCls(m) {
  return (m && m.ben >= m.bear)
    ? 'bg-red-500/15 text-red-300 border-red-500/30'
    : 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
}
const holdingMsgOpen = ref({})
function toggleHoldingMsg(code) {
  holdingMsgOpen.value = { ...holdingMsgOpen.value, [code]: !holdingMsgOpen.value[code] }
}
// 语义遵循全站「红=利好/上涨、绿=承压/下跌」（panel 里不是涨跌，但方向色保持一致）
function msgCls(lean) {
  const s = String(lean || '')
  if (s.startsWith('受益')) return 'bg-red-500/15 text-red-300 border-red-500/30'
  if (s.startsWith('承压')) return 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30'
  return 'bg-white/5 text-muted border-border'
}
function hitCls(d) {
  const s = String(d || '')
  if (s === '多' || s === '利好' || s === '受益') return 'text-rise'
  if (s === '空' || s === '利空' || s === '承压') return 'text-fall'
  return 'text-muted'
}
// ★ 2026-09-29：行情**数据自身时刻**（后端 `quote_as_of` / `quote_from_snapshot`，来自
//   `tencent._cache["data_ts"]`）—— 与 `radarAsOf`（= 本次计算时刻）**不是一回事**。
const radarQuoteAsOf = ref('')
const radarQuoteSnap = ref(false)
// ★ 2026-10-18（用户报"行情 10-16 03:10 · 快照"）：快照态下 `quote_as_of` 是**写入时刻**，
//   内容可能是上一交易日收盘（10-16 03:10 写的快照装的是 10-15 的数据）⇒ 必须另存
//   「数据所属交易日」并优先展示它，否则用户会把"写入时刻"读成"行情是哪天的"。
const radarQuoteDataDate = ref('')
// ★ 2026-09-29：**组合层**风控三件（风格暴露 / 板块集中度 / 海外敏感链）——
//   逐只 alerts 之外的整体视角，数据零新增请求（后端 `portfolio_risk`）。
const radarRisk = ref(null)
const radarErr = ref('')
const topItems = ref([])
const topErr = ref('')
// ★ 2026-10-25（用户："评分榜区域新增防御 Top10 标签页，与 Top10 自由切换"）：
//   H2' 阴跌防御影子榜（`/score/batch/shadow-defensive`，日批落库）—— 与 `topItems`
//   **分开加载/分开 err**：影子榜挂了不能拖累生产榜（反过来也一样）。
//   ⚠️ 它是**日频**数据，只在切阶段/进页面加载，不挂 120s 轮询（盘中重拉没有意义）。
const defTopItems = ref([])
const defMeta = ref({})
const defErr = ref('')
// ★★ 2026-10-25（用户："想让它在盘中变动"）：工作台这张卡也支持「实时」预览 ——
//   官方日批（`variant='defensive'` 快照）↔ 实时（当前精算榜代入同一冻结公式重排）。
//   `defSnap` 存官方那份，点「日批」直接还原（零请求）；实时首次约 1 分钟（后端 3 分钟缓存）。
const defSnap = ref({ items: [], meta: {} })
const defIsLive = ref(false)
const defLiveLoading = ref(false)
// ★★ 2026-10-25（用户选"前端本地引擎算组内 100 只"）：宽防御组**名单**（日批小表）。
//   本地实时 = 名单（≈15KB/日）+ 浏览器内精算 ⇒ 后端零精算 egress、也不读 kline_cache。
const defGroup = ref({ ok: false, date: '', items: [], counts: {}, gate: false, eq_state: '' })
// ★ 2026-09-28：评分榜口径标记（'local'=本地实时 / 'backend'=后端批次）—— 卡片标题旁如实标注
const topMode = ref('')
// ★ 2026-09-28（用户："评分榜 Top10 在盘中会有显示倒计时的吧？这样用户才知道他在实时刷新"）：
//   距下次刷新的**秒数**。仅**交易时段**走字（非交易时段数据不变，显示倒计时是误导），
//   0 = 前端隐藏。与 120s 轮询**同起点注册**（都在 `startPolling()` 里）⇒ 读数相差 ≤1 tick。
const POLL_SEC = 120
// ★★ 2026-10-26（第④步）：**行情快组 60s**（pulse），慢组（板块/外盘/评分榜…）仍 120s。
//   为什么这么分：pulse 读后端内存缓存（零上游），提频只花浏览器往返；
//   而 `sectorTop`（东财 60s TTL）与外盘若同步提频，会**翻倍外部请求**（红线）。
const FAST_SEC = 60
const refreshLeft = ref(0)
const gwItems = ref([])
const gwErr = ref('')
// ★ 2026-09-25（P1 观察池实化）：原实现只取 `items`，**把总览字段全丢了**
//   （regime / total / ready3 / counts / data_date / strategy_hits 后端都返回了）
//   ⇒ 卡里只能写"候选 N 只"。现在一并存下来，卡上就能回答"池子多大、几个快出手了、哪天的数据"。
const gwMeta = ref({})
// ★ 2026-09-30（用户："工作台的观察池需要展示个股的涨跌幅，等状态文字可去掉"）：
//   观察池的 `ready/phase` 是**日频**（`mainforce_state` 日更），而涨跌幅要**实时** ⇒
//   照搬榜单页（`ScoreRank.vue` 2026-09-22 的 `watchPriceMap` + `getBatchPrices`），
//   理由同那边注释：① 闸门是**全池重算**（>30s，会撞前端超时）⇒ 不能拿它做轮询；
//   ② 涨跌幅是高频变量 ⇒ 60s 只刷**价格**、闸门数据不重算。
const gwPrices = ref({})      // {code: {price, change_pct}}
// ★ 2026-09-25（P1「明日准备」）：待触发交易计划（status=waiting）
const plans = ref([])
const plansErr = ref('')
// ★ 2026-09-25（盘后补强 · 缺口2）「今日执行」：今天推了几条教练卡、执行/放弃/未响应各几。
//   ⚠️ 走 `/coach/consistency?day=today`（**按日精确**），**不是**复盘那张"近 N 天"的窗口统计 ——
//     后端 `/coach/consistency` 本次新增 `day` 参数（`days=1` 是"近 1 天"，含昨天，不能顶替"今天"）。
const todayExec = ref(null)
const todayExecErr = ref('')
const pushItems = ref([])
const pushConnErr = ref(false)
// ★ 2026-09-28（用户："今日系统时间线放在顶部展开收起，默认收起"）—— 折叠态，同观察池。
const pushOpen = ref(false)
const consistency = ref(null)
const reportMd = ref('')
const statusHtml = ref('点击展开后加载…')
const statusOpen = ref(false)
// ★ 2026-09-25 用户反馈：盘前加"宏观与环境"卡（数据同数据中心：早盘锁定快照优先）
const macro = ref(null)
const macroErr = ref('')
// ★ 2026-10-30（简报第1项）：组合风险档（滞胀交易）。**只标注，不改 macro.direction.score**
const stagflation = ref(null)
// ★ 盘中看盘序/情绪（2026-09-25 交易方法论 A1/A3/B2）
const emotion = ref(null)
// 初始即给完整结构：模板首帧（接口返回前）会读取 steps/stocks，null 会崩
const limitReview = ref({ steps: [], stocks: [] })
// ★★ 2026-10-28（用户："日批炸板率样本标注"）：日批炸板率的分母来自 `backtest_prices`，
//   它只覆盖回填过的股票（实测当日 1032 只）⇒ 与**同卡上方**的实时全市场口径不可比：
//     实时（zzshare rt_k 全市场）炸板 50.0% ｜ 日批（1032 只样本）炸板率 42.1%
//   差 8 个点几乎全部来自分母，不是"两个数打架"。⇒ 覆盖率 <50% 时降级为「参考值」并明示。
//   覆盖率口径 = 样本内**封住的涨停股数** / 官方涨停家数（`ladder.total`），
//   因为炸板率关心的是"涨停股的代表性"，用涨停股覆盖率比用股票数更贴切。
const statsSampleLow = computed(() => {
  const c = limitReview.value?.stats?.coverage_pct
  return c != null && c < 50
})
// ★ 2026-09-29（P1）：盘中实时涨停/炸板（rt_k 全市场精确口径）—— 与 limitReview 的
//   「收盘定稿」口径互补：盘前/休市时后端返回 is_intraday=false（上一交易日收盘定稿）。
const realtimeUplimit = ref(null)
// ★★ 2026-09-29（P3）：**晋级率**（昨日的涨停股今天还活着吗）—— 后端 `promotion` 块，
//   与上面的今日统计**同一次 rt_k 抓取**（零新增请求），见 `app/promotion.py`。
//   ⚠️ 它是 `realtimeUplimit` 的**子字段** ⇒ 不另开 ref/请求，否则两份数据会各自刷新而漂移。
const promotion = computed(() => realtimeUplimit.value?.promotion || null)
// 承接强 = 红（与卡片既有「涨停 red / 跌停 emerald」同一套涨跌色）；仅用于配色，不改口径。
const promotionColor = computed(() => {
  const r = promotion.value?.promote_rate
  if (r == null) return 'text-muted'
  if (r >= 50) return 'text-red-400'
  if (r < 25) return 'text-emerald-400'
  return 'text-gray-300'
})
const promotionTip = computed(() => {
  const p = promotion.value
  if (!p?.available) return ''
  const gs = Object.entries(p.groups || {})
    .map(([k, v]) => `${k} ${v.promoted}/${v.n} = ${v.rate == null ? '—' : v.rate + '%'}`)
    .join('\n')
  const sm = (arr) => (arr || []).map(x => x.name || x.code).join('、') || '—'
  return [
    `昨日涨停名单 ${p.universe_date}（${p.universe_count} 只，对权威家数覆盖 ${p.coverage_pct}%）`,
    `分母 ${p.den} = 昨日涨停且有今日数据的只数（停牌/无数据 ${p.missing} 只单列，不进分母）`,
    `晋级 ${p.promoted} / 炸板 ${p.broken} / 未触板 ${p.no_touch}`,
    '晋级率 = 昨日涨停股今日封住 / 分母；昨涨停炸板率 = 昨日涨停股今日曾触板未封 / 分母',
    `按板幅：\n${gs || '—'}`,
    `晋级样本：${sm(p.samples?.promoted)}`,
    `炸板样本：${sm(p.samples?.broken)}`,
    p.note || '',
    '⚠️ 昨日名单来自盘中抓取的日批快照（实测 14:52，非收盘后），收盘前几分钟的炸板/回封会偏差',
    '⚠️ 盘中动态口径、炸板可能回封；收盘后权威家数以日批 ban_info 为准；不进决策链',
  ].filter(Boolean).join('\n')
})
// ★ 2026-09-29：两市成交额环比（U 型曲线法）。⚠️ `reliable=false`（14:00 前）时
//   只展示累计值 —— 早盘预估中位误差 16%（曲线留出验证），不得给缩量/放量结论。
const amountRt = ref(null)
// 成交额格式化：后端单位是**亿** ⇒ ≥1 万亿时换算显示（看盘序格子窄，字数越少越好）
const fmtYi = (yi) => {
  if (yi == null || yi === '') return '—'
  const n = Number(yi)
  if (!Number.isFinite(n)) return '—'
  return Math.abs(n) >= 10000 ? (n / 10000).toFixed(2) + '万亿' : Math.round(n) + '亿'
}
const sectorTop = ref([])
// ★ 2026-09-30（P2 续）：**板块 Top5 的数据来源要标注** —— 盘中走东财实时（含领涨股），
//   盘后/复盘走 zzshare 当日快照（**无领涨股**）。不标注会让用户以为"盘后也有领涨股"，
//   也分不清"这张榜是实时的还是收盘定稿的"。取值：'east' | 'zz' | 'snap' | ''
const sectorTopSrc = ref('')
// ★ 2026-09-25 需求 4：行业成交额占比（框架「板块主线层 · 成交占比」）——
//   回答"资金此刻真金白银集中在哪个板块"，与上面"涨幅榜"互补（涨得好≠成交额集中）。
//   ⚠️ 数据源独立于上面的东财接口 ⇒ 东财被封时上面可能空白、而这块照常显示。
const amountShare = ref(null)
// 占比条宽度：以**榜首为满格**（相对长度比绝对百分比更直观）
const barWShare = (pct) => {
  const rows = amountShare.value?.rows || []
  const max = Math.max(1, ...rows.map(r => Number(r.share_pct) || 0))
  return Math.round(((Number(pct) || 0) / max) * 100) + '%'
}
const calendarToday = ref([])
const calendarErr = ref('')
// ★ 2026-10-23：今日事件默认只显示 3 条（盘前卡够用即可），可展开
const calOpen = ref(false)
const calShown = computed(() =>
  calOpen.value ? calendarToday.value : calendarToday.value.slice(0, 3))
// ★ 2026-09-29（P1）：日历**前瞻**（未来 3 天核心事件）—— 与盘前企微推送**同口径**
//   （筛选全在后端 `flash/calendar_ahead`：国家白名单 + 核心指标词 + 5星通道 + 同指标合并）。
//   与下方 `calendarToday`（全量今日事件、含官员讲话）互补：它回答"**哪天是数据敏感期**"。
const calendarAhead = ref(null)
// ★ P1 决策卡（规则引擎确定性输出；空状态由后端 error 兜底）
const dc = ref(null)
const dcLoading = ref(false)
const dcErr = ref('')
// ★ 2026-09-25 用户："负面清单…跟…决策简报（盘前）重复" ——
//   `negatives` 里的「矛盾」类就是 `data["actions"]` 的 R1，而简报的「该做」段
//   （`actions_md`）已逐条列出 ⇒ 决策卡不再重复刷屏，只报数量 + 指路简报。
//   （个股级的两类——持仓主力出货 / 候选信号冲突——保留，它们是**可点击个股**，
//     在简报文本里没有这个交互。）
const negativeList = computed(() => (dc.value?.negatives || []).filter(n => n.scope !== '矛盾'))
const conflictCount = computed(() => (dc.value?.negatives || []).filter(n => n.scope === '矛盾').length)
// ★★ 2026-09-27（P2-a 落地）：春节「日历例外」展示辅助。
//   后端 `dc.calendar_exception` 已给出全部数据（阶段/标题/文案/标的/仓位/依据），
//   前端**只做「枚举 → 中文」映射**，无自由裁量 —— 与 `trade_gate.rules()` 下发参数的
//   思路一致，避免"前端写死导致口径漂移"（项目前车之鉴：本地 68.8 vs 后端 72.6）。
const SF_ACTION_CN = { watch: '预警', buy: '可介入', hold: '持有中', exit: '已到期', none: '—' }
const sfActionCn = (a) => SF_ACTION_CN[a] || (a || '—')
const SF_REGIME_CN = { offensive: '进攻', neutral: '震荡', neutral_bearish: '震荡偏空', defensive: '防御' }
const sfRegimeCn = (r) => SF_REGIME_CN[r] || (r || '—')
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
// ★★ 2026-09-29（P2）：股指期货基差（`/macro/snapshot.futures_basis`）—— 北向实时额停披后
//   现存少数的「内资情绪 / 对冲需求」温度计（IF/IH/IC/IM 当月+次月，含年化）。
//   ⚠️ 独立 ref 而非挂在 `macro` 上：`macro` 可能是 `macro_daily` 的**当日锁定快照**
//     （不含该字段）⇒ 挂上去会"时有时无"（与 `macroSentiment` 同款理由）。
//   纯展示层、不进决策链（口径与三条边界见后端 `macro.get_index_futures_basis`）。
const macroFuturesBasis = ref(null)
// ★ 2026-10-25（eypai 溯源调研）：流动性三件套（两融/中美利差/当月贴水率）的
//   **日批落库历史序列**（`/macro/liquidity`）—— 基差块的"须先积累历史基线"由此积累。
//   日频快照 ⇒ 进页面加载一次即可（不挂轮询）；口径见后端 `app/liquidity_watch.py`。
//   纯展示层、不进决策链。
const macroLiquidity = ref(null)
async function loadLiquidity() {
  try {
    const { data } = await getMacroLiquidity(30)
    if (data && data.ok) macroLiquidity.value = data
  } catch { /* 静默保留上次值 */ }
}
// ★ 2026-09-25：仓位建议的**定量上限**（`/api/user/position-sizing` 的 total_limit_pct）。
//   宏观卡原只显示 LLM 的定性词（如"轻仓观望"），补上引擎算出的具体上限数字。
const sizing = ref(null)
// ★ 2026-09-28：组合**周回撤熔断**（`sizing.portfolio_drawdown`）的 `pdd` / `pddTitle`
//   **已随「宏观与环境」卡搬进 `MacroEnvCard.vue`**（只被那张卡消费 ⇒ 内聚到组件里）。
// ★ 2026-09-25 P3：情绪对账（盘前预判 vs 当日实际）—— 用户："对错了要回溯修正，形成闭环，
//   否则情绪模型永远校准不了"。数据来自 `/api/market/emotion-review`（读同日两组字段）。
const emotionReview = ref(null)
// ★ 2026-09-25 需求 3（框架 C2）：尾盘承接（14:30 基线 → 现在/收盘）—— 决定是否持仓过夜。
//   数据来自 `/market/tail-review`（后端对比 14:30 落库的基线；无基线时整块不渲染）。
const tailReview = ref(null)
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
/** ★ 2026-09-28：是否"数据会变"的时段（竞价/盘中/午盘）—— 轮询倒计时与强制刷新共用判据。 */
function isLivePhase() {
  const p = computePhase()
  return p === 'auction' || p === 'intraday' || p === 'midday'
}
const livePhase = ref(computePhase())
// ★ 盘前是否已结束（9:30 后）：用于盘前视图的"回顾"标注
const premarketOver = computed(() => {
  const d = new Date(Date.now() + (new Date().getTimezoneOffset() + 480) * 60000)
  const m = d.getHours() * 60 + d.getMinutes()
  return m >= 570
})
// （`renderMd` 已于 2026-10-11 收拢：现为 `import { renderMd } from '../composables/markdown'`）
const firstLine = (s) => String(s || '').split('\n').find(x => x.trim()) || ''
const fmtNum = (v) => (v == null ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 }))
const fmtPct = (v) => (v == null ? '—' : `${Number(v) >= 0 ? '+' : ''}${Number(v).toFixed(2)}%`)
// ★ 2026-09-28：`signNum` / `pctClass` 已上移到 `composables/displayMeta`（共享源）。
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

// ══ ★★ 2026-09-25「竞价看板图表化」（用户：截图不够直观，问能不能用图表）══
// 【为什么不用 echarts】Workbench 是打开最频繁的页；echarts 虽在依赖里（别的页面在用），
//   但引进来会让本页首包 +约 350KB(gzip)。而这里要表达的三件事 —— **分布 / 断层 / 刻度** ——
//   用 CSS 条 + absolute 定位就能完整表达：零依赖、无需 resize/主题适配、与卡片风格天然一致。
// 【A1】全市场涨幅分布：条长对**最大档**归一化（看形状），右侧另标家数与占比（读数准确）。
const auctionHist = computed(() => {
  const a = emotion.value?.auction
  const rows = a?.histogram || []
  const total = a?.market_count || rows.reduce((s, r) => s + (Number(r.count) || 0), 0)
  if (!rows.length || !total) return []
  const max = Math.max(1, ...rows.map(r => Number(r.count) || 0))
  return rows.map(r => {
    const n = Number(r.count) || 0
    return {
      ...r,
      pctNum: Math.round((n / total) * 1000) / 10,     // 占全市场 %（1 位小数）
      w: Math.max(1, Math.round((n / max) * 100)),     // 条长（含 1% 保底 ⇒ 0 家也留一丝）
    }
  })
})

// 【A2】榜单 → 条形：**全部 20 只都画条**（用户 2026-09-25：“改为 top20 吧”）。
//   条长对**榜内最大幅度**归一化 —— 目的正是看"断层"（一字板那一簇 vs 后面的普通高开）。
//   ★ 为什么能直接 20 行而不炸高度：两榜已**左右并排**（各 ~350px 宽）⇒ 右列高度
//     ≈ 20 行 ≈ 左列高度 ⇒ 卡片总高几乎不变（并排的"预算冗余"正好被 20 行用掉）。
//   ★ 原先"10 行条形 + 11-20 紧凑标签"的双份展示已**删除**：同一批股票出现两次既重复、
//     又让"下面那堆没标题的标签"像另一组数据（用户正是因此要求合并为 20 行）。
const AUCTION_BAR_N = 20
const auctionBars = (list) => {
  const arr = (list || []).slice(0, AUCTION_BAR_N)
  const max = Math.max(1, ...arr.map(x => Math.abs(Number(x.gap_pct) || 0)))
  return arr.map(g => ({
    ...g,
    w: Math.max(3, Math.round((Math.abs(Number(g.gap_pct) || 0) / max) * 100)),
  }))
}

// 【A3】刻度尺：把「接力溢价」与「全市场平均」放到**同一根尺**上看背离。
//   ⚠️ 固定 ±3% 刻度（非自适应）才能跨日横比；超出范围时游标贴边，但**数值仍显示真实值**
//     （图可以clamp，读数绝不撒谎）。
const AUCTION_SCALE = 3
const auctionScale = computed(() => {
  const a = emotion.value?.auction
  const pos = (v) => (v == null || Number.isNaN(Number(v))
    ? null
    : Math.max(0, Math.min(100, ((Number(v) + AUCTION_SCALE) / (2 * AUCTION_SCALE)) * 100)))
  return {
    relay: a?.avg_gap ?? null, relayPos: pos(a?.avg_gap),
    all: a?.avg_gap_all ?? null, allPos: pos(a?.avg_gap_all),
  }
})
// ★ 2026-09-25 P3：情绪对账的结论配色 —— 一致=绿 / 偏保守=琥珀 / **偏乐观=红**
//   （"偏乐观"最危险：它意味着没看到退潮，而退潮是要降仓的）
const relCls = (r) => (r === '一致' ? 'text-emerald-400'
  : r === '偏保守' ? 'text-amber-300'
    : r === '偏乐观' ? 'text-red-400' : 'text-muted')
// ★ 2026-09-25 需求 3：尾盘承接的配色（与涨跌色一致，避免误读）：
//   走强=红（资金愿意持股过夜）｜走弱=绿（有资金尾盘撤退）｜平稳=灰
const tailCls = computed(() => (tailReview.value?.verdict === 'strong' ? 'text-red-400'
  : tailReview.value?.verdict === 'weak' ? 'text-emerald-400' : 'text-gray-300'))
// Δ用的是**整数家数/亿**，用 signNum（两位小数）会显示 "+6.00" ⇒ 单独一个整数格式
const tailDelta = (v) => (v == null ? '—' : (v > 0 ? '+' : '') + Math.round(Number(v)))
const tailTitle = computed(() => {
  const t = tailReview.value
  if (!t?.available) return ''
  const idx = (t.indices || []).map(i => `${i.name} ${signNum(i.d_pct)}%`).join('｜')
  return `14:30 → ${t.as_of ? String(t.as_of).slice(11, 16) : '现在'}（含收盘）\n${idx}\n${t.advice || ''}\n${t.note || ''}`
})
// ★ 2026-09-28：`scoreClass` 已上移到 `composables/displayMeta`（共享源）。

// ★ 2026-09-28（用户："顶部的指数点击能像雪球一样跳转到详情页吗？"）：
//   把雪球链接**预先算好**放进项里（模板不再重复调用/判断）。
//   ⚠️ 只做**雪球**，不做本地详情页 —— 本地详情页是**个股**页（`/api/score/{symbol}`），
//     指数没有评分；且它按 `_CODE_TO_PREFIX` 反查代码，指数不在股票池里 ⇒ 落到
//     "0 开头=sz" 的兜底 ⇒ `000001` 会被当成**平安银行**（错的标的）。
//   ⚠️ 前缀由后端 `indices[].prefix` 下发（唯一源 `routers/market.MAIN_INDICES`）；
//     老数据/降级无该字段 ⇒ `href=''` ⇒ 模板退回纯文本（宁可不给链接也不指错）。
const topIndices = computed(() => (overview.value.indices || []).slice(0, 3)
  .map(ix => ({ ...ix, href: xqIndexUrl(ix.prefix, ix.code) })))
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
    + `数据 ${(s.from_snapshot && s.data_date) ? `${s.data_date} 收盘` : (s.as_of || '—')}`
    + `${s.from_snapshot ? '（收盘快照，非实时）' : ''}\n${s.note || ''}`
})
const radarSummaryKeyed = computed(() => radarSummary.value || {})
// ★ 2026-09-29：卡片"时点"文案 —— 快照态显示 `MM-DD HH:MM · 快照`（标黄），实时态只显示 HH:MM。
const radarQuote = computed(() => {
  const ts = radarQuoteAsOf.value
  if (!ts) return null
  if (!radarQuoteSnap.value) return { text: ts.slice(11, 16), stale: false }
  // ★★ 2026-10-18：快照态**优先显示数据所属交易日**（"10-15 收盘（快照）"），
  //   只有后端没给 data_date（老版本/老快照）时才退回写入时刻 —— 这是最后兜底，
  //   因为它会把"10-16 03:10 写的昨日快照"读成"10-16 的行情"。
  const dd = radarQuoteDataDate.value
  return {
    text: dd ? `${dd.slice(5)} 收盘（快照）` : `${ts.slice(5, 16)} · 快照`,
    stale: true,
  }
})
// ⚠️ hover 文案放 computed 里而不是模板内联：模板里**跨行**的反引号字符串会被
//   @vue/compiler 的表达式解析器判成 "Unterminated template"（实测踩到，构建直接失败）。
const radarQuoteTitle = computed(() => {
  if (!radarQuote.value) return ''
  const dd = radarQuoteDataDate.value
  return [
    dd ? `数据所属交易日 = ${dd}（收盘）` : `行情写入时刻 = ${radarQuoteAsOf.value}`,
    radarQuoteSnap.value
      ? '⚠ 来自收盘快照（非实时）：这是"最近已完成交易日"的收盘价，不是今天的盘中价'
      : `抓取时刻 = ${radarQuoteAsOf.value}`,
    `本卡片计算时刻 = ${radarAsOf.value || '—'}（"什么时候算的"，不是"数据是什么时候的"）`,
    '现价 / 今日 / 盈亏 都取自这份行情；数据日 ≠ 最近交易日 = 行情源陈旧',
  ].join('\n')
})
// 组合待办：未决策教练卡（当天实时 / 回放日全部）
const todayCoach = computed(() => {
  if (isReplay.value) return replay.value.coach
  return coachList.value.filter(a => a.alert_date === todayStr)
})
const todoList = computed(() => todayCoach.value.filter(a => !a.executed))
const todoCount = computed(() => todoList.value.length)
// ★ 2026-09-28（用户："只留一份在右栏即可…加上收起展开功能，默认展开"）：
//   教练卡的**唯一**展示位 = 常驻右栏（盘中/午盘主区那张已删）⇒ 不需要"何时隐藏"的判断；
//   只留一个折叠态，**默认展开**（同观察池 / 系统时间线）。
const todoOpen = ref(true)
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
    // ★ 2026-09-25（用户需求「中概/美股现货」）：只挑**2 个代表**进顶栏（防过载）——
    //   标普500=美股大盘现货、中国金龙=中概正统口径。
    //   ⚠️ 道指/纳指**现货**不进顶栏：与已有的纳指**期货**重复度高，且隔夜块
    //   （按 |变化| 自动排序取前 2）明天起会把它们在"变化大时"自动冒出来 ——
    //   那才是隔夜信息的正确用法（常驻的是"最相关的少数"，异常的自动冒头）。
    { key: 'us_spx', label: '标普500' },
    { key: 'cn_hxc', label: '中国金龙' },
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
// ★★ 2026-09-29（P1）：**A50 隔夜 vs 竞价整体高开 = 期现背离**（内外资态度差）。
//   为什么这个指标现在特别有意义：北向实时额 2024-08 停披后，**这是现存唯一可观测的
//   「外资对 A 股的定价 vs 内资集合竞价结果」之差**——
//     · A50 期货走夜盘、连续交易 ⇒ 它涨 = **外资**愿为 A 股付更高价；
//     · 竞价整体高开均值 = 9:25 全市场集合竞价的**内资**结论。
//   ⇒ 两者背离（A50 高而竞价平）说明外资看多、内资不跟，反之亦然。
//   ⚠️⚠️ 纪律：**纯展示层，绝不进决策链**（与 `macro.py` 头注释、`_OVERNIGHT_KEYS`
//     同一条）：A50/纳指对 A 股次日的预测力在**可交易口径**下已实测塌陷至 |IC|≤0.04，
//     这里只做"态度差温度计"。±0.5pt 阈值为**经验初值、未回测**（与竞价看板其它阈值同款标注）。
const A50_DIVERGE_PT = 0.5
const a50Divergence = computed(() => {
  const a50 = (overnightItems.value || []).find(x => x.key === 'a50')
  const gap = emotion.value?.auction?.avg_gap_all
  if (!a50 || a50.pct == null || gap == null) return null
  const a50p = Number(a50.pct)
  const g = Number(gap)
  if (!Number.isFinite(a50p) || !Number.isFinite(g)) return null
  const spread = g - a50p                       // 负 = 内资比外资保守
  let label = ''
  if (spread <= -A50_DIVERGE_PT) label = '外资偏多 · 内资不跟'
  else if (spread >= A50_DIVERGE_PT) label = '内资偏多 · 外资不跟'
  const base = String(macroOvernight.value?.base_time || '').slice(5, 16).replace('T', ' ')
  return {
    a50: a50p, gap: g, spread, label,
    title: [
      `A50 期货 ${signNum(a50p)}%（自上次 A 股收盘${base ? ' ' + base : ''}以来；`
        + `夜盘连续交易 ⇒ 代表**外资**对 A 股的定价）`,
      `竞价整体高开 ${signNum(g)}%（9:25 全市场集合竞价结果 ⇒ 代表**内资**态度）`,
      `背离 ${signNum(spread)}pt（负 = 内资比外资更保守）`,
      '阈值 ±0.5pt 为经验初值、未回测；本项**只作态度差温度计**，不进决策链'
        + '（A50/纳指对次日的预测力在可交易口径已实测塌陷）',
    ].join('\n'),
  }
})
// ★ 2026-09-28：情绪温度计的「过热 / 过冷」子项切分（`hotSubs` / `coldSubs`）
//   **已随「宏观与环境」卡搬进 `MacroEnvCard.vue`**（阈值口径注释也在那边）。

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
// ★ 2026-09-25 用户："底部的系统状态移到顶部的日期的下面，把简要信息展示一行，
//   点击再悬浮展示内容" ⇒ 顶栏常驻一行摘要（摘要字段在此聚合），点开才渲染完整 HTML。
// ★ 2026-09-28：补 `memPct` —— 缩写后"内存"**只在高压时才亮**（见 `memWarn`），
//   而判断要看**百分比**（`rss_mb` 是绝对 MB，拿它比阈值必错）。
const statusBrief = ref({ summary: '', dbPct: null, memMb: null, memPct: null })
// ★ 2026-09-28（用户："缩写系统状态 · 13/13 新鲜 · 库 46.8% 截至 15:18 ▾ 的宽度"）：
//   内存**只在 ≥75% 时**才亮出来 —— 平时它不是关注点，而它最易波动
//   （看护告警线 80%，见后端 `memory_watch.ALERT_PCT`）。
const memWarn = computed(() => (statusBrief.value.memPct || 0) >= 75)
/** 缩写后的字段含义靠 hover 补全（`13/13`=数据源新鲜数；`库`=Supabase 库体积占用）。 */
const statusTitle = computed(() => {
  const b = statusBrief.value || {}
  const parts = []
  if (b.summary) parts.push(`数据源新鲜：${b.summary}`)
  if (b.dbPct != null) parts.push(`库体积占用 ${b.dbPct}%`)
  if (b.memMb != null) parts.push(`进程内存 ${b.memMb}MB（${b.memPct ?? '—'}%）`)
  if (statusTime.value) parts.push(`状态截至 ${statusTime.value}`)
  return (parts.length ? parts.join('\n') : '系统状态') + '\n（点击看完整明细）'
})
// ★ 2026-09-28（方案 A）：顶栏「宏观」浮层开关 + hover 说明（放情绪左边那个入口用）。
const macroOpen = ref(false)
// ★ 2026-10-23：顶栏「隔夜」明细浮层（用户："布伦特/道指…也做点击显示更多"）
const overnightOpen = ref(false)
const macroTip = computed(() => {
  const d = macro.value?.direction
  if (!d) return '宏观方向（未加载）—— 点击展开完整「宏观与环境」卡'
  const lock = macro.value?.locked
    ? `（今日锁定 · ${hhmm(macro.value.generated_at)}）` : '（实时口径）'
  return `宏观方向 · ${d.level || '—'} ${d.score ?? '—'}${lock}\n${d.advisory || ''}`
    + '\n（点击展开：快讯诊断 / 支撑因素 / 压制因素 / 过热项 / 过冷项）'
})

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
// ★ 2026-10-18：市场底确认清单（观察器）。失败静默保留上次值（铁律 11）。
const pbw = ref(null)
// ★ 2026-10-23：清单移到右栏后支持收起展开（默认展开，localStorage 记忆）
const PBW_KEY = 'wb_pbw_open'
const pbwOpen = ref((() => {
  try {
    const v = localStorage.getItem(PBW_KEY)
    return v == null ? true : v === '1'
  } catch { return true }
})())
function togglePbw() {
  pbwOpen.value = !pbwOpen.value
  try { localStorage.setItem(PBW_KEY, pbwOpen.value ? '1' : '0') } catch { /* ignore */ }
}
const PBW_LABELS = {
  nation_team_ann: '国家队公告',
  amount_dry_up: '成交连续地量',
  chinext_weekly: '创业板周线止跌',
  breadth_ratio_2d: '涨跌比连续转暖',
  us10y_or_oil: '美债/油价缓和',
  // ★ 2026-10-23：2018 年底剧本——市场底前最后一个信号是强势板块（银行/红利）补跌；
  //   工行在普跌日创新高 = 磨底未到最后段。银行单日收跌且跑输中证全指时记 1。
  bank_catchdown: '强势板块补跌',
}
const pbwItems = computed(() => {
  const items = (pbw.value && pbw.value.items) || {}
  return Object.keys(PBW_LABELS).map(k => {
    const o = items[k] || {}
    // ★ 2026-10-19：第①条分两层 ——
    //   `note` = 人工备注（结论）；`hint_text` = 自动扫描结果（事实，含"未发现"）。
    //   有备注就显示备注，否则显示自动结果，避免"未确认"三个字盖掉"已扫到 2 条公告"。
    const detail = k === 'nation_team_ann'
      ? (o.note || o.hint_text || '—')
      : (o.detail || o.note || '—')
    return {
      key: k,
      label: PBW_LABELS[k],
      checked: !!o.checked,
      insufficient: !!o.insufficient,
      detail,
      // 自动提示（只有第①条有；其余为 undefined）
      hints: o.hints || [],
      hintCount: o.hint_count || 0,
      hintStatus: o.hint_status || '',   // found / clear / unavailable / none
      hintText: o.hint_text || '',
      // ★ 份额异动（行为级证据；连续 2 日 ≥8% ⇒ 后端已自动 checked）
      etfRows: o.etf_rows || [],
      etfHits: o.etf_hits || [],
      etfStatus: o.etf_status || '',     // confirmed / watch / clear / none
      etfConfirmed: !!o.etf_confirmed,
      etfText: o.etf_text || '',
      // ★ 2026-10-23：回购/增持公告密度（承接盘强度，只展示）
      flowText: o.flow_text || '',
      autoChecked: !!o.auto_checked,
      manualDecided: !!o.manual_decided,
    }
  })
})
async function loadPolicyBottom() {
  try {
    const { data } = await getPolicyBottom()
    if (data && data.items) pbw.value = data
  } catch { /* 静默保留上次值 */ }
}
// ★ 2026-10-25：李大霄视角卡（xiaopage LLM 解读推送；**观察器，静默失败保留上次值**）。
//   数据由 xiaopage 在 LLM 输出解读的时刻 POST /market/lidaxiao/push 落库（事件驱动，
//   非"通知后回拉"——该时刻 GitHub raw 尚未 commit，回拉必拿旧数据），本卡只读。
const lidx = ref(null)
async function loadLidaxiao() {
  try {
    const { data } = await getLidaxiaoView()
    if (data && data.latest) lidx.value = data
  } catch { /* 静默保留上次值 */ }
}
// ★ 2026-10-28（用户："盘后也会有更新，加个手动更新按钮"）：手动补拉。
//   后端拉 xiaopage 的 videos.json（~6.4MB）→ 同一个 ingest 落库（幂等）。
//   ⚠️ 这是**慢请求**：Render 海外 1~3s，本地国内网络实测 59s ⇒
//     必须 busy 态 + 反馈文案，否则用户会重复点击（虽然有 ETag 短路）。
const lidxBusy = ref(false)
const lidxMsg = ref(null)
async function refreshLidx() {
  if (lidxBusy.value) return
  lidxBusy.value = true
  lidxMsg.value = null
  try {
    const { data } = await refreshLidaxiao()
    if (data && data.view && data.view.latest) lidx.value = data.view
    if (data && data.ok === false) {
      lidxMsg.value = { ok: false, text: '更新失败：' + (data.error || '未知原因') }
    } else if (data && data.unchanged) {
      lidxMsg.value = { ok: true, text: '上游无新解读（已是最新）' }
    } else {
      const d = String((data && data.latest && data.latest.date) || '').slice(5, 16)
      lidxMsg.value = {
        ok: true,
        text: `已更新：新增 ${(data && data.stored) ?? 0} 条` + (d ? ` · 最新 ${d}` : ''),
      }
    }
  } catch (e) {
    lidxMsg.value = { ok: false, text: '更新失败（网络或超时，可稍后重试）' }
  } finally {
    lidxBusy.value = false
    setTimeout(() => { lidxMsg.value = null }, 5000)
  }
}
// ★ 2026-10-27（市场底清单并入观察器）：摘要行 —— 收起时也能看出"3/6 · 距 4 条还差 1 条"。
//   `n_pass` 由后端下发（默认 4），不写死，避免阈值调了文案没跟。
const pbwLine = computed(() => {
  if (!pbw.value) return '加载中…'
  const n = Number(pbw.value.n_checked) || 0
  const total = Number(pbw.value.n_total) || 6
  const pass = Number(pbw.value.n_pass) || 4
  const gap = pass - n
  return `${n}/${total}` + (gap > 0 ? ` · 距 ${pass} 条还差 ${gap} 条` : ' · 已达成')
})

// ★★ 2026-10-26（用户："李大霄卡片加展开收起；一天可能有几个视频，卡内 tab 切换，
//   水平滚动，选中后 tab 自行居中"）：
//   · `items` 由后端下发（最近 12 期精简投影）；缺 items 时回退单条 latest（旧接口兼容）
//   · 选中态按 `entry_id` 记忆 —— 60s 轮询有新视频插到队首时，不会把用户踢回第一条
const lidxOpen = ref(true)
const lidxSel = ref(0)
const lidxSelId = ref('')
const lidxTabEls = []
function setLidxTab(el, i) { if (el) lidxTabEls[i] = el }
const lidxItems = computed(() => {
  const arr = (lidx.value && lidx.value.items) || []
  if (arr.length) return arr
  return (lidx.value && lidx.value.latest) ? [lidx.value.latest] : []
})
const lidxCur = computed(() => lidxItems.value[lidxSel.value] || lidxItems.value[0] || {})
function lidxTabLabel(t) {
  const s = String((t && t.date) || '')
  return s.slice(5, 16) || '—'    // MM-DD HH:MM（同日多条视频靠时刻区分）
}
function selectLidx(i) {
  lidxSel.value = i
  lidxSelId.value = (lidxItems.value[i] || {}).entry_id || ''
  // 选中后把自己滚到可视区中央（横向 tab 条可能很长）
  nextTick(() => {
    const el = lidxTabEls[i]
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' })
    }
  })
}
watch(lidxItems, (arr) => {
  if (!arr.length) return
  const i = lidxSelId.value ? arr.findIndex(x => x.entry_id === lidxSelId.value) : -1
  lidxSel.value = i >= 0 ? i : 0
})
// 情绪六级配色（口径=后端 sentiment_level，移植自 xiaopage sentiment.ts；多→红系，空→蓝青系）
function lidxLevelColor(level) {
  return { '极度狂热': 'text-red-400', '乐观': 'text-orange-400', '谨慎偏多': 'text-amber-400',
           '谨慎偏空': 'text-cyan-400', '悲观': 'text-blue-400', '极度恐慌': 'text-blue-300' }[level] || 'text-muted'
}
function lidxBarColor(score) {
  const s = Number(score) || 0
  if (s >= 76) return '#ef4444'
  if (s >= 56) return '#f97316'
  if (s >= 46) return '#f59e0b'
  if (s >= 36) return '#06b6d4'
  return '#3b82f6'
}
// ★ 2026-10-19：手动重扫公告（后端 scan_and_store(push=False)，不推企微）。
//   重扫完必须重拉清单 —— 提示挂在清单第①条里，不重拉会显示旧结果。
const annRefreshing = ref(false)
async function refreshAnnouncements() {
  if (annRefreshing.value) return
  annRefreshing.value = true
  try {
    await refreshPolicyAnnouncements()
    await loadPolicyBottom()
  } catch (e) {
    console.warn('[pbw] 公告重扫失败', e)
  } finally {
    annRefreshing.value = false
  }
}
// ★ 2026-10-19：手动重算份额异动（上交所官方接口，不推企微）。
const etfRefreshing = ref(false)
async function refreshEtf() {
  if (etfRefreshing.value) return
  etfRefreshing.value = true
  try {
    await refreshPolicyEtfShares()
    await loadPolicyBottom()      // 异动会改变第①条的 checked ⇒ 必须重拉清单
  } catch (e) {
    console.warn('[pbw] 份额异动重算失败', e)
  } finally {
    etfRefreshing.value = false
  }
}
async function toggleNationTeam() {
  const cur = !!(pbw.value && pbw.value.items
    && pbw.value.items.nation_team_ann && pbw.value.items.nation_team_ann.checked)
  try {
    const { data } = await confirmPolicyBottom(!cur, '')
    if (data && data.ok !== false && data.items) pbw.value = data
    else await loadPolicyBottom()
  } catch (e) {
    // 失败不动 UI（宁可显示旧状态，也不要显示"已勾选"但其实没落库）
    console.warn('[pbw] 人工打勾失败', e)
  }
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

// ★ 2026-10-22：盘中「变化检测器」——8 档规则采样（09:45~14:45）。
//   定位：**展示项**，回答问题"这半小时有没有料"，不进决策链、不调 LLM。
//   数据：/api/system/workbench-scan（当日 8 档）+ /summary（7 日 worth_rate）。
const wsDay = ref(null)
const wsSummary = ref(null)
const wsLoading = ref(false)
const wsErr = ref('')
const wsSel = ref('')
const wsRows = computed(() => (wsDay.value && wsDay.value.rows) || [])
const wsPick = computed(() => wsRows.value.find(x => x.slot === wsSel.value) || null)

// ★ 2026-10-23：观察器合并后，4 段折叠状态用 localStorage 记忆（默认全收起）
const OBS_KEY = 'wb_obs_open'
const _obs = (() => {
  try { return JSON.parse(localStorage.getItem(OBS_KEY) || '{}') } catch { return {} }
})()
const wsOpen = ref(!!_obs.ws)
// ★ 2026-10-26（用户："这些文字放在一个 ? 里"）：观察器标题旁 `?` 的悬停说明。
//   内容与原先卡底常显的两段文字**完全一致**（判据 + 配色），只是不再占版面。
//   `\n` 在原生 title 里会换行（浏览器支持）。
const obsHelp = [
  '退潮=已从 120 日高点回撤 ≥25% 且近 5 日仍跌（相对行业中位）',
  '失守=今日跌破且昨日仍在其上；篮子=6 只 AI 硬件龙头等权，回撤为距 120 日高点',
  '观察器不构成买卖信号',
  '配色：涨跌幅=红涨绿跌；回撤 ≤-25% 标红（风险）；距支撑 ≤1.5% 标黄（逼近）；状态标签单独着色',
].join('\n')
const crowdOpen = ref(!!_obs.crowd)
const scenOpen = ref(!!_obs.scen)
const aiOpen = ref(!!_obs.ai)
function saveObs() {
  try {
    localStorage.setItem(OBS_KEY, JSON.stringify({
      ws: wsOpen.value, crowd: crowdOpen.value,
      scen: scenOpen.value, ai: aiOpen.value,
      pbw: pbwOpen.value,     // ★ 2026-10-27：市场底清单并入后一起记忆
    }))
  } catch { /* 隐私模式忽略 */ }
}
const obsAllOpen = computed(() =>
  wsOpen.value && crowdOpen.value && scenOpen.value && aiOpen.value && pbwOpen.value)
function toggleAllObs() {
  const v = !obsAllOpen.value
  wsOpen.value = v
  crowdOpen.value = v
  scenOpen.value = v
  aiOpen.value = v
  pbwOpen.value = v
  try { localStorage.setItem(PBW_KEY, v ? '1' : '0') } catch { /* ignore */ }
  saveObs()
}

// 摘要行：收起时也必须能判断"要不要点开"（数字 + 状态点）
const wsLine = computed(() => {
  if (wsLoading.value) return '加载中…'
  if (wsErr.value) return wsErr.value
  if (!wsDay.value) return '—'
  const p = wsPick.value || (wsRows.value.length ? wsRows.value[wsRows.value.length - 1] : null)
  if (!p) return '当日尚无采样'
  const stale = (p.reasons || []).some(x => x.tag === 'stale')
  const v = wsVerdict(p)
  const head = `${p.slot} · ${stale ? '快照不计分'
    : (v.cn + (p.bias ? ' ' + wsBiasText(p) : ''))} ${p.score}分`
  // 当日分布：正/反/中性（后端 2026-10-27 起返回；老响应回退到 worth_n）
  let day = ''
  if (wsDay.value.judged) {
    day = (wsDay.value.pos_n != null)
      ? ` · 当日 正${wsDay.value.pos_n}/反${wsDay.value.neg_n}/中性${wsDay.value.neu_n}`
      : ` · 当日 ${wsDay.value.worth_n}/${wsDay.value.judged}`
  }
  return head + day
})
const wsDot = computed(() => {
  const p = wsPick.value || (wsRows.value.length ? wsRows.value[wsRows.value.length - 1] : null)
  if (!p) return 'bg-slate-500'
  if ((p.reasons || []).some(x => x.tag === 'stale')) return 'bg-slate-400'
  return wsVerdict(p).dot
})
const crowdLine = computed(() => {
  const rows = crowdTop.value || []
  if (!rows.length) return '—'
  const n = rows.filter(r => String(r.level || '').includes('退潮')).length
  const k = (keyLv.value?.rows || [])[0]
  return `${rows.length} 个赛道` + (n ? ` · ${n} 个退潮` : '') +
    (k ? ` · ${k.name} ${k.close}` : '')
})
const crowdDot = computed(() =>
  (crowdTop.value || []).some(r => String(r.level || '').includes('退潮'))
    ? 'bg-rose-400' : 'bg-emerald-400')
// ★ 2026-10-24：情景从摘要文字里抽成**彩色徽标**（收起时也能一眼看出倾向）
const scenBadgeClass = computed(() => {
  const s = scen.value
  if (!s) return 'bg-slate-500/20 text-slate-300'
  if (s.geo_candidate || s.scenario === '熊市加深') return 'bg-rose-500/20 text-rose-300'
  if (s.scenario === '提前修复') return 'bg-emerald-500/20 text-emerald-300'
  return 'bg-amber-500/20 text-amber-300'
})
const scenLine = computed(() => {
  const s = scen.value
  if (!s) return '加载中…'
  const a = s.anchor || {}
  return `${s.lean || '—'} ${s.n_up ?? 0}多/${s.n_down ?? 0}空` +
    (a.level ? ` · 锚点${a.state || '—'}` : '')
})
const scenDot = computed(() => {
  const s = scen.value
  if (!s) return 'bg-slate-500'
  if (s.geo_candidate || s.scenario === '熊市加深') return 'bg-rose-400'
  if (s.scenario === '提前修复') return 'bg-emerald-400'
  return 'bg-amber-400'
})
const aiLine = computed(() => {
  const b = aiBasket.value?.basket
  if (!b) return '—'
  return `回撤 ${fmtPct(b.dd120)} · 5日 ${fmtPct(b.ret5)} · 30日 ${fmtPct(b.ret30)}`
})
const aiDot = computed(() =>
  (aiBasket.value?.basket?.dd120 || 0) <= -25 ? 'bg-rose-400' : 'bg-slate-500')
function wsColor(score) {
  const s = Number(score) || 0
  if (s >= 80) return 'bg-rose-500/20 text-rose-300 border-rose-500/40'
  if (s >= 60) return 'bg-orange-500/20 text-orange-300 border-orange-500/40'
  if (s >= 40) return 'bg-amber-500/20 text-amber-300 border-amber-500/40'
  if (s >= 20) return 'bg-sky-500/20 text-sky-300 border-sky-500/40'
  return 'bg-slate-500/10 text-muted border-border'
}
// ★ 2026-10-23（用户："以上的英文改为中文"）：/workbench-scan 的 reason tag 是英文，
//   展示层统一映射成中文（title 里保留原 tag，便于对照后端规则）。
const WS_TAG_CN = {
  idx_move: '指数急动', breadth_shift: '宽度变化', breadth_flip: '宽度翻转',
  limit_up: '涨停变化', limit_down: '跌停变化', sector_leader: '领涨切换',
  sector_rotate: '板块轮动', volume: '区间放量', news: '快讯密集',
  news_key: '关键快讯', gap: '跳点', baseline: '基线', stale: '快照',
}
function wsTagCn(t) { return WS_TAG_CN[t] || t || '—' }
// ★★ 2026-10-27（用户确认）：把「有料」升级为**正/反影响** —— 强度(score) + 方向(bias) 两维。
//   positive/negative：|bias| ≥ 20（后端判）；neutral：在动但方向不明（这本身是信息）；
//   none：score < 40，没有实质变化。颜色沿用全站红涨绿跌（正=红 / 反=绿）。
const WS_VERDICT = {
  positive: { cn: '正影响', cls: 'text-rose-300', dot: 'bg-rose-400' },
  negative: { cn: '反影响', cls: 'text-emerald-300', dot: 'bg-emerald-400' },
  neutral: { cn: '中性变化', cls: 'text-amber-300', dot: 'bg-amber-400' },
  none: { cn: '无明显变化', cls: 'text-muted', dot: 'bg-slate-500' },
}
function wsVerdict(r) { return WS_VERDICT[(r && r.verdict) || 'none'] || WS_VERDICT.none }
function wsBiasText(r) {
  const b = Number((r && r.bias) || 0)
  return b > 0 ? `+${b}` : String(b)
}
// 逐条 reason 的方向（后端 dir：+1 利多 / -1 利空 / 0 中性）
function wsDirCn(d) { const n = Number(d) || 0; return n > 0 ? '利多' : (n < 0 ? '利空' : '中性') }
function wsDirCls(d) { const n = Number(d) || 0; return n > 0 ? 'text-rise' : (n < 0 ? 'text-fall' : 'text-muted') }
async function loadWorkbenchScan() {
  wsLoading.value = true
  wsErr.value = ''
  try {
    const [d, s] = await Promise.all([
      getWorkbenchScan(selectedDate.value),
      getWorkbenchScanSummary(7),
    ])
    wsDay.value = d.data || null
    wsSummary.value = s.data || null
    // 默认选中**最后一个"有方向"的档**（正/反影响优先，其次中性变化，最后任意一档）
    if (!wsSel.value && wsRows.value.length) {
      const dir = wsRows.value.filter(x => x.verdict === 'positive' || x.verdict === 'negative')
      const any = wsRows.value.filter(x => x.worth)
      wsSel.value = ((dir[dir.length - 1] || any[any.length - 1]
        || wsRows.value[wsRows.value.length - 1]) || {}).slot
    }
  } catch (e) {
    wsErr.value = '盘中变化数据加载失败（接口需登录）'
    wsDay.value = null
  } finally {
    wsLoading.value = false
  }
}
// ★ 2026-10-23：拥挤赛道 / 指数关键位（纯观察器）
const crowd = ref(null)
// ★ 2026-10-30：金十每日汇总（默认收起——每天 50~60 条，展开会撑爆观察器组）
const jin10 = ref(null)
const jin10Open = ref(false)
const openJin10 = ref('')          // 当前展开的那一条（`category_id-seq`）
const keyLv = ref(null)
const aiBasket = ref(null)
const riskLoading = ref(false)
const riskErr = ref('')
const crowdTop = computed(() => {
  const rows = (crowd.value && crowd.value.rows) || []
  const rank = { 高拥挤: 0, 升温: 1, 退潮: 2, 正常: 3 }
  return [...rows].sort((a, b) =>
    (rank[a.level] ?? 9) - (rank[b.level] ?? 9) || (b.score || 0) - (a.score || 0),
  ).slice(0, 5)
})
function crowdBadge(level) {
  if (level === '高拥挤') return 'bg-rose-500/20 text-rose-300'
  if (level === '升温') return 'bg-amber-500/20 text-amber-300'
  if (level === '退潮') return 'bg-slate-500/20 text-slate-300'
  return 'bg-border/40 text-muted'
}
function keyBadge(status) {
  if (status === '失守年内低点' || status === '失守前低') return 'bg-rose-500/20 text-rose-300'
  if (status === '失守整数位') return 'bg-orange-500/20 text-orange-300'
  if (status === '逼近支撑') return 'bg-amber-500/20 text-amber-300'
  return 'bg-border/40 text-muted'
}
// ★ 2026-10-30：金十汇总——折叠态的一行摘要（各栏目条数）
const jin10Line = computed(() => {
  const secs = jin10.value?.sections || []
  if (!secs.length) return jin10.value ? '暂无' : ''
  const parts = secs.map(s => `${s.name} ${s.items.length}`)
  const total = secs.reduce((n, s) => n + s.items.length, 0)
  return `${parts.join(' · ')}（共 ${total} 条）`
})
// 标签配色：与"连板/涨停结构"那套语义区分开，这里只用浅底小徽章
function jin10TagCls(tag) {
  if (tag === '政策') return 'bg-violet-500/15 text-violet-300'
  if (tag === '资金面') return 'bg-amber-500/15 text-amber-300'
  if (tag === '业绩') return 'bg-emerald-500/15 text-emerald-300'
  if (tag === '订单') return 'bg-sky-500/15 text-sky-300'
  if (tag === '存储' || tag === '半导体') return 'bg-rose-500/15 text-rose-300'
  if (tag === '光模块' || tag === '算力') return 'bg-cyan-500/15 text-cyan-300'
  if (tag === '汽车' || tag === '地产') return 'bg-orange-500/15 text-orange-300'
  return 'bg-border/40 text-muted'
}
async function loadRiskWatch() {
  riskLoading.value = true
  riskErr.value = ''
  try {
    const [c, k, a] = await Promise.all([
      getCrowdingWatch(selectedDate.value),
      getKeyLevelWatch(selectedDate.value),
      getAiBasketWatch(selectedDate.value),
    ])
    crowd.value = c.data || null
    keyLv.value = k.data || null
    aiBasket.value = a.data || null
  } catch (e) {
    riskErr.value = '拥挤/关键位数据加载失败（接口需登录）'
  } finally {
    riskLoading.value = false
  }
  // ★ 2026-10-30：金十汇总**单独请求**，不放进上面的 Promise.all。
  //   它的失败原因通常是 `JIN10_X_TOKEN` 过期（401）—— 属常态性运维问题，
  //   若塞进 Promise.all 会把整个"拥挤/关键位"块拖成加载失败。
  try {
    const t = await getJin10Tidy(selectedDate.value)
    jin10.value = t.data || null
  } catch {
    jin10.value = null
  }
}
// ★ 2026-10-23：磨底 2.0 推演证伪记分卡
const scen = ref(null)
function scenBadge(s) {
  if (s === '熊市加深') return 'bg-rose-500/20 text-rose-300'
  if (s === '提前修复') return 'bg-emerald-500/20 text-emerald-300'
  return 'bg-amber-500/20 text-amber-300'
}
function leanDot(l) {
  if (l === '多') return 'bg-emerald-400'
  if (l === '空') return 'bg-rose-400'
  if (l === '不足') return 'bg-slate-500'
  return 'bg-slate-400/60'
}
function scenVal(s) {
  const v = s.value
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  return `${Number.isInteger(n) ? n : n.toFixed(2)}${s.unit || ''}`
}
// ★★ 2026-10-24（用户："观察器什么时候能加上 llm 分析"）：观察器 AI 解读。
//   · **默认收起、点开才请求** —— 它是同步 LLM 调用；按日缓存 + 数据指纹，30 分钟内重复点免费。
//   · 只做"性质判断 + 推翻条件"，不给仓位/买卖建议（观察器不进决策链）。
const obsAIOpen = ref(false)
const obsAI = ref({ loading: false, error: '', markdown: '', conclusion: '', reasons: [],
                    cached: false, as_of: '', date: '' })
const obsAISummary = computed(() => {
  const o = obsAI.value
  if (o.loading) return '分析中…（LLM，约十几秒）'
  if (o.error) return '解读失败（点开查看）'
  if (o.conclusion) return o.conclusion
  return '点开让 AI 解读今天的变化（反弹 / 反转 + 推翻条件）'
})
async function loadObsAI(force = false) {
  if (obsAI.value.loading) return
  obsAI.value = { ...obsAI.value, loading: true, error: '' }
  try {
    const { data } = await getObserverReview(selectedDate.value, !!force)
    if (data && data.ok === false) {
      obsAI.value = { ...obsAI.value, loading: false, error: data.error || '解读失败' }
      return
    }
    obsAI.value = {
      loading: false, error: '',
      markdown: data.markdown || '', conclusion: data.conclusion || '',
      reasons: data.reasons || [], cached: !!data.cached,
      as_of: data.as_of || '', date: data.date || '',
    }
  } catch (e) {
    obsAI.value = { ...obsAI.value, loading: false,
                    error: '请求失败：' + (e?.message || e) }
  }
}
function toggleObsAI() {
  obsAIOpen.value = !obsAIOpen.value
  if (obsAIOpen.value && !obsAI.value.loading
      && !obsAI.value.markdown && !obsAI.value.conclusion) {
    loadObsAI(false)
  }
}

async function loadScenario() {
  try {
    const { data } = await getScenarioWatch(selectedDate.value)
    scen.value = data || null
  } catch (e) {
    scen.value = null
  }
  // 换日 / 回放切日：清掉上一天的 AI 解读（按日缓存，留着会误导）
  obsAI.value = { loading: false, error: '', markdown: '', conclusion: '', reasons: [],
                  cached: false, as_of: '', date: '' }
  if (obsAIOpen.value) loadObsAI(false)
}
watch(selectedDate, () => {
  wsSel.value = ''
  loadWorkbenchScan()
  loadRiskWatch()
  loadScenario()
})

// ★ 2026-09-27 事件驱动信号（v0 展示项）。定位三行（项目约定）：
//   **是什么**  极端普涨（涨家数占比≥90%）+ 涨停骤增（≥50 家）的"政策脉冲"实时提示
//   **不是什么** 不是买卖信号；不进决策链；不改市况/仓位/战法准入
//   **下一步**  实盘样本积累 1~2 季后复核「能否作为 defensive 先行解除」
async function loadEvents() {
  try {
    const { data } = await getMarketEvents()
    eventSignal.value = (data && data.event) || null
  } catch { /* 静默保留上次值：事件极稀有，失败不该打扰用户 */ }
}
/** 事件悬浮说明（顶栏 title）：带历史预期，防止被当买卖信号用。 */
const eventTitle = computed(() => {
  const e = eventSignal.value
  if (!e || !e.available) return ''
  const base = `涨家数占比 ${((Number(e.up_ratio) || 0) * 100).toFixed(1)}%`
    + ` · 涨停 ${e.limit_up} · 跌停 ${e.limit_down}`
  const s = e.e2_stats
  if (e.e2_policy_surge && s) {
    const d = (s.by_regime && s.by_regime.defensive) || {}
    // ★ 可执行标的（真实指数终验 + 成本口径，见 _report_事件驱动信号源接入 §8.4）
    const ex = (e.exec_stats && e.exec_stats.targets) || []
    const exLine = ex.length
      ? `历史同态可执行标的（T+1 开盘买、持 20 日、已扣 0.3% 成本）：\n`
        + ex.map(t => `  ${t.name}(${t.etf})  +${t.net_edge}pp  P=${t.p}  n=${t.n}`).join('\n')
        + `\n（沪深300 扣成本后为负；-7% 止损为负贡献，未采用）\n`
      : ''
    return `${base}\nE2 政策脉冲（历史 ${s.n} 天 / ${s.n_clusters} 簇）：`
      + `后 20 日全市场等权 +${s.h20_mean}%，基准 +${s.h20_base}%，胜率 ${s.h20_win}%\n`
      + `防御期内增量 +${d.diff}pp（n=${d.n}）\n`
      + exLine
      + `⚠️ 展示项：历史统计参考，非投资建议，不改市况/仓位`
  }
  if (e.e1_capitulation) {
    return `${base}\n冰点信号（E1）：历史上无额外 edge（已归档，仅作背景提示）`
  }
  return base
})
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

async function loadBrief(phase, force = false) {
  const day = selectedDate.value
  const ck = `brief:${phase}`
  if (!force && _dayCached(ck, day)) return
  // ★★ 2026-09-30（用户："llm 分析失败，可以加一个手动重试"）：`force=true` 时
  //   ① 先清掉"当日已取到"标记 —— 否则重试后切回本段仍被缓存挡住（用户以为没生效）；
  //   ② 传 `refresh=true` 让后端**重新生成**（消耗 1 次 LLM，api 层已给 180s 超时）。
  if (force) delete _dayCache[ck]
  briefErr.value = ''
  briefLoading.value = true
  briefPhase.value = phase
  try {
    const { data } = await getTraderBrief(!!force, phase)
    briefMd.value = data?.markdown || ''
    briefDegraded.value = !!data?.degraded
    // ★ 纪律③：降级版不缓存（后端窗口内还会重试出 LLM 版）
    if (briefMd.value && !briefDegraded.value) _dayMark(ck, day)
  } catch (e) {
    briefMd.value = ''
    briefErr.value = (e?.response?.data?.detail || e?.message) || '未知错误'
  } finally {
    briefLoading.value = false
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
    radarQuoteAsOf.value = (data && data.quote_as_of) || ''
    radarQuoteSnap.value = !!(data && data.quote_from_snapshot)
    radarQuoteDataDate.value = (data && data.quote_data_date) || ''
    radarRisk.value = (data && data.portfolio_risk) || null
    radarErr.value = ''
  } catch (e) {
    radarErr.value = (e && e.message) || '未知错误'   // 保留上次数据（铁律 11）
  }
}
// ★ 2026-10-25：防御 Top10（H2' 影子榜，日批快照）—— 与 loadTop 解耦：
//   · 失败只置 `defErr`，**不动 topItems**（两张榜各自独立，铁律 11 保留上次值）；
//   · 后端 `ok=false`（尚无 defensive 行）时 `defItems=[]` + note 透传，卡内显示「等日批产出」。
async function loadDefTop() {
  try {
    const { data } = await getShadowDefensive(10)
    if (data && data.ok === false) {
      defErr.value = data.note || data.reason || '暂无防御影子榜数据'
      return
    }
    defTopItems.value = (data && data.items) || []
    defMeta.value = {
      date: (data && data.date) || '',
      gate: !!(data && data.gate),
      eq_state: (data && data.eq_state) || '',
      eq_date: (data && data.eq_date) || '',
      delta: (data && data.delta != null) ? data.delta : null,
    }
    defIsLive.value = false
    defSnap.value = { items: defTopItems.value, meta: defMeta.value }
    defErr.value = ''
  } catch (e) {
    defErr.value = '读取失败：' + ((e && e.message) || e)   // 保留上次数据
  }
}

// 实时预览：当前精算榜 + 同一冻结公式（银行组 ×(1+δ)）重排。不改官方榜、不进判定。
// 宽防御组名单（银行 ∪ 高股息代理 ∪ 低波）：日批落库，读一次 ≈15KB，失败静默
async function loadDefGroup() {
  try {
    const { data } = await getDefensiveGroup()
    defGroup.value = {
      ok: !!(data && data.ok),
      date: (data && data.date) || '',
      items: (data && data.items) || [],
      counts: (data && data.counts) || {},
      gate: !!(data && data.gate),
      eq_state: (data && data.eq_state) || '',
    }
  } catch (e) {
    console.warn('[workbench] 宽防御组读取失败:', e?.message || e)
  }
}

async function loadDefLive() {
  if (defLiveLoading.value) return
  // ① **本地引擎优先**（用户拍板）：名单内的 137 只逐只在浏览器精算
  //    ⇒ 不调 /batch/top、不读 kline_cache，后端只剩 15KB 名单 +
  //      （首次的）财报批量接口。
  if (useFrontendMode.value && feDbReady.value && (defGroup.value.items || []).length) {
    defLiveLoading.value = true
    defErr.value = ''
    try {
      const codes = defGroup.value.items.map(x => x.code)
      const rows = await scoreCodeList(codes)
      if (!rows || !rows.length) {
        defErr.value = '本地精算无结果（K 线包未覆盖组内代码？可先下载 K 线包）'
        return
      }
      const delta = defMeta.value.delta != null ? Number(defMeta.value.delta) : 0.80
      const gate = !!defGroup.value.gate
      const items = rows
        .map(r => ({
          code: r.code, name: r.name || '',
          raw_score: r.total_score,
          total_score: gate
            ? Math.round(Number(r.total_score || 0) * (1 + delta) * 10) / 10
            : r.total_score,
          change_pct: r.change_pct,
          boosted: gate,
        }))
        .sort((a, b) => (b.total_score || 0) - (a.total_score || 0))
        .slice(0, 10)
        .map((x, i) => ({ ...x, rank_pos: i + 1 }))
      defTopItems.value = items
      defMeta.value = {
        ...defMeta.value,
        date: defGroup.value.date || defMeta.value.date,
        gate,
        eq_state: defGroup.value.eq_state || defMeta.value.eq_state,
        live: true, local: true,
        as_of: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
        group_n: codes.length,
      }
      defIsLive.value = true
    } catch (e) {
      defErr.value = '本地精算失败：' + ((e && e.message) || e)
    } finally {
      defLiveLoading.value = false
    }
    return
  }
  // ② 回退：后端实时重排（本地引擎未就绪时；首次约 1 分钟、10~15MB）
  defLiveLoading.value = true
  defErr.value = ''
  try {
    const { data } = await getShadowDefensive(10, true)
    if (!data || data.ok === false) {
      defErr.value = (data && (data.note || data.reason)) || '实时重排失败'
      return
    }
    defTopItems.value = (data && data.items) || []
    defMeta.value = {
      date: (data && data.date) || '',
      gate: !!(data && data.gate),
      eq_state: (data && data.eq_state) || '',
      eq_date: (data && data.eq_date) || '',
      delta: (data && data.delta != null) ? data.delta : null,
      live: true,
      as_of: (data && data.as_of) || '',
    }
    defIsLive.value = true
  } catch (e) {
    defErr.value = '实时重排失败：' + ((e && e.message) || e)
  } finally {
    defLiveLoading.value = false
  }
}

function toggleDefLive() {
  if (defIsLive.value) {
    // 还原官方日批（内存里就有，零请求）
    defTopItems.value = defSnap.value.items || []
    defMeta.value = defSnap.value.meta || {}
    defIsLive.value = false
    defErr.value = ''
    return
  }
  loadDefLive()
}

async function loadTop(force = false) {
  try {
    // ★ 2026-09-28（用户："评分榜之前不就是实时的吗?我都是使用本地计算的"）——
    //   **本地优先**：用户在榜单页选过「本地计算」且 K 线包就绪 ⇒ 走**同一套前端引擎**
    //   （`computeRanking`：全市场**实时行情** + 本地精算）⇒ 盘中即实时，与榜单页同结果。
    //   ⚠️ `computeRanking` 很重（拉全市场行情 + 分批财报 + Worker 精算）⇒
    //      **有 5 分钟内的结果就直接复用**，不无脑重算（工作台会切阶段/轮询）。
    if (useFrontendMode.value && feDbReady.value) {
      const _fresh = feLastScoreTime.value
        && (Date.now() - feLastScoreTime.value.getTime() < 5 * 60 * 1000)
      let rows = feScoringResult.value || []
      // ★ 2026-09-28：`force=true`（轮询调用）**跳过 5 分钟复用窗口** —— 否则界面写着
      //   "120s 后刷新"而数据几分钟不动（复用窗口本是为"切阶段别重算"设的，轮询语义不同）。
      // ★ 2026-10-24（BK-56）：复用窗口失效只在**交易时段**触发重算——夜间行情不变，
      //   "过期"的本地结果就是最新可得；旧实现 `!_fresh` 无条件重算 ⇒ 本地模式下
      //   夜间每 5 分钟一次全市场 computeRanking（很重），与注释意图相反。
      //   force=true 只在 isLivePhase() 时由 120s 轮询传入 ⇒ 语义不变。
      if (!rows.length || force || (!_fresh && isLivePhase())) {
        rows = await computeRanking({ mode: 'top', limit: 10 })
      }
      topItems.value = (rows || []).slice(0, 10)
      topMode.value = 'local'
      topErr.value = ''
      return
    }
    // 兜底：未启用本地计算（或 K 线包未就绪）→ 原后端批次接口
    const { data } = await getScoreTop({ limit: 10 })
    topItems.value = (data && data.data) || []
    topMode.value = 'backend'
    topErr.value = ''
  } catch (e) { topErr.value = (e && e.message) || '未知错误' }
}
async function loadGateWatch() {
  try {
    const { data } = await getGateWatch(80)
    gwItems.value = (data && data.items) || []
    // ★ 2026-09-25：总览字段一并保存（原来只留 items ⇒ 卡里只能写"候选 N 只"）
    gwMeta.value = {
      regime: (data && data.regime) || '',
      total: (data && data.total) || 0,
      ready3: (data && data.ready3) || 0,
      counts: (data && data.counts) || {},
      strategy_hits: (data && data.strategy_hits) || 0,
      source: (data && data.source) || '',
      data_date: (data && data.data_date) || '',
    }
    gwErr.value = ''
    // ★ 2026-09-30：拿到 code 清单后**顺带拉一次实时涨跌幅**（不阻塞上面的渲染 ——
    //   价格到达前那格显示「—」，见观察池卡内注释）。
    loadGwPrices()
  } catch (e) { gwErr.value = (e && e.message) || '未知错误' }
}

// ★ 2026-09-30：观察池实时涨跌幅（复用榜单页同款做法）。
//   ⚠️ 失败**保留上次的值**（不清空 ⇒ 页面不闪「—」）：交易时段外多为休市/网络抖动。
async function loadGwPrices() {
  const codes = (gwItems.value || []).map(g => g.code).filter(Boolean)
  if (!codes.length) return
  try {
    // ★ 2026-10-26（P1 修复）：原来用 `getBatchPrices` ⇒ **每次直连腾讯**（120s 组里每 2 分钟
    //   一次外部请求，多标签页翻倍），与「观察池报价走内存缓存」的登记口径不符。
    //   改 `cached=1`：只读调度器维护的全市场缓存，**上游请求 = 0**；返回是对象（老的数组结构
    //   做兜底，防后端未热更时白屏）。
    const { data } = await getBatchPricesCached(codes)
    const rows = Array.isArray(data) ? data : ((data && data.rows) || [])
    const m = {}
    for (const s of rows) {
      if (s && s.code && Number(s.price) > 0) m[s.code] = { price: s.price, change_pct: s.change_pct }
    }
    if (Object.keys(m).length) gwPrices.value = m
  } catch (e) {
    console.warn('[gw] 实时价刷新失败（保留上次）', e?.message || e)
  }
}

// ★★ 2026-10-26（G1 轮询合并）：盘面脉冲 —— 一次请求拿回 5 块同源数据。
//   【覆盖哪 5 块】overview（涨跌家数/涨停/成交额）、temperature（温度）、emotion（情绪判读）、
//     amount_share（行业成交额占比）、gw（观察池报价，随 `codes` 传参才有）。
//   【为什么能合】它们的上游都是 `tencent._cache["stocks"]`（同源同刻），且都只读缓存 ⇒
//     合并后**服务端外部请求不变**，只是把浏览器 5 次往返压成 1 次。
//   【失败语义】按块降级：`blocks.xxx === null` 时**保留该块旧值**（不清空、不显示 0），
//     其余块照常渲染；整包失败则所有块保持原样。
// 应用一份 pulse payload（实时取回 / 跨标签缓存复用，走同一条路径）
function applyPulse(data) {
  const b = (data && data.blocks) || {}
  if (b.overview) overview.value = b.overview || {}
  if (b.temperature) temperature.value = b.temperature
  if (b.emotion) emotion.value = b.emotion
  if (b.amount_share) amountShare.value = b.amount_share
  const gw = b.gw
  if (gw && (gw.rows || []).length) {
    const m = {}
    for (const s of gw.rows) {
      if (s && s.code && Number(s.price) > 0) m[s.code] = { price: s.price, change_pct: s.change_pct }
    }
    if (Object.keys(m).length) gwPrices.value = m
  }
}

// 跨标签：60s 快组在多标签下不该翻倍 —— 用 localStorage 租约选一个"抓取者"，
// 其余标签复用同一份 payload（体积 ~8KB，配额无压力；超过 200KB 就放弃缓存）。
const PULSE_LEASE = 'wb:pulse:lease'
const PULSE_CACHE = 'wb:pulse:cache'
const TAB_ID = Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
function _lsGet(k) { try { const r = localStorage.getItem(k); return r ? JSON.parse(r) : null } catch { return null } }
function _lsSet(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); return true } catch { return false } }

async function loadPulse() {
  if (typeof document !== 'undefined' && document.hidden) return   // 后台标签不发
  const codes = (gwItems.value || []).map(g => g.code).filter(Boolean)
  const now = Date.now()
  const lease = _lsGet(PULSE_LEASE)
  const otherOwner = lease && lease.id !== TAB_ID && (now - (lease.ts || 0) < FAST_SEC * 1000 - 3000)
  if (otherOwner) {
    const c = _lsGet(PULSE_CACHE)
    if (c && c.payload && (now - (c.ts || 0) < FAST_SEC * 1000 + 5000)) {
      applyPulse(c.payload)      // 另一个标签刚取过 ⇒ 直接复用，本次零请求
      return
    }
  }
  try {
    const { data } = await getMarketPulse(codes)
    if (data && data.blocks) {
      applyPulse(data)
      _lsSet(PULSE_LEASE, { id: TAB_ID, ts: Date.now() })
      const js = JSON.stringify({ ts: Date.now(), payload: data })
      if (js.length < 200000) _lsSet(PULSE_CACHE, JSON.parse(js))
    }
  } catch (e) {
    console.warn('[workbench] 盘面脉冲失败（各块保留旧值）', e?.message || e)
  }
}

// ★ 2026-09-25（P1「明日准备」）：待触发的交易计划 —— 复盘 → 次日的**交接棒**。
//   口径：只取 `status === 'waiting'`（已触发/已完成的不该再叫"待办"）。
//   ⚠️ 这里只把"明天开盘要盯的条件"摆出来，不自动改状态、不催办。
async function loadPlans() {
  try {
    const { data } = await getUserPlans()
    plans.value = ((data && data.data) || []).filter(p => (p.status || 'waiting') === 'waiting')
    plansErr.value = ''
  } catch (e) {
    plansErr.value = (e && e.message) || '未知错误'   // 保留上次数据（铁律 11）
  }
}

// ★ 2026-09-25（盘后补强 · 缺口2）：**今日执行回看** —— 只统计"今天"已推送的教练卡。
//   `days` 传 1 只是满足签名（走 `day` 分支时它被忽略），真正生效的是 `bjToday()`。
async function loadTodayExec() {
  try {
    const { data } = await getCoachConsistency(1, bjToday())
    todayExec.value = data || null
    todayExecErr.value = ''
  } catch (e) {
    todayExecErr.value = (e && e.message) || '未知错误'   // 保留上次数据（铁律 11）
  }
}

// ★★ 2026-09-25（用户："把合理的都做了"）—— P0 盘后「**今日结算**」。
//   【为什么要它】盘后原本只有"系统榜 + 推送日志"，**看不到"我今天的结果"**：
//     `portfolio_radar.summary` 只有 {n, risk, opportunity, in_watch}，全页无盈亏汇总
//     ⇒ 收盘时页面在讲"系统"，不在讲"你"（与盘前有持仓预案、盘中有持仓卡的取向不一致）。
//   【口径（如实标注在卡上）】按**当前持仓市值加权**：
//     组合当日收益 = 今日市值 / 昨收市值 − 1，其中个股昨收市值 = 今日市值 /(1 + 当日涨跌%)
//     ⇒ 加总后再相除，**不是** `Σ(市值×涨幅)/Σ市值`（后者忽略复利、只是近似）。
//   ⚠️ 缺 `shares`（无股数）或当日无行情的持仓**只计入家数、不计入加权** ⇒
//     卡上同时显示"参与加权 N 只"，避免"3 只持仓却按 1 只算"这种数字对不上。
const portfolioDay = computed(() => {
  const all = radarItems.value || []
  if (!all.length) return null
  const usable = all.filter(x => x.day_pct != null && Number(x.price) > 0 && Number(x.shares) > 0)
  const up = all.filter(x => Number(x.day_pct) > 0).length
  const down = all.filter(x => Number(x.day_pct) < 0).length
  if (!usable.length) {
    return { n: all.length, usable: 0, dayPct: null, pnlPct: null, dayAmt: null,
             mvNow: null, up, down, flat: all.length - up - down,
             best: null, worst: null, ranked: [] }
  }
  let mvNow = 0, mvPrev = 0, costSum = 0
  for (const x of usable) {
    const r = Number(x.day_pct) / 100
    const mv = Number(x.price) * Number(x.shares)
    mvNow += mv
    mvPrev += mv / (1 + r)          // ← 昨收市值（r = 当日涨跌%，含 0 ⇒ 停牌不计变动）
    costSum += Number(x.cost || 0) * Number(x.shares)
  }
  const r100 = (v) => (v == null ? null : Math.round(v * 100) / 100)
  const ranked = [...usable].sort((a, b) => Number(b.day_pct) - Number(a.day_pct))
  return {
    n: all.length, usable: usable.length,
    dayPct: mvPrev > 0 ? r100((mvNow / mvPrev - 1) * 100) : null,
    pnlPct: costSum > 0 ? r100((mvNow / costSum - 1) * 100) : null,
    dayAmt: mvPrev > 0 ? Math.round(mvNow - mvPrev) : null,   // 当日盈亏（元）
    mvNow: Math.round(mvNow),
    up, down, flat: all.length - up - down,
    best: ranked[0] || null, worst: ranked[ranked.length - 1] || null,
    ranked,
  }
})

// ★ 2026-09-25（缺口2）：今日**已推送**的教练卡清单 —— 与「今日执行」的数字**同口径**
//   （`pushed` + `alert_date = 今天`）。`pushed` 是本次为列表接口补上的字段
//   （见后端 `_ALERT_LIST_COLS` 注释），缺它就只能把"落库但未推送"的建议也列进来，
//   导致清单条数与上方数字对不上。
const todayPushedCards = computed(() => (coachList.value || [])
  .filter(a => String(a.alert_date || '').slice(0, 10) === todayStr && a.pushed))
async function loadPush(date) {
  try {
    const { data } = await getPushLog(date || undefined, 80)
    pushItems.value = (data && data.items) || []
    pushConnErr.value = false
  } catch {
    pushConnErr.value = true        // 静默保留上次数据
  }
}

// ══════════════════════════════════════════════════════════════════════════
//  ★★ 2026-09-30（用户："LLM 自洽性审查接进右栏时间线卡"）
//  定位：`ScoreRank.vue` 的「今日雷达」= ①系统提示时间线 ②LLM 自洽性审查。其中 ①
//    工作台**早就有**（`pushItems` ← `loadPush` ← `/flash/push-log`，右栏顶部「今日系统
//    时间线」卡）⇒ 本轮只补 ②，且**接进同一张卡内**：二者同源（都基于今日提示集合），
//    9/28 刚因"同源两张卡"删过一份（`留两份只会同屏重复`），不再新开"今日雷达"卡。
//  【为什么默认不请求 · 三条约束】
//    ① **展开才请求**：它是**同步 LLM 调用**。后端 **按日落库缓存** ⇒
//       `refresh=false` 时：**同日已分析过 ⇒ 直接返回缓存（零成本）**；
//       **当日尚未分析过 ⇒ 仍会真调一次 LLM**（约十几秒）。
//       ⇒ 折叠默认 + 展开才请求，避免"每次开页面都烧一次"；点「重新分析」= 强制重算。
//    ② **明示输入规模**：`items_used` = 喂给 LLM 的提示条数 —— 结论可信度取决于喂了什么。
//    ③ **失败只占一行**：不拖垮时间线本体（与 `pushConnErr` 同一条纪律）。
//  ⚠️ 清空时机：**换日/回放切日时清**（见 `resetRadar`）—— 结果按日缓存，留着会显示
//     **另一天**的结论；**刻意不放在 `loadPush` 里**，因为 `loadPush` 在 120s 轮询里，
//     放那儿会每 2 分钟把用户正在看的分析清空。
// ══════════════════════════════════════════════════════════════════════════
// ★ 2026-10-11（L3）：加 `conclusion` / `reasons` —— 后端已把结论与依据**分开下发**，
//   前端只按角色渲染（旧数据/未升级后端 ⇒ '' / [] ⇒ 自动退回文本通道，行为与 L2 一致）。
const radarAnalysis = ref({ loading: false, markdown: '', conclusion: '', reasons: [],
                            error: '', cached: false, items_used: 0 })
const radarOpen = ref(false)

function resetRadar() {
  radarAnalysis.value = { loading: false, markdown: '', conclusion: '', reasons: [],
                          error: '', cached: false, items_used: 0 }
}

async function toggleRadar() {
  radarOpen.value = !radarOpen.value
  // 展开时走 `refresh=false`：**当日已分析过 ⇒ 直接读后端缓存（免费）**；
  //   **当日尚未分析过 ⇒ 后端仍会真调一次 LLM**（约十几秒）—— 必须对用户说清，
  //   不能说成"展开一定免费"（首版文案就是这么写的，已自查修正）。
  if (radarOpen.value && !radarAnalysis.value.markdown && !radarAnalysis.value.loading) {
    await loadRadarAnalysis(false)
  }
}

async function loadRadarAnalysis(refresh = false) {
  if (radarAnalysis.value.loading) return
  radarAnalysis.value = { ...radarAnalysis.value, loading: true, error: '' }
  // 回放时按所选日期取（后端按日缓存）；实时传 null ⇒ 后端用"今天"
  const day = selectedDate.value !== todayStr ? selectedDate.value : null
  try {
    const { data } = await getRadarAnalysis(day, refresh)
    radarAnalysis.value = {
      loading: false, markdown: data?.markdown || '', error: data?.error || '',
      conclusion: data?.conclusion || '', reasons: data?.reasons || [],
      cached: !!data?.cached, items_used: data?.items_used || 0,
    }
  } catch (e) {
    radarAnalysis.value = {
      loading: false, markdown: '', conclusion: '', reasons: [], cached: false, items_used: 0,
      error: '分析失败：' + (e?.response?.data?.detail || e?.message || e),
    }
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
// ★ 2026-09-25 需求 3：尾盘承接（14:30 基线 → 现在/收盘）。⚠️ **刻意不用当日缓存** ——
//   它在盘中是持续变化的（14:30 后每分钟都可能不同），缓存会锁死第一次的值。
//   后端很轻：未到 14:30 / 无基线时只读一行表就返回（零腾讯请求）。
async function loadTailReview() {
  try {
    const { data } = await getMarketTailReview(
      selectedDate.value !== todayStr ? selectedDate.value : undefined)
    tailReview.value = data || null
  } catch { tailReview.value = null }
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
// ★ 2026-10-23：`d_state_type` 可能带一长串括注（…VIX 19.387…）⇒ 只显示标签，
//   全文放 title；否则会把内联卡撑破（用户实测）。
function shortDiagType(s) {
  const t = String(s || '').split(/[（(]/)[0].trim()
  if (!t) return '不适用'
  return t.length > 14 ? `${t.slice(0, 14)}…` : t
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
// ★ 2026-09-29（P1）：日历前瞻（未来 3 天核心事件）——复用 `_dayCached` 日缓存
//   （日历每天更新一次，同日内重复拉无意义）。
async function loadCalendarAhead() {
  const cday = selectedDate.value
  if (_dayCached('cal_ahead', cday)) return
  try {
    const { data } = await getCalendarAhead(3)
    calendarAhead.value = (data && data.items && data.items.length) ? data : null
    if (calendarAhead.value) _dayMark('cal_ahead', cday)
  } catch { calendarAhead.value = null }
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
    macroFuturesBasis.value = (data && data.futures_basis) || null   // ★ 股指期货基差（P2）
    // ★ 2026-09-28（方案 A）：**当天没有"早盘锁定快照"时**，顺手用这一份 `/macro/snapshot`
    //   补 `direction` / `tags_*` —— 顶栏「宏观」摘要要用。这样就不必为摘要再单独发一次
    //   一模一样的外部请求（该接口对 A 股是**外部源**，能省则省）。
    //   ⚠️ 有锁定快照时**绝不动它**（锁定口径更权威，见 `loadMacro`）。
    if (!macro.value || !macro.value.locked) {
      macro.value = {
        direction: (data && data.direction) || null,
        tags_bull: (data && data.tags_bull) || [],
        tags_bear: (data && data.tags_bear) || [],
        generated_at: (data && (data.data_time || data.generated_at)) || null,
        locked: false,
      }
    }
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
// ★ 2026-09-29（P1）：盘中实时涨停/炸板（rt_k 全市场 5569 只精确口径）。
//   失败静默（该块是补充信息，不能拖垮整卡）；60s 轮询、仅交易时段真正发请求。
async function loadRealtimeUplimit() {
  try {
    const { data } = await getRealtimeUplimit()
    realtimeUplimit.value = data?.available ? data : null
  } catch { realtimeUplimit.value = null }
}
// ★ 2026-09-29：两市成交额环比（U 型曲线法）——失败静默；60s 轮询、仅交易时段发请求。
async function loadAmountRt() {
  try {
    const { data } = await getAmountRealtime()
    amountRt.value = data?.available ? data : null
  } catch { amountRt.value = null }
}
// ★ 2026-10-26（G2 轮询合并）：盘中两块（实时涨停/炸板 + 两市成交额环比）**一次请求拿回**。
//   【为什么能合】同门控（仅 `isLivePhase()`）、同 60s 节奏、都只读后端进程缓存
//     ⇒ 服务端外部请求**一次都不多**，只是把浏览器两次往返压成一次。
//   【失败语义】按块降级：哪块 `available=false` 就把哪块置空（与各自旧实现一致），
//     网络整体失败时**两块都保留上一次值**（不清空、不互相拖累）。
//   ⚠️ 首次加载仍走 `loadRealtimeUplimit()` / `loadAmountRt()`（阶段加载路径不变，互不影响）。
async function loadIntradayLite() {
  try {
    const { data } = await getIntradayLite()
    if (data && data.uplimit) {
      realtimeUplimit.value = data.uplimit.available ? data.uplimit : null
    }
    if (data && data.amount) {
      amountRt.value = data.amount.available ? data.amount : null
    }
  } catch (e) {
    console.warn('[workbench] 盘中轻量加载失败（保留上次）', e?.message || e)
  }
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
  const live = selectedPhase.value === 'intraday' || selectedPhase.value === 'midday'
  // ★★ 2026-09-30（P2 续）：**盘后/复盘优先 zzshare 当日快照**，不再先问东财。
  //   【为什么】① 东财 push2 盘后常被风控 ⇒ 实时接口失败；② 其回退源 `sector_daily`
  //     （东财快照）行数残缺且**与个股真值矛盾**（实测 09-29 只有 300/996 行，
  //     且称"房地产 -0.72%"而当日万科 **+9.97% 涨停**）；
  //   ③ zzshare 侧 P0 已修（日批每晚落库 + `task_data_gap` 自愈）⇒ 当日快照**完整
  //     104 板块**，且与「板块分化」页**同源同口径**。
  //   ⚠️ 代价：zzshare 快照**不含领涨股** ⇒ 盘后卡内不显示领涨股（如实标注来源）。
  if (!live) {
    try {
      const { data } = await getZzSectorSnapshot(selectedDate.value || todayStr,
                                                 { kind: 'industry' })
      const rows = ((data && data.data) || []).slice(0, 5)
      if (rows.length) {
        sectorTop.value = rows
        sectorTopSrc.value = 'zz'
        return
      }
    } catch { /* 落到东财实时 */ }
  }
  try {
    const { data } = await getSectorIndustry({ limit: 5 })
    const rows = (data && data.data) || []
    if (rows.length) {
      sectorTop.value = rows.slice(0, 5)
      sectorTopSrc.value = 'east'
      return
    }
  } catch { /* 落到本地聚合 */ }
  // ★★ 2026-10-28（用户："27 日盘中主线 Top5 不是实时的"）：
  //   东财 clist 盘中常被风控（实测 10-27 主站 + delay 全挂）⇒ 原来直接退到「快照回退」，
  //   而那是**昨天/更早**的收盘板块数据 ⇒ 盘中看着像"不实时"。
  //   这里先试**本地内存全市场聚合**：行业均值涨幅来自 `/market/amount-share`
  //   （读 `_cache` + `industry_map`，**零外部请求**，随行情缓存 120s 刷新）
  //   ⇒ 至少是"今天盘中"的行业强弱。
  //   ⚠️ 口径差异（必须如实标注）：本地是**等权平均**、49 个行业分类；
  //      东财是板块市值加权、90 板块；且本地**没有领涨股**（leader 留空）。
  try {
    const { data } = await getSectorAmountShare(10)
    // ★★ 2026-10-28（用户："可以"）：本地聚合**也能给领涨股**了 ——
    //   后端用 fuyao 涨停池的「涨停原因」按行业名词典反推（`data.leaders[行业]`），
    //   匹配不到就不标（宁可少标也不制造错误关联）。字段对齐东财分支的
    //   `leader/leader_code/leader_change_pct`，模板无需分叉。
    const leaders = (data && data.leaders) || {}
    const rows = ((data && data.rows) || [])
      .filter(r => r.avg_change_pct != null && (r.stock_n || 0) >= 3)
      .sort((a, b) => Number(b.avg_change_pct) - Number(a.avg_change_pct))
      .slice(0, 5)
      .map(r => {
        const ld = leaders[r.industry]
        return {
          name: r.industry,
          change_pct: r.avg_change_pct,
          leader: ld ? ld.name : null,
          leader_code: ld ? ld.code : null,
          leader_change_pct: ld ? ld.chg_pct : null,
          // ★ 2026-10-28：连板高度 —— 「首板」是**新启动**，「5 连板」是**人气高但接力风险也高**，
          //   两者含义差很远，混在一起看不出主线阶段。fuyao `continue_day_text` 直接给中文（"首板"/"2连板"）。
          leader_boards: ld ? (ld.boards || 1) : null,
          leader_boards_text: ld ? (ld.boards_text || '') : '',
        }
      })
    if (rows.length) {
      sectorTop.value = rows
      sectorTopSrc.value = 'local'
      return
    }
  } catch { /* 落到快照回退 */ }
  try {
    const { data } = await getSectorSnapshot(todayStr, { limit: 5 })
    sectorTop.value = ((data && data.data) || []).slice(0, 5)
    sectorTopSrc.value = 'snap'
  } catch { sectorTop.value = []; sectorTopSrc.value = '' }
}
// ★ 2026-09-25 需求 4：行业成交额占比（失败静默 ⇒ 整块不渲染，不影响板块卡其余部分）。
//   ⚠️ **刻意不用当日缓存**：资金在板块间的流动是盘中变量（120s 轮询刷新才有意义）。
async function loadAmountShare() {
  try {
    const { data } = await getSectorAmountShare(15)
    amountShare.value = data || null
  } catch { amountShare.value = null }
}
// ★ 2026-10-30（简报第1项）：组合风险档（滞胀交易）——独立加载。
//   ① 不放进 `loadMacro` 的 try：它失败不该让宏观卡显示"加载失败"；
//   ② 不受 `_dayCached('macroLocked')` 的早退影响：那是宏观快照的缓存，
//      状态位是另一张表，且查询很轻（单行）。
async function _loadStagflation() {
  try {
    const { data } = await getStagflation(selectedDate.value)
    stagflation.value = data || null
  } catch {
    stagflation.value = null
  }
}
// 宏观方向（早盘锁定快照优先，回退实时计算）——与 Dashboard.vue 同源同口径
async function loadMacro() {
  _loadStagflation()
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
// ★ 2026-09-28：`dirColor` / `levelColor` **已上移到 `composables/displayMeta`** ——
//   顶栏「宏观」摘要与 `MacroEnvCard`（子组件）都要用，留在页内就得复制一份 ⇒ 必然漂移。
//   本文件改为顶部 import（见 import 区），此处不再本地定义。
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
  const brief = { summary: '', dbPct: null, memMb: null }   // 顶栏摘要（见 statusBrief）
  try {
    const { data } = await getSystemStatus()
    // ★ 2026-10-28（用户："统一只认数据自身的 MAX(date)"）：圆点此前只看**接口有没有返回**
    //   （`!!data`）⇒ 只要后端活着就是绿的，即使 13 项里 5 项 stale 也照样绿。
    //   改为看**数据源判定**：任一非 ok 即非绿（浮层里逐项列出，避免"静默健康"）。
    const srcAll = data?.sources
    freshnessOk.value = !!data && !(Array.isArray(srcAll) && srcAll.some(v => v.status && v.status !== 'ok'))
    if (data?.generated_at) statusTime.value = String(data.generated_at).slice(11, 16)
    brief.summary = data?.summary || ''
    const src = data?.sources
    if (Array.isArray(src)) {
      const rows = src.slice(0, 12).map(v => {
        const st = v.status || '?'
        // ★ 2026-10-28：`suspect` = 今天跑过但数据没落库（后端 sync_meta 自检得出），
        //   与 stale（上游没给数据）成因相反、更严重 ⇒ 红色 + 独立文案。
        const cls = st === 'ok' ? 'text-emerald-400'
          : st === 'suspect' ? 'text-rose-400' : 'text-amber-400'
        const lag = st === 'suspect' ? '，今日已跑但数据未落库'
          : (v.lag_days != null && v.lag_days !== 0) ? `，滞后 ${v.lag_days} 天` : ''
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
      brief.memMb = data.rss_mb
      // ★ 2026-09-28：摘要里"内存只在 ≥75% 才亮"需要百分比 ⇒ 一并存下。
      brief.memPct = data.used_pct ?? null
      html += `<div class="mt-1">内存: ${data.rss_mb}MB（峰值 ${data.peak_mb ?? '—'}MB）· 占用 ${data.used_pct ?? '—'}%</div>`
    }
  } catch { /* 忽略 */ }
  try {
    const { data } = await getDbUsage()
    if (data && typeof data === 'object' && data.total_mb != null) {
      brief.dbPct = data.used_pct ?? null
      html += `<div>库体积: ${data.total_mb} / ${data.limit_mb ?? '—'} MB（已用 ${data.used_pct ?? '—'}%）</div>`
    } else if (data != null) {
      html += `<div>库体积: ${data} MB</div>`
    }
  } catch { /* 忽略 */ }
  statusBrief.value = brief
  statusHtml.value = html || '<div>—</div>'
}

// ── 阶段切换与懒加载 ──
async function loadPhaseData(phase) {
  if (isReplay.value) return
  if (phase === 'premarket') { await loadBrief('premarket'); await loadDecisionCard(); await Promise.all([loadMacro(), loadFlashDiag(), loadEmotion(), loadCalendarToday(), loadCalendarAhead(), loadSizing()]) }
  // ★ 2026-09-25：竞价段只拉"竞价相关"的（情绪快照里的 auction + 外盘）—— 零新增接口
  else if (phase === 'auction') { await Promise.all([loadEmotion(), loadGlobals()]) }
  // ★ 2026-09-25（盘后补强 · 缺口1）：盘后补拉**情绪快照 + 板块** —— 「今日盘面定稿」卡要用。
  //   原来只 `loadTop/loadGateWatch` ⇒ 15:00 的页面**完全没有"今天什么行情"**
  //   （日报要 22:16 才生成，zzshare 快照此刻还是**昨天的** ⇒ 都不能用）。
  //   ⚠️ `emotion` 走**内存行情**（收盘后不再变化 ⇒ 即收盘定稿），这是 15:00 唯一可用的今日口径。
  // ★ 同日（缺口2）：再补「今日执行」（按日精确的执行一致性）。
  // ★ 2026-09-30：**补 `loadBrief('postmarket')`** —— 「决策简报（盘后）」卡原先在本段
  //   **从不加载**（只有切到「复盘」段才拉）⇒ 该卡要么空着、要么显示盘前内容
  //   （`briefMd` 是多阶段共用的单个 ref）。这是顺带修掉的既有缺陷。
  // ★ 2026-09-30（P2 续）：补 `loadAmountShare()` —— 「行业成交额占比」原先**只在盘中/午盘**
  //   加载（见本函数末段）⇒ 盘后这张卡缺一半（成交占比块整块不渲染，而它正是"资金此刻
  //   集中在哪"的答案）。盘后该接口走 `tencent._cache` 的**收盘快照** ⇒ 数据是定稿口径。
  else if (phase === 'postmarket') { await loadTop(); await loadDefTop(); await loadDefGroup(); await loadGateWatch(); await loadEmotion(); await loadSectorTop(); await loadAmountShare(); await loadTodayExec(); await loadBrief('postmarket') }
  // ★ 2026-09-25 用户需求 2：复盘也拉仓位（含**组合周回撤**）—— 复盘正是检视
  //   "本周组合回撤了多少、是否该降仓"的时点。⚠️ 刻意**不加进盘中**：该接口会为每只
  //   持仓取一次历史 K 线（有缓存），盘中 120s 轮询没必要反复算慢变量。
  // ★ 2026-09-25（P1）：复盘也拉"待触发计划"（明日准备）—— 复盘正是"把今天结论转成明天待办"的时点
  // ★ 2026-09-30（P2 续）：复盘补「板块 Top5 + 成交占比」—— 复盘要看"今天什么行情、
  //   资金在哪个板块"，而这两块此前**只挂盘中** ⇒ 复盘时段整个板块卡是空的（切过来才残留旧值）。
  else if (phase === 'review') { await loadConsistency(); await loadEmotionReview(); await loadSizing(); await loadTailReview(); await loadBrief('postmarket'); await loadReport(todayStr); await loadPlans(); await loadSectorTop(); await loadAmountShare(); }
  if (phase === 'intraday' || phase === 'midday') {
    // ★ 2026-09-28：盘中/午盘也加载「评分榜 + 观察池」（原只在盘后拉 ⇒ 盘中两卡不显示）。
    //   ⚠️ 二者是**日批/盘后**数据（观察池 = 晚间日批快照）⇒ 盘中显示的是上一交易日；
    //      与"板块 Top5 走实时东财接口"不同。卡内 `data_date` 已标实际日期，不假装是今天。
    await Promise.all([loadEmotion(), loadLimitReview(), loadRealtimeUplimit(),
                       loadSectorTop(), loadGlobals(),
                       loadAmountShare(), loadTop(), loadDefTop(), loadDefGroup(), loadGateWatch()])
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
  // ★★ 2026-09-26 最终形态（用户两次否决后的结论）：**不要"页面自己等"，也不要"服务端轮询"**
  //   演进：① 页面每 5 分钟重拉（拉）→ 用户否决"不要让页面自己等"；
  //         ② 后端轮询三表齐了就推 → 用户否决"而不是轮询的方式"；
  //         ③ **现方案（事件驱动）**：`daily-batch.yml` 跑完 → `POST /api/system/batch-done`
  //            → 后端即推「日批完成 · 复盘已就绪」。
  //   判据 = 日报 / 评分快照 / 矛盾扫描三表**最新一行是否刚被写入**（`routers/system.py`，
  //   刻意**不看日期键** —— 本项目日期键有"运行日/交易日"两派，按日期判会在跨午夜日失效）。
  //   ⇒ 这是"**推**"而不是"拉"：页面不必轮询、不必一直开着，用户收到通知再打开即可。
  //   ⚠️ 此处**刻意不加**自动重试定时器 —— 保留本注释是防止日后有人"好心补回来"
  //     （那会重新制造"开着页面等"的体验，且用户已明确否决）。
  timers.push(setInterval(() => { loadCoach(); loadRadar(); loadLidaxiao() }, 60000))
  //   ↑ ★ 2026-10-25：李大霄视角卡并入教练卡 60s 轮询（用户拍板"跟教练一起轮训"）——
  //     接口只读小表前 15 行（~3KB/次），页面开一整天也不构成 egress 负担。
  // ★ 2026-09-29（P1）：盘中实时涨停/炸板 60s 轮询（与后端 rt_k 预热 120s 错频，
  //   一半会命中同一份后端缓存，无额外外部请求）——⚠️ 仅交易时段真发请求。
  // ★★ 2026-10-26（G2 + G3）：**两条 60s 轮询合并为一条** —— 涨停/炸板 + 成交额环比
  //   同门控同节奏，原先各占一个 ticker、各打一个接口（每分钟 2 次往返）；
  //   现在一个 ticker 打 `/market/intraday-lite`（1 次往返），服务端外部请求不变。
  //   失败按块降级（见 `loadIntradayLite`），保留旧值。
  timers.push(setInterval(() => { if (isLivePhase()) loadIntradayLite() }, 60000))
  // ★★ 2026-10-26（第④步）：**行情快组 60s** —— 只刷 `loadPulse()`（5 块同源行情）。
  //   · 上游全读后端内存缓存 ⇒ **外部请求不增加**（东财/外盘仍留在 120s 慢组）；
  //   · 跨标签用 localStorage 租约，多开也只让一个标签真正发请求（见 `loadPulse`）；
  //   · 后台标签不发（`document.hidden`）。
  timers.push(setInterval(() => { if (isLivePhase()) loadPulse() }, FAST_SEC * 1000))
  // ★ 2026-10-22：盘中变化检测器 5 分钟刷一次（采样本身 30 分钟一档；
  //   5 分钟足够让新档出现后不久可见，且只读 2 个小查询，不构成 egress 负担）。
  timers.push(setInterval(() => { if (isLivePhase()) loadWorkbenchScan() }, 300000))
  timers.push(setInterval(() => loadPush(todayStr), 120000))
  // ★ 2026-09-25（竞价段评估 #2）：竞价窗口只有 10 分钟（9:15-9:25），而主轮询是 120s
  //   ⇒ 最多刷 5 次，**9:24→9:25 的定稿瞬间可能滞后 ≤2 分钟**（那是最关键的几十秒）。
  //   ⇒ 竞价时段单独加快到 **30s**；⚠️ 只在窗口内真正发请求（`computePhase()` 是纯函数，
  //     其余时段空转 ⇒ 零成本、零额外流量）。
  timers.push(setInterval(() => {
    if (computePhase() === 'auction') loadEmotion()
  }, 30000))
  timers.push(setInterval(() => {
    // ★ 2026-09-25 用户再次明确：**不要自动流转**。
    //   此处原先有「P0-7 阶段自动流转」—— 阶段推进就 `selectedPhase = livePhase`
    //   把主工作区**切走**（注释说是为修"主体停在盘前、顶栏已是盘中"的混搭）。
    //   ⚠️ 但用户裁定：自动切换会打断正在读的内容（盘前简报读到一半被切走），
    //   宁可自己点。⇒ **只更新 livePhase（供时间轴上的温和提示用）**，
    //   主工作区**永不自动切换**；提示见模板里的「● 已进入，点击切换」。
    //   （此前 dbdb3cf 声称已"回退温和提示"，但这段 P0-7 实际还在 ⇒ 本次真正移除。）
    livePhase.value = computePhase()
    // ★ 2026-10-24（BK-55）：主轮询按变量时效分两组——
    //   夜间（!isLivePhase）只保留 **24h 变量**：外盘四件套 loadGlobals()（美股时段
    //   21:30-04:00 正在走）与快讯诊断 loadFlashDiag()（金十夜间照发，尤其美联储窗口）；
    //   A股变量（overview/temperature/emotion/tailReview/policyBottom/sectorTop/
    //   amountShare/events/gwPrices）夜间不会变 ⇒ 停轮询（挂载时的首次加载不受影响）。
    //   旧实现十余个加载器整夜每 2 分钟空转（"页面开一天"的 egress 白付），
    //   与本组上方自注释"仅交易时段真发请求"不符。
    loadGlobals(); loadFlashDiag()
    if (!isLivePhase()) { refreshLeft.value = 0; return }
    // ★★ 2026-10-26（第④步）：`loadPulse()` **已上移到 60s 快组**（见上方 ticker），
    //   本组（120s）只留"变化慢/外部源"的项 ⇒ 行情新鲜度翻倍，而东财与外盘不增频。
    loadTailReview()
    // ★ 2026-10-27：市场底清单已移出 120s 组（日频数据，改为 300s，见上方 ticker）
    // ★★ 2026-09-28（用户："主线板块 Top5 也不是实时的" —— 真因就在这里）：
    //   `loadSectorTop` 原先**只在切阶段时调一次**，切进盘中后就不再刷新
    //   ⇒ 页面上的板块排名停在"进入该阶段那一刻"（尽管接口本身是实时的）。
    //   纳入 120s 轮询即可实时；⚠️ 不会打爆东财：`eastmoney` 的板块列表有 **60s TTL 缓存**
    //   （`TTL_SECTOR=60`）⇒ 最多每 2 分钟一次真实请求，与原来切阶段的用法同量级。
    //   ⚠️ 它**不并入 pulse**：数据源是东财（外部），与"读内存缓存"那 5 块不是同源，
    //      合并会把东财的失败语义传染给整包。
    loadSectorTop()
    // ★ 2026-09-27：事件状态盘中会变（涨家数占比随行情走）⇒ 纳入 120s 轮询。
    //   ⚠️ 新增的刷新项必须加进本组，否则盘中静默不更新（项目约定）。
    loadEvents()
    // ★ 2026-09-28（用户："评分榜 Top10 …这样用户才知道他在实时刷新"）：
    //   让 120s 轮询**真的刷新评分榜卡片** —— 原先 `loadTop()` 只在切阶段时调用
    //   ⇒ 盘中两卡（评分榜 Top10 / 观察池）静默不更新，卡片上的"实时"名不副实。
    //   ⚠️ 只在**数据会变**的时段强制（`isLivePhase()`）：非交易时段白跑一次
    //      全市场精算没意义（本地模式下 `computeRanking` 很重）。
    //   ⚠️ 也不并入 pulse：评分榜是计算密集项（服务端 3 分钟缓存），与行情块不同性质。
    loadTop(isLivePhase())
    refreshLeft.value = isLivePhase() ? FAST_SEC : 0
  }, POLL_SEC * 1000))
  // ★ 2026-09-28：倒计时 1s ticker（与上面 120s 轮询同起点注册 ⇒ 读数差 ≤1 tick）。
  //   非交易时段恒置 0 ⇒ 卡片隐藏倒计时（数据不变时显示倒计时是误导）。
  //   ★ 2026-10-26（第④步）：读数按**快组 60s**循环（行情每 60s 刷一次）。
  timers.push(setInterval(() => {
    if (!isLivePhase()) { refreshLeft.value = 0; return }
    refreshLeft.value = refreshLeft.value > 1 ? refreshLeft.value - 1 : FAST_SEC
  }, 1000))
}
function stopPolling() { timers.forEach(clearInterval); timers = [] }

// ── 日期切换（回放入口）──
watch(selectedDate, async (d) => {
  syncQuery()
  // ★ 2026-09-30：换日（含回放切日）时清掉自洽性审查 —— 结果按日缓存，留着会显示
  //   **另一天**的结论；若当前是展开态则顺手按新日期重读（走缓存，不消耗 LLM）。
  resetRadar()
  if (radarOpen.value) loadRadarAnalysis(false)
  if (d && d !== todayStr) {
    stopPolling()
    await Promise.all([loadReplayDay(d), loadPush(d)])   // 回放右栏也切当日推送
  } else {
    await Promise.all([loadCoach(), loadPush(todayStr)])
    startPolling()
    loadPhaseData(selectedPhase.value)
  }
})

// ★ 数据可信度（供模板标注各区块的验证状态；失败静默不影响主流程）
const { load: loadVer, statusOf: verStatus } = useVerification()

onMounted(async () => {
  await loadDayIndex()
  loadStatus()   // 顶栏新鲜度灯的数据源（失败自动置黄灯）
  loadVer()      // 数据可信度总表（幂等，失败不影响主流程）
  // ★ 2026-09-28：初始化前端本地评分引擎（评分榜「本地优先」需要）。
  //   与榜单页共用**同一个 IndexedDB**（K 线包/指标包）⇒ 用户已在榜单页下过数据则秒就绪；
  //   未下过 ⇒ `dbReady` 股票数为 0 ⇒ `loadTop()` 自动回退后端接口（不会卡住页面）。
  //   ⚠️ **不 await**：init 内含"后台静默更新"，await 会拖慢首屏（与榜单页同款）。
  //   ⚠️ init 完成后若已可用且当前是本地模式，**补刷一次**评分榜 —— 否则首次 loadTop
  //      可能早于 dbReady 而回了后端（用户会看到"后端批次"而非"本地实时"）。
  initFrontendScoring().then(() => {
    if (useFrontendMode.value && feDbReady.value && topMode.value !== 'local') loadTop()
  }).catch((e) => console.warn('[workbench] 本地评分初始化失败:', e))
  // ★ 外盘四件套已移顶栏常驻（所有阶段可见）——挂载即加载 + 120s 轮询刷新
  loadGlobals()
  // ★ 2026-09-28（方案 A/C）：顶栏「宏观」摘要与右栏「快讯诊断」要**全天常驻** ⇒
  //   挂载即拉一次（原先只在 `loadPhaseData('premarket')` 里拉 ⇒ 当天没进过盘前就永远是空的）。
  //   `loadMacro` 有"今日锁定"当日缓存（拿到锁定快照后不再重拉）；`loadSizing` 是仓位建议。
  loadMacro(); loadFlashDiag(); loadSizing()
  // ★ 2026-09-29：实时涨停**全天常驻**（与宏观摘要同理）——盘前显示的是上一交易日
  //   收盘定稿口径（后端 `is_intraday=false`），盘中为此刻动态值 ⇒ 任何时段都有意义。
  await Promise.all([loadOverview(), loadTemperature(), loadRegime(), loadEvents(), loadCoach(), loadRadar(), loadPush(todayStr), loadRealtimeUplimit(), loadCalendarAhead(), loadAmountRt(), loadPolicyBottom(), loadLidaxiao(), loadLiquidity(), loadWorkbenchScan(), loadRiskWatch(), loadScenario()])
  loadPhaseData(selectedPhase.value)
  startPolling()
  // 快讯共享源：与 <FlashBriefCard> 共用引用计数 ⇒ 仍只有一条 60s 轮询；
  // 这里 start 是为了「快讯卡被收起/尚未渲染」时持仓行的消息徽标也有数据。
  fb.start(selectedDate.value || '')
})
onUnmounted(() => { stopPolling(); fb.stop() })
</script>

<!-- ★ 2026-10-11（L1）：`.md-body` 样式已收拢到全局 `src/styles/md.css`（原先本文件有一份副本，
     与另外 4 个页面各自漂移）。新增"观点输出"请直接加 `md-body` 类 + `v-html="renderMd(...)"`。 -->

<style scoped>
/* 卡内 tab 横向滚动：细滚动条，避免右栏被撑高（2026-10-26 李大霄多视频 tab） */
.wb-hscroll::-webkit-scrollbar { height: 4px; }
.wb-hscroll::-webkit-scrollbar-thumb { background: rgba(255,255,255,.18); border-radius: 2px; }
.wb-hscroll { scrollbar-width: thin; }
</style>
