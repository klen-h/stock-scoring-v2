# workbench_scan.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

【文件作用】工作台盘中「变化检测器」——先证明信息量，再决定要不要上 LLM

背景（用户 2026-10-21）：
想在盘中每 30 分钟让 LLM 分析一次工作台。但工作台大半数据是**日频**的
（mainforce / 评分快照 / 教练卡 / 行业映射），半小时一报会有大量复读；
且 LLM 有额度与熔断成本。⇒ 先上**纯规则、零 LLM** 的变化检测器跑几天，
用数据回答："8 个采样点里，真正值得说一句的有几个？"

设计（与用户讨论结论一致）：
① **零外部增量**：宽度/板块/个股全部读 `tencent._cache` 内存行情；
只有 3 个核心指数走 1 次腾讯请求（8 次/交易日，可忽略）。
② **零 LLM**：纯规则打分；`score >= WORTH_SCORE` 记为 worth。
③ **只采盘中变量**：指数、宽度（涨跌家数/涨停跌停）、板块强弱、量能、快讯；
日频项一律

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`
- `app.flash.store`
- `app.routers.market`
- `app.sector_industry`
- `app.tencent`

## 被谁依赖

- `backend/app/flash/scheduler.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_ensure_table`, `_num`, `_collect_breadth`, `_collect_sectors`, `_collect_news`, `collect`, `_hm`, `_trading_minutes`, `_diff`, `_judge`, `_load_prev`, `run_slot`, `_due_slot`, `preview`, `day_detail`, `summary`

## 已知坑

- 工作台盘中变化检测器（零 LLM）：交易时段 8 档采样规则打分；worth_rate 分母剔除 stale/baseline。

规模：622 行。
