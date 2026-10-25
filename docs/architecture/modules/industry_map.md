# industry_map.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】个股→行业映射的**唯一缓存入口**（egress 治理，2026-10-19）
问题（探针实测）：`SELECT code, main_industry FROM stock_industry`
（约 5.9 千行 ≈ 1MB/次）此前在 4 处各自整表读：
· `mainline.compute_mainline`（日批 + 工作台主线卡）
· `stock_moves.snapshot` 的行业聚合（日报/复盘）
· `earnings_pressure._industry_of`（日批）
· `sector_industry._industry_map`（板块成交额占比，**本来就有 1h 缓存**）
2026-10-18 13:11 → 10-19 21:28（32h）探针实测：**457 次调用 / 8.9 万行**
（≈11.6MB，按整行宽算的**上限**）——其中一半是重复的整表读。

口径：映射表由 `sector_

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- `backend/app/crowding_watch.py`
- `backend/app/earnings_pressure.py`
- `backend/app/flash_history.py`
- `backend/app/mainline.py`
- `backend/app/routers/scoring.py`
- `backend/app/sector_industry.py`
- `backend/app/sector_strength.py`
- `backend/app/stock_moves.py`
- `scripts/sector_linkage_edge_check.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`get_industry_map`, `invalidate`

## 已知坑

- 2026-10-19 起个股→行业映射唯一缓存入口（1h TTL）；mainline/stock_moves/earnings_pressure/sector_industry 已接，新增消费方禁止再各自整表读 5932 行。

规模：50 行。
