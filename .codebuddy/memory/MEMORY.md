# 长期记忆（stock-scoring-v2）

> ## 🔁 换账号 / 新会话 / 换机器：如何接上上下文
> 上下文 = 本目录 Markdown，与账号无关；`.codebuddy/memory/*.md` 已 git 跟踪 ⇒ clone 后即在。
> 新会话第一句：`先读 .codebuddy/memory/MEMORY.md 与最近 2 天 daily，读完复述进展与未完成项再继续。`
> 阅读顺序：本文件 → 最近 1~2 天 daily → 更早按日期翻。不依赖聊天记录（绑账号）；换机前先 commit。

---

## 架构与运行形态
- 单进程 FastAPI（`backend/app/main.py`）`/api/*` + 前端静态；生产 `uvicorn 0.0.0.0:8000`，**不要 `--reload`**。
- 前端生产 = GitHub Pages（pnpm 构建推 gh-pages，入口 `klen-h.github.io/stock-scoring-v2`）；Render 后端兜底 dist。
- DB 默认 SQLite，`DATABASE_URL` 走 PG（Supabase 东京）；`app/database.py` 自动转 `%s` 兼容双库。
- 调度器 `flash/scheduler.py` ~30 asyncio loop 按日幂等；LLM/轻推送 `create_task`，重走 `*_heavy`（`RENDER_READ_ONLY=1` 全关）。
- 盘后链路：K线 15:30 → 指标 16:40 → 快照 18:00 → 主线/消息分/日报 19:15+；简报按 `(date,phase)` 落 `trader_briefs`。
- ★★ **日期键"两派" + 循环守卫铁律**（2026-10-08 查"日批每天两次/假期照跑"时挖出）：Render 侧 `*_loop` **必须用 `rules.is_trading_day()`**（只写 `weekday()<5` 会把 A 股节假日当工作日）**且显式传 `latest_completed_trading_day()`**（不传 ⇒ 内部 `_today()` = **运行日** ⇒ 写出"**键=假日、正文=上一交易日**"的错位行）。已修 `daily_report_loop`（19:30-23:59，实测它造出 09-25/10-01/02/05/06/07 六个假日键日报，已清）。**同类残留未清**：`contradictions` 2 行、`trader_briefs` 3 行、`sector_daily` **1496 行** 假日键。**同一批日批内日期键也不一致**（`ranking_history`/`industry_mainline`/`sector_amount_daily` 用运行日，`plate_daily_zz`/`mainforce_state` 用交易日）—— 查"数据为何出现未来日期键"先看这条。
- **日批本身没有 cron**：由上游 `Generate Backend Data Pack` 成功后 `workflow_run` 接棒（原 20:30 兜底已停用，注释明写"保留会重复跑一次"）。上游**两条触发（2026-10-08 用户给运行列表证实）**：① cron-job.org → `workflow_dispatch`，页面显示 **"Manually run by klen-h"，19:00 北京准时** ✓；② GitHub 兜底 `schedule: cron '0 14 * * 1-5'`（UTC 名义=北京 22:00，**实测总要晚 4~7h 才起跑**，落在北京 02:52~05:23）⇒ **日批每天两次 = 上游成功两次**。兜底本该被"最近 12h 已有成功 run ⇒ skip"的 guard 挡住，**实际从未生效**（时长 1h12m~1h31m = 整包跑满）⇒ 根因：guard job **没有 `actions/checkout`**，而 **`gh` 不读 `GITHUB_REPOSITORY`**（只从 CWD 的 git remote 或 `GH_REPO`/`--repo` 取仓库）⇒ `gh run list` 报 "not a git repository"，又被 **`2>/dev/null || echo 0` 补成 0** ⇒ 判据恒 0。**已修**：加 `GH_REPO` + `permissions: actions: read` + 不再吞错（失败打 warning 且按"不跳过"处理，宁多跑不误跳）；每天省 ~2h15m（包 ~1h20m + 日批 ~55m）。
- ★★ **反复模式：Render 循环停摆 ⇒ 数据静默断档 ⇒ 迁入日批**（已发生：`mainforce_state` 09-09、`weekly_report`/`news_snapshot`/`lhb` 09-11、`zz_finance` 09-19、**`sector_snapshot_zz` 09-24（09-30 修）**）。**兜底闸（2026-09-30 起）**：`app/data_gaps.py` + 日批 `data_gap` 任务（排在所有数据写入之后）每晚自愈板块缺口（最近 5 交易日，无缺口零请求）并做 7 张关键表断档告警；判据口径是**交易日**（自然日会在休市期误报），**落后 ≥2 个交易日即告警**（容差 1 —— 设 2 会漏掉 09-24→09-29 这种夹着中秋休市的断档）。手动补历史：`python scripts/backfill_plate_daily.py --apply`（zzshare 支持历史日期；东财 clist 只给当前快照**不可回补**）。

## 前端布局纪律（浮层/弹窗）
- ★★ 顶栏浮层统一做法：`<Teleport to="body">` + `fixed` + `max-h-[calc(100vh_-_Nrem)]` + `overflow-y-auto overscroll-contain`；水平居中 `left-1/2 -translate-x-1/2`。锚点+视口比例 max-h 必然横向出屏/纵向不扣顶栏（09-29 实证）。Tailwind calc 空格写 `_`；核对 grep `max-height:calc` 而非字面量。
- ★ 同一组件"内联 vs 浮层"诉求相反 ⇒ prop 分流（`MacroEnvCard.full`）；放开截断仍要宽度上限（`flex-shrink-0` 内文本按 max-content 撑破容器）。
- ★ 浮层层次三件套：遮罩压暗 `bg-black/60` + 亮描边 `border-white/15` + 深投影 `shadow-[0_24px_64px_-16px_rgba(0,0,0,0.9)]`（`shadow-2xl` 深色底无效）；核对投影 grep `--tw-shadow` 变量。
- ★ 滚动容器必须自带底色（`bg-card`）+ **显式** `overflow-x-auto`（`overflow-y-auto` 会把另一轴算成 auto；别用 `overflow-x-hidden` 裁信息）。

## 数据与缓存
- ★★★ `tencent._cache`：显示"数据时点"必须用 `data_ts`+`from_snapshot`，**绝不用 `last_update`**（=填充时刻，快照恢复故意设"现在"）。事故：线上缓存冻结 09-24 两天才被用户肉眼发现。已暴露 `quote_as_of/quote_from_snapshot`（`/api/market/emotion` + `portfolio_radar.build()`）；`stock_cache_refresh_loop` 巡检 data_ts vs 期望交易日。**判定缓存冻结日**：`/score/batch/top` 的 change_pct 与 `backtest_prices` 逐日逐只比对。`/api/market/emotion` 的 `data_date` 是日历推断，不能证明缓存日期。
- ★ 时点：`datetime.fromtimestamp` 走服务器本地时区（生产 UTC）差 8h ⇒ `time.gmtime(ts+8*3600)` 或 `fromtimestamp(ts, tz=timezone(timedelta(hours=8)))`；`datetime.now()` 一律 `flash.rules.beijing_now()`。
- ★ Vue 模板属性里跨行反引号 ⇒ `Unterminated template` 构建失败；多行文案放 computed。
- 轮询接口必须"裁列+截断正文"（`SUBSTR`，SQLite 无 `LEFT()`）；判据：前端定时轮询+返回表行 ⇒ 先问"列表需要整行吗"。
- 本机持久缓存统一模式（flow/l3/emotion-closes/ind-dist-src）：进程内→本机 gzip/json→回源；指纹单行查询；取不到指纹不走缓存；空结果不写盘。
- 竞价落库：`market_emotion_daily` 三组互不覆盖字段（主/close_*/auction_*）；竞价数据只在 10 分钟窗口存在，窗外不写；`_emotion_daily_row` 必须组装整行（SQLite `INSERT OR REPLACE` 缺列清空）。
- `kline_cache`/`indicator_cache`：indicator 36h；`kline_count`∈(0,250) 视为截断跳过。
- 两条包发布链独立：`kline-data.yml`(18:00)→前端包+realtime-quotes；`backend-pack.yml`(19:00)→`backend-pack.db.gz`（日批唯一依赖）。前端包失败≠日批失败；瓶颈=K线阶段 1566 只单只请求。
- ★★ **腾讯 qt 快照字段下标实测地图**（2026-10-08 真值核对，勿再按文档抄——本项目踩过 44/45/46 的坑）：`[3]现价 [4]昨收 [6]成交量(手) [7]外盘 [8]内盘 [9..28]五档(买一价/量…卖五价/量) [31]涨跌额 [32]涨跌幅% [37]成交额(万) [38]换手% [39]PE [41]最高 [42]最低 [43]振幅 [44]流通市值(亿) [45]总市值(亿) [46]PB [47]涨停价 [48]跌停价 [49]量比 [50]委差(手，=买五档量−卖五档量) [51]均价 [57]成交额(万,重复)`。验证法：涨停价必须 = 昨收×1.10（万科A 4.26→4.69 ✓）、均价 = 成交额元/成交量股（✓）、委差 = 五档差（✓）。**已落库的解析字段**：`tencent._fetch_tencent` 现含上述 18+5 个（新增 `limit_up/limit_down/volume_ratio/bid_diff/avg_price`）。
- **涨速自算**（腾讯无该字段）：`tencent._SPEED_CACHE` + `_record_speed()`（**钩在 `refresh_all_stocks` 尾部，绝不能在 `_fetch_tencent`** —— 否则 `restore_market_snapshot` 会把**收盘快照**当实时价记进序列）＋ `speed_pct(code, minutes=5)`。仅 `get_china_market_status().is_open` 时采样；保留 6min/最小间隔 20s/≤32 点。**数据不足返回 None，不返回 0**。实际采样间隔 ≈2min（调度器 120s + 函数内 60s 冷却）⇒ `speed_5min` 可用、`speed_1min` 基本恒 None。
- **单股异动前端入口（P3）**：`GET /api/stock/anomaly-signals/{symbol}` = **轻量闸门**（不调 LLM、复用 `detect_single_anomaly`，毫秒级）⇒ `StockDetail.vue` 挂载时调用，**折叠标题上主动标出异动标签**（用户原话"项目察觉不到"的正解：页面先察觉、人再决定要不要深看）。分析块**默认折叠**，展开才请求，`refresh` 强制重算；卡片含结论标签/置信度/支持·反对证据/观察位/风险/缺失维度/矛盾告警/免责声明。
- **观察池共享入口（P3-b，已做）**：`frontend/src/components/AnomalyEntry.vue` = 一个 `异动` 小 chip 链接 → `stockHref(code) + '?anomaly=1'`（**必须用共享 `stockHref`**，别硬编码路由前缀；`target=_blank` + `@click.stop` 防父行 `goDetail` 冒泡）。**挂两处**：① 工作台右栏 `components/workbench/StockListsCards.vue` 观察池行（该行**已加 `flex-wrap`** —— 右栏仅 320px，加元素要能优雅换行而非被裁）② 榜单页 `ScoreRank.vue` 观察池表**末列**。详情页读 `?anomaly=1` ⇒ **只把分析块展开、不自动请求**（成本仍由人点「开始分析」；"导航即烧钱"与按需纪律相悖）。⚠️ 观察池是**两个独立实现**（工作台子组件 vs 榜单页表格），共享入口只能靠**同一组件**保证不漂移。
- ★★ **前端消费字段 vs 后端返回字段的自动对照校验**（可复用做法）：用正则把 `.vue` 里实际读的 `xxx.field` 抓出来，逐个断言 API 响应里存在（条件字段如 `error`/`cached_age_min` 单列白名单）。P3 用它一次验完 13 个字段 —— 这类"前端读了个后端不返回的字段"的静默 bug 已发生 3 次，**别靠肉眼**。
- ★★ 板块源 = zzshare（`plates_rank` plate_type=14行业/15概念，**支持历史日期可回填**），落独立表 `plate_daily_zz`；与东财 taxonomy 不兼容**绝不混表**。`app/sector_zz.py` / `/sector/zz/*` / `scripts/backfill_plate_zz.py`；`eastmoney.get_sectors` 降级链 东财→zzshare→新浪；新浪无涨跌家数给 `None`（前端判 null 才渲染，不显假 0）。

## 路由与验证纪律
- FastAPI 按注册顺序：`routers/scoring.py` 的 `@router.get("/{symbol}")` 在 L918 ⇒ 单段静态路由必须注册在其**前**，否则 200 `{"error":...}` 非静默。
- `routers/system.py` 挂 `/api/system` 前缀（main.py include 决定）；路径错会落 SPA 兜底返回 200 text/html 极难发现。判据：正确路径返回 JSON（未带 token 为 401）。
- 验证三条：① import 调用≠HTTP 验证（前后端配合必须真起后端+HTTP/浏览器）；② `pnpm build` 通过≠功能可用；③ 前端"本地优先"路径单独验（`tryLoadLocalAll`）。

## 告警与信号纪律
- 口径：『跌多少』（绝对涨跌幅）vs『从高点回撤』（路径）是两条规则；双条件缺一不可；高波动标的门槛按倍数加严；一次拉取喂多条规则。
- ★★ **涨/跌停家数只能"精确判"，绝不能用涨跌幅近似**（2026-10-09 事故：盘中误推「跌停 123 只/黑天鹅熔断」）。**唯一口径 = `app/limit_stats.py`**（2026-10-10 收拢；三层：① `realtime_uplimit.peek()` zzshare rt_k + `high_limit/low_limit` 逐票判、覆盖率 100%、**只读缓存不抓取** ② 内存行情**真值限价** `tencent.limit_up/limit_down` ③ 项目唯一板幅 `backtest.engine._limit_pct` + 0.3pt 容差。⚠️ 判据**照抄** `realtime_uplimit.classify`：涨停用 `_BREAK_TOL`(0.2%) 缓冲、**跌停严格 `close<=low_limit`（不擅自"对称化"）**——我第一版自己写了 0.005 元固定容差，实测涨停差 24 只）。**禁用** `change_pct <= -9.7 / >= 9.9` 之类固定阈值：创业板/科创板 ±20%、ST ±5%、北交 ±30% ⇒ 20cm 板跌 10% 离跌停还差一半却被计入（10-09 早盘 826涨/4044跌 时把计数放大到 **123**，而**收盘真值仅 11~13 只**、当日还是 96 涨停的**上涨日**）。采用 rt_k 值前**必须**同时满足 `is_intraday`（盘前/休市是上一交易日收盘定稿口径）与 `trading>=3500`（`snapshot()` fail-open，缺批会**静默漏报**）；两层回退也必须是精确口径（`tencent.limit_down` 真值价 → 板块保守阈值）。`black_swan_active()` 为真会**撤销模拟盘待确认买单**（`paper_trading.py`）；本次实测 `exit_reason='black_swan'` 0 条，**无实际损害**。**2026-10-10 已全量迁移**（此前我以为只有 overview 没迁 —— **判断错了**：它 09-30 就切了；真正残留的是 `market_regime._market_breadth_now`（→**E2 事件信号**）、`coach/rules._breadth`（→**加仓条件 `ld<10` / `position_sizing` `ld≥100 ⇒ 仓位×0.7`**）、`flash/intraday_alerts`；`routers/market` 两函数改为**委托**共享模块）。实测新口径与 rt_k **逐值一致**（涨停 72/72、跌停 11/11），旧 ±9.9 口径偏高 **+27 涨停 / +4 跌停**。★ 顺带修掉一个**项目旧实现也有**的缺陷：**新股上市首 5 日/退市整理期无涨跌幅限制**（`limit_*=0`，如 `C力勤 001246` 跌 15.87%）曾被算成跌停 ⇒ 加守卫"**涨跌幅超板幅 ⇒ 不受约束 ⇒ 不计数**"。**未做（须另立研究）**：`strategies/*` 用 `change_pct>=9.5` 在历史K线上认涨停 —— 那是**战法定义**，改它会作废既有回测证据，需预登记 + 重跑。
- 新信号上线先做预测力检验（①会不会过吵 ②响了有没有用）；模板 `scripts/reversal_edge_check.py`；**判定标准预登记**写死脚本头。★ **预登记脚本必须接进周期日批**（样板 `subfactor_ic`，每月首个交易日）—— 否则"样本是时钟"意味着样本够了也没人跑（2026-09-30 实测 `event_live_review`/`gate_ready_backtest`/`sector_momentum_edge_check` 三个脚本**全项目无调用方**）。
- **策略上线三重检验铁律**（bootstrap 证伪后沉淀）：① block bootstrap 显著性（按信号日分块，B≥10000）② 复利净值 ③ 分年分解。`均收益为正`≠`赚钱`（算术均值被"大赚笔+复利损耗"双重欺骗）。
- 推送唯一入口 `flash.wechat.push_markdown_batched`；持仓告警已有两处（`coach/monitor.py` + `strategies/exit_alert.py`）新增前防重复。

## 项目已有能力清单（提需求前先查）
- 盘中警示 `intraday_alerts.py`（**指数级**：指数急跌/冲高回落/涨跌比/跌停家数）；矛盾扫描 `contradictions/`；LLM 素材 `flash/llm.py`；事件信号 `events/signal.py`。
- ★ **个股异动判定唯一口径 = `routers/stock.py::_detect_anomaly()`**（2026-10-08 P1 抽出，全市场 `/api/stock/anomalies` 与单票 `detect_single_anomaly()` **共用**，防两处漂移）。8 条规则：急涨≥5% / 急跌≤-5% / 涨停 / 跌停（**用真值限价 `tencent.limit_up|limit_down` 比价** ⇒ ST±5%、创业板科创板±20% 不再漏判；旧口径 `9.8≤涨跌幅≤10.1` 判不出 19.9% 的真涨停） / 高换手>10% / 大振幅>8% / **放量（量比≥3 **且** \|涨跌幅\|≥2，severity=1 防过吵）** / **急拉升·急跳水（5分钟涨速≥3%）**。单票闸门 `detect_single_anomaly(code)` 返回 `should_analyze/tags/speed_5min/data_ts/from_snapshot`，**不调 LLM**。
- ★★ **静默失效又一例（已修）**：前端 `ScoreRank.vue` 异动标签一直是 `v-for="tag in a.tags"`，而后端只返回 `signals`（结构体）⇒ **`tags` 恒 undefined ⇒ 标签从来没渲染出来过**（`v-for` over undefined 静默，构建/日志无痕）。后端现已同时返回 `tags`（= signames 的 type 数组）。**纪律：前端消费的字段名必须与后端逐一对照**——本项目此类问题已第 3 次（`getMarketOverview` 未 import、`getSectorSnapshot` 未 import、本次 `tags`）。
- ★ **单股异动 LLM 分析（P2，2026-10-08）**：`GET /api/stock/anomaly-analysis/{symbol}?refresh=` —— 判读「拉高出货/诱多上套/真实突破/**暂无定论**」。数据四层：L1 盘口快照（复用 `detect_single_anomaly`）＋L2 主力（`_mainforce_for`，口径**照抄详情页**：优先 `mainforce_state` 但**日期须与最新K线一致**才算新鲜，否则现算 `float_shares=float_cap×1e4÷现价`）＋L3 大盘（`llm.format_a_share_context`）＋L4 历史（`backtest_prices` **只取价格**，绝不跨源算量比）。表 `stock_anomaly_analysis(code,date,result_json,created_at)`，**复用窗口仅 30 分钟**（盘中结论会过时）。护栏：`daily_report._ORDER_WORDS` 剔指令式措辞（计 `filtered_fields`）＋结论与主力资金标签矛盾则**代码层强制降置信度**（`_consistency_warnings`）＋**失败不写缓存**（否则窗口内一直返回失败）。**结论不进决策链**；成本=最多 1 次 LLM/次、`tier="fast"`、受日熔断与 6s 节流约束。⚠️ `_stock_history` 两条诚实前提：`backtest_prices` 仅 ~800 只（池外返回 None）、日线 **15:40 后**才回填（盘中最后一根是**昨日** ⇒ 文案须标注"截至上一交易日"）。
- 持仓聚合 `portfolio_radar.py` + `/score/batch/portfolio-radar`。
- 运维五件套：`/api/system/{status,runtime-files,memory(52探针),db-usage}` + `memory_watch.py` + `db_retention.py`（Supabase 500MB 超限=只读）。
- 评分榜双模式：前端本地引擎 `useFrontendScoring`（K线包入 IndexedDB+实时行情+本地精算）；工作台 `loadTop` 本地优先（5 分钟复用窗口，force 跳过），`topMode` 标记「本地实时/后端批次」。
- **观察池涨跌幅**：`gate_watch` 接口**不含 `change_pct`**（避免全池重算被拖到 >30s）⇒ 实时涨跌幅一律由前端 `getBatchPrices` 批量补（榜单页 09-22 起、工作台 09-30 起，同一做法；盘中随轮询只刷价格）。两处的「等状态/还差什么」文字走 **hover 提示**而非常显（右栏仅 320px）。
- 宏观面板 `macro.py` 已抓：A50/纳指期货/恒生科技/美债/VIX/美元/离岸在岸人民币/金龙指数(gb_$hxc)/原油/黑色系；顶栏外盘四件套 = A50/离岸/布伦特/纳指期货 + 隔夜累计变化（自上次 A 股收盘以来）+ 外盘开市指示；「隔夜与今日（财经日历）」卡。
- coach 外部领先预警：纳指隔夜+美元 5 日，**仅 defensive 市有增量价值**；⚠️ 纳指隔夜 IC 0.1609 在可交易口径塌陷至 0.0115（已如实标注"解释/风控用途"）。
- 涨停梯队（zzshare 日批）+ 炸板率/大面率（按板幅分桶、剔一字板）+ 连板梯队断层判读 + 昨日涨停赚钱效应。
- 板块五层（**提"板块察觉不到"类需求前必读**）：① `mainline`/`industry_mainline` = **评分 Top50 的行业扎堆**（≠ 板块涨跌幅，大盘股板块天然低配：房地产日均仅 1.5 只）② 板块快照双源 —— 东财 `sector_daily`（日批写，常缺行、09-29 与真值矛盾不可信，**盘后不要用它**）+ zzshare `plate_daily_zz`（前端「板块分化」+ 盘后板块卡用，可回填）③ `industry_amount_share`（腾讯内存聚合，需行情缓存就绪）④ **`app/sector_momentum.py` = 板块动量/异动侦测**：异动=3日≥+5%/5日≥+8% **或** 当日前5且≥+2%（双口径），连续命中只报首日，同日≥8 项判为系统性普涨/普跌 ⑤ **`app/stock_moves.py` = 个股级"放量拉升"（含涨停，按 `stock_industry.main_industry` 聚合）**：涨幅 ≥3% 且【涨停 **或**（换手 ≥5% **或** 成交额 ≥15亿且换手 ≥1.5%）】，流通 ≥50亿、剔新股/ST；一字板另标。⚠️ **大盘股换手天然低**（实测涨幅≥3% 且流通≥300亿的换手均值仅 3.52%）⇒ 必须留"成交额"路径，否则系统性误杀云铝这类票（换手 1.97% 但成交额 17.7亿）。④⑤ **只进日报/复盘、不推送不进决策链**（因子验证 `scripts/sector_momentum_edge_check.py` 预登记，当前 INSUFFICIENT）。⚠️ zzshare 104 粗分 与 新浪 49 类**仅 4 个同名** ⇒ 板块层**不可关联个股**（强行匹配会给误导性 0）——个股层的行业标签走 `stock_industry` 天然可用。

## Supabase egress（详见 EGRESS.md）
- 账号级 5GB/月（本地+Render+Actions 共用）；实测 ~273MB/天；头号放大器=进程内缓存×低频数据+`--reload` 重启整份重读。排障：`pg_stat_statements` 按 rows + 必查 `stats_reset`（否则 14 天累计当一天）；探针 `scripts/egress_probe.py`（两次快照做差 = 真实字节）。⚠️ 报告里带 `≤` 的是**上限**（只取部分列的语句按整行宽估，可虚高两个数量级）。
- ★★ **Actions 的 egress 无法靠"缓存"解决**（2026-10-10 实测 6.14GB/29天 ≈ 212MB/天）：Actions 每次是**全新 runner** ⇒ `backend/data/` 下所有本机缓存层（`sync_meta` 版本门控 / 本机 SQLite / 指纹 / TTL）**全部失效** ⇒ **凡没进数据包的库表，每次跑都整表读一次**。⇒ 只有两条路：**并入数据包** 或 **窄化读取**。
- **Actions 里连库的只有 2 个作业**（其余已逐一排除）：① 日批 `daily-batch.yml`（`DATA_SOURCE=pack` ✓ + `ensure_pack_fresh()` 会下包 ⇒ K线/指标/回测价格**零回源**；但冷启必付 `mainflow_history` + `stock_finance_zz` + `stock_finance_ocf_hist`）② 产包 `backend-pack.yml`（只读 `kline_cache` 代码表 / `market_snapshot` 快照 1.1MB / 近 6 日资金流）。`ci.yml`（无 DATABASE_URL，只 compileall+import）、`deploy-preview.yml`、`kline-data.yml` **都不碰 Supabase**。⇒ Actions ≈ **27MB/天**（兜底 guard 修好后 1 次/天）；**200MB/天 是账号级总量**，其余在本地/Render。
- ★★ **`load_flow_map(codes=None, days=N)` 窗口化**（2026-10-10）：`mainforce/state.refresh_all` 只用**近 5 日**资金流（overlay 里是 `flow_rows[-5:]`），却整表读 146 日/只（12.9MB / 120,850 行 / 次）⇒ 传 `days=FLOW_WINDOW_DAYS(20)`（实得 27 交易日）后 **25,732 / 128,535 行 = 20%**。**语义边界**：`flow5_amt`/`flow5_amt_yuan` 逐值不变；`flow_consec` 上限=窗口天数但**全库无消费方**；**陈旧票（最后一行早于窗口）或判不出 ⇒ 自动退回整表读**（否则这些票会从"有旧数据"变"无数据"，静默改 overlay 降级口径）。窗口读**不读不写** `flow-map.db`（整表口径）。
- ★★ **`local` 模式包陈旧 ⇒ 整份静默回退 DB**（`pack_source._maybe_refresh` 开头就 `if _DATA_SOURCE != "pack": return`）⇒ 只 `print` 一次警告，极易漏看。**纪律：`DATA_SOURCE=local` 前必须先 `python scripts/sync_local.py --force`**。
- ★ `main.py` 的 `_performance_warmup`（2026-10-10 修）：原先**只判 `READ_ONLY`**（注释却写"db 模式且非只读才预热"）⇒ 已补 `pack_source.enabled() ⇒ 跳过`。理由：它在启动 30s 后跑，而此刻包可能**还没下完**（读侧静默回退 DB）⇒ `_load_prices_map` 无 start 会拉全历史（db 模式 20-40MB/次）。
- ★★ **`pack_source` 的读侧语义 = 「命中包→用包；**未命中→回退 DB**」** ⇒ **包的覆盖口径必须 ⊇ 所有消费方池**，否则那批代码天天回源（这是"配了 pack 也照样烧"的第二种形态）。**2026-10-10 实测并修复**：`backtest_prices` 954 只里 **129 只（13.5%）不在包内**（被产包的**质量过滤**——ST/科创板/市值<50亿——剔掉，而它们仍是回测/绩效/周报的既有池）＋基准 `sh000852`(中证1000)/`sh000905`/`sh512100`(E2 标的) 从未入池 ⇒ 对应 pg_stat_statements 里 **43MB/天、46K 行/次** 的 `backtest_prices` 大 IN（单语句第二名）。修法见 `generate_backend_pack.py::_backtest_pool_codes` + `_EXTRA_INDEX_CODES`（**消费方池豁免质量过滤**，并打印"回测池覆盖 0 缺口"自证）。⚠️ 教训：**质量过滤只应筛"新增候选"，绝不能筛掉既有消费方代码**；同类 bug 2026-09-09 修过战法池、**漏了 backtest_prices 池**。
- ★ **线上数据源/包新鲜度的可观测端点**（排障第一站，公开）：
  `GET /api/score/kline-cache/status` ⇒ pack/local 模式直接给 `{total_cached, newest_update(包日期), source}`。
  2026-10-10 用它证实 Render 运行期 `source=pack` ✓，并发现 **Render 的包滞后 ~10h**（根因：`_pack_outdated()/_is_stale()` 调 `_latest_available_pack_day()` **不传时区** ⇒ Render(UTC) 的 22:00 闸门落在 BJ 06:00-08:00 ⇒ 当晚新包次日早上才下）。**不影响 egress**（UTC 只会算出更早的 latest ⇒ 保守），只影响新鲜度；建议修法：**只给 `_pack_outdated()` 传 `beijing_now()`**（`_is_stale()` 保持宽松，避免"包延迟→全量回退 DB"）。

## 回测数据质量
- 体检 `scripts/audit_backtest_data.py`；源异常日已标 `price_anomalies`（集中 2024，源里存在不可修复）。
- 已知待修：盘中技术面昨收；`score_single` vs `batch/top` 盘中不同分；`score_snapshot_loop` 15:15 早于数据刷新；gate-watch 全池>20s 建议读日批快照。

## 战法回测事实（2026-09-26/27）
- **样本量看独立信号日**（≥20 为格子激活判据），非笔数（一天几十只高度相关）。
- `backtest_prices` 池 = 战法选股∪近30天上榜+ETF（810 只全主板）⇒ 选择偏差且**自我固化**（没选中⇒无数据⇒永不可检验）。⚠️ 另：该表仅 **839 只**（非全市场 2083），由它自算的涨停名单必然偏少。
- 24 格矩阵（6战法×4态）19 负 5 正，正者 bootstrap 全不显著 ⇒ **现有战法体系不可交易**（已砍 `ma_convergence`）。闸门必须按战法校准。
- 数据基础：`data/zzshare_daily.db`（21 年 1672 万行含真实涨跌停价 high_limit/low_limit）+ `data/idx_daily.json`（腾讯 fqkline 分段翻页缓存）。

## 事件驱动 E2（涨停潮）
- 生效口径：`up_ratio≥0.90 且 涨停比例≥2.0%`（比例化修掉"绝对家数随池子漂移"；换算比例必须用历史中位池子 ~2444 只）。核心=**涨停潮非普涨**；涨停约束不可删（去掉 edge 腰斩）；阈值是"平台"非"尖峰"；`能过判据≠该改`。
- 可执行性终验（真实指数，T+1 开盘买持 20 日）：**中证1000 +2.76pp**(P=0.0012)、中证500 +2.03、创业板指 +1.53 可执行；沪深300 +0.12 不可执行 ⇒ **E2=小盘风险偏好回升信号**（edge 随市值下沉单调递增）。扣 0.3% 成本中证1000 +2.46pp；T+20 为倒 U 顶点（非事后挑选）；止损 -7% 负贡献不采用。展示层 `EXEC_STATS`→前端 title。
- 复利检验：只做 E2 28.4%/年 < 全程持有 35.2%/年 ⇒ **不能独立择时**，只能作"防御期提前解除"判据。独立簇 57 次中位 +5.3%、胜率 68%。
- 局限：极稀有（2021-2023 三年零事件、2026:0）；2008 熊市假信号（28 次 −1.1%）⇒ 不脱离 regime 单独用。
- 触达闭环完整：`events/alert.py` 推送（簇去重窗口=**28 自然日=20 交易日**，与 CLUSTER_GAP 一致）+ `daily_report` 1.5 小节 + `market_events` 落库。前端 Workbench 顶栏事件项（仅 E1/E2 触发占位「事件·仅提示」）。**v0 不进决策链**；升级需 §9.1 预登记判据（样本≥10天/簇≥5 等），等实盘样本（`event_live_review.py` 就绪）。
- 归档：E1 冰点/E3 恐慌反转无 edge；**行业动量无预测力**（E2 期=普涨非轮动，买宽基即可）；**小盘偏好轮动**相对多空 P=0.0000 但不可执行（轮动 +2.80% < 一直持小盘 +2.98%）。
- ★ 方法论（通用铁律）：**状态/区间类信号必须用"进入点"检验**（重叠偏差：阴跌案例每日口径 +2.82 翻转进入点 −0.50）；**事件优于状态**（当日可判定离散事件可执行）；样本内发现换定义重验；**因子显著≠可交易**（多空要有做空手段、轮动要优于静态持有）；分层收益先分年拆解+组收益用中位数+必须用超额口径剔 β；`|涨跌幅|/|跳空|>11%` 过滤异常值。

## 春节效应（P2-a，已进决策链）
- 主结果：节后 T+1 收盘买持 3 日：中证500 +1.75%（扣成本，17/20）、中证1000 +2.56%（12/12）；**春节特有**（国庆 −0.27%/五一 +0.72% 证伪"长假效应"）；**无需持股过节**（首日跳空仅 +0.18%）；仅中小盘（沪深300 不显著）；无法与 2 月季节性分离（诚实标注叠加）。
- 落点：决策卡独立字段 `calendar_exception`（`backend/app/spring_festival.py` + `trader_brief.py` + Workbench 独立块）；`overrides` 仅 regime∈{defensive,neutral_bearish} 给出；**不改 `trade_gate.evaluate`/`REGIME_ALLOWED`/`REGIME_POSITION`**（叠加提示通道，与 E2 落点一致）。
- ★ 定位技巧：用「当月最长休市缺口」自动定位长假，不硬编码（22/22 吻合农历正月初一）。
- ★ 维护：**每年国务院公告后把次年春节假期加入 `flash.rules.HOLIDAYS`**（precision 自动升 actual）。验证 `scripts/test_spring_festival.py`（勿删）。
- 踩坑：同一语义在不同分支必须共用同一推导口径（固定偏移曾致 `action=buy` 永不出现）。
- 未做（诚实标注）：企微推送（需定"推几次"防刷屏）、日报小节。

## LLM 配置
- 主力 `deepseek-ai/DeepSeek-R1`；免费站影子 `LLM_FREE_SHADOW=1`（=排除出正式链，名字反直觉易设错）。熔断单站连败 2 次→30min；排障 `/api/system/llm-usage`。`render.yaml` 只是环境变量清单非事实来源。

## 因子体检周期化
- `subfactor_ic_backtest.py` 每月首交易日随日批跑；幂等判据必须读库（Actions 每次全新 checkout）。企微推送分级：有告警 force，无告警尊重 `WECHAT_BUSINESS_ALERTS`；企微不支持 markdown 表格（自动转列表）。

## 日期口径（全项目规范）
- "数据所属日期"一律 `rules.latest_completed_trading_day()`（15:00 分界+跳周末/HOLIDAYS），绝不用北京自然日。
- **凡"是否落后/停更/过期"一律比交易日不比自然日**（中秋/国庆连休误报教训；`restore_regime_cache_from_db` 已改）。
- 两把尺子：15:00（业务）vs 22:00（包可用下限），15:00~22:00 窗口分开判，判早回退查 Supabase ≈600MB/天。
- `routers/scoring.py` 里"榜是否今天"继续用 `_today_bj()` 自然日（新鲜度判据）**不要换**。
- 批任务日期由调用方传入（`_batch_trading_day()`），不能用 now()；"连续 N 天"按交易日步进；判任务完成看 `created_at`。

## 跨市场因子口径
- 外部分子（纳指/VIX/美元）按"同日"对齐=时区前视 ⇒ 评估一律 lag1/"A 股开盘前已知"口径。
- 跨市场因子价值常是非线性、条件性 ⇒ 必须分组检验。算"隔夜变化"基准用 A 股收盘后快照，不能用 `change_pct`。

## 部署与资源
- ★★ **线上部署滞后 main**：排查线上异常第一步 `curl /api/health` 看 `build`（=RENDER_GIT_COMMIT 前 7 位）vs `git rev-parse HEAD`。旧版替代法：`git log -S "<新代码字面量>"` + curl 端点卡版本区间 + `merge-base --is-ancestor`。免费信号：免费版部署清空 `backend/data/` ⇒ 旧文件在=期间没部署。
- 数据源告警两道闸：① `_NO_WECHAT_SOURCES`（东财板块/主站——封 IP 常态有兜底）② 环境总开关 `WECHAT_SOURCE_ALERTS=0`。抑制时日志 `[health] 已抑制企微推送（…）` ⇒ **用户收到推送但无此行 = 旧代码**。
- Render free = 500MB/0.1CPU；**缓存四件套**：条数上限+单条上限+取用时物理删除+★**字节级上限**（`strategies._PRICES_BARS_MAX=50000`、`flow._FLOW_MAP_ROWS_MAX=120000`，超限整体清空——前三条挡不住"每条都很大"）。
- `cache_release.py` 主动释放：盘后 19:00 每日一次 + RSS≥75% 自适应（120min 防抖）；**绝不释放**：`tencent._cache`（盘后=收盘定稿快照）、`flash.*`（重复推送）、`wechat._token_cache`、`signal_bus`、`sync_meta`。手动 `POST /api/system/cache-release`。
- OOM 真实触发=特定请求一次性灌入（`performance._replay_track` + `flow.load_flow_map` 整表 8.8 万行≈110MB）⇒ 诊断要问"哪个请求触发的"。内存阈值判断用 `used_pct` 百分比非绝对 MB。
- 生产 Python 3.9 禁 PEP 604；FastAPI 无 await 路由写 `def`；后端日志 ASCII。

## 环境传导链
- 四层合成 `mainforce/confluence.py`：宏观±2 + regime±2/±1 + 板块±1 + 个股±2，sum(−7~+7)。

## Coach
- `rules.yaml`/`rules.py`/`audit.py`/`monitor.py`；持仓源 `paper_positions`+`user_portfolio`；真实持仓默认止损=成本×0.92。API `/api/coach/alerts|consistency|abandon-reasons|plans*`。

## 项目约定
- **推送纪律（用户明确）**：不自行 git push；改完先汇报改动+验证结果，等指令再推；未明确要求不自动 commit。
- gh-pages 三写入者必须同一 `concurrency.group` + `cancel-in-progress:false` + `keep_files:true`。
- 新看板/tab 自带一行定位（是什么/不是什么/下一步）；榜单=候选池+变化监测非买点清单；新 tab 接 `startAutoRefresh`。
- ★★ **同一数据两处渲染=必然漂移**：共用同一 computed 或只留一处渲染（教练卡实证：一处过滤 executed 一处没漏；`readonly` 漏传致重复回写）。`TodoCard` 自判 `isDone`（`executed` 是 `'yes'|'no'|null` 三态字符串），不再依赖外层传参。
- ★★ `coach_alerts.alert_time` 是完整 ISO，展示一律 `displayMeta.hhmm()`；后端刻意不改（历史行已 ISO，改则同列混格式）。
- ★★ 行情缓存 `change_pct` 开盘前=上一交易日涨幅、开盘后=当日涨幅 ⇒ 历史名单用 `backtest_prices` 自算的 `prev_limit`（当天恒定）；`_prev_trading_day` 走交易日历（休市≠缺口）。
- ★ "信息只在某阶段看得到"=渲染位置问题，先查 `v-if` 在哪个分支再谈数据。
- ★ 顶栏"摘要+点击浮层"既定模式；缩写前提=完整信息有别的可达入口；内存只在 ≥75% 才亮。
- `macro.direction` 早盘锁定（`macro_daily_loop` 08:55–13:00 一次，当日固定；未锁定回退实时算，有锁定快照绝不动）；`flashDiag`/`sentiment` 全天会变。
- ★★ **接口实时≠页面实时**：新刷新项必须进 `startPolling()` 120s 组，否则盘中静默不更新。
- ★ ★ 时间字段两种差 8h 错法：`datetime.now()`/`time.strftime()`（服务器本地）与 `fromtimestamp` 无 tz ⇒ 正解 `beijing_now()` 或带 tz 的 fromtimestamp。
- ★ `composables/displayMeta.js` = 前端 helper 唯一共享源：`PHASE_STYLE/PHASE_CN/phaseCn/STRATEGY_SHORT/readyCls/readyChipCls/stockHref/xqUrl/xqIndexUrl/pctClass/signNum/scoreClass/hhmm/dirColor/levelColor`。**指数跳转必须 `xqIndexUrl(prefix,code)`**（`000001` 撞平安银行；市场前缀唯一源=后端 `MAIN_INDICES` 经 `/market/overview` 下发）；新组件 import 不许页面内再定义。
- ★ 主力 `phase` 全覆盖六档 vs `signal` 稀有（157/2083）⇒ 要每行有标签用 `phase`；中文名优先后端 `phase_cn`，`phaseCn` 兜底。
- 站内带 tab 页面跳转统一 `?tab=<key>` 直达；默认 tab 显式排除（否则守卫跳过加载⇒白屏）。
- 图表偏好：榜单/分布纯 CSS 条，Workbench 不引 echarts。
- 测试脚本 import app.database 前设 `DATABASE_URL=sqlite:///...`（否则跑线上 Supabase）。

## 主力节奏跟随框架（项目北极星）
- 一句话：跟对主力节奏但永远慢一步。三层可靠度递减：①状态（有回测背书→可决策）②方向（flow5 倒U/两融，看连续趋势）③时点（仅形态参考，永不进决策链）。
- 红线：跟"已确认的节奏"不抢跑；盘中观察→盘后确认→次日执行。
- ★ flow5 IC/accum-distribution 是横截面证据（选股），"跟节奏"是时间序列命题（择时）——横截面≠时间序列。Phase 0：Gate A NO-GO、防守侧=价格状态别名（provisional）、时间序列未 KILL。

## 工程方法论（评审沉淀）
- 任何"节拍/周期"检验先跑 null 模拟（bootstrap 摧毁时序结构）对比。
- 控制变量是生死项：表观效应被价格状态解释掉 2/3 是常态；未控制归因不进结论。
- 失败条款先于结果落盘（预注册 GO/DOWNGRADE/KILL，防 forking paths）；工程失败≠科学证伪。
- 稀有标签一致率有基数率 bug ⇒ sensitivity/precision/κ 条件化。

## 踩坑纪律（一行精华版）
- **验证**：py_compile 查不出 NameError ⇒ 真 import+真调用+独立进程；空数据 0==0 假绿灯（先校验样本非空）；★★ 同批次不对同一文件连发多编辑（静默丢，分批+每批 grep 复验关键行）。
- **数据/字段**：缺数据 None 不填 0；`dict.get(k,default)` 对 None 不生效；跨系统按字段名取不按位置；同一指标一页一口径；一个字段不回答两个相反问题；表列 vs 代码常量先 grep 谁在用。
- **外部源静默失效**：恒 0 比报错危险（被 docstring"属正常"合理化）⇒ 显式状态字段 `available/reason/stopped_since` + 保留 falsy 旧字段向后兼容（北向 2024-08-19 断供、`flash_calendar` 停 09-04 实证）。
- **日期/时间**：时间条件 vs 日历条件正交；日期键"运行日/交易日"两派混用失效；相对日期声明锚点；缓存写入时刻≠数据时刻。
- **SQL/双库**：PG/SQLite 四坑（JSON 列已对象别再 loads / Decimal 显式 float / NULLS LAST 是 PG 专有 / 占位符统一 %s）；`INSERT OR REPLACE` 整行替换先读合并；加列用 `ALTER TABLE ADD COLUMN`。
- **PowerShell/git**：`git commit -m` 单引号且内容无引号；`python -c` 含中文/%/in 易解析坏 ⇒ 转义写法或临时脚本。
- **并发/前端**：同文件多 edit 串行；改枚举同步硬编码数量；删函数 grep 全库（含注释）；前端未 import 却调用=静默失效（vite 不查未定义标识符）。
- **决策/语义**：缺证据≠反证；用户说改名先确认同一概念；完成时刻不确定→事件驱动>轮询；展示与计算层都显式区分 0 与缺失。

## 本机环境 / 用户偏好
- Windows GBK：print 别带 emoji/⇒；本机起后端先 `PYTHONIOENCODING=utf-8`（否则 lifespan 直接失败，易误判）；HTTP 验证最稳=单进程 uvicorn 子线程+urllib。
- 日报不推企微只前端 `/report`；主使用习惯=每天挂 Top50 看实时行情（主链路=后端评分，前端本地计算次要）。

## 当前进行中（2026-09-29）
- **战法体系 bootstrap 证伪** ⇒ 现有 6 战法全部不可上线；框架=诚实告知"战法体系不可交易"。
- **E2 已完成全链**（阈值比例化→可执行性四步验证→前端顶栏→推送/日报/落库闭环）⇒「推得出去+标的明确（中证1000 ETF 512100）」。剩余：① 等实盘样本（`event_live_review.py`）② 升级决策链单独预登记（报告 §9.1 判据已落盘）。
- **春节效应已进决策链**，观察 2027 实盘首触（届时维护 HOLIDAYS）。事件方向其他候选：国庆/五一已证伪；FOMC/两会缺历史数据需评估成本。
- **北向资金已评估否决**（断供+自有资金流无预测力双重否定）；事件域聚焦三件：E2+春节+解禁避险。
- 评分权重/衰减冻结，等 shadow_rank ≥10 快照日+defensive 截面日（约 2026-10-08 触发 `compare_shadow_rank.py`）。
- 待用户配置：`BATCH_CALLBACK_TOKEN`（Render env + GitHub secret 同值）。
