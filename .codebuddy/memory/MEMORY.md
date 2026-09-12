# 长期记忆（stock-scoring-v2）

## 架构与运行形态（稳定事实）
- 单进程 FastAPI（`backend/app/main.py`）同时提供 `/api/*` 与前端静态页：`frontend/dist` 存在时由 `/{full_path:path}` 回退 `index.html`（Vue Router 接管）。生产用 `uvicorn app.main:app --host 0.0.0.0 --port 8000`（不要 `--reload`）。
- 数据库：默认 SQLite（`backend/data/app.db`）；设了 `DATABASE_URL` 走 PostgreSQL（项目实际用 Supabase 东京节点）。%s 占位符由 `app/database.py` 自动转换，兼容两种库。
- 调度器：`app/flash/scheduler.py` 的 `start()` 起约 30 个 asyncio 常驻 loop，靠 `store.is_schedule_done/mark_schedule_done` 做当日幂等。**调度器随进程常驻，进程停则任务停**。
  - `start()` 里分两组：**"LLM 叙事类"直接用 `asyncio.create_task`（只读模式保留）**，重活类走 `*_heavy(...)`（`RENDER_READ_ONLY=1` 下全关）。新增轻量 LLM/推送类 loop 应放进前者。
- 决策简报（`trader_briefs` 表，`PLAN_TRADER_WORKFLOW` Phase 1，2026-09-12 完成双轨）：**盘后**由日批 `trader_brief` 任务生成并推企微（Actions）；**盘前**由 Render 的 `trader_brief_premarket_loop`（09:10-11:30 窗口、当日幂等）生成并推企微。两者按 `(date, phase)` 分别落库、互不覆盖；前端 `GET /api/system/trader-brief` 按需读取。
  - ★ 2026-09-13：**盘前简报固定含「## 资金与情绪」段**（两融 5 日净变化 + 情绪温度计，复用 `flash/margin_sentiment`；净减 ≤`MARGIN_DRAIN_ALERT_YI`（默认 -200 亿）时追加"杠杆撤离→拉高出货"警示）。磨底市最关键的变盘领先指标。
  - 盘后依赖链（2026-09-05 重排后）：K线刷新 15:30 → **指标刷新 16:40 由 GitHub Actions 跑**（`.github/workflows/indicator-refresh.yml`，入口 `scripts/refresh_indicators.py`，批量版 300 只秒级；本进程设 `ENABLE_HEAVY_JOBS=0` 即跳过该 loop）→ 评分快照 **18:00**（等指标到"今日"，最迟 18:45 强制）→ 主线 19:15 / 消息分快照 19:20 / 日报 19:30（三者都读当日 ranking_history）。
  - 旧快照 15:15 的 bug 已修：原先快照早于数据刷新，`ranking_history` 长期是「今日价+昨日技术特征」。
- K 线缓存：`app/scoring/kline_cache.py` `CACHE_POOL_SIZE=500`、`CACHE_KLINE_COUNT=500`、`MIN_SCORING_KLINE_COUNT=250`；`app/tencent.py` 另有内存 `KLINE_CACHE`（key 含 count，落盘 `backend/kline_cache.json`，加载时丢弃 >24h 条目）。
- 指标缓存 `indicator_cache` **只服务评分链路**：`/score/batch/top` 与 `/score/batch/bottom`（`_batch_with_precise_top` → `get_cached_technical_batch_sql` 一条 SQL 批量预加载）、Top5 的买入时机/趋势健康度（`_compute_top5_extras`）、以及 `score_snapshot_loop` 每日快照。**个股详情页 `score_single` 与战法扫描都不读它**（详情页走 `kline_cache` 现算 `_calc_technical`）。有效期 `MAX_INDICATOR_AGE_HOURS=36`；`kline_count` 落在 (0,250) 视为短拉取截断、指标不可信直接跳过 → 指标刷新必须排在 K 线刷新之后。
- 两层缓存的分工意义（不要合并）：`kline_cache` 存原始 OHLCV（详情页、筹码分布/主力行为叠加必须拿 raw bar），`indicator_cache` 存**预计算的近 80 天指标数组 `_series` + 增量状态 `_state`**，把评分从「实时拉腾讯 + numpy 全量重算 500 根」变成「一条 SQL 读 DB 直接喂 `engine.score_stock`」，一次性解决三个瓶颈：腾讯 WAF、CPU 配额、跨境 DB 的 N 次往返；额外收益是支持盘中 `incremental_update(code, price, high, low)` 做 O(1) 滚动更新。
- 缓存命中与否直接决定评分吞吐：`use_db_cache`（`kline_cache.get_cache_status()["total_cached"]>50` 或预加载覆盖过半）为真时并发 10 且不 sleep，否则并发 3 + 每只 `sleep(0.3)` 防 WAF —— 差一个数量级。

## egress 治理机制（2026-09-12 起，稳定事实）
- **版本门控 `app/sync_meta.py`**：给"每天只变一次"的远端数据（如 `mainflow_history`）打版本号 —— 生产方写完 `touch(key)`，消费方缓存数据 + 版本号，TTL 到期先查版本（十几字节）：没变就续期（0 流量）、变了立刻重拉、表不可用则退化为长 TTL。比单纯 TTL 更省也更准，**跨进程可见**（渲染常驻进程 vs Actions 日批是两个进程）。
- **研究脚本本机缓存 `app/research_cache.py`**：`backend/data/research-cache.db`，`ohlc_all()` / `flow_map()` 默认 24h 复用；K 线**优先从数据包取**（零 egress），包未覆盖的再分块查库。研究脚本（`mainforce_factor_backtest` / `strategy_mainforce_filter_test` / `factor_analysis`）都走它。
- **进程内读缓存**（`flash/store.py` 的 market_snapshot/macro_history、`signals/tracker.py` 的 tracking、`mainforce/flow.py` 的浮筹）：统一模式 = **写入即失效 + TTL 短于数据变化周期**，返回深拷贝防调用方原地改写污染缓存。
- 大结果集一律**显式列**（不再 `SELECT *`），`backtest_prices` 已去掉 id/code/name。

## 战法推送白名单（2026-09-12 复核后）
- 白名单是**动态算的**（`app/strategies/recommendation.py`）：从 `strategy_results` 重放撮合（T+1 开盘成交、涨停一字剔除、主力闸门 + 退出 v2），6h 进程缓存；`PUSH_STRATEGY_WHITELIST` 只是**计算异常时的兜底**。
- **该体系是"低胜率 + 高盈亏比"型**（自家闸门验证：胜率 48.1→50.3%、均收益 +0.07→+0.44%、盈亏比 1.05→1.36）→ 用「胜率 ≥55%」当门槛会算出空集、推送静默。**看战法好不好一律看均收益/盈亏比，不看胜率**。
- 判据可配：`WHITELIST_CRITERION=win_rate|expectancy`（默认 win_rate）、`WHITELIST_MIN_AVG_RET`（默认 0.3%，= A 股双边成本+滑点粗估，回放均收益是毛值）、`WHITELIST_MIN_PROFIT_FACTOR`（默认 1.2）、`WHITELIST_MIN_SAMPLES=30`。
- 复核工具：`scripts/strategy_whitelist_review.py`（零行情回源，K 线走本机包）。
- 实测基线（2026-08-20~09-11）：龙回头 136/48.5%/+0.46%/PF1.39 · 单阳不破 281/51.6%/+0.19%/PF1.13 · 均线粘合 198/39.9%/-0.32% · 均线回踩 27/37%/-0.99%。
- **双轨重放 + 半衰期监控（2026-09-13 起）**：判据同时跑「全期 + 近 `WHITELIST_ROLLING_DAYS`（默认 250）交易日」两轨，**任一轨跌破即暂停推送**（近轨样本 <30 视为不可评估→保守不通过）；另有**半衰期监控**（后半段胜率 < 前半段 ×`WHITELIST_HALF_LIFE_RATIO`=50% 时告警）。开关 `WHITELIST_ROLLING=0` 回退单轨。近轨日期 = trades 的 signal_date 去重末 N 个（不依赖交易日历）；交易日不足 N 时降级为全期单轨。实测已捕捉 `ma_convergence_breakout`（17.0% vs 49.5%）、`ma_pullback`（17.6% vs 50.0%）的后半段衰减。

## 环境传导链（`app/mainforce/confluence.py`，2026-09-13 修复后）
- 四层合成：宏观 direction.score(±2) + 市场 regime(offensive+2/neutral+1/nb-1/defensive-2) + 板块(资金流±1、在主线额外+1) + 个股(mainforce_state accum+2/distribution-2)，sum(-7~+7) → 顺流共振/偏多/中性拉锯/偏逆/逆流共振。快照 30min 进程缓存 `_env_cache`。
- **⚠️ 2026-09-13 前该函数有两个致命 bug（已修）**：① 模块顶部**从未 `import db`** → `_env_snapshot` 里 `db.fetch(...)` 每次抛 `NameError` 被 `except Exception: pass` 吞掉 → **板块资金流表与主线名单长期恒为空**（板块层从未生效，flow 永远"未知"、in_mainline 永远 False；传导链实际只有三层）。修法：新增 `db_fetch()` 延迟导入 helper。② `sector_daily.name` 是**东财细分名**而 `stock_industry.main_industry` 是**新浪一级名** → key 对不上、资金层恒"未知"。修法：`_normalize_industry` 归一化并按一级行业**累加**净流入。**修复属行为变更**：板块层从"长期 0"恢复真实值，会改变推送环境链判级。
- **拥挤主线（只减不加）**：`industry_mainline.crowded`（`mainline._crowding_flags`：行业内候选股命中 ret20>30%/ret60>50%/距250日高点<5% 的占比 ≥50% 判拥挤）→ 拥挤主线**不再给板块层 +1**（资金流出仍照常 -1）。回测背书：拥挤组 T+5 均 -1.411% vs 不拥挤 -0.292%（-7.6pp/-1.12pt）。

## 资源占用基线（2026-09-05 实测）
- 后端常驻 Working Set ≈ 100 MB（非交易时段、单进程）。盘后 15:15–16:30 批量窗口会明显升高。
- 部署推荐：云上最低 2核2G（需 swap），**建议 2核4G**；本机部署（DEPLOY.md 方式〇）零成本且性能更好。详见 2026-09-05.md。

## 两条数据分发链路（并存，勿混为一谈）
- **后端链路**：`kline_cache` / `indicator_cache` 两张 **Supabase 表** → 供后端 Python 评分（`batch/top`、`score_snapshot_loop`、日报、回测）。优势是可 `WHERE code IN (...)` 一条 SQL 取子集，服务端集中一致；成本是 Render→东京的跨境 RTT。
- **前端本地评分链路（独立模式）**：`.github/workflows/kline-data.yml` 每工作日 **UTC 08:00（北京 16:00）** 跑 `scripts/generate-kline-pack.py` → 产出 `kline-pack-YYYYMMDD.json.gz`（全A股 150 天，5–10MB）+ `kline-delta-*.json`（~500KB）+ `kline-pack-latest.json.gz` → 用 `peaceiris/actions-gh-pages` 发布到 **GitHub Pages**（需 secret `GH_PAGES_TOKEN`）→ 前端 `KLINE_DATA_BASE_URL = https://klen-h.github.io/stock-scoring-v2/data` 下载存 **IndexedDB**，由 `useFrontendScoring.js` 在 Web Worker 里本地算排名，**零后端请求**。入口在 ScoreRank.vue 的「下载K线数据」按钮（可选模式，非默认）。
- 两者**不是替代关系**：pack 只喂浏览器（浏览器查不了 DB），DB 只喂后端 Python（日报/快照/回测需要服务端落库，浏览器算的结果回不来）。可改进点：给前端 pack 增加预计算 `indicators` 字段（后端用 500 天算的更准），省掉浏览器端从 150 天 K 线算 MACD/KDJ。
- ⚠️ **JSON 版 backend-pack 是内存炸弹**（45MB JSON → Python dict 常驻 150-200MB，512MB 实例 OOM 实测）→ **已废弃，改为 SQLite 版 `backend-pack.db.gz`**（2026-09-06）：常驻 ≈0，按需查单只 <5ms。Render 可安全启用 `DATA_SOURCE=pack`。
- **三个包的归属（易混淆）**：`backend-pack.db.gz` 只给 Python 后端（Render 自动下载/本地 sync_local.py），**浏览器永远不读它**；浏览器只读 `kline-pack`（K线图表/本地评分，已有 delta 增量）和 `indicators-pack.json.gz`（前端指标，待接线）。页面无法直接观察 backend-pack 是否在用——去 Render/本地日志搜 `[pack_source]`，前提是设了 `DATA_SOURCE=pack/local`。

## 已知问题 / 待修（与评分数据时效性相关）
- **盘中技术面是"昨收"的，不随行情变化**：排行榜 `batch/top` 命中 `indicator_cache` → 技术面、资金面（`_score_amount` 用 `tech_data[-10:]`）均基于上一交易日收盘；只有价格/涨跌幅/PE/PB/成交额/换手率来自实时快照。成长/质量是季度财报，本就静态。
- **详情页与排行榜盘中会给出不同分**：`score_single` 盘中 `_trading_now=True` 时**跳过缓存直接实时拉 500 根 K 线**（含当日半根 bar），而 `batch/top` 走缓存 → 同一只股票盘中两处分数不一致（既有行为，非 bug 引入）。
- **`incremental_update`（盘中 O(1) 滚动更新指标）目前是死代码**：只有 `POST /api/score/indicator-cache/incremental` 接口 + 前端 `api/index.js` 的 `incrementalIndicatorUpdate` 定义，**后端无调度任务、前端无任何页面调用**。想让盘中技术面动起来必须自己接线。
- **⚠️ 每日权威快照早于数据刷新**：`score_snapshot_loop` 窗口 15:15，而 K 线刷新 15:30、指标刷新 16:00 → 每天写进 `ranking_history` 的"盘后权威快照"实际是**前一交易日收盘的指标 + 当日实时价**的混合体；而回测/日报/拥挤度因子全读 `ranking_history`。修法：把 `SCORE_SNAPSHOT_WINDOW` 挪到指标刷新之后（≥16:30），或加"等待刷新完成"的前置校验。

## 项目约定
- **计划文档约定（2026-09-12 起）**：仓库根目录只保留两份 —— `PLAN_<日期>.md`（**唯一现行主计划**：遗留项 + 下一阶段 + 观察清单 + 风险回滚）与 `PLAN_ARCHIVE_<日期>.md`（历史 plan 原文归档：目录/处置表 + 逐字原文）。此前散落的 11 份专题 plan（`PLAN_MAINFORCE.md` / `PLAN_NEXT_PHASE.md` / `PLAN_PACK_MIGRATION.md` / `先知雷达_功能Plan_v1.0.md` …）已全部收拢进归档并从根目录删除。**代码注释里引用的旧 plan 文件名，内容在归档对应章节查**。新增计划沿用同一模式，不要再往根目录加散文件。
- **生产 = Python 3.9**（backend/Dockerfile python:3.9-slim；本地开发是 3.12）。新代码**禁用 PEP 604 注解**（`x: str | None`），要么 `Optional[str]`，要么文件头加 `from __future__ import annotations`。ci.yml 的 `import app.main`（3.9）能拦住这类问题——本地 3.12 跑通不代表 3.9 可用。
- **FastAPI 路由禁用"假 async"（2026-09-05 血泪教训）**：函数体没有 `await` 的路由必须写普通 `def`（FastAPI 自动放线程池），写成 `async def` 会让同步的腾讯 HTTP/DB/numpy 重算直接阻塞事件循环，一个慢请求卡死整个进程 → Render 网关 502（无 CORS 头）→ 前端误报 CORS blocked。2026-09-05 已把 stock.py/market.py 全部及 scoring.py 大部分路由改为 `def`；保留 async 的只有真用 asyncio 的（`_batch_with_precise_top`、score_top/bottom/by_signal、`backtest` 的嵌套 gather）。新路由默认写 `def`。

## 本机环境（Windows 开发机）
- **Python 进程 DNS 间歇性故障**（2026-09-12 发现）：getaddrinfo 对 supabase pooler 等跨国域名间歇失败（报 "could not translate host name"），但 nslookup/Resolve-DnsName 正常、Dnscache 正常、无代理——根因指向路由器（192.168.0.1）DNS 转发抖动 + Windows 负缓存放大；已建议改网卡 DNS 为 223.5.5.5/119.29.29.29。**只影响本机开发/研究脚本，不影响生产**（Render 新加坡→pooler 同区域内网）。遇到时先重试一次即可自愈；长时间跑不通就别硬刚，改天再跑。
- 执行命令被 `genie-ps-*.ps1`（Temp 下）PowerShell 包装器包裹——本机有管理进程的工具，stderr 会出现包装器报错噪音，忽略即可。

## 用户偏好
- 日报不推送企微，只在前端 `/report` 页查看（避免刷屏）。
- 改动后倾向于"先验证再提交"；未明确要求时不要自动 git commit。
