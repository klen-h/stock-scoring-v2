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
- 已知待修：盘中技术面是昨收；`score_single` 实时算与 `batch/top` 缓存算盘中不同分；`incremental_update` 是死代码；`score_snapshot_loop` 15:15 早于数据刷新。

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

## 日期口径（全项目规范 —— 2026-09-19 第 3 次踩坑后固化）
- **凡「数据所属日期」一律用「最近的已完成交易日」，绝不用北京自然日。**
  自然日只在日批正常时点（20:43 盘后）碰巧等于交易日；**凌晨 / 周末 / 节假日 / 盘前**
  都会错位（表现为"信号日期是周六""连续上榜跨周末清零""包日期永远对不上"）。
- **可复用实现**（都跳过周末 + `flash/rules.HOLIDAYS`，节假日表含 2026 全年）：
  · `pack_source._latest_available_pack_day()` —— 包日期，**22:00** 分界
  · `scheduler._latest_trading_day()` —— **15:00** 分界
  · `signal_persistence._scan_day()` —— **15:00** 分界（2026-09-19 新增）
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
- 根目录只保留 `PLAN_<日期>.md`（现行）与 `PLAN_ARCHIVE_<日期>.md`；旧专题 plan 已归档。
- 改动后"先验证再提交"；未明确要求不自动 git commit。

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

## 本机环境 / 用户偏好
- Windows 本机 Python DNS 对跨国域名间歇故障（路由器 DNS 抖动），重试即可；生产无影响。
- 日报不推企微，只在前端 `/report` 查看。

## ★ 当前进行中（2026-09-17 进入数据积累期）
- **状态**：评分有效性专项整治一轮完成并已部署（前端 Pages + Render，2026-09-17 用户亲手提交部署）。**冻结一切权重/衰减的生产改动**，等两组数据攒够。
- **衰减口径（二版，2026-09-17 定稿）**：`backend/app/scoring/decay.py` 为单一事实源。主版本 **grad = 梯度降权**（成长/质量**权重乘数**：公告后 0-5 天 → 0 剔除、6-20 天 → 0.5、>20 天 → 1.0）——语义中性、尺度连续；保留 **zero（×0 归零）作对照**。初版 ×0 实测过激（000612 age19：73.7→33.4，掉榜且把刚公告股全压到 30-40 分档）。
- **在等什么**：① `shadow_rank_daily` 影子榜 **≥10 个快照日**（日批 task_shadow_rank 每晚 ~21:57 落库 **base/zero/grad 三套** top50）；② `market_regime_history` 的 **defensive 截面日积累**（scheduler mark_done 已修，每天正常落库）。
- **触发条件与到期动作**（快照日 ≥10，约 2026-10-08 前后）：跑 `scripts/compare_shadow_rank.py`（base/zero/grad 三套 T+5/T+10 收益对比）+ 重跑 `scripts/quality_defense_backtest.py --optimize --decay`（补 defensive 缺口）→ 综合决策：选哪套衰减（grad/zero/不衰减）进生产 + defensive 权重是否调整（候选 growth 0.30/quality 0.18）。
- **已部署改动**（详见 2026-09-16/17 daily）：`scoring.py`（并发漏 rank_mode 修复 + `/score/batch/shadow-rank` 实时衰减接口 + `_decay_total` helper）；`scheduler.py`（regime mark_done 日期推进修复）；前端「衰减对比」tab（实时两份 top50 + 主力标签 + 60s 刷新）；`generate_backend_pack.py` 补 `flow5_amt_yuan`；`daily_batch.py` + task_shadow_rank。**指标包侧的修正（chips 换手率 + flow5_amt_yuan）需 backend-pack 重跑后生效**（自动：周一~五北京 19:00）。
- **2026-09-18 验证清单**：① `market_regime_history` 有 9/17 行（scheduler 修复生效）；② `shadow_rank_daily` 有 9/17 base+decay（灰度首日）；③ `backtest_prices` 9/17 覆盖 ≥600 只（不限量回填生效）；④ 002452 详情页 chip ≈ 65%/55%/10% 且 5日净流入有值（指标包修正生效）。
- **可选并行（不依赖等待）**：宽度重算器（节奏框架 Phase 0 终判，三年价量代理）；B 类因子（质押比例/经营现金流，需新数据源）。
