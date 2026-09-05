# 长期记忆（stock-scoring-v2）

## 架构与运行形态（稳定事实）
- 单进程 FastAPI（`backend/app/main.py`）同时提供 `/api/*` 与前端静态页：`frontend/dist` 存在时由 `/{full_path:path}` 回退 `index.html`（Vue Router 接管）。生产用 `uvicorn app.main:app --host 0.0.0.0 --port 8000`（不要 `--reload`）。
- 数据库：默认 SQLite（`backend/data/app.db`）；设了 `DATABASE_URL` 走 PostgreSQL（项目实际用 Supabase 东京节点）。%s 占位符由 `app/database.py` 自动转换，兼容两种库。
- 调度器：`app/flash/scheduler.py` 的 `start()` 起 29 个 asyncio 常驻 loop（评分快照/K线缓存/战法扫描/指标刷新/回测回填/日报/矛盾扫描/快讯等），靠 `store.is_schedule_done/mark_schedule_done` 做当日幂等。**调度器随进程常驻，进程停则任务停**。
- K 线缓存：`app/scoring/kline_cache.py` `CACHE_POOL_SIZE=500`、`CACHE_KLINE_COUNT=500`、`MIN_SCORING_KLINE_COUNT=250`；`app/tencent.py` 另有内存 `KLINE_CACHE`（key 含 count，落盘 `backend/kline_cache.json`，加载时丢弃 >24h 条目）。

## 资源占用基线（2026-09-05 实测）
- 后端常驻 Working Set ≈ 100 MB（非交易时段、单进程）。盘后 15:15–16:30 批量窗口会明显升高。
- 部署推荐：云上最低 2核2G（需 swap），**建议 2核4G**；本机部署（DEPLOY.md 方式〇）零成本且性能更好。详见 2026-09-05.md。

## 用户偏好
- 日报不推送企微，只在前端 `/report` 页查看（避免刷屏）。
- 改动后倾向于"先验证再提交"；未明确要求时不要自动 git commit。
