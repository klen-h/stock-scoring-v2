# 长期记忆（stock-scoring-v2）

## 架构与运行形态（稳定事实）
- 单进程 FastAPI（`backend/app/main.py`）同时提供 `/api/*` 与前端静态页：`frontend/dist` 存在时由 `/{full_path:path}` 回退 `index.html`（Vue Router 接管）。生产用 `uvicorn app.main:app --host 0.0.0.0 --port 8000`（不要 `--reload`）。
- 数据库：默认 SQLite（`backend/data/app.db`）；设了 `DATABASE_URL` 走 PostgreSQL（项目实际用 Supabase 东京节点）。%s 占位符由 `app/database.py` 自动转换，兼容两种库。
- 调度器：`app/flash/scheduler.py` 的 `start()` 起 29 个 asyncio 常驻 loop，靠 `store.is_schedule_done/mark_schedule_done` 做当日幂等。**调度器随进程常驻，进程停则任务停**。
  - 盘后依赖链（2026-09-05 重排后）：K线刷新 15:30 → **指标刷新 16:40 由 GitHub Actions 跑**（`.github/workflows/indicator-refresh.yml`，入口 `scripts/refresh_indicators.py`，批量版 300 只秒级；本进程设 `ENABLE_HEAVY_JOBS=0` 即跳过该 loop）→ 评分快照 **18:00**（等指标到"今日"，最迟 18:45 强制）→ 主线 19:15 / 消息分快照 19:20 / 日报 19:30（三者都读当日 ranking_history）。
  - 旧快照 15:15 的 bug 已修：原先快照早于数据刷新，`ranking_history` 长期是「今日价+昨日技术特征」。
- K 线缓存：`app/scoring/kline_cache.py` `CACHE_POOL_SIZE=500`、`CACHE_KLINE_COUNT=500`、`MIN_SCORING_KLINE_COUNT=250`；`app/tencent.py` 另有内存 `KLINE_CACHE`（key 含 count，落盘 `backend/kline_cache.json`，加载时丢弃 >24h 条目）。
- 指标缓存 `indicator_cache` **只服务评分链路**：`/score/batch/top` 与 `/score/batch/bottom`（`_batch_with_precise_top` → `get_cached_technical_batch_sql` 一条 SQL 批量预加载）、Top5 的买入时机/趋势健康度（`_compute_top5_extras`）、以及 `score_snapshot_loop` 每日快照。**个股详情页 `score_single` 与战法扫描都不读它**（详情页走 `kline_cache` 现算 `_calc_technical`）。有效期 `MAX_INDICATOR_AGE_HOURS=36`；`kline_count` 落在 (0,250) 视为短拉取截断、指标不可信直接跳过 → 指标刷新必须排在 K 线刷新之后。
- 两层缓存的分工意义（不要合并）：`kline_cache` 存原始 OHLCV（详情页、筹码分布/主力行为叠加必须拿 raw bar），`indicator_cache` 存**预计算的近 80 天指标数组 `_series` + 增量状态 `_state`**，把评分从「实时拉腾讯 + numpy 全量重算 500 根」变成「一条 SQL 读 DB 直接喂 `engine.score_stock`」，一次性解决三个瓶颈：腾讯 WAF、CPU 配额、跨境 DB 的 N 次往返；额外收益是支持盘中 `incremental_update(code, price, high, low)` 做 O(1) 滚动更新。
- 缓存命中与否直接决定评分吞吐：`use_db_cache`（`kline_cache.get_cache_status()["total_cached"]>50` 或预加载覆盖过半）为真时并发 10 且不 sleep，否则并发 3 + 每只 `sleep(0.3)` 防 WAF —— 差一个数量级。

## 资源占用基线（2026-09-05 实测）
- 后端常驻 Working Set ≈ 100 MB（非交易时段、单进程）。盘后 15:15–16:30 批量窗口会明显升高。
- 部署推荐：云上最低 2核2G（需 swap），**建议 2核4G**；本机部署（DEPLOY.md 方式〇）零成本且性能更好。详见 2026-09-05.md。

## 两条数据分发链路（并存，勿混为一谈）
- **后端链路**：`kline_cache` / `indicator_cache` 两张 **Supabase 表** → 供后端 Python 评分（`batch/top`、`score_snapshot_loop`、日报、回测）。优势是可 `WHERE code IN (...)` 一条 SQL 取子集，服务端集中一致；成本是 Render→东京的跨境 RTT。
- **前端本地评分链路（独立模式）**：`.github/workflows/kline-data.yml` 每工作日 **UTC 08:00（北京 16:00）** 跑 `scripts/generate-kline-pack.py` → 产出 `kline-pack-YYYYMMDD.json.gz`（全A股 150 天，5–10MB）+ `kline-delta-*.json`（~500KB）+ `kline-pack-latest.json.gz` → 用 `peaceiris/actions-gh-pages` 发布到 **GitHub Pages**（需 secret `GH_PAGES_TOKEN`）→ 前端 `KLINE_DATA_BASE_URL = https://klen-h.github.io/stock-scoring-v2/data` 下载存 **IndexedDB**，由 `useFrontendScoring.js` 在 Web Worker 里本地算排名，**零后端请求**。入口在 ScoreRank.vue 的「下载K线数据」按钮（可选模式，非默认）。
- 两者**不是替代关系**：pack 只喂浏览器（浏览器查不了 DB），DB 只喂后端 Python（日报/快照/回测需要服务端落库，浏览器算的结果回不来）。可改进点：给前端 pack 增加预计算 `indicators` 字段（后端用 500 天算的更准），省掉浏览器端从 150 天 K 线算 MACD/KDJ。

## 已知问题 / 待修（与评分数据时效性相关）
- **盘中技术面是"昨收"的，不随行情变化**：排行榜 `batch/top` 命中 `indicator_cache` → 技术面、资金面（`_score_amount` 用 `tech_data[-10:]`）均基于上一交易日收盘；只有价格/涨跌幅/PE/PB/成交额/换手率来自实时快照。成长/质量是季度财报，本就静态。
- **详情页与排行榜盘中会给出不同分**：`score_single` 盘中 `_trading_now=True` 时**跳过缓存直接实时拉 500 根 K 线**（含当日半根 bar），而 `batch/top` 走缓存 → 同一只股票盘中两处分数不一致（既有行为，非 bug 引入）。
- **`incremental_update`（盘中 O(1) 滚动更新指标）目前是死代码**：只有 `POST /api/score/indicator-cache/incremental` 接口 + 前端 `api/index.js` 的 `incrementalIndicatorUpdate` 定义，**后端无调度任务、前端无任何页面调用**。想让盘中技术面动起来必须自己接线。
- **⚠️ 每日权威快照早于数据刷新**：`score_snapshot_loop` 窗口 15:15，而 K 线刷新 15:30、指标刷新 16:00 → 每天写进 `ranking_history` 的"盘后权威快照"实际是**前一交易日收盘的指标 + 当日实时价**的混合体；而回测/日报/拥挤度因子全读 `ranking_history`。修法：把 `SCORE_SNAPSHOT_WINDOW` 挪到指标刷新之后（≥16:30），或加"等待刷新完成"的前置校验。

## 项目约定
- **生产 = Python 3.9**（backend/Dockerfile python:3.9-slim；本地开发是 3.12）。新代码**禁用 PEP 604 注解**（`x: str | None`），要么 `Optional[str]`，要么文件头加 `from __future__ import annotations`。ci.yml 的 `import app.main`（3.9）能拦住这类问题——本地 3.12 跑通不代表 3.9 可用。

## 用户偏好
- 日报不推送企微，只在前端 `/report` 页查看（避免刷屏）。
- 改动后倾向于"先验证再提交"；未明确要求时不要自动 git commit。
