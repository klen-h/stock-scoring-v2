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
                  class="bg-background border border-border rounded px-2 py-1 text-xs">
            <option v-for="d in dayList" :key="d.date" :value="d.date">
              {{ d.date === todayStr ? '今天 ' + d.date : d.date }}
            </option>
          </select>
        </label>
        <button class="flex items-center gap-1.5 text-[10px] text-muted hover:text-gray-300 w-fit"
                @click.stop="statusOpen = !statusOpen">
          <span class="inline-block w-2 h-2 rounded-full"
                :class="freshnessOk ? 'bg-emerald-500' : 'bg-amber-500'"></span>
          系统状态
          <span v-if="statusBrief.summary" class="text-muted">· {{ statusBrief.summary }}</span>
          <span v-if="statusBrief.dbPct != null" class="text-muted">· 库 {{ statusBrief.dbPct }}%</span>
          <span v-if="statusBrief.memMb != null" class="text-muted">· 内存 {{ statusBrief.memMb }}MB</span>
          <span v-if="statusTime" class="text-muted">· 截至 {{ statusTime }}</span>
          <span class="text-muted">▾</span>
        </button>
        <div v-if="statusOpen" class="fixed inset-0 z-40" @click="statusOpen = false"></div>
        <div v-if="statusOpen"
             class="absolute left-0 top-full z-50 mt-1 w-[min(92vw,420px)] max-h-[70vh] overflow-auto
                    bg-card border border-border rounded-lg p-3 shadow-xl text-xs space-y-1"
             v-html="statusHtml"></div>
      </div>

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
          <div class="bg-card border border-indigo-500/40 rounded-lg p-4">
            <div class="flex items-center justify-between mb-2">
              <div class="text-sm font-semibold">竞价看板
                <span class="text-[10px] text-muted font-normal">（9:20 后不可撤单 · 9:25 定稿）</span></div>
              <span class="text-[10px] text-muted font-mono">{{ emotion?.as_of || '' }}</span>
            </div>
            <div v-if="!emotion" class="text-muted text-xs">—（加载失败）</div>
            <template v-else>
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
                       title="昨日涨停股今日**开盘缺口**的平均值 =（开盘价 / 昨收 − 1）。只反映竞价那一刻的接力意愿，不含盘中变化。">平均高开（缺口）</div>
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
              <div v-if="(emotion.auction?.top || []).length" class="flex flex-wrap gap-1.5 mt-2 pt-2 border-t border-border/40">
                <span class="text-[10px] text-muted w-full mb-0.5">昨日涨停股高开 Top5（接力视角）</span>
                <a v-for="g in emotion.auction.top" :key="g.code" :href="stockHref(g.code)" target="_blank"
                   class="px-1.5 py-0.5 rounded border border-border/60 font-mono text-xs hover:border-accent"
                   :class="g.gap_pct >= 0 ? 'text-red-400' : 'text-emerald-400'">
                  {{ g.name }} {{ signNum(g.gap_pct) }}%
                </a>
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
                      :title="`数据口径日 = ${emotion?.price_date || '—'}${isTodayPrice() ? '（即今天）' : '（休市/盘前，不是自然日「今天」）'}。涨停/跌停/连板高度均为该日；「昨涨停均」= ${prevLimitLabel()} 涨幅≥9.5% 的股票在${priceDayLabel()}的平均涨跌幅`">{{ isTodayPrice() ? '今日' : '口径 ' + mmdd(emotion?.price_date) }} · 涨停/连板为 ≥9.5% 近似</span>
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
          <!-- ★★ 2026-09-25 用户："信号×行业是战法扫描出来的吗？如果是，今日决策卡的战法内容
               也可以摘出来，剩余的部分跟隔夜与今日（财经日历）形成左右卡片。"
               ⇒ 确认：`signal_industry_cross` 读的就是 `strategy_results`（战法扫描）⇒ 同源。
               ⇒ 两列起点**上移到决策卡**（原先只从"信号×行业"起 ⇒ 决策卡仍全宽）：
                  左列＝决策卡（剩余：立场/做多少/错了/负面清单）+ 战法卡（战法内容+信号分布）
                  右列＝隔夜与今日（财经日历）+ 决策简报（盘前）
               · 断点 **2xl**：主区已被常驻右栏占 320px，xl 下每列仅 ~460px 太挤。
               · 两列**各自堆叠**（masonry）而非 grid 行对齐 —— 否则短列会留大片空洞。 -->
          <div class="grid grid-cols-1 2xl:grid-cols-2 gap-4 items-start">
          <div class="space-y-4 min-w-0">
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
                <span class="text-[10px] text-muted font-normal">（能不能做 · 为什么 · 信号分布）</span></div>
            </div>
            <!-- ① 做什么（白名单 + 候选）+ 回避 —— 从决策卡摘出 -->
            <div class="text-xs space-y-1">
              <div>
                <span class="text-muted">做什么：</span>
                <b>{{ (dc.do?.whitelist || []).join('、') || '无白名单战法（推送静默）' }}</b>
                <template v-if="(dc.do?.candidates || []).length">
                  · 候选 {{ dc.do.candidates.map(c => `${c.name} ${c.score}分`).join('、') }}
                </template>
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
          </div>
          </div>
        </template>

        <!-- 实时模式 · ② 盘中 / ③ 午盘（执行模式：大字、少文字）
             ★ 2026-09-25 用户要求：指数大字卡删除——与顶栏重复，指数保留顶栏常驻 -->
        <template v-else-if="selectedPhase === 'intraday' || selectedPhase === 'midday'">
          <!-- ★★ 2026-09-25（用户："同理，你调整一下盘中的布局" + "这个部分我想用一个长卡片展示"）：
               **① 看盘序 = 全宽长卡，不参与分栏** —— 它内部是 6 格横向指标 + 数行横向信息
               （大小盘风格 / 尾盘承接 / 昨日涨停表现 / 竞价 Top5），天然"横着读"
               ⇒ 挤进半列必然折行。②③④⑤ 才分栏（安排见下方注释）。 -->
          <!-- ★ A1 盘中看盘序（框架：指数→涨跌家数/涨跌停→成交额→情绪） -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div v-if="emotion && emotion.trading_day === false"
                 class="text-[11px] text-amber-400 mb-2">⚠ 今日休市——以下为最近交易日快照数据，非实时</div>
            <!-- ★ 2026-09-25 用户："这里可以加个上下边距" ⇒ 6 格指标区加 `py-3`（与下方
                 风格/尾盘/昨日涨停各行的呼吸感一致；它们各自带 border-t + mt-2 pt-2）。 -->
            <div class="grid grid-cols-3 md:grid-cols-6 gap-2 text-center py-3">
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
            <!-- ★★ 2026-09-25 需求 3（框架 C2「14:30 承接：回封/抢筹/跳水」）：
                 尾盘半小时的承接强度**决定是否持仓过夜** —— 走强=资金愿持股过夜；
                 走弱=有资金尾盘撤退（该减仓过夜）。对比 14:30 基线 vs 现在。
                 ⚠️ 基线需 14:30 时后端在线（由 intraday_alert_loop 落库）；无基线时整块不渲染。 -->
            <div v-if="tailReview?.available" class="border-t border-border/40 mt-2 pt-2 text-[11px]">
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
            <div v-if="emotion" class="text-[11px] text-muted mt-2 border-t border-border/40 pt-2">
              {{ prevLimitLabel() }}涨停 {{ emotion.prev_limit_count ?? '—' }} 只 · {{ priceDayLabel() }}平均表现
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

          <!-- ★★ 2026-09-25 分栏安排（① 看盘序已全宽，在分栏之外）：
                 · 左列 = **盘面结构**（② 涨停梯队与清单 ③ 主线板块 Top5）
                 · 右列 = **行动**（④ 待执行决策 ⑤ 持仓状态）
               与竞价看板同一切法逻辑（左"读"右"做"），但**不加竖线**（多张独立卡各有边框）。
               ⚠️ 断点 **xl**(1280)；两列各自 `space-y-4` 堆叠、不做行对齐 ——
                 ⑤ 持仓状态的高度随持仓只数变化，行对齐必在短列留大片空洞。 -->
          <div class="grid grid-cols-1 xl:grid-cols-2 gap-x-4 gap-y-4 items-start">
          <div class="min-w-0 space-y-4">
          <!-- ★ B2 涨停复盘（zzshare：连板梯队/涨停清单；匿名限流时占位）
               ★ 2026-09-25：优先读**已落库快照**（`zz_daily_snapshots`）—— 原实现每次直连
                 zzshare ⇒ 匿名受限/盘中易失败，而日批明明已落库一份完整 payload。 -->
          <div class="bg-card border border-border rounded-lg p-4">
            <div class="text-sm font-semibold mb-2">涨停梯队与清单
              <!-- ★ 2026-09-25：补**数据时点**。原来只写"读已落库快照"却**不说是哪天的**，
                   而最常见状态恰恰是"今天日批还没跑 ⇒ 退回昨日快照"
                   ⇒ 会被当成"今天的涨停数据"读（`stale`=双源都失败后的降级兜底）。 -->
              <span class="text-[10px] text-muted font-normal">
                （zzshare 口径 · {{ limitReview.source === 'snapshot' ? '读已落库快照' : '实时直连' }}
                <template v-if="limitReview.as_of">· 数据 <b class="font-mono">{{ limitReview.as_of }}</b></template>
                <span v-if="limitReview.stale" class="text-amber-400">· 上一交易日（今日快照未生成）</span>）</span></div>
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

          <!-- ★ 2026-09-25 用户："持仓状态放在涨停梯队与清单下面，主线板块 Top5 移到右边"
               ⇒ 左列 = ② 涨停梯队与清单 + ⑤ 持仓状态；右列 = ③ 主线板块 + ④ 待执行决策。
               ★ 本次**必须搬内容**（顺序变更无法用"选切点"实现）：先在 ② 之后**插入副本**，
                 再删除 ④ 之后的原块（**先插后删** ⇒ 中途失败也不会丢卡片）。 -->
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
                  <!-- ★ 2026-09-25：改用**共享** `PHASE_STYLE`。原实现只认 `signal`——
                       出货红 / 吸筹绿，**盘整、下跌、拉升、洗盘一律灰** ⇒ 六种阶段实际只呈现两色
                       （用户反馈"主力阶段颜色没生效"，这是真凶之一）。 -->
                  <span v-if="it.phase" class="px-1 rounded cursor-help"
                        :class="(MF_PHASE_STYLE[it.phase] || {}).cls || 'bg-white/5 text-muted'"
                        :title="(MF_PHASE_STYLE[it.phase] || {}).tip || ''">主力·{{ it.phase_cn || it.phase }}</span>
                  <span v-else-if="it.phase_cn" class="px-1 rounded text-muted">主力·{{ it.phase_cn }}</span>
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
          </div>
          <!-- 右列：**盘面参考 + 行动**（③ 主线板块 ④ 待执行决策）——
               ★ 无竖分割线：两边都是**独立卡片**（各带边框），再加一条线是重复装饰
                 （用户："盘中卡片与卡片之间就不需要用分割线了"）。 -->
          <div class="min-w-0 space-y-4">

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
                  <!-- ★ 2026-09-26（用户："主线板块 top5 加多一个股票代码，也跳到雪球"）：
                       后端 `/sector/industry` **本就返回 `leader_code`**（零后端改动），
                       补上代码并跳雪球 —— 与全站口径一致（名称→本地详情页、代码→雪球）。 -->
                  <a v-if="s.leader_code" :href="xqUrl(s.leader_code)" target="_blank" rel="noopener"
                     class="font-mono text-muted hover:text-accent">{{ s.leader_code }}</a>
                  <span class="font-mono" :class="pctClass(s.leader_change_pct)">
                    {{ signNum(s.leader_change_pct) }}%</span>
                </div>
              </div>
            </div>

            <!-- ★★ 2026-09-25 需求 4（框架「板块主线层 · 成交占比」）：
                 资金此刻**真金白银**集中在哪个板块 —— 与上面的"涨幅榜"互补
                 （涨得好 ≠ 成交额集中；一个真主线通常两者兼具）。
                 ⚠️ 数据源**独立于上面的东财接口**：走腾讯内存行情 + 落库行业映射自行聚合
                 ⇒ 东财被封时上面可能空白，而这块照常显示（这正是它更可靠的原因）。
                 ⚠️ 行业是**最细分子板块**（一票一行业、不重复计算）⇒ 只展示 Top + Top5 集中度。 -->
            <div v-if="amountShare?.available" class="border-t border-border/40 mt-3 pt-2">
              <div class="flex items-center justify-between mb-1.5 flex-wrap gap-2">
                <div class="text-xs font-semibold">行业成交额占比
                  <span class="text-[10px] text-muted font-normal">
                    （{{ amountShare.industry_n }} 个细分行业 · Top5 占 {{ amountShare.top5_share_pct }}%）</span>
                </div>
                <span class="text-[10px] text-muted cursor-help" :title="amountShare.note">
                  数据 {{ amountShare.as_of }}{{ amountShare.from_snapshot ? '（收盘快照）' : '' }}</span>
              </div>
              <div class="space-y-0.5 text-[11px]">
                <div v-for="(r, i) in amountShare.rows" :key="r.industry"
                     class="flex items-center gap-2 border-b border-border/30 py-0.5">
                  <span class="w-4 text-muted font-mono">{{ i + 1 }}</span>
                  <span class="font-semibold w-24 truncate" :title="r.industry">{{ r.industry }}</span>
                  <span class="flex-1 h-1.5 rounded bg-border/40 overflow-hidden min-w-[40px]">
                    <span class="block h-full bg-accent/70"
                          :style="{ width: barWShare(r.share_pct) }"></span>
                  </span>
                  <span class="font-mono text-gray-300 w-14 text-right">{{ r.share_pct }}%</span>
                  <span class="font-mono text-muted w-16 text-right">{{ r.amount_yi }}亿</span>
                  <span class="font-mono w-14 text-right" :class="pctClass(r.avg_change_pct)">
                    {{ signNum(r.avg_change_pct) }}%</span>
                </div>
              </div>
            </div>
          </div>

          <!-- ★ Phase 2：教练卡盘中镜像（右栏为主，此处直达） -->
          <div v-if="todoList.length" class="bg-card border border-amber-500/40 rounded-lg p-4">
            <div class="text-sm font-semibold mb-1">待执行决策（{{ todoList.length }}）</div>
            <TodoCard v-for="a in todoList" :key="a.id" :alert="a" @done="loadCoach" />
          </div>
          </div>
          </div>
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
              盘面定稿 {{ emotion ? '✓' : '未就绪' }}<template v-if="ovStyle?.as_of">（{{ ovStyle.as_of }}<template
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
                  v-if="ovStyle?.as_of"> · 行情 {{ ovStyle.as_of }}</template>）</span></div>
              <!-- ★ 2026-09-25 加固（用户问"15:00 真能拿到吗"）：**数据时刻必须取"数据自身时刻"**
                   `ovStyle.as_of`（后端来自 `_cache['data_ts']`），而不是 `emotion.as_of`
                   （那只是**计算时刻**）—— 两者在"进程重启后从收盘快照恢复"时会差一整天，
                   用错就是**谎报新鲜度**（后端注释里专门警告过这一点）。 -->
              <span v-if="ovStyle?.from_snapshot" class="text-[10px] text-amber-400"
                    title="行情缓存来自数据库收盘快照（进程刚重启或非交易时段不重新抓取）⇒ 这是**上一交易日**的数据，不是今天的">
                ⚠ 来自收盘快照（非实时）</span>
              <span v-else-if="emotion && emotion.trading_day === false"
                    class="text-[10px] text-amber-400">⚠ 非交易日（为最近交易日快照）</span>
            </div>
            <div class="grid grid-cols-3 md:grid-cols-6 gap-2 text-center py-3">
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
            <!-- 大小盘风格（决定"今天赚指数还是赚个股"，与盘后结算卡互为解释） -->
            <div v-if="ovStyle?.available" class="border-t border-border/40 pt-2 text-[11px]">
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
                <span class="text-[10px]">（含板块内领涨股）</span>
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
                    <b :class="pctClass(s.leader_change_pct)">{{ signNum(s.leader_change_pct) }}%</b>）</span>
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
                    <span class="text-muted font-mono">{{ (a.alert_time || '').slice(11, 16) }}</span>
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
          <div class="grid grid-cols-1 xl:grid-cols-2 gap-x-4 gap-y-4 items-stretch">
          <div class="min-w-0 h-full">
          <div class="bg-card border border-border rounded-lg p-4 h-full">
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
          </div>
          <div class="min-w-0">
          <!-- ★ 2026-09-25（P1 观察池实化）：原实现只渲染**一行**"候选 N 只，前三：A、B、C"，
               而后端返回的 `{code,name,ready,label,missing,phase_cn,strategies}` 与
               总览 `{regime,total,ready3,counts,strategy_hits,data_date}` **全都拿到了却没用**
               ⇒ 一张卡占位、几乎零信息。现在展开成列表 + 就绪度分布。
               ⚠️ 口径（后端注释）：ready≥2 = "主力有根据 + 不追高，只等市况/时机"的**候池**，
                  三绿是低频条件 ⇒ 池空属正常，不写成"没有机会"。 -->
          <div class="bg-card border border-border rounded-lg p-4 h-full">
            <div class="flex items-center justify-between mb-2 flex-wrap gap-2">
              <div class="text-sm font-semibold">观察池（买入闸门候选）
                <span class="text-[10px] text-muted font-normal">
                  （ready≥2 的"等时机"池<template v-if="gwMeta.regime"> · 市况 {{ gwMeta.regime }}</template><template
                  v-if="gwMeta.data_date"> · 数据 {{ gwMeta.data_date }}</template>）</span></div>
              <router-link target="_blank" to="/score" class="text-xs text-accent hover:underline">详情</router-link>
            </div>
            <div v-if="gwErr" class="text-muted text-xs">—（加载失败：{{ gwErr }}）</div>
            <template v-else-if="gwItems.length">
              <div class="text-[11px] text-muted mb-1.5">
                候选 <b class="text-gray-200 font-mono">{{ gwMeta.total || gwItems.length }}</b> 只 ·
                三绿 <b class="text-emerald-400 font-mono">{{ gwMeta.ready3 || 0 }}</b> 只
                <span v-if="gwMeta.counts" class="ml-1">
                  （分布 1绿 {{ gwMeta.counts[1] || 0 }} / 2绿 {{ gwMeta.counts[2] || 0 }} / 3绿 {{ gwMeta.counts[3] || 0 }}）</span>
                <span v-if="gwMeta.strategy_hits" class="ml-1">· 有战法信号 {{ gwMeta.strategy_hits }} 只</span>
              </div>
              <div class="space-y-1 text-xs">
                <div v-for="g in gwItems.slice(0, 8)" :key="g.code"
                     class="flex items-center gap-2 border-b border-border/40 py-1"
                     :title="g.hint || g.missing || ''">
                  <a :href="stockHref(g.code)" target="_blank"
                     class="font-semibold hover:text-accent truncate max-w-[88px]">{{ g.name || g.code }}</a>
                  <!-- ★ 2026-09-25 用户："点击代码跳到雪球" —— 与榜单页/持仓卡同口径
                       （名称 → 本地详情页，代码 → 雪球）。 -->
                  <a :href="xqUrl(g.code)" target="_blank" rel="noopener" title="雪球"
                     class="font-mono text-muted hover:text-accent shrink-0">{{ g.code }}</a>
                  <span class="px-1 rounded font-mono text-[10px] shrink-0"
                        :class="g.ready === 3 ? 'bg-emerald-500/15 text-emerald-400' : 'bg-amber-500/15 text-amber-300'">
                    {{ g.ready }}/3</span>
                  <span class="text-muted truncate">{{ g.label || g.missing || '' }}</span>
                  <!-- ★ 2026-09-25 用户："盘整、下跌的…跟现项目观察池的颜色逻辑一致"
                       ⇒ 主力阶段改用**共享** `PHASE_STYLE`（绿=机会/红=风险：吸筹绿、出货红、
                          拉升琥珀、洗盘青、下跌灰、盘整淡）—— 与榜单页持仓表逐字同一份。 -->
                  <span v-if="g.phase" class="px-1 rounded shrink-0 cursor-help"
                        :class="(MF_PHASE_STYLE[g.phase] || {}).cls || 'bg-white/5 text-muted'"
                        :title="(MF_PHASE_STYLE[g.phase] || {}).tip || ''">{{ g.phase_cn || g.phase }}</span>
                  <!-- ★ 同日：战法名改用**中文**（共享 `strategyShort`）——
                       未登记的战法回退英文名，不隐藏（否则新增战法没人发现漏登记）。 -->
                  <span v-for="(s, si) in (g.strategies || [])" :key="si"
                        class="ml-auto px-1 rounded bg-accent/10 text-accent border border-accent/30 text-[10px] shrink-0">
                    {{ strategyShort(s) }}</span>
                </div>
              </div>
              <div v-if="gwItems.length > 8" class="text-[10px] text-muted mt-1">
                仅列前 8 只（共 {{ gwItems.length }} 只，全部见榜单页）
              </div>
            </template>
            <div v-else class="text-muted text-xs">
              —（当前无 ready≥2 的候选。三绿是**低频**条件，平时池空属正常）
            </div>
          </div>
          </div>
          </div>
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

        <!-- ★ 2026-09-26（用户："将今日雷达摘要改为今日系统时间线，给个最大高度，溢出滚动"）：
             ① 名称与盘后主区那张**统一** —— 两处本来就是同一数据源（`push_log`，见上一轮问答）
                ⇒ 叫法一致后，"今日系统时间线"在右栏（全天常驻）与盘后（完整回看）指的是同一份东西；
                ⚠️ 回放历史日时标题**带日期**（原为「<日期> 推送」⇒ 现统一为「<日期> 系统时间线」），
                   否则"今日"二字会误导（回放的是历史日推送）。
             ② 由 `slice(0, 5)` 改为**全部** + `max-h-80 overflow-y-auto`
                ⇒ 右栏能看到完整时间线，但卡片高度**封顶 320px**（不会把整页撑长）；
                原先只显示 5 条，压根不会溢出 ⇒ "溢出滚动"的前提就是**放开条数**。 -->
        <div class="bg-card border border-border rounded-lg p-4">
          <div class="text-sm font-semibold mb-2">
            {{ isReplay ? selectedDate + ' 系统时间线' : '今日系统时间线' }}</div>
          <div v-if="pushConnErr" class="text-[10px] text-amber-400">连接中断（保留上次数据）</div>
          <div v-if="!pushItems.length" class="text-muted text-xs">—</div>
          <div v-else class="max-h-80 overflow-y-auto pr-1">
            <div v-for="(p, i) in pushItems" :key="i" class="text-xs border-b border-border/40 py-1.5">
              <div class="flex items-center gap-2">
                <span class="font-mono text-muted">{{ (p.ts || '').slice(11, 16) }}</span>
                <span class="font-semibold truncate">{{ p.title }}</span>
              </div>
              <div class="text-muted mt-0.5">{{ firstLine(p.content) }}</div>
            </div>
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
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MarkdownIt from 'markdown-it'
import TodoCard from '../components/workbench/TodoCard.vue'
// ★ 2026-09-25（用户："观察池…跟现项目观察池的颜色逻辑一致…战法名称用中文"）：
//   与榜单页**共用同一份**展示元数据（主力阶段配色 / 战法中文名 / 闸门就绪配色）。
//   ⚠️ 本文件已有一个同名 `PHASE_STYLE` —— 那是**时段**配色（盘前/竞价/盘中…），
//      语义完全不同 ⇒ 主力阶段配色必须**重命名导入**（`MF_PHASE_STYLE`），避免撞名。
import { PHASE_STYLE as MF_PHASE_STYLE, strategyShort } from '../composables/displayMeta'
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
  getMarketTailReview,
  // ★ 2026-09-25 修复：**这两个原本就没 import** —— `loadSectorTop` 里一直在用
  //   `getSectorSnapshot` 却从未导入 ⇒ `vite build` 不做未定义变量检查（ESM 下被当成全局
  //   变量，不报错），而运行时抛 `ReferenceError` 被 `try/catch` 静默吞掉
  //   ⇒ **板块卡一直显示"（当日无板块快照）"**，从构建与日志里都看不出来。
  //   A1 收尾时改用实时 `/sector/industry`（含领涨股）才发现 ⇒ 两个都补上。
  getSectorIndustry, getSectorSnapshot,
  getSectorAmountShare,
  // ★ 2026-09-25（P1「明日准备」）：待触发交易计划 —— 接口**早已存在**
  //   （`/user/plans`，含 status / plan_type / trigger_high_open / trigger_volume_break），
  //   此前工作台从未用过 ⇒ 复盘的结论没有被转成"明日待办"，一天的首尾没接上。
  getUserPlans,
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
// ★ 2026-09-25（P1 观察池实化）：原实现只取 `items`，**把总览字段全丢了**
//   （regime / total / ready3 / counts / data_date / strategy_hits 后端都返回了）
//   ⇒ 卡里只能写"候选 N 只"。现在一并存下来，卡上就能回答"池子多大、几个快出手了、哪天的数据"。
const gwMeta = ref({})
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
// ★ 2026-09-25 用户："底部的系统状态移到顶部的日期的下面，把简要信息展示一行，
//   点击再悬浮展示内容" ⇒ 顶栏常驻一行摘要（摘要字段在此聚合），点开才渲染完整 HTML。
const statusBrief = ref({ summary: '', dbPct: null, memMb: null })

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
  } catch (e) { gwErr.value = (e && e.message) || '未知错误' }
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
// ★ 2026-09-25 需求 4：行业成交额占比（失败静默 ⇒ 整块不渲染，不影响板块卡其余部分）。
//   ⚠️ **刻意不用当日缓存**：资金在板块间的流动是盘中变量（120s 轮询刷新才有意义）。
async function loadAmountShare() {
  try {
    const { data } = await getSectorAmountShare(15)
    amountShare.value = data || null
  } catch { amountShare.value = null }
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
  const brief = { summary: '', dbPct: null, memMb: null }   // 顶栏摘要（见 statusBrief）
  try {
    const { data } = await getSystemStatus()
    freshnessOk.value = !!data
    if (data?.generated_at) statusTime.value = String(data.generated_at).slice(11, 16)
    brief.summary = data?.summary || ''
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
      brief.memMb = data.rss_mb
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
  if (phase === 'premarket') { await loadBrief('premarket'); await loadDecisionCard(); await Promise.all([loadMacro(), loadFlashDiag(), loadEmotion(), loadCalendarToday(), loadSizing()]) }
  // ★ 2026-09-25：竞价段只拉"竞价相关"的（情绪快照里的 auction + 外盘）—— 零新增接口
  else if (phase === 'auction') { await Promise.all([loadEmotion(), loadGlobals()]) }
  // ★ 2026-09-25（盘后补强 · 缺口1）：盘后补拉**情绪快照 + 板块** —— 「今日盘面定稿」卡要用。
  //   原来只 `loadTop/loadGateWatch` ⇒ 15:00 的页面**完全没有"今天什么行情"**
  //   （日报要 22:16 才生成，zzshare 快照此刻还是**昨天的** ⇒ 都不能用）。
  //   ⚠️ `emotion` 走**内存行情**（收盘后不再变化 ⇒ 即收盘定稿），这是 15:00 唯一可用的今日口径。
  // ★ 同日（缺口2）：再补「今日执行」（按日精确的执行一致性）。
  else if (phase === 'postmarket') { await loadTop(); await loadGateWatch(); await loadEmotion(); await loadSectorTop(); await loadTodayExec(); }
  // ★ 2026-09-25 用户需求 2：复盘也拉仓位（含**组合周回撤**）—— 复盘正是检视
  //   "本周组合回撤了多少、是否该降仓"的时点。⚠️ 刻意**不加进盘中**：该接口会为每只
  //   持仓取一次历史 K 线（有缓存），盘中 120s 轮询没必要反复算慢变量。
  // ★ 2026-09-25（P1）：复盘也拉"待触发计划"（明日准备）—— 复盘正是"把今天结论转成明天待办"的时点
  else if (phase === 'review') { await loadConsistency(); await loadEmotionReview(); await loadSizing(); await loadTailReview(); await loadBrief('postmarket'); await loadReport(todayStr); await loadPlans(); }
  if (phase === 'intraday' || phase === 'midday') {
    await Promise.all([loadEmotion(), loadLimitReview(), loadSectorTop(), loadGlobals(),
                       loadAmountShare()])
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
  timers.push(setInterval(() => { loadCoach(); loadRadar() }, 60000))
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
    loadOverview(); loadTemperature(); loadEmotion(); loadGlobals(); loadTailReview()
    loadAmountShare()      // ★ 需求 4：资金在板块间的流动是盘中变量 ⇒ 纳入轮询
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
