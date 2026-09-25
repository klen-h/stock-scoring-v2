# 长期记忆（stock-scoring-v2）

## 架构与运行形态
- 单进程 FastAPI（`backend/app/main.py`）提供 `/api/*` 与前端静态页；生产用 `uvicorn app.main:app --host 0.0.0.0 --port 8000`，**不要 `--reload`**。
- **前端生产部署 = GitHub Pages 自动**：`.github/workflows/deploy-preview.yml` 用 **pnpm** 构建并推 `gh-pages`，主入口 `https://klen-h.github.io/stock-scoring-v2`；Render 后端仅兜底 `frontend/dist`。
- 数据库默认 SQLite，设 `DATABASE_URL` 走 PostgreSQL（Supabase 东京）；`app/database.py` 自动转换 `%s` 占位符兼容两者。
- 调度器：`app/flash/scheduler.py` 起约 30 个 asyncio loop，按日幂等。**LLM/轻量推送类用 `asyncio.create_task`**（保留），重活走 `*_heavy`（`RENDER_READ_ONLY=1` 全关）。
- 决策简报：盘后 Actions 日批生成，`trader_briefs` 按 `(date, phase)` 落库；盘前 Render 09:10–11:30 窗口幂等生成。盘后链路：K线 15:30 → 指标 16:40（Actions）→ 快照 18:00 → 主线/消息分/日报 19:15+。

## 数据与缓存
- `kline_cache` 存原始 OHLCV（`CACHE_POOL_SIZE=500`，`CACHE_KLINE_COUNT=500`），服务详情页与筹码/主力叠加；`indicator_cache` 存预计算指标序列，服务评分链路（`batch/top/bottom`、快照）。
- `indicator_cache` 有效期 `MAX_INDICATOR_AGE_HOURS=36`；`kline_count` 落在 (0,250) 视为截断、指标跳过。
- 缓存命中决定评分吞吐：命中时并发 10 无 sleep，否则并发 3 + `sleep(0.3)` 防 WAF。
- 版本门控 `app/sync_meta.py` 管理日更远端数据；研究脚本走 `app/research_cache.py` 并优先读本地包。
- 两条链路并存：后端读 DB 包；前端本地评分读 GitHub Pages 的 `kline-pack` + `indicators-pack`。**`backend-pack.db.gz` 只给 Python，浏览器不读**。
- ⚠️ **两条「包发布链」互相独立，故障不传染（易混，2026-09-23 澄清）**：
  · `kline-data.yml`（18:00）→ 前端包 `kline-pack-*` + `indicators-pack` + `realtime-quotes.json`
    （`deploy-pages` job 是 `needs: [frontend-pack]` ⇒ 抓取失败 ⇒ 发布 **skipped**，前端包不更新）
  · `backend-pack.yml`（19:00）→ `backend-pack.db.gz`（**日批与后端唯一依赖**）
  ⇒ **前端包失败 ≠ 日批失败**（日批只吃后端包）；唯一连带影响是 `realtime-quotes.json`
  未更新 ⇒ backend-pack 用旧行情/自拉兜底 ⇒ 只影响 `codes` 表 `name`/`market_cap` 新鲜度。
  ⇒ 前端包超时的**真瓶颈是 K 线阶段**（1566 只×单只请求，`PACK_QPS=2` 顺畅也 ~13 分钟）
  + WAF 冷却，**不是** 240 批行情（那部分只占几分钟）⇒ 优化别找错瓶颈。
- 已知待修：盘中技术面是昨收；`score_single` 实时算与 `batch/top` 缓存算盘中不同分；`incremental_update` 是死代码；`score_snapshot_loop` 15:15 早于数据刷新；**`gate-watch` 首次全池计算（2196 只）> 前端 20s 超时 ⇒ 建议改读 `gate_snapshot_history` 日批快照（2026-09-22 发现，线上 Render 更慢必超时）**。

## ⚠️ 路由顺序与「真跑验证」纪律（2026-09-22 观察池空的教训）
- **FastAPI/Starlette 按注册顺序匹配**：`routers/scoring.py` 的 `@router.get("/{symbol}")`
  （**单段通配**）在 L918 ⇒ **新增单段静态路由必须注册在它之前**，否则请求被吞掉、
  当成股票代码 ⇒ 返回 **`200 {"error":"未找到股票 xxx"}`**（不是 404）⇒ 前端 `catch`
  抓不到 ⇒ **静默空**。（`/batch/*` 等两段路径不受影响；`/gate-watch` 已改
  `/batch/gate-watch`。已加固：`/{symbol}` 对含 `-`/`_` 的 symbol 返 404。）
- **验证纪律（三条，都是验证方式本身的坑）**：
  1. **只 `import` 函数直接调用 ≠ 验证 HTTP 路由** —— 观察池本地"实测 156 只"走的是
     函数调用，HTTP 层早已被通配吞掉；凡前后端配合的功能，验收必须走**真实入口**
     （起后端 + HTTP / 浏览器）。
  2. **`pnpm build` 通过 ≠ 功能可用** —— build 不查运行时契约（如 axios 拦截器返回的是
     `response` 还是 `response.data`）；前端改动要真跑页面。
  3. **前端「本地优先」路径要单独验** —— 详情页 `tryLoadLocalAll` 成功就不调后端，
     只测后端 API 会漏掉本地分支（P1 标签丢失即此类）。

## 告警规则口径：「绝对量」vs「日内路径」（2026-09-23 冲高回落的教训）
- **同一现象要问两遍口径**：『跌了多少』（现价 vs 昨收 = 绝对涨跌幅）与『从日内高点回撤
  多少』（路径）是**两条不同规则**，只做前者会漏掉典型形态。实例：`intraday_alerts` 原只看
  绝对涨跌幅（上证 -1.5%）⇒「早盘冲高 +1.2%、午后回落到 -0.5%」**永不报警**（用户报的场景）
  ⇒ 已加 `_index_reversal_alert`（`peak ≥ 门槛` **且** `距日高 ≤ -门槛`）。
- 同类新规则：**双条件缺一不可**（否则把低位窄幅震荡误报成回落）；高波动标的门槛按倍数加严。
- **别为一条新规则多拉一次外部请求**：`_index_watch_quotes()` 一次拉取喂多条规则
  （`get_index()` 无缓存层，各拉一遍即翻倍）。
- **新信号/告警上生产前先做预测力检验**（同「真跑验证」的精神）：要问两遍 ——
  ①会不会过吵（阈值频率）②**响了有没有用**（对照组收益差）。工具模板：
  `scripts/reversal_edge_check.py`（判定标准**预先定死**防事后挑格子）。
  实例 2026-09-23：冲高回落形态按预登记判定为 **NO EDGE**（A−B 差 +0.10pct，110 vs 127
  样本）⇒ 功能保留但**改了 prompt 语义**（禁止 LLM 给方向性建议）—— 防「把无预测力的
  形态当交易信号」，与「白名单负期望」同类错误。

## 项目已有能力清单（**提需求前先查这里 —— 多数需求已有零件**）
- 盘中警示：`app/flash/intraday_alerts.py`（3 分钟一轮、READ_ONLY 例外必跑；绝对涨跌幅 /
  冲高回落 / 涨跌比 / 跌停 / 黑天鹅 ⇒ 企微，每类每档每日一次）
- 矛盾扫描：`app/contradictions/`（`scanner.py` 多维度；`store.load_contradictions(resolved=0)`
  读未兑现；`scheduler._run_midday_scan` 午间实时扫描并推企微）
- LLM 提示词素材（`app/flash/llm.py`）：`format_user_holdings()`（持仓 + 实时价 + 日内高低/距高）、
  `format_a_share_context()`（regime 序列 + 沪深300 含距高 + 宽度）、`_internal_context()`（温度/宏观/行业资金）
- 推送 `app/flash/wechat.push_markdown_batched()`｜数据源健康 `app/health.py`（`_NO_WECHAT_SOURCES`
  里的源只进页面通知）
- **全部推送的唯一单点入口 = `flash.wechat.push_markdown_batched`**：要「捕获/汇总/改推送行为」
  改这里，**不要**逐个改 ~10 个调用方（`flash/signal_bus.py` 已在此埋记录器 ⇒ 前端「今日雷达」时间线）。
- 持仓类告警**已有两处**（新增前必看，否则重复告警）：`coach/monitor.py`（30s 轮询 light 规则
  ⇒ `notify("coach", force=True)`，`dedupe_key`=日|规则|标的 一天一次，10:30/14:45 体检卡）+
  `strategies/exit_alert.py`（止损缓冲 0.5% / 破支撑 1% / RSI 高位回落 10 点 / 放量下跌 2 倍量）。
- 持仓聚合视图 = `backend/app/portfolio_radar.py` + `GET /api/score/batch/portfolio-radar`
  （前端「我的持仓」tab）：**全复用既有事实源**，配 single-flight + 进程缓存 + `scheduler` 预热。
- **运维出口五件套**：① 数据新鲜度 `GET /api/system/status`（首页卡片，断链一眼可见）
  ② 文件型数据 `GET /api/system/runtime-files`（`app/data_files.py`：启动 + **每 6h 核对 +
  每项每日一次企微告警** —— 要加"定期体检"类检查可照它的模式）③ **进程内存
  `GET /api/system/memory`**（`routers/system.py`：RSS/峰值/线程 + **52 项模块级缓存探针** +
  GC 对象类型分布 + diff；首页自动调用且 `types=0`）④ **内存看护 `app/memory_watch.py`**
  （2026-09-23）：采样落库 `memory_probe` ⇒ **跨 OOM 重启仍有趋势**（③ 的 diff 基准在进程内，
  重启即清零，而那正是最需要对比的时刻）；每 30 分钟一条 + 5 分钟去抖；RSS ≥**80%** 上限推
  企微且**每自然日一次**（复用 `store.is_schedule_done` 北京日期比对）；保留 14 天。
  ⑤ **数据库体积 `GET /api/system/db-usage`**（2026-09-24）：Supabase 免费档 **500MB/项目**，
  ★ **超限 ⇒ 项目只读**（写入全失败、**不会自愈**，比 OOM 更致命）⇒ 必须常显。
  PG 走 `pg_database_size` + `pg_stat_user_tables`（**分表体积/活行/死行 dead_ratio**），
  SQLite 降级报库文件大小；60s 缓存；异常 fail-open；≥100% 报🔴只读、≥80% 报清理建议。
  ⚠️ 普通 `VACUUM` **不缩小文件**，只有 `VACUUM FULL` 才会（短暂锁表 + 需临时空间）。
  ★ ③ 与 ④ 的采集**共用** `collect_memory_snapshot()`/`diff_caches()`（单一实现，防口径漂移）。
  ★ 五件套共同原则：**先量后修、只读探针零副作用、失败静默不反噬主流程、大容器采样不复制**。
- **库体积治理 `app/db_retention.py`（2026-09-24）**：日批最后一步跑（`daily_batch` 的
  `retention` 任务）。**只清理「已逐一核实全部读取点只读最新」的表** —— 当前为
  `mainforce_state` / `ranking_live`（默认 `RETENTION_DAYS=14`）。⚠️⚠️ **反直觉**：保留期
  不是"立刻变小"而是「**先涨到上限再平**」⇒ 天数宁短勿长（`mainforce_state` 现 11 天/7.5MB，
  设 90 天会先涨到 ~61MB）。★ 安全阀 `KEEP_MIN_DAYS=3`：**至少保留最近 3 个不同日期**，
  防日批连挂时 `date < cutoff` 把表删空。**加表前必须先核实所有读取点**（勿凭猜）。
- ⚠️ **写测试脚本的硬纪律（2026-09-24 踩坑）**：`.env` 里的 `DATABASE_URL` 指向**线上
  Supabase** ⇒ 不显式覆盖的话，测试会**直接跑在线上库**（实测误建了测试表）。
  **必须在 `import app.database` 之前**设 `os.environ["DATABASE_URL"] = "sqlite:///backend/data/app.db"`
  并断言 `db._use_postgres is False`。（顺带的好处：必要时本就不该连线上。）
- ⚠️ 本地 Windows 控制台是 **GBK** ⇒ `print` 含 emoji/`⇒`/`✓` 会抛 `UnicodeEncodeError`
  ⇒ 测试/脚本的日志一律用 **ASCII 标记**（`memory_watch.py` 已有教训）；临时用
  `$env:PYTHONIOENCODING="utf-8"` 也可。

## Supabase egress 治理（2026-09-18 沉淀，详见根目录 `EGRESS.md`）
- **egress 是账号级的**：本地进程与线上 Render **共用** 5GB/月（≈167MB/天）额度 ⇒「本地跑」不免费。
- **头号放大器 =「进程内缓存 × 低频数据」**：本地 `run.py --reload`（改代码即重启）+ 各种脚本
  ⇒ 每重启一次就整份重读。已治理 4 处 —— `mainflow_history` 整表读 8.7MB/次、
  `market_snapshot` 整行 336KB/次（两处读取点）、`stock_industry JOIN stock_finance` 1.68 万行/次、
  研究脚本 `backtest_prices` 22.7 万行/次（无 `start` 读全历史）。常态 ~140MB/天 → 预期 ~20MB/天。
- **跨进程版本门控四手段**：① `sync_meta` 版本号（写入方能配合）② 数据自身 `saved_at`
  （写入方在别的机器、无法要求其配合）③ `pg_stat_user_tables` 写入计数指纹（无时间戳列可用）
  ④ 兜底 TTL。**探测只取版本列**（单行几十字节），别整行读。
- **排障标准动作**：`pg_stat_statements` 按 `rows` 排序 + **必查 `stats_reset`**（否则 N 天当一天）；
  取**完整** SQL（`LEFT(query,N)` 会误判批大小）；表级 `seq_tup_read` ≠ egress（**只看 `rows`**）。
- **已知噪声**：`pg_timezone_names` / `pg_stat_*` 是 Supabase 平台语句（`egress_probe.py` 的 `_NOISE`），
  不是本项目；`pgbouncer.get_auth` 返回 1 行 1 列可忽略。项目自带 `scripts/egress_probe.py` 做字节级对账。
- **短 TTL 陷阱**：日频数据配 5min 进程内 TTL → 常驻进程最坏 ≈288 次/天（`market_snapshot` 曾 97MB/天）。
  **TTL 长短必须匹配数据更新频率。**

## 回测数据质量（2026-09-18 体检沉淀）
- **体检工具**：`scripts/audit_backtest_data.py`（九节：总量/覆盖/数据质量/连续性/源异常/关联表/
  包与库一致性/异常日×信号重叠/落库）。**遵守 egress 纪律**：能聚合的用聚合 SQL，需逐只序列的走
  `research_cache.ohlc_for`（本机）⇒ 跑它几乎零 Supabase 流量。
- **两个已修的坑**（都在 `backend/backfill_history.py`）：
  1. **掉池即停更** —— 回填池只取「战法信号 + 近 30 天上榜股」，股票掉出榜单就**永久停更**
     （实测 73 只漏回填中 **70 只不在池里**；`601012` 停 08-21 而源上有 19 个交易日新数据）。
     已并入「已入库的全部代码」（回填池 641 → 749）。
  2. **库与源不一致** —— ① `save_prices` 是 `ON CONFLICT DO NOTHING`，**全量重拉并不覆盖已有行**
     （需 `replace=True`）；② `_basis_changed` 阈值 `1e-6` 太敏感，**0.09% 的精度噪声就会触发
     全量重拉**（749 只 × 25 分钟）⇒ 已放宽到 **0.2%**（真基准变更在股息率量级 ≥0.5%）。
- **源数据异常日（不可修复，已标记）**：**120 只（16.3%）/ 334 处（0.062% 的 bar）**，
  集中在 **2024 年（75%）**。已确认**在源里就存在**（直接抓腾讯原始 `qfq` 序列同样是
  −12.18% / +16.00%，而同期别的股票正常）⇒ 与拼接/解析/复权计算无关。
  清单落库到 **`price_anomalies`**（`audit_backtest_data.py --write-anomalies` 生成），
  读侧 `data.anomalies_in()` / `data.has_anomaly()`（**fail-open，不调用则行为与从前完全一致**）。
  **★ 与当前战法回测零重叠**（异常日全在 2025-10 之前，战法信号数据只有 2026-08 之后）
  ⇒ 现有结论不受影响；**但** `quality_defense_backtest` / `mainforce_factor_backtest` 用 3 年数据，
  **曾以为会命中** ⇒ **2026-09-19 已接入并实测证伪**：真正的约束是**数据可得性**，不是 `years=3` ——
  · `stock_finance.notice_date` 仅覆盖 **2025-07-07 起** ⇒ `quality_defense_backtest` 的截面样本
    全落在 2025-07 之后，命中 **2 条 / 36128**；
  · `mainflow_history` 仅覆盖 **2026-03-16 起** ⇒ `mainforce_factor_backtest` 的截面样本
    与最晚异常日（2025-10-29）**无交集**，命中 **0 条**。
  ⇒ **两脚本的既有结论均不受污染**（qd 剔除后结论一字未变）。过滤已接：
  `data.anomaly_index()` / `window_has_anomaly()`（一次查库 + 内存 bisect），
  默认**只统计不剔除**，`--drop-anomaly` 才剔除 ⇒ 将来新增异常日（2026 年）会自动被兜住。

## LLM 配置（2026-09-19 起有启动自检）
- **`env_check.log_switch_report()` 现在同时打印 LLM provider 链**（覆盖 `app/main.py` 启动 +
  `scripts/daily_batch.py` 两个入口）⇒ 空链 / 链首非 free / `SHADOW=1` / 缺哪个变量，
  **启动第一屏就能看到**，不必再靠反推猜。
- 只回填资金流、**不碰 LLM** 的步骤用 **`ENV_CHECK_SKIP_LLM=1`** 抑制
  （`backend-pack.yml` 的 mainflow 步骤已设），避免误报导致**告警脱敏**。
- **`LLM_FREE_SHADOW` 语义反直觉**：`1` = 影子模式 = **把免费站排除出正式链**；
  `0` = 免费站进链首。名字像"启用"，极易设错（2026-09-19 用户线上就是这么中招的）。
- **`render.yaml` 在本项目里只是「环境变量清单文档」，不是事实来源** ——
  Render 服务是**控制台手工建**的，文件里的 `value:` / `sync: false` 都不生效；
  只把它当"控制台该配什么"的 checklist 用。任何变量都可能漏配，且**静默**。
- **`LLM_FREE_API_KEY` 是多环境共用的**（Render 常驻 + Actions 日批 + 本地脚本）
  ⇒ RPM/TPM 被三家瓜分 ⇒ 撞车即 **429**。主解是 `LLM_MIN_INTERVAL`（跨进程不协调），
  多 key 分环境是辅助。

## 因子体检周期化（2026-09-20 起）
- **`scripts/subfactor_ic_backtest.py` 每月首个交易日随日批自动跑**
  （任务名 `subfactor_ic`，`DEFAULT_ORDER` 第 18 位；`--tasks subfactor_ic` 可强制）。
- **幂等判据必须读库**（`report_store` 中 tag=`subfactor_ic` 最新报告是否属于本轮交易日的月份）——
  **不可用文件判据**：日批跑在 GitHub Actions，工作区每次 checkout 全新 ⇒ 文件级判据恒为
  "没有报告" ⇒ 会天天跑。与 `task_weekly_report` 的 `_weekly_due()` 同一原因、同一做法。
- 每轮与上轮基线（库中 `subfactor_ic_latest.json`）对比，输出「IC 变化表 + 负 IC 复现度」；
  三条告警判据**写死在 `_review()`**：负 IC 复现度下降 / |上轮 IC|≥0.04 符号翻转 /
  **倒U两个子项（主力净流入、主力极端流入）IC 转负 = 倒U背书失效**。
- 报告**落库**（tag=`subfactor_ic`，前端「回测中心」可见）+ 落盘 `backend/backtest_reports/`。
- **企微推送分级**（`daily_batch._push_ic_summary`）：**有告警 ⇒ `force=True`**（关键通知，
  穿透业务开关）；**无告警 ⇒ 普通推送**，尊重 `WECHAT_BUSINESS_ALERTS`（**默认 '0' = 关闭**，
  即默认不发月报）。⚠️ **`push_markdown_batched` 返回 None ⇒ 无法判断是否真发出** ⇒
  必须由调用方显式检查 `WECHAT_WEBHOOK` / `BUSINESS_ALERTS_ENABLED` 并把状态写进返回值，
  否则会出现「以为挂了推送、其实静默没发」。
- ⚠️ **企微不支持 markdown 表格**：`push_markdown_batched` 会经
  `wechat_fmt.markdown_tables_to_lists` 把表格转成列表（幂等）⇒ 推送内容直接写成行内拼接更可控。

## 日期口径（2026-09-20 扩展；**易错点，改前必读**）
- **"数据所属日"的全项目唯一口径 = `rules.latest_completed_trading_day()`**
  （15:00 分界 + 跳周末 / `HOLIDAYS`）。已收敛的写者：
  `ranking_live.rank_date`（`live_ranking._rank_day()`）、`signal_persistence`（`_scan_day()`）、
  `strategy_results.scan_date`（`base.save_scan_result`）、`paper_trading.auto_ingest_signals`。
- ⚠️⚠️ **`routers/scoring.py` 里"库里的榜是否今天的"判据必须继续用 `_today_bj()`（自然日），
  不要换成 `_rank_day()`**：那是**新鲜度**判据 —— 盘中必须与库里的上一交易日榜**不匹配**，
  才会退化走在线两阶段拿实时行情；换成交易日口径会让**盘中误用昨日静态榜**。
  ⇒ **同一模块里两个日期函数并存是有意的**（`_today_bj` = 今天这一"生成日"，
  `_rank_day` = 数据所属交易日），改任何一个之前先想清"调用方要的是哪个语义"。
- `shadow_rank_daily.rank_date` **不是自己算的** —— 照搬 `ranking_live` 的 `MAX(rank_date)`；
  所以 `ranking_live` 的日期口径错会**连带**污染它（2026-09-20 修的就是这条链）。
- **日批跨午夜**是这类 bug 的共同触发场景（9-18 深夜的批 → 9-19 凌晨落库）；凡"盘后写快照"
  的任务都应使用 `latest_completed_trading_day()` 而非自然日。
- 设计动机：2026-09-20 首次体检发现**技术面 8/8 子项 5-10 日负 IC**（A 股短窗截面反转），
  并解释「周报买入信号 5 日胜率 41.5%」——结论会随样本期漂移，必须周期化复核。
- ⚠️ **2026-09-20 深夜重审降级**：该"最强发现"对采样密度敏感——同窗 step 5→1，
  负 IC 砍半（RSI -0.218→-0.093、KDJ 转正）⇒ 修正为"约 4 个弱负（0.05~0.09）+
  **尾部驱动**"（负 IC 几乎全由最高分位/超买档贡献，中段无害）⇒ 修正方向是
  "技术分最高档尾部过滤/减分"而非整体降权。详见体检报告 §6 附录。
  **默认 step 已改 1**（2026-09-21），首轮与 step=5 旧基线的"变化偏大"标记属预期。

## ⚠️ 跨市场因子的口径纪律（2026-09-20 实测教训，重要）
- **外部分子（纳指/VIX/美元）按「同日」对齐 = 时区前视偏差** ⚠️：这些序列的『当日』
  波动**大部分发生在 A 股收盘之后**（美股夜盘）⇒ 等于用『次日才知道的信息』预测
  ⇒ **不可交易**。`latest`/same 口径算出的高 IC 会**系统性高估**。
- 实测（`scripts/fake_lead_diagnosis.py`）**same → lag1**：NQ `+0.1609→+0.0115`
  （塌陷 93%）、VX `-0.1124→-0.0052`（95%）、UDI `-0.0972→-0.0230`（76%）
  ⇒ **Phase 1「外部因子可前瞻」的判定须降级**；评估一律用 **lag1 / "A 股开盘前已知"** 口径。
  稳健性：无论序列 `date` 是美东还是北京语义，结论一致（都含 A 股收盘后信息）。
- **但 `regime_external_lead_test.py`（Phase 2）不受影响** —— 它的分组检验**本就用的 lag1**
  ⇒ defensive 内「预警 vs 对照」差 1.44/2.83pt 依然成立 ✓
- **通用纪律**：跨市场/外部分子的价值常是**非线性、条件性**的（只在脆弱 regime 触发）
  ⇒ 「全样本 IC≈0」与「条件子集内有价值」**可以并存** ⇒ 必须用**分组检验**评估，
  只看全样本 IC 会误判为无效而丢弃。
- **本项目引号纪律**：写含引号的中文串（f-string 或普通字符串）一律用 `『』` ——
  Py3.11 下 f-string 内嵌同类引号是**语法错误**（PEP 701 仅 3.12+），普通字符串内嵌
  同类引号同样报错。今日两次踩坑。
- **★ 截面采样密度纪律（2026-09-20 体检报告重审教训）**：**step=5 的 10 个截面
  不足以支撑因子级结论** —— 同窗 step 5→1，技术面负 IC 直接砍半
  （RSI -0.218→-0.093、KDJ 转正）。凡截面采样型回测（子因子 IC / 分层胜率），
  结论前必须做**密度敏感性检验**（step=1 复核）；`subfactor_ic_backtest.py` 的
  周期体检默认 step 建议改为 1。负 IC 结构多为**尾部驱动**（Q5 超买档贡献绝大部分，
  中段无害）⇒ 修正方向是"尾部过滤"而非"整体降权"。

## 日期口径（全项目规范 —— 2026-09-19 第 3 次踩坑后固化）
- **凡「数据所属日期」一律用「最近的已完成交易日」，绝不用北京自然日。**
  自然日只在日批正常时点（20:43 盘后）碰巧等于交易日；**凌晨 / 周末 / 节假日 / 盘前**
  都会错位（表现为"信号日期是周六""连续上榜跨周末清零""包日期永远对不上"）。
- **可复用实现**（都跳过周末 + `flash/rules.HOLIDAYS`，节假日表含 2026 全年）：
  · `pack_source._latest_available_pack_day()` —— 包日期，**22:00** 分界
  · `scheduler._latest_trading_day()` —— **15:00** 分界
  · `signal_persistence._scan_day()` —— **15:00** 分界（2026-09-19 新增）
- ⚠️ **这两把尺子在同一流程里必须都对照（2026-09-23 日批事故）**：**15:00 分界**算的是
  「数据所属交易日」（业务口径），**22:00 分界**算的是「包**本应可用**的下限」（工程口径：
  后端包 19:00 才构建、~20:43 发布，判早了会让读侧判陈旧 ⇒ 全量回退查 Supabase ≈600MB/天）。
  **在 15:00~22:00 窗口二者会分开**：若只用 22:00 那把尺子（`got >= want`），会把"还没发布"
  当成"可用"⇒ 用**昨天**的 K 线算出 `scan_date=今天` 的信号 = **日期与数据脱钩且静默**
  ⇒ 已在 `daily_batch.ensure_pack_fresh` 补第二判据 `need = _batch_trading_day()`（15:00 分界），
  放行条件收紧为 `got >= need`，并**接住返回值中止**（原先丢弃 ⇒ 失败也继续跑）。
  **判据不达标时宁可中止，也不用错日期数据**（同"宁可返回空也不用错日期"的项目纪律）。
- **日批时间线（北京，改相关逻辑前必看）**：kline-data **18:00** → backend-pack **19:00 触发**
  （脚本注释按 ~1h43m 估 ~20:43，**实测 2026-09-23 是 ~20:00 就完成**）→ daily-batch 由
  `workflow_run` **立即接棒**（~74 分钟）。
- ⚠️ **`workflow_run` 立即接棒 ⇒ 自动日批天然紧贴发布、游走在"Pages 生效空窗"边缘**
  （`pack_source` 已记录：「Pages 站点部署/CDN 有 **1~2 分钟空窗**，本地包 mtime 新鲜但
  内容是旧的」）。2026-09-23 实测踩中：backend-pack 20:00 完成、日批 20:01:09 下载 ⇒
  拿到的是**旧包（9-22）**，而日志「校验通过: 2026-09-22（此刻应可用 2026-09-22）」
  **两值相等、看起来完全正常**（因为 22:00 前 want 只要求上一交易日）。
  ⇒ 已加第二判据 `need`（见上）⇒ 判据不过会自动重下等新包，**覆盖空窗**。
  ⇒ **手动补跑**：随时可跑（判据会自己等），但若在判据修复前/用旧代码跑，务必加 `--force`
  （忽略"当日已完成"标记，否则会跳过）。
  **15:00 分界是必须的**：盘前/盘中跑到时当日 K 线还不存在，用"今天"会让日期与价格脱钩。
- **已修**：`scripts/daily_batch.py::ensure_pack_fresh` 的 `want`、
  `app/mainline.py::compute_mainline` 的 `date`、`signal_persistence` 的 4 处。
- **✅ 已修（2026-09-19，成对改完）**：口径统一到 **`rules.latest_completed_trading_day()`**
  （纯函数、唯一真源），四处委托：`base.save_scan_result`（写 `strategy_results.scan_date`）、
  `paper_trading.auto_ingest_signals`（按它查出结果入池）、`router` 的缓存命中判定、
  `signal_persistence._scan_day()`。
  · **未动**：`paper_trading._bj_date()` / `base._bj_today()` —— 另作持仓/结算的**北京日期**用，
    语义不同（09-08 事故后刻意保留的那层）。
  · **附带收益（幂等）**：改成交易日后同一批信号 upsert 到同一行 ⇒ 凌晨补跑不再产出
    "周六"的重复行，白名单重放不再把同一批信号算两次。
  · 仍按 **`scan_date >= since` 区间查询**的读者（`backfill_history` / `trader_brief`）天然免疫。
- **同类第二条**：任何"连续 N 天"统计必须按**交易日**步进 ——
  `signal_persistence._calc_consecutive_days` 原本按自然日（跨周末必断链，报 1 而非 2），
  2026-09-19 已修并回归验证。
- **`--rebuild`**：`python backfill_history.py --rebuild` —— 对已入库每只全量重拉并**覆盖**
  （749 只 ≈ 25 分钟，幂等）。2026-09-18 已跑完：**749/749 成功、零失败**。
- **已知未定论**：库与源之间有 **0.01%~0.09% 的系统性精度差**（几乎每个交易日都有）——
  不影响回测量级，但若将来做「逐日精确对账」需先弄清。
- **样本期偏短**：`strategy_results` 仅 **128 行 / 22 个扫描日（2026-08-20 ~ 09-14）**
  ⇒ 战法回测的有效样本期约 1 个月。

## 部署与资源
- 后端常驻 ≈100 MB；云上最低 2核2G（需 swap），**建议 2核4G**。
- ⚠️ **实际线上 = Render `plan: free` = 500MB / 0.1CPU（用户 2026-09-23 确认）**，长期在边缘：
  监控图显示内存**锯齿状反复**（~10% 缓慢爬到 80~90% → **骤降 = 被杀重启**，48h 内多次），
  同期 CPU 长期 <5%（**不是算力，是内存**）。诊断出口 = `GET /api/system/memory`
  （已接入首页「数据新鲜度」卡片，自动调用 ⇒ diff 累积成"谁在涨"的时间序列；见 2026-09-23 daily）。
  **长期只能二选一：升计划（2GB）或把日报/复盘的重部分外迁 GitHub Actions。**
- ★★ **500MB 的实测账（2026-09-24 诊断端点首次线上读数）**：RSS 366.7MB（73.3%），
  其中**已知缓存 359.88MB、未归类仅 6.8MB** ⇒ **几乎全部内存都在模块级缓存里**
  （**没有框架层泄漏、也不是 RSS 高水位 ⇒ 修缓存 ＝ 修问题**）。两个大头：
  · `backtest/strategies._PRICES_CACHE` **234MB / 808 条**（**47%**，头号）—— **已修**（见下）
  · `mainforce/flow._FLOW_MAP_CACHE` **107MB / 1 条**（21%，整表资金流的 Python 表示）—— 待优化
  趋势 24 次跨重启 78.4 → 83.9% ⇒ **起点基线就 78%**（重启即高），缓爬到 84%+ 再 OOM ⇒ 锯齿。
- ★★ **同类缓存「写了两遍、只修一遍」是踩过的真坑（2026-09-24）**：`backtest/data.py` 与
  `backtest/strategies.py` 各有一份 `_PRICES_CACHE`，**只有 data.py 有**条数上限 200 / 过期即 pop /
  >900 根不缓存 / 满时淘汰最旧 1/4，strategies.py **三样全缺** ⇒ 线上 234MB（47%）。
  **⇒ 新增或修改缓存前，先 grep 同类实现、对齐成熟样板**（别再造残缺版）。
- ★★ **「TTL 只逻辑过期、从不物理删除」＝ 隐形泄漏（2026-09-24）**：命中判断写成
  `now - ts <= TTL` 时，过期只是当 miss 重新加载，**旧条目仍占着内存**（唯一释放时机退化成
  「回填后的 invalidate」，每天一次）⇒ 内存呈**每天从 0 涨到几百 MB 再清零的锯齿**。
  **缓存三件套：条数上限 + 单条上限 + 取用时物理删除过期项**（`_prices_cache_get/_put` 可照抄）。
- ★★ **修 500 别只修 traceback 指的那一行（2026-09-25）**：`market_emotion` 连崩两次——
  第一次修 `dates_all[-1]`（空列表 IndexError），第二个 500 又出现在**同一函数的另一处**
  （`yc > 0` 里 `yc` 为 `None`）。根因相同（**外部数据源缺数据**），位置不同。
  ⇒ **修 500 时把该函数所有"从外部取数"的字段统一过一遍类型/缺失**，否则就是挤牙膏。
  **判据**：`None > 0` / `'12.34' > 0` / `None / x` 都是 TypeError ⇒ 任何"取数后直接比较/相除"的
  地方都要先安全转换。
- ★★ **缺数据要返回 `None`、不要填 `0`（2026-09-25）**：`x or 0` 会把"**没有数据**"变成
  "**数值 0**" —— 于是涨跌幅 0%、除零、或把缺价当成"平价"。**脏数据伪装成有效值，比崩溃更难发现**
  （崩溃至少有人报错）。工具函数命名即契约：`_num_or_none()`（本文件 `routers/market.py`）、
  `eastmoney._to_float(v, default=0.0)`（该用 default 时才用）。
  **唯一例外**：确实想要"缺省 0"语义时（如求和累加）才显式写 `or 0`。
- ★★ **`dict.get(k, default)` 在「值为 None」时不会用 default（2026-09-25）**：它**只在 key 缺失时**
  生效 ⇒ `em.get("pct", "—")` 遇到 `{"pct": None}` 仍返回 **None** ⇒ f-string 里打出 **"None%"**。
  **正确写法**：`v if v is not None else "—"`（或 `v or "—"` **仅当 0 不合法**时）。
  ⚠️ **别用 `or` 图省事**：`x or "—"` 会把合法的 **0 / 0.0 / ""** 也替换掉 ⇒ 显示层与计算层都要
  显式区分「0」与「缺失」。**同一纪律的两个方向：计算时别把缺失填 0；显示时别把 0 当缺失。**
- ★★ **"在交易时刻" ≠ "是交易日"（2026-09-25 测试抓到的真 bug）**：`flash/rules.get_market_clock()`
  里各 `is_*_trading` **只看时刻、不看节假日** ⇒ 实测 2026-09-25（中秋休市）**13:02 时
  `is_a_stock_trading=True`** ⇒ 若拿它判断"市场是否开市"，**休市日的白天会误报开市**。
  ⇒ **凡"是否开市/能否交易"的判断，必须写成 `is_xxx_trading and is_trading_day`**。
  已加 `is_trading_day` 字段（纯新增，不改既有语义）。
  ★ 教训泛化：**时间条件与日历条件是两个正交维度**，只用一个必出边界错。
  ⚠️ 港股/日经/美股**无假期表** ⇒ 只能按交易时段近似（要在 UI/注释里写明"近似"，别当精确）。
- ★★ **"缺失"与"0"是两种状态，UI 徽标也必须诚实（2026-09-25）**：休市时 `market_emotion` 原返回
  `limit_up=0`，界面显示"涨停 0 家"，而盘前卡徽标却写「休市日·显示最近交易日数据」——
  **徽标承诺与实际不符**（实际是"没有行情"，不是"最近交易日快照"）。
  ⇒ 修法：后端增 `has_live`（行情缓存非空）+ `data_date`（价格截至哪天），无行情时"今日"字段
  返 **None**；前端显示「—」+ 徽标"无实时行情（价格截至 MM-DD）"。**别让 UI 替数据撒谎。**
- ★★ **"隔夜/时段"归属：外盘 24h 连续，同一涨跌幅在不同时段含义完全不同（2026-09-25）**：
  A 股收盘后新涨的 0.5%（需消化，重要）vs 收盘前就有的 0.5%（昨天已反映，不重要）。
  ⇒ 展示外盘必须**标出"此刻哪个市场在开市"**（用 `market_clock` 单一来源，前端不复制逻辑）。
  ⚠️ 但**别高估它的预测力**：`regime_external_lead_test.py` 实测纳指隔夜→A 股次日
  **IC 0.1609 在可交易口径（lag1）塌陷至 0.0115**（含时区前视）。
  ⇒ **价值在"解释"与"风控"，不在"预测"**；`coach/rules.py` 已落地"仅 defensive 市 10 日 +2.53pt"。
  ★ **算"隔夜变化"的基准要用「A 股收盘后（15:00 之后）的快照」，不能用 `change_pct`（相对昨结）**
    —— 后者含"A 股收盘前就已反映"的部分，两个含义不同的数字混在一起无法解读。
    `macro.get_overnight_change` 已实现（基准取自 `macro_history` 的 `HH:MM>=15:00` 最近一条）。
  ⚠️ **`macro_history` 的键名与面板不一致**（`nikkei`→**`nke`**、`us10y`→**`us10yt`**）
    ⇒ 跨"面板/历史"取数必须**显式映射**，别用同名假设（已用 `_OVERNIGHT_KEYS` 三元组）。
- ★★ **PowerShell 里 `git commit -m` 的引号规则（2026-09-25 连踩两次后的正确版）**：
  · 双引号 `"..."` ⇒ 会展开 `$`、反引号是转义符 ⇒ 危险，**改用单引号**。
  · ⚠️ **但单引号串里不能出现半角引号（`'`）** —— 一旦出现就**提前闭合**，后面的正文
    被当成额外 pathspec ⇒ 报 `error: pathspec 'xxx' did not match any file(s) known to git`。
  · ⚠️⚠️ **最坑的是它会伪装成功**：`git push` 仍输出 `Everything up-to-date`（因为根本没生成
    commit）⇒ **极易误判为"已提交"**。
  · **正确做法**：message 里**一个引号都不用**（连全角引号也避免），用「」和（）；
    或干脆 `git commit -F <msgfile>` 从文件读。
  · **必查**：提交后看 `[main xxxxx] 标题` 那一行 + `git log --oneline -1`，
    **不能只看 push 输出**。
- ★★ **「只在特定数据条件下爆炸」的 bug 靠走查最难发现（2026-09-25 实例）**：
  `trader_brief.collect_brief_data` 为 R5 追加 action 时误写成 `add(...)`，而本作用域只定义了
  `_add` ⇒ **只有"有持仓被标记主力出货"（risks 非空）时才抛 NameError**，而该函数被
  决策卡与盘前简报共用 ⇒ 两者一起失败。⇒ **同一作用域内的近名函数（`_add`/`add`）要警惕**；
  用**静态断言测试**锁住（`inspect.getsource` 里查 `_add("R5"` 存在、裸 `add("R5"` 不存在）。
- ★★ **`py_compile` 查不出 `NameError` ⇒ 后端改动必须做「真实 import」冒烟（2026-09-25）**：
  `macro.py` 新函数写 `-> Optional[dict]` 但该文件**从未 import typing** ⇒ **定义期抛 NameError
  ⇒ 整个模块加载失败**，而 `py_compile` 照样报 "compile OK"（它只查语法、不解析名字）。
  ⇒ **验证固定动作**：`python -c "import sys; sys.path.insert(0,'backend'); import app.<改动模块>"`
  （或让测试脚本真的 import 它）—— 本轮就是靠这个抓到的。
  ⚠️ 同理：**源码字符串断言要排除注释/docstring**（我断言 `"get_sentiment()" not in source`
  被自己写的解释性注释打假 ⇒ 9/10 假失败）。
- ★★ **用户说"把 A 改名成 B"时，先确认 A 和 B 是不是同一个概念（2026-09-25）**：
  用户要求把「负相关（弱）」改成「压制因素」，但前者是**油金相关性**状态、后者是**对 A 股的
  利空因素** —— **不是一回事**，直接改名会制造错误认知。
  ⇒ 追问一句"你要的是不是**另一个东西出现在同一个位置**"，往往能挖出真实需求
  （本例真需求 = 多空标签没标题 ⇒ 加「支撑因素/压制因素」两栏即可，语义不动）。
  ★ 泛化：**用户描述的是"体验痛点"，给出的却是"实现方案"** —— 痛点要满足，方案要复核。
- ★★ **后端改动的验证必须分三层，缺一层就会漏 bug（2026-09-25，一天内连踩两次）**：
  ① **`py_compile`** ⇒ 只查语法，**连 `NameError` 都发现不了**（`-> Optional[dict]` 没 import
     typing ⇒ 整个模块加载失败，它照样报 "compile OK"）。
  ② **真实 import 冒烟**（`python -c "import app.xxx"`）⇒ 能挡**定义期**错误（注解/装饰器），
     **挡不住函数体内**的 `NameError`（漏 `import time` ⇒ 只有调用时才炸）。
  ③ **真实调用 / 真实数据**（打桩测试 + 跑一次线上数据）⇒ 才能挡住函数体错误与**脏数据全集**
     （`main_industry_code` 里混着 `new_dlhy`、**`BK1386`**、`-` —— 打桩永远造不出来）。
  ⇒ **新增/改动函数后，固定动作：编译 → import → 真的调用一次（最好跑真实数据）。**
- ★★ **打桩测试的最大盲区是"真实数据的脏值分布"（2026-09-25）**：打桩只能验证**你能想到的**
  分支；真实库里同一列常有**多种来源混用**（本项目 `main_industry_code`：新浪 node `new_xxx`、
  东财板块码 `BK1386`、东财原始名 `油服工程`、空值 `-`）。
  ⇒ 展示类/聚合类改动，**先用一条只查长度或少量样本的探针脚本摸清真实分布**（egress 极小），
  再写过滤规则；过滤判据宁可**用正向判据**（如"必须含中文"）而不是**列举黑名单前缀**
  （后者每来一种新来源就要改一次）。
- ★★ **PG / SQLite 双库的四个坑（2026-09-25 一天内全踩到，`db` 层只转占位符、不转方言）**：
  ① **JSON 列**：`seats_json` 在 PG 是 `JSON` 类型 ⇒ psycopg2 读出来**已经是 list/dict**
     （**不能再 `json.loads`**），SQLite 则是 TEXT（需 loads）⇒ **必须兼容两种形态**；
     且 SQL 侧比较要 `seats_json::text NOT IN ('[]','null')` —— 直接和空串比会报
     **`invalid input syntax for type json`**。
  ② **`Decimal`**：PG 的 `SUM(numeric)` / `numeric` 列返回 **`Decimal`** ⇒ 直接塞进响应会
     被序列化成**字符串**（前端拿到 `"1211695537"` 而非数字）⇒ **显式 `float(v)`**。
  ③ **`NULLS LAST`** 是 **PG 专有**、SQLite 不支持 ⇒ 排序改用 `COALESCE(col, 0)`。
  ④ 占位符统一 `%s`（`db` 层自动转换），**别写 `?`**。
  ⇒ 写跨库 SQL / 读数值与 JSON 列时，**默认按"两边都要能跑"设计**，并在真机上各验一次。
- 生产 Python 3.9；禁用 PEP 604（`str | None`），用 `Optional` 或 `from __future__ import annotations`。
- FastAPI 路由：没有 `await` 的必须写 `def`，禁止假 async 阻塞事件循环。

## 战法推送白名单
- 动态计算（`app/strategies/recommendation.py`），从 `strategy_results` 重放撮合；`PUSH_STRATEGY_WHITELIST` 仅兜底。
- 低胜率 + 高盈亏比体系；判据看均收益/盈亏比，**不看胜率**。
- 可配：`WHITELIST_CRITERION`（默认 win_rate）、`WHITELIST_MIN_AVG_RET`（0.3%）、`WHITELIST_MIN_PROFIT_FACTOR`（1.2）、`WHITELIST_MIN_SAMPLES`（30）。
- 双轨 + 半衰期监控（2026-09-13）：全期 + 近 250 交易日任一轨跌破即暂停；后半段胜率 < 前半段 ×50% 告警。

## 环境传导链
- 四层合成：`app/mainforce/confluence.py` = 宏观 ±2 + regime ±2/±1 + 板块 ±1（拥挤主线只减不加）+ 个股 mainforce_state ±2，sum(-7~+7)。
- 2026-09-13 修复：补 `db_fetch`、行业名归一化；修复后板块层从长期 0 恢复真实值，会改变判级。

## LLM 接入（Provider 链，2026-09-17 事故后固化）
- 主力站：`LLM_BASE_URL=https://api.siliconflow.cn/v1` + `LLM_MODEL=deepseek-ai/DeepSeek-R1`（**推理模型**，思考常需分钟级）。
- 免费站 `LLM_FREE_*`（本机配 `deepseek-v4-flash`）+ `LLM_FREE_SHADOW=1` → **影子模式，免费站不进正式链** ⇒ 实际链 = `main:DeepSeek-R1` 单站。
- 超时：`LLM_TIMEOUT_FAST`（默认 300）/ `LLM_TIMEOUT_SLOW`（600）。**fast 短超时只对「链首是免费站」有意义**；链首是推理模型时 `call_llm` 自动退回 slow（显式设置 `LLM_TIMEOUT_FAST` 则强制生效）。唯一用 fast 档的是 `trader_brief.py` 盘前简报。
- **熔断是"一直失败"的来源**：单站连续失败 2 次 → 熔断 30min，期间**所有** LLM 调用（含 600s 档日报/周报）直接跳过。排障看 `/api/system/llm-usage` 的 `last_error` / `provider_chain` / `circuit_open`。
- 陷阱：`_providers()` 里 main 的 `"reasoning": False` 语义是「不注入 `reasoning_effort`」，**不是**「非推理模型」——不可拿它判断模型类型。

## 交易员教练 Coach
- 模块：`rules.yaml` · `rules.py` · `audit.py` · `monitor.py`。
- 红线：数字代码注入；30s 轮询只读 market 缓存；`coach_loop` 挂 `asyncio.create_task`；执行一致性分母=已决策。
- 持仓源：`paper_positions` + `user_portfolio`；真实持仓默认止损 = 成本 × 0.92。
- W2（2026-09-15）：前端教练 Tab + 执行回写 + LLM 翻译层 `explainer.py`（LLM 输出禁数字）+ 模拟盘剧本闭环（`coach_plans` 含 status/abandon_reason/closed_at/followed，平仓自动关 / 主动放弃接口 / 预承诺执行率）。
- API：`/api/coach/alerts|consistency|abandon-reasons|plans/execution-rate|plans|plans/{id}/abandon`。

## 项目约定
- **改动推送纪律（2026-09-23 立）**：① **批量改动攒一次推** —— 每次 main 推送都会触发
  `deploy-preview`（前端构建 + gh-pages 发布），而发布要抢 `gh-pages-publish` **全局锁**
  （与 kline-data / backend-pack 的**数据包发布**串行）⇒ 频繁小提交会无谓把数据包发布往后排
  （daily-batch 由 backend-pack 完成后接棒 ⇒ 连带推迟整条盘后链）。
  ⇒ 已给 `deploy-preview.yml` 加 `paths: ['frontend/**', …]`，纯后端/文档/记忆提交不再触发。
  ② **gh-pages 有三个写入者**（`deploy-preview` 前端 / `kline-data.deploy-pages` /
  `backend-pack.deploy-pages` 数据包），**必须保持**：同一 `concurrency.group: gh-pages-publish`
  + `cancel-in-progress: false` + 各自 `keep_files: true`（数据包另用 `destination_dir: data`）。
  这三条是 2026-09-08 两次覆盖事故后的加固 —— peaceiris 是「clone 分支→改文件→push」，
  并发布时**后 push 的会基于旧树把先 push 的文件覆盖回去**。改这三处配置前先读这段。
- 根目录只保留 `PLAN_<日期>.md`（现行）与 `PLAN_ARCHIVE_<日期>.md`；旧专题 plan 已归档。
- 改动后"先验证再提交"；未明确要求不自动 git commit。
- **新增看板/tab 必须自带一行定位**（是什么 / **不是什么** / 下一步去哪）—— 2026-09-23 用户
  连续两次追问「那个榜的意义」，根因是页面**从未解释自己**；好样板：`trade_gate.summarize`
  的「状态展示，非买入信号」声明、观察池的「候池非出票」注释、`portfolio_radar` 的
  「非买卖信号」定位。**榜单（Top50）的真实定位 = 候选池 + 变化监测，不是买点清单**
  （真实出手点是低频三绿；榜单有短期反转 + 阴跌市排序失效 + 高分档尾部风险三条实证边界）。
- **新增 tab 必须同时接 `startAutoRefresh` 分支**（`views/ScoreRank.vue`）—— 否则盘中它落到
  末尾的 `else loadData()`，**静默刷的是 Top50 榜单**、本 tab 纹丝不动。**此坑已犯两次**：
  观察池（2026-09-22）、我的持仓/今日雷达（2026-09-23）。
- **战法「三层禁、扫描不禁」结构**（改战法相关逻辑前必读）：扫描层 `for_scan=True` 防御市
  **放行**（攒样本，防 `strategy_results` 断档）；入场/推送层 `for_scan=False` 防御市
  **禁止**；买入闸门 `REGIME_POSITION[defensive]=0`（禁止）；模拟盘**不入池**。另有阴跌子档
  `STRATEGY_GRIND_GATE`（震荡市+MA 向下 ⇒ 全禁）。**推送白名单独立把关**（2026-09-23 实测为空
  ⇒ 战法信号根本不推企微）。

## 主力节奏跟随框架（2026-09-16，项目北极星/底层座基）
- **一句话**：跟对主力节奏，但永远慢一步——**识别状态、顺应方向、不猜时点**。
- **三层节奏模型（可靠度递减）**：①状态（吸筹/拉升/出货/洗盘，`mainforce_state`，有回测背书→可决策）②方向（flow5 倒U/北向/两融持续趋势，IC 正但单日噪声大→看连续趋势）③时点（哪天进撤，样本少→仅形态参考，**永不进决策链**）。
- **红线（区分跟节奏 vs 追涨杀跌）**：跟「已确认的节奏」（慢一步），不抢跑「未发生的节奏」；盘中观察→盘后确认→次日执行。
- **"主力"= 可观测代理指标，非机构意图**（大单轧差口径、解释非事实、东财/新浪两源不可互校验）。
- **现有模块已隐式执行**：评分资金面=方向、主力标签=状态、白名单闸门=状态闸门、教练=纪律、风控=逆势止损、组合分=错杀识别。不重构，只标注归位。
- 详见 `主力节奏跟随框架_20260916.md`（北极星/认知论）+ `主力节奏框架_开发计划_v1.0.md`（机制/验证设计，能直接开工）；事件应对是其具体实例（`PLAN_EVENT_PLAYBOOK_20260915.md`）。
- **★ 关键区分（开发地基）**：flow5 IC / accum/distribution 标签全是**横截面证据**（选股），而「跟节奏」座基是**时间序列命题**（市场级流入期该重仓）——横截面证据 ≠ 时间序列证据。显式拆成命题A（横截面，归选股）+ 命题B（时间序列，当前=4次FOMC+半年≈零证据，Phase 0 待验证核心）。
- **不对称现实·防守优先**：识别出货灵(-7.5pt)、识别吸筹不灵(+1.1pt)；流入是脉冲、流出是趋势 → 座基第一形态大概率是防守型（回避流出），进攻侧挂 Gate B 通过后。
- **事件应对日期口径铁律**：D0=A股反应日（美东决议日+1 的北京交易日）；曾因标签偏移把结论说反，务必写清 D-1/D0/D+1。
- **Phase 0 画像结论（127 日，Gate 判定书 2026-09-16 二审）**：Gate A NO-GO（episode 与随机切割不可区分，null 模拟 p=0.473）、Gate B 进攻侧无信号（B2 t=0.286）、防守侧=价格状态别名（控制后 -1.94%→-0.69% 不显著，provisional）、净产出两条半（反转纪律 R_{t-1}=-0.169 为主 + B3 涨日主力净卖为注脚；原「顶部选择器-236bp」控制后塌缩并入反转）。**时间序列半边未 KILL，等宽度重算器（三年价量代理）终判**。

## 工程方法论（评审沉淀，2026-09-16 记入制度）
- **「形式达标是 null 的预言」**：白噪声累计=随机游走，zigzag 切随机游走天然产生"n≥5、中位≥3、θ平滑"的表象。任何"节拍/周期"类检验必须先跑 null 模拟（bootstrap 重采样摧毁时序结构）对比，否则把随机性当结构。
- **代理/重算前必查原判据的数据依赖**：dist 有纯价量退化路径、accum 依赖资金流无退化路径——不查就重算会死于"不知道自己代理丢了什么"（代理的代理，失败不反证原标签）。
- **控制变量是生死项（1:20 打样）**：小样本先跑控制回归；表观效应被价格状态解释掉 2/3 是常态（-1.94%→-0.69%）。
- **稀有标签的一致性指标有基数率 bug**：原始一致率被「双方都无标签」格点主导，垃圾代理也能 ~81%。必须 sensitivity/precision/κ 条件化，且先算理论上限。
- **工程失败 ≠ 科学证伪**：仪器坏了（一致率不过）命题只是"暂不可检验"（可重开），只有仪器有效下主检验失败才是真 KILL。三终局必须分开写。
- **未控制的归因数字不可进结论**：对照组 -236bp 未控制 → 控制后 -1.08% 不显著。归因型结论必须过同款控制回归。
- **失败条款先于结果落盘 + 角色定义「不是翻案」**：预注册写死 GO/DOWNGRADE/KILL 与失败条款，防看完结果改规则（forking paths）。
- **关键路径的 `print` 别带 emoji（2026-09-23 实测）**：中文 Windows 控制台是 **GBK** ⇒
  `print("⚠️ …")` 抛 `UnicodeEncodeError`。危害不在 Windows 本身，而在**它把流程做了一半**：
  内存告警里"今日已告警"标记**先占**、随后 print 崩 ⇒ 异常中断 ⇒ **当天不再重试 = 静默丢告警**。
  三条规矩：① 日志用 ASCII 标记（`[WARN]`/`[OK]`；推送**内容**里的 emoji 无妨 —— 那是 HTTP UTF-8）；
  ② **告警/推送分支整体 `try` 包住**（哨兵模块绝不允许反噬主流程）；
  ③ **时间戳当主键时用微秒**（秒级下同秒多次写会互相覆盖，实测 5 次只剩 4 条）。
- **观测/诊断工具自身的三个坑（2026-09-23 内存诊断实测，写这类工具前必看）**：
  ① 探针**只读 `sys.modules`、不主动 import** —— 零副作用，且未加载的模块本来就不占内存；
  ② 深估算必须**采样**（`sys.getsizeof` 只算容器壳）且用 **`islice` 取样本、绝不 `list()` 复制**
     整个大容器 —— 在被观测方已逼近内存上限时，复制会**把进程压垮**（观测工具杀死被观测对象）；
  ③ **采样要取「前 N + 末尾 N」**：新增数据总是**追加在末尾**，只采头部会把"刚加进去的大条目"
     平均掉（实测漏报：209 条 K 线缓存追加 1 条 3000 根 K 线，头部采样仅 +43KB 看不见，
     修后正确报出 +8.6MB）；**双维度上报（MB 估算 + 精确条目数）**比单一 MB 更可靠。

## 本机环境 / 用户偏好
- ⚠️⚠️ **不要自行 `git push`（2026-09-25 用户明确指令）**：改完先汇报改动内容与验证结果，
  **等用户指令再推送**。比 2026-09-23 那条"批量攒一次推"更严 —— 那条是防 gh-pages 锁竞争，
  这条是**推送权归用户**。（自行 push 会直接触发前端自动部署上线，用户要保留这个决定权。）
- Windows 本机 Python DNS 对跨国域名间歇故障（路由器 DNS 抖动），重试即可；生产无影响。
- 日报不推企微，只在前端 `/report` 查看。
- **主使用习惯（2026-09-23 自述）：每天挂着 Top50 看实时行情** ⇒ 用户主链路 = **后端评分**
  （榜单默认后端），前端本地计算/kline-pack 对他是次要路径 ⇒ 评估"前端包故障影响"时，
  主场景不受影响；唯一接触点是详情页 K 线图（本地优先，本地有包就用本地的）。

## ★ 当前进行中（2026-09-17 进入数据积累期）
- **状态**：评分有效性专项整治一轮完成并已部署（前端 Pages + Render，2026-09-17 用户亲手提交部署）。**冻结一切权重/衰减的生产改动**，等两组数据攒够。
- **衰减口径（二版，2026-09-17 定稿）**：`backend/app/scoring/decay.py` 为单一事实源。主版本 **grad = 梯度降权**（成长/质量**权重乘数**：公告后 0-5 天 → 0 剔除、6-20 天 → 0.5、>20 天 → 1.0）——语义中性、尺度连续；保留 **zero（×0 归零）作对照**。初版 ×0 实测过激（000612 age19：73.7→33.4，掉榜且把刚公告股全压到 30-40 分档）。
- **在等什么**：① `shadow_rank_daily` 影子榜 **≥10 个快照日**（日批 task_shadow_rank 每晚 ~21:57 落库 **base/zero/grad 三套** top50）；② `market_regime_history` 的 **defensive 截面日积累**（scheduler mark_done 已修，每天正常落库）。
- **触发条件与到期动作**（快照日 ≥10，约 2026-10-08 前后）：跑 `scripts/compare_shadow_rank.py`（base/zero/grad 三套 T+5/T+10 收益对比）+ 重跑 `scripts/quality_defense_backtest.py --optimize --decay`（补 defensive 缺口）→ 综合决策：选哪套衰减（grad/zero/不衰减）进生产 + defensive 权重是否调整（候选 growth 0.30/quality 0.18）。
- **已部署改动**（详见 2026-09-16/17 daily）：`scoring.py`（并发漏 rank_mode 修复 + `/score/batch/shadow-rank` 实时衰减接口 + `_decay_total` helper）；`scheduler.py`（regime mark_done 日期推进修复）；前端「衰减对比」tab（实时两份 top50 + 主力标签 + 60s 刷新）；`generate_backend_pack.py` 补 `flow5_amt_yuan`；`daily_batch.py` + task_shadow_rank。**指标包侧的修正（chips 换手率 + flow5_amt_yuan）需 backend-pack 重跑后生效**（自动：周一~五北京 19:00）。
- **2026-09-18 验证清单**：① `market_regime_history` 有 9/17 行（scheduler 修复生效）；② `shadow_rank_daily` 有 9/17 base+decay（灰度首日）；③ `backtest_prices` 9/17 覆盖 ≥600 只（不限量回填生效）；④ 002452 详情页 chip ≈ 65%/55%/10% 且 5日净流入有值（指标包修正生效）。
- **可选并行（不依赖等待）**：宽度重算器（节奏框架 Phase 0 终判，三年价量代理）；B 类因子（质押比例/经营现金流，需新数据源）。
