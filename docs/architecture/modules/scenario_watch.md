# scenario_watch.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app`
- `app.database`
- `app.flash.rules`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `SC_AMT_HIGH`、`SC_AMT_LOW`、`SC_BRENT_HIGH`、`SC_BRENT_LOW`、`SC_GEO_KINETIC`、`SC_RATIO_HIGH`、`SC_RATIO_LOW`、`SC_SH_ANCHOR`、`SC_SH_FIX`、`SC_SH_FIX_DAYS`、`SC_SH_LAST`、`SC_SH_REDLINE`、`SC_US10Y_HIGH`、`SC_US10Y_LOW`

## 顶层结构（def/class，前 25 个）

`_geo_signal`, `_today`, `ensure_table`, `_num`, `_sig`, `evaluate`, `save`, `load`, `run_daily`

## 已知坑

- 推演证伪记分卡：组合 pbw/key_level/etf 信号 + 情景倾向；第 9 项接金十快讯（硬触发须含"伊朗"，聚合稿剔除）；纯观察器不进决策链；当日无指数收盘必须标"待日批"。

规模：470 行。
