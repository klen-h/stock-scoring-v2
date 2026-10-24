# flash_history.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash.rules`
- `app.industry_map`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `FLASH_HISTORY_DAYS`、`FLASH_HISTORY_LIMIT`

## 顶层结构（def/class，前 25 个）

`_cutoff`, `_age_days`, `_industry`, `search`, `prompt_block`

## 已知坑

- 异动分析调 LLM 前检索 flash_news（保留期 120 天）五路打分 + 时间衰减，剔聚合稿、去重 Top6；命中以 [n] 进 prompt 且只许引用编号。

规模：215 行。
