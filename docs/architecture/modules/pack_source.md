# pack_source.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】数据包（Pack）数据源：肥数据的"非 Supabase"读取层（SQLite 版）

背景：Supabase 免费版 egress 5GB/周期被读爆（10.6GB，2026-09-05），
大头是 backtest_prices(71MB)/kline_cache(19MB)/indicator_cache(9MB) 被反复读。
这三类数据本质是"腾讯行情的本地副本"，由 GitHub Actions 每日拉取打包成
SQLite 发 GitHub Pages（零流量费），本模块负责下载与按需查询。

为什么是 SQLite 而不是内存 JSON：45MB JSON 解析成 dict 常驻 150-200MB
（Render 512MB 直接 OOM，2026-09-05 实测）；SQLite 落磁盘按需查单只（<5ms），
常驻内存 ≈0。

三档数据源（环境变量 DATA_SOURCE）：
db     默认。行为与历史版本完全一致（读 S

## 输入 / 输出

- 【文件作用】数据包（Pack）数据源：肥数据的"非 Supabase"读取层（SQLite 版）

## 依赖谁（本项目内 import）

- `app.flash`
- `app.flash.wechat`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `backend/run.py`
- `scripts/daily_batch.py`
- `scripts/dev_local.py`

## 关键阈值 / 环境变量

- `DATA_SOURCE`、`PACK_URL`

## 顶层结构（def/class，前 25 个）

`enabled`, `source_name`, `pack_file`, `_warn_once`, `_alert_pack_issue`, `_db_fresh`, `_download_and_unpack`, `_ensure_ready`, `_query`, `_pack_date`, `redownload`, `_pack_date_raw`, `_parse_pack_date`, `_pack_outdated`, `_maybe_refresh`, `_latest_available_pack_day`, `_is_stale`, `get_klines`, `get_prices`, `get_indicators`, `get_name_cap`, `get_codes`, `status`

## 已知坑

- 读侧=「命中包→用包；未命中→回退 DB」⇒ 包覆盖口径必须 ⊇ 所有消费方池（2026-10-10 修过：质量过滤只应筛新增候选不能筛既有消费方）；DATA_SOURCE=local 前先 sync_local.py --force。

规模：440 行。
