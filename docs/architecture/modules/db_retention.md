# db_retention.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】数据库保留期清理（Supabase 500MB 上限治理）—— 2026-09-24 新增
背景：Supabase 免费档 **500MB/项目**，用户 2026-09-24 实测已用 **231MB（46%）**，
增速 ≈ **2.3MB/交易日**（≈48MB/月）⇒ 约 **4~6 个月**撞线。★ 而**超限后果比 Render
OOM 更严重**：项目进入**只读**（写入全失败）⇒ 日批全挂，且**不会自动恢复**。

真实分表（实测）显示结构是「**大的不涨、涨的不大**」：
· **存量层**（占 75%）：`backtest_prices` 100MB / `mainflow_history` 22MB /
`kline_cache` 18MB / `stock_finance_zz` 15MB / `indicator_cache` 7.7MB
—— 但**日增合计仅 ~0.5MB** ⇒ 它们不威胁撞线，却

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `RETENTION_DAYS`

## 顶层结构（def/class，前 25 个）

`_cutoff`, `_cleanup_one`, `cleanup`, `summarize`

## 已知坑

- Supabase 500MB 治理任务；egress 是大头，接口避免全量大查询。

规模：123 行。
