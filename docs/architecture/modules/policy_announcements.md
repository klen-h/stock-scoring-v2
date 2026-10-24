# policy_announcements.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`_clean`, `_bj_date`, `_query`, `_classify`, `_classify_flow`, `scan`, `ensure_table`, `save`, `load`, `density`, `scan_and_store`, `summarize`

## 已知坑

- 巨潮公告粗召回 + 本地连续字串过滤（searchkey 是分词全文匹配不能当判据）；只提示不打勾；失败返回 ok=0 不抛。

规模：396 行。
