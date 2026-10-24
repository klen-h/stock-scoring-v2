# key_level_watch.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.contradictions.scanner`
- `app.database`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `KEY_LEVELS_EXTRA`

## 顶层结构（def/class，前 25 个）

`_extra_levels`, `ensure_table`, `_num`, `_analyze`, `compute`, `load`

## 已知坑

- 指数关键位观察器：前低/整数位/年内低点首次失守 + 距支撑；支持 KEY_LEVELS_EXTRA 自定义；399006/399986 腾讯常空 → 东财 push2his 兜底。

规模：221 行。
