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

## 交易员教练 Coach
- 模块：`rules.yaml` · `rules.py` · `audit.py` · `monitor.py`。
- 红线：数字代码注入；30s 轮询只读 market 缓存；`coach_loop` 挂 `asyncio.create_task`；执行一致性分母=已决策。
- 持仓源：`paper_positions` + `user_portfolio`；真实持仓默认止损 = 成本 × 0.92。
- W2（2026-09-15）：前端教练 Tab + 执行回写 + LLM 翻译层 `explainer.py`（LLM 输出禁数字）+ 模拟盘剧本闭环（`coach_plans` 含 status/abandon_reason/closed_at/followed，平仓自动关 / 主动放弃接口 / 预承诺执行率）。
- API：`/api/coach/alerts|consistency|abandon-reasons|plans/execution-rate|plans|plans/{id}/abandon`。

## 项目约定
- 根目录只保留 `PLAN_<日期>.md`（现行）与 `PLAN_ARCHIVE_<日期>.md`；旧专题 plan 已归档。
- 改动后"先验证再提交"；未明确要求不自动 git commit。

## 本机环境 / 用户偏好
- Windows 本机 Python DNS 对跨国域名间歇故障（路由器 DNS 抖动），重试即可；生产无影响。
- 日报不推企微，只在前端 `/report` 查看。
