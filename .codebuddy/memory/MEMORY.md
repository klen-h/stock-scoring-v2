# 长期记忆（stock-scoring-v2）

> ## 🔁 换账号 / 新会话 / 换机器：如何接上上下文
> **本项目的"上下文"= 本目录下的 Markdown 文件，不是聊天记录**，与 CodeBuddy 账号无关、只跟项目目录走；
> `.codebuddy/memory/*.md` 已 git 跟踪 ⇒ `git clone` 后也在。
> **新会话第一句照抄**：`先读 .codebuddy/memory/MEMORY.md 与最近 2 天 daily，读完复述进展与未完成项再继续。`
> **阅读顺序**：① 本 `MEMORY.md`（跨会话长期事实/纪律，必读）→ ② `memory/YYYY-MM-DD.md` 最近 1~2 天 → ③ 更早按日期往前翻。
> ⚠️ 不要依赖聊天记录（会话历史绑账号）；换账号/机器前先 `git commit` 推送未提交改动；会话级 Memory 工具不当作唯一载体。

---

## 架构与运行形态
- 单进程 FastAPI（`backend/app/main.py`）提供 `/api/*` + 前端静态页；生产 `uvicorn app.main:app --host 0.0.0.0 --port 8000`，**不要 `--reload`**。
- 前端生产 = GitHub Pages 自动（`.github/workflows/deploy-preview.yml`，pnpm 构建推 gh-pages，主入口 `https://klen-h.github.io/stock-scoring-v2`）；Render 后端仅兜底 `frontend/dist`。
- 数据库默认 SQLite，设 `DATABASE_URL` 走 PostgreSQL（Supabase 东京）；`app/database.py` 自动转 `%s` 占位符兼容两者。
- 调度器 `app/flash/scheduler.py` 起 ~30 个 asyncio loop，按日幂等。LLM/轻量推送用 `asyncio.create_task`；重活走 `*_heavy`（`RENDER_READ_ONLY=1` 全关）。
- 盘后链路：K线 15:30 → 指标 16:40 → 快照 18:00 → 主线/消息分/日报 19:15+；决策简报按 `(date, phase)` 落 `trader_briefs`。

## 数据与缓存
- **轮询接口必须"裁列 + 截断正文"**：前端 60s/120s 轮询的列表接口是最大 egress 大户（页面开着就持续消耗）。加 `trunc` 参数用 `SUBSTR(col,1,%s)`（⚠️ SQLite 无 `LEFT()`）；列表显式列并排除大字段。判据：**凡是"前端定时轮询 + 返回表行"都要问"列表真的需要整行吗"**。
- **本机持久缓存统一模式**（flow/l3/market-emotion-closes/ind-dist-src 4 处）：进程内 → 本机 gzip/json → 回源；指纹用单行查询；⚠️ 指纹取不到 ⇒ 不走缓存直接回源；空结果不写盘。
- 竞价判读/落库（2026-09-25）：`market_emotion_daily` 三组互不覆盖字段（主字段/close_*/auction_*）；竞价数据只在那 10 分钟存在，不落库永久丢；窗口外一律不写；`_emotion_daily_row` 必须组装整行（SQLite 是 INSERT OR REPLACE 缺列清空）。
- `kline_cache`/`indicator_cache` 服务详情与评分链路；`indicator_cache` 有效期 36h；`kline_count`∈(0,250) 视为截断跳过。
- 两条包发布链独立（易混）：`kline-data.yml`(18:00)→前端包+realtime-quotes；`backend-pack.yml`(19:00)→`backend-pack.db.gz`（日批唯一依赖）。前端包失败≠日批失败；前端包超时真瓶颈是 K 线阶段（1566 只单只请求），非 240 批行情。

## ⚠️ 路由顺序与「真跑验证」纪律
- FastAPI 按注册顺序匹配：`routers/scoring.py` 的 `@router.get("/{symbol}")`（单段通配）在 L918 ⇒ 新增单段静态路由必须注册在它**之前**，否则被吞成股票代码、返 `200 {"error":"未找到股票 xxx"}`（非 404）⇒ 前端 catch 抓不到 ⇒ 静默空。
- **验证纪律三条**：① 只 import 函数调用 ≠ 验证 HTTP 路由（凡前后端配合必须走真实入口起后端+HTTP/浏览器）；② `pnpm build` 通过 ≠ 功能可用（不查运行时契约）；③ 前端"本地优先"路径要单独验（`tryLoadLocalAll` 成功就不调后端）。

## 告警规则口径
- 同一现象问两遍口径：『跌多少』（绝对涨跌幅）vs『从日内高点回撤多少』（路径）是两条规则；双条件缺一不可；高波动标的门槛按倍数加严。
- 别为一条新规则多拉外部请求（一次拉取喂多条规则）。
- 新信号/告警上生产前先做预测力检验：①会不会过吵 ②响了有没有用（对照组收益差）。模板 `scripts/reversal_edge_check.py`（判定标准预登记）。

## 项目已有能力清单（提需求前先查这里）
- 盘中警示 `app/flash/intraday_alerts.py`；矛盾扫描 `app/contradictions/`；LLM 提示词素材 `app/flash/llm.py`；推送唯一单点入口 `flash.wechat.push_markdown_batched`（改推送行为只改这里，`signal_bus.py` 在此埋记录器⇒前端今日雷达）。
- 持仓告警已有两处（新增前必看防重复）：`coach/monitor.py` + `strategies/exit_alert.py`。持仓聚合视图 = `portfolio_radar.py` + `/api/score/batch/portfolio-radar`。
- 运维出口五件套：① 数据新鲜度 `/api/system/status` ② 文件数据 `/api/system/runtime-files` ③ 进程内存 `/api/system/memory`（52 项缓存探针）④ 内存看护 `memory_watch.py`（跨 OOM 重启有趋势）⑤ 库体积 `/api/system/db-usage`（Supabase 500MB 超限=项目只读，比 OOM 致命）。
- 库体积治理 `db_retention.py`：只清理"已核实只读最新"的表；保留期反直觉（先涨到上限再平，天数宁短勿长）；`KEEP_MIN_DAYS=3` 安全阀。

## Supabase egress 治理（详见根目录 EGRESS.md）
- egress 是账号级（本地+Render 共用 5GB/月）；头号放大器 = 进程内缓存 × 低频数据 + `--reload` 重启整份重读。
- 实测长期 ~273MB/天（非某天突增）；"已治理"清单必须定期用探针复验，且先看 `stats_reset`（否则 14 天累计当一天，差 14 倍）。
- 排障标准动作：`pg_stat_statements` 按 `rows` 排序 + 必查 `stats_reset` + 取完整 SQL + 只看 `rows`（表级 seq_tup_read≠egress）。
- 短 TTL 陷阱：TTL 长短必须匹配数据更新频率。

## 回测数据质量
- 体检工具 `scripts/audit_backtest_data.py`（遵守 egress 纪律）。源数据异常日已标记到 `price_anomalies`（集中 2024 年，源里就存在、不可修复）；与当前战法回测零重叠。
- 已知待修：盘中技术面是昨收；`score_single` 实时 vs `batch/top` 缓存盘中不同分；`incremental_update` 死代码；`score_snapshot_loop` 15:15 早于数据刷新；`gate-watch` 首次全池计算超 20s 超时建议改读日批快照。

## 战法回测：三个必须记住的事实（2026-09-26 第 1 步实测）
- **样本量要看「独立信号日」，不是笔数**：一天几十只股票高度相关 ⇒ 实测 `n=487` 实际只有 **9 天**、
  `n=131` 只有 **2 天**。⇒ 格子激活判据应为 **`≥20 个独立信号日`**（笔数仅辅助）。
  ★ 这也解释了半衰期告警为何剧烈：所谓"前半段/后半段"各只有几天，胜率差反映的是
  **几天市场环境**，不是战法衰减属性。
- **`backtest_prices` 池 = 「战法选股 ∪ 近 30 天上榜股」+ ETF（810 只，**全为主板**）**
  （`backend/backfill_history.py:_collect_strategy_codes`）：结果导向 ⇒ 继承选择偏差
  （零创业板/零科创板、微盘少、绝对家数被稀释约 1/4），且**自我固化**（没被选中 ⇒ 无数据 ⇒
  永不可被回测检验）。**池内一切回测结论都受此约束**（含宽度序列、格子表）。
- **regime 引擎在 V 型反转处系统性滞后**：2024-09-24（政策底暴涨）、2024-09-30（涨停 262）
  均被判 **defensive**（MA20<MA60 未修复）⇒ 派生"季节"视图在拐点初期必错
  ⇒ **政策脉冲（924 式）依然会踏空**。改法：反转确认需叠加宽度/情绪先行条件，不能只等均线。

## LLM 配置
- 主力站 `deepseek-ai/DeepSeek-R1`（推理模型，分钟级思考）；免费站影子模式（`LLM_FREE_SHADOW=1` = 排除出正式链，名字反直觉易设错）。
- `render.yaml` 只是"环境变量清单文档"非事实来源（服务控制台手工建）；`LLM_FREE_API_KEY` 多环境共用撞车 429。
- 熔断：单站连败 2 次→熔断 30min 期间所有 LLM 调用跳过；排障看 `/api/system/llm-usage`。

## 因子体检周期化
- `scripts/subfactor_ic_backtest.py` 每月首交易日随日批跑；幂等判据必须读库（不可用文件判据，Actions 每次全新 checkout）。
- 企微推送分级：有告警 force=True，无告警尊重 `WECHAT_BUSINESS_ALERTS`（默认关）；企微不支持 markdown 表格（自动转列表）。

## 日期口径（全项目规范，多次踩坑后固化）
- **凡"数据所属日期"一律用 `rules.latest_completed_trading_day()`**（15:00 分界 + 跳周末/`HOLIDAYS`），绝不用北京自然日。
- **两把尺子必须都对照**：15:00 分界（数据所属交易日=业务口径）vs 22:00 分界（包可用下限=工程口径）；15:00~22:00 窗口二者分开，判早会回退查 Supabase ≈600MB/天。
- ⚠️ `routers/scoring.py` 里"库里的榜是否今天"判据**继续用 `_today_bj()`（自然日）不要换交易日口径**（那是新鲜度判据，盘中须与上一交易日榜不匹配才退化走实时）。
- 日批时间线（北京）：kline-data 18:00 → backend-pack 19:00 触发（~20:43）→ daily-batch `workflow_run` 立即接棒（~74min）。兜底日拖到 ~00:57。
- 批处理任务日期必须由调用方传入（用 `_batch_trading_day()`），不能用 `now()`（跨午夜会变次日 ⇒ 判断被跳过或写错日期）。
- "连续 N 天"统计按交易日步进。

## ⚠️ 跨市场因子口径
- 外部分子（纳指/VIX/美元）按"同日"对齐 = 时区前视偏差（大部分波动在 A 股收盘后）⇒ 评估一律用 lag1 / "A 股开盘前已知"口径。
- 跨市场因子价值常是非线性、条件性 ⇒ 必须分组检验，"全样本 IC≈0"与"条件子集有价值"可并存。
- 算"隔夜变化"基准用 A 股收盘后快照，不能用 `change_pct`（相对昨结，含收盘前已反映部分）。

## 部署与资源
- 实际线上 Render `plan: free` = 500MB/0.1CPU，长期边缘（内存锯齿：爬到 80~90% → 被杀重启）。500MB 实测几乎全在模块级缓存（`backtest/strategies._PRICES_CACHE` 曾 234MB/47%）。
- 缓存三件套：条数上限 + 单条上限 + 取用时物理删除过期项（TTL 只逻辑过期=隐形泄漏）。
- 生产 Python 3.9，禁 PEP 604；FastAPI 无 await 的路由写 `def`。

## 战法推送白名单
- 动态计算（`strategies/recommendation.py`），判据 `expectancy`（均收益>0.3% 且 PF≥1.2），**不看胜率**。
- 双轨「全期 AND 近轨(250 交易日)」；**全期轨累积不可逆**（一旦全期不达标即使转暖也永久出局）。
- ⚠️ 2026-09-26 实测：6 战法全负期望 ⇒ 白名单空 ⇒ 推送静默（**不是门槛问题，是战法负期望**，放宽=推负期望信号）。白名单只管买入推送，不管出场。
- `PUSH_STRATEGY_WHITELIST` 仅兜底。

## ★ 战法分状态回测结论（2026-09-27，全市场 10 年 38.8 万成交，详见 `_report_分状态回测结论_20260927.md`）
- **整体负 alpha**（超额 -0.27%），四态全负：进攻 -0.21% / 震荡 -0.29% / **阴跌 -0.03%**（最不差）/ 防御 -0.39%。
- **唯一稳健正 alpha = `ma_pullback`（均线回踩）× 阴跌市**（+1.12%/PF1.55/超额+1.21%，2665 笔/213 独立日）。
- 次优：`dragon_turnaround`×进攻（+0.42%/超额+0.18%，闸门后转正）、`limit_up_boomerang`×进攻（+1.42%/超额+0.77%）。
- **「阴跌禁 A 族」方向反了**：阴跌市恰恰是均线回踩主场。
- ⚠️ `limit_up_boomerang` 防御 alpha 被闸门削弱（无闸门 +0.80%→有闸门 +0.11%）：它的收益部分来自拉升段追高，无闸门会高估。
- 砍掉/降权：single_yang、ma_convergence（占 90% 信号、全线负 alpha）。
- ★ 数据基础设施已就绪：`data/zzshare_daily.db`（21 年全市场 1672 万行）+ `scripts/rescan_strategies.py`（重扫）+
  `scripts/backtest_rescan_by_regime.py`（撮合+四态+benchmark+部分闸门）+ `scripts/build_nb_history.py`（nb 序列）。

## 环境传导链
- 四层合成 `mainforce/confluence.py`：宏观±2 + regime±2/±1 + 板块±1 + 个股±2，sum(-7~+7)。

## 交易员教练 Coach
- 模块 `rules.yaml`/`rules.py`/`audit.py`/`monitor.py`；持仓源 `paper_positions`+`user_portfolio`；真实持仓默认止损 = 成本×0.92。
- API：`/api/coach/alerts|consistency|abandon-reasons|plans/execution-rate|plans|plans/{id}/abandon`。

## 项目约定
- **推送纪律（2026-09-25 用户明确）**：**不要自行 git push**，改完先汇报改动+验证结果，等用户指令再推（推送权归用户）。比 09-23"批量攒一次推"更严。
- gh-pages 有三个写入者（deploy-preview/kline-data/backend-pack），必须保持同一 `concurrency.group` + `cancel-in-progress:false` + `keep_files:true`（数据包 `destination_dir:data`）。已给 deploy-preview 加 `paths:['frontend/**']`，纯后端/文档提交不触发前端部署。
- 改动后"先验证再提交"；未明确要求不自动 commit。
- 新增看板/tab 必须自带一行定位（是什么/不是什么/下一步）；榜单真实定位 = 候选池+变化监测，非买点清单。
- 新增 tab 必须接 `startAutoRefresh` 分支（否则盘中静默刷 Top50）。战法"三层禁、扫描不禁"结构：扫描层防御市放行攒样本，入场/推送层禁止。

## 主力节奏跟随框架（项目北极星）
- **一句话**：跟对主力节奏，但永远慢一步——识别状态、顺应方向、不猜时点。
- 三层节奏模型（可靠度递减）：①状态（吸筹/拉升/出货/洗盘，有回测背书→可决策）②方向（flow5 倒U/北向/两融，看连续趋势）③时点（仅形态参考，永不进决策链）。
- 红线：跟"已确认的节奏"不抢跑"未发生的节奏"；盘中观察→盘后确认→次日执行。
- ★ 关键区分：flow5 IC/accum-distribution 标签是**横截面证据（选股）**，而"跟节奏"是**时间序列命题（择时）**——横截面≠时间序列。
- Phase 0 画像结论（127 日）：Gate A NO-GO、Gate B 进攻侧无信号、防守侧=价格状态别名（provisional）；时间序列半边未 KILL，等宽度重算器终判。

## 工程方法论（评审沉淀，挑最重要的）
- 「形式达标是 null 的预言」：任何"节拍/周期"检验先跑 null 模拟（bootstrap 摧毁时序结构）对比，否则把随机性当结构。
- 控制变量是生死项：表观效应被价格状态解释掉 2/3 是常态。未控制的归因数字不进结论。
- 失败条款先于结果落盘（预注册写死 GO/DOWNGRADE/KILL），防看完结果改规则（forking paths）。工程失败≠科学证伪。
- 稀有标签一致率有基数率 bug（双方都无标签格点主导）⇒ 必须 sensitivity/precision/κ 条件化。

## ★ 踩坑纪律（按主题分组，每条一行精华）
**验证：** py_compile 查不出 NameError ⇒ 必须真实 import 冒烟 + 真调用一次（最好真实数据）；打桩≠真实调用（互补，真实库脏值分布打桩造不出）；"测试全绿"≠"验到了"（空数据让 0==0 断言绿灯，先校验样本非空）；真实调用要开独立进程（reload 不还原桩）；打桩勿复制被测代码；FAIL 先查自己算式与测试数据。
**数据/字段：** 缺数据返回 None 不填 0（脏数据伪装有效值比崩溃难发现）；`dict.get(k,default)` 对 None 不生效；不确定字段不要依赖宁可反推；跨系统数据按字段名取不按位置；同一指标页面只能一个口径；一个字段不回答两个相反问题；表列 vs 代码常量先 grep 谁在用；面板加品种同步所有消费点；真实数据可能恰好为空。
**日期/时间：** 时间条件 vs 日历条件正交（开市判断 = is_trading and is_trading_day）；日期键两派（运行日/交易日）混用会让"按日期判齐没齐"失效 ⇒ 判任务完成看 `created_at`；相对日期要声明锚点；某天没数据先问是不是交易日；历史异常≠现在还有 bug（问"现在代码还会产生吗"）；缓存写入时刻≠数据时刻（展示新鲜度要看数据自带的 ts）。
**SQL/双库：** PG/SQLite 四个坑（JSON 列已是对象别再 loads / Decimal 显式 float / NULLS LAST 是 PG 专有 / 占位符统一 %s）；SQLite `INSERT OR REPLACE` 整行替换（别依赖部分更新，先读合并再写全字段）；加列用 `ALTER TABLE ADD COLUMN`（CREATE IF NOT EXISTS 不管已存在表）。
**PowerShell/git：** `git commit -m` 用单引号且内容不含任何引号（半角/全角单双都不行，否则 pathspec 报错且 push 伪装成功）；push 进度走 stderr 会渲染成假警报。
**并发/编辑：** 同文件多 edit 必须串行 + 逐个 grep 验证（报告成功≠已生效，Vue 不校验事件绑定）；改枚举同步硬编码数量；删函数 grep 全库引用（含注释）；前端未 import 却调用 = 静默失效（vite build 不查未定义标识符，try/catch 吞掉 ReferenceError）。
**决策/语义：** 缺证据≠反证（A✗ 无证据应中性，B✗ 拥挤才是反证）；用户说改名先确认是不是同一概念（体验痛点 vs 实现方案）；完成时刻不确定→事件驱动>轮询；进度中间态≠稳态（定阈值等终态）；放宽条件要同步查副作用（会不会重复发生）；展示层与计算层都要显式区分 0 与缺失；图表固定量程可失真但读数不可。

## 本机环境 / 用户偏好
- Windows 本机 Python DNS 对跨国域名间歇故障，重试即可；控制台 GBK，print 别带 emoji/⇒（用 ASCII 标记）。
- 日报不推企微，只在前端 `/report` 查看。
- 主使用习惯：每天挂着 Top50 看实时行情 ⇒ 主链路 = 后端评分，前端本地计算是次要路径。
- 图表偏好：榜单/分布优先纯 CSS 条（不把 echarts 引进 Workbench）。
- 测试脚本必须在 import app.database 前设 `DATABASE_URL=sqlite:///...`（否则跑线上 Supabase）。

## ★ 当前进行中（2026-09-27）
- **战法分状态回测已完整闭环**（全市场 10 年 38.8 万成交），结论见上「战法分状态回测结论」+ 正式报告
  `_report_分状态回测结论_20260927.md`。**下一步 = 白名单格子化落地**：把「阴跌市启用 ma_pullback、
  进攻市启用 dragon_turnaround / limit_up_boomerang」做成 战法×状态 准入规则，模拟盘验证后上生产。
- 评分权重/衰减仍冻结等数据攒够（shadow_rank ≥10 快照日 + defensive 截面日），约 2026-10-08 前后触发 compare_shadow_rank.py 决策。
- 待用户配置（一次性）：`BATCH_CALLBACK_TOKEN`（Render env + GitHub secret 各配同值），未配则日批完成通知不发（跳过非报错）。
- 评分权重/衰减仍冻结等数据攒够（shadow_rank ≥10 快照日 + defensive 截面日），约 2026-10-08 前后触发 compare_shadow_rank.py 决策。
- 待用户配置（一次性）：`BATCH_CALLBACK_TOKEN`（Render env + GitHub secret 各配同值），未配则日批完成通知不发（跳过非报错）。
