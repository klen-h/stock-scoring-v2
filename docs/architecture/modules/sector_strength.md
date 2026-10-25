# sector_strength.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】行业强度（强势板块判定）—— 评分排行榜「板块联动筛选」的数据层
判据**已冻结**：`PLAN_SECTOR_LINKAGE.md` v1.0（2026-10-19）。
改参数 = 让预登记失效 ⇒ 必须在那份文档里新开一节并说明"不是事后调参"。

核心口径（与冻结文档逐条对应）：
· 行业维度 = `stock_industry.main_industry`（新浪 49 类，**唯一可与个股精确关联**的
taxonomy；zzshare 104 板块与它只有 4 个同名 ⇒ 刻意不桥接）；
· 行业指数 = 行业内成分股**日收益等权均值**（当日无价/停牌剔出分母；缺失≠0）；
· 强度 = 最近 **5 个交易日**复利累计 `ret5`；
· 强势 = `ret5 > 0` 且横截面分位 ≥ 70%（Top 30%），且成分股 ≥ 5 只；
· 普涨守卫 = 连续 3 日「池内上涨家数占比 ≥ 70%」⇒ 当日

## 输入 / 输出

- 改参数 = 让预登记失效 ⇒ 必须在那份文档里新开一节并说明"不是事后调参"。

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`
- `app.industry_map`
- `app.pattern_launch`
- `app.scoring.live_ranking`

## 被谁依赖

- `backend/app/env_check.py`
- `backend/app/routers/scoring.py`
- `scripts/daily_batch.py`
- `scripts/sector_linkage_edge_check.py`

## 关键阈值 / 环境变量

- `SECTOR_LINKAGE_MODE`

## 顶层结构（def/class，前 25 个）

`mode`, `enabled`, `ensure_table`, `_industry_of`, `_code6`, `_pct`, `_cum`, `compute`, `_read_rows`, `load`, `_code_of`, `filter_ranked`, `ensure_ranking_table`, `save_ranking`, `snapshot_from_live`

## 已知坑

- 板块联动筛选数据层（判据冻结 PLAN_SECTOR_LINKAGE v1.0）：stock_industry 49 行业 + backtest_prices 等权自建行业指数（不桥接 zzshare 104 板块——与个股行业仅 4 个同名）；任何缺失 fail-open 放行 + 显式 reason。

规模：457 行。
