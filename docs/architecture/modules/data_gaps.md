# data_gaps.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】数据底座断档自检 + 板块快照自愈回填（2026-09-30，P0）
背景（为什么需要它）：
2026-09-30 用户问「前几天的地产、今天的金属拉升，项目似乎察觉不到？」
⇒ 排查发现"察觉不到"的**数据层**两个真问题（另有口径层，见《诊断》记忆）：
① `plate_daily_zz`（zzshare 板块快照 —— **前端「板块分化」的数据源**）**停在 09-24**，
而它原先只挂在 Render 的 `sector_snapshot_loop`（15:10-16:40）上 ⇒ 循环停摆
即**静默断档 5 个交易日**；日批的 `task_sector_snapshot` 只写了东财版 ⇒ 无人发现。
（同 `mainforce_state` 09-09 / `weekly_report` 09-11 / `zz_finance` 09-19 的老毛病：

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash`
- `app.flash.rules`
- `app.flash.wechat`
- `app.sector_zz`

## 被谁依赖

- `scripts/backfill_plate_daily.py`
- `scripts/daily_batch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`trading_days`, `lag_trading_days`, `plate_missing`, `backfill_plate`, `check_gaps`, `format_markdown`, `_ensure_log`, `run_gap_check`

## 已知坑

- （见文件头 docstring 的「为什么/事故」段；或暂无已知坑记录）

规模：266 行。
