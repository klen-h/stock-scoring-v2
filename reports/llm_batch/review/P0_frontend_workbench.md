审查范围：3 文件 / 约 5453 行 / 2026-10-24

说明：以下每条引文均从文件实际内容复制并核对过行号；tencent.js 字段索引已与 backend/app/tencent.py（315~359 行）逐项比对。

---

### [P1] frontend/src/views/Workbench.vue:4626 主轮询绝大多数加载器不受交易时段约束，页面开一天会整夜持续请求
- 原文: `    loadOverview(); loadTemperature(); loadEmotion(); loadGlobals(); loadTailReview()`
- 问题: 120s 主轮询回调里只有 realtimeUplimit/amountRt/workbenchScan/竞价 emotion 走 `isLivePhase()` 门控，overview/temperature/emotion/globals/tailReview/policyBottom/flashDiag/sectorTop/amountShare/events/gwPrices 全部无条件每 120s 发请求，交易时段外（夜间/周末）照跑。
- 影响: 页面开一天，夜间仍每 2 分钟向后端发十余个请求（backend 模式下含 90s 超时的 getScoreTop），与注释"⚠️ 仅交易时段真发请求"的声明不符，浪费后端资源与 egress。
- 建议: 把主轮询回调体统一包一层 `if (!isLivePhase() && !需要常驻) return`，或将非交易时段降频到 10 分钟级（顶栏状态/宏观摘要可保留低频）。
- 置信度: 高

### [P2] frontend/src/views/Workbench.vue:4012 本地评分模式在非交易时段仍每 5 分钟做一次全市场精算
- 原文: `      if (!rows.length || force || !_fresh) rows = await computeRanking({ mode: 'top', limit: 10 })`
- 问题: 注释声称"非交易时段白跑一次全市场精算没意义"并传 `loadTop(isLivePhase())`，但 force 只影响跳过复用窗口；夜间 `_fresh`（5 分钟窗口）过期后条件 `!_fresh` 成立，仍会重算。
- 影响: 页面夜间常开时，每 5 分钟触发一次"拉全市场行情（分批请求腾讯代理）+ 批量财报 + Worker 精算"的重活，纯浪费且与代码注释意图相反。
- 建议: 夜间（非 isLivePhase）直接跳过 loadTop（或把复用窗口放宽到收盘时段），只在 live phase 强制刷新。
- 置信度: 高

### [P2] frontend/src/views/Workbench.vue:19 `bg-background` 不是 Tailwind 令牌，日期下拉无底色（深色主题违例）
- 原文: `                  class="bg-background border border-border rounded px-2 py-1 text-xs">`
- 问题: tailwind.config.js 的 colors 只定义了 rise/fall/bg/card/border/accent/muted，没有 `background`；已核实 dist 产物 CSS 中不存在 `.bg-background` 规则 ⇒ 该类静默失效，`<select>` 用浏览器默认底色。
- 影响: 深色主题下日期选择器可能呈系统默认（浅色）底，是顶栏最常点的控件之一；同文件 1804 行行业标签 `bg-background` 同样失效（退化为透明底）。
- 建议: 改用项目既有令牌 `bg-bg`（全站通用写法），并顺手排查 TodoCard.vue:46、TradePlans.vue:180、LhbView.vue:12 的同款 `bg-background`。
- 置信度: 高

### [P2] frontend/src/api/index.js:57 401 跳转用 path `/login` 与 hash 路由不一致，且一次 401 重定向后条件永久失效
- 原文: `        window.location.href = '/login'`
- 问题: 项目是 `createWebHashHistory` 路由，登录页真实地址是 `#/login`；此处跳 path `/login`（靠 nginx try_files 兜底 + 路由守卫二次转跳才落到登录页）。且跳转后 `pathname` 恒为 `/login`，56 行条件 `window.location.pathname !== '/login'` 从此为假。
- 影响: 经历过一次 401 重定向的会话，此后 token 再次过期时拦截器不再跳登录页，用户停在原页、所有请求持续 401 静默失败。
- 建议: 改为 `window.location.hash = '#/login'`（与路由模式一致），并把条件改为只判断 hash。
- 置信度: 高

### [P2] frontend/src/api/tencent.js:87 市场前缀映射不识别北交所代码（4/8/92 开头）
- 原文: `    const prefix = code.startsWith('6') ? 'sh' : 'sz'`
- 问题: 只区分 6→sh、其余→sz；北交所 4/8/92 开头代码会拼成 `sz8xxxxx` 请求腾讯，返回无效数据后被 `fields.length < 50` 静默丢弃。项目自身 `displayMeta.xqUrl` 已按"4/8→BJ"处理（其注释明示"6/9→SH、4/8→BJ、其余 SZ"），说明 BJ 代码在系统口径内。
- 影响: 若 K 线包/调用方传入 BJ 代码，这些票拿不到行情、被评分池静默排除且无任何报错（本地榜与真实全市场口径悄悄漂移）。
- 建议: 增加 `code.startsWith('4')||code.startsWith('8')||code.startsWith('92') ? 'bj' : ...` 分支，或在入口显式过滤非沪深代码并 console.warn。
- 置信度: 中（映射错误确凿；实际触发取决于 kline 包是否含 BJ 代码）

### [P3] frontend/src/views/Workbench.vue:1057 站内 router-link 新开标签但缺 rel="noopener"（11 处）
- 原文: `                  <router-link target="_blank" to="/report" class="text-accent hover:underline">盘前简报</router-link>`
- 问题: 工作台约定"站内跳转一律 target=_blank + rel=noopener"，但 router-link 只带了 target：1057/1268/1323/1863/1991/2143/2170/2347/2394/2460/2586（2607 在死代码块内）；另多处 `<a :href="stockHref(...)" target="_blank">`（如 554/930/1478/1799/2027/2199/2409）同样缺 rel。
- 影响: 现代浏览器对 target=_blank 有隐式 noopener，实际风险低，但与项目红线写法不一致、依赖浏览器版本行为。
- 建议: 批量补 `rel="noopener"`（router-link 会透传该属性到渲染出的 `<a>`）。
- 置信度: 高

### [P3] frontend/src/views/Workbench.vue:1755 持仓状态标题在数据缺失/加载失败时填 0 而非 "—"
- 原文: `              <div class="text-sm font-semibold">持仓状态（{{ radarSummary.n || 0 }} 只 ·`
- 问题: `radarErr` 时下方正文显示"加载失败"，但标题仍渲染"持仓状态（0 只 · 风险 0 / 机会 0）"，把"未知"伪装成"确认为 0"，违反"数据缺失显示 '—' 不得填 0"的时点诚实红线。
- 影响: 接口失败或加载中时用户先读到"0 只"的确定结论，与正文的失败提示互相打架。
- 建议: 改为 `radarErr ? '—' : (radarSummary.n ?? '—')`，risk/opportunity 同理。
- 置信度: 高

### [P3] frontend/src/views/Workbench.vue:424 观察器悬停高亮 `hover:bg-white/[0.03]` 肉眼不可辨
- 原文: `                     class="flex items-center gap-1.5 rounded px-1 py-0.5 hover:bg-white/[0.03]">`
- 问题: 3% 白叠在 #161b22 卡片上与底色几乎无差（同卡展开面板用的 `bg-white/[0.06]` 尚可辨），悬停反馈基本不可见。
- 影响: 推演证伪信号格的 hover 可交互暗示丢失，用户难以发现可悬停/可读 title。
- 建议: 提到 `hover:bg-white/[0.06]` 与同卡展开面板对齐，或改用 `hover:bg-border/40`。
- 置信度: 中

### [P3] frontend/src/views/Workbench.vue:1648 死代码块使用未定义令牌 `bg-panel`
- 原文: `          <div v-if="false" class="rounded-xl border border-border p-4 bg-panel/60">`
- 问题: `panel` 不在 tailwind.config.js colors 中（已核实产物 CSS 无该规则）；该块是 2026-10-23 移入右栏后刻意 `v-if="false"` 保留的旧"市场底确认清单"。
- 影响: 当前无视觉影响（不渲染），但一旦有人删掉 v-if 恢复启用，卡片将无底色、比同页其它卡"发虚"。
- 建议: 恢复前先把 `bg-panel/60` 改为 `bg-card`，或直接删除该死块（右栏已有新版）。
- 置信度: 高

### [P3] frontend/src/views/Workbench.vue:2736 导入未使用 + 取数后不渲染的死绑定
- 原文: `  PHASE_STYLE as MF_PHASE_STYLE, strategyShort,`
- 问题: `strategyShort`、`scoreClass`（2737 行）导入后全文件无调用点；`wsSummary`（3646 声明、3765 赋值）从未渲染，却让 `getWorkbenchScanSummary(7)` 随每次切日期 + 每 5 分钟轮询白拉；`scenBadge`（3823）、`shortDiagType`（4254）定义后无引用。
- 影响: 无功能危害，但属死代码；其中 wsSummary 是真实的无谓请求（每 5 分钟一次、交易时段内）。
- 建议: 删未用导入与函数；`wsSummary` 要么渲染 7 日 worth_rate（数据本就是为摘要准备的），要么停掉该请求。
- 置信度: 高

### [P3] frontend/src/views/Workbench.vue:2863 竞价榜单单击/双击共用一个模块级定时器，跨行互相取消
- 原文: `  if (_auctionClickTimer) {          // 第二次 click ⇒ 属于双击序列 ⇒ 撤销单击动作`
- 问题: `_auctionClickTimer` 全榜（高开榜+低开榜）共用一份：260ms 内先后点两行不同股票，第二次 click 会把第一行的跳转当"双击序列"取消，第一行无任何反应。
- 影响: 快速连点不同行的用户偶尔遇到"点了没反应"，且无提示；单行内双击语义不受影响。
- 建议: 定时器改为挂在行级（如 Map<code, timer>），或取消时记录"上一个 code"，code 不同则放行第一次跳转。
- 置信度: 中

### [P3] frontend/src/views/Workbench.vue:57 statusHtml 用 v-html 渲染未转义的后端字段拼接
- 原文: `             v-html="statusHtml"></div>`
- 问题: `loadStatus()`（4488~4533）把 `data.summary`、`v.name`、`v.status` 等后端字段直接字符串拼接进 HTML 后 `v-html` 渲染，无转义。
- 影响: 数据源为自家后端，风险低；但一旦上游数据源名/摘要被注入恶意串，即成存储型 XSS 落点（该浮层无用户输入，故评级 P3）。
- 建议: 改用插值列表渲染（与 sources 数组结构天然匹配），或对字段统一过一遍 escapeHtml。
- 置信度: 中

---

### 摘要 frontend/src/views/Workbench.vue
职责：一天流程工作台主视图（盘前/竞价/盘中/午盘/盘后/复盘六段主区 + 常驻右栏），聚合行情、情绪、决策卡、教练卡、观察器、评分榜/观察池、时间线等约 30 个数据模块；含历史日期回放模式（只读）。
输入：`../api` 约 40 个只读/触发接口（轮询 60s/120s/300s 分组）+ `useFrontendScoring` 本地评分引擎（IndexedDB K 线包 + 腾讯行情）+ `useVerification` 可信度标注。
输出：主区按 `selectedPhase` 条件渲染各卡；右栏常驻快讯卡/市场底清单/教练卡/评分榜/时间线；顶栏摘要与浮层（系统状态、宏观、隔夜）。
关键阈值：轮询 POLL_SEC=120（coach/radar/涨停 60s、竞价 30s、扫描 300s）；本地评分复用窗口 5 分钟；执行率≥60% 显绿；晋级率 ≥50% 红 / <25% 绿；A50 期现背离 ±0.5pt；wsColor 80/60/40/20 分档；内存 ≥75% 才亮；换日差 1 天才用"昨日"措辞。
依赖：InsightBody、TodoCard、StockListsCards、MacroEnvCard、FlashBriefCard、ReserveCard 六个子组件；displayMeta（MF_PHASE_STYLE/stockHref/xqUrl/hhmm/pctClass/signNum/dirColor/levelColor）、markdown.renderMd、useFrontendScoring、useVerification。
已知坑：文件内自注释极厚——`briefMd` 多阶段共用需配 `briefPhase`；`limitReview` 初值必须给 `{steps:[],stocks:[]}` 防首帧崩；`PHASE_STYLE`（时段）与 `MF_PHASE_STYLE`（主力阶段）同名需重命名导入；`renderMd` 已收拢到 composposables/markdown；两处 `v-if="false"` 死块（旧市场底清单/持仓摘要）为有意保留；用户已裁定不要阶段自动流转。

### 摘要 frontend/src/api/index.js
职责：全站唯一 axios 实例与 API 面板：token 存取（localStorage）、请求带 Bearer、401 自动清 token 跳登录、约 130 个具名接口封装（含各自定制 timeout）。
输入：`import.meta.env.VITE_API_BASE_URL || '/api'`、各调用方参数。
输出：解包前的 AxiosResponse（仅 getFinanceBatch 统一解包 .data）；token/user 的 localStorage 读写。
关键阈值：默认 timeout 30s；同步 LLM 类给 120~180s（runFlashReview/refreshFlashBrief/getTraderBrief-refresh/getRadarAnalysis/getStockAnomalyAnalysis）；冷路径 getScoreTop 90s、getSystemPerformance 120s、backtest 120s。
依赖：axios；被全部视图/composable 引用。
已知坑：注释自证过两段路径坑——`/score/batch/*` 必须两段（后端 `/{symbol}` 单段通配吞路由）、workbench 三端点必须带 `/system` 前缀（否则 200 + HTML 静默空数据）；`refresh=true` 会烧 LLM（getTraderBrief 同时带 push）。

### 摘要 frontend/src/api/tencent.js
职责：浏览器端腾讯行情客户端（qt.gtimg.cn）：批量拉实时行情、GBK 解码、解析 `~` 分隔协议字段，供 useFastQuotes 与 useFrontendScoring（本地评分榜）使用。
输入：纯数字股票代码数组；开发走 Vite 代理 `/tencent-api`，生产可配 CF 代理否则直连。
输出：`{ code: { price, change_pct, pe, pb, market_cap(万元), amount(元), ... } }`（fetchBatchPrices 简化为 price/change_pct）。
关键阈值：BATCH_SIZE=50/批、TIMEOUT=10s、批间 sleep 100ms、失败 sleep 1s 后整批重试一次、`fields.length<50` 视为残缺丢弃。
依赖：原生 fetch + AbortController + TextDecoder('gbk')；无项目内依赖。
已知坑：字段索引必须与后端 tencent.py 对齐（本次逐项核对一致：3/4/5/6/32/37/38/39/41/42/43/44/45/46）；data[40] 为空、data[57]/[58] 早期误用已修正（注释在案）；GBK 解码失败降级 UTF-8 会乱码；北交所前缀缺失见上文问题条。

---

## 未发现问题
- 模板引用未声明变量：未发现。主模板所有绑定（含 `wsPick`/`promotion`/`portfolioDay`/`radarQuote`/`obsAI` 等复杂表达式）均在 `<script setup>` 有对应声明；两处 `v-if="false"` 死块内引用的 `pbwItems`/`toggleNationTeam`/`refreshAnnouncements`/`refreshEtf` 等亦已声明。
- 定时器/内存泄漏：未发现。`timers` 数组集中注册，`stopPolling()` 逐个 clearInterval，`onUnmounted(stopPolling)` 兜底；`watch(selectedDate)` 切换先 stop 后 start，`backToToday` 重复调用 startPolling 也先 stop，无重复注册；`coachList`/`pushItems`/`gwPrices` 轮询均为整体替换而非追加。
- tencent.js 字段索引与后端对齐：与 backend/app/tencent.py 315~359 行逐项一致（price[3]/prev[4]/open[5]/volume[6]/pct[32]/amount[37]/换手[38]/pe[39]/high[41]/low[42]/振幅[43]/流通市值[44]/总市值[45]/pb[46]，万元/元单位换算也一致）。
- 同一份数据同屏重复渲染：未发现。教练卡、系统时间线、评分榜/观察池均只有右栏一份；宏观卡用同一 `MacroEnvCard` 组件复用（盘前内联 + 顶栏浮层），组件级复用避免了两套渲染漂移；盘中"看盘序"与盘后"今日盘面定稿"虽同源但分属互斥的 phase 分支且注释声明有意不抽组件。
- 站内同标签跳转：未发现。全部 `<router-link>`/外链均为 target="_blank"（缺 rel="noopener" 见上文 P3）。
- 观察器卡（盘中变化/拥挤与关键位/推演证伪/AI 硬件篮子/AI 解读）：未见买卖/加减仓指令式文案，均带"纯观察 · 不进决策链 / 观察器不构成买卖信号"定位标注。
- 时点诚实（主体）：缺失值普遍按 "—" 显示（`?? '—'` 大量使用），as_of/data_date/quote_as_of/from_snapshot 广泛标注；仅 1755 行一处 0 填充与 `scenLine` 的 `n_up ?? 0` 属同类小瑕疵（前者已列条目）。
