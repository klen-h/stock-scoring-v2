# BACKLOG —— 全项目待办盘点（2026-10-24 定稿）

> 三处交叉来源：① `docs/plans/PLAN_NEXT.md`（权威待办，逐条判定）；② 全仓
> TODO/FIXME/暂不/未做/尚未扫描 118 处（逐条判定，淘汰项见 §C）；③
> `.codebuddy/memory/MEMORY.md` 遗留条目 + 本次代码审查（`reports/llm_batch/review/`）新增发现。
> 每条必须有证据（文件:行 或 现象）；优先级：P0=正确性/资金/线上可用，P1=决策质量，P2=债务/体验。
> 模块分派：后端 / 前端 / 数据流水线 / 文档。责任人统一为「用户」。

## A. 仍待办（active）

| ID | 模块 | 标题 | 证据 | 优先级 | 依赖 | 预估 | 验收口径 |
|---|---|---|---|---|---|---|---|
| BK-01 | 后端 | 请求路径触发全市场同步刷新（refresh_all_stocks 冷启动 2-4 分钟，30s 轮询堆积等待） | `routers/scoring.py:1288`、`routers/market.py:373`（review/P0_routers_scoring_market.md P2-1） | **P0** | 无 | 0.5d | 接口内零全市场刷新；冷启动首请求 <2s 或返回显式"预热中"降级 |
| BK-02 | 后端 | 主力闸门数据缺失 fail-open 放行（高位/拉升股绕过唯一有效提纯过滤，且静默无告警） | `mainforce/gate.py:237`（取不到 bars/chip 时 `ok=True`）、`mainforce/trade_gate.py:138`（chip 全缺时条件 B 白送通过，ready 虚高）（review/P0_mainforce.md P1-1/2） | **P0** | 无 | 1d | 缺数据日改 fail-closed 或显式 reason+计数；重放验证 ready≥2 候选池不含无数据个股 |
| BK-03 | 后端 | market overview 对 change_pct/amount 裸下标访问，池内 None ⇒ 整接口 500（fail-closed 反例） | `routers/market.py:330/340/358`；scoring.py:1329 自认池内存在 None（review/P0_routers_scoring_market.md P2-5） | **P0** | 无 | 0.5d | 含 None 个股不致 500；该块缺失置空其余照常（fail-open）✅ 2026-10-24 已修复（commit 510af769：裸下标改 None 安全 + 语义快测） |
| BK-04 | 后端 | async 端点内同步 DB/HTTP/CPU 直跑事件循环（score_top 内东财快讯最长 ~22s；shadow_rank 每请求必打库；backtest 端点在事件循环跑几十秒回测；tasks.py async 内同步 requests.post） | `routers/scoring.py:1440、1358/1341/1460/446、1572、636`、`routers/tasks.py:62`（review/P0_routers_scoring_market.md P1-1~4、review/P0_routers_core.md P1-5） | **P0** | 无 | 1d | 五处改 `to_thread`/后台任务；压测单请求不阻塞 `/api/health` |
| BK-04a | 后端 | flash 路由组无任何鉴权：匿名 POST 可烧 LLM、推企微、触发外网轮询 | `routers/flash.py:130`（review/P0_routers_core.md P1-2；main.py 无全局鉴权中间件） | **P0** | 无 | 0.5d | 写操作挂 `verify_token` 依赖（与 user/auth 同机制）；匿名只读白名单显式列举✅ 2026-10-24 已修复（commit afc3db93：写端点挂鉴权） |
| BK-04b | 后端 | JWT_SECRET 缺失时回退机器名可预测密钥 ⇒ token 可伪造、全线鉴权失效 | `routers/auth.py:17` 回退逻辑（review/P0_routers_core.md P1-6；`app/auth.py:24-29`） | **P0** | 无 | 0.2d | 缺失时拒绝启动或显式 fail-closed（Render 环境变量补齐属建议变更，不改生产）✅ 2026-10-24 已修复（commit e6beffd0：随机临时密钥 + 恒定时间口令比较） |
| BK-04c | 后端 | PUT /plans 按 id upsert 可跨用户接管交易计划 + mass assignment；/sync 计划 id 客户端可控 | `routers/user.py:186`、`:322`（review/P0_routers_core.md P1-3/4；database.py `ON CONFLICT(id)` 生成已核实） | **P0** | 无 | 0.5d | upsert 强制 `user_id=当前用户` 且白名单列；越权 404✅ 2026-10-24 已修复（commit a057c794 + HTTP 验证修正：PUT 改 UPDATE 语义） |
| BK-04d | 后端 | POST /finance-refresh 未鉴权且请求内做全市场财报抓取（500 行/页 × 60 页） | `routers/stock.py:281`（review/P0_routers_core.md P1-1） | **P0** | 无 | 0.3d | 挂鉴权；抓取迁后台/日批，接口只读缓存✅ 2026-10-24 已修复（commit afc3db93：鉴权 + BackgroundTasks + 单飞/冷却） |
| BK-04e | 后端 | 异动涨跌停判定自造 0.005 元固定容差，与唯一口径 limit_stats（0.2% 相对容差，其文档警告固定容差实测差 24 只）口径分裂 | `routers/stock.py:403`（review/P0_routers_core.md P2） | P1 | 无 | 0.2d | 改调 `limit_stats.is_limit_up/down` |
| BK-04f | 后端 | /calendar 缓存空时 GET 内现场外拉（违反"接口只读缓存"红线）；batch-done 共享密钥非常量时间比较；口令实为 salt+SHA-256 而头注释声称 bcrypt | `routers/flash.py:53`、`routers/system.py:114`、`routers/auth.py:89`（review/P0_routers_core.md P2） | P1 | 无 | 0.5d | /calendar 空缓存返回 404/降级；`compare_digest`；注释改实话或换 bcrypt |
| BK-33 | 后端 | 回测涨停一字判定基于前复权价四舍五入+±0.001 容差，小于复权舍入误差 ⇒ 除权日前历史一字板约半数漏判、按涨停价成交，回测收益/胜率系统性虚高 | `backtest/engine.py:120`（review/P0_backtest.md P0，置信度中） | **P0** | 须重放对照 | 1d | 改比值判据（close/limit_price ≥ 1−ε）；重放 skipped_out 数量对照 |
| BK-34 | 后端 | 宏观回测指数缺失时 `_benchmark_ret` 返回 0.0 ⇒ "缺失"渲染成"基准 0.00%、超额=全部收益"（违反缺失≠0 红线） | `backtest/strategies.py:642`（review/P0_backtest.md P1） | **P0** | 无 | 0.2d | 返回 None 并在报告标注"缺失"（同文件 `_equal_weight_ret` 已是正确写法）✅ 2026-10-24 已修复（commit b999db26：返回 None + compute_metrics None 安全 + 回归测试） |
| BK-35 | 后端 | 建表 DDL 用 PG 专有 SERIAL：本地 SQLite 必抛错且被吞 ⇒ 报告归档/nb 两日确认失效；`database._DDL_DONE` 执行前登记 ⇒ 失败不重试 | `backtest/market_regime.py:604`、`backtest/report_store.py:33`（review/P0_backtest.md P1） | P1 | 无 | 0.5d | DDL 兼容双库（INTEGER PRIMARY KEY 自增）；DDL 失败从 _DONE 移除✅ 2026-10-24 已修复（commit ea129843：id 列/ADD COLUMN 双库分支 + is_postgres 公共化 + _DDL_DONE 失败移出） |
| BK-36 | 后端 | 数据右缘截断的持仓标"持有到期"全额计入指标（无 truncated 标记）；盘后信号以当日收盘价为入场基准且零成本 ⇒ 回测偏乐观 | `backtest/engine.py:175`、`backtest/strategies.py:615`（review/P0_backtest.md P1） | P1 | 无 | 1d | truncated 显式标记；入场改次日开盘+计入成本（口径变化须披露） |
| BK-37 | 后端 | etf_close 日期非零填充+TEXT 字典序排序 ⇒ 10 月两位数行被 trim 误删、load_etf_close 返回 9 月快照（污染复盘 prompt 的"昨日收盘 ETF 参考"；macro 同款已修此处漏修） | `flash/store.py:544/560/569/584`（review/P0_flash_llm_store.md P1） | P1 | 无 | 0.3d | 零填充日期 + 解析排序（同 load_macro_daily_history 修法）✅ 2026-10-24 已修复（commit 342733f7：行键 ISO 化 + 幂等迁移旧行） |
| BK-38 | 后端 | LLM 日熔断 TOCTOU：检查与记账间无原子性，并发可越过 LLM_DAILY_MAX_CALLS；`_call_json` 不校验顶层类型 ⇒ 非 dict 返回抛 TypeError 中断游标（重复分析+重复推送） | `flash/llm.py:573`、`:705`（review/P0_flash_llm_store.md P2） | P1 | 无 | 0.5d | 熔断检查+记账原子化（SQL 单语句条件自增）；JSON 顶层类型校验+降级 |
| BK-39 | 后端 | 回测/形态扫描/行业地图等重接口无鉴权：匿名 GET 可同步触发全量回测重算（无单飞锁，并发重复重算）、匿名 POST 可反复触发东财/新浪抓取（封 IP 攻击面） | `routers/backtest.py:187`、`routers/pattern.py:28`、`routers/sector.py:112`（review/P0_routers_misc.md P1-1~3） | **P0** | 无 | 0.5d | 重计算端点挂鉴权 + 单飞锁；匿名触发返回 401✅ 2026-10-24 已修复（commit afc3db93：重计算端点挂鉴权；单飞锁由三层缓存+审计路径另行评估） |
| BK-40 | 后端 | 模拟盘 11 端点全无鉴权（close/cancel/unfreeze 可匿名操作，污染前瞻记录）；coach 全端点注入 user 不校验属主（横向越权）；macro GET `refresh` 参数匿名绕 6h 缓存全量重算 | `routers/paper.py:143`、`routers/coach.py:52`、`routers/macro.py:67`（review/P0_routers_misc.md P2） | P1 | 无 | 0.5d | 写端点挂鉴权+属主校验；refresh 参数鉴权化✅ 2026-10-24 部分修复（commit afc3db93：paper 5 端点 + macro refresh 参数挂鉴权；coach 行级属主校验**未实施**——coach_alerts 表无 user_id 列，需先做列迁移，已按诚实原则保留为后续项） |
| BK-41 | 后端 | 龙虎榜统计 SQL 用 PG 专有 `::text`/`ANY(%s)`：SQLite 下语法错被吞返回自相矛盾部分数据（同函数自述双库兼容铁律） | `routers/capital.py:198`、`routers/performance.py:108`（review/P0_routers_misc.md P2） | P1 | 无 | 0.3d | 双库兼容写法；SQLite 下显式报错而非静默 |
| BK-42 | 后端 | **`_trust_advice` 输出"放心买，极强共识/可重仓"**（trust 公式评分占 40%），经 `POST /ranking-persistence` 返回前端——与 2026-10-11 评分 KILL 预登记直接冲突（遗留代码 KILL 落地时未回收） | `scoring/ranking_history.py:347`（review/P0_scoring.md P1；git 显示 2026-08-21 引入） | **P0** | 无 | 0.2d | 措辞改为事实描述并带 KILL 披露链接；与 B1 文案口径一致✅ 2026-10-24 已修复（commit b999db26：改为纯事实描述，含 KILL 冲突史留痕） |
| BK-43 | 后端 | `indicator_cache` 增量更新落库 JSON 不含 `_series` ⇒ 盘中一次增量调用抹掉该股预计算指标序列（前端已接该端点） | `scoring/indicator_cache.py:713`（review/P0_scoring.md P2） | P1 | 无 | 0.3d | 增量落库合并 `_series`（读改写）而非整行覆盖✅ 2026-10-24 已修复（commit e7d2bd46：增量更新保留 _series/kline_count） |
| BK-44 | 后端 | `get_cached_klines_batch` 无 36h TTL 检查（单只路径有）⇒ 盘前/周末批量评分与单只详情口径分叉；pack 通道跳过当日 bar 合成校验（15:00-22:00 窗口差一根 bar，score_single vs batch/top 的收盘后变体） | `scoring/kline_cache.py:316`、`:220`（review/P0_scoring.md P2） | P1 | 无 | 0.5d | 批量路径补 TTL；pack 通道补合成校验 |
| BK-45 | 后端 | 收益验证用自然日比较 ⇒ 周末把最新快照"伪验证"成约 0 收益污染 winRate；`ranking_history` import 时执行 5 条 DDL（与 P1-18 懒执行修复不一致） | `scoring/ranking_history.py:797`、`:99`（review/P0_scoring.md P2） | P1 | 无 | 0.3d | 交易日比较（`latest_completed_trading_day`）；DDL 懒执行 |
| BK-46 | 后端 | NB_TURNOVER_CAP 开关仅后端可关、前端硬编码（前端注释自认会分叉）⇒ 开关一关破坏前后端 diff=0 验收基线 | `scoring/engine.py:221`（review/P0_scoring.md P2） | P1 | 无 | 0.2d | 开关值随评分接口下发（加列不加义），前端不再硬编码 |
| BK-47 | 数据流水线 | **`risk_calendar` 已注册但不在 DEFAULT_ORDER**（TASKS 43 键 vs ORDER 42 项）⇒ 生产唯一路径 `--tasks all` 从不执行；全仓无其他调用方 ⇒ C1 限售解禁底座静默断档（与 market_regime 断档同型） | `scripts/daily_batch.py:1198`（review/P0_infra_daily_batch.md P1） | **P0** | 无 | 0.1d | 补进 DEFAULT_ORDER 并跑 `--tasks all --list` 断言键集一致（加一致性检查）✅ 2026-10-24 已修复（commit e046534e：补单+模块级断言） |
| BK-48 | 后端 | `_is_stale_kline` 用 naive `datetime.now()` ⇒ UTC 部署收盘后约 8h 内把上一交易日 K 线判"新鲜"放行（08-28/08-31 信号事故同款判定口径） | `tencent.py:219`（review/P0_infra_daily_batch.md P2） | P1 | 无 | 0.2d | 改 `rules.beijing_now()` |
| BK-49 | 后端 | 行情刷新全失败时仍无条件用空 dict 整体替换缓存 ⇒ 网络故障 >2 分钟即清空全站行情 | `tencent.py:425`（review/P0_infra_daily_batch.md P2） | **P0** | 无 | 0.2d | 全失败时保留旧缓存并告警（宁陈旧勿清空，快照恢复路径同理） |
| BK-50 | 后端 | tencent 字段守卫 `len<50` 但访问到 `data[51]` ⇒ 50/51 字段行被 IndexError 静默丢弃；database DDL 执行前记 _DDL_DONE 失败不重试；SQLite upsert 用 INSERT OR REPLACE 忽略 conflict_columns 与 PG 分叉；lock_timeout 仅覆盖 DDL（5.5 分钟 hang 事故根因对 DML 仍在） | `tencent.py:313`、`database.py:148/258/169`（review/P0_infra_daily_batch.md P2） | P1 | 无 | 1d | 守卫长度对齐实际最大下标；DDL 失败移出 _DONE；SQLite upsert 改 ON CONFLICT 语义（冲突列删除重建）；连接级 statement_timeout |
| BK-51 | 后端 | **A4 交易计划触发功能静默死亡**：`flash_rules.get_china_market_status()` 引用未导入名字，NameError 被 `except: pass` 吞 ⇒ `is_open` 恒 False ⇒ 止损/兑现/买点提醒整条链不跑（start() 注释还声称该门存在） | `flash/scheduler.py:1421`（review/P1_flash_scheduler.md P1） | **P0** | 无 | 0.2d | 改 `rules.get_china_market_status()`；加"吞异常必须计数/告警"的 review 门✅ 2026-10-24 已修复（commit 5f2f50b7：改 rules 调用 + except 可见） |
| BK-52 | 后端 | flash_brief 自动解读日上限永不生效（date 主键表上 COUNT 恒 ≤1，FLASH_BRIEF_DAILY_MAX=8 形同虚设，仅剩 30min 冷却）；缓存 TTL 判断 naive/aware 相减必抛被吞 ⇒ "TTL 内读缓存"永不命中（潜伏成本漏洞） | `flash_brief.py:621`、`:479/:552`（review/P1_flash_scheduler.md P2） | P1 | 无 | 0.3d | 日上限改按行数/独立计数表；TTL 统一 tz-aware 比较✅ 2026-10-24 已修复（commit e826fb97：auto_runs 计数列 + TTL aware-aware） |
| BK-53 | 后端 | 反静默巡检兜底分支用未导入的 `time.strftime` ⇒ data_date 缺失时 NameError 被吞（巡检在最需要时静默失效）；K 线/指标每日刷新跨天重置窗口 `h==0 and m<5` 与 300s 轮询等宽 ⇒ 相位错开自锁停摆 | `flash/scheduler.py:275`、`:441/:496`（review/P1_flash_scheduler.md P2） | P1 | 无 | 0.3d | 兜底分支修 import 并入 fail 可见路径；重置窗口改 `>=24h 未刷新` 语义 |
| BK-54 | 后端 | rules.HOLIDAYS 仅 2026 ⇒ 2027-01-01 起节假日识别失效（is_trading_day 把假期当交易日） | `flash/rules.py` HOLIDAYS 常量（review/P1_flash_scheduler.md P3；MEMORY §2 已有"每年公告后追加次年"约定） | P1 | 国务院公告 | 0.1d | 2027 年假期加入；或加"跨年无数据即告警"防静默 |
| BK-55 | 前端 | 工作台 120s 主轮询十余个加载器不受 isLivePhase 门控 ⇒ 页面开一天整夜每 2 分钟持续请求（与自注释"仅交易时段真发请求"不符） | `frontend/src/views/Workbench.vue:4626`（review/P0_frontend_workbench.md P1） | P1 | 无 | 0.3d | 非交易时段仅保留必要项或停轮询；压测"挂一晚"请求量✅ 2026-10-24 已修复（commit d65c9e71：主 120s 轮询按变量时效分组，夜间仅留 loadGlobals/loadFlashDiag 两个 24h 变量；coach/radar/push 同步挂门控） |
| BK-56 | 前端 | 本地评分模式夜间每 5 分钟触发一次全市场 computeRanking（`!_fresh` 过期使复用窗口失效，与注释意图相反） | `frontend/src/views/Workbench.vue:4012`（review/P0_frontend_workbench.md P2） | P1 | 无 | 0.2d | 复用窗口语义修正（陈旧 ⇒ 显式降级而非静默重算）✅ 2026-10-24 已修复（commit d65c9e71：复用窗口失效仅在 isLivePhase 时重算） |
| BK-57 | 前端 | `bg-background` 非 tailwind 令牌（dist CSS 无该规则）⇒ 日期下拉深色主题露系统默认色；401 跳转用 path `/login`（hash 路由）且二次过期条件永久失效；tencent.js 前缀映射不识别北交所 4/8/92 ⇒ 本地评分池悄悄缺票 | `Workbench.vue:19`、`api/index.js:57`、`api/tencent.js:87`（review/P0_frontend_workbench.md P2） | P1 | 无 | 0.5d | 换合法令牌；401 用 hash 路由跳转并修判据；前缀映射补北交所（与后端 `_limit_pct` 号段同源）✅ 2026-10-24 部分修复（commit c67ff3fc：401 跳转两项；tencent.js 北交所前缀/bg-background 令牌两项仍留）✅ 2026-10-24 已修复（commit c67ff3fc 401 跳转 + d65c9e71：bg-background→bg-card×5（全仓 grep=0）+ tencent.js 北交所前缀映射（node 14 用例快测全对）） |
| BK-58 | 后端 | **教练 message 大量祈使句**（"按纪律离场/优先在反弹中减仓，不追加/准空仓运行只减不加"），免责前缀只覆盖 coach_tick 推送路径，体检卡/落库路径裸奔——违反 Guardrails #5 | `coach/rules.py:355/:401/:429/:587`（review/P1_coach_watchers.md P1-1） | **P0** | 无 | 0.5d | 改事实描述 + 全部输出路径统一免责前缀；重查 position_sizing 措辞✅ 2026-10-24 已修复（commit b999db26：4 条祈使句改事实陈述 + 源码级禁词锁测试） |
| BK-59 | 后端 | **市场底清单跨 4/5 幂等标记自毁**：防重推把 crossed_4 强制改 False 并落库，而落库值恰是下次 already_crossed 判据 ⇒ ≥2 次重跑会重复推企微 | `policy_bottom_watch.py:768`（review/P1_coach_watchers.md P1-2，本次审查最重发现之一） | **P0** | 无 | 0.3d | 标记只写不回抹；重跑幂等用独立 pushed_at 判据✅ 2026-10-25 已修复（commit 5513b31d：crossed_4 只写不回抹 + first_cross 独立信号；冒烟：跨过→重跑×3 推送信号 [True,False,False]、回落日不回抹） |
| BK-60 | 后端 | scenario_watch 对 pbw"不足"分支标量取 `[-1]` ⇒ TypeError 整卡当日失败；银行/创业板/ETF 信号缺失记"中性"而非"不足"；ETF chg_pct 缺失被 `or 0` 记 0%（违反缺失≠0） | `scenario_watch.py:235` 及同文件（review/P1_coach_watchers.md P2） | P1 | 无 | 0.3d | 类型守卫；缺失显式 insufficient✅ 2026-10-25 已修复（commit d765902c：_last_of 标量守卫 + 银行/创业板/ETF 缺失显式"不足" + ETF or0 修复 + 文档头 3550→SC_SH_LAST；整卡冒烟通过） |
| BK-61 | 数据流水线 | **publish orphan 重建残余风险**：本地缺失的 PACK_FILES 只打印跳过就继续（线上同名文件被整批删除）且 kline_date="" 时护栏 4 整体失效；dist 缺失提示与 orphan 实际行为矛盾；回滚护栏只比对 kline 包（backend/indicators 包可被 --force 回滚） | `scripts/publish_packs_local.py:226/:216/:273`（review/P1_pack_scripts.md P1-1~3） | **P0** | 无 | 0.5d | 缺文件改为硬失败；护栏覆盖全部三包；提示语与实际行为一致✅ 2026-10-24 已修复（commit 8ca3ab4b：缺包/缺 dist 硬失败 + 远端比对扩到 indicators，--dry-run 实测拦截命中） |
| BK-62 | 数据流水线 | update_backend_pack_incremental 的 date 仍用日历今天（update_pack_incremental 的同款问题 10-24 已修、此处漏修）⇒ 周末/盘中运行产生混日期包或被护栏逼出 --force | `scripts/update_backend_pack_incremental.py:342`（review/P1_pack_scripts.md P1-4） | P1 | 无 | 0.2d | date 改最近已完成交易日（与 update_pack_incremental:324 同修法） |
| BK-63 | 后端 | coach `REAL_STOP_LOSS_PCT=-8.0` 硬编码违反自家"阈值一律 yaml"约定；`_macro` 失败不缓存（同文件 `_usd5` 已修同款 ⇒ "只缓存成功=没缓存"复发点） | `coach/rules.py`（review/P1_coach_watchers.md P2） | P2 | 无 | 0.3d | 阈值进 yaml；失败路径短 TTL 缓存✅ 2026-10-25 已修复（commit 见 coach/rules+yaml：阈值进 rules.yaml（env 可覆盖）+ _macro 失败占住 600s TTL；冒烟：yaml/env/失败尝试次数=1 全过） |
| BK-05 | 后端 | E2 事件信号涨停用 `change_pct>=9.9` 近似（真值限价已在内存行情可得） | `events/signal.py:37`、`:237`（有 note 自我披露；review/_pattern_scan.md limit 段） | P1 | 须预登记披露口径切换（E2 历史结论按旧口径） | 0.5d | 家数与 `limit_stats` 同口径；`SIGNAL_STATUS` 标注切换日 |
| BK-06 | 后端 | mainflow_history 缺 code 索引（逐股查询全表扫；lhb 同场景已补） | `mainforce/flow.py:711`（review/P0_mainforce.md P2） | P1 | 无 | 0.1d | `CREATE INDEX idx_mainflow_code(code, trade_date)`；点查 <10ms✅ 2026-10-24 已修复（commit ea129843：PG/SQLite 双分支补 idx_mainflow_code；幂等索引已在生产库生效） |
| BK-07 | 后端 | 空浮筹表被缓存 6h，与"空结果不写盘"惯例矛盾（price_pos 漂移） | `mainforce/gate.py:201`（review/P0_mainforce.md P2） | P1 | 无 | 0.5d | 统一"空结果短 TTL + 显式 reason"；重放无阶跃 |
| BK-08 | 后端 | refresh-status 用 naive `fromtimestamp`（生产 UTC ⇒ 显示差 8h；同文件已两次裁决同型坑） | `routers/market.py:539`（review/P0_routers_scoring_market.md P2-6） | P1 | 无 | 0.1d | 显示时点=北京时间（tz-aware）✅ 2026-10-24 已修复（commit 20460586：显式 UTC+8） |
| BK-09 | 后端 | `market_emotion_daily.date` 用写入自然日而非数据交易日（"写入时刻≠数据所属时刻"第④案，唯一未修） | `routers/market.py:999`（`day = _bj_now().strftime`）+ MEMORY §2④ | P1 | 无 | 0.5d | 键=交易日（复用 `latest_completed_trading_day`）；消费方（policy_bottom_watch）回归✅ 2026-10-24 已修复（commit 20460586：锚定 _price_date，回放不落库；含 2 条回归测试） |
| BK-10 | 后端 | bucket/composite 缓存键漏 `days/horizon` 参数（改参数返回旧缓存） | `routers/scoring.py:717/747`（review/P0_routers_scoring_market.md P2-3） | P1 | 无 | 0.2d | 改参数后缓存失效（键含全参）✅ 2026-10-24 已修复（commit e7d2bd46：缓存键含 days/horizon） |
| BK-11 | 后端 | 换手率兜底路径 `np.convolve(mode="same")` 零填充 ⇒ 无流通股本个股换手率高估约一倍，恰污染末根 | `mainforce/chips.py:96`（review/P0_mainforce.md P2） | P1 | 数值阶跃需披露 | 0.5d | 末根换手与手写窗口均值一致；日报披露切换日 |
| BK-12 | 后端 | mainforce_state bars 全空时失败率防护被 `if total and` 短路 ⇒ 空跑标记成功 | `mainforce/state.py:280`（review/P0_mainforce.md P2） | P1 | 无 | 0.2d | 空跑显式失败计数，日批可见✅ 2026-10-24 已修复（commit e826fb97：空跑 raise，冒烟验证） |
| BK-13 | 后端 | lhb seats 解析只认顶层 traders（嵌套结构静默不落库）；SQLite 分支 INSERT OR REPLACE 抹历史 seats_json/circ_mktcap | `mainforce/lhb.py:130/174`（review/P0_mainforce.md P2） | P1 | 无 | 0.5d | 嵌套 seats 落库；SQLite 分支 COALESCE 合并✅ 2026-10-24 已修复（commit 342733f7：嵌套 traders 兼容 + sqlite ON CONFLICT COALESCE 保历史） |
| BK-14 | 数据流水线 | LHB 月度复检在线上走 pack 备源口径（与 428 日本地定案口径数字不同 ⇒ 月度企微噪音） | `docs/plans/PLAN_NEXT.md` §B5（处置①②③未做，用户 2026-10-14 决定暂缓） | P1 | 用户拍板 | 0.5d | 注册表 skip 或报告显式标注"备源口径" |
| BK-15 | 后端 | `mf_edge_check` 首跑未执行（判据 v1.0 已冻结、回填一致性闸 99.0% 已过，等用户发令） | `docs/plans/PLAN_NEXT.md` §C4、`docs/plans/PLAN_MAINFORCE_EDGE.md` | P1 | 用户发令 | 0.1d | 首跑结论写回 PLAN_MAINFORCE_EDGE §9 |
| BK-16 | 后端 | A1 评分 T+20 次判据到点（≈2026-10-23），核对月度 `edge_verify` 是否已出结论 | `docs/plans/PLAN_NEXT.md` §A1 | P1 | 无 | 0.1d | 结论写回 `PLAN_SCORE_EDGE.md` §9（FAIL/PASS 均如实） |
| BK-17 | 数据流水线 | 指数 K 线/ETF 份额修复在本地未 push ⇒ Actions/Render 仍跑旧代码 | MEMORY §9「这些修复在本地未 push 时，线上仍跑旧代码——必须先推」 | P1 | 用户执行 git push | 0 | 线上 `/api/health.build` 对准本地 HEAD |
| BK-18 | 前端 | Workbench.vue 4713 行单文件组件（P0 清单最大文件） | manifest.json（loc 4713）+ review/P0_frontend_workbench.md | P2 | 无 | 2d | 按卡面拆子组件；`npm run build` 0 error |
| BK-19 | 前端 | Watchlist / Portfolio 仍走后端 `getStockScore`，未统一到前端本地评分引擎（与排行榜/详情页同源性缺口） | `frontend/src/views/Watchlist.vue:223`、`Portfolio.vue:413`（`getStockScore` import）+ CONTEXT.md 已知问题#2 | P2 | 无 | 2d | 两页评分与 `scoreStock` 同源（同股 diff=0） |
| BK-20 | 后端 | indicatorWorker 指标全量重算 TODO（未实现增量更新） | `frontend/src/workers/indicatorWorker.js:307` | P2 | 无 | 1d | 增量追加当日 bar 而非全量重算 |
| BK-21 | 后端 | A2 数据源扩容待办（"不报错不崩但不产生信号"，需架构调整） | `backend/app/tencent.py:48` 注释；选型分析 `docs/reports/A2_价格源扩容_选型分析_20260929.md`（未立项） | P2 | 立项 | — | 立项后按选型文档排期 |
| BK-22 | 后端 | 板块关联查询 TODO（strategies/base 需板块成分股数据） | `backend/app/strategies/base.py:118`；判定：zzshare 104 粗分与 49 行业仅 4 个同名（MEMORY §9）⇒ 现有源不可行 | P2 | 板块成分股数据源选型 | — | 换源可行性结论先出 |
| BK-23 | 后端 | C6 龙虎榜游资席位信号检验（已登记未立项；判据未冻结不得跑数） | `docs/plans/PLAN_NEXT.md` §C6（数据底座已备：2,408 条游资记录） | P2 | 用户点头 → 冻结判据 | — | `PLAN_LHB_YOUZI_EDGE.md` v1.0 冻结后才跑 |
| BK-24 | 后端 | C1 regime FAIL 后续三选一（滞后修偏 / 接受叙事定位 / 月度复检跟踪） | `docs/plans/PLAN_NEXT.md` 附 A 首跑 FAIL（−0.297pt/20 日，KILL 成立） | P2 | 另行预登记 | — | 任一路线须新预登记文档 |
| BK-25 | 后端 | 春节效应：企微推送、日报小节未做（calendar_exception 已上线） | MEMORY §14「**未做**：企微推送、日报小节」 | P2 | 无 | 0.5d | 推送走 `push_markdown_batched` 唯一入口；与持仓告警防重复 |
| BK-26 | 后端 | live_ranking `_build_pool` 注释自称"复刻 score_top 的池子"——复刻即漂移源 | MEMORY §10（快照 22:43 永远赶不上 Actions 精算榜） | P2 | 无 | 1d | 两处池构建抽公共函数，单一实现 |
| BK-27 | 后端 | LLM 复盘超时（DeepSeek-R1 600s，Render 0.1 CPU） | CONTEXT.md 已知问题#1（缓解：窗口内重试已修）；根因=模型性能 | P2 | 换更快模型决策 | — | 超时率 <10%（月度观测） |
| BK-28 | 数据流水线 | 战法 × regime 分层样本不足（ADMISSION_MATRIX 仍是专家规则初始版） | CONTEXT.md 已知问题#4（防御市全禁 ⇒ 样本等 regime 转好） | P2 | 等样本（可能数月） | — | 高波动期覆盖后重跑 `--strategy regime_warfare` 校准 |
| BK-29 | 文档 | 根目录 4 个临时脚本 `_tmp_*.py` 待删（Guardrails/DEV_CHECKLIST 要求用完即删，本次不删只列清单） | `_tmp_dr_check.py`、`_tmp_live_etf.py`、`_tmp_pack_cost.py`、`_tmp_runs_today.py`（manifest buckets） | P2 | 用户确认删除 | 0 | 用户批准后 `git rm`（红线：删除须确认）✅ 2026-10-25 已执行（commit 42d5ded6：用户批准后 git rm 四个脚本） |
| BK-30 | 数据流水线 | frontend/dist 构建产物入库（污染全仓 grep/审查；压缩 JS 被 manifest 当源码统计） | `frontend/dist/assets/*.js` 在 git 内（_pattern_scan.md limit 段命中） | P2 | 先查 CI 是否依赖仓内 dist | 0.2d | 若无依赖：用户批准后 `git rm`；有依赖则从 manifest 排除✅ 2026-10-25 判定：**前提不成立**——frontend/dist 从未被 git 跟踪（frontend/.gitignore:5 `dist/`），manifest 的 SKIP_DIRS 也已含 dist；审查与上轮台账均基于文件系统扫描误报，未验证 git 跟踪状态（教训记入勘误） |
| BK-31 | 后端 | market_realtime size 无上限 / tail-review、limit-review 每请求外呼无冷却 / `_first_seen_map` 失败缓存 1h | review/P0_routers_scoring_market.md P3（scoring.py/market.py） | P2 | 无 | 1d | size 上限 + 冷却；失败缓存 TTL ≤10min（"降级路径也要缓存"的反例修正）✅ 2026-10-25 已修复（commit 1b734824：size 收口 [1,200] + _index_snapshot 60s TTL + limit-review 直连 300s 冷却 + _first_seen_map 失败 600s/成功 1h；冒烟 4/4） |
| BK-32 | 后端 | 中文日志违反 ASCII 铁律（routers/scoring.py 等） | review/P0_routers_scoring_market.md P3；Guardrails #9（生产 GBK 编码问题） | P2 | 无 | 0.5d | 生产日志抽查无中文（中文提示走返回值） |

## B. 等时钟 / 等拍板（非开发项，登记防催办）

| 事项 | 时点 | 证据 |
|---|---|---|
| A2 回避侧反向检验（唯一含小开发：`score_edge_check --source=live`，**达标前不实现不看数字**） | ≈2026-11 中旬 | PLAN_NEXT §A2 |
| A3 shadow_rank 权重解冻复检 | ≈2026-11 中旬 | PLAN_NEXT §A3 |
| C5 缩量启动形态前瞻样本（闸 ≥20 信号日 ∧ ≥120 观测） | 1~3 个月 | PLAN_NEXT §C5 |
| B 类待拍板 | **0 项** | PLAN_NEXT 开工摘要 |

## C. 判定为已失效 / 已完成的旧 TODO（淘汰清单）

| 原条目 | 证据 | 判定 | 理由 |
|---|---|---|---|
| `scripts/news_score_validation.py:4`（消息分 vs 未来收益验证） | PLAN_NEXT 变更记录 2026-10-18 | **已完成（FAIL）** | 按日 IC 均值 T+2 −0.003 / T+5 +0.004，CI 跨 0 ⇒ 无预测力，维持独立展示不进综合分 |
| MEMORY §0「daily_batch.py `_batch_already_done()` 改 batch_runs 未提交」 | `scripts/daily_batch.py:1405-1440` 已在库 | **已提交** | 判据已从 daily_reports 改为 batch_runs 表并入库 |
| CONTEXT.md 已知#3「指标刷新迁 GitHub Actions」 | MEMORY §3：2026-10-22 kline-data/backend-pack 已改跑增量脚本 | **已实施** | Actions 全新 runner 的缓存缺失已由"并入包/窄化读取"消化 |
| CONTEXT.md 已知#5「消息分回测验证」 | 同第 1 条 | **已完成（FAIL）** | 同上 |
| 模拟盘卖出侧跌停顺延「待做」（CONTEXT 引 PLAN_NEXT_PHASE） | `strategies/paper_trading.py` `_limit_down_locked`（CONTEXT 关键文件清单） | **已完成** | 卖出侧顺延已实现（2026-09-05 批次） |
| PLAN_2026-09-12.md 旧看板 36 项未勾选 | `docs/reviews/AUDIT_PLAN_2026-09-12.md`（36 项逐项核验） | **已收口** | 6 项关闭/前提消失、7 部分、14 低价值未做、其余完成；8 项真待办已并入本表与 PLAN_NEXT |
| `frontend/src/views/Workbench.vue:2717/4064`「待办」字样 | 上下文为 UI 文案（右栏待办卡） | **非待办** | 界面用语，非 TODO 标记 |
| `frontend/dist/assets/*.js` 内 `>=9.5` 命中 | 构建产物 | **非源码** | 处置见 BK-30 |
| `strategies/*` 涨跌停 `>=9.5` | MEMORY §7「**战法定义**，改它作废既有回测证据，须预登记」 | **刻意保留** | 不是口径事故残留；改动须预登记 |

## 统计

- A 表：69 条（P0×18 / P1×35 / P2×16）；
  按模块：后端 55 / 前端 6 / 数据流水线 7 / 文档 1。
- B 表：4 项等时钟；B 类待拍板 0。
- C 表：9 条淘汰（完成 5 / 已提交 1 / 非待办 3）。

> P0 18 条超出 EXECUTION_PLAN「P0 ≤ 10」的建议值：本次为全量代码审查首次盘点
> （历史积累一次性浮出），未做人为压缩；建议修复顺序按组推进——
> 安全鉴权组（BK-04a~d、39）→ 静默死亡组（BK-47/49/51/59）→
> 红线口径组（BK-05/34/42/58）→ 撮合与数据正确性（BK-33/61）→ 性能组（BK-01/03/04）。


---

## 台账同步：2026-10-26（aa23e88e → 1d5487b3，31 commits，主干合入后补记）

> 本节由 git 提交记录回填（其余各表数字未随之改动；下列事项**均已完成**，commit 可查）。

### 已完成（按主题归组）
| 主题 | 内容 | commit |
|---|---|---|
| **评分重构 H2'（方案 §7 冻结项）** | 阴跌防御影子榜上线：独立标签页 + 工作台防御 Top10 + 盘中实时预览（live=1 同一冻结公式） | cce9d950 / 19400d6a / 86bca76e |
| 防御组扩宽 | 宽防御组（银行∪高股息代理∪低波）+ 前端本地精算实时榜；只读候选 K 线（分块 150）替代整表 41MB | 10c68515 / d9ec46d1 |
| 性能：/batch/top | 非交易时段直接服务库内全量榜（修周末反复精算） | b15cccc7 |
| 性能：mainflow 日更 | 当日缺口改全市场批量（~1min）+ 日更改准增量——修每晚 53 分钟重跑 | 86c929e1 / 542c1744 |
| 性能：索引 | mainforce_state(date) 单列索引（按日过滤整表扫描→索引） | 02d8b662 |
| 修复：白屏 | score 解构错名整页白屏（fund→score）+ 卫生门新增解构错名规则、清 9 处静默异常 | 406f0e4 / 54925053 |
| 修复：主力行为 | 资金流补洞覆盖评分宇宙 + 主力列空值退档显示阶段 | e1096548 |
| 修复：BK-47 同型 | defensive_group 补进 DEFAULT_ORDER（TASKS/排单自检拦截后修复）+ 手动补跑白名单 | 19b93735 / 202ba1e5 |
| 新功能 | 流动性三件套观察器（接口+前端卡）；李大霄视角卡（xiaopage 事件推送） | 22860a33 / d9973f9e |
| 运维 | 数据库流量归因快照（日批任务 + CLI） | d92235d0 |

### 对既有 BK 条目的判定
- BK-55/56（前端轮询/夜间重算）：b15cccc7 属同族延伸（周末 /batch/top 反复精算）——**已在服务端另修**，原前端两项维持已修状态。
- BK-47（TASKS/排单错配）：机制自检**已两次拦截真实错配**（risk_calendar 原案 + 本次 defensive_group）⇒ 防复发机制有效性获实证。
- 其余 P0/P1 未动条目（BK-01/02/04/33 等）状态不变，仍待排期。
