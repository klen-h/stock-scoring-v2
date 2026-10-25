# macro_daily.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】外盘日频收盘历史表 `macro_daily_history` —— 补 `macro_history` 的短板

背景（2026-10-10 黄金/商品联动研究，见 .codebuddy/memory/2026-10-10.md §13）：
`macro_history` 从 2026-08 起每天记 09:10（盘前）+ 15:03（盘后）两条快照，到当时只有
~32 个收盘点 ⇒ 做不了"6 个月/多年"的领先滞后与相关性。本模块落一张**日频收盘表**：
· 历史回填：`scripts/backfill_macro_daily.py`（akshare，一次性、幂等）
· 逐日增量：`flash/store.append_macro_history` 在 15:03 盘后快照时顺手 upsert
（复用既有快照，零新增网络/运行时依赖）
表：macro_daily_history(code, date, clo

## 输入 / 输出

- 表：macro_daily_history(code, date, clo

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`ensure_table`, `upsert`, `bulk_upsert`, `load`, `upsert_from_panel`, `stats`

## 已知坑

- 外盘日频历史（已回填 5.5 年）；⚠️ date 列混 "2026/9/7" 格式——必须解析 datetime 排序，禁止 SQL 文本排序（曾致外部领先预警被无声关闭两周）。

规模：145 行。
