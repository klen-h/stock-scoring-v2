<!--
================================================================================
【文件作用】做T 交易台（大页面，2026-11-15）
================================================================================
用户（2026-11-15）："做T 看板本身也需要开一个路由大页面。"

【与工作台右栏摘要卡的分工】
  · 右栏「做T 看板」= **摘要**（可行数 + 今日盈亏 + 前 3 只），一眼看完，不占版面；
  · 本页 `/t-trade`    = **操作台**（全市场清单 + 池子增删 + 成交明细 + 单只诊断）。

【数据来源】全部读库，零额外外部请求：
  盘后 `t_trade_scan_loop` 扫全市场 → `t_plans`（只存 feasible）
  盘中 `t_trade_loop` 读预案 + 实时价 → 触发推送 + 写 `t_trades`
  ⚠️ `plan_date` 是**上一交易日收盘**算的，页面必须显示，不能假装是盘中算的。

【池子 vs 自选】
  `t_watchlist`（本页管理）才是做T 的关注范围；
  `user_watchlist`（自选）**不参与** —— 自选是"想盯的票"，池是"能做T 的票"。
================================================================================
-->
<template>
  <div class="p-4 space-y-3 max-w-[1500px] mx-auto">
    <!-- ── 头部 ── -->
    <div class="flex items-start justify-between gap-3 flex-wrap">
      <div>
        <h1 class="text-lg font-semibold">做T 交易台</h1>
        <div class="text-xs text-muted mt-0.5">
          <span v-if="planDate">{{ planDate }} 预案</span>
          <span v-else>—</span>
          <span class="ml-2 opacity-70">（预案由上一交易日收盘计算；盘中只读不比价）</span>
        </div>
      </div>
      <div class="flex items-center gap-2">
        <button type="button" class="px-2.5 py-1 rounded text-xs border border-border
                                     text-muted hover:text-accent hover:border-accent/40
                                     transition-colors"
                :disabled="busy" @click="loadAll">
          {{ busy ? '刷新中…' : '刷新' }}
        </button>
        <button type="button" class="px-2.5 py-1 rounded text-xs border border-border
                                     text-muted hover:text-accent hover:border-accent/40
                                     transition-colors"
                :disabled="scanning" @click="rescan">
          {{ scanning ? '扫描中…' : '补跑扫描' }}
        </button>
        <!-- ★★ 2026-11-19（用户："池子系统通过挑选自动加入池"）：
             平时盘后扫描会自动跑；这里用于立即重选（不改手动加的票）。 -->
        <button type="button" class="px-2.5 py-1 rounded text-xs border border-border
                                     text-violet-300 hover:border-violet-400/40
                                     transition-colors"
                :disabled="autoPicking"
                title="按当日可行预案的「空间/ATR」自动挑前 N 只进池；只替换自动挑的行"
                @click="autoPick">
          {{ autoPicking ? '挑选中…' : '自动选池' }}
        </button>
        <!-- ★ 2026-11-15（用户："交易信息会通知到我吗？"）：
             做T 走 `notify("coach")`，但历史上从没触发过该分类
             ⇒ 加个自检按钮，别等真信号来了才发现没收到。 -->
        <button type="button" class="px-2.5 py-1 rounded text-xs border border-border
                                     text-muted hover:text-accent hover:border-accent/40
                                     transition-colors"
                :disabled="pushTesting" @click="testPush">
          {{ pushTesting ? '发送中…' : '测试推送' }}
        </button>
      </div>
    </div>

    <div v-if="err" class="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/30
                            rounded-lg px-3 py-2">{{ err }}</div>
    <!-- 成功提示（测试推送等）；与 err 分开，否则"已发送"会用红色显示 -->
    <div v-if="notice" class="text-xs text-emerald-300 bg-emerald-500/10
                              border border-emerald-500/30 rounded-lg px-3 py-2">{{ notice }}</div>

    <!-- ── 概览 ── -->
    <div class="grid grid-cols-2 md:grid-cols-4 gap-2">
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-[11px] text-muted">全市场可行</div>
        <div class="text-xl font-semibold mt-0.5">{{ marketTotal }}</div>
        <div class="text-[10px] text-muted">当前市况能做T 的标的数</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <!-- ★ 2026-11-16：这里原叫"做T 池"，现在口径是**完整监控范围**（持仓 + 池） -->
        <div class="text-[11px] text-muted">监控范围</div>
        <div class="text-xl font-semibold mt-0.5">{{ poolList.length }}</div>
        <div class="text-[10px] text-muted">
          持仓 + 池 · 预案可行 <b class="text-accent">{{ poolPlans.length }}</b> 只
          <!-- ★ 可下单 ≠ 预案可行：无底仓一律不能做T，必须分开显示 -->
          <template v-if="canTradeCount !== poolPlans.length">
            · 可下单 <b class="text-emerald-300">{{ canTradeCount }}</b> 只
          </template>
        </div>
        <!-- ★ 2026-11-16：模拟底仓（自动建仓）—— 与真实持仓分开计数，
             否则"监控范围"会把模拟仓也算成你的持仓 -->
        <div v-if="(simInfo && simInfo.total) || (simInfo && simInfo.auto_build)"
             class="text-[10px] mt-0.5">
          <span v-if="simInfo.total" class="text-amber-300">模拟仓 {{ simInfo.total }}</span>
          <span v-if="simInfo.auto_build" class="text-muted"
                title="建仓点命中后自动建模拟底仓（不真实下单），随即自动做T">· 自动建仓 开</span>
          <span v-else class="text-muted" title="T_AUTO_BUILD=0">· 自动建仓 关</span>
        </div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-[11px] text-muted">今日成交</div>
        <div class="text-xl font-semibold mt-0.5">{{ stats.trades || 0 }}</div>
        <div class="text-[10px] text-muted">已开 {{ stats.open || 0 }} · 已平 {{ stats.closed || 0 }}</div>
      </div>
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-[11px] text-muted cursor-help"
             title="已扣佣金（每笔最低 5 元）+ 印花税 + 过户费">今日盈亏（模拟·净）</div>
        <div class="text-xl font-semibold mt-0.5 font-mono"
             :class="Number(stats.pnl) >= 0 ? 'text-rose-300' : 'text-emerald-300'">
          {{ Number(stats.pnl) > 0 ? '+' : '' }}{{ stats.pnl || 0 }}
        </div>
        <div class="text-[10px] text-muted">
          <span v-if="stats.win_rate_pct != null">胜率 {{ stats.win_rate_pct }}%</span>
          <span v-else>—</span>
        </div>
      </div>
    </div>

    <!-- ── Tab ── -->
    <div class="flex items-center gap-1 border-b border-border pb-1">
      <button v-for="t in tabs" :key="t.key" type="button"
              class="px-3 py-1 rounded text-xs transition-colors"
              :class="tab === t.key ? 'bg-accent/20 text-accent' : 'text-muted hover:text-gray-200'"
              @click="tab = t.key">
        {{ t.label }}
        <span v-if="t.key === 'pool' && poolList.length" class="opacity-60">({{ poolList.length }})</span>
        <span v-if="t.key === 'market'" class="opacity-60">({{ marketTotal }})</span>
      </button>
    </div>

    <!-- ── ① 我的池子 ── -->
    <div v-show="tab === 'pool'" class="bg-card border border-border rounded-lg overflow-hidden">
      <!-- ★ 2026-11-15（用户："按你建议来" ⇒ 清掉 19 只无底仓的票）：
           空态必须说清"持仓不需要进池"，否则用户会以为池空 = 没有票被监控。 -->
      <!-- ★ 2026-11-16：`poolList` 现在**含持仓** ⇒ 只有"既无持仓又无池"才走到这里 -->
      <div v-if="!poolList.length" class="p-4 text-xs text-muted space-y-1.5">
        <div>既没有持仓、也没有手动加入的标的 —— 做T 目前**没有任何监控对象**。</div>
        <div>
          <b class="text-gray-300">有持仓就会自动出现在这里</b>，不需要手动加池
          （监控范围 = <b>持仓 + 池</b>）。
        </div>
        <div>
          池子放的是「还没建仓、先观察」的票 ——
          但<span class="text-amber-300">做T 必须有底仓</span>（A 股 T+1），
          无底仓的票只会显示「无底仓」，永远不会触发。
        </div>
      </div>
      <table v-else class="w-full text-xs">
        <thead class="text-muted border-b border-border">
          <tr>
            <th class="text-left px-3 py-1.5">代码</th>
            <th class="text-left px-3 py-1.5">名称</th>
            <!-- ★ 2026-11-16（用户："池子不会自动加入吗？"）：
                 持仓**本来就在监控范围内**（`watch_codes = 持仓 + 池`），
                 只是原先表格不显示 ⇒ 池空时看起来像"什么都没盯"。 -->
            <th class="text-left px-3 py-1.5">来源</th>
            <th class="text-right px-3 py-1.5">现价</th>
            <th class="text-right px-3 py-1.5">涨跌</th>
            <th class="text-right px-3 py-1.5">量比</th>
            <th class="text-right px-3 py-1.5">换手</th>
            <th class="text-right px-3 py-1.5">支撑</th>
            <th class="text-right px-3 py-1.5">阻力</th>
            <th class="text-right px-3 py-1.5">空间</th>
            <th class="text-right px-3 py-1.5">×ATR</th>
            <th class="text-right px-3 py-1.5">仓位</th>
            <th class="text-left px-3 py-1.5">状态</th>
            <th class="text-right px-3 py-1.5">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in poolRows" :key="r.code" class="border-b border-border/50 hover:bg-white/5">
            <!-- ★ 代码 → 雪球；名称 → 个股详情页（用户 2026-11-15 明确要求） -->
            <td class="px-3 py-1.5">
              <a :href="getXueqiuUrl(r.code)" target="_blank" rel="noopener"
                 class="font-mono hover:text-accent hover:underline" title="在雪球查看">{{ r.code }}</a>
            </td>
            <td class="px-3 py-1.5">
              <router-link :to="'/stock/' + r.code" class="hover:text-accent hover:underline"
                           :title="`查看 ${r.name || r.code} 详情`">{{ r.name || '—' }}</router-link>
            </td>
            <!-- 来源：持仓自动监控 / 手动加池 / 两者都有 -->
            <td class="px-3 py-1.5">
              <span v-if="r.source === 'holding'" class="text-[10px] text-cyan-300"
                    title="持仓自动纳入监控，不需要加进池">持仓</span>
              <span v-else-if="r.source === 'both'" class="text-[10px] text-cyan-300"
                    title="既是持仓，也在观察池里">持仓+池</span>
              <!-- ★ 2026-11-16（用户："价格到位，系统自动处理"）：
                   建仓点命中后系统自动建的是**模拟底仓**（不真实下单）⇒
                   必须与真实持仓在视觉上分开，否则用户会以为已经买进了。 -->
              <span v-else-if="r.source === 'sim'" class="text-[10px] text-amber-300"
                    title="建仓点触发后系统自动建的模拟底仓（未真实下单）；真实买入后可清除它">模拟仓</span>
              <!-- ★ 2026-11-19（用户："池子系统通过挑选自动加入池"）：
                   必须能看出"这只票是系统挑的还是我加的" ——
                   自动挑的每天盘后会被替换，手动加的永远不会被自动删掉。 -->
              <span v-else-if="r.pool_source === 'auto'" class="text-[10px] text-violet-300"
                    title="系统按当日「空间/ATR」自动挑选，盘后扫描时刷新">自动挑</span>
              <span v-else class="text-[10px] text-muted"
                    title="你手动加入的观察标的（自动选池不会删它）">手动</span>
            </td>
            <td class="px-3 py-1.5 text-right font-mono">{{ fmt(r.price) }}</td>
            <td class="px-3 py-1.5 text-right font-mono" :class="chgClass(r.change_pct)">{{ fmtChg(r.change_pct) }}</td>
            <td class="px-3 py-1.5 text-right font-mono">{{ fmtVol(r.vol_ratio) }}</td>
            <td class="px-3 py-1.5 text-right font-mono">{{ r.turnover_pct != null ? Number(r.turnover_pct).toFixed(2) + '%' : '—' }}</td>
            <td class="px-3 py-1.5 text-right font-mono" :title="r.support_type">{{ fmt(r.support) }}</td>
            <td class="px-3 py-1.5 text-right font-mono" :title="r.resistance_type">{{ fmt(r.resistance) }}</td>
            <td class="px-3 py-1.5 text-right font-mono">{{ r.space_pct != null ? Number(r.space_pct).toFixed(2) + '%' : '—' }}</td>
            <td class="px-3 py-1.5 text-right font-mono">{{ r.space_atr_mult != null ? Number(r.space_atr_mult).toFixed(2) : '—' }}</td>
            <td class="px-3 py-1.5 text-right font-mono">{{ r.ratio != null ? Math.round(Number(r.ratio) * 100) + '%' : '—' }}</td>
            <!-- ★ 2026-11-15（用户："要看下资金现状给不给买入"）：
                 三态必须分开 —— 混淆会让人误判处置方式：
                   今日可做 = 预案可行 **且** 底仓/资金够下单
                   仓位不足 = 预案可行但买不了（要么加仓要么换标的）
                   不可行   = 预案本身不行（等机会） -->
            <td class="px-3 py-1.5">
              <template v-if="r.feasible">
                <span v-if="r.buy_ok !== false" class="text-emerald-300">今日可做</span>
                <span v-else class="text-amber-300" :title="r.buy_reason">
                  {{ Number(r.base_shares) > 0 ? '仓位不足' : '无底仓' }}
                </span>
              </template>
              <span v-else class="text-muted" :title="r.reason">不可行</span>
            </td>
            <td class="px-3 py-1.5 text-right space-x-2 whitespace-nowrap">
              <!-- ★ 2026-11-15（用户："我的池子符合做T，可以加入某个支撑位买入底仓的功能吗？"）：
                   只对**可行 + 无底仓**的票显示 —— 有底仓的不需要建仓点；
                   不可行的票没有可信支撑位，不该让人照着买。
                   ⚠️ 这是**建仓**（新增敞口），不是做T（净仓位不变）。 -->
              <button v-if="r.feasible && !(Number(r.base_shares) > 0)"
                      type="button"
                      class="text-[11px] text-accent hover:underline"
                      :title="`用支撑 ${fmt(r.support)} 建底仓计划（止损 ${fmt((r.support || 0) - (r.atr || 0) * 0.5)}，目标 ${fmt(r.resistance)}）⚠️ 这是建仓不是做T`"
                      @click="makePlan(r.code)">设建仓点</button>
              <!-- ★ 模拟底仓：随时可清（真实买入后**必须清**，否则两层底仓并存） -->
              <button v-if="r.source === 'sim'" type="button"
                      class="text-[11px] text-amber-300 hover:underline"
                      title="清除模拟底仓。若你已真实买入，请先在持仓里录入，再清掉这里"
                      @click="clearSim(r.code)">清模拟仓</button>
              <!-- 仅持仓的行不在池里，"移除"无意义（它由持仓自动纳入） -->
              <button v-if="r.source !== 'holding'" type="button"
                      class="text-[11px] text-muted hover:text-rose-300"
                      :title="r.pool_source === 'auto'
                        ? '移除后本次不再显示；下次盘后自动选池若仍排在前 N 会重新加入'
                        : '移除你手动加的票（自动选池不会再加它）'"
                      @click="removeFromPool(r.code)">移除</button>
              <span v-else class="text-[11px] text-muted"
                    title="来自持仓，删掉持仓才会消失">自动</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- ── ② 全市场可行 ── -->
    <div v-show="tab === 'market'" class="space-y-2">
      <div class="flex items-center gap-2 text-xs text-muted">
        <input v-model.trim="kw" type="text" placeholder="按代码/名称筛选"
               class="px-2 py-1 rounded bg-transparent border border-border text-xs
                      text-gray-200 placeholder:text-muted focus:border-accent/50 outline-none w-52" />
        <span>共 {{ filteredMarket.length }} 只（按空间/ATR 降序）</span>
        <span class="opacity-70">· 已在池中 {{ inPoolCount }} 只</span>
      </div>
      <div class="bg-card border border-border rounded-lg overflow-hidden">
        <table class="w-full text-xs">
          <thead class="text-muted border-b border-border">
            <tr>
              <th class="text-left px-3 py-1.5">代码</th>
              <th class="text-left px-3 py-1.5">名称</th>
              <th class="text-right px-3 py-1.5">现价</th>
              <th class="text-right px-3 py-1.5">涨跌</th>
              <th class="text-right px-3 py-1.5">量比</th>
              <th class="text-right px-3 py-1.5">换手</th>
              <th class="text-right px-3 py-1.5">流通市值</th>
              <th class="text-right px-3 py-1.5">支撑</th>
              <th class="text-right px-3 py-1.5">阻力</th>
              <th class="text-right px-3 py-1.5">空间</th>
              <th class="text-right px-3 py-1.5">×ATR</th>
              <th class="text-right px-3 py-1.5">仓位</th>
              <th class="text-right px-3 py-1.5">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="r in pagedMarket" :key="r.code" class="border-b border-border/50 hover:bg-white/5">
              <td class="px-3 py-1.5">
                <a :href="getXueqiuUrl(r.code)" target="_blank" rel="noopener"
                   class="font-mono hover:text-accent hover:underline" title="在雪球查看">{{ r.code }}</a>
              </td>
              <td class="px-3 py-1.5">
                <router-link :to="'/stock/' + r.code" class="hover:text-accent hover:underline"
                             :title="`查看 ${r.name || r.code} 详情`">{{ r.name || '—' }}</router-link>
              </td>
              <td class="px-3 py-1.5 text-right font-mono">
                {{ quoteOf(r.code).price != null ? Number(quoteOf(r.code).price).toFixed(2) : fmt(r.price_ref) }}
              </td>
              <td class="px-3 py-1.5 text-right font-mono" :class="chgClass(quoteOf(r.code).change_pct)">
                {{ fmtChg(quoteOf(r.code).change_pct) }}
              </td>
              <td class="px-3 py-1.5 text-right font-mono">{{ fmtVol(r.vol_ratio) }}</td>
              <td class="px-3 py-1.5 text-right font-mono">{{ r.turnover_pct != null ? Number(r.turnover_pct).toFixed(2) + '%' : '—' }}</td>
              <td class="px-3 py-1.5 text-right font-mono">{{ r.float_cap_yi != null ? Number(r.float_cap_yi).toFixed(0) + '亿' : '—' }}</td>
              <td class="px-3 py-1.5 text-right font-mono" :title="r.support_type">{{ fmt(r.support) }}</td>
              <td class="px-3 py-1.5 text-right font-mono" :title="r.resistance_type">{{ fmt(r.resistance) }}</td>
              <td class="px-3 py-1.5 text-right font-mono">{{ Number(r.space_pct).toFixed(2) }}%</td>
              <td class="px-3 py-1.5 text-right font-mono">{{ Number(r.space_atr_mult || 0).toFixed(2) }}</td>
              <td class="px-3 py-1.5 text-right font-mono">{{ Math.round(Number(r.ratio || 0) * 100) }}%</td>
              <td class="px-3 py-1.5 text-right">
                <span v-if="poolCodes.has(r.code)" class="text-[11px] text-muted">已在池中</span>
                <button v-else type="button" class="text-[11px] text-accent hover:underline"
                        @click="addFromMarket(r)">加入池</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-if="filteredMarket.length > pageSize" class="flex items-center justify-center gap-2 text-xs text-muted">
        <button type="button" class="px-2 py-0.5 rounded border border-border hover:text-accent"
                :disabled="page === 0" @click="page--">上一页</button>
        <span>{{ page + 1 }} / {{ totalPages }}</span>
        <button type="button" class="px-2 py-0.5 rounded border border-border hover:text-accent"
                :disabled="page >= totalPages - 1" @click="page++">下一页</button>
      </div>
    </div>

    <!-- ── ③ 今日成交 ── -->
    <div v-show="tab === 'trades'" class="space-y-2">
      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs font-semibold mb-1.5">未平（盘中在盯）</div>
        <div v-if="!(summary && summary.open && summary.open.length)" class="text-[11px] text-muted">
          没有未平的T。
        </div>
        <table v-else class="w-full text-xs">
          <thead class="text-muted border-b border-border">
            <tr>
              <th class="text-left px-2 py-1">代码</th>
              <th class="text-left px-2 py-1">名称</th>
              <th class="text-left px-2 py-1">方向</th>
              <th class="text-right px-2 py-1">开仓价</th>
              <th class="text-right px-2 py-1">股数</th>
              <th class="text-left px-2 py-1">开仓时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in summary.open" :key="t.id" class="border-b border-border/50">
              <td class="px-2 py-1">
                <a :href="getXueqiuUrl(t.code)" target="_blank" rel="noopener"
                   class="font-mono hover:text-accent hover:underline" title="在雪球查看">{{ t.code }}</a>
              </td>
              <td class="px-2 py-1">
                <router-link :to="'/stock/' + t.code" class="hover:text-accent hover:underline">{{ t.name }}</router-link>
              </td>
              <td class="px-2 py-1">{{ t.direction === 'long_t' ? '正T（先买）' : '反T（先卖）' }}</td>
              <td class="px-2 py-1 text-right font-mono">{{ Number(t.open_price).toFixed(2) }}</td>
              <td class="px-2 py-1 text-right font-mono">{{ t.open_shares }}</td>
              <td class="px-2 py-1 text-muted">{{ t.open_time }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="bg-card border border-border rounded-lg p-3">
        <div class="text-xs font-semibold mb-1.5">已平</div>
        <div v-if="!(summary && summary.closed && summary.closed.length)" class="text-[11px] text-muted">
          今天还没有已平的T。
        </div>
        <table v-else class="w-full text-xs">
          <thead class="text-muted border-b border-border">
            <tr>
              <th class="text-left px-2 py-1">代码</th>
              <th class="text-left px-2 py-1">名称</th>
              <th class="text-left px-2 py-1">方向</th>
              <th class="text-right px-2 py-1">开仓</th>
              <th class="text-right px-2 py-1">平仓</th>
              <th class="text-right px-2 py-1">盈亏</th>
              <th class="text-left px-2 py-1">备注</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="t in summary.closed" :key="t.id" class="border-b border-border/50">
              <td class="px-2 py-1">
                <a :href="getXueqiuUrl(t.code)" target="_blank" rel="noopener"
                   class="font-mono hover:text-accent hover:underline" title="在雪球查看">{{ t.code }}</a>
              </td>
              <td class="px-2 py-1">
                <router-link :to="'/stock/' + t.code" class="hover:text-accent hover:underline">{{ t.name }}</router-link>
              </td>
              <td class="px-2 py-1">{{ t.direction === 'long_t' ? '正T' : '反T' }}</td>
              <td class="px-2 py-1 text-right font-mono">{{ Number(t.open_price).toFixed(2) }}</td>
              <td class="px-2 py-1 text-right font-mono">{{ Number(t.close_price || 0).toFixed(2) }}</td>
              <td class="px-2 py-1 text-right font-mono cursor-help"
                  :class="netOf(t) >= 0 ? 'text-rose-300' : 'text-emerald-300'"
                  :title="feeTitle(t)">
                {{ netOf(t) > 0 ? '+' : '' }}{{ netOf(t).toFixed(2) }}
                <span class="opacity-70">（{{ Number(t.pnl_pct || 0).toFixed(2) }}%）</span>
              </td>
              <td class="px-2 py-1 text-muted truncate max-w-[280px]" :title="t.note">{{ t.note }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- ── ④ 历史成交（跨日 + 累计收益） ── -->
    <div v-show="tab === 'history'" class="space-y-2">
      <div v-if="historyBusy"
           class="bg-card border border-border rounded-lg p-6 text-center text-[11px] text-muted">
        加载中…
      </div>
      <template v-else-if="historyData">
        <!-- 累计统计（只算已平，未平盈亏仍浮动） -->
        <div class="grid grid-cols-2 md:grid-cols-5 gap-2">
          <div class="bg-card border border-border rounded-lg p-3">
            <div class="text-[11px] text-muted cursor-help"
                 title="新记录已扣交易费用；历史记录（无 fee 列）显示的是毛利">累计盈亏（净）</div>
            <div class="text-lg font-semibold mt-0.5 font-mono"
                 :class="Number(historyData.stats.pnl) >= 0 ? 'text-rose-300' : 'text-emerald-300'">
              {{ Number(historyData.stats.pnl) > 0 ? '+' : '' }}{{ Number(historyData.stats.pnl || 0).toFixed(2) }}
            </div>
            <div class="text-[10px] text-muted">仅已平仓</div>
          </div>
          <div class="bg-card border border-border rounded-lg p-3">
            <div class="text-[11px] text-muted">笔数</div>
            <div class="text-lg font-semibold mt-0.5">{{ historyData.stats.trades }}</div>
            <div class="text-[10px] text-muted">已平 {{ historyData.stats.closed }} · 未平 {{ historyData.stats.open }}</div>
          </div>
          <div class="bg-card border border-border rounded-lg p-3">
            <div class="text-[11px] text-muted">胜率</div>
            <div class="text-lg font-semibold mt-0.5">
              {{ historyData.stats.win_rate_pct == null ? '—' : historyData.stats.win_rate_pct + '%' }}
            </div>
            <div class="text-[10px] text-muted">赢 {{ historyData.stats.win }} 笔</div>
          </div>
          <div class="bg-card border border-border rounded-lg p-3">
            <div class="text-[11px] text-muted">平均每笔</div>
            <div class="text-lg font-semibold mt-0.5 font-mono"
                 :class="Number(historyData.stats.avg_pnl || 0) >= 0 ? 'text-rose-300' : 'text-emerald-300'">
              {{ historyData.stats.avg_pnl == null
                  ? '—'
                  : (Number(historyData.stats.avg_pnl) > 0 ? '+' : '') + Number(historyData.stats.avg_pnl).toFixed(2) }}
            </div>
          </div>
          <div class="bg-card border border-border rounded-lg p-3">
            <div class="text-[11px] text-muted">涉及交易日</div>
            <div class="text-lg font-semibold mt-0.5">{{ historyData.stats.days }}</div>
          </div>
        </div>

        <!-- 明细 -->
        <div class="bg-card border border-border rounded-lg p-3">
          <div class="flex items-center justify-between mb-1.5">
            <div class="text-xs font-semibold">成交明细</div>
            <div class="text-[10px] text-muted">
              {{ historyData.scope }} · 最近 {{ historyData.data.length }} 笔
            </div>
          </div>
          <div v-if="!historyData.data.length" class="text-[11px] text-muted py-4 text-center">
            还没有任何做T 记录。
          </div>
          <table v-else class="w-full text-xs">
            <thead class="text-muted border-b border-border">
              <tr>
                <th class="text-left px-2 py-1">日期</th>
                <th class="text-left px-2 py-1">代码</th>
                <th class="text-left px-2 py-1">名称</th>
                <th class="text-left px-2 py-1">方向</th>
                <th class="text-right px-2 py-1">开仓</th>
                <th class="text-right px-2 py-1">平仓</th>
                <th class="text-right px-2 py-1">股数</th>
                <th class="text-right px-2 py-1">盈亏</th>
                <th class="text-left px-2 py-1">状态</th>
                <th class="text-left px-2 py-1">备注</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="t in historyData.data" :key="t.id" class="border-b border-border/50">
                <td class="px-2 py-1 text-muted font-mono">{{ t.trade_date }}</td>
                <td class="px-2 py-1">
                  <a :href="getXueqiuUrl(t.code)" target="_blank" rel="noopener"
                     class="font-mono hover:text-accent hover:underline" title="在雪球查看">{{ t.code }}</a>
                </td>
                <td class="px-2 py-1">
                  <router-link :to="'/stock/' + t.code" class="hover:text-accent hover:underline">{{ t.name }}</router-link>
                </td>
                <td class="px-2 py-1">{{ t.direction === 'long_t' ? '正T（先买）' : '反T（先卖）' }}</td>
                <td class="px-2 py-1 text-right font-mono">{{ Number(t.open_price).toFixed(2) }}</td>
                <td class="px-2 py-1 text-right font-mono">
                  {{ t.close_price == null ? '—' : Number(t.close_price).toFixed(2) }}
                </td>
                <td class="px-2 py-1 text-right font-mono">{{ t.open_shares }}</td>
                <td class="px-2 py-1 text-right font-mono cursor-help"
                    :class="t.pnl == null ? 'text-muted' : (netOf(t) >= 0 ? 'text-rose-300' : 'text-emerald-300')"
                    :title="t.pnl == null ? '' : feeTitle(t)">
                  <template v-if="t.pnl == null">—</template>
                  <template v-else>
                    {{ netOf(t) > 0 ? '+' : '' }}{{ netOf(t).toFixed(2) }}
                    <span class="opacity-70">（{{ Number(t.pnl_pct || 0).toFixed(2) }}%）</span>
                  </template>
                </td>
                <td class="px-2 py-1">
                  <span class="px-1.5 rounded text-[10px]"
                        :class="t.status === 'open' ? 'bg-accent/15 text-accent' : 'bg-white/10 text-muted'">
                    {{ t.status === 'open' ? '未平' : (t.status === 'stopped' ? '止损' : '已平') }}
                  </span>
                </td>
                <td class="px-2 py-1 text-muted truncate max-w-[240px]" :title="t.note">{{ t.note }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <div v-else class="bg-card border border-border rounded-lg p-6 text-center text-[11px] text-muted">
        切到本页会自动加载历史成交。
      </div>
    </div>

    <!-- ── ⑤ 单只诊断 ── -->
    <div v-show="tab === 'explain'" class="space-y-2">
      <div class="bg-card border border-border rounded-lg p-3 space-y-2">
        <div class="text-xs font-semibold">为什么某只票能做 / 不能做？</div>
        <div class="flex items-center gap-2">
          <input v-model.trim="explainCode" type="text" placeholder="输入 6 位代码，如 000048"
                 class="px-2 py-1 rounded bg-transparent border border-border text-xs
                        text-gray-200 placeholder:text-muted focus:border-accent/50 outline-none w-40 font-mono"
                 @keyup.enter="doExplain" />
          <button type="button" class="px-2.5 py-1 rounded text-xs border border-border
                                       text-muted hover:text-accent hover:border-accent/40"
                  :disabled="explainBusy" @click="doExplain">
            {{ explainBusy ? '查询中…' : '诊断' }}
          </button>
          <span class="text-[11px] text-muted">实时算（不依赖是否在池里）</span>
        </div>
        <div v-if="explainErr" class="text-[11px] text-rose-300">{{ explainErr }}</div>
        <div v-if="explain" class="text-[11px] space-y-1 pt-1">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="font-mono">{{ explain.code }}</span>
            <span>{{ explain.name || '—' }}</span>
            <span class="px-1.5 rounded text-[10px]"
                  :class="explain.feasible ? 'bg-emerald-500/20 text-emerald-300' : 'bg-white/10 text-muted'">
              {{ explain.feasible ? '今日可做' : '今日不可做' }}
            </span>
            <span v-if="explain.as_of" class="text-muted">数据截至 {{ explain.as_of }}</span>
          </div>
          <div class="grid grid-cols-2 md:grid-cols-4 gap-x-4 gap-y-1 text-muted">
            <div>现价 <span class="text-gray-200 font-mono">{{ fmt(explain.price) }}</span></div>
            <div>支撑 <span class="text-gray-200 font-mono">{{ fmt(explain.support) }}</span>（{{ explain.support_type || '—' }}）</div>
            <div>阻力 <span class="text-gray-200 font-mono">{{ fmt(explain.resistance) }}</span>（{{ explain.resistance_type || '—' }}）</div>
            <div>ATR <span class="text-gray-200 font-mono">{{ fmt(explain.atr) }}</span>
              <span v-if="explain.atr_pct">（{{ Number(explain.atr_pct).toFixed(2) }}%）</span></div>
            <div>空间 <span class="text-gray-200 font-mono">{{ fmt(explain.space_pct) }}%</span></div>
            <div>空间/ATR <span class="text-gray-200 font-mono">{{ fmt(explain.space_atr_mult) }}</span></div>
            <div>建议仓位 <span class="text-gray-200 font-mono">{{ explain.ratio != null ? Math.round(Number(explain.ratio) * 100) + '%' : '—' }}</span></div>
          </div>
          <div v-if="!explain.feasible && explain.reason" class="text-amber-300 pt-0.5">
            原因：{{ explain.reason }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import {
  getTTradeSummary, getTTradePlans, getTTradeExplain,
  getTTradePool, addTTradePool, delTTradePool, triggerTTradeScan,
  getBatchPricesCached, testTTradePush, buildTTradePlan,
  getTTradeSim, delTTradeSim, autoTTradePool, getTTradeHistory,
} from '../api'
// ★ 2026-11-15（用户："点击代码跳转雪球，名称跳转个股详情页"）：
//   雪球链接复用项目唯一实现 `getXueqiuUrl`（`composables/stockUtils`），
//   不在本页自己拼 URL —— 与评分榜/持仓页同款，避免两处各写一份。
import { getXueqiuUrl } from '../composables/stockUtils'

// ★ 2026-11-24（用户："把成本模型加进 t_trades 的盈亏计算"）：
//   后端新记录带 `pnl_net`（毛利 − 佣金/印花税/过户费），
//   历史记录没有该列（NULL）⇒ 回退 `pnl`（毛利），并在 hover 里说明是毛利。
function netOf(t) {
  const v = t?.pnl_net
  return v == null ? Number(t?.pnl || 0) : Number(v)
}
function feeTitle(t) {
  if (t?.fee == null) {
    return '该笔无费用记录（历史数据）⇒ 显示的是毛利，实际更低'
  }
  return `毛利 ${Number(t.pnl || 0).toFixed(2)} − 费用 ${Number(t.fee).toFixed(2)} = 净利 ${Number(t.pnl_net || 0).toFixed(2)}`
}

const tabs = [
  { key: 'pool', label: '我的池子' },
  { key: 'market', label: '全市场可行' },
  { key: 'trades', label: '今日成交' },
  // ★ 2026-11-22（用户："做t明明有交易，但是我看不到历史及收益情况"）：
  //   「今日成交」只查当天 ⇒ 隔天/周末打开历史的 T 就消失了。补一个跨日的历史页。
  { key: 'history', label: '历史成交' },
  { key: 'explain', label: '单只诊断' },
]

const tab = ref('pool')
const busy = ref(false)
const scanning = ref(false)
const err = ref('')
// ★ 2026-11-16：模拟底仓信息（`auto_build` = 建仓点命中后是否自动建模拟仓）
const simInfo = ref(null)

const summary = ref(null)
// ★ 2026-11-22：历史成交（跨日）+ 累计收益，切到 tab 时懒加载
const historyData = ref(null)
const historyBusy = ref(false)
const poolPlans = ref([])
const marketPlans = ref([])
const marketTotal = ref(0)
const poolList = ref([])

const kw = ref('')
const page = ref(0)
const pageSize = 40

const explainCode = ref('')
const explain = ref(null)
const explainErr = ref('')
const explainBusy = ref(false)

// ── 实时行情（与评分排行榜**同口径**）────────────────────────────────
// 【为什么用 `getBatchPricesCached`（cached=1）】
//   调度器每 ~120s 已刷全市场行情到内存，这里只读缓存 ⇒ **上游请求 = 0**。
//   参考 `ScoreRank.vue` / `PaperTrading.vue`：同一接口、同一字段（红涨绿跌）。
// ⚠️ 返回是**对象** `{ok, cached, rows:[{code,name,price,change_pct}], data_date, as_of}`
//    （老接口是数组）—— 取 `data.rows`，别再按数组处理。
const quotes = ref({})
const quoteAsOf = ref('')

function quoteOf(code) { return quotes.value[String(code)] || {} }
function chgClass(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return 'text-muted'
  return n > 0 ? 'text-red-400' : n < 0 ? 'text-emerald-400' : 'text-muted'
}
function fmtChg(v) {
  const n = Number(v)
  if (!Number.isFinite(n)) return '—'
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%'
}
function fmtVol(v) {
  const n = Number(v)
  return Number.isFinite(n) && n > 0 ? n.toFixed(2) : '—'
}

async function loadQuotes(codes) {
  const uniq = [...new Set((codes || []).map((c) => String(c)).filter(Boolean))]
  if (!uniq.length) return
  try {
    const { data } = await getBatchPricesCached(uniq)
    const arr = (data && data.rows) || (Array.isArray(data) ? data : [])
    const m = {}
    arr.forEach((x) => { m[String(x.code)] = x })
    quotes.value = m
    quoteAsOf.value = (data && (data.as_of || data.data_date)) || ''
  } catch (e) {
    // 行情失败不能把整页打挂（预案仍然可用）⇒ 记在 err 上，表格显示 "—"
    err.value = '实时行情加载失败（预案数据仍可看）：' + ((e && e.message) || '')
  }
}

const stats = computed(() => (summary.value && summary.value.stats) || {})
const planDate = computed(() => {
  const a = poolPlans.value[0] || marketPlans.value[0]
  if (a && a.plan_date) return a.plan_date
  const ps = (summary.value && summary.value.plans) || []
  return (ps[0] && ps[0].plan_date) || ''
})
const poolCodes = computed(() => new Set(poolList.value.map((x) => x.code)))

/** 池内每只：能做的用预案数据，不能做的标不可行；两者都并上实时行情 */
const poolRows = computed(() => {
  const pmap = {}
  poolPlans.value.forEach((p) => { pmap[p.code] = p })
  return poolList.value.map((x) => {
    const p = pmap[x.code]
    const qq = quoteOf(x.code)
    const base = p ? { ...p, name: p.name || x.name } : { ...x }
    return {
      ...base,
      // ★ 2026-11-16：**保留来源**（`pool` / `holding` / `both`）。
      //   ⚠️ 必须显式写，否则被 `...p`（预案行，没有 source）覆盖掉。
      source: x.source || 'pool',
      shares: x.shares,
      feasible: !!p,
      // 现价优先用**实时价**（`price_ref` 是预案生成时的收盘价，盘中已过时）
      price: qq.price != null ? qq.price : (p ? p.price_ref : null),
      change_pct: qq.change_pct,
      reason: p ? '' : '今日无可行预案（空间不足或数据缺失）',
    }
  })
})

const filteredMarket = computed(() => {
  const k = kw.value.trim()
  if (!k) return marketPlans.value
  return marketPlans.value.filter((p) =>
    String(p.code).includes(k) || String(p.name || '').includes(k))
})
const totalPages = computed(() => Math.max(1, Math.ceil(filteredMarket.value.length / pageSize)))
const pagedMarket = computed(() =>
  filteredMarket.value.slice(page.value * pageSize, (page.value + 1) * pageSize))
const inPoolCount = computed(() =>
  marketPlans.value.filter((p) => poolCodes.value.has(p.code)).length)
// ★ 2026-11-16（用户看着"今日可做 4 只"问"明天是不是只有粤高速"）：
//   `poolPlans` 是**预案可行**（有支撑阻力）的票，不等于**能下单**。
//   无底仓时 `buy_check` 会拒绝（A股 T+1：先买后卖卖的是原有底仓，
//   没有底仓那不是做T 是建仓）⇒ 两个数字差得很远（今天 4 vs 1）。
//   混在一起显示会让人以为"明天有 4 只机会"，必须分开说。
const canTradeCount = computed(() =>
  poolPlans.value.filter((p) => p.buy_ok === true).length)

function fmt(v) {
  if (v === null || v === undefined || v === '') return '—'
  const n = Number(v)
  return Number.isFinite(n) ? n.toFixed(2) : '—'
}

async function loadAll() {
  busy.value = true
  err.value = ''
  try {
    const [s, wp, mp, pl, sm] = await Promise.all([
      getTTradeSummary(),
      getTTradePlans({ watch_only: true, limit: 200 }),
      getTTradePlans({ watch_only: false, limit: 500 }),
      getTTradePool(),
      // ★ 2026-11-16：模拟底仓（自动建仓的落点）——只用来展示"自动建仓是否开启"
      getTTradeSim(),
    ])
    simInfo.value = (sm && sm.data) || null
    summary.value = (s && s.data) || null
    poolPlans.value = ((wp && wp.data) || {}).data || []
    const mb = (mp && mp.data) || {}
    marketPlans.value = mb.data || []
    marketTotal.value = mb.total || marketPlans.value.length
    poolList.value = ((pl && pl.data) || {}).data || []
    page.value = 0
    // 实时行情：市场清单 + 池子，**一次合并请求**（只读后端内存缓存，上游 0 请求）
    const codes = [
      ...marketPlans.value.map((x) => x.code),
      ...poolList.value.map((x) => x.code),
    ]
    await loadQuotes(codes)
  } catch (e) {
    err.value = (e && e.message) || '加载失败'
  } finally {
    busy.value = false
  }
}

/** ★ 2026-11-19：立即自动选池（只替换 source='auto' 的行）。 */
const autoPicking = ref(false)
async function autoPick() {
  autoPicking.value = true
  err.value = ''
  notice.value = ''
  try {
    const { data } = await autoTTradePool()
    if (data && data.plan_date) {
      notice.value = `已按 ${data.plan_date} 预案自动挑选 ${(data.picked || []).length} 只`
        + `（排除持仓 ${data.excluded_held || 0} / 手动 ${data.excluded_manual || 0}）`
    } else {
      notice.value = (data && data.note) || '已执行'
    }
    await loadAll()
  } catch (e) {
    err.value = (e && e.message) || '自动选池失败'
  } finally {
    autoPicking.value = false
  }
}

async function rescan() {
  scanning.value = true
  err.value = ''
  try {
    await triggerTTradeScan()
    err.value = '后台扫描已启动（约 1~2 分钟），完成后刷新即可看到新预案'
  } catch (e) {
    err.value = (e && e.message) || '启动失败'
  } finally {
    scanning.value = false
  }
}

const pushTesting = ref(false)
const notice = ref('')
async function testPush() {
  pushTesting.value = true
  err.value = ''
  notice.value = ''
  try {
    const { data } = await testTTradePush()
    const ok = !!(data && data.success)
    const msg = (data && data.note) || (ok ? '已发送' : '未送达')
    if (ok) notice.value = msg
    else err.value = msg
  } catch (e) {
    err.value = (e && e.message) || '发送失败'
  } finally {
    pushTesting.value = false
  }
}

async function addFromMarket(p) {
  try {
    await addTTradePool([p.code], '做T 台手动加入')
    await loadAll()
  } catch (e) {
    err.value = (e && e.message) || '加入失败'
  }
}

/** ★ 用支撑位建「底仓建仓点」交易计划（走 user_trade_plans，复用既有买点提醒） */
async function makePlan(code) {
  err.value = ''
  notice.value = ''
  try {
    const { data } = await buildTTradePlan(code)
    const d = data || {}
    if (d.success) {
      notice.value = d.note
        || `已建立仓计划：买 ${d.buy_price} / 止损 ${d.stop_loss} / 目标 ${d.target}`
      err.value = ''
    } else {
      err.value = d.note || '建立仓计划失败'
    }
  } catch (e) {
    err.value = (e && e.message) || '建立仓计划失败'
  }
}

/** ★ 清除模拟底仓（价格到位后系统自动建的那笔，不是真实持仓）。 */
async function clearSim(code) {
  err.value = ''
  notice.value = ''
  try {
    await delTTradeSim(code)
    notice.value = `${code} 模拟底仓已清除`
    await loadAll()
  } catch (e) {
    err.value = (e && e.message) || '清除失败'
  }
}

async function removeFromPool(code) {
  try {
    await delTTradePool(code)
    await loadAll()
  } catch (e) {
    err.value = (e && e.message) || '移除失败'
  }
}

async function doExplain() {
  const c = explainCode.value.trim()
  if (!c) return
  explainBusy.value = true
  explainErr.value = ''
  try {
    const { data } = await getTTradeExplain(c)
    explain.value = (data && data.data) || data || null
  } catch (e) {
    explainErr.value = (e && e.message) || '查询失败'
    explain.value = null
  } finally {
    explainBusy.value = false
  }
}

// ── 历史成交：切到 tab 时懒加载（不拖慢首屏的 loadAll）──
// ★ 2026-11-22（审查补）：原实现只在 `!historyData` 时拉 ⇒ **拉过一次就再不刷新**。
//   但做T 是盘中持续产生的，切回来应该能看到新成交。
//   ⇒ 加 60s 节流：60 秒内切回来复用，超时才重拉（既不"永不更新"也不"每次切都打"）。
let _historyTs = 0
async function loadHistory() {
  historyBusy.value = true
  err.value = ''
  try {
    const { data } = await getTTradeHistory({ limit: 200 })
    historyData.value = data || null
    _historyTs = Date.now()
  } catch (e) {
    err.value = '历史成交加载失败：' + (e.response?.data?.detail || e.message)
    historyData.value = null      // 失败清空 ⇒ 下次切回会重试
  } finally {
    historyBusy.value = false
  }
}

watch(tab, (v) => {
  if (v !== 'history') return
  if (historyData.value && Date.now() - _historyTs < 60000) return
  loadHistory()
})

onMounted(loadAll)
</script>
