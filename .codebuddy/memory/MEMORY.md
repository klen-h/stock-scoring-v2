# 长期记忆（stock-scoring-v2）

> ## 接续方式（换账号 / 新会话 / 换机器）
> 上下文 = 本目录 Markdown，与账号无关，已 git 跟踪。
> 新会话第一句：`读 .codebuddy/memory/MEMORY.md + 最近 2 天 daily，复述进展与未完成项再继续`。
> 阅读顺序：本文件 → 最近 1~2 天 daily → `PLAN_NEXT.md` 顶部「开工摘要」。
> ★ 本文件只放**跨会话必须记住的结论与铁律**；完整经过、证据、逐条验证记录在各日 daily 里，此处不重抄。

## 0. 当前状态（2026-10-19）
- 未提交：`scripts/daily_batch.py` —— `_batch_already_done()` 判据由 `daily_reports` 改为**自建 `batch_runs` 表**。
  原因：Render 的日报兜底 loop 19:30 就写了 `date=10-19` 的日报，早于日批 20:17 ⇒ 第一版守卫把**当天整批跳过**
  （实测 10-19 只剩 19:31 那条日报，`market_snapshot`/`contradictions`/`gate_snapshot_history` 全停在 10-16）。
  ⇒ **"批次是否完成"必须用只有日批会写的表**，`daily_reports` 有多个写入方。
- 权威待办 = `PLAN_NEXT.md` 顶部「开工摘要」；**B 类待拍板 = 0**。
- 只能等时钟：A1 评分 T+20（≈10-23）、A2 回避侧 / A3 影子榜解冻（≈11 月中）、C4/C5 等前瞻样本、C6 未立项。

## 1. 架构与运行形态
- 单进程 FastAPI（`backend/app/main.py`）`/api/*` + 前端静态；生产 `uvicorn 0.0.0.0:8000`，**不要 `--reload`**。
- 前端生产 = GitHub Pages（pnpm 构建推 gh-pages）；Render 后端兜底 dist。
- DB 默认 SQLite，`DATABASE_URL` 走 PG（Supabase 东京）；`app/database.py` 自动转 `%s` 兼容双库。
- 调度器 `flash/scheduler.py` ~30 个 asyncio loop 按日幂等；**`RENDER_READ_ONLY=1` 下所有 `_heavy` 循环不跑 = 死代码**，
  重计算一律迁进日批（`scripts/daily_batch.py`）。★ 反复模式：Render 循环停摆 ⇒ 数据静默断档（已发生 5 次）。
- 盘后链路（北京，实测）：18:00 kline-data → 19:00 backend-pack → 20:17 完成 → 日批 20:17~22:39（`workflow_run` 接棒，日批无自己的 cron）。
- ★★ **重复日批的真因**：`backend-pack.yml` 兜底 `schedule: 0 14 * * 1-5`（UTC=BJ 22:00）常被 GitHub 延迟 3~4h
  到次日 01:50；guard 秒跳过但 run 仍 `success` ⇒ `workflow_run` 把日批**全量跑第二遍**（10-17 01:50~03:25）。
  ⇒ 才有 `_batch_already_done()` 守卫。**别用库里时间戳反推调度时区**（cron 一直是 Asia/Shanghai）。
- ★★ **日期键两派 + 循环守卫铁律**：`*_loop` 必须用 `rules.is_trading_day()`（`weekday()<5` 会把节假日当工作日）
  **且显式传 `latest_completed_trading_day()`**（不传 ⇒ 内部 `_today()` = 运行日 ⇒ 写出"键=假日、正文=上一交易日"的错位行）。
  `scheduler.py` 21 处守卫已统一；**刻意保留**：`_latest_trading_day`（自身即节假日实现）、
  `trade_plan_trigger_loop`/`paper_track_loop`(盘中分支，另有 `get_market_status()["is_open"]`)、
  `zz_finance_sync_loop`(周一)、`backtest_report_loop`(周五/周六) —— **别误改**。
- 同一批日批内日期键也不一致：`ranking_history`/`industry_mainline`/`sector_amount_daily` 用运行日，
  `plate_daily_zz`/`mainforce_state` 用交易日。查"未来日期键"先看这条。
- 节假日键清理：`scripts/cleanup_holiday_keys.py`（分表策略：交易日键类只删「上一交易日已有」的重复行、孤儿行改键；
  **运营计数表 `llm_usage_*`/`push_log` 按自然日统计属设计，显式跳过**）+ 日批兜底 `data_hygiene`。

## 2. 时间 / 日期口径（铁律）
- "数据所属日期"一律 `rules.latest_completed_trading_day()`（15:00 分界 + 跳周末/HOLIDAYS），绝不用北京自然日。
- **凡"落后/停更/过期"一律比交易日**，不比自然日（长假误报）。两把尺子：15:00（业务）vs 22:00（包可用下限）。
- ★★★ **"写入时刻" ≠ "数据所属时刻"** —— 本项目已踩四次，落库前必问一句：
  ① `market_snapshot.saved_at`（≠`data_date`，10-14 修）；② 模拟盘 `exit_date`（10-18 修，改 `exit_price_date`）；
  ③ 行情陈旧巡检比 `data_ts` 的日期（快照态=写入时刻 ⇒ 零告警，10-18 修）；④ `market_emotion_daily.date` 仍用写入日（**未修**）。
  ⇒ 对外展示"行情是哪天的"只能用 `data_date`；`from_snapshot=True` 要带 ⚠ 徽章。
- ★★ `tencent._cache`：显示时点用 `data_ts`+`from_snapshot`，**绝不用 `last_update`**（=填充时刻，快照恢复故意设"现在"）。
  已暴露 `quote_as_of/quote_from_snapshot`；`stock_cache_refresh_loop` 巡检 data_ts。
- ★ **"只在交易时段跑"的循环 + 盘后重启 = 静默陈旧**：非交易时段必须有"库里有没有更新快照"的路径
  （`tencent.sync_snapshot_if_newer()`，10-18 上线）。
- ★ 时区：`datetime.now()` 一律 `flash.rules.beijing_now()`；`fromtimestamp` 不带 tz 走服务器本地（生产 UTC）差 8h。
- 批任务日期由调用方传入（`_batch_trading_day()`）；"连续 N 天"按交易日步进；判任务完成看 `created_at`。
- `routers/scoring.py` 里"榜是否今天"继续用 `_today_bj()` 自然日（新鲜度判据）**不要换**。
- 春节：**每年国务院公告后把次年假期加进 `flash.rules.HOLIDAYS`**（precision 自动升 actual）。

## 3. 数据与缓存
- ★★ 腾讯 qt 快照字段下标实测（勿按文档抄）：`[3]现价 [4]昨收 [6]量(手) [7]外盘 [8]内盘 [9..28]五档
  [31]涨跌额 [32]涨跌幅 [37]成交额(万) [38]换手 [39]PE [41]高 [42]低 [43]振幅 [44]流通市值(亿)
  [45]总市值(亿) [46]PB [47]涨停价 [48]跌停价 [49]量比 [50]委差（买五−卖五，手）[51]均价 [57]成交额(万,重复)`。
- 涨速自算（腾讯无该字段）：`_SPEED_CACHE` + `_record_speed()` **钩在 `refresh_all_stocks` 尾部**（不能在 `_fetch_tencent`，
  否则快照恢复会把收盘价当实时价）。仅 `is_open` 时采样；**数据不足返回 None 不返回 0**；实际间隔≈2min ⇒ `speed_1min` 基本恒 None。
- 竞价落库：`market_emotion_daily` 三组互不覆盖字段（主 / `close_*` / `auction_*`）；只在 10 分钟窗口写；
  `_emotion_daily_row` 必须组装整行（SQLite `INSERT OR REPLACE` 缺列会清空）。
- `kline_cache` 36h；`kline_count∈(0,250)` 视为截断跳过。**`kline_cache`/`indicator_cache` 是生产表**（评分引擎靠它避免回源），不可当缓存清。
- 本机持久缓存统一模式（flow/l3/emotion-closes/ind-dist-src）：进程内 → 本机 gzip/json → 回源；指纹单行查询；取不到指纹不走缓存；空结果不写盘。
- ★ **2026-10-19 起 `app/industry_map.py` = 个股→行业映射的唯一缓存入口**（1h，`get_industry_map()` / `invalidate()`），
  已接 `mainline` / `stock_moves` / `earnings_pressure` / `sector_industry` —— 此前 4 处各自整表读 5932 行。
- 两条包发布链独立：`kline-data.yml`(18:00)→前端包 + realtime-quotes；`backend-pack.yml`(19:00)→`backend-pack.db.gz`（日批唯一依赖）。
- ★★ `pack_source` 读侧 = 「命中包→用包；**未命中→回退 DB**」⇒ **包的覆盖口径必须 ⊇ 所有消费方池**。
  2026-10-10 修过一次（回测池 129 只 + 基准指数被质量过滤剔掉 ⇒ 43MB/天）。**质量过滤只应筛新增候选，不能筛既有消费方代码**。
- ★ `local` 模式包陈旧 ⇒ 整份静默回退 DB（只 print 一次警告）⇒ 用 `DATA_SOURCE=local` 前先 `python scripts/sync_local.py --force`。
- ★ 排障第一站（公开）：`GET /api/score/kline-cache/status` ⇒ `{total_cached, newest_update, source}`。
- ★ 本地零 egress 历史库：`data/zzshare_daily.db`（4.2GB，`daily` 1672 万行 / 6123 只 / 2005-01-04~2026-09-24，
  含 `high_limit/low_limit/factor/turnover_rate` + `_code_industry` + `meta` 交易日历）；`backend/data/pack/backend-pack.db`（klines/indicators/codes，
  仅沪深主板+创业板，**无 688/北交**、股票池是"当前"的 ⇒ 有幸存者偏差）；`data/rescan.db`（战法重扫）。
  ⚠️ 它们在工作区根，`search_file` 看不到 ⇒ **盘点本地资产用 os.walk**。
- ★★ 两源日线口径：`backtest_prices.close` 与 zzshare `daily.close` **逐日完全一致（原始价）**，可互认但**不能混**。
  zzshare `daily.factor` **不是前复权乘数** ⇒ 算收益用**原始收盘比**（除权跳空未复权，已知简化），需除权后单日收益用 `pct_chg`。

## 4. Supabase 容量 / egress
- 账号级 5GB/月（本地 + Render + Actions 共用）；2026-10-18 实测 430MB/天。库容已 409→275MB。
- ★★ **2026-10-19 复核（面板 190MB/天）**：探针 `cleanup_20261018`→`today1019`（32h）≈**166MB**
  （按整行宽算的**上限** ⇒ ≈123MB/天）；对照 `now1013`→`cleanup_20261018` 是 **304MB/天**
  ⇒ 10-18 的清理确实见效（`backtest_prices` 从 660 万行/5 天降到 4.9 万行）。
  今日大头（探针口径）：**mainflow 整表读 9 次 31MB**（来自研究脚本/`research_cache`，**不是日批**）、
  `strategy_results` 28MB（战法信号映射，多进程各持缓存）、`coach` 列表+归因 ≈27MB、
  `stock_industry` 12MB、`macro_daily` 8.6MB、`macro_history` 7.2MB。
  ★★ **通用坑 1："只缓存成功"等于没缓存** —— `_usd_5d_change` 在"数据不够/序列过期/异常"分支
  直接 `return None` **不写缓存** ⇒ 每次调用都整表读（32h 实测 515 次 / 4.6k 行）。**降级路径也要缓存**（短 TTL）。
  ★★ **通用坑 2：多个进程各持一份进程内缓存**（Render + 本地后端 + 脚本）⇒ 调大 TTL 的收益按进程数打折；
  判断"某语句为何一天几百次"先问"是不是有 N 个进程"。
  ★ 探针本身：`python scripts/egress_probe.py snapshot --tag X` / `diff --from A --to B` / `top`；
  Windows 先 `$env:PYTHONIOENCODING="utf-8"`（脚本 print 含 ✅，GBK 会崩但快照已落盘）。
- ★★ **研究数据绝不落 PG**（`mf_edge_daily` 曾 134MB，权威副本是本地 `backend/data/mf_edge_daily.db`）⇒ 新研究一律写本地 SQLite。
- ★★ **读 `backtest_prices` 必须带窗口**：批量用 `ROW_NUMBER() OVER (PARTITION BY code)` 一次查询取全池最近 N 根
  （形态扫描 178s→6.4s、45MB→8.6MB/天）；**禁止逐只往返**。`load_flow_map` 生产必须传 `days`（整表读≈8.7MB/次）。
- ★★ 包新鲜度/预热类"靠 Render 循环保活"在只读模式**等于从未生效**（`backtest_preheat` 停在 09-22）。
  已迁日批 `task_backtest_preheat`；消费侧加"陈旧≥6h 且距上次≥30min 才刷新"。
- ★ Actions 每次全新 runner ⇒ `backend/data/` 下所有本机缓存全失效 ⇒ 没进数据包的库表每次整表读。只有两条路：并入包 / 窄化读取。
- ★ Supabase 长查询会 SSL 断连 ⇒ 大表读按 code 分块（120/块）+ 重试 + `db._reset_pg_conn()`。
- 排障：`pg_stat_statements` 按 rows（必查 `stats_reset`）；探针 `scripts/egress_probe.py snapshot/diff`。
  报告里带 `≤` 的是**上限**（按整行宽估，可虚高两个数量级）。
- Render free = 500MB / 0.1CPU；缓存四件套：条数上限 + 单条上限 + 取用时物理删除 + **字节级上限**
  （`strategies._PRICES_BARS_MAX=50000`、`flow._FLOW_MAP_ROWS_MAX=120000`，超限整体清空）。
- `cache_release.py`：盘后 19:00 每日一次 + RSS≥75% 自适应（120min 防抖）；**绝不释放** `tencent._cache`（收盘定稿）、
  `flash.*`、`wechat._token_cache`、`signal_bus`、`sync_meta`。

## 5. 前端纪律
- ★★ **观点输出**（LLM 日报/简报/复盘/矛盾报告/雷达/一页纸/事实问答）一律 `class="md-body"` + `v-html="renderMd(text)"`；
  渲染器 `composables/markdown.js`、样式 `styles/md.css` **全站各只有一份**（`main.js` 引入）。
  **绝不再写 `whitespace-pre-wrap` 裸文本**（会把 `**加粗**` 显示成星号）。
  **刻意例外**：`ScoreRank` 推送时间线用 `contentSegs()` 做「名称(代码)→详情页」分词，保持裸文本；**不要为它开 `html:true`**（安全底线）。
- ★★ **观点卡 + 一句话结论（L2）**：卡片内嵌观点用 `components/InsightBody.vue`（结论置顶为琥珀结论条 + 依据可折叠）；
  整页长文仍用 `md-body`。契约：生成侧首行输出 `**结论**：<一句话>`；解析 `markdown.js::splitInsight` **只认显式标记，
  绝不猜"第一段就是结论"**；**无标记 ⇒ 完全按普通 md 渲染**（旧数据零变化，这是能安全上线的唯一理由）。
- ★★ **结构化结论（L3）**：后端 `app/insight.py::parse_insight` 唯一入口（JSON → 逐字段校验 → `{conclusion, reasons[]}`，
  同时组装 markdown ⇒ 新老通道同源）。**解析失败 = 原文照旧渲染**（`structured=False`），永不比改造前差。
  已接：雷达、一页纸诊断（★ **缓存命中分支也必须带这两个字段**）；纪律：结论 >120 字 ⇒ **丢弃不截断**；越界词 ⇒ 整段失败。
- ★ 重点层级（改前先读 `md.css` 头部注释）：`h2/h3` 左侧色条、`strong` 提亮纯白、**`> 引用块` = 琥珀结论条**。
- ★★ 用色纪律：`rise/fall`（红涨绿跌）+ `displayMeta` 的"绿=机会/红=风险""红=净流入"已占满红绿 ⇒
  **强调不得用红/绿**，只用琥珀/蓝/白灰阶 + 结构；"重点"必须稀缺（单条结论 ≤1~2 处）。
- ★★ **顶栏浮层**：`<Teleport to="body">` + `fixed` + `max-h-[calc(100vh_-_Nrem)]` + `overflow-y-auto overscroll-contain`；
  水平居中 `left-1/2 -translate-x-1/2`。层次三件套：`bg-black/60` 遮罩 + `border-white/15` 亮描边 +
  `shadow-[0_24px_64px_-16px_rgba(0,0,0,0.9)]`（`shadow-2xl` 在深色底无效）。
  滚动容器自带底色 `bg-card` + **显式** `overflow-x-auto`（`overflow-y-auto` 会把另一轴算 auto）。
- ★ 同一组件"内联 vs 浮层"诉求相反 ⇒ prop 分流（`MacroEnvCard.full`）。
- ★★ **前端消费字段必须与后端逐一对照**（此类静默 bug 已 3 次：未 import 却调用、`tags` 恒 undefined）⇒
  **别靠肉眼**，用正则抓 `.vue` 里读的 `xxx.field` 逐个断言 API 有。
- ★ `composables/displayMeta.js` = 前端 helper 唯一共享源（`PHASE_STYLE/phaseCn/STRATEGY_SHORT/readyCls/stockHref/
  xqIndexUrl/pctClass/scoreClass/hhmm/dirColor/levelColor/boardChip`）；新组件 import，不许页面内再定义。
  **指数跳转必须 `xqIndexUrl(prefix,code)`**（`000001` 撞平安银行）。
- ★★ 同一数据两处渲染=必然漂移（教练卡实证）⇒ 共用同一 computed 或只留一处。
  `coach_alerts.alert_time` 是完整 ISO ⇒ 展示一律 `displayMeta.hhmm()`。
- ★ **接口实时 ≠ 页面实时**：新刷新项必须进 `startPolling()` 120s 组，否则盘中静默不更新。
- ★ Vue 模板属性里跨行反引号 ⇒ `Unterminated template`；多行文案放 computed。图表偏好：榜单/分布纯 CSS 条，Workbench 不引 echarts。
- 站内带 tab 跳转统一 `?tab=<key>`；默认 tab 显式排除（否则守卫跳过加载 ⇒ 白屏）。
- 新看板/tab 自带一行定位（是什么/不是什么/下一步）；榜单=候选池+变化监测，非买点清单。

## 6. 路由 / 验证纪律
- FastAPI 按注册顺序：`routers/scoring.py` 的 `@router.get("/{symbol}")` 在 L918 ⇒ 单段静态路由必须注册在其**前**
  （`/score/diag/{symbol}` 必须两段，否则被通配吞掉，返回 200 `{"error":...}` 非静默）。
- `routers/system.py` 挂 `/api/system`；路径错会落 SPA 兜底返回 200 text/html，极难发现（正确路径返回 JSON）。
- 验证三条：① import 调用 ≠ HTTP 验证（必须真起后端 + HTTP/浏览器）；② `pnpm build` 通过 ≠ 功能可用；
  ③ 前端"本地优先"路径单独验（`tryLoadLocalAll`）。
- ★ **临时脚本纪律**：中文引号一律 `「」`（ASCII 引号在 `.py` 里直接语法错）；**先 `python -m py_compile` 再跑**；
  Windows GBK 下 print 别带 emoji/特殊箭头。
- ★ **验证脚本自己也会错** ⇒ 失败先分清"产品错"还是"断言错"；判"是否还有某写法"用 **AST 不用文本匹配**（注释密度极高）。
- 测试脚本 import `app.database` 前设 `DATABASE_URL=sqlite:///...`（否则跑线上 Supabase）。

## 7. 告警与信号纪律
- ★★ **涨/跌停家数只能精确判**，唯一口径 = `app/limit_stats.py`（三层：rt_k 逐票判 high/low_limit → 内存真值限价 →
  `backtest.engine._limit_pct` + 0.3pt 容差；**只读缓存不抓取**）。
  **禁用** `change_pct<=-9.7/>=9.9` 近似（20cm 板会把跌 10% 算成跌停；10-09 事故把 11~13 只放大到 123 只）。
  ★ 判据照抄 `realtime_uplimit.classify`：涨停 `_BREAK_TOL` 0.2% 缓冲、**跌停严格 `close<=low_limit`（不擅自对称化）**。
  用 rt_k 前必须同时满足 `is_intraday` 且 `trading>=3500`；**新股首 5 日/退市整理期 `limit_*=0` ⇒ 不计数**。
  ⚠️ `strategies/*` 用 `change_pct>=9.5` 在历史 K 线认涨停 = **战法定义**，改它作废既有回测证据，须预登记。
- 口径：『跌多少』（绝对涨跌幅）vs『从高点回撤』（路径）是两条规则；高波动标的门槛按倍数加严；一次拉取喂多条规则。
- 新信号上线先做预测力检验（①会不会过吵 ②响了有没有用）；模板 `scripts/reversal_edge_check.py`；**判据预登记写死脚本头**。
- ★ **预登记脚本必须接进周期日批**（`app.edge_verify.SCRIPTS` + 日批 `task_verify_monthly`，幂等 + 分级推送）；
  脚本须支持 `--json`（末行 `{"script","verdict","n","need","unit","detail"}`）。现有 7 个。
- **策略上线三重检验**：block bootstrap 显著性（按信号日分块 B≥10000）+ 复利净值 + 分年分解。
  `均收益为正` ≠ `赚钱`（算术均值被"大赚笔 + 复利损耗"双重欺骗）。
- ★★ **事件类信号进决策链前必须过"次日开盘"这一关**：收盘价入场在 A 股系统性高估（收盘才知道事件，次日开盘已跳空）。
  样板 `scripts/lhb_executability_check.py` / `event_executability_check.py`。
- ★★ **覆盖率缺口若与效应强度相关，会把结论系统性拉平** ⇒ 见"覆盖率门/子样本限制"先问：缺的是随机样本还是与效应相关的样本？
- 推送唯一入口 `flash.wechat.push_markdown_batched`；持仓告警已有两处（`coach/monitor.py` + `strategies/exit_alert.py`），新增前防重复。
- ★ `flow_consec` KILL 曾写成"差≤0 **或** P≥0.20"= 单条件过松 ⇒ **KILL 用双条件**或「95%CI 上界<0」；GO 用「差≥阈值 ∧ CI 下界>0」。

## 8. 信号检验状态（**别重复研究**）
| 假设 | 结论 | 关键数字 |
|---|---|---|
| 评分 Top50 买入含义 | **FAIL/KILL** | T+5 −1.43pt、T+10 −2.21pt（vs 可取价池 941 只），P<0.0001 |
| regime 判市减仓 | **FAIL/KILL** | vs 永远满仓 −0.30pt/20 日，95% 上界 +0.24pt；机制=趋势跟随滞后 |
| 主力节奏 H1（吸筹∧三绿） | **PARTIAL** | T+5 +0.19pt(P .25)、T+10 +1.14pt(P .0025) 但子样本 −0.28、T+20 +0.144(P .40) ⇒ **无稳健超额** |
| 消息分（news_score） | **FAIL** | 34 快照日，按日 rank IC 均值 T+2 −0.003 / T+5 +0.004（判据 ≥0.10） |
| 龙虎榜净买强度 | 多头**禁用** / 空头**可用** | close0 +4.29pct；**次日开盘 +1.44pct、CI 跨 0** ⇒ 落地=回避标签 |
| 缩量启动形态 | **预登记中**（C5） | T+10 净 +0.91~+1.99pp（11/12 组合）、T+5 全负；只对 `date≥2026-10-15` 前瞻样本生效 |
| 现有 6 战法 | **不可交易** | 24 格矩阵 19 负 5 正，正者 bootstrap 全不显著（已砍 `ma_convergence`） |
| E2 涨停潮 | 小盘风险偏好回升信号 | up_ratio≥0.90 ∧ 涨停≥2.0%；中证1000 +2.76pp(P=.0012) 可执行，沪深300 不可执行 |
| 春节效应 | **已进决策链** | 节后 T+1 收盘买持 3 日：中证500 +1.75%、中证1000 +2.56%；国庆/五一双双证伪 |
| 行业动量 / 小盘轮动 / 北向 | 无预测力或不可执行 | 详见 daily 2026-09 段 |

- ★ 方法论铁律（通用）：**状态/区间类信号必须用"进入点"检验**（重叠偏差能翻转符号）；**事件优于状态**；
  样本内发现要换定义重验；**因子显著 ≠ 可交易**；分层收益先分年 + 组分位用中位数 + 必须超额口径剔 β。
- ★ **预登记前只看数据可用性、绝不看结果**（一旦偷看，判据会被无意识调向好看那侧）。
- ★ **形态纪律**：已在全历史看过三次 ⇒ **同一段历史无检验力** ⇒ 判据只对前瞻样本生效，闸门 ≥20 独立信号日 ∧ ≥120 观测，
  **不得放宽参数再试**；GO 且用户拍板才改 `SIGNAL_STATUS`。
- 年度维护：`PLAN_*.md` 冻结判据、每月 `edge_verify` 自动复检；`SIGNAL_STATUS` 如实披露到前端。

## 9. 已建成能力（提需求前先查）
- **个股一页纸诊断** `GET /score/diag/{symbol}`（`app/stock_diagnosis.py`，路径必须两段）：事实先渲染（评分盘后口径/主力/闸门缺哪条/
  龙虎榜风险/财报现金质量），每条带 `source`+`as_of`，缺失如实披露；AI 解读**按需点按钮**（12h 缓存、不进轮询），越界话术整段丢弃。
- **事实问答** `POST /score/ask`（只读、不落库、不做交易动作；输入清洗 + `<<< >>>` 分隔块 + 输出侧机械拦截；3s 限速；首次取事实 11s ⇒ 30min 事实缓存）。
- **个股异动** 唯一口径 `routers/stock.py::_detect_anomaly()`（8 条规则；涨停/跌停用真值限价比价；放量要求量比≥3 **且** |涨跌幅|≥2）。
  单票轻量闸门 `GET /api/stock/anomaly-signals/{symbol}`（毫秒、不调 LLM）⇒ 详情页挂载时调用、折叠标题先标标签；
  LLM 深读 `GET /api/stock/anomaly-analysis/{symbol}`（30 分钟复用窗口、失败不写缓存、结论不进决策链）。
- **观察池入口** `components/AnomalyEntry.vue`（`stockHref(code)+'?anomaly=1'`，两处：工作台 `StockListsCards.vue` + `ScoreRank.vue`）。
- **盘中警示** `flash/intraday_alerts.py`（指数级）；**矛盾扫描** `contradictions/`；**事件信号** `events/signal.py`；
  **组合雷达** `portfolio_radar.py` + `/score/batch/portfolio-radar`；**教练** `coach/`（`rules.yaml` + `paper_positions`/`user_portfolio`）。
- **运维五件套** `/api/system/{status,runtime-files,memory,db-usage}` + `memory_watch.py` + `db_retention.py`。
- **评分榜双模式**：前端本地引擎 `useFrontendScoring`（K 线包入 IndexedDB + 实时行情 + 本地精算）；工作台 `loadTop` 本地优先（5 分钟复用，`topMode` 标记）。
- **板块五层**：① `mainline`/`industry_mainline` = 评分 Top50 的行业扎堆（≠板块涨跌幅）② `sector_daily`（东财，常缺行、**盘后别用**）
  + `plate_daily_zz`（zzshare，可回填，前端"板块分化"用）③ `industry_amount_share`（腾讯内存聚合）
  ④ `app/sector_momentum.py`（板块动量/异动侦测）⑤ `app/stock_moves.py`（个股级放量拉升，按 `stock_industry.main_industry` 聚合）。
  ④⑤ 只进日报/复盘，不推送不进决策链。⚠️ zzshare 104 粗分 与新浪 49 类**仅 4 个同名** ⇒ 板块层不可关联个股。
- **形态扫描（生产）**：`app/pattern_launch.py` + `/api/pattern/*` + 日批 `task_pattern_launch_scan` + 前端「形态扫描」tab。
  表 `pattern_scan_daily` / `pattern_watch`（T+1/5/10/20 追踪）/ `pattern_config`。
  ★ 陷阱：扫描日**不能用 MAX(date)**（逐股回填，当天可能只 150 只）⇒ 取"近 5 日里覆盖 ≥800 只"的最新一日。
- **市场底确认清单（C7）**：`app/policy_bottom_watch.py`，**纯观察器零决策链**，五条（国家队公告/成交<1.3 万亿×3/创业板周线转涨/
  涨跌比>0.8×2/10Y<5% 或布伦特<95），达成 4/5 首次推一次企微；**试错仓明确不做**。
  第①条三层证据：公告提示（`policy_announcements.py`，只提示不打勾）+ ETF 份额异动（连续 2 日 ≥8% 自动打勾）+ 人工覆盖优先。
  日批顺序：`etf_share_watch` → `policy_announcement_watch` → `policy_bottom_watch` → `daily_report`。
  ⚠️ 数据坑：`market_amount_daily` **不存在**（用 `intraday_path` 沪+深末点）；`macro_daily.date` 混 `2026/9/7` 格式（**必须解析 datetime 排序**）；
  `market_emotion_daily.up/down` 是**前一日**宽度、`close_*` 会冻结 ⇒ 判据用 `close_*` + 同双校验，不合法标 `insufficient`（**缺失≠不满足**）。
  ★★ **2026-10-19：这个"混合日期格式"的坑还有两个消费方，已修** —— `flash/store.load_macro_daily_history`
  与 `coach/rules._usd_5d_change` 都写 `ORDER BY date DESC LIMIT N`（文本排序）⇒ 取到 9 月那批；
  后者因此判"序列过期"并 `return None` ⇒ **外部领先预警（纳指隔夜 + 美元 5 日）被无声关闭了两周**。
  修法：`macro_daily` 只有几十行 ⇒ **全量取回 + 按日期对象排序**（别再靠 SQL 文本排序）。
- **数据源实测**：ETF 份额主源 = 上交所 `query.sse.com.cn`（`STAT_DATE` 可查历史、`pageSize=5000` 一次 924 只、`TOT_VOL`=总份额万份）；
  深交所 `CATALOGID=1945` 固定 10 条/页（758 只 = 76 页）⇒ 159915 **有意不纳入**。
  巨潮公告 `searchkey` 是**分词全文匹配**（"汇金"命中"承兑汇票/募集资金"）⇒ **不能当精确判据**，必须按日全量拉 + 本地连续字串过滤。

## 10. 股票池 / 榜单口径（2026-10-11 裁决）
- 快照 / `/batch/top` / 精算榜 = **全市场口径（含 688 + 创业板）**，只剔 ST + 市值≥50 亿 + 价≥3 元
  （`routers/scoring._pool_quality_filter`，三路共用）。**不许用过滤抹平差异**（科创板是机会发现口径）。
- 前端本地榜 `useFrontendScoring.isValidStock` 只剔 ST/亏损（创业板+科创板全放开）；`generate-kline-pack.py` 的 `DISABLED_PREFIXES` 已清空。
- K 线包已扩 688（`_pack_quality` 撤剔除，包 +2.6MB，RAM 不变）；战法池自身 filter 层仍剔 688 ⇒ 无害。
- ★ 观察池不得混入 ETF/指数：`mainforce_state` 宇宙继承 `backtest_prices`（含基准 ETF，带 sh/sz 前缀）⇒
  `routers/scoring._is_stock_code`（6 位纯数字 + 0/3/6 开头）在 live 重算 + 快照读取**双处过滤**。
- ★ 陷阱：`live_ranking._build_pool` 注释自称"复刻 score_top 的池子"——**"复刻"就是漂移源**；快照（22:43）永远赶不上 Actions 精算榜（次日凌晨）。
- ⚠️ `_pool_quality_filter` 只管批量池，详情页 `score_single` 不经过它。

## 11. 模拟盘 / 决策链事实
- ★★ 平仓价与退出日**必须成对赋值**（`_close_position(..., price_date)`，`exit_price_date` 列；`paper_stats`/`paper_attribution`
  排除 `exit_price_date IS NULL`）。2026-10-18 修：此前 9/46（20%）成交价落在当日 [low,high] 之外。回填 38 对 / 4 校正 / 4 无法归属。
  ⇒ **跑任何模拟盘绩效评估前先看 `scripts/paper_vs_backtest_check.py` 的价格自检**。
- F1（模拟盘 vs 回测引擎，同批 41 笔可信）：胜率 43.9% vs 41.5%、均 −0.311% vs −0.263% ⇒ **撮合假设与实测基本吻合**。
- ★ 防御市（regime=defensive）`is_strategy_admitted()` 对全部战法返回 False ⇒ 入场层全禁（扫描层仍攒样本）。
  **决定：不开影子仓**（避免复现已被回测否决的场景、弄脏"模拟盘"口径；补样本用回测切片快两个数量级）。
- ★ `MAINFORCE_MODE` 定案 **off**（出货乘数 `mult` 自 P1-12 方案A 起**无任何消费方**，只作标签）⇒ auto/off 等价，
  留 auto 会让未来接线把未预登记背书的乘数静默启用。四处配置已统一；Render 控制台若仍 auto，`/api/health.switches_mismatch` 会报。
- ★ 矛盾验证闭环：91 条里 **75 条属非方向性类型（设计上不验证）**，真正有方向的只有 19 条 ⇒ "验证数少"多数是结构限制。
  日批已调 `validate_yesterday()` + `today` 改北京时间 + 跳过原因拆 `非方向性/超龄作废`。
- ★ 通用教训：**"样本不够"要先区分「没在产生」「在产生但没被记录」「在设计上就不产生」**，三者修法完全不同。

## 12. 项目约定 / 用户偏好
- ★★ **推送纪律**：不自行 `git push`；改完先汇报改动 + 验证结果，等指令再推；未明确要求不自动 commit。
- 日报不推企微、只前端 `/report`；主使用习惯 = 每天挂 Top50 看实时行情（主链路 = 后端评分，前端本地计算次要）。
- 本机：Windows GBK；起后端先 `PYTHONIOENCODING=utf-8`（否则 lifespan 直接失败，易误判）；
  HTTP 验证最稳 = 单进程 uvicorn 子线程 + urllib。
- gh-pages 三写入者必须同一 `concurrency.group` + `cancel-in-progress:false` + `keep_files:true`。
- `report_store` **不能被"顺手写"**：`_weekly_due()` 自愈判据是"最新一份报告（不看 tag）的日期"⇒ 新落库报告会破坏周报漏跑自愈。
- 周度闸门别自己写 `weekday()==4`：用 `_batch_trading_day()` + `_week_last_trading_day(d)`。
- 文档：`README.md`（门面 + 红线 + **LLM 外发数据清单**：日报/简报/战法诊断/复盘/盘中异动解读都会把持仓送出网）；
  `开发文档.md`（动手手册，6 条红线 + 架构速查 + 已验证/已证伪清单 + 不做清单）；`Agent知识库_完整操作手册_v3.1.md`（第九章=数据包与迁移，
  **删任何包头/包文件前先查消费者矩阵**）。`CONTEXT.md` 停在 2026-09-06，别再当现状读。
  ★ 维护约定：稳定规则写手册；**易变清单（表结构/任务表/进度）只给入口不给快照**。

## 13. 工程方法论 / 踩坑精华
- **验证**：`py_compile` 查不出 NameError ⇒ 真 import + 真调用 + 独立进程；空数据 0==0 是假绿灯（先校验样本非空）；
  ★★ 同批次不对同一文件连发多编辑（静默丢，分批 + 每批 grep 复验关键行）。
- **数据/字段**：缺数据 `None` 不填 0；`dict.get(k, default)` 对 None 不生效；跨系统按字段名取不按位置；
  同一指标一页一口径；一个字段不回答两个相反问题；改枚举同步硬编码数量。
- **外部源静默失效**：恒 0 比报错危险（被 docstring"属正常"合理化）⇒ 显式 `available/reason/stopped_since` + 保留 falsy 旧字段。
- **SQL/双库**：PG/SQLite 四坑（JSON 列已对象别再 loads / Decimal 显式 float / NULLS LAST 是 PG 专有 / 占位符统一 `%s`）；
  `INSERT OR REPLACE` 整行替换先读合并；加列用 `ALTER TABLE ADD COLUMN`；★ **SQL 里的字面 `%` 必须写 `%%`**（`LIKE '688%'` 会报错，用 `LEFT(code,3)`）。
- **前端**：未 import 却调用 = 静默失效（vite 不查未定义标识符）；"信息只在某阶段看得到"= 先查 `v-if` 在哪个分支。
- **决策/语义**：缺证据 ≠ 反证；完成时刻不确定 ⇒ 事件驱动 > 轮询；展示层与计算层都显式区分 0 与缺失。
- **PowerShell/git**：`git commit -m` 单引号且内容无引号；`python -c` 含中文/%/in 易解析坏 ⇒ 写临时脚本。

## 14. 其他长期事实（细节见对应 daily）
- **主力节奏跟随框架（项目北极星）**：跟对主力节奏但永远慢一步。三层可靠度递减：①状态（有回测背书→可决策）
  ②方向（flow5 倒 U / 两融，看连续趋势）③时点（仅形态参考，永不进决策链）。红线：盘中观察→盘后确认→次日执行。
  ★ flow5 IC / accum-distribution 是**横截面证据（选股）**，"跟节奏"是**时间序列命题（择时）**——横截面 ≠ 时间序列。
- **环境传导链**：`mainforce/confluence.py` 四层合成 宏观±2 + regime±2/±1 + 板块±1 + 个股±2，sum(−7~+7)。
- **LLM**：主力 `deepseek-ai/DeepSeek-R1`；`LLM_FREE_SHADOW=1` = 影子站**排除出正式链**（名字反直觉易设错）；
  单站连败 2 次→熔断 30min；排障 `/api/system/llm-usage`。`render.yaml` 只是变量清单，不是事实来源。
- **宏观面板**：`macro.py` 已抓 A50/纳指期货/恒生科技/美债/VIX/美元/离岸在岸人民币/金龙指数/原油/黑色系；
  顶栏外盘四件套 = A50/离岸/布伦特/纳指期货 + 隔夜累计变化（自上次 A 股收盘）。`macro.direction` 早盘锁定（08:55–13:00 一次，当日固定）。
- **coach 外部领先预警**：纳指隔夜 + 美元 5 日，**仅 defensive 市有增量**；⚠️ 纳指隔夜 IC 0.1609 在可交易口径塌陷至 0.0115（已如实标注"解释/风控用途"）。
- **涨停梯队**（zzshare 日批）+ 炸板率/大面率（按板幅分桶、剔一字板）+ 连板断层判读 + 昨日涨停赚钱效应。
- **跨市场因子**：外部因子按"同日"对齐 = 时区前视 ⇒ 评估一律 lag1 / "A 股开盘前已知"口径；价值常非线性 ⇒ 必须分组检验。
- **因子体检周期化**：`subfactor_ic_backtest.py` 每月首个交易日随日批跑；幂等判据必须读库（Actions 每次全新 checkout）。
  企微**不支持 markdown 表格**（自动转列表）。
- **部署**：★ 排查线上异常第一步 `curl /api/health` 看 `build`（=RENDER_GIT_COMMIT 前 7 位）vs `git rev-parse HEAD`。
  数据源告警两道闸：`_NO_WECHAT_SOURCES`（东财板块/主站，封 IP 常态有兜底）+ `WECHAT_SOURCE_ALERTS=0`；
  抑制时日志有 `[health] 已抑制企微推送` ⇒ **收到推送但无此行 = 旧代码**。
  生产 Python 3.9 **禁 PEP 604**；FastAPI 无 await 的路由写 `def`；后端日志 ASCII。OOM 诊断要问"哪个请求一次性灌入"。
- **回测数据质量**：体检 `scripts/audit_backtest_data.py`，源异常日标 `price_anomalies`（集中 2024，源里不可修复）。
  ✅ gate-watch 早已优化：快照读 0.000s（热）/ 1.4~5.5s（冷）vs live 39.8s；快照 >5 天会回退 live（异常态宁慢要真数据）。
  ✅ 盘中 `score_single` vs `batch/top` 不同分 = `_calc_technical` 不用当日半根 ⇒ 已加 `kline_cache.append_intraday_bar()`
  （零网络，**合成序列绝不写回缓存**）；两套指标末根 ≤0.012 系统差 ⇒ 评分统一用 fast。
  ★ `_tencent_cache_key` **只认裸 6 位码**（传 `sh600000` 取不到行情 ⇒ 静默不合成）。
  ✅ `score_snapshot` 已改 22:30-24:00 + 判据改用**数据包日期** + "当日已写不覆盖"；日批 `mainforce_signal`→`mainforce` 键名 bug 已修
  （曾致 BucketStats 吸筹/出货分桶整整一个月零数据）。★ 教训：`_heavy()` 里的循环在 READ_ONLY 下 = 死代码，
  改之前先确认**线上到底哪条路径在跑**（"修在不跑的路径上"已发生两次）。
- **战法池口径**：`backtest_prices` 池 = 战法选股 ∪ 近 30 天上榜 + ETF（≈810~995 只，全主板）⇒ **有选择偏差且自我固化**
  （没选中 ⇒ 无数据 ⇒ 永不可检验）；该表**不是全市场**，由它自算的涨停名单必然偏少。
- **春节效应落点**：`calendar_exception` 独立字段（`app/spring_festival.py` + `trade_brief.py` + Workbench 独立块），
  `overrides` 仅在 `regime∈{defensive, neutral_bearish}` 给出；**不改 `trade_gate.evaluate`/`REGIME_ALLOWED`/`REGIME_POSITION`**。
  定位长假用「当月最长休市缺口」，不硬编码（22/22 吻合）。**未做**：企微推送、日报小节。
- **观察池涨跌幅**：`gate_watch` 接口**不含 `change_pct`**（全池重算会 >30s）⇒ 前端一律 `getBatchPrices` 批量补（榜单页 09-22 起、工作台 09-30 起）。
- 轮询接口必须"裁列 + 截断正文"（`SUBSTR`，SQLite 无 `LEFT()`）；判据：前端定时轮询 + 返回表行 ⇒ 先问"列表需要整行吗"。

## 15. 计划索引
- `PLAN_NEXT.md` = **权威待办**（顶部 5 行开工摘要 / A 等时钟 / B 已清零 / C 立项 / D 不做）。
- `PLAN_SCORE_EDGE.md`（评分 KILL 判据 v1.1 + 结果 §9）、`PLAN_MAINFORCE_EDGE.md`（主力节奏 v1.0）、
  `PLAN_PATTERN_LAUNCH_EDGE.md`（形态 v1.0 预登记）、`PLAN_RESERVE_SIGNALS.md`（储备信号 P0~P2 已落地）、
  `AUDIT_PLAN_2026-09-12.md`（旧看板 36 项审计，**`PLAN_2026-09-12.md` 已退役**）、`EGRESS.md`。
