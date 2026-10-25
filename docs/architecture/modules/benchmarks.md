# benchmarks.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】**基准收益的唯一事实源**（2026-09-29，P0 双口径系列的**口径收敛**）
【为什么需要（本模块的由来）】
2026-09-29 做「P0 基准双口径」时，同一个公式（**首末收盘算收益**）被写在 **4 处**：
· `daily_report._bench5`            —— 日报「空仓对照」近 5 交易日
· `weekly_review._idx_ret`          —— 周复盘「本周绩效」块
· `routers.performance._bench_pair` —— 性能页三轨道
· `coach.attribution._bench_ret`    —— 教练建议事后归因
这是**当天新造的债**，且违反项目「单一事实源」纪律（前车之鉴：本地 68.8 vs
后端 72.6 的评分漂移）⇒ 收进本模块，四处共用；**改本文件 = 全项目同步改口径**。

【口

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`index_ret`, `recent_ret`, `dual_bench`, `recent_dual`, `ret_in_window`, `pick_bench`

## 已知坑

- 基准池（可取价池等权口径）。

规模：144 行。
