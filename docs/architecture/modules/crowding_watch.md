# crowding_watch.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.industry_map`

## 被谁依赖

- `backend/app/ai_basket_watch.py`

## 关键阈值 / 环境变量

- （未直接引用环境变量）

## 顶层结构（def/class，前 25 个）

`ensure_table`, `_num`, `_close`, `_date`, `_pack`, `_PackView`, `_industry_of`, `compute`, `load`

## 已知坑

- 拥挤赛道观察器：行业 30 日收益中位 + 高位股占比 + 回撤；退潮等级=比行业中位回撤差 10pp 且近 5 日仍跌；纯观察器。

规模：273 行。
