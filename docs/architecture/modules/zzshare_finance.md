# zzshare_finance.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】zzshare 财报扩展因子存储（质量维度的现金流/扣非/商誉等扩展数据源）

设计：
- 独立表 stock_finance_zz（每 code 一行最新快照，含 indicator/balance/cash_flow
三表 JSON），不修改现有 stock_finance/engine，杜绝影响线上评分。
- 表内保留 pub_date（zzshare statDate/pubDate），供"无前视"因子分析过滤。
- 合并 key：code 主键。code 统一转内部 6 位。

用法：
from app.zzshare_finance import sync_latest_finance
sync_latest_finance(codes, chunk=50, verbose=True)

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.zzshare_client`

## 被谁依赖

- `backend/app/flash/scheduler.py`
- `scripts/daily_batch.py`
- `scripts/zz_finance_sync.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`ensure_table`, `_now`, `_rows_from_df`, `_fetch_table`, `sync_latest_finance`, `get_extra`, `stats`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：156 行。
