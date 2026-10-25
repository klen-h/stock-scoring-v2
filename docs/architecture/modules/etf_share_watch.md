# etf_share_watch.py

> 生成 2026-10-24（全项目清理，`docs/CLEANUP_REPORT.md` §6）· **以代码为准**：职责摘自文件头 docstring，依赖/阈值由 AST/正则提取自源码。

## 职责

（文件头无 docstring，见源码与顶层结构。）

## 输入 / 输出

- （docstring 未显式声明；见「职责」与顶层结构。）

## 依赖谁（本项目内 import）

- `app.database`
- `app.flash`
- `app.flash.rules`

## 被谁依赖

- （未被其他模块 import；可能是入口/脚本/独立功能）

## 关键阈值 / 环境变量

- `ETF_CONFIRM_DAYS`、`ETF_SHARE_CHG_PCT`、`ETF_STREAK_MAX`

## 顶层结构（def/class，前 25 个）

`_sse_map`, `_prev_trading_day`, `ensure_table`, `save`, `load`, `scan`, `scan_and_store`, `backfill`, `summarize`

## 已知坑

- 上交所 ETF 份额异动（TOT_VOL 万份、pageSize=5000 一次 924 只）；连续 2 日 >=8% ⇒ 市场底清单①自动打勾（人工覆盖优先）；深市 159915 有意不纳入（深交所接口 76 页/日成本过高）。

规模：353 行。
